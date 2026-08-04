#!/usr/bin/env python3
"""rebake_spice.py — re-bake the FULL spice look after an editor fixed wording in Premiere.

The round-trip's second half. The editor reviewed the plain editable caption track,
fixed any wrong words, and exported it back to SRT (Premiere: Captions ▸ Export). This
takes that corrected SRT + our ORIGINAL styled .ass and:

  • matches each corrected cue to the original .ass cue BY TIMECODE (robust to the
    host/guest split — pass both SRTs; each cue finds its line by start time),
  • swaps the corrected word TEXT into the .ass token-by-token, KEEPING every style tag
    (weight \\fn, size \\fscx, italic \\fax, color \\1c) and the timing — so the look the
    editor already approved is preserved exactly, only the spelling changes,
  • burns the patched .ass with burn_captions.py.

Typo/word fixes (same word count per cue) patch 1:1. A cue whose word count changed is
re-flowed using that cue's dominant style (logged) — safe, not silently wrong.

Usage:
  python3 rebake_spice.py --ass subs_text.ass --srt host.srt [--srt guest.srt] \\
      --video src.mp4 --out final.mp4 [--start 0 --end DUR] [--patch-only]
"""
from __future__ import annotations
import argparse, re, subprocess, sys
from pathlib import Path

SC = Path(__file__).resolve().parent
TOK = re.compile(r"\{([^}]*)\}([^{]*)")


def srt_time_to_s(t: str) -> float:
    t = t.strip().replace(",", ".")
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def ass_time_to_s(t: str) -> float:
    h, m, rest = t.split(":")
    s, cs = rest.split(".")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100.0


def parse_srt(path: Path) -> list[tuple[float, float, str]]:
    cues, block = [], []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines() + [""]:
        if line.strip() == "":
            if len(block) >= 2:
                tl = next((b for b in block if "-->" in b), None)
                if tl:
                    st, en = tl.split("-->")
                    txt = " ".join(block[block.index(tl) + 1:]).strip()
                    txt = re.sub(r"</?[^>]+>", "", txt)  # tolerate any stray markup
                    if txt:
                        cues.append((srt_time_to_s(st), srt_time_to_s(en), txt))
            block = []
        else:
            block.append(line)
    return cues


def patch_dialogue(text_field: str, new_words: list[str], log: list) -> str:
    """Replace word text in a Dialogue text field, preserving the {tags} on each token."""
    segs = TOK.findall(text_field)              # [(tags, word), ...]
    # first segment is the line-level override block (its 'word' is empty after strip)
    head = []
    word_segs = []
    for i, (tags, word) in enumerate(segs):
        if i == 0 and word.strip() == "":
            head.append((tags, word))
        else:
            word_segs.append([tags, word])
    if not word_segs:
        return text_field
    if len(new_words) == len(word_segs):
        for ws, nw in zip(word_segs, new_words):
            # keep trailing space pattern: original word seg may carry a trailing space
            ws[1] = (nw + (" " if ws[1].endswith(" ") else ""))
    else:
        # structural change — re-flow with the cue's dominant (first word) tag, log it
        log.append(f"cue word-count {len(word_segs)}→{len(new_words)} (re-flowed uniform)")
        tag = word_segs[0][0]
        word_segs = [[tag, w + " "] for w in new_words]
        if word_segs:
            word_segs[-1][1] = word_segs[-1][1].rstrip()
    out = "".join("{" + t + "}" + w for t, w in head)
    out += "".join("{" + t + "}" + w for t, w in word_segs)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ass", type=Path, required=True, help="ORIGINAL styled subs_text.ass")
    ap.add_argument("--srt", type=Path, action="append", required=True,
                    help="corrected SRT (pass twice for host + guest)")
    ap.add_argument("--video", type=Path, help="source video (required unless --patch-only)")
    ap.add_argument("--out", type=Path, help="output mp4 (required unless --patch-only)")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=None)
    ap.add_argument("--patched-ass", type=Path, default=None, help="where to write the patched .ass")
    ap.add_argument("--patch-only", action="store_true", help="patch the .ass and skip the burn (test)")
    ap.add_argument("--tol", type=float, default=0.25, help="cue start match tolerance (s)")
    a = ap.parse_args()

    lines = a.ass.read_text(encoding="utf-8", errors="replace").splitlines()
    # index Dialogue lines by start-seconds
    dlg = []  # (line_idx, start_s)
    for i, ln in enumerate(lines):
        if ln.startswith("Dialogue:"):
            parts = ln.split(",", 9)
            if len(parts) >= 10:
                dlg.append((i, ass_time_to_s(parts[1])))

    corrected = []
    for srt in a.srt:
        corrected += parse_srt(srt)
    corrected.sort()

    matched, unmatched, logs = 0, 0, []
    used = set()
    for st, en, txt in corrected:
        # nearest unused Dialogue by start time within tolerance
        best, bestd = None, a.tol
        for li, ds in dlg:
            if li in used:
                continue
            d = abs(ds - st)
            if d <= bestd:
                best, bestd = li, d
        if best is None:
            unmatched += 1
            logs.append(f"no .ass cue near {st:.2f}s for '{txt[:30]}' (timing changed?)")
            continue
        used.add(best)
        parts = lines[best].split(",", 9)
        new_words = txt.split()
        parts[9] = patch_dialogue(parts[9], new_words, logs)
        lines[best] = ",".join(parts)
        matched += 1

    patched = a.patched_ass or a.ass.with_name(a.ass.stem + "_edited.ass")
    patched.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"rebake: matched {matched} cues, {unmatched} unmatched → {patched.name}")
    for l in logs[:12]:
        print(f"   · {l}")

    if a.patch_only:
        return 0
    if not (a.video and a.out):
        sys.exit("ERROR: --video and --out required unless --patch-only")
    end = a.end
    if end is None:
        dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nokey=1:noprint_wrappers=1", str(a.video)],
                             capture_output=True, text=True).stdout.strip()
        end = float(dur) if dur else 0.0
    cmd = [sys.executable, str(SC / "burn_captions.py"), str(a.video), str(patched),
           "--start", str(a.start), "--end", str(end), "--out", str(a.out)]
    print(f"burning: {' '.join(cmd)}")
    r = subprocess.run(cmd)
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
