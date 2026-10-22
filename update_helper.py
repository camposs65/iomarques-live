"""Apply a verified update after the sales app exits, without touching live data."""

import argparse
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import installation


UPDATER_NAME = "IoMarques Atualizador.exe"
JOB_SCHEMA = 1
MAX_JOB_SIZE = 16384
PARENT_WAIT_SECONDS = 90
BOOTLOADER_WAIT_SECONDS = 10
_VERSION = re.compile(r"(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})\Z")
_SHA256 = re.compile(r"[a-fA-F0-9]{64}\Z")


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedUpdate:
    helper: Path
    job: Path
    helper_sha256: str

    @property
    def command(self):
        return [str(self.helper), "--job", str(self.job)]


def cache_root():
    location = os.environ.get("LOCALAPPDATA")
    if not location or not Path(location).is_absolute():
        raise UpdateError("Não encontrei a pasta de atualizações deste usuário do Windows.")
    return installation._checked_path(Path(location) / "IoMarquesLive" / "atualizacoes")


def _cached_path(value, *, allow_root=False):
    if not isinstance(value, (str, os.PathLike)) or not Path(value).is_absolute():
        raise UpdateError("A atualização contém um caminho inválido. Nada foi instalado.")
    path = installation._checked_path(value)
    root = cache_root()
    if (path == root and not allow_root) or not path.is_relative_to(root):
        raise UpdateError("A atualização está fora da pasta de downloads do aplicativo.")
    return path


def _destination(value=None):
    expected = installation._checked_path(installation.install_directory())
    if value is not None and (not isinstance(value, (str, os.PathLike)) or not Path(value).is_absolute()):
        raise UpdateError("A pasta da instalação não foi reconhecida. Nada foi alterado.")
    target = installation._checked_path(value if value is not None else expected)
    if target != expected:
        raise UpdateError("O atualizador só pode alterar a instalação padrão deste usuário.")
    return target


def _hash_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path, limit):
    with Path(path).open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise UpdateError("Os dados da atualização são inválidos. Nada foi instalado.")

    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate field")
            value[key] = item
        return value

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (ValueError, UnicodeError) as error:
        raise UpdateError("Os dados da atualização são inválidos. Nada foi instalado.") from error
    if not isinstance(value, dict):
        raise UpdateError("Os dados da atualização são inválidos. Nada foi instalado.")
    return value


def _validate_job(value):
    required = {"schema", "parent_pid", "parent_executable", "destination", "archive",
                "version", "sha256", "size"}
    if (not isinstance(value, dict) or not required.issubset(value)
            or set(value) - required - {"parent_started"}
            or type(value["schema"]) is not int or value["schema"] != JOB_SCHEMA
            or type(value["parent_pid"]) is not int or not 0 < value["parent_pid"] <= 0xFFFFFFFF
            or not isinstance(value["version"], str) or not _VERSION.fullmatch(value["version"])
            or not isinstance(value["sha256"], str) or not _SHA256.fullmatch(value["sha256"])
            or type(value["size"]) is not int or value["size"] <= 0
            or not isinstance(value["destination"], str) or not Path(value["destination"]).is_absolute()):
        raise UpdateError("A solicitação de atualização é inválida. Nada foi instalado.")
    if "parent_started" in value and (type(value["parent_started"]) is not int
                                      or value["parent_started"] <= 0):
        raise UpdateError("A identificação do aplicativo aberto é inválida.")
    target = _destination(value["destination"])
    parent = value["parent_executable"]
    if not isinstance(parent, str) or not Path(parent).is_absolute():
        raise UpdateError("O aplicativo que solicitou a atualização não foi reconhecido.")
    parent = installation._checked_path(parent, target)
    if parent != target / installation.EXE_NAME:
        raise UpdateError("O aplicativo que solicitou a atualização não foi reconhecido.")
    archive = _cached_path(value["archive"])
    if not archive.is_file():
        raise UpdateError("O download da atualização não está mais disponível. Baixe novamente.")
    return {**value, "destination": target, "parent_executable": parent, "archive": archive}


def load_update_job(path):
    return _validate_job(_read_json(_cached_path(path), MAX_JOB_SIZE))


