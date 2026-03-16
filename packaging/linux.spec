# -*- mode: python ; coding: utf-8 -*-
# Сборка TG WS Proxy для Linux (Debian/Ubuntu)
# Требует на целевой системе: libappindicator3-1, gir1.2-appindicator3-0.1, libgtk-3-0

import sys
import os

SPEC = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SPEC)

block_cipher = None

import customtkinter
ctk_path = os.path.dirname(customtkinter.__file__)

a = Analysis(
    [os.path.join(ROOT, 'linux.py')],
    pathex=[ROOT],
    binaries=[],
    datas=[
        (ctk_path, 'customtkinter/'),
        (os.path.join(ROOT, 'proxy'), 'proxy'),
    ],
    hiddenimports=[
        'pystray._appindicator',
        'pystray._gtk',
        'pystray._util.gtk',
        'PIL._tkinter_finder',
        'customtkinter',
        'cryptography.hazmat.primitives.ciphers',
        'cryptography.hazmat.primitives.ciphers.algorithms',
        'cryptography.hazmat.primitives.ciphers.modes',
        'cryptography.hazmat.backends.openssl',
        'gi',
        'gi.repository.Gtk',
        'gi.repository.GLib',
        'gi.repository.GObject',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='tg-ws-proxy',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
)
