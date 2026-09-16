import base64
import ctypes
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


CONFIG_FILENAME = "integracao_supabase.json"
LOCAL_CONFIG_FILENAME = "integracao_supabase.local.json"
SESSION_FILENAME = "integracao_sessao.dat"
QUEUE_FILENAME = "integracao_pendencias.json"
MAX_BATCH_SIZE = 100
RETRY_SECONDS = 30


class SyncError(Exception):
    pass


class SyncAuthError(SyncError):
    pass


class SyncTemporaryError(SyncError):
    pass


@dataclass(frozen=True)
class SyncConfig:
    url: str
    publishable_key: str
    auth_email_base: str


def _read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _read_dotenv(path):
    values = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _validated_config(value):
    if not isinstance(value, dict):
        return None

    url = str(value.get("supabase_url", "") or "").strip().rstrip("/")
    key = str(value.get("supabase_publishable_key", "") or "").strip()
    email_base = str(value.get("auth_email_base", "") or "").strip()

    if not url.startswith("https://") or not key or "@" not in email_base:
        return None

    return SyncConfig(url=url, publishable_key=key, auth_email_base=email_base)


def load_sync_config(app_dir, source_dir=None):
    app_dir = Path(app_dir)
    candidates = [
        app_dir / CONFIG_FILENAME,
        app_dir / LOCAL_CONFIG_FILENAME,
    ]
    if source_dir:
        source_dir = Path(source_dir)
        candidates.extend(
            [
                source_dir / LOCAL_CONFIG_FILENAME,
                source_dir.parent / "brecho-controle" / ".env.local",
            ]
        )

    environment = {
        "supabase_url": os.environ.get("IOMARQUES_SUPABASE_URL", ""),
        "supabase_publishable_key": os.environ.get(
            "IOMARQUES_SUPABASE_PUBLISHABLE_KEY", ""
        ),
        "auth_email_base": os.environ.get("IOMARQUES_AUTH_EMAIL_BASE", ""),
    }
    configured = _validated_config(environment)
    if configured:
        return configured

    for path in candidates:
        if path.name == ".env.local":
            dotenv = _read_dotenv(path)
            value = {
                "supabase_url": dotenv.get("VITE_SUPABASE_URL", ""),
                "supabase_publishable_key": dotenv.get(
                    "VITE_SUPABASE_PUBLISHABLE_KEY", ""
                ),
                "auth_email_base": dotenv.get("VITE_AUTH_EMAIL_BASE", ""),
            }
        else:
            value = _read_json(path)

        configured = _validated_config(value)
        if configured:
            return configured

    return None


def _atomic_write(path, content, binary=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.stem}_{uuid.uuid4().hex}.tmp")
    try:
        if binary:
            with temporary.open("wb") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
        else:
            with temporary.open("w", encoding="utf-8") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
        temporary.replace(path)
    finally:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass


def _protect_windows(data):
    if not sys.platform.startswith("win"):
        raise OSError("A proteção da sessão está disponível somente no Windows.")

    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
        ]

    source_buffer = ctypes.create_string_buffer(data)
    source = DataBlob(
        len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte))
    )
    result = DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    success = crypt32.CryptProtectData(
        ctypes.byref(source),
        "IoMarques Brecho",
        None,
        None,
        None,
        0x01,
        ctypes.byref(result),
    )
    if not success:
        raise ctypes.WinError()

    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)


def _unprotect_windows(data):
    if not sys.platform.startswith("win"):
        raise OSError("A proteção da sessão está disponível somente no Windows.")

    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
        ]

    source_buffer = ctypes.create_string_buffer(data)
    source = DataBlob(
        len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte))
    )
    result = DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    success = crypt32.CryptUnprotectData(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        0x01,
        ctypes.byref(result),
    )
    if not success:
        raise ctypes.WinError()

    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)


def _parse_duration(value):
    parts = str(value or "").strip().split(":")
    if len(parts) != 3:
        return None
    try:
        hours, minutes, seconds = [int(part) for part in parts]
    except ValueError:
        return None
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        return None
    total = hours * 3600 + minutes * 60 + seconds
    return total if total <= 86400 else None


def _parse_timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value or "").strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(timezone.utc)


