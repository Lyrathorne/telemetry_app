param(
    [ValidateSet("Debug", "Release")]
    [string] $Configuration = "Release",
    [string] $PythonPath,
    [switch] $SkipTests
)

$ErrorActionPreference = "Stop"

$script:Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $script:Root

$script:AppName = "RacingTelemetry"
$script:BuildDir = Join-Path $script:Root "build_new"
$script:DistDir = Join-Path $script:Root "dist_new"
$script:FinalDistDir = Join-Path $script:Root "dist"
$script:PreviousDistDir = Join-Path $script:Root "dist_previous"
$script:SmokeRoot = Join-Path $script:Root "build_smoke_test"
$script:ReleaseRoot = Join-Path $script:Root "release"
$script:LogDir = Join-Path $script:Root "build_logs"
$script:Stage = "initialization"

New-Item -ItemType Directory -Force -Path $script:LogDir | Out-Null
$script:LogPath = Join-Path $script:LogDir ("build-{0}-{1}.log" -f $Configuration.ToLowerInvariant(), (Get-Date -Format "yyyyMMdd-HHmmss"))

function Write-Log {
    param(
        [string] $Message = "",
        [ConsoleColor] $Color = [ConsoleColor]::Gray
    )
    Write-Host $Message -ForegroundColor $Color
    Add-Content -LiteralPath $script:LogPath -Value $Message -Encoding UTF8
}

function Write-Stage {
    param([string] $Name)
    $script:Stage = $Name
    Write-Log ""
    Write-Log "== $Name ==" Cyan
}

function Fail-Build {
    param(
        [string] $Message,
        [int] $Code = 1,
        [string[]] $NotStartedStages = @()
    )
    Write-Log ""
    Write-Log "Build failed during: $script:Stage" Red
    Write-Log $Message Red
    if ($NotStartedStages.Count -gt 0) {
        Write-Log "Build stages not started:" Yellow
        foreach ($stageName in $NotStartedStages) {
            Write-Log "- $stageName" Yellow
        }
    }
    Write-Log "Log: $script:LogPath"
    if (Test-Path -LiteralPath $script:FinalDistDir) {
        Write-Log "Existing dist was preserved: $script:FinalDistDir"
    } else {
        Write-Log "dist does not exist."
    }
    if (Test-Path -LiteralPath $script:DistDir) {
        Write-Log "Temporary dist_new was preserved for diagnostics: $script:DistDir"
    } else {
        Write-Log "dist_new does not exist."
    }
    exit $Code
}

function Fail-Environment {
    param([string] $Message)
    Fail-Build $Message 1 @(
        "dependency installation",
        "tests",
        "PyInstaller",
        "smoke test",
        "release packaging"
    )
}

function Invoke-External {
    param(
        [string] $Name,
        [string] $FilePath,
        $Arguments = @(),
        $ExtraEnvironment = @{}
    )

    Write-Stage $Name
    $argumentList = @($Arguments)
    $environmentMap = @{}
    if ($ExtraEnvironment -is [hashtable]) {
        $environmentMap = $ExtraEnvironment
    }
    Write-Log ("> {0} {1}" -f $FilePath, ($Arguments -join " "))

    $stdoutPath = Join-Path $script:LogDir ("{0}-{1}-stdout.log" -f (Get-Date -Format "yyyyMMdd-HHmmssfff"), ($Name -replace '[^A-Za-z0-9_-]', '_'))
    $stderrPath = Join-Path $script:LogDir ("{0}-{1}-stderr.log" -f (Get-Date -Format "yyyyMMdd-HHmmssfff"), ($Name -replace '[^A-Za-z0-9_-]', '_'))
    $oldEnvironment = @{}
    foreach ($key in $environmentMap.Keys) {
        $oldEnvironment[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
        [Environment]::SetEnvironmentVariable($key, [string] $environmentMap[$key], "Process")
    }

    try {
        $process = Start-Process -FilePath $FilePath `
            -ArgumentList (Join-ProcessArguments $argumentList) `
            -WorkingDirectory $script:Root `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath `
            -NoNewWindow `
            -Wait `
            -PassThru
        $exitCode = $process.ExitCode
    } finally {
        foreach ($key in $oldEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable($key, $oldEnvironment[$key], "Process")
        }
    }

    if (Test-Path -LiteralPath $stdoutPath) {
        $stdoutLines = Get-Content -LiteralPath $stdoutPath -Encoding UTF8
        foreach ($line in $stdoutLines) {
            Write-Host $line
            Add-Content -LiteralPath $script:LogPath -Value $line -Encoding UTF8
        }
    }
    if (Test-Path -LiteralPath $stderrPath) {
        $stderrLines = Get-Content -LiteralPath $stderrPath -Encoding UTF8
        foreach ($line in $stderrLines) {
            Write-Host $line -ForegroundColor Yellow
            Add-Content -LiteralPath $script:LogPath -Value $line -Encoding UTF8
        }
    }

    Write-Log "$Name exited with code $exitCode."
    if ($exitCode -ne 0) {
        Fail-Build "$Name exited with code $exitCode." $exitCode
    }
}

function Join-ProcessArguments {
    param($Arguments)
    $quoted = @()
    foreach ($argument in @($Arguments)) {
        $value = [string] $argument
        if ($value -match '[\s"]') {
            $quoted += '"' + ($value -replace '"', '\"') + '"'
        } else {
            $quoted += $value
        }
    }
    return ($quoted -join " ")
}

function Invoke-WithRetry {
    param(
        [scriptblock] $Action,
        [string] $Description,
        [int] $Attempts = 6,
        [int] $DelaySeconds = 2
    )
    $lastError = $null
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            & $Action
            return
        } catch {
            $lastError = $_
            Write-Log "$Description failed on attempt $attempt/${Attempts}: $($_.Exception.Message)" Yellow
            if ($attempt -lt $Attempts) {
                Start-Sleep -Seconds $DelaySeconds
            }
        }
    }
    throw $lastError
}

