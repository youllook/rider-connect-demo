#!/usr/bin/env python3
# =============================================================================
# dashboard_server.py — RIDER 即時視覺儀表板(深色)後端
# =============================================================================
# 顯示 Dronetag RIDER 的連線方式(USB 序列 / BLE 藍牙)與狀態燈號(灰/綠/紅),
# 並把 RIDER 送來的原始 bytes(hex)即時推到瀏覽器滾動顯示。
#
# 架構(刻意只用標準庫 + 已裝套件,不引入 web 框架):
#   - 執行緒 A:ThreadingHTTPServer(標準庫)服務靜態頁 + SSE(/events)。
#   - 執行緒 B:一個 asyncio 事件迴圈,跑兩個狀態機 task:
#       * USB:輪詢 serial.tools.list_ports,認 VID:PID 303A:1001(ESP32),
#              開埠 + 走既有 SLIP 解碼鏈。
#       * BLE:複用 ble_reader 的 _connect_with_retry / _is_rider / Notify UUID,
#              訂閱後把每則 notification 的 raw bytes 推出。
#   - 兩執行緒間用 thread-safe 的 per-client queue.Queue 做 fan-out。
#   - 前端用瀏覽器原生 EventSource 收 SSE(單向推送,不需 websocket)。
#
# 為什麼 SSE 不用 WebSocket:venv 沒有 websockets/aiohttp/flask,資料流又是
# 單向(後端→瀏覽器),SSE 是標準庫就能做的最小可行解。
#
# 燈號判定(USB / BLE 各自一顆):
#   灰 gray  = 未偵測到裝置
#   綠 green = 通道就緒(USB 開埠成功 / BLE 連線+訂閱成功);資料有無放 detail
#   紅 red   = 偵測到裝置但連線失敗(被占用 / 連線重試耗盡 / 開埠例外)
#
# 啟動:  .\venv\Scripts\python.exe dashboard_server.py
#         然後瀏覽器開 http://127.0.0.1:8000 (預設會自動開)
# =============================================================================

import asyncio
import json
import queue
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import serial  # pyserial
import serial.tools.list_ports

from bleak import BleakClient, BleakScanner

# 複用既有程式 —— 不重造輪子
from ble_reader import _connect_with_retry, _is_rider, RIDER_NOTIFY_CHAR_UUID
from odid_slip_reader import (
    ProtobufDelimitedBuffer,
    dt_dri_pb_handler,
    SlipDispatcher,
)
from raw_logger import RawLogger
import config
from webhook_forwarder import WebhookForwarder

# ---- 設定(全部來自 config / .env;預設值對齊原本寫死的值)----
HOST = config.HOST
PORT = config.PORT

def _resource_dir() -> Path:
    """靜態資源(dashboard.html / rider.avif)所在目錄。
    PyInstaller 凍結時,資料檔被解壓到 sys._MEIPASS;一般執行時就是本檔目錄。"""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else Path(__file__).resolve().parent

ROOT = _resource_dir()
HTML_FILE = ROOT / "dashboard.html"
RIDER_AVIF = ROOT / "rider.avif"

RIDER_VID = config.RIDER_VID   # Espressif (ESP32) —— 此台 RIDER 的 USB-CDC
RIDER_PID = config.RIDER_PID
RIDER_BLE_ADDR = config.RIDER_BLE_ADDR   # 實測過、最穩;掃描為輔

DATA_FRESH_SEC = config.DATA_FRESH_SEC   # 最近幾秒內有資料才算「接收中」(僅影響 detail 文字)

# webhook 轉拋器(全域;依 .env 決定是否啟用)。在 main() start()/stop()。
webhook = WebhookForwarder.from_config()


# =============================================================================
#  解碼摘要 —— 把一筆 protobuf payload 解成人類可讀的一行(複用 dt_odid_parser)
# =============================================================================
from dtproto_receiver import DriMessage as _DriMsgBP
import betterproto as _bp

try:
    from dt_odid_parser import dt_odid_parser as _dt_odid_parser
    from dtproto_receiver import dri_message_pb2 as _pb2
    _HAS_PARSER = True
except Exception:
    _HAS_PARSER = False


