#!/usr/bin/env python3
# =============================================================================
# test_offline_demo_wifi.py — WiFi 傳輸的離線端到端 demo(無需實體 RIDER)
# =============================================================================
# 與 test_offline_demo.py 同構,差別只在「傳輸技術」:OpenDroneID 可經由
# 序列、藍牙、或 *WiFi*(Beacon / NAN)被接收。RIDER 把接收 metadata(MAC、
# RSSI、技術別、頻道、頻段)連同 OpenDroneID payload 一起包進 DriMessage 的
# odid_payload.transmission_info(oneof)。
#
# 這支 demo 合成兩個 WiFi 接收場景:
#   1) WiFi Beacon (tech="WB") — 2.4GHz, channel 6
#   2) WiFi NAN    (tech="WN") — 5GHz
# 各自包成完整 DriMessage,走 odid_slip_reader 真實的解碼鏈,印出結果。
#
# 為避免重複樣板,SLIP / varint / handler 餵入等共用邏輯直接從
# test_offline_demo.py 匯入。
#
# 用法:
#   .\venv\Scripts\python.exe test_offline_demo_wifi.py
# =============================================================================

import asyncio

from dtproto_receiver import dri_message_pb2 as pb
from dtproto_receiver import odid_payload_pb2 as opb

# 重用既有 demo 的共用工具與真實 handler
from test_offline_demo import (
    build_odid_location_bytes,   # 合法的 ASTM F3411 Location bytes
    build_serial_frame,          # address + varint + protobuf -> SLIP
)
from odid_slip_reader import SlipSerialReader, dt_odid_pb_handler


def build_dri_message_wifi_beacon(encoded_odid: bytes) -> bytes:
    """帶 WiFi Beacon 接收資訊的 DriMessage(tech 會解成 'WB')。"""
    m = pb.DriMessage()
    m.odid_payload.encoded_message = encoded_odid
    m.odid_payload.counter = 7
    m.odid_payload.wifi_beacon_info.mac = bytes([0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF])
    m.odid_payload.wifi_beacon_info.rssi = -55
    m.odid_payload.wifi_beacon_info.channel = 6
    m.odid_payload.wifi_beacon_info.frequency = opb.ODID_WIFI_FREQ_2_4_GHZ
    m.receiver_data.receiver_type = pb.ReceiverType.NIC
    m.receiver_data.component_id = 1
    return m.SerializeToString()


def build_dri_message_wifi_nan(encoded_odid: bytes) -> bytes:
    """帶 WiFi NAN 接收資訊的 DriMessage(tech 會解成 'WN')。"""
    m = pb.DriMessage()
    m.odid_payload.encoded_message = encoded_odid
    m.odid_payload.counter = 8
    m.odid_payload.wifi_nan_info.mac = bytes([0x11, 0x22, 0x33, 0x44, 0x55, 0x66])
    m.odid_payload.wifi_nan_info.rssi = -60
    m.odid_payload.wifi_nan_info.frequency = opb.ODID_WIFI_FREQ_5_GHZ
    m.receiver_data.receiver_type = pb.ReceiverType.NIC
    m.receiver_data.component_id = 2
    return m.SerializeToString()


async def feed_and_drain(reader: SlipSerialReader, frame: bytes) -> None:
    """把 frame(可分段)餵進 reader,等待派送的 task 跑完。"""
    mid = len(frame) // 2
    reader.data_received(frame[:mid])
    reader.data_received(frame[mid:])
    await asyncio.sleep(0)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending)


async def main() -> None:
    print("=" * 72)
    print(" WiFi 傳輸離線 demo — OpenDroneID over WiFi Beacon / NAN(無實體 RIDER)")
    print("=" * 72)

    odid_bytes = build_odid_location_bytes()
    print(f"\n共用 OpenDroneID Location ({len(odid_bytes)} bytes): {odid_bytes.hex()}")

    handler_map = {0x2A: [dt_odid_pb_handler]}
    reader = SlipSerialReader(handler_map)

    print("\n[A] ── WiFi Beacon (2.4GHz, ch6) ──")
    frame_b = build_serial_frame(build_dri_message_wifi_beacon(odid_bytes))
    print(f"    SLIP frame ({len(frame_b)} bytes): {frame_b.hex()}")
    await feed_and_drain(reader, frame_b)

    print("\n[B] ── WiFi NAN (5GHz) ──")
    frame_n = build_serial_frame(build_dri_message_wifi_nan(odid_bytes))
    print(f"    SLIP frame ({len(frame_n)} bytes): {frame_n.hex()}")
    await feed_and_drain(reader, frame_n)

    print("\n" + "=" * 72)
    print(" ✅ WiFi 傳輸路徑解碼完成(上方 JSON 的 tech 應為 WB / WN)")
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(main())
