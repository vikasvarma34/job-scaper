"""Explicit live smoke test: only create/modify/delete uniquely tagged disposable rows.
Run manually: .venv/bin/python tests/check_cover_letter_supabase.py --live
Never applies migrations or operates on existing user documents.
"""
import base64
import copy
import json
import logging
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run():
    import app as application
    import supabase_utils
    logging.disable(logging.CRITICAL)
    company = 'Codex disposable cover letter test ' + str(uuid.uuid4())
    created = set()
    client = application.app.test_client()
    password = os.environ.get('DASHBOARD_PASSWORD', '').strip()
    headers = {'Authorization': 'Basic ' + base64.b64encode(('test:' + password).encode()).decode()} if password else {}
    base = '/api/builder/cover-letter'
    data = json.loads((ROOT / 'cover_letter_builder.json').read_text())
    data['recipient']['company'] = company
    payload = dict(cover_letter_data=data, file_name='Disposable-A.pdf')

    def request(method, path, expected, body=None):
        response = client.open(base + path, method=method, headers=headers, json=body)
        if response.status_code != expected:
            # Never print connection details or database responses.
            raise RuntimeError(f'{method} {path.split("/")[0]} returned HTTP {response.status_code}; expected {expected}')
        return response.get_json()

    try:
        preflight = supabase_utils.supabase.table('saved_cover_letters').select('id').eq('company_name', company).limit(1).execute()
        if preflight.data:
            raise RuntimeError('Disposable test marker unexpectedly already exists')
        first = request('POST', '/save', 200, payload)['saved']
        created.add(first['id'])
        loaded = request('GET', '/saved/' + first['id'], 200)['cover_letter']
        assert loaded['company_name'] == company
        updated = copy.deepcopy(payload)
        updated['id'] = first['id']
        updated['cover_letter_data']['subject'] = 'Disposable update'
        assert request('POST', '/save', 200, updated)['action'] == 'updated'
        duplicate = request('POST', '/save', 409, payload)
        assert duplicate['code'] == 'duplicate_document'
        second_payload = dict(payload, file_name='Disposable-B.pdf')
        second = request('POST', '/save', 200, second_payload)['saved']
        created.add(second['id'])
        conflict_payload = dict(updated, file_name='Disposable-B.pdf')
        conflict = request('POST', '/save', 409, conflict_payload)['conflict']
        replaced = request('POST', '/save', 200, dict(conflict_payload, replace_id=conflict['id'], replace_updated_at=conflict['updated_at']))
        assert replaced['saved']['id'] == second['id']
        original = request('GET', '/saved/' + first['id'], 200)['cover_letter']
        assert original['file_name'] == 'Disposable-A.pdf'
        assert {first['id'], second['id']} <= {r['id'] for r in request('GET', '/saved', 200)['cover_letters']}
        request('DELETE', '/saved/' + first['id'], 200)
        assert request('POST', '/save', 404, updated)['code'] == 'stale_record'
        request('DELETE', '/saved/' + second['id'], 200)
        print('PASS: live Supabase disposable create/list/load/update/conflict/explicit replacement/stale-ID/delete checks.')
    finally:
        # Catch even a successfully inserted row whose API response was lost.
        rows = supabase_utils.supabase.table('saved_cover_letters').select('id').eq('company_name', company).execute().data or []
        for row in rows:
            supabase_utils.supabase.table('saved_cover_letters').delete().eq('company_name', company).eq('id', row['id']).execute()
        remaining = supabase_utils.supabase.table('saved_cover_letters').select('id').eq('company_name', company).limit(1).execute().data
        if remaining:
            raise RuntimeError('Disposable test cleanup did not complete')
        print('Cleanup verified: no disposable test records remain.')


if __name__ == '__main__':
    if sys.argv[1:] != ['--live']:
        raise SystemExit('No live requests made. Pass --live explicitly to run the disposable Supabase check.')
    try:
        run()
    except Exception as error:
        print('Live Supabase check could not complete. Error type:', type(error).__name__)
        # Numeric database code is safe; never print raw exceptions containing URLs.
        code = str(getattr(error, 'code', ''))
        if code.isalnum() and len(code) <= 8:
            print('Database error code:', code)
        raise SystemExit(1)
