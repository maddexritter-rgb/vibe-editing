"""Offline contract tests. No credentials or live accounts are loaded."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deliver import make_plan, push, upload_parts
from delivery_ledger import Ledger, fingerprint


def config():
    route = {'content_code': 'SF', 'frame': {'account_id': 'account-a', 'project_id': 'project-a',
             'project_name': 'Project A', 'folder_id': 'folder-a', 'folder_name': 'Exports'},
             'monday': {'board_id': 'board-a', 'board_name': 'Shorts A', 'group_id': 'review',
             'group_name': 'For Review', 'status_column_id': 'status', 'status_label': 'For Review',
             'link_column_id': 'link', 'revision_group_id': 'final', 'revision_group_name': 'Final Review',
             'revision_status_label': 'Final Review'}}
    other = copy.deepcopy(route)
    other['frame'].update(account_id='account-b', project_id='project-b', project_name='Project B', folder_id='folder-b')
    other['monday'].update(board_id='board-b', board_name='Shorts B')
    return {'editor': 'ED', 'brands': {'one': {'code': 'ONE', 'routes': {'shorts': route}},
            'two': {'code': 'TWO', 'routes': {'shorts': other}}}}


class Frame:
    def __init__(self, route):
        self.route, self.files, self.stacks, self.created = route['frame'], {}, {}, 0

    def get(self, path):
        if path.endswith('/status'):
            return {'data': {'upload_complete': False, 'upload_failed': False}}
        if '/files/' in path:
            return {'data': self.files[path.rsplit('/', 1)[-1]]}
        if '/folders/' in path:
            return {'data': {'name': self.route['folder_name'], 'project_id': self.route['project_id']}}
        return {'data': {'name': self.route['project_name']}}

    def paginate(self, path):
        if '/version_stacks/' in path:
            return self.stacks[path.split('/')[-2]]
        return list(self.files.values())

    def post(self, path, body):
        if path.endswith('/version_stacks'):
            old, new = body['data']['file_ids']
            self.stacks['stack'] = [self.files[new], self.files[old]]
            self.files[new]['parent_id'] = self.files[old]['parent_id'] = 'stack'
            return {'data': {'id': 'stack'}}
        self.created += 1
        data = body['data']
        fid = str(self.created)
        self.files[fid] = {**data, 'id': fid, 'parent_id': self.route['folder_id'], 'status': 'transcoded',
            'view_url': 'https://next.frame.io/view/' + fid, 'upload_urls': [{'size': data['file_size'], 'url': 'https://upload.invalid'}]}
        return {'data': self.files[fid]}


class Monday:
    def __init__(self, route):
        self.route, self.items, self.created = route['monday'], {}, 0

    def board(self, board_id):
        r = self.route
        return {'id': board_id, 'name': r['board_name'], 'groups': [
            {'id': 'review', 'title': 'For Review'}, {'id': 'final', 'title': 'Final Review'}],
            'columns': [{'id': 'link', 'type': 'link'}, {'id': 'status', 'type': 'status',
            'settings_str': json.dumps({'labels': {'0': 'For Review', '1': 'Final Review'}})}]}

    def names(self, board_id):
        return list(self.items.values())

    def create(self, route, name, url):
        self.created += 1
        iid = str(self.created)
        self.items[iid] = {'id': iid, 'name': name, 'url': 'https://test.monday.com/boards/1/pulses/' + iid,
            'board': {'id': route['board_id']}, 'group': {'id': 'review'},
            'column_values': [{'id': 'link', 'value': json.dumps({'url': url}), 'text': url},
                              {'id': 'status', 'value': '{}', 'text': ''}]}
        return iid

    def item(self, iid):
        return self.items[iid]

    def status(self, route, iid, label):
        self.items[iid]['column_values'][1]['text'] = label
        self.items[iid]['group']['id'] = 'final' if label == 'Final Review' else 'review'


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.file = self.root / 'clip.mp4'
        self.file.write_bytes(b'not-real-video-but-stable-content')
        self.clip = {'path': str(self.file), 'source': 'DTC', 'title': 'BetterHabits',
                     'shoot_date': '20261026', 'version': 1}
        self.plan = make_plan(config(), 'one', [self.clip])
        self.fc, self.mc = Frame(self.plan['route']), Monday(self.plan['route'])
        self.ledger = self.root / 'batch.ledger.json'

    def run_push(self, plan=None):
        return push(plan or self.plan, self.ledger, self.fc, self.mc, wait_seconds=0)

    def test_naming_and_local_original_untouched(self):
        result = self.run_push()
        self.assertTrue(result['complete'])
        self.assertEqual(result['files'][0]['name'], 'ONE_SF_DTC_BetterHabits_ED_20261026_V1.mp4')
        self.assertEqual(self.mc.items['1']['name'], 'ONE_SF_DTC_BetterHabits_ED_20261026')
        self.assertTrue(self.file.exists())

    def test_explicit_brand_routes_and_no_first_brand_fallback(self):
        other = make_plan(config(), 'two', [self.clip])
        self.assertEqual(other['route']['frame']['folder_id'], 'folder-b')
        self.assertEqual(other['route']['monday']['board_id'], 'board-b')
        with self.assertRaises(KeyError):
            make_plan(config(), 'missing', [self.clip])

    def test_same_ledger_resume_does_not_duplicate(self):
        self.run_push()
        self.assertTrue(self.run_push()['complete'])
        self.assertEqual((self.fc.created, self.mc.created), (1, 1))

    def test_same_size_file_change_stops_before_network(self):
        self.file.write_bytes(b'x' * self.file.stat().st_size)
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.run_push()
        self.assertEqual(self.fc.created, 0)

    def test_stale_destination_stops_before_write(self):
        self.fc.route = {**self.fc.route, 'project_name': 'Wrong Project'}
        with self.assertRaisesRegex(ValueError, 'project name changed'):
            self.run_push()
        self.assertEqual(self.fc.created, 0)

    def test_remote_name_collision_not_adopted(self):
        self.fc.files['unowned'] = {'name': self.plan['files'][0]['name']}
        with self.assertRaisesRegex(ValueError, 'collision'):
            self.run_push()
        self.assertEqual(self.fc.created, 0)

    def test_unknown_create_outcome_not_retried(self):
        with patch.object(self.fc, 'post', side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                self.run_push()
        with self.assertRaisesRegex(RuntimeError, 'Unknown prior outcome'):
            self.run_push()
        self.assertEqual(self.fc.created, 0)

    def test_pending_transcode_resumes_by_id(self):
        original = self.fc.post
        def created(path, body):
            result = original(path, body)
            result['data']['status'] = 'created'
            return result
        with patch.object(self.fc, 'post', side_effect=created), patch('deliver.upload_parts') as upload:
            self.assertFalse(self.run_push()['complete'])
            upload.assert_called_once()
        self.fc.files['1']['status'] = 'transcoded'
        self.assertTrue(self.run_push()['complete'])
        self.assertEqual(self.fc.created, 1)

    def test_unknown_upload_outcome_stops(self):
        original = self.fc.post
        def created(path, body):
            result = original(path, body)
            result['data']['status'] = 'created'
            return result
        with patch.object(self.fc, 'post', side_effect=created), patch('deliver.upload_parts', side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                self.run_push()
        with self.assertRaisesRegex(RuntimeError, 'Unknown prior outcome'):
            self.run_push()
        self.assertEqual(self.fc.created, 1)

    def test_v2_preserves_card_link_and_stacks(self):
        old_result = self.run_push()
        revised = copy.deepcopy(self.clip)
        revised.update(version=2, previous={'file_id': '1', 'monday_item_id': '1',
            'view_url': old_result['files'][0]['frame_url']})
        plan = make_plan(config(), 'one', [revised])
        self.ledger = self.root / 'v2.ledger.json'
        result = self.run_push(plan)
        self.assertTrue(result['complete'])
        self.assertEqual(self.mc.created, 1)
        self.assertEqual(result['files'][0]['frame_url'], old_result['files'][0]['frame_url'])
        self.assertEqual(self.fc.stacks['stack'][0]['id'], '2')
        self.assertTrue(self.run_push(plan)['complete'])

    def test_ready_assets_complete_without_reupload(self):
        original = self.fc.post
        def created(path, body):
            response = original(path, body)
            response['data']['status'] = 'ready'
            return response
        with patch.object(self.fc, 'post', side_effect=created), patch('deliver.upload_parts') as upload:
            self.assertTrue(self.run_push()['complete'])
            upload.assert_not_called()
        self.assertTrue(self.run_push()['complete'])
        self.assertEqual(self.fc.created, 1)

    def test_uncertain_upload_reconciles_complete_bytes_by_exact_id(self):
        self.run_push()
        data = json.loads(self.ledger.read_text())
        data['entries']['clip.mp4']['bytes'] = 'unknown'
        self.ledger.write_text(json.dumps(data))
        self.fc.files['1']['status'] = 'processing'
        original = self.fc.get
        def get(path):
            if path.endswith('/status'):
                return {'data': {'upload_complete': True, 'upload_failed': False}}
            return original(path)
        with patch.object(self.fc, 'get', side_effect=get), patch('deliver.upload_parts') as upload:
            self.assertFalse(self.run_push()['complete'])
            upload.assert_not_called()
        self.assertEqual(json.loads(self.ledger.read_text())['entries']['clip.mp4']['bytes'], 'complete')
        self.fc.files['1']['status'] = 'ready'
        self.assertTrue(self.run_push()['complete'])

    def test_missing_revision_ids_and_bad_date_stop(self):
        for update in [{'version': 2}, {'shoot_date': '20260230'}]:
            with self.assertRaises(ValueError):
                make_plan(config(), 'one', [{**self.clip, **update}])

    def test_private_ledger_and_route_change_rejected(self):
        self.run_push()
        self.assertEqual(self.ledger.stat().st_mode & 0o777, 0o600)
        plan = copy.deepcopy(self.plan)
        plan['route']['monday']['status_label'] = 'Final Review'
        with self.assertRaisesRegex(ValueError, 'scope/content mismatch'):
            self.run_push(plan)

    def test_signed_upload_headers_part_sizes_and_no_auth(self):
        entry = self.plan['files'][0]
        with patch('deliver.urllib.request.urlopen') as request:
            upload_parts(entry, [{'size': 4, 'url': 'https://upload.invalid/one'},
                                 {'size': entry['bytes'] - 4, 'url': 'https://upload.invalid/two'}])
            requests = [call.args[0] for call in request.call_args_list]
            self.assertEqual(b''.join(r.data for r in requests), self.file.read_bytes())
            self.assertTrue(all(r.get_header('Content-type') == 'video/mp4' for r in requests))
            self.assertTrue(all(not r.get_header('Authorization') for r in requests))


if __name__ == '__main__':
    unittest.main()