def _parse_money_cents(value):
    text = str(value or "").strip()
    if not text:
        return None
    clean = text.lower().replace("r$", "").replace(" ", "")
    if "," in clean:
        clean = clean.replace(".", "").replace(",", ".")
    elif clean.count(".") > 1:
        clean = clean.replace(".", "")
    try:
        amount = Decimal(clean)
    except InvalidOperation:
        return None
    if amount < 0:
        return None
    cents = int((amount * 100).quantize(Decimal("1")))
    return cents if cents <= 999_999_999 else None


def prepare_live_payload(live):
    if not isinstance(live, dict) or not isinstance(live.get("rows"), list):
        return None

    live_id = str(live.get("id", "") or "").strip()
    started_at = _parse_timestamp(live.get("started_at"))
    finished_at = _parse_timestamp(live.get("finished_at"))
    if not live_id or len(live_id) > 200 or not started_at or not finished_at:
        return None
    if finished_at < started_at:
        return None

    duration = _parse_duration(live.get("duration"))
    if duration is None:
        duration = max(0, round((finished_at - started_at).total_seconds()))
    if duration > 86400:
        return None

    unsold = []
    for index, row in enumerate(live["rows"]):
        if not isinstance(row, dict):
            continue
        value_text = str(row.get("valor", "") or "").strip()
        code = str(row.get("codigo", "") or "").strip()
        client = str(row.get("cliente", "") or "").strip()
        if client or (not value_text and not code):
            continue
        unsold.append(
            {
                "numero": index + 1,
                "codigo": code[:100],
                "valor_texto": value_text[:50],
                "valor_centavos": _parse_money_cents(value_text),
                "tempo_segundos": _parse_duration(row.get("tempo")),
            }
        )

    return {
        "id": live_id,
        "iniciada_em": started_at.isoformat(),
        "finalizada_em": finished_at.isoformat(),
        "duracao_segundos": duration,
        "pecas": unsold,
    }


def prepare_history_payload(lives):
    if not isinstance(lives, list):
        return []
    prepared = []
    for live in lives:
        payload = prepare_live_payload(live)
        if payload:
            prepared.append(payload)
    return prepared


