# README

## Setup Instructions

### 1. Create and activate a Python virtual environment (recommended)

```bash
python3 -m venv venv
source venv/bin/activate   # On Windows use: venv\Scripts\activate
```

### 2. Install dependencies from `requirements.txt`

```bash
pip install -r requirements.txt
```

### 3. Install the Dronetag Proto Receiver package

Make sure you have the `dtproto_receiver-2.1.0-py3-none-any.whl` file available locally, then run:

```bash
pip install dtproto_receiver-2.1.0-py3-none-any.whl
```

---

## Windows / 一行重建 (含開源 OpenDroneID 解碼)

上游的 `requirements.txt` 只列了 `pyserial-asyncio`,實際跑起來並**完整解碼 OpenDroneID**
還需要 `betterproto`、本地 wheel、以及開源解碼器。為此提供 `requirements-windows.txt`,
把這一整組(2026-06-03 在 Python 3.11.9 實測驗證)固定下來,一行就能重建環境。

```powershell
# 1) 從真正的 Python 安裝建立專屬 venv(不要用其他工具的 venv)
C:\path\to\Python311\python.exe -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip

# 2) 在專案根目錄一行裝齊(相對路徑 wheel 必須從此目錄執行)
.\venv\Scripts\python.exe -m pip install -r requirements-windows.txt
```

