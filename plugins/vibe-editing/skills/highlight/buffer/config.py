"""
Config + credential loader for /highlight MODE C (FILL the queue).

Nothing here is hardcoded to any account. Every id / url / key is read from the
environment or from ~/.config/, with a clear error telling YOU how to set it.

Bring your OWN:
  - a Buffer account + API token        -> BUFFER_TOKEN  (or ~/.config/buffer/api_key)
  - the channel/profile you want to post -> BUFFER_CHANNEL_ID (run `fill_queue.py discover`)
  - a storage bucket for public URLs     -> SUPABASE_URL / SUPABASE_KEY / SUPABASE_BUCKET
"""

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Share the same private credential file as guided onboarding.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from vibe_credentials import load_keys
load_keys()

# Local config dirs (used if the matching env var is unset).
BUFFER_CONFIG = Path.home() / ".config" / "buffer"
SUPABASE_CONFIG = Path.home() / ".config" / "supabase"


# ── small helpers ──────────────────────────────────────────────────────

def _read_file(path: Path) -> str | None:
    try:
        v = path.read_text().strip()
        return v or None
    except FileNotFoundError:
        return None


def _from_env_or_file(env_name: str, file_path: Path) -> str | None:
    """Env var first, then a one-line file in ~/.config/."""
    return os.environ.get(env_name) or _read_file(file_path)


# ── Buffer ─────────────────────────────────────────────────────────────

BUFFER_API_ENDPOINT = os.environ.get("BUFFER_API_ENDPOINT", "https://api.buffer.com")


def buffer_token() -> str:
    """Your Buffer API token. Env BUFFER_TOKEN, else ~/.config/buffer/api_key."""
    tok = _from_env_or_file("BUFFER_TOKEN", BUFFER_CONFIG / "api_key")
    if not tok:
        raise FileNotFoundError(
            "Buffer API token not found.\n"
            "  1. Create a token at https://publish.buffer.com/settings/api\n"
            "  2. Either:  export BUFFER_TOKEN='your-token'\n"
            f"     or:      mkdir -p {BUFFER_CONFIG} && "
            f"echo 'your-token' > {BUFFER_CONFIG / 'api_key'}"
        )
    return tok


def buffer_channel_id() -> str:
    """The channel/profile you post to. Env BUFFER_CHANNEL_ID, else file.

    Discover yours with:  python3 fill_queue.py discover
    """
    cid = _from_env_or_file("BUFFER_CHANNEL_ID", BUFFER_CONFIG / "channel_id")
    if not cid or cid == "YOUR_CHANNEL_ID":
        raise ValueError(
            "No Buffer channel configured (placeholder is 'YOUR_CHANNEL_ID').\n"
            "  1. List your channels:  python3 fill_queue.py discover\n"
            "  2. Either:  export BUFFER_CHANNEL_ID='<id-from-discover>'\n"
            f"     or:      mkdir -p {BUFFER_CONFIG} && "
            f"echo '<id-from-discover>' > {BUFFER_CONFIG / 'channel_id'}"
        )
    return cid


# ── Storage (public URL host for Buffer) ───────────────────────────────
# Buffer needs a public media URL, so each video is uploaded to YOUR object
# storage first. Defaults target a Supabase Storage bucket, but any S3-style
# public bucket works — just point these at it.

def storage_url() -> str:
    """Base URL of your storage project. Env SUPABASE_URL, else file."""
    url = _from_env_or_file("SUPABASE_URL", SUPABASE_CONFIG / "url")
    if not url:
        raise ValueError(
            "No storage URL configured.\n"
            "  Buffer needs a PUBLIC url for each video, so we upload to your own bucket first.\n"
            "  Either:  export SUPABASE_URL='https://YOUR-PROJECT.supabase.co'\n"
            f"     or:   mkdir -p {SUPABASE_CONFIG} && "
            f"echo 'https://YOUR-PROJECT.supabase.co' > {SUPABASE_CONFIG / 'url'}\n"
            "  (Or skip uploads entirely by passing --video-url for a pre-hosted clip.)"
        )
    return url.rstrip("/")


def storage_key() -> str:
    """Your storage service/API key. Env SUPABASE_KEY, else file."""
    key = _from_env_or_file("SUPABASE_KEY", SUPABASE_CONFIG / "service_key")
    if not key:
        raise FileNotFoundError(
            "No storage key configured.\n"
            "  Either:  export SUPABASE_KEY='your-service-role-key'\n"
            f"     or:   mkdir -p {SUPABASE_CONFIG} && "
            f"echo 'your-key' > {SUPABASE_CONFIG / 'service_key'}"
        )
    return key


def storage_bucket() -> str:
    """Bucket name videos are uploaded into. Env SUPABASE_BUCKET, default 'vibe-media'."""
    return (
        os.environ.get("SUPABASE_BUCKET")
        or _read_file(SUPABASE_CONFIG / "bucket")
        or "vibe-media"
    )


# Prefix (folder) inside the bucket for these uploads.
STORAGE_PREFIX = os.environ.get("SUPABASE_PREFIX", "buffer-videos")


# ── Source folder of finished 9:16 shorts ──────────────────────────────

def shorts_dir(cli_dir: str | None = None) -> Path:
    """Folder to scan for finished 9:16 shorts.

    Resolution order:
      1. --dir on the CLI
      2. env VIBE_SHORTS_DIR
      3. the kit's default deliver folder (repo brand/deliver, or ./deliver here)
    """
    if cli_dir:
        return Path(cli_dir).expanduser()
    env = os.environ.get("VIBE_SHORTS_DIR")
    if env:
        return Path(env).expanduser()
    return _default_deliver_dir()


def _default_deliver_dir() -> Path:
    """The kit's own deliver folder, located relative to the plugin root."""
    root = os.environ.get("VIBE_PIPELINE_ROOT") or os.environ.get("CLAUDE_PLUGIN_ROOT")
    if root:
        repo_root = Path(root).resolve().parent.parent  # parent of plugins/
        cand = repo_root / "brand" / "deliver"
        if cand.exists():
            return cand
    # last-ditch local fallback so the tool always has *a* place to look
    return HERE / "deliver"


# ── Daily cap (loud) ───────────────────────────────────────────────────

DAILY_CAP = int(os.environ.get("VIBE_BUFFER_DAILY_CAP", "10"))
