"""Monday client with explicit board routing; never retries a mutation."""
import json
from pathlib import Path
import urllib.request
import urllib.error

CFG = Path.home() / '.config/vibe-editing/delivery'


class MondayClient:
    def __init__(self):
        self.token = json.loads((CFG / 'monday.json').read_text())['token']

    def gql(self, query, variables=None):
        req = urllib.request.Request('https://api.monday.com/v2',
            data=json.dumps({'query': query, 'variables': variables or {}}).encode(),
            headers={'Authorization': self.token, 'Content-Type': 'application/json',
                     'API-Version': '2026-07'})
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                result = json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise RuntimeError(f'Monday request failed: HTTP {error.code}') from None
        if result.get('errors') or not result.get('data'):
            raise RuntimeError('Monday request failed; inspect the board permissions/schema before retrying.')
        return result['data']

    def board(self, board_id):
        rows = self.gql('''query($ids:[ID!]) { boards(ids:$ids) {
            id name groups {id title} columns {id title type settings_str} } }''', {'ids': [board_id]})['boards']
        if len(rows) != 1:
            raise ValueError('Configured Monday board is inaccessible.')
        return rows[0]

    def item(self, item_id):
        rows = self.gql('''query($ids:[ID!]) { items(ids:$ids) {
            id name url board {id} group {id} column_values {id text value} } }''', {'ids': [item_id]})['items']
        if len(rows) != 1:
            raise ValueError('Recorded Monday item is inaccessible.')
        return rows[0]

    def names(self, board_id):
        page = self.gql('''query($ids:[ID!]) { boards(ids:$ids) {
            items_page(limit:500) {cursor items {id name}} } }''', {'ids': [board_id]})['boards'][0]['items_page']
        while True:
            yield from page['items']
            if not page.get('cursor'):
                break
            page = self.gql('''query($cursor:String!) { next_items_page(limit:500,cursor:$cursor)
                {cursor items {id name}} }''', {'cursor': page['cursor']})['next_items_page']

    def create(self, route, name, url):
        values = {route['link_column_id']: {'url': url, 'text': 'Frame.io'}}
        return self.gql('''mutation($b:ID!,$g:String!,$n:String!,$c:JSON!) {
            create_item(board_id:$b,group_id:$g,item_name:$n,column_values:$c){id} }''',
            {'b': route['board_id'], 'g': route['group_id'], 'n': name,
             'c': json.dumps(values)})['create_item']['id']

    def status(self, route, item_id, label):
        return self.gql('''mutation($b:ID!,$i:ID!,$c:String!,$v:JSON!) {
            change_column_value(board_id:$b,item_id:$i,column_id:$c,value:$v){id} }''',
            {'b': route['board_id'], 'i': item_id, 'c': route['status_column_id'],
             'v': json.dumps({'label': label})})
