# 東石影音 × A.R.T.H.U.R. × Discord

東石影音（本 repo）已經接上 A.R.T.H.U.R. 的 Discord 伺服器「EASTONE SYSTEM」，頻道是 `#東石影音`。
**這個 repo 不需要任何 Discord 金鑰或程式碼**：開工與通知都由 A.R.T.H.U.R.（eastonesd/arthur）在 Supabase 上處理。

## 開工：Discord `/saga`

1. 在 Discord 輸入 `/saga`，會跳出表單：靈感（必填）、集數（預設 60）、每集秒數（預設 90）。
2. 送出後，A.R.T.H.U.R. 的 `arthur-discord` 函式用 GitHub API 在本 repo 開一張 Issue：
   - 標題 `新劇：<靈感前 24 字>`，label `新劇`
   - 內文與 `.github/ISSUE_TEMPLATE` 的新劇表單相同格式（`### 靈感`、`### 類型`、`### 畫風`、`### 集數`、`### 每集長度（秒）`），結尾註明「由 A.R.T.H.U.R. 從 Discord 送出」
3. 之後的流程跟手動開 Issue 完全一樣（本 repo 的 workflow 接手企劃、製作、上架）。

> **契約**：只要「新劇」label 與 Issue 表單的標題格式不變，Discord 開工就不會壞。
> 若要改表單欄位或 label 名稱，請同步告知 A.R.T.H.U.R.（arthur repo 的 `supabase/functions/arthur-discord/index.ts` 的 `sagaCreate`）。

## 通知：送到 `#東石影音`

A.R.T.H.U.R. 的 `arthur-hub` 每 10 分鐘掃描一次本 repo（只讀），比較上一次的結果：

| 事件 | 怎麼偵測 |
|---|---|
| 新劇開拍 | label 為 `新劇` 的 Issue 數量增加 |
| 新完成 N 集 | Releases 裡檔名符合 `-epNN.mp4` 的附件數量增加 |
| 流程失敗 | 任一 GitHub Actions workflow 最近一次結果為 failure／timed_out（24 小時內，同一次只通知一次） |
| 網站打不開 | GitHub Pages（docs/）回應不是 2xx |

> **契約**：集數統計依賴 Release 附件檔名 `…-epNN.mp4`。改命名規則前請先告知 A.R.T.H.U.R.。

## 想主動發通知？

目前不需要。若之後要讓本 repo 的 workflow 直接發 Discord（例如「第 12 集已上傳 YouTube」這種即時事件），
做法是在 arthur-discord 加一個 `notify` 端點、本 repo 存一個 `ARTHUR_NOTIFY_TOKEN` secret，workflow 用 curl 呼叫。
要做的話請在 A.R.T.H.U.R. 的對話提出，由那邊開端點，這邊只加一個 workflow 步驟。

## 權限

Discord 指令只有已授權的人能用（主人＋用 `/allow` 加入的人）；其他人下指令什麼都不會發生。
