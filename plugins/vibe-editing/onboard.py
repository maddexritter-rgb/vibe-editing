#!/usr/bin/env python3
"""Agent setup helper: private entry, redacted status and read-only key checks."""
import argparse
import getpass
import json
import os
import tempfile
import subprocess
import urllib.request
import urllib.error
from pathlib import Path

from vibe_credentials import CONFIG, FIELDS, KEYS_FILE, load_keys, read_values

STATE = CONFIG / 'onboarding.json'
FEATURES = ('editing', 'frame-monday', 'drive', 'youtube', 'buffer', 'topaz')


def private_json(path, value):
    private_write(path, json.dumps(value, indent=2) + '\n')


def private_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    fd, temp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def save_field(name, value):
    if name not in FIELDS or not value.strip() or '\n' in value or '\r' in value:
        raise ValueError('Choose a supported field and a nonempty single-line value.')
    values = read_values(KEYS_FILE)
    values[name] = value.strip()
    private_write(KEYS_FILE, '# Private Vibe Editing credentials; never commit or paste into chat.\n' +
                  ''.join(f'{k}={v}\n' for k, v in values.items()))


def status():
    load_keys()
    state = json.loads(STATE.read_text()) if STATE.exists() else {'schema': 'vibe-onboarding/v1', 'features': {}}
    return {'onboarding': state, 'configured_fields': {name: bool(os.environ.get(name)) for name in FIELDS},
            'note': 'Configured means present, not authenticated or tested. Read ONBOARDING.md for the interview.'}


def verify_key(name):
    load_keys()
    value = os.environ.get(name)
    if not value:
        raise ValueError('Credential not configured.')
    requests = {
        'ASSEMBLYAI_API_KEY': ('https://api.assemblyai.com/v2/transcript?limit=1', {'authorization': value}),
        'GROQ_API_KEY': ('https://api.groq.com/openai/v1/models', {'Authorization': 'Bearer ' + value}),
        'GEMINI_API_KEY': ('https://generativelanguage.googleapis.com/v1beta/models', {'x-goog-api-key': value}),
    }
    if name not in requests:
        raise ValueError('Use the feature-specific read-only check in docs/CONNECTIONS.md.')
    url, headers = requests[name]
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            response.read()  # Never print provider responses: they can include private transcript text.
    except urllib.error.HTTPError as error:
        raise RuntimeError(f'{name} read check failed: HTTP {error.code}') from None
    except (urllib.error.URLError, OSError):
        raise RuntimeError(f'{name} read check could not connect.') from None
    return {'credential': name, 'read_access': 'verified',
            'note': 'No media uploaded. This does not verify credit balance, all models, or end-to-end execution.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    p = sub.add_parser('run', help='Run a helper with the private environment; never use this to print secrets.')
    p.add_argument('argv', nargs=argparse.REMAINDER)
    p = sub.add_parser('set')
    p.add_argument('field', choices=FIELDS)
    p = sub.add_parser('verify')
    p.add_argument('field', choices=FIELDS)
    p = sub.add_parser('record')
    p.add_argument('feature', choices=FEATURES)
    p.add_argument('--status', choices=['pending', 'configured', 'verified', 'skipped'], required=True)
    args = parser.parse_args()
    if args.command == 'run':
        command = args.argv[1:] if args.argv[:1] == ['--'] else args.argv
        if not command:
            parser.error('run needs a helper command')
        load_keys()
        raise SystemExit(subprocess.run(command, env=dict(os.environ)).returncode)
    elif args.command == 'set':
        save_field(args.field, getpass.getpass(args.field + ' (hidden): '))
        print('Saved privately. Run the relevant read check next.')
    elif args.command == 'verify':
        print(json.dumps(verify_key(args.field), indent=2))
    elif args.command == 'status':
        print(json.dumps(status(), indent=2))
    else:
        state = json.loads(STATE.read_text()) if STATE.exists() else {'schema': 'vibe-onboarding/v1', 'features': {}}
        state['features'][args.feature] = args.status
        private_json(STATE, state)
        print(f'{args.feature}: {args.status}')


if __name__ == '__main__':
    main()
