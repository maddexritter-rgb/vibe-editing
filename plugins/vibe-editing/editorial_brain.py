"""Read team editorial documents for the active model; no LLM API or database-write access."""
import argparse
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from onboard import private_json

CONFIG = Path(__file__).resolve().parent / 'config/editorial-brain.json'
FORMATS = ('monologue', 'q_and_a', 'podcast', 'story')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward a team credential to another URL.


def read_connection(config_path=CONFIG, credential_path=None):
    config = json.loads(Path(config_path).read_text())
    url = urllib.parse.urlparse(config['function_url'])
    if (url.scheme != 'https' or url.hostname != config['project_ref'] + '.supabase.co'
            or url.path != '/functions/v1/vibe-editorial-read' or url.query or url.fragment):
        raise ValueError('Unexpected editorial-brain endpoint; refusing to send the team credential.')
    token = config.get('token')
    if not token:
        path = Path(credential_path or config['credential_file']).expanduser()
        if not path.is_file():
            raise ValueError('Team access has not been provisioned on this installation. Use bundled editorial documents; editors do not create their own brain token.')
        token = json.loads(path.read_text()).get('token')
    if not isinstance(token, str) or len(token.strip()) < 32:
        raise ValueError('Team editorial credential is missing or invalid.')
    return config, token.strip()


def validate_context(data, project, fmt):
    if not isinstance(data, dict) or data.get('schema') != 'editorial-brain.context/v1' or data.get('project_ref') != project:
        raise ValueError('Editorial context has the wrong schema or project.')
    expected = {'universal', fmt}
    books = data.get('playbooks')
    if not isinstance(books, list) or len(books) != 2 or {x.get('format') for x in books} != expected:
        raise ValueError('The expected active playbooks are missing.')
    for book in books:
        body = book.get('body')
        if not isinstance(body, str) or not body or hashlib.sha256(body.encode()).hexdigest() != book.get('body_sha256'):
            raise ValueError('Editorial playbook body does not match its source hash.')
    policies = (data.get('required_reading') or {}).get('policies')
    if not isinstance(policies, list) or len(policies) != 2 or {p.get('format') for p in policies} != expected:
        raise ValueError('Required editorial reading is missing.')
    for policy in policies:
        if not policy.get('documents'):
            raise ValueError('Required reading contains no documents.')
        for doc in policy['documents']:
            body = doc.get('source_body')
            if not isinstance(body, str) or not body or hashlib.sha256(body.encode()).hexdigest() != doc.get('source_sha256'):
                raise ValueError('Required editorial document has an invalid source hash.')
    return data


def fetch(fmt, out, config_path=CONFIG, credential_path=None):
    if fmt not in FORMATS:
        raise ValueError('Unsupported source format.')
    if Path(out).exists():
        raise ValueError('Reuse the existing document receipt or choose a new revision path.')
    config, token = read_connection(config_path, credential_path)
    body = json.dumps({'problem': 'source editorial construction guidance', 'format': fmt,
                       'limit': 1, 'include_required_reading': True}).encode()
    request = urllib.request.Request(config['function_url'], data=body, method='POST', headers={
        'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=45) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'Editorial read failed: HTTP {exc.code}') from None
    except (urllib.error.URLError, OSError):
        raise RuntimeError('Editorial read could not connect.') from None
    validate_context(data, config['project_ref'], fmt)
    private_json(out, data)
    return {'playbooks': len(data['playbooks']), 'required_policies': len(data['required_reading']['policies']),
            'path': str(Path(out).resolve()), 'note': 'The active agent must read the documents; retrieval is not proof of reading or approval.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--format', choices=FORMATS, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--credentials', type=Path, help='Admin-provisioned private team credential file, never a token value.')
    args = parser.parse_args()
    print(json.dumps(fetch(args.format, args.out, credential_path=args.credentials), indent=2))


if __name__ == '__main__':
    main()
