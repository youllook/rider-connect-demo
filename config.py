#!/usr/bin/env python3
# =============================================================================
# config.py — 集中設定載入(從 .env 讀,缺值用預設 fallback)
# =============================================================================
# 把原本散在 dashboard_server.py / raw_logger.py 裡寫死的值,全部收斂到這裡,
# 統一從同目錄(或 exe 同層)的 .env 載入。設計原則:
#
#   1) 零第三方依賴 —— 自己解析 .env(KEY=VALUE),不引入 python-dotenv,
#      免得 PyInstaller 打包時多一個相依。
#   2) 全部有預設 fallback —— 沒有 .env、或某欄位沒填,行為與現狀完全一致,
#      不會因為缺設定就壞掉。
#   3) 凍結(PyInstaller)相容 —— .env 找「exe 所在資料夾」而非暫存解壓夾,
#      讓使用者把 .env 放在 exe 旁邊就能改設定,不必重新打包。
#
# 讀取優先序(高 → 低):
#   作業系統環境變數  >  .env 檔  >  程式內建預設
# (因此 CI / 容器可用環境變數覆寫,一般使用者改 .env 即可。)
# =============================================================================

import os
import sys
from pathlib import Path
from typing import List, Optional


def _app_dir() -> Path:
    """回傳「設定檔應該在的資料夾」。
    - 一般 python 執行:本檔所在目錄。
    - PyInstaller 凍結:exe 所在目錄(sys.executable 的資料夾),
      而非 sys._MEIPASS 那個一次性解壓暫存夾 —— 使用者要改的 .env 在 exe 旁邊。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _load_dotenv(path: Path) -> dict:
    """極簡 .env 解析:KEY=VALUE,一行一筆。
    - 略過空行與 # 開頭的整行註解。
    - 去掉 value 兩端引號與空白。
    - 不處理多行值/變數內插(MVP 不需要)。
    解析失敗(檔案讀不到)回空 dict,不拋例外 —— 缺 .env 應走預設。
    """
    data: dict = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return data
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'").strip()
        if key:
            data[key] = val
    return data


# 載入一次(import 時)。OS 環境變數優先,其次 .env。
_ENV_FILE = _app_dir() / ".env"
_DOTENV = _load_dotenv(_ENV_FILE)


def _get(key: str, default: str = "") -> str:
    """取字串設定:OS 環境變數 > .env > default。"""
    if key in os.environ:
        return os.environ[key]
    return _DOTENV.get(key, default)


def _get_int_auto(key: str, default: int) -> int:
    """取整數,自動辨識 0x 十六進位(給 VID/PID 用)。空值/壞值回 default。"""
    raw = _get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw, 0)  # base=0:自動依字面辨識 0x / 0o / 十進位
    except ValueError:
        return default


def _get_float(key: str, default: float) -> float:
    raw = _get(key, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _get_bool(key: str, default: bool) -> bool:
    """1/true/yes/on = True;0/false/no/off = False;其他回 default。"""
    raw = _get(key, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def _get_list(key: str, default: Optional[List[str]] = None) -> List[str]:
    """逗號分隔字串 → list,去空白、去空項。給 webhook 多端點用。"""
    raw = _get(key, "").strip()
    if not raw:
        return list(default or [])
    return [item.strip() for item in raw.split(",") if item.strip()]


# =============================================================================
#  設定值 —— 全部有預設,對齊原本寫死的值
# =============================================================================

# ---- HTTP 儀表板 ----
HOST: str = _get("RIDER_HOST", "127.0.0.1")
PORT: int = _get_int_auto("RIDER_PORT", 8000)
OPEN_BROWSER: bool = _get_bool("RIDER_OPEN_BROWSER", True)

# ---- RIDER USB(序列)----
RIDER_VID: int = _get_int_auto("RIDER_VID", 0x303A)   # Espressif (ESP32) USB-CDC
RIDER_PID: int = _get_int_auto("RIDER_PID", 0x1001)
BAUDRATE: int = _get_int_auto("RIDER_BAUDRATE", 115200)

# ---- RIDER BLE(藍牙)----
RIDER_BLE_ADDR: str = _get("RIDER_BLE_ADDR", "E8:DF:6A:3F:D3:6D")

# ---- 行為 ----
DATA_FRESH_SEC: float = _get_float("RIDER_DATA_FRESH_SEC", 5.0)

# ---- 原始資料落地(raw log;沿用 raw_logger.py 既有的環境變數名)----
RAWLOG_ENABLED: bool = _get_bool("RIDER_RAWLOG", True)
RAWLOG_DIR: str = _get("RIDER_RAWLOG_DIR", "raw_logs")

# ---- Webhook 轉拋 ----
# 多端點:逗號分隔多個 URL。任一端點失敗不影響其他。空清單 = 不轉拋。
WEBHOOK_ENABLED: bool = _get_bool("RIDER_WEBHOOK_ENABLED", False)
WEBHOOK_URLS: List[str] = _get_list("RIDER_WEBHOOK_URLS", [])
WEBHOOK_TIMEOUT: float = _get_float("RIDER_WEBHOOK_TIMEOUT", 5.0)
WEBHOOK_RETRIES: int = _get_int_auto("RIDER_WEBHOOK_RETRIES", 2)
WEBHOOK_LOG_DIR: str = _get("RIDER_WEBHOOK_LOG_DIR", "webhook_logs")
# 只轉「RID」(真無人機 Remote ID),不轉 RIDER 自身遙測(廠商私有半解碼)。
WEBHOOK_RID_ONLY: bool = _get_bool("RIDER_WEBHOOK_RID_ONLY", True)


def effective_webhook_urls() -> List[str]:
    """實際生效的 webhook 端點:必須 enabled 且清單非空。"""
    return WEBHOOK_URLS if (WEBHOOK_ENABLED and WEBHOOK_URLS) else []


def summary() -> str:
    """啟動時印一份目前生效設定(遮蔽冗長),方便使用者確認 .env 有被讀到。"""
    urls = effective_webhook_urls()
    lines = [
        f"  設定檔        : {_ENV_FILE}  ({'已載入' if _DOTENV else '不存在 → 全用預設'})",
        f"  HTTP          : http://{HOST}:{PORT}  (自動開瀏覽器={OPEN_BROWSER})",
        f"  RIDER USB     : VID:PID={RIDER_VID:#06x}:{RIDER_PID:#06x}  baud={BAUDRATE}",
        f"  RIDER BLE     : {RIDER_BLE_ADDR}",
        f"  raw log       : {'開' if RAWLOG_ENABLED else '關'} -> {RAWLOG_DIR}",
        f"  webhook       : {'開' if WEBHOOK_ENABLED else '關'}"
        + (f"  {len(urls)} 個端點 -> {WEBHOOK_LOG_DIR}" if urls else "  (無端點)"),
    ]
    return "\n".join(lines)
