"""Portable links-first delivery. Plan is offline; push requires explicit batch authorization."""
import argparse
import concurrent.futures
import datetime
import fcntl
import json
import re
import time
import urllib.request
from pathlib import Path

from delivery_ledger import Ledger, atomic_json, fingerprint
from frameio_client import FrameClient, UA
from monday_client import MondayClient, CFG

COMPLETE_STATUSES = {'transcoded', 'ready'}


def token(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9-]+', value):
        raise ValueError('Naming fields must be nonempty letters/numbers/hyphens, without underscores.')
    return value


def make_plan(config, brand, clips, content_type='shorts'):
    profile = config['brands'][brand]
    route = profile['routes'][content_type]
    if not clips:
        raise ValueError('Select at least one exact clip.')
    files = []
    for clip in clips:
        p = Path(clip['path']).expanduser().resolve()
        if not p.is_file() or p.suffix.lower() != '.mp4' or not p.stat().st_size:
            raise ValueError('Each clip must be a nonempty local MP4.')
        date = clip['shoot_date']
        datetime.datetime.strptime(date, '%Y%m%d')
        if not re.fullmatch(r'\d{8}', date):
            raise ValueError('Shoot date must be YYYYMMDD.')
        version = clip.get('version', 1)
        if type(version) is not int or version < 1:
            raise ValueError('Version must be a positive integer.')
        # Title is supplied from the clip, never inferred from a generic export name.
        stem = '_'.join([token(profile['code']), token(route['content_code']),
                         token(clip['source']), token(clip['title']),
                         token(config['editor']), date])
        entry = {**fingerprint(p), 'name': f'{stem}_V{version}.mp4',
                 'item_name': stem, 'version': version}
        if version > 1:
            prior = clip.get('previous') or {}
            if not all(prior.get(k) for k in ('file_id', 'monday_item_id', 'view_url')):
                raise ValueError('Revisions need exact prior file ID, Monday item ID and original link.')
            entry['previous'] = prior
        files.append(entry)
    if len({f['name'] for f in files}) != len(files) or len({Path(f['path']).name for f in files}) != len(files):
        raise ValueError('Duplicate file or delivery name in batch.')
    return {'schema': 'vibe-delivery-plan/v1', 'brand': brand, 'content_type': content_type,
            'route': route, 'files': files}


def verify_route(fc, mc, route):
    f = route['frame']
    base = f"/v4/accounts/{f['account_id']}"
    folder = fc.get(f"{base}/folders/{f['folder_id']}")['data']
    if folder['name'] != f['folder_name'] or str(folder['project_id']) != str(f['project_id']):
        raise ValueError('Frame destination changed. Reconfirm the saved route.')
    project = fc.get(f"{base}/projects/{f['project_id']}")['data']
    if project['name'] != f['project_name']:
        raise ValueError('Frame project name changed. Reconfirm the saved route.')
    m = route['monday']
    board = mc.board(m['board_id'])
    if board['name'] != m['board_name']:
        raise ValueError('Monday board name changed. Reconfirm the saved route.')
    groups = {g['id']: g['title'] for g in board['groups']}
    for prefix in ('', 'revision_'):
        if groups.get(m[prefix + 'group_id']) != m[prefix + 'group_name']:
            raise ValueError('Monday group changed. Reconfirm the saved route.')
    columns = {c['id']: c for c in board['columns']}
    for key, kind in [('link_column_id', 'link'), ('status_column_id', 'status')]:
        if columns.get(m[key], {}).get('type') != kind:
            raise ValueError('Configured Monday link/status column is missing or changed.')
    settings = json.loads(columns[m['status_column_id']]['settings_str'])
    labels = settings.get('labels', {}).values()
    if m['status_label'] not in labels or m['revision_status_label'] not in labels:
        raise ValueError('Requested Monday status does not exist; do not create a label.')
    return base


def verify_item(mc, route, item_id, name, url, final=False, revision=False):
    item = mc.item(item_id)
    cols = {c['id']: c for c in item['column_values']}
    link = json.loads(cols[route['link_column_id']]['value'] or '{}').get('url')
    if str(item['board']['id']) != str(route['board_id']) or item['name'] != name or link != url:
        raise ValueError('Monday item identity or Frame link mismatch.')
    prefix = 'revision_' if revision else ''
    if final and (item['group']['id'] != route[prefix + 'group_id'] or
                  cols[route['status_column_id']]['text'] != route[prefix + 'status_label']):
        raise ValueError('Monday status/group not finalized; board automation may still be running.')
    return item


