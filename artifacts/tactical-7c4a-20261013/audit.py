"""Recheck recorded sources and state tapes; summarize paired timings."""
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from backend.high_wilderness_sidecar.persistent_ship import canonical_sha256

OUT=Path(__file__).resolve().parent
def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def change(a,b):return 100*(b/a-1)

sources=sorted(p for p in OUT.iterdir() if p.is_dir() and p.name.startswith(('verify-','measure-','live-')))
base=read(OUT/'verify-mixed-9v9/source.json')
for name,expected in read(OUT/'validated-source.json').items():
    assert sha256((ROOT/name).read_bytes()).hexdigest()==expected,f'Validated source changed: {name}'
for directory in sources:
    source=read(directory/'source.json')
    assert source['reference_head']==base['reference_head']
    for section in ('files','tools_7c'):
        assert source[section]==base[section],f'Source differs: {directory}/{section}'
for section in ('files','tools_7c'):
    for name,expected in base[section].items():
        assert sha256((ROOT/name).read_bytes()).hexdigest()==expected,f'Source changed: {name}'
verified=[]
for case in ('guns-4v4','guns-9v9','mixed-4v4','mixed-4v9','mixed-4v9-reversed','mixed-9v9'):
    directory=OUT/f'verify-{case}';report=read(directory/'verification.json');tape=read(directory/'expanded-tape.json')
    previous=read(ROOT/f'artifacts/tactical-7c3-20261012/verify-{case}/verification.json')
    assert report['steps']==len(tape)==900 and len(report['sections'])==18
    assert canonical_sha256(tape)==report['expanded_tape_sha256']==previous['expanded_tape_sha256']
    assert report['fixture_sha256']==previous['fixture_sha256']
    public=read(directory/'publications.json')
    assert canonical_sha256(public['tape'])==public['sha256'] and public['matched_reads']==len(public['tape'])==227
    verified.append(dict(case=case,steps=900,sections=18,matched_reads=public['matched_reads'],matches_7c3=True))
timings=[]
for case in ('guns-9v9','mixed-9v9'):
    rows=read(OUT/f'measure-{case}/measurements.json');assert len(rows)==6
    tape=read(OUT/f'verify-{case}/expanded-tape.json');report=read(OUT/f'verify-{case}/verification.json')
    previous=read(ROOT/f'artifacts/tactical-7c3-20261012/measure-{case}/1-candidate.json')
    for row in rows:
        assert row['steps']==900 and row['physics_caches_cleared']
        assert row['authority_tape_sha256']==previous['authority_tape_sha256']
        assert row['expanded_final']==tape[-1] and row['fixture_sha256']==report['fixture_sha256'] and row['final_load']==report['final_load']
    for pair in range(1,4):
        a=next(r for r in rows if r['pair']==pair and r['variant']=='reference')
        b=next(r for r in rows if r['pair']==pair and r['variant']=='candidate')
        timings.append(dict(case=case,pair=pair,reference={k:a[k] for k in ('step','publish','read','service_wall_s','service_cpu_s')},
            candidate={k:b[k] for k in ('step','publish','read','service_wall_s','service_cpu_s')},
            changes_pct={k:change(a[k]['mean_ms'],b[k]['mean_ms']) for k in ('step','publish','read')},
            wall_change_pct=change(a['service_wall_s'],b['service_wall_s']),cpu_change_pct=change(a['service_cpu_s'],b['service_cpu_s'])))
live=[]
for directory in sources:
    if not directory.name.startswith('live-'):continue
    backend=read(directory/'backend.json');browser=read(directory/'browser.json');path=read(directory/'path.json')
    assert not browser['errors'] and backend['error'] is None
    live.append(dict(name=directory.name,status=backend['status'],simulated_s=backend['simulated_s'],wall_s=backend['wall_s'],
        costs=backend['costs'],path=path['metrics'],browser_requests=browser['requests'],browser_metrics=browser['metrics'],
        selection_ms=browser['selectionClickThroughTwoFramesMs'],peak_response_bytes=browser['peakResponseBytes']))
summary=dict(reference_head=base['reference_head'],source_files=len(base['files']),tools=len(base['tools_7c']),identical_sources=len(sources),
    verification=verified,measurements=timings,live=live,scope='Offline AB/BA/AB timings are uninstrumented. Live diagnostics are instrumented, bounded and not native Tauri or battle-command latency acceptance.')
(OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8',newline='\n')
print(json.dumps(dict(verified=verified,sources=len(sources),timing_changes=[{k:r[k] for k in ('case','pair','changes_pct','wall_change_pct','cpu_change_pct')} for r in timings]),indent=2))