function Test-WindowsAppsPython {
    param([string] $Path)
    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $false
    }
    return ($Path -match '\\AppData\\Local\\Microsoft\\WindowsApps\\python(\d+(\.\d+)*)?\.exe$')
}

function Get-ResolvedCommandPath {
    param([string] $Command)
    if ([string]::IsNullOrWhiteSpace($Command)) {
        return $null
    }
    if (Test-Path -LiteralPath $Command -PathType Leaf) {
        return (Resolve-Path -LiteralPath $Command).Path
    }
    $found = Get-Command $Command -ErrorAction SilentlyContinue
    if ($null -eq $found) {
        return $null
    }
    return $found.Source
}

function New-PythonCandidate {
    param(
        [string] $Label,
        [string] $Command,
        [string[]] $Arguments = @(),
        [switch] $Required
    )
    [pscustomobject]@{
        Label = $Label
        Command = $Command
        Arguments = @($Arguments)
        Required = [bool] $Required
    }
}

function Test-PythonCandidate {
    param($Candidate)

    $resolvedCommand = Get-ResolvedCommandPath $Candidate.Command
    Write-Log "Python candidate: $($Candidate.Label)"
    Write-Log "Python command: $($Candidate.Command)"
    if ($Candidate.Arguments.Count -gt 0) {
        Write-Log "Python command arguments: $($Candidate.Arguments -join ' ')"
    }
    if ($resolvedCommand) {
        Write-Log "Python command path: $resolvedCommand"
    }

    if (-not $resolvedCommand) {
        Write-Log "Python executable: "
        Write-Log "Python version: "
        Write-Log "Python architecture: "
        Write-Log "Candidate rejected: command was not found" Yellow
        Write-Log "Reason: command was not found"
        return $null
    }

    if (Test-WindowsAppsPython $resolvedCommand) {
        Write-Log "Python executable: $resolvedCommand"
        Write-Log "Python version: "
        Write-Log "Python architecture: "
        Write-Log "Candidate rejected: WindowsApps alias" Yellow
        Write-Log "Reason: The WindowsApps python.exe entry is only a Microsoft Store alias and cannot be used for this build."
        return $null
    }

    $probe = "import sys, struct; print(sys.executable); print(sys.version.replace(chr(10), ' ')); print(struct.calcsize('P') * 8)"
    $probeArgs = @($Candidate.Arguments) + @("-c", $probe)
    $output = @()
    $exitCode = 0
    try {
        $output = & $resolvedCommand @probeArgs 2>&1
        $exitCode = $LASTEXITCODE
    } catch {
        $output = @($_.Exception.Message)
        $exitCode = 1
    }

    if ($exitCode -ne 0 -or $output.Count -lt 3) {
        Write-Log "Python executable: "
        Write-Log "Python version: "
        Write-Log "Python architecture: "
        Write-Log "Candidate rejected: probe command failed" Yellow
        Write-Log "Reason: python probe exited with code $exitCode. $($output -join ' ')"
        return $null
    }

    $executable = [string] $output[0]
    $version = [string] $output[1]
    $architecture = [string] $output[2]
    Write-Log "Python executable: $executable"
    Write-Log "Python version: $version"
    Write-Log "Python architecture: $architecture"

    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        Write-Log "Candidate rejected: sys.executable does not point to an existing file" Yellow
        Write-Log "Reason: sys.executable was $executable"
        return $null
    }
    if (Test-WindowsAppsPython $executable) {
        Write-Log "Candidate rejected: WindowsApps alias" Yellow
        Write-Log "Reason: sys.executable points to the Microsoft Store alias."
        return $null
    }
    if ($architecture -ne "64") {
        Write-Log "Candidate rejected: unsupported architecture" Yellow
        Write-Log "Reason: expected 64-bit Python, got $architecture-bit."
        return $null
    }
    if ($version -notmatch '^3\.(11|12|13|14)\.') {
        Write-Log "Candidate rejected: unsupported Python version" Yellow
        Write-Log "Reason: expected Python 3.11, 3.12, 3.13, or 3.14."
        return $null
    }

    Write-Log "Candidate accepted: $executable" Green
    Write-Log "Reason: usable 64-bit Python."
    [pscustomobject]@{
        Command = $resolvedCommand
        Arguments = @($Candidate.Arguments)
        Executable = $executable
        Version = $version
        Architecture = $architecture
        Label = $Candidate.Label
    }
}

