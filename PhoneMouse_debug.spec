# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['phone_mouse.py'],
    pathex=[],
    binaries=[],
    datas=[('index.html', '.'), ('assets/phone_mouse.png', 'assets'), ('assets/phone_mouse.ico', 'assets'), ('assets/favicon.ico', 'assets'), ('assets/apple-touch-icon.png', 'assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PhoneMouse_debug',
    icon='assets/phone_mouse.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
