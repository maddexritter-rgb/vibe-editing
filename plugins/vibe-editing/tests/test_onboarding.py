"""Offline setup/runtime contracts; never load the machine's private credentials."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import onboard
import vibe_credentials as credentials


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.keys = self.root / 'private/keys.env'
        self.state = self.root / 'private/onboarding.json'
        for obj, name, value in [(credentials, 'KEYS_FILE', self.keys), (credentials, 'ROOT', self.root),
                                  (onboard, 'KEYS_FILE', self.keys), (onboard, 'STATE', self.state)]:
            p = patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.dict(os.environ, {}, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def test_save_preserves_fields_and_private_permissions(self):
        onboard.save_field('ASSEMBLYAI_API_KEY', 'test-aai-secret')
        onboard.save_field('GEMINI_API_KEY', 'test-gemini-secret')
        self.assertEqual(credentials.read_values(self.keys)['ASSEMBLYAI_API_KEY'], 'test-aai-secret')
        self.assertEqual(self.keys.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.keys.parent.stat().st_mode & 0o777, 0o700)

    def test_runtime_precedence(self):
        onboard.save_field('ASSEMBLYAI_API_KEY', 'private')
        old = self.root / 'config/keys.env'
        old.parent.mkdir()
        old.write_text('ASSEMBLYAI_API_KEY=legacy\nGROQ_API_KEY=legacy-groq\n')
        os.environ['ASSEMBLYAI_API_KEY'] = 'explicit-process'
        credentials.load_keys()
        self.assertEqual(os.environ['ASSEMBLYAI_API_KEY'], 'explicit-process')
        self.assertEqual(os.environ['GROQ_API_KEY'], 'legacy-groq')
        os.environ.pop('ASSEMBLYAI_API_KEY')
        credentials.load_keys()
        self.assertEqual(os.environ['ASSEMBLYAI_API_KEY'], 'private')

    def test_empty_export_does_not_mask_private_key(self):
        onboard.save_field('ASSEMBLYAI_API_KEY', 'private')
        os.environ['ASSEMBLYAI_API_KEY'] = ''
        credentials.load_keys()
        self.assertEqual(os.environ['ASSEMBLYAI_API_KEY'], 'private')

    def test_status_redacts_values_and_does_not_claim_auth(self):
        onboard.save_field('ASSEMBLYAI_API_KEY', 'secret-never-print')
        result = onboard.status()
        self.assertTrue(result['configured_fields']['ASSEMBLYAI_API_KEY'])
        self.assertNotIn('secret-never-print', json.dumps(result))
        self.assertIn('not authenticated', result['note'])

    def test_newlines_and_unsupported_keys_rejected(self):
        for name, value in [('ASSEMBLYAI_API_KEY', 'x\nOTHER=y'), ('OPENAI_API_KEY', 'unused')]:
            with self.assertRaises(ValueError):
                onboard.save_field(name, value)

    def test_read_probe_uses_headers_and_redacts_response(self):
        onboard.save_field('ASSEMBLYAI_API_KEY', 'dummy-test-key')
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"transcripts":["private transcript"]}'
        with patch('onboard.urllib.request.urlopen', return_value=response) as network:
            result = onboard.verify_key('ASSEMBLYAI_API_KEY')
        req = network.call_args.args[0]
        self.assertEqual(req.get_method(), 'GET')
        self.assertNotIn('dummy-test-key', req.full_url)
        self.assertEqual(req.get_header('Authorization'), 'dummy-test-key')
        self.assertNotIn('private transcript', json.dumps(result))

    def test_failed_read_does_not_leak_provider_body(self):
        onboard.save_field('ASSEMBLYAI_API_KEY', 'test-key')
        import urllib.error
        error = urllib.error.HTTPError('url', 401, 'denied', {}, None)
        self.addCleanup(error.close)
        with patch('onboard.urllib.request.urlopen', side_effect=error):
            with self.assertRaisesRegex(RuntimeError, 'HTTP 401'):
                onboard.verify_key('ASSEMBLYAI_API_KEY')

    def test_loader_never_executes_shell_values(self):
        self.keys.parent.mkdir()
        self.keys.write_text('ASSEMBLYAI_API_KEY="$(touch /never-run-this)"\n')
        credentials.load_keys()
        self.assertEqual(os.environ['ASSEMBLYAI_API_KEY'], '$(touch /never-run-this)')

    def test_source_profile_prefers_aai_even_when_groq_exists(self):
        spec = importlib.util.spec_from_file_location('portable_asr_test', ROOT / 'lib/_shared/asr.py')
        asr = importlib.util.module_from_spec(spec)
        with patch.object(credentials, 'load_keys'):
            spec.loader.exec_module(asr)
        os.environ.update(ASSEMBLYAI_API_KEY='dummy-aai', GROQ_API_KEY='dummy-groq')
        audio = self.root / 'source.wav'
        audio.write_bytes(b'audio')
        aai = {'engine': 'assemblyai', 'words': [{'word': 'um', 'start': 0, 'end': 0.1}], 'text': 'um'}
        with patch.object(asr, '_wav16', return_value=str(audio)), patch.object(asr, '_assemblyai', return_value=aai) as primary, patch.object(asr, '_groq') as fallback:
            result = asr.transcribe('source.mp4', use_cache=False, profile='editorial_ingest')
        primary.assert_called_once()
        fallback.assert_not_called()
        self.assertEqual(result['engine'], 'assemblyai')
        self.assertEqual(result['evidence_status'], 'FULL')

    def test_provider_milliseconds_speakers_fillers_preserved(self):
        spec = importlib.util.spec_from_file_location('portable_asr_conversion_test', ROOT / 'lib/_shared/asr.py')
        asr = importlib.util.module_from_spec(spec)
        with patch.object(credentials, 'load_keys'):
            spec.loader.exec_module(asr)
        audio = self.root / 'clip.wav'
        audio.write_bytes(b'audio')
        native = {'id': 'transcript-1', 'status': 'completed', 'text': 'Um, yes.',
                  'words': [{'text': 'Um,', 'start': 125, 'end': 350, 'confidence': 0.9, 'speaker': 'A'}],
                  'utterances': [{'text': 'Um, yes.', 'start': 125, 'end': 1000, 'speaker': 'A'}]}
        def response(data):
            mock = MagicMock()
            mock.json.return_value = data
            return mock
        with patch('httpx.post', side_effect=[response({'upload_url': 'https://upload.invalid'}), response({'id': 'transcript-1'})]) as post, patch('httpx.get', return_value=response(native)):
            result = asr._assemblyai(str(audio), 'dummy-key', None, None, state_path=self.root / 'job.json')
        body = post.call_args.kwargs['json']
        self.assertTrue(body['disfluencies'])
        self.assertTrue(body['speaker_labels'])
        self.assertEqual(result['words'][0]['start'], 0.125)
        self.assertEqual(result['words'][0]['end'], 0.35)
        self.assertEqual(result['words'][0]['speaker'], 'A')
        self.assertEqual(result['provider_response'], native)

    def test_legacy_caption_entrypoint_selects_assemblyai_without_groq(self):
        spec = importlib.util.spec_from_file_location('portable_caption_entry', ROOT / 'skills/caption-clips/scripts/transcribe_lv3.py')
        caption = importlib.util.module_from_spec(spec)
        with patch.object(credentials, 'load_keys'):
            spec.loader.exec_module(caption)
        os.environ['ASSEMBLYAI_API_KEY'] = 'dummy-aai'
        fake = MagicMock()
        fake.transcribe.return_value = {'engine': 'assemblyai', 'words': [{'word': 'Hello', 'start': 0.1, 'end': 0.3}]}
        with patch.dict(sys.modules, {'asr': fake}), patch.object(caption.subprocess, 'run'):
            words = caption.transcribe_clip('source.mp4', 2.0, 4.0, None)
        self.assertEqual(words[0]['word'], 'Hello')
        self.assertEqual(fake.transcribe.call_args.kwargs['profile'], 'editorial_ingest')


if __name__ == '__main__':
    unittest.main()
