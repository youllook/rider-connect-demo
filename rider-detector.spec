# -*- mode: python ; coding: utf-8 -*-
# =============================================================================
# rider-detector.spec — PyInstaller 打包設定(RIDER Local Detector MVP)
# =============================================================================
# 產出單一資料夾(one-dir)的 rider-detector,內含 rider-detector.exe。
# 選 one-dir 而非 one-file 的理由:
#   - bleak 的 WinRT 後端 + protobuf 系套件有大量動態載入,one-dir 啟動更快、
#     出問題更好查(檔案攤開可見),對「給客戶的 MVP」反而比單檔可靠。
#   - 使用者只要進資料夾、把 .env 放進去、雙擊 exe 即可。
#
# 打包指令(在專案根目錄、venv 下):
#   .\venv\Scripts\pyinstaller.exe rider-detector.spec --noconfirm
#
# 麻煩套件用 collect_all 一網打盡(動態 import / 資料檔抓不到的):
#   bleak(WinRT 後端)、betterproto、grpclib、dtproto_receiver、dtpyodid
# =============================================================================

from PyInstaller.utils.hooks import collect_all

datas = [
    ('dashboard.html', '.'),
    ('rider.avif', '.'),
]
binaries = []
hiddenimports = []

# 這些套件有動態 import / 隨附資料,靠 collect_all 把模組、資料、動態庫全帶上
for pkg in ('bleak', 'betterproto', 'grpclib', 'dtproto_receiver', 'dtpyodid'):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as e:
        print(f"[spec] collect_all({pkg}) 略過: {e}")

# 專案自己的模組(確保都被收進來)
hiddenimports += [
    'config', 'webhook_forwarder', 'ble_reader',
    'odid_slip_reader', 'raw_logger', 'dt_odid_parser', 'dtpyodid_patch',
]

block_cipher = None

a = Analysis(
    ['dashboard_server.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'PIL'],  # 用不到,排除省體積
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
    name='rider-detector',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,        # 保留主控台:看得到設定摘要、燈號訊息、webhook 轉拋 log
    disable_windowed_traceback=False,
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
    name='rider-detector',
)
