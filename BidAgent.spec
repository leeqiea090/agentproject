# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


project_root = Path(SPECPATH).resolve()

a = Analysis(
    ["desktop_launcher.py"],
    pathex=[str(project_root)],
    binaries=[],
    datas=[(str(project_root / "app" / "static"), "app/static")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BidAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="BidAgent",
)

app = BUNDLE(
    coll,
    name="BidAgent.app",
    icon=None,
    bundle_identifier="com.lq.bidagent",
)
