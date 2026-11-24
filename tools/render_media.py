"""Render post media from the working product with Playwright.

Five kinds are rendered here (a sixth, "file", is copied by build.py):

  card        a 1600x900 branded card: headline, optional kicker, and an
              optional live capture of the product inset on the right
  screenshot  a capture of a page or one element of the built site
  signal      a number-first card: one figure, what it counts, an optional
              bar series, an optional ticker chip; all taken from the post file
  evidence    a live capture of the product filling the frame, with one
              element marked and a one-line caption
  video       a short scripted screen recording (tools/video/<name>.json),
              encoded as H.264 MP4

Captures come from the site as built in dist/, served locally, talking to the
live API. Nothing here is mocked: if the product shows it, the media shows it.
"""
from __future__ import annotations

import base64
import functools
import html
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

VIEWPORTS = {"desktop": (1280, 800), "phone": (390, 844)}
VIDEO_VIEWPORTS = {"desktop": (1280, 720), "phone": (360, 640)}  # phone is encoded at 2x: 720x1280
IMAGE_LIMIT = 4_500_000
ROOT = Path(__file__).resolve().parents[1]

CARD = """<!doctype html><html><head><meta charset="utf-8"><style>
@font-face{font-family:Geist;src:url('/geist.woff2') format('woff2');font-weight:400 700}
*{box-sizing:border-box;margin:0}
body{width:1600px;height:900px;background:#05070b;color:#f4f3ee;font-family:Geist,Arial,sans-serif;overflow:hidden;position:relative}
.grid{position:absolute;inset:0;background:linear-gradient(rgba(255,255,255,.045) 1px,transparent 1px) 0 0/80px 80px,linear-gradient(90deg,rgba(255,255,255,.045) 1px,transparent 1px) 0 0/80px 80px;-webkit-mask-image:radial-gradient(ellipse at 70% 30%,#000 10%,transparent 75%)}
.glow{position:absolute;inset:0;background:radial-gradient(900px 620px at 82% 12%,rgba(255,148,87,.30),transparent 70%),radial-gradient(700px 520px at 4% 100%,rgba(109,92,255,.20),transparent 70%)}
.frame{position:absolute;inset:0;padding:72px 80px;display:flex;flex-direction:column;justify-content:space-between}
.brand{display:flex;align-items:center;font-size:40px;font-weight:650;letter-spacing:-1.8px}
.brand i{color:#ff9457;font-style:normal}
.mark{height:36px;width:41px;margin-right:16px;background:linear-gradient(90deg,#ff9457 0 19%,transparent 19% 39%,#ff9457 39% 59%,transparent 59% 79%,#ff9457 79%);clip-path:polygon(0 65%,19% 65%,19% 100%,39% 100%,39% 32%,59% 32%,59% 100%,79% 100%,79% 0,100% 0,100% 100%,0 100%)}
.copy{max-width:__COPY_WIDTH__}
.kicker{font:22px/1.4 'Courier New',monospace;letter-spacing:4px;color:#ff9457;text-transform:uppercase;margin-bottom:26px}
h1{font-size:__HEADLINE_SIZE__;line-height:1.02;letter-spacing:-.055em;font-weight:650;text-wrap:balance;background:linear-gradient(180deg,#fff 30%,#ffc7a6 100%);-webkit-background-clip:text;background-clip:text;color:transparent;padding-bottom:.08em}
.foot{display:flex;justify-content:space-between;gap:28px;font-size:24px;color:#aeb6c1;max-width:__FOOT_WIDTH__}
.foot b{color:#f2f1ec;font-weight:500}
.shot{position:absolute;right:__SHOT_RIGHT__;top:96px;bottom:-60px;width:__SHOT_WIDTH__;border:1.5px solid #2c3442;border-radius:__SHOT_RADIUS__;overflow:hidden;background:#101319;box-shadow:0 30px 140px rgba(255,148,87,.26)}
.shot{padding:__SHOT_PAD__}
.shot img{display:block;width:100%;height:auto}
.shot::after{content:"";position:absolute;inset:0;background:linear-gradient(180deg,transparent 62%,#05070b 100%)}
.bars{position:absolute;right:80px;bottom:150px;width:420px;height:300px;filter:drop-shadow(0 0 60px rgba(255,148,87,.55));background:linear-gradient(90deg,#ff9457 0 19%,transparent 19% 39%,#ff9457 39% 59%,transparent 59% 79%,#ff9457 79%);clip-path:polygon(0 65%,19% 65%,19% 100%,39% 100%,39% 32%,59% 32%,59% 100%,79% 100%,79% 0,100% 0,100% 100%,0 100%)}
</style></head><body><div class="grid"></div><div class="glow"></div>__VISUAL__<div class="frame">
<div class="brand"><span class="mark"></span>__NAME__<i>.</i></div>
<div class="copy">__KICKER__<h1>__HEADLINE__</h1></div>
<div class="foot"><b>__SITE__</b><span>__CREDIT__</span></div>
</div></body></html>"""

