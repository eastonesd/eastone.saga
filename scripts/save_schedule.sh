#!/usr/bin/env bash
# 把 schedule/ 的變更（已觸發標記）推回 main
git config user.name "saga-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add schedule
git diff --cached --quiet && exit 0
git commit -q -m "排程上架：已觸發 [skip ci]"
for i in 1 2 3 4 5; do git pull -q --rebase origin main && git push -q origin HEAD:main && exit 0; sleep 5; done
exit 1