`requirements-windows.txt` 比上游多了關鍵的開源解碼器
[`dronetag/python-odid`](https://github.com/dronetag/python-odid)(安裝後 import 名為 `dtpyodid`)。
`dt_odid_parser.py` 內建三層 fallback:優先用 Dronetag 內部的 `pyopendroneid`(未公開),
其次用**開源的 `dtpyodid`**,都沒有才退回只 dump 原始 protobuf。裝了這份 requirements
即啟用第二層,**無需 Dronetag 專有庫也能完整解碼**,且不必修改任何程式碼。

驗證解碼路徑與功能:

```powershell
$env:PYTHONIOENCODING = "utf-8"
# 應印出 has_dtpyodid (open-source): True
.\venv\Scripts\python.exe -c "import dt_odid_parser as m; print('dtpyodid:', m._has_dtpyodid)"
# 解碼一段合成 Basic ID,應印出 uas_id='ABCD1234567890ABCDEF'
.\venv\Scripts\python.exe -c "import dtpyodid.parser as d; print(d.parse(bytes([0x02,0x12])+b'ABCD1234567890ABCDEF'+b'\x00\x00\x00'))"
```

> 在 Windows 上序列埠名稱為 `COM3` 之類,而非 README 其他處的 `/dev/ttyUSB0`。
> `install.sh` 為 Linux 專用(udev + systemd),Windows 不適用——上面的手動步驟即為對等做法。

### 離線端到端測試(無需實體 RIDER)

`test_offline_demo.py` 自己合成一個完整的 `DriMessage`(內含 OpenDroneID Location),
走完 `odid_slip_reader.py` 真正會經歷的整條鏈路——SLIP 編碼 → varint 長度前綴 →
真實的 `SlipSerialReader` → `0x2A` handler → 開源 `dtpyodid` 解碼——並印出人類可讀結果。
除了用假 transport 取代序列埠外,**不 mock 任何解析邏輯**,可在沒有硬體時驗證安裝是否完整。

```powershell
$env:PYTHONIOENCODING = "utf-8"
.\venv\Scripts\python.exe test_offline_demo.py
```

> 合成測資以 `dtpyodid.Location(...).pack()` 產生。注意該開源版 encoder 對「負」垂直速度
> 編碼不對稱(僅影響合成資料;真實 RIDER 由韌體編碼,不走此路徑)。

---

## Example Usage

```bash
python odid_slip_reader.py -p /dev/ttyUSB0 --init "2A0A0A"
```

Replace `/dev/ttyUSB0` with your actual serial port name (the Dronetag RIDER appears as `/dev/ttyDRI` after the udev rule is installed).

---

## Automated Installation (udev + systemd)

`install.sh` installs the slip reader into a system-wide virtual environment, creates a udev rule that detects the Dronetag RIDER, and starts the reader automatically each time the device is plugged in. Output is written to a datetime-stamped log file.

### Usage

```bash
sudo ./install.sh --output ./output
```

The `--output` argument sets the directory where log files are written. Each connection creates a new file named `<YYYYMMDD_HHMMSS>_<device>.log` inside that directory.

### What it installs

| Path | Description |
|---|---|
| `/opt/odid_slip_reader/` | Script, wheel, and virtual environment |
| `/etc/udev/rules.d/99-odid-rider.rules` | Udev rule for Dronetag RIDER (VID `10c4`, PID `ea60`, serial `0x6969`) |
| `/etc/systemd/system/odid-slip-reader@.service` | Systemd service template started by the udev rule |

### How it works

1. The udev rule matches the Dronetag RIDER on plug-in and sets `SYSTEMD_WANTS=odid-slip-reader@<dev>.service`.
2. The systemd service calls the wrapper script with the kernel device name.
3. The wrapper opens the serial port and redirects all output to `<output_dir>/<timestamp>_<dev>.log`.

To verify after plugging in:

```bash
systemctl status "odid-slip-reader@ttyUSB0.service"
ls ./output/
```

---

## Notes

- The virtual environment keeps dependencies isolated.
- The wheel package `dtproto_receiver-2.1.0-py3-none-any.whl` must be downloaded or copied to your project folder before installing.
- Your `requirements.txt` should contain all other needed libraries (e.g., `serial_asyncio`).

---

## Message Encoding

### SLIP Encoding

Messages over the serial interface use the [SLIP (Serial Line Internet Protocol)](https://tools.ietf.org/html/rfc1055) format for framing.

SLIP special characters:

- `END` (`0x0A` or `\n`) — marks the end of a message
- `ESC` (`0xDB`) — escape character
- `ESC_END` (`0xDC`) — used to encode `END` inside data
- `ESC_ESC` (`0xDD`) — used to encode `ESC` inside data

#### Encoding Rules

- `END` (`0x0A`) is encoded as `ESC` `ESC_END` (`0xDB 0xDC`)
- `ESC` (`0xDB`) is encoded as `ESC` `ESC_ESC` (`0xDB 0xDD`)
- Encoded messages are terminated with `END` (`0x0A`)

---

### Protobuf Message Framing

The decoded SLIP payload is expected to be:

```
[address][protobuf message]
```

- The first byte (`address`) is used to dispatch the message to the appropriate handler.
- The remainder is a protobuf message, encoded with a **varint-prefixed length** (as used in Protobuf's delimited messages).

#### Protobuf Delimited Format

Each protobuf message is preceded by a varint indicating its size:

```
[varint length][protobuf bytes]
```

This format allows concatenated protobuf messages to be read from a stream.

The script includes a buffering mechanism to reassemble fragmented protobuf messages and remove the varint prefix before deserializing.

---

## Bluetooth Support (Dronetag RIDER)

In addition to serial SLIP communication, the script can also interact with the **Dronetag RIDER** device over **Bluetooth Low Energy (BLE)**.

The Dronetag RIDER exposes a custom **Notify Characteristic** for streaming messages using a Protobuf-based format. Unlike the serial interface, Bluetooth messages are **not SLIP-encoded** — they are raw protobuf messages framed using a **length-prefixed (varint) format**, suitable for stream-based processing.

### Bluetooth UUIDs

The Bluetooth GATT service and characteristic UUIDs used for communication are:

- **Service UUID**:
  ```
  898aa51c-f6be-4ad5-9398-e43f27cd93fc
  ```
- **Notify Characteristic UUID**:
  ```
  edb0b8a3-cf30-485c-bd8f-61f8ce998de8
  ```

### Message Format

Each notification from the characteristic contains:

```
[varint length][protobuf bytes]
```

These messages can be buffered and deserialized using standard Protobuf mechanisms for delimited streams (e.g., `FromString()` after stripping the length prefix).

### Integration Notes

- Notifications are typically sent periodically or upon new data being available.
- Since SLIP framing is not used, message boundaries are determined purely by the varint-prefixed Protobuf framing.

This approach is useful when a BLE connection is preferred over a wired serial interface, such as in mobile or embedded setups where physical access to USB is limited.

### BLE 接收端實作 — `ble_reader.py`

上述章節原本只有文件描述,此專案已補上**可執行的 BLE 接收端** [`ble_reader.py`](ble_reader.py)。
因為 BLE 訊息不走 SLIP、只是 varint 長度前綴的 protobuf 流,它**直接重用** `odid_slip_reader.py`
既有的 `ProtobufDelimitedBuffer` 與 `0x2A` handler,解碼路徑與序列埠完全共用(同樣使用開源
`dtpyodid` 解碼 OpenDroneID)。需要 `bleak`(`requirements-windows.txt` 已含;Windows 走 WinRT)。

```powershell
# 掃描附近 BLE 裝置,找出 RIDER 位址(不需先連線,可驗證本機藍牙堆疊)
.\venv\Scripts\python.exe ble_reader.py --scan

# 連上指定位址並開始接收 + 解碼(最可靠;弱訊號時優先用位址)
.\venv\Scripts\python.exe ble_reader.py --address E8:DF:6A:3F:D3:6D

# 不給位址時:掃描並連上第一個 RIDER(以「名稱含 RIDER」比對為主)
.\venv\Scripts\python.exe ble_reader.py
```

每則 BLE notification 的**原始 bytes 會先落地**到 `raw_logs/raw_ble_<時間>.log`(由
[`raw_logger.py`](raw_logger.py) 處理,序列接收端共用),即使後續 protobuf/ODID 解析失敗,
原料仍可事後離線重解。`RIDER_RAWLOG=0` 可關閉、`RIDER_RAWLOG_DIR=...` 可改輸出資料夾。

#### 真實硬體實測結果(2026-06-15, Windows 10 + bleak/WinRT)

拿實體 RIDER + 另一台廣播 Open Drone ID 的來源實測,**BLE 通道端到端打通、解碼正確**
(BasicID 序號、Location、System、OperatorID 四型全解出,無解析錯誤)。過程中歸納出幾個
與原始文件不同、且已反映進 `ble_reader.py` 的要點:

- **此台 RIDER 的 USB 晶片是 ESP32 內建 USB-CDC(VID:PID `303A:1001`),不是文件 `install.sh`
  udev 規則寫的 Silicon Labs CP210x(`10C4:EA60`)。** 序列埠以「USB 序列裝置 (COMx)」現身。
- **RIDER 的 BLE 廣播階段不帶 service UUID**(只在連上後才暴露 `898aa51c-...`),故
  `auto_connect()` 改以**名稱含 "rider"** 比對;單靠 service UUID 過濾永遠掃不到。
- **掃描用 `discover()` 掃滿時間窗再挑**,而非 `find_device_by_filter` 邊掃邊停 —— RIDER
  廣播間歇、訊號偏弱(rssi ~-86),邊掃邊停常誤判「找不到」。
- **連線需重試**:Windows BLE 頭一兩次常見 `TimeoutError` / `OSError(E_UNEXPECTED)`
  (「災難性的失敗」),`_connect_with_retry()` 重試前先 rescan 刷新快取,通常第 2~3 次成功。
- **轉發別人的 RID 不需要 RIDER 自己有 GPS fix** —— 室內(RIDER 無定位)仍正常收到並解出
  來源無人機的 ODID;ODID 訊息走文件特徵 `edb0b8a3-...`。
- ⚠ **不要從 Windows「設定 → 新增裝置」配對 RIDER**(會跳 PIN);BLE GATT 直連由 `bleak`
  自行處理,不需經 Windows 經典配對。

### WiFi 傳輸離線 demo — `test_offline_demo_wifi.py`

OpenDroneID 的四種傳輸技術中,**WiFi(Beacon / NAN)** 也是規劃內的接收通道。
[`test_offline_demo_wifi.py`](test_offline_demo_wifi.py) 比照序列版離線 demo,合成帶
`OdidWifiBeaconInfo` / `OdidWifiNanInfo` 的 `DriMessage`,走完整解碼鏈,輸出 `tech=WB`(Beacon)
與 `tech=WN`(NAN)的結果。無需硬體:

```powershell
$env:PYTHONIOENCODING = "utf-8"
.\venv\Scripts\python.exe test_offline_demo_wifi.py
```

> 三種接收通道對照:**序列 SLIP**(`odid_slip_reader.py`,已含)、**BLE**(`ble_reader.py`,本次新增)、
> **WiFi**(目前以離線 demo 示範解碼;真實 WiFi 嗅探需 monitor-mode 網卡,不在此範圍)。

---

## Serial Message Address and Activation

When using the serial SLIP interface to communicate with **Dronetag RIDER**, messages carrying Protobuf data are sent with the address:

```
0x2A
```

### Activation Requirement

To begin receiving messages on this channel, you **must first activate it** by sending an initial message. This is handled automatically by the script using the default `--init` value:

```
2A0A0A
```

This message is:

- Address byte: `0x2A`
- Payload: `0x0A 0x0A` (dummy data to trigger the channel)

The script sends this message (SLIP-encoded) right after opening the serial port.

You can modify or disable this activation sequence using the `--init` command-line argument.


---

## OpenDroneID Parser Notice

This sample project is designed to work with **Protobuf messages** that contain OpenDroneID data along with reception metadata (RSSI, MAC address, technology, etc.).

Dronetag uses an **internal Python OpenDroneID parser** to decode and display these messages in a human-readable format. This parser is **not publicly available**.

While the script includes code compatible with the internal parser (`pyopendroneid` and `dtproto_receiver`), full decoding of OpenDroneID data requires access to our proprietary parser.

If you require access for development or integration purposes, please **contact our support team at**:

📧 [support@dronetag.com](mailto:support@dronetag.com)
