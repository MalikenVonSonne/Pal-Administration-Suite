# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve().parent
PALSAV_PATH = os.environ.get("PALADMIN_PALSAV", "")

datas = [
    (str(ROOT / 'data' / 'catalogs'), 'data/catalogs'),
    (str(ROOT / 'tools' / 'asset-extraction' / 'PalCalc' / 'PalCalc.UI' / 'Resources' / 'Pals'), 'data/portraits'),
    (str(ROOT / 'data' / 'portraits' / 'ATTRIBUTION.txt'), 'data/portraits'),
    (str(ROOT / 'tools' / 'asset-extraction' / 'PalCalc' / 'LICENSE.txt'), 'data/portraits'),
    (str(ROOT / 'LICENSE'), 'licenses'),
    (str(ROOT / 'licenses' / 'palsav-flex-GPL-3.0-or-later.txt'), 'licenses'),
    (str(ROOT / 'THIRD_PARTY_NOTICES.md'), 'licenses'),
]
binaries = []
hiddenimports = ['pal_editor.gui', 'palooz']
tmp_ret = collect_all('palsav')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ooz = collect_all('palooz')
datas += tmp_ooz[0]; binaries += tmp_ooz[1]; hiddenimports += tmp_ooz[2]


a = Analysis(
    [str(ROOT / 'project' / 'editor' / 'launch_paladmin.py')],
    pathex=[str(ROOT / 'project' / 'editor')] + ([PALSAV_PATH] if PALSAV_PATH else []),
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
    [],
    exclude_binaries=True,
    name='PalAdmin',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='PalAdmin',
)
