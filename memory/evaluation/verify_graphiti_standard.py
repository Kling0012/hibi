"""Non-destructive REST smoke test of the standard Graphiti registration pipeline."""
import os
import argparse
import json
import time
import urllib.request
from pathlib import Path

MEMORY_USER = os.environ.get("MEMORY_USER", "default")


def call(base, path, payload=None):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    request = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=600) as response:
        return json.load(response)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--base-url', required=True)
    p.add_argument('--run', required=True)
    p.add_argument('--output', required=True)
    args = p.parse_args()
    project = 'graphiti-jev-eval-' + args.run
    # Exclusive output file prevents an accidental repeated write on rerun.
    with Path(args.output).open('x', encoding='utf-8') as stream:
        health = call(args.base_url, '/health')
        assert health['long_term']['pipeline']['ingestion_mode'] == 'graphiti'
        assert health['long_term']['pipeline']['node_resolution_provider'] == 'jev'
        payload = {'type': 'project_configuration', 'content': '検証用Cedarプロジェクトは2026年9月1日から本番データベースとしてAmberDBを使用する。',
                   'scope': {'user': MEMORY_USER, 'project': project},
                   'source': {'agent': 'codex', 'session': project, 'uri': 'synthetic:graphiti-jev-smoke'},
                   'refs': ['agent-memory/docs/graphiti_jev_integration.md'], 'valid_from': '2026-09-01T00:00:00Z'}
        stream.write(json.dumps({'state': 'posting', 'project': project}) + '\n')
        stream.flush()
        start = time.monotonic()
        record = call(args.base_url, '/v1/memories', payload)
        elapsed = time.monotonic()-start
        stream.write(json.dumps({'memory_id': record['memory_id'], 'episode_uuid': record['episode_uuid'], 'write_seconds': elapsed}) + '\n')
        stream.flush()
        assert record['episode_uuid'] and record['enrichment_status'] == 'ready'
        fetched = call(args.base_url, '/v1/memories/' + record['memory_id'])
        assert fetched['content'] == payload['content'] and fetched['source'] == payload['source']
        query = {'query': 'Cedar AmberDB 本番データベース', 'user': MEMORY_USER, 'project': project, 'agent': 'codex', 'limit': 10}
        results = call(args.base_url, '/v1/memories/search', query)['results']
        assert any(r['memory']['memory_id'] == record['memory_id'] for r in results)
        query['project'] += '-other'
        results = call(args.base_url, '/v1/memories/search', query)['results']
        assert all(r['memory']['memory_id'] != record['memory_id'] for r in results)
        outcome = {'standard_write': True, 'get': True, 'search': True, 'scope_isolation': True, 'write_seconds': elapsed}
        stream.write(json.dumps(outcome) + '\n')
        print(json.dumps(outcome), flush=True)


if __name__ == '__main__':
    main()
