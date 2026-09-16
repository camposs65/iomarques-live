"""Started by Instalar.bat after its private Python is ready."""

import argparse
import ctypes
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from installation import (install, install_directory, legacy_candidates,
                          previous_shortcut_directory)


class Installer(tk.Tk):
    def __init__(self, source):
        super().__init__()
        self.source = Path(source).resolve()
        self.destination = install_directory()
        self.log = Path(os.environ["LOCALAPPDATA"]) / "IoMarquesLive" / "instalador" / "instalacao.log"
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.events = queue.Queue()
        self.busy = False
        self.executable = None
        self.title("Instalar IoMarques Brechó")
        self.geometry("640x330")
        self.resizable(False, False)
        self.configure(bg="#F9F0F5")
        tk.Label(self, text="IoMarques Brechó", bg="#76324f", fg="white",
                 font=("Segoe UI", 24, "bold"), pady=22).pack(fill="x")
        panel = tk.Frame(self, bg="#F9F0F5", padx=28, pady=22)
        panel.pack(fill="both", expand=True)
        self.status = tk.Label(panel, text="Preparando a instalação...", bg="#F9F0F5",
                               fg="#2C1040", font=("Segoe UI", 11), anchor="w", wraplength=570)
        self.status.pack(fill="x")
        self.progress = ttk.Progressbar(panel, maximum=100)
        self.progress.pack(fill="x", pady=16)
        tk.Label(panel, text="O atalho ficará na Área de Trabalho. Seus dados serão preservados.",
                 bg="#F9F0F5", fg="#76324f", font=("Segoe UI", 10), wraplength=570).pack(anchor="w")
        buttons = tk.Frame(panel, bg="#F9F0F5")
        buttons.pack(fill="x", pady=(18, 0))
        self.open_button = ttk.Button(buttons, text="Abrir aplicativo", command=self.open_app, state="disabled")
        self.open_button.pack(side="right")
        self.close_button = ttk.Button(buttons, text="Fechar", command=self.close)
        self.close_button.pack(side="right", padx=8)
        ttk.Button(buttons, text="Ver relatório", command=self.open_log).pack(side="left")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(100, self.poll)
        self.after(250, self.start_install)

    def start_install(self):
        try:
            candidates = legacy_candidates(self.source, self.destination, previous_shortcut_directory())
            legacy = candidates[0] if len(candidates) == 1 else None
            if len(candidates) > 1:
                messagebox.showinfo("Dados da versão antiga", "Encontrei mais de uma pasta com dados antigos. "
                                    "Selecione a pasta usada pelo aplicativo da loja (onde estão os arquivos "
                                    "live_atual.json ou historico_lives.json). Nenhum original será apagado.")
                chosen = filedialog.askdirectory(title="Pasta usada pela versão antiga", initialdir=self.source)
                if not chosen or Path(chosen).resolve() not in candidates:
                    raise RuntimeError("Instalação cancelada para preservar os dados. "
                                       "Selecione uma das pastas encontradas:\n" + "\n".join(map(str, candidates)))
                legacy = Path(chosen).resolve()
            self.busy = True
            self.close_button.configure(state="disabled")
            threading.Thread(target=self.worker, args=(legacy,), daemon=True).start()
        except Exception as exc:
            self.show_error(str(exc))

    def worker(self, legacy):
        try:
            executable = install(self.source, self.destination, legacy, self.log,
                                 lambda value, text: self.events.put(("progress", value, text)))
            self.events.put(("done", executable))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "progress":
                    self.progress["value"] = event[1]
                    self.status.configure(text=event[2])
                elif event[0] == "done":
                    self.busy = False
                    self.executable = event[1]
                    self.close_button.configure(state="normal")
                    self.open_button.configure(state="normal")
                    self.status.configure(text="Pronto! Abra pelo atalho e entre com seu usuário do brechó.")
                elif event[0] == "error":
                    self.show_error(event[1])
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def show_error(self, detail):
        self.busy = False
        self.close_button.configure(state="normal")
        self.status.configure(text="A instalação não foi concluída.")
        messagebox.showerror("Instalação interrompida", detail)

    def open_log(self):
        if self.log.exists():
            os.startfile(self.log)

    def close(self):
        if self.busy:
            messagebox.showinfo("Instalação em andamento", "Aguarde a instalação terminar antes de fechar.")
            return
        self.destroy()

    def open_app(self):
        if self.executable:
            try:
                subprocess.Popen([str(self.executable)], cwd=self.destination,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
            except OSError as exc:
                messagebox.showerror("Não foi possível abrir", str(exc))
                return
            self.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Este instalador é destinado ao Windows.")
    if sys.prefix == sys.base_prefix:
        parser.error("Abra Instalar.bat para preparar o ambiente privado do instalador.")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.CreateMutexW(None, False, "Local\\IoMarquesLiveInstaller")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        ctypes.windll.user32.MessageBoxW(None, "O instalador já está aberto.", "IoMarques Brechó", 0)
        kernel32.CloseHandle(handle)
        return 1
    try:
        installer = Installer(args.source_dir)
        installer.mainloop()
        return 0 if installer.executable else 1
    finally:
        kernel32.CloseHandle(handle)


if __name__ == "__main__":
    raise SystemExit(main())
