# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


ROOT = Path(SPECPATH)

datas = []
resources_dir = ROOT / "resources"
if resources_dir.exists():
    datas.append((str(resources_dir), "resources"))
binaries = []

hiddenimports = []
for package in ("app", "telemetry", "ui", "storage", "collectors", "parsers"):
    hiddenimports += collect_submodules(package)
hiddenimports += [
    "numpy",
    "pyqtgraph",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "telemetry.assetto_corsa",
    "telemetry.assetto_corsa_competizione",
    "telemetry.demo",
    "telemetry.f1_2018",
]

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(ROOT / "runtime_hooks" / "pyi_rth_qt_paths.py")],
    excludes=["pytest", "unittest", "tests"],
    noarchive=False,
)

pyz = PYZ(a.pure)

CONFIGURATION = os.environ.get("RT_BUILD_CONFIGURATION", "Release").lower()
IS_DEBUG = CONFIGURATION == "debug"
TARGET_NAME = "RacingTelemetry-debug" if IS_DEBUG else "RacingTelemetry"

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=TARGET_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=IS_DEBUG,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name=TARGET_NAME,
)
