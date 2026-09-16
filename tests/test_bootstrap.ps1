# Offline tests: no downloads, real installs, application launch or Supabase calls.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'instalar.ps1')

$script:TestsRun = 0
function Assert-Equal {
    param($Expected, $Actual, [string]$Message)
    $script:TestsRun++
    if ($Expected -cne $Actual) { throw "$Message - esperado: [$Expected], recebido: [$Actual]" }
}
function Assert-Throws {
    param([scriptblock]$Action, [string]$Pattern)
    $script:TestsRun++
    try { & $Action } catch {
        if ($_.Exception.Message -notmatch $Pattern) { throw }
        return
    }
    throw 'Era esperada uma falha controlada.'
}

$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('iomarques-bootstrap-tests-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $testRoot
try {
    Assert-Equal '""' (ConvertTo-NativeArgument '') 'Argumento vazio'
    Assert-Equal '"pasta com espaco"' (ConvertTo-NativeArgument 'pasta com espaco') 'Espacos preservados'
    Assert-Equal '"C:\pasta\\"' (ConvertTo-NativeArgument 'C:\pasta\') 'Barra final preservada'
    Assert-Equal '"nome \"entre aspas\""' (ConvertTo-NativeArgument 'nome "entre aspas"') 'Aspas internas preservadas'
    Assert-Equal $false (Test-CompatiblePython 'C:\inexistente\python.exe') 'Executavel ausente'
    Assert-Equal $false (Test-CompatiblePython 'C:\Users\Teste\Microsoft\WindowsApps\python.exe') 'Alias Windows Store rejeitado'
    Assert-Equal $false (Test-OfficialInstaller (Join-Path $testRoot 'inexistente.exe')) 'Download ausente rejeitado'

    $fakeInstaller = Join-Path $testRoot 'invalido.exe'
    $null = New-Item -ItemType File -Path $fakeInstaller
    Assert-Equal $false (Test-OfficialInstaller $fakeInstaller) 'Hash invalido rejeitado antes de executar'
    Assert-ManagedChild (Join-Path $testRoot 'filho\arquivo.exe') $testRoot
    Assert-Throws { Assert-ManagedChild $testRoot $testRoot } 'fora da pasta'
    Assert-Throws { Assert-ManagedChild ($testRoot + '-outro\arquivo') $testRoot } 'fora da pasta'
    Assert-Throws { Assert-ManagedChild (Join-Path $testRoot '..\fora') $testRoot } 'fora da pasta'

    & {
        $script:ProbeCalled = $false
        function Invoke-BootstrapProcess {
            param($Executable, $Arguments, $TimeoutSeconds)
            $script:ProbeCalled = $true
            Assert-Equal 30 $TimeoutSeconds 'Timeout da sondagem'
            Assert-Equal '-I' $Arguments[0] 'Sondagem isolada'
            return [pscustomobject]@{ ExitCode = 0; Output = "IOMARQUES_PYTHON_OK`n"; Error = '' }
        }
        Assert-Equal $true (Test-CompatiblePython $fakeInstaller) 'Sondagem compativel reconhecida'
        Assert-Equal $true $script:ProbeCalled 'Sondagem executada'
    }
    & {
        function Invoke-BootstrapProcess {
            param($Executable, $Arguments, $TimeoutSeconds)
            return [pscustomobject]@{ ExitCode = 1; Output = 'IOMARQUES_PYTHON_OK'; Error = 'erro' }
        }
        Assert-Equal $false (Test-CompatiblePython $fakeInstaller) 'Codigo de erro nao ignorado'
    }
    & {
        function Test-CompatiblePython { param($Executable, [switch]$RequirePip) return $true }
        function Get-PythonCandidates { throw 'Nao deveria procurar Python quando o venv ja funciona.' }
        function Install-PrivatePython { throw 'Nao deveria baixar Python quando o venv ja funciona.' }
        Assert-Equal (Join-Path $testRoot 'venv\Scripts\python.exe') (Get-PrivateEnvironment $testRoot) 'Ambiente existente reutilizado'
    }
    & {
        $script:VenvCreated = $false
        function Test-CompatiblePython {
            param($Executable, [switch]$RequirePip)
            if ($RequirePip) { return $script:VenvCreated }
            return $Executable -eq $fakeInstaller
        }
        function Get-PythonCandidates { param($InstallerRoot) return @('inexistente', $fakeInstaller) }
        function Install-PrivatePython { throw 'Python existente deveria ser reutilizado.' }
        function Invoke-BootstrapProcess {
            param($Executable, $Arguments)
            Assert-Equal $fakeInstaller $Executable 'Python existente usado'
            Assert-Equal '-I -m venv' ($Arguments[0..2] -join ' ') 'Venv criado sem pip global'
            Assert-Equal (Join-Path $testRoot 'venv') $Arguments[3] 'Destino privado do venv'
            $script:VenvCreated = $true
            return [pscustomobject]@{ ExitCode = 0; Output = ''; Error = '' }
        }
        Assert-Equal (Join-Path $testRoot 'venv\Scripts\python.exe') (Get-PrivateEnvironment $testRoot) 'Criacao isolada com Python encontrado'
    }
    & {
        function Test-CompatiblePython { param($Executable, [switch]$RequirePip) return $false }
        function Get-PythonCandidates { param($InstallerRoot) return @() }
        function Install-PrivatePython { param($InstallerRoot) return $fakeInstaller }
        function Invoke-BootstrapProcess {
            param($Executable, $Arguments)
            return [pscustomobject]@{ ExitCode = 7; Output = ''; Error = 'falha simulada' }
        }
        Assert-Throws { Get-PrivateEnvironment $testRoot } 'falha simulada'
    }
    & {
        $script:DownloadCalled = $false
        $script:NativeInstallCalled = $false
        function Get-RegisteredPython313 { return $null }
        function Test-OfficialInstaller { param($Path) return $false }
        function Invoke-BootstrapProcess { $script:NativeInstallCalled = $true; throw 'Nao executar download invalido.' }
        # Define the switch with the same shape as Invoke-WebRequest in PS 5.1.
        function Invoke-WebRequest {
            param([switch]$UseBasicParsing, $Uri, $OutFile, $TimeoutSec)
            $script:DownloadCalled = $true
        }
        Assert-Throws { Install-PrivatePython $testRoot } 'verificacao de seguranca'
        Assert-Equal $true $script:DownloadCalled 'Download simulado solicitado'
        Assert-Equal $false $script:NativeInstallCalled 'Download adulterado jamais executado'
    }
    & {
        function Get-RegisteredPython313 { return $null }
        function Test-OfficialInstaller { param($Path) return $true }
        function Test-CompatiblePython { param($Executable, [switch]$RequirePip) return $true }
        function Invoke-WebRequest { throw 'Nao baixar novamente cache oficial valido.' }
        function Invoke-BootstrapProcess {
            param($Executable, $Arguments)
            foreach ($argument in @('InstallAllUsers=0', 'PrependPath=0', 'AppendPath=0', 'AssociateFiles=0', 'Include_launcher=0', 'Include_tcltk=1', 'Include_pip=1')) {
                Assert-Equal $true ($Arguments -contains $argument) ('Opcao segura: ' + $argument)
            }
            return [pscustomobject]@{ ExitCode = 0; Output = ''; Error = '' }
        }
        Assert-Equal (Join-Path $testRoot 'python-3.13.15\python.exe') (Install-PrivatePython $testRoot) 'Python oficial privado preparado'
    }
    & {
        function Get-RegisteredPython313 { return 'C:\Outro programa\Python313' }
        function Invoke-WebRequest { throw 'Nao baixar quando ha uma instalacao alheia incompleta.' }
        Assert-Throws { Install-PrivatePython $testRoot } 'nao alterar outros programas'
    }
    & {
        $actualPython = Get-PythonCandidates $testRoot | Where-Object { Test-CompatiblePython $_ } | Select-Object -First 1
        if ($actualPython) {
            $values = @('', 'pasta com espaco', 'C:\pasta\', 'aspas "preservadas"', "apostrofo ' e & parenteses (texto)")
            $probe = 'import json,sys; print(json.dumps(sys.argv[1:]))'
            $result = Invoke-BootstrapProcess $actualPython (@('-I', '-c', $probe) + $values) -TimeoutSeconds 30
            Assert-Equal 0 $result.ExitCode 'Processo real somente leitura'
            $received = $result.Output | ConvertFrom-Json
            for ($i = 0; $i -lt $values.Count; $i++) { Assert-Equal $values[$i] $received[$i] 'Argumento round-trip Windows' }
            $failure = Invoke-BootstrapProcess $actualPython @('-I', '-c', 'raise SystemExit(17)') -TimeoutSeconds 30
            Assert-Equal 17 $failure.ExitCode 'Exit code real preservado'
        } else {
            Write-Host 'Python compativel ausente: teste opcional de round-trip ignorado.'
        }
    }
    Write-Host ("OK: {0} verificacoes do bootstrap passaram, sem instalar nada." -f $script:TestsRun)
} finally {
    $resolvedTestRoot = [IO.Path]::GetFullPath($testRoot)
    $expectedParent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
    if ([IO.Path]::GetDirectoryName($resolvedTestRoot) -ne $expectedParent -or
        [IO.Path]::GetFileName($resolvedTestRoot) -notmatch '^iomarques-bootstrap-tests-[0-9a-f]{32}$') {
        throw 'Diretorio temporario inesperado; limpeza cancelada.'
    }
    Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
}