function Get-PythonCandidates {
    $candidates = @()
    if (-not [string]::IsNullOrWhiteSpace($PythonPath)) {
        $candidates += New-PythonCandidate -Label "explicit -PythonPath" -Command $PythonPath -Required
    }

    $venvPython = Join-Path $script:Root ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        $candidates += New-PythonCandidate -Label "project .venv" -Command $venvPython
    }

    $pyLauncher = Get-ResolvedCommandPath "py"
    if ($pyLauncher) {
        $candidates += New-PythonCandidate -Label "Python Launcher py -3.13" -Command $pyLauncher -Arguments @("-3.13")
        $candidates += New-PythonCandidate -Label "Python Launcher py -3.12" -Command $pyLauncher -Arguments @("-3.12")
        $candidates += New-PythonCandidate -Label "Python Launcher py -3.11" -Command $pyLauncher -Arguments @("-3.11")
    }

    $pathPython = Get-ResolvedCommandPath "python"
    if ($pathPython) {
        $candidates += New-PythonCandidate -Label "python.exe from PATH" -Command $pathPython
    }

    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        foreach ($versionDir in @("Python313", "Python312", "Python311")) {
            $candidatePath = Join-Path $env:LOCALAPPDATA "Programs\Python\$versionDir\python.exe"
            if (Test-Path -LiteralPath $candidatePath -PathType Leaf) {
                $candidates += New-PythonCandidate -Label "standard install $versionDir" -Command $candidatePath
            }
        }
    }

    $systemCandidates = @(
        "$env:ProgramFiles\Python313\python.exe",
        "$env:ProgramFiles\Python312\python.exe",
        "$env:ProgramFiles\Python311\python.exe",
        "${env:ProgramFiles(x86)}\Python313\python.exe",
        "${env:ProgramFiles(x86)}\Python312\python.exe",
        "${env:ProgramFiles(x86)}\Python311\python.exe",
        "C:\Python313\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe"
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    foreach ($candidatePath in $systemCandidates) {
        if (Test-Path -LiteralPath $candidatePath -PathType Leaf) {
            $candidates += New-PythonCandidate -Label "system install $(Split-Path -Leaf (Split-Path -Parent $candidatePath))" -Command $candidatePath
        }
    }

    return $candidates
}

