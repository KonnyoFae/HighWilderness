"""Provisional ammunition scale: one resource point occupies ten litres."""
from copy import deepcopy
import json
from pathlib import Path
from 高天荒野舰艇数据契约 import ModulePrototypeCatalog, canonical_sha256

ROOT=Path(__file__).resolve().parents[1]


def build():
    def read(path):return json.loads((ROOT/path).read_text(encoding='utf-8'))
    old=read('舰艇数据/模块/测试夹具/战斗系统模块目录.v1.json')
    magazine=deepcopy(next(m for m in old['modules'] if m['id']=='gtw.module.fixture.ammunition_magazine'))
    magazine.update(version=2,name='标准弹药库',balance_status='prototype_unbalanced')
    magazine['capability']['capacity_units']=10000
    gun=deepcopy(next(m for m in read('舰艇数据/模块/测试夹具/战术火炮目录.v2.json')['modules'] if m['id']=='gtw.module.gun.30mm'))
    gun['version']=2
    gun['capability']['ready_round_capacity']=2000
    catalog=ModulePrototypeCatalog.parse(dict(schema='gaotian.module-prototype-catalog/v3',kind='ModulePrototypeCatalog',
        id='gtw.module_catalog.tactical.ammunition_scale',version=3,name='战术弹药仓容校准',fixture_level='prototype_unbalanced',modules=[magazine,gun])).to_dict()
    modules={m['id']:m for m in catalog['modules']}
    magazine=modules[magazine['id']];gun=modules[gun['id']]
    policy=read('contracts/web_bridge/fixtures/missile-preparation-policy.5c.json')
    policy.update(id='gtw.preparation.ammunition_scale',version=12,ammunition_resource_liters=10)
    policy['modules'].append(dict(prototype=dict(id=magazine['id'],version=2),prototype_sha256=canonical_sha256(magazine),
        binding=dict(capacity_resources=10000)))
    gun_rule=deepcopy(next(r for r in policy['modules'] if r['prototype']['id']==gun['id']))
    gun_rule.update(prototype=dict(id=gun['id'],version=2),prototype_sha256=canonical_sha256(gun))
    gun_rule['binding']['ready_capacity']=2000
    policy['modules'].append(gun_rule)
    # Provisional common-material volume grows with caliber cubed. Preserve
    # loading batch sizes except the newly specified 2,000-round CIWS magazine.
    projectiles={p['id']:p for p in policy['projectiles']}
    for recipe in policy['recipes']:
        recipe['version']+=1
        if recipe['id'].startswith('recipe.3a.30mm.'):
            recipe.update(rounds=2000,ammo_cost=40)
            # Special ammunition keeps its original material consumption per
            # round; only common ammunition resources are being rebalanced.
            for cost in recipe['cargo_costs']:
                cost['quantity']=(cost['quantity']*2000+59)//60
        caliber=projectiles[recipe['projectile']['id']]['ballistics']['caliber_mm']
        numerator=recipe['rounds']*caliber**3
        denominator=50*30**3
        recipe['ammo_cost']=(numerator+denominator-1)//denominator
    for path,value in [('舰艇数据/模块/测试夹具/战术弹药库目录.v3.json',catalog),
                       ('contracts/web_bridge/fixtures/ammunition-preparation-policy.v12.json',policy)]:
        (ROOT/path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

    # v12 has shipped. Preserve its exact contents for saved preparations and
    # add the user's explicit 50/75/120 mm scale as a new policy generation.
    current=deepcopy(policy)
    current['version']=13
    guns=read('舰艇数据/模块/测试夹具/战术火炮目录.v2.json')['modules']
    revised=[]
    for caliber,capacity in ((50,30),(75,8)):
        original=next(m for m in guns if m['id']==f'gtw.module.gun.{caliber}mm')
        module=deepcopy(original)
        module['version']=2
        module['capability']['ready_round_capacity']=capacity
        revised.append(module)
    calibers=ModulePrototypeCatalog.parse(dict(schema='gaotian.module-prototype-catalog/v3',kind='ModulePrototypeCatalog',
        id='gtw.module_catalog.tactical.ammunition_calibers',version=3,name='战术火炮装填校准',
        fixture_level='prototype_unbalanced',modules=revised)).to_dict()
    for module in calibers['modules']:
        rule=deepcopy(next(r for r in current['modules'] if r['prototype']['id']==module['id']))
        rule.update(prototype=dict(id=module['id'],version=2),prototype_sha256=canonical_sha256(module))
        rule['binding']['ready_capacity']=module['capability']['ready_round_capacity']
        current['modules'].append(rule)
    for recipe in current['recipes']:
        for caliber,rounds in ((50,30),(75,8),(120,1)):
            if recipe['id'].startswith(f'recipe.3a.{caliber}mm.'):
                recipe.update(version=recipe['version']+1,rounds=rounds,ammo_cost=1)
    for path,value in [('舰艇数据/模块/测试夹具/战术火炮装填校准目录.v3.json',calibers),
                       ('contracts/web_bridge/fixtures/ammunition-preparation-policy.v13.json',current)]:
        (ROOT/path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':build()