def upload_parts(entry, urls):
    if sum(int(part['size']) for part in urls) != entry['bytes']:
        raise ValueError('Upload part sizes do not match the approved file.')
    with open(entry['path'], 'rb') as source:
        for part in urls:
            data = source.read(int(part['size']))
            req = urllib.request.Request(part['url'], data=data, method='PUT', headers={
                'Content-Type': 'video/mp4', 'x-amz-acl': 'private', 'User-Agent': UA})
            # Presigned S3 URLs must never receive Adobe/Monday credentials.
            with urllib.request.urlopen(req, timeout=900) as response:
                response.read()


def push(plan, ledger_path, fc=None, mc=None, wait_seconds=60):
    if plan.get('schema') != 'vibe-delivery-plan/v1' or not plan.get('files'):
        raise ValueError('Invalid or empty delivery plan.')
    for entry in plan['files']:
        current = fingerprint(entry['path'])
        if any(current[k] != entry[k] for k in ('path', 'bytes', 'sha256')):
            raise ValueError('Approved file changed. Rebuild the plan before any external write.')
    if len({Path(e['path']).name for e in plan['files']}) != len(plan['files']):
        raise ValueError('Batch requires unique local filenames.')
    fc, mc = fc or FrameClient(), mc or MondayClient()
    route = plan['route']
    base = verify_route(fc, mc, route)
    folder = route['frame']['folder_id']
    monday = route['monday']
    paths = [e['path'] for e in plan['files']]
    # Bind remote names, route, revision IDs and full local content to this ledger.
    ledger = Ledger(ledger_path, plan, paths)
    existing = {x['name'] for x in fc.paginate(f'{base}/folders/{folder}/children')}
    monday_names = {x['name'] for x in mc.names(monday['board_id'])}
    for entry in plan['files']:
        name = entry['name']
        state = ledger.row(name=Path(entry['path']).name)
        # Ledger keys follow the original local filename; names in plan are remote SOP names.
        if not state.get('fid') and name in existing:
            raise ValueError('Remote filename collision without ledger ownership: ' + name)
        if not entry.get('previous') and not state.get('monday_id') and entry['item_name'] in monday_names:
            raise ValueError('Monday name collision without ledger ownership: ' + entry['item_name'])
        if entry.get('previous'):
            prior = entry['previous']
            old = fc.get(f"{base}/files/{prior['file_id']}")['data']
            expected_old = re.sub(r'_V\d+\.mp4$', f"_V{entry['version'] - 1}.mp4", name)
            if old['name'] != expected_old or old['view_url'] != prior['view_url']:
                raise ValueError('Prior version ID/name/link mismatch; versions must increase by one.')
            if str(old['parent_id']) != str(folder):
                # Existing stacks require the documented append flow, not a second stack.
                if not state.get('stack_id'):
                    raise ValueError('Prior file is in a version stack. Use references/revisions.md.')
            verify_item(mc, monday, prior['monday_item_id'], entry['item_name'], prior['view_url'])

    def mutation(key, step, call, result_key):
        state = ledger.row(key)
        if state.get(result_key):
            return state[result_key]
        ledger.intent(key, step)
        try:
            result = call()
            if not result:
                raise ValueError('Missing remote result; reconcile before retry.')
            ledger.update(key, **{result_key: result, step: 'confirmed'})
            return result
        except Exception:
            # Never serialize signed URLs or API responses into errors.
            ledger.failed(key, step, 'Unknown outcome. Reconcile exact IDs before retry.')
            raise

    # All placeholders + board links first, followed by bounded parallel uploads.
    for entry in plan['files']:
        key = Path(entry['path']).name
        state = ledger.row(key)
        def create(entry=entry):
            response = fc.post(f'{base}/folders/{folder}/files/local_upload', {
                'data': {'name': entry['name'], 'file_size': entry['bytes']}})['data']
            # Retain identity immediately, even if a later response field is missing.
            ledger.update(key, fid=response['id'], upload_urls=response.get('upload_urls'))
            return response['id']
        fid = mutation(key, 'create', create, 'fid')
        remote = fc.get(f'{base}/files/{fid}')['data']
        allowed_parent = state.get('stack_id', folder)
        if (remote['name'] != entry['name'] or str(remote['parent_id']) != str(allowed_parent)
                or int(remote['file_size']) != entry['bytes']):
            raise ValueError('Recorded Frame file identity/size/folder changed.')
        url = remote.get('view_url')
        if not url:
            raise ValueError('Frame link pending. Resume this ledger.')
        ledger.update(key, view_url=url)
        if entry.get('previous'):
            ledger.update(key, monday_id=str(entry['previous']['monday_item_id']))
        else:
            item_id = mutation(key, 'monday', lambda: mc.create(monday, entry['item_name'], url), 'monday_id')
            item_id = str(item_id)
            ledger.update(key, monday_id=item_id)
            verify_item(mc, monday, item_id, entry['item_name'], url)

    def upload(entry):
        key = Path(entry['path']).name
        state = ledger.row(key)
        remote = fc.get(f"{base}/files/{state['fid']}")['data']
        if remote.get('status') in COMPLETE_STATUSES or state.get('bytes') == 'complete':
            return
        if state.get('bytes') in ('dispatching', 'unknown'):
            upload_status = fc.get(f"{base}/files/{state['fid']}/status")['data']
            if upload_status.get('upload_complete') is True and not upload_status.get('upload_failed'):
                ledger.update(key, bytes='complete')
                return
            raise RuntimeError('Unknown prior outcome: upload is incomplete or failed; reconcile the recorded asset before retry.')
        if fingerprint(entry['path']) != next(x for x in ledger.data['files'] if x['path'] == entry['path']):
            raise ValueError('Local file changed after planning.')
        ledger.intent(key, 'bytes')
        try:
            upload_parts(entry, state['upload_urls'])
            ledger.update(key, bytes='complete')
        except Exception:
            ledger.failed(key, 'bytes', 'Upload outcome unknown; inspect recorded asset before retry.')
            raise
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(upload, plan['files']))

    deadline = time.monotonic() + wait_seconds
    while True:
        ready = []
        for entry in plan['files']:
            state = ledger.row(Path(entry['path']).name)
            remote = fc.get(f"{base}/files/{state['fid']}")['data']
            ledger.update(Path(entry['path']).name, remote_status=remote.get('status'))
            ready.append(remote.get('status') in COMPLETE_STATUSES)
        if all(ready):
            break
        if time.monotonic() >= deadline:
            return {'complete': False, 'message': 'Uploads recorded; transcoding pending. Resume this same plan and ledger.'}
        time.sleep(5)
    results = []
    for entry in plan['files']:
        key = Path(entry['path']).name
        state = ledger.row(key)
        revision = bool(entry.get('previous'))
        if revision:
            prior = entry['previous']
            stack_id = mutation(key, 'stack', lambda: fc.post(f'{base}/folders/{folder}/version_stacks', {
                'data': {'file_ids': [prior['file_id'], state['fid']]}})['data']['id'], 'stack_id')
            children = list(fc.paginate(f'{base}/version_stacks/{stack_id}/children'))
            old = fc.get(f"{base}/files/{prior['file_id']}")['data']
            if [str(c['id']) for c in children[:2]] != [str(state['fid']), str(prior['file_id'])] or str(old['parent_id']) != str(stack_id):
                raise ValueError('Version stack verification failed.')
        url = entry['previous']['view_url'] if revision else state['view_url']
        prefix = 'revision_' if revision else ''
        verify_item(mc, monday, state['monday_id'], entry['item_name'], url)
        # Setting the same status by exact ID is idempotent and triggers board automations.
        mc.status(monday, state['monday_id'], monday[prefix + 'status_label'])
        item = verify_item(mc, monday, state['monday_id'], entry['item_name'], url, final=True, revision=revision)
        ledger.update(key, verified=True)
        results.append({'name': entry['name'], 'frame_url': url, 'monday_url': item['url']})
    return {'complete': True, 'files': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('plan')
    p.add_argument('--config', type=Path, default=CFG / 'routes.json')
    p.add_argument('--brand', required=True)
    p.add_argument('--type', default='shorts')
    p.add_argument('--clips', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p = commands.add_parser('push')
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--ledger', type=Path, required=True)
    p.add_argument('--approved', action='store_true', help='Use only for this batch explicitly requested by the user.')
    args = parser.parse_args()
    if args.command == 'plan':
        plan = make_plan(json.loads(args.config.read_text()), args.brand,
                         json.loads(args.clips.read_text()), args.type)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(args.out, plan)
        print(json.dumps(plan, indent=2))
    else:
        if not args.approved:
            parser.error('Push requires the user to request this exact batch, then --approved.')
        plan = json.loads(args.plan.read_text())
        args.ledger.parent.mkdir(parents=True, exist_ok=True)
        # Prevent concurrent processes from racing the durable ownership ledger.
        with open(str(args.ledger) + '.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            print(json.dumps(push(plan, args.ledger), indent=2))


if __name__ == '__main__':
    main()