def _fmt_loc(L):
    """把一個 Location-like 物件/字典整成一句摘要。"""
    def g(k):
        if isinstance(L, dict):
            return L.get(k)
        return getattr(L, k, None)
    lat, lon = g("latitude"), g("longitude")
    parts = []
    if lat is not None and lon is not None:
        parts.append(f"{lat:.6f},{lon:.6f}")
    ag = g("altitude_geo")
    if ag is not None:
        parts.append(f"geo {ag}m")
    sh = g("speed_horizontal"); sv = g("speed_vertical")
    if sh is not None:
        parts.append(f"H{sh}m/s")
    if sv is not None:
        parts.append(f"V{sv}m/s")
    d = g("direction")
    if d is not None:
        parts.append(f"航向{d}°")
    return " ".join(str(p) for p in parts)


def _summarize_one(m, mn):
    """把單一 dtpyodid 訊息(BasicID/Location/System/OperatorID/SelfID/Auth)整成 bits 串。"""
    out = []
    g = lambda k: getattr(m, k, None)
    if mn == "BasicID":
        uid = g("uas_id")
        if uid:
            out.append(f"序號={uid}")
    elif mn == "Location":
        loc = _fmt_loc(m)
        if loc:
            out.append(loc)
    elif mn == "System":
        lat, lon = g("latitude"), g("longitude")
        if lat is not None and lon is not None:
            out.append(f"操作員 {lat:.6f},{lon:.6f}")
        ag = g("altitude_geodetic")
        if ag is not None:
            out.append(f"geo {ag}m")
    elif mn == "OperatorID":
        op = g("operator_id")
        ops = str(op).strip("\x00") if op is not None else ""
        out.append(f"operator_id={ops}" if ops else "operator_id=(空)")
    elif mn == "SelfID":
        desc = g("description") or g("text")
        if desc:
            out.append(f"註記={str(desc).strip(chr(0))}")
    return out


def _read_varint(buf, i):
    """從 buf[i] 讀一個 varint,回傳 (value, 下一個 index)。"""
    result = 0
    shift = 0
    while i < len(buf):
        b = buf[i]
        result |= (b & 0x7F) << shift
        i += 1
        if not (b & 0x80):
            return result, i
        shift += 7
    raise ValueError("varint truncated")


def _raw_protobuf_summary(data: bytes):
    """不需 schema,用 protobuf wire format 通用拆解 —— 給廠商私有格式(RIDER 自身遙測)
    做「半解碼」:列出 field 編號 + 值,並抽出可見 ASCII(序號之類)。失敗回 None。"""
    # 先抽 ASCII 片段(序號、ID 常是字串)
    ascii_runs = []
    cur = b""
    for b in data:
        if 32 <= b < 127:
            cur += bytes([b])
        else:
            if len(cur) >= 4:
                ascii_runs.append(cur.decode("ascii", "ignore"))
            cur = b""
    if len(cur) >= 4:
        ascii_runs.append(cur.decode("ascii", "ignore"))

    # 再拆 top-level protobuf 欄位(只拆第一層,夠用)
    fields = []
    i = 0
    try:
        while i < len(data):
            tag, i = _read_varint(data, i)
            field_no = tag >> 3
            wire = tag & 0x7
            if wire == 0:          # varint
                val, i = _read_varint(data, i)
                fields.append(f"f{field_no}={val}")
            elif wire == 2:        # length-delimited(字串/bytes/嵌套)
                ln, i = _read_varint(data, i)
                chunk = data[i:i + ln]
                i += ln
                # 可印就印,否則標長度
                if chunk and all(32 <= c < 127 for c in chunk):
                    fields.append(f'f{field_no}="{chunk.decode("ascii")}"')
                else:
                    fields.append(f"f{field_no}=<{ln}B>")
            elif wire == 5:        # 32-bit
                i += 4
                fields.append(f"f{field_no}=<32bit>")
            elif wire == 1:        # 64-bit
                i += 8
                fields.append(f"f{field_no}=<64bit>")
            else:
                break              # 未知 wire type,停
    except Exception:
        pass

    if not fields and not ascii_runs:
        return None
    bits = []
    device = " ".join(ascii_runs) if ascii_runs else None
    if ascii_runs:
        bits.append("序號/ID=" + " ".join(ascii_runs))
    if fields:
        bits.append(" ".join(fields[:8]))   # 最多列 8 個欄位免太長
    # category: RIDER = 接收機自身遙測(廠商私有格式)
    return {"category": "RIDER", "kind": "RIDER遙測",
            "text": " · ".join(bits) + " (廠商私有格式)",
            "rssi": None, "tech": None, "device": device}


