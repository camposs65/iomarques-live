"""Build and install locally. Never opens the app or contacts its database."""

import json
import ctypes
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

from preparar_config_integracao import write_config


APP_NAME = "IoMarques Brecho"
EXE_NAME = f"{APP_NAME}.exe"
SOURCE_FILES = ("app.py", "cloud_store.py", "supabase_sync.py", "roguelike_game.py",
                "gerar_assets_logo.py")
ASSET_FILES = ("logo_original.png", "entregas.png", "avaliacoes.pdf")
RUNTIME_FILES = ("cloud_pendencias.dat", "cloud_migracao.json", "integracao_sessao.dat",
                 "integracao_pendencias.json", "live_atual.json", "historico_lives.json")
PACKAGE_FILES = (EXE_NAME, "integracao_supabase.json")


def install_directory():
    location = os.environ.get("LOCALAPPDATA")
    if not location or not Path(location).is_absolute():
        raise RuntimeError("Não encontrei a pasta de aplicativos deste usuário do Windows.")
    return Path(location) / "Programs" / "IoMarquesLive"


def run_command(command, cwd, log):
    """Log command output, but never echo environment/configuration values."""
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    with Path(log).open("a", encoding="utf-8") as stream:
        result = subprocess.run(command, cwd=cwd, stdout=stream,
                                stderr=subprocess.STDOUT, **options)
    if result.returncode:
        raise RuntimeError(f"Uma etapa da instalação falhou (código {result.returncode}). "
                           f"Consulte o relatório em:\n{log}")


def _powershell(script):
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise RuntimeError("O Windows não conseguiu consultar ou criar o atalho do aplicativo.")
    return result.stdout.strip()


def _windows_arguments(command):
    """Decode argv using Windows itself; quoted paths and apostrophes are data."""
    if not command:
        return []
    count = ctypes.c_int()
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    arguments = shell32.CommandLineToArgvW(command, ctypes.byref(count))
    if not arguments:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return [arguments[index] for index in range(count.value)]
    finally:
        kernel32.LocalFree(arguments)


def _python_script(arguments):
    index = 1
    while index < len(arguments):
        argument = arguments[index]
        if argument == "-" or argument.startswith(("-c", "-m")):
            return None
        if argument == "--":
            return arguments[index + 1] if index + 1 < len(arguments) else None
        if argument in {"-X", "-W"}:
            index += 2
            continue
        if not argument.startswith("-"):
            return argument
        index += 1
    return None


def _is_live_process(process, known_scripts):
    name = str(process.get("name") or "").casefold()
    if name == EXE_NAME.casefold():
        return True
    if not (name.startswith("python") or name in {"py.exe", "pyw.exe"}):
        return False
    script = _python_script(_windows_arguments(str(process.get("command") or "")))
    if not script or Path(script).name.casefold() != "app.py":
        return False
    if Path(script).is_absolute():
        return os.path.normcase(os.path.abspath(script)) in known_scripts
    title = unicodedata.normalize("NFKD", str(process.get("title") or ""))
    title = "".join(char for char in title if not unicodedata.combining(char)).casefold()
    # Relative app.py alone is used by many unrelated projects. Only the
    # identifiable sales window makes a relative invocation ours.
    return title.startswith("iomarques brecho - controle de vendas da live")


def ensure_app_closed(source=None, legacy=None, destination=None):
    if os.name != "nt":
        return
    directories = [Path(value) for value in (source, legacy, destination) if value]
    if source:
        directories.append(Path(source) / "dist")
    known_scripts = {os.path.normcase(os.path.abspath(path / "app.py")) for path in directories}
    result = _powershell(
        "$ErrorActionPreference = 'Stop'; "
        "[Console]::OutputEncoding = [Text.UTF8Encoding]::new(); "
        "$items = @(Get-CimInstance Win32_Process -Filter \"Name = 'IoMarques Brecho.exe' "
        "OR Name LIKE 'python%.exe' OR Name = 'py.exe' OR Name = 'pyw.exe'\" | "
        "ForEach-Object { $window = Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue; "
        "[pscustomobject]@{ name = $_.Name; command = $_.CommandLine; title = $window.MainWindowTitle } }); "
        "ConvertTo-Json -InputObject $items -Compress"
    )
    try:
        processes = json.loads(result)
        if not isinstance(processes, list) or any(not isinstance(value, dict) for value in processes):
            raise ValueError("unexpected process response")
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Não foi possível verificar se a versão antiga está aberta. "
                           "Feche o aplicativo e tente novamente.") from exc
    if any(_is_live_process(process, known_scripts) for process in processes):
        raise RuntimeError("Feche o IoMarques Brechó antes de instalar ou atualizar. "
                           "Confira primeiro se não há alterações pendentes de envio.")


