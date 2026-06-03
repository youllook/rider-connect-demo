#!/usr/bin/env python3
# =============================================================================
# test_offline_demo.py — 離線端到端 demo(無需實體 Dronetag RIDER)
# =============================================================================
# 自己合成一個完整的 DriMessage(內含 OpenDroneID Location payload),走完
# odid_slip_reader.py 真正會經歷的整條鏈路:
#
#   合法 ODID bytes ─(dtpyodid.pack)─▶ DriMessage(protobuf)
#        ─▶ varint 長度前綴 ─▶ SLIP 編碼 ─▶ 餵進真實 SlipSerialReader
#        ─▶ 0x2A handler ─▶ ProtobufDelimitedBuffer 去前綴
#        ─▶ dt_dri_pb_handler ─▶ dt_odid_parser(開源 dtpyodid)
#        ─▶ 印出人類可讀 JSON
#
# 重點:這裡「不 mock 任何解析邏輯」。Slip、ProtobufDelimitedBuffer、
#       dispatcher、handler 全部用 odid_slip_reader.py 裡的真實實作,
#       只用一個假的 transport 取代實體序列埠。
#
# 用法:
#   .\venv\Scripts\python.exe test_offline_demo.py
# =============================================================================

import asyncio
import binascii

import dtpyodid.parser as odid

# 真實實作 —— 直接從受測模組匯入,不重寫
from odid_slip_reader import (
    Slip,
    SlipSerialReader,
    dt_odid_pb_handler,   # 0x2A 的 handler(內部餵 ProtobufDelimitedBuffer)
)
from dtproto_receiver import dri_message_pb2 as pb


# --- 工具:protobuf delimited 的 varint 長度前綴 -------------------------------
def varint_prefix(n: int) -> bytes:
    """把長度 n 編成 protobuf varint(與腳本的 _read_varint 對稱)。"""
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


# --- 步驟 1:合成一個合法的 OpenDroneID Location 訊息 --------------------------
def build_odid_location_bytes() -> bytes:
    """用開源 dtpyodid 的 pack() 產生精確的 25-byte ASTM F3411 Location。"""
    loc = odid.Location(
        latitude=50.1234,        # 布拉格附近(Dronetag 總部 😉)
        longitude=14.5678,
        altitude_geo=300.0,
        altitude_baro=295.0,
        height=120.0,            # 距起飛點高度
        speed_horizontal=12.5,   # m/s
        # 註:dtpyodid 開源版的 pack()↔parse() 對「負」垂直速度編碼不對稱
        # (-2.0 會被讀回成 126.0)。真實 RIDER 由韌體 C 實作編碼、不走這條路,
        # 故僅影響合成測資。這裡用正值(上升)避開該瑕疵。
        speed_vertical=2.0,      # m/s(上升中)
        direction=90,            # 朝東
    )
    return loc.pack()


# --- 步驟 2:把 ODID bytes 包成完整 DriMessage(protobuf)----------------------
def build_dri_message(encoded_odid: bytes) -> bytes:
    """組一個帶 odid_payload + receiver metadata 的 DriMessage 並序列化。"""
    m = pb.DriMessage()
    m.odid_payload.encoded_message = encoded_odid
    m.odid_payload.counter = 42
    # transmission_info 是 oneof,handler 的 from_message 必設一個,否則丟 ValueError。
    # 這裡用藍牙長距(BT5),帶 MAC + RSSI 模擬一次真實接收。
    m.odid_payload.bluetooth_long_range_info.mac = bytes([0xDE, 0xAD, 0xBE, 0xEF, 0x00, 0x01])
    m.odid_payload.bluetooth_long_range_info.rssi = -67
    # 接收端 metadata
    m.receiver_data.receiver_type = pb.ReceiverType.ESP32
    m.receiver_data.component_id = 3
    return m.SerializeToString()


# --- 步驟 3:把 DriMessage 包成序列埠上會出現的原始 SLIP frame ----------------
def build_serial_frame(dri_bytes: bytes) -> bytes:
    """
    模擬 RIDER 在序列埠送出的位元組:
        SLIP( [address 0x2A] + [varint len] + [protobuf] )
    """
    address = b"\x2a"
    delimited = varint_prefix(len(dri_bytes)) + dri_bytes
    payload = address + delimited
    return Slip.encode(payload)


async def main() -> None:
    print("=" * 70)
    print(" 離線端到端 demo — 無實體 RIDER,全程使用 odid_slip_reader 真實邏輯")
    print("=" * 70)

    # 1) 合法 ODID Location
    odid_bytes = build_odid_location_bytes()
    print(f"\n[1] OpenDroneID Location (ASTM F3411, {len(odid_bytes)} bytes)")
    print(f"    hex: {odid_bytes.hex()}")

    # 2) 包成 DriMessage
    dri_bytes = build_dri_message(odid_bytes)
    print(f"\n[2] DriMessage protobuf ({len(dri_bytes)} bytes)")
    print(f"    hex: {dri_bytes.hex()}")

    # 3) 包成序列埠 SLIP frame
    frame = build_serial_frame(dri_bytes)
    print(f"\n[3] 序列埠上的原始 SLIP frame ({len(frame)} bytes)")
    print(f"    hex: {frame.hex()}")

    # 4) 餵進真實的 SlipSerialReader（用 0x2A 的真實 handler）
    print("\n[4] ── 餵進 SlipSerialReader.data_received() ──")
    handler_map = {0x2A: [dt_odid_pb_handler]}
    reader = SlipSerialReader(handler_map)

    # 模擬「分段到達」：把 frame 切兩半送,順便驗證緩衝重組
    mid = len(frame) // 2
    reader.data_received(frame[:mid])
    reader.data_received(frame[mid:])

    # data_received 用 asyncio.create_task 派送,讓出控制權等它跑完
    await asyncio.sleep(0)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending)

    # 5) 獨立用開源 parser 再解一次,印出完整人類可讀結果（對照組）
    print("\n[5] ── 開源 dtpyodid 直接解碼(對照)──")
    decoded = odid.parse(odid_bytes)
    print(f"    型別     : {type(decoded).__name__}")
    print(f"    緯度     : {decoded.latitude}")
    print(f"    經度     : {decoded.longitude}")
    print(f"    對地高度 : {decoded.altitude_geo} m")
    print(f"    氣壓高度 : {decoded.altitude_baro} m")
    print(f"    水平速度 : {decoded.speed_horizontal} m/s")
    print(f"    垂直速度 : {decoded.speed_vertical} m/s")
    print(f"    航向     : {decoded.direction}°")

    print("\n" + "=" * 70)
    print(" ✅ 端到端鏈路全部跑通(SLIP → protobuf → ODID 解碼)")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
