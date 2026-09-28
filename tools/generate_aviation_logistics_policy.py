"""Explicit new-import policy; never rewrite historical v17 archives."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    fixtures=ROOT/'contracts/web_bridge/fixtures'
    p=json.loads((fixtures/'ammunition-preparation-policy.v17.json').read_text(encoding='utf-8'))
    catalog=json.loads((fixtures/'tactical-aviation.av0.json').read_text(encoding='utf-8'))
    p['version']=18
    payloads={x['id']:'cargo.aviation.'+x['id'] for x in catalog['payloads']}
    cannons={x['cannon_id']:'cargo.aviation.'+x['cannon_id'].split('.')[-1] for x in catalog['aircraft'] if x['cannon_id']}
    stock={x['id']:'supply.aviation.'+x['id'].split('.')[-1] for x in catalog['aircraft']}
    pilot='supply.aviation.pilot'
    for key in [*payloads.values(),*cannons.values(),*stock.values(),pilot]:
        p['goods'].append(dict(id=key,version=1,unit='item',unit_volume_cm3=1000 if key in cannons.values() else 100000,
                               unit_mass_g=100 if key in cannons.values() else 10000))
    p['aviation']=dict(interface='gaotian.aviation-logistics/av1-v1',catalog=catalog,facilities=[],temporary_person_volume_cm3=1_000_000,
        recipes=dict(repair_parts_per_slot=10,prepare_parts_per_slot=2,payload_goods=payloads,cannon_goods=cannons,aircraft_stock=stock,pilot_stock=pilot))
    (fixtures/'ammunition-preparation-policy.v18.json').write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':main()
