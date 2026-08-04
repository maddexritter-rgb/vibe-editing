#!/usr/bin/env python3
"""Premium "N. CATEGORY" pill tabs for SPICE listicle captions (Operator 2026-06-05).

One pill per tactic reading "1. OFFER" (number + category on ONE line), persistent over the tactic,
sitting ABOVE the caption line. The SPICE caption layer is untouched.

LOCKED premium build (glass = the Speaker/SF listicle default):
  - true CAPSULE shape (fully rounded ends) via \\p1 vector, anchored TOP-LEFT (\\an7).
  - text INK-CENTERED in the bubble using real font metrics (PIL getbbox + getmetrics) — exact,
    no guessed nudge — so the number+word sit dead-centre both axes and FILL the bubble.
  - a SEPARATE blurred shadow drawing underneath (soft diffuse drop shadow, not a hard \\shad offset).
  - two-tone text (muted/accent number + category), upright refined weights.
  - resolution-aware: reads PlayResX/Y from the .ass and scales geometry (works at 1080 or 4K).

Named styles (--style): glass (default) | mono | ivory.

Usage:
  spice_tabs.py <captions.ass> --clip-end 46.92 --style glass \
      --point "3.38:#1:OFFER" --point "8.02:#2:MARKETING" ... [--y 1140] [--fs N] [--out out.ass]
"""
# ── vibe-editing portable path bootstrap (auto-inserted) ──
import os as _os, sys as _sys
import pathlib as _pl
def _acq_root():
    r = _os.environ.get("VIBE_PIPELINE_ROOT") or _os.environ.get("CLAUDE_PLUGIN_ROOT")
    if r and _os.path.isdir(_os.path.join(r, ".claude-plugin")):
        return r
    d = _os.path.dirname(_os.path.abspath(__file__))
    while d != _os.path.dirname(d):
        if _os.path.isdir(_os.path.join(d, ".claude-plugin")):
            return d
        d = _os.path.dirname(d)
    return _os.path.dirname(_os.path.abspath(__file__))
VIBE_ROOT = _acq_root()
VIBE_SHARED = _os.path.join(VIBE_ROOT, "lib", "_shared")
VIBE_SKILLS = _os.path.join(VIBE_ROOT, "skills")
VIBE_VAULT  = _os.path.join(VIBE_ROOT, "vault")
VIBE_ASSETS = _os.environ.get("VIBE_ASSETS") or _os.path.join(VIBE_ROOT, "assets")
def _acq(p):
    parts = [x for x in str(p).strip("/").split("/") if x]
    if parts and parts[0] == "_shared":
        return _pl.Path(_os.path.join(VIBE_ROOT, "lib", *parts))
    return _pl.Path(_os.path.join(VIBE_SKILLS, *parts))
def _acqv(p):
    return _pl.Path(_os.path.join(VIBE_VAULT, *[x for x in str(p).strip("/").split("/") if x]))
if VIBE_SHARED not in _sys.path:
    _sys.path.insert(0, VIBE_SHARED)
# ── end bootstrap ──
import argparse, os, re
from pathlib import Path
from PIL import ImageFont

FONTDIR = _acq("caption-clips/fonts/free_font")

# ---- premium style presets (geometry tuned in 1080-space; auto-scaled to PlayResX) ----------
# padx/pady = padding around the TEXT INK (not the font box). fs bigger + pad tighter = "filled out".
STYLES = {
    # DARK translucent capsule, white category + warm-gold number, upright Bold, faint light edge  [LOCKED default]
    'glass': dict(family="Montserrat Bold", file="Montserrat-ExtraBold.otf", fs=62,
                  fill="1B1B1D", fill_a="22", numcol="E7B65C", catcol="FFFFFF",
                  padx=40, pady=26, track=0, bord=2.0, bordcol="FFFFFF", borda="C8",
                  shad_dy=10, shad_col="000000", shad_a="64", shad_blur=16, sep=". "),
    # refined WHITE capsule, two-tone (muted-grey number / near-black category)
    'mono':  dict(family="Montserrat Bold", file="Montserrat-ExtraBold.otf", fs=58,
                  fill="FFFFFF", fill_a="00", numcol="9A9A9A", catcol="151515",
                  padx=40, pady=24, track=0, bord=0, bordcol="FFFFFF", borda="FF",
                  shad_dy=8, shad_col="000000", shad_a="78", shad_blur=12, sep=". "),
    # warm IVORY capsule, all near-black Black weight, WIDE editorial tracking
    'ivory': dict(family="Montserrat Black", file="MontserratBlack.otf", fs=54,
                  fill="F6F3EC", fill_a="00", numcol="1A1A1A", catcol="1A1A1A",
                  padx=48, pady=24, track=4, bord=0, bordcol="FFFFFF", borda="FF",
                  shad_dy=6, shad_col="000000", shad_a="84", shad_blur=9, sep=". "),
}


