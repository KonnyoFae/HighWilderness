"""New imports get explicit interceptor loadouts; historical v18 remains readable."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def main():
    folder=ROOT/'contracts/web_bridge/fixtures'
    p=json.loads((folder/'ammunition-preparation-policy.v18.json').read_text(encoding='utf-8'))
    p['version']=19;av=p['aviation'];c=av['catalog']
    c['payloads'].append(dict(id='small_interceptor',size='small',kind='interceptor',powered=True,inherits_launch_velocity=False))
    for m in c['aircraft']:
        if m['id'].endswith(('.f1','.b1')):
            m['version']=2
            for point in m['hardpoints']:point['compatible_payloads'].insert(2,'small_interceptor')
    c['tuning_note']='AV3 小型拦截弹与反舰弹分开整备；武器用途固定，数值为可调整测试初值。'
    av['recipes']['payload_goods']['small_interceptor']='cargo.aviation.small_interceptor'
    p['goods'].append(dict(id='cargo.aviation.small_interceptor',version=1,unit='item',unit_volume_cm3=100000,unit_mass_g=10000))
    av['combat']=json.loads((folder/'tactical-aviation-combat.av3.json').read_text(encoding='utf-8'))
    (folder/'ammunition-preparation-policy.v19.json').write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':main()