function Get-ProjectPython {
    $venvPython = Join-Path $script:Root ".venv\Scripts\python.exe"
    $candidates = Get-PythonCandidates
    $accepted = $null
    foreach ($candidate in $candidates) {
        $accepted = Test-PythonCandidate $candidate
        if ($accepted) {
            break
        }
        if ($candidate.Required) {
            break
        }
    }

    if (-not $accepted) {
        $message = @"
Primary failure: Python environment discovery

No usable 64-bit Python installation was found.

The WindowsApps python.exe entry is only a Microsoft Store alias and cannot be used for this build.

Install 64-bit Python from python.org or pass its path explicitly:

.\build.ps1 -PythonPath "C:\Path\To\python.exe"

End users do not need Python. Python is required only to build the application.
"@
        Fail-Environment $message
    }

    if ((Test-Path -LiteralPath $venvPython -PathType Leaf) -and ((Resolve-Path -LiteralPath $venvPython).Path -ieq (Resolve-Path -LiteralPath $accepted.Executable).Path)) {
        Write-Log "Using existing project .venv: $venvPython" Green
        return $venvPython
    }

    if (($accepted.Label -eq "explicit -PythonPath") -and -not (Test-Path -LiteralPath (Join-Path $script:Root ".venv") -PathType Container)) {
        Write-Log "No project .venv was found. Creating .venv with explicit PythonPath: $($accepted.Executable)"
        Invoke-External -Name "create .venv" -FilePath $accepted.Command -Arguments (@($accepted.Arguments) + @("-m", "venv", ".venv"))
        $venvCheck = Test-PythonCandidate (New-PythonCandidate -Label "newly created project .venv" -Command $venvPython -Required)
        if (-not $venvCheck) {
            Fail-Environment "The project .venv was created, but .venv\Scripts\python.exe failed validation."
        }
        return $venvPython
    }

    if ($accepted.Label -eq "explicit -PythonPath") {
        Write-Log "Using explicit PythonPath: $($accepted.Executable)" Green
        return $accepted.Executable
    }

    if (Test-Path -LiteralPath (Join-Path $script:Root ".venv") -PathType Container) {
        Fail-Environment "A project .venv exists but its Python interpreter was not usable. Remove or recreate only this project's .venv after reviewing the candidate log above: $(Join-Path $script:Root ".venv")"
    }

    Write-Log "No project .venv was found. Creating .venv with: $($accepted.Executable)"
    Invoke-External -Name "create .venv" -FilePath $accepted.Command -Arguments (@($accepted.Arguments) + @("-m", "venv", ".venv"))
    $venvCheck = Test-PythonCandidate (New-PythonCandidate -Label "newly created project .venv" -Command $venvPython -Required)
    if (-not $venvCheck) {
        Fail-Environment "The project .venv was created, but .venv\Scripts\python.exe failed validation."
    }
    return $venvPython
}