def col(hexs):
    h = hexs.lstrip('#'); return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def s2t(x):
    x = max(0.0, x); h = int(x // 3600); x -= h * 3600; m = int(x // 60); x -= m * 60
    s = int(x); cs = int(round((x - s) * 100))
    if cs == 100: s += 1; cs = 0
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def capsule_tl(W, H, r):
    """Rounded-rect / capsule path, TOP-LEFT origin (0,0)->(W,H), clockwise. r<=min(W,H)/2."""
    r = max(2, min(r, W // 2, H // 2))
    return (f"m {r} 0 l {W - r} 0 b {W} 0 {W} {r} {W} {r} "
            f"l {W} {H - r} b {W} {H} {W - r} {H} {W - r} {H} "
            f"l {r} {H} b 0 {H} 0 {H - r} 0 {H - r} "
            f"l 0 {r} b 0 0 {r} 0 {r} 0")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('ass', type=Path)
    ap.add_argument('--point', action='append', required=True, help='"start:#N:CATEGORY"')
    ap.add_argument('--clip-end', type=float, required=True)
    ap.add_argument('--style', default='glass', choices=list(STYLES))
    ap.add_argument('--y', type=int, default=None, help='pill CENTER Y in PlayRes coords (default ~54%%)')
    ap.add_argument('--fs', type=int, default=None, help='override style font size (1080-space)')
    ap.add_argument('--out', type=Path, default=None)
    a = ap.parse_args()
    S = dict(STYLES[a.style])

    # resolution-aware: scale 1080-tuned geometry to the .ass PlayResX
    ass_text = a.ass.read_text()
    mx = re.search(r'PlayResX:\s*(\d+)', ass_text)
    my = re.search(r'PlayResY:\s*(\d+)', ass_text)
    W_play = int(mx.group(1)) if mx else 1080
    H_play = int(my.group(1)) if my else 1920
    SCALE = W_play / 1080.0
    CX = W_play // 2
    fs = int(round((a.fs or S['fs']) * SCALE))
    PADX = int(round(S['padx'] * SCALE)); PADY = int(round(S['pady'] * SCALE))
    TRK = int(round(S['track'] * SCALE))
    SHAD_DY = int(round(S['shad_dy'] * SCALE)); SHAD_BLUR = max(1, int(round(S['shad_blur'] * SCALE)))
    BORD = round(S['bord'] * SCALE, 1)
    Y = a.y if a.y is not None else int(round(0.5417 * H_play))   # ~1040 @ 1920

    pts = []
    for p in a.point:
        b = p.split(':')
        pts.append((float(b[0]), b[1].lstrip('#').strip(), b[2].strip().upper()))
    pts.sort()
    spans = [(st, (pts[i + 1][0] if i + 1 < len(pts) else a.clip_end), num, cat)
             for i, (st, num, cat) in enumerate(pts)]

    font = ImageFont.truetype(os.path.join(FONTDIR, S['file']), fs)
    asc, desc = font.getmetrics()                # for true vertical ink-centering
    rt, rb = font.getbbox("OFFER")[1], font.getbbox("OFFER")[3]
    INK_H = rb - rt                              # constant pill height from cap-ink
    H = INK_H + 2 * PADY
    R = H // 2                                   # capsule = fully rounded ends

    styles = [
        f"Style: TabBox,Arial,12,&H00FFFFFF,&H00FFFFFF,&H00FFFFFF,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        f"Style: TabTxt,{S['family']},{fs},{col(S['catcol'])},{col(S['catcol'])},&H00FFFFFF,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1",
    ]
    fsp = f"\\fsp{TRK}" if TRK else ""
    cues = []
    for st, en, num, cat in spans:
        label = f"{num}{S['sep']}{cat}"
        n_ch = len(label)
        l, t, r, b = font.getbbox(label)
        extra = TRK * (n_ch - 1)                 # libass \fsp adds spacing BETWEEN chars
        ink_w = (r - l) + extra
        W = int(round(ink_w)) + 2 * PADX
        x0 = CX - W // 2
        y0 = Y - H // 2
        adv = font.getlength(label) + extra
        pos_x = CX + adv / 2.0 - (l + (r - l) / 2.0 + extra / 2.0)
        pos_y = Y + (asc + desc) / 2.0 - (t + b) / 2.0
        path = capsule_tl(W, H, R)
        b0, b1 = s2t(st), s2t(en)
        # layer 0 — soft diffuse shadow (separate blurred drawing, offset down)
        cues.append(f"Dialogue: 0,{b0},{b1},TabBox,,0,0,0,,"
                    f"{{\\an7\\pos({x0},{y0 + SHAD_DY})\\fad(140,0)\\1c{col(S['shad_col'])}\\1a&H{S['shad_a']}&"
                    f"\\blur{SHAD_BLUR}\\bord0\\p1}}{path}{{\\p0}}")
        # layer 1 — crisp pill fill (+ optional faint border)
        bord = (f"\\bord{BORD}\\3c{col(S['bordcol'])}\\3a&H{S['borda']}&" if S['bord'] else "\\bord0")
        cues.append(f"Dialogue: 1,{b0},{b1},TabBox,,0,0,0,,"
                    f"{{\\an7\\pos({x0},{y0})\\fad(140,0)\\1c{col(S['fill'])}\\1a&H{S['fill_a']}&{bord}\\p1}}{path}{{\\p0}}")
        # layer 2 — two-tone label, ink-centred
        txt = (f"{{\\1c{col(S['numcol'])}}}{num}{S['sep']}{{\\1c{col(S['catcol'])}}}{cat}")
        cues.append(f"Dialogue: 2,{b0},{b1},TabTxt,,0,0,0,,"
                    f"{{\\an5\\pos({pos_x:.1f},{pos_y:.1f}){fsp}\\fad(140,0)}}{txt}")

    out = []
    for ln in ass_text.split('\n'):
        out.append(ln)
        if ln.startswith('Style: the reference editor') and 'SpiceNum' not in ln and 'Tab' not in ln:
            out.extend(styles)
        if ln.startswith('Format: Layer, Start'):
            out.extend(cues)
    (a.out or a.ass).write_text('\n'.join(out))
    print(f"tabs[{a.style}]: {len(spans)} capsule pills @ y={Y}, fs={fs}, H={H} (PlayRes {W_play}x{H_play}, scale {SCALE:.2f})")


if __name__ == '__main__':
    main()
