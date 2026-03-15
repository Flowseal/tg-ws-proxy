# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for macOS — builds a .app bundle
# Usage: pyinstaller macos.spec
#
# Before building, generate icon.icns:
#   iconutil -c icns TgWsProxy.iconset -o icon.icns
# (iconutil is built into macOS, no install needed)

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ['macos.py'],
    pathex=[str(Path('proxy').resolve())],
    binaries=[],
    # Bundle icon_tray.png so it's available next to the exe at runtime
    datas=[
        ('icon_tray.png', '.'),
        ('updater.py', '.'),
    ],
    hiddenimports=[
        'proxy.tg_ws_proxy',
        'updater',
        'cryptography',
        'cryptography.hazmat.primitives.ciphers',
        'cryptography.hazmat.primitives.ciphers.algorithms',
        'cryptography.hazmat.primitives.ciphers.modes',
        'rumps',
        'psutil',
        'asyncio',
        'ssl',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter'],
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
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=True,
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
    icon='icon.icns',   # Finder/Dock icon — generate with iconutil (see top comment)
    bundle_identifier='com.tgwsproxy.app',
    info_plist={
        'NSPrincipalClass': 'NSApplication',
        'NSAppleScriptEnabled': False,
        'LSUIElement': True,
        'CFBundleShortVersionString': '1.1.0',
        'NSHighResolutionCapable': True,
    },
)
