#!/usr/bin/env python3
# =============================================================================
# webhook_forwarder.py — 把解碼後的 RID 轉拋到多個 webhook 端點(+ log)
# =============================================================================
# RIDER 收到並解碼出一筆 RID 後,除了推到瀏覽器儀表板,也 POST 一份 JSON 到
# 設定檔(.env 的 RIDER_WEBHOOK_URLS)填的「多個」HTTP 端點 —— 給 HQ / 客戶
# 後端接。這是 MVP「Rider Local Detector + Webhook 端點」的轉拋核心。
#
# 設計重點:
#   1) 不阻塞接收 —— BLE/USB 的 callback 只把事件丟進 queue 就返回;真正的
#      HTTP 送出在獨立的背景 worker 執行緒做,網路再慢也不卡解碼迴圈。
#   2) 多端點各自獨立 —— 對每個 URL 分別送;一個端點逾時/失敗,不影響其他
#      端點,也不影響後續訊息。
#   3) 失敗重試 —— 每個端點送失敗時重試 N 次(指數退避的簡化:固定間隔),
#      仍失敗就記 log 放棄該筆(RID 是高頻串流,丟一筆可容忍,不無限卡死)。
#   4) 全程 log —— 每次嘗試(端點、HTTP 狀態碼、成功/失敗、第幾次)都寫到
#      webhook_logs/webhook_<啟動時間>.log,事後可稽核「到底有沒有送出去」。
#   5) 零第三方依賴 —— 純標準庫 urllib,減少 PyInstaller 打包負擔。
#
# 用法(在 dashboard_server.py 裡):
#   fwd = WebhookForwarder.from_config()   # 依 .env 建立;沒開/沒端點則為「停用」
#   fwd.start()
#   ...
#   fwd.enqueue(source="ble", summary=decoded_dict)   # 非阻塞,丟進 queue
#   ...
#   fwd.stop()                              # 收尾,等 queue 排空
# =============================================================================

import json
import queue
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import config


def _now_iso() -> str:
    n = datetime.now()
    return n.strftime("%Y-%m-%dT%H:%M:%S.") + f"{n.microsecond // 1000:03d}"


