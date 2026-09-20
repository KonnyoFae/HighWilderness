"""Backed-up, atomic turning-thruster upgrade for existing tactical ships."""
import argparse
from datetime import datetime
import json
from pathlib import Path
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from backend.high_wilderness_sidecar.preparation_transactions import PreparationStore
from backend.high_wilderness_sidecar.preparation_ammunition_upgrade import upgrade

ROOT=Path(__file__).resolve().parents[1]

from backend.high_wilderness_sidecar.preparation_maneuver_upgrade import policy_upgrade

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply',action='store_true')
    p.add_argument('--directory',type=Path,default=ROOT/'.local/tactical')
    args=p.parse_args()
    store=PreparationStore(args.directory,ResourceIndex(ROOT))
    backup=args.directory/'maneuver-upgrades'/f'before-{datetime.now():%Y%m%d-%H%M%S-%f}.json'
    print(json.dumps(upgrade(store,ROOT,apply=args.apply,backup_path=backup,policy_upgrade=policy_upgrade,skip_unavailable=True),ensure_ascii=False,indent=2))
