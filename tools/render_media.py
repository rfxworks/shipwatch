"""Render post media from the working product with Playwright.

Three kinds are rendered here (a fourth, "file", is copied by build.py):

  card        a 1600x900 branded card: headline, optional kicker, and an
              optional live capture of the product inset on the right
  screenshot  a capture of a page or one element of the built site
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
body{width:1600px;height:900px;background:#101319;color:#f2f1ec;font-family:Geist,Arial,sans-serif;overflow:hidden;position:relative}
.frame{position:absolute;inset:0;padding:72px 80px;display:flex;flex-direction:column;justify-content:space-between}
.brand{display:flex;align-items:center;font-size:40px;font-weight:650;letter-spacing:-1.8px}
.brand i{color:#ff9457;font-style:normal}
.mark{height:36px;width:41px;margin-right:16px;background:linear-gradient(90deg,#ff9457 0 19%,transparent 19% 39%,#ff9457 39% 59%,transparent 59% 79%,#ff9457 79%);clip-path:polygon(0 65%,19% 65%,19% 100%,39% 100%,39% 32%,59% 32%,59% 100%,79% 100%,79% 0,100% 0,100% 100%,0 100%)}
.copy{max-width:__COPY_WIDTH__}
.kicker{font:22px/1.4 'Courier New',monospace;letter-spacing:4px;color:#ff9457;text-transform:uppercase;margin-bottom:26px}
h1{font-size:__HEADLINE_SIZE__;line-height:1.02;letter-spacing:-.055em;font-weight:600;text-wrap:balance}
.foot{display:flex;justify-content:space-between;gap:28px;font-size:24px;color:#aeb6c1;max-width:__FOOT_WIDTH__}
.foot b{color:#f2f1ec;font-weight:500}
.shot{position:absolute;right:__SHOT_RIGHT__;top:96px;bottom:-60px;width:__SHOT_WIDTH__;border:1px solid #303741;border-radius:__SHOT_RADIUS__;overflow:hidden;background:#171c23;box-shadow:-40px 40px 120px rgba(0,0,0,.55)}
.shot{padding:__SHOT_PAD__}
.shot img{display:block;width:100%;height:auto}
.shot::after{content:"";position:absolute;inset:0;background:linear-gradient(180deg,transparent 62%,#101319 100%)}
.bars{position:absolute;right:80px;bottom:150px;width:420px;height:300px;opacity:.9;background:linear-gradient(90deg,#ff9457 0 19%,transparent 19% 39%,#ff9457 39% 59%,transparent 59% 79%,#ff9457 79%);clip-path:polygon(0 65%,19% 65%,19% 100%,39% 100%,39% 32%,59% 32%,59% 100%,79% 100%,79% 0,100% 0,100% 100%,0 100%)}
</style></head><body>__VISUAL__<div class="frame">
<div class="brand"><span class="mark"></span>__NAME__<i>.</i></div>
<div class="copy">__KICKER__<h1>__HEADLINE__</h1></div>
<div class="foot"><b>__SITE__</b><span>__CREDIT__</span></div>
</div></body></html>"""

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

    def _capture(self, path, viewport="desktop", selector=None, wait_for=None) -> bytes:
        width, height = VIEWPORTS[viewport]
        context = self.browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=2,
                                           color_scheme="dark", reduced_motion="reduce")
        try:
            page = self._open(context, path, wait_for)
            target = page.locator(selector).first if selector else page
            if selector:
                target.scroll_into_view_if_needed()
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
