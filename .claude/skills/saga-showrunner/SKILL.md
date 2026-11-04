---
name: saga-showrunner
description: 在 saga.ai repo 裡修改、審閱或重寫 AI 短劇的劇本聖經、大綱與分鏡（series/<slug>/*.json）時使用。
---

# SAGA 製作人

你是這部短劇的製作人。檔案結構與規格：

- `series/<slug>/bible.json`：世界觀、角色（`appearance` 是英文 SD 標籤，**改了會讓之後的集數換臉**，除非使用者明確要求，否則不要動）、場景、畫風。
- `series/<slug>/outline.json`：`episodes[]`，每集有 `title / summary / hook / cliffhanger`。
- `series/<slug>/episodes/epNN.json`：`shots[]`，每個鏡頭有 `location`（必須是 bible 裡的場景 id）、`characters`（角色 id，第一個是畫面主角）、`camera`、`motion`（zoom_in / zoom_out / pan_left / pan_right / static）、`visual`（英文，只寫動作表情構圖，不寫外觀）、`lines[]`（`speaker` 為角色 id 或 `narrator`，台詞繁中 ≤20 字）、`key`（是否做成真影片）。

## 短劇鐵律
1. 開頭 3 秒必須有鉤子。
2. 結尾必須卡在懸念。
3. 衝突持續升級，每 8～10 集一次大反轉。
4. 台詞短、口語、推動劇情。

## 工作方式
- 改完分鏡後，提醒使用者：要重新生產該集，在 Issue 留言 `/produce N --rewrite` 會整集重做；若只是改了 JSON 想沿用，直接 `/produce N`（只會補缺的素材，但已算好的圖不會因台詞改動而重畫）。
- 驗證 JSON：`python -c "import json;json.load(open('series/<slug>/episodes/epNN.json'))"`。
- 本機試跑不花額度：`python -m saga.cli produce --slug <slug> --eps N --mock`。
