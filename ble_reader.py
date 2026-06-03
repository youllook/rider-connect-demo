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

from bleak import BleakClient, BleakScanner

# 重用既有的解碼鏈 —— 不重造輪子
from odid_slip_reader import ProtobufDelimitedBuffer, dt_dri_pb_handler

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
        is_rider = RIDER_SERVICE_UUID in uuids
        tag = "  <<< RIDER service" if is_rider else ""
        name = dev.name or adv.local_name or "(無名稱)"
        print(f"  {addr}  rssi={adv.rssi:>4}  {name}{tag}")


async def receive(address: str) -> None:
    """連上指定 BLE 位址,訂閱 Notify Characteristic,並解碼收到的 protobuf。"""
    # BLE notification 是 length-prefixed protobuf 流 —— 直接餵進既有緩衝器,
    # 它會去掉 varint 前綴、重組分段訊息,再交給 dt_dri_pb_handler 解碼。
    buffer = ProtobufDelimitedBuffer(dt_dri_pb_handler)

    def on_notify(_characteristic, data: bytearray) -> None:
        # bleak 的 notify callback 可為同步;用 create_task 接到 asyncio 緩衝器
        asyncio.create_task(buffer.feed(bytes(data)))

    print(f"連線到 {address} ...")
    async with BleakClient(address, services=[RIDER_SERVICE_UUID]) as client:
        print(f"已連線。訂閱 Notify Characteristic {RIDER_NOTIFY_CHAR_UUID}")
        await client.start_notify(RIDER_NOTIFY_CHAR_UUID, on_notify)
        print("接收中 — 按 Ctrl+C 停止。\n")
        try:
            await asyncio.Event().wait()   # 持續接收
        finally:
            await client.stop_notify(RIDER_NOTIFY_CHAR_UUID)


async def auto_connect() -> None:
    """沒給位址時:掃描並連上第一個帶 RIDER service UUID 的裝置。"""
    print("未指定位址 — 自動搜尋帶 RIDER service UUID 的裝置...")
    dev = await BleakScanner.find_device_by_filter(
        lambda d, adv: RIDER_SERVICE_UUID in [u.lower() for u in (adv.service_uuids or [])],
        timeout=10.0,
    )
    if dev is None:
        print("找不到 RIDER。請先用 --scan 確認,或用 --address 指定位址。")
        return
    print(f"找到 RIDER: {dev.address}")
    await receive(dev.address)


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
        await auto_connect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