# Shared look of the data cards: near-black, a fine grid, one glowing accent.
TONES = {"watch": "#ff9457", "ship": "#7dff9b", "alert": "#ff5d6c", "info": "#5ad7ff"}
BASE = """@font-face{font-family:Geist;src:url('/geist.woff2') format('woff2');font-weight:400 700}
*{box-sizing:border-box;margin:0}
body{width:1600px;height:900px;background:#05070b;color:#f4f3ee;font-family:Geist,Arial,sans-serif;overflow:hidden;position:relative;--tone:__TONE__}
.grid{position:absolute;inset:0;background:linear-gradient(rgba(255,255,255,.045) 1px,transparent 1px) 0 0/80px 80px,linear-gradient(90deg,rgba(255,255,255,.045) 1px,transparent 1px) 0 0/80px 80px;-webkit-mask-image:radial-gradient(ellipse at 70% 30%,#000 10%,transparent 75%)}
.glow{position:absolute;inset:0;background:radial-gradient(900px 620px at 82% 12%,color-mix(in srgb,var(--tone) 34%,transparent),transparent 70%),radial-gradient(700px 520px at 4% 100%,rgba(109,92,255,.20),transparent 70%)}
.brand{display:flex;align-items:center;font-size:34px;font-weight:650;letter-spacing:-1.4px}
.brand i{color:#ff9457;font-style:normal}
.mark{height:30px;width:34px;margin-right:13px;background:linear-gradient(90deg,#ff9457 0 19%,transparent 19% 39%,#ff9457 39% 59%,transparent 59% 79%,#ff9457 79%);clip-path:polygon(0 65%,19% 65%,19% 100%,39% 100%,39% 32%,59% 32%,59% 100%,79% 100%,79% 0,100% 0,100% 100%,0 100%)}
.chip{display:flex;align-items:baseline;gap:16px;padding:12px 24px;border:1.5px solid var(--tone);border-radius:999px;background:color-mix(in srgb,var(--tone) 13%,transparent);box-shadow:0 0 46px color-mix(in srgb,var(--tone) 38%,transparent);font:600 34px 'Courier New',monospace;letter-spacing:-.5px}
.chip span{font-size:22px;font-weight:400;color:#b9c1cc}
"""