def _process_handle(pid):
    if os.name != "nt":
        raise UpdateError("A atualização automática está disponível apenas no Windows.")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    kernel.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                                ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
    kernel.QueryFullProcessImageNameW.restype = ctypes.c_int
    kernel.GetProcessTimes.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.c_void_p, ctypes.c_void_p]
    kernel.GetProcessTimes.restype = ctypes.c_int
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.WaitForSingleObject.restype = ctypes.c_uint32
    handle = kernel.OpenProcess(0x00100000 | 0x1000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return kernel, None
        raise UpdateError("O Windows não permitiu verificar o aplicativo aberto. Feche-o e tente novamente.")
    return kernel, handle


def _process_identity(kernel, handle):
    size = ctypes.c_uint32(32768)
    path = ctypes.create_unicode_buffer(size.value)
    if not kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
        raise UpdateError("Não foi possível reconhecer o aplicativo aberto. Nada foi instalado.")
    created, exited, system, user = (ctypes.c_uint64() for _ in range(4))
    if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                 ctypes.byref(system), ctypes.byref(user)):
        raise UpdateError("Não foi possível verificar o início do aplicativo aberto.")
    return installation._checked_path(path.value), created.value


def _parent_started(pid, executable):
    kernel, handle = _process_handle(pid)
    if not handle:
        raise UpdateError("O aplicativo foi encerrado antes de preparar a atualização.")
    try:
        image, created = _process_identity(kernel, handle)
        if image != executable:
            raise UpdateError("O processo que solicitou a atualização não é o aplicativo instalado.")
        return created
    finally:
        kernel.CloseHandle(handle)


def wait_for_parent(job, timeout=PARENT_WAIT_SECONDS):
    kernel, handle = _process_handle(job["parent_pid"])
    if not handle:
        return
    try:
        image, created = _process_identity(kernel, handle)
        if image != job["parent_executable"] or (
                "parent_started" in job and created != job["parent_started"]):
            raise UpdateError("O processo aberto mudou. A atualização foi cancelada por segurança.")
        result = kernel.WaitForSingleObject(handle, int(timeout * 1000))
        if result == 0x102:
            raise UpdateError("O aplicativo ainda está aberto. Feche-o e tente atualizar novamente.")
        if result != 0:
            raise UpdateError("O Windows não confirmou o encerramento do aplicativo. Nada foi instalado.")
    finally:
        kernel.CloseHandle(handle)


def _installed_helper(destination):
    helper = installation._checked_path(destination / UPDATER_NAME, destination)
    manifest_path = installation._checked_path(destination / "release.json", destination)
    manifest = _read_json(manifest_path, 65536)
    record = manifest.get("files", {}).get(UPDATER_NAME) if isinstance(manifest.get("files"), dict) else None
    if (type(manifest.get("schema")) is not int or manifest["schema"] != 1 or not isinstance(record, dict)
            or type(record.get("size")) is not int or record["size"] <= 0
            or not isinstance(record.get("sha256"), str)
            or not _SHA256.fullmatch(record["sha256"])
            or not helper.is_file() or helper.stat().st_size != record["size"]
            or _hash_file(helper) != record["sha256"].lower()):
        raise UpdateError("O atualizador instalado precisa ser reparado. Execute o instalador novamente.")
    return helper, record["sha256"].lower()


def _ensure_newer_version(version, destination):
    manifest_path = installation._checked_path(destination / "release.json", destination)
    manifest = _read_json(manifest_path, 65536)
    current = manifest.get("version")
    if (type(manifest.get("schema")) is not int or manifest["schema"] != 1
            or not isinstance(current, str) or not _VERSION.fullmatch(current)):
        raise UpdateError("A versão instalada não foi reconhecida. Execute o instalador novamente.")
    if tuple(map(int, version.split("."))) <= tuple(map(int, current.split("."))):
        raise UpdateError("O aplicativo já tem esta versão ou uma versão mais nova. Nada foi substituído.")


