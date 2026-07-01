$ErrorActionPreference = "Stop"

$script:Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $script:Root

$script:AppName = "RacingTelemetry"
$script:BuildDir = Join-Path $script:Root "build_new"
$script:DistDir = Join-Path $script:Root "dist_new"
$script:FinalBuildDir = Join-Path $script:Root "build"
$script:FinalDistDir = Join-Path $script:Root "dist"
$script:PreviousDistDir = Join-Path $script:Root "dist_previous"
$script:DebugExe = Join-Path $script:FinalDistDir "$script:AppName-debug.exe"
$script:ReleaseExe = Join-Path $script:FinalDistDir "$script:AppName.exe"
$script:LogDir = Join-Path $script:Root "build_logs"
$script:Stage = "initialization"

New-Item -ItemType Directory -Force -Path $script:LogDir | Out-Null
$script:LogPath = Join-Path $script:LogDir ("build-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))

Start-Transcript -Path $script:LogPath -Force | Out-Null

function Write-Stage {
    param([string] $Name)
    $script:Stage = $Name
    Write-Host ""
    Write-Host "== $Name =="
}

function Fail-Build {
    param(
        [string] $Message,
        [int] $Code = 1
    )
    Write-Host ""
    Write-Host "Build failed during: $script:Stage"
    Write-Host $Message
    Write-Host "Log: $script:LogPath"
    Write-Host "Expected debug executable: $script:DebugExe"
    Write-Host "Expected release executable: $script:ReleaseExe"
    Write-Host "Existing dist contents:"
    if (Test-Path -LiteralPath $script:FinalDistDir) {
        Get-ChildItem -Force -LiteralPath $script:FinalDistDir | Format-Table Name, Length, LastWriteTime
    } else {
        Write-Host "dist does not exist."
    }
    Write-Host "Temporary output contents:"
    if (Test-Path -LiteralPath $script:DistDir) {
        Get-ChildItem -Force -LiteralPath $script:DistDir | Format-Table Name, Length, LastWriteTime
    } else {
        Write-Host "dist_new does not exist."
    }
    Stop-Transcript | Out-Null
    exit $Code
}

function Invoke-CommandChecked {
    param(
        [string] $Name,
        [string] $FilePath,
        [string[]] $Arguments = @()
    )
    Write-Stage $Name
    Write-Host "> $FilePath $($Arguments -join ' ')"
    & $FilePath @Arguments
    $exitCode = if ($global:LASTEXITCODE -is [int]) { $global:LASTEXITCODE } else { 0 }
    if ($exitCode -ne 0) {
        Fail-Build "$Name exited with code $exitCode." $exitCode
    }
}

function Stop-RunningBuiltExecutables {
    Write-Stage "stop running built executables"
    $targets = @(
        (Join-Path $script:FinalDistDir "$script:AppName.exe"),
        (Join-Path $script:FinalDistDir "$script:AppName-debug.exe")
    )
    $running = Get-CimInstance Win32_Process -Filter "name = '$script:AppName.exe' or name = '$script:AppName-debug.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $targets -contains $_.ExecutablePath }
    foreach ($process in $running) {
        Write-Host "Stopping running executable: $($process.ExecutablePath) pid=$($process.ProcessId)"
        try {
            Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
        } catch {
            Write-Host "Could not stop process $($process.ProcessId): $($_.Exception.Message)"
        }
    }
    if (-not $running) {
        Write-Host "No running built executable found."
    }
    Start-Sleep -Seconds 3
}

function Invoke-WithRetry {
    param(
        [scriptblock] $Action,
        [string] $Description,
        [int] $Attempts = 8,
        [int] $DelaySeconds = 2
    )
    $lastError = $null
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            & $Action
            return
        } catch {
            $lastError = $_
            Write-Host "$Description failed on attempt $attempt/${Attempts}: $($_.Exception.Message)"
            if ($attempt -lt $Attempts) {
                Start-Sleep -Seconds $DelaySeconds
            }
        }
    }
    throw $lastError
}

