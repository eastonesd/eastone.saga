"""排程上架：讀 schedule/publish.json，時間到了就觸發「批次上架」，並標記 done。由 schedule.yml 每 10 分鐘執行。"""
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

P = Path("schedule/publish.json")
items = json.loads(P.read_text(encoding="utf-8"))
now = datetime.now(timezone.utc)
changed = False
for it in items:
    if it.get("done") or datetime.fromisoformat(it["at"]) > now:
        continue
    r = subprocess.run(["gh", "workflow", "run", "publish.yml", "-f", f"slug={it['slug']}", "-f", f"to={it['to']}",
                        "-f", f"eps={it['eps']}", "-f", f"limit={it.get('limit', 0)}"], capture_output=True, text=True)
    it["done"] = now.astimezone().isoformat(timespec="minutes") if r.returncode == 0 else None
    it["result"] = "已觸發" if r.returncode == 0 else f"觸發失敗：{r.stderr[:200]}"
    print(it.get("note", it["slug"]), it["result"], flush=True)
    changed = True
if changed:
    P.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
else:
    print("沒有到期的排程")
