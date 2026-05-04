import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk


ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
EXE_PATH = ROOT / "dist" / "IoMarques Brecho.exe"

PRIMARY = "#7B3F7A"
SECONDARY = "#EDD6ED"
BG = "#F9F0F5"
TEXT = "#2C1040"


class Installer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Instalador IoMarques Brechó")
        self.geometry("640x360")
        self.resizable(False, False)
        self.configure(bg=BG)

        self.prepare_splash_asset()
        self.splash_image = self._load_image("install_splash.png")
        self._build_ui()
        self.after(250, self.start_install)

    def _load_image(self, name):
        path = ASSETS / name
        if not path.exists():
            return None
        try:
            return tk.PhotoImage(file=str(path))
        except tk.TclError:
            return None

    def prepare_splash_asset(self):
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0
        subprocess.run(
            [sys.executable, "gerar_assets_logo.py"],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )

    def _build_ui(self):
        if self.splash_image:
            image_label = tk.Label(self, image=self.splash_image, bd=0)
            image_label.pack(fill="x")
            self.geometry(f"{self.splash_image.width()}x{self.splash_image.height() + 104}")
        else:
            header = tk.Frame(self, bg=PRIMARY)
            header.pack(fill="x")
            tk.Label(
                header,
                text="Instalador IoMarques Brechó",
                bg=PRIMARY,
                fg="white",
                font=("Segoe UI Semibold", 20),
            ).pack(pady=22)

        panel = tk.Frame(self, bg=BG)
        panel.pack(fill="both", expand=True, padx=28, pady=20)

        self.status = tk.Label(
            panel,
            text="Preparando instalação...",
            bg=BG,
            fg=TEXT,
            anchor="w",
            font=("Segoe UI", 11),
        )
        self.status.pack(fill="x", pady=(0, 12))

        self.progress = ttk.Progressbar(panel, mode="determinate", maximum=100)
        self.progress.pack(fill="x")

        self.detail = tk.Label(
            panel,
            text="",
            bg=BG,
            fg=PRIMARY,
            anchor="w",
            font=("Segoe UI", 9),
        )
        self.detail.pack(fill="x", pady=(10, 0))

    def start_install(self):
        threading.Thread(target=self.install, daemon=True).start()

    def set_step(self, percent, status, detail=""):
        self.after(0, lambda: self._set_step(percent, status, detail))

    def _set_step(self, percent, status, detail):
        self.progress["value"] = percent
        self.status.configure(text=status)
        self.detail.configure(text=detail)

    def run_command(self, command, detail):
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0
        result = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        if result.returncode != 0:
            raise RuntimeError(f"{detail}\n\n{result.stdout}")

    def install(self):
        try:
            self.set_step(8, "Instalando dependências...", "Isso pode levar alguns minutos na primeira vez.")
            self.run_command([sys.executable, "-m", "pip", "install", "--upgrade", "pip"], "Falha ao atualizar pip")
            self.run_command([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], "Falha nas dependências")

            self.set_step(28, "Preparando logo e ícone...", "Usando assets/logo_original.png quando existir.")
            self.run_command([sys.executable, "gerar_assets_logo.py"], "Falha ao preparar os assets")

            self.set_step(45, "Instalando empacotador...", "Preparando PyInstaller.")
            self.run_command([sys.executable, "-m", "pip", "install", "pyinstaller"], "Falha ao instalar PyInstaller")

            self.set_step(60, "Criando aplicativo...", "Gerando o executável sem terminal.")
            command = [
                sys.executable,
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--clean",
                "--onefile",
                "--windowed",
                "--name",
                "IoMarques Brecho",
            ]
            icon_path = ASSETS / "app_icon.ico"
            if icon_path.exists():
                command += ["--icon", str(icon_path)]
            if ASSETS.exists():
                command += ["--add-data", f"{ASSETS};assets"]
            command.append("app.py")
            self.run_command(command, "Falha ao criar o executável")

            self.set_step(86, "Criando atalho na Área de Trabalho...", str(EXE_PATH))
            if not EXE_PATH.exists():
                raise RuntimeError(f"Executável não encontrado em:\n{EXE_PATH}")
            self.create_shortcut()

            self.set_step(100, "Instalação concluída.", "Atalho criado na Área de Trabalho.")
            self.after(0, lambda: messagebox.showinfo("Pronto", "O app IoMarques Brechó foi instalado."))
        except Exception as exc:
            self.set_step(0, "Não consegui concluir a instalação.", "Veja a mensagem de erro.")
            self.after(0, lambda: messagebox.showerror("Erro na instalação", str(exc)))

    def create_shortcut(self):
        if not sys.platform.startswith("win"):
            return
        desktop = Path.home() / "Desktop"
        shortcut_path = desktop / "IoMarques Brecho.lnk"

        script = (
            "$shell = New-Object -ComObject WScript.Shell\n"
            f"$shortcut = $shell.CreateShortcut('{shortcut_path}')\n"
            f"$shortcut.TargetPath = '{EXE_PATH}'\n"
            f"$shortcut.WorkingDirectory = '{EXE_PATH.parent}'\n"
            "$shortcut.Description = 'Controle de vendas da live - IoMarques Brecho'\n"
            "$shortcut.Save()\n"
        )
        self.run_command(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], "Falha no atalho")


if __name__ == "__main__":
    app = Installer()
    app.mainloop()