class SupabaseHistorySync:
    def __init__(self, app_dir, source_dir=None, status_callback=None):
        self.app_dir = Path(app_dir)
        self.config = load_sync_config(self.app_dir, source_dir)
        self.status_callback = status_callback
        self.session_path = self.app_dir / SESSION_FILENAME
        self.queue_path = self.app_dir / QUEUE_FILENAME
        self.lock = threading.RLock()
        self.timer = None
        self.flushing = False
        self.closed = False
        self.session = self._load_session()
        self.pending_lives, self.pending_deletions = self._load_queue()
        self.status = "starting"
        self.detail = "Preparando sincronização."

    @property
    def is_configured(self):
        return self.config is not None

    @property
    def is_authenticated(self):
        with self.lock:
            return bool(self.session and self.session.get("refresh_token"))

    @property
    def username(self):
        with self.lock:
            return str((self.session or {}).get("username", "") or "")

    def start(self):
        if not self.config:
            self._emit("not_configured", "A integração do Supabase não foi configurada neste app.")
            return
        if not self.is_authenticated:
            self._emit("auth_required", "Entre para enviar as peças não vendidas automaticamente.")
            return
        self._emit("pending", "Verificando envios pendentes.")
        self._schedule_flush(0.2)

    def shutdown(self):
        with self.lock:
            self.closed = True
            if self.timer:
                self.timer.cancel()
                self.timer = None

    def queue_history(self, lives, immediate=False):
        prepared = prepare_history_payload(lives)
        if not prepared:
            return 0
        with self.lock:
            for live in prepared:
                self.pending_lives[live["id"]] = live
                self.pending_deletions.discard(live["id"])
            self._save_queue_locked()
        if self.config and self.is_authenticated:
            self._emit("pending", "Existem alterações aguardando envio.")
            self._schedule_flush(0 if immediate else 1.5)
        return len(prepared)

    def queue_delete(self, live_id, immediate=False):
        live_id = str(live_id or "").strip()
        if not live_id:
            return
        with self.lock:
            self.pending_lives.pop(live_id, None)
            self.pending_deletions.add(live_id)
            self._save_queue_locked()
        if self.config and self.is_authenticated:
            self._emit("pending", "Existe uma exclusão aguardando envio.")
            self._schedule_flush(0 if immediate else 1.5)

    def sync_now(self):
        if not self.config:
            self._emit("not_configured", "A integração do Supabase não foi configurada neste app.")
            return
        if not self.is_authenticated:
            self._emit("auth_required", "Entre para iniciar a sincronização.")
            return
        self._schedule_flush(0)

    def login_async(self, username, password, completion=None):
        if not self.config:
            if completion:
                completion(False, "A integração do Supabase não foi configurada.")
            return

        username = str(username or "").strip().lower()
        password = str(password or "")

        def worker():
            try:
                email = self._username_to_email(username)
                response = self._request_json(
                    "/auth/v1/token?grant_type=password",
                    {"email": email, "password": password},
                )
                session = self._session_from_response(response, username)
                with self.lock:
                    self.session = session
                    try:
                        self._save_session_locked()
                    except OSError as exc:
                        self.session = None
                        raise SyncAuthError(
                            "O Windows não conseguiu proteger a sessão neste computador."
                        ) from exc
                self._emit("pending", "Conta conectada. Preparando o primeiro envio.")
                self._schedule_flush(0)
                if completion:
                    completion(True, "Conta conectada com segurança.")
            except SyncError as exc:
                self._emit("auth_required", str(exc))
                if completion:
                    completion(False, str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def logout(self):
        with self.lock:
            self.session = None
            try:
                self.session_path.unlink(missing_ok=True)
            except OSError:
                pass
        self._emit("auth_required", "Conta desconectada. Os dados locais foram mantidos.")

    def _username_to_email(self, username):
        if not re.fullmatch(r"[a-z0-9._-]+", username):
            raise SyncAuthError(
                "O usuário deve conter apenas letras, números, ponto, hífen ou underline."
            )
        name, separator, domain = self.config.auth_email_base.partition("@")
        if not separator or not name or not domain:
            raise SyncAuthError("A configuração de autenticação é inválida.")
        return f"{name}+{username}@{domain}"

    def _session_from_response(self, response, username):
        if not isinstance(response, dict):
            raise SyncAuthError("O servidor não confirmou o acesso.")
        access_token = str(response.get("access_token", "") or "")
        refresh_token = str(response.get("refresh_token", "") or "")
        try:
            expires_in = max(60, int(response.get("expires_in", 3600)))
        except (TypeError, ValueError):
            expires_in = 3600
        if not access_token or not refresh_token:
            raise SyncAuthError("O servidor não confirmou o acesso.")
        return {
            "username": username,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": int(time.time()) + expires_in,
        }

    def _request_json(self, path, payload, access_token=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "apikey": self.config.publishable_key,
            "Content-Type": "application/json",
        }
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        request = urllib.request.Request(
            f"{self.config.url}{path}", data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                content = response.read().decode("utf-8")
                return json.loads(content) if content else None
        except urllib.error.HTTPError as exc:
            try:
                error_body = json.loads(exc.read().decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                error_body = {}
            message = str(
                error_body.get("msg")
                or error_body.get("message")
                or error_body.get("error_description")
                or ""
            ).lower()
            if (
                exc.code in {401, 403}
                or "invalid login" in message
                or (path.startswith("/auth/v1/") and exc.code == 400)
            ):
                raise SyncAuthError("Usuário ou senha inválidos.") from exc
            if "function" in message and "schema cache" in message:
                raise SyncTemporaryError(
                    "A atualização da integração ainda não foi aplicada no Supabase."
                ) from exc
            raise SyncTemporaryError(
                "O servidor não conseguiu receber as peças não vendidas."
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SyncTemporaryError(
                "Sem conexão com o servidor. O envio continuará pendente."
            ) from exc

    def _refresh_session(self):
        with self.lock:
            current = dict(self.session or {})
        refresh_token = str(current.get("refresh_token", "") or "")
        if not refresh_token:
            raise SyncAuthError("Entre novamente para continuar sincronizando.")
        response = self._request_json(
            "/auth/v1/token?grant_type=refresh_token",
            {"refresh_token": refresh_token},
        )
        refreshed = self._session_from_response(response, current.get("username", ""))
        with self.lock:
            self.session = refreshed
            try:
                self._save_session_locked()
            except OSError as exc:
                raise SyncTemporaryError(
                    "Não foi possível atualizar a sessão protegida neste computador."
                ) from exc
        return refreshed["access_token"]

    def _access_token(self):
        with self.lock:
            current = dict(self.session or {})
        if int(current.get("expires_at", 0) or 0) <= int(time.time()) + 60:
            return self._refresh_session()
        token = str(current.get("access_token", "") or "")
        if not token:
            return self._refresh_session()
        return token

    def _rpc(self, function_name, payload):
        token = self._access_token()
        try:
            return self._request_json(
                f"/rest/v1/rpc/{function_name}", payload, access_token=token
            )
        except SyncAuthError:
            token = self._refresh_session()
            return self._request_json(
                f"/rest/v1/rpc/{function_name}", payload, access_token=token
            )

    def _schedule_flush(self, delay):
        with self.lock:
            if self.closed:
                return
            if self.timer:
                self.timer.cancel()
            self.timer = threading.Timer(delay, self._flush)
            self.timer.daemon = True
            self.timer.start()

    def _flush(self):
        with self.lock:
            self.timer = None
            if self.closed or self.flushing:
                return
            self.flushing = True

        try:
            self._emit("syncing", "Enviando somente as peças não vendidas.")
            while True:
                with self.lock:
                    items = list(self.pending_lives.items())[:MAX_BATCH_SIZE]
                if not items:
                    break
                payload = [item[1] for item in items]
                confirmed = self._rpc(
                    "importar_lives_nao_vendidas", {"p_lives": payload}
                )
                if confirmed != len(payload):
                    raise SyncTemporaryError("O servidor não confirmou todo o envio.")
                with self.lock:
                    for live_id, sent in items:
                        if self.pending_lives.get(live_id) == sent:
                            self.pending_lives.pop(live_id, None)
                    self._save_queue_locked()

            while True:
                with self.lock:
                    live_id = next(iter(self.pending_deletions), None)
                if not live_id:
                    break
                confirmed = self._rpc(
                    "excluir_live_vendas_importada", {"p_live_id": live_id}
                )
                if confirmed != live_id:
                    raise SyncTemporaryError("O servidor não confirmou uma exclusão.")
                with self.lock:
                    self.pending_deletions.discard(live_id)
                    self._save_queue_locked()

            self._emit("synced", "Peças não vendidas sincronizadas.")
        except SyncAuthError as exc:
            with self.lock:
                self.session = None
                try:
                    self.session_path.unlink(missing_ok=True)
                except OSError:
                    pass
            self._emit("auth_required", str(exc))
        except SyncTemporaryError as exc:
            self._emit("pending", str(exc))
            self._schedule_flush(RETRY_SECONDS)
        finally:
            with self.lock:
                self.flushing = False
                has_more = bool(self.pending_lives or self.pending_deletions)
            if has_more and self.is_authenticated and self.status == "synced":
                self._schedule_flush(0.5)

    def _emit(self, status, detail):
        self.status = status
        self.detail = detail
        if self.status_callback:
            try:
                self.status_callback(status, detail, self.username)
            except Exception:
                pass

    def _load_queue(self):
        value = _read_json(self.queue_path) or {}
        lives = value.get("lives", {})
        deletions = value.get("deletions", [])
        if not isinstance(lives, dict):
            lives = {}
        if not isinstance(deletions, list):
            deletions = []
        valid_lives = {
            str(live_id): payload
            for live_id, payload in lives.items()
            if isinstance(live_id, str) and isinstance(payload, dict)
        }
        valid_deletions = {
            str(live_id).strip() for live_id in deletions if str(live_id).strip()
        }
        return valid_lives, valid_deletions

    def _save_queue_locked(self):
        value = {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "lives": self.pending_lives,
            "deletions": sorted(self.pending_deletions),
        }
        _atomic_write(
            self.queue_path,
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        )

    def _load_session(self):
        try:
            encoded = self.session_path.read_bytes()
            protected = base64.b64decode(encoded, validate=True)
            value = json.loads(_unprotect_windows(protected).decode("utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _save_session_locked(self):
        if not self.session:
            return
        serialized = json.dumps(self.session, ensure_ascii=False).encode("utf-8")
        protected = _protect_windows(serialized)
        _atomic_write(self.session_path, base64.b64encode(protected), binary=True)
