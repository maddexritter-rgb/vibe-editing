#!/usr/bin/env python3
"""AssemblyAI-first source ingest using the live /edit ASR profile."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'lib/_shared'))
from asr import transcribe, assemblyai_key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if not assemblyai_key():
        parser.error('Connect ASSEMBLYAI_API_KEY through onboarding before primary ingest.')
    if args.out.exists():
        parser.error('Output already exists. Reuse the current transcript or choose a new revision.')
    result = transcribe(str(args.source), use_cache=False, profile='editorial_ingest',
                        state_path=args.out.with_suffix('.job.json'))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x') as out:
        json.dump(result, out, indent=2)
    print(f"Saved {len(result['words'])} words ({result['engine']}, {result['evidence_status']}).")


if __name__ == '__main__':
    main()
