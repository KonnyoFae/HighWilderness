"""Rebuild/check the review summary from frozen profiles and existing 7c.4b evidence."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.high_wilderness_sidecar.persistent_ship import canonical_sha256

OUT = Path(__file__).resolve().parent
PREVIOUS = ROOT / 'artifacts/tactical-7c4b-20261013'
CASES = ('guns-4v4', 'mixed-4v4', 'guns-9v9', 'mixed-9v9', 'mixed-4v9', 'mixed-4v9-reversed')


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def stats(values):
    ordered = sorted(values)
    return dict(count=len(values), mean_ms=sum(values)/len(values),
                p95_ms=ordered[int((len(values)-1)*.95)], max_ms=max(values))


def build():
    final = read(PREVIOUS / 'paced-guns-4v4/source.json')
    for name, expected in final['files'].items():
        assert digest(ROOT / name) == expected, name
    profiles = []
    for case in ('mixed-9v9', 'guns-9v9'):
        source = read(OUT / case / 'source.json')
        report = read(OUT / case / 'hotspots.json')
        assert source['head'] == 'ecafbff40826ac92c3eb4683bee80c19776a01e9'
        assert source['files'] == final['files']
        assert canonical_sha256(source['files']) == source['digest']
        assert source['attribution_tool_sha256'] == digest(ROOT / 'tools/profile_tactical_7c_hotspots.py')
        fixture = ROOT / 'artifacts/tactical-7b-20261009' / case
        assert report['fixture_sha256'] == canonical_sha256(read(fixture / 'fixture.json'))
        assert report['authority_tape_sha256'] == canonical_sha256(read(fixture / 'fixed-tape.json'))
        assert report['matched_steps'] == 900
        assert [(w['first_step'], w['last_step']) for w in report['windows']] == [(1, 60), (601, 660)]
        profiles.append(dict(case=case, matched_steps=report['matched_steps'], boundaries=report['boundaries'],
            late_inclusive=report['windows'][1]['profile']['by_inclusive'][:60],
            publication=report['publication']['by_inclusive'][:15], read=report['read']['by_inclusive'][:10]))
    paced = []
    for case in CASES:
        source = read(PREVIOUS / f'paced-{case}/source.json')
        assert source['files'] == final['files']
        for variant in ('fixed', 'adaptive'):
            row = read(PREVIOUS / f'paced-{case}/{variant}.json')
            assert row['error'] is None
            if variant == 'adaptive':
                assert row['steps'] == 900 and row['status']['running'] and row['peak_debt_steps'] < 15
            paced.append({key: row[key] for key in ('case', 'variant', 'steps', 'simulated_s', 'wall_s',
                'status', 'peak_debt_steps', 'step', 'read_and_encode')})
    live = []
    for case in CASES[:4]:
        source = read(PREVIOUS / f'live-{case}-adaptive/source.json')
        assert source['files'] == final['files']
        row = read(PREVIOUS / f'live-{case}-adaptive/browser.json')
        commands = row['commands']
        assert len(commands) == 6 and not row['errors']
        assert all(c['status'] == 'executed' and c['replyStatus'] == 'accepted' for c in commands)
        # Pair timestamps for the SAME command. Never subtract aggregate P95s.
        waits = [c['canvasMs']-c['receiptMs'] for c in commands]
        assert all(value >= 0 for value in waits)
        live.append(dict(case=case, command_summary=row['commandSummary'],
            receipt_to_canvas_ms=waits, receipt_to_canvas=stats(waits),
            metrics=row['metrics'], node_response_parse=row['nodeResponseParse'],
            selection_ms=row['selectionClickThroughTwoFramesMs'], peak_response_bytes=row['peakResponseBytes']))
    return dict(reused_7c4b_head=final['head'], profile_head='ecafbff40826ac92c3eb4683bee80c19776a01e9',
        source_files=len(final['files']), profiles=profiles, paced=paced, live=live,
        scope='New cProfile attribution on the pushed runtime; paced/page measurements reused from 7c.4b with identical source hashes. Inclusive times overlap and include profiler overhead. No new throughput benchmark or native/full-battle acceptance.')


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode('utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--write', action='store_true', help='Rebuild derived summary and evidence hashes')
    args = parser.parse_args()
    result = build()
    if args.write:
        (OUT / 'summary.json').write_bytes(encoded(result))
    else:
        assert (OUT / 'summary.json').read_bytes() == encoded(result), 'summary drift'
    paths = [p for p in OUT.rglob('*') if p.is_file() and p.name != 'evidence-index.json' and '__pycache__' not in p.parts]
    index = {p.relative_to(OUT).as_posix(): digest(p) for p in sorted(paths)}
    if args.write:
        (OUT / 'evidence-index.json').write_bytes(encoded(index))
    else:
        assert read(OUT / 'evidence-index.json') == index, 'evidence drift'
    print(json.dumps(dict(profiles=len(result['profiles']), matched_steps_each=900,
        source_files=result['source_files'], reused_paced=len(result['paced']), reused_pages=len(result['live']))))
