# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for macOS — builds a .app bundle
# Usage: pyinstaller macos.spec

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ['macos.py'],
    pathex=[str(Path('proxy').resolve())],
    binaries=[],
    datas=[],
    hiddenimports=[
        'proxy.tg_ws_proxy',
        'cryptography',
        'cryptography.hazmat.primitives.ciphers',
        'cryptography.hazmat.primitives.ciphers.algorithms',
        'cryptography.hazmat.primitives.ciphers.modes',
        'rumps',
        'psutil',
        'tkinter',
        'tkinter.ttk',
        'tkinter.messagebox',
        'asyncio',
        'ssl',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='TgWsProxy',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # No terminal window
    disable_windowed_traceback=False,
    argv_emulation=True,    # Required for macOS .app
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='TgWsProxy',
)

app = BUNDLE(
    coll,
    name='TgWsProxy.app',
    # icon='icon.icns',   # Uncomment if you have an .icns file
    bundle_identifier='com.tgwsproxy.app',
    info_plist={
        'NSPrincipalClass': 'NSApplication',
        'NSAppleScriptEnabled': False,
        'LSUIElement': True,           # Hides from Dock (menu bar only app)
        'CFBundleShortVersionString': '1.1.0',
        'NSHighResolutionCapable': True,
    },
)