def decode_summary(payload: bytes):
    """從一筆 protobuf payload 解出人類可讀摘要(dict)。失敗回 None。
    回傳例:{'kind':'Location','text':'序號=... 24.98,121.28 geo 269m ...','rssi':-38,'tech':'B5'}"""
    if not _HAS_PARSER:
        return None
    try:
        dri = _pb2.DriMessage.FromString(payload)
    except Exception:
        return None
    if not dri.HasField("odid_payload"):
        return None
    try:
        info = _dt_odid_parser(dri)
    except Exception:
        return None
    if not info:
        return None
    odid = info.get("odid")
    rssi = info.get("rssi")
    tech = info.get("tech")
    kind = "ODID"
    bits = []

    try:
        # MessagePack:裡面一串 messages,逐一摘要
        msgs = getattr(odid, "messages", None)
        if msgs:
            kind = "MessagePack"
            for m in msgs:
                bits.extend(_summarize_one(m, type(m).__name__))
        else:
            # 單一訊息。dtpyodid 類型名:BasicID/Location/System/OperatorID/SelfID/Auth
            kind = type(odid).__name__ if odid is not None else "?"
            bits = _summarize_one(odid, kind)
    except Exception:
        pass

    text = " · ".join(bits) if bits else f"{kind}"
    # category: RID = RIDER 轉發的(被嗅到的)無人機 Remote ID
    # device:   若解出無人機序號(BasicID),當作此段的 device info
    device = None
    for b in bits:
        if b.startswith("序號="):
            device = b[len("序號="):]
            break
    return {"category": "RID", "kind": kind, "text": text,
            "rssi": rssi, "tech": tech, "device": device}


# =============================================================================
#  Hub —— 集中保存目前狀態 + 對所有 SSE client 廣播事件(thread-safe)
# =============================================================================
class Hub:
    def __init__(self):
        self._clients: list[queue.Queue] = []
        self._lock = threading.Lock()
        # 兩條通道的狀態(state: gray/green/red, detail: 說明字串)
        self.status = {
            "usb": {"state": "gray", "detail": "未偵測", "port": None},
            "ble": {"state": "gray", "detail": "未偵測", "addr": RIDER_BLE_ADDR},
        }

    # --- SSE client 管理 ---
    def add_client(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=2000)
        with self._lock:
            self._clients.append(q)
        return q

    def remove_client(self, q: queue.Queue):
        with self._lock:
            if q in self._clients:
                self._clients.remove(q)

    def _broadcast(self, event: str, data: dict):
        payload = (event, data)
        with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(payload)
            except queue.Full:
                # client 太慢、塞爆 → 丟最舊一筆再塞(raw 可容忍掉幀)
                try:
                    q.get_nowait()
                    q.put_nowait(payload)
                except queue.Empty:
                    pass

    # --- 對外:推 raw / 推狀態 ---
    def push_raw(self, source: str, data: bytes):
        # 時間只取時分秒.毫秒,對齊 raw log 觀感
        ts = time.strftime("%H:%M:%S", time.localtime()) + f".{int(time.time() * 1000) % 1000:03d}"
        self._broadcast("raw", {
            "ts": ts,
            "src": source,
            "len": len(data),
            "hex": data.hex(),
        })

    def push_decoded(self, source: str, summary: dict):
        if not summary:
            return
        self._broadcast("decoded", {"src": source, **summary})
        # 同步轉拋到 webhook 端點(非阻塞;rid_only 過濾在 forwarder 內處理)
        webhook.enqueue(source, summary)

    def set_status(self, channel: str, state: str, detail: str, **extra):
        cur = self.status.get(channel, {})
        cur.update({"state": state, "detail": detail})
        cur.update(extra)
        self.status[channel] = cur
        self._broadcast("status", self.status)

    def snapshot(self) -> dict:
        return self.status


hub = Hub()


# =============================================================================
#  USB(序列)狀態機 —— 在 asyncio 迴圈裡跑
# =============================================================================
def _find_rider_com():
    """回傳 RIDER 的 COM 埠名(VID:PID 303A:1001),沒有則 None。"""
    for p in serial.tools.list_ports.comports():
        if p.vid == RIDER_VID and p.pid == RIDER_PID:
            return p.device
    return None