def _checked_path(path, root=None):
    """Validate lexical paths and every existing ancestor before resolving links."""
    candidate = Path(os.path.abspath(path))
    if root is not None:
        boundary = Path(os.path.abspath(root))
        if candidate == boundary or not candidate.is_relative_to(boundary):
            raise RuntimeError("Caminho fora da pasta exclusiva da instalação. Operação cancelada.")
    for current in (candidate, *candidate.parents):
        try:
            attributes = current.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(attributes.st_mode)
                or getattr(attributes, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise RuntimeError("A instalação não pode usar links ou junções de pastas. "
                               f"Nenhum destino externo foi alterado: {current}")
    return candidate


def previous_shortcut_directory():
    if os.name != "nt":
        return None
    target = _powershell(
        "[Console]::OutputEncoding = [Text.UTF8Encoding]::new(); "
        "$path = Join-Path ([Environment]::GetFolderPath('Desktop')) 'IoMarques Brecho.lnk'; "
        "if (Test-Path -LiteralPath $path) { "
        "$shell = New-Object -ComObject WScript.Shell; "
        "$shell.CreateShortcut($path).TargetPath }"
    )
    if target and Path(target).is_absolute() and Path(target).name == EXE_NAME:
        return Path(target).parent.resolve()
    return None


def has_runtime_data(directory):
    directory = Path(directory)
    return any((directory / name).exists() for name in RUNTIME_FILES) or any(
        (directory / "backups" / name).exists()
        for name in ("live_atual.bak.json", "historico_lives.bak.json")
    )


def legacy_candidates(source, destination, previous=None):
    """Existing installations win; ambiguous legacy folders require a user choice."""
    source, destination = Path(source).resolve(), _checked_path(destination)
    if has_runtime_data(destination):
        return []
    if previous and Path(previous).resolve() != destination and has_runtime_data(previous):
        return [Path(previous).resolve()]
    return [directory for directory in (source / "dist", source)
            if directory != destination and has_runtime_data(directory)]


def find_automation(source, previous=None, destination=None):
    """Preserve the optional separate Direct project, not its credentials."""
    if destination:
        try:
            settings = json.loads((Path(destination) / "instalacao.json").read_text(encoding="utf-8"))
            candidate = Path(settings.get("automation_app", ""))
            if candidate.is_absolute() and candidate.is_file() and candidate.name == "app.py":
                return str(candidate)
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    for directory in (source, previous, Path.home() / "placeholder"):
        if not directory:
            continue
        for parent in (Path(directory).parent, Path(directory).parent.parent):
            candidate = parent / "iomarques-instagram-direct" / "app.py"
            if candidate.is_file():
                return str(candidate.resolve())
    return None


def build_package(source, output, work, python, log, progress=lambda *_: None):
    """An allowlist keeps backups, sessions, outbox and exports out of the executable."""
    source, output, work = Path(source), Path(output), Path(work)
    staged_source = work / "source"
    staged_source.mkdir(parents=True)
    for name in SOURCE_FILES:
        shutil.copy2(source / name, staged_source / name)
    staged_assets = staged_source / "assets"
    staged_assets.mkdir()
    for name in ASSET_FILES:
        shutil.copy2(source / "assets" / name, staged_assets / name)
    output.mkdir(parents=True, exist_ok=True)
    write_config(output / "integracao_supabase.json", root=source)
    shutil.copy2(output / "integracao_supabase.json", staged_source / "config_publica.json")
    progress(35, "Preparando a identidade do aplicativo...")
    run_command([str(python), "-X", "utf8", "gerar_assets_logo.py"], staged_source, log)
    progress(45, "Criando o aplicativo. Essa etapa pode levar alguns minutos...")
    run_command([
        str(python), "-X", "utf8", "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onefile", "--windowed", "--name", APP_NAME,
        "--distpath", str(output), "--workpath", str(work / "build"),
        "--specpath", str(work), "--icon", str(staged_assets / "app_icon.ico"),
        "--add-data", f"{staged_assets};assets",
        "--add-data", f"{staged_source / 'config_publica.json'};.", "app.py",
    ], staged_source, log)
    for name in PACKAGE_FILES:
        if not (output / name).is_file() or not (output / name).stat().st_size:
            raise RuntimeError(f"O pacote ficou incompleto: {name}.")
    run_command([str(output / EXE_NAME), "--verificar-instalacao"], output, log)
    return output


def _safe_runtime_files(legacy):
    legacy = _checked_path(legacy)
    files = [legacy / name for name in RUNTIME_FILES if (legacy / name).exists()]
    if (legacy / "backups").is_dir():
        files.extend((legacy / "backups").glob("*.json"))
    for path in files:
        _checked_path(path, legacy)
        if not path.is_file():
            raise RuntimeError("A pasta antiga contém um caminho de dados inesperado. Nada foi apagado.")
        yield path, path.relative_to(legacy)


def deploy_package(package, destination, legacy=None, automation_app=None):
    """Replace only program files. On failure restore old program and newly copied data."""
    package, destination = Path(package).resolve(), _checked_path(destination)
    if not all((package / name).is_file() for name in PACKAGE_FILES):
        raise RuntimeError("Pacote incompleto. A instalação existente foi preservada.")
    destination.mkdir(parents=True, exist_ok=True)
    _checked_path(destination)
    staging = Path(tempfile.mkdtemp(prefix=".instalar-", dir=destination))
    preserve_staging = False
    try:
        _checked_path(staging, destination)
        changes = []
        if legacy and not has_runtime_data(destination):
            for original, relative in _safe_runtime_files(legacy):
                staged = staging / "new" / relative
                staged.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original, staged)
                changes.append(relative)
        for name in PACKAGE_FILES:
            staged = staging / "new" / name
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(package / name, staged)
            changes.append(Path(name))
        metadata = {"installer_version": 1}
        if automation_app:
            metadata["automation_app"] = str(automation_app)
        (staging / "new" / "instalacao.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        changes.append(Path("instalacao.json"))
        originals, written = {}, []
        try:
            for relative in changes:
                target = _checked_path(destination / relative, destination)
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    backup = _checked_path(staging / "old" / relative, staging)
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, backup)
                    originals[relative] = backup
                os.replace(staging / "new" / relative, target)
                written.append(relative)
        except Exception as error:
            recovery_errors = []
            for relative in reversed(written):
                try:
                    target = _checked_path(destination / relative, destination)
                    if relative in originals:
                        original = _checked_path(originals[relative], staging)
                        os.replace(original, target)
                    else:
                        target.unlink()
                except Exception as recovery_error:
                    recovery_errors.append(f"{relative}: {recovery_error}")
            if recovery_errors:
                preserve_staging = True
                raise RuntimeError(
                    "A atualização falhou e a recuperação automática não pôde terminar. "
                    "Não abra o aplicativo até recuperar a instalação. "
                    f"As cópias de segurança foram preservadas em:\n{staging}\n"
                    + "\n".join(recovery_errors)
                ) from error
            raise
    finally:
        if not preserve_staging:
            # Only the uniquely-created child may be removed, after success or
            # a complete rollback. Never erase recovery originals after failure.
            _checked_path(staging, destination)
            if staging.parent != destination or not staging.name.startswith(".instalar-"):
                raise RuntimeError("Pasta temporária inesperada. Limpeza cancelada.")
            shutil.rmtree(staging)
    return destination / EXE_NAME


