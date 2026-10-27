"""Offline regressions identified by the independent review-agent pass."""
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import vibe_credentials


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    with patch.object(vibe_credentials, 'load_keys'):
        spec.loader.exec_module(value)
    return value


def response(data):
    value = MagicMock()
    value.json.return_value = data
    return value


class AsrReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.audio = self.root / 'dialogue.wav'
        self.audio.write_bytes(b'first-audio')
        self.receipt = self.root / 'job.json'
        self.asr = module('review_asr', ROOT / 'lib/_shared/asr.py')
        p = patch.dict(os.environ, {'VIBE_ASR_CACHE': str(self.root / 'cache')}, clear=True)
        p.start()
        self.addCleanup(p.stop)
        self.native = {'status': 'completed', 'speech_model_used': 'universal-2', 'text': 'Um, yes.',
            'words': [{'text': 'Um,', 'start': 125, 'end': 350, 'speaker': 'A', 'confidence': 0.9}],
            'utterances': [{'text': 'Um, yes.', 'start': 125, 'end': 1000, 'speaker': 'A',
                'words': [{'text': 'Um,', 'start': 125, 'end': 350, 'speaker': 'A', 'confidence': 0.9}]}]}

    def transcribe(self):
        return self.asr._assemblyai(str(self.audio), 'dummy-key', None, None, state_path=self.receipt)

    def test_polling_interruption_resumes_existing_paid_job(self):
        with patch('httpx.post', side_effect=[response({'upload_url': 'https://upload.invalid'}), response({'id': 'job-1'})]) as post, patch('httpx.get', side_effect=TimeoutError):
            with self.assertRaises(self.asr.AsrPending):
                self.transcribe()
            self.assertEqual(post.call_count, 2)
        self.assertEqual(json.loads(self.receipt.read_text())['transcript_id'], 'job-1')
        self.assertEqual(self.receipt.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('dummy-key', self.receipt.read_text())
        with patch('httpx.post') as post, patch('httpx.get', return_value=response(self.native)) as get:
            result = self.transcribe()
            post.assert_not_called()
            self.assertTrue(get.call_args.args[0].endswith('/job-1'))
        self.assertEqual(result['provider_transcript_id'], 'job-1')

    def test_unknown_submit_cannot_blindly_resubmit(self):
        with patch('httpx.post', side_effect=[response({'upload_url': 'https://upload.invalid'}), TimeoutError]):
            with self.assertRaises(self.asr.AsrPending):
                self.transcribe()
        with patch('httpx.post') as post:
            with self.assertRaisesRegex(self.asr.AsrPending, 'unknown'):
                self.transcribe()
            post.assert_not_called()

    def test_pending_job_does_not_fall_through_to_another_asr(self):
        os.environ.update(ASSEMBLYAI_API_KEY='dummy-aai', GROQ_API_KEY='dummy-groq')
        extracted = self.root / 'extracted.wav'
        extracted.write_bytes(b'audio')
        with patch.object(self.asr, '_wav16', return_value=str(extracted)), patch.object(self.asr, '_assemblyai', side_effect=self.asr.AsrPending('poll later')), patch.object(self.asr, '_groq') as groq:
            with self.assertRaises(self.asr.AsrPending):
                self.asr.transcribe(str(self.audio), use_cache=False, profile='editorial_ingest', state_path=self.receipt)
            groq.assert_not_called()

    def test_actual_model_and_nested_word_contract_are_preserved(self):
        with patch('httpx.post', side_effect=[response({'upload_url': 'https://upload.invalid'}), response({'id': 'job-1'})]), patch('httpx.get', return_value=response(self.native)):
            result = self.transcribe()
        self.assertEqual(result['model'], 'universal-2')
        self.assertEqual(result['segments'][0]['words'], result['words'])
        self.assertEqual(result['segments'][0]['words'][0]['start'], 0.125)

    def test_cache_rejects_same_size_same_mtime_changed_audio(self):
        original = self.asr._cache_path(str(self.audio), 'audit')
        metadata = self.audio.stat()
        self.audio.write_bytes(b'other-audio')
        os.utime(self.audio, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
        self.assertNotEqual(self.asr._cache_path(str(self.audio), 'audit'), original)
        with_prompt = self.asr._cache_path(str(self.audio), 'editorial_ingest', prompt='a')
        self.assertNotEqual(with_prompt, self.asr._cache_path(str(self.audio), 'editorial_ingest', prompt='b'))


class ListenerReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.gemini = module('review_gemini', ROOT / 'lib/_shared/gemini_audio.py')

    def test_missing_first_model_tries_the_next_model(self):
        with patch.object(self.gemini, 'call', side_effect=[{'status': 'UNAVAILABLE', 'http_status': 404}, {'status': 'OK', 'result': {'tags': []}}]) as call:
            result = self.gemini.call_chain(b'audio', 'prompt', models=('unavailable', 'available'))
        self.assertEqual(result['status'], 'OK')
        self.assertEqual(call.call_count, 2)
        self.assertEqual(result['model'], 'available')

    def test_broken_cache_is_rebuilt_atomically(self):
        key = hashlib.sha256(b'audio\0prompt\0' + self.gemini.DEFAULT_MODEL.encode()).hexdigest()
        cached = self.root / (key + '.json')
        cached.write_text('{interrupted')
        body = {'usageMetadata': {}, 'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': '{"tags": []}'}]}}]}
        remote = MagicMock()
        remote.__enter__.return_value.read.return_value = json.dumps(body).encode()
        with patch.object(self.gemini, 'credential', return_value='dummy'), patch('urllib.request.urlopen', return_value=remote):
            result = self.gemini.call(b'audio', 'prompt', cache_dir=self.root, budget=self.gemini.Budget())
        self.assertEqual(result['status'], 'OK')
        self.assertEqual(json.loads(cached.read_text())['result'], {'tags': []})
        self.assertEqual(list(self.root.glob('*.pending')), [])

    def test_null_tag_array_records_unavailable_listener_evidence(self):
        delivery = module('review_null_delivery_map', ROOT / 'skills/long-form-ingest/scripts/delivery_map.py')
        words = self.root / 'words.json'
        words.write_text(json.dumps({'words': [{'word': 'one', 'start': 0, 'end': 0.5}]}))
        result = delivery.build(words, 'source.wav', self.root / 'map.json', self.root / 'cache',
            encoder=lambda *args: b'audio', caller=lambda *a, **kw: {'status': 'OK', 'result': {'tags': None}, 'usd': 0.25, 'usage': {'promptTokenCount': 10}, 'model': 'fixture-model'})
        self.assertEqual(result['coverage']['status'], 'UNAVAILABLE')
        self.assertEqual(result['tags'], [])
        self.assertIn('invalid tags', result['chunks'][0]['detail'])
        self.assertEqual(result['cost_estimate_usd'], 0.25)
        self.assertEqual(result['chunks'][0]['usage'], {'promptTokenCount': 10})
        self.assertEqual(result['chunks'][0]['model'], 'fixture-model')

    def test_plugin_artifact_contains_a_reachable_onboarding_graph(self):
        import re
        skill = ROOT / 'skills/edit/SKILL.md'
        targets = re.findall(r'\]\(([^)]+)\)', skill.read_text())
        onboarding = next((skill.parent / target).resolve() for target in targets if target.endswith('/docs/ONBOARDING.md'))
        self.assertTrue(onboarding.is_file())
        self.assertTrue(onboarding.is_relative_to(ROOT))
        for document in (onboarding, ROOT / 'docs/CONNECTIONS.md', ROOT / 'docs/INSTALL.md'):
            for target in re.findall(r'\]\(([^)]+)\)', document.read_text()):
                if '://' in target or target.startswith('#'):
                    continue
                path = (document.parent / target.split('#')[0]).resolve()
                self.assertTrue(path.is_relative_to(ROOT), (document, target))
                self.assertTrue(path.is_file(), (document, target))

    def test_failed_listener_chunk_is_recorded_and_other_chunks_continue(self):
        delivery = module('review_delivery_map', ROOT / 'skills/long-form-ingest/scripts/delivery_map.py')
        words = self.root / 'words.json'
        words.write_text(json.dumps({'words': [{'word': 'one', 'start': 0, 'end': 0.5}, {'word': 'two', 'start': 20, 'end': 20.5}]}))
        def encoder(audio, start, duration):
            if start < 1:
                raise OSError('fixture error')
            return b'audio'
        result = delivery.build(words, 'source.wav', self.root / 'map.json', self.root / 'cache', chunk_s=10,
            encoder=encoder, caller=lambda *a, **kw: {'status': 'OK', 'result': {'tags': []}}, transcriber=lambda *a: '')
        self.assertEqual(result['coverage']['status'], 'PARTIAL')
        self.assertEqual(result['coverage']['available'], 1)
        self.assertEqual(result['chunks'][0]['status'], 'UNAVAILABLE')


if __name__ == '__main__':
    unittest.main()
