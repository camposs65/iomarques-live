import json
import math
import os
import queue
import random
import subprocess
import sys
import tempfile
import textwrap
import tkinter as tk
import time
import uuid
import ctypes
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from ctypes import wintypes

from cloud_store import CloudLiveStore

try:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
except ImportError:
    Workbook = None

from roguelike_game import RogueBrechoGame


APP_VERSION = "1.7.2"

COLUMNS = ("valor", "codigo", "cliente", "suplente", "tempo")
HEADERS = {
    "valor": "Valor",
    "codigo": "Código",
    "cliente": "Cliente",
    "suplente": "Suplente",
    "tempo": "Tempo",
}
COLUMN_WIDTHS = (130, 110, 250, 250, 115)
INDEX_COLUMN_WIDTH = 68

VALUE_COL = COLUMNS.index("valor")
CODE_COL = COLUMNS.index("codigo")
CLIENT_COL = COLUMNS.index("cliente")
SUPLENTE_COL = COLUMNS.index("suplente")
TIME_COL = COLUMNS.index("tempo")
CLEARABLE_CLIENT_COLS = (CLIENT_COL, SUPLENTE_COL)

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
else:
    APP_DIR = Path(__file__).resolve().parent
    RESOURCE_DIR = APP_DIR
AUTOSAVE_PATH = APP_DIR / "live_atual.json"
HISTORY_PATH = APP_DIR / "historico_lives.json"
ASSETS_DIR = RESOURCE_DIR / "assets"
BACKUP_DIR = APP_DIR / "backups"
AUTOSAVE_BACKUP_PATH = BACKUP_DIR / "live_atual.bak.json"
HISTORY_BACKUP_PATH = BACKUP_DIR / "historico_lives.bak.json"
AUTOSAVE_INTERVAL_MS = 10_000
SUMMARY_SEPARATOR = "-------------------------------------------------------------"
CHECKBOX_TEXT = "[ ]"
REPORT_TWO_COLUMN_MIN_LINES = 38
REPORT_PRINT_WIDTH_ONE_COLUMN = 90
REPORT_PRINT_WIDTH_TWO_COLUMNS = 36
DELIVERY_PRINT_ASSET = "entregas.png"
EVALUATION_PRINT_ASSET = "avaliacoes.pdf"
PIX_MESSAGE_TEXT = "\n".join(
    [
        "Chave pix: 51984854922 (celular)",
        "Io Marques",
        "Banco: Inter",
    ]
)

COLORS = {
    "app_bg": "#F9F0F5",
    "primary": "#7B3F7A",
    "secondary": "#EDD6ED",
    "text": "#2C1040",
    "button_text": "#4A2060",
    "table_bg": "#FFFFFF",
    "table_header": "#F3F3F3",
    "table_header_text": "#2C1040",
    "grid": "#D7D0D7",
    "sold_row": "#EAF7EA",
    "active_border": "#7B3F7A",
    "timer_bg": "#FFFFFF",
    "warning_bg": "#FFF1D6",
    "warning_text": "#7A2E0E",
    "duplicate_cell": "#FFE4E1",
}


def find_message_automation_app(app_dir, user_home):
    """Locate the optional companion app without depending on the checkout location."""
    app_dir = Path(app_dir)
    candidates = []
    try:
        installation = json.loads((app_dir / "instalacao.json").read_text(encoding="utf-8"))
        value = installation.get("automation_app") if isinstance(installation, dict) else None
        if isinstance(value, str) and value.strip():
            configured = Path(value)
            if configured.is_absolute() and configured.name.lower() == "app.py" and configured.is_file():
                candidates.append(configured)
    except (OSError, UnicodeError, ValueError):
        # Installation metadata is optional; legacy discovery remains available.
        pass
    candidates.extend([
        Path(user_home) / "iomarques-instagram-direct" / "app.py",
        app_dir.parent / "iomarques-instagram-direct" / "app.py",
        app_dir.parent.parent / "iomarques-instagram-direct" / "app.py",
    ])
    return next((candidate for candidate in candidates if candidate.is_file()), None)


class LiveSalesApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"IoMarques Brechó - Controle de Vendas da Live · v{APP_VERSION}")
        self.geometry("1160x720")
        self.minsize(960, 580)

        self.row_vars = []
        self.index_frames = []
        self.index_labels = []
        self.time_check_vars = []
        self.time_check_buttons = []
        self.manual_time_vars = []
        self.cell_frames = []
        self.cell_entries = []
        self.clear_buttons = []
        self.active_cell = None
        self.hovered_cell = None
        self.undo_stack = []
        self.redo_stack = []
        self.restoring_rows = False
        self.max_undo_steps = 100
        self.search_var = tk.StringVar()
        self.filter_vars = [tk.StringVar() for _ in COLUMNS]
        self.filter_buttons = []

        self.live_running = False
        self.live_id = None
        self.live_first_started_at = None
        self.live_started_at = None
        self.live_finished_at = None
        self.live_elapsed_seconds = 0
        self.timer_job = None
        self.autosave_job = None
        self.logo_header_image = None
        self.logo_icon_image = None
        self.sync_manager = None
        self.sync_status = "starting"
        self.sync_detail = "Preparando sincronização."
        self.sync_username = ""
        self.sync_window = None
        self.sync_dialog_status_label = None
        self.cloud_events = queue.Queue()
        self.cloud_poll_job = None
        self.history_refreshers = {}
        self.sheet_dirty = False
        self.applying_cloud = False
        self.last_saved_sheet = None
        self.last_save_failed = False
        self.closing = False
        self.initial_pending_recovery = True

        self._setup_style()
        self._build_ui()

        self.sync_manager = CloudLiveStore(
            APP_DIR,
            Path(__file__).resolve().parent,
            self._handle_sync_status,
            self._handle_cloud_data,
        )

        self._add_row()
        if self.sync_manager.needs_legacy_bootstrap:
            try:
                history, saved_data = self._read_legacy_data()
                self.sync_manager.bootstrap_legacy(history, saved_data)
            except ValueError:
                self.sync_manager.bootstrap_legacy([], {}, error=(
                    "Não foi possível ler o histórico ou a live antiga, nem seu backup. "
                    "Os arquivos foram preservados; a migração precisa ser revisada."
                ))
        self._apply_cloud_data()
        self._poll_cloud_events()
        self.sync_manager.start()
        self._refresh_totals()
        self._refresh_live_controls()
        self._schedule_timer_tick()
        self._schedule_periodic_autosave()
        self._bind_shortcuts()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _asset_path(self, filename):
        path = ASSETS_DIR / filename
        return path if path.exists() else None

    def _load_photo(self, filename, subsample=None):
        path = self._asset_path(filename)
        if path is None:
            return None
        try:
            image = tk.PhotoImage(file=str(path))
            if subsample:
                image = image.subsample(subsample, subsample)
            return image
        except tk.TclError:
            return None

    def _setup_style(self):
        self.configure(bg=COLORS["app_bg"])

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            "Primary.TButton",
            background=COLORS["primary"],
            foreground="#FFFFFF",
            font=("Segoe UI Semibold", 10),
            padding=(14, 9),
            borderwidth=0,
            focusthickness=0,
        )
        style.map(
            "Primary.TButton",
            background=[("active", "#6C366B"), ("pressed", "#5D2F5D")],
            foreground=[("active", "#FFFFFF")],
        )
        style.configure(
            "Secondary.TButton",
            background=COLORS["secondary"],
            foreground=COLORS["button_text"],
            font=("Segoe UI Semibold", 10),
            padding=(14, 9),
            borderwidth=0,
            focusthickness=0,
        )
        style.map(
            "Secondary.TButton",
            background=[("active", "#E3C6E3"), ("pressed", "#D8B6D8")],
            foreground=[("active", COLORS["button_text"])],
        )

    def _handle_sync_status(self, status, detail, username):
        # O worker nunca chama Tk: a fila é consumida pelo thread da janela.
        self.cloud_events.put(("status", (status, detail, username)))

    def _handle_cloud_data(self, *_args):
        self.cloud_events.put(("data", None))

    def _poll_cloud_events(self):
        while True:
            try:
                kind, value = self.cloud_events.get_nowait()
            except queue.Empty:
                break
            if kind == "status":
                self._apply_sync_status(*value)
            elif kind == "data":
                self._apply_cloud_data()
            elif kind == "call":
                value()
        if not self.closing:
            self.cloud_poll_job = self.after(150, self._poll_cloud_events)

    def _apply_sync_status(self, status, detail, username):
        if self.last_save_failed and status not in ("auth_required", "not_configured"):
            status, detail = "error", "Há alterações na planilha que ainda não puderam ser protegidas. Não feche o programa."
        elif (status not in ("auth_required", "not_configured") and self.live_id
              and self.sync_manager and self.sync_manager.is_deleted(self.live_id)):
            status = "deleted"
            detail = "Esta live foi excluída do Supabase. A planilha está somente para consulta e exportação. Use Nova live para continuar."
        self.sync_status, self.sync_detail, self.sync_username = status, detail, username
        labels = {
            "not_configured": "Supabase não configurado",
            "auth_required": "Conectar ao Supabase",
            "pending": "Aguardando conexão",
            "syncing": "Sincronizando...",
            "synced": "Salvo no Supabase",
            "starting": "Carregando Supabase...",
            "loading": "Carregando Supabase...",
            "conflict": "Conflito: revisar alterações",
            "error": "Não foi possível salvar",
            "deleted": "Live excluída do Supabase",
        }
        self.sync_status_button.configure(
            text=labels.get(status, "Ver sincronização"),
            fg="#2E6F47" if status == "synced" else COLORS["warning_text"]
            if status in ("pending", "conflict", "error", "deleted", "not_configured") else COLORS["primary"],
        )
        if self.sync_dialog_status_label is not None and self.sync_dialog_status_label.winfo_exists():
            self.sync_dialog_status_label.configure(text=detail)
        self._refresh_cloud_editability()

    def _refresh_cloud_editability(self):
        editable = bool(self.sync_manager and self.sync_manager.ready
                        and not (self.live_id and self.sync_manager.is_deleted(self.live_id)))
        for entries in self.cell_entries:
            for entry in entries:
                entry.configure(state="normal" if editable else "disabled")
        for button in self.time_check_buttons:
            button.configure(state="normal" if editable else "disabled")
        for buttons in self.clear_buttons:
            for button in buttons:
                if button is not None:
                    button.configure(state="normal" if editable else "disabled")
        self.start_button.state(["!disabled" if editable and not self.live_running else "disabled"])
        self.finish_button.state(["!disabled" if editable and self.live_running else "disabled"])

    def _sheet_is_pristine(self):
        return not self.sheet_dirty and not self.live_id and not self._rows()

    def _apply_cloud_data(self):
        if self.sync_manager is None:
            return
        # Somente a fila do próprio computador é recuperada automaticamente.
        # Lives vindas de outro computador exigem escolha explícita da usuária.
        if self.initial_pending_recovery:
            self.initial_pending_recovery = False
            pending = self.sync_manager.pending_states()
            if self._sheet_is_pristine() and len(pending) == 1:
                self._apply_saved_state(pending[0])
        for window, refresh in list(self.history_refreshers.items()):
            if window.winfo_exists():
                refresh()
            else:
                self.history_refreshers.pop(window, None)
        self._refresh_cloud_editability()
        if self.live_id and self.sync_manager.is_deleted(self.live_id):
            self._apply_sync_status(self.sync_status, self.sync_detail, self.sync_username)

    def _apply_saved_state(self, data, history=None):
        old_id = self.live_id
        self.applying_cloud = True
        try:
            if self.timer_job is not None:
                self.after_cancel(self.timer_job)
                self.timer_job = None
            self._reset_sheet_widgets()
            self._restore_live_state(data)
            if not self._load_saved_rows(data):
                self._add_row()
            self.undo_stack.clear()
            self.redo_stack.clear()
            self.sheet_dirty = False
            self.last_saved_sheet = self._sheet_data()
            self.last_save_failed = False
            self._refresh_totals()
            self._refresh_live_controls()
            self._schedule_timer_tick()
            if old_id:
                self.sync_manager.end_edit(old_id)
            if self.live_id:
                if history is None:
                    self.sync_manager.begin_edit(self.live_id, state=data)
                else:
                    self.sync_manager.begin_edit(self.live_id, history=history)
        finally:
            self.applying_cloud = False

    def _reset_sheet_widgets(self):
        for child in self.body_frame.winfo_children():
            child.destroy()
        for name in ("row_vars", "index_frames", "index_labels", "time_check_vars",
                     "time_check_buttons", "manual_time_vars", "cell_frames", "cell_entries", "clear_buttons"):
            getattr(self, name).clear()
        self.active_cell = self.hovered_cell = None

    def open_sync_dialog(self):
        if self.sync_window is not None and self.sync_window.winfo_exists():
            self.sync_window.lift()
            self.sync_window.focus_force()
            return

        window = tk.Toplevel(self)
        self.sync_window = window
        window.title(f"Dados no Supabase · v{APP_VERSION}")
        window.geometry("540x590")
        window.minsize(470, 440)
        window.configure(bg=COLORS["app_bg"])
        window.transient(self)

        def close_window():
            self.sync_dialog_status_label = None
            self.sync_window = None
            window.destroy()

        window.protocol("WM_DELETE_WINDOW", close_window)

        top = tk.Frame(window, bg=COLORS["primary"])
        top.pack(fill="x")
        tk.Label(
            top,
            text="Lives salvas no Supabase",
            bg=COLORS["primary"],
            fg="#FFFFFF",
            font=("Segoe UI Semibold", 17),
        ).pack(anchor="w", padx=22, pady=(17, 3))
        tk.Label(
            top,
            text="Integração com o Controle do Brechó",
            bg=COLORS["primary"],
            fg=COLORS["app_bg"],
            font=("Segoe UI", 10),
        ).pack(anchor="w", padx=22, pady=(0, 17))

        body = tk.Frame(window, bg=COLORS["app_bg"])
        body.pack(fill="both", expand=True, padx=24, pady=20)
        tk.Label(
            body,
            text=(
                "A live atual e o histórico completo ficam no Supabase, incluindo "
                "peças, clientes e suplentes. Se a conexão cair, somente as alterações "
                "pendentes ficam protegidas neste computador até o banco confirmar."
            ),
            bg=COLORS["app_bg"],
            fg=COLORS["text"],
            justify="left",
            wraplength=450,
            font=("Segoe UI", 10),
        ).pack(anchor="w")

        self.sync_dialog_status_label = tk.Label(
            body,
            text=self.sync_detail,
            bg=COLORS["secondary"],
            fg=COLORS["button_text"],
            justify="left",
            wraplength=420,
            padx=12,
            pady=9,
            font=("Segoe UI Semibold", 9),
        )
        self.sync_dialog_status_label.pack(fill="x", pady=(15, 16))

        if not self.sync_manager or not self.sync_manager.is_configured:
            tk.Label(
                body,
                text=(
                    "Este executável foi criado sem o arquivo de configuração da integração. "
                    "Gere novamente a pasta dist antes de levar o app para a loja."
                ),
                bg=COLORS["app_bg"],
                fg=COLORS["warning_text"],
                justify="left",
                wraplength=450,
                font=("Segoe UI", 10),
            ).pack(anchor="w")
            ttk.Button(
                body,
                text="Fechar",
                command=close_window,
                style="Secondary.TButton",
            ).pack(anchor="e", pady=(22, 0))
            return

        if self.sync_manager.is_authenticated and self.sync_status != "auth_required":
            account = self.sync_manager.username or self.sync_username
            tk.Label(
                body,
                text=f"Conta conectada: @{account}",
                bg=COLORS["app_bg"],
                fg=COLORS["text"],
                font=("Segoe UI Semibold", 11),
            ).pack(anchor="w")

            actions = tk.Frame(body, bg=COLORS["app_bg"])
            actions.pack(fill="x", pady=(22, 0))

            def sync_now():
                if not self._save_rows():
                    return
                self.sync_manager.sync_now()
                close_window()

            def disconnect():
                self._finish_active_cell()
                if not self._save_rows() or self.sync_manager.has_pending:
                    messagebox.showwarning(
                        "Envios pendentes",
                        "Aguarde a confirmação do Supabase antes de desconectar. "
                        "Você pode fechar o programa e recuperar a fila protegida depois.",
                        parent=window,
                    )
                    return
                if not messagebox.askyesno(
                    "Desconectar conta?",
                    "Os dados confirmados continuam no Supabase. A planilha será fechada neste computador.",
                    parent=window,
                ):
                    return
                self.sync_manager.logout()
                self._apply_saved_state({})
                for history_window in list(self.history_refreshers):
                    if history_window.winfo_exists():
                        history_window.destroy()
                self.history_refreshers.clear()
                close_window()

            ttk.Button(
                actions,
                text="Sincronizar agora",
                command=sync_now,
                style="Primary.TButton",
            ).pack(side="left")
            ttk.Button(
                actions,
                text="Desconectar",
                command=disconnect,
                style="Secondary.TButton",
            ).pack(side="right")
            ttk.Button(
                body, text="Retomar uma live / rascunho",
                command=self.show_cloud_lives, style="Secondary.TButton",
            ).pack(fill="x", pady=(15, 0))
            if self.sync_manager.conflicts():
                ttk.Button(
                    body, text="Revisar conflitos de sincronização",
                    command=self.show_cloud_conflicts, style="Secondary.TButton",
                ).pack(fill="x", pady=(10, 0))
            return

        form = tk.Frame(body, bg=COLORS["app_bg"])
        form.pack(fill="x")
        tk.Label(
            form,
            text="Usuário",
            bg=COLORS["app_bg"],
            fg=COLORS["text"],
            font=("Segoe UI Semibold", 10),
        ).pack(anchor="w")
        username_entry = tk.Entry(form, font=("Segoe UI", 11), relief="solid", bd=1)
        username_entry.pack(fill="x", ipady=6, pady=(4, 12))
        tk.Label(
            form,
            text="Senha",
            bg=COLORS["app_bg"],
            fg=COLORS["text"],
            font=("Segoe UI Semibold", 10),
        ).pack(anchor="w")
        password_entry = tk.Entry(form, show="•", font=("Segoe UI", 11), relief="solid", bd=1)
        password_entry.pack(fill="x", ipady=6, pady=(4, 14))

        login_button = ttk.Button(form, text="Conectar", style="Primary.TButton")
        login_button.pack(anchor="e")

        def finish_login(success, message):
            def update_dialog():
                if not window.winfo_exists():
                    return
                login_button.state(["!disabled"])
                self.sync_dialog_status_label.configure(text=message)
                if success:
                    close_window()

            self.cloud_events.put(("call", update_dialog))

        def login(_event=None):
            username = username_entry.get().strip()
            password = password_entry.get()
            if not username or not password:
                self.sync_dialog_status_label.configure(
                    text="Preencha o usuário e a senha."
                )
                return
            login_button.state(["disabled"])
            self.sync_dialog_status_label.configure(text="Conectando com segurança...")
            self.sync_manager.login_async(username, password, finish_login)

        login_button.configure(command=login)
        username_entry.bind("<Return>", lambda _event: password_entry.focus_set())
        password_entry.bind("<Return>", login)
        username_entry.focus_set()

    def _build_ui(self):
        self.brand_header = tk.Frame(self, bg=COLORS["primary"])
        self.brand_header.pack(fill="x")

        header_inner = tk.Frame(self.brand_header, bg=COLORS["primary"])
        header_inner.pack(fill="x", padx=24, pady=18)

        self.logo_header_image = self._load_photo("logo_round.png", subsample=4)
        if self.logo_header_image is not None:
            logo_label = tk.Label(header_inner, image=self.logo_header_image, bg=COLORS["primary"], bd=0)
            logo_label.pack(side="left", padx=(0, 14))

        self.logo_icon_image = self._load_photo("app_icon.png")
        if self.logo_icon_image is not None:
            self.iconphoto(True, self.logo_icon_image)

        title_block = tk.Frame(header_inner, bg=COLORS["primary"])
        title_block.pack(side="left", fill="x", expand=True)

        title = tk.Label(
            title_block,
            text="IoMarques Brechó",
            bg=COLORS["primary"],
            fg="#FFFFFF",
            font=("Segoe UI Semibold", 24),
        )
        title.pack(anchor="w")

        subtitle = tk.Label(
            title_block,
            text="controle de vendas da live no Instagram",
            bg=COLORS["primary"],
            fg=COLORS["app_bg"],
            font=("Segoe UI", 11),
        )
        subtitle.pack(anchor="w", pady=(3, 0))

        self.toolbar = tk.Frame(self, bg=COLORS["app_bg"])
        self.toolbar.pack(fill="x", padx=24, pady=(18, 14))

        self.toolbar_actions = tk.Frame(self.toolbar, bg=COLORS["app_bg"])
        self.toolbar_actions.pack(side="left", fill="x", expand=True)

        self.pre_live_frame = tk.Frame(self.toolbar_actions, bg=COLORS["app_bg"])
        self.live_frame = tk.Frame(self.toolbar_actions, bg=COLORS["app_bg"])
        self.post_live_frame = tk.Frame(self.toolbar_actions, bg=COLORS["app_bg"])

        self.start_button = ttk.Button(
            self.pre_live_frame, text="Iniciar live", command=self.start_live, style="Primary.TButton"
        )
        self.start_button.pack(side="left", padx=(0, 8))
        self._build_final_action_buttons(self.pre_live_frame)

        self.finish_button = ttk.Button(
            self.live_frame, text="Finalizar live", command=self.finish_live, style="Primary.TButton"
        )
        self.finish_button.pack(side="left", padx=(0, 8))

        self._build_final_action_buttons(self.post_live_frame)

        timer_panel = tk.Frame(self.toolbar, bg=COLORS["app_bg"])
        timer_panel.pack(side="right")

        self.live_status_label = tk.Label(
            timer_panel,
            text="Aguardando início",
            bg=COLORS["app_bg"],
            fg=COLORS["button_text"],
            font=("Segoe UI Semibold", 10),
        )
        self.live_status_label.pack(side="top", anchor="e")

        self.timer_label = tk.Label(
            timer_panel,
            text="00:00:00",
            bg=COLORS["timer_bg"],
            fg=COLORS["text"],
            font=("Consolas", 18, "bold"),
            padx=14,
            pady=3,
        )
        self.timer_label.pack(side="top", anchor="e", pady=(3, 0))

        self.total_sold_label = tk.Label(
            timer_panel,
            text="Vendido: R$ 0,00",
            bg=COLORS["app_bg"],
            fg=COLORS["button_text"],
            font=("Segoe UI Semibold", 10),
        )
        self.total_sold_label.pack(side="top", anchor="e", pady=(4, 0))

        self.sync_status_button = tk.Button(
            timer_panel,
            text="Conectar recortes",
            command=self.open_sync_dialog,
            bg=COLORS["app_bg"],
            fg=COLORS["button_text"],
            activebackground=COLORS["secondary"],
            activeforeground=COLORS["button_text"],
            relief="flat",
            bd=0,
            padx=0,
            pady=2,
            cursor="hand2",
            font=("Segoe UI Semibold", 9, "underline"),
        )
        self.sync_status_button.pack(side="top", anchor="e", pady=(2, 0))

        self.search_panel = tk.Frame(self, bg=COLORS["app_bg"])

        tk.Label(
            self.search_panel,
            text="Pesquisar",
            bg=COLORS["app_bg"],
            fg=COLORS["button_text"],
            font=("Segoe UI Semibold", 10),
        ).pack(side="left", padx=(0, 8))

        self.search_entry = tk.Entry(
            self.search_panel,
            textvariable=self.search_var,
            width=28,
            relief="solid",
            bd=1,
            fg=COLORS["text"],
            font=("Segoe UI", 10),
        )
        self.search_entry.pack(side="left", padx=(0, 8), ipady=4)
        self.search_entry.bind("<Return>", lambda _event: self._find_next_match())
        self.search_entry.bind("<Escape>", lambda _event: self._hide_search())

        ttk.Button(self.search_panel, text="Próximo", command=self._find_next_match, style="Secondary.TButton").pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(self.search_panel, text="Limpar", command=self._clear_search, style="Secondary.TButton").pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(self.search_panel, text="Fechar", command=self._hide_search, style="Secondary.TButton").pack(
            side="left"
        )

        self.duplicate_code_alert = tk.Frame(
            self,
            bg=COLORS["warning_bg"],
            highlightbackground="#E2B56F",
            highlightthickness=1,
        )
        tk.Label(
            self.duplicate_code_alert,
            text="!",
            bg=COLORS["warning_bg"],
            fg=COLORS["warning_text"],
            font=("Segoe UI Semibold", 12),
            width=2,
        ).pack(side="left", padx=(10, 4), pady=7)
        self.duplicate_code_alert_label = tk.Label(
            self.duplicate_code_alert,
            text="",
            bg=COLORS["warning_bg"],
            fg=COLORS["warning_text"],
            font=("Segoe UI Semibold", 10),
            anchor="w",
            justify="left",
        )
        self.duplicate_code_alert_label.pack(side="left", fill="x", expand=True, padx=(0, 10), pady=7)

        self.table_shell = tk.Frame(self, bg=COLORS["grid"], bd=1)
        self.table_shell.pack(fill="both", expand=True, padx=24, pady=(0, 14))

        header_shell = tk.Frame(self.table_shell, bg=COLORS["grid"])
        header_shell.pack(fill="x")

        self.header_frame = tk.Frame(header_shell, bg=COLORS["grid"])
        self.header_frame.pack(side="left", fill="x", expand=True)
        self.header_spacer = tk.Frame(header_shell, bg=COLORS["grid"], width=17)
        self.header_spacer.pack(side="right", fill="y")
        self.header_spacer.pack_propagate(False)
        self._build_table_header()

        body_shell = tk.Frame(self.table_shell, bg=COLORS["grid"])
        body_shell.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(
            body_shell,
            bg=COLORS["table_bg"],
            highlightthickness=0,
            borderwidth=0,
        )
        self.canvas.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(body_shell, orient="vertical", command=self.canvas.yview)
        scroll.pack(side="right", fill="y")
        self.header_spacer.configure(width=scroll.winfo_reqwidth())
        self.canvas.configure(yscrollcommand=scroll.set)

        self.body_frame = tk.Frame(self.canvas, bg=COLORS["grid"])
        self.body_window = self.canvas.create_window((0, 0), window=self.body_frame, anchor="nw")

        self.body_frame.bind("<Configure>", self._on_body_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        footer = tk.Frame(self, bg=COLORS["app_bg"])
        footer.pack(fill="x", padx=24, pady=(0, 14))

        footer_hint = tk.Label(
            footer,
            text=(
                "Clique em Iniciar live para gravar os tempos. "
                "Ao preencher Valor ou Código, o Tempo da peça é salvo automaticamente."
            ),
            bg=COLORS["app_bg"],
            fg=COLORS["button_text"],
            font=("Segoe UI", 10),
            anchor="w",
        )
        footer_hint.pack(side="left", fill="x", expand=True)

        tk.Label(
            footer,
            text="Created By Dudu",
            bg=COLORS["app_bg"],
            fg=COLORS["button_text"],
            font=("Segoe UI", 9, "italic"),
        ).pack(side="right", padx=(12, 0))

    def _build_final_action_buttons(self, parent):
        self._build_actions_menu(parent).pack(side="left", padx=(0, 8))
        self._build_prints_menu(parent).pack(side="left", padx=(0, 8))
        ttk.Button(parent, text="Jogo", command=self.show_memory_game, style="Secondary.TButton").pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(parent, text="Aventura", command=self.show_roguelike_game, style="Secondary.TButton").pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(parent, text="Nova live", command=self.clear_all, style="Secondary.TButton").pack(
            side="left"
        )

    def _build_actions_menu(self, parent):
        container = tk.Frame(parent, bg=COLORS["app_bg"])
        button = tk.Menubutton(
            container,
            text="Ações da live ▾",
            bg=COLORS["primary"],
            fg="#FFFFFF",
            activebackground="#6C366B",
            activeforeground="#FFFFFF",
            relief="flat",
            bd=0,
            padx=14,
            pady=8,
            font=("Segoe UI Semibold", 10),
            cursor="hand2",
        )
        button.pack(side="left")

        menu = tk.Menu(
            button,
            tearoff=False,
            bg="#FFFFFF",
            fg=COLORS["text"],
            activebackground=COLORS["secondary"],
            activeforeground=COLORS["button_text"],
            font=("Segoe UI", 10),
            bd=0,
            relief="flat",
        )
        menu.add_command(label="Resumo final", command=self.show_summary)
        menu.add_command(label="Mensagens clientes", command=self.show_client_messages)
        menu.add_command(label="Automatizar mensagens", command=self.open_message_automation)
        menu.add_command(label="Exportar Excel", command=self.export_excel)
        menu.add_separator()
        menu.add_command(label="Imprimir todos", command=self.print_all_reports)
        menu.add_command(label="Imprimir resumo", command=self.print_report)
        menu.add_command(label="Imprimir planilha", command=self.print_sheet)
        menu.add_command(label="Imprimir não vendidas", command=self.print_unsold_pieces)
        menu.add_separator()
        menu.add_command(label="Histórico de lives", command=self.show_history)
        menu.add_command(label="Retomar live / rascunho", command=self.show_cloud_lives)
        button.configure(menu=menu)
        return container

    def _build_prints_menu(self, parent):
        container = tk.Frame(parent, bg=COLORS["app_bg"])
        button = tk.Menubutton(
            container,
            text="Impressões ▾",
            bg=COLORS["secondary"],
            fg=COLORS["button_text"],
            activebackground="#E3C6E3",
            activeforeground=COLORS["button_text"],
            relief="flat",
            bd=0,
            padx=14,
            pady=8,
            font=("Segoe UI Semibold", 10),
            cursor="hand2",
        )
        button.pack(side="left")

        menu = tk.Menu(
            button,
            tearoff=False,
            bg="#FFFFFF",
            fg=COLORS["text"],
            activebackground=COLORS["secondary"],
            activeforeground=COLORS["button_text"],
            font=("Segoe UI", 10),
            bd=0,
            relief="flat",
        )
        menu.add_command(label="Imprimir Entregas", command=self.print_delivery_pages)
        menu.add_command(label="Imprimir Avaliações", command=self.print_evaluation_pages)
        button.configure(menu=menu)
        return container

    def show_roguelike_game(self):
        RogueBrechoGame(self, COLORS)

    def show_memory_game(self):
        window = tk.Toplevel(self)
        window.title("Jogo da memória - IoMarques Brechó")
        window.geometry("520x620")
        window.minsize(460, 560)
        window.configure(bg=COLORS["app_bg"])
        window.transient(self)

        state = {
            "cards": [],
            "buttons": [],
            "revealed": [],
            "matched": set(),
            "moves": 0,
            "started_at": None,
            "timer_job": None,
            "locked": False,
        }

        top = tk.Frame(window, bg=COLORS["primary"])
        top.pack(fill="x")
        tk.Label(
            top,
            text="Jogo da memória",
            bg=COLORS["primary"],
            fg="#FFFFFF",
            font=("Segoe UI Semibold", 18),
        ).pack(anchor="w", padx=18, pady=(14, 2))
        tk.Label(
            top,
            text="IoMarques Brechó",
            bg=COLORS["primary"],
            fg=COLORS["app_bg"],
            font=("Segoe UI", 10),
        ).pack(anchor="w", padx=18, pady=(0, 14))

        stats = tk.Frame(window, bg=COLORS["app_bg"])
        stats.pack(fill="x", padx=18, pady=(16, 10))

        moves_var = tk.StringVar(value="Jogadas: 0")
        pairs_var = tk.StringVar(value="Pares: 0/8")
        time_var = tk.StringVar(value="Tempo: 00:00")
        status_var = tk.StringVar(value="")

        for text_var in (moves_var, pairs_var, time_var):
            tk.Label(
                stats,
                textvariable=text_var,
                bg=COLORS["timer_bg"],
                fg=COLORS["text"],
                font=("Segoe UI Semibold", 10),
                padx=12,
                pady=6,
            ).pack(side="left", padx=(0, 8))

        ttk.Button(stats, text="Reiniciar", command=lambda: reset_game(), style="Secondary.TButton").pack(side="right")

        board = tk.Frame(window, bg=COLORS["grid"], bd=1)
        board.pack(fill="both", expand=True, padx=18, pady=(0, 12))
        for row in range(4):
            board.grid_rowconfigure(row, weight=1, uniform="memory_rows")
        for col in range(4):
            board.grid_columnconfigure(col, weight=1, uniform="memory_cols")

        status = tk.Label(
            window,
            textvariable=status_var,
            bg=COLORS["app_bg"],
            fg=COLORS["button_text"],
            font=("Segoe UI Semibold", 10),
        )
        status.pack(fill="x", padx=18, pady=(0, 14))

        def format_elapsed():
            if state["started_at"] is None:
                return "00:00"
            elapsed = max(0, int((datetime.now() - state["started_at"]).total_seconds()))
            minutes, seconds = divmod(elapsed, 60)
            return f"{minutes:02d}:{seconds:02d}"

        def update_timer():
            if not window.winfo_exists():
                return
            time_var.set(f"Tempo: {format_elapsed()}")
            state["timer_job"] = window.after(500, update_timer)

        def hidden_style(button):
            button.configure(
                text="?",
                state="normal",
                bg=COLORS["primary"],
                fg="#FFFFFF",
                activebackground="#6C366B",
                activeforeground="#FFFFFF",
                relief="flat",
            )

        def reveal_style(button, label):
            button.configure(
                text=label,
                state="normal",
                bg="#FFFFFF",
                fg=COLORS["primary"],
                activebackground="#FFFFFF",
                activeforeground=COLORS["primary"],
                relief="solid",
            )

        def matched_style(button, label):
            button.configure(
                text=label,
                state="disabled",
                bg=COLORS["sold_row"],
                fg=COLORS["text"],
                disabledforeground=COLORS["text"],
                relief="solid",
            )

        def refresh_stats():
            moves_var.set(f"Jogadas: {state['moves']}")
            pairs_var.set(f"Pares: {len(state['matched']) // 2}/8")

        def finish_game():
            if state["timer_job"] is not None:
                window.after_cancel(state["timer_job"])
                state["timer_job"] = None
            status_var.set(f"Completo em {format_elapsed()} com {state['moves']} jogadas.")

        def hide_unmatched(first, second):
            if not window.winfo_exists():
                return
            if first not in state["matched"]:
                hidden_style(state["buttons"][first])
            if second not in state["matched"]:
                hidden_style(state["buttons"][second])
            state["revealed"].clear()
            state["locked"] = False

        def on_card_click(index):
            if state["locked"] or index in state["matched"] or index in state["revealed"]:
                return

            if state["started_at"] is None:
                state["started_at"] = datetime.now()
            status_var.set("")

            reveal_style(state["buttons"][index], state["cards"][index])
            state["revealed"].append(index)

            if len(state["revealed"]) < 2:
                return

            state["moves"] += 1
            refresh_stats()
            first, second = state["revealed"]
            if state["cards"][first] == state["cards"][second]:
                state["matched"].update((first, second))
                matched_style(state["buttons"][first], state["cards"][first])
                matched_style(state["buttons"][second], state["cards"][second])
                state["revealed"].clear()
                refresh_stats()
                if len(state["matched"]) == len(state["cards"]):
                    finish_game()
                return

            state["locked"] = True
            window.after(700, lambda: hide_unmatched(first, second))

        def reset_game():
            if state["timer_job"] is not None:
                window.after_cancel(state["timer_job"])
                state["timer_job"] = None

            for child in board.winfo_children():
                child.destroy()

            labels = ["PIX", "LIVE", "BRECHÓ", "LOOK", "CAIXA", "VALOR", "FOTO", "ENVIO"]
            cards = labels * 2
            random.shuffle(cards)

            state["cards"] = cards
            state["buttons"] = []
            state["revealed"] = []
            state["matched"] = set()
            state["moves"] = 0
            state["started_at"] = None
            state["locked"] = False
            status_var.set("")
            refresh_stats()
            time_var.set("Tempo: 00:00")

            for index, _label in enumerate(cards):
                row, col = divmod(index, 4)
                card = tk.Button(
                    board,
                    text="?",
                    command=lambda card_index=index: on_card_click(card_index),
                    font=("Segoe UI Semibold", 15),
                    width=8,
                    height=4,
                    cursor="hand2",
                    bd=0,
                    highlightthickness=1,
                    highlightbackground=COLORS["grid"],
                )
                hidden_style(card)
                card.grid(row=row, column=col, sticky="nsew", padx=4, pady=4)
                state["buttons"].append(card)

            state["timer_job"] = window.after(500, update_timer)

        def on_close():
            if state["timer_job"] is not None:
                window.after_cancel(state["timer_job"])
            window.destroy()

        window.protocol("WM_DELETE_WINDOW", on_close)
        reset_game()

    def show_story_adventure(self):
        window = tk.Toplevel(self)
        window.title("Aventura da Peça 777 - IoMarques Brechó")
        window.geometry("900x700")
        window.minsize(820, 640)
        window.configure(bg=COLORS["app_bg"])
        window.transient(self)

        canvas_width = 840
        canvas_height = 540
        player_radius = 16
        keys = set()
        loop_job = {"id": None}

        chapters = [
            {
                "title": "Capítulo 1 - Depois da Live",
                "story": [
                    "A live acabou, as luzes do brechó piscaram e uma peça sumiu do estoque.",
                    "Dudu encontrou uma pista: etiquetas brilhantes apontam o caminho até a Peça 777.",
                    "Colete as 4 etiquetas de luz e atravesse o portal no fundo da sala.",
                ],
                "start": (88, 448),
                "goal": "Colete 4 etiquetas de luz",
                "items": [
                    (176, 126, "TAG", "#FFD166"),
                    (418, 96, "LUZ", "#F9A8D4"),
                    (668, 196, "IO", "#A7F3D0"),
                    (324, 418, "777", "#93C5FD"),
                ],
                "walls": [(250, 170, 586, 200), (126, 312, 432, 342), (586, 328, 710, 358)],
                "enemies": [
                    {"x": 636, "y": 102, "vx": 2.2, "vy": 1.4, "r": 18},
                    {"x": 522, "y": 426, "vx": -1.7, "vy": 1.8, "r": 17},
                ],
                "exit": (738, 414, 806, 498),
            },
            {
                "title": "Capítulo 2 - Corredor dos Looks",
                "story": [
                    "O portal abre para um corredor onde cada arara guarda uma lembrança de live.",
                    "As sombras tentam bagunçar os cabides. Passe por elas e recupere os looks perdidos.",
                    "Com 5 looks coletados, o caminho para o estoque secreto aparece.",
                ],
                "start": (80, 90),
                "goal": "Colete 5 looks perdidos",
                "items": [
                    (216, 86, "LOOK", "#FDE68A"),
                    (516, 92, "FIT", "#C4B5FD"),
                    (710, 252, "TOP", "#F9A8D4"),
                    (206, 404, "SAIA", "#A7F3D0"),
                    (518, 450, "BAG", "#93C5FD"),
                ],
                "walls": [
                    (142, 168, 244, 374),
                    (342, 52, 372, 276),
                    (492, 266, 708, 296),
                    (612, 354, 642, 514),
                ],
                "enemies": [
                    {"x": 422, "y": 150, "vx": 2.4, "vy": 0.9, "r": 18},
                    {"x": 724, "y": 110, "vx": -2.1, "vy": 1.8, "r": 17},
                    {"x": 310, "y": 462, "vx": 1.8, "vy": -2.0, "r": 17},
                ],
                "exit": (742, 36, 812, 118),
            },
            {
                "title": "Capítulo 3 - Estoque Secreto",
                "story": [
                    "Atrás da última arara existe uma sala que só abre para quem sabe garimpar.",
                    "A Peça 777 está no centro do estoque. Pegue-a e fuja pelo portal antes que as sombras fechem a loja.",
                    "Dica: movimentos curtos ajudam a passar entre as vitrines.",
                ],
                "start": (78, 462),
                "goal": "Pegue a Peça 777",
                "items": [
                    (420, 264, "PEÇA 777", "#FFD166"),
                    (184, 120, "CHAVE", "#A7F3D0"),
                    (702, 408, "LUZ", "#F9A8D4"),
                ],
                "walls": [
                    (152, 194, 312, 224),
                    (402, 74, 432, 250),
                    (402, 330, 432, 510),
                    (542, 194, 706, 224),
                    (152, 326, 312, 356),
                    (542, 326, 706, 356),
                ],
                "enemies": [
                    {"x": 308, "y": 94, "vx": 2.3, "vy": 1.7, "r": 18},
                    {"x": 626, "y": 114, "vx": -1.8, "vy": 2.2, "r": 18},
                    {"x": 286, "y": 450, "vx": 1.9, "vy": -2.0, "r": 18},
                    {"x": 642, "y": 448, "vx": -2.2, "vy": -1.7, "r": 18},
                ],
                "exit": (742, 234, 812, 318),
            },
        ]

        state = {
            "chapter": 0,
            "player_x": 80,
            "player_y": 440,
            "items": [],
            "walls": [],
            "enemies": [],
            "particles": [],
            "matched": 0,
            "lives": 3,
            "tick": 0,
            "story_index": 0,
            "story_mode": True,
            "paused": False,
            "game_over": False,
            "victory": False,
            "invulnerable": 0,
        }

        top = tk.Frame(window, bg=COLORS["primary"])
        top.pack(fill="x")
        tk.Label(
            top,
            text="Aventura da Peça 777",
            bg=COLORS["primary"],
            fg="#FFFFFF",
            font=("Segoe UI Semibold", 20),
        ).pack(anchor="w", padx=18, pady=(14, 2))
        tk.Label(
            top,
            text="Setas/WASD para mover, Espaço para avançar a história, P para pausar",
            bg=COLORS["primary"],
            fg=COLORS["app_bg"],
            font=("Segoe UI", 10),
        ).pack(anchor="w", padx=18, pady=(0, 14))

        hud = tk.Frame(window, bg=COLORS["app_bg"])
        hud.pack(fill="x", padx=18, pady=(14, 10))
        chapter_var = tk.StringVar()
        objective_var = tk.StringVar()
        lives_var = tk.StringVar()
        for variable in (chapter_var, objective_var, lives_var):
            tk.Label(
                hud,
                textvariable=variable,
                bg=COLORS["timer_bg"],
                fg=COLORS["text"],
                font=("Segoe UI Semibold", 10),
                padx=12,
                pady=6,
            ).pack(side="left", padx=(0, 8))

        ttk.Button(hud, text="Recomeçar", command=lambda: reset_adventure(), style="Secondary.TButton").pack(
            side="right"
        )

        canvas = tk.Canvas(
            window,
            width=canvas_width,
            height=canvas_height,
            bg="#1C1424",
            highlightthickness=0,
            bd=0,
        )
        canvas.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        canvas.focus_set()

        def blend(start, end, amount):
            start = start.lstrip("#")
            end = end.lstrip("#")
            values = []
            for index in range(0, 6, 2):
                a = int(start[index:index + 2], 16)
                b = int(end[index:index + 2], 16)
                values.append(int(a + (b - a) * amount))
            return "#" + "".join(f"{value:02x}" for value in values)

        def chapter():
            return chapters[state["chapter"]]

        def found_count():
            return sum(1 for item in state["items"] if item["found"])

        def exit_open():
            return found_count() >= len(state["items"])

        def player_rect_at(x, y):
            return (x - player_radius, y - player_radius, x + player_radius, y + player_radius)

        def rects_intersect(first, second):
            return not (
                first[2] < second[0]
                or first[0] > second[2]
                or first[3] < second[1]
                or first[1] > second[3]
            )

        def circle_hits_wall(x, y, radius):
            probe = (x - radius, y - radius, x + radius, y + radius)
            return any(rects_intersect(probe, wall) for wall in state["walls"])

        def refresh_hud():
            current = chapter()
            chapter_var.set(current["title"])
            objective_var.set(f"{current['goal']} - {found_count()}/{len(state['items'])}")
            lives_var.set(f"Vidas: {state['lives']}")

        def add_particles(x, y, color, amount=16):
            for _index in range(amount):
                angle = random.uniform(0, math.tau)
                speed = random.uniform(1.2, 4.2)
                state["particles"].append(
                    {
                        "x": x,
                        "y": y,
                        "vx": math.cos(angle) * speed,
                        "vy": math.sin(angle) * speed,
                        "life": random.randint(18, 34),
                        "color": color,
                    }
                )

        def build_level(index):
            current = chapters[index]
            state["chapter"] = index
            state["player_x"], state["player_y"] = current["start"]
            state["items"] = [
                {"x": x, "y": y, "label": label, "color": color, "found": False}
                for x, y, label, color in current["items"]
            ]
            state["walls"] = list(current["walls"])
            state["enemies"] = [enemy.copy() for enemy in current["enemies"]]
            state["particles"] = []
            state["story_index"] = 0
            state["story_mode"] = True
            state["paused"] = False
            state["game_over"] = False
            state["victory"] = False
            state["invulnerable"] = 50
            refresh_hud()
            canvas.focus_set()

        def reset_adventure():
            state["lives"] = 3
            state["tick"] = 0
            build_level(0)

        def damage_player():
            if state["invulnerable"] > 0 or state["game_over"] or state["victory"]:
                return
            state["lives"] -= 1
            add_particles(state["player_x"], state["player_y"], "#FCA5A5", amount=24)
            if state["lives"] <= 0:
                state["game_over"] = True
                state["story_mode"] = True
            else:
                state["player_x"], state["player_y"] = chapter()["start"]
                state["invulnerable"] = 70
            refresh_hud()

        def advance_story():
            if state["game_over"] or state["victory"]:
                reset_adventure()
                return
            if not state["story_mode"]:
                return
            state["story_index"] += 1
            if state["story_index"] >= len(chapter()["story"]):
                state["story_mode"] = False
            canvas.focus_set()

        def complete_level():
            add_particles(state["player_x"], state["player_y"], "#A7F3D0", amount=34)
            if state["chapter"] >= len(chapters) - 1:
                state["victory"] = True
                state["story_mode"] = True
                return
            build_level(state["chapter"] + 1)

        def update_world():
            speed = 4.4
            dx = 0
            dy = 0
            if keys.intersection({"left", "a"}):
                dx -= speed
            if keys.intersection({"right", "d"}):
                dx += speed
            if keys.intersection({"up", "w"}):
                dy -= speed
            if keys.intersection({"down", "s"}):
                dy += speed

            if dx and dy:
                dx *= 0.72
                dy *= 0.72

            next_x = max(player_radius, min(canvas_width - player_radius, state["player_x"] + dx))
            if not circle_hits_wall(next_x, state["player_y"], player_radius):
                state["player_x"] = next_x
            next_y = max(player_radius, min(canvas_height - player_radius, state["player_y"] + dy))
            if not circle_hits_wall(state["player_x"], next_y, player_radius):
                state["player_y"] = next_y

            if state["invulnerable"] > 0:
                state["invulnerable"] -= 1

            for enemy in state["enemies"]:
                for axis in ("x", "y"):
                    enemy[axis] += enemy["v" + axis]
                    hit_bounds = (
                        enemy[axis] < enemy["r"]
                        or enemy[axis] > (canvas_width if axis == "x" else canvas_height) - enemy["r"]
                    )
                    if hit_bounds or circle_hits_wall(enemy["x"], enemy["y"], enemy["r"]):
                        enemy[axis] -= enemy["v" + axis]
                        enemy["v" + axis] *= -1

                if math.hypot(state["player_x"] - enemy["x"], state["player_y"] - enemy["y"]) < player_radius + enemy["r"]:
                    damage_player()

            for item in state["items"]:
                if item["found"]:
                    continue
                if math.hypot(state["player_x"] - item["x"], state["player_y"] - item["y"]) < 28:
                    item["found"] = True
                    add_particles(item["x"], item["y"], item["color"])
                    refresh_hud()

            if exit_open():
                exit_rect = chapter()["exit"]
                if rects_intersect(player_rect_at(state["player_x"], state["player_y"]), exit_rect):
                    complete_level()

            for particle in list(state["particles"]):
                particle["x"] += particle["vx"]
                particle["y"] += particle["vy"]
                particle["vy"] += 0.08
                particle["life"] -= 1
                if particle["life"] <= 0:
                    state["particles"].remove(particle)

        def draw_background():
            canvas.delete("all")
            for index in range(28):
                amount = index / 27
                canvas.create_rectangle(
                    0,
                    index * canvas_height / 28,
                    canvas_width,
                    (index + 1) * canvas_height / 28 + 1,
                    fill=blend("#1A1024", "#4C2450", amount),
                    outline="",
                )

            for index in range(34):
                x = (index * 97 + state["tick"] * 0.7) % (canvas_width + 80) - 40
                y = 36 + (index * 53) % (canvas_height - 92)
                radius = 1 + (index % 3)
                color = "#F9D8F9" if index % 2 else "#D7FBE8"
                canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill=color, outline="")

            for x in range(0, canvas_width, 56):
                canvas.create_line(x, 0, x - 90, canvas_height, fill="#2C1B35", width=1)
            for y in range(36, canvas_height, 56):
                canvas.create_line(0, y, canvas_width, y + 40, fill="#2C1B35", width=1)

        def draw_exit():
            x1, y1, x2, y2 = chapter()["exit"]
            pulse = 8 + math.sin(state["tick"] * 0.12) * 4
            color = "#A7F3D0" if exit_open() else "#6B5A70"
            glow = "#D9FBE7" if exit_open() else "#2E2235"
            canvas.create_oval(x1 - pulse, y1 - pulse, x2 + pulse, y2 + pulse, fill=glow, outline="")
            canvas.create_rectangle(x1, y1, x2, y2, fill=color, outline="#FFFFFF", width=2)
            label = "PORTAL" if exit_open() else "FECHADO"
            canvas.create_text((x1 + x2) / 2, (y1 + y2) / 2, text=label, fill=COLORS["text"], font=("Segoe UI Semibold", 10))

        def draw_walls():
            for x1, y1, x2, y2 in state["walls"]:
                canvas.create_rectangle(x1 + 6, y1 + 8, x2 + 6, y2 + 8, fill="#150E1E", outline="")
                canvas.create_rectangle(x1, y1, x2, y2, fill="#6C366B", outline="#D8B6D8", width=2)
                canvas.create_line(x1 + 8, y1 + 8, x2 - 8, y1 + 8, fill="#B98ABC", width=1)

        def draw_items():
            for item in state["items"]:
                if item["found"]:
                    continue
                pulse = 1 + math.sin(state["tick"] * 0.16 + item["x"]) * 0.12
                radius = 20 * pulse
                x = item["x"]
                y = item["y"]
                canvas.create_oval(x - radius * 1.6, y - radius * 1.6, x + radius * 1.6, y + radius * 1.6, fill="#4A2A57", outline="")
                canvas.create_polygon(
                    x,
                    y - radius,
                    x + radius,
                    y,
                    x,
                    y + radius,
                    x - radius,
                    y,
                    fill=item["color"],
                    outline="#FFFFFF",
                    width=2,
                )
                canvas.create_text(x, y + 34, text=item["label"], fill="#FFFFFF", font=("Segoe UI Semibold", 9))

        def draw_enemies():
            for enemy in state["enemies"]:
                x = enemy["x"]
                y = enemy["y"]
                r = enemy["r"] + math.sin(state["tick"] * 0.18 + x) * 2
                canvas.create_oval(x - r + 5, y - r + 8, x + r + 5, y + r + 8, fill="#130C18", outline="")
                canvas.create_oval(x - r, y - r, x + r, y + r, fill="#2B1934", outline="#B65AC0", width=2)
                canvas.create_oval(x - 7, y - 4, x - 2, y + 2, fill="#FDE68A", outline="")
                canvas.create_oval(x + 2, y - 4, x + 7, y + 2, fill="#FDE68A", outline="")
                canvas.create_arc(x - r, y - r, x + r, y + r, start=205, extent=130, outline="#F9A8D4", width=2, style="arc")

        def draw_player():
            x = state["player_x"]
            y = state["player_y"]
            alpha_flash = state["invulnerable"] > 0 and state["tick"] % 8 < 4
            body = "#FFFFFF" if alpha_flash else "#F9F0F5"
            canvas.create_oval(x - 16 + 5, y - 16 + 8, x + 16 + 5, y + 16 + 8, fill="#120B18", outline="")
            canvas.create_oval(x - 16, y - 16, x + 16, y + 16, fill=body, outline=COLORS["primary"], width=3)
            canvas.create_polygon(x - 10, y - 4, x + 12, y - 12, x + 8, y + 7, fill="#F9A8D4", outline=COLORS["primary"])
            canvas.create_oval(x - 6, y - 5, x - 2, y - 1, fill=COLORS["text"], outline="")
            canvas.create_oval(x + 4, y - 5, x + 8, y - 1, fill=COLORS["text"], outline="")

        def draw_particles():
            for particle in state["particles"]:
                size = max(1, particle["life"] // 8)
                canvas.create_oval(
                    particle["x"] - size,
                    particle["y"] - size,
                    particle["x"] + size,
                    particle["y"] + size,
                    fill=particle["color"],
                    outline="",
                )

        def story_text():
            if state["victory"]:
                return (
                    "Final - A Peça 777 voltou para a arara principal.",
                    "A loja acendeu inteira, as sombras sumiram e o brechó ficou pronto para a próxima live. Pressione Espaço para jogar novamente.",
                )
            if state["game_over"]:
                return (
                    "As luzes apagaram",
                    "As sombras fecharam o estoque antes de você encontrar a Peça 777. Pressione Espaço para tentar outra vez.",
                )
            current_story = chapter()["story"]
            index = min(state["story_index"], len(current_story) - 1)
            return (chapter()["title"], current_story[index])

        def draw_story_overlay():
            if not state["story_mode"] and not state["paused"]:
                return
            title, text = ("Pausado", "Pressione P para voltar ao jogo.") if state["paused"] else story_text()
            canvas.create_rectangle(42, canvas_height - 172, canvas_width - 42, canvas_height - 34, fill="#FFF7FF", outline=COLORS["primary"], width=3)
            canvas.create_text(70, canvas_height - 142, anchor="w", text=title, fill=COLORS["primary"], font=("Segoe UI Semibold", 14))
            canvas.create_text(
                70,
                canvas_height - 108,
                anchor="nw",
                text=text,
                width=canvas_width - 140,
                fill=COLORS["text"],
                font=("Segoe UI", 11),
            )
            hint = "Espaço para continuar" if not state["paused"] else "P para continuar"
            canvas.create_text(canvas_width - 70, canvas_height - 56, anchor="e", text=hint, fill=COLORS["button_text"], font=("Segoe UI Semibold", 10))

        def render():
            draw_background()
            draw_exit()
            draw_walls()
            draw_items()
            draw_enemies()
            draw_particles()
            draw_player()
            draw_story_overlay()

        def game_loop():
            if not window.winfo_exists():
                return
            state["tick"] += 1
            if not state["paused"] and not state["story_mode"] and not state["game_over"] and not state["victory"]:
                update_world()
            render()
            loop_job["id"] = window.after(30, game_loop)

        def on_key_press(event):
            key = event.keysym.lower()
            if key in {"space", "return"}:
                advance_story()
                return
            if key == "p":
                if not state["story_mode"] and not state["game_over"] and not state["victory"]:
                    state["paused"] = not state["paused"]
                return
            if key == "r":
                reset_adventure()
                return
            keys.add(key)

        def on_key_release(event):
            keys.discard(event.keysym.lower())

        def on_close():
            if loop_job["id"] is not None:
                window.after_cancel(loop_job["id"])
            window.destroy()

        window.bind("<KeyPress>", on_key_press)
        window.bind("<KeyRelease>", on_key_release)
        canvas.bind("<Button-1>", lambda _event: canvas.focus_set())
        window.protocol("WM_DELETE_WINDOW", on_close)
        reset_adventure()
        game_loop()
        window.after(100, canvas.focus_set)

    def _build_table_header(self):
        self.filter_buttons = []
        index_cell = tk.Frame(
            self.header_frame,
            bg=COLORS["table_header"],
            highlightbackground=COLORS["grid"],
            highlightthickness=1,
        )
        index_cell.grid(row=0, column=0, sticky="nsew")
        self.header_frame.grid_columnconfigure(0, weight=0, minsize=INDEX_COLUMN_WIDTH)
        tk.Label(
            index_cell,
            text="Peça",
            bg=COLORS["table_header"],
            fg=COLORS["table_header_text"],
            font=("Segoe UI Semibold", 10),
            anchor="center",
            padx=6,
            pady=9,
        ).pack(fill="both", expand=True)

        for col, header in enumerate(HEADERS[column] for column in COLUMNS):
            cell = tk.Frame(
                self.header_frame,
                bg=COLORS["table_header"],
                highlightbackground=COLORS["grid"],
                highlightthickness=1,
            )
            cell.grid(row=0, column=col + 1, sticky="nsew")
            self.header_frame.grid_columnconfigure(col + 1, weight=1, minsize=COLUMN_WIDTHS[col])

            label = tk.Label(
                cell,
                text=header,
                bg=COLORS["table_header"],
                fg=COLORS["table_header_text"],
                font=("Segoe UI Semibold", 11),
                anchor="w",
                padx=10,
                pady=9,
            )
            label.pack(side="left", fill="both", expand=True)

            filter_button = tk.Button(
                cell,
                text="▼",
                command=lambda c=col: self._show_filter_popup(c),
                bg=COLORS["table_header"],
                fg=COLORS["button_text"],
                activebackground=COLORS["secondary"],
                activeforeground=COLORS["button_text"],
                relief="flat",
                bd=0,
                width=2,
                padx=2,
                pady=0,
                font=("Segoe UI", 8),
                cursor="hand2",
                takefocus=False,
            )
            filter_button.pack(side="right", padx=(0, 4), pady=6)
            self.filter_buttons.append(filter_button)

    def _on_body_configure(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self.body_window, width=event.width)

    def _on_mousewheel(self, event):
        if not self.canvas.winfo_ismapped() or not self._pointer_inside_widget(self.canvas):
            return
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _bind_shortcuts(self):
        self.bind_all("<Control-z>", self.undo)
        self.bind_all("<Control-Z>", self.undo)
        self.bind_all("<Control-y>", self.redo)
        self.bind_all("<Control-Y>", self.redo)
        self.bind_all("<Control-f>", self.focus_search)
        self.bind_all("<Control-F>", self.focus_search)

    def focus_search(self, _event=None):
        if not self.search_panel.winfo_ismapped():
            self.search_panel.pack(fill="x", padx=24, pady=(0, 10), before=self.table_shell)
        self.search_entry.focus_set()
        self.search_entry.selection_range(0, tk.END)
        return "break"

    def _on_key_press(self, event, row, col):
        if not self._cell_exists(row, col) or self.restoring_rows:
            return
        if event.state & 0x4:
            return
        ignored = {
            "Shift_L",
            "Shift_R",
            "Control_L",
            "Control_R",
            "Alt_L",
            "Alt_R",
            "Caps_Lock",
            "Escape",
            "Return",
            "Tab",
            "ISO_Left_Tab",
            "Up",
            "Down",
            "Left",
            "Right",
            "Home",
            "End",
            "Prior",
            "Next",
        }
        if event.keysym in ignored:
            return
        self._record_undo_state()

    def _record_undo_state(self):
        if self.restoring_rows:
            return
        snapshot = self._rows(include_empty=True)
        if self.undo_stack and self.undo_stack[-1] == snapshot:
            return
        self.undo_stack.append(snapshot)
        if len(self.undo_stack) > self.max_undo_steps:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def undo(self, _event=None):
        if not self.undo_stack:
            return "break"
        current = self._rows(include_empty=True)
        snapshot = self.undo_stack.pop()
        if snapshot == current and self.undo_stack:
            snapshot = self.undo_stack.pop()
        self.redo_stack.append(current)
        self._restore_rows_snapshot(snapshot)
        return "break"

    def redo(self, _event=None):
        if not self.redo_stack:
            return "break"
        current = self._rows(include_empty=True)
        snapshot = self.redo_stack.pop()
        self.undo_stack.append(current)
        self._restore_rows_snapshot(snapshot)
        return "break"

    def _restore_rows_snapshot(self, rows):
        if not self.sync_manager.ready or (self.live_id and self.sync_manager.is_deleted(self.live_id)):
            return
        active_cell = self.active_cell
        self.restoring_rows = True
        try:
            while len(self.row_vars) < len(rows):
                self._add_row()
            while len(self.row_vars) > len(rows):
                self._remove_last_row()
            if not rows:
                self._add_row()

            changed_rows = set()
            for row_index, row in enumerate(rows):
                for col, column in enumerate(COLUMNS):
                    value = str(row.get(column, "")).strip()
                    if self.row_vars[row_index][col].get() != value:
                        self.row_vars[row_index][col].set(value)
                        changed_rows.add(row_index)

            self._ensure_blank_row()
            for row_index in range(len(self.row_vars)):
                self._refresh_time_checkbox(row_index)
                if row_index in changed_rows or row_index >= len(rows):
                    self._refresh_row_style(row_index)
            if active_cell and self._cell_exists(*active_cell):
                self._set_active_cell(*active_cell)
                self.cell_entries[active_cell[0]][active_cell[1]].focus_set()
            self._refresh_totals()
            self._apply_filters()
            self._save_rows()
        finally:
            self.restoring_rows = False

    def _remove_last_row(self):
        if not self.row_vars:
            return
        self.index_frames[-1].destroy()
        for cell in self.cell_frames[-1]:
            cell.destroy()
        self.index_frames.pop()
        self.index_labels.pop()
        self.time_check_vars.pop()
        self.time_check_buttons.pop()
        self.manual_time_vars.pop()
        self.row_vars.pop()
        self.cell_frames.pop()
        self.cell_entries.pop()
        self.clear_buttons.pop()
        if self.active_cell and self.active_cell[0] >= len(self.row_vars):
            self.active_cell = None

    def _show_filter_popup(self, col):
        popup = tk.Toplevel(self)
        popup.title(f"Filtro - {HEADERS[COLUMNS[col]]}")
        popup.configure(bg=COLORS["app_bg"])
        popup.resizable(False, False)
        popup.transient(self)

        tk.Label(
            popup,
            text=f"Filtrar {HEADERS[COLUMNS[col]]}",
            bg=COLORS["app_bg"],
            fg=COLORS["button_text"],
            font=("Segoe UI Semibold", 10),
        ).pack(anchor="w", padx=12, pady=(10, 4))

        entry = tk.Entry(
            popup,
            textvariable=self.filter_vars[col],
            width=28,
            relief="solid",
            bd=1,
            fg=COLORS["text"],
            font=("Segoe UI", 10),
        )
        entry.pack(fill="x", padx=12, pady=(0, 10), ipady=4)

        actions = tk.Frame(popup, bg=COLORS["app_bg"])
        actions.pack(fill="x", padx=12, pady=(0, 12))

        def apply_and_close():
            self._apply_filters()
            popup.destroy()

        def clear_and_close():
            self.filter_vars[col].set("")
            self._apply_filters()
            popup.destroy()

        ttk.Button(actions, text="Aplicar", command=apply_and_close, style="Primary.TButton").pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(actions, text="Limpar", command=clear_and_close, style="Secondary.TButton").pack(side="left")

        entry.bind("<KeyRelease>", lambda _event: self._apply_filters())
        entry.bind("<Return>", lambda _event: apply_and_close())
        entry.bind("<Escape>", lambda _event: popup.destroy())

        button = self.filter_buttons[col]
        x = button.winfo_rootx()
        y = button.winfo_rooty() + button.winfo_height()
        popup.geometry(f"+{x}+{y}")
        entry.focus_set()
        entry.selection_range(0, tk.END)

    def _clear_filters(self):
        for var in self.filter_vars:
            var.set("")
        self._apply_filters()

    def _active_filters(self):
        return [var.get().strip().lower() for var in self.filter_vars]

    def _apply_filters(self):
        if not self.row_vars:
            self._refresh_filter_buttons()
            self._refresh_duplicate_code_alert()
            return
        filters = self._active_filters()
        has_filters = any(filters)
        for row_index, row_vars in enumerate(self.row_vars):
            visible = True
            if has_filters:
                for col, query in enumerate(filters):
                    if query and query not in row_vars[col].get().strip().lower():
                        visible = False
                        break
                if visible and not any(var.get().strip() for var in row_vars):
                    visible = False

            for col, cell in enumerate(self.cell_frames[row_index]):
                if visible:
                    self.index_frames[row_index].grid(row=row_index, column=0, sticky="nsew")
                    cell.grid(row=row_index, column=col + 1, sticky="nsew")
                else:
                    self.index_frames[row_index].grid_remove()
                    cell.grid_remove()

        self._on_body_configure()
        self._refresh_filter_buttons()
        self._refresh_duplicate_code_alert()

    def _refresh_filter_buttons(self):
        if not self.filter_buttons:
            return
        for col, button in enumerate(self.filter_buttons):
            active = bool(self.filter_vars[col].get().strip())
            button.configure(
                bg=COLORS["secondary"] if active else COLORS["table_header"],
                fg=COLORS["primary"] if active else COLORS["button_text"],
            )

    def _find_next_match(self):
        query = self.search_var.get().strip().lower()
        if not query:
            return

        matches = []
        for row_index, row_vars in enumerate(self.row_vars):
            for col, var in enumerate(row_vars):
                if query in var.get().strip().lower():
                    matches.append((row_index, col))

        if not matches:
            messagebox.showinfo("Pesquisa", "Não encontrei esse texto na planilha.")
            return

        start = -1
        if self.active_cell in matches:
            start = matches.index(self.active_cell)
        row, col = matches[(start + 1) % len(matches)]
        self._focus_cell(row, col)

    def _clear_search(self):
        self.search_var.set("")
        self.search_entry.focus_set()

    def _hide_search(self):
        self.search_var.set("")
        self.search_panel.pack_forget()
        if self.active_cell and self._cell_exists(*self.active_cell):
            self.cell_entries[self.active_cell[0]][self.active_cell[1]].focus_set()
        return "break"

    def _add_row(self, values=None):
        if values is None:
            values = [""] * len(COLUMNS)

        row_index = len(self.row_vars)
        row_vars = []
        row_frames = []
        row_entries = []
        row_buttons = []

        self.body_frame.grid_columnconfigure(0, weight=0, minsize=INDEX_COLUMN_WIDTH)
        index_cell = tk.Frame(
            self.body_frame,
            bg=COLORS["table_header"],
            highlightbackground=COLORS["grid"],
            highlightthickness=1,
        )
        index_cell.grid(row=row_index, column=0, sticky="nsew")
        time_check_var = tk.BooleanVar(value=bool(values[TIME_COL].strip() if TIME_COL < len(values) else ""))
        manual_time_var = tk.BooleanVar(value=False)
        time_check = tk.Checkbutton(
            index_cell,
            variable=time_check_var,
            command=lambda r=row_index: self._toggle_manual_time(r),
            bg=COLORS["table_header"],
            activebackground=COLORS["table_header"],
            selectcolor="#DDF4E5",
            relief="flat",
            bd=0,
            padx=0,
            pady=0,
            width=1,
            cursor="hand2",
            takefocus=False,
        )
        time_check.pack(side="left", padx=(3, 0), pady=0)
        index_label = tk.Label(
            index_cell,
            text=str(row_index + 1),
            bg=COLORS["table_header"],
            fg=COLORS["button_text"],
            font=("Segoe UI Semibold", 10),
            anchor="center",
            padx=2,
            pady=7,
        )
        index_label.pack(side="left", fill="both", expand=True)

        for col in range(len(COLUMNS)):
            self.body_frame.grid_columnconfigure(col + 1, weight=1, minsize=COLUMN_WIDTHS[col])

            cell = tk.Frame(
                self.body_frame,
                bg=COLORS["table_bg"],
                highlightbackground=COLORS["grid"],
                highlightthickness=1,
            )
            cell.grid(row=row_index, column=col + 1, sticky="nsew")

            value = values[col] if col < len(values) else ""
            var = tk.StringVar(value=value)
            entry = tk.Entry(
                cell,
                textvariable=var,
                width=1,
                relief="flat",
                bd=0,
                bg=COLORS["table_bg"],
                fg=COLORS["text"],
                insertbackground=COLORS["text"],
                font=("Segoe UI", 11),
            )
            entry.pack(side="left", fill="both", expand=True, padx=(10, 4), pady=7)

            clear_button = None
            if col in CLEARABLE_CLIENT_COLS:
                clear_button = tk.Button(
                    cell,
                    text="X",
                    command=lambda r=row_index, c=col: self._clear_client_cell(r, c),
                    bg=COLORS["table_bg"],
                    fg=COLORS["button_text"],
                    activebackground=COLORS["secondary"],
                    activeforeground=COLORS["button_text"],
                    relief="flat",
                    bd=0,
                    padx=7,
                    pady=0,
                    font=("Segoe UI Semibold", 9),
                    cursor="hand2",
                    takefocus=False,
                )

            for widget in (cell, entry, clear_button):
                if widget is None:
                    continue
                widget.bind("<Enter>", lambda _e, r=row_index, c=col: self._show_clear_button(r, c))
                widget.bind("<Leave>", lambda _e, r=row_index, c=col: self._schedule_hide_clear_button(r, c))

            entry.bind("<FocusIn>", lambda _e, r=row_index, c=col: self._set_active_cell(r, c))
            entry.bind("<FocusOut>", lambda _e, r=row_index, c=col: self._finish_cell_edit(r, c))
            entry.bind("<KeyPress>", lambda e, r=row_index, c=col: self._on_key_press(e, r, c))
            entry.bind("<KeyRelease>", lambda e, r=row_index, c=col: self._on_key_release(e, r, c))
            entry.bind("<Return>", lambda _e, r=row_index, c=col: self._move_focus(r, c, 1, 0))
            entry.bind("<Up>", lambda _e, r=row_index, c=col: self._move_focus(r, c, -1, 0))
            entry.bind("<Down>", lambda _e, r=row_index, c=col: self._move_focus(r, c, 1, 0))
            entry.bind("<Left>", lambda _e, r=row_index, c=col: self._move_focus_left(r, c))
            entry.bind("<Right>", lambda _e, r=row_index, c=col: self._move_focus_right(r, c))
            entry.bind("<Tab>", lambda _e, r=row_index, c=col: self._move_focus_right(r, c))
            entry.bind("<Shift-Tab>", lambda _e, r=row_index, c=col: self._move_focus_left(r, c))

            row_vars.append(var)
            row_frames.append(cell)
            row_entries.append(entry)
            row_buttons.append(clear_button)

        self.index_frames.append(index_cell)
        self.index_labels.append(index_label)
        self.time_check_vars.append(time_check_var)
        self.time_check_buttons.append(time_check)
        self.manual_time_vars.append(manual_time_var)
        self.row_vars.append(row_vars)
        self.cell_frames.append(row_frames)
        self.cell_entries.append(row_entries)
        self.clear_buttons.append(row_buttons)
        self._refresh_row_style(row_index)
        self._on_body_configure()
        if hasattr(self, "filter_vars") and any(var.get().strip() for var in self.filter_vars):
            self._apply_filters()

    def _set_active_cell(self, row, col):
        previous = self.active_cell
        self.active_cell = (row, col)
        if previous:
            self._refresh_cell_border(*previous)
        self._refresh_cell_border(row, col)

    def _refresh_cell_border(self, row, col):
        if not self._cell_exists(row, col):
            return
        color = COLORS["active_border"] if self.active_cell == (row, col) else COLORS["grid"]
        self.cell_frames[row][col].configure(highlightbackground=color)

    def _finish_cell_edit(self, row, col):
        if not self._cell_exists(row, col):
            return
        if col == VALUE_COL:
            self.row_vars[row][col].set(self._format_money_input(self.row_vars[row][col].get()))
        if col in (VALUE_COL, CODE_COL):
            self._maybe_stamp_piece_time(row)
        self._refresh_time_checkbox(row)
        self._refresh_row_style(row)
        self._refresh_totals()
        self._ensure_blank_row()
        self._apply_filters()
        self._save_rows()

    def _on_key_release(self, event, row, col):
        if event.keysym in {"Up", "Down", "Left", "Right", "Return", "Tab", "ISO_Left_Tab"}:
            return
        if col in (VALUE_COL, CODE_COL):
            self._maybe_stamp_piece_time(row)
        self._refresh_time_checkbox(row)
        self._refresh_row_style(row)
        self._refresh_totals()
        self._ensure_blank_row()
        self._apply_filters()
        self._save_rows()
        if self.hovered_cell == (row, col):
            self._show_clear_button(row, col)

    def _maybe_stamp_piece_time(self, row):
        if not self._cell_exists(row, TIME_COL):
            return
        has_piece_reference = (
            self.row_vars[row][VALUE_COL].get().strip() or self.row_vars[row][CODE_COL].get().strip()
        )
        manual_time = row < len(self.manual_time_vars) and self.manual_time_vars[row].get()
        if not has_piece_reference and not manual_time:
            self.row_vars[row][TIME_COL].set("")
        elif self.live_running and not self.row_vars[row][TIME_COL].get().strip():
            self.row_vars[row][TIME_COL].set(self._format_elapsed(self._current_elapsed_seconds()))
        self._refresh_time_checkbox(row)

    def _toggle_manual_time(self, row):
        if not self._cell_exists(row, TIME_COL) or self.restoring_rows:
            return

        checked = self.time_check_vars[row].get()
        current_time = self.row_vars[row][TIME_COL].get().strip()
        if checked and not current_time:
            self._record_undo_state()
            self.manual_time_vars[row].set(True)
            self.row_vars[row][TIME_COL].set(self._format_elapsed(self._current_elapsed_seconds()))
        elif not checked and current_time:
            self._record_undo_state()
            self.manual_time_vars[row].set(False)
            self.row_vars[row][TIME_COL].set("")
        elif not checked:
            self.manual_time_vars[row].set(False)

        self._refresh_time_checkbox(row)
        self._refresh_row_style(row)
        self._refresh_totals()
        self._ensure_blank_row()
        self._apply_filters()
        self._save_rows()

    def _refresh_time_checkbox(self, row):
        if row < 0 or row >= len(self.time_check_vars) or not self._cell_exists(row, TIME_COL):
            return
        has_time = bool(self.row_vars[row][TIME_COL].get().strip())
        self.time_check_vars[row].set(has_time)
        if not has_time and row < len(self.manual_time_vars):
            self.manual_time_vars[row].set(False)

    def _move_focus_left(self, row, col):
        target_row = row
        target_col = col - 1
        if target_col < 0:
            target_row = max(0, row - 1)
            target_col = len(COLUMNS) - 1
        return self._focus_cell(target_row, target_col)

    def _move_focus_right(self, row, col):
        target_row = row
        target_col = col + 1
        if target_col >= len(COLUMNS):
            target_row = row + 1
            target_col = 0
        return self._focus_cell(target_row, target_col)

    def _move_focus(self, row, col, row_delta, col_delta):
        return self._focus_cell(row + row_delta, col + col_delta)

    def _focus_cell(self, row, col):
        if self.active_cell and self._cell_exists(*self.active_cell):
            self._finish_cell_edit(*self.active_cell)

        row = max(0, row)
        col = min(max(0, col), len(COLUMNS) - 1)
        while row >= len(self.row_vars):
            self._add_row()

        entry = self.cell_entries[row][col]
        self._set_active_cell(row, col)
        entry.focus_set()
        entry.icursor(tk.END)
        entry.selection_range(0, tk.END)
        self.canvas.after(10, lambda: self._scroll_cell_into_view(row))
        return "break"

    def _scroll_cell_into_view(self, row):
        if row < 0 or row >= len(self.cell_frames):
            return
        cell = self.cell_frames[row][0]
        canvas_height = max(1, self.canvas.winfo_height())
        y = cell.winfo_y()
        row_height = max(1, cell.winfo_height())
        view_top = self.canvas.canvasy(0)
        view_bottom = view_top + canvas_height
        total_height = max(1, self.body_frame.winfo_height())

        if y < view_top:
            self.canvas.yview_moveto(y / total_height)
        elif y + row_height > view_bottom:
            self.canvas.yview_moveto((y + row_height - canvas_height) / total_height)

    def _show_clear_button(self, row, col):
        self.hovered_cell = (row, col)
        if col not in CLEARABLE_CLIENT_COLS or not self._cell_exists(row, col):
            return

        button = self.clear_buttons[row][col]
        if button is None:
            return

        if self.row_vars[row][col].get().strip():
            bg = self._row_bg(row)
            button.configure(bg=bg)
            if not button.winfo_ismapped():
                button.pack(side="right", padx=(0, 5), pady=6)
        elif button.winfo_ismapped():
            button.pack_forget()

    def _schedule_hide_clear_button(self, row, col):
        self.after(60, lambda: self._hide_clear_button_if_pointer_left(row, col))

    def _hide_clear_button_if_pointer_left(self, row, col):
        if not self._cell_exists(row, col) or col not in CLEARABLE_CLIENT_COLS:
            return
        if self._pointer_inside_widget(self.cell_frames[row][col]):
            return
        button = self.clear_buttons[row][col]
        if button is not None and button.winfo_ismapped():
            button.pack_forget()
        if self.hovered_cell == (row, col):
            self.hovered_cell = None

    def _pointer_inside_widget(self, parent):
        widget = self.winfo_containing(self.winfo_pointerx(), self.winfo_pointery())
        while widget is not None:
            if widget is parent:
                return True
            widget = widget.master
        return False

    def _clear_client_cell(self, row, col):
        if not self._cell_exists(row, col) or col not in CLEARABLE_CLIENT_COLS:
            return

        self._record_undo_state()
        if col == CLIENT_COL:
            self.row_vars[row][CLIENT_COL].set(self.row_vars[row][SUPLENTE_COL].get().strip())
            self.row_vars[row][SUPLENTE_COL].set("")
        elif col == SUPLENTE_COL:
            self.row_vars[row][SUPLENTE_COL].set("")

        for clear_col in CLEARABLE_CLIENT_COLS:
            button = self.clear_buttons[row][clear_col]
            if button is not None:
                button.pack_forget()

        self._refresh_row_style(row)
        self._refresh_totals()
        self._ensure_blank_row()
        self._apply_filters()
        self._save_rows()

    def _refresh_row_style(self, row):
        if row < 0 or row >= len(self.row_vars):
            return

        bg = self._row_bg(row)
        if row < len(self.index_frames):
            self.index_frames[row].configure(bg=COLORS["table_header"])
        if row < len(self.index_labels):
            self.index_labels[row].configure(bg=COLORS["table_header"])
        if row < len(self.time_check_buttons):
            self.time_check_buttons[row].configure(
                bg=COLORS["table_header"],
                activebackground=COLORS["table_header"],
            )
        for col in range(len(COLUMNS)):
            cell = self.cell_frames[row][col]
            entry = self.cell_entries[row][col]
            cell_bg = COLORS["duplicate_cell"] if col == CODE_COL and self._is_duplicate_code_row(row) else bg
            cell.configure(bg=cell_bg)
            entry.configure(bg=cell_bg, fg=COLORS["text"])
            button = self.clear_buttons[row][col]
            if button is not None:
                button.configure(bg=bg)
        if self.active_cell:
            self._refresh_cell_border(*self.active_cell)

    def _row_bg(self, row):
        if row < 0 or row >= len(self.row_vars):
            return COLORS["table_bg"]
        return COLORS["sold_row"] if self.row_vars[row][CLIENT_COL].get().strip() else COLORS["table_bg"]

    def _duplicate_code_groups(self):
        grouped = defaultdict(list)
        display_codes = {}
        for row_index, row_vars in enumerate(self.row_vars):
            code = row_vars[CODE_COL].get().strip()
            if not code:
                continue
            key = code.casefold()
            grouped[key].append(row_index)
            display_codes.setdefault(key, code)
        return [
            (display_codes[key], rows)
            for key, rows in grouped.items()
            if len(rows) > 1
        ]

    def _is_duplicate_code_row(self, row):
        if not self.live_running or not self._cell_exists(row, CODE_COL):
            return False
        code = self.row_vars[row][CODE_COL].get().strip()
        if not code:
            return False
        key = code.casefold()
        matches = [
            row_index
            for row_index, row_vars in enumerate(self.row_vars)
            if row_vars[CODE_COL].get().strip().casefold() == key
        ]
        return len(matches) > 1

    def _refresh_duplicate_code_alert(self):
        if not hasattr(self, "duplicate_code_alert"):
            return

        groups = self._duplicate_code_groups() if self.live_running else []
        for row_index in range(len(self.row_vars)):
            self._refresh_duplicate_code_cell(row_index)

        if not groups:
            if self.duplicate_code_alert.winfo_ismapped():
                self.duplicate_code_alert.pack_forget()
            return

        pieces = []
        for code, rows in groups[:5]:
            row_numbers = ", ".join(str(row + 1) for row in rows)
            pieces.append(f"{code}: peças {row_numbers}")
        extra = "" if len(groups) <= 5 else f" + {len(groups) - 5} outro(s)"
        self.duplicate_code_alert_label.configure(
            text=f"Atenção: códigos repetidos na live em andamento - {'; '.join(pieces)}{extra}"
        )
        if not self.duplicate_code_alert.winfo_ismapped():
            self.duplicate_code_alert.pack(fill="x", padx=24, pady=(0, 10), before=self.table_shell)

    def _refresh_duplicate_code_cell(self, row):
        if not self._cell_exists(row, CODE_COL):
            return
        bg = COLORS["duplicate_cell"] if self._is_duplicate_code_row(row) else self._row_bg(row)
        self.cell_frames[row][CODE_COL].configure(bg=bg)
        self.cell_entries[row][CODE_COL].configure(bg=bg)

    def _ensure_blank_row(self):
        if not self.row_vars:
            self._add_row()
            return
        if any(var.get().strip() for var in self.row_vars[-1]):
            self._add_row()

    def _cell_exists(self, row, col):
        return 0 <= row < len(self.row_vars) and 0 <= col < len(COLUMNS)

    def _rows(self, include_empty=False):
        rows = []
        for row_vars in self.row_vars:
            row = {column: row_vars[col].get().strip() for col, column in enumerate(COLUMNS)}
            if include_empty or any(row.values()):
                rows.append(row)
        return rows

    def _read_legacy_data(self):
        # Apenas migração: não atualizar, copiar nem apagar os arquivos antigos.
        def read_pair(primary, backup, validator, default):
            for path in (primary, backup):
                data = self._read_json_file(path)
                if validator(data):
                    return data
            if primary.exists() or backup.exists():
                raise ValueError("Legacy data could not be read safely")
            return default

        def valid_history(data):
            lives = data if isinstance(data, list) else data.get("lives") if isinstance(data, dict) else None
            return isinstance(lives, list) and all(isinstance(live, dict) for live in lives)

        history = read_pair(HISTORY_PATH, HISTORY_BACKUP_PATH, valid_history, [])
        state = read_pair(AUTOSAVE_PATH, AUTOSAVE_BACKUP_PATH,
                          lambda value: isinstance(value, dict) and isinstance(value.get("rows"), list)
                          and isinstance(value.get("live"), dict), {})
        return history if isinstance(history, list) else history["lives"], state

    def _sheet_data(self):
        return {
            "live": {
                "id": self.live_id,
                "running": self.live_running,
                "first_started_at": (
                    self.live_first_started_at.isoformat(timespec="seconds")
                    if self.live_first_started_at
                    else None
                ),
                "started_at": self.live_started_at.isoformat(timespec="seconds") if self.live_started_at else None,
                "finished_at": self.live_finished_at.isoformat(timespec="seconds") if self.live_finished_at else None,
                "elapsed_seconds": self.live_elapsed_seconds,
            },
            "rows": self._rows(),
        }

    def _save_rows(self, show_error=True):
        if self.applying_cloud:
            return True
        if not self.live_id and not self.live_running and not self._rows():
            return True
        if not self.live_id:
            self.live_id = f"live_{uuid.uuid4().hex}"
            if self.sync_manager:
                self.sync_manager.begin_edit(self.live_id)
        data = self._sheet_data()
        if data == self.last_saved_sheet and not self.last_save_failed:
            return True
        self.sheet_dirty = True
        history = None
        if self.live_finished_at is not None and not self.live_running:
            history = self._current_live_history_record(self.live_finished_at)
        # A cópia temporária é criptografada pelo serviço antes de confirmar o save.
        snapshot = {**data, "updated_at": datetime.now().isoformat(timespec="seconds")}
        saved = bool(self.sync_manager and self.sync_manager.queue_state(snapshot, history=history))
        self.last_save_failed = not saved
        if saved:
            self.last_saved_sheet = data
        elif show_error:
            messagebox.showerror(
                "Alterações ainda não protegidas",
                "Não foi possível guardar as alterações na fila protegida. "
                "Não feche o programa nem limpe a planilha. Tente sincronizar novamente.",
            )
        return saved

    def _read_json_file(self, path):
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None

    def _restore_live_state(self, data):
        live = data.get("live", {}) if isinstance(data, dict) else {}
        self.live_id = live.get("id") or None
        try:
            self.live_elapsed_seconds = max(0, int(live.get("elapsed_seconds", 0) or 0))
        except (TypeError, ValueError):
            self.live_elapsed_seconds = 0

        self.live_running = bool(live.get("running"))
        first_started_at = live.get("first_started_at")
        started_at = live.get("started_at")
        finished_at = live.get("finished_at")
        self.live_first_started_at = None
        self.live_started_at = None
        self.live_finished_at = None

        if first_started_at:
            try:
                self.live_first_started_at = datetime.fromisoformat(first_started_at)
            except ValueError:
                self.live_first_started_at = None

        if self.live_running and started_at:
            try:
                self.live_started_at = datetime.fromisoformat(started_at)
            except ValueError:
                self.live_running = False

        if finished_at:
            try:
                self.live_finished_at = datetime.fromisoformat(finished_at)
            except ValueError:
                self.live_finished_at = None

        if self.live_running and self.live_started_at is None:
            self.live_running = False

    def _load_saved_rows(self, data):
        rows = data.get("rows", []) if isinstance(data, dict) else []
        if not rows:
            return False

        for row in rows:
            values = [self._saved_row_value(row, col) for col in COLUMNS]
            values[VALUE_COL] = self._format_money_input(values[VALUE_COL])
            self._add_row(values)

        self._ensure_blank_row()
        return True

    def _saved_row_value(self, row, column):
        if column == "suplente":
            values = []
            for key in ("suplente", "suplente1", "suplente2"):
                value = str(row.get(key, "") or "").strip()
                if value and value not in values:
                    values.append(value)
            return " / ".join(values)
        return str(row.get(column, "")).strip()

    def _read_history(self):
        return self.sync_manager.history() if self.sync_manager else []

    def _write_history(self, lives, deleted_live_ids=None):
        if not self.sync_manager:
            return False
        if deleted_live_ids:
            if not self.sync_manager.is_admin:
                return False
            return all(self.sync_manager.queue_delete(live_id, immediate=True) for live_id in deleted_live_ids)
        return self.sync_manager.queue_history(lives)

    def _history_commission_amount(self, live):
        total = self._parse_money(str(live.get("total", "0") if isinstance(live, dict) else "0"))
        return (total * Decimal("0.10")).quantize(Decimal("0.01")) + Decimal("50.00")

    def _history_commission_value(self, live):
        return self._format_money(self._history_commission_amount(live))

    def _history_month_groups(self, lives):
        month_names = (
            "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
            "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
        )
        dated_lives = [
            (
                self._parse_history_datetime(live.get("finished_at"))
                or self._parse_history_datetime(live.get("started_at")),
                live,
            )
            for live in lives
        ]
        dated_lives.sort(key=lambda item: item[0].isoformat() if item[0] else "", reverse=True)
        groups = {}
        for date, live in dated_lives:
            key = (date.year, date.month) if date else (0, 0)
            if key not in groups:
                groups[key] = {
                    "key": key,
                    "label": f"{month_names[date.month - 1]} de {date.year}" if date else "Sem data",
                    "lives": [],
                    "total": Decimal("0"),
                    "commission": Decimal("0"),
                }
            group = groups[key]
            group["lives"].append(live)
            group["total"] += self._parse_money(str(live.get("total", "0")))
            group["commission"] += self._history_commission_amount(live)
        return [groups[key] for key in sorted(groups, reverse=True)]

    def _current_live_stats(self):
        rows = self._rows()
        presented_rows = [row for row in rows if row["valor"].strip() or row["codigo"].strip()]
        sold_rows = [row for row in rows if row["cliente"].strip()]
        total = sum((self._parse_money(row["valor"]) for row in sold_rows), Decimal("0"))
        presented_total = sum((self._parse_money(row["valor"]) for row in presented_rows), Decimal("0"))
        presented_count = len(presented_rows)
        presented_average = presented_total / Decimal(presented_count) if presented_count else Decimal("0")
        clients = sorted({row["cliente"].strip() for row in sold_rows if row["cliente"].strip()}, key=str.lower)
        return {
            "pieces_count": len(rows),
            "presented_count": presented_count,
            "sold_count": len(sold_rows),
            "clients_count": len(clients),
            "clients": clients,
            "total": total,
            "presented_total": presented_total,
            "presented_average": presented_average,
        }

    def _refresh_totals(self):
        if not hasattr(self, "total_sold_label"):
            return
        stats = self._current_live_stats()
        self.total_sold_label.configure(text=f"Vendido: {self._format_money(stats['total'])}")

    def _current_live_history_record(self, finished_at=None):
        stats = self._current_live_stats()
        finished_at = finished_at or self.live_finished_at or datetime.now()
        started_at = self.live_first_started_at or self.live_started_at or finished_at
        live_id = self.live_id or f"live_{started_at.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        self.live_id = live_id
        return {
            "id": live_id,
            "started_at": started_at.isoformat(timespec="seconds"),
            "finished_at": finished_at.isoformat(timespec="seconds"),
            "duration": self._format_elapsed(self.live_elapsed_seconds),
            "pieces_count": stats["pieces_count"],
            "sold_count": stats["sold_count"],
            "clients_count": stats["clients_count"],
            "clients": stats["clients"],
            "total": self._format_money(stats["total"]),
            "rows": self._rows(),
        }

    def _upsert_current_live_history(self, finished_at=None):
        if not self._rows():
            return
        record = self._current_live_history_record(finished_at)
        lives = self._read_history()
        replaced = False
        for index, live in enumerate(lives):
            if live.get("id") == record["id"]:
                if live == record:
                    return
                lives[index] = record
                replaced = True
                break
        if not replaced:
            lives.append(record)
        lives.sort(key=lambda live: live.get("finished_at", ""), reverse=True)
        self._write_history(lives)

    def show_cloud_lives(self):
        if not self.sync_manager:
            return
        self.sync_manager.sync_now()
        states = self.sync_manager.current_states()
        if not states:
            messagebox.showinfo(
                "Lives e rascunhos",
                "Não há live em andamento ou rascunho carregado. "
                "Se acabou de conectar, aguarde a sincronização e tente novamente.",
            )
            return
        window = tk.Toplevel(self)
        window.title("Retomar live / rascunho")
        window.geometry("660x340")
        window.configure(bg=COLORS["app_bg"])
        tk.Label(window, text="Escolha a live que deseja continuar neste computador.",
                 bg=COLORS["app_bg"], fg=COLORS["text"], font=("Segoe UI", 11)).pack(padx=18, pady=15)
        tree = ttk.Treeview(window, columns=("date", "status", "pieces"), show="headings", selectmode="browse")
        tree.heading("date", text="Início")
        tree.heading("status", text="Situação")
        tree.heading("pieces", text="Linhas")
        tree.pack(fill="both", expand=True, padx=18)
        for index, data in enumerate(states):
            live = data.get("live", {})
            tree.insert("", "end", iid=str(index), values=(
                self._format_history_datetime(live.get("first_started_at")) or "Rascunho não iniciado",
                "Em andamento" if live.get("running") else "Rascunho",
                len(data.get("rows", [])),
            ))

        def resume():
            selection = tree.selection()
            if not selection:
                return
            self._finish_active_cell()
            if self.live_running:
                messagebox.showwarning("Live em andamento", "Finalize a live aberta antes de trocar de planilha.", parent=window)
                return
            if not messagebox.askyesno(
                "Continuar neste computador?",
                "Abra esta live somente se ninguém estiver editando a mesma planilha em outro computador. "
                "Se duas pessoas alterarem, o app preserva a fila e avisa sobre o conflito.\n\n"
                "Os dados da planilha atual serão guardados antes da troca. Continuar?", parent=window,
            ):
                return
            if not self._save_rows():
                return
            selected_id = states[int(selection[0])].get("live", {}).get("id")
            current = next((state for state in self.sync_manager.current_states()
                            if state.get("live", {}).get("id") == selected_id), None)
            if current is None:
                messagebox.showinfo("Live atualizada", "Essa live foi finalizada ou removida. Atualize a lista.", parent=window)
                return
            self._apply_saved_state(current)
            window.destroy()

        ttk.Button(window, text="Retomar selecionada", command=resume, style="Primary.TButton").pack(pady=15)
        if self.sync_manager.is_admin:
            def delete_draft():
                selection = tree.selection()
                if not selection:
                    return
                live_id = states[int(selection[0])].get("live", {}).get("id")
                if live_id == self.live_id:
                    messagebox.showwarning("Planilha aberta", "Use 'Nova live' para fechar esta planilha antes de excluí-la.", parent=window)
                    return
                if not messagebox.askyesno(
                    "Excluir live / rascunho?",
                    "Essa ação exclui definitivamente a live e suas peças do Supabase. Continuar?", parent=window,
                ):
                    return
                if not self.sync_manager.queue_delete(live_id, immediate=True):
                    messagebox.showerror("Exclusão não enviada", "Verifique a conexão e seu acesso de administrador.", parent=window)
                    return
                messagebox.showinfo("Exclusão solicitada", "Aguarde a confirmação do Supabase antes de considerar a live excluída.", parent=window)
                window.destroy()

            ttk.Button(window, text="Excluir selecionada", command=delete_draft,
                       style="Secondary.TButton").pack(pady=(0, 12))

    def show_cloud_conflicts(self):
        conflicts = self.sync_manager.conflicts() if self.sync_manager else []
        if not conflicts:
            messagebox.showinfo("Sem conflitos", "Nenhuma alteração precisa de revisão.")
            return
        window = tk.Toplevel(self)
        window.title("Revisar conflitos")
        window.geometry("700x400")
        window.configure(bg=COLORS["app_bg"])
        tk.Label(window, text="As alterações deste computador estão protegidas e não sobrescreveram o banco. "
                 "Se precisar conservar essa versão, abra a planilha ou o histórico e exporte para Excel antes de descartar.",
                 bg=COLORS["app_bg"], fg=COLORS["warning_text"], wraplength=650,
                 font=("Segoe UI Semibold", 11)).pack(padx=18, pady=16)
        tree = ttk.Treeview(window, columns=("id", "reason"), show="headings", selectmode="browse")
        tree.heading("id", text="Live")
        tree.heading("reason", text="Motivo")
        tree.pack(fill="both", expand=True, padx=18)
        reasons = {
            "versao": "Alterada em outro computador",
            "excluida": "Excluída no Supabase",
            "migracao": "Backup diferente da versão no Supabase",
        }
        for index, conflict in enumerate(conflicts):
            tree.insert("", "end", iid=str(index), values=(conflict["id"],
                        reasons.get(conflict.get("motivo"), "Alteração em outro computador")))

        def discard():
            selected = tree.selection()
            if not selected:
                return
            conflict_id = conflicts[int(selected[0])]["id"]
            if not messagebox.askyesno(
                "Descartar alterações deste computador?",
                "Isso descarta as alterações pendentes desta live neste computador e mantém a versão do Supabase. "
                "Não será possível recuperar essas alterações pela fila. Deseja continuar?", parent=window,
            ):
                return
            if not self.sync_manager.discard_conflict(conflict_id):
                messagebox.showerror("Não foi possível descartar", "A fila protegida não pôde ser atualizada. Tente novamente.", parent=window)
                return
            if self.live_id == conflict_id:
                document = self.sync_manager.remote_document(conflict_id) or {}
                state = document.get("estado")
                self._apply_saved_state(state or {})
                if state is None:
                    live = document.get("historico")
                    if live is not None:
                        self._load_history_live_into_main_sheet(live)
            window.destroy()
            self.sync_manager.sync_now()

        ttk.Button(window, text="Descartar minha alteração e usar Supabase", command=discard,
                   style="Secondary.TButton").pack(pady=15)

    def show_history(self):
        lives = self._read_history()
        if self.sync_manager:
            self.sync_manager.sync_now()
        window = tk.Toplevel(self)
        window.title("Histórico de lives - IoMarques Brechó")
        window.geometry("1000x520")
        window.minsize(780, 360)
        window.configure(bg=COLORS["app_bg"])

        top = tk.Frame(window, bg=COLORS["primary"])
        top.pack(fill="x")
        tk.Label(
            top,
            text="Histórico de lives",
            bg=COLORS["primary"],
            fg="#FFFFFF",
            font=("Segoe UI Semibold", 17),
        ).pack(side="left", padx=18, pady=12)

        columns = ("duration", "pieces_count", "sold_count", "clients_count", "total", "commission")
        headings = {
            "duration": "Duração",
            "pieces_count": "Peças",
            "sold_count": "Vendidas",
            "clients_count": "Clientes",
            "total": "Total vendido",
            "commission": "10% + R$ 50",
        }
        table = tk.Frame(window, bg=COLORS["app_bg"])
        table.pack(fill="both", expand=True, padx=18, pady=18)
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        tree = ttk.Treeview(table, columns=columns, show="tree headings", selectmode="browse")
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal_scrollbar = ttk.Scrollbar(table, orient="horizontal", command=tree.xview)
        horizontal_scrollbar.grid(row=1, column=0, sticky="ew")
        tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal_scrollbar.set)
        tree.heading("#0", text="Mês / Finalizada em", anchor="w")
        tree.column("#0", width=275, minwidth=250, anchor="w")
        tree.tag_configure(
            "month",
            background=COLORS["secondary"],
            foreground=COLORS["text"],
            font=("Segoe UI", 10, "bold"),
        )

        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=75, minwidth=65, anchor="center")
        tree.column("duration", width=105, minwidth=85)
        tree.column("total", width=150, minwidth=130, anchor="e")
        tree.column("commission", width=140, minwidth=120, anchor="e")

        actions = tk.Frame(window, bg=COLORS["app_bg"])
        actions.pack(fill="x", padx=18, pady=(0, 18))

        empty = tk.Label(
            table,
            text="Nenhuma live finalizada ainda.",
            bg=COLORS["app_bg"],
            fg=COLORS["text"],
            font=("Segoe UI", 11),
        )

        item_to_live = {}

        def selected_live():
            selection = tree.selection()
            if not selection:
                return None
            return item_to_live.get(selection[0])

        def refresh_actions(_event=None):
            state = "!disabled" if selected_live() is not None else "disabled"
            open_button.state([state])
            admin = bool(self.sync_manager and self.sync_manager.is_admin)
            delete_button.state([state if admin else "disabled"])
            if admin:
                delete_button.pack(side="right")
            else:
                delete_button.pack_forget()

        def refresh_empty_state():
            empty.configure(text="Nenhuma live finalizada ainda." if self.sync_manager and self.sync_manager.cloud_loaded
                            else "Conecte ao Supabase e aguarde o carregamento do histórico.")
            if lives:
                empty.place_forget()
            else:
                empty.place(relx=0.5, rely=0.5, anchor="center")
            refresh_actions()

        def populate_history():
            nonlocal lives
            selected = selected_live()
            selected_id = selected.get("id") if selected else None
            lives = self._read_history()
            expanded = {item: tree.item(item, "open") for item in tree.get_children()}
            if expanded:
                tree.delete(*expanded)
            item_to_live.clear()
            for group in self._history_month_groups(lives):
                year, month = group["key"]
                month_id = f"month_{year}_{month}"
                count = len(group["lives"])
                tree.insert(
                    "",
                    "end",
                    iid=month_id,
                    text=f"{group['label']} ({count} {'live' if count == 1 else 'lives'})",
                    open=expanded.get(month_id, True),
                    tags=("month",),
                    values=(
                        "", "", "", "",
                        self._format_money(group["total"]),
                        self._format_money(group["commission"]),
                    ),
                )
                for index, live in enumerate(group["lives"]):
                    item_id = f"{month_id}_live_{index}"
                    item_to_live[item_id] = live
                    tree.insert(
                        month_id,
                        "end",
                        iid=item_id,
                        text=self._format_history_datetime(live.get("finished_at", "")) or "Sem data",
                        values=(
                            live.get("duration", ""),
                            live.get("pieces_count", 0),
                            live.get("sold_count", 0),
                            live.get("clients_count", 0),
                            live.get("total", "R$ 0,00"),
                            self._history_commission_value(live),
                        ),
                    )
                    if live.get("id") == selected_id:
                        tree.selection_set(item_id)
            refresh_empty_state()

        def delete_selected_history():
            nonlocal lives

            if not self.sync_manager or not self.sync_manager.is_admin:
                return

            live = selected_live()
            if live is None:
                messagebox.showinfo("Selecione uma live", "Escolha uma live do histórico para excluir.")
                return
            if live.get("id") == self.live_id:
                messagebox.showwarning("Planilha aberta", "Use 'Nova live' para fechar esta planilha antes de excluí-la.", parent=window)
                return

            finished_at = self._format_history_datetime(live.get("finished_at", "")) or "data não informada"
            total = live.get("total", "R$ 0,00")
            answer = messagebox.askyesno(
                "Excluir live do histórico?",
                (
                    f"Excluir a live finalizada em {finished_at}, com total {total}?\n\n"
                    "Essa ação exclui a live, suas peças e os dados vinculados no Supabase. "
                    "Ela só desaparece desta lista depois da confirmação do banco."
                ),
            )
            if not answer:
                return

            current_lives = self._read_history()
            live_id = live.get("id")
            if live_id:
                lives = [record for record in current_lives if record.get("id") != live_id]
            else:
                removed = False
                lives = []
                for record in current_lives:
                    if not removed and record == live:
                        removed = True
                        continue
                    lives.append(record)

            deleted_ids = [live_id] if live_id else []
            if not self._write_history(lives, deleted_live_ids=deleted_ids):
                messagebox.showerror("Não foi possível solicitar a exclusão", "Verifique a conexão e seu acesso de administrador.", parent=window)
                return
            messagebox.showinfo("Exclusão solicitada", "Aguardando confirmação do Supabase. A lista será atualizada automaticamente.", parent=window)
            populate_history()

        def open_selected_history():
            live = selected_live()
            if live is None:
                messagebox.showinfo("Selecione uma live", "Escolha uma live do histórico para abrir.")
                return
            if self._load_history_live_into_main_sheet(live):
                window.destroy()

        open_button = ttk.Button(
            actions,
            text="Abrir na planilha principal",
            command=open_selected_history,
            style="Primary.TButton",
        )
        open_button.pack(side="left")

        delete_button = ttk.Button(
            actions,
            text="Excluir live selecionada",
            command=delete_selected_history,
            style="Secondary.TButton",
        )

        ttk.Button(actions, text="Atualizar", command=self.sync_manager.sync_now if self.sync_manager else lambda: None,
                   style="Secondary.TButton").pack(side="left", padx=8)

        def double_click_history(event):
            item_id = tree.identify_row(event.y)
            if item_id in item_to_live and tree.identify_region(event.x, event.y) in ("tree", "cell"):
                tree.selection_set(item_id)
                open_selected_history()
                return "break"

        def activate_history(_event):
            if selected_live() is not None:
                open_selected_history()
            elif tree.selection():
                item_id = tree.selection()[0]
                tree.item(item_id, open=not tree.item(item_id, "open"))
            return "break"

        tree.bind("<<TreeviewSelect>>", refresh_actions)
        tree.bind("<Double-1>", double_click_history)
        tree.bind("<Return>", activate_history)
        tree.bind("<Delete>", lambda _event: delete_selected_history() if selected_live() is not None else None)

        self.history_refreshers[window] = populate_history
        window.bind("<Destroy>", lambda event: self.history_refreshers.pop(window, None) if event.widget is window else None)
        populate_history()

    def _load_history_live_into_main_sheet(self, live):
        rows = live.get("rows") if isinstance(live, dict) else []
        if not rows:
            messagebox.showinfo(
                "Planilha indisponível",
                "Essa live foi salva no histórico antes do app guardar as linhas detalhadas.",
            )
            return False

        self._finish_active_cell()
        if self.live_running:
            messagebox.showwarning(
                "Live em andamento",
                "Finalize a live atual antes de abrir uma live do histórico na planilha principal.",
            )
            return False

        if self._rows():
            messagebox.showwarning(
                "Planilha principal ocupada",
                (
                    "A planilha principal já tem dados.\n\n"
                    "Use 'Nova live' antes de abrir uma live do histórico."
                ),
            )
            return False

        self._apply_saved_state({
            "live": {
                "id": live.get("id"), "running": False,
                "first_started_at": live.get("started_at"), "started_at": None,
                "finished_at": live.get("finished_at"),
                "elapsed_seconds": self._history_elapsed_seconds(live),
            },
            "rows": rows,
        }, history=live)
        self.search_var.set("")
        self._hide_search()
        for var in self.filter_vars:
            var.set("")
        self._apply_filters()
        self.canvas.yview_moveto(0)
        return True

    def _history_elapsed_seconds(self, live):
        duration = str(live.get("duration", "") if isinstance(live, dict) else "").strip()
        parts = duration.split(":")
        if len(parts) == 3:
            try:
                hours, minutes, seconds = [int(part) for part in parts]
                return max(0, hours * 3600 + minutes * 60 + seconds)
            except ValueError:
                pass

        started_at = self._parse_history_datetime(live.get("started_at", "") if isinstance(live, dict) else "")
        finished_at = self._parse_history_datetime(live.get("finished_at", "") if isinstance(live, dict) else "")
        if started_at and finished_at:
            return max(0, int((finished_at - started_at).total_seconds()))
        return 0

    def _parse_history_datetime(self, value):
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None

    def _show_history_live(self, live):
        rows = live.get("rows") if isinstance(live, dict) else []
        if not rows:
            messagebox.showinfo(
                "Planilha indisponível",
                "Essa live foi salva no histórico antes do app guardar as linhas detalhadas.",
            )
            return

        window = tk.Toplevel(self)
        window.title("Planilha da live - IoMarques Brechó")
        window.geometry("980x560")
        window.configure(bg=COLORS["app_bg"])

        top = tk.Frame(window, bg=COLORS["primary"])
        top.pack(fill="x")
        title = (
            f"Live {self._format_history_datetime(live.get('finished_at', ''))}"
            f" | {live.get('total', 'R$ 0,00')}"
        )
        tk.Label(
            top,
            text=title,
            bg=COLORS["primary"],
            fg="#FFFFFF",
            font=("Segoe UI Semibold", 16),
        ).pack(side="left", padx=18, pady=12)

        columns = ("peca",) + COLUMNS
        tree = ttk.Treeview(window, columns=columns, show="headings")
        tree.pack(fill="both", expand=True, padx=18, pady=18)

        headings = {"peca": "Peça", **HEADERS}
        widths = {"peca": 65, "valor": 120, "codigo": 110, "cliente": 230, "suplente": 220, "tempo": 110}
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths.get(column, 130), anchor="w")

        for index, row in enumerate(rows, start=1):
            tree.insert(
                "",
                "end",
                values=(
                    index,
                    self._saved_row_value(row, "valor"),
                    self._saved_row_value(row, "codigo"),
                    self._saved_row_value(row, "cliente"),
                    self._saved_row_value(row, "suplente"),
                    self._saved_row_value(row, "tempo"),
                ),
            )

    def _format_history_datetime(self, value):
        parsed = self._parse_history_datetime(value)
        return parsed.strftime("%d/%m/%Y %H:%M") if parsed else value

    def start_live(self):
        if (self.live_running or not self.sync_manager or not self.sync_manager.ready
                or (self.live_id and self.sync_manager.is_deleted(self.live_id))):
            return
        now = datetime.now()
        if not self.live_id:
            self.live_id = f"live_{uuid.uuid4().hex}"
            self.sync_manager.begin_edit(self.live_id)
        if self.live_first_started_at is None:
            self.live_first_started_at = now
        self.live_started_at = now
        self.live_finished_at = None
        self.live_running = True
        self._refresh_live_controls()
        self._save_rows()
        self._schedule_timer_tick()

    def finish_live(self):
        if not self.live_running or (self.live_id and self.sync_manager.is_deleted(self.live_id)):
            return
        answer = messagebox.askyesno(
            "Finalizar live?",
            "Tem certeza que deseja finalizar a live agora?",
        )
        if not answer:
            return
        finished_at = datetime.now()
        self.live_elapsed_seconds = self._current_elapsed_seconds()
        self.live_started_at = None
        self.live_finished_at = finished_at
        self.live_running = False
        if self.timer_job is not None:
            self.after_cancel(self.timer_job)
            self.timer_job = None
        self._refresh_live_controls()
        self._save_rows()

    def _refresh_live_controls(self):
        elapsed = self._current_elapsed_seconds()
        self.timer_label.configure(text=self._format_elapsed(elapsed))
        self.pre_live_frame.pack_forget()
        self.live_frame.pack_forget()
        self.post_live_frame.pack_forget()
        if self.live_running:
            self.brand_header.pack_forget()
            self.live_status_label.configure(text="Live em andamento")
            self.live_frame.pack(side="left", fill="x")
            self.start_button.state(["disabled"])
            self.finish_button.state(["!disabled"])
        elif elapsed > 0 or self.live_finished_at is not None:
            if not self.brand_header.winfo_ismapped():
                self.brand_header.pack(fill="x", before=self.toolbar)
            self.live_status_label.configure(text="Live finalizada")
            self.post_live_frame.pack(side="left", fill="x")
            self.start_button.state(["!disabled"])
            self.finish_button.state(["disabled"])
        else:
            if not self.brand_header.winfo_ismapped():
                self.brand_header.pack(fill="x", before=self.toolbar)
            self.live_status_label.configure(text="Aguardando início")
            self.pre_live_frame.pack(side="left", fill="x")
            self.start_button.state(["!disabled"])
            self.finish_button.state(["disabled"])
        self._refresh_duplicate_code_alert()
        self._refresh_cloud_editability()

    def _current_live_status(self):
        if self.live_running:
            return "Em andamento"
        if self.live_elapsed_seconds > 0:
            return "Finalizada"
        return "Não iniciada"

    def _schedule_timer_tick(self):
        self._refresh_live_controls()
        if self.live_running:
            self.timer_job = self.after(1000, self._schedule_timer_tick)
        else:
            self.timer_job = None

    def _schedule_periodic_autosave(self):
        self._save_rows(show_error=False)
        self.autosave_job = self.after(AUTOSAVE_INTERVAL_MS, self._schedule_periodic_autosave)

    def _current_elapsed_seconds(self):
        elapsed = self.live_elapsed_seconds
        if self.live_running and self.live_started_at is not None:
            now = datetime.now(self.live_started_at.tzinfo)
            elapsed += max(0, int((now - self.live_started_at).total_seconds()))
        return elapsed

    def _format_elapsed(self, seconds):
        seconds = max(0, int(seconds))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def _parse_money_optional(self, value):
        clean = value.strip().replace("R$", "").replace("r$", "").replace(" ", "")
        if not clean:
            return None

        if "," in clean:
            clean = clean.replace(".", "").replace(",", ".")
        elif "." in clean:
            parts = clean.split(".")
            if len(parts) == 2 and 1 <= len(parts[1]) <= 2:
                clean = ".".join(parts)
            else:
                clean = clean.replace(".", "")

        try:
            return Decimal(clean)
        except InvalidOperation:
            return None

    def _parse_money(self, value):
        amount = self._parse_money_optional(value)
        return amount if amount is not None else Decimal("0")

    def _format_money_input(self, value):
        if not value.strip():
            return ""
        amount = self._parse_money_optional(value)
        if amount is None:
            return value.strip()
        return self._format_money(amount)

    def _format_money(self, value):
        quantized = Decimal(value).quantize(Decimal("0.01"))
        return f"R$ {quantized:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    def _summary(self):
        grouped = defaultdict(list)
        for row in self._rows():
            cliente = row["cliente"].strip()
            if not cliente:
                continue
            valor = self._parse_money(row["valor"])
            grouped[cliente].append(
                {
                    "codigo": row["codigo"],
                    "tempo": row["tempo"],
                    "valor_texto": row["valor"],
                    "valor": valor,
                    "suplente": row["suplente"],
                }
            )
        return dict(sorted(grouped.items(), key=lambda item: item[0].lower()))

    def _summary_text(self):
        sections = self._summary_sections()
        if not sections:
            return "Nenhuma peça com cliente titular ainda.\n"

        lines = []
        for index, section in enumerate(sections):
            if index > 0:
                lines.append(SUMMARY_SEPARATOR)
            lines.append(section["header"])
            lines.extend(section["items"])
        lines.append(SUMMARY_SEPARATOR)
        lines.extend(self._summary_totals_lines())

        return "\n".join(lines) + "\n"

    def _summary_totals_lines(self):
        stats = self._current_live_stats()
        return [
            f"Clientes: {stats['clients_count']}",
            f"Peças vendidas: {stats['sold_count']}",
            f"Total vendido: {self._format_money(stats['total'])}",
            "",
            f"Peças apresentadas: {stats['presented_count']}",
            f"Valor total das peças apresentadas: {self._format_money(stats['presented_total'])}",
            f"Valor médio das peças apresentadas: {self._format_money(stats['presented_average'])}",
        ]

    def _summary_sections(self):
        sections = []
        for cliente, items in self._summary().items():
            subtotal = sum((item["valor"] for item in items), Decimal("0"))
            codes = ", ".join(item["codigo"] or "sem código" for item in items)
            item_lines = []
            for item in items:
                codigo = item["codigo"] or "sem código"
                tempo = item["tempo"] or "-"
                valor = self._format_money(item["valor"])
                suplente = item.get("suplente", "").strip()
                suplente_text = f"   sup: {suplente}" if suplente else ""
                item_lines.append(f"{CHECKBOX_TEXT} {codigo} - {tempo} - {valor}{suplente_text}")
            sections.append(
                {
                    "cliente": cliente,
                    "header": f"{cliente} - {codes} | Total = {self._format_money(subtotal)}",
                    "items": item_lines,
                }
            )
        return sections

    def _alternates_text(self):
        rows = [row for row in self._rows() if row.get("suplente", "").strip()]
        if not rows:
            return "Nenhuma suplente registrada.\n"

        lines = []
        for row in rows:
            codigo = row["codigo"] or "sem código"
            tempo = row["tempo"] or "-"
            valor = self._format_money(self._parse_money(row["valor"]))
            cliente = row["cliente"] or "sem cliente"
            suplente = row["suplente"]
            lines.append(f"{suplente} - peça {codigo} - {valor}")
            lines.append(f"  Titular: {cliente} | Tempo: {tempo}")
            lines.append("")
        return "\n".join(lines) + "\n"

    def _client_message_text(self, items, cliente):
        subtotal = sum((item["valor"] for item in items), Decimal("0"))
        piece_lines = []
        for item in items:
            codigo = item["codigo"] or "sem código"
            piece_lines.append(f"- Peça {codigo}: {self._format_money(item['valor'])}")
        lines = [
            cliente,
            "",
            "Oi amada!😃",
            "Você arrematou na LIVE ",
            "as seguintes peças:",
            *piece_lines,
            f"e ficou o total de {self._format_money(subtotal)}.",
            "",
            "👉 Você pode optar por fazer Pix",
            "👉 Ou link de pagamento para pagar no cartão de crédito.",
            "",
            "Espero seu retorno com brevidade.",
            "Pois temos muitas vezes fila nas peças.",
            "",
            "Aaaa não esqueça de mandar o comprovante.",
            " Combinado?😉",
        ]
        return "\n".join(lines)

    def _client_messages(self):
        summary = self._summary()
        if not summary:
            return []

        messages = []
        for cliente, items in summary.items():
            instagram = f"@{cliente.strip().lstrip('@').strip()}"
            messages.append((instagram, self._client_message_text(items, instagram)))
        return messages

    def _client_messages_text(self):
        messages = []
        for index, (cliente, message) in enumerate(self._client_messages(), start=1):
            messages.append(f"[ ] {index} - Cliente: {cliente}\n\n{message}")
        if not messages:
            return "Nenhuma peça com cliente titular ainda.\n"
        return "\n\n---\n\n".join(messages) + "\n"

    def _client_message_jobs(self):
        return [
            {"cliente": cliente.strip().lstrip("@"), "mensagem": message}
            for cliente, message in self._client_messages()
            if cliente.strip() and message.strip()
        ]

    def open_message_automation(self):
        self._finish_active_cell()
        jobs = self._client_message_jobs()
        if not jobs:
            messagebox.showinfo("Automatizar mensagens", "Nenhuma peça com cliente titular ainda.")
            return

        automation_app = self._message_automation_app_path()
        if automation_app is None:
            messagebox.showerror(
                "Automatizador não encontrado",
                "Não encontrei o projeto de automação em:\n"
                f"{Path.home() / 'iomarques-instagram-direct'}",
            )
            return

        queue_path = Path(tempfile.gettempdir()) / "iomarques_fila_direct_clientes.json"
        try:
            queue_path.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Automatizar mensagens", f"Não consegui preparar a fila de Directs:\n{exc}")
            return

        self._launch_message_automation(automation_app, queue_path)

    def _message_automation_app_path(self):
        return find_message_automation_app(APP_DIR, Path.home())

    def _launch_message_automation(self, automation_app, queue_path):
        app_args = [str(automation_app), "--queue-file", str(queue_path)]
        commands = []
        if not getattr(sys, "frozen", False):
            commands.append([sys.executable, *app_args])
        if sys.platform.startswith("win"):
            commands.extend((["pythonw", *app_args], ["pyw", *app_args]))
        commands.append(["python", *app_args])

        popen_kwargs = {"cwd": str(automation_app.parent)}
        if sys.platform.startswith("win"):
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        last_error = None
        for command in commands:
            try:
                subprocess.Popen(command, **popen_kwargs)
            except OSError as exc:
                last_error = exc
                continue
            return

        messagebox.showerror(
            "Automatizar mensagens",
            f"Não consegui abrir o automatizador de Directs:\n{last_error}",
        )

    def _clip_text(self, value, width):
        text = str(value).strip()
        if len(text) <= width:
            return text
        if width <= 3:
            return text[:width]
        return text[: width - 3] + "..."

    def _printable_report(self):
        return "\n".join(line for line, _bold in self._printable_report_lines()) + "\n"

    def _printable_report_lines(self):
        now = datetime.now().strftime("%d/%m/%Y %H:%M")
        sections = self._summary_sections()
        lines = [
            (f"IoMarques Brechó - Resumo | {now}", True),
            (f"Duração: {self._format_elapsed(self._current_elapsed_seconds())}", False),
            ("", False),
        ]
        if not sections:
            lines.append(("Nenhuma peça com cliente titular ainda.", False))
            return lines

        for index, section in enumerate(sections):
            if index > 0:
                lines.append((SUMMARY_SEPARATOR, False))
            lines.append((section["header"], True))
            lines.extend((item, False) for item in section["items"])
        lines.append((SUMMARY_SEPARATOR, False))
        lines.extend((line, True) for line in self._summary_totals_lines())
        return lines

    def _wrap_report_lines_for_print(self, print_lines, columns):
        width = REPORT_PRINT_WIDTH_TWO_COLUMNS if columns > 1 else REPORT_PRINT_WIDTH_ONE_COLUMN
        wrapped = []
        for line, bold in print_lines:
            if not line:
                wrapped.append(("", bold))
                continue
            if line == SUMMARY_SEPARATOR:
                wrapped.append(("-" * width, bold))
                continue
            parts = textwrap.wrap(
                line,
                width=width,
                subsequent_indent="  ",
                break_long_words=True,
                break_on_hyphens=False,
            )
            wrapped.extend((part, bold) for part in parts)
        return wrapped

    def show_summary(self):
        self._finish_active_cell()

        window = tk.Toplevel(self)
        window.title("Resumo final - IoMarques Brechó")
        window.geometry("760x540")
        window.configure(bg=COLORS["app_bg"])

        top = tk.Frame(window, bg=COLORS["primary"])
        top.pack(fill="x")
        tk.Label(
            top,
            text="Resumo final",
            bg=COLORS["primary"],
            fg="#FFFFFF",
            font=("Segoe UI Semibold", 17),
        ).pack(side="left", padx=18, pady=12)
        ttk.Button(top, text="Imprimir", command=self.print_report, style="Secondary.TButton").pack(
            side="right", padx=18, pady=10
        )

        text = tk.Text(
            window,
            wrap="word",
            bg="#FFFFFF",
            fg=COLORS["text"],
            relief="flat",
            font=("Consolas", 11),
            padx=16,
            pady=14,
        )
        text.pack(fill="both", expand=True, padx=18, pady=18)
        text.tag_configure("client_header", font=("Consolas", 11, "bold"))
        text.tag_configure("summary_total", font=("Consolas", 11, "bold"))
        self._insert_summary_text(text)
        text.configure(state="disabled")

    def _insert_summary_text(self, text):
        sections = self._summary_sections()
        if not sections:
            text.insert("end", "Nenhuma peça com cliente titular ainda.\n")
            return

        for index, section in enumerate(sections):
            if index > 0:
                text.insert("end", SUMMARY_SEPARATOR + "\n")
            cliente = section.get("cliente", "")
            header = section["header"]
            if cliente and header.startswith(cliente):
                text.insert("end", cliente, "client_header")
                text.insert("end", header[len(cliente) :] + "\n")
            else:
                text.insert("end", header + "\n", "client_header")
            for line in section["items"]:
                text.insert("end", line + "\n")
        text.insert("end", SUMMARY_SEPARATOR + "\n")
        for line in self._summary_totals_lines():
            text.insert("end", line + "\n", "summary_total")

    def show_client_messages(self):
        self._finish_active_cell()

        window = tk.Toplevel(self)
        window.title("Mensagens para clientes - IoMarques Brechó")
        window.geometry("900x540")
        window.configure(bg=COLORS["app_bg"])

        top = tk.Frame(window, bg=COLORS["primary"])
        top.pack(fill="x")
        tk.Label(
            top,
            text="Mensagens para clientes",
            bg=COLORS["primary"],
            fg="#FFFFFF",
            font=("Segoe UI Semibold", 17),
        ).pack(side="left", padx=18, pady=12)

        messages = self._client_messages()
        if not messages:
            tk.Label(
                window,
                text="Nenhuma peça com cliente titular ainda.",
                bg=COLORS["app_bg"],
                fg=COLORS["button_text"],
                font=("Segoe UI", 11),
            ).pack(fill="both", expand=True, padx=18, pady=18)
            return

        list_shell = tk.Frame(window, bg="#FFFFFF", highlightbackground=COLORS["grid"], highlightthickness=1)
        list_shell.pack(fill="both", expand=True, padx=18, pady=18)

        canvas = tk.Canvas(list_shell, bg="#FFFFFF", highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_shell, orient="vertical", command=canvas.yview)
        list_frame = tk.Frame(canvas, bg="#FFFFFF")
        list_window = canvas.create_window((0, 0), window=list_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def configure_scroll(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def configure_width(event):
            canvas.itemconfigure(list_window, width=event.width)

        list_frame.bind("<Configure>", configure_scroll)
        canvas.bind("<Configure>", configure_width)

        copied_bg = "#DDF4E5"
        for index, (cliente, message) in enumerate(messages, start=1):
            row = tk.Frame(list_frame, bg="#FFFFFF", highlightbackground=COLORS["grid"], highlightthickness=1)
            row.pack(fill="x", padx=10, pady=(10 if index == 1 else 0, 8))
            row.grid_columnconfigure(2, weight=1)

            copied_var = tk.BooleanVar(value=False)
            pix_copied_var = tk.BooleanVar(value=False)

            def paint_copied_state(copied_var=copied_var, pix_copied_var=pix_copied_var, row=row):
                bg = copied_bg if copied_var.get() or pix_copied_var.get() else "#FFFFFF"
                row.configure(bg=bg)
                for child in row.winfo_children():
                    if isinstance(child, tk.Checkbutton):
                        child.configure(bg=bg, activebackground=bg)
                    elif isinstance(child, tk.Label):
                        child.configure(bg=bg)
                    elif isinstance(child, tk.Entry):
                        child.configure(readonlybackground=bg)

            check = tk.Checkbutton(
                row,
                variable=copied_var,
                command=paint_copied_state,
                bg="#FFFFFF",
                activebackground="#FFFFFF",
                selectcolor=copied_bg,
                relief="flat",
                bd=0,
                padx=0,
                pady=0,
                cursor="hand2",
                takefocus=False,
            )
            check.grid(row=0, column=0, padx=(10, 6), pady=10)

            index_label = tk.Label(
                row,
                text=f"{index}.",
                bg="#FFFFFF",
                fg=COLORS["text"],
                font=("Segoe UI Semibold", 11),
                anchor="w",
            )
            index_label.grid(row=0, column=1, sticky="w", padx=(0, 4), pady=10)

            name_var = tk.StringVar(value=cliente)
            name_entry = tk.Entry(
                row,
                textvariable=name_var,
                state="readonly",
                readonlybackground="#FFFFFF",
                fg=COLORS["text"],
                relief="flat",
                bd=0,
                font=("Segoe UI Semibold", 11),
                cursor="xterm",
            )
            name_entry.grid(row=0, column=2, sticky="ew", padx=(0, 10), pady=10)

            def copy_message(message=message, copied_var=copied_var, paint_copied_state=paint_copied_state):
                self.clipboard_clear()
                self.clipboard_append(message)
                copied_var.set(True)
                paint_copied_state()

            button = ttk.Button(row, text="Copiar Mensagem", command=copy_message, style="Primary.TButton")
            button.grid(row=0, column=3, padx=(0, 10), pady=8)

            pix_check = tk.Checkbutton(
                row,
                variable=pix_copied_var,
                command=paint_copied_state,
                bg="#FFFFFF",
                activebackground="#FFFFFF",
                selectcolor=copied_bg,
                relief="flat",
                bd=0,
                padx=0,
                pady=0,
                cursor="hand2",
                takefocus=False,
            )
            pix_check.grid(row=0, column=4, padx=(0, 4), pady=10)

            def copy_pix(pix_copied_var=pix_copied_var, paint_copied_state=paint_copied_state):
                self.clipboard_clear()
                self.clipboard_append(PIX_MESSAGE_TEXT)
                pix_copied_var.set(True)
                paint_copied_state()

            pix_button = ttk.Button(row, text="Copiar Pix", command=copy_pix, style="Secondary.TButton")
            pix_button.grid(row=0, column=5, padx=(0, 10), pady=8)

    def export_excel(self):
        self._finish_active_cell()

        if Workbook is None:
            messagebox.showerror(
                "Biblioteca ausente",
                "Instale a biblioteca openpyxl com:\n\npip install openpyxl",
            )
            return

        rows = self._rows()
        if not rows:
            messagebox.showinfo("Nada para exportar", "Preencha pelo menos uma linha antes de exportar.")
            return

        default_name = f"live_iomarques_brecho_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.xlsx"
        path = filedialog.asksaveasfilename(
            title="Salvar planilha da live",
            defaultextension=".xlsx",
            initialfile=default_name,
            filetypes=[("Planilha Excel", "*.xlsx")],
        )
        if not path:
            return

        wb = Workbook()
        sales = wb.active
        sales.title = "Vendas"
        sales.append(["Peça"] + [HEADERS[col] for col in COLUMNS])
        for index, row in enumerate(rows, start=1):
            sales.append([index] + [row[col] for col in COLUMNS])

        summary_sheet = wb.create_sheet("Resumo por cliente")
        summary_sheet.append(["Peça", "Cliente", "Código", "Tempo", "Valor", "Total da cliente"])
        summary = self._summary()
        grand_total = Decimal("0")
        piece_index = 1
        for cliente, items in summary.items():
            subtotal = sum((item["valor"] for item in items), Decimal("0"))
            grand_total += subtotal
            first_row = True
            for item in items:
                summary_sheet.append(
                    [
                        piece_index,
                        cliente if first_row else "",
                        item["codigo"],
                        item["tempo"],
                        float(item["valor"]),
                        float(subtotal) if first_row else "",
                    ]
                )
                piece_index += 1
                first_row = False
        summary_sheet.append([])
        summary_sheet.append(["", "TOTAL GERAL", "", "", "", float(grand_total)])

        alternates_sheet = wb.create_sheet("Suplentes")
        alternates_sheet.append(["Peça", "Suplente", "Código", "Tempo", "Valor", "Cliente titular"])
        for index, row in enumerate((row for row in rows if row["suplente"].strip()), start=1):
            alternates_sheet.append(
                [
                    index,
                    row["suplente"],
                    row["codigo"],
                    row["tempo"],
                    float(self._parse_money(row["valor"])),
                    row["cliente"],
                ]
            )

        metadata_sheet = wb.create_sheet("Dados da live")
        stats = self._current_live_stats()
        metadata_sheet.append(["Campo", "Valor"])
        metadata_sheet.append(["ID da live", self.live_id or ""])
        metadata_sheet.append(["Status", self._current_live_status()])
        metadata_sheet.append(
            [
                "Início",
                self.live_first_started_at.strftime("%d/%m/%Y %H:%M:%S") if self.live_first_started_at else "",
            ]
        )
        metadata_sheet.append(
            [
                "Finalização",
                self.live_finished_at.strftime("%d/%m/%Y %H:%M:%S") if self.live_finished_at else "",
            ]
        )
        metadata_sheet.append(["Duração registrada", self._format_elapsed(self._current_elapsed_seconds())])
        metadata_sheet.append(["Peças preenchidas", stats["pieces_count"]])
        metadata_sheet.append(["Peças apresentadas", stats["presented_count"]])
        metadata_sheet.append(["Peças vendidas", stats["sold_count"]])
        metadata_sheet.append(["Clientes diferentes", stats["clients_count"]])
        metadata_sheet.append(["Total vendido", self._format_money(stats["total"])])
        metadata_sheet.append(["Valor total das peças apresentadas", self._format_money(stats["presented_total"])])
        metadata_sheet.append(["Valor médio das peças apresentadas", self._format_money(stats["presented_average"])])

        history_sheet = wb.create_sheet("Histórico de lives")
        history_sheet.append(
            ["Finalizada em", "Iniciada em", "Duração", "Peças", "Vendidas", "Clientes", "Total", "10% + R$ 50", "Nomes das clientes"]
        )
        for live in self._read_history():
            history_sheet.append(
                [
                    self._format_history_datetime(live.get("finished_at", "")),
                    self._format_history_datetime(live.get("started_at", "")),
                    live.get("duration", ""),
                    live.get("pieces_count", 0),
                    live.get("sold_count", 0),
                    live.get("clients_count", 0),
                    live.get("total", "R$ 0,00"),
                    self._history_commission_value(live),
                    ", ".join(live.get("clients", [])),
                ]
            )

        self._format_workbook(sales, summary_sheet, alternates_sheet, metadata_sheet, history_sheet)
        wb.save(path)
        self._save_rows()
        messagebox.showinfo("Exportação concluída", f"Planilha salva em:\n{path}")

    def _format_workbook(self, *sheets):
        header_fill = PatternFill("solid", fgColor=COLORS["table_header"].replace("#", ""))
        sold_fill = PatternFill("solid", fgColor=COLORS["sold_row"].replace("#", ""))
        header_font = Font(color=COLORS["table_header_text"].replace("#", ""), bold=True)
        text_font = Font(color=COLORS["text"].replace("#", ""))

        for sheet in sheets:
            for cell in sheet[1]:
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(horizontal="center")

            for row in sheet.iter_rows(min_row=2):
                for cell in row:
                    cell.font = text_font

            for column_cells in sheet.columns:
                length = max(len(str(cell.value or "")) for cell in column_cells)
                sheet.column_dimensions[get_column_letter(column_cells[0].column)].width = min(max(length + 3, 14), 34)

            sheet.freeze_panes = "A2"
            if sheet.max_row > 1 and sheet.max_column > 1:
                sheet.auto_filter.ref = sheet.dimensions

        for row in range(2, sheets[0].max_row + 1):
            cliente = sheets[0].cell(row=row, column=CLIENT_COL + 2).value
            if cliente:
                for col in range(1, len(COLUMNS) + 2):
                    sheets[0].cell(row=row, column=col).fill = sold_fill

        for row in range(2, sheets[1].max_row + 1):
            sheets[1].cell(row=row, column=5).number_format = '"R$" #,##0.00'
            sheets[1].cell(row=row, column=6).number_format = '"R$" #,##0.00'

        for row in range(2, sheets[2].max_row + 1):
            sheets[2].cell(row=row, column=5).number_format = '"R$" #,##0.00'

    def print_sheet(self, show_success=True, show_empty=True):
        self._finish_active_cell()
        rows = self._rows()
        if not rows:
            if show_empty:
                messagebox.showinfo("Nada para imprimir", "Preencha pelo menos uma linha antes de imprimir.")
            return False
        return self._print_rows_as_workbook(rows, "Planilha da live", "iomarques_planilha", show_success=show_success)

    def print_unsold_pieces(self, show_success=True, show_empty=True):
        self._finish_active_cell()
        rows = [
            {**row, "peca": index}
            for index, row in enumerate(self._rows(include_empty=True), start=1)
            if not row["cliente"].strip() and (row["valor"].strip() or row["codigo"].strip())
        ]
        if not rows:
            if show_empty:
                messagebox.showinfo("Sem peças não vendidas", "Não encontrei peças preenchidas sem cliente.")
            return False
        return self._print_rows_as_workbook(rows, "Peças não vendidas", "iomarques_nao_vendidas", show_success=show_success)

    def _print_rows_as_workbook(self, rows, title, filename_prefix, show_success=True):
        printable_text = self._printable_rows_text(rows, title)
        try:
            self._send_text_to_printer(printable_text, title)
        except Exception as exc:
            path = Path(tempfile.gettempdir()) / f"{filename_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            try:
                path.write_text(printable_text, encoding="utf-8")
                saved_message = f"Salvei uma cópia para impressão em:\n{path}\n\n"
            except OSError:
                saved_message = ""
            messagebox.showerror(
                "Não consegui imprimir",
                f"{saved_message}Erro ao enviar para a impressora:\n{exc}",
            )
            return False

        if show_success:
            messagebox.showinfo("Impressão enviada", "Enviei a planilha para a impressora padrão.")
        return True

    def _printable_rows_text(self, rows, title):
        now = datetime.now().strftime("%d/%m/%Y %H:%M")
        widths = {
            "check": 3,
            "peca": 4,
            "valor": 11,
            "codigo": 8,
            "cliente": 20,
            "suplente": 16,
            "tempo": 8,
        }

        def cell(value, width, align="left"):
            text = self._clip_text(value, width)
            return text.rjust(width) if align == "right" else text.ljust(width)

        header = (
            f"{cell('', widths['check'])} "
            f"{cell('Peça', widths['peca'])} "
            f"{cell('Valor', widths['valor'])} "
            f"{cell('Código', widths['codigo'])} "
            f"{cell('Cliente', widths['cliente'])} "
            f"{cell('Suplente', widths['suplente'])} "
            f"{cell('Tempo', widths['tempo'])}"
        )
        separator = "-" * len(header)
        lines = [
            f"IoMarques Brechó - {title}",
            f"Gerado em: {now} | Duração: {self._format_elapsed(self._current_elapsed_seconds())} | Linhas: {len(rows)}",
            "",
            header,
            separator,
        ]
        for index, row in enumerate(rows, start=1):
            lines.append(
                f"{cell(CHECKBOX_TEXT, widths['check'])} "
                f"{cell(row.get('peca', index), widths['peca'], 'right')} "
                f"{cell(row['valor'], widths['valor'])} "
                f"{cell(row['codigo'], widths['codigo'])} "
                f"{cell(row['cliente'], widths['cliente'])} "
                f"{cell(row['suplente'], widths['suplente'])} "
                f"{cell(row['tempo'], widths['tempo'])}"
            )
        return "\n".join(lines) + "\n"

    def _send_text_to_printer(self, text, title):
        if sys.platform.startswith("win"):
            self._send_text_to_windows_printer(text, title)
            return

        path = Path(tempfile.gettempdir()) / f"iomarques_print_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        path.write_text(text, encoding="utf-8")
        subprocess.run(["lp", str(path)], check=True)

    def _send_text_to_windows_printer(self, text, title):
        print_lines = [(line, False) for line in (text.splitlines() or [""])]
        self._send_print_lines_to_windows_printer(print_lines, title)

    def _send_print_lines_to_windows_printer(self, print_lines, title, columns=1, paginate=False):
        printer_name = self._default_windows_printer()
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

        class DOCINFOW(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_int),
                ("lpszDocName", wintypes.LPCWSTR),
                ("lpszOutput", wintypes.LPCWSTR),
                ("lpszDatatype", wintypes.LPCWSTR),
                ("fwType", wintypes.DWORD),
            ]

        gdi32.CreateDCW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p]
        gdi32.CreateDCW.restype = wintypes.HDC
        gdi32.DeleteDC.argtypes = [wintypes.HDC]
        gdi32.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
        gdi32.GetDeviceCaps.restype = ctypes.c_int
        gdi32.CreateFontW.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            wintypes.LPCWSTR,
        ]
        gdi32.CreateFontW.restype = wintypes.HFONT
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
        gdi32.StartDocW.argtypes = [wintypes.HDC, ctypes.POINTER(DOCINFOW)]
        gdi32.StartDocW.restype = ctypes.c_int
        gdi32.EndDoc.argtypes = [wintypes.HDC]
        gdi32.AbortDoc.argtypes = [wintypes.HDC]
        gdi32.StartPage.argtypes = [wintypes.HDC]
        gdi32.EndPage.argtypes = [wintypes.HDC]
        gdi32.TextOutW.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.LPCWSTR, ctypes.c_int]
        gdi32.GetTextExtentPoint32W.argtypes = [
            wintypes.HDC,
            wintypes.LPCWSTR,
            ctypes.c_int,
            ctypes.POINTER(wintypes.SIZE),
        ]
        gdi32.GetTextExtentPoint32W.restype = wintypes.BOOL

        hdc = gdi32.CreateDCW("WINSPOOL", printer_name, None, None)
        if not hdc:
            raise ctypes.WinError(ctypes.get_last_error())

        print_lines = [(str(line), bool(bold)) for line, bold in print_lines] or [("", False)]
        columns = max(1, min(2, int(columns or 1)))
        print_columns = self._split_print_columns(print_lines, columns)
        fit_lines = [line for column in print_columns for line in column]
        max_column_lines = max((len(column) for column in print_columns), default=1)
        regular_font = None
        bold_font = None
        old_font = None
        doc_started = False
        page_started = False
        try:
            dpi_y = max(1, gdi32.GetDeviceCaps(hdc, 90))
            page_width = max(1, gdi32.GetDeviceCaps(hdc, 8))
            page_height = max(1, gdi32.GetDeviceCaps(hdc, 10))
            dpi_x = max(1, gdi32.GetDeviceCaps(hdc, 88))
            margin_x = max(30, int(dpi_x * 0.18))
            margin_y = max(30, int(dpi_y * 0.18))
            printable_width = max(1, page_width - margin_x * 2)
            printable_height = max(1, page_height - margin_y * 2)
            column_gap = max(20, int(dpi_x * 0.16)) if columns > 1 else 0
            column_width = max(1, int((printable_width - column_gap * (columns - 1)) / columns))

            regular_font, bold_font, line_height = self._fit_print_font(
                gdi32,
                hdc,
                fit_lines,
                dpi_y,
                column_width,
                printable_height,
                max_lines_per_column=max_column_lines,
                fit_width=(columns == 1),
                fit_height=not paginate,
            )
            old_font = gdi32.SelectObject(hdc, regular_font)
            gdi32.SetBkMode(hdc, 1)

            docinfo = DOCINFOW(ctypes.sizeof(DOCINFOW), f"IoMarques Brechó - {title}", None, None, 0)
            if gdi32.StartDocW(hdc, ctypes.byref(docinfo)) <= 0:
                raise ctypes.WinError(ctypes.get_last_error())
            doc_started = True
            if gdi32.StartPage(hdc) <= 0:
                raise ctypes.WinError(ctypes.get_last_error())
            page_started = True

            for column_index, column in enumerate(print_columns):
                x = margin_x + column_index * (column_width + column_gap)
                y = margin_y
                printed_on_page = False
                for line, bold in column:
                    if paginate and printed_on_page and y + line_height > margin_y + printable_height:
                        if gdi32.EndPage(hdc) <= 0:
                            raise ctypes.WinError(ctypes.get_last_error())
                        if gdi32.StartPage(hdc) <= 0:
                            raise ctypes.WinError(ctypes.get_last_error())
                        y = margin_y
                        printed_on_page = False
                    selected_font = bold_font if bold else regular_font
                    gdi32.SelectObject(hdc, selected_font)
                    printable_line = self._clip_line_for_print_width(gdi32, hdc, line, column_width)
                    gdi32.TextOutW(hdc, x, y, printable_line, len(printable_line))
                    y += line_height
                    printed_on_page = True

            if gdi32.EndPage(hdc) <= 0:
                raise ctypes.WinError(ctypes.get_last_error())
            page_started = False
            if gdi32.EndDoc(hdc) <= 0:
                raise ctypes.WinError(ctypes.get_last_error())
            doc_started = False
        except Exception:
            if page_started or doc_started:
                gdi32.AbortDoc(hdc)
            raise
        finally:
            if old_font:
                gdi32.SelectObject(hdc, old_font)
            if regular_font:
                gdi32.DeleteObject(regular_font)
            if bold_font:
                gdi32.DeleteObject(bold_font)
            gdi32.DeleteDC(hdc)

    def _split_print_columns(self, print_lines, columns):
        if columns <= 1 or len(print_lines) <= REPORT_TWO_COLUMN_MIN_LINES:
            return [print_lines]
        midpoint = (len(print_lines) + 1) // 2
        separator_indexes = [
            index
            for index, (line, _bold) in enumerate(print_lines)
            if line and set(line) == {"-"} and 4 < index < len(print_lines) - 4
        ]
        if separator_indexes:
            midpoint = min(separator_indexes, key=lambda index: abs(index - midpoint)) + 1
        return [print_lines[:midpoint], print_lines[midpoint:]]

    def _fit_print_font(
        self,
        gdi32,
        hdc,
        print_lines,
        dpi_y,
        printable_width,
        printable_height,
        max_lines_per_column=None,
        fit_width=True,
        fit_height=True,
    ):
        max_lines = max(1, max_lines_per_column or len(print_lines))
        height_points = int(printable_height * 72 / (dpi_y * max_lines * 1.18))
        starting_points = 10 if not fit_height else min(10, max(4, height_points))
        best_regular_font = None
        best_bold_font = None
        best_line_height = 1
        for points in range(starting_points, 3, -1):
            regular_font = self._create_print_font(gdi32, dpi_y, points)
            bold_font = self._create_print_font(gdi32, dpi_y, points, weight=700)
            size = wintypes.SIZE()
            widest = 0
            for line, bold in print_lines:
                old_font = gdi32.SelectObject(hdc, bold_font if bold else regular_font)
                gdi32.GetTextExtentPoint32W(hdc, line, len(line), ctypes.byref(size))
                gdi32.SelectObject(hdc, old_font)
                widest = max(widest, size.cx)

            line_height = max(1, int(points * dpi_y / 72 * 1.22))
            height_fits = (not fit_height) or line_height * max_lines <= printable_height
            if (not fit_width or widest <= printable_width) and height_fits:
                return regular_font, bold_font, line_height
            if best_regular_font:
                gdi32.DeleteObject(best_regular_font)
            if best_bold_font:
                gdi32.DeleteObject(best_bold_font)
            best_regular_font = regular_font
            best_bold_font = bold_font
            best_line_height = line_height
        return best_regular_font, best_bold_font, best_line_height

    def _create_print_font(self, gdi32, dpi_y, points, weight=400):
        height = -max(1, int(points * dpi_y / 72))
        return gdi32.CreateFontW(height, 0, 0, 0, weight, 0, 0, 0, 1, 0, 0, 5, 49, "Consolas")

    def _clip_line_for_print_width(self, gdi32, hdc, line, printable_width):
        size = wintypes.SIZE()
        gdi32.GetTextExtentPoint32W(hdc, line, len(line), ctypes.byref(size))
        if size.cx <= printable_width:
            return line
        if printable_width <= 0:
            return ""

        ellipsis = "..."
        low = 0
        high = max(0, len(line) - len(ellipsis))
        best = ellipsis
        while low <= high:
            mid = (low + high) // 2
            candidate = line[:mid].rstrip() + ellipsis
            gdi32.GetTextExtentPoint32W(hdc, candidate, len(candidate), ctypes.byref(size))
            if size.cx <= printable_width:
                best = candidate
                low = mid + 1
            else:
                high = mid - 1
        return best

    def _default_windows_printer(self):
        winspool = ctypes.WinDLL("winspool.drv", use_last_error=True)
        winspool.GetDefaultPrinterW.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        winspool.GetDefaultPrinterW.restype = wintypes.BOOL
        needed = wintypes.DWORD(0)
        winspool.GetDefaultPrinterW(None, ctypes.byref(needed))
        if needed.value == 0:
            raise OSError("Nenhuma impressora padrão configurada no Windows.")

        buffer = ctypes.create_unicode_buffer(needed.value)
        if not winspool.GetDefaultPrinterW(buffer, ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        return buffer.value

    def _format_print_workbook(self, sheet):
        header_fill = PatternFill("solid", fgColor=COLORS["table_header"].replace("#", ""))
        header_font = Font(color=COLORS["table_header_text"].replace("#", ""), bold=True)
        text_font = Font(color=COLORS["text"].replace("#", ""))

        sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(COLUMNS) + 1)
        sheet.cell(row=1, column=1).font = Font(color=COLORS["text"].replace("#", ""), bold=True, size=13)
        sheet.cell(row=2, column=1).font = text_font
        sheet.cell(row=3, column=1).font = text_font

        header_row = 5
        for cell in sheet[header_row]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")

        for row in sheet.iter_rows(min_row=header_row + 1):
            for cell in row:
                cell.font = text_font
                cell.alignment = Alignment(vertical="top", wrap_text=True)

        widths = [7, 12, 11, 22, 18, 12]
        for column_index, width in enumerate(widths, start=1):
            sheet.column_dimensions[get_column_letter(column_index)].width = width

        sheet.freeze_panes = "A6"
        sheet.auto_filter.ref = f"A5:{get_column_letter(len(COLUMNS) + 1)}{sheet.max_row}"
        sheet.print_area = f"A1:{get_column_letter(len(COLUMNS) + 1)}{sheet.max_row}"
        sheet.page_setup.orientation = "portrait"
        sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 1
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_margins.left = 0.25
        sheet.page_margins.right = 0.25
        sheet.page_margins.top = 0.35
        sheet.page_margins.bottom = 0.35

    def _send_file_to_printer(self, path):
        if sys.platform.startswith("win"):
            os.startfile(str(path), "print")
        elif sys.platform == "darwin":
            subprocess.run(["lp", str(path)], check=True)
        else:
            subprocess.run(["lp", str(path)], check=True)

    def print_delivery_pages(self):
        return self._print_static_asset_pages(DELIVERY_PRINT_ASSET, "Entregas", print_as_image=True)

    def print_evaluation_pages(self):
        return self._print_static_asset_pages(EVALUATION_PRINT_ASSET, "Avaliações", print_as_pdf=True)

    def _print_static_asset_pages(self, asset_filename, title, print_as_image=False, print_as_pdf=False):
        self._finish_active_cell()
        asset_path = self._asset_path(asset_filename)
        if asset_path is None:
            messagebox.showerror(
                "Arquivo não encontrado",
                f"Não encontrei o arquivo de {title.lower()} dentro da pasta assets.",
            )
            return False

        page_count = simpledialog.askinteger(
            f"Imprimir {title}",
            f"Quantas páginas de {title.lower()} deseja imprimir?",
            parent=self,
            initialvalue=1,
            minvalue=1,
            maxvalue=200,
        )
        if page_count is None:
            return False

        try:
            for page_index in range(page_count):
                if print_as_pdf and sys.platform.startswith("win"):
                    self._send_pdf_to_windows_printer(asset_path, title)
                elif print_as_image and sys.platform.startswith("win"):
                    self._send_image_to_windows_printer(asset_path, title)
                else:
                    self._send_file_to_printer(asset_path)
                    if page_index < page_count - 1:
                        time.sleep(0.8)
        except Exception as exc:
            messagebox.showerror(
                "Não consegui imprimir",
                f"Erro ao enviar {title.lower()} para a impressora:\n{exc}",
            )
            return False

        plural = "página" if page_count == 1 else "páginas"
        messagebox.showinfo(
            "Impressão enviada",
            f"Enviei {page_count} {plural} de {title.lower()} para a impressora padrão.",
        )
        return True

    def _send_image_to_windows_printer(self, path, title):
        from PIL import Image

        with Image.open(path) as source:
            image = self._prepare_print_image(source)

        self._send_pil_image_to_windows_printer(image, title)

    def _send_pdf_to_windows_printer(self, path, title):
        import fitz
        from PIL import Image

        document = fitz.open(str(path))
        try:
            if document.page_count <= 0:
                raise ValueError("O PDF não tem páginas para imprimir.")

            zoom = 200 / 72
            matrix = fitz.Matrix(zoom, zoom)
            for page_number in range(document.page_count):
                page = document.load_page(page_number)
                pixmap = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=False)
                image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                page_title = title if document.page_count == 1 else f"{title} - página {page_number + 1}"
                self._send_pil_image_to_windows_printer(image, page_title)
        finally:
            document.close()

    def _prepare_print_image(self, source):
        from PIL import Image

        if source.mode in ("RGBA", "LA") or "transparency" in source.info:
            transparent = source.convert("RGBA")
            image = Image.new("RGB", transparent.size, "#FFFFFF")
            image.paste(transparent, mask=transparent.getchannel("A"))
            return image
        return source.convert("RGB")

    def _send_pil_image_to_windows_printer(self, image, title):
        from PIL import ImageWin

        printer_name = self._default_windows_printer()
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

        class DOCINFOW(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_int),
                ("lpszDocName", wintypes.LPCWSTR),
                ("lpszOutput", wintypes.LPCWSTR),
                ("lpszDatatype", wintypes.LPCWSTR),
                ("fwType", wintypes.DWORD),
            ]

        gdi32.CreateDCW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p]
        gdi32.CreateDCW.restype = wintypes.HDC
        gdi32.DeleteDC.argtypes = [wintypes.HDC]
        gdi32.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
        gdi32.GetDeviceCaps.restype = ctypes.c_int
        gdi32.StartDocW.argtypes = [wintypes.HDC, ctypes.POINTER(DOCINFOW)]
        gdi32.StartDocW.restype = ctypes.c_int
        gdi32.EndDoc.argtypes = [wintypes.HDC]
        gdi32.AbortDoc.argtypes = [wintypes.HDC]
        gdi32.StartPage.argtypes = [wintypes.HDC]
        gdi32.EndPage.argtypes = [wintypes.HDC]

        hdc = gdi32.CreateDCW("WINSPOOL", printer_name, None, None)
        if not hdc:
            raise ctypes.WinError(ctypes.get_last_error())

        doc_started = False
        page_started = False
        try:
            dpi_x = max(1, gdi32.GetDeviceCaps(hdc, 88))
            dpi_y = max(1, gdi32.GetDeviceCaps(hdc, 90))
            page_width = max(1, gdi32.GetDeviceCaps(hdc, 8))
            page_height = max(1, gdi32.GetDeviceCaps(hdc, 10))
            margin_x = max(20, int(dpi_x * 0.12))
            margin_y = max(20, int(dpi_y * 0.12))
            printable_width = max(1, page_width - margin_x * 2)
            printable_height = max(1, page_height - margin_y * 2)

            scale = min(printable_width / image.width, printable_height / image.height)
            draw_width = max(1, int(image.width * scale))
            draw_height = max(1, int(image.height * scale))
            left = margin_x + (printable_width - draw_width) // 2
            top = margin_y + (printable_height - draw_height) // 2

            docinfo = DOCINFOW(ctypes.sizeof(DOCINFOW), f"IoMarques Brechó - {title}", None, None, 0)
            if gdi32.StartDocW(hdc, ctypes.byref(docinfo)) <= 0:
                raise ctypes.WinError(ctypes.get_last_error())
            doc_started = True
            if gdi32.StartPage(hdc) <= 0:
                raise ctypes.WinError(ctypes.get_last_error())
            page_started = True

            ImageWin.Dib(image).draw(hdc, (left, top, left + draw_width, top + draw_height))

            if gdi32.EndPage(hdc) <= 0:
                raise ctypes.WinError(ctypes.get_last_error())
            page_started = False
            if gdi32.EndDoc(hdc) <= 0:
                raise ctypes.WinError(ctypes.get_last_error())
            doc_started = False
        except Exception:
            if page_started or doc_started:
                gdi32.AbortDoc(hdc)
            raise
        finally:
            gdi32.DeleteDC(hdc)

    def print_report(self, show_success=True):
        self._finish_active_cell()

        report_lines = self._printable_report_lines()
        report = self._printable_report()
        try:
            if sys.platform.startswith("win"):
                report_lines = self._wrap_report_lines_for_print(report_lines, columns=1)
                self._send_print_lines_to_windows_printer(report_lines, "Resumo", columns=1, paginate=True)
            else:
                self._send_text_to_printer(report, "Resumo")
        except Exception as exc:
            path = Path(tempfile.gettempdir()) / f"iomarques_live_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            try:
                path.write_text(report, encoding="utf-8")
                saved_message = f"Salvei o relatório em:\n{path}\n\n"
            except OSError:
                saved_message = ""
            messagebox.showerror(
                "Não consegui imprimir",
                f"{saved_message}Erro ao enviar para a impressora:\n{exc}",
            )
            return False

        if show_success:
            messagebox.showinfo("Impressão enviada", "Enviei o resumo para a impressora padrão.")
        return True

    def print_all_reports(self):
        self._finish_active_cell()

        sent = []
        skipped = []
        if self.print_report(show_success=False):
            sent.append("resumo")
        if self.print_sheet(show_success=False, show_empty=False):
            sent.append("planilha")
        else:
            skipped.append("planilha sem dados")
        if self.print_unsold_pieces(show_success=False, show_empty=False):
            sent.append("peças não vendidas")
        else:
            skipped.append("sem peças não vendidas")

        if sent:
            detail = f"Enviei para a impressora: {', '.join(sent)}."
            if skipped:
                detail += f"\n\nItens ignorados: {', '.join(skipped)}."
            messagebox.showinfo("Impressão enviada", detail)
        else:
            messagebox.showinfo("Nada para imprimir", "Não encontrei dados para enviar para a impressora.")

    def clear_all(self):
        self._finish_active_cell()
        if self.live_running and not (self.live_id and self.sync_manager.is_deleted(self.live_id)):
            messagebox.showwarning("Live em andamento", "Finalize a live antes de abrir uma nova planilha.")
            return
        answer = messagebox.askyesno(
            "Começar nova live?",
            "A planilha atual será guardada e uma nova será aberta. "
            "O histórico e os rascunhos não serão excluídos do Supabase. Continuar?",
        )
        if not answer:
            return

        if not self._save_rows():
            return
        self._apply_saved_state({})

    def _finish_active_cell(self):
        if self.active_cell and self._cell_exists(*self.active_cell):
            self._finish_cell_edit(*self.active_cell)

    def _on_close(self):
        self._finish_active_cell()
        saved = self._save_rows(show_error=False)
        if not saved:
            if not messagebox.askyesno(
                "Alterações sem proteção",
                "Não foi possível guardar as últimas alterações. Fechar agora pode perdê-las. "
                "Deseja descartá-las e fechar mesmo assim?", default="no",
            ):
                return
        elif self.sync_manager and self.sync_manager.has_pending:
            if not messagebox.askyesno(
                "Ainda há envios pendentes",
                "As alterações ainda não foram confirmadas pelo Supabase. "
                "Elas estão na fila protegida deste computador e serão recuperadas ao reabrir. "
                "Deseja fechar agora?", default="no",
            ):
                return
        self.closing = True
        if self.timer_job is not None:
            self.after_cancel(self.timer_job)
        if self.autosave_job is not None:
            self.after_cancel(self.autosave_job)
        if self.cloud_poll_job is not None:
            self.after_cancel(self.cloud_poll_job)
        if self.sync_manager is not None:
            self.sync_manager.shutdown()
        self.destroy()


