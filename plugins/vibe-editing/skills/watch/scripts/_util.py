"""Shared helpers for the watch skill scripts: ffprobe, frame extraction, fonts."""
import subprocess, os

FONTS = [
    # Windows system fonts (this machine)
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    # macOS fallbacks (original kit)
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/SFNS.ttf",
]


def load_font(size):
    from PIL import ImageFont
    for f in FONTS:
        if os.path.exists(f):
            try:
                return ImageFont.truetype(f, size)
            except Exception:
                pass
    return ImageFont.load_default()


def run(cmd):
    # errors="replace": ffmpeg/tesseract can emit non-UTF-8 bytes on stderr
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace")


def ffprobe_duration(path):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nk=1:nw=1", path])
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def ffprobe_dims(path):
    r = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", path])
    try:
        w, h = r.stdout.strip().split(",")[:2]
        return int(w), int(h)
    except Exception:
        return (0, 0)


def extract_frame(path, t, out, width=None):
    """Extract one frame at time t (seconds) to out. Optional downscale width."""
    vf = ["-vf", f"scale={int(width)}:-2"] if width else []
    run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", path,
         "-frames:v", "1", "-q:v", "2", *vf, out])
    return os.path.exists(out)


def fmt_ts(t):
    m = int(t // 60)
    s = t - 60 * m
    return f"{m:02d}:{s:05.2f}"
