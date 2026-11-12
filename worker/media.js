// SAGA 影片轉送站（Cloudflare Worker，免費額度每天 10 萬次請求）
// 為什麼需要：GitHub Release 下載一律回 Content-Type: application/octet-stream ＋ attachment ＋ nosniff，
// iPhone／iPad 的 Safari 因此拒絕在 <video> 裡播放（電腦版 Chrome 會自己猜格式所以正常）。
// 這裡把同一個檔案轉送出去，但改成正確的 video/mp4、inline，並保留 Range（iOS 播放必須支援分段讀取）。
// 網址格式：https://<worker>/<tag>/<檔名>，例如 /s5/s5-ep01.mp4
const REPO = "eastonesd/eastone.saga";
const TYPES = { mp4: "video/mp4", jpg: "image/jpeg" };

export default {
  async fetch(req) {
    const cors = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Range",
                   "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges" };
    if (req.method === "OPTIONS") return new Response(null, { headers: cors });
    if (req.method !== "GET" && req.method !== "HEAD") return new Response("只接受 GET", { status: 405 });
    const m = new URL(req.url).pathname.match(/^\/([\w.-]+)\/([\w.-]+\.(mp4|jpg))$/);
    if (!m) return new Response("找不到", { status: 404, headers: cors });
    const [, tag, name, ext] = m;
    // 第一步：問 GitHub 真正的檔案位置（簽名網址），不自動跟隨，才能把 Range 帶到最終主機
    const hop = await fetch(`https://github.com/${REPO}/releases/download/${tag}/${name}`, { redirect: "manual" });
    const loc = hop.headers.get("Location");
    if (!loc) return new Response("找不到", { status: 404, headers: cors });
    const fwd = new Headers();
    const range = req.headers.get("Range");
    if (range) fwd.set("Range", range);
    const res = await fetch(loc, { method: req.method, headers: fwd });
    const h = new Headers(cors);
    for (const k of ["Content-Length", "Content-Range", "ETag", "Last-Modified"]) {
      const v = res.headers.get(k); if (v) h.set(k, v);
    }
    h.set("Content-Type", TYPES[ext]);
    h.set("Content-Disposition", `inline; filename="${name}"`);
    h.set("Accept-Ranges", "bytes");
    h.set("Cache-Control", "public, max-age=86400");
    return new Response(res.body, { status: res.status, headers: h });
  },
};
