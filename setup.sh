#!/usr/bin/env bash
# One-command setup for Vibe Editing.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PLUG="$DIR/plugins/vibe-editing"
echo "== Vibe Editing setup =="
if command -v brew >/dev/null 2>&1; then
  echo "Installing system tools via Homebrew (ffmpeg, tesseract, yt-dlp, rclone)..."
  brew install ffmpeg tesseract yt-dlp rclone || true
else
  echo "!! Homebrew not found. Install it (https://brew.sh) then re-run, or install"
  echo "   ffmpeg + tesseract + yt-dlp yourself."
fi
echo "Setting up Python environment..."
cd "$PLUG"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt
echo ""
echo "Running health check..."
python3 "$PLUG/doctor.py" || true
echo ""
echo "Next steps:"
echo "  1) Open this repo in your agent and say: Set up Vibe Editing for me (ONBOARDING.md)"
echo "  2) In Claude Code:  /edit <your youtube link>"
echo "     or from the terminal:  ./bin/vibe-editing \"<your youtube link>\""
