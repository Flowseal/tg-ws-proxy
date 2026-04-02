# -*- mode: python ; coding: utf-8 -*-

import sys
import os

block_cipher = None

# customtkinter ships JSON themes + assets that must be bundled
import customtkinter
import setuptools

ctk_path = os.path.dirname(customtkinter.__file__)
# pkg_resources (pyi_rth_pkgres) prepends setuptools/_vendor to sys.path and
# imports jaraco.* from there; frozen builds need the tree on disk.
_vendor = os.path.join(os.path.dirname(setuptools.__file__), '_vendor')
_bundle_datas = [(ctk_path, 'customtkinter/')]
if os.path.isdir(_vendor):
    _bundle_datas.append((_vendor, os.path.join('setuptools', '_vendor')))

a = Analysis(
    [os.path.join(os.path.dirname(SPEC), os.pardir, 'windows.py')],
    pathex=[],
    binaries=[],
    datas=_bundle_datas,
    hiddenimports=[
        'pystray._win32',
        'PIL._tkinter_finder',
        'customtkinter',
        'cryptography.hazmat.primitives.ciphers',
        'cryptography.hazmat.primitives.ciphers.algorithms',
        'cryptography.hazmat.primitives.ciphers.modes',
        'cryptography.hazmat.backends.openssl',
        'platformdirs',
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
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path if os.path.exists(icon_path) else None,
)