SIGNAL = """<!doctype html><html><head><meta charset="utf-8"><style>__BASE__
.frame{position:absolute;inset:0;padding:60px 76px 54px;display:flex;flex-direction:column;justify-content:space-between}
.top{display:flex;justify-content:space-between;align-items:center}
.mid{display:flex;align-items:flex-end;gap:44px}
.num{font-size:__NUMBER_SIZE__;font-weight:700;letter-spacing:-.065em;line-height:.8;padding-right:.05em;background:linear-gradient(180deg,#fff 18%,var(--tone) 96%);-webkit-background-clip:text;background-clip:text;color:transparent;filter:drop-shadow(0 0 54px color-mix(in srgb,var(--tone) 42%,transparent))}
.what{padding-bottom:10px;min-width:0}
.label{font-size:66px;line-height:1.02;font-weight:600;letter-spacing:-.045em;text-wrap:balance}
.detail{margin-top:18px;font:26px/1.35 'Courier New',monospace;color:#b9c1cc}
.chart{margin-top:6px}
.bars{display:flex;align-items:flex-end;gap:5px;height:150px;border-bottom:1.5px solid rgba(255,255,255,.22)}
.bars i{flex:1;min-height:3px;border-radius:3px 3px 0 0;background:linear-gradient(180deg,var(--tone),color-mix(in srgb,var(--tone) 35%,transparent))}
.bars i.zero{background:rgba(255,255,255,.10)}
.bars i.peak{background:linear-gradient(180deg,#fff,var(--tone));box-shadow:0 0 30px var(--tone)}
.axis{display:flex;justify-content:space-between;margin-top:12px;font:20px 'Courier New',monospace;letter-spacing:1px;color:#8d96a3;text-transform:uppercase}
.foot{display:flex;justify-content:space-between;font-size:22px;color:#aeb6c1}
.foot b{color:#f4f3ee;font-weight:500}
</style></head><body><div class="grid"></div><div class="glow"></div><div class="frame">
<div class="top"><div class="brand"><span class="mark"></span>__NAME__<i>.</i></div>__CHIP__</div>
<div class="mid"><div class="num">__NUMBER__</div><div class="what"><p class="label">__LABEL__</p>__DETAIL__</div></div>
__CHART__
<div class="foot"><b>__SITE__</b><span>__CREDIT__</span></div>
</div></body></html>"""

EVIDENCE = """<!doctype html><html><head><meta charset="utf-8"><style>__BASE__
.window{position:absolute;left:150px;right:150px;top:72px;bottom:-30px;padding:34px 40px 0;border:1.5px solid #2c3442;border-radius:20px 20px 0 0;overflow:hidden;background:#101319;box-shadow:0 0 0 1px rgba(255,255,255,.04),0 30px 140px color-mix(in srgb,var(--tone) 30%,transparent)}
.window img{display:block;width:100%;height:auto}
.shade{position:absolute;left:0;right:0;bottom:0;height:330px;background:linear-gradient(180deg,transparent,#05070b 62%)}
.tag{position:absolute;right:190px;top:52px;padding:8px 14px;border-radius:6px;background:#05070b;border:1px solid rgba(255,255,255,.16);font:19px 'Courier New',monospace;letter-spacing:2px;color:#d7dde6}
.tag::before{content:"";display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:10px;background:var(--tone);box-shadow:0 0 14px var(--tone)}
.bar{position:absolute;left:76px;right:76px;bottom:50px;display:flex;align-items:center;gap:34px}
.caption{flex:1;min-width:0;font-size:46px;line-height:1.08;font-weight:600;letter-spacing:-.04em;text-wrap:balance}
</style></head><body><div class="grid"></div><div class="glow"></div>
<div class="window"><img src="__SHOT__"></div><div class="shade"></div><div class="tag">LIVE CAPTURE &middot; __SITE__</div>
<div class="bar"><div class="brand"><span class="mark"></span>__NAME__<i>.</i></div><p class="caption">__CAPTION__</p>__CHIP__</div>
</body></html>"""

# Draw attention to one element before a capture, the way a marker pen would.
HIGHLIGHT_JS = """([selector, colour]) => {
  const node = document.querySelector(selector);
  if (!node) return false;
  node.scrollIntoView({block: 'center'});
  Object.assign(node.style, {padding: '16px 18px', margin: '10px 0', borderRadius: '10px', borderBottomColor: 'transparent',
    boxShadow: 'inset 0 0 0 3px ' + colour + ', 0 0 40px ' + colour + '55', background: colour + '12'});
  return true;
}"""