def create_shortcut(executable):
    executable = Path(executable).resolve()
    if not executable.is_file():
        raise RuntimeError("O aplicativo não foi encontrado para criar o atalho.")
    target = str(executable).replace("'", "''")
    folder = str(executable.parent).replace("'", "''")
    _powershell(
        "$ErrorActionPreference = 'Stop'; "
        "$desktop = [Environment]::GetFolderPath('Desktop'); "
        "$shell = New-Object -ComObject WScript.Shell; "
        "$shortcut = $shell.CreateShortcut((Join-Path $desktop 'IoMarques Brecho.lnk')); "
        f"$shortcut.TargetPath = '{target}'; $shortcut.WorkingDirectory = '{folder}'; "
        f"$shortcut.IconLocation = '{target},0'; "
        "$shortcut.Description = 'IoMarques Brecho - Controle de vendas da live'; $shortcut.Save()"
    )


def install(source, destination, legacy, log, progress=lambda *_: None):
    ensure_app_closed(source, legacy, destination)
    progress(10, "Baixando as dependências na pasta privada do instalador...")
    run_command([sys.executable, "-X", "utf8", "-m", "pip", "install",
                 "--disable-pip-version-check", "--only-binary=:all:",
                 "-r", str(Path(source) / "requirements-build.txt")], source, log)
    with tempfile.TemporaryDirectory(prefix="iomarques-build-") as temporary:
        work = Path(temporary)
        package = build_package(source, work / "package", work, sys.executable, log, progress)
        ensure_app_closed(source, legacy, destination)
        progress(85, "Instalando e preservando os dados existentes...")
        exe = deploy_package(package, destination, legacy,
                             find_automation(source, legacy, destination))
    progress(95, "Criando o atalho na Área de Trabalho...")
    create_shortcut(exe)
    progress(100, "Instalação concluída!")
    return exe
