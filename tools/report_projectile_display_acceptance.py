"""Collect D5 evidence without rerunning gameplay or changing raw measurements."""
import argparse
import json
from pathlib import Path


def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--native',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    baseline=read('artifacts/projectile-display-d0-20260921/report.json')
    current=read('artifacts/projectile-display-d5-measurement-20260921/report.json')
    mixed=read('artifacts/projectile-display-d5-joint-20260921/report.json')
    frontend=read('artifacts/projectile-display-d5-render-headless-20260921/report.json')
    native=read(args.native)
    assert all(x['status']=='PASS' for x in (mixed,frontend))
    assert native['status'] in ('PASS','FLOW_VERIFIED_METRICS_UNAVAILABLE')
    assert len(baseline['cases'])==len(current['cases'])==2
    for a,b in zip(baseline['cases'],current['cases']):
        for key in ('case','steps','shots','peak_projectiles','shot_events'): assert a[key]==b[key],key
    old_cpu=sum(baseline['timings'][k]['total_ms'] for k in ('publish','read'))
    new_cpu=sum(current['timings'][k]['total_ms'] for k in ('publish','read'))
    transport_reduction=1-current['bytes']['projectile_total']/baseline['bytes']['projectile_total']
    cpu_reduction=1-new_cpu/old_cpu
    assert transport_reduction>=.5 and cpu_reduction>=.3
    assert len(mixed['recording_checks'])==3
    assert all(r['missing_appearances']==r['missing_terminals']==0 for r in mixed['recording_checks'])
    assert len(native['metrics']['scenes'])==2 and native['metrics']['lastSaved']
    result=dict(status='PASS',authority='Three 120 s simulated legacy/stream pairs: full motion/damage/missile state, inventory and settlement hashes match',
        observed_flights=sum(r['appearances'] for r in mixed['recording_checks']),
        transport=dict(baseline=baseline['bytes'],current=current['bytes'],projectile_reduction_percent=transport_reduction*100),
        publication_cpu=dict(baseline_total_ms=old_cpu,current_total_ms=new_cpu,reduction_percent=cpu_reduction*100),
        frontend=dict(dense=frontend['reports'][:2],warm_heap_growth_bytes=frontend['warmHeapGrowthBytes'],
            note='Peak counters include setup/scene transition frames; sustained active-flight peaks come from backend evidence.'),
        native=dict(report=str(args.native),status=native['status'],scope=native['scope'],metrics=native['metrics'],checks=native['checks']),
        limits=['CPU numbers are one-machine diagnostics, not GPU timing or a universal frame-rate guarantee.',
                'Browser recording is a separate display benchmark; native validation uses the packaged executable and real IPC.',
                'Native visibility/minimization was excluded from this delivery at user request, not reported as passed.',
                '9v9, hours-long stability, and point-defense prediction remain the overall work-package-7 scope.'])
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(status='PASS',flights=result['observed_flights'],transport_percent=transport_reduction*100,cpu_percent=cpu_reduction*100)))


if __name__=='__main__':main()