async def usb_state_machine():
    """偵測 RIDER USB 序列埠 → 開埠 → 走 SLIP 解碼鏈,並維護燈號。"""
    last_data = {"ts": 0.0}

    # 序列 raw 落地(沿用既有 RawLogger)+ 推前端
    raw = RawLogger("serial")

    async def on_decoded(payload: bytes):
        # 0x2A = 標準 ODID:解成人類可讀摘要 → 推前端(附在 raw 底下)
        summary = decode_summary(payload)
        if summary:
            hub.push_decoded("serial", summary)

    async def on_telemetry(payload: bytes):
        # 0x26 = RIDER 自身遙測(廠商私有格式):做半解碼(欄位結構 + ASCII)
        summary = _raw_protobuf_summary(payload)
        if summary:
            hub.push_decoded("serial", summary)

    while True:
        port = _find_rider_com()
        if port is None:
            hub.set_status("usb", "gray", "未偵測到 RIDER USB", port=None)
            await asyncio.sleep(2)
            continue

        # 偵測到 → 嘗試開埠
        hub.set_status("usb", "green", f"已開埠 {port},等待資料", port=port)
        dispatcher = SlipDispatcher()
        # 包一層:每段序列 raw 先落地 + 推前端,再進既有解碼
        buffer = ProtobufDelimitedBuffer(on_decoded)

        class _Tap:
            """模擬 RawLogger 介面但同時推前端;交給 SlipSerialReader 當 raw_logger。"""
            count = 0
            def log(self, data: bytes):
                if not data:
                    return
                self.count += 1
                raw.log(data)
                last_data["ts"] = time.time()
                hub.push_raw("serial", data)

        dispatcher.register_handler(0x2A, lambda p: buffer.feed(p))
        dispatcher.register_handler(0x26, on_telemetry)  # RIDER 自身遙測(0x26)半解碼
        tap = _Tap()
        try:
            transport, _ = await dispatcher.start(port, baudrate=115200, raw_logger=tap)
        except Exception as e:
            hub.set_status("usb", "red", f"開埠失敗: {type(e).__name__}", port=port)
            await asyncio.sleep(3)
            continue

        # 送 init 啟動 0x2A 通道(SLIP 編碼 2A0A0A)
        try:
            from odid_slip_reader import Slip
            transport.write(Slip.encode(bytes.fromhex("2A0A0A")))
        except Exception:
            pass

        # 開著 + 心跳更新狀態,直到埠消失(拔線)
        try:
            while True:
                await asyncio.sleep(1)
                if _find_rider_com() != port:
                    raise RuntimeError("port gone")  # 拔線 → 跳出重偵測
                fresh = (time.time() - last_data["ts"]) < DATA_FRESH_SEC
                hub.set_status(
                    "usb", "green",
                    "接收中" if fresh else f"已開埠 {port},等待資料",
                    port=port,
                )
        except Exception:
            try:
                transport.close()
            except Exception:
                pass
            hub.set_status("usb", "gray", "USB 已移除", port=None)
            await asyncio.sleep(1)