CAPTION_JS = """message => {
  let bar = document.getElementById('demo-caption');
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'demo-caption';
    Object.assign(bar.style, {position:'fixed',bottom:'14px',left:'50%',transform:'translateX(-50%)',padding:'10px 14px',
      background:'#101319',border:'1px solid #ff9457',color:'#f2f1ec',font:'500 15px Geist,Arial,sans-serif',
      maxWidth:'92vw',width:'max-content',textAlign:'center',zIndex:'999',borderRadius:'4px'});
    document.body.append(bar);
  }
  bar.textContent = message;
}"""


class _Handler(http.server.SimpleHTTPRequestHandler):
    pages: dict = {}

    def do_GET(self):
        page = self.pages.get(self.path)
        if page is None:
            return super().do_GET()
        raw = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


def find_ffmpeg() -> str:
    candidate = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if candidate:
        return candidate
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise RuntimeError("video media needs ffmpeg with libx264 (install ffmpeg or `pip install imageio-ffmpeg`)") from None


class Renderer:
    def __init__(self, out: Path):
        from playwright.sync_api import sync_playwright
        import tomllib
        self.out = out
        data = tomllib.loads((ROOT / "project.toml").read_text(encoding="utf-8"))["project"]
        self.project = {"name": data["name"], "site": data["site"], "credit": data.get("credit", "")}
        handler = functools.partial(_Handler, directory=str(out))
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch()

    def close(self):
        try:
            self.browser.close()
            self.playwright.stop()
        finally:
            self.server.shutdown()
            self.server.server_close()

    # -- helpers ---------------------------------------------------------
    def _open(self, context, path, wait_for=None):
        page = context.new_page()
        page.goto(self.base + path, wait_until="load")
        if wait_for:
            page.locator(wait_for).first.wait_for(timeout=25_000)
        else:
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                pass
        page.wait_for_timeout(500)
        return page

    def _capture(self, path, viewport="desktop", selector=None, wait_for=None, highlight=None, colour="#ff9457") -> bytes:
        width, height = VIEWPORTS[viewport]
        context = self.browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=2,
                                           color_scheme="dark", reduced_motion="reduce")
        try:
            page = self._open(context, path, wait_for)
            target = page.locator(selector).first if selector else page
            if selector:
                # One element is being shown: keep the page's sticky header and progress line out of the picture.
                page.add_style_tag(content=".header{position:static!important}.progress{display:none!important}")
                target.scroll_into_view_if_needed()
            if highlight:
                # Optional: when the element is not on the page today, the capture is simply left unmarked.
                page.evaluate(HIGHLIGHT_JS, [highlight, colour])
                page.wait_for_timeout(150)
            raw = target.screenshot(type="png")
            if len(raw) > IMAGE_LIMIT:
                raw = target.screenshot(type="jpeg", quality=88)
            return raw
        finally:
            context.close()

    @staticmethod
    def _save(raw: bytes, target_dir: Path, index: int) -> Path:
        suffix = ".png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
        target = target_dir / f"{index}{suffix}"
        target.write_bytes(raw)
        return target

    # -- kinds -----------------------------------------------------------
    def render(self, post: dict, item: dict, index: int, target_dir: Path) -> Path:
        kind = item["kind"]
        if kind == "screenshot":
            raw = self._capture(item["path"], item.get("viewport", "desktop"), item.get("selector"), item.get("wait_for"))
            return self._save(raw, target_dir, index)
        if kind == "card":
            return self._save(self._card(item), target_dir, index)
        if kind == "signal":
            return self._save(self._signal(item), target_dir, index)
        if kind == "evidence":
            return self._save(self._evidence(item), target_dir, index)
        if kind == "video":
            return self._video(item, target_dir, index)
        raise ValueError(f"{post['id']}: cannot render media kind {kind}")

    def _card(self, item: dict) -> bytes:
        visual = '<div class="bars"></div>'
        copy_width, shot_width, foot_width, shot_pad = "1040px", "0", "none", "0"
        shot_right, shot_radius = "-40px", "18px 0 0 0"  # a whole page bleeds off the right edge
        headline = item["headline"]
        size = "132px" if len(headline) <= 26 else "108px" if len(headline) <= 48 else "84px"
        if item.get("capture"):
            viewport = item.get("viewport", "desktop")
            shot = self._capture(item["capture"], viewport, item.get("selector"), item.get("wait_for"))
            mime = "image/png" if shot[:4] == b"\x89PNG" else "image/jpeg"
            shot_width = "560px" if viewport == "phone" else "860px"
            copy_width = "860px" if viewport == "phone" else "620px"
            foot_width = copy_width  # keep the footer clear of the inset capture
            # A single element has no margin of its own; a whole page already does.
            if item.get("selector"):
                # One element is shown whole, as a panel inside the card's margin.
                shot_pad, shot_right, shot_radius = "30px 34px 0 34px", "64px", "18px 18px 0 0"
            size = "104px" if len(headline) <= 22 else "84px" if len(headline) <= 44 else "66px"
            visual = f'<div class="shot"><img src="data:{mime};base64,{base64.b64encode(shot).decode()}"></div>'
        kicker = f'<p class="kicker">{html.escape(item["kicker"])}</p>' if item.get("kicker") else ""
        site = self.project["site"].split("://", 1)[-1].rstrip("/")
        page_html = (CARD.replace("__VISUAL__", visual).replace("__COPY_WIDTH__", copy_width)
                     .replace("__SHOT_WIDTH__", shot_width).replace("__FOOT_WIDTH__", foot_width).replace("__SHOT_PAD__", shot_pad).replace("__SHOT_RIGHT__", shot_right).replace("__SHOT_RADIUS__", shot_radius).replace("__HEADLINE_SIZE__", size)
                     .replace("__NAME__", html.escape(self.project["name"].lower()))
                     .replace("__KICKER__", kicker).replace("__HEADLINE__", html.escape(headline).replace("\\n", "<br>"))
                     .replace("__SITE__", html.escape(site)).replace("__CREDIT__", html.escape(self.project.get("credit", ""))))
        route = f"/__card/{len(_Handler.pages)}"
        _Handler.pages[route] = page_html
        context = self.browser.new_context(viewport={"width": 1600, "height": 900}, device_scale_factor=1)
        try:
            page = context.new_page()
            page.goto(self.base + route, wait_until="load")
            page.evaluate("document.fonts.ready")
            page.wait_for_timeout(300)
            return page.screenshot(type="png")
        finally:
            context.close()

    def _page_image(self, page_html: str) -> bytes:
        """Screenshot a 1600x900 page built from one of the templates above."""
        route = f"/__card/{len(_Handler.pages)}"
        _Handler.pages[route] = page_html
        context = self.browser.new_context(viewport={"width": 1600, "height": 900}, device_scale_factor=1)
        try:
            page = context.new_page()
            page.goto(self.base + route, wait_until="load")
            page.evaluate("document.fonts.ready")
            page.wait_for_timeout(300)
            return page.screenshot(type="png")
        finally:
            context.close()

    def _common(self, template: str, item: dict) -> str:
        tone = TONES.get(item.get("tone", "watch"), TONES["watch"])
        chip = ""
        if item.get("ticker"):
            subject = f'<span>{html.escape(item["subject"])}</span>' if item.get("subject") else ""
            chip = f'<div class="chip">{html.escape(item["ticker"])}{subject}</div>'
        site = self.project["site"].split("://", 1)[-1].rstrip("/")
        return (template.replace("__BASE__", BASE).replace("__TONE__", tone).replace("__CHIP__", chip)
                .replace("__NAME__", html.escape(self.project["name"].lower())).replace("__SITE__", html.escape(site))
                .replace("__CREDIT__", html.escape(self.project.get("credit", ""))))

    def _signal(self, item: dict) -> bytes:
        """A number-first card. Everything on it comes from the post file, so it renders the same on any day."""
        number = str(item["number"])
        size = "400px" if len(number) <= 2 else "330px" if len(number) == 3 else "270px" if len(number) == 4 else "210px" if len(number) <= 6 else "160px"
        chart = ""
        series = [max(0.0, float(value)) for value in item.get("series", [])]
        if series:
            top = max(series) or 1.0
            peak = series.index(max(series))
            bars = "".join(f'<i class="{"zero" if value == 0 else "peak" if index == peak else ""}" style="height:{max(2.0, 100 * value / top):.1f}%"></i>'
                           for index, value in enumerate(series))
            axis = "".join(f"<span>{html.escape(str(part))}</span>" for part in item.get("axis", []))
            chart = f'<div class="chart"><div class="bars">{bars}</div><div class="axis">{axis}</div></div>'
        detail = f'<p class="detail">{html.escape(item["detail"])}</p>' if item.get("detail") else ""
        page_html = (self._common(SIGNAL, item).replace("__NUMBER_SIZE__", size).replace("__NUMBER__", html.escape(number))
                     .replace("__LABEL__", html.escape(item["label"])).replace("__DETAIL__", detail).replace("__CHART__", chart))
        return self._page_image(page_html)

    def _evidence(self, item: dict) -> bytes:
        """The product itself, captured live, with one element marked and a one-line caption."""
        tone = TONES.get(item.get("tone", "watch"), TONES["watch"])
        shot = self._capture(item["capture"], item.get("viewport", "desktop"), item.get("selector"), item.get("wait_for"),
                             item.get("highlight"), tone)
        mime = "image/png" if shot[:4] == b"\x89PNG" else "image/jpeg"
        page_html = (self._common(EVIDENCE, item).replace("__SHOT__", f"data:{mime};base64,{base64.b64encode(shot).decode()}")
                     .replace("__CAPTION__", html.escape(item["caption"])))
        return self._page_image(page_html)

    def _video(self, item: dict, target_dir: Path, index: int) -> Path:
        script = json.loads((ROOT / "tools" / "video" / (item["script"] + ".json")).read_text(encoding="utf-8"))
        viewport = item.get("viewport", script.get("viewport", "phone"))
        width, height = VIDEO_VIEWPORTS[viewport]
        with tempfile.TemporaryDirectory() as scratch:
            # The recorder captures CSS pixels whatever the pixel density, so the
            # phone layout is recorded at 360x640 and scaled to 720x1280 when encoded.
            context = self.browser.new_context(viewport={"width": width, "height": height}, color_scheme="dark",
                                               record_video_dir=scratch, record_video_size={"width": width, "height": height})
            page = context.new_page()
            try:
                for step in script["steps"]:
                    (action, value), = step.items()
                    if action == "goto":
                        page.goto(self.base + value, wait_until="load")
                    elif action == "wait_for":
                        page.locator(value).first.wait_for(timeout=25_000)
                    elif action == "wait":
                        page.wait_for_timeout(int(value))
                    elif action == "click":
                        page.locator(value).first.click()
                    elif action == "scroll_to":
                        page.locator(value).first.evaluate("element => element.scrollIntoView({block: 'start', behavior: 'instant'})")
                    elif action == "scroll_top":
                        page.evaluate("scrollTo(0, 0)")
                    elif action == "caption":
                        page.evaluate(CAPTION_JS, value)
                    else:
                        raise ValueError(f"unknown video step '{action}' in {item['script']}.json")
            finally:
                recording = page.video
                context.close()
            source = Path(recording.path())
            target = target_dir / f"{index}.mp4"
            scale = "scale=iw*2:ih*2:flags=lanczos" if viewport == "phone" else "scale=trunc(iw/2)*2:trunc(ih/2)*2"
            command = [find_ffmpeg(), "-y", "-loglevel", "error", "-i", str(source), "-an", "-vf", scale, "-r", "30",
                       "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(target)]
            subprocess.run(command, check=True, timeout=300)
        return target
