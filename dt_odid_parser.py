import base64
from enum import Enum, IntEnum
from dataclasses import dataclass
from typing import Optional
from dtproto_receiver import dri_message_pb2, DriMessage

try:
    from pyopendroneid.helper import parseOpenDroneID, datify_opendroneid_dict
    _has_pyopendroneid = True
except ImportError:
    _has_pyopendroneid = False

try:
    import dtpyodid.parser as dtparser
    _has_dtpyodid = True
    # 修正 dtpyodid 垂直速度 signed-decode bug(見 KNOWN_ISSUES.md / dtpyodid_patch.py)。
    # 沒有此 patch 時,任何「下降」會被解成荒謬正大數(緩降 -2 m/s → +126 m/s)。
    # patch 為幂等;若模組不存在則靜默略過,不影響解碼其餘部分。
    try:
        import dtpyodid_patch  # import 即自動套用
    except Exception:
        pass
except ImportError:
    _has_dtpyodid = False


class Tech(Enum):
    BT4 = "B4"
    BT5 = "B5"
    WIFI_NAN = "WN"
    WIFI_BEACON = "WB"


class OdidType(IntEnum):
    # ASTM F3411 message type values
    BASIC_ID = 0
    LOCATION = 1
    AUTH = 2
    SELF_ID = 3
    SYSTEM = 4
    OPERATOR_ID = 5
    PACKED = 0xF
    INVALID = 0xFF  # sentinel — not a protocol value

    @classmethod
    def get_type(cls, type_id: int) -> "OdidType":
        try:
            return OdidType((type_id & 0xF0) >> 4)
        except Exception:
            return OdidType.INVALID


@dataclass
class DriReaderInfo:
    mac: bytes
    tech: Tech
    odid_type: OdidType
    rssi: int
    counter: int
    recv_id: int = 0

    @classmethod
    def from_message(cls, dri_message: dri_message_pb2.DriMessage) -> Optional["DriReaderInfo"]:
        recv_id = dri_message.receiver_data.component_id << 4 | dri_message.receiver_data.receiver_type

        if not dri_message.HasField("odid_payload"):
            return None

        odid_type = OdidType.get_type(dri_message.odid_payload.encoded_message[0])
        counter = dri_message.odid_payload.counter
        which = dri_message.odid_payload.WhichOneof("transmission_info")

        if which == "wifi_beacon_info":
            return DriReaderInfo(recv_id=recv_id, odid_type=odid_type, counter=counter,
                                 mac=dri_message.odid_payload.wifi_beacon_info.mac,
                                 rssi=dri_message.odid_payload.wifi_beacon_info.rssi,
                                 tech=Tech.WIFI_BEACON)
        elif which == "wifi_nan_info":
            return DriReaderInfo(recv_id=recv_id, odid_type=odid_type, counter=counter,
                                 mac=dri_message.odid_payload.wifi_nan_info.mac,
                                 rssi=dri_message.odid_payload.wifi_nan_info.rssi,
                                 tech=Tech.WIFI_NAN)
        elif which == "bluetooth_legacy_info":
            return DriReaderInfo(recv_id=recv_id, odid_type=odid_type, counter=counter,
                                 mac=dri_message.odid_payload.bluetooth_legacy_info.mac,
                                 rssi=dri_message.odid_payload.bluetooth_legacy_info.rssi,
                                 tech=Tech.BT4)
        elif which == "bluetooth_long_range_info":
            return DriReaderInfo(recv_id=recv_id, odid_type=odid_type, counter=counter,
                                 mac=dri_message.odid_payload.bluetooth_long_range_info.mac,
                                 rssi=dri_message.odid_payload.bluetooth_long_range_info.rssi,
                                 tech=Tech.BT5)
        else:
            raise ValueError(
                "DRI transmission_info none of "
                "wifi_beacon_info, wifi_nan_info, bluetooth_legacy_info, bluetooth_long_range_info"
            )


def dt_odid_parser(dri_message: dri_message_pb2.DriMessage):
    encoded = dri_message.odid_payload.encoded_message

    if _has_pyopendroneid:
        _, odid_parsed = parseOpenDroneID(encoded)
        if odid_parsed:
            odid_parsed = datify_opendroneid_dict(odid_parsed)
    elif _has_dtpyodid:
        odid_parsed = dtparser.parse(encoded)
    else:
        odid_parsed = None

    dri_info = DriReaderInfo.from_message(dri_message)
    if dri_info is None:
        print("Could not extract the info")
        return None

    return {
        "mac": dri_info.mac.hex(":"),
        "counter": dri_info.counter,
        "rssi": dri_info.rssi,
        "tech": dri_info.tech.value,
        "recv_id": dri_info.recv_id,
        "msg_type": dri_info.odid_type.value,
        "odid": odid_parsed,
        "odid_raw": base64.b64encode(encoded),
    }
