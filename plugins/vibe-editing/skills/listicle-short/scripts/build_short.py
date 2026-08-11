#!/usr/bin/env python3
"""listicle-short — render a numbered rapid-fire listicle short from a long-form + an approved spec.

Pipeline (all locked from the Creator 13-years build, 2026-06-04):
  cut each soundbite (frame-accurate) -> concat -> [face-tracked 9:16 reframe] ->
  SPICE caption chain (transcribe lv3 -> normalize_simple -> spice_normalize -> caption director ->
  generate_spice -> persistent #N numbers above each tactic) -> level audio (-6 dB, optional music) -> V1.

Reuses the caption-clips + horizontal-to-vertical scripts (single source of truth — no duplicated logic).

SPEC (json the agent authors after mining the transcript; in/out are LONG-FORM seconds):
  { "title": "13-years-of-marketing",
    "segments": [
      {"in": 9.33, "out": 12.86},            # hook / non-numbered intro (omit "n")
      {"in": 12.60, "out": 17.28, "n": 1},   # tactic #1
      {"in": 17.92, "out": 20.48, "n": 2},   # tactic #2
      ... ] }

Usage:
  build_short.py --source <longform.mp4> --spec spec.json --out <dir>
      [--no-reframe | --stop-after-assemble] [--music <track.mp3>] [--director stream.json] [--res auto|1080|4k]
"""
# ── vibe-editing portable path bootstrap (auto-inserted) ──
# ── engine bundled-keys autoload (config/keys.env) ──
import tempfile
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
import argparse, json, os, subprocess, sys, shutil
from pathlib import Path

sys.path.insert(0, VIBE_SHARED)
from fast_encode import encoder_args  # Brand fast-render standard — VideoToolbox hardware encode

CAP = _acq("caption-clips/scripts")
PRESETS = _acq("caption-clips/presets")
FONTSDIR = _acq("caption-clips/fonts/free_font")
REFRAME = _acq("horizontal-to-vertical/scripts/reframe.sh")
AUDIO_AF = "highpass=f=80,loudnorm=I=-16:LRA=11:TP=-7,alimiter=limit=0.45:level=disabled"
# Locked natural-warm color correction (matches the Speaker reference look; also corrects the
# reframe's bt601/green matrix shift). Applied to the video BEFORE captions are drawn on top.
GRADE = "eq=contrast=1.06:saturation=1.08:gamma=0.98,colorbalance=rm=0.015:gm=-0.022:bm=-0.035"
LISTICLE_CAP_PCT = 66   # captions sit lower than normal (60) to clear the category pill above them
PILL_Y_RATIO = 0.594    # pill centre as a fraction of frame height (~1140 @ 1920); above the caption line


def run(cmd, **kw):
    r = subprocess.run([str(c) for c in cmd], **kw)
    return r


def probe_dur(p):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                          '-of', 'csv=p=0', str(p)], capture_output=True, text=True).stdout.strip()
    return float(out) if out else 0.0


def probe_h(p):
    out = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                          'stream=height', '-of', 'csv=p=0', str(p)], capture_output=True, text=True).stdout.strip()
    return int(out) if out else 1920


# --- eye-line lock: measure the subject's eye-line on a cut robustly so every segment can be
#     reframed to the SAME output eye level (kills the per-segment vertical drift). ----------------
try:
    import cv2 as _CV2
    _FACE_CAS = _CV2.CascadeClassifier(_CV2.data.haarcascades + 'haarcascade_frontalface_default.xml')
except Exception:
    _CV2 = None


