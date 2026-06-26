#!/usr/bin/env python3
# =============================================================================
# raw_logger.py — RIDER 原始 bytes 落地器(序列 / BLE 共用)
# =============================================================================
# 把每一筆「還沒經過任何解析」的原始 bytes append 到一個時間戳記 log 檔,
# 萬一 protobuf / ODID 解析失敗,原料仍在,可事後重現與離線重解。
#
# 兩條接收通道共用同一個工具:
#   - odid_slip_reader.py (序列): 在 data_received 收到的 SLIP 原始流
#   - ble_reader.py (BLE):        每則 notification 的 raw bytes
#
# log 行格式(每行一筆,易 grep / 易事後解析):
#   <ISO8601 時間> <來源> <位元組數>B <hex>
# 例:
#   2026-06-15T14:03:22.481 BLE 37B 1232082a32130a06...
#
# 預設寫到 ./raw_logs/raw_<來源>_<啟動時間>.log;可用環境變數覆寫:
#   RIDER_RAWLOG_DIR=...   指定輸出資料夾
#   RIDER_RAWLOG=0         關閉落地(只靠畫面)
# =============================================================================

import os
from datetime import datetime
from pathlib import Path
from typing import Optional


def _now_iso() -> str:
    # 本地時間即可,毫秒精度方便比對封包間隔
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S.") + f"{datetime.now().microsecond // 1000:03d}"


class RawLogger:
    """把原始 bytes 即時 append 到 log 檔。行緩衝 flush,確保中途 Ctrl+C 也留得住。"""

    def __init__(self, source: str, enabled: Optional[bool] = None, log_dir: Optional[str] = None):
        self.source = source
        if enabled is None:
            enabled = os.environ.get("RIDER_RAWLOG", "1") != "0"
        self.enabled = enabled
        self.count = 0
        self.total_bytes = 0
        self._fh = None
        self.path: Optional[Path] = None
        if not self.enabled:
            return
        base = log_dir or os.environ.get("RIDER_RAWLOG_DIR") or "raw_logs"
        d = Path(base)
        d.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = d / f"raw_{source}_{stamp}.log"
        # line-buffered 文字檔(buffering=1)
        self._fh = open(self.path, "a", encoding="utf-8", buffering=1)
        print(f"[raw_logger] 原始資料落地中 -> {self.path}")

    def log(self, data: bytes) -> None:
        """記錄一筆原始 bytes(同時更新計數)。空資料略過。"""
        if not data:
            return
        self.count += 1
        self.total_bytes += len(data)
        if not self.enabled or self._fh is None:
            return
        self._fh.write(f"{_now_iso()} {self.source} {len(data)}B {data.hex()}\n")

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.flush()
                self._fh.close()
            except Exception:
                pass
            self._fh = None
        if self.enabled and self.path is not None:
            print(f"[raw_logger] 共記錄 {self.count} 筆 / {self.total_bytes} bytes -> {self.path}")
