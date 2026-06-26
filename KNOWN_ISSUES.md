# Known Issues

## 1. `dtpyodid` 垂直速度(vertical speed)負值解碼錯誤 — 影響下降/降落場景

**嚴重度:** 高(影響真實硬體資料,且方向相反)
**狀態:** 已確認根因、已驗證修法;尚未回報上游
**影響範圍:** 開源 OpenDroneID 解碼器 [`dronetag/python-odid`](https://github.com/dronetag/python-odid)(import 名 `dtpyodid`),`Location` 訊息的 `speed_vertical` 欄位
**發現日期:** 2026-06-03 / 驗證環境:Windows 10, Python 3.11.9, dtpyodid (`firmware-pyodid` 99.99)

### 症狀

只要無人機/飛機**下降**(垂直速度為負),`dtpyodid.parse()` 會把它解成一個
**荒謬的正大數**(約 `128 + 真實值`),等於把「下降」讀成「高速上升」。
上升、平飛、以及所有其他欄位(經緯度、各種高度、水平速度、航向)均正常。

### 真實硬體路徑實測(直接以 ASTM F3411 標準線上 byte 餵 `parse`,**不經** `pack`)

ASTM F3411 定義:vertical speed = **signed int8** × 0.5 m/s(正=上升,負=下降)。
真實韌體以 two's complement 送出負值。

| 真實情境 | 線上 byte | ASTM 正解 | dtpyodid 解出 | 判定 |
|---|---|---|---|---|
| 平飛 | `0x00` | 0.0 m/s | +0.0 m/s | ✅ |
| 緩降 −2 m/s | `0xFC` | **−2.0 m/s** | **+126.0 m/s** | ❌ |
| 進場 −5 m/s | `0xF6` | **−5.0 m/s** | **+123.0 m/s** | ❌ |
| 自由落體 ~−15 m/s | `0xE2` | **−15.0 m/s** | **+113.0 m/s** | ❌ |
| 爬升 +5 m/s | `0x0A` | +5.0 m/s | +5.0 m/s | ✅ |

錯誤關係:`錯誤值 = 128 + 正確值`(任何下降都被讀成 ≥128 m/s 的正數)。

### 根因

`dtpyodid/messages/location.py` 的 `_parse()`(約第 60、84 行):

```python
speed_vert = data[3]                                  # ← bytes index 永遠回傳 0–255 無號值
...
speed_vertical = SPEED_VERTICAL_MULTIPLIER * speed_vert
```

`data[3]` 讀出的是**無號** 0–255,但該 byte 在 ASTM 標準下是**有號 int8**。
負值(如 `−4` → `0xFC`)被當成 `252`,再乘 0.5 → 126.0。

> 注意:這**不是** Linux/Windows 平台差異。此庫所有 `struct` 與 `to_bytes` 均明確使用
> `<`(little-endian),跨平台一致;問題純粹是 decoder 漏做 signed 轉換。
> 在任何 OS 上都會得到相同的錯誤值。

### 同源確認

合成測資路徑(`Location(speed_vertical=-2.0).pack()` → `parse()`)也得到 `+126.0`,
與真實硬體路徑(手組 `0xFC` byte → `parse()`)結果一致,證明兩條獨立路徑錯在
**同一個** decoder 缺陷(`data[3]` 無號讀取),而非合成測資本身的問題。

### 修法(已 monkey-patch 驗證,五情境含自由落體全部修正)

`location.py` 第 60 行:

```python
# 現況(錯):
speed_vert = data[3]
# 正解:
import struct
speed_vert = struct.unpack('b', data[3:4])[0]   # 有號 int8: −128..127
```

驗證結果(套用修法後):

| 情境 | ASTM 正解 | 修法後 |
|---|---|---|
| 平飛 | 0.0 | +0.0 ✅ |
| 緩降 −2 m/s | −2.0 | −2.0 ✅ |
| 進場 −5 m/s | −5.0 | −5.0 ✅ |
| 自由落體 −15 m/s | −15.0 | −15.0 ✅ |
| 爬升 +5 m/s | +5.0 | +5.0 ✅ |

### 待辦

- [x] 提供本地 monkey-patch 模組(不改 venv 內的庫檔)→ **[`dtpyodid_patch.py`](dtpyodid_patch.py)**。
      由 `dt_odid_parser.py` 在成功 import `dtpyodid` 後自動套用,三個接收端(serial / BLE /
      WiFi-demo)皆自動受惠。幂等、缺模組時靜默略過;`python dtpyodid_patch.py` 可跑自我驗證。
- [ ] 向上游 [`dronetag/python-odid`](https://github.com/dronetag/python-odid) 回報 issue / 送 PR
- [x] 檢查 `speed_horizontal` 是否有類似隱患 → 已查:**無**(其 `speed_mult` 兩段式縮放
      將值壓進 0–255 無號範圍,且速率語意恆 ≥ 0,故無 signed 問題)

### 套用方式(本地修正)

```python
import dtpyodid_patch          # import 即自動修正下降速度
```

實務上**無需手動 import** —— `dt_odid_parser.py` 已在內部自動套用,因此使用
`odid_slip_reader.py`(序列)、`ble_reader.py`(BLE)或 WiFi demo 時皆已修正。
此 patch 僅修 **decode** 端;上游 `pack()`(encode)未在範圍內(真實韌體不走 pack)。

### 真實硬體實測注記(2026-06-15)

拿實體 RIDER 經 BLE 收到真實無人機 ODID 並完整解碼成功(見 README「真實硬體實測結果」)。
但**該批真資料皆為平飛/空中、`speed_vertical=0`,未觸發本下降速度 bug**——亦即「真機測過」
**不等於**此 bug 已不存在。其正確性目前仍靠離線五情境(含自由落體 −15 m/s,見上表)驗證;
待日後取得「下降中」的真實飛行資料,即為此 patch 在真硬體路徑上發光之時。
