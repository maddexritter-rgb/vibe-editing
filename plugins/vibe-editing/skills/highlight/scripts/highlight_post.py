#!/usr/bin/env python3
"""
highlight_post.py — POST MODE: title + schedule the mids you've ALREADY uploaded as drafts,
to YOUR OWN YouTube channel, via the YouTube Data API v3 + your own Google sign-in.

Workflow: mine mids (MINE mode) → upload them as DRAFTS / unlisted-private to your channel →
run this and say "post & schedule these". It:
  1) Lists your channel's private/draft uploads (newest first).
  2) Titles each one with the subs-optimized rules (title_sop.py). Uses ANTHROPIC_API_KEY if set;
     otherwise a deterministic key-free local titler from the video's description/transcript.
  3) Schedules each to the next open daily slot as `private` + `publishAt` (a safe, reversible
     scheduled-publish).

Nothing here is tied to any specific account — it acts ONLY on the channel your OAuth signs into.

ONE-TIME SETUP (the recipient does this):
  • Create a Google Cloud project, enable the "YouTube Data API v3".
  • Create an OAuth client of type "Desktop app", download it as client_secret.json.
  • Save it at  <repo>/plugins/vibe-editing/config/youtube_client_secret.json
    (or pass --client-secret /path/to/client_secret.json).
  • pip install google-api-python-client google-auth-oauthlib
  • First run opens a browser for YOUR Google sign-in; the token is cached next to the secret.

Usage:
  highlight_post.py --channel-id UCxxxxxxxx --dry-run            # preview the plan, write nothing
  highlight_post.py --channel-id UCxxxxxxxx                      # title + schedule
  highlight_post.py --channel-id UCxxxxxxxx --slots 12:00,18:00 --tz America/Los_Angeles --limit 10
"""
# ── vibe-editing portable path bootstrap ──
import os as _os, sys as _sys
def _vibe_root():
    r = _os.environ.get("VIBE_PIPELINE_ROOT") or _os.environ.get("CLAUDE_PLUGIN_ROOT")
    if r and _os.path.isdir(_os.path.join(r, ".claude-plugin")):
        return r
    d = _os.path.dirname(_os.path.abspath(__file__))
    while d != _os.path.dirname(d):
        if _os.path.isdir(_os.path.join(d, ".claude-plugin")):
            return d
        d = _os.path.dirname(d)
    return _os.path.dirname(_os.path.abspath(__file__))
VIBE_ROOT = _vibe_root()
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
CONFIG_DIR = _os.path.join(VIBE_ROOT, "config")
_sys.path.insert(0, VIBE_ROOT)
from vibe_credentials import load_keys
load_keys()
# ── end bootstrap ──
import argparse, datetime as dt
from title_sop import build_title1_system, local_title

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
DEFAULT_SECRET = _os.path.join(CONFIG_DIR, "youtube_client_secret.json")
DEFAULT_TOKEN = _os.path.join(CONFIG_DIR, "youtube_token.json")


# --------------------------------------------------------------------------- titling
def make_title(text, use_llm=True):
    """Subs-optimized title. LLM if ANTHROPIC_API_KEY is set, else key-free local titler."""
    key = _os.environ.get("ANTHROPIC_API_KEY")
    if use_llm and key and text.strip():
        try:
            import urllib.request, json as _json
            body = {
                "model": _os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest"),
                "max_tokens": 64,
                "system": build_title1_system(),
                "messages": [{"role": "user", "content": f"TRANSCRIPT:\n{text[:6000]}\n\nWrite the title."}],
            }
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=_json.dumps(body).encode(),
                headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=40) as r:
                j = _json.loads(r.read())
            out = "".join(b.get("text", "") for b in j.get("content", [])).strip().splitlines()
            if out and out[0].strip():
                return out[0].strip().strip('"').strip()[:95] if not out[0].strip().startswith('"') else out[0].strip()[:95]
        except Exception as e:
            print(f"  (LLM titler failed: {e} — using local titler)")
    return local_title(text)[:95]


