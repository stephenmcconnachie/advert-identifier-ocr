"""Generate a themed reference HTML grid from clipped advert videos.

Scans a directory of clip MP4s (output of ``single_advert_clip``),
extracts the first frame of each, and builds a responsive grid with
thumbnails and filenames.  Clips are ordered by creation time
(detection sequence) with section headers from pipeline state JSONs.
"""

from __future__ import annotations

import base64
import html
import json
import logging
import re
import subprocess
import sys
from datetime import datetime
from io import BytesIO
from pathlib import Path

logger = logging.getLogger(__name__)


def _load_state_map(clips_dir: Path) -> dict[str, tuple[str, int, int]]:
    """Build unique_id -> (video_stem, break_idx, adv_idx) from pipeline
    state files, sorted by state file modification time (detection order).

    Where the same unique_id appears in multiple videos, the LAST
    occurrence wins (the state file that was most recently written).
    """
    state_paths = sorted(
        clips_dir.glob("*_pipeline_state.json"),
        key=lambda p: p.stat().st_mtime,
    )
    result: dict[str, tuple[str, int, int]] = {}
    for sp in state_paths:
        video_stem = sp.stem.replace("_pipeline_state", "")
        try:
            with open(sp) as f:
                state = json.load(f)
        except Exception:
            continue
        for bi, break_data in enumerate(state.get("ad_breaks", [])):
            for ai, adv in enumerate(break_data.get("adverts", [])):
                uid = adv.get("unique_id", "")
                if uid:
                    result[uid] = (video_stem, bi, ai)
    return result