def prepare_update_job(ready, destination=None, parent_pid=None, parent_executable=None, cache_dir=None):
    """Copy the trusted installed helper, so replacing its installed EXE is possible."""
    from update_client import validate_package

    target = _destination(destination)
    directory = _cached_path(cache_dir or cache_root(), allow_root=True)
    directory.mkdir(parents=True, exist_ok=True)
    _cached_path(directory, allow_root=True)
    if not isinstance(ready, dict):
        raise UpdateError("O download da atualização não foi reconhecido.")
    value = {"schema": JOB_SCHEMA, "parent_pid": parent_pid if parent_pid is not None else os.getpid(),
             "parent_executable": str(parent_executable if parent_executable is not None else sys.executable),
             "destination": str(target), "archive": str(ready.get("path", ready.get("archive", ""))),
             "version": ready.get("version"), "sha256": ready.get("sha256"), "size": ready.get("size")}
    job = _validate_job(value)
    validate_package(job["archive"], job["version"], job["sha256"], job["size"])
    _ensure_newer_version(job["version"], target)
    value["parent_started"] = _parent_started(job["parent_pid"], job["parent_executable"])
    source, digest = _installed_helper(target)
    working = Path(tempfile.mkdtemp(prefix="aplicar-", dir=directory))
    _cached_path(working)
    helper = working / UPDATER_NAME
    shutil.copy2(source, helper)
    if _hash_file(helper) != digest:
        raise UpdateError("Não foi possível preparar o atualizador. Tente novamente.")
    job_path = working / "job.json"
    with job_path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return PreparedUpdate(helper, job_path, digest)


def launch_update(prepared):
    if not isinstance(prepared, PreparedUpdate):
        raise UpdateError("O atualizador não foi preparado corretamente.")
    helper, job_path = _cached_path(prepared.helper), _cached_path(prepared.job)
    if (helper.parent != job_path.parent or helper.name != UPDATER_NAME
            or _hash_file(helper) != prepared.helper_sha256):
        raise UpdateError("O atualizador preparado mudou. A atualização foi cancelada.")
    load_update_job(job_path)
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    return subprocess.Popen(prepared.command, cwd=job_path.parent, close_fds=True, **options)


def _preflight(package, runner):
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    for name, argument in ((installation.EXE_NAME, "--verificar-instalacao"),
                           (UPDATER_NAME, "--verificar-atualizador")):
        result = runner([str(package / name), argument], cwd=package, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, timeout=120, **options)
        if result.returncode:
            raise UpdateError("A nova versão não passou pela verificação. A instalação anterior foi preservada.")


def _fingerprint(destination):
    result = {}
    for name in (*installation.PACKAGE_FILES, "instalacao.json"):
        path = installation._checked_path(destination / name, destination)
        if path.exists():
            if not path.is_file():
                raise UpdateError("A instalação contém um arquivo inesperado. Nada foi alterado.")
            result[name] = _hash_file(path)
    return result


def _launch_app(executable):
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    return subprocess.Popen([str(executable)], cwd=executable.parent, close_fds=True, **options)


def _write_log(path, message):
    path = _cached_path(path)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"{datetime.now(timezone.utc).isoformat()} {message}\n")


def _acquire_installation_lock(destination):
    if os.name != "nt":
        return lambda: None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.WaitForSingleObject.restype = ctypes.c_uint32
    kernel.ReleaseMutex.argtypes = [ctypes.c_void_p]
    kernel.ReleaseMutex.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    identity = hashlib.sha256(os.path.normcase(str(destination)).encode("utf-8")).hexdigest()
    handle = kernel.CreateMutexW(None, False, "Local\\IoMarquesLiveAtualizacao-" + identity)
    if not handle:
        raise UpdateError("O Windows não conseguiu reservar a instalação para atualizar. Tente novamente.")
    if kernel.WaitForSingleObject(handle, 0) not in (0, 0x80):
        kernel.CloseHandle(handle)
        raise UpdateError("Outra atualização já está em andamento. Aguarde a conclusão.")

    def release():
        kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)

    return release


def _wait_until_app_closed(destination, ensure_closed, *, timeout=BOOTLOADER_WAIT_SECONDS,
                           clock=None, sleeper=None):
    """The one-file bootloader may outlive its Python child while cleaning up."""
    clock = clock or time.monotonic
    sleeper = sleeper or time.sleep
    deadline = clock() + timeout
    while True:
        try:
            ensure_closed(destination=destination)
            return
        except RuntimeError as error:
            remaining = deadline - clock()
            if remaining <= 0:
                raise UpdateError("O aplicativo ainda está aberto ou o Windows não confirmou seu encerramento. "
                                  "Feche todas as janelas do IoMarques Live e tente atualizar novamente. "
                                  "A instalação e os dados foram preservados.") from error
            sleeper(min(0.25, remaining))


