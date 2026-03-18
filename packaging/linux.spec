# -*- mode: python ; coding: utf-8 -*-

import sys
import os
import glob

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

# customtkinter ships JSON themes + assets that must be bundled
import customtkinter
ctk_path = os.path.dirname(customtkinter.__file__)

# Collect gi (PyGObject) submodules and data so pystray._appindicator works
gi_hiddenimports = collect_submodules('gi')
gi_datas = collect_data_files('gi')

# Collect GObject typelib files from the system
typelib_dirs = glob.glob('/usr/lib/*/girepository-1.0')
typelib_datas = []
for d in typelib_dirs:
    typelib_datas.append((d, 'gi_typelibs'))

a = Analysis(
    [os.path.join(os.path.dirname(SPEC), os.pardir, 'linux.py')],
    pathex=[],
    binaries=[],
    datas=[(ctk_path, 'customtkinter/')] + gi_datas + typelib_datas,
    hiddenimports=[
        'pystray._appindicator',
        'PIL._tkinter_finder',
        'customtkinter',
        'cryptography.hazmat.primitives.ciphers',
        'cryptography.hazmat.primitives.ciphers.algorithms',
        'cryptography.hazmat.primitives.ciphers.modes',
        'cryptography.hazmat.backends.openssl',
        'gi',
        '_gi',
        'gi.repository.GLib',
        'gi.repository.GObject',
        'gi.repository.Gtk',
        'gi.repository.Gdk',
        'gi.repository.AyatanaAppIndicator3',
    ] + gi_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

icon_path = os.path.join(os.path.dirname(SPEC), os.pardir, 'icon.ico')
if os.path.exists(icon_path):
    a.datas += [('icon.ico', icon_path, 'DATA')]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='TgWsProxy',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
