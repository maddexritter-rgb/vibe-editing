"""Private keys shared by setup and runtime. Values are parsed as data, never sourced."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = Path.home() / '.config/vibe-editing'
KEYS_FILE = CONFIG / 'keys.env'
FIELDS = (
    'ASSEMBLYAI_API_KEY', 'GEMINI_API_KEY', 'GROQ_API_KEY',
    'TOPAZ_API_KEY', 'BUFFER_TOKEN', 'BUFFER_CHANNEL_ID',
    'SUPABASE_URL', 'SUPABASE_KEY', 'SUPABASE_BUCKET',
)

# Retain previously saved optional-helper values without asking new editors for them.
# Saving another key must never erase an existing user's private configuration.
LEGACY_FIELDS = ('ANTHROPIC_API_KEY', 'EDITORIAL_BRAIN_READ_KEY',
                 'EDITORIAL_BRAIN_FUNCTION_URL', 'EDITORIAL_BRAIN_PROJECT_REF')


def read_values(path):
    values = {}
    if not Path(path).is_file():
        return values
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        name, value = line.removeprefix('export ').split('=', 1)
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if name in (*FIELDS, *LEGACY_FIELDS) and value and not value.startswith(('PASTE', 'YOUR_', '<')):
            values[name] = value
    return values


def load_keys():
    # Explicit process environment wins; user's private file wins over legacy local config.
    for path in (KEYS_FILE, ROOT / 'config/keys.env'):
        for name, value in read_values(path).items():
            if not os.environ.get(name):
                os.environ[name] = value
