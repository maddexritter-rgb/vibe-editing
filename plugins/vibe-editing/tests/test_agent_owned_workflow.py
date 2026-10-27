"""The current agent owns editorial/style decisions without a second model credential."""
import importlib.util
import json
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import onboard
import vibe_credentials


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    with patch.object(vibe_credentials, 'load_keys'):
        spec.loader.exec_module(module)
    return module


class AgentOwnedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.caption = load('agent_spice_caption', ROOT / 'skills/caption-clips/scripts/spice_caption.py')
        self.words = self.root / 'words.json'
        self.words.write_text(json.dumps({'words': [{'word': 'Hello', 'start': 0, 'end': 0.4}]}))
        self.stream = self.root / 'agent-style.json'
        self.stream.write_text(json.dumps({'words_sha256': hashlib.sha256(self.words.read_bytes()).hexdigest(), 'words': {'0': {'w': 'strong', 'c': 'speaker'}}, 'voice_spans': []}))
        self.args = SimpleNamespace(words=self.words, corrections=None, context=None, director=self.stream)

    def run_gen(self):
        def deterministic(command, label, env, **kwargs):
            if 'spice_format.py' in str(command[1]):
                Path(command[-1]).write_text(self.words.read_text())
        with patch.object(self.caption, '_probe_dur', return_value=1), patch.object(self.caption, 'run', side_effect=deterministic) as commands:
            result = self.caption.do_gen(self.root / 'video.mp4', self.root / 'generated', {}, self.args)
            return result, commands

    def test_agent_stream_is_used_without_another_model_call(self):
        (spice, stream), commands = self.run_gen()
        self.assertEqual(json.loads(stream.read_text()), json.loads(self.stream.read_text()))
        launched = [str(call.args[0]) for call in commands.call_args_list]
        self.assertFalse(any('caption_director.py' in command or 'claude' in command for command in launched))
        self.assertTrue(spice.exists())

    def test_missing_stream_requests_active_agent_instead_of_api_key(self):
        self.args.director = None
        with self.assertRaisesRegex(SystemExit, 'active agent'):
            self.run_gen()
        self.assertTrue((self.root / 'generated/spice_norm.json').exists())

    def test_invalid_word_index_is_rejected(self):
        self.stream.write_text(json.dumps({'words_sha256': hashlib.sha256(self.words.read_bytes()).hexdigest(), 'words': {'99': {'w': 'strong'}}}))
        with self.assertRaisesRegex(ValueError, 'index'):
            self.run_gen()

    def test_style_for_changed_words_is_rejected_even_if_indices_still_fit(self):
        self.words.write_text(json.dumps({'words': [{'word': 'Different', 'start': 0, 'end': 0.4}]}))
        with self.assertRaisesRegex(ValueError, 'not bound'):
            self.run_gen()

    def test_onboarding_does_not_request_editor_model_or_individual_brain_keys(self):
        self.assertNotIn('ANTHROPIC_API_KEY', vibe_credentials.FIELDS)
        self.assertNotIn('OPENAI_API_KEY', vibe_credentials.FIELDS)
        self.assertNotIn('EDITORIAL_BRAIN_READ_KEY', vibe_credentials.FIELDS)

    def test_saving_media_key_preserves_existing_private_helper_settings(self):
        path = self.root / 'keys.env'
        path.write_text('ANTHROPIC_API_KEY=existing-legacy-value\n')
        with patch.object(onboard, 'KEYS_FILE', path):
            onboard.save_field('ASSEMBLYAI_API_KEY', 'dummy-media-key')
        self.assertIn('ANTHROPIC_API_KEY=existing-legacy-value', path.read_text())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_wrapper_directors_use_the_same_agent_artifact_contract(self):
        helper = load('agent_style_helper_test', ROOT / 'skills/caption-clips/scripts/agent_style.py')
        self.assertEqual(helper.load_agent_style(self.stream, self.words)['words']['0']['w'], 'strong')
        with self.assertRaisesRegex(ValueError, 'active agent'):
            helper.load_agent_style(None, self.words)

    def test_render_requires_agent_artifact_before_starting_subprocesses(self):
        stage = load('agent_caption_stage', ROOT / 'skills/render/stages/captions.py')
        with patch.object(stage.subprocess, 'run') as process:
            with self.assertRaisesRegex(ValueError, 'active agent'):
                stage.run(self.root / 'render', {}, {'cut': self.root / 'cut.mp4'}, {}, self.root, {}, self.root / 'out.mp4')
            process.assert_not_called()


if __name__ == '__main__':
    unittest.main()
