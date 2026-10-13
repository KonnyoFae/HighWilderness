"""Verify and summarize closeout traces without rerunning battle simulation."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
CASES=('guns-4v4','mixed-4v4','guns-9v9','mixed-9v9')


def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def sha(path):return sha256(path.read_bytes()).hexdigest()
def stats(values):
    ordered=sorted(values)
    return dict(count=len(values),mean_ms=sum(values)/len(values),p95_ms=ordered[int((len(values)-1)*.95)],max_ms=max(values))


def summarize(case):
    folder=OUT/f'complete-{case}'
    source=read(folder/'source.json');browser=read(folder/'browser.json');backend=read(folder/'backend.json')
    trace=read(folder/'trace.json');events=trace['events']
    assert not trace['dropped'] and not browser['errors'] and backend['error'] is None
    assert browser['transport']=='json-string-binding'
    ended=browser['finalStatus']['pause_reason']=='battle_finished'
    assert ended or browser['wallMs']>=45000 and browser['finalStatus']['running']
    if not ended:assert browser['finalStatus']['generation']==0 and backend['status']['pause_reason']=='manual'
    else:assert backend['status']['pause_reason']=='battle_finished'
    assert backend['peak_debt_steps']<15 and browser['peakResponseBytes']<1024*1024
    for section in ('files','tools'):
        for name,value in source[section].items():assert sha(ROOT/name)==value,name
    baseline=read(ROOT/'artifacts/tactical-7c4b-20261013/paced-guns-4v4/source.json')
    changed={'apps/desktop/src/tactical/presentation.ts','apps/desktop/src/tactical/presentation.test.ts'}
    assert {name for name,value in baseline['files'].items() if source['files'].get(name)!=value}==changed
    assert set(source['files'])-set(baseline['files'])=={'apps/desktop/src/tactical/presentationSpeed.test.ts'}
    commands=browser['commands'];assert len(commands)==20
    assert [c['sequence'] for c in commands]==list(range(1,21))
    rows=[]
    for command in commands:
        sequence=command['sequence']
        accepted=[e for e in events if e['kind']=='accepted' and e['sequence']==sequence]
        resolved=[e for e in events if e['kind']=='resolved' and e['sequence']==sequence]
        assert len(accepted)==len(resolved)==1
        a=accepted[0];r=resolved[0]
        assert r['status']==command['status']=='executed' and command['replyStatus']=='accepted'
        assert r['step']==command['resolvedStep']==command['targetStep']+1
        assert command['shownStep']>=r['step']
        p=next(e for e in events if e['kind']=='publication' and e['at_ms']>=r['at_ms'] and e['step']>=r['step'])
        delivered=next(c for c in browser['calls'] if c.get('viewStep',-1)>=r['step'] and c['end']>=command['submitted'])
        node=next(n for n in browser['requestTrace'] if n.get('browserSerial')==delivered['serial'])
        server=next(e for e in events if e['kind']=='request' and e['request_id']==node['request_id'])
        read_event=next(e for e in events if e['kind']=='read' and e['request_id']==node['request_id'] and e['view_step']>=r['step'])
        times=dict(click_to_submit=command['clickToSubmitMs'],submit_to_accept=command['replyMs'],
            submit_to_receipt=command['receiptMs'],submit_to_canvas=command['canvasMs'],
            click_to_canvas=command['clickToCanvasMs'],receipt_to_canvas=command['receiptToCanvasMs'],
            timeline_to_canvas=command['timelineToCanvasMs'],reference_submit_to_canvas=command['referenceCanvasMs'],
            delivered_to_timeline=command['submitted']+command['timelineMs']-delivered['end'],
            accepted_to_executed=r['at_ms']-a['at_ms'],executed_to_publication=p['at_ms']-r['at_ms'],
            publication_to_read=read_event['at_ms']-p['at_ms'],
            read_server_execute=server['at_ms']-server['start_ms'],
            read_node_outside_execute=node['ms']-(server['at_ms']-server['start_ms']),
            read_browser_outside_node=(delivered['end']-delivered['start'])-node['ms'])
        assert all(value>=-1 for value in times.values()),(sequence,times)
        rows.append(dict(sequence=sequence,ms=times))
    return dict(case=case,source_files=len(source['files']),commands=rows,
        latency={key:stats([r['ms'][key] for r in rows]) for key in rows[0]['ms']},
        selection=browser['runningSelection'],simulated_s=backend['simulated_s'],wall_s=backend['wall_s'],naturally_finished=ended,
        peak_debt_steps=backend['peak_debt_steps'],final_speed=browser['finalStatus']['speed']['effective'],
        peak_response_bytes=browser['peakResponseBytes'])


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--write',action='store_true');args=parser.parse_args()
    summary=dict(cases=[summarize(case) for case in CASES],
        scope='Four instrumented headless runs, at most 45 wall seconds or natural battle end, 20 commands each. Reference clock consumes identical delivered committed states at identical sample times. Shadow cost is present in both clock observations. Separate browser/Node/Python monotonic durations; do not directly subtract timestamps from different processes or add nested statistics. No backend simulation or input-rule change, no new six-case equivalence or 1x/native/full-battle acceptance.')
    encoded=(json.dumps(summary,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    if args.write:(OUT/'summary.json').write_bytes(encoded)
    else:assert (OUT/'summary.json').read_bytes()==encoded,'summary drift'
    # Store manifests only for reviewable evidence, not regenerable SQL stores.
    paths=[p for p in OUT.rglob('*') if p.is_file() and 'store' not in p.parts and '__pycache__' not in p.parts and p.name!='evidence-index.json']
    index={p.relative_to(OUT).as_posix():sha(p) for p in sorted(paths)}
    if args.write:(OUT/'evidence-index.json').write_text(json.dumps(index,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    else:assert read(OUT/'evidence-index.json')==index,'evidence drift'
    print(json.dumps(dict(cases=len(summary['cases']),executed=sum(len(c['commands']) for c in summary['cases']),
        source_files=summary['cases'][0]['source_files'],backend_sources_unchanged=True)))
