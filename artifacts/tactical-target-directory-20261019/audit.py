"""Audit completed P2a.1c runs; writes a compact, reproducible evidence summary."""
import json
from hashlib import sha256
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from backend.high_wilderness_sidecar.persistent_ship import canonical_sha256

BASE=Path(__file__).resolve().parent
load=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
sources=sorted((ROOT/'artifacts/tactical-7b-20261009').glob('*/fixture.json'))
assert len(sources)==6
checks=[];manifest=None
for fixture in sources:
    run=BASE/'verification-final-2'/fixture.parent.name
    source=load(run/'source.json');result=load(run/'verification.json');publication=load(run/'publications.json')
    if manifest is None:manifest=source['files']
    assert source['files']==manifest
    assert source['digest']==canonical_sha256(manifest)
    assert source['reference_head']=='352f3e5f5f8bca499a6b77cb27332057f2bd3be3'
    assert source['verifier_sha256']==sha256((ROOT/'tools/verify_tactical_target_directory.py').read_bytes()).hexdigest()
    assert result['steps']==900 and len(result['sections'])==18
    assert result['fixture_sha256']==canonical_sha256(load(fixture))
    assert result['expanded_tape_sha256']==canonical_sha256(load(run/'expanded-tape.json'))
    assert publication['matched_reads']==publication['directory_checks']==227
    assert publication['sha256']==canonical_sha256(publication['tape'])
    assert publication['directory_tape_sha256']==canonical_sha256(publication['directory_tape'])
    checks.append(dict(case=result['case'],steps=result['steps'],public_reads=publication['matched_reads']))
saved=load(BASE/'summary.json') if (BASE/'summary.json').exists() else None
normalized={}
for name,digest in manifest.items():
    raw=(ROOT/name).read_bytes()
    normalized[name]=sha256(raw.replace(b'\r\n',b'\n')).hexdigest()
    assert sha256(raw).hexdigest()==digest or saved and normalized[name]==saved['normalized_source_files'][name],name
browser=load(BASE/'browser-final/browser.json')
assert browser['errors']==[] and len(browser['checks'])==3
assert all(row['edgeCases']=='passed' and row['maxRenderedRows']==14 for row in browser['checks'][:2])
assert browser['checks'][2]['realSidecar']=='passed' and browser['checks'][2]['pausedStep']>=30
log=(BASE/'unit-tests.txt').read_text(encoding='utf-8-sig')
assert 'Ran 81 tests' in log and log.rstrip().endswith('OK')
assert sum(line.startswith('test_') and line.endswith(' ... ok') for line in log.splitlines())==81
summary=dict(cases=checks,matched_steps=sum(r['steps'] for r in checks),public_reads=sum(r['public_reads'] for r in checks),
    backend_tests=81,browser=browser,source_files=len(manifest),normalized_source_files=normalized,
    tools={str(p.relative_to(ROOT)):sha256(p.read_bytes()).hexdigest() for p in (
        ROOT/'tools/test_tactical_target_directory.py',ROOT/'tools/verify_tactical_target_directory.py',
        ROOT/'tools/verify_tactical_target_directory.mjs',ROOT/'apps/desktop/target-directory-test.html')},
    scope='Correctness and page acceptance only; UI/publication performance comparison belongs to P2a.1d.')
(BASE/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in summary.items() if k not in ('normalized_source_files','tools','browser')},ensure_ascii=False,indent=2))
