"""D3 cross-language references: real movement, explicitly scripted steering."""
import argparse
import json
from pathlib import Path
from tools.test_missile_display import scenarios,record_case


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    rows=[]
    for label,p,goal in scenarios():
        raw,frames,history,_=record_case(p,goal)
        published=history.missiles.published[p.id]
        rows.append(dict(label=label,raw=raw,frames=frames,samples=published['missile_samples'],states=published['missile_states']))
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(rows,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(dict(cases=len(rows))))


if __name__=='__main__':main()
