# 東石影音（SAGA.ai）交接紀錄

> 期間：2026-11-01 ～ 2026-11-12　｜　交接日：2026-11-12 10:45（台北）
> Repo：**eastonesd/eastone.saga**（11/10 由 `saga.ai` 改名；舊名會轉址，但工具權限要用新名）
> 給下一個工作階段：先讀完本檔，再讀 `ARTHUR-DISCORD.md`（與 A.R.T.H.U.R. 的串接契約）。

---

## 一、主人的偏好與規矩（務必遵守）

- 稱呼「葉里克先生（sir）」，身分是管家「亞瑟」；**只用繁體中文**（主人看不懂英文）。語氣幽默、優雅、冷靜，直接給解法。
- 重視**效率與省錢**：能用免費資源就不用付費；Modal 每月上限 30 美元。
- **取消主人自己發起的工作要先問**；刪檔要先取得同意。
- 不喜歡手動 merge、不喜歡打終端機指令 → 一律直接 commit 到 main、用 App 按鈕或 Issue 留言觸發。
- 本機 repo 放在 `C:\Users\krfin\Documents\GitHub`（GitHub Desktop）。
- 給操作步驟前**先確認畫面是最新版**（11/12 因 Meta 改版給錯步驟，主人很不滿）。不確定就請主人截圖。
- 檔名慣例：下載到主人電腦的成片一律叫 `《劇名》ep01.mp4`、海報 `《劇名》海報.jpg`；GitHub release 內維持 `sN-epNN.mp4`（中文與標點在 release 檔名不穩）。
- commit 結尾加：
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01KLCHjm6aFzGXaGx7kYS7Qx
  ```

---

## 二、系統全貌（一句話）

靈感 → GitHub Issue（label「新劇」，可從 App、Issue 表單或 Discord 經 A.R.T.H.U.R. 開）→ LLM 寫劇本 → Kaggle T4×2 免費 GPU 算圖 ＋ Modal L4 配音 → GitHub Actions 平行剪輯 → 成片放 GitHub Release（每部劇一個 tag：s1、s2、s5…）→ 一鍵／排程上架 YouTube、Instagram → Discord 通知。

### 技術棧
| 環節 | 用什麼 | 備註 |
|---|---|---|
| 劇本 | Gemini → Groq（gpt-oss-120b）→ OpenRouter 免費模型 | 自動換手、冷卻機制在 `saga/llm.py` |
| 算圖 | Kaggle T4×2：SDXL ＋ Lightning 8 步 ＋ IP-Adapter Plus Face ＋ 畫風 LoRA | 模型直接從 HF 下載（比掛載快）；橫式 1344×768 |
| 配音 | IndexTTS-2.5（Modal L4 最多 10 台平行為主，Kaggle 備援，Edge-TTS 最後手段） | 聲線庫 AISHELL-3 在 `assets/voices/` |
| 剪輯 | FFmpeg（運鏡、故障特效、ASS 字幕、名牌、BGM 壓低） | 1920×1080 ＋ 自動產 9:16 直式版 |
| App | GitHub Pages：`eastonesd.github.io/eastone.saga`（docs/） | PWA，service worker 版本 `saga-v10` |

### 主要 workflows（.github/workflows）
| 檔案 | 用途 | 觸發 |
|---|---|---|
| plan.yml | 新劇企劃（劇本聖經、大綱） | 開「新劇」Issue |
| produce.yml | 生產：算圖＋配音＋剪輯＋直式版＋上傳 release | Issue 留言 `/produce all\|N-M [--video\|--rewrite\|--redo\|--revoice]` |
| reoutline.yml | 調整集數／長度 | `/reoutline N 秒數` |
| poster.yml | 劇集海報（9:16＋3:4，右下角 SAGA 小徽章） | `/poster [N] [--regen]` |
| vertical.yml | 補做 9:16 直式版（含 1.5 秒 SAGA.ai 片頭） | `/vertical [N-M]` |
| publish.yml | 批次上架 YouTube／IG；`to=check` 只檢查授權＋IG 預演不發布 | `/publish youtube\|instagram\|all [N-M]`、App「批次上架」 |
| schedule.yml | 排程上架：讀 `schedule/publish.json`，**工作睡到指定時間再發**（不靠 GitHub 定時器） | 改 publish.json 後 push、或手動觸發 |
| stats.yml | 營利進度：每天 06:41 抓 YT／IG／Threads 數據 → `stats/monetization.json` | 定時＋手動 |
| benchmark.yml | 標竿片分析（量化＋Gemini 導演視角） | label「標竿」的 Issue、`/analyze` |
| stylelab.yml | 畫風實驗室（多組算圖設定對照圖） | 手動，設定檔 `lab/*.json` |
| stylebench / voicebench / diag / test | 畫風／聲線測試、診斷（`diag` 可指定 Kaggle notebook） | 手動 |

### Secrets（repo Settings → Secrets and variables → Actions）
GEMINI_API_KEY、GROQ_API_KEY、OPENROUTER_API_KEY、KAGGLE_USERNAME、KAGGLE_KEY、HF_TOKEN、MODAL_TOKEN_ID／SECRET、
**YT_CLIENT_ID／YT_CLIENT_SECRET／YT_REFRESH_TOKEN**（含 youtube ＋ yt-analytics.readonly 權限，Google 應用程式已設為正式版）、
**IG_TOKEN**（Meta「統一登入」的企業整合系統使用者權杖，EAA 開頭，綁商家資產組合，不會隨個人帳號過期）、
**THREADS_TOKEN**、**DISCORD_WEBHOOK**（`#東石影音` 頻道）。

---

## 三、作品現況

| 代號 | 劇名 | 規格 | YouTube | Instagram | TikTok |
|---|---|---|---|---|---|
| s1 | 《鐵獄逆襲：血債血償》 | 11 集，寫實→後改 3D 國漫 | 未上 | ep01–03（主人 11/9 手動）；**主人指示不再發** | 11 集已手動上（ep07–11 疑被防洗版限流，觀看 0） |
| s2 | 《重生皇叔：制霸三國》 | 60 集 × 50 秒，3D 國漫 | **60 集全公開**（自動上架，播放清單已建） | ep01–03（11/12 09:27 補發成功） | — |
| s5 | 《反轉吧！劇本守護者》 | 10 集 × 30 秒 | 未上 | **排程 11/12 12:00 發 ep01–03**（schedule.yml 正在睡等） | — |

- 主人電腦的兩個資料夾（桌面）：`《重生皇叔：制霸三國》`、`《鐵獄逆襲：血債血償》`，內含**直式版**（含片頭）＋海報＋文案檔 `《劇名》文案.txt`；原本的橫式版收在子資料夾「橫式」。
- YouTube 頻道「SAGA ｜ 故事放映室🎥」；IG／Threads 帳號 `saga.storyroom`。
- YouTube 上架設定（讀回自既有影片，程式沿用）：公開、非兒童內容、類別「電影與動畫」、標準授權、可嵌入、繁中；說明欄＝單集鉤子→全 N 集→簡介→AI 生成聲明→hashtag。⚠️ 「AI 合成內容」旗標讀回為空，待確認是否要在 Studio 手動勾。
- 上架文案固定格式（主人訂的）：`《劇名》epNN` ／ 單集鉤子（ep01 多一行簡介）／ `🔍點擊主頁連結，觀看完整版劇情🎞️` ／ `#saga #短劇 #類型 #劇名` ＋ `#因為一個片段看了整部劇`。

---

## 四、11/01～11/12 完成事項（依時間）

1. **從零建好整條產線**（11/1～11/6）：Issue 觸發、PWA App、LLM 備援鏈、Kaggle 算圖、IndexTTS（Modal 10 台平行）、矩陣剪輯、自動接力、海報系統。s1、s2 完成。
2. **s2 下載與修補**：60 集下載到主人電腦；ep01/06/07/40 殘片（劇本在存檔競爭中遺失）以 `--rewrite` 重做。
3. **批次上架**（`saga/publish.py`）：YouTube 斷點續傳＋播放清單＋縮圖；IG Reels；`published.json` 防重複。
4. **9:16 直式版**（`saga/vertical.py`）：置中裁切保留原字幕大小，上方劇名集數、下方追更提示＋AI 標示；**1.5 秒 SAGA.ai 徽章片頭**；只用 CPU、零 GPU 成本。
5. **品牌視覺**：App 圖示、favicon、README 換新標誌（主人要求「不裁切、讓他糊」）；海報右下角小徽章。
6. **Q 版公仔畫風研究**：找到 `Muapi/nendoroid-chibi-art-style-capture-lora-xl`（openrail++）；首輪試拍質感到位但角色跑掉（綠髮、日系臉）。
   - 根因：Lightning 8 步 **CFG=0 → 負面提示詞完全無效**；提示詞超過 77 token 被截斷。
   - 已改：kaggle_kernel 支援切換主模型（lightning／sdxl／jugg＝Juggernaut XL v9）、CFG＋負面提示、多 LoRA 疊加；畫風實驗室 `stylelab`。
   - **第一輪 `lab/chibi_r1.json`（4 組對照）跑了 90 分鐘撞 GitHub 時限中止，結果未取回** ← 待續。
7. **標竿片分析**（`saga/benchmark.py`）：Discord 丟片 → Issue →量化（切鏡、動態量、語速、停頓、響度，自動裁掉螢幕錄影的手機介面）＋ Gemini 導演視角報告。主人決定**暫時維持 Gemini**。
   - 發現：標竿片畫面動態量約為我們 **11 倍**；響度標竿 −8.4 LUFS vs 我們 −17.2（我們偏小聲）。
8. **安裝 FFmpeg／中文字型不再走 apt**（`scripts/setup_media.sh`，GitHub 直接下載＋快取）：s5 曾因 apt 卡 12 分鐘失敗，改後 8 秒裝完；s5 生產完成。
9. **營利進度追蹤**（`saga/monetize.py`）：YouTube（訂閱、90 天上傳、365 天長片時數、90 天 Shorts 觀看）、IG 粉絲、Threads 粉絲；TikTok 需手動回報（`stats/manual.json`）。門檻、擇一條件、每日成長、預計達標日都算好。
10. **Discord 通知**（`saga/notify.py`，Webhook）：排程設定公告、每次上架完成通報。
11. **排程上架**：08:00 那次因 GitHub 定時器整晚沒啟動而失敗 → 另一工作階段改成「工作睡到時間再發」並補發 s2。

---

## 五、待辦（依優先序）

1. **確認 12:00 s5 IG ep01–03 是否發出**（看 Discord 通知或 `series/s5/published.json`）。
2. **Q 版畫風精修**：重跑 `stylelab`（preset `chibi_r1`）。建議先縮小對照組數或降低定妝照解析度，避免再撞時限；用 `diag`（kernel=saga-lab）看 Kaggle 時間花在哪。目標是選出標竿設定，再生新劇。
3. **配音改善**（主人反映很糟）：換有情緒張力的聲音樣本（或 AI 設計聲線）、加後製（殘響、等化、**響度拉到約 −14 LUFS**）、與 CosyVoice 做 A/B 盲測。
4. **動畫化**（主人要「一天兩集、動畫細節拉滿」）：在 T4 實測 Wan 2.2（Apache 2.0）與 LTX-2.3（營收門檻授權）的每段耗時，評估重點鏡頭全動態＋其他微動態。
5. **A.R.T.H.U.R. 端待實作**（規格都在 `ARTHUR-DISCORD.md`）：`/saga-ref` 指令（附件要先轉存 Supabase，Discord 臨時網址會 403）、營利進度條（讀 `stats/monetization.json`）、`/saga-stats tiktok` 手動回報。
6. **改名後續**：App 網址已變 `eastonesd.github.io/eastone.saga`，手機上舊安裝的 App 要重裝；App 設定中的 repo 名稱要改成 `eastone.saga`。
7. YouTube「AI 合成內容」旗標未生效，確認處理方式。
8. TikTok：之後發片每天 1～2 支、每支間隔 1～2 小時、務必打開「AI 生成內容」開關；s1 ep07–11 被限流的影片不要刪除重傳。

---

## 六、踩過的坑（別再踩）

- **GitHub 定時器不可靠**：新加的排程可能好幾小時不啟動 → 重要時間點用「工作睡到時間」。
- **apt 套件伺服器會卡** → 用 `scripts/setup_media.sh`。
- **Kaggle 送出的 payload 要小於約 1MB** → 參考圖與聲線用 raw.githubusercontent 網址傳。
- **多工作同時 commit 會互相蓋掉** → 一律用 `scripts/commit.sh`（會重試 rebase；已涵蓋 series、lab、stats、bench）。
- **Discord 附件網址幾小時內失效**；本工作環境連不到 Discord CDN／Meta Graph，要經 GitHub Actions 或主人電腦。
- **Meta 介面常改版**：IG 已改「統一登入」（需商家資產組合、權杖 EAA 開頭、API 主機 graph.facebook.com）；IG 上架改用「給公開網址讓 IG 自己抓」，直傳通道會回 ProcessingFailedError。
- **錯誤訊息絕不輸出任何權杖字元**（repo 是公開的）。11/12 曾誤將應用程式密鑰前 4 碼寫入公開紀錄，已建議主人重設密鑰。
- 同一時間可能有**其他工作階段**在改同一個 repo：動手前先 `git pull`，看到別人已在處理就不要重做。
- 「卡在 queued 的幽靈工作」會佔住同一個 concurrency group → 上架檢查已改用獨立佇列。

---

## 七、常用操作速查

- 看某部劇進度：App 劇集頁，或 GitHub Issue（劇的編號＝sN 的 N）。
- 重做某幾集：`/produce 1,6,7,40 --rewrite`（逗號清單可用）。
- 上架前檢查（不發布、含 IG 預演）：Actions → 批次上架 → `to=check`。
- 新增排程：編輯 `schedule/publish.json` 加一筆 `{"at": "2026-11-13T08:00:00+08:00", "slug": "s5", "to": "instagram", "eps": "4-6", "note": "說明"}`，push 後自動在 Discord 公告並守時發布。
- 下載到主人電腦：主人電腦已連結；用電腦端 shell 以 curl 直接從 release 下載（並行數 ≤2、加重試，否則代理會 504），再逐一比對檔案大小。