function Stop-RunningBuiltExecutables {
    Write-Stage "stop running built executables"
    $targets = @(
        (Join-Path $script:FinalDistDir "$script:AppName\$script:AppName.exe"),
        (Join-Path $script:FinalDistDir "$script:AppName-debug\$script:AppName-debug.exe")
    )
    $running = Get-CimInstance Win32_Process -Filter "name = '$script:AppName.exe' or name = '$script:AppName-debug.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $targets -contains $_.ExecutablePath }
    foreach ($process in $running) {
        Write-Log "Stopping running executable: $($process.ExecutablePath) pid=$($process.ProcessId)"
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if (-not $running) {
        Write-Log "No running built executable found."
    }
}

function New-Readme {
    param([string] $AppDir)
    $text = @"
Racing Telemetry for Windows x64

1. Extract the ZIP completely.
2. Do not move RacingTelemetry.exe away from the _internal folder.
3. Run RacingTelemetry.exe.
4. Python, pip, PyInstaller, PySide6, NumPy and pyqtgraph do not need to be installed on the target PC.
5. Windows SmartScreen can warn about unsigned apps. Open additional details only if this archive came from a trusted source.
6. User data is stored in %LOCALAPPDATA%\RacingTelemetry:
   - data\racing_telemetry.sqlite3
   - logs\racing_telemetry.log
   - settings\
   - exports\
7. If the app crashes before the window appears, check %LOCALAPPDATA%\RacingTelemetry\logs\crash-*.log.

The whole RacingTelemetry folder is the application. Copying only the EXE is not supported.
"@
    Set-Content -LiteralPath (Join-Path $AppDir "README.txt") -Value $text -Encoding UTF8
}

function Invoke-SmokeTest {
    param(
        [string] $SourceAppDir,
        [string] $ExeName
    )
    Write-Stage "smoke test"
    if (Test-Path -LiteralPath $script:SmokeRoot) {
        Remove-Item -LiteralPath $script:SmokeRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $script:SmokeRoot | Out-Null
    $smokeAppDir = Join-Path $script:SmokeRoot (Split-Path -Leaf $SourceAppDir)
    Copy-Item -LiteralPath $SourceAppDir -Destination $smokeAppDir -Recurse -Force
    $smokeExe = Join-Path $smokeAppDir $ExeName
    if (-not (Test-Path -LiteralPath $smokeExe)) {
        Fail-Build "Smoke executable was not found: $smokeExe"
    }
    Push-Location -LiteralPath $script:SmokeRoot
    try {
        Invoke-External -Name "run packaged smoke test" -FilePath $smokeExe -Arguments @("--smoke-test")
    } finally {
        Pop-Location
    }
}

function Replace-FinalDist {
    param([string] $TargetFolderName)
    Write-Stage "replace final dist"
    Stop-RunningBuiltExecutables

    if (Test-Path -LiteralPath $script:PreviousDistDir) {
        Invoke-WithRetry { Remove-Item -LiteralPath $script:PreviousDistDir -Recurse -Force -ErrorAction Stop } "Removing dist_previous"
    }

    New-Item -ItemType Directory -Force -Path $script:FinalDistDir | Out-Null
    New-Item -ItemType Directory -Force -Path $script:PreviousDistDir | Out-Null

    $sourceTarget = Join-Path $script:DistDir $TargetFolderName
    $finalTarget = Join-Path $script:FinalDistDir $TargetFolderName
    $previousTarget = Join-Path $script:PreviousDistDir $TargetFolderName
    if (-not (Test-Path -LiteralPath $sourceTarget)) {
        throw "Temporary target folder was not found: $sourceTarget"
    }

    $movedOldTarget = $false
    if (Test-Path -LiteralPath $finalTarget) {
        Invoke-WithRetry { Move-Item -LiteralPath $finalTarget -Destination $previousTarget -Force -ErrorAction Stop } "Moving existing target to dist_previous"
        $movedOldTarget = $true
    }
    try {
        Invoke-WithRetry { Move-Item -LiteralPath $sourceTarget -Destination $finalTarget -Force -ErrorAction Stop } "Moving new target to dist"
    } catch {
        if ($movedOldTarget -and (Test-Path -LiteralPath $previousTarget) -and -not (Test-Path -LiteralPath $finalTarget)) {
            Move-Item -LiteralPath $previousTarget -Destination $finalTarget -Force -ErrorAction SilentlyContinue
        }
        throw
    }

    if (Test-Path -LiteralPath $script:PreviousDistDir) {
        Remove-Item -LiteralPath $script:PreviousDistDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $script:DistDir) {
        Remove-Item -LiteralPath $script:DistDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function New-ReleaseZip {
    param([string] $AppDir)
    Write-Stage "release zip"
    New-Item -ItemType Directory -Force -Path $script:ReleaseRoot | Out-Null
    $zipPath = Join-Path $script:ReleaseRoot "$script:AppName-Windows-x64.zip"
    if (Test-Path -LiteralPath $zipPath) {
        Remove-Item -LiteralPath $zipPath -Force
    }
    Compress-Archive -LiteralPath $AppDir -DestinationPath $zipPath -Force
    $hash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash
    Write-Log "ZIP: $zipPath"
    Write-Log "SHA-256: $hash"
    return $zipPath
}

try {
    Write-Stage "environment"
    Write-Log "Project root: $script:Root"
    Write-Log "Configuration: $Configuration"
    Write-Log "Log: $script:LogPath"
    if ($SkipTests) {
        Write-Log "WARNING: Tests were skipped. This build is not fully validated." Yellow
    }

    $python = Get-ProjectPython
    Invoke-External -Name "Python version" -FilePath $python -Arguments @("--version")
    Invoke-External -Name "Python architecture" -FilePath $python -Arguments @("-c", "import struct; print(struct.calcsize('P') * 8)")
    Invoke-External -Name "Python executable" -FilePath $python -Arguments @("-c", "import sys; print(sys.executable); print(sys.version)")

    Write-Stage "dependency check"
    Invoke-External -Name "install project requirements" -FilePath $python -Arguments @("-m", "pip", "install", "-r", "requirements.txt", "-r", "requirements-dev.txt")
    Invoke-External -Name "import runtime dependencies" -FilePath $python -Arguments @("-c", "import PySide6, numpy, pyqtgraph, PyInstaller")
    Invoke-External -Name "PyInstaller version" -FilePath $python -Arguments @("-m", "PyInstaller", "--version")

    if (-not $SkipTests) {
        Invoke-External -Name "tests" -FilePath $python -Arguments @("-m", "unittest", "discover", "-s", "tests", "-p", "test*.py", "-v") -ExtraEnvironment @{ "QT_QPA_PLATFORM" = "offscreen" }
    }

    Write-Stage "cleanup temporary build folders"
    if (Test-Path -LiteralPath $script:BuildDir) {
        Remove-Item -LiteralPath $script:BuildDir -Recurse -Force
    }
    if (Test-Path -LiteralPath $script:DistDir) {
        Remove-Item -LiteralPath $script:DistDir -Recurse -Force
    }

    $env:RT_BUILD_CONFIGURATION = $Configuration
    try {
        Invoke-External -Name "PyInstaller build" -FilePath $python -Arguments @(
            "-m", "PyInstaller",
            "--noconfirm",
            "--clean",
            "--workpath", $script:BuildDir,
            "--distpath", $script:DistDir,
            "RacingTelemetry.spec"
        ) -ExtraEnvironment @{ "RT_BUILD_CONFIGURATION" = $Configuration }
    } finally {
        Remove-Item Env:\RT_BUILD_CONFIGURATION -ErrorAction SilentlyContinue
    }

    Write-Stage "verify temporary package"
    $targetFolderName = if ($Configuration -eq "Debug") { "$script:AppName-debug" } else { $script:AppName }
    $targetExeName = if ($Configuration -eq "Debug") { "$script:AppName-debug.exe" } else { "$script:AppName.exe" }
    $tempAppDir = Join-Path $script:DistDir $targetFolderName
    $tempExe = Join-Path $tempAppDir $targetExeName
    if (-not (Test-Path -LiteralPath $tempExe)) {
        Fail-Build "Build finished, but executable was not found: $tempExe"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $tempAppDir "_internal"))) {
        Fail-Build "Build finished, but _internal folder was not found in: $tempAppDir"
    }
    New-Readme $tempAppDir
    Get-ChildItem -Force -LiteralPath $tempAppDir | ForEach-Object { Write-Log ("{0} {1}" -f $_.Name, $_.Length) }

    Invoke-SmokeTest $tempAppDir $targetExeName

    Replace-FinalDist $targetFolderName

    if (Test-Path -LiteralPath (Join-Path $script:Root "build")) {
        Remove-Item -LiteralPath (Join-Path $script:Root "build") -Recurse -Force
    }
    if (Test-Path -LiteralPath $script:BuildDir) {
        Rename-Item -LiteralPath $script:BuildDir -NewName "build"
    }

    $finalAppDir = Join-Path $script:FinalDistDir $targetFolderName
    $finalExe = Join-Path $finalAppDir $targetExeName
    if (-not (Test-Path -LiteralPath $finalExe)) {
        Fail-Build "Final executable was not found after replacing dist: $finalExe"
    }

    if ($Configuration -eq "Release") {
        $zipPath = New-ReleaseZip $finalAppDir
        Write-Stage "smoke test extracted zip"
        $extractRoot = Join-Path $script:Root "release_smoke_test"
        if (Test-Path -LiteralPath $extractRoot) {
            Remove-Item -LiteralPath $extractRoot -Recurse -Force
        }
        New-Item -ItemType Directory -Force -Path $extractRoot | Out-Null
        Expand-Archive -LiteralPath $zipPath -DestinationPath $extractRoot -Force
        $extractedExe = Join-Path $extractRoot "$script:AppName\$script:AppName.exe"
        Push-Location -LiteralPath $extractRoot
        try {
            Invoke-External -Name "run extracted release smoke test" -FilePath $extractedExe -Arguments @("--smoke-test")
        } finally {
            Pop-Location
        }
    }

    Write-Stage "summary"
    Write-Log "Build succeeded."
    Write-Log "Executable: $finalExe"
    if ($Configuration -eq "Release") {
        Write-Log "ZIP: $(Join-Path $script:ReleaseRoot "$script:AppName-Windows-x64.zip")"
    }
    Write-Log "Log: $script:LogPath"
    exit 0
} catch {
    Fail-Build $_.Exception.Message 1
}