class WebhookForwarder:
    """背景轉拋器:enqueue() 非阻塞收件,worker 執行緒負責對多端點 POST + 重試 + log。"""

    # queue 上限:RID 高頻時若後端塞住,最多積這麼多,超過丟最舊(容忍掉幀)
    _QUEUE_MAX = 1000

    def __init__(
        self,
        urls: List[str],
        timeout: float = 5.0,
        retries: int = 2,
        retry_delay: float = 1.0,
        log_dir: str = "webhook_logs",
        rid_only: bool = True,
    ):
        self.urls = list(urls)
        self.timeout = timeout
        self.retries = max(0, retries)
        self.retry_delay = retry_delay
        self.rid_only = rid_only
        self.enabled = bool(self.urls)

        self._q: "queue.Queue[Optional[dict]]" = queue.Queue(maxsize=self._QUEUE_MAX)
        self._worker: Optional[threading.Thread] = None
        self._stop = threading.Event()

        # 統計(供儀表板/收尾顯示)
        self.sent_ok = 0
        self.sent_fail = 0
        self.dropped = 0

        # log 檔(沿用 raw_logger 的風格:啟動時開、line-buffered)
        self._fh = None
        self.log_path: Optional[Path] = None
        if self.enabled:
            d = Path(log_dir)
            d.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.log_path = d / f"webhook_{stamp}.log"
            self._fh = open(self.log_path, "a", encoding="utf-8", buffering=1)

    # ---- 建構:依 config(.env)決定要不要啟用 ----
    @classmethod
    def from_config(cls) -> "WebhookForwarder":
        return cls(
            urls=config.effective_webhook_urls(),
            timeout=config.WEBHOOK_TIMEOUT,
            retries=config.WEBHOOK_RETRIES,
            log_dir=config.WEBHOOK_LOG_DIR,
            rid_only=config.WEBHOOK_RID_ONLY,
        )

    # ---- log ----
    def _log(self, msg: str) -> None:
        line = f"{_now_iso()} {msg}"
        if self._fh is not None:
            try:
                self._fh.write(line + "\n")
            except Exception:
                pass
        # 同時印到 stdout,方便執行 exe 時直接看到轉拋情形
        print(f"[webhook] {msg}", flush=True)

    # ---- 生命週期 ----
    def start(self) -> None:
        if not self.enabled:
            print("[webhook] 未啟用(無端點或停用)— 不轉拋", flush=True)
            return
        self._log(f"啟動轉拋,共 {len(self.urls)} 個端點:{', '.join(self.urls)}"
                  f"  (timeout={self.timeout}s, retries={self.retries}, rid_only={self.rid_only})")
        self._worker = threading.Thread(target=self._run, name="webhook-fwd", daemon=True)
        self._worker.start()

    def stop(self, drain_timeout: float = 5.0) -> None:
        if not self.enabled:
            return
        # 送一個 sentinel 讓 worker 排空後退出
        try:
            self._q.put_nowait(None)
        except queue.Full:
            pass
        self._stop.set()
        if self._worker is not None:
            self._worker.join(timeout=drain_timeout)
        self._log(f"停止。累計 成功={self.sent_ok} 失敗={self.sent_fail} 丟棄={self.dropped}")
        if self._fh is not None:
            try:
                self._fh.flush()
                self._fh.close()
            except Exception:
                pass
            self._fh = None

    # ---- 收件(接收迴圈呼叫;務必非阻塞)----
    def enqueue(self, source: str, summary: dict) -> None:
        """把一筆解碼摘要排進轉拋佇列。非阻塞:佇列滿就丟最舊一筆(容忍掉幀)。"""
        if not self.enabled or not summary:
            return
        # 只轉真無人機 RID,跳過 RIDER 自身遙測(category != 'RID')
        if self.rid_only and summary.get("category") != "RID":
            return
        item = {
            "ts": _now_iso(),
            "source": source,          # 'ble' / 'serial'
            "category": summary.get("category"),
            "kind": summary.get("kind"),
            "text": summary.get("text"),
            "rssi": summary.get("rssi"),
            "tech": summary.get("tech"),
            "device": summary.get("device"),
        }
        try:
            self._q.put_nowait(item)
        except queue.Full:
            # 佇列爆 → 丟最舊,塞新的(後端塞住時優先保最新)
            try:
                self._q.get_nowait()
                self._q.put_nowait(item)
                self.dropped += 1
            except queue.Empty:
                pass

    # ---- worker 主迴圈 ----
    def _run(self) -> None:
        while True:
            try:
                item = self._q.get(timeout=0.5)
            except queue.Empty:
                if self._stop.is_set():
                    break
                continue
            if item is None:   # sentinel
                break
            body = json.dumps(item, ensure_ascii=False).encode("utf-8")
            # 對每個端點各自送(獨立成敗)
            for url in self.urls:
                self._post_with_retry(url, body, item)
        # 收尾:把殘留的排空(盡力)
        self._drain_remaining()

    def _drain_remaining(self) -> None:
        while True:
            try:
                item = self._q.get_nowait()
            except queue.Empty:
                break
            if item is None:
                continue
            body = json.dumps(item, ensure_ascii=False).encode("utf-8")
            for url in self.urls:
                self._post_with_retry(url, body, item)

    def _post_with_retry(self, url: str, body: bytes, item: dict) -> None:
        attempt = 0
        while True:
            attempt += 1
            ok, detail = self._post_once(url, body)
            if ok:
                self.sent_ok += 1
                self._log(f"OK   {url}  [{item.get('kind')}] {detail}"
                          + (f"  (第{attempt}次)" if attempt > 1 else ""))
                return
            # 失敗
            if attempt <= self.retries:
                self._log(f"重試 {url}  第{attempt}次失敗:{detail} → {self.retry_delay}s 後再試")
                time.sleep(self.retry_delay)
                continue
            self.sent_fail += 1
            self._log(f"FAIL {url}  放棄(共試{attempt}次):{detail}")
            return

    def _post_once(self, url: str, body: bytes):
        """單次 POST。回 (ok: bool, detail: str)。"""
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                code = resp.getcode()
                if 200 <= code < 300:
                    return True, f"HTTP {code}"
                return False, f"HTTP {code}"
        except urllib.error.HTTPError as e:
            return False, f"HTTP {e.code}"
        except urllib.error.URLError as e:
            return False, f"URLError: {e.reason}"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"


# 獨立自我測試:起一個本地接收器,送幾筆,看 log + 統計
if __name__ == "__main__":
    import http.server
    import socketserver
    import threading as _t

    received = []

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            received.append(self.rfile.read(n))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

    srv = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    _t.Thread(target=srv.serve_forever, daemon=True).start()

    fwd = WebhookForwarder(
        urls=[f"http://127.0.0.1:{port}/ingest", "http://127.0.0.1:1/dead"],  # 一好一壞
        timeout=2.0, retries=1, retry_delay=0.2, log_dir="webhook_logs",
    )
    fwd.start()
    fwd.enqueue("ble", {"category": "RID", "kind": "Location",
                        "text": "序號=ABC 24.98,121.28", "rssi": -38, "tech": "B5"})
    fwd.enqueue("ble", {"category": "RIDER", "kind": "RIDER遙測", "text": "自身"})  # rid_only 應跳過
    time.sleep(2.0)
    fwd.stop()
    srv.shutdown()
    print(f"\n本地接收器收到 {len(received)} 筆(應為 1):")
    for r in received:
        print("  ", r.decode("utf-8"))
    print(f"統計 → 成功={fwd.sent_ok} 失敗={fwd.sent_fail}  (好端點應成功、壞端點應失敗)")