def apply_update(job_path, *, wait_parent=None, ensure_closed=None, deploy=None,
                 runner=None, launcher=None, wait_closed=None):
    """The injected seams allow testing every write in a disposable installation."""
    from update_client import extract_verified_package

    job_path = _cached_path(job_path)
    job = load_update_job(job_path)
    wait_parent = wait_parent or wait_for_parent
    ensure_closed = ensure_closed or installation.ensure_app_closed
    wait_closed = wait_closed or _wait_until_app_closed
    deploy = deploy or installation.deploy_package
    runner = runner or subprocess.run
    launcher = launcher or _launch_app
    log = job_path.parent / "atualizacao.log"
    target = job["destination"]
    original = None
    staging = None
    _write_log(log, "Preparando a atualização verificada.")
    release_lock = _acquire_installation_lock(target)
    try:
        wait_parent(job)
        wait_closed(target, ensure_closed)
        _ensure_newer_version(job["version"], target)
        original = _fingerprint(target)
        if installation.EXE_NAME not in original:
            raise UpdateError("A instalação anterior não foi encontrada. Execute o instalador novamente.")
        staging = Path(tempfile.mkdtemp(prefix="pacote-", dir=job_path.parent))
        _cached_path(staging)
        extract_verified_package(job["archive"], staging, job["version"], job["sha256"], job["size"])
        _preflight(staging, runner)
        current_config = installation._checked_path(target / "integracao_supabase.json", target)
        if current_config.is_file():
            shutil.copy2(current_config, staging / "integracao_supabase.json")
        ensure_closed(destination=target)
        _ensure_newer_version(job["version"], target)
        if original != _fingerprint(target):
            raise UpdateError("A instalação mudou durante a verificação. Tente atualizar novamente.")
        deploy(staging, target)
        _write_log(log, "Atualização instalada. Os dados da live não foram alterados.")
        launcher(target / installation.EXE_NAME)
        return target / installation.EXE_NAME
    except Exception as error:
        _write_log(log, f"Atualização interrompida ({type(error).__name__}).")
        try:
            restored = original is not None and original == _fingerprint(target)
        except (OSError, RuntimeError):
            restored = False
        if restored:
            try:
                ensure_closed(destination=target)
                launcher(target / installation.EXE_NAME)
                _write_log(log, "A versão anterior foi preservada e reaberta.")
            except Exception:
                _write_log(log, "A versão anterior foi preservada; abra-a pelo atalho.")
        if isinstance(error, UpdateError):
            raise
        if restored:
            raise UpdateError("Não foi possível atualizar. A versão anterior e os dados foram preservados. "
                              "Tente novamente ou consulte o relatório da atualização.") from error
        raise UpdateError("Não foi possível concluir a atualização. Nenhum dado de live foi removido. "
                          "Consulte o relatório e execute o instalador novamente antes de abrir o app.") from error
    finally:
        try:
            if staging is not None:
                _cached_path(staging)
                if staging.parent != job_path.parent or not staging.name.startswith("pacote-"):
                    raise UpdateError("A limpeza temporária foi cancelada por segurança.")
                try:
                    shutil.rmtree(staging)
                except OSError:
                    _write_log(log, "O Windows manteve arquivos temporários no cache; nenhum dado de live foi removido.")
        finally:
            release_lock()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Atualizador do IoMarques Live")
    parser.add_argument("--verificar-atualizador", action="store_true")
    parser.add_argument("--job")
    args = parser.parse_args(argv)
    if args.verificar_atualizador:
        return 0
    if not args.job:
        return 2
    try:
        apply_update(args.job)
    except Exception as error:
        message = (str(error) if isinstance(error, UpdateError) else
                   "Não foi possível preparar a atualização. A instalação e os dados foram preservados.")
        try:
            report = _cached_path(args.job).parent / "atualizacao.log"
            if report.is_file():
                message += f"\n\nRelatório:\n{report}"
        except (OSError, RuntimeError, TypeError, ValueError):
            pass
        if os.name == "nt":
            ctypes.windll.user32.MessageBoxW(None, message, "Atualização do IoMarques Live", 0x10)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
