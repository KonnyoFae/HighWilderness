"""X1a.4 entry-only assembly of saved designs; no design parsing in flight."""
from dataclasses import dataclass, replace
from math import hypot
from . import battle_preparation as bp, persistent_ship as ps
from .tactical_gunnery import GunneryBattle
from .tactical_damage import DamageState
from .tactical import render_static
from 高天荒野舰艇统一战术场景 import TacticalSceneShipBinding


@dataclass
class PreparedScenario:
    bindings: tuple
    projectile_catalog: object
    material_registry: object
    manifest: dict


def ignition_enemy(binding, snapshot, policy):
    """Explicit fresh technical-enemy extension; never migrate a saved player ship."""
    from . import tactical_fuel as fuel, tactical_ignition as ignition
    definition = binding.resources.definition()
    definition.update(interface=ignition.RESOURCE_INTERFACE, id='gtw.enemy.h5d', version=1,
        goods=ps.clone(policy['goods']), filling_holds=[], damage_controls=[],
        continuous_damage=ps.clone(policy['continuous_damage']), repair=ps.clone(policy['repair']),
        fuel=ps.clone(policy['fuel']), fuel_tanks=fuel.definitions(snapshot,policy['fuel']),
        ignition=ps.clone(policy['ignition']), ignition_decks=ignition.definitions(snapshot,policy['ignition']))
    definition['projectiles'].append(ps.clone(next(p for p in policy['projectiles'] if p['id']==ignition.PROJECTILE[0])))
    definition['recipes'].append(ps.clone(next(r for r in policy['recipes'] if r['id']==ignition.RECIPE)))
    for weapon in definition['weapons']:
        weapon['recipe_ids'].append(ignition.RECIPE)
    rules={r['prototype']['id']:r['binding'] for r in policy['modules']}
    for m in binding.resources.seed.resources.modules:
        if m.prototype.category=='damage_control':
            ps.need(m.prototype.reference.id in rules, '$.enemy', 'Missing technical damage-control binding')
            definition['damage_controls'].append(dict(module_id=m.id,**rules[m.prototype.reference.id]))
    pack=ps.compile_resources(binding.resources.seed,definition)
    value=ps.fresh_instance(pack,binding.instance.to_dict()['instance_id']).to_dict()
    old=binding.instance.to_dict()
    for key in old:
        if key not in ('interface','resources_sha256'):
            value[key]=old[key]
    # Finite fresh-enemy fixture inventory, independent from player preparation supply.
    capacity=sum(h['capacity_cm3'] for h in definition['holds'])
    unit=next(g['unit_volume_cm3'] for g in definition['goods'] if g['id']==ignition.GOOD)
    value['cargo']=[dict(good_id=ignition.GOOD,quantity=min(10,capacity//unit))]
    for weapon in value['weapons']:
        if weapon['ready_rounds']:
            weapon['recipe_id']=ignition.RECIPE
    return ps.InstanceBinding(pack,ps.parse_instance(value,pack))


def build(ships, direct_instance_id, template, technical_scenario, *, allow_test_ignition=False):
    ps.need(1 <= len(ships) <= 15, '$.ships', '本次交战支持 1—15 艘准备舰船及 1 艘测试敌舰')
    ps.need(direct_instance_id in {r['state']['instance_id'] for _,r in ships}, '$.direct_instance_id', '请选择参战舰船作为旗舰')
    seeds, bindings, instances, armors, latches, names = [], [], [], [], [], {}
    radii = [max((hypot(*p) for deck in d.snapshot.hull.normalized_blueprint.decks for region in deck.regions for p in region.vertices_m),default=50) for d,_ in ships]
    spacing=max(150,2*max(radii)+50)
    direct=None
    for i,(design,record) in enumerate(ships):
        record=bp.validate_record(record,design); v=record['state']; seed=design.resources.seed
        ps.need(v['service']['status']=='available', '$.service', '参战舰船已失去作战能力，需要后续维修或救援')
        ps.need('fuel_tanks' in v or v['fuel_units']==seed.motion.fuel_units, '$.fuel', '旧配置仍保留原燃料状态')
        modules={m['module_id']:m for m in v['modules']}
        loaded=replace(seed,
            motion=replace(seed.motion,position_world_m=replace(seed.motion.position_world_m,x=(i-(len(ships)-1)/2)*spacing,y=-max(300,max(radii)+100)),
                hull_integrity_fraction=v['hull_integrity_fraction'],fuel_units=v['fuel_units']),
            devices=replace(seed.devices,initial_durability_points=tuple(modules[m.instance_id]['durability_points'] for m in seed.devices.modules)),
            resources=replace(seed.resources,modes=tuple('active' if m.prototype.category in ('weapon','sensor','fire_control') else modules[m.id]['operating_mode'] for m in seed.resources.modules),
                crew=tuple((c['crew_type'],c['count']) for c in v['crew']),policy=ps.RuntimePowerPolicyInput.parse(v['power_policy'],'$.power_policy')),
            command=replace(seed.command,wounded_aboard=v['wounded_aboard']))
        sid=seed.contributions.ship_id
        if v['instance_id']==direct_instance_id: direct=sid
        seeds.append(loaded); latches.append((sid,tuple(v['engine_latches'])))
        bindings.append(TacticalSceneShipBinding(sid,design.snapshot,design.sortie,side_id='side.blue',fleet_id='fleet.blue'))
        instances.append(ps.InstanceBinding(design.resources,ps.parse_instance(v,design.resources)))
        armors.append(record['armor']); names[sid]=design.archive()['document']['outfit']['name']
    seeds.append(template.session._seeds[1]); bindings.append(technical_scenario.bindings[1])
    enemy=template.inventory.prepared.bindings[1]
    from . import tactical_ignition as ignition
    new_design=next((d for d,_ in ships if d.resources.definition()['interface']==ignition.RESOURCE_INTERFACE),None)
    if new_design:
        enemy=ignition_enemy(enemy,technical_scenario.bindings[1].snapshot,new_design.archive()['policy'])
    instances.append(enemy); names[seeds[-1].contributions.ship_id]='红方测试舰'
    session=ps.sf.SimplifiedFlightSession(tuple(seeds),template.session._profile,direct_ship_id=direct,initial_resource_latches=tuple(latches))
    ps.need(next(s for s in session.world.ships if s.ship_id==direct).authority_allowed,'$.service','所选旗舰当前状态无法操纵，战损已保留')
    scenario=PreparedScenario(tuple(bindings),technical_scenario.projectile_catalog,technical_scenario.material_registry,
        dict(interface='gaotian.prepared-deployment/x1a-v1',scenario_id='gtw.prepared.skirmish.v1',
             ship_names=names,cargo_mass_policy='fixed-design-mass-v1',enemy_policy='h5d-ignitable-technical-enemy' if new_design else 'p2a-technical-enemy'))
    battle=GunneryBattle(session,scenario,template.config,damage_enabled=True,instance_bindings=tuple(instances),allow_test_ignition=allow_test_ignition)
    armor=[]
    for i,rows in enumerate(armors):
        values={(r['deck_id'],r['deck_level'],r['region_id'],r['edge_index']):r['durability'] for r in rows}
        ps.need(set(values)=={e.key for e in battle.damage.edges[i]},'$.armor','入战装甲边与保存设计不匹配')
        armor.append(tuple(values[e.key] for e in battle.damage.edges[i]))
    armor.append(battle.damage.initial.armor[-1])
    battle.damage_state=DamageState(tuple(armor));battle.entry_armor=battle.damage_state.armor
    return battle,render_static(scenario)
