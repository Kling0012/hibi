"""Read-only canonical migration audit; print IDs and checks, never memory bodies."""
import argparse
import json
import os
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from migration.import_legacy_sqlite import TYPE_MAP, load_manifest, prepare_content

MEMORY_USER = os.environ.get("MEMORY_USER", "default")


def fetch(base, path):
    with urllib.request.urlopen(base + path, timeout=30) as response:
        return json.load(response)


def audit(database, manifest, base):
    connection = sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    rows = connection.execute("SELECT * FROM entry WHERE kind IN ('fact','decision','state') ORDER BY id").fetchall()
    states = load_manifest(Path(manifest))
    results = []
    for row in rows:
        event = states.get(row['id'], {})
        if event.get('status') != 'imported':
            results.append({'legacy_id': row['id'], 'missing': True})
            continue
        record = fetch(base, '/v1/memories/' + event['memory_id'])
        details = fetch(base, '/v1/memories/' + event['memory_id'] + '/enrichment')
        refs = json.loads(row['refs'])
        created = datetime.fromisoformat(row['first_ts'].replace('Z', '+00:00'))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        expected = {
            'content': prepare_content(row['body'], refs).strip(),
            'type': TYPE_MAP[row['kind']], 'refs': refs,
            'scope': {'user': MEMORY_USER, 'project': 'all', 'agent': None},
            'source': {'agent': row['author'].split('@', 1)[0], 'session': 'legacy-sqlite-import', 'uri': f"legacy-sqlite:entry/{row['id']}"},
            'visibility': 'project', 'name': row['name'] or None,
            'tags': list(dict.fromkeys(json.loads(row['tags']) + [f"legacy-topic:{row['topic']}"])),
        }
        mismatch = [key for key, value in expected.items() if record.get(key) != value]
        if datetime.fromisoformat(record['valid_from'].replace('Z', '+00:00')) != created:
            mismatch.append('valid_from')
        results.append({'legacy_id': row['id'], 'memory_id': event['memory_id'], 'mismatch': mismatch, **details})
    connection.close()
    return {'count': len(rows), 'matched': sum(not r.get('missing') and not r.get('mismatch') for r in results), 'ready': sum(r.get('status') == 'ready' for r in results), 'records': results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('database')
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--base-url', required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.database, args.manifest, args.base_url), ensure_ascii=False, indent=2))
