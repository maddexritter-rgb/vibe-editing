"""Read an active-agent style artifact; never invoke an external model."""
import json
import hashlib
from pathlib import Path


def load_agent_style(path, words_path):
    if not path:
        raise ValueError(f'Have the active agent read {words_path} and author --director <style.json>. No extra model API key is needed.')
    style = json.loads(Path(path).read_text())
    words_file = Path(words_path)
    words = json.loads(words_file.read_text())['words']
    digest = hashlib.sha256(words_file.read_bytes()).hexdigest()
    if not isinstance(style, dict) or style.get('words_sha256') != digest:
        raise ValueError('Agent style is not bound to these normalized words. Read the current words and set words_sha256 to their file SHA-256 before rendering.')
    if not isinstance(style, dict) or not isinstance(style.get('words'), dict):
        raise ValueError('Agent style stream must contain a words object keyed by normalized word index.')
    for index, settings in style['words'].items():
        if not str(index).isdigit() or not 0 <= int(index) < len(words) or not isinstance(settings, dict):
            raise ValueError('Agent style stream has an invalid normalized-word index or style.')
    if style.get('voice_spans'):
        raise ValueError('Use per-word c="guest" for agent-authored streams; voice_spans must be empty.')
    return style
