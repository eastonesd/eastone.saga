/* SAGA.ai — 短劇工廠遙控器
 * 純前端：透過 GitHub API 開 Issue（送靈感）、留言（下生產指令）、讀 Release（看成片）。
 * 金鑰只存在這台裝置的瀏覽器裡。
 */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const view = $("#view");
const GENRES = ["都市逆襲", "霸總甜寵", "古裝宮鬥", "懸疑推理", "玄幻修仙", "重生復仇"];
const STYLES = ["寫實電影感", "日系動漫", "國風古韻", "3D 卡通"];

// ── 設定 ────────────────────────────────────────────────
const store = {
  get(k, d = "") { try { return localStorage.getItem("saga." + k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("saga." + k, v); } catch { /* 隱私模式 */ } },
  del(k) { try { localStorage.removeItem("saga." + k); } catch { /* */ } },
};
const cfg = {
  get repo() { return store.get("repo", "eastonesd/saga.ai"); },
  get token() { return store.get("token"); },
};

// ── GitHub API ──────────────────────────────────────────
class ApiError extends Error {
  constructor(status, msg) { super(msg); this.status = status; }
}
async function gh(path, { method = "GET", body, raw = false } = {}) {
  const url = path.startsWith("http") ? path : `https://api.github.com${path.replace("{repo}", cfg.repo)}`;
  const res = await fetch(url, {
    method,
    headers: {
      Authorization: `Bearer ${cfg.token}`,
      Accept: raw ? "application/vnd.github.raw+json" : "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).message || msg; } catch { /* */ }
    if (res.status === 401) msg = "金鑰無效或已過期，請到設定重新貼上";
    if ((res.status === 403 || res.status === 429) && /rate limit/i.test(msg)) {
      const reset = Number(res.headers.get("x-ratelimit-reset")) * 1000;
      msg = "GitHub 查詢次數暫時用完" + (reset ? `，${new Date(reset).toLocaleTimeString("zh-TW", { hour: "2-digit", minute: "2-digit" })} 後恢復` : "，約一小時內恢復") + "。工廠不受影響，會照常生產";
    }
    if (res.status === 403 && /permission|accessible/i.test(msg)) msg = "金鑰權限不足：需要 Issues 讀寫、Contents 讀取、Actions 讀取";
    throw new ApiError(res.status, msg);
  }
  if (res.status === 204) return null;
  return raw ? res.text() : res.json();
}
const getJSON = async (path) => JSON.parse(await gh(`/repos/{repo}/contents/${path}`, { raw: true }));

// ── 小工具 ──────────────────────────────────────────────
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let toastTimer;
function toast(msg, bad = false) {
  document.querySelector(".toast")?.remove();
  const t = document.createElement("div");
  t.className = "toast" + (bad ? " bad" : "");
  t.setAttribute("role", "status");
  t.textContent = msg;
  document.body.append(t);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.remove(), 4200);
}
function md(text) {
  const html = window.marked ? marked.parse(text, { breaks: true }) : esc(text).replace(/\n/g, "<br>");
  return window.DOMPurify ? DOMPurify.sanitize(html) : html;
}
function ago(iso) {
  const m = Math.round((Date.now() - new Date(iso)) / 60000);
  if (m < 1) return "剛剛";
  if (m < 60) return `${m} 分鐘前`;
  const h = Math.round(m / 60);
  return h < 24 ? `${h} 小時前` : `${Math.round(h / 24)} 天前`;
}
const slugOf = (issue) => `s${issue.number}`;
const titleOf = (issue) => {
  const m = issue.title.match(/《(.+?)》/);
  return m ? m[1] : issue.title.replace(/^新劇[:：]\s*/, "");
};
function handleError(e) {
  console.error(e);
  toast(e.message || "連線失敗，請檢查網路", true);
  if (e.status === 401) location.hash = "#/settings";
}

// ── 工廠狀態（右上角） ──────────────────────────────────
async function refreshFactory() {
  if (!cfg.token) return;
  const el = $("#factory");
  try {
    const { workflow_runs: runs } = await gh("/repos/{repo}/actions/runs?per_page=6");
    const live = runs.find((r) => r.status === "in_progress" || r.status === "queued");
    el.classList.toggle("on", !!live);
    el.href = live ? live.html_url : `https://github.com/${cfg.repo}/actions`;
    $("span", el).textContent = live
      ? `${live.name}・${live.status === "queued" ? "排隊中" : ago(live.run_started_at).replace("前", "")}`
      : "工廠待命";
  } catch {
    $("span", el).textContent = "狀態未知";
  }
}

// ── 路由 ────────────────────────────────────────────────
let poll;
async function route() {
  clearInterval(poll);
  const hash = location.hash || (cfg.token ? "#/series" : "#/settings");
  const [, page, arg] = hash.split("/");
  const tab = page === "s" ? "series" : page || "series";
  document.querySelectorAll(".tabs a").forEach((a) => {
    if (a.dataset.tab === tab) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  $("#tabs").hidden = !cfg.token;
  $("#factory").hidden = !cfg.token;
  if (!cfg.token && page !== "settings") { location.hash = "#/settings"; return; }
  window.scrollTo(0, 0);
  delete view.dataset.series;
  try {
    if (page === "new") renderNew();
    else if (page === "s") await renderSeries(Number(arg));
    else if (page === "settings") renderSettings();
    else await renderList();
  } catch (e) { handleError(e); }
  refreshFactory();
}
addEventListener("hashchange", route);

// ── 頁面：新靈感 ────────────────────────────────────────
function chipGroup(name, options) {
  return `<div class="chips" role="group" data-group="${name}">${options.map((o) =>
    `<button type="button" class="chip" aria-pressed="false" data-v="${esc(o)}">${esc(o)}</button>`).join("")}</div>`;
}
function stepper(id, value, min, max, step) {
  return `<div class="stepper"><button type="button" data-step="-${step}" aria-label="減少">−</button>
    <input id="${id}" type="number" inputmode="numeric" value="${value}" min="${min}" max="${max}" step="1">
    <button type="button" data-step="${step}" aria-label="增加">＋</button></div>`;
}
function wireSteppers(root) {
  root.querySelectorAll(".stepper").forEach((s) => {
    const input = $("input", s);
    input.addEventListener("blur", () => {
      const v = Math.round(Number(input.value) || Number(input.min));
      input.value = Math.min(Number(input.max), Math.max(Number(input.min), v));
      input.dispatchEvent(new Event("change"));
    });
    s.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
      const v = Number(input.value || 0) + Number(b.dataset.step);
      input.value = Math.min(Number(input.max), Math.max(Number(input.min), v));
      input.dispatchEvent(new Event("change"));
    }));
  });
}

function renderNew() {
  let draft = {};
  try { draft = JSON.parse(store.get("draft2", "{}")); } catch { /* */ }
  view.innerHTML = `
    <h1>今天想拍什麼？</h1>
    <p class="lede">一句話就夠。主角是誰、遇到什麼事、觀眾爽在哪，寫得越具體，劇本越好看。</p>
    <form id="idea-form">
      <div class="field">
        <label for="idea">靈感</label>
        <textarea id="idea" class="idea" required placeholder="被趕出家門的千金，三年後以神秘投資人身分回歸，讓當年的渣男跪著求她">${esc(draft.idea || "")}</textarea>
      </div>
      <div class="field"><span class="label">類型</span>${chipGroup("genre", GENRES)}</div>
      <div class="field"><span class="label">畫風</span>${chipGroup("style", STYLES)}</div>
      <div class="row field">
        <div><label for="eps">集數</label>${stepper("eps", draft.eps || 60, 1, 120, 10)}</div>
        <div><label for="secs">每集秒數</label>${stepper("secs", draft.secs || 90, 30, 300, 15)}</div>
      </div>
      <button class="btn primary" type="submit">開始企劃</button>
      <p class="muted small" style="margin-top:12px">送出後幾分鐘內，編劇部會寫好角色表與 60 集大綱。</p>
    </form>`;
  wireSteppers(view);
  const idea = $("#idea");
  const pickOf = (g) => $(`[data-group=${g}] [aria-pressed=true]`)?.dataset.v || "";
  const saveDraft = () => store.set("draft2", JSON.stringify({
    idea: idea.value, genre: pickOf("genre"), style: pickOf("style"), eps: $("#eps").value, secs: $("#secs").value,
  }));
  for (const g of ["genre", "style"]) {
    const chip = draft[g] && view.querySelector(`[data-group=${g}] [data-v="${CSS.escape(draft[g])}"]`);
    if (chip) chip.setAttribute("aria-pressed", "true");
  }
  view.querySelectorAll("[data-group]").forEach((g) => g.addEventListener("click", (e) => {
    const b = e.target.closest(".chip"); if (!b) return;
    const was = b.getAttribute("aria-pressed") === "true";
    g.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", "false"));
    b.setAttribute("aria-pressed", String(!was));
    saveDraft();
  }));
  idea.addEventListener("input", saveDraft);
  view.querySelectorAll(".stepper input").forEach((i) => i.addEventListener("change", saveDraft));
  $("#idea-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("button[type=submit]", e.target);
    const pick = (g) => pickOf(g) || "_No response_";
    const text = idea.value.trim();
    if (!text) return;
    btn.disabled = true; btn.textContent = "送出中…";
    try {
      const issue = await gh("/repos/{repo}/issues", {
        method: "POST",
        body: {
          title: "新劇：" + text.slice(0, 24),
          labels: ["新劇"],
          body: `### 靈感\n\n${text}\n\n### 類型\n\n${pick("genre")}\n\n### 畫風\n\n${pick("style")}\n\n` +
                `### 集數\n\n${$("#eps").value}\n\n### 每集長度（秒）\n\n${$("#secs").value}\n`,
        },
      });
      store.del("draft2");
      toast("已送出，編劇部開工");
      location.hash = `#/s/${issue.number}`;
    } catch (err) {
      handleError(err);
      btn.disabled = false; btn.textContent = "開始企劃";
    }
  });
}

