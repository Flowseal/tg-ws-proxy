# -*- mode: python ; coding: utf-8 -*-

import sys
import os

block_cipher = None

a = Analysis(
    [os.path.join(os.path.dirname(SPEC), os.pardir, 'macos.py')],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'rumps',
        'objc',
        'Foundation',
        'AppKit',
        'PyObjCTools',
        'PyObjCTools.AppHelper',
        'cryptography.hazmat.primitives.ciphers',
        'cryptography.hazmat.primitives.ciphers.algorithms',
        'cryptography.hazmat.primitives.ciphers.modes',
        'cryptography.hazmat.backends.openssl',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

icon_path = os.path.join(os.path.dirname(SPEC), os.pardir, 'icon.icns')
if not os.path.exists(icon_path):
    icon_path = None

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
    upx=False,
    console=False,
    argv_emulation=False,
    target_arch='universal2',
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='TgWsProxy',
)

app = BUNDLE(
    coll,
    name='TG WS Proxy.app',
    icon=icon_path,
    bundle_identifier='com.tgwsproxy.app',
    info_plist={
        'CFBundleName': 'TG WS Proxy',
        'CFBundleDisplayName': 'TG WS Proxy',
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion': '1.0.0',
        'LSMinimumSystemVersion': '10.15',
        'LSUIElement': True,
        'NSHighResolutionCapable': True,
        'NSAppleEventsUsageDescription':
            'TG WS Proxy needs to display dialogs.',
    },
)
