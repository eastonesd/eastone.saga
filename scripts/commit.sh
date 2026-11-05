#!/usr/bin/env bash
# 把 series/ 的變更推回 repo。多個工作同時推送時會衝突：自動 rebase（以本次內容為準）並重試，
# 真的推不上去就讓步驟失敗，避免像之前那樣默默弄丟分鏡劇本。
set -u
MSG="$1"
git config user.name "saga-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add series
git diff --cached --quiet && { echo "沒有變更"; exit 0; }
git commit -q -m "$MSG"
for i in 1 2 3 4 5 6; do
  if git pull -q --rebase -X theirs origin main && git push -q origin HEAD:main; then
    echo "已推送：$MSG"; exit 0
  fi
  git rebase --abort 2>/dev/null || true
  echo "推送衝突，第 $i 次重試…"; sleep $((i * 5))
done
echo "::error::推送失敗：$MSG"; exit 1