// ── 頁面：我的劇 ────────────────────────────────────────
async function releaseOf(slug) {
  try { return await gh(`/repos/{repo}/releases/tags/${slug}`); }
  catch (e) { if (e.status === 404) return null; throw e; }
}
const posterOf = (rel, slug) => {
  const find = (n) => (rel?.assets || []).find((a) => a.name === n)?.browser_download_url;
  return { main: find(`${slug}-poster.jpg`), sq: find(`${slug}-poster-3x4.jpg`) };
};
const episodeFiles = (rel) => {
  const map = {};
  for (const a of rel?.assets || []) {
    const m = a.name.match(/-ep(\d+)\.(mp4|jpg)$/);
    if (m) (map[Number(m[1])] ||= {})[m[2]] = a.browser_download_url;
  }
  return map;
};

async function renderList() {
  view.innerHTML = `<h1>我的劇</h1><div class="skeleton"></div><div class="skeleton"></div>`;
  const issues = (await gh("/repos/{repo}/issues?labels=%E6%96%B0%E5%8A%87&state=all&per_page=50"))
    .filter((i) => !i.pull_request);
  if (!issues.length) {
    view.innerHTML = `<div class="empty">
      <img src="icons/logo-640.jpg" alt="SAGA.ai">
      <h1>還沒有任何劇</h1>
      <p class="lede">把第一個靈感交給我，剩下的交給工廠。</p>
      <a class="btn primary" href="#/new">寫下第一個靈感</a></div>`;
    return;
  }
  view.innerHTML = `<h1>我的劇</h1><ul class="series-list">${issues.map((i) => `
    <li><a class="series-item" href="#/s/${i.number}">
      <div class="poster" data-poster="${i.number}"></div>
      <div style="flex:1;min-width:0">
        <div class="t">${esc(titleOf(i))}</div>
        <div class="meta" data-meta="${i.number}">${slugOf(i)}・${ago(i.updated_at)}更新</div>
        <div class="progress"><b data-bar="${i.number}" style="width:0"></b></div>
      </div></a></li>`).join("")}</ul>`;
  // 每部劇的完成集數（平行載入）
  issues.slice(0, 20).forEach(async (i) => {
    const [rel, bible] = await Promise.all([
      releaseOf(slugOf(i)).catch(() => null),
      getJSON(`series/${slugOf(i)}/bible.json`).catch(() => null),
    ]);
    const files = episodeFiles(rel);
    const done = Object.values(files).filter((f) => f.mp4).length;
    const total = bible?.episode_count || 60;
    const first = Object.keys(files).sort((a, b) => a - b).find((k) => files[k].jpg);
    const pst = posterOf(rel, slugOf(i)).main;
    const meta = $(`[data-meta="${i.number}"]`);
    if (meta) meta.innerHTML = bible
      ? `${done} / ${total} 集完成・${ago(i.updated_at)}更新`
      : `<span class="pill live">企劃中</span> ${ago(i.created_at)}送出`;
    const bar = $(`[data-bar="${i.number}"]`);
    if (bar) bar.style.width = `${(done / total) * 100}%`;
    const poster = $(`[data-poster="${i.number}"]`);
    if (poster && (pst || first)) poster.style.backgroundImage = `url("${pst || files[first].jpg}")`;
  });
}