def _frame_data_uri(clip_path: Path, time_seconds: float = 0, width: int = 220) -> str:
    try:
        if time_seconds < 0:
            seek_flag = "-sseof"
            seek_val = f"{time_seconds:.3f}"
        else:
            seek_flag = "-ss"
            seek_val = f"{time_seconds:.3f}"
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                seek_flag,
                seek_val,
                "-i",
                str(clip_path),
                "-vframes",
                "1",
                "-f",
                "image2pipe",
                "-vcodec",
                "png",
                "-",
            ],
            capture_output=True,
            check=True,
            timeout=30,
        )
        from PIL import Image

        img = Image.open(BytesIO(result.stdout))
        w_percent = width / float(img.size[0])
        height = int(float(img.size[1]) * w_percent)
        thumb = img.resize((width, height), Image.LANCZOS)
        thumb = thumb.quantize(colors=128, method=Image.Quantize.MEDIANCUT)
        buf = BytesIO()
        thumb.save(buf, "PNG", optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception as exc:
        logger.warning(
            "Failed to extract frame at %.3fs from %s: %s",
            time_seconds,
            clip_path.name,
            exc,
        )
        return ""


_REFERENCE_CSS = """
html { transition: background 0.3s, color 0.3s; }
:root {
  --bg: #2e3440; --bg-card: #3b4252; --bg-code: #242933;
  --fg: #eceff4; --fg-muted: #81a1c1; --border: #4c566a;
  --accent: #88c0d0; --link: #88c0d0;
}
[data-theme="light"] {
  --bg: #eceff4; --bg-card: #ffffff; --bg-code: #e5e9f0;
  --fg: #2e3440; --fg-muted: #5e81ac; --border: #d8dee9;
  --accent: #5e81ac; --link: #5e81ac;
}
[data-theme="gruvbox"] {
  --bg: #1d2021; --bg-card: #282828; --bg-code: #0d0d0d;
  --fg: #fbf1c7; --fg-muted: #a89984; --border: #665c54;
  --accent: #fabd2f; --link: #8ec07c;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: var(--bg); color: var(--fg); padding: 20px; line-height: 1.5; }
h1 { font-size: 1.5rem; margin-bottom: 4px; }
.subtitle { color: var(--fg-muted); font-size: 0.85rem; margin-bottom: 16px; }
.theme-nav { display: flex; gap: 8px; margin-bottom: 20px; }
.theme-btn { padding: 4px 12px; border: 1px solid var(--border); border-radius: 4px; background: var(--bg-card); color: var(--fg); cursor: pointer; font-size: 0.8rem; }
.theme-btn.active { border-color: var(--accent); color: var(--accent); }
.break-section { margin-bottom: 32px; }
.break-header { font-size: 1.1rem; font-weight: 600; margin-bottom: 8px; padding-bottom: 4px; border-bottom: 1px solid var(--border); color: var(--accent); }
.break-header .video-label { color: var(--fg-muted); font-size: 0.85rem; font-weight: 400; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 20px; }
.card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; transition: border-color 0.2s, box-shadow 0.2s; }
.card:hover { border-color: var(--accent); box-shadow: 0 2px 8px rgba(0,0,0,0.3); }
.card-frames { display: flex; gap: 2px; }
.card-frame { flex: 1; display: block; text-decoration: none; color: inherit; }
.card-frame img { width: 100%; height: auto; display: block; }
.placeholder { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; background: var(--bg-code); padding: 40px 20px; color: var(--fg-muted); }
.placeholder-icon { font-size: 2rem; }
.card-body { padding: 10px 12px 12px; text-align: center; }
.card-id { font-family: "SF Mono", "Fira Code", monospace; font-size: 0.75rem; color: var(--fg-muted); word-break: break-all; margin-bottom: 2px; }
.card-category { font-size: 0.78rem; color: var(--fg-muted); word-break: break-all; margin-bottom: 2px; }
.card-brand { font-weight: 600; font-size: 0.85rem; word-break: break-all; }
.footer { text-align: center; color: var(--fg-muted); font-size: 0.75rem; margin-top: 40px; padding-top: 16px; border-top: 1px solid var(--border); }
"""

_REFERENCE_JS = """
(function() {
  var btns = document.querySelectorAll('.theme-btn');
  var root = document.documentElement;
  function setTheme(t) {
    root.setAttribute('data-theme', t);
    btns.forEach(function(b) { b.classList.toggle('active', b.dataset.theme === t); });
    try { localStorage.setItem('report-theme', t); } catch(e) {}
  }
  var initial = 'dark';
  try {
    var saved = localStorage.getItem('report-theme');
    if (saved) initial = saved;
    else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) initial = 'light';
  } catch(e) {}
  setTheme(initial);
  btns.forEach(function(b) { b.addEventListener('click', function() { setTheme(b.dataset.theme); }); });
})();
"""


def _frame_tag(img_data: str, label: str) -> str:
    if img_data:
        return f'<img src="{img_data}" alt="{label}" loading="lazy">'
    return (
        '<div class="placeholder">'
        '<span class="placeholder-icon">&#x1F5BC;</span>'
        f"</div>"
    )


def _make_card(clip: Path, unique_id: str, category: str, brand: str) -> str:
    first_img = _frame_data_uri(clip, 0)
    last_img = _frame_data_uri(clip, -0.1)
    clip_name_esc = html.escape(clip.name)
    return f"""<div class="card">
  <div class="card-frames">
    <a href="{clip_name_esc}" target="_blank" class="card-frame">
      {_frame_tag(first_img, f"{unique_id} first frame")}
    </a>
    <a href="{clip_name_esc}" target="_blank" class="card-frame">
      {_frame_tag(last_img, f"{unique_id} last frame")}
    </a>
  </div>
  <div class="card-body">
    <div class="card-id">{html.escape(unique_id)}</div>
    <div class="card-category">{html.escape(category)}</div>
    <div class="card-brand">{html.escape(brand)}</div>
  </div>
</div>"""


def generate_reference_html_from_clips(
    clips_dir: Path,
    output_path: Path,
) -> str:
    """Scan *clips_dir* for clip MP4s, extract first/last-frame
    thumbnails, and write a themed reference HTML page.

    Clips are ordered by creation time (detection sequence).  Section
    headers are inserted for each ad break using pipeline state JSONs.

    Returns the HTML string.
    """
    all_mp4s = sorted(
        clips_dir.glob("*.mp4"),
        key=lambda p: p.stat().st_mtime_ns,
    )
    clips = [p for p in all_mp4s if re.match(r"^[A-Z]{2,}\d+_", p.stem)]
    if not clips:
        logger.warning(
            "No advert clip .mp4 files found in %s (filtered %d non-clip mp4s)",
            clips_dir,
            len(all_mp4s),
        )

    state_map = _load_state_map(clips_dir)

    last_group: tuple[str, int] | None = None
    sections: list[str] = []
    current_cards: list[str] = []

    def _flush() -> None:
        if current_cards:
            sections.append('<div class="grid">')
            sections.extend(current_cards)
            sections.append("</div></div>")
            current_cards.clear()

    for clip in clips:
        uid = clip.stem.split("_", 1)[0]
        parts = clip.stem.split("_", 2)
        category = parts[1] if len(parts) > 1 else ""
        brand = parts[2] if len(parts) > 2 else ""

        pos = state_map.get(uid)
        if pos is not None:
            video_stem, bi, ai = pos
            group = (video_stem, bi)
        else:
            group = None

        if group != last_group:
            _flush()
            if group is not None:
                video_stem, bi = group
                sections.append(
                    '<div class="break-section">'
                    f'<div class="break-header">'
                    f"Break {bi + 1} &mdash; "
                    f'<span class="video-label">{html.escape(video_stem)}</span>'
                    f"</div>"
                )
            else:
                sections.append(
                    '<div class="break-section">'
                    '<div class="break-header">Unmatched</div>'
                )
            last_group = group

        current_cards.append(_make_card(clip, uid, category, brand))

    _flush()

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stem_esc = html.escape(clips_dir.name)
    html_str = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Reference Frames &mdash; {stem_esc}</title>
<style>{_REFERENCE_CSS}</style>
</head>
<body>

<h1>Reference Frames &mdash; {stem_esc}</h1>
<div class="subtitle">Generated: {now} &mdash; {len(clips)} advert clip(s)</div>

<div class="theme-nav">
  <button class="theme-btn" data-theme="light">Light</button>
  <button class="theme-btn active" data-theme="dark">Dark</button>
  <button class="theme-btn" data-theme="gruvbox">Gruvbox</button>
</div>

{"".join(sections)}

<div class="footer">Advert Identifier OCR &mdash; Reference Frames</div>

<script>{_REFERENCE_JS}</script>

</body>
</html>"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_str, encoding="utf-8")
    logger.info("Reference HTML written to: %s", output_path)
    return html_str


def main(args: list[str] | None = None) -> int:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate a themed reference HTML grid from clipped advert videos",
    )
    parser.add_argument(
        "--clips-dir",
        type=str,
        required=True,
        help="Directory containing advert clip .mp4 files",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Directory for the reference HTML (default: parent of --clips-dir)",
    )
    parser.add_argument(
        "--video-stem",
        type=str,
        default=None,
        help="Source video filename stem for the HTML filename "
        "(default: clips directory name)",
    )

    parsed = parser.parse_args(args)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler()],
    )

    clips_dir = Path(parsed.clips_dir)
    if not clips_dir.is_dir():
        logger.error("Clips directory not found: %s", clips_dir)
        return 1

    stem = parsed.video_stem or clips_dir.name
    output_dir = Path(parsed.output_dir) if parsed.output_dir else clips_dir.parent
    output_path = output_dir / f"{stem}_reference.html"

    generate_reference_html_from_clips(clips_dir, output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
