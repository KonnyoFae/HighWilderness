"""E2.2b command producer + full implemented flight/resource scope, offline gate."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools import tactical_realtime_baseline as baseline
from tools import verify_tactical_resources_runtime as resources


def build():
    return resources.build(with_command=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    out=parser.parse_args().out.resolve(); out.mkdir(parents=True,exist_ok=False)
    try:
        sources=baseline.freeze(out)
        manifest=json.loads((out/'manifest.json').read_text(encoding='utf-8'))
        manifest['build']='E2.2b device/resource/command/lifecycle producers; synthetic manual/electric flagship + observer; no combat/UI'
        baseline.write_json(out/'manifest.json',manifest)
        sample=build()
        baseline.write_json(out/'resources.json',[asdict(s) for s in sample._seeds])
        cases=[resources.tape(sample._seeds[0],n) for n in ('steady','resource_bursts')]
        loss=resources.tape(sample._seeds[0],'command_loss')
        loss['resources']=[dict(step=2100,phase='closing',kind='mode',target='cic',value='off',sequence=1),
            dict(step=2160,phase='opening',kind='mode',target='cic',value='active',sequence=2)]
        cases.append(loss)
        baseline.write_json(out/'inputs.json',cases)
        results=[]
        for case in cases:
            results.append(resources.verify(case,out,factory=build)); print(results[-1],flush=True)
        final=json.loads((out/'command_loss-final.json').read_text(encoding='utf-8'))['ships'][0]
        assert not final['authority_allowed'] and final['command']['fleet_phase']=='command_defeat_withdrawal'
        assert final['command']['lifecycle']['command_status']=='scene_command'
        assert not any(final['propulsion']['output_percent_units'])
        assert sources==baseline.source_inventory()
        baseline.write_json(out/'result.json',dict(status='PASS',stage='E2.2b',full_E2='NOT_PASSED',product_realtime='NOT_PASSED',
            warmup_steps=600,measured_steps=3600,repeats=3,workloads=results,
            scope='Two ships, single flagship, crewed synthetic manual/electric fixture. Includes device, phase allocation, command and lifecycle producers. Other remote/exit cases covered by unit tests. Excludes setup, oracle/profile, RTS, distance/radio models, actual combat/repair, save/UI/long run.',
            evidence_sha256={p.name:baseline.digest(p) for p in out.iterdir() if p.is_file()}))
    except Exception as error:
        baseline.write_json(out/'failure.json',dict(status='FAIL',error=repr(error))); raise


if __name__=='__main__': main()
