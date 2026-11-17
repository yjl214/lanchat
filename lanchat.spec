# -*- mode: python ; coding: utf-8 -*-
#
# LANChat 打包脚本（PyInstaller 6.x）
#
# 构建：  python -m PyInstaller lanchat.spec --noconfirm
# 产物：  dist\lanchat.exe（单文件，内置 CS1_6 整个目录）
#

a = Analysis(
    ['lanchat.py'],
    pathex=[SPECPATH],                # ← 改为 SPECPATH，更健壮
    binaries=[],
    datas=[('CS1_6', 'CS1_6')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'ssl', '_ssl', 'hashlib', '_hashlib', '_md5', '_sha1', '_sha2', '_sha3', '_blake2',
        'email', 'http', 'urllib', 'xml', 'xmlrpc', 'html',
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

# ★★★ 重要：删除了之前手动过滤 Tcl/Tk 数据的代码 ★★★

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
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)