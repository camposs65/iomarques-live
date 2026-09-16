@echo off
setlocal
title Instalar Io Marques Brecho
if not exist "%~dp0instalar.ps1" (
    echo Extraia primeiro TODOS os arquivos do ZIP usando "Extrair Tudo".
    echo Depois abra a pasta extraida e clique novamente em Instalar.
    pause
    exit /b 1
)
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1"
set "INSTALL_RESULT=%ERRORLEVEL%"
if not "%INSTALL_RESULT%"=="0" (
    echo.
    echo A instalacao nao foi concluida. Veja a mensagem acima e tente novamente.
    pause
)
exit /b %INSTALL_RESULT%
