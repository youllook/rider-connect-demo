#!/usr/bin/env python3
# =============================================================================
# dtpyodid_patch.py — 本地修正 dtpyodid 垂直速度 signed-decode bug
# =============================================================================
# 詳見 KNOWN_ISSUES.md。摘要:
#   開源 dtpyodid 的 Location._parse 以 `data[3]`(無號 0–255)讀取 ASTM F3411
#   裡本為「有號 int8」的 vertical-speed 欄位,導致任何「下降」(負垂直速度)
#   被解成荒謬正大數(約 128 + 真實值)。例:緩降 −2 m/s → +126 m/s。
#
# 本模組以 monkey-patch 在 *執行期* 修正,**不修改 venv 內的庫檔**
#   (那會在下次 pip install 被覆蓋)。只要在用到解碼前 `import dtpyodid_patch`
#   並呼叫 apply()(或直接 import 即自動套用),下降速度就會正確。
#
# 為何 patch 在 Location._parse 這一層:
#   - dtpyodid.parser.parse 與 MessagePack._parse 都經由 Location.parse ->
#     Location._parse 進行解碼;patch 這一個 classmethod 即同時覆蓋「單則訊息」
#     與「MessagePack 內嵌」兩條路徑。
#   - data[3] 是 _parse 內的區域變數,無法從外層攔截單行;因此包裝整個 _parse,
#     在拿到結果後用「正確的 signed 值 × multiplier」覆寫 speed_vertical。
#
# 用法:
#   import dtpyodid_patch          # import 即自動 apply()
#   # ... 之後照常使用 dtpyodid / dt_odid_parser / odid_slip_reader 即可
#
#   # 或在程式進入點明確呼叫:
#   import dtpyodid_patch; dtpyodid_patch.apply()
#
#   # 驗證 patch 是否生效:
#   python dtpyodid_patch.py          # 跑內建自我驗證
# =============================================================================

import struct

import dtpyodid.messages.location as _loc
from dtpyodid.message import SPEED_VERTICAL_MULTIPLIER as _MULT

# 旗標:確保幂等(重複 import / 重複 apply 不會層層包裝)
_PATCH_ATTR = "_speed_vertical_signed_patched"


def _correct_speed_vertical(body: bytes) -> float:
    """
    從「已剝掉 rid 的 Location body」重新計算正確的 signed 垂直速度。

    body 佈局(見 location.py._parse):
        [0]=header  [1]=direction  [2]=speed_h  [3]=speed_v(signed int8)
    原碼用 data[3](無號);這裡改用 struct 'b'(有號 int8 −128..127)。
    """
    signed = struct.unpack("b", body[3:4])[0]
    return _MULT * signed


def apply() -> bool:
    """
    套用 patch。回傳 True 表示本次實際套用,False 表示先前已套用(幂等)。
    """
    if getattr(_loc.Location, _PATCH_ATTR, False):
        return False  # 已套用,不重複

    # 取出原始未綁定函式(classmethod 底層的 __func__)
    _orig_parse = _loc.Location._parse.__func__

    def _patched_parse(cls, data: bytes):
        loc = _orig_parse(cls, data)          # 走原本完整解析
        loc.speed_vertical = _correct_speed_vertical(data)  # 只覆寫這一欄
        return loc

    _loc.Location._parse = classmethod(_patched_parse)
    setattr(_loc.Location, _PATCH_ATTR, True)
    return True


def is_applied() -> bool:
    """patch 是否已生效。"""
    return bool(getattr(_loc.Location, _PATCH_ATTR, False))


# import 即自動套用 —— 讓 `import dtpyodid_patch` 一行就修好
apply()


# --- 自我驗證 ----------------------------------------------------------------
def verify() -> bool:
    """
    用「真實韌體會送的」ASTM 線上 byte 直接餵 parse,確認 patch 後下降速度正確。
    回傳 True = 全部通過。
    """
    import dtpyodid.parser as P

    def real_frame(speed_v_int8: int) -> bytes:
        # [rid|ver][header][dir][speed_h][speed_v(signed)] + 後續欄位(填 0/合理值)
        body = bytes([(0x1 << 4) | 0x2, (2 << 4) | (1 << 2), 90, 0])
        body += struct.pack("b", speed_v_int8)
        body += struct.pack("<iihhh", 0, 0, 2000, 2000, 2240)
        body += struct.pack("<BBHB", 0, 0, 0, 0)
        body += b"\x00"
        return body

    cases = [
        ("平飛",            0,   0.0),
        ("緩降 -2 m/s",    -4,  -2.0),
        ("進場 -5 m/s",    -10, -5.0),
        ("自由落體 -15 m/s", -30, -15.0),
        ("快速爬升 +5 m/s", 10,  5.0),
    ]
    print(f"patch 已套用: {is_applied()}\n")
    print(f"{'情境':<20}{'線上byte':<12}{'期望':<12}{'解出':<12}判定")
    print("-" * 60)
    all_ok = True
    for name, sv_int8, expected in cases:
        frame = real_frame(sv_int8)
        loc = P.parse(frame)
        got = loc.speed_vertical
        ok = abs(got - expected) < 0.01
        all_ok &= ok
        sv_byte = struct.pack("b", sv_int8)[0]
        print(f"{name:<20}0x{sv_byte:02x}{'':<8}{expected:>+6.1f} m/s {got:>+8.1f} m/s   {'OK' if ok else 'XX'}")
    print("-" * 60)
    print("✅ 全部通過 — 下降速度已正確" if all_ok else "❌ 仍有錯誤")
    return all_ok


if __name__ == "__main__":
    import sys
    sys.exit(0 if verify() else 1)
