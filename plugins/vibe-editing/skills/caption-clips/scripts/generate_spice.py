#!/usr/bin/env python3
"""Dynamic "spice" pro captions — the reference editor-style multi-weight + voice-color + gentle italics.

Reverse-engineered from the team's best editor (see references/spice_caption_spec.md). Distinct
from the LOCKED single-axis pro_locked/speaker_canon path: this one uses the FULL Montserrat weight
palette as an emphasis dial, GENTLE (un-leaned) italics via \\fax, and per-word voice color.

Pipeline: word-level transcript (PUNCTUATE FIRST) -> SOP chunking (hard break at every sentence
.?! and clause , ; then pack <=3 words AND <=18 displayed chars) -> function-word onset-correction
(hold a caption through a pause Groq folded into the next word) + de-flash min-duration + zero-gap
-> per-word styling -> ASS -> optional burn.

The 4 styling axes (THE LOGIC) come from a per-word STYLE STREAM (the "caption director"),
hand- or LLM-authored:
  COLOR  = whose voice   -> white (Speaker) / yellow #FECB00 (guest or Speaker impersonating someone)
  WEIGHT = vocal stress  -> Light(mute) Regular(base) Medium(soft) Bold(strong) Extrabold(emphasis) Black(payoff)
  ITALIC = quote / reflective / contrast  (gentle \\fax, not synthetic \\i1)
  QUOTES = explicitly role-played speech  (always italic; color follows the voice)
Without a style stream a light default (numbers + an emphasis-word list -> heavier weight) keeps
solo clips reasonable.

Usage:
  generate_spice.py <transcript.json> --out subs.ass [--style director.json]
                    [--burn <source.mp4> --burn-out <final.mp4>]
                    [--number-color FECB00] [--no-onset-correct]

Style-stream JSON (all keys optional):
  { "words": { "<word_index>": {"w":"emphasis","i":true,"c":"guest","q":true}, ... },
    "voice_spans": [ [start_sec, end_sec, "guest"], ... ] }   # color a whole time range one voice
  ("words" may also be the top-level object directly.) w=weight tier, i=italic, c=voice, q=quoted.
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
import argparse
import json
import subprocess
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
import sys as _sys
_sys.path.insert(0, VIBE_SHARED)
try:
    from fast_encode import encoder_args, encoder_args_for   # Brand fast-render standard (VideoToolbox HW, ~4x vs libx264)
except Exception:
    encoder_args = encoder_args_for = None

# Unstressed function words: a long Groq span on one of these = absorbed pause, never emphatic
# stretching, so its real onset is near the span's END. (Kept in sync with generate_ass.py.)
FUNCTION_WORDS = {
    "the", "a", "an", "and", "or", "but", "because", "so", "if", "of", "to", "in", "on", "for",
    "with", "that", "this", "is", "are", "was", "were", "be", "been", "am", "as", "at", "by",
    "from", "into", "than", "then", "when", "while", "you", "i", "it", "we", "they", "he", "she",
    "your", "my", "our", "their", "its", "his", "her", "will", "would", "can", "could", "do",
    "does", "did", "have", "has", "had", "not", "no", "there", "here", "what", "which", "who",
    "just", "like", "about",
}
KEEP_I = {"I", "I'm", "I'll", "I'd", "I've"}
NUMWORDS = {"zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
            "hundred", "thousand", "million", "billion", "trillion", "percent", "dollars", "grand"}


def disp(word: str) -> str:
    """Displayed token: drop terminal . and , (keep ? ! ' $ % # and inner punctuation), keep I-forms,
    and PRESERVE proper-noun / acronym capitalization (Apollo Creed, Bangladesh, AI) — the transcript
    is already lowercased by normalize_simple, so any remaining uppercase is intentional."""
    t = word.strip().rstrip(".,")
    if t in KEEP_I:
        return t
    if any(c.isupper() for c in t):
        return t
    return t.lower()


def bare(word: str) -> str:
    return "".join(c for c in word.lower() if c.isalnum())


def ts(t: float) -> str:
    if t < 0:
        t = 0.0
    h = int(t // 3600); t -= h * 3600
    m = int(t // 60); t -= m * 60
    s = int(t); cs = int(round((t - s) * 100))
    if cs == 100:
        s += 1; cs = 0
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_color(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.strip().lstrip("#")
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def is_number(token: str) -> bool:
    return any(c.isdigit() for c in token) or bare(token) in NUMWORDS


_FONT_CACHE = {}
def line_width_px(text: str, px: int) -> int:
    """Rendered width of `text` at `px` using Montserrat Bold (the widest common weight — a
    conservative estimate so the safe-zone cap never under-shrinks). Falls back to a char-advance
    estimate if PIL/the font isn't available."""
    try:
        from PIL import ImageFont
        f = _FONT_CACHE.get(px)
        if f is None:
            f = ImageFont.truetype(str(SKILL / "fonts" / "free_font" / "Montserrat-ExtraBold.otf"), px)
            _FONT_CACHE[px] = f
        return f.getbbox(text)[2]
    except Exception:
        return int(len(text) * px * 0.52)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript", type=Path, help="Word-level transcript JSON (punctuated first!)")
    ap.add_argument("--preset", type=Path, default=SKILL / "presets" / "spice.json")
    ap.add_argument("--style", type=Path, default=None, help="Per-word style-stream JSON (caption director)")
    ap.add_argument("--out", type=Path, required=True, help="Output .ass")
    ap.add_argument("--burn", type=Path, default=None, help="Source video to burn captions onto")
    ap.add_argument("--burn-out", type=Path, default=None, help="Output MP4 (with --burn)")
    ap.add_argument("--layout", type=Path, default=None,
                    help="Per-frame layout track from caption-app's layout_analyze.py. "
                         "When set, each cue's Y is picked from the segment its midpoint falls in "
                         "(safe_y_pct * frame_height) so captions sit in the seam on split-screen "
                         "and below the face on fullscreen, instead of the preset's static Y.")
    ap.add_argument("--number-color", default=None, help="Hex RRGGBB for numeric tokens (e.g. FECB00)")
    ap.add_argument("--no-onset-correct", action="store_true", default=False)
    ap.add_argument("--min-cue-dur", type=float, default=None,
                    help="Override min on-screen seconds per caption. Lower (~0.25) for fast talkers "
                         "whose captions otherwise lag behind the audio.")
    a = ap.parse_args()

    P = json.loads(a.preset.read_text())
    words = json.loads(a.transcript.read_text())["words"]
    N = len(words)
    if not N:
        print("No words in transcript."); return 1

    # --- style stream ---
    style = json.loads(a.style.read_text()) if (a.style and a.style.exists()) else {}
    perword = style.get("words", style if all(k.lstrip("-").isdigit() for k in style) else {}) if isinstance(style, dict) else {}
    voice_spans = style.get("voice_spans", []) if isinstance(style, dict) else []
    # gloss = {"<word_index>": "definition text"} -> a smaller secondary line UNDER the caption while
    # that word is on screen (for foreign/jargon/rare terms, e.g. raison d'être -> "[life's purpose]").
    gloss = style.get("gloss", {}) if isinstance(style, dict) else {}
    number_color = (a.number_color or P.get("number_color") or "").lstrip("#") or None

    weights = P["weights"]
    default_weight = P.get("default_weight", "base")
    auto = P.get("auto_emphasis", {})
    strong_words = set(w.lower() for w in auto.get("words_strong", []))
    colors = P["colors"]
    default_voice = P.get("default_voice", "speaker")
    # SIZE axis (data-derived from the reference editor's 19 clips): base 100, light/strong/peak bumps.
    sizes = P.get("sizes", {"base": 100, "emph": 125, "strong": 150, "peak": 180})
    auto_size = P.get("auto_size", {})           # default size bumps with no style stream, e.g. {"numbers": "emph"}

    def spec(i):
        return perword.get(str(i), {}) if isinstance(perword, dict) else {}

    def voice_of(i):
        s = spec(i)
        if "c" in s:
            return s["c"]
        st = float(words[i]["start"])
        for span in voice_spans:
            if len(span) >= 3 and span[0] <= st < span[1]:
                return span[2]
        return default_voice

    def weight_of(i):
        s = spec(i)
        if "w" in s:
            return s["w"]
        tok = words[i]["word"]
        if number_color is None and is_number(tok) and auto.get("numbers"):
            return auto["numbers"]
        if bare(tok) in strong_words:
            return "strong"
        return default_weight

    def color_hex(i):
        if number_color and is_number(words[i]["word"]):
            return number_color
        return colors.get(voice_of(i), colors.get("speaker", "FFFFFF"))

    def size_of(i):
        # \fscx/\fscy percent. Style stream 's' = a tier name ("emph"/"strong"/"peak") or a raw
        # number (e.g. 140). Auto-bump numbers when no explicit size (The reference editor bumps numbers/money).
        tier = spec(i).get("s")
        if tier is None and auto_size.get("numbers") and is_number(words[i]["word"]):
            tier = auto_size["numbers"]
        if tier is None:
            return 100
        if isinstance(tier, (int, float)):
            return int(tier)
        return int(sizes.get(tier, 100))

    # quote runs: open " before a contiguous run of q-words, close " after it
    q = [bool(spec(i).get("q")) for i in range(N)]
    italic = [bool(spec(i).get("i")) or q[i] for i in range(N)]  # quoted speech is always italic

    # --- chunk per LOCKED SOP: <=18 displayed chars AND <=3 words; break after . ? ! , ---
    MAXC = P["layout"].get("max_chars_per_line", 18)
    MAXW = P["layout"].get("max_words_per_screen", 3)

    def chunk_chars(idxs):
        return len(" ".join(disp(words[j]["word"]) for j in idxs))

    # PAUSE SPLIT (2026-06-14): a cue must never carry a post-pause word together with the words
    # before the pause — it would PRE-REVEAL (display during the silence before it's spoken). Because
    # spice_format strips periods, the .?! break below rarely fires on a normal statement, so this
    # timing-based split is the reliable guard. Threshold is in seconds of inter-word gap.
    PAUSE_SPLIT = float(P.get("timing", {}).get("pause_split_s", 0.35))
    chunks, cur = [], []
    for i, wd in enumerate(words):
        # HARD BREAK at a SPEAKER CHANGE (2026-06-10, Operator): the reference editor NEVER shows Speaker (white)
        # and the guest (yellow) in the same cue. A caption cue holds exactly ONE voice — when
        # Speaker asks and the guest answers (or vice-versa), the question ends on its own line and
        # the answer starts fresh. Without this, the char/word packer would merge the tail of one
        # speaker with the head of the next into a mixed white+yellow line.
        speaker_change = bool(cur) and voice_of(i) != voice_of(cur[-1])
        pause_break = bool(cur) and (float(wd["start"]) - float(words[cur[-1]]["end"]) > PAUSE_SPLIT)
        if cur and (speaker_change or pause_break or chunk_chars(cur + [i]) > MAXC or len(cur) + 1 > MAXW):
            chunks.append(cur); cur = [i]
        else:
            cur.append(i)
        if wd["word"].rstrip().endswith((".", "?", "!", ",")):
            chunks.append(cur); cur = []
    if cur:
        chunks.append(cur)

    # Merge orphan 1-word cues into a neighbor — breaking after every .?!, can strand a single
    # word ("well", "okay", "yeah", "the", "now") as its own cue. The reference editor's rule (see caption_director
    # header) is PHRASE RUNS, not lone floating words; the SF audit also flags these as "floating
    # fragments". Attach the orphan to whichever neighbor keeps the line short (prefer the previous
    # cue), as long as it stays within a small slack over the char limit.
    if len(chunks) > 1:
        merged = []
        for ch in chunks:
            # NEVER merge an orphan across a speaker change — that would re-create a mixed
            # white+yellow cue. Only fold into the previous cue when it's the SAME voice.
            # …and NEVER merge across a pause (would undo the pause-split → pre-reveal again).
            if (len(ch) == 1 and merged
                    and voice_of(ch[0]) == voice_of(merged[-1][-1])
                    and (float(words[ch[0]]["start"]) - float(words[merged[-1][-1]]["end"]) <= PAUSE_SPLIT)
                    and chunk_chars(merged[-1] + ch) <= MAXC + 7):
                merged[-1] = merged[-1] + ch
            else:
                merged.append(ch)
        # a leading orphan can't merge backward above; fold it into the next cue if same voice + fits
        # (and only if no pause separates them).
        if (len(merged) > 1 and len(merged[0]) == 1
                and voice_of(merged[0][0]) == voice_of(merged[1][0])
                and (float(words[merged[1][0]]["start"]) - float(words[merged[0][0]]["end"]) <= PAUSE_SPLIT)
                and chunk_chars(merged[0] + merged[1]) <= MAXC + 7):
            merged[1] = merged[0] + merged[1]; merged = merged[1:]
        chunks = merged

    # --- timing: function-word onset-correction + min-duration + zero-gap ---
    T = P.get("timing", {})
    MIN_DUR = a.min_cue_dur if a.min_cue_dur is not None else float(T.get("min_cue_dur", 0.40))
    onset_on = T.get("onset_correct", True) and not a.no_onset_correct

    def eff_onset(i):
        s, e = float(words[i]["start"]), float(words[i]["end"])
        dur = e - s
        exp = 0.10 + 0.055 * (len(bare(words[i]["word"])) or 1)
        if onset_on and dur > exp + 0.45 and bare(words[i]["word"]) in FUNCTION_WORDS:
            return max(s, e - exp)
        return s

    starts = [eff_onset(ch[0]) for ch in chunks]

    # --- render ---
    W = P["video"]["width"]; H = P["video"]["height"]
    FS = P.get("font_size_px", 150)
    # RESOLUTION-ADAPTIVE (2026-06-11): the two-layer gblur shadow sigma is FS-relative but ffmpeg
    # applies it in REAL output pixels, while libass rescales the TEXT from PlayRes->frame. If the
    # preset's dims don't match the actual burn frame, the text self-corrects but the shadow does NOT
    # -> a halo ~(frame/preset)x the wrong size (the months-long "wrong shadow" bug, and the /edit
    # default of the 4K preset on a 1080 frame). FIX AT THE SOURCE: when burning, probe the real frame
    # and scale W/H/FS to it so PlayRes==frame (text identical) AND the FS-relative shadow lands at the
    # right radius. A matched preset is unchanged (scale==1 -> byte-identical). This makes a wrong-res
    # shadow IMPOSSIBLE for EVERY caller (caption-app + qa_build + qa_assembly), not merely detected.
    if a.burn:
        try:
            _bd = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(a.burn)],
                capture_output=True, text=True).stdout.strip().split("x")
            bw, bh = int(_bd[0]), int(_bd[1])
            if bh > 0 and H > 0 and (bw, bh) != (W, H):
                sc = bh / H
                print(f"resolution-adaptive: preset {W}x{H} FS{FS} -> burn frame {bw}x{bh} "
                      f"FS{int(round(FS*sc))} (shadow scale {sc:.3f})")
                W, H, FS = bw, bh, int(round(FS * sc))
        except Exception as _e:
            print(f"resolution-adaptive probe skipped ({_e}); using preset dims {W}x{H}")
    Y_DEFAULT = int(round(H * P["layout"]["y_percent_from_top"] / 100))

    # Per-cue Y from layout track. If --layout is set, each cue's Y comes from the
    # layout segment its time-midpoint falls in (safe_y_pct in the JSON is a 0..1
    # fraction of FRAME height — we multiply by H here). With no layout, every cue
    # uses the preset's static Y (the legacy spice behavior).
    #
    # FRAME-ACCURATE TRANSITIONS: layout_analyze.py now outputs segments with
    # hard-cut boundaries (no smoothing across cuts). We store the transition
    # timestamps so generate_spice can SPLIT any caption cue that spans a
    # transition — the Y snaps at the exact frame the camera angle changes.
    layout_segs = None
    layout_transitions = []  # [(time, y_before, y_after), ...]
    if a.layout and a.layout.exists():
        try:
            ld = json.loads(a.layout.read_text())
            fps = ld.get("meta", {}).get("fps", 30.0)
            layout_segs = [
                {"t0": s["start_i"] / fps, "t1": (s["end_i"] + 1) / fps,
                 "y": int(round(H * float(s["safe_y_pct"])))}
                for s in ld.get("segments", [])
            ]
            # Only record a Y transition when the jump is significant enough
            # to be visible. Small Y changes (<50px on 1920-tall frame ≈ 2.6%)
            # come from face detection noise (slightly different face position
            # between angles) and create imperceptible cue splits that waste
            # subtitle events and confuse the renderer.
            MIN_Y_JUMP = 50  # pixels — ~2.6% of 1920px frame height
            # FRAME-EXACT SNAP (2026-06-10): the Y must change on the cut frame itself,
            # not the frame after. ts() rounds to centiseconds (~0.3 frame), so a snap
            # time of exactly start_i/fps can round UP and render the new Y one frame
            # late — which reads as jumpy (Operator: "if you don't do it the second the
            # frame switches it looks shitty"). Nudge the split time back HALF a frame so
            # the rounding lands the new Y on start_i. Both halves of the split share this
            # time, so they stay coincident — only the rendered frame of the snap shifts.
            half_frame = 0.5 / fps
            for i in range(1, len(layout_segs)):
                prev, cur = layout_segs[i - 1], layout_segs[i]
                if abs(prev["y"] - cur["y"]) >= MIN_Y_JUMP:
                    layout_transitions.append((max(0.0, cur["t0"] - half_frame), prev["y"], cur["y"]))
            print(f"layout: {len(layout_segs)} segments, Y range "
                  f"{min(s['y'] for s in layout_segs)}-{max(s['y'] for s in layout_segs)} px"
                  f", {len(layout_transitions)} significant Y-transitions (≥{MIN_Y_JUMP}px)")
        except Exception as e:
            print(f"layout: parse failed ({e}), using static Y={Y_DEFAULT}")
            layout_segs = None

    def y_at(t: float) -> int:
        if not layout_segs:
            return Y_DEFAULT
        for s in layout_segs:
            if s["t0"] <= t < s["t1"]:
                return s["y"]
        return layout_segs[-1]["y"]  # past-end → hold last segment

    def transition_in(t0: float, t1: float):
        """Return the first layout transition (time, y_before, y_after) within [t0, t1), or None."""
        for tr in layout_transitions:
            if t0 < tr[0] < t1:
                return tr
        return None

    CX = W // 2
    fax = P.get("italic_fax", -0.12)  # NEGATIVE = forward/right lean; positive leans LEFT in libass
    sh = P.get("shadow", {})
    shadow_mode = sh.get("mode", "classic")  # "classic" = old single-layer | "premiere" = two-layer soft
    shad = sh.get("offset_y", 9); blur = sh.get("blur", 18)
    sh_a = sh.get("alpha", 42); sh_c = sh.get("color", "000000")
    stroke = sh.get("stroke_px", 5); stroke_c = sh.get("stroke_color", "141414")
    # Premiere-mode shadow: separate blurred layer underneath crisp text.
    # shadow blurs independently — text stays sharp. Looks like Premiere Pro.
    # In premiere mode, the SHADOW provides text separation (not a hard outline).
    # Use premiere_stroke_px (default 0) instead of the thick classic stroke.
    if shadow_mode == "premiere":
        stroke = sh.get("premiere_stroke_px", 0)
    # Premiere Drop Shadow — TWO STACKED LAYERS, decoded from the reference editor's .prtextstyle preset.
    # The preset's binary holds TWO down-right (~110 deg) drop-shadow records, not one:
    #   Layer 1 (WIDE):  distance 20, blur 15  -> bigger, softer, drawn underneath
    #   Layer 2 (TIGHT): distance 10, blur 8   -> closer, denser, stacked on top
    # (a 3rd cluster = opacity 100 / size 250 = the text FILL, not a shadow.)
    # One layer alone reads mushy (no edge contrast); the pair gives a dense core at the
    # letters AND a soft trailing falloff — the Premiere look Operator flagged.
    # FONT-RELATIVE (FS_REF=150) so both layers scale to any render resolution.
    FS_REF = 150.0
    fs_scale = FS / FS_REF
    def _sc(v): return round(v * fs_scale, 1)
    def _sci(v): return int(round(v * fs_scale))
    # Layer 1 — WIDE / soft (preset shadow 1: dist20 / blur15)
    pr_sh_dx     = _sci(sh.get("offset_x", 5))
    pr_sh_dy     = _sci(sh.get("premiere_offset_y", 9))
    pr_sh_sigma  = _sc(sh.get("premiere_sigma", 15))
    pr_sh_border = _sci(sh.get("premiere_border", 8))
    pr_sh_intensity = sh.get("premiere_intensity", 0.70)
    # Layer 2 — TIGHT / dense (preset shadow 2: dist10 / blur8)
    pr_sh2_enabled   = sh.get("premiere_layer2", True)
    pr_sh2_dx        = _sci(sh.get("offset_x2", 2))
    pr_sh2_dy        = _sci(sh.get("premiere_offset_y2", 4))
    pr_sh2_sigma     = _sc(sh.get("premiere_sigma2", 7))
    pr_sh2_border    = _sci(sh.get("premiere_border2", 6))
    pr_sh2_intensity = sh.get("premiere_intensity2", 0.97)
    # Legacy ASS-only params (not used for gblur path, kept for reference)
    pr_sh_blur = sh.get("premiere_blur", 22)
    pr_sh_spread = sh.get("premiere_spread", 35)
    pr_sh_opacity = sh.get("premiere_opacity", 0.28)
    pr_sh_alpha = max(0, min(255, int(round((1.0 - pr_sh_opacity) * 255))))
    an = P.get("animation", {})
    # The caption "drop-in" distance is a PIXEL value tuned for the Premiere "Text Down Small" look
    # at 4K (FS_REF 150 -> ~16px). Like the shadow, it must scale with FS/resolution, else a 1080 frame
    # gets the full 16px = ~2x the relative drop (the "animation looks different" Operator caught). _sci
    # makes it FS-relative (16px@4K -> 8px@1080), so the subtle Premiere drop reads identically at any res.
    drop = _sci(an.get("position_drop_px", 16)); drop_ms = an.get("position_drop_duration_ms", 500)
    op_from = an.get("opacity_from", 0.90); op_ms = an.get("opacity_fade_duration_ms", 83)
    op_a = max(0, min(255, int(round((1.0 - op_from) * 255))))
    base_family = weights.get(default_weight, "Montserrat Regular")
    # gloss (under-caption definition) styling: smaller Montserrat Medium, one line below the caption.
    GP = P.get("gloss", {})
    gloss_fs = int(round(FS * GP.get("scale", 0.5)))
    gloss_dy = int(round(FS * GP.get("dy", 0.95)))
    gloss_font = GP.get("font", "Montserrat Medium")

    hdr = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: the reference editor,{base_family},{FS},&H00FFFFFF,&H00FFFFFF,{ass_color(stroke_c)},&H00000000,0,0,0,0,100,100,0,0,1,{stroke},0,5,80,80,80,1
