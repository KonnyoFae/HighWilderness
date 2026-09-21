"""Export authoritative D2 curve references for independent browser sampling."""
import argparse
import json
from pathlib import Path
from backend.high_wilderness_sidecar import tactical_ballistics as physics
from backend.high_wilderness_sidecar.shell_presentation import simplify
from tools.test_shell_presentation import ballistic_cases


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    rows=[]
    for label,p in ballistic_cases():
        raw=[(0,*p.position,*p.velocity)]
        for step in range(1,17):
            p=physics.advance_projectile(p);raw.append((step,*p.position,*p.velocity))
        samples=[]
        for first in range(0,16,4):
            part=simplify(raw[first:first+5]);samples.extend(part if first==0 else part[1:])
        rows.append(dict(label=label,raw=raw,samples=samples))
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(rows,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(dict(cases=len(rows),samples=sum(len(r['samples']) for r in rows))))


if __name__=='__main__':main()
