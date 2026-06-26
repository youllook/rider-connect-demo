# RIDER Local Detector — 部署手冊（MVP）

> 給「拿到這個程式、要把它跑起來」的人看。不需要懂程式，照著步驟做即可。

---

## 這是什麼

一個 Windows 小程式，連上 **Dronetag RIDER** 接收器，即時顯示附近無人機廣播的 Remote ID（電子車牌），並可把每一筆資料**自動轉送（webhook）到你的後端系統**。

畫面長這樣：一個深色網頁儀表板，兩顆燈號（USB / 藍牙），收到資料就即時滾動顯示。

---

## 你會拿到的東西

一個資料夾，裡面有：

| 檔案 / 資料夾 | 用途 |
|---|---|
| `rider-detector.exe` | 主程式（雙擊執行）|
| `.env.example` | 設定範本（要複製改名成 `.env`）|
| `_internal\` | 程式相依檔（**不要刪、不要動**）|

---

## 三步驟啟動

### 步驟 1：準備設定檔

1. 把 `.env.example` **複製一份**
2. 把複製出來的檔案**改名為 `.env`**（注意：前面有個點，後面沒有副檔名）
3. 用記事本打開 `.env`，至少確認這一項：

   ```
   RIDER_BLE_ADDR=E8:DF:6A:3F:D3:6D
   ```
   把等號右邊改成**你自己那台 RIDER 的藍牙位址**。
   （不知道位址？先照預設跑起來，用「藍牙掃描」找到位址再回來填。）

### 步驟 2：雙擊啟動

雙擊 `rider-detector.exe`。會跳出一個黑色主控台視窗，顯示目前設定，然後自動打開瀏覽器到儀表板。

> 若 Windows 跳出「不明發行者」警告 → 點「更多資訊」→「仍要執行」。
> （這是因為程式沒有花錢買數位簽章，不是病毒。）

### 步驟 3：連上 RIDER

- **USB 連接**：把 RIDER 用 **Type-C 線直接插電腦**（不要透過 USB hub）→ USB 燈轉 🟢 綠
- **藍牙連接**：把 RIDER 開機放旁邊 → 程式自動搜尋、連上 → 藍牙燈轉 🟢 綠

連上後，打開無人機 / RID 來源，儀表板就會開始滾動顯示資料。

---

## 燈號看法

| 燈號 | 意思 |
|---|---|
| ⚪ 灰 | 還沒偵測到 RIDER（正常待命）|
| 🟢 綠 | 已連上、通道就緒（顯示「接收中」代表正在收資料）|
| 🔴 紅 | 偵測到 RIDER 但連不上（被占用 / 連線失敗）|

---

## macOS 使用者請看這裡

> 本程式**同一份原始碼可跨 Windows / macOS**，但**執行檔不能跨平台共用**：
> Windows 的 `.exe` 不能在 Mac 跑，反之亦然。Mac 上要用，**必須在 Mac 上自行 build**（步驟見下方）。
> 已在 **macOS 15.6（Apple Silicon / arm64）** 實機驗證：解碼、儀表板、webhook 轉拋皆正常。

### macOS 上如何 build 出 Mac 執行檔

需要 Python 3.11（建議用 Homebrew 安裝）：

```bash
# 1) 用 Python 3.11 建立 venv
/opt/homebrew/bin/python3.11 -m venv venv
./venv/bin/python -m pip install --upgrade pip

# 2) 裝相依（含開源 ODID 解碼器）
./venv/bin/python -m pip install -r requirements-windows.txt
#   ↑ 檔名雖叫 -windows，但相依清單跨平台通用；macOS 同樣適用