Style: SpiceShadow,{base_family},{FS},&H00000000,&H00000000,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,80,80,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    # --- caption bubble (Brand testimonial style): translucent rounded bg behind each cue ---
    # Gated on preset["bubble"]; absent => no change to any other brand's spice render.
    BUB = P.get("bubble")
    def _rrect(w, h, r):
        # top-left-anchored rounded rect (0,0)->(w,h); emitted with \an7 + offset \pos so it
        # centers under the cue. (\an5 mis-anchors \p1 drawings in libass -> shifted left.)
        r = max(0.0, min(r, w / 2.0, h / 2.0))
        x0, y0, x1, y1 = 0.0, 0.0, w, h
        return (f"m {x0 + r:.0f} {y0:.0f} l {x1 - r:.0f} {y0:.0f} "
                f"b {x1:.0f} {y0:.0f} {x1:.0f} {y0 + r:.0f} {x1:.0f} {y0 + r:.0f} "
                f"l {x1:.0f} {y1 - r:.0f} b {x1:.0f} {y1:.0f} {x1 - r:.0f} {y1:.0f} {x1 - r:.0f} {y1:.0f} "
                f"l {x0 + r:.0f} {y1:.0f} b {x0:.0f} {y1:.0f} {x0:.0f} {y1 - r:.0f} {x0:.0f} {y1 - r:.0f} "
                f"l {x0:.0f} {y0 + r:.0f} b {x0:.0f} {y0:.0f} {x0 + r:.0f} {y0:.0f} {x0 + r:.0f} {y0:.0f}")
    bubble_ev = []
    ev = []
    for ci, ch in enumerate(chunks):
        st = starts[ci]
        en = starts[ci + 1] if ci + 1 < len(chunks) else float(words[ch[-1]]["end"]) + 0.30
        if en <= st:
            en = st + 0.30
        if ci + 1 < len(chunks) and en - st < MIN_DUR:  # never flash; hold + delay next (zero-gap kept)
            starts[ci + 1] = round(st + MIN_DUR, 2); en = starts[ci + 1]

        toks = []
        # SIZE PER CUE (Operator 2026-06-11 CORRECTION): a size bump may land on a SINGLE-WORD caption
        # OR the ENTIRE line uniformly — NEVER on one word inside a multi-word line (that reads broken;
        # The reference editor never did it). So size is decided per CUE, not per word, and every word in the cue
        # gets the SAME size. WEIGHT + italic still vary per word (bold the key word) — only SIZE is
        # locked uniform within a multi-word line.
        solo = len(ch) == 1
        _ws = [size_of(i) for i in ch]
        if solo:
            cue_sz = _ws[0]                                    # (a) single-word caption -> keep its bump
        elif any(is_number(words[i]["word"]) for i in ch):
            cue_sz = int(sizes.get("strong", 150))             # (b) money/number line -> bump the WHOLE line
        elif len(set(_ws)) == 1 and _ws[0] != 100:
            cue_sz = _ws[0]                                     # (b) director sized the whole line uniformly
        else:
            cue_sz = 100                                       # multi-word, mixed -> base (never one word bigger)
        # SAFE-ZONE CAP (Operator 2026-06-11): a size bump must NEVER run a line off-screen / out of the
        # UI ("$10M minimum" at 150% hit 99% of frame width). Measure the displayed line at the bumped
        # size and shrink the bump until it fits within ~82% of frame width.
        if cue_sz > 100:
            cue_text = " ".join(disp(words[i]["word"]) for i in ch)
            safe_px = int(0.82 * W)
            wpx = line_width_px(cue_text, int(FS * cue_sz / 100))
            if wpx > safe_px:
                cue_sz = max(100, int(cue_sz * safe_px / wpx))
        # PER-CUE quotes (The reference editor 2026-06-04: "a quotation mark for each subtitle segment"):
        # each caption cue wraps its OWN quoted span in " ... ", so a multi-cue quote shows marks
        # on every segment — not just the first/last cue of the run.
        qin = [i for i in ch if q[i]]
        fq, lq = (qin[0], qin[-1]) if qin else (None, None)
        for idx in ch:
            fam = weights.get(weight_of(idx), base_family)
            f = fax if italic[idx] else 0
            text = disp(words[idx]["word"])
            if idx == fq:
                text = '"' + text
            if idx == lq:
                text = text + '"'
            sz = cue_sz
            scale = f"\\fscx{sz}\\fscy{sz}" if sz != 100 else ""
            toks.append(f"{{\\fn{fam}\\b0\\i0\\fax{f}{scale}\\1c{ass_color(color_hex(idx))}}}{text}")

        # FRAME-ACCURATE Y: check if a layout transition falls inside this cue.
        # If so, split the cue into two Dialogue events at the exact transition
        # frame — the text is identical but Y snaps instantly at the cut.
        body = " ".join(toks)
        # bubble geometry for this cue (sized to the displayed text + padding)
        bub_draw = None; _btags = ""
        if BUB:
            _px = max(1, int(FS * cue_sz / 100))
            _ct = " ".join(disp(words[i]["word"]) for i in ch)
            _tw = line_width_px(_ct, _px)
            _safe = 0.86 * W
            _nl = max(1, -(-int(_tw) // int(_safe)))      # ceil -> line count if it wraps
            _lw = min(float(_tw), _safe)
            _lh = _px * 1.16
            _padx = BUB.get("pad_x_em", 0.5) * _px
            _pady = BUB.get("pad_y_em", 0.34) * _px
            _bw = _lw + 2 * _padx
            _bh = _nl * _lh + 2 * _pady
            _rad = min(BUB.get("radius_em", 0.55) * _px, _bh / 2.0, _bw / 2.0)
            bub_draw = _rrect(_bw, _bh, _rad)
            _ba = int(BUB.get("alpha", 128)); _bl = BUB.get("blur", 4); _bc = BUB.get("color", "000000")
            _btags = f"\\p1\\bord0\\shad0\\blur{_bl}\\1c{ass_color(_bc)}\\1a&H{_ba:02X}&"
        # Shadow-only body: same text structure but all words forced to black (for the shadow layer).
        # We keep the per-word \fn so the shadow matches the text shape exactly.
        if shadow_mode == "premiere":
            shadow_toks = []
            for idx in ch:
                fam = weights.get(weight_of(idx), base_family)
                f = fax if italic[idx] else 0
                text = disp(words[idx]["word"])
                if idx == fq:
                    text = '"' + text
                if idx == lq:
                    text = text + '"'
                sz = cue_sz   # shadow scales with the per-CUE size
                scale = f"\\fscx{sz}\\fscy{sz}" if sz != 100 else ""
                shadow_toks.append(f"{{\\fn{fam}\\b0\\i0\\fax{f}{scale}}}{text}")
            shadow_body = " ".join(shadow_toks)

        tr = transition_in(st, en)
        if tr:
            cut_t, y_before, y_after = tr
            if shadow_mode == "premiere":
                # --- PREMIERE SHADOW: two-layer (shadow underneath, crisp text on top) ---
                # Shadow layer (Layer 0) — blurred black text, offset, semi-transparent
                sh_pre1 = (f"{{\\an5\\pos({CX + pr_sh_dx},{y_before + pr_sh_dy})"
                           f"\\bord{pr_sh_spread}\\shad0\\blur{pr_sh_blur}"
                           f"\\1c&H00000000&\\3c&H00000000&"
                           f"\\1a&H{pr_sh_alpha:02X}&\\3a&H{pr_sh_alpha:02X}&\\4a&HFF&}}")
                ev.append(f"Dialogue: 0,{ts(st)},{ts(cut_t)},SpiceShadow,,0,0,0,,{sh_pre1}{shadow_body}")
                sh_pre2 = (f"{{\\an5\\pos({CX + pr_sh_dx},{y_after + pr_sh_dy})"
                           f"\\bord{pr_sh_spread}\\shad0\\blur{pr_sh_blur}"
                           f"\\1c&H00000000&\\3c&H00000000&"
                           f"\\1a&H{pr_sh_alpha:02X}&\\3a&H{pr_sh_alpha:02X}&\\4a&HFF&}}")
                ev.append(f"Dialogue: 0,{ts(cut_t)},{ts(en)},SpiceShadow,,0,0,0,,{sh_pre2}{shadow_body}")
                # Text layer (Layer 1) — crisp, thin outline, no blur, no shadow
                pre1 = (f"{{\\an5\\pos({CX},{y_before})"
                        f"\\1a&H{op_a:02X}&\\t(0,{op_ms},\\1a&H00&)"
                        f"\\shad0\\blur0\\3c{ass_color(stroke_c)}}}")
                ev.append(f"Dialogue: 1,{ts(st)},{ts(cut_t)},the reference editor,,0,0,0,,{pre1}{body}")
                pre2 = (f"{{\\an5\\pos({CX},{y_after})"
                        f"\\shad0\\blur0\\3c{ass_color(stroke_c)}}}")
                ev.append(f"Dialogue: 1,{ts(cut_t)},{ts(en)},the reference editor,,0,0,0,,{pre2}{body}")
            else:
                # --- CLASSIC SHADOW: single-layer ASS shadow ---
                pre1 = (f"{{\\an5\\pos({CX},{y_before})"
                        f"\\1a&H{op_a:02X}&\\t(0,{op_ms},\\1a&H00&)"
                        f"\\shad{shad}\\blur{blur}\\4c{ass_color(sh_c)}\\4a&H{sh_a:02X}&\\3c{ass_color(stroke_c)}}}")
                ev.append(f"Dialogue: 0,{ts(st)},{ts(cut_t)},the reference editor,,0,0,0,,{pre1}{body}")
                pre2 = (f"{{\\an5\\pos({CX},{y_after})"
                        f"\\shad{shad}\\blur{blur}\\4c{ass_color(sh_c)}\\4a&H{sh_a:02X}&\\3c{ass_color(stroke_c)}}}")
                ev.append(f"Dialogue: 0,{ts(cut_t)},{ts(en)},the reference editor,,0,0,0,,{pre2}{body}")
            if bub_draw:
                _bx = CX - _bw / 2.0
                bubble_ev.append(f"Dialogue: 0,{ts(st)},{ts(cut_t)},the reference editor,,0,0,0,,{{\\an7\\pos({_bx:.0f},{y_before - _bh/2:.0f}){_btags}}}{bub_draw}")
                bubble_ev.append(f"Dialogue: 0,{ts(cut_t)},{ts(en)},the reference editor,,0,0,0,,{{\\an7\\pos({_bx:.0f},{y_after - _bh/2:.0f}){_btags}}}{bub_draw}")
        else:
            Y_cue = y_at((st + en) / 2.0)
            if bub_draw:
                _bx = CX - _bw / 2.0
                bubble_ev.append(f"Dialogue: 0,{ts(st)},{ts(en)},the reference editor,,0,0,0,,"
                                 f"{{\\an7\\move({_bx:.0f},{Y_cue - drop - _bh/2:.0f},{_bx:.0f},{Y_cue - _bh/2:.0f},0,{drop_ms}){_btags}}}{bub_draw}")
            if shadow_mode == "premiere":
                # --- PREMIERE SHADOW: two-layer ---
                # Shadow layer (Layer 0)
                sh_pre = (f"{{\\an5\\move({CX + pr_sh_dx},{Y_cue - drop + pr_sh_dy},{CX + pr_sh_dx},{Y_cue + pr_sh_dy},0,{drop_ms})"
                          f"\\bord{pr_sh_spread}\\shad0\\blur{pr_sh_blur}"
                          f"\\1c&H00000000&\\3c&H00000000&"
                          f"\\1a&H{pr_sh_alpha:02X}&\\3a&H{pr_sh_alpha:02X}&\\4a&HFF&}}")
                ev.append(f"Dialogue: 0,{ts(st)},{ts(en)},SpiceShadow,,0,0,0,,{sh_pre}{shadow_body}")
                # Text layer (Layer 1) — crisp
                pre = (f"{{\\an5\\move({CX},{Y_cue-drop},{CX},{Y_cue},0,{drop_ms})"
                       f"\\1a&H{op_a:02X}&\\t(0,{op_ms},\\1a&H00&)"
                       f"\\shad0\\blur0\\3c{ass_color(stroke_c)}}}")
                ev.append(f"Dialogue: 1,{ts(st)},{ts(en)},the reference editor,,0,0,0,,{pre}{body}")
            else:
                # --- CLASSIC SHADOW ---
                pre = (f"{{\\an5\\move({CX},{Y_cue-drop},{CX},{Y_cue},0,{drop_ms})"
                       f"\\1a&H{op_a:02X}&\\t(0,{op_ms},\\1a&H00&)"
                       f"\\shad{shad}\\blur{blur}\\4c{ass_color(sh_c)}\\4a&H{sh_a:02X}&\\3c{ass_color(stroke_c)}}}")
                ev.append(f"Dialogue: 0,{ts(st)},{ts(en)},the reference editor,,0,0,0,,{pre}{body}")
        # under-caption gloss for any glossed word in this cue
        gtxt = next((gloss[str(i)] for i in ch if str(i) in gloss), None)
        if gtxt:
            Y_g = y_at((st + en) / 2.0) + gloss_dy
            gpre = (f"{{\\an5\\pos({CX},{Y_g})\\fn{gloss_font}\\b0\\i0\\fs{gloss_fs}"
                    f"\\1c&H00FFFFFF&\\shad{shad}\\blur{blur}\\4c{ass_color(sh_c)}\\4a&H{sh_a:02X}&"
                    f"\\3c{ass_color(stroke_c)}\\1a&H28&\\t(0,120,\\1a&H00&)}}")
            ev.append(f"Dialogue: 0,{ts(st)},{ts(en)},the reference editor,,0,0,0,,{gpre}{gtxt}")

    a.out.write_text(hdr + "\n".join(ev) + "\n")
    print(f"wrote {len(ev)} caption cues -> {a.out}")

    # Premiere gblur shadow: write separate shadow (white blob) + text-only ASS files.
    # The shadow ASS has white text + white border + no blur (gblur applied via ffmpeg).
    # The text ASS has only the crisp Layer 1 events.
    shadow_ass_path = shadow2_ass_path = text_ass_path = None
    if shadow_mode == "premiere":
        import re as _re
        def _shadow_events(border):
            out = []
            for line in ev:
                if line.startswith("Dialogue: 0,"):
                    l = line
                    l = _re.sub(r'\\bord\d+', f'\\\\bord{border}', l)
                    l = _re.sub(r'\\blur\d+', r'\\blur0', l)
                    l = l.replace('\\1c&H00000000&', '\\1c&H00FFFFFF&')
                    l = l.replace('\\3c&H00000000&', '\\3c&H00FFFFFF&')
                    l = _re.sub(r'\\1a&H[0-9A-Fa-f]+&', r'\\1a&H00&', l)
                    l = _re.sub(r'\\3a&H[0-9A-Fa-f]+&', r'\\3a&H00&', l)
                    out.append(l)
            return out
        text_ev = [l for l in ev if l.startswith("Dialogue: 1,")]
        # Layer 1 blob uses Size=pr_sh_border; Layer 2 blob uses Size=pr_sh2_border (Premiere "Size"/spread).
        shadow_ass_path  = a.out.with_name(a.out.stem + "_shadow.ass")
        shadow2_ass_path = a.out.with_name(a.out.stem + "_shadow2.ass")
        text_ass_path    = a.out.with_name(a.out.stem + "_text.ass")
        shadow_ass_path.write_text(hdr + "\n".join(_shadow_events(pr_sh_border)) + "\n")
        shadow2_ass_path.write_text(hdr + "\n".join(_shadow_events(pr_sh2_border)) + "\n")
        text_ass_path.write_text(hdr + "\n".join((bubble_ev + text_ev) if BUB else text_ev) + "\n")
        print(f"premiere gblur: wrote shadow blobs (border {pr_sh_border}/{pr_sh2_border}) + {len(text_ev)} text cues")
        # Auto-export a styled SRT for Premiere caption track import.
        # The reference editor imports this → applies his Montserrat + shadow caption style to the track;
        # bold/italic/color per word is already encoded inline. No extra flags needed.
        try:
            import subprocess as _sp, sys as _sys
            _exp = Path(__file__).parent / "export_premiere_srt.py"
            _srt_out = text_ass_path.with_suffix(".srt")
            _r = _sp.run([_sys.executable, str(_exp), str(text_ass_path),
                          "--out", str(_srt_out), "--split"],
                         capture_output=True, text=True)
            if _r.returncode == 0:
                print(f"premiere SRT: {_r.stdout.strip()}")
            else:
                print(f"premiere SRT: skipped ({_r.stderr.strip()[:120]})")
        except Exception as _e:
            print(f"premiere SRT: skipped ({_e})")

    if a.burn:
        out = a.burn_out or a.burn.with_name(a.burn.stem + "_spice.mp4")
        # ffmpeg filter-graph path escaping: forward slashes (Windows backslashes get eaten
        # by the filter parser), THEN escape the drive colon.
        def _filt_path(p):
            return str(p).replace("\\", "/").replace(":", r"\:")
        fdir = _filt_path((SKILL / P["fonts_dir"]).resolve())
        # Bitrate must track the ACTUAL burn output resolution, not the preset PlayRes (which only
        # drives caption scaling). Else a 720 proxy gets the 4K bitrate -> ~5x too big. Probe the input.
        venc = (list(encoder_args_for(str(a.burn), "ffmpeg", tier="delivery")) if encoder_args_for
                else ["-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p"])

        if shadow_mode == "premiere" and shadow_ass_path and text_ass_path:
            # Premiere TWO-LAYER gblur shadow (decoded from the reference editor's preset).
            # One white blob (from the shadow ASS) -> two independently blurred/offset layers:
            #   WIDE (soft, underneath) then TIGHT (dense, on top) -> crisp text on top.
            # Each layer: gblur(sigma) -> curves(intensity) -> alphamerge w/ solid black -> overlay(dx,dy).
            sh_sub  = _filt_path(shadow_ass_path)
            sh2_sub = _filt_path(shadow2_ass_path)
            tx_sub  = _filt_path(text_ass_path)
            if pr_sh2_enabled:
                fc = (
                    f"[0:v]split=5[video][bw][sw][bt][st];"
                    # LAYER 1 (drawn first/underneath) — blob border = pr_sh_border
                    f"[bw]drawbox=c=black:t=fill[bwk];"
                    f"[bwk]ass='{sh_sub}':fontsdir='{fdir}'[wob_w];"
                    f"[wob_w]gblur=sigma={pr_sh_sigma},curves=all='0/0 1/{pr_sh_intensity:.2f}'[glow_w];"
                    f"[sw]drawbox=c=black:t=fill,format=yuva444p[sbk_w];"
                    f"[sbk_w][glow_w]alphamerge[layer_w];"
                    f"[video][layer_w]overlay=x={pr_sh_dx}:y={pr_sh_dy}:format=auto:shortest=1[v1];"
                    # LAYER 2 (drawn over layer 1) — blob border = pr_sh2_border
                    f"[bt]drawbox=c=black:t=fill[btk];"
                    f"[btk]ass='{sh2_sub}':fontsdir='{fdir}'[wob_t];"
                    f"[wob_t]gblur=sigma={pr_sh2_sigma},curves=all='0/0 1/{pr_sh2_intensity:.2f}'[glow_t];"
                    f"[st]drawbox=c=black:t=fill,format=yuva444p[sbk_t];"
                    f"[sbk_t][glow_t]alphamerge[layer_t];"
                    f"[v1][layer_t]overlay=x={pr_sh2_dx}:y={pr_sh2_dy}:format=auto:shortest=1[v2];"
                    f"[v2]ass='{tx_sub}':fontsdir='{fdir}'[final]"
                )
            else:
                fc = (
                    f"[0:v]split=3[video][forblack][forsolid];"
                    f"[forblack]drawbox=c=black:t=fill[black1];"
                    f"[black1]ass='{sh_sub}':fontsdir='{fdir}'[white_on_black];"
                    f"[white_on_black]gblur=sigma={pr_sh_sigma},curves=all='0/0 1/{pr_sh_intensity:.2f}'[glow];"
                    f"[forsolid]drawbox=c=black:t=fill,format=yuva444p[solid_black];"
                    f"[solid_black][glow]alphamerge[shadow_layer];"
                    f"[video][shadow_layer]overlay=x={pr_sh_dx}:y={pr_sh_dy}:format=auto:shortest=1[with_shadow];"
                    f"[with_shadow]ass='{tx_sub}':fontsdir='{fdir}'[final]"
                )
            r = subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(a.burn),
                "-filter_complex", fc,
                "-map", "[final]", "-map", "0:a",
                *venc, "-c:a", "copy", "-movflags", "+faststart", str(out),
            ])
        else:
            # Classic single-pass ASS burn
            sub = _filt_path(a.out)
            r = subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(a.burn),
                "-vf", f"subtitles=filename='{sub}':fontsdir='{fdir}'",
                *venc, "-c:a", "copy", "-movflags", "+faststart", str(out),
            ])
        print("burn rc", r.returncode, "->", out)
        if r.returncode != 0:
            print(f"✗ BURN FAILED (ffmpeg rc={r.returncode}) — propagating non-zero so the caller "
                  f"(caption_one / daemon / qa_build) marks the job failed instead of shipping a "
                  f"partial or empty mp4 as 'done'", file=_sys.stderr)
            return r.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
