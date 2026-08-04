#!/usr/bin/env python3
"""contact_sheet — generate a 4-col grid thumbnail of all delivered clips for at-a-glance review.

One representative frame per clip at ~1.5s (past head-trim, into the hook so split-screen
opens are visible), padded to 9:16 with black, labeled with the short name + an extension-
based color tag (gold = Q&A clip, white = monologue/highlight).

USE WHEN: a batch of clips has been delivered and you want a single image the human can
scan in 3 seconds to spot which clips look off (wrong cam, frozen frame, missing captions,
random audience face on a guest panel). Saves the human from opening 16 separate mp4s.

Usage:
  python3 contact_sheet.py <delivery_dir> [--out PATH] [--cols 4] [--frame-at 1.5]

Defaults:
  --cols 4              4-col grid (auto rows)
  --frame-at 1.5        seconds into each clip; pick > head-trim but < cam-cut, so split
                        opens stay visible. Lower this if many clips are < 5s.
  --out                 <delivery_dir>/CONTACT_SHEET.jpg

Conventions:
  - Files matching SPEAKER_QA_* (or any name containing '_QA_') get the gold label = Q&A
  - All other SPEAKER_* files get the white label = monologue/highlight
  - Files are sorted Q&A first (alphabetical), then highlights (alphabetical)
"""
from __future__ import annotations
import argparse, subprocess, tempfile, os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


THUMB_W, THUMB_H = 360, 640
PAD = 10
TITLE_H = 50


def classify(name: str) -> str:
    """Q&A clip vs monologue highlight based on filename convention."""
    return "qa" if "_QA_" in name else "hl"


def short_label(name: str) -> str:
    """SPEAKER_HOTLINE_Tier1DAY2_Foo_Operator_..._V1.mp4 -> H_Foo
       SPEAKER_QA_Tier1DAY2_Foo_Operator_..._V1.mp4 -> Q_Foo"""
    parts = name.split("_")
    if len(parts) < 4: return name[:24]
    prefix = "Q_" if parts[1] == "QA" else "H_"
    # take the title slug (3rd or 4th token depending on the date/session token)
    # Conservative: take the token after the Tier1DAY2 / session id
    body = None
    for i, p in enumerate(parts):
        if p.startswith(("Tier1", "L2", "L3", "DTC")) or p.endswith(("DAY1", "DAY2", "DAY3")):
            body = parts[i + 1] if i + 1 < len(parts) else parts[-1]
            break
    if body is None: body = parts[3]
    return prefix + body[:24]


def extract_frame(clip: Path, at_s: float, thumb_w: int, thumb_h: int) -> str:
    tmp = tempfile.mktemp(suffix=".jpg")
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{at_s:.2f}", "-i", str(clip), "-frames:v", "1",
                    "-vf", f"scale={thumb_w}:{thumb_h}:force_original_aspect_ratio=decrease,"
                           f"pad={thumb_w}:{thumb_h}:(ow-iw)/2:(oh-ih)/2:color=black",
                    "-q:v", "3", tmp], check=True)
    return tmp


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("delivery_dir", type=Path)
    ap.add_argument("--out", type=Path, default=None,
                    help="output path (default: <delivery_dir>/CONTACT_SHEET.jpg)")
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--frame-at", type=float, default=1.5, dest="frame_at",
                    help="seconds into each clip to grab the thumb")
    ap.add_argument("--title", default=None, help="title text in the top bar")
    a = ap.parse_args()

    out_path = a.out or (a.delivery_dir / "CONTACT_SHEET.jpg")
    clips = sorted(a.delivery_dir.glob("SPEAKER_*.mp4"))
    if not clips:
        # Fall back to any mp4 — keeps the tool generic if naming convention changes
        clips = sorted(a.delivery_dir.glob("*.mp4"))
    if not clips:
        raise SystemExit(f"no mp4s in {a.delivery_dir}")

    # Sort: Q&As first, then highlights, alphabetical within each
    qa = [c for c in clips if classify(c.name) == "qa"]
    hl = [c for c in clips if classify(c.name) == "hl"]
    ordered = qa + hl
    n = len(ordered)
    rows = (n + a.cols - 1) // a.cols

    canvas_w = a.cols * THUMB_W + (a.cols + 1) * PAD
    canvas_h = TITLE_H + rows * THUMB_H + (rows + 1) * PAD
    canvas = Image.new("RGB", (canvas_w, canvas_h), (15, 15, 18))
    draw = ImageDraw.Draw(canvas)

    font_title = font_label = None
    for _fp in (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf",
                "/System/Library/Fonts/HelveticaNeue.ttc"):
        try:
            font_title = ImageFont.truetype(_fp, 30)
            font_label = ImageFont.truetype(_fp, 22)
            break
        except IOError:
            continue
    if font_title is None:
        font_title = font_label = ImageFont.load_default()

    title = a.title or f"{a.delivery_dir.parent.parent.name} — {n} clips   (Q_ = Q&A · H_ = highlight)"
    draw.text((PAD, 12), title, fill=(220, 220, 230), font=font_title)

    print(f"Extracting {n} thumbs from {a.delivery_dir}…")
    tmpfiles = []
    try:
        for i, c in enumerate(ordered):
            try:
                tpath = extract_frame(c, a.frame_at, THUMB_W, THUMB_H)
                tmpfiles.append(tpath)
            except Exception as e:
                print(f"  ! {c.name}: {e}")
                continue
            r, col = divmod(i, a.cols)
            x = PAD + col * (THUMB_W + PAD)
            y = TITLE_H + PAD + r * (THUMB_H + PAD)
            img = Image.open(tpath)
            canvas.paste(img, (x, y))
            overlay = Image.new("RGBA", (THUMB_W, 40), (0, 0, 0, 180))
            canvas.paste(overlay, (x, y), overlay)
            kind = classify(c.name)
            color = (240, 215, 0) if kind == "qa" else (240, 240, 240)
            draw.text((x + 8, y + 8), short_label(c.name), fill=color, font=font_label)
    finally:
        for t in tmpfiles:
            try: os.unlink(t)
            except OSError: pass

    canvas.save(out_path, "JPEG", quality=88, optimize=True)
    print(f"Wrote {out_path}  ({out_path.stat().st_size // 1024} KB, {canvas.size[0]}×{canvas.size[1]})")


if __name__ == "__main__":
    main()