def eye_y_frac(path, n=10, t0=None, t1=None):
    """Median source eye-line (fraction 0..1 of height) over n samples. t0/t1 sample a SOURCE time range
    (else the whole clip). Rejects logo/false faces (a shirt logo reads as a small low 'face'). None if no face."""
    if _CV2 is None:
        return None
    if t0 is None:
        dur = probe_dur(path); base = 0.0
    else:
        dur = max(0.05, float(t1) - float(t0)); base = float(t0)
    if dur <= 0:
        return None
    ys = []
    for k in range(n):
        t = base + dur * (k + 1) / (n + 1)
        tmp = os.path.join(tempfile.gettempdir(), '_eyey_bs.png')
        subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-ss', f'{t:.3f}', '-i', str(path),
                        '-frames:v', '1', '-vf', 'scale=960:-1', tmp], check=False)
        img = _CV2.imread(tmp)
        if img is None:
            continue
        g = _CV2.cvtColor(img, _CV2.COLOR_BGR2GRAY); H = img.shape[0]
        for (x, y, fw, fh) in _FACE_CAS.detectMultiScale(g, 1.1, 5, minSize=(80, 80)):
            fr = fh / H; eyy = (y + 0.4 * fh) / H
            if 0.18 <= fr <= 0.42 and 0.12 <= eyy <= 0.45:   # real face, reject shirt-logo/chin false-positive
                ys.append(eyy); break
    if not ys:
        return None
    ys.sort()
    return ys[len(ys) // 2]                    # robust median


def shell_key(name):
    v = os.environ.get(name)
    if v:
        return v
    r = subprocess.run(['zsh', '-ic', f'printf %s "${name}"'], capture_output=True, text=True)
    return r.stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', required=True, type=Path, help='long-form video')
    ap.add_argument('--spec', required=True, type=Path, help='approved clip spec json')
    ap.add_argument('--out', required=True, type=Path, help='output dir')
    ap.add_argument('--no-reframe', action='store_true', help='caption the horizontal assembly (skip 9:16)')
    ap.add_argument('--stop-after-assemble', action='store_true', help='cut+concat only, output horizontal + stop')
    ap.add_argument('--music', type=Path, default=None, help='music track to mix low under the voice')
    ap.add_argument('--director', type=Path, default=None, help='per-word style stream (else auto/light)')
    ap.add_argument('--res', default='auto', choices=['auto', '1080', '4k'])
    ap.add_argument('--grade', action='store_true', help='apply the locked natural-warm color correction before captions')
    ap.add_argument('--per-seg', action='store_true', help='reframe EACH cut on its OWN face-track, then concat (supercuts: no drift/slide across seams)')
    ap.add_argument('--nose-y', type=int, default=None, help='nose Y in 1080-ref frame (default 719; LOWER = subject higher in frame)')
    ap.add_argument('--zoom', type=float, default=None, help='reframe zoom (default 1.15; HIGHER = bigger/tighter face, fills the frame like the reference shorts)')
    ap.add_argument('--lock-x', action='store_true', help='lock X static per segment (smoothest for seated talkers — no per-frame tracking jitter)')
    ap.add_argument('--eye-lock', action='store_true', help='measure each cut\'s eye-line and lock all cuts to the SAME output eye level (kills per-segment vertical drift). Per-seg only.')
    ap.add_argument('--eye-y-out', type=float, default=566.0, help='output eye-line target in 1920-ref (default 566 ≈ 0.295); used with --eye-lock')
    ap.add_argument('--tighten', action='store_true', help='jump-cut dead air between lessons for snappier pacing (before captions, so they re-sync)')
    ap.add_argument('--max-pause', type=float, default=0.14, help='longest pause kept by --tighten (s); lower = snappier')
    ap.add_argument('--tighten-noise', default='-30dB', help='silence threshold for --tighten (more negative = only deader silence cut)')
    a = ap.parse_args()
    _rfx = (lambda: (['--zoom', str(a.zoom)] if a.zoom else []) + (['--lock-x'] if a.lock_x else []) + (['--nose-y', str(a.nose_y)] if a.nose_y else []))

    out = a.out; work = out / '_work'; clips = work / 'clips'
    clips.mkdir(parents=True, exist_ok=True)
    spec = json.loads(a.spec.read_text())
    title = spec.get('title', 'listicle_short')

    # 1) cut each soundbite (frame-accurate re-encode), measure real durations
    _per = a.per_seg and not a.no_reframe
    print(f"[1/6] cutting {len(spec['segments'])} segments" + (" + per-segment face-track" if _per else ""))
    _src_h = probe_h(a.source)
    _src_wh = (2160, 3840) if _src_h >= 2100 else (1080, 1920)
    seglist = []
    # eye-lock PRE-PASS: measure every cut's eye-line on the SOURCE, then CLAMP outliers to the clip
    # median — so one badly-detected cut can't get cropped out of frame (the "Speaker out of frame" bug).
    eye_ys = None
    if a.eye_lock and _per:
        raw = [eye_y_frac(a.source, t0=float(s['in']), t1=float(s['out'])) for s in spec['segments']]
        good = sorted(y for y in raw if y is not None)
        med = good[len(good) // 2] if good else None
        eye_ys = [(y if (y is not None and med is not None and abs(y - med) <= 0.05) else med) for y in raw]
        nclamp = sum(1 for y, c in zip(raw, eye_ys) if y != c and c is not None)
        print(f"[eye-lock] clip median eye-line {med}; clamped {nclamp} outlier seg(s) to median", flush=True)
    for i, s in enumerate(spec['segments']):
        o = clips / f'seg_{i:02d}.mp4'
        d = round(float(s['out']) - float(s['in']), 3)
        # de-click a-fades at the seam edges. precision_cut spans already end on the TRUE acoustic
        # end (+ silence margin) and start clamped before the onset, so a 10/15ms ramp lands in the
        # silence margins — kills concat clicks WITHOUT softening the word (playbook: a-fades at joins).
        _fo = max(d - 0.015, 0.0)
        run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-ss', s['in'], '-i', a.source,
             '-t', d, *encoder_args(_src_wh[0], _src_wh[1], 'ffmpeg', tier="delivery", crf=18),
             '-af', 'afade=t=in:d=0.008',   # fade-IN ONLY — a fade-OUT here clips the last word's tail (playbook). Join de-click comes from the next seg's fade-in.
             '-c:a', 'aac', '-movflags', '+faststart', o])
        if _per:                                   # face-track THIS cut on its own -> seams disappear
            rf = clips / f'seg_{i:02d}_9x16.mp4'
            _eyearg = []
            if a.eye_lock:                          # lock THIS cut's eye-line to the shared output level
                ey = eye_ys[i] if eye_ys else eye_y_frac(o)
                if ey is not None:
                    _eyearg = ['--eye-y-src', f'{ey:.4f}', '--eye-y-out', str(a.eye_y_out)]
                    print(f"    seg{i:02d} eye-line src={ey:.3f} -> locked", flush=True)
            run(['bash', REFRAME, o, rf] + (['--res', a.res] if a.res != 'auto' else []) + _rfx() + _eyearg)
            o = rf
        seglist.append((o, probe_dur(o), s.get('n'), s.get('cat')))

    # 2) concat (identical codecs -> stream copy)
    print("[2/6] assembling")
    concat = work / 'concat.txt'
    # ABSOLUTE paths — ffmpeg's concat demuxer resolves 'file' entries relative to the concat
    # file's OWN dir, so relative paths double up (build/_work/build/_work/...) and fail.
    concat.write_text('\n'.join(f"file '{c[0].resolve()}'" for c in seglist) + '\n')
    assembled = work / 'assembled.mp4'
    run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-f', 'concat', '-safe', '0',
         '-i', concat, '-c', 'copy', '-movflags', '+faststart', assembled])
    if not assembled.exists() or probe_dur(assembled) < 0.1:
        sys.exit("[build_short] concat failed — no assembled.mp4 (check segment paths)")

    if a.stop_after_assemble:
        dest = out / f'{title}_assembled_horizontal.mp4'
        shutil.copy(assembled, dest)
        print(f"STOP-AFTER-ASSEMBLE -> {dest}  (reframe/bg yourself, then re-run with --source it --no-reframe)")
        return 0

    # 3) reframe 9:16 (face-tracked) unless captioning horizontal or already per-segment
    base = assembled
    if _per:
        print("[3/6] reframe done per-segment (each cut tracked on its own — no drift across seams)")
    elif not a.no_reframe:
        print("[3/6] face-tracked 9:16 reframe")
        v = work / 'assembled_9x16.mp4'
        run(['bash', REFRAME, assembled, v] + (['--res', a.res] if a.res != 'auto' else []) + _rfx())
        base = v
    else:
        print("[3/6] reframe skipped (captioning horizontal)")

    # 3.5) optional dead-air tighten — jump-cut the silence between lessons for snappier pacing.
    #      Runs BEFORE captions so the transcribe re-syncs to the tightened audio automatically.
    if getattr(a, 'tighten', False):
        bt = work / 'assembled_tight.mp4'
        print(f"[3/6] dead-air tighten (jump-cut pauses > {a.max_pause}s)")
        run([sys.executable, CAP / 'jumpcut.py', base, bt, '--max-pause', str(a.max_pause),
             '--noise', a.tighten_noise, '--min-detect', '0.12'])
        if bt.exists() and probe_dur(bt) > 1.0:
            base = bt

    # 4) SPICE caption chain
    print("[4/6] transcribe + normalize + spice_normalize")
    genv = dict(os.environ); k = shell_key('GROQ_API_KEY')
    if k: genv['GROQ_API_KEY'] = k
    t_raw = work / 't_raw.json'; t_norm = work / 't_norm.json'; t_spice = work / 't_spice.json'
    run([sys.executable, CAP / 'transcribe_lv3.py', base, '--start', 0, '--end', round(probe_dur(base), 3),
         '--out', t_raw], env=genv)
    run([sys.executable, CAP / 'normalize_simple.py', t_raw, t_norm])
    run([sys.executable, CAP / 'spice_normalize.py', t_norm, t_spice])

    # director style stream: provided > auto (caption_director) > light default
    style_arg = []
    director = a.director
    if not director:
        ak = shell_key('ANTHROPIC_API_KEY')
        if ak:
            auto = work / 'director.json'
            r = run([sys.executable, CAP / 'caption_director.py', t_spice, '--out', auto],
                    env=dict(os.environ, ANTHROPIC_API_KEY=ak))
            if r.returncode == 0 and auto.exists():
                director = auto
    if director:
        style_arg = ['--style', director]
    else:
        print("    (no director stream — using generate_spice light auto-emphasis)")

    # category tabs (glass pills) fire when every numbered segment carries a "cat"; else plain #N numbers
    numbered = [s for s in spec['segments'] if s.get('n') is not None]
    use_tabs = bool(numbered) and all(s.get('cat') for s in numbered)
    base_preset = PRESETS / 'spice.json'  # ONE preset for all res (generate_spice is resolution-adaptive)
    if use_tabs:                                   # lower the caption line to make room for the pill above it
        Pj = json.loads(base_preset.read_text())
        Pj['layout']['y_percent_from_top'] = LISTICLE_CAP_PCT
        preset = work / 'preset_listicle.json'; preset.write_text(json.dumps(Pj))
    else:
        preset = base_preset
    ass = work / 'spice.ass'
    run([sys.executable, CAP / 'generate_spice.py', t_spice, '--preset', preset, *style_arg, '--out', ass])

    # 5) tactic labels above each numbered segment — point = cumulative start of that segment
    #    glass "N. CATEGORY" pills when cats are present (locked Speaker/SF look); else persistent #N numbers
    print("[5/6] category tabs" if use_tabs else "[5/6] numbering tactics")
    cum = 0.0; points = []
    for (_c, d, n, cat) in seglist:
        if n is not None:
            points.append((round(cum, 2), n, cat))
        cum += d
    if points and use_tabs:
        pill_y = int(round(probe_h(base) * PILL_Y_RATIO))
        pargs = []
        for st, n, cat in points:
            pargs += ['--point', f'{st}:#{n}:{cat}']
        tabbed = work / 'spice_tabs.ass'
        run([sys.executable, CAP / 'spice_tabs.py', ass, '--clip-end', round(cum, 2),
             '--style', 'glass', '--y', pill_y, *pargs, '--out', tabbed])
        ass = tabbed
    elif points:
        pargs = []
        for st, n, _cat in points:
            pargs += ['--point', f'{st}:#{n}']
        numbered = work / 'spice_num.ass'
        run([sys.executable, CAP / 'spice_number.py', ass, '--above', '--clip-end', round(cum, 2),
             *pargs, '--out', numbered])
        ass = numbered

    # 6) burn + level audio (+ optional music)
    print("[6/6] burn + audio -> V1")
    final = out / f'{title}_v1.mp4'
    _base_wh = (2160, 3840) if probe_h(base) >= 2100 else (1080, 1920)
    sub = f"subtitles=filename='{ass}':fontsdir='{FONTSDIR}'"
    vf = f"{GRADE},{sub}" if a.grade else sub   # grade first, then burn captions on top (captions stay clean)
    if a.music and a.music.exists():
        fc = (f"[0:a]highpass=f=80,loudnorm=I=-16:LRA=11:TP=-7[v];"
              f"[1:a]loudnorm=I=-30:LRA=11:TP=-9,afade=t=in:st=0:d=1.0,"
              f"afade=t=out:st={max(0, round(cum-1.5,2))}:d=1.5[m];"
              f"[v][m]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.45:level=disabled[a]")
        run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-i', base, '-i', a.music,
             '-vf', vf, '-filter_complex', fc, '-map', '0:v', '-map', '[a]',
             *encoder_args(_base_wh[0], _base_wh[1], 'ffmpeg', tier="delivery", crf=16),
             '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', final])
    else:
        run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-i', base,
             '-vf', vf, '-af', AUDIO_AF,
             *encoder_args(_base_wh[0], _base_wh[1], 'ffmpeg', tier="delivery", crf=16),
             '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', final])
    print(f"\nV1 -> {final}  ({round(cum,1)}s)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