// ── 頁面：單部劇 ────────────────────────────────────────
async function renderSeries(num) {
  clearInterval(poll);  // 每次重畫前先關掉舊的計時器，避免倍增
  const silent = view.dataset.series === String(num);
  view.dataset.series = String(num);
  if (!silent) view.innerHTML = `<div class="skeleton" style="height:120px"></div><div class="skeleton" style="height:200px"></div>`;
  const slug = `s${num}`;
  const [issue, comments, rel, bible, outline] = await Promise.all([
    gh(`/repos/{repo}/issues/${num}`),
    gh(`/repos/{repo}/issues/${num}/comments?per_page=100`),
    releaseOf(slug),
    getJSON(`series/${slug}/bible.json`).catch(() => null),
    getJSON(`series/${slug}/outline.json`).catch(() => null),
  ]);
  const files = episodeFiles(rel);
  const pst = posterOf(rel, slug);
  const total = bible?.episode_count || 0;
  const lastPoster = [...comments].reverse().find((c) => c.body.startsWith("/poster"));
  const lastPosterDone = [...comments].reverse().find((c) => c.user.type === "Bot" && /海報完成|海報失敗/.test(c.body));
  const postering = Boolean(lastPoster && (!lastPosterDone || new Date(lastPosterDone.created_at) < new Date(lastPoster.created_at)));
  const raw = `https://raw.githubusercontent.com/${cfg.repo}/main/series/${slug}/poster`;
  const titles = Object.fromEntries((outline?.episodes || []).map((e) => [e.ep, e.title]));

  // 生產中：最後一次 /produce 之後還沒有回報，或最後的回報說「接力下一批」
  const lastCmd = [...comments].reverse().find((c) => c.body.startsWith("/produce"));
  const lastReport = [...comments].reverse().find((c) => c.user.type === "Bot" && /集完成|生產失敗|全部完成|停止接力/.test(c.body));
  const cmdAfterReport = lastCmd && (!lastReport || new Date(lastReport.created_at) < new Date(lastCmd.created_at));
  const pending = Boolean(cmdAfterReport || (lastCmd && lastReport && /接力下一批/.test(lastReport.body)));
  const queued = new Set();
  if (pending) {
    const range = new Set();
    if (/\ball\b/.test(lastCmd.body)) for (let k = 1; k <= total; k++) range.add(k);
    for (const part of ((lastCmd.body.replace(/--\w+/g, "").match(/[\d,\-]+/) || [""])[0]).split(",")) {
      const [a, b] = part.split("-").map(Number);
      if (a) for (let k = a; k <= (b || a); k++) range.add(k);
    }
    [...range].sort((x, y) => x - y).filter((k) => !files[k]?.mp4).slice(0, 10).forEach((k) => queued.add(k));
  }
  const left = total - Object.values(files).filter((f) => f.mp4).length;
  // 調整集數：最後一次 /reoutline 之後還沒有回報 → 處理中
  const lastRe = [...comments].reverse().find((c) => c.body.startsWith("/reoutline"));
  const lastReDone = [...comments].reverse().find((c) => c.user.type === "Bot" && /已調整為|調整集數失敗/.test(c.body));
  const reoutlining = Boolean(lastRe && (!lastReDone || new Date(lastReDone.created_at) < new Date(lastRe.created_at)));
  const MODES = [
    ["", "只做還沒完成的", "預設：已上架的集數會跳過"],
    ["--revoice", "只重錄配音", "沿用畫面，換聲線或配音失敗時用"],
    ["--redo", "重做畫面與配音", "沿用劇本，畫面不滿意時用"],
    ["--rewrite", "重寫劇本並全部重做", "劇情或長度不滿意時用"],
  ];
  const nextEp = Array.from({ length: total }, (_, k) => k + 1).find((k) => !files[k]?.mp4 && !queued.has(k)) || 1;

  const frames = Array.from({ length: total }, (_, k) => {
    const ep = k + 1, f = files[ep] || {};
    const cls = f.mp4 ? "done" : queued.has(ep) ? "queued" : "";
    const bg = f.jpg ? ` style="background-image:url('${f.jpg}')"` : "";
    return `<button type="button" class="frame ${cls}" data-ep="${ep}"${bg} ${f.mp4 ? "" : 'tabindex="-1"'}
      aria-label="第 ${ep} 集 ${esc(titles[ep] || "")}${f.mp4 ? "，播放" : ""}">
      <span class="n">${ep}</span><span class="ft">${esc(titles[ep] || "")}</span></button>`;
  }).join("");

  const done = Object.values(files).filter((f) => f.mp4).length;
  view.innerHTML = `
    <a class="btn text" href="#/series">‹ 我的劇</a>
    <h1>${esc(bible?.title || titleOf(issue))}</h1>
    ${bible ? `
      <p class="hero-logline">${esc(bible.logline)}</p>
      <div class="muted small">${esc(bible.genre)}・${total} 集 × 約 ${bible.episode_seconds} 秒・已完成 ${done} 集</div>
      <div class="cast">${bible.characters.map((c) => `<span><b>${esc(c.name)}</b> ${esc(c.role)}</span>`).join("")}</div>
      <div class="film" id="film">${frames}</div>
      <div class="film-legend"><span>亮格：可播放</span><span>紫框：生產中</span><span>暗格：尚未生產</span></div>
      <section class="panel" id="produce">
        <h2>${pending ? "工廠運轉中" : left ? `還有 ${left} 集待生產` : "全劇已完成"}</h2>
        <p class="muted small" style="margin:-4px 0 14px">${pending
          ? "每批 10 集，做完自動接力下一批，可以關掉 App 去忙別的。"
          : "一鍵生產到完結：每批 10 集自動接力，中途出錯會自動重試。"}</p>
        <button class="btn primary" id="go-all" type="button" ${pending || reoutlining ? "disabled" : ""}></button>
        <div class="field" style="margin-top:16px">
          <span class="label">重做方式</span>
          <div class="chips" role="radiogroup" id="mode">${MODES.map(([v, t], i) =>
            `<button type="button" class="chip" role="radio" aria-pressed="${i === 0}" data-v="${v}">${t}</button>`).join("")}</div>
          <p class="muted small" id="mode-hint" style="margin:8px 0 0">${MODES[0][2]}</p>
        </div>
        <details style="margin-top:6px">
        <summary class="muted small" style="cursor:pointer">只做某幾集</summary>
        <div style="margin-top:14px"></div>
        <div class="row field">
          <div><label for="from">從第</label>${stepper("from", nextEp, 1, total, 1)}</div>
          <div><label for="to">到第</label>${stepper("to", Math.min(total, nextEp + 2), 1, total, 1)}</div>
        </div>
        <label class="toggle"><input type="checkbox" id="video"> 關鍵鏡頭做成真影片 <span class="muted small">較耗 GPU 時數</span></label>
        <button class="btn ghost" id="go" type="button" style="width:100%" ${pending || reoutlining ? "disabled" : ""}>${pending ? "工廠正在處理上一批" : ""}</button>
        </details>
      </section>
      <section class="panel" id="poster-panel">
        <h2>劇集海報</h2>
        ${pst.main ? `
          <div style="display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap">
            <a href="${pst.main}" target="_blank" rel="noopener"><img src="${pst.main}" alt="海報" style="width:150px;aspect-ratio:9/16;object-fit:cover;border-radius:10px;border:1px solid var(--line)"></a>
            <div style="flex:1;min-width:160px">
              <p class="muted small" style="margin:0 0 10px">直式 1080×1920，TikTok／Reels／Shorts／紅果共用；另有 3:4 版給 Instagram 主頁格狀預覽。</p>
              <a class="btn ghost" href="${pst.main}" download style="width:100%;margin-bottom:8px">下載直式海報</a>
              ${pst.sq ? `<a class="btn ghost" href="${pst.sq}" download style="width:100%">下載 3:4 版</a>` : ""}
            </div>
          </div>` : `<p class="muted small" style="margin:-4px 0 12px">第一批生產時會自動畫海報；舊劇可以直接按下方「重畫 4 張底圖」。</p>`}
        <details style="margin-top:12px" ${postering ? "open" : ""}>
          <summary class="muted small" style="cursor:pointer">換底圖</summary>
          <img src="${raw}/candidates.jpg" alt="" onerror="this.remove()" style="width:100%;margin:12px 0;border-radius:8px">
          <div class="chips" id="poster-pick">${[1, 2, 3, 4].map((n) => `<button type="button" class="chip" data-n="${n}" ${postering ? "disabled" : ""}>用第 ${n} 張</button>`).join("")}</div>
          <button class="btn ghost" id="poster-regen" type="button" style="width:100%;margin-top:12px" ${postering ? "disabled" : ""}>${postering ? "海報製作中…" : "重畫 4 張底圖"}</button>
        </details>
      </section>
      <section class="panel">
        <h2>調整集數與長度</h2>
        <p class="muted small" style="margin:-4px 0 14px">目前 ${total} 集 × 約 ${bible.episode_seconds} 秒。調整後會依原大綱重新濃縮（保留結局），並清除舊分鏡；之後用「重做畫面與配音」把已上架的集數重做。</p>
        <div class="row field">
          <div><label for="re-eps">總集數</label>${stepper("re-eps", total, 5, 120, 5)}</div>
          <div><label for="re-secs">每集秒數</label>${stepper("re-secs", bible.episode_seconds, 30, 180, 10)}</div>
        </div>
        <button class="btn ghost" id="go-re" type="button" style="width:100%" ${pending || reoutlining ? "disabled" : ""}>${reoutlining ? "大綱重排中…" : pending ? "等工廠完成這一批" : "重排大綱"}</button>
      </section>` : `
      <section class="panel">
        <h2>編劇部正在寫劇本聖經</h2>
        <p class="muted" style="margin:0">角色表與分集大綱通常在幾分鐘內完成，這頁會自動更新。</p>
      </section>`}
    <h2>工作紀錄</h2>
    <ul class="feed">${[...comments].reverse().map((c) => {
      const bot = c.user.type === "Bot";
      return `<li><div class="who">${bot ? "工廠" : "您"}・${ago(c.created_at)}</div>
        ${c.body.startsWith("/") ? `<div class="cmd">${esc(c.body)}</div>` : `<div class="md">${md(c.body)}</div>`}</li>`;
    }).join("") || `<li class="muted">還沒有紀錄</li>`}
      <li><div class="who">您・${ago(issue.created_at)}</div><div class="md">${md(issue.body || "")}</div></li>
    </ul>
    <p><a class="small" href="${issue.html_url}" target="_blank" rel="noopener">在 GitHub 上開啟</a></p>`;

  if (bible) {
    wireSteppers(view);
    const from = $("#from"), to = $("#to"), go = $("#go");
    const label = () => {
      if (pending) return;
      const a = Number(from.value), b = Math.max(a, Number(to.value));
      go.textContent = a === b ? `生產第 ${a} 集` : `生產第 ${a}～${b} 集`;
    };
    from.addEventListener("change", () => { if (Number(to.value) < Number(from.value)) to.value = from.value; label(); });
    to.addEventListener("change", label);
    label();
    let mode = "";
    const allLabel = () => {
      const b = $("#go-all");
      if (pending) b.textContent = "生產中…";
      else if (reoutlining) b.textContent = "大綱重排中…";
      else if (mode) b.textContent = `${MODES.find((m) => m[0] === mode)[1]}（全部集數）`;
      else b.textContent = left ? "全部生產到完結" : "全部完成";
      b.disabled = pending || reoutlining || (!mode && !left);
    };
    $("#mode").addEventListener("click", (e) => {
      const c = e.target.closest(".chip"); if (!c) return;
      $("#mode").querySelectorAll(".chip").forEach((x) => x.setAttribute("aria-pressed", String(x === c)));
      mode = c.dataset.v;
      $("#mode-hint").textContent = MODES.find((m) => m[0] === mode)[2];
      allLabel();
    });
    allLabel();
    const send = async (btn, cmd, reset) => {
      btn.disabled = true; btn.textContent = "送出中…";
      try {
        await gh(`/repos/{repo}/issues/${num}/comments`, { method: "POST", body: { body: cmd } });
        toast("已下單，工廠開工");
        setTimeout(() => renderSeries(num).catch(handleError), 1500);
      } catch (e) { handleError(e); reset(); btn.disabled = false; }
    };
    go.addEventListener("click", () => {
      const a = Number(from.value), b = Math.max(a, Number(to.value));
      send(go, `/produce ${a === b ? a : `${a}-${b}`}${$("#video").checked ? " --video" : ""}${mode ? " " + mode : ""}`, label);
    });
    $("#go-all").addEventListener("click", (e) => {
      if (mode && !confirm(`確定要對全部 ${total} 集「${MODES.find((m) => m[0] === mode)[1]}」嗎？`)) return;
      send(e.currentTarget, `/produce all${$("#video").checked ? " --video" : ""}${mode ? " " + mode : ""}`, allLabel);
    });
    $("#poster-pick").addEventListener("click", (e) => {
      const c = e.target.closest(".chip"); if (!c || postering) return;
      send(c, `/poster ${c.dataset.n}`, () => { c.textContent = `用第 ${c.dataset.n} 張`; });
    });
    $("#poster-regen").addEventListener("click", (e) => {
      if (!confirm("重新畫 4 張海報底圖？約需 5～10 分鐘。")) return;
      send(e.currentTarget, "/poster --regen", () => { e.currentTarget.textContent = "重畫 4 張底圖"; });
    });
    $("#go-re").addEventListener("click", (e) => {
      const n = Number($("#re-eps").value), sec = Number($("#re-secs").value);
      if (n === total && sec === bible.episode_seconds) { toast("集數與長度沒有變動"); return; }
      if (!confirm(`改成 ${n} 集 × ${sec} 秒？會重排大綱並清除所有舊分鏡。`)) return;
      send(e.currentTarget, `/reoutline ${n} ${sec}`, () => { e.currentTarget.textContent = "重排大綱"; });
    });
    $("#film").addEventListener("click", (e) => {
      const f = e.target.closest(".frame.done");
      if (f) openPlayer(files, titles, Number(f.dataset.ep), bible.title);
    });
    const firstTodo = $("#film .frame.queued") || $("#film .frame:not(.done)");
    if (firstTodo) $("#film").scrollLeft = Math.max(0, firstTodo.offsetLeft - 220);
  }
  // 企劃中或生產中時自動刷新
  if ((!bible || pending || reoutlining || postering) && location.hash === `#/s/${num}`) {
    poll = setInterval(() => { if (!document.hidden && location.hash === `#/s/${num}`) renderSeries(num).catch(handleError); }, 60000);
  }
}

