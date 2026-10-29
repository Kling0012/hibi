"""Sequential, checkpointed recovery benchmark through the Gateway only."""
import argparse
import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path

TARGETS = {
    18: 'c02dfeb1-7e63-5ad7-8091-28b9760f6f3c',
    19: '2b12dcf3-edd9-5677-92bb-7f35d6369f82',
    21: 'aa4e2551-f6e4-51c9-9c1e-73bc84de7519',
    25: '5af43a07-b1c3-5932-953d-663d7adcb028',
    29: 'cd0ca6b3-77c0-5b23-8b4c-cc97ccae5a14',
    32: '8ed9c4e2-49ee-54ec-96fc-c0992de9a4ad',
}


def call(base,path,post=False):
    request = urllib.request.Request(base+path, method='POST' if post else 'GET')
    with urllib.request.urlopen(request,timeout=1250) as response:
        return json.load(response)


def fingerprint(record):
    fields = ('memory_id','type','content','scope','source','visibility','created_at','valid_from','valid_to','refs','tags','idempotency_key')
    return hashlib.sha256(json.dumps({k:record[k] for k in fields},sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--base-url',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--allow-reviewed-retry',action='store_true')
    args=p.parse_args()
    with Path(args.output).open('x',encoding='utf-8') as stream:
        def record(event):
            stream.write(json.dumps(event,ensure_ascii=False)+'\n')
            stream.flush()
            os.fsync(stream.fileno())
            print(json.dumps(event,ensure_ascii=False),flush=True)
        initial=call(args.base_url,'/health')['long_term']
        assert initial['pipeline']['ingestion_mode']=='graphiti'
        assert initial['pipeline']['node_resolution_provider']=='jev'
        for legacy_id,memory_id in TARGETS.items():
            path='/v1/memories/'+memory_id
            before=call(args.base_url,path)
            assert before['source']['uri']==f'legacy-sqlite:entry/{legacy_id}'
            allowed = ('uncertain','review_required') if args.allow_reviewed_retry else ('uncertain',)
            assert before['enrichment_status'] in allowed and not before['episode_uuid']
            digest=fingerprint(before)
            record({'legacy_id':legacy_id,'memory_id':memory_id,'state':'started','canonical_sha256':digest})
            start=time.monotonic()
            try:
                suffix='?allow_reviewed_retry=true' if args.allow_reviewed_retry else ''
                result=call(args.base_url,path+'/resume-standard'+suffix,post=True)
            except Exception as exc:
                record({'legacy_id':legacy_id,'state':'request_failed','error':type(exc).__name__,'wall_seconds':time.monotonic()-start})
                # Do not blindly repeat a timed out write or proceed after an unknown partial failure.
                raise
            after=call(args.base_url,path)
            assert fingerprint(after)==digest, 'canonical metadata changed'
            assert after['enrichment_status']=='ready' and after['episode_uuid']
            record({'legacy_id':legacy_id,'state':'complete','wall_seconds':time.monotonic()-start,'canonical_unchanged':True,**result})
        final=call(args.base_url,'/health')['long_term']
        assert final['total']==initial['total'], 'canonical record count changed'
        record({'state':'all_complete','total':final['total'],'enrichment':final['enrichment']})


if __name__=='__main__':
    main()
