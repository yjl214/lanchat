# -*- mode: python ; coding: utf-8 -*-
#
# LANChat 打包脚本（PyInstaller 6.x）
#
# 构建：  python -m PyInstaller lanchat.spec --noconfirm
# 产物：  dist\lanchat.exe（单文件，内置 CS1_6 整个目录）
#
# 体积优化（改实现，不用压缩）：
# - excludes 剔除程序完全用不到的标准库：TLS（ssl/_ssl/hashlib → OpenSSL 约 7MB）、
#   email/urllib/http/xml、decimal、多进程/协程、sqlite3、bz2/lzma/zstd 等扩展模块。
# - optimize=2 让打包进去的字节码去掉文档字符串与断言。
# - 过滤 Tcl/Tk 中聊天程序永远不会加载的测试与 HTTP 组件。
# - CS1_6 内部体积由游戏资源决定，这里只排除崩溃转储（*.mdmp）这类无用文件。
# - 不要开启 UPX：800MB 资源做压缩会让打包和启动都极慢。
a = Analysis(
    ['lanchat.py'],
    pathex=[],
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

# 剔除 Tcl/Tk 的测试套件与 HTTP 包（Tkinter 聊天程序不会加载）
_DROP_FILES = ('tcltest-2.5.8.tm', 'http-2.9.8.tm')
_DROP_DIRS = ('tcl8/8.5/', '_tcl_data/http1.0/')
a.datas = [
    d for d in a.datas
    if not d[0].replace('\\', '/').endswith(_DROP_FILES)
    and not any(p in d[0].replace('\\', '/') for p in _DROP_DIRS)
    and not d[0].lower().endswith('.mdmp')
]

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
