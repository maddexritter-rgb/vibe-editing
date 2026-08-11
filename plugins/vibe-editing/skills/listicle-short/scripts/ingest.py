#!/usr/bin/env python3
"""listicle-short ingest — YouTube URL (or local file) -> timestamped transcript + 1080p video.

Outputs into <out>/:
  source.mp4            1080p H.264 (the long-form, for cutting)
  transcript_ts.txt     one line per caption event: "[mm:ss] text"  (for the agent to read + mine)
  transcript_words.json word-level {start_ms,text} (json3-derived; precise timestamps for the spec)
  meta.json             {title, duration_s, words}

For YouTube we pull yt-dlp json3 auto-captions (fast, accurate, free — no transcription needed for mining).
For a local file with no captions, pass --whisper to transcribe with Groq lv3 instead.

Usage:
  ingest.py "<youtube-url-or-file>" --out <dir> [--whisper]
"""
# ── vibe-editing portable path bootstrap (auto-inserted) ──
# ── engine bundled-keys autoload (config/keys.env) ──
import os as _ko, pathlib as _kp
def _acq_load_keys():
    d = _kp.Path(__file__).resolve()
    for p in (d, *d.parents):
        if (p / ".claude-plugin").is_dir():
            f = p / "config" / "keys.env"
            if f.is_file():
                for _ln in f.read_text().splitlines():
                    _ln = _ln.strip()
                    if _ln and not _ln.startswith("#") and "=" in _ln:
                        _k, _v = _ln.split("=", 1); _k, _v = _k.strip(), _v.strip()
                        if _k and "PASTE" not in _v and not _ko.environ.get(_k):
                            _ko.environ[_k] = _v
            return
_acq_load_keys()
# ── end keys ──
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
import argparse, json, subprocess, sys, re
from pathlib import Path


def run(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], **kw)


def parse_json3(p):
    d = json.loads(Path(p).read_text())
    lines = []  # (start_ms, text)
    for e in d.get('events', []):
        if 'segs' not in e:
            continue
        text = ''.join(s.get('utf8', '') for s in e['segs'])
        if text.strip() in ('', '\n'):
            continue
        lines.append((e.get('tStartMs', 0), ' '.join(text.split())))
    return lines


def ts(ms):
    s = int(ms / 1000)
    return f"{s // 60:02d}:{s % 60:02d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src', help='YouTube URL or local video path')
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--whisper', action='store_true', help='force Groq lv3 transcription (local files w/o captions)')
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    is_url = a.src.startswith('http')

    # --- video ---
    source = a.out / 'source.mp4'
    if is_url:
        print("[ingest] downloading 1080p video")
        run(['yt-dlp', '-f', "bv*[height<=1080]+ba/b[height<=1080]", '--merge-output-format', 'mp4',
             '-o', str(source), '--no-playlist', a.src])
    else:
        source = Path(a.src)

    # --- transcript ---
    words = []  # (start_ms, text)
    if is_url and not a.whisper:
        print("[ingest] pulling json3 captions")
        run(['yt-dlp', '--skip-download', '--write-auto-subs', '--write-subs', '--sub-langs', 'en.*',
             '--sub-format', 'json3', '-o', str(a.out / 'cap.%(ext)s'), a.src],
            capture_output=True, text=True)
        j3 = next(iter(sorted(a.out.glob('cap*.json3'))), None)
        if j3:
            words = parse_json3(j3)
    if not words:  # local file, or no captions -> Groq lv3
        print("[ingest] transcribing (Groq lv3)")
        import os
        k = os.environ.get('GROQ_API_KEY') or subprocess.run(
            ['zsh', '-ic', 'printf %s "$GROQ_API_KEY"'], capture_output=True, text=True).stdout.strip()
        dur = float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                    '-of', 'csv=p=0', str(source)], capture_output=True, text=True).stdout.strip())
        traw = a.out / 't_raw.json'
        run([sys.executable, _acq("caption-clips/scripts/transcribe_lv3.py"),
             str(source), '--start', 0, '--end', round(dur, 2), '--out', traw],
            env=dict(os.environ, GROQ_API_KEY=k))
        for w in json.loads(traw.read_text())['words']:
            words.append((int(w['start'] * 1000), w['word']))

    (a.out / 'transcript_ts.txt').write_text('\n'.join(f"[{ts(ms)}] {t}" for ms, t in words))
    (a.out / 'transcript_words.json').write_text(json.dumps(
        [{'start_ms': ms, 'text': t} for ms, t in words], indent=1))
    dur_s = round(words[-1][0] / 1000, 1) if words else 0
    (a.out / 'meta.json').write_text(json.dumps(
        {'title': a.src, 'duration_s': dur_s, 'words': len(words), 'source': str(source)}, indent=1))
    print(f"[ingest] OK -> {a.out}  ({len(words)} caption lines, ~{dur_s}s)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
