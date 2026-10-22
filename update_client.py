"""Downloads verified public releases; installing them is a separate operation."""

import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from supabase_sync import _validated_config


REPOSITORY = "camposs65/iomarques-live"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
ASSET_NAME = "IoMarquesLive-Windows-x64.zip"
PACKAGE_FILES = frozenset({
    "IoMarques Brecho.exe", "IoMarques Atualizador.exe",
    "integracao_supabase.json", "release.json",
})
PAYLOAD_FILES = PACKAGE_FILES - {"release.json"}
MAX_PACKAGE_SIZE = 250 * 1024 * 1024
MAX_UNPACKED_SIZE = 500 * 1024 * 1024
MAX_METADATA_SIZE = 1024 * 1024
MAX_MANIFEST_SIZE = 64 * 1024
MAX_CONFIG_SIZE = 32 * 1024
NETWORK_TIMEOUT = 15
CHUNK_SIZE = 256 * 1024
ALLOWED_HOSTS = frozenset({
    "github.com", "api.github.com", "release-assets.githubusercontent.com",
    "objects.githubusercontent.com", "github-releases.githubusercontent.com",
})


class UpdateError(ValueError):
    pass


class _Cancelled(Exception):
    pass


def parse_version(value):
    """Only stable, unambiguous X.Y.Z versions participate in updates."""
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})", value
    ):
        raise UpdateError("Versão de atualização inválida.")
    return tuple(int(part) for part in value.split("."))


def _safe_integer(value, maximum):
    return type(value) is int and 0 < value <= maximum


def _safe_digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", value):
        raise UpdateError("A atualização não possui um SHA-256 válido.")
    return value.lower()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise UpdateError("A atualização contém campos duplicados.")
        result[key] = value
    return result


def _read_json(value):
    try:
        return json.loads(value.decode("utf-8-sig"), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise UpdateError("Os dados da atualização estão inválidos.") from error


def _is_reparse(path):
    details = path.lstat()
    return stat.S_ISLNK(details.st_mode) or bool(
        getattr(details, "st_file_attributes", 0) & 0x400
    )


def _check_directory(path):
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        if item.exists() or item.is_symlink():
            if _is_reparse(item) or not item.is_dir():
                raise UpdateError("A pasta da atualização não é segura.")
    return path


def _hash_stream(stream, limit, cancel=None):
    digest = hashlib.sha256()
    size = 0
    while True:
        if cancel is not None and cancel.is_set():
            raise _Cancelled()
        chunk = stream.read(min(CHUNK_SIZE, limit - size + 1))
        if not chunk:
            return digest.hexdigest(), size
        size += len(chunk)
        if size > limit:
            raise UpdateError("A atualização ultrapassa o tamanho permitido.")
        digest.update(chunk)


def _validate_open_package(stream, expected_version, expected_sha256, expected_size):
    parse_version(expected_version)
    expected_sha256 = _safe_digest(expected_sha256)
    if not _safe_integer(expected_size, MAX_PACKAGE_SIZE):
        raise UpdateError("Tamanho de atualização inválido.")
    actual_digest, actual_size = _hash_stream(stream, MAX_PACKAGE_SIZE)
    if actual_size != expected_size or actual_digest != expected_sha256:
        raise UpdateError("O download não confere com a versão publicada. Tente novamente.")
    stream.seek(0)
    try:
        archive = zipfile.ZipFile(stream)
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(entries) != len(PACKAGE_FILES) or set(names) != PACKAGE_FILES:
            raise UpdateError("A atualização contém arquivos inesperados ou incompletos.")
        total = 0
        for entry in entries:
            mode = entry.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if (entry.is_dir() or entry.filename != entry.orig_filename
                    or entry.flag_bits & 1 or entry.external_attr & 0x400
                    or kind not in {0, stat.S_IFREG}
                    or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                    or entry.file_size <= 0 or entry.file_size > MAX_UNPACKED_SIZE
                    or entry.file_size > max(entry.compress_size, 1) * 200):
                raise UpdateError("A atualização contém um arquivo inseguro.")
            total += entry.file_size
        if total > MAX_UNPACKED_SIZE:
            raise UpdateError("A atualização descompactada ultrapassa o limite permitido.")
        if archive.getinfo("release.json").file_size > MAX_MANIFEST_SIZE:
            raise UpdateError("O manifesto da atualização é muito grande.")
        manifest_content = archive.read("release.json")
        manifest = _read_json(manifest_content)
        archive._verified_manifest_sha256 = hashlib.sha256(manifest_content).hexdigest()
        if (not isinstance(manifest, dict) or set(manifest) != {"schema", "version", "files"}
                or type(manifest["schema"]) is not int or manifest["schema"] != 1
                or manifest["version"] != expected_version
                or not isinstance(manifest["files"], dict)
                or set(manifest["files"]) != PAYLOAD_FILES):
            raise UpdateError("O manifesto não corresponde à versão da atualização.")
        for name in PAYLOAD_FILES:
            record = manifest["files"][name]
            if (not isinstance(record, dict) or set(record) != {"sha256", "size"}
                    or not _safe_integer(record["size"], MAX_UNPACKED_SIZE)):
                raise UpdateError("O manifesto possui um arquivo inválido.")
            digest = _safe_digest(record["sha256"])
            if archive.getinfo(name).file_size != record["size"]:
                raise UpdateError("Um arquivo da atualização tem tamanho incorreto.")
            with archive.open(name) as source:
                actual_digest, actual_size = _hash_stream(source, record["size"])
            if actual_digest != digest or actual_size != record["size"]:
                raise UpdateError("Um arquivo da atualização não passou na verificação SHA-256.")
        if archive.getinfo("integracao_supabase.json").file_size > MAX_CONFIG_SIZE:
            raise UpdateError("A configuração da atualização é muito grande.")
        config = _read_json(archive.read("integracao_supabase.json"))
        if (not isinstance(config, dict)
                or set(config) != {"supabase_url", "supabase_publishable_key", "auth_email_base"}
                or _validated_config(config) is None):
            raise UpdateError("A atualização não contém uma configuração pública válida.")
        return archive, manifest
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError) as error:
        raise UpdateError("O arquivo de atualização está corrompido ou é incompatível.") from error