function Replace-FinalDist {
    Write-Stage "replace final dist"
    Stop-RunningBuiltExecutables

    if (Test-Path -LiteralPath $script:PreviousDistDir) {
        Invoke-WithRetry { Remove-Item -LiteralPath $script:PreviousDistDir -Recurse -Force -ErrorAction Stop } "Removing dist_previous"
    }

    $movedOldDist = $false
    if (Test-Path -LiteralPath $script:FinalDistDir) {
        Invoke-WithRetry { Rename-Item -LiteralPath $script:FinalDistDir -NewName "dist_previous" -ErrorAction Stop } "Moving existing dist to dist_previous"
        $movedOldDist = $true
    }

    try {
        Invoke-WithRetry { Rename-Item -LiteralPath $script:DistDir -NewName "dist" -ErrorAction Stop } "Moving dist_new to dist"
    } catch {
        if ($movedOldDist -and (Test-Path -LiteralPath $script:PreviousDistDir) -and -not (Test-Path -LiteralPath $script:FinalDistDir)) {
            Rename-Item -LiteralPath $script:PreviousDistDir -NewName "dist" -ErrorAction SilentlyContinue
        }
        throw
    }

    if ($movedOldDist -and (Test-Path -LiteralPath $script:PreviousDistDir)) {
        Remove-Item -LiteralPath $script:PreviousDistDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

try {
    Write-Stage "environment"
    Write-Host "Project root: $script:Root"
    Write-Host "Log: $script:LogPath"

    $venvPython = Join-Path $script:Root ".venv\Scripts\python.exe"
    $legacyVenvPython = Join-Path $script:Root ".venv.venv\Scripts\python.exe"

    if (Test-Path -LiteralPath $venvPython) {
        $python = $venvPython
    } elseif (Test-Path -LiteralPath $legacyVenvPython) {
        $python = $legacyVenvPython
    } else {
        $systemPython = Get-Command python -ErrorAction SilentlyContinue
        if ($null -eq $systemPython) {
            Fail-Build "Python was not found. Install Python 3.14 or copy/build from a machine with Python installed."
        }
        Write-Host "No project .venv was found. Creating .venv with: $($systemPython.Source)"
        & $systemPython.Source -m venv ".venv"
        if ($LASTEXITCODE -ne 0) {
            Fail-Build "Could not create .venv. Python venv support may be missing." $LASTEXITCODE
        }
        $python = $venvPython
    }

    Write-Host "Using Python:"
    & $python -c "import sys; print(sys.executable); print(sys.version)"
    if ($LASTEXITCODE -ne 0) {
        Fail-Build "Selected Python could not start." $LASTEXITCODE
    }

    Write-Stage "dependency check"
    & $python -c "import PySide6, numpy, pyqtgraph, PyInstaller"
    if ($LASTEXITCODE -ne 0) {
        Invoke-CommandChecked "install project requirements" $python @("-m", "pip", "install", "-r", "requirements.txt", "-r", "requirements-dev.txt")
    }
    Invoke-CommandChecked "PyInstaller version" $python @("-m", "PyInstaller", "--version")

    Write-Stage "cleanup temporary build folders"
    if (Test-Path -LiteralPath $script:BuildDir) {
        Remove-Item -LiteralPath $script:BuildDir -Recurse -Force
    }
    if (Test-Path -LiteralPath $script:DistDir) {
        Remove-Item -LiteralPath $script:DistDir -Recurse -Force
    }

    $env:QT_QPA_PLATFORM = "offscreen"
    Invoke-CommandChecked "test run" $python @("-m", "unittest", "discover", "-s", "tests")
    Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue

    Invoke-CommandChecked "PyInstaller build" $python @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--workpath", $script:BuildDir,
        "--distpath", $script:DistDir,
        "RacingTelemetry.spec"
    )

    Write-Stage "verify temporary executables"
    $tempDebugExe = Join-Path $script:DistDir "$script:AppName-debug.exe"
    $tempReleaseExe = Join-Path $script:DistDir "$script:AppName.exe"
    if (-not (Test-Path -LiteralPath $tempDebugExe)) {
        Fail-Build "Build finished, but debug executable was not found: $tempDebugExe"
    }
    if (-not (Test-Path -LiteralPath $tempReleaseExe)) {
        Fail-Build "Build finished, but release executable was not found: $tempReleaseExe"
    }
    Get-ChildItem -Force -LiteralPath $script:DistDir | Format-Table Name, Length, LastWriteTime

    Replace-FinalDist

    if (Test-Path -LiteralPath $script:FinalBuildDir) {
        Remove-Item -LiteralPath $script:FinalBuildDir -Recurse -Force
    }
    if (Test-Path -LiteralPath $script:BuildDir) {
        Rename-Item -LiteralPath $script:BuildDir -NewName "build"
    }

    Write-Stage "final executable verification"
    if (-not (Test-Path -LiteralPath $script:DebugExe)) {
        Fail-Build "Final debug executable was not found after replacing dist."
    }
    if (-not (Test-Path -LiteralPath $script:ReleaseExe)) {
        Fail-Build "Final release executable was not found after replacing dist."
    }
    Get-ChildItem -Force -LiteralPath $script:FinalDistDir | Format-Table Name, Length, LastWriteTime

    Write-Host ""
    Write-Host "Build succeeded."
    Write-Host "Debug executable: $script:DebugExe"
    Write-Host "Release executable: $script:ReleaseExe"
    Write-Host "Log: $script:LogPath"
    Stop-Transcript | Out-Null
    exit 0
} catch {
    Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue
    Fail-Build $_.Exception.Message 1
}
