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

## Fullscreen And Shortcuts

- `F11` toggles fullscreen.
- `Escape` exits fullscreen.
- `Ctrl+I` imports telemetry.
- `Ctrl+Shift+G` adds a graph panel.
- `Ctrl+R` starts or stops recording.
- `Ctrl+0` resets the dashboard layout.
- `Space` pauses/resumes graph rendering when a graph panel has focus.

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

In F1 2018, enable UDP telemetry in the game settings and use the same port shown in the app. The default is `20777`. The port field is a validated numeric control with range `1-65535`, and the selected value is saved in application settings. If the port is already used by another application, Racing Telemetry shows a visible error and stays stopped.

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

Imported session metadata and saved settings are stored under the same `%LOCALAPPDATA%\RacingTelemetry\` tree. Imported sessions are referenced from their original file path and also stored in the app's session index for offline use.

## Dashboard Panels And Layouts

The dashboard uses movable Qt dock panels:

- `Live Telemetry`
- `Live Graphs`
- `Source Status`
- `Connection Diagnostics`
- `Imported Sessions`
- `Comparison Graphs`

Use the `View` menu to hide or restore panels. Panels can be docked, floated, resized, and moved around the window. The current geometry and dock arrangement are saved automatically when the app closes and restored on the next start when layout restoration is enabled.

The `Layouts` menu includes:

- `Default`
- `Live Driving`
- `Telemetry Analysis`
- `Save layout as...`
- `Load layout...`

`Reset layout` restores a sensible default arrangement.

## Importing Telemetry

Use `File > Import telemetry...` or the `Import` button in the `Imported Sessions` panel.

Supported formats:

- CSV
- JSON
- The app's own exported JSON telemetry session format

Preferred CSV schema:

```csv
timestamp,session_time,lap_number,lap_time,lap_distance,speed_kph,rpm,gear,throttle,brake,clutch,steering
1710000000.125,15.240,1,15.240,532.6,184.2,7200,5,0.84,0.00,0.00,-0.07
```

Simpler CSV files are also accepted:

```csv
time,speed,rpm,throttle,brake
0.000,0.0,900,0.0,0.0
0.050,2.1,1100,0.3,0.0
```

Recognized column aliases include:

```text
time, timestamp, session_time, lap, lap_number, lap_time, lap_distance,
distance, speed, speed_kph, speed_kmh, speed_mph, rpm, gear,
throttle, accelerator, brake, steering, steer, clutch
```

Supported delimiters are comma, semicolon, and tab. UTF-8 and common Windows text encodings are tried. Decimal comma is supported for numeric values when possible.

Units are normalized internally:

- speed: km/h
- throttle, brake, clutch: percent `0-100`
- steering: imported numeric value, usually normalized or angle depending on the source file
- time values: seconds

Small example files live in `examples/`.

## Comparing Sessions

Select two or more sessions in the `Imported Sessions` panel by checking their `Compare` boxes, then press `Refresh comparison`.

Comparison supports:

- imported session versus imported session
- live recorded data versus imported data while recording
- speed, RPM, throttle, brake, gear, clutch, and steering when available

The automatic X-axis chooses lap distance when all selected sessions provide it, lap time when available, and elapsed time otherwise. If selected sessions have conflicting track metadata, comparison is blocked unless `Allow track mismatch` is checked.

For speed comparisons, the graph also adds a simple speed-delta series between the first two selected sessions.

## Recording A Live Session

Recording is separate from viewing telemetry:

1. Start a telemetry source.
2. Press `Start recording`.
3. Drive or run demo telemetry.
4. Press `Stop recording`.
5. Use `Save recorded session...` to save JSON.
6. Import or compare the saved session later.

Recorded sessions use the same canonical sample/session model as imported files.

## Settings

Open `Settings > Application settings...` to configure:

- default F1 2018 UDP port
- data, import, and export directories
- graph refresh rate
- maximum live graph history
- restore previous layout at startup
- confirm before removing sessions
- optional fullscreen startup
- reset settings

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
