# -*- mode: python ; coding: utf-8 -*-
#
# LANChat 打包脚本（PyInstaller 6.x）
#
# 构建：  python -m PyInstaller lanchat.spec --noconfirm
# 产物：  dist\lanchat.exe（单文件，首次运行从 7z 直链下载 CS1.6）
#
import os
import sys
import tkinter

# ---------- 手动添加 Tcl 数据目录 ----------
_tcl_dir = None
# 优先从 tkinter 模块位置查找
try:
    _tcl_dir = os.path.join(os.path.dirname(tkinter.__file__), 'tcl')
    if not os.path.isdir(_tcl_dir):
        _tcl_dir = None
except Exception:
    pass
# 备选：从 Python 根目录查找
if not _tcl_dir:
    _tcl_dir = os.path.join(sys.base_prefix, 'tcl')
    if not os.path.isdir(_tcl_dir):
        _tcl_dir = None

_datas = []
if _tcl_dir:
    _datas.append((_tcl_dir, '_tcl_data'))   # 目标路径必须是 _tcl_data
else:
    print("WARNING: Could not locate Tcl data directory. Tkinter may fail at runtime.")

a = Analysis(
    ['lanchat.py'],
    pathex=[SPECPATH],
    binaries=[],
    datas=_datas,                          # 使用动态构建的列表
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'xml', 'xmlrpc',
        'unittest', 'doctest', 'pydoc', 'pdb', 'difflib',
        'decimal', '_decimal',
        'asyncio', 'concurrent', 'multiprocessing', 'sqlite3', '_sqlite3',
        'bz2', '_bz2', 'lzma', '_lzma', 'compression.zstd', '_zstd',
        'tkinter.test', 'test',
    ],
    noarchive=False,
    optimize=2,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='lanchat',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,             # 正式发布时保持 False
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