def verify_installation():
    """Offline packaging check: never instantiate CloudLiveStore or migrate data."""
    import fitz
    from PIL import Image
    from supabase_sync import load_sync_config

    if Workbook is None or load_sync_config(APP_DIR, RESOURCE_DIR) is None:
        return 1
    for filename in ("logo_round.png", "app_icon.ico", DELIVERY_PRINT_ASSET, EVALUATION_PRINT_ASSET):
        if not (ASSETS_DIR / filename).is_file():
            return 1
    with Image.open(ASSETS_DIR / DELIVERY_PRINT_ASSET) as picture:
        picture.verify()
    with fitz.open(ASSETS_DIR / EVALUATION_PRINT_ASSET) as document:
        if document.page_count < 1:
            return 1
    window = tk.Tk()
    window.withdraw()
    try:
        tk.PhotoImage(master=window, file=str(ASSETS_DIR / "logo_round.png"))
    finally:
        window.destroy()
    return 0


if __name__ == "__main__":
    if "--verificar-instalacao" in sys.argv:
        try:
            verification_result = verify_installation()
        except Exception:
            # A failed frozen check must return to the installer, not leave a
            # PyInstaller error dialog waiting for input in a hidden process.
            verification_result = 1
        raise SystemExit(verification_result)
    app = LiveSalesApp()
    app.mainloop()
