"""Audit frozen inputs, paired authority traces and observed 7b evidence."""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import shutil
import struct
from tools.tactical_7b_fixture import CASES,write
from backend.high_wilderness_sidecar.persistent_ship import canonical_sha256


def read(path):return json.loads(path.read_text(encoding='utf-8'))


def pack(root):
    # Only our generated frame recordings; preserve timings before fresh unrecorded runs.
    for case in CASES:
        folder=root/case;source=folder/'fixed-frames.json'
        if not source.exists():continue
        target=source.with_suffix('.json.gz')
        with source.open('rb') as src,gzip.open(target,'wb',compresslevel=6) as dst:shutil.copyfileobj(src,dst)
        with gzip.open(target,'rb') as stream:assert json.load(stream)==read(source)
        source.unlink()
        for suffix in ('.json','-source.json','-tape.json'):
            shutil.copyfile(folder/('fixed'+suffix),folder/('recorded'+suffix))


def check(root):
    cases=[];sources=[]
    for case,(left,right,mixed,reverse) in CASES.items():
        folder=root/case;f=read(folder/'fixture.json');fixed=read(folder/'fixed.json');auto=read(folder/'automatic.json')
        assert len(f['ships'])==left+right and all(v['valid'] for v in f['fleet_qualification'])
        assert f['requests']['fixed']['player_side_id']==('side.enemy' if reverse else 'side.player')
        for r in (fixed,auto):
            assert r['fixture_sha256']==canonical_sha256(f) and r['aircrew_conserved']
            assert r['milestones_steps']['any_contact']==0 and r['ending'] is None
        assert fixed['steps']==900 and auto['steps']==300
        assert fixed['peak']['aircraft']==(10 if mixed else 0) and fixed['peak']['pilots']==(18 if mixed else 0)
        if mixed:
            assert fixed['peak']['airborne']==6 and fixed['peak']['jammers']==2 and fixed['peak']['jobs']==6
            assert fixed['peak']['missile_shots']>0 and fixed['peak']['interceptions']>0
            assert any(e['kind']=='fired' for e in fixed['aviation_events'])
        else:assert fixed['peak']['guns']==3*(left+right) and fixed['peak']['shots']>0
        assert auto['contact_measurement']['result']['distance_m']==f['entry_measurement']['contact']['distance_m']
        compared=[]
        for other in ('recorded','fixed-metered'):
            if (folder/(other+'.json')).exists():
                assert read(folder/(other+'-tape.json'))==read(folder/'fixed-tape.json'),(case,other)
                compared.append(other)
        source=read(folder/'fixed-source.json')
        product={k:v for k,v in source['files'].items() if not k.startswith('tools/')};sources.append(canonical_sha256(product))
        cases.append(dict(case=case,fixture_sha256=canonical_sha256(f),ships=left+right,
            roles=dict(Counter(s['role'] for s in f['ships'])),step=fixed['step'],publish=fixed['publish'],read=fixed['read'],
            peak=fixed['peak'],wire=fixed['wire'],retained=fixed['retained'],fixed_milestones=fixed['milestones_steps'],
            auto_milestones=auto['milestones_steps'],contact=auto['contact_measurement'],build_s=fixed['build_s'],
            authority_comparisons=compared,step_budget_pass=fixed['step']['p95_ms']<=1000/60))
    assert len(set(sources))==1,'Production source drift between baseline runs'
    live=[];replay=[]
    for name in ('guns-4v4','mixed-4v4-r2','guns-9v9','mixed-9v9'):
        folder=root/('live-'+name);b=read(folder/'backend.json');web=read(folder/'browser.json')
        assert not web['errors'] and b['error'] is None
        assert struct.unpack('>II',(folder/'battle.png').read_bytes()[16:24])==(1440,1000)
        live.append(dict(case=name,status=b['status']['pause_reason'],steps=b['status']['fixed_step'],wall_s=b['wall_s'],debt_steps=b['peak_debt_steps']))
    for name in ('mixed-4v4','guns-9v9','mixed-9v9'):
        folder=root/('replay-'+name);r=read(folder/'replay.json')
        assert not r['errors'] and r['metrics']['parse']['count']==225
        assert struct.unpack('>II',(folder/'replay.png').read_bytes()[16:24])==(1440,1000)
        replay.append(dict(case=name,metrics=r['metrics'],maxFlights=r['maxFlights'],maxFrames=r['maxFrames'],maxDisplayed=r['maxDisplayed']))
    report=dict(cases=cases,live=live,replay=replay,viewport=dict(width=1440,height=1000,screenshot_pixels=[1440,1000]),
        production_source_sha256=sources[0],scope='Baseline correctness audit. Performance failures remain failures; not complete-battle/native acceptance.')
    write(root/'summary.json',report);print(json.dumps(dict(audited_cases=len(cases),step_budget_passes=sum(r['step_budget_pass'] for r in cases)),ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--pack',action='store_true');a=p.parse_args()
    pack(a.root) if a.pack else check(a.root)
