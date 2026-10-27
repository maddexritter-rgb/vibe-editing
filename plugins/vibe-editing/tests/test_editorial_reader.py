"""The active agent retrieves verified documents with a team read credential, not a model key."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import editorial_brain


class EditorialReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.credentials = self.root / 'private.json'
        self.credentials.write_text(json.dumps({'token': 'dummy-test-token-' * 4}))
        self.config = self.root / 'connection.json'
        self.settings = {'function_url': 'https://project.supabase.co/functions/v1/vibe-editorial-read',
                         'project_ref': 'project', 'credential_file': str(self.credentials)}
        self.config.write_text(json.dumps(self.settings))
        body = 'Editorial instructions for the active agent.'
        digest = hashlib.sha256(body.encode()).hexdigest()
        self.context = {'schema': 'editorial-brain.context/v1', 'project_ref': 'project',
            'playbooks': [{'format': fmt, 'body': body, 'body_sha256': digest} for fmt in ('universal', 'monologue')],
            'required_reading': {'policies': [{'format': fmt, 'documents': [{'source_body': body, 'source_sha256': digest}]} for fmt in ('universal', 'monologue')]}}

    def test_team_configuration_needs_no_individual_provider_signup(self):
        config, token = editorial_brain.read_connection(self.config)
        self.assertEqual(token, 'dummy-test-token-' * 4)
        self.assertEqual(config['project_ref'], 'project')

    def test_bundled_team_token_works_without_a_private_credential_file(self):
        self.settings['token'] = 'bundled-read-only-fixture-' * 3
        self.settings.pop('credential_file')
        self.config.write_text(json.dumps(self.settings))
        self.credentials.unlink()
        config, token = editorial_brain.read_connection(self.config)
        self.assertEqual(token, 'bundled-read-only-fixture-' * 3)
        self.assertEqual(config['project_ref'], 'project')

    def test_unprovisioned_installation_does_not_request_a_new_token(self):
        self.credentials.unlink()
        with self.assertRaisesRegex(ValueError, 'editors do not create their own'):
            editorial_brain.read_connection(self.config)

    def test_wrong_endpoint_is_rejected_before_using_credential(self):
        self.settings['function_url'] = 'https://other.example/functions/v1/vibe-editorial-read'
        self.config.write_text(json.dumps(self.settings))
        with self.assertRaisesRegex(ValueError, 'refusing'):
            editorial_brain.read_connection(self.config)

    def test_verified_document_fetch_uses_bearer_header_and_private_receipt(self):
        remote = MagicMock()
        remote.__enter__.return_value.read.return_value = json.dumps(self.context).encode()
        opener = MagicMock()
        opener.open.return_value = remote
        with patch('editorial_brain.urllib.request.build_opener', return_value=opener):
            result = editorial_brain.fetch('monologue', self.root / 'context.json', self.config)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + 'dummy-test-token-' * 4)
        self.assertNotIn('dummy-test-token', request.full_url)
        self.assertNotIn('dummy-test-token', json.dumps(result))
        self.assertEqual((self.root / 'context.json').stat().st_mode & 0o777, 0o600)

    def test_wrong_project_or_changed_document_is_rejected(self):
        bad_project = {**self.context, 'project_ref': 'other'}
        with self.assertRaises(ValueError):
            editorial_brain.validate_context(bad_project, 'project', 'monologue')
        self.context['playbooks'][0]['body'] = 'unexpected source mutation'
        with self.assertRaisesRegex(ValueError, 'hash'):
            editorial_brain.validate_context(self.context, 'project', 'monologue')


if __name__ == '__main__':
    unittest.main()
