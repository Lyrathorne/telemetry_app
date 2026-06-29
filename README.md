# Racing Telemetry

Offline Windows desktop telemetry dashboard for racing games. The app is written in Python and PySide6 and can run from source or as a standalone Windows executable.

## Supported Sources

- Demo telemetry
- F1 2018 UDP telemetry
- Assetto Corsa shared memory
- Assetto Corsa Competizione shared memory

The app launches even when the games are closed. AC and ACC will show `Waiting for game` until their shared-memory pages exist.

## Requirements

- Windows 10 or newer
- Python 3.14
- Dependencies in `requirements.txt`

## Run From Source

```bat
python -m pip install -r requirements.txt
python main.py
```

Open directly in demo mode:

```bat
python main.py --demo
```

Run diagnostics:

```bat
python main.py --diagnostics
```

## Build The Executable

Use the project build script:

```bat
build.bat
```

The script installs runtime and build dependencies, removes old `build/` and `dist/` output, runs tests, and builds both executables:

- `dist\RacingTelemetry-debug.exe` - console-enabled debug build
- `dist\RacingTelemetry.exe` - windowed release build

Use the debug executable first when investigating startup problems:

```bat
dist\RacingTelemetry-debug.exe --diagnostics
```

## F1 2018 UDP Setup

In F1 2018, enable UDP telemetry in the game settings and use the same port shown in the app. The default is `20777`. If the port is already used by another application, Racing Telemetry shows a visible error and stays stopped.

## Assetto Corsa And ACC

AC and ACC expose telemetry through Windows shared memory while the game/session is running. When the game is closed, the app reports `Waiting for game` or `No telemetry received`; this is expected and should not be treated as a crash.

## Logs And Data

Runtime files are stored under:

```text
%LOCALAPPDATA%\RacingTelemetry\
    logs\
    data\
    exports\
    settings\
```

Startup and telemetry errors are written to:

```text
%LOCALAPPDATA%\RacingTelemetry\logs\racing_telemetry.log
```

The app never writes logs next to the executable.

## Troubleshooting

### Executable opens and immediately closes

Run the debug executable from a terminal:

```bat
dist\RacingTelemetry-debug.exe
```

Then check `%LOCALAPPDATA%\RacingTelemetry\logs\racing_telemetry.log`.

### Missing Qt platform plugin

Rebuild with `build.bat`. The PyInstaller spec collects PySide6 data and dynamic libraries so the Windows Qt platform plugin is bundled with the executable.

### UDP port already in use

Choose another port in the app and in F1 2018, or close the process that owns the port. Diagnostics can probe the default port:

```bat
dist\RacingTelemetry-debug.exe --diagnostics
```

### Game is running but no telemetry appears

For F1 2018, confirm UDP telemetry is enabled and the port matches. For AC/ACC, start or enter a live session so shared memory starts updating.

### Antivirus blocks the executable

Build artifacts may be scanned or quarantined because they are locally generated unsigned executables. Allow the executable or rebuild it locally from source.

### Works on the development PC but not another PC

Copy the executable from `dist\`, not the old source-tree artifact. Run `RacingTelemetry-debug.exe --diagnostics` on the target PC and check the log file under `%LOCALAPPDATA%`.

## Known Limitations

Hardware/game integration still needs validation with actual F1 2018, Assetto Corsa, and ACC sessions. Unit tests intentionally do not require any game to be installed.