def _open_package(path):
    path = Path(path)
    try:
        if _is_reparse(path) or not path.is_file():
            raise UpdateError("O arquivo de atualização não é seguro.")
        return path.open("rb")
    except OSError as error:
        raise UpdateError("Não foi possível ler a atualização baixada.") from error


def validate_package(archive, expected_version, expected_sha256, expected_size):
    with _open_package(archive) as stream:
        package, manifest = _validate_open_package(
            stream, expected_version, expected_sha256, expected_size
        )
        package.close()
        return manifest


def extract_verified_package(archive, destination, expected_version, expected_sha256,
                             expected_size):
    """Extract only into an empty, non-linked staging folder; never replace user data."""
    destination = _check_directory(destination)
    if destination.exists() and any(destination.iterdir()):
        raise UpdateError("A pasta de preparação da atualização precisa estar vazia.")
    created = []
    with _open_package(archive) as stream:
        package, manifest = _validate_open_package(
            stream, expected_version, expected_sha256, expected_size
        )
        try:
            destination.mkdir(parents=True, exist_ok=True)
            for name in sorted(PACKAGE_FILES):
                _check_directory(destination)
                target = destination / name
                record = manifest["files"].get(name, {
                    "size": package.getinfo("release.json").file_size,
                    "sha256": package._verified_manifest_sha256,
                })
                digest = hashlib.sha256()
                size = 0
                with package.open(name) as source, target.open("xb") as output:
                    created.append(target)
                    while True:
                        chunk = source.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > package.getinfo(name).file_size:
                            raise UpdateError("Arquivo de atualização maior que o esperado.")
                        output.write(chunk)
                        digest.update(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if (size != record["size"]
                        or digest.hexdigest() != record["sha256"].lower()):
                    raise UpdateError("O arquivo mudou durante a preparação da atualização.")
            return manifest
        except (OSError, zipfile.BadZipFile, RuntimeError, UpdateError) as error:
            for target in created:
                try:
                    target.unlink()
                except OSError:
                    pass
            if isinstance(error, UpdateError):
                raise
            raise UpdateError("Não foi possível preparar os arquivos da atualização.") from error
        finally:
            package.close()


def _safe_url(url):
    try:
        parsed = urllib.parse.urlsplit(url)
        valid = (isinstance(url, str) and not re.search(r"[\s\\]", url)
                 and parsed.scheme == "https" and parsed.hostname in ALLOWED_HOSTS
                 and parsed.port in {None, 443} and parsed.username is None
                 and parsed.password is None and not parsed.fragment)
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise UpdateError("A atualização foi direcionada para um endereço não autorizado.")
    return url


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        _safe_url(newurl)
        return super().redirect_request(request, response, code, message, headers, newurl)


class UpdateClient:
    """One background check at a time. Callbacks never run on the UI thread."""

    def __init__(self, installed_version, cache_dir, callback):
        self.installed_version = installed_version
        self.version_tuple = parse_version(installed_version)
        self.cache_dir = Path(cache_dir)
        self.callback = callback
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self._opener = urllib.request.build_opener(_SafeRedirectHandler())

    def start(self):
        return self.check_now()

    def check_now(self):
        with self._lock:
            if self._cancel.is_set() or (self._thread is not None and self._thread.is_alive()):
                return False
            self._thread = threading.Thread(target=self._run, name="IoMarquesUpdates", daemon=True)
            self._thread.start()
            return True

    def shutdown(self):
        self._cancel.set()

    def _emit(self, event, payload=None):
        if not self._cancel.is_set():
            try:
                self.callback(event, payload or {})
            except Exception:
                pass

    def _request(self, url, accept):
        if self._cancel.is_set():
            raise _Cancelled()
        request = urllib.request.Request(_safe_url(url), headers={
            "User-Agent": f"IoMarquesLive/{self.installed_version}", "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
        })
        response = self._opener.open(request, timeout=NETWORK_TIMEOUT)
        try:
            _safe_url(response.geturl())
        except Exception:
            response.close()
            raise
        return response

    def _release(self):
        with self._request(LATEST_RELEASE_URL, "application/vnd.github+json") as response:
            content = bytearray()
            while True:
                if self._cancel.is_set():
                    raise _Cancelled()
                chunk = response.read(min(CHUNK_SIZE, MAX_METADATA_SIZE - len(content) + 1))
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > MAX_METADATA_SIZE:
                    raise UpdateError("A resposta do GitHub ultrapassa o tamanho permitido.")
        release = _read_json(content)
        if not isinstance(release, dict) or release.get("draft") is not False \
                or release.get("prerelease") is not False:
            raise UpdateError("O GitHub não retornou uma versão estável válida.")
        tag = release.get("tag_name")
        if not isinstance(tag, str) or not tag.startswith("v"):
            raise UpdateError("A versão publicada no GitHub é inválida.")
        version = tag[1:]
        remote_tuple = parse_version(version)
        if remote_tuple <= self.version_tuple:
            return None
        assets = release.get("assets")
        if not isinstance(assets, list):
            raise UpdateError("A versão publicada não contém o instalador para Windows.")
        matches = [item for item in assets if isinstance(item, dict)
                   and item.get("name") == ASSET_NAME]
        if len(matches) != 1:
            raise UpdateError("A versão publicada não contém um pacote único para Windows.")
        asset = matches[0]
        size = asset.get("size")
        digest = asset.get("digest")
        if not _safe_integer(size, MAX_PACKAGE_SIZE):
            raise UpdateError("O pacote publicado possui tamanho inválido.")
        if not isinstance(digest, str) or not digest.startswith("sha256:"):
            raise UpdateError("O GitHub não forneceu o SHA-256 da atualização.")
        sha256 = _safe_digest(digest[7:])
        url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{ASSET_NAME}"
        if asset.get("browser_download_url") != url:
            raise UpdateError("O endereço do pacote publicado não corresponde ao projeto.")
        return {"version": version, "url": url, "sha256": sha256, "size": size}

    def _download(self, release):
        cache = _check_directory(self.cache_dir)
        cache.mkdir(parents=True, exist_ok=True)
        target = cache / f"v{release['version']}-{release['sha256']}.zip"
        if target.exists() or target.is_symlink():
            try:
                validate_package(target, release["version"], release["sha256"], release["size"])
                return target
            except UpdateError:
                if _is_reparse(target) or not target.is_file():
                    raise UpdateError("O cache de atualizações contém um arquivo inseguro.")
                target.unlink()
        descriptor, filename = tempfile.mkstemp(prefix=".download-", suffix=".tmp", dir=cache)
        temporary = Path(filename)
        try:
            self._emit("downloading", {"percent": 0, "version": release["version"]})
            with os.fdopen(descriptor, "wb") as output:
                with self._request(release["url"], "application/octet-stream") as response:
                    declared = response.headers.get("Content-Length")
                    if declared is not None and declared != str(release["size"]):
                        raise UpdateError("O tamanho do download não corresponde ao publicado.")
                    digest = hashlib.sha256()
                    size = 0
                    last_percent = 0
                    while True:
                        if self._cancel.is_set():
                            raise _Cancelled()
                        chunk = response.read(min(CHUNK_SIZE, release["size"] - size + 1))
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > release["size"]:
                            raise UpdateError("O download ultrapassou o tamanho publicado.")
                        output.write(chunk)
                        digest.update(chunk)
                        percent = min(99, int(size * 100 / release["size"]))
                        if percent != last_percent:
                            self._emit("downloading", {"percent": percent,
                                                       "version": release["version"]})
                            last_percent = percent
                    if size != release["size"] or digest.hexdigest() != release["sha256"]:
                        raise UpdateError("O download foi interrompido ou não passou na verificação SHA-256.")
                output.flush()
                os.fsync(output.fileno())
            if self._cancel.is_set():
                raise _Cancelled()
            validate_package(temporary, release["version"], release["sha256"], release["size"])
            if self._cancel.is_set():
                raise _Cancelled()
            _check_directory(cache)
            if target.is_symlink() or (target.exists() and _is_reparse(target)):
                raise UpdateError("O cache de atualizações contém um arquivo inseguro.")
            os.replace(temporary, target)
            self._emit("downloading", {"percent": 100, "version": release["version"]})
            return target
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _run(self):
        self._emit("checking")
        try:
            release = self._release()
            if release is None:
                self._emit("current", {"version": self.installed_version})
                return
            target = self._download(release)
            self._emit("ready", {"version": release["version"], "path": str(target),
                                 "sha256": release["sha256"], "size": release["size"]})
        except _Cancelled:
            return
        except UpdateError as error:
            self._emit("error", {"message": str(error)})
        except Exception:
            self._emit("error", {"message": (
                "Não foi possível consultar ou baixar a atualização. "
                "Confira a internet e tente novamente."
            )})