# --------------------------------------------------------------------------- youtube
def get_service(client_secret, token_path):
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        _sys.exit("pip install google-api-python-client google-auth-oauthlib")
    creds = None
    if _os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not _os.path.exists(client_secret):
                _sys.exit(f"Missing OAuth client secret at {client_secret}.\n"
                          "Create a Desktop OAuth client in Google Cloud (YouTube Data API v3) and save it there.")
            flow = InstalledAppFlow.from_client_secrets_file(client_secret, SCOPES)
            creds = flow.run_local_server(port=0)
        _os.makedirs(_os.path.dirname(token_path), exist_ok=True)
        open(token_path, "w").write(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def list_private_uploads(svc, channel_id, limit):
    """Return [{id,title,description,privacyStatus}] of the channel's private (draft-style) uploads."""
    ch = svc.channels().list(part="contentDetails", id=channel_id).execute()
    items = ch.get("items") or []
    if not items:
        _sys.exit(f"channel {channel_id} not found / not owned by the signed-in account.")
    uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    vids, page = [], None
    while len(vids) < limit * 4:
        pl = svc.playlistItems().list(part="contentDetails", playlistId=uploads,
                                      maxResults=50, pageToken=page).execute()
        ids = [it["contentDetails"]["videoId"] for it in pl.get("items", [])]
        if ids:
            vr = svc.videos().list(part="snippet,status", id=",".join(ids)).execute()
            for v in vr.get("items", []):
                if v["status"].get("privacyStatus") == "private" and not v["status"].get("publishAt"):
                    vids.append({"id": v["id"], "title": v["snippet"].get("title", ""),
                                 "description": v["snippet"].get("description", ""),
                                 "categoryId": v["snippet"].get("categoryId", "22")})
        page = pl.get("nextPageToken")
        if not page:
            break
    return vids[:limit]


def next_slots(n, slots, tz_name, start=None):
    """Generate the next n publish datetimes (UTC ISO) at the given daily HH:MM slots."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = dt.timezone.utc
    now = (start or dt.datetime.now(tz))
    hhmm = []
    for s in slots:
        h, m = s.split(":"); hhmm.append((int(h), int(m)))
    out, day = [], now.date()
    while len(out) < n:
        for (h, m) in hhmm:
            cand = dt.datetime(day.year, day.month, day.day, h, m, tzinfo=tz)
            if cand > now:
                out.append(cand.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
                if len(out) >= n:
                    break
        day += dt.timedelta(days=1)
    return out


def main():
    ap = argparse.ArgumentParser(description="POST mode — title + schedule YOUR uploaded drafts via the YouTube API")
    ap.add_argument("--channel-id", required=True, help="YOUR channel id (UC...)")
    ap.add_argument("--limit", type=int, default=20, help="cap how many drafts to process")
    ap.add_argument("--slots", default="12:00,18:00", help="daily publish slots, comma-separated HH:MM")
    ap.add_argument("--tz", default="America/Los_Angeles", help="timezone for the slots")
    ap.add_argument("--client-secret", default=DEFAULT_SECRET)
    ap.add_argument("--token", default=DEFAULT_TOKEN)
    ap.add_argument("--no-llm", action="store_true", help="force the key-free local titler")
    ap.add_argument("--no-retitle", action="store_true", help="keep each draft's existing title; only schedule")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; write nothing")
    a = ap.parse_args()
    slots = [s.strip() for s in a.slots.split(",") if s.strip()]

    print("POST mode: acts ONLY on YOUR channel via YOUR Google sign-in. Schedules as private + publishAt.")
    svc = get_service(a.client_secret, a.token)
    drafts = list_private_uploads(svc, a.channel_id, a.limit)
    if not drafts:
        print("No private/draft uploads found. Upload your mids as private drafts first, then re-run.")
        return
    when = next_slots(len(drafts), slots, a.tz)

    print(f"\n{len(drafts)} draft(s) to schedule at slots {slots} ({a.tz}):\n")
    plan = []
    for d, w in zip(drafts, when):
        title = d["title"] if a.no_retitle else make_title(d["description"] or d["title"], use_llm=not a.no_llm)
        plan.append((d, title, w))
        print(f"  {d['id']}  →  {w}")
        print(f"     title: {title}")

    if a.dry_run:
        print("\n[DRY-RUN] nothing written. Re-run without --dry-run to title + schedule.")
        return

    for d, title, w in plan:
        body = {"id": d["id"],
                "snippet": {"title": title[:95], "categoryId": d.get("categoryId", "22")},
                "status": {"privacyStatus": "private", "publishAt": w}}
        svc.videos().update(part="snippet,status", body=body).execute()
        print(f"[post] ✅ {d['id']} scheduled {w} — {title}")
    print("\n[post] done. Verify in YouTube Studio → Content → Scheduled.")


if __name__ == "__main__":
    main()