// ── 播放器 ──────────────────────────────────────────────
function openPlayer(files, titles, ep, show) {
  const eps = Object.keys(files).map(Number).filter((k) => files[k].mp4).sort((a, b) => a - b);
  const sheet = document.createElement("div");
  sheet.className = "sheet";
  sheet.setAttribute("role", "dialog");
  sheet.setAttribute("aria-modal", "true");
  const close = () => { sheet.remove(); removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  const draw = () => {
    const i = eps.indexOf(ep);
    sheet.innerHTML = `
      <header><div class="t">${esc(show)}・第 ${ep} 集 ${esc(titles[ep] || "")}</div>
        <button class="icon-btn" aria-label="關閉">✕</button></header>
      <video src="${files[ep].mp4}" controls autoplay playsinline ${files[ep].jpg ? `poster="${files[ep].jpg}"` : ""}></video>
      <footer>
        <button class="btn ghost" data-go="-1" ${i <= 0 ? "disabled" : ""}>上一集</button>
        <a class="btn ghost" href="${files[ep].mp4}" download>下載</a>
        <button class="btn ghost" data-go="1" ${i >= eps.length - 1 ? "disabled" : ""}>下一集</button>
      </footer>`;
    $(".icon-btn", sheet).onclick = close;
    sheet.querySelectorAll("[data-go]").forEach((b) => b.onclick = () => { ep = eps[i + Number(b.dataset.go)]; draw(); });
    $("video", sheet).onended = () => { if (i < eps.length - 1) { ep = eps[i + 1]; draw(); } };
    $(".icon-btn", sheet).focus();
  };
  addEventListener("keydown", onKey);
  document.body.append(sheet);
  draw();
}

// ── 頁面：設定 ──────────────────────────────────────────
let installEvt = null;
addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); installEvt = e; });

