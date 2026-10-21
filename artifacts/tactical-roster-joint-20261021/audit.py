"""Verify P2a.1d provenance and aggregate serial measurements without hiding regressions."""
from hashlib import sha256
import json
from pathlib import Path
from statistics import mean
import sys
import subprocess

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from backend.high_wilderness_sidecar.persistent_ship import canonical_sha256
from tools.profile_tactical_fire_control import stats

BASE=Path(__file__).resolve().parent
load=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
sha=lambda p:sha256(p.read_bytes()).hexdigest()
summary={'case_results':{},'reused_correctness':{},'page_results':[],'component_results':{}}
# Original 1c six-case correctness evidence remains valid for every existing source.
prior=ROOT/'artifacts/tactical-target-directory-20261019'
saved=load(prior/'summary.json')
committed=subprocess.check_output(['git','show','540c2df:artifacts/tactical-target-directory-20261019/summary.json'],cwd=ROOT)
assert json.loads(committed)==saved
for name,digest in saved['normalized_source_files'].items():
    assert sha256((ROOT/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest()==digest,name
assert saved['matched_steps']==5400 and saved['public_reads']==1362
summary['reused_correctness']=dict(commit='540c2df4cbcfbe1259bdc11e3c5b858b81a4bdd0',
    steps=5400,reads=1362,source_files=len(saved['normalized_source_files']),
    source_summary_sha256=sha(prior/'summary.json'),scope='Original 1c evidence, source-checked reuse; not rerun in 1d.')
manifest=None
for short,case in [('guns','guns-9v9'),('mixed','mixed-9v9')]:
    location=BASE/('measure-'+short);source=load(location/'joint-source.json')
    if manifest is None:manifest=source['files_lf']
    assert source['files_lf']==manifest
    assert source['reference_head']=='352f3e5f5f8bca499a6b77cb27332057f2bd3be3'
    assert source['current_head']==source['head']=='540c2df4cbcfbe1259bdc11e3c5b858b81a4bdd0'
    assert source['runner_sha256']==sha(ROOT/'tools/measure_tactical_target_directory.py')
    reports=load(location/'measurements.json');assert len(reports)==9
    fixture=load(ROOT/f'artifacts/tactical-7b-20261009/{case}/fixture.json')
    expected=load(ROOT/f'artifacts/tactical-7b-20261009/{case}/fixed-tape.json')
    for r in reports:
        assert r==load(location/f'{r["round"]}-{r["variant"]}.json')
        assert r['steps']==900 and r['fixture_sha256']==canonical_sha256(fixture)
        assert r['authority_tape_sha256']==canonical_sha256(expected)
        assert r['expanded_final']==reports[0]['expanded_final']
        assert len(r['expanded_final'])==18 and len(r['samples_s']['step'])==900
        assert all(len(r['samples_s'][key])==225 for key in ('publish','read','encode'))
        assert r['timings']=={k:stats(v) for k,v in r['samples_s'].items()}
        assert r['service_wall_s']==sum(sum(v) for v in r['samples_s'].values())
    variants={}
    for variant in ('reference','backend','full'):
        group=[r for r in reports if r['variant']==variant];assert {r['round'] for r in group}=={1,2,3}
        variants[variant]=dict(timings={k:stats([x for r in group for x in r['samples_s'][k]]) for k in reports[0]['samples_s']},
            mean_wall_s=mean(r['service_wall_s'] for r in group),mean_cpu_s=mean(r['service_cpu_s'] for r in group),
            mean_wire_bytes=mean(r['wire_bytes']['mean'] for r in group),
            mean_directory_bytes=mean(r['directory_bytes']['mean'] for r in group))
    paced={}
    for stage in ('paced','capture'):
        provenance=load(BASE/f'{stage}-{short}/joint-source.json')
        assert provenance['files_lf']==manifest
        assert provenance['runner_sha256']==source['runner_sha256']
    for variant in ('backend','full'):
        for mode in ('fixed','adaptive'):
            r=load(BASE/f'paced-{short}/{variant}-{mode}.json');assert r['error'] is None
            assert r['fixture_sha256']==canonical_sha256(fixture)
            if mode=='adaptive':assert r['steps']==900 and r['status']['running']
            paced[variant+'-'+mode]={k:r[k] for k in ('steps','wall_s','peak_debt_steps','status','rate_changes')}
    summary['case_results'][case]=dict(variants=variants,paced=paced)
    capture=BASE/f'capture-{short}/frames.json';frames=load(capture);assert len(frames)==76
    browser=load(BASE/f'component-{short}/browser.json')
    assert browser['inputSha256']==sha(capture)
    assert browser['runnerSha256']==sha(ROOT/'tools/measure_tactical_directory_browser.mjs')
    assert len(browser['results'])==36
    assert len({(r['size']['width'],r['load'],r['round'],r['variant']) for r in browser['results']})==36
    for r in browser['results']:
        assert all(s['scrollWidth']<=s['width']+1 for s in r['samples'])
        if r['variant']!='legacy':assert r['maxRows']<=72
    build=load(BASE/'component-build.json')
    assert build['builderSha256']==sha(ROOT/build['builder'])
    assert browser['bundleHashes']==build['bundleHashes']
    # Bundles are ignored build output; compare them too when still present.
    for name,digest in browser['bundleHashes'].items():
        generated=ROOT/'apps/desktop/dist/target-directory-bench'/name
        if generated.exists():assert sha(generated)==digest,name
    summary['component_results'][case]=[dict(size=r['size'],load=r['load'],round=r['round'],variant=r['variant'],
        total=r['total'],maxRows=r['maxRows'],maxNodes=r['maxNodes'],heapDelta=r['after']['usedSize']-r['before']['usedSize']) for r in browser['results']]
    for width in (1366,1920):
        report=load(BASE/f'page-{short}-{width}/browser.json')
        assert report['viewport']==dict(width=width,height=768 if width==1366 else 1080)
        page_source=load(BASE/f'page-{short}-{width}/source.json')
        for name,digest in page_source['files'].items():assert sha(ROOT/name)==digest,name
        for name,digest in page_source['tools'].items():assert sha(ROOT/name)==digest,name
        assert report['errors']==[]
        assert report['commandSummary']['submitted']==report['commandSummary']['executed']==20
        assert report['commandSummary']['canvas']['count']==20
        trace=load(BASE/f'page-{short}-{width}/trace.json')
        assert trace['dropped']==0
        assert len([e for e in trace['events'] if e['kind']=='resolved' and e['status']=='executed'])==20
        summary['page_results'].append(dict(case=case,width=width,commands=report['commandSummary'],finalStatus=report['finalStatus'],metrics=report['metrics']))
for name,digest in manifest.items():
    if name=='apps/desktop/src/tactical/testing/target-directory-perf.tsx':
        # Only this isolated test page changed after backend timing. Preserve
        # the first source and verify the corrected source against its build.
        original=(BASE/'component-layout-incomplete-source.tsx').read_bytes()
        assert sha256(original.replace(b'\r\n',b'\n')).hexdigest()==digest
        build=load(BASE/'component-build.json')
        assert build['benchmarkSource']==name and sha(ROOT/name)==build['benchmarkSourceSha256']
        continue
    assert sha256((ROOT/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest()==digest,name
backend=(BASE/'unit-tests.txt').read_text(encoding='utf-8-sig')
frontend=(BASE/'frontend-tests.txt').read_text(encoding='utf-8-sig')
assert 'Ran 195 tests' in backend and backend.rstrip().endswith('OK')
assert '217 passed' in frontend and '50 passed' in frontend
summary['tests']=dict(backend=195,frontend=217)
summary['source_files']=len(manifest)
summary['scope']='Incremental relative to 352f3e5 (already includes 1a roster). Three-way offline comparison, separate paced service and UI cost; no native/full battle claim.'
(BASE/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(dict(source_files=len(manifest),tests=summary['tests'],case_count=len(summary['case_results']),page_count=len(summary['page_results'])),ensure_ascii=False))