# 3) 裝打包工具並 build
./venv/bin/python -m pip install pyinstaller
./venv/bin/pyinstaller rider-detector.spec --noconfirm --clean
```

build 完成後，執行檔在 `dist/rider-detector/rider-detector`（Mach-O arm64，無副檔名）。
> build 過程若看到 `betterproto.plugin ... SystemExit` 的訊息，**那是無害警告**（它是產生程式碼用的開發工具，執行階段用不到），不影響成品。

### ⚠️ macOS 最重要的一步：授權藍牙

macOS 對藍牙管控嚴格——**App 第一次要用藍牙時，系統會跳出授權請求，必須按「允許」**，否則藍牙燈會一直紅、訊息顯示
`Bluetooth is not authorized`。

- 若沒跳出請求、或不小心按了拒絕：到 **系統設定 → 隱私權與安全性 → 藍牙**，把這支程式（終端機 / rider-detector）打開授權，再重開程式。
- **注意**：從 SSH 遠端、或沒有圖形登入的情況下執行，藍牙**一定**拿不到授權（系統限制）。請在 Mac 上**直接登入、用「終端機」或雙擊執行**。

### macOS 上啟動

```bash
cd dist/rider-detector
# 把 .env.example 複製成 .env 並填好（同 Windows 步驟）
cp ../../.env.example .env
./rider-detector
```

首次執行若 macOS 跳「無法驗證開發者」（Gatekeeper）：
到 **系統設定 → 隱私權與安全性**，找到被攔下的項目按「仍要開啟」；或在終端機執行
`xattr -dr com.apple.quarantine dist/rider-detector` 解除隔離屬性。

### macOS 與 Windows 的差異速查

| 項目 | Windows | macOS |
|---|---|---|
| 執行檔 | `rider-detector.exe` | `rider-detector`（Mach-O，無副檔名）|
| 藍牙底層 | WinRT | CoreBluetooth（**需隱私權授權**）|
| USB 序列埠 | `COM3` 之類 | `/dev/tty.usb*` 之類 |
| 首次執行攔截 | 「不明發行者」→ 仍要執行 | Gatekeeper → 隱私權與安全性放行 |
| 藍牙連不上最常見原因 | 頭幾次 timeout（會自動重試）| **沒授權藍牙**（去隱私權設定開）|

---

## 設定 Webhook 轉拋（選用）

如果你要把收到的無人機 RID **自動送到你的後端系統**，打開 `.env` 改這幾項：

```
# 打開總開關
RIDER_WEBHOOK_ENABLED=true

# 填你的接收網址。可以填多個，用「逗號」隔開：
RIDER_WEBHOOK_URLS=https://你的後端/rid-ingest,http://192.168.0.50:5000/hook
```

改完**重開程式**生效。

之後每收到一筆無人機 RID，程式就會把一份 JSON **POST** 到上面每一個網址。送出的內容長這樣：

```json
{
  "ts": "2026-06-22T12:00:00.000",
  "source": "ble",
  "category": "RID",
  "kind": "Location",
  "text": "序號=ABC123 24.980000,121.280000 geo 100m",
  "rssi": -40,
  "tech": "B5",
  "device": "ABC123"
}
```

**送出紀錄**（成功/失敗）會寫在程式資料夾下的 `webhook_logs\` 裡，事後可查「到底有沒有送出去」。

> 一個網址連不上不會影響其他網址，也不會影響儀表板顯示。送失敗會自動重試幾次，仍失敗就記 log 跳過該筆。

---

## 常見問題

**Q：藍牙一直連不上？**
A：Windows 藍牙頭一兩次連線失敗是常見的，程式會自動重試（通常第 2～3 次成功）。若一直失敗，確認：① RIDER 有開機、② 電腦藍牙有開、③ **不要**從 Windows「設定 →新增裝置」去配對 RIDER（會跳 PIN 卡住），程式會自己連。

**Q：USB 燈一直灰色？**
A：① 確認用 Type-C **直插**電腦（不要過 hub）、② 換條線試試、③ 如果是不同型號的 RIDER，可能要在 `.env` 調整 `RIDER_VID` / `RIDER_PID`。

**Q：8000 連接埠被占用 / 網頁打不開？**
A：在 `.env` 改 `RIDER_PORT=8080`（或其他數字），重開程式。

**Q：改了 `.env` 沒反應？**
A：設定要**重開程式**才生效。並確認檔名是 `.env`（不是 `.env.txt`）。

**Q：`_internal` 資料夾可以刪嗎？**
A：不行。那是程式運作必要的相依檔，刪了就跑不起來。整個資料夾要一起保留、一起搬移。

---

## 設定項速查

| 設定 | 預設 | 說明 |
|---|---|---|
| `RIDER_HOST` | `127.0.0.1` | 儀表板主機；改 `0.0.0.0` 可讓同網段其他電腦連 |
| `RIDER_PORT` | `8000` | 儀表板連接埠 |
| `RIDER_OPEN_BROWSER` | `true` | 啟動時自動開瀏覽器 |
| `RIDER_BLE_ADDR` | （範例值）| **你那台 RIDER 的藍牙位址（必改）** |
| `RIDER_VID` / `RIDER_PID` | `0x303A` / `0x1001` | RIDER 的 USB 識別碼；一般不用改 |
| `RIDER_WEBHOOK_ENABLED` | `false` | Webhook 轉拋總開關 |
| `RIDER_WEBHOOK_URLS` | （空）| 轉拋端點，逗號分隔多個 |
| `RIDER_WEBHOOK_RID_ONLY` | `true` | 只轉真無人機 RID，跳過 RIDER 自身遙測 |
| `RIDER_RAWLOG` | `1` | 是否保存原始資料 |

完整設定與註解見 `.env.example`。
