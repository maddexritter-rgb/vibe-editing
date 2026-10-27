"""Private credential entry and read-only identity check. Run in an interactive terminal."""
import argparse
import getpass
from delivery_ledger import atomic_json
from monday_client import CFG, MondayClient
from frameio_client import FrameClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['credentials', 'check'])
    args = parser.parse_args()
    if args.action == 'credentials':
        CFG.mkdir(parents=True, exist_ok=True)
        CFG.chmod(0o700)
        frame = CFG / 'frameio'
        frame.mkdir(exist_ok=True)
        frame.chmod(0o700)
        # getpass keeps secrets out of chat, command arguments and terminal history.
        monday = getpass.getpass('Your Monday personal API token (blank keeps existing): ')
        if monday.strip():
            atomic_json(CFG / 'monday.json', {'token': monday.strip()})
        client_id = getpass.getpass('Adobe OAuth Web App client ID (blank keeps existing): ')
        if client_id.strip():
            secret = getpass.getpass('Adobe client secret: ')
            if not secret.strip():
                raise ValueError('Client secret is required.')
            atomic_json(frame / 'credentials.json', {'client_id': client_id.strip(), 'client_secret': secret.strip()})
        print('Saved privately. Next: run setup_frameio_oauth.py to sign into Frame.')
    else:
        fc, mc = FrameClient(), MondayClient()
        f = fc.get('/v4/me')['data']
        m = mc.gql('{me {id name account {id name}}}')['me']
        print('Frame:', f['name'], '| Monday:', m['name'], '/', m['account']['name'])
        print('Identity verified. Destinations still need verification before delivery.')


if __name__ == '__main__':
    main()
