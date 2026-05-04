$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

Write-Host "Instalando dependencias..."
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller

Write-Host "Preparando logo e tela de carregamento..."
python gerar_assets_logo.py

Write-Host "Criando executavel..."
$pyinstallerArgs = @(
    "--noconfirm",
    "--clean",
    "--onefile",
    "--windowed",
    "--name", "IoMarques Brecho"
)

if (Test-Path "assets\app_icon.ico") {
    $pyinstallerArgs += @("--icon", "assets\app_icon.ico")
}

if (Test-Path "assets") {
    $pyinstallerArgs += @("--add-data", "assets;assets")
}

$pyinstallerArgs += "app.py"
python -m PyInstaller @pyinstallerArgs

$exePath = Join-Path $PSScriptRoot "dist\IoMarques Brecho.exe"
if (-not (Test-Path $exePath)) {
    throw "Executavel nao encontrado em: $exePath"
}

$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "IoMarques Brecho.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $exePath
$shortcut.WorkingDirectory = Split-Path $exePath
$shortcut.Description = "Controle de vendas da live - IoMarques Brecho"
$shortcut.Save()

Write-Host ""
Write-Host "Pronto!"
Write-Host "Executavel: $exePath"
Write-Host "Atalho criado em: $shortcutPath"
