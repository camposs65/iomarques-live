import json
import os
import subprocess
import sys
import tempfile
import tkinter as tk
import uuid
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
except ImportError:
    Workbook = None


COLUMNS = ("valor", "codigo", "tempo", "cliente", "suplente1", "suplente2")
HEADERS = {
    "valor": "Valor",
    "codigo": "Código",
    "tempo": "Tempo",
    "cliente": "Cliente",
    "suplente1": "Suplente 1",
    "suplente2": "Suplente 2",
}
COLUMN_WIDTHS = (130, 110, 105, 215, 215, 215)

VALUE_COL = COLUMNS.index("valor")
CODE_COL = COLUMNS.index("codigo")
TIME_COL = COLUMNS.index("tempo")
CLIENT_COL = COLUMNS.index("cliente")
SUPLENTE1_COL = COLUMNS.index("suplente1")
SUPLENTE2_COL = COLUMNS.index("suplente2")

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
else:
    APP_DIR = Path(__file__).resolve().parent
    RESOURCE_DIR = APP_DIR
AUTOSAVE_PATH = APP_DIR / "live_atual.json"
HISTORY_PATH = APP_DIR / "historico_lives.json"
ASSETS_DIR = RESOURCE_DIR / "assets"

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
}


class LiveSalesApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("IoMarques Brechó - Controle de Vendas da Live")
        self.geometry("1160x720")
        self.minsize(960, 580)

        self.row_vars = []
        self.cell_frames = []
        self.cell_entries = []
        self.clear_buttons = []
        self.active_cell = None
        self.hovered_cell = None

        self.live_running = False
        self.live_id = None
        self.live_first_started_at = None
        self.live_started_at = None
        self.live_finished_at = None
        self.live_elapsed_seconds = 0
        self.timer_job = None
        self.logo_header_image = None
        self.logo_icon_image = None

        self._setup_style()
        self._build_ui()

        saved_data = self._read_saved_data()
        self._restore_live_state(saved_data)
        loaded = self._load_saved_rows(saved_data)
        if not loaded:
            self._add_row()

        self._refresh_live_controls()
        self._schedule_timer_tick()
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

    def _build_ui(self):
        header = tk.Frame(self, bg=COLORS["primary"])
        header.pack(fill="x")

        header_inner = tk.Frame(header, bg=COLORS["primary"])
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

        toolbar = tk.Frame(self, bg=COLORS["app_bg"])
        toolbar.pack(fill="x", padx=24, pady=(18, 14))

        self.toolbar_actions = tk.Frame(toolbar, bg=COLORS["app_bg"])
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

        timer_panel = tk.Frame(toolbar, bg=COLORS["app_bg"])
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

        table_shell = tk.Frame(self, bg=COLORS["grid"], bd=1)
        table_shell.pack(fill="both", expand=True, padx=24, pady=(0, 14))

        header_shell = tk.Frame(table_shell, bg=COLORS["grid"])
        header_shell.pack(fill="x")

        self.header_frame = tk.Frame(header_shell, bg=COLORS["grid"])
        self.header_frame.pack(side="left", fill="x", expand=True)
        self.header_spacer = tk.Frame(header_shell, bg=COLORS["grid"], width=17)
        self.header_spacer.pack(side="right", fill="y")
        self.header_spacer.pack_propagate(False)
        self._build_table_header()

        body_shell = tk.Frame(table_shell, bg=COLORS["grid"])
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

        footer = tk.Label(
            self,
            text=(
                "Clique em Iniciar live para gravar os tempos. "
                "Ao preencher Valor ou Código, o Tempo da peça é salvo automaticamente."
            ),
            bg=COLORS["app_bg"],
            fg=COLORS["button_text"],
            font=("Segoe UI", 10),
        )
        footer.pack(fill="x", padx=24, pady=(0, 14))

    def _build_final_action_buttons(self, parent):
        ttk.Button(parent, text="Resumo final", command=self.show_summary, style="Primary.TButton").pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(parent, text="Exportar Excel", command=self.export_excel, style="Primary.TButton").pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(parent, text="Imprimir resumo", command=self.print_report, style="Secondary.TButton").pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(parent, text="Histórico de lives", command=self.show_history, style="Secondary.TButton").pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(parent, text="Nova live / Limpar tudo", command=self.clear_all, style="Secondary.TButton").pack(
            side="left"
        )

    def _build_table_header(self):
        for col, header in enumerate(HEADERS[column] for column in COLUMNS):
            cell = tk.Frame(
                self.header_frame,
                bg=COLORS["table_header"],
                highlightbackground=COLORS["grid"],
                highlightthickness=1,
            )
            cell.grid(row=0, column=col, sticky="nsew")
            self.header_frame.grid_columnconfigure(col, weight=1, minsize=COLUMN_WIDTHS[col])

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
            label.pack(fill="both", expand=True)

    def _on_body_configure(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self.body_window, width=event.width)

    def _on_mousewheel(self, event):
        if not self.canvas.winfo_ismapped() or not self._pointer_inside_widget(self.canvas):
            return
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _add_row(self, values=None):
        if values is None:
            values = [""] * len(COLUMNS)

        row_index = len(self.row_vars)
        row_vars = []
        row_frames = []
        row_entries = []
        row_buttons = []

        for col in range(len(COLUMNS)):
            self.body_frame.grid_columnconfigure(col, weight=1, minsize=COLUMN_WIDTHS[col])

            cell = tk.Frame(
                self.body_frame,
                bg=COLORS["table_bg"],
                highlightbackground=COLORS["grid"],
                highlightthickness=1,
            )
            cell.grid(row=row_index, column=col, sticky="nsew")

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
            if col >= CLIENT_COL:
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

        self.row_vars.append(row_vars)
        self.cell_frames.append(row_frames)
        self.cell_entries.append(row_entries)
        self.clear_buttons.append(row_buttons)
        self._refresh_row_style(row_index)
        self._on_body_configure()

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
        self._refresh_row_style(row)
        self._ensure_blank_row()
        self._save_rows()

    def _on_key_release(self, event, row, col):
        if event.keysym in {"Up", "Down", "Left", "Right", "Return", "Tab", "ISO_Left_Tab"}:
            return
        if col in (VALUE_COL, CODE_COL):
            self._maybe_stamp_piece_time(row)
        self._refresh_row_style(row)
        self._ensure_blank_row()
        self._save_rows()
        if self.hovered_cell == (row, col):
            self._show_clear_button(row, col)

    def _maybe_stamp_piece_time(self, row):
        if not self.live_running or not self._cell_exists(row, TIME_COL):
            return
        has_piece_reference = (
            self.row_vars[row][VALUE_COL].get().strip() or self.row_vars[row][CODE_COL].get().strip()
        )
        if has_piece_reference and not self.row_vars[row][TIME_COL].get().strip():
            self.row_vars[row][TIME_COL].set(self._format_elapsed(self._current_elapsed_seconds()))

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
        if col < CLIENT_COL or not self._cell_exists(row, col):
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
        if not self._cell_exists(row, col) or col < CLIENT_COL:
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
        if not self._cell_exists(row, col) or col < CLIENT_COL:
            return

        if col == CLIENT_COL:
            self.row_vars[row][CLIENT_COL].set(self.row_vars[row][SUPLENTE1_COL].get().strip())
            self.row_vars[row][SUPLENTE1_COL].set(self.row_vars[row][SUPLENTE2_COL].get().strip())
            self.row_vars[row][SUPLENTE2_COL].set("")
        elif col == SUPLENTE1_COL:
            self.row_vars[row][SUPLENTE1_COL].set(self.row_vars[row][SUPLENTE2_COL].get().strip())
            self.row_vars[row][SUPLENTE2_COL].set("")
        elif col == SUPLENTE2_COL:
            self.row_vars[row][SUPLENTE2_COL].set("")

        for clear_col in (CLIENT_COL, SUPLENTE1_COL, SUPLENTE2_COL):
            button = self.clear_buttons[row][clear_col]
            if button is not None:
                button.pack_forget()

        self._refresh_row_style(row)
        self._ensure_blank_row()
        self._save_rows()

    def _refresh_row_style(self, row):
        if row < 0 or row >= len(self.row_vars):
            return

        bg = self._row_bg(row)
        for col in range(len(COLUMNS)):
            cell = self.cell_frames[row][col]
            entry = self.cell_entries[row][col]
            cell.configure(bg=bg)
            entry.configure(bg=bg, fg=COLORS["text"])
            button = self.clear_buttons[row][col]
            if button is not None:
                button.configure(bg=bg)
        if self.active_cell:
            self._refresh_cell_border(*self.active_cell)

    def _row_bg(self, row):
        if row < 0 or row >= len(self.row_vars):
            return COLORS["table_bg"]
        return COLORS["sold_row"] if self.row_vars[row][CLIENT_COL].get().strip() else COLORS["table_bg"]

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

    def _read_saved_data(self):
        if not AUTOSAVE_PATH.exists():
            return {}
        try:
            return json.loads(AUTOSAVE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_rows(self):
        data = {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
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
        tmp_path = AUTOSAVE_PATH.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_path.replace(AUTOSAVE_PATH)
        except OSError as exc:
            messagebox.showerror("Erro ao salvar", f"Não consegui salvar a live atual:\n{exc}")

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
            values = [str(row.get(col, "")).strip() for col in COLUMNS]
            values[VALUE_COL] = self._format_money_input(values[VALUE_COL])
            self._add_row(values)

        self._ensure_blank_row()
        return True

    def _read_history(self):
        if not HISTORY_PATH.exists():
            return []
        try:
            data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if isinstance(data, list):
            return data
        return data.get("lives", []) if isinstance(data, dict) else []

    def _write_history(self, lives):
        data = {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "lives": lives,
        }
        tmp_path = HISTORY_PATH.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_path.replace(HISTORY_PATH)
        except OSError as exc:
            messagebox.showerror("Erro ao salvar histórico", f"Não consegui salvar o histórico de lives:\n{exc}")

    def _current_live_stats(self):
        rows = self._rows()
        sold_rows = [row for row in rows if row["cliente"].strip()]
        total = sum((self._parse_money(row["valor"]) for row in sold_rows), Decimal("0"))
        clients = sorted({row["cliente"].strip() for row in sold_rows if row["cliente"].strip()}, key=str.lower)
        return {
            "pieces_count": len(rows),
            "sold_count": len(sold_rows),
            "clients_count": len(clients),
            "clients": clients,
            "total": total,
        }

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
        }

    def _upsert_current_live_history(self, finished_at=None):
        if not self._rows():
            return
        record = self._current_live_history_record(finished_at)
        lives = self._read_history()
        replaced = False
        for index, live in enumerate(lives):
            if live.get("id") == record["id"]:
                lives[index] = record
                replaced = True
                break
        if not replaced:
            lives.append(record)
        lives.sort(key=lambda live: live.get("finished_at", ""), reverse=True)
        self._write_history(lives)

    def show_history(self):
        lives = self._read_history()
        window = tk.Toplevel(self)
        window.title("Histórico de lives - IoMarques Brechó")
        window.geometry("900x520")
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

        columns = ("finished_at", "duration", "pieces_count", "sold_count", "clients_count", "total")
        headings = {
            "finished_at": "Finalizada em",
            "duration": "Duração",
            "pieces_count": "Peças",
            "sold_count": "Vendidas",
            "clients_count": "Clientes",
            "total": "Total",
        }
        tree = ttk.Treeview(window, columns=columns, show="headings", selectmode="browse")
        tree.pack(fill="both", expand=True, padx=18, pady=18)

        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=130, anchor="w")

        for live in lives:
            tree.insert(
                "",
                "end",
                values=(
                    self._format_history_datetime(live.get("finished_at", "")),
                    live.get("duration", ""),
                    live.get("pieces_count", 0),
                    live.get("sold_count", 0),
                    live.get("clients_count", 0),
                    live.get("total", "R$ 0,00"),
                ),
            )

        if not lives:
            empty = tk.Label(
                window,
                text="Nenhuma live finalizada ainda.",
                bg=COLORS["app_bg"],
                fg=COLORS["text"],
                font=("Segoe UI", 11),
            )
            empty.place(relx=0.5, rely=0.55, anchor="center")

    def _format_history_datetime(self, value):
        if not value:
            return ""
        try:
            return datetime.fromisoformat(value).strftime("%d/%m/%Y %H:%M")
        except ValueError:
            return value

    def start_live(self):
        if self.live_running:
            return
        now = datetime.now()
        if not self.live_id:
            self.live_id = f"live_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        if self.live_first_started_at is None:
            self.live_first_started_at = now
        self.live_started_at = now
        self.live_finished_at = None
        self.live_running = True
        self._refresh_live_controls()
        self._save_rows()
        self._schedule_timer_tick()

    def finish_live(self):
        if not self.live_running:
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
        self._upsert_current_live_history(finished_at)

    def _refresh_live_controls(self):
        elapsed = self._current_elapsed_seconds()
        self.timer_label.configure(text=self._format_elapsed(elapsed))
        self.pre_live_frame.pack_forget()
        self.live_frame.pack_forget()
        self.post_live_frame.pack_forget()
        if self.live_running:
            self.live_status_label.configure(text="Live em andamento")
            self.live_frame.pack(side="left", fill="x")
            self.start_button.state(["disabled"])
            self.finish_button.state(["!disabled"])
        elif elapsed > 0:
            self.live_status_label.configure(text="Live finalizada")
            self.post_live_frame.pack(side="left", fill="x")
            self.start_button.state(["!disabled"])
            self.finish_button.state(["disabled"])
        else:
            self.live_status_label.configure(text="Aguardando início")
            self.pre_live_frame.pack(side="left", fill="x")
            self.start_button.state(["!disabled"])
            self.finish_button.state(["disabled"])

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

    def _current_elapsed_seconds(self):
        elapsed = self.live_elapsed_seconds
        if self.live_running and self.live_started_at is not None:
            elapsed += max(0, int((datetime.now() - self.live_started_at).total_seconds()))
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
                    "suplente1": row["suplente1"],
                    "suplente2": row["suplente2"],
                }
            )
        return dict(sorted(grouped.items(), key=lambda item: item[0].lower()))

    def _summary_text(self):
        summary = self._summary()
        if not summary:
            return "Nenhuma peça com cliente titular ainda.\n"

        lines = []
        grand_total = Decimal("0")
        for cliente, items in summary.items():
            subtotal = sum((item["valor"] for item in items), Decimal("0"))
            grand_total += subtotal
            lines.append(cliente)
            for item in items:
                codigo = item["codigo"] or "sem código"
                tempo = f" ({item['tempo']})" if item["tempo"] else ""
                lines.append(f"  - Peça {codigo}{tempo}: {self._format_money(item['valor'])}")
            lines.append(f"  Total: {self._format_money(subtotal)}")
            lines.append("")
        lines.append(f"TOTAL GERAL: {self._format_money(grand_total)}")
        return "\n".join(lines) + "\n"

    def _printable_report(self):
        rows = self._rows()
        now = datetime.now().strftime("%d/%m/%Y %H:%M")
        lines = [
            "IoMarques Brechó - Relatório da live",
            f"Gerado em: {now}",
            f"Duração registrada: {self._format_elapsed(self._current_elapsed_seconds())}",
            "",
            "VENDAS",
            (
                f"{'Valor':<14} {'Código':<10} {'Tempo':<10} {'Cliente':<22} "
                f"{'Suplente 1':<22} {'Suplente 2':<22}"
            ),
            "-" * 104,
        ]

        if rows:
            for row in rows:
                lines.append(
                    f"{row['valor']:<14} {row['codigo']:<10} {row['tempo']:<10} "
                    f"{row['cliente']:<22} {row['suplente1']:<22} {row['suplente2']:<22}"
                )
        else:
            lines.append("Nenhuma peça preenchida ainda.")

        lines.extend(["", "RESUMO POR CLIENTE", self._summary_text()])
        return "\n".join(lines)

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
        text.insert("end", self._summary_text())
        text.configure(state="disabled")

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
        sales.append([HEADERS[col] for col in COLUMNS])
        for row in rows:
            sales.append([row[col] for col in COLUMNS])

        summary_sheet = wb.create_sheet("Resumo por cliente")
        summary_sheet.append(["Cliente", "Código", "Tempo", "Valor", "Total da cliente"])
        summary = self._summary()
        grand_total = Decimal("0")
        for cliente, items in summary.items():
            subtotal = sum((item["valor"] for item in items), Decimal("0"))
            grand_total += subtotal
            first_row = True
            for item in items:
                summary_sheet.append(
                    [
                        cliente if first_row else "",
                        item["codigo"],
                        item["tempo"],
                        float(item["valor"]),
                        float(subtotal) if first_row else "",
                    ]
                )
                first_row = False
        summary_sheet.append([])
        summary_sheet.append(["TOTAL GERAL", "", "", "", float(grand_total)])

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
        metadata_sheet.append(["Peças vendidas", stats["sold_count"]])
        metadata_sheet.append(["Clientes diferentes", stats["clients_count"]])
        metadata_sheet.append(["Total vendido", self._format_money(stats["total"])])

        history_sheet = wb.create_sheet("Histórico de lives")
        history_sheet.append(
            ["Finalizada em", "Iniciada em", "Duração", "Peças", "Vendidas", "Clientes", "Total", "Nomes das clientes"]
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
                    ", ".join(live.get("clients", [])),
                ]
            )

        self._format_workbook(sales, summary_sheet, metadata_sheet, history_sheet)
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

        for row in range(2, sheets[0].max_row + 1):
            cliente = sheets[0].cell(row=row, column=CLIENT_COL + 1).value
            if cliente:
                for col in range(1, len(COLUMNS) + 1):
                    sheets[0].cell(row=row, column=col).fill = sold_fill

        for row in range(2, sheets[1].max_row + 1):
            sheets[1].cell(row=row, column=4).number_format = '"R$" #,##0.00'
            sheets[1].cell(row=row, column=5).number_format = '"R$" #,##0.00'

    def print_report(self):
        self._finish_active_cell()

        report = self._printable_report()
        path = Path(tempfile.gettempdir()) / f"iomarques_live_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        try:
            path.write_text(report, encoding="utf-8")
            if sys.platform.startswith("win"):
                os.startfile(str(path), "print")
            elif sys.platform == "darwin":
                subprocess.run(["lp", str(path)], check=True)
            else:
                subprocess.run(["lp", str(path)], check=True)
        except Exception as exc:
            messagebox.showerror(
                "Não consegui imprimir",
                f"Salvei o relatório em:\n{path}\n\nErro ao enviar para a impressora:\n{exc}",
            )
            return

        messagebox.showinfo("Impressão enviada", "Enviei o resumo para a impressora padrão.")

    def clear_all(self):
        self._finish_active_cell()
        answer = messagebox.askyesno(
            "Começar nova live?",
            "Isso apaga todos os dados da tabela atual, o cronômetro e o salvamento automático. Deseja limpar tudo?",
        )
        if not answer:
            return

        if self.timer_job is not None:
            self.after_cancel(self.timer_job)
            self.timer_job = None
        self.live_running = False
        self.live_id = None
        self.live_first_started_at = None
        self.live_started_at = None
        self.live_finished_at = None
        self.live_elapsed_seconds = 0

        for child in self.body_frame.winfo_children():
            child.destroy()
        self.row_vars.clear()
        self.cell_frames.clear()
        self.cell_entries.clear()
        self.clear_buttons.clear()
        self.active_cell = None
        self.hovered_cell = None
        self._add_row()
        self._refresh_live_controls()
        self._save_rows()

    def _finish_active_cell(self):
        if self.active_cell and self._cell_exists(*self.active_cell):
            self._finish_cell_edit(*self.active_cell)

    def _on_close(self):
        self._finish_active_cell()
        self._save_rows()
        if self.timer_job is not None:
            self.after_cancel(self.timer_job)
        self.destroy()


if __name__ == "__main__":
    app = LiveSalesApp()
    app.mainloop()
