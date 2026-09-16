# Requires Windows PowerShell 5.1. Dot-source this file to test its functions safely.
[CmdletBinding()]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

# Published checksum: https://www.python.org/downloads/release/python-31315/
$script:PythonVersion = '3.13.15'
$script:PythonInstallerUrl = 'https://www.python.org/ftp/python/3.13.15/python-3.13.15-amd64.exe'
$script:PythonInstallerSha256 = 'edec09c4853aeae9ac36efb8c9f95b6b8e2fee65eee56d9767a8b7c69c574403'

function ConvertTo-NativeArgument {
    param([AllowEmptyString()][string]$Value)
    # ProcessStartInfo on .NET Framework takes a Windows command line, not argv.
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function Invoke-BootstrapProcess {
    param(
        [Parameter(Mandatory)][string]$Executable,
        [string[]]$Arguments = @(),
        [int]$TimeoutSeconds = 0
    )
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $Executable
    $startInfo.Arguments = (($Arguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' ')
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    try {
        if (-not $process.Start()) { throw "Nao foi possivel iniciar: $Executable" }
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if ($TimeoutSeconds -gt 0) {
            if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
                $process.Kill()
                $process.WaitForExit()
                throw "O processo demorou mais que o esperado: $Executable"
            }
        } else {
            $process.WaitForExit()
        }
        return [pscustomobject]@{
            ExitCode = $process.ExitCode
            Output = $stdout.GetAwaiter().GetResult()
            Error = $stderr.GetAwaiter().GetResult()
        }
    } finally {
        $process.Dispose()
    }
}

function Test-CompatiblePython {
    param([string]$Executable, [switch]$RequirePip)
    if ([string]::IsNullOrWhiteSpace($Executable) -or $Executable -match '(?i)[\\/]WindowsApps[\\/]') {
        return $false
    }
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return $false }
    $probe = "import struct,sys,tkinter,venv; assert sys.platform == 'win32'; assert (3,11) <= sys.version_info[:2] < (3,14); assert struct.calcsize('P') == 8; tkinter.Tcl().eval('info patchlevel'); print('IOMARQUES_PYTHON_OK')"
    if ($RequirePip) { $probe = 'import pip; ' + $probe }
    try {
        $result = Invoke-BootstrapProcess $Executable @('-I', '-c', $probe) -TimeoutSeconds 30
        return ($result.ExitCode -eq 0 -and $result.Output.Trim() -eq 'IOMARQUES_PYTHON_OK')
    } catch {
        return $false
    }
}

function Get-PythonCandidates {
    param([Parameter(Mandatory)][string]$InstallerRoot)
    Join-Path $InstallerRoot ('python-' + $script:PythonVersion + '\python.exe')
    foreach ($commandName in @('python.exe', 'python3.exe')) {
        Get-Command $commandName -CommandType Application -All -ErrorAction SilentlyContinue |
            ForEach-Object { $_.Source }
    }
    foreach ($minor in @('3.13', '3.12', '3.11')) {
        foreach ($registryRoot in @('HKCU:\Software\Python\PythonCore', 'HKLM:\Software\Python\PythonCore')) {
            $key = Join-Path $registryRoot ($minor + '\InstallPath')
            if (Test-Path -LiteralPath $key) {
                $installPath = (Get-Item -LiteralPath $key).GetValue('')
                if (-not [string]::IsNullOrWhiteSpace($installPath)) {
                    Join-Path $installPath 'python.exe'
                }
            }
        }
        Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) ('Programs\Python\Python' + $minor.Replace('.', '') + '\python.exe')
    }
}

function Assert-SupportedWindows {
    if ([Environment]::OSVersion.Platform -ne 'Win32NT' -or [Environment]::OSVersion.Version.Major -lt 10) {
        throw 'Este instalador precisa do Windows 10 ou Windows 11.'
    }
    $architecture = $env:PROCESSOR_ARCHITECTURE
    if ($env:PROCESSOR_ARCHITEW6432) { $architecture = $env:PROCESSOR_ARCHITEW6432 }
    if (-not [Environment]::Is64BitOperatingSystem -or $architecture -ne 'AMD64') {
        throw 'Este instalador e para Windows x64 (Intel/AMD). ARM e Windows de 32 bits ainda nao sao suportados.'
    }
}