function renderSettings() {
  const first = !cfg.token;
  const ios = /iphone|ipad|ipod/i.test(navigator.userAgent);
  const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  view.innerHTML = `
    ${first ? `<img class="setup-logo" src="icons/logo-640.jpg" alt="SAGA.ai">
      <h1>連上您的短劇工廠</h1>
      <p class="lede">這個 App 透過 GitHub 遙控 saga.ai。只需要貼一次金鑰，它只會存在這台裝置上。</p>` :
      `<h1>設定</h1>`}
    <form id="cfg">
      <div class="field">
        <label for="token">GitHub 金鑰</label>
        <input id="token" type="password" autocomplete="off" placeholder="github_pat_…" value="${esc(cfg.token)}">
      </div>
      <div class="field">
        <label for="repo">工廠所在的 repo</label>
        <input id="repo" type="text" autocomplete="off" value="${esc(cfg.repo)}">
      </div>
      <button class="btn primary" type="submit">${first ? "連線" : "儲存"}</button>
    </form>
    <h2>如何取得金鑰</h2>
    <ol class="steps">
      <li>開啟 <a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener">GitHub 建立 fine-grained token</a></li>
      <li><b>Repository access</b> 選 Only select repositories → <b>saga.ai</b></li>
      <li><b>Permissions</b>：Issues 設為 Read and write；Contents、Actions 設為 Read-only</li>
      <li>產生後複製，貼到上面</li>
    </ol>
    ${standalone ? "" : `<h2>安裝成 App</h2>
      ${ios ? `<p class="muted">用 Safari 開啟這頁，點下方的分享按鈕，選「加入主畫面」。</p>`
            : `<p class="muted" id="install-hint">用 Chrome 開啟時，網址列右側或選單裡會出現「安裝應用程式」。</p>
               <button class="btn ghost" id="install" type="button" hidden>安裝 SAGA.ai</button>`}`}
    ${first ? "" : `<h2>這台裝置</h2><button class="btn ghost" id="logout" type="button">移除金鑰</button>`}`;

  if (installEvt) {
    const b = $("#install");
    if (b) {
      b.hidden = false;
      b.onclick = async () => { installEvt.prompt(); await installEvt.userChoice; installEvt = null; b.hidden = true; };
    }
  }
  $("#logout")?.addEventListener("click", () => { store.del("token"); toast("金鑰已從這台裝置移除"); route(); });
  $("#cfg").addEventListener("submit", async (e) => {
    e.preventDefault();
    const token = $("#token").value.trim(), repo = $("#repo").value.trim().replace(/^https:\/\/github\.com\//, "");
    if (!token || !/^[\w.-]+\/[\w.-]+$/.test(repo)) { toast("請填入金鑰與正確的 repo（帳號/名稱）", true); return; }
    const btn = $("button[type=submit]", e.target);
    btn.disabled = true; btn.textContent = "驗證中…";
    store.set("token", token); store.set("repo", repo);
    try {
      const r = await gh(`/repos/${repo}`);
      if (!r.permissions?.push && !r.permissions?.triage) throw new ApiError(403, "這把金鑰沒有這個 repo 的寫入權限");
      toast("已連線");
      location.hash = "#/series";
    } catch (err) {
      store.del("token");
      handleError(err);
      btn.disabled = false; btn.textContent = first ? "連線" : "儲存";
    }
  });
}

// ── 啟動 ────────────────────────────────────────────────
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
setInterval(() => { if (!document.hidden) refreshFactory(); }, 60000);
route();
