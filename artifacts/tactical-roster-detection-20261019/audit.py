"""Validate final evidence and summarize all pairs, retaining regressions."""
from hashlib import sha256
import json
from pathlib import Path
from statistics import mean
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
REFERENCE = '352f3e5f5f8bca499a6b77cb27332057f2bd3be3'
CASES = ('guns-4v4', 'guns-9v9', 'mixed-4v4', 'mixed-4v9', 'mixed-4v9-reversed', 'mixed-9v9')


def read(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(value):return sha256(value).hexdigest()
def canonical(value):return sha((json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)+'\n').encode('utf-8'))
def git(path):return subprocess.check_output(['git', 'show', REFERENCE+':'+path], cwd=ROOT)
def lf(path):return sha(path.read_bytes().replace(b'\r\n', b'\n'))


def audit():
    sources = []
    def source(directory, *, paced=False):
        value = read(directory/'source.json'); sources.append(value)
        assert value['reference_head'] == REFERENCE
        assert canonical(value['files']) == value['digest']
        assert value['files_lf'] == sources[0]['files_lf'], directory
        assert sha((ROOT/'tools/profile_tactical_roster_detection.py').read_bytes()) == value['verifier_sha256']
        for name, digest in value['reference_files'].items():assert sha(git(name)) == digest, name
        if paced:assert sha((ROOT/'tools/measure_tactical_roster_service.py').read_bytes()) == value['runner_sha256']
        return value

    diagnostic = {}; steps = reads = 0
    for case in CASES:
        directory = HERE/'verification-final'/case; source(directory)
        result = read(directory/'verification.json'); tape = read(directory/'expanded-tape.json')
        public = read(directory/'publications.json')
        assert result['case'] == case and result['steps'] == len(tape) == 900
        assert len(result['sections']) == 18 and all(len(row) == 18 for row in tape)
        assert canonical(tape) == result['expanded_tape_sha256']
        fixture_path = f'artifacts/tactical-7b-20261009/{case}/fixture.json'
        fixture = read(ROOT/fixture_path)
        assert fixture == json.loads(git(fixture_path))
        assert canonical(fixture) == result['fixture_sha256']
        assert public['matched_reads'] == len(public['tape']) == 227
        assert canonical(public['tape']) == public['sha256']
        assert result['counters']['candidate'].get('full_target_builds', 0) == 0
        diagnostic[case] = {k: result[k] for k in ('counters', 'late_601_660')}
        steps += 900; reads += 227

    chaff = HERE/'verify-chaff-final'; source(chaff)
    result = read(chaff/'verification.json'); tape = read(chaff/'expanded-tape.json')
    assert result['steps'] == len(tape) == 200 and len(result['sections']) == 18
    assert canonical(tape) == result['tape_sha256']
    assert canonical(read(chaff/'inputs.json')) == result['inputs_sha256']
    assert [row['quality'] for row in result['checkpoints']] == ['normal', 'degraded', 'degraded', 'normal']

    performance = {}; paced = {}
    for case in ('guns-9v9', 'mixed-9v9'):
        directory = HERE/f'measure-{case}'; source(directory)
        rows = read(directory/'measurements.json')
        assert len(rows) == 6 and {r['pair'] for r in rows} == {1, 2, 3}
        assert all(r['steps'] == 900 and r['physics_caches_cleared'] for r in rows)
        assert all(r['expanded_final'] == rows[0]['expanded_final'] for r in rows)
        assert all(r['authority_tape_sha256'] == rows[0]['authority_tape_sha256'] for r in rows)
        pairs = []
        for pair in (1, 2, 3):
            ref, new = (next(r for r in rows if r['pair'] == pair and r['variant'] == name) for name in ('reference', 'candidate'))
            for row in (ref, new):assert row == read(directory/f'{pair}-{row["variant"]}.json')
            pairs.append(dict(pair=pair, step_mean_reduction_percent=(1-new['step']['mean_ms']/ref['step']['mean_ms'])*100,
                              step_p95_reduction_percent=(1-new['step']['p95_ms']/ref['step']['p95_ms'])*100,
                              service_wall_reduction_percent=(1-new['service_wall_s']/ref['service_wall_s'])*100))
        averages = {}
        for name in ('reference', 'candidate'):
            selected = [r for r in rows if r['variant'] == name]
            averages[name] = {key: mean(r[key] for r in selected) for key in ('service_cpu_s', 'service_wall_s')}
            for section in ('step', 'publish', 'read'):
                averages[name][section] = {key: mean(r[section][key] for r in selected) for key in ('mean_ms', 'p95_ms')}
        performance[case] = dict(pairs=pairs, averages=averages, scope='Arithmetic means of three runs; P95 values are averaged run percentiles, not a pooled percentile.')
        directory = HERE/f'paced-{case}'; source(directory, paced=True)
        paced[case] = {}
        for name in ('reference-fixed', 'reference-adaptive', 'candidate-fixed', 'candidate-adaptive'):
            row = read(directory/f'{name}.json'); assert row['case'] == case and row['variant'] == name
            assert row['error'] is None, (case, name, row['error'])
            if name.endswith('-adaptive'):assert row['steps'] == 900 and row['status']['running']
            paced[case][name] = {k: row[k] for k in ('steps', 'wall_s', 'peak_debt_steps', 'status', 'error')}

    directory = HERE/'profile-mixed-9v9'; prof_source = source(directory)
    assert sha((ROOT/'tools/diagnose_tactical_roster_detection.py').read_bytes()) == prof_source['runner_sha256']
    profiles = {}
    for name in ('reference', 'candidate'):
        row = read(directory/f'{name}.json')
        assert row['steps'] == [601, 660] and row['verified_steps'] == 660
        profiles[name] = row
    for path, digest in sources[0]['files_lf'].items():assert lf(ROOT/path) == digest, path
    tests = read(HERE/'unit-tests.json')
    assert tests['final_passed'] is True
    assert tests['tests'] == 178
    for path, digest in tests['sources_lf'].items():assert lf(ROOT/path) == digest, path
    return dict(cases=6, matched_steps=steps, public_reads=reads, chaff_steps=200,
                production_files=len(sources[0]['files_lf']), tests=tests['tests'],
                diagnostics=diagnostic, performance=performance, paced=paced, profiles=profiles)


if __name__ == '__main__':
    result = audit()
    (HERE/'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k not in ('diagnostics', 'paced', 'profiles')}, ensure_ascii=False, indent=2))