function Assert-ManagedChild {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Root)
    $rootPath = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    $childPath = [IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    if (-not $childPath.StartsWith($rootPath + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Caminho fora da pasta exclusiva do instalador. Operacao interrompida.'
    }
    # Do not follow a junction/symlink while managing installer files.
    $currentPath = $childPath
    while ($currentPath.Length -ge $rootPath.Length) {
        if (Test-Path -LiteralPath $currentPath) {
            $item = Get-Item -LiteralPath $currentPath -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'A pasta do instalador nao pode usar links ou juncoes.'
            }
        }
        if ($currentPath -eq $rootPath) { break }
        $currentPath = [IO.Path]::GetDirectoryName($currentPath)
    }
}

function Test-OfficialInstaller {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ine $script:PythonInstallerSha256) { return $false }
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    return ($signature.Status -eq 'Valid' -and $null -ne $signature.SignerCertificate -and
        $signature.SignerCertificate.Subject -match '(^|,\s*)O=Python Software Foundation(,|$)')
}

function Get-RegisteredPython313 {
    # The official installer uses one per-user slot per minor version. Avoid
    # modifying somebody else's incomplete 3.13 installation in maintenance mode.
    $key = 'HKCU:\Software\Python\PythonCore\3.13\InstallPath'
    if (Test-Path -LiteralPath $key) { return (Get-Item -LiteralPath $key).GetValue('') }
    return $null
}

function Install-PrivatePython {
    param([Parameter(Mandatory)][string]$InstallerRoot)
    $runtimePath = Join-Path $InstallerRoot ('python-' + $script:PythonVersion)
    $downloadPath = Join-Path $InstallerRoot ('python-' + $script:PythonVersion + '-amd64.exe')
    Assert-ManagedChild $runtimePath $InstallerRoot
    Assert-ManagedChild $downloadPath $InstallerRoot
    $registeredPath = Get-RegisteredPython313
    if (-not [string]::IsNullOrWhiteSpace($registeredPath) -and
        [IO.Path]::GetFullPath($registeredPath).TrimEnd('\') -ine [IO.Path]::GetFullPath($runtimePath).TrimEnd('\')) {
        throw 'Existe um Python 3.13 incompleto neste usuario. Para nao alterar outros programas, a instalacao foi interrompida. Repare esse Python incluindo Tcl/Tk e pip, e clique em Instalar novamente.'
    }
    if (-not (Test-OfficialInstaller $downloadPath)) {
        Write-Host 'Baixando Python oficial. Isso pode levar alguns minutos...'
        $partialPath = Join-Path $InstallerRoot ('download-' + [guid]::NewGuid().ToString('N') + '.exe')
        Assert-ManagedChild $partialPath $InstallerRoot
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -UseBasicParsing -Uri $script:PythonInstallerUrl -OutFile $partialPath -TimeoutSec 300
            if (-not (Test-OfficialInstaller $partialPath)) {
                throw 'O download do Python nao passou na verificacao de seguranca (SHA-256 e assinatura). Nada foi executado. Verifique sua internet e tente novamente.'
            }
            Move-Item -LiteralPath $partialPath -Destination $downloadPath -Force
        } finally {
            if (Test-Path -LiteralPath $partialPath) {
                Assert-ManagedChild $partialPath $InstallerRoot
                Remove-Item -LiteralPath $partialPath -Force
            }
        }
    }
    Write-Host 'Preparando Python para este usuario, sem alterar PATH ou outros programas...'
    $logPath = Join-Path $InstallerRoot 'python-instalacao.log'
    $arguments = @('/quiet', '/norestart', '/log', $logPath,
        'InstallAllUsers=0', ('TargetDir=' + $runtimePath), 'PrependPath=0', 'AppendPath=0',
        'AssociateFiles=0', 'Shortcuts=0', 'Include_launcher=0', 'InstallLauncherAllUsers=0',
        'Include_doc=0', 'Include_test=0', 'Include_tools=0', 'Include_tcltk=1',
        'Include_pip=1', 'Include_exe=1', 'Include_lib=1', 'Include_dev=1',
        'Include_debug=0', 'Include_symbols=0', 'Include_freethreaded=0')
    $result = Invoke-BootstrapProcess $downloadPath $arguments
    if ($result.ExitCode -notin @(0, 3010)) {
        throw "Nao foi possivel preparar o Python (codigo $($result.ExitCode)). Detalhes: $logPath"
    }
    $pythonPath = Join-Path $runtimePath 'python.exe'
    if (-not (Test-CompatiblePython $pythonPath)) {
        if ($result.ExitCode -eq 3010) { throw 'O Windows pediu uma reinicializacao. Reinicie o computador e clique em Instalar novamente.' }
        throw "O Python instalado nao esta disponivel. Detalhes: $logPath"
    }
    return $pythonPath
}

