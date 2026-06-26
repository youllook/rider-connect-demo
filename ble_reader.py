#!/usr/bin/env python3
# =============================================================================
# ble_reader.py — Dronetag RIDER 的 BLE(Bluetooth Low Energy)接收端
# =============================================================================
# 實作 README「Bluetooth Support」章節規劃、但原本只有文件、沒有程式碼的那條
# 接收通道。RIDER 透過一個自訂的 Notify Characteristic 串流訊息;與序列介面
# 不同,BLE 訊息 **不經 SLIP**,而是 raw protobuf,以 varint 長度前綴 framing。
#
# 設計重點:正因為 BLE 不走 SLIP,只是 length-prefixed protobuf 流,因此可以
# 直接重用 odid_slip_reader.py 既有的 ProtobufDelimitedBuffer 與 0x2A handler
# (dt_odid_pb_handler)——把每則 BLE notification 的 bytes 餵進同一個緩衝器
# 即可,解碼路徑與序列埠完全共用(同樣享有開源 dtpyodid 的 ODID 解碼)。
#
# 依賴:bleak(跨平台 BLE;Windows 走 WinRT 後端)。
#
# 用法:
#   # 1) 掃描附近 BLE 裝置,找出 RIDER 的位址(不需先連線)
#   .\venv\Scripts\python.exe ble_reader.py --scan
#
#   # 2) 連上指定位址並開始接收 + 解碼
#   .\venv\Scripts\python.exe ble_reader.py --address <ADDR-or-UUID>
#
#   # 3) 不給位址時,自動掃描並連上第一個帶 RIDER service UUID 的裝置
#   .\venv\Scripts\python.exe ble_reader.py
# =============================================================================

import argparse
import asyncio
from typing import Optional

from bleak import BleakClient, BleakScanner

# 重用既有的解碼鏈 —— 不重造輪子
from odid_slip_reader import ProtobufDelimitedBuffer, dt_dri_pb_handler
from raw_logger import RawLogger

# README「Bluetooth UUIDs」章節給定的 GATT UUID
RIDER_SERVICE_UUID = "898aa51c-f6be-4ad5-9398-e43f27cd93fc"
RIDER_NOTIFY_CHAR_UUID = "edb0b8a3-cf30-485c-bd8f-61f8ce998de8"


async def scan(timeout: float = 8.0) -> None:
    """列出附近 BLE 裝置;標出帶 RIDER service UUID 的。"""
    print(f"掃描 BLE 裝置中({timeout:.0f}s)...")
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
    if not devices:
        print("（沒掃到任何裝置 — 確認本機藍牙已開啟）")
        return
    print(f"\n找到 {len(devices)} 個裝置:")
    for addr, (dev, adv) in devices.items():
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        name = dev.name or adv.local_name or "(無名稱)"
        if _is_rider(dev, adv):
            tag = "  <<< RIDER (service UUID)" if RIDER_SERVICE_UUID in uuids else "  <<< RIDER (靠名稱識別)"
        else:
            tag = ""
        print(f"  {addr}  rssi={adv.rssi:>4}  {name}{tag}")


async def _connect_with_retry(address: str, attempts: int = 3, timeout: float = 15.0) -> BleakClient:
    """連線 + 重試。Windows BLE 頭一兩次常見 TimeoutError / E_UNEXPECTED
    (「災難性的失敗」),重試前先 rescan 刷新 WinRT 對該裝置的快取——這是真機
    實測(E8:DF:6A:3F:D3:6D,第 3 次才連上)歸納出的可靠做法。
    不帶 services=[...] filter:RIDER 連上後才暴露 service,連線階段過濾反而易卡握手。"""
    last_err: Optional[Exception] = None
    for i in range(1, attempts + 1):
        try:
            print(f"連線到 {address} ...(第 {i}/{attempts} 次)")
            client = BleakClient(address, timeout=timeout)
            await client.connect()
            return client
        except Exception as e:               # TimeoutError / OSError(E_UNEXPECTED) 等
            last_err = e
            print(f"  連線失敗: {type(e).__name__}: {e}")
            if i < attempts:
                print("  3 秒後 rescan 並重試...")
                await asyncio.sleep(3)
                try:
                    await BleakScanner.find_device_by_address(address, timeout=6.0)
                except Exception:
                    pass
    raise RuntimeError(f"連線 {attempts} 次均失敗") from last_err


