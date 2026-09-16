"""Supabase is authoritative; only unacknowledged changes are stored on disk.

The durable outbox is protected by Windows DPAPI. The legacy JSON files are
import inputs, never an ongoing cache. Network callbacks run outside Tk's thread.
"""

import base64
import copy
import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from supabase_sync import (
    SESSION_FILENAME, RETRY_SECONDS, SupabaseHistorySync, SyncError, SyncAuthError,
    SyncTemporaryError, _atomic_write, _protect_windows, _unprotect_windows,
    load_sync_config, prepare_live_payload,
)


OUTBOX_FILENAME = "cloud_pendencias.dat"
MIGRATION_FILENAME = "cloud_migracao.json"
REFRESH_SECONDS = 60


class CloudConflictError(SyncTemporaryError):
    pass


class CloudDeletedError(SyncTemporaryError):
    pass


class CloudStorageError(SyncTemporaryError):
    pass


class CloudInactiveError(SyncAuthError):
    pass


def _clone(value):
    return copy.deepcopy(value)


def _live_id(value):
    return value if isinstance(value, str) and 0 < len(value.strip()) <= 200 else None


class CloudLiveStore(SupabaseHistorySync):
    """RAM read model plus an encrypted, crash-safe, revision-aware outbox.

    ``ready`` also permits recovering unsent work offline; ``cloud_loaded`` is
    the separate guarantee that the full history was actually read remotely.
    A True enqueue return means durable locally, never confirmation from cloud.
    """

    def __init__(self, app_dir, source_dir=None, status_callback=None,
                 data_callback=None):
        # Deliberately do not call the legacy constructor: its JSON outbox must
        # not be loaded or flushed by this new all-data persistence layer.
        self.app_dir = Path(app_dir)
        self.config = load_sync_config(self.app_dir, source_dir)
        self.status_callback = status_callback
        self.data_callback = data_callback
        self.session_path = self.app_dir / SESSION_FILENAME
        self.queue_path = self.app_dir / OUTBOX_FILENAME
        self.migration_path = self.app_dir / MIGRATION_FILENAME
        self.lock = threading.RLock()
        self.timer = None
        self.flushing = False
        self.closed = False
        self.session = self._load_session()
        self.status = "starting"
        self.detail = "Preparando os dados no Supabase."
        self._remote = {}
        self._pending = {}
        self._cloud_loaded = False
        self._is_admin = False
        self._storage_blocked = False
        self._auth_blocked = False
        self._legacy = None
        self._legacy_error = None
        self._legacy_ids = set()
        self._legacy_resolved = set()
        self._legacy_draft_id = None
        self._bootstrap_requested = False
        self._editing_versions = {}
        self._deleted_ids = set()
        self._last_pull = 0.0
        self._last_profile = 0.0
        self._force_pull = True
        self._force_profile = True
        self._generation = 0
        self._migration_done = self._read_migration_marker()
        self._read_outbox()

    @property
    def cloud_loaded(self):
        with self.lock:
            return self._cloud_loaded

    @property
    def ready(self):
        with self.lock:
            return (not self._storage_blocked and not self._auth_blocked
                    and (self._cloud_loaded or bool(self._pending)))

    @property
    def has_pending(self):
        with self.lock:
            return bool(self._pending)

    @property
    def needs_legacy_bootstrap(self):
        with self.lock:
            return not self._migration_done

    @property
    def is_admin(self):
        with self.lock:
            return self._is_admin and self.is_authenticated

    def state_pending(self, live_id):
        with self.lock:
            entry = self._pending.get(live_id, {})
            return entry.get("tipo") == "save" and bool(entry.get("documento", {}).get("estado"))

    def is_deleted(self, live_id):
        with self.lock:
            return (live_id in self._deleted_ids
                    or self._pending.get(live_id, {}).get("conflito") == "excluida")

    def begin_edit(self, live_id, state=None, history=None):
        """Pin the shown revision so a background pull cannot bless stale edits.

        Pass the exact snapshot shown by the GUI. A snapshot that became stale
        before opening is deliberately pinned at zero, causing a safe conflict.
        """
        with self.lock:
            if live_id in self._pending:
                version = self._pending[live_id]["versao_esperada"]
            else:
                remote = self._remote.get(live_id)
                version = remote["versao"] if remote else 0
                if remote and ((state is not None and state != remote["documento"].get("estado"))
                               or (history is not None and history != remote["documento"].get("historico"))):
                    version = 0
            self._editing_versions[live_id] = version

    def end_edit(self, live_id=None):
        with self.lock:
            if live_id is None:
                self._editing_versions.clear()
            else:
                self._editing_versions.pop(live_id, None)

    def remote_document(self, live_id):
        with self.lock:
            return _clone(self._remote.get(live_id, {}).get("documento"))

    def _documents_locked(self):
        documents = {key: _clone(item["documento"]) for key, item in self._remote.items()}
        for key, item in self._pending.items():
            # A delete never hides data until the server confirms it.
            if item.get("documento"):
                documents[key] = _clone(item["documento"])
        return documents

    def history(self):
        with self.lock:
            result = [doc["historico"] for doc in self._documents_locked().values()
                      if isinstance(doc.get("historico"), dict)]
        return sorted(result, key=lambda item: str(item.get("finished_at", "")), reverse=True)

    def current_states(self):
        with self.lock:
            documents = self._documents_locked().values()
            return [doc["estado"] for doc in documents if self._is_current(doc)]

    def pending_states(self):
        with self.lock:
            return [_clone(item["documento"]["estado"]) for item in self._pending.values()
                    if item.get("tipo") == "save" and self._is_current(item["documento"])]

    @staticmethod
    def _is_current(document):
        state = document.get("estado")
        return (isinstance(state, dict) and not state.get("live", {}).get("finished_at"))

    def conflicts(self):
        with self.lock:
            return [{"id": key, "motivo": item["conflito"],
                     "documento": _clone(item["documento"])}
                    for key, item in self._pending.items() if item.get("conflito")]

    def _notify_data(self):
        if self.data_callback and not self.closed:
            try:
                self.data_callback()
            except Exception:
                pass

    def _project_fingerprint(self):
        return hashlib.sha256(self.config.url.encode()).hexdigest() if self.config else None

    def _read_outbox(self):
        if not self.queue_path.exists():
            return
        try:
            raw = _unprotect_windows(base64.b64decode(self.queue_path.read_bytes(), validate=True))
            value = json.loads(raw.decode("utf-8"))
            if (not isinstance(value, dict) or value.get("version") != 1
                    or not isinstance(value.get("pending"), dict)):
                raise ValueError("Invalid outbox")
            project = value.get("project")
            if project and self.config and project != self._project_fingerprint():
                raise ValueError("Different project")
            for key, item in value["pending"].items():
                if not _live_id(key) or not isinstance(item, dict):
                    raise ValueError("Invalid item")
                self._validate_document(key, item.get("documento"))
                if (item.get("tipo") not in {"save", "delete"}
                        or type(item.get("versao_esperada")) is not int
                        or item["versao_esperada"] < 0):
                    raise ValueError("Invalid operation")
                uuid.UUID(item["operacao_id"])
                if item.get("enviado") is not None:
                    self._validate_document(key, item["enviado"])
            self._pending = value["pending"]
            self._legacy_ids = set(value.get("legacy_ids", []))
            self._legacy_resolved = set(value.get("legacy_resolved", []))
            self._legacy_draft_id = _live_id(value.get("legacy_draft_id"))
            self._bootstrap_requested = bool(self._legacy_ids or self._legacy_resolved)
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, AttributeError):
            self._storage_blocked = True
            self.status = "error"
            self.detail = ("Não foi possível abrir a fila protegida deste Windows. "
                           "Ela foi preservada; não crie alterações antes de recuperá-la.")

    def _persist_locked(self, pending, legacy_ids=None, legacy_resolved=None):
        if self._storage_blocked:
            raise CloudStorageError(self.detail)
        legacy_ids = self._legacy_ids if legacy_ids is None else legacy_ids
        legacy_resolved = self._legacy_resolved if legacy_resolved is None else legacy_resolved
        try:
            if pending or legacy_ids or legacy_resolved:
                value = {"version": 1, "project": self._project_fingerprint(),
                         "pending": pending, "legacy_ids": sorted(legacy_ids),
                         "legacy_resolved": sorted(legacy_resolved),
                         "legacy_draft_id": self._legacy_draft_id}
                encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
                protected = _protect_windows(encoded)
                _atomic_write(self.queue_path, base64.b64encode(protected), binary=True)
            else:
                self.queue_path.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError) as exc:
            raise CloudStorageError(
                "Não foi possível proteger a alteração neste Windows. "
                "Ela ainda não foi salva; mantenha o aplicativo aberto e tente novamente."
            ) from exc

    @staticmethod
    def _validate_document(live_id, document):
        if not isinstance(document, dict) or set(document) != {"estado", "historico"}:
            raise ValueError("Invalid document")
        state, history = document["estado"], document["historico"]
        if state is None and history is None:
            raise ValueError("Empty document")
        if state is not None and (not isinstance(state, dict)
                                  or not isinstance(state.get("live"), dict)
                                  or state["live"].get("id") != live_id
                                  or not isinstance(state.get("rows"), list)):
            raise ValueError("Invalid state")
        if history is not None and (not isinstance(history, dict)
                                    or history.get("id") != live_id
                                    or prepare_live_payload(history) is None):
            raise ValueError("Invalid history")
        # Reject non-JSON objects and NaN before they reach the encrypted outbox.
        json.dumps(document, ensure_ascii=False, allow_nan=False)

    def _new_entry_locked(self, live_id, document):
        self._validate_document(live_id, document)
        current = self._pending.get(live_id)
        if current:
            if current["tipo"] == "delete":
                raise CloudStorageError("A exclusão desta live ainda está aguardando confirmação.")
            updated = _clone(current)
            updated["documento"] = _clone(document)
            return updated
        return {"tipo": "save", "documento": _clone(document), "enviado": None,
                "versao_esperada": self._editing_versions.get(
                    live_id, self._remote.get(live_id, {}).get("versao", 0)),
                "operacao_id": str(uuid.uuid4()), "conflito": None}

    def queue_state(self, data, history=None):
        try:
            live_id = _live_id(data.get("live", {}).get("id"))
            if not live_id:
                raise ValueError("Missing id")
            with self.lock:
                document = self._documents_locked().get(live_id, {"estado": None, "historico": None})
                document["estado"] = _clone(data)
                if history is not None:
                    document["historico"] = _clone(history)
                return self._queue_documents_locked({live_id: document})
        except (ValueError, TypeError, AttributeError):
            self._emit("error", "A live está incompleta e não pôde ser salva. Confira os dados.")
            return False

    def queue_history(self, lives, immediate=False):
        if not isinstance(lives, list):
            self._emit("error", "O histórico informado não é válido.")
            return False
        try:
            with self.lock:
                documents = self._documents_locked()
                changes = {}
                for history in lives:
                    live_id = _live_id(history.get("id")) if isinstance(history, dict) else None
                    if not live_id:
                        raise ValueError("Missing id")
                    document = _clone(documents.get(live_id, {"estado": None, "historico": None}))
                    document["historico"] = _clone(history)
                    changes[live_id] = document
                return self._queue_documents_locked(changes, immediate=immediate)
        except (ValueError, TypeError):
            self._emit("error", "O histórico está incompleto e não pôde ser salvo.")
            return False

    def _queue_documents_locked(self, documents, immediate=False):
        if self._auth_blocked:
            self._emit("auth_required", "Esta conta não está ativa. Entre com uma conta autorizada antes de editar.")
            return False
        pending = _clone(self._pending)
        current = self._documents_locked()
        changed = False
        try:
            for live_id, document in documents.items():
                if live_id in self._deleted_ids and live_id not in self._pending:
                    raise CloudStorageError("Esta live foi excluída do Supabase. Exporte a folha se precisar e inicie uma nova live.")
                self._validate_document(live_id, document)
                if document != current.get(live_id):
                    pending[live_id] = self._new_entry_locked(live_id, document)
                    changed = True
            if not changed:
                return True
            self._persist_locked(pending)
            self._pending = pending
        except CloudStorageError as exc:
            self._emit("error", str(exc))
            return False
        self._emit_pending()
        self._notify_data()
        if self.is_authenticated:
            self._schedule_flush(0 if immediate else 1.5)
        return True

    def queue_delete(self, live_id, immediate=False):
        with self.lock:
            if not self.is_admin or not self._cloud_loaded:
                self._emit("error", "Somente uma administradora conectada pode excluir lives.")
                return False
            if not _live_id(live_id):
                return False
            document = self._documents_locked().get(live_id)
            if document is None:
                return False
            existing = self._pending.get(live_id)
            if existing and existing.get("enviado") is not None:
                self._emit("pending", "Aguarde o salvamento pendente antes de excluir esta live.")
                return False
            pending = _clone(self._pending)
            pending[live_id] = {"tipo": "delete", "documento": document, "enviado": None,
                                "versao_esperada": self._remote.get(live_id, {}).get("versao", 0),
                                "operacao_id": str(uuid.uuid4()), "conflito": None}
            try:
                self._persist_locked(pending)
            except CloudStorageError as exc:
                self._emit("error", str(exc))
                return False
            self._pending = pending
            self._force_profile = True
        self._emit("pending", "Exclusão aguardando confirmação do Supabase; a live continua visível.")
        self._schedule_flush(0 if immediate else 0.5)
        return True

    def discard_conflict(self, live_id):
        """Explicit user decision: discard only this retained local conflict."""
        with self.lock:
            current = self._pending.get(live_id)
            if not current or not current.get("conflito"):
                return False
            pending = _clone(self._pending)
            pending.pop(live_id)
            legacy_ids = self._legacy_ids - {live_id}
            resolved = self._legacy_resolved | ({live_id} if live_id in self._legacy_ids else set())
            try:
                self._persist_locked(pending, legacy_ids, resolved)
            except CloudStorageError as exc:
                self._emit("error", str(exc))
                return False
            self._pending, self._legacy_ids = pending, legacy_ids
            self._legacy_resolved = resolved
        try:
            self._finish_migration_if_confirmed()
        except CloudStorageError as exc:
            self._emit("error", str(exc))
        self._notify_data()
        self.sync_now()
        return True

    def _read_migration_marker(self):
        try:
            value = json.loads(self.migration_path.read_text(encoding="utf-8"))
            return (isinstance(value, dict) and value.get("version") == 1
                    and value.get("completed") is True
                    and value.get("project") == self._project_fingerprint())
        except (OSError, ValueError):
            return False

    def bootstrap_legacy(self, history, state, error=None):
        """Stage one-time import; never upload legacy data before a remote read."""
        with self.lock:
            if self._migration_done:
                return
            self._bootstrap_requested = True
            self._legacy_error = ("Os arquivos antigos não puderam ser lidos. A migração não foi concluída; os backups foram preservados."
                                  if error else None)
            self._legacy = (_clone(history) if isinstance(history, list) else [],
                            _clone(state) if isinstance(state, dict) else None)

    def _bootstrap_after_pull(self):
        with self.lock:
            if self._migration_done or self._legacy is None:
                return
            if self._legacy_error:
                raise CloudStorageError(self._legacy_error)
            history, state = self._legacy
            provided = {}
            for record in history:
                key = _live_id(record.get("id")) if isinstance(record, dict) else None
                if not key:
                    raise CloudStorageError("O histórico antigo contém uma live sem identificação válida. O backup foi preservado para revisão.")
                provided[key] = {"estado": None, "historico": record}
            key = _live_id(state.get("live", {}).get("id")) if state else None
            if state and not key and any(
                any(str(value or "").strip() for value in row.values())
                for row in state.get("rows", []) if isinstance(row, dict)
            ):
                state = _clone(state)
                key = self._legacy_draft_id or f"live_{uuid.uuid4().hex}"
                self._legacy_draft_id = key
                state.setdefault("live", {})["id"] = key
            if key:
                document = provided.setdefault(key, {"estado": None, "historico": None})
                document["estado"] = state
            pending = _clone(self._pending)
            ids = set(self._legacy_ids)
            staged = False
            for key, legacy in provided.items():
                if key in self._pending or key in self._legacy_resolved:
                    continue
                try:
                    self._validate_document(key, legacy)
                    remote = self._remote.get(key)
                    if remote:
                        document = _clone(remote["documento"])
                        differs = False
                        for part, value in legacy.items():
                            if value is None:
                                continue
                            candidate, known = _clone(value), _clone(document[part])
                            if part == "estado":
                                candidate.pop("updated_at", None)
                                if isinstance(known, dict):
                                    known.pop("updated_at", None)
                            if candidate != known:
                                differs = True
                                document[part] = _clone(value)
                        if not differs:
                            continue
                        entry = self._new_entry_locked(key, document)
                        entry["conflito"] = "migracao"
                    else:
                        entry = self._new_entry_locked(key, legacy)
                    pending[key] = entry
                    ids.add(key)
                    staged = True
                except (TypeError, ValueError) as exc:
                    raise CloudStorageError(
                        "O backup antigo contém uma live inválida. Ele foi preservado para revisão."
                    ) from exc
            if staged:
                self._persist_locked(pending, ids)
                self._pending, self._legacy_ids = pending, ids
            self._legacy = None
        if staged:
            self._notify_data()
        self._finish_migration_if_confirmed()

    def _finish_migration_if_confirmed(self):
        with self.lock:
            if (self._migration_done or not self._bootstrap_requested
                    or self._legacy is not None or not self._cloud_loaded):
                return
            if self._legacy_ids & self._pending.keys():
                return
            try:
                _atomic_write(self.migration_path, json.dumps({"version": 1, "completed": True,
                              "project": self._project_fingerprint()}) + "\n")
                self._persist_locked(self._pending, set(), set())
            except OSError as exc:
                raise CloudStorageError("Não foi possível confirmar a migração local. Os backups foram preservados.") from exc
            self._migration_done = True
            self._legacy_ids.clear()
            self._legacy_resolved.clear()
            self._legacy_draft_id = None

    def start(self):
        if self._storage_blocked:
            self._emit("error", self.detail)
        elif not self.config:
            self._emit("error", "O Supabase não foi configurado neste aplicativo.")
        elif not self.is_authenticated:
            self._emit("auth_required", "Entre para carregar os dados do Supabase e enviar as pendências.")
        else:
            self._emit("loading", "Carregando o histórico do Supabase.")
            self._schedule_flush(0.2)

    def sync_now(self):
        with self.lock:
            self._force_pull = True
            self._force_profile = True
        if self._storage_blocked or not self.config or not self.is_authenticated:
            self.start()
        else:
            self._schedule_flush(0)

    def logout(self):
        with self.lock:
            self._generation += 1
            self._remote = {}
            self._cloud_loaded = False
            self._is_admin = False
            self._editing_versions.clear()
            self._deleted_ids.clear()
            self._force_pull = True
            self._force_profile = True
            if self.timer:
                self.timer.cancel()
                self.timer = None
        super().logout()
        self._emit("auth_required", "Conta desconectada. Somente alterações ainda pendentes foram preservadas e protegidas.")
        self._notify_data()

    def login_async(self, username, password, completion=None):
        if not self.config:
            if completion:
                completion(False, "O Supabase não foi configurado neste aplicativo.")
            return
        with self.lock:
            self._generation += 1
            generation = self._generation
            self._is_admin = False
            self._cloud_loaded = False
            self._remote = {}
            self._force_pull = True
            self._force_profile = True
        username = str(username or "").strip().lower()

        def worker():
            try:
                response = self._request_json("/auth/v1/token?grant_type=password", {
                    "email": self._username_to_email(username), "password": str(password or "")})
                session = self._session_from_response(response, username)
                with self.lock:
                    if generation != self._generation or self.closed:
                        return
                    self.session = session
                    try:
                        self._save_session_locked()
                    except OSError as exc:
                        self.session = None
                        raise SyncAuthError("O Windows não conseguiu proteger a sessão neste computador.") from exc
                self._emit("loading", "Conta conectada. Carregando os dados do Supabase.")
                self._schedule_flush(0)
                if completion:
                    completion(True, "Conta conectada com segurança.")
            except SyncError as exc:
                with self.lock:
                    if generation != self._generation or self.closed:
                        return
                self._emit("auth_required", str(exc))
                if completion:
                    completion(False, str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_session(self):
        with self.lock:
            current = dict(self.session or {})
            generation = self._generation
        if not current.get("refresh_token"):
            raise SyncAuthError("Entre novamente para continuar sincronizando.")
        response = self._request_json("/auth/v1/token?grant_type=refresh_token", {
            "refresh_token": current["refresh_token"]})
        refreshed = self._session_from_response(response, current.get("username", ""))
        with self.lock:
            if generation != self._generation or self.closed:
                raise SyncAuthError("A conta foi desconectada.")
            self.session = refreshed
            try:
                self._save_session_locked()
            except OSError as exc:
                raise SyncTemporaryError("Não foi possível atualizar a sessão protegida neste computador.") from exc
        return refreshed["access_token"]

    def _request_json(self, path, payload, access_token=None):
        """Classify only known error codes; never expose arbitrary server text."""
        body = None if payload is None else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        headers = {"apikey": self.config.publishable_key, "Content-Type": "application/json"}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        request = urllib.request.Request(f"{self.config.url}{path}", data=body,
                                         headers=headers, method="GET" if payload is None else "POST")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                text = response.read().decode("utf-8")
                return json.loads(text) if text else None
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8"))
                message = str(body.get("message", "")) if isinstance(body, dict) else ""
            except (ValueError, UnicodeError):
                message = ""
            if "LIVE_EXCLUIDA" in message:
                raise CloudDeletedError("Esta live já foi excluída no Supabase.") from exc
            if "LIVE_CONFLITO" in message:
                raise CloudConflictError("Outra versão desta live foi salva. A alteração local foi preservada para revisão.") from exc
            if exc.code in {401, 403} or (path.startswith("/auth/v1/") and exc.code == 400):
                raise SyncAuthError("Entre novamente com uma conta ativa para continuar.") from exc
            if "schema cache" in message.lower() or exc.code == 404:
                raise SyncTemporaryError("A atualização dos dados completos ainda não foi aplicada no Supabase.") from exc
            raise SyncTemporaryError("O Supabase não confirmou a gravação. A alteração continua pendente.") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SyncTemporaryError("Aguardando conexão. As alterações pendentes continuam protegidas neste computador.") from exc
        except (ValueError, UnicodeError) as exc:
            raise SyncTemporaryError("O Supabase respondeu de forma inesperada; nenhum envio foi dado como confirmado.") from exc

    def _load_profile(self):
        token = self._access_token()
        user = self._request_json("/auth/v1/user", None, token)
        try:
            user_id = str(uuid.UUID(user["id"]))
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise SyncAuthError("O servidor não confirmou a identidade da conta.") from exc
        profile = self._request_json(
            f"/rest/v1/funcionarias?id=eq.{user_id}&select=id,tipo,ativa", None, token)
        if (not isinstance(profile, list) or len(profile) != 1
                or not isinstance(profile[0], dict)
                or profile[0].get("id") != user_id or profile[0].get("ativa") is not True):
            raise CloudInactiveError("Esta conta não está ativa para acessar as lives.")
        return profile[0].get("tipo") == "administrador"

    def _valid_envelope(self, item):
        if not isinstance(item, dict) or not _live_id(item.get("id")):
            raise SyncTemporaryError("O servidor retornou uma live inválida.")
        if type(item.get("versao")) is not int or item["versao"] < 1:
            raise SyncTemporaryError("O servidor não confirmou a versão da live.")
        try:
            uuid.UUID(item["ultima_operacao"])
            self._validate_document(item["id"], item.get("documento"))
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise SyncTemporaryError("O servidor retornou dados incompletos da live.") from exc
        return _clone(item)

    def _pull(self, generation):
        with self.lock:
            before = _clone(self._remote)
        versions = {}
        cursor = ""
        while True:
            batch = self._rpc("listar_versoes_lives", {"p_apos_id": cursor, "p_limite": 100})
            if not isinstance(batch, list) or len(batch) > 100:
                raise SyncTemporaryError("O servidor não confirmou o carregamento do histórico.")
            for item in batch:
                if (not isinstance(item, dict) or not _live_id(item.get("id"))
                        or type(item.get("versao")) is not int or item["versao"] < 1):
                    raise SyncTemporaryError("O servidor não confirmou as versões do histórico.")
                if item["id"] <= cursor:
                    raise SyncTemporaryError("A paginação do histórico não foi confirmada.")
                cursor = item["id"]
                versions[cursor] = item["versao"]
            if len(batch) < 100:
                break
        remote = {key: value for key, value in before.items() if key in versions}
        changed = [key for key, version in versions.items()
                   if key not in before or before[key]["versao"] != version]
        for start in range(0, len(changed), 100):
            keys = changed[start:start + 100]
            batch = self._rpc("obter_dados_lives", {"p_ids": keys})
            if not isinstance(batch, list) or len(batch) != len(keys):
                raise SyncTemporaryError("O histórico mudou durante a leitura. Tentaremos carregar novamente.")
            received = {}
            for item in batch:
                envelope = self._valid_envelope(item)
                key = envelope["id"]
                if (key not in keys or key in received or envelope["versao"] < versions[key]
                        or envelope["versao"] < before.get(key, {}).get("versao", 0)):
                    raise SyncTemporaryError("O servidor não confirmou a leitura consistente do histórico.")
                received[key] = envelope
            remote.update(received)
        with self.lock:
            if generation != self._generation or self.closed:
                return False
            self._remote = remote
            self._cloud_loaded = True
            self._deleted_ids.update(set(before) - set(remote))
            self._deleted_ids.difference_update(remote)
            self._last_pull = time.monotonic()
        self._notify_data()
        return True

    def _emit_pending(self):
        if self.conflicts():
            self._emit("conflict", "Há alterações de outro computador ou lives excluídas. A cópia pendente foi preservada para revisão.")
        else:
            self._emit("pending", "Alterações protegidas neste computador; aguardando confirmação do Supabase.")

    def _mark_conflict(self, live_id, reason):
        with self.lock:
            if live_id not in self._pending:
                return
            pending = _clone(self._pending)
            pending[live_id]["conflito"] = reason
            self._persist_locked(pending)
            self._pending = pending
        self._notify_data()

    def _send_one(self, live_id, generation):
        with self.lock:
            entry = self._pending.get(live_id)
            if not entry or entry.get("conflito") or generation != self._generation:
                return
            if entry["tipo"] == "save" and entry["enviado"] is None:
                pending = _clone(self._pending)
                pending[live_id]["enviado"] = _clone(entry["documento"])
                self._persist_locked(pending)
                self._pending = pending
            sent = _clone(self._pending[live_id])
        if sent["tipo"] == "delete":
            if not self.is_admin:
                raise SyncAuthError("A exclusão pendente exige uma administradora ativa.")
            confirmed = self._rpc("excluir_live_vendas_importada", {"p_live_id": live_id})
            if confirmed != live_id:
                raise SyncTemporaryError("O Supabase não confirmou a exclusão.")
            envelope = None
        else:
            document = sent["enviado"]
            envelope = self._valid_envelope(self._rpc("salvar_dados_live", {
                "p_live_id": live_id, "p_documento": document,
                "p_versao_esperada": sent["versao_esperada"],
                "p_operacao_id": sent["operacao_id"],
                "p_resumo": prepare_live_payload(document["historico"]) if document["historico"] else None,
            }))
            if (envelope["id"] != live_id
                    or envelope["versao"] != sent["versao_esperada"] + 1
                    or envelope["ultima_operacao"] != sent["operacao_id"]
                    or envelope["documento"] != document):
                raise SyncTemporaryError("O Supabase não confirmou exatamente a alteração enviada.")
        with self.lock:
            if generation != self._generation or self.closed:
                return
            current = self._pending.get(live_id)
            if not current or current["operacao_id"] != sent["operacao_id"]:
                return
            pending = _clone(self._pending)
            if sent["tipo"] == "delete" or current["documento"] == sent["enviado"]:
                pending.pop(live_id)
            else:
                pending[live_id] = {**current, "versao_esperada": envelope["versao"],
                                    "operacao_id": str(uuid.uuid4()), "enviado": None}
            legacy_ids = self._legacy_ids - ({live_id} if live_id not in pending else set())
            resolved = self._legacy_resolved | (
                {live_id} if live_id in self._legacy_ids and live_id not in pending else set())
            # If this write fails, keep the original immutable request for retry.
            self._persist_locked(pending, legacy_ids, resolved)
            self._pending, self._legacy_ids = pending, legacy_ids
            self._legacy_resolved = resolved
            if envelope:
                self._remote[live_id] = envelope
                if self._editing_versions.get(live_id) == sent["versao_esperada"]:
                    self._editing_versions[live_id] = envelope["versao"]
            else:
                self._remote.pop(live_id, None)
                self._deleted_ids.add(live_id)
        self._notify_data()

    def _flush(self):
        with self.lock:
            self.timer = None
            if self.closed or self.flushing or self._storage_blocked:
                return
            self.flushing = True
            generation = self._generation
            refresh = self._force_pull or not self._cloud_loaded or time.monotonic() - self._last_pull >= REFRESH_SECONDS
            profile_due = self._force_profile or not self._cloud_loaded or time.monotonic() - self._last_profile >= REFRESH_SECONDS
            self._force_pull = False
            self._force_profile = False
        success = False
        try:
            if not self.config or not self.is_authenticated:
                self._emit("auth_required", "Entre para carregar os dados e enviar as pendências.")
                return
            self._emit("syncing" if self.cloud_loaded else "loading", "Conferindo os dados e as permissões no Supabase.")
            if profile_due:
                admin = self._load_profile()
                with self.lock:
                    if generation != self._generation or self.closed:
                        return
                    self._is_admin = admin
                    self._auth_blocked = False
                    self._last_profile = time.monotonic()
            if refresh and not self._pull(generation):
                return
            self._bootstrap_after_pull()
            # Bounded pass avoids starving pulls while a live is edited constantly.
            with self.lock:
                ids = list(self._pending)
            for live_id in ids:
                with self.lock:
                    if generation != self._generation or self.closed:
                        return
                try:
                    self._send_one(live_id, generation)
                except CloudDeletedError:
                    with self.lock:
                        self._deleted_ids.add(live_id)
                        bootstrap = live_id in self._legacy_ids
                    if bootstrap:
                        # A legacy backup cannot resurrect an administrator's deletion.
                        self._mark_conflict(live_id, "excluida")
                        self.discard_conflict(live_id)
                    else:
                        self._mark_conflict(live_id, "excluida")
                except CloudConflictError:
                    self._mark_conflict(live_id, "versao")
            with self.lock:
                if generation != self._generation or self.closed:
                    return
            if self.conflicts():
                self._pull(generation)
            self._finish_migration_if_confirmed()
            success = True
            if self.has_pending:
                self._emit_pending()
            else:
                self._emit("synced", "Salvo no Supabase. Histórico atualizado; nenhuma alteração pendente.")
        except CloudStorageError as exc:
            self._emit("error", str(exc))
        except CloudInactiveError as exc:
            with self.lock:
                self._is_admin = False
                self._auth_blocked = True
                self._cloud_loaded = False
                self._remote = {}
            self._emit("auth_required", str(exc))
            self._notify_data()
        except SyncAuthError as exc:
            with self.lock:
                self._is_admin = False
            self._emit("auth_required", str(exc))
        except SyncTemporaryError as exc:
            if self.conflicts():
                self._emit("conflict", "Há conflitos preservados neste computador. A conexão com o Supabase não foi confirmada.")
            else:
                suffix = " O histórico completo ainda não foi carregado." if not self.cloud_loaded else ""
                self._emit("pending" if self.has_pending else "error", str(exc) + suffix)
        finally:
            with self.lock:
                self.flushing = False
                keep_running = not self.closed and generation == self._generation
                more = any(not item.get("conflito") for item in self._pending.values())
            if keep_running and self.is_authenticated and self.status != "auth_required":
                self._schedule_flush(1 if success and more else RETRY_SECONDS)