function Get-PrivateEnvironment {
    param([Parameter(Mandatory)][string]$InstallerRoot)
    $venvPath = Join-Path $InstallerRoot 'venv'
    $venvPython = Join-Path $venvPath 'Scripts\python.exe'
    Assert-ManagedChild $venvPath $InstallerRoot
    if (Test-CompatiblePython $venvPython -RequirePip) { return $venvPython }
    $pythonPath = $null
    foreach ($candidate in @(Get-PythonCandidates $InstallerRoot | Select-Object -Unique)) {
        if (Test-CompatiblePython $candidate) {
            $pythonPath = $candidate
            break
        }
    }
    if (-not $pythonPath) { $pythonPath = Install-PrivatePython $InstallerRoot }
    if (Test-Path -LiteralPath $venvPath) {
        # Preserve an interrupted environment for diagnostics instead of deleting it.
        $backupPath = Join-Path $InstallerRoot ('venv-incompleto-' + [guid]::NewGuid().ToString('N'))
        Assert-ManagedChild $backupPath $InstallerRoot
        Move-Item -LiteralPath $venvPath -Destination $backupPath
    }
    Write-Host 'Criando o ambiente isolado do instalador...'
    $result = Invoke-BootstrapProcess $pythonPath @('-I', '-m', 'venv', $venvPath)
    if ($result.ExitCode -ne 0 -or -not (Test-CompatiblePython $venvPython -RequirePip)) {
        throw "Nao foi possivel criar o ambiente do instalador. $($result.Error.Trim())"
    }
    return $venvPython
}

function Invoke-InstallBootstrap {
    param([Parameter(Mandatory)][string]$SourceDirectory)
    Assert-SupportedWindows
    $sourcePath = (Resolve-Path -LiteralPath $SourceDirectory).Path
    $installerScript = Join-Path $sourcePath 'instalar_app_windows.py'
    if (-not (Test-Path -LiteralPath $installerScript -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $sourcePath 'app.py') -PathType Leaf)) {
        throw 'Extraia TODOS os arquivos do ZIP (Extrair Tudo) antes de clicar em Instalar.'
    }
    $localData = [Environment]::GetFolderPath('LocalApplicationData')
    if ([string]::IsNullOrWhiteSpace($localData)) { throw 'Nao foi possivel localizar a pasta local do seu usuario do Windows.' }
    $installerRoot = Join-Path $localData 'IoMarquesLive\instalador'
    Assert-ManagedChild $installerRoot $localData
    $null = New-Item -ItemType Directory -Path $installerRoot -Force
    $userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $mutex = New-Object Threading.Mutex($false, ('Local\IoMarquesLive.Bootstrap.' + $userSid))
    $ownsMutex = $false
    try {
        try { $ownsMutex = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $ownsMutex = $true }
        if (-not $ownsMutex) { throw 'Outro instalador do Io Marques ja esta aberto. Aguarde ou feche a outra janela.' }
        Write-Host 'Io Marques Brecho - preparando a instalacao'
        Write-Host 'Mantenha esta janela aberta. Na primeira vez, e necessario acesso a internet.'
        $venvPython = Get-PrivateEnvironment $installerRoot
        Write-Host 'Abrindo a janela de instalacao...'
        $result = Invoke-BootstrapProcess $venvPython @('-X', 'utf8', $installerScript, '--source-dir', $sourcePath)
        if ($result.ExitCode -ne 0) {
            throw "O instalador terminou sem concluir (codigo $($result.ExitCode)). $($result.Error.Trim())"
        }
    } finally {
        if ($ownsMutex) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}

# Importing functions in tests must not install software or open the application.
if ($MyInvocation.InvocationName -ne '.') {
    try {
        Invoke-InstallBootstrap -SourceDirectory $PSScriptRoot
        exit 0
    } catch {
        Write-Host ''
        Write-Host ('ERRO: ' + $_.Exception.Message) -ForegroundColor Red
        exit 1
    }
}