async def receive(address: str) -> None:
    """連上指定 BLE 位址,訂閱 Notify Characteristic,並解碼收到的 protobuf。"""
    # BLE notification 是 length-prefixed protobuf 流 —— 直接餵進既有緩衝器,
    # 它會去掉 varint 前綴、重組分段訊息,再交給 dt_dri_pb_handler 解碼。
    buffer = ProtobufDelimitedBuffer(dt_dri_pb_handler)
    raw = RawLogger("ble")   # 每則 notification 的原始 bytes 先落地,再進解析鏈

    def on_notify(_characteristic, data: bytearray) -> None:
        b = bytes(data)
        # 先保留原料:印 hex + 寫 log,確保即使後續解析失敗也留得住
        raw.log(b)
        print(f"[BLE raw #{raw.count}] {len(b)}B {b.hex()}", flush=True)
        # bleak 的 notify callback 可為同步;用 create_task 接到 asyncio 緩衝器
        asyncio.create_task(buffer.feed(b))

    client = await _connect_with_retry(address)
    print(f"已連線。訂閱 Notify Characteristic {RIDER_NOTIFY_CHAR_UUID}")
    await client.start_notify(RIDER_NOTIFY_CHAR_UUID, on_notify)
    print("\n" + "=" * 60, flush=True)
    print("  ✅ BLE 已連線、Notify 已訂閱 —— 通道就緒!", flush=True)
    print("  >>> 現在請打開 RID 來源(無人機 / Beacon / RID app)<<<", flush=True)
    print("  收到的每一則都會即時印在下方並寫進 raw log。", flush=True)
    print("  (按 Ctrl+C 停止)", flush=True)
    print("=" * 60 + "\n", flush=True)
    try:
        await asyncio.Event().wait()   # 持續接收
    finally:
        raw.close()
        try:
            await client.stop_notify(RIDER_NOTIFY_CHAR_UUID)
        finally:
            await client.disconnect()


def _is_rider(d, adv) -> bool:
    """判斷一個廣播是否來自 RIDER。
    真機實測(2026-06-15):RIDER 在廣播階段 **不帶** service UUID(只在連上後才暴露),
    因此單靠 service UUID 過濾永遠掃不到。改以「名稱含 rider」為主、service UUID 為輔,
    兩者任一命中即可。"""
    name = (d.name or adv.local_name or "")
    if "rider" in name.lower():
        return True
    return RIDER_SERVICE_UUID in [u.lower() for u in (adv.service_uuids or [])]


async def auto_connect(scan_timeout: float = 10.0) -> None:
    """沒給位址時:掃描並連上第一個 RIDER(以名稱為主,service UUID 為輔)。
    用 discover() 掃滿整個時間窗再從結果挑 RIDER,而非 find_device_by_filter 的
    邊掃邊停——真機實測:RIDER 廣播間歇且訊號偏弱(rssi ~-86),邊掃邊停常在時間窗
    內錯過它的廣播包而誤判「找不到」;掃滿再挑明顯可靠得多。"""
    print("未指定位址 — 自動搜尋 RIDER(名稱比對,廣播多半不帶 service UUID)...")
    devices = await BleakScanner.discover(timeout=scan_timeout, return_adv=True)
    riders = [(addr, dev, adv) for addr, (dev, adv) in devices.items() if _is_rider(dev, adv)]
    if not riders:
        print("找不到 RIDER。請先用 --scan 確認,或用 --address 指定位址。")
        return
    # 多台時挑訊號最強的
    addr, dev, adv = max(riders, key=lambda t: t[2].rssi)
    print(f"找到 RIDER: {addr}（{dev.name or adv.local_name or '名稱不明'}, rssi={adv.rssi}）")
    await receive(addr)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dronetag RIDER BLE 接收端(raw protobuf, 非 SLIP)"
    )
    parser.add_argument("--scan", action="store_true",
                        help="只掃描列出附近 BLE 裝置後結束")
    parser.add_argument("--address", type=str, default=None,
                        help="要連線的 BLE 位址 / UUID(Windows 上常為 UUID 形式)")
    parser.add_argument("--scan-timeout", type=float, default=8.0,
                        help="掃描秒數(預設 8)")
    args = parser.parse_args()

    if args.scan:
        await scan(args.scan_timeout)
    elif args.address:
        await receive(args.address)
    else:
        await auto_connect(args.scan_timeout)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
