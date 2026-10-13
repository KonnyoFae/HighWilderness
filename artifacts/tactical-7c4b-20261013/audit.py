"""Audit frozen equivalence, source provenance and wall-paced evidence."""
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from backend.high_wilderness_sidecar.persistent_ship import canonical_sha256

OUT=Path(__file__).resolve().parent
CASES=('guns-4v4','mixed-4v4','guns-9v9','mixed-9v9','mixed-4v9','mixed-4v9-reversed')
LIVE=CASES[:4]
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def speed_check(row):
    speed=row['status']['speed']
    assert abs(sum(row['rate_wall_s'].values())-speed['active_wall_s'])<1e-6
    assert 0<speed['effective']<=1
    assert all(.05<=float(k)<=1 and v>=0 for k,v in row['rate_wall_s'].items())

final=read(OUT/'paced-guns-4v4/source.json')
for name,value in read(OUT/'validated-source.json').items():
    assert sha256((ROOT/name).read_bytes()).hexdigest()==value,name
for section in ('files','tools'):
    for name,value in final[section].items():
        assert sha256((ROOT/name).read_bytes()).hexdigest()==value,name

# The state oracles preceded a frontend-only command-headroom correction.
# All simulation/runtime sources must match; those three UI files are exempt
# only in synthetic verification, never in the final paced/page runs.
early_ui={'apps/desktop/src/tactical/RealtimePanel.tsx','apps/desktop/src/tactical/realtime.ts',
          'apps/desktop/src/tactical/realtime.test.ts'}
verification=[];paced=[];live=[]
for case in CASES:
    directory=OUT/f'verify-{case}';source=read(directory/'source.json')
    assert source['head']==final['head'] and source['policy']==final['policy']
    assert source['files'].keys()==final['files'].keys()
    assert all(value==final['files'][name] for name,value in source['files'].items() if name not in early_ui)
    for name in ('tools/profile_tactical_7c4b.py',):assert source['tools'][name]==final['tools'][name]
    old=read(ROOT/f'artifacts/tactical-7c4a-20261013/verify-{case}/expanded-tape.json')
    expected=canonical_sha256(old)
    fixture=canonical_sha256(read(ROOT/f'artifacts/tactical-7b-20261009/{case}/fixture.json'))
    for variant in ('fixed','half','adaptive'):
        row=read(directory/f'{variant}.json');tape=read(directory/f'{variant}-tape.json')
        assert row['steps']==len(tape)==900 and len(tape[0])==18
        assert canonical_sha256(tape)==row['expanded_tape_sha256']==expected
        assert row['fixture_sha256']==fixture and row['error'] is None
        assert row['status']['running'] and row['status']['generation']==0 and row['peak_debt_steps']<15
        speed_check(row)
        verification.append(dict(case=case,variant=variant,steps=900,sections=18,matches_7c4a=True))
    for prefix in ('paced',):
        directory=OUT/f'{prefix}-{case}';source=read(directory/'source.json')
        assert source['files']==final['files'] and source['tools']==final['tools'] and source['policy']==final['policy']
        for variant in ('fixed','adaptive'):
            row=read(directory/f'{variant}.json');speed_check(row)
            assert row['error'] is None and row['fixture_sha256']==fixture
            if variant=='adaptive':assert row['steps']==900 and row['status']['running'] and row['peak_debt_steps']<15
            else:assert row['steps']==900 or row['status']['pause_reason']=='overload'
            paced.append({k:row[k] for k in ('case','variant','steps','simulated_s','wall_s','status','peak_debt_steps','rate_wall_s','step','read_and_encode')})
for case in LIVE:
    for variant in ('fixed','adaptive'):
        directory=OUT/f'live-{case}-{variant}';source=read(directory/'source.json')
        assert source['files']==final['files'] and source['tools']==final['tools'] and source['policy']==final['policy']
        backend=read(directory/'backend.json');browser=read(directory/'browser.json');speed=read(directory/'speed.json')
        assert not browser['errors'] and backend['error'] is None and browser['peakResponseBytes']<1024*1024
        assert abs(sum(speed['rate_wall_s'].values())-speed['speed']['active_wall_s'])<1e-6
        if variant=='adaptive':
            assert browser['wallMs']>=30_000 and browser['finalStatus']['running'] and backend['status']['pause_reason']=='manual'
            assert browser['commandSummary']['submitted']==browser['commandSummary']['executed']==6
            assert browser['commandSummary']['rejected']==0
            assert browser['commandSummary']['canvas']['count']==6
            assert [c['sequence'] for c in browser['commands']]==list(range(1,7))
            assert all(c['replyStatus']=='accepted' and c['resolvedStep']==c['targetStep']+1
                       and c['shownStep']>=c['resolvedStep'] for c in browser['commands'])
            assert browser['finalStatus']['generation']==0
        live.append(dict(case=case,variant=variant,backend=backend,speed=speed,browser=browser))
summary=dict(head=final['head'],source_files=len(final['files']),verification=verification,paced=paced,live=live,
    scope='Six synthetic 900-step oracles per variant; six serial real-clock service cases and four serial fixed/adaptive headless page pairs. No native, natural full battle, long-term or 1x acceptance claim.')
(OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
print(json.dumps(dict(verified=len(verification),paced=len(paced),live=len(live),source_files=len(final['files']))))