# =============================================================================
#  BLE(藍牙)狀態機 —— 複用 ble_reader 的連線重試 / RIDER 辨識
# =============================================================================
async def ble_state_machine():
    last_data = {"ts": 0.0}
    raw = RawLogger("ble")

    async def on_decoded(payload: bytes):
        # 每筆 delimited protobuf:解碼成人類可讀摘要 → 推前端(附在 raw 底下)
        summary = decode_summary(payload)
        if summary:
            hub.push_decoded("ble", summary)

    buffer = ProtobufDelimitedBuffer(on_decoded)

    def on_notify(_char, data: bytearray):
        b = bytes(data)
        raw.log(b)
        last_data["ts"] = time.time()
        hub.push_raw("ble", b)
        asyncio.create_task(buffer.feed(b))

    while True:
        # 1) 先確認 RIDER 是否在(掃描;掃不到固定位址也試)
        hub.set_status("ble", "gray", "搜尋 RIDER 中...")
        found = False
        try:
            devs = await BleakScanner.discover(timeout=6.0, return_adv=True)
            for addr, (d, adv) in devs.items():
                if _is_rider(d, adv) or addr.upper() == RIDER_BLE_ADDR.upper():
                    found = True
                    break
        except Exception as e:
            hub.set_status("ble", "red", f"掃描失敗: {type(e).__name__}")
            await asyncio.sleep(3)
            continue

        if not found:
            hub.set_status("ble", "gray", "未偵測到 RIDER")
            await asyncio.sleep(3)
            continue

        # 2) 偵測到 → 連線(重試);失敗 = 紅
        hub.set_status("ble", "red", "偵測到 RIDER,連線中...")
        try:
            client = await _connect_with_retry(RIDER_BLE_ADDR, attempts=3, timeout=15.0)
        except Exception as e:
            hub.set_status("ble", "red", f"連線失敗: {type(e).__name__}")
            await asyncio.sleep(3)
            continue

        # 3) 連上 → 訂閱 = 綠
        try:
            await client.start_notify(RIDER_NOTIFY_CHAR_UUID, on_notify)
            hub.set_status("ble", "green", "已連線,等待 RID 來源")
        except Exception as e:
            hub.set_status("ble", "red", f"訂閱失敗: {type(e).__name__}")
            try:
                await client.disconnect()
            except Exception:
                pass
            await asyncio.sleep(3)
            continue

        # 4) 維持連線 + 心跳;斷線就重來
        try:
            while client.is_connected:
                await asyncio.sleep(1)
                fresh = (time.time() - last_data["ts"]) < DATA_FRESH_SEC
                hub.set_status("ble", "green", "接收中" if fresh else "已連線,等待 RID 來源")
        except Exception:
            pass
        finally:
            try:
                await client.stop_notify(RIDER_NOTIFY_CHAR_UUID)
            except Exception:
                pass
            try:
                await client.disconnect()
            except Exception:
                pass
        hub.set_status("ble", "gray", "BLE 已斷線,重新搜尋")
        await asyncio.sleep(1)


def start_async_loop():
    """在獨立執行緒跑 asyncio 事件迴圈(BLE + USB 狀態機)。"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def supervisor():
        await asyncio.gather(
            usb_state_machine(),
            ble_state_machine(),
        )

    try:
        loop.run_until_complete(supervisor())
    except Exception as e:
        print(f"[async loop] 結束: {e}")


# =============================================================================
#  HTTP / SSE
# =============================================================================
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # 安靜,不洗版

    def _send_bytes(self, body: bytes, content_type: str, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            try:
                self._send_bytes(HTML_FILE.read_bytes(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send_bytes(b"dashboard.html not found", "text/plain", 404)
            return

        if self.path == "/rider.avif":
            if RIDER_AVIF.exists():
                self._send_bytes(RIDER_AVIF.read_bytes(), "image/avif")
            else:
                self._send_bytes(b"rider.avif not found", "text/plain", 404)
            return

        if self.path == "/api/status":
            self._send_bytes(json.dumps(hub.snapshot()).encode("utf-8"),
                             "application/json")
            return

        if self.path == "/events":
            self._serve_sse()
            return

        self._send_bytes(b"not found", "text/plain", 404)

    def _serve_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = hub.add_client()
        # 一連上先補一筆目前狀態快照
        try:
            self._write_sse("status", hub.snapshot())
            while True:
                try:
                    event, data = q.get(timeout=15)
                    self._write_sse(event, data)
                except queue.Empty:
                    # 心跳註解,維持連線不被 proxy 砍
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            hub.remove_client(q)

    def _write_sse(self, event: str, data: dict):
        msg = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        self.wfile.write(msg.encode("utf-8"))
        self.wfile.flush()


def main():
    # 啟動先印一份生效設定,讓使用者確認 .env 有被讀到
    print("=" * 60)
    print("  RIDER Local Detector — 設定")
    print(config.summary())
    print("=" * 60)

    if not HTML_FILE.exists():
        print(f"[警告] 找不到 {HTML_FILE},前端頁面無法服務。")

    # 啟動 webhook 轉拋器(若 .env 沒開或沒端點,內部會略過)
    webhook.start()

    # 啟動 asyncio 狀態機執行緒
    t = threading.Thread(target=start_async_loop, daemon=True)
    t.start()

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"  RIDER 即時儀表板已啟動 → {url}")
    print("  (USB 插上 / 藍牙開啟後,對應燈號會轉綠並開始顯示 raw)")
    print("  按 Ctrl+C 停止")
    print("=" * 60)
    if config.OPEN_BROWSER:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n停止中...")
        server.shutdown()
    finally:
        webhook.stop()


if __name__ == "__main__":
    main()
