"""X1a.1 boundary compilation and preparation contracts; no fixed-step work.

Saved editor documents retain their original geometry and resource references.
Only the known catalog migrations and explicit no-burn projection are applied.
Inventory edits are drafts here; X1a.2 owns committing supply/reload transactions.
"""
from dataclasses import asdict, dataclass

from 高天荒野舰艇数据契约 import (
    ModulePrototypeCatalog, OutfitPlanInput, SortieConfigurationInput, ResourceReference,
    merge_module_prototype_catalogs, migrate_known_module_catalog_v1_to_v2,
    canonical_sha256,
)
from 高天荒野舰艇无界面舾装编译器 import compile_outfit, build_derived_ship_snapshot
from 高天荒野舰艇出航配置编译器 import compile_sortie_configuration
from 高天荒野舰艇运行时参数编译器 import initialize_ship_instance_snapshot, compile_runtime_ship_parameters
from 高天荒野舰艇战术机动求解器 import build_tactical_ship_model, initialize_tactical_motion_state
from . import outfit_documents, outfits, persistent_ship as ps, tactical_settlement as ts, damage_control_resources as dc
from .tactical_resources import compile_tactical_fuel_resources
from . import tactical_fuel as fuel, tactical_ignition as ignition
from .simplified_propulsion import compile_snapshot_contributions
from .tactical_devices import seed_from_snapshot as device_seed
from .tactical_resources_runtime import seed_from_snapshot as resource_seed
from .tactical_command_runtime import seed_from_snapshot as command_seed

DESIGN_INTERFACE = 'gaotian.battle-preparation-design/x1a-v1'
DRAFT_INTERFACE = 'gaotian.battle-preparation-draft/x1a-v1'
SUPPLY_INTERFACE = 'gaotian.battle-preparation-supply/x1a-v1'
POLICY_INTERFACE = 'gaotian.battle-preparation-policy/x1a-v1'
SWAP_POLICY = 'explicit-discard-no-refund/v1'


@dataclass(frozen=True)
class PreparedDesign:
    archive_json: str
    snapshot: object
    sortie: object
    resources: ps.ResourcePack

    def archive(self):
        return ps.decode(self.archive_json)


def compile_design(document, index, deployment, policy, *, ship_id):
    """Host supplies an already granted/saved document, never a path from JSON.

    Arbitrary layouts using the supported exact editor catalogs are accepted;
    no named outfit migration, sample ship replacement or thrust multiplier.
    """
    ps.identifier(ship_id, '$.ship_id')
    document, deployment, policy = map(ps.clone, (document, deployment, policy))
    source, binding = outfit_documents.unpack(document, index)
    doc = outfits.document(source, index, binding['hull'] if binding else None)
    source_outfit = doc.compile()
    hull = doc._hull
    allowed=('gtw.filling.none','gtw.filling.rack')+(('gtw.filling.spirit_fuel',) if policy.get('interface') in dc.FUEL_POLICY_INTERFACES else ())+(('gtw.filling.fireproof',) if policy.get('interface')==ignition.POLICY_INTERFACE else ())
    ps.need(all(d.filling is None or d.filling.config.id in allowed for d in hull.decks),
        '$.hull.filling', '当前旧配置尚未接入此填充效果；请使用支持对应效果的新配置')
    ps.obj(policy, 'interface id version modules propulsion_timing goods projectiles recipes fire_control enabled_recipe_ids' +
        (' continuous_damage' if policy.get('interface') in dc.FIRE_POLICY_INTERFACES else '') +
        (' repair' if policy.get('interface') in dc.REPAIR_POLICY_INTERFACES else '')+
        (' fuel' if policy.get('interface') in dc.FUEL_POLICY_INTERFACES else '')+(' ignition' if policy.get('interface')==ignition.POLICY_INTERFACE else ''), '$.policy')
    ps.need(policy['interface'] in (POLICY_INTERFACE, dc.POLICY_INTERFACE, *dc.FIRE_POLICY_INTERFACES), '$.policy.interface', '不支持的战前准备资源政策')
    # Make indexed legacy plans portable too, with an exact embedded hull.
    if binding is None:
        binding = outfit_documents.bind(hull.normalized_blueprint.to_dict(), index)
    saved_document = dict(interface=outfit_documents.DOCUMENT_INTERFACE, outfit=source, hull_binding=binding)
    catalogs = [ModulePrototypeCatalog.parse(s) for d, s in index.resources.values()
                if d['kind'] == 'ModulePrototypeCatalog']
    migrated = [migrate_known_module_catalog_v1_to_v2(c) for c in catalogs]
    catalog = merge_module_prototype_catalogs(migrated, id='gtw.module_catalog.preparation', version=2,
        name='战前准备技术目录', fixture_level='contract_fixture', schema=migrated[0].schema)
    # Legacy 0.2 s thrusters cannot schedule all discrete thrust stages at 60 Hz.
    # Adapt explicitly by prototype hash and policy, never infer new timing from a name.
    timing = {}
    ps.need(type(policy['propulsion_timing']) is list, '$.propulsion_timing', '需要推进时间适配表')
    for row in policy['propulsion_timing']:
        ps.obj(row, 'prototype prototype_sha256 response_time_s', '$.propulsion_timing')
        ref = ResourceReference.parse(row['prototype'], '$.propulsion_timing.prototype')
        original = doc._module_catalog.module(ref)
        ps.need(original.category in ('main_engine', 'maneuver_thruster') and ref.id not in timing
            and canonical_sha256(original) == row['prototype_sha256'], '$.propulsion_timing', '推进时间来源不匹配')
        timing[ref.id] = ps.number(row['response_time_s'], '$.response_time_s', minimum=0.000001)
    ps.need(set(timing) == {m.reference.id for m in catalog.modules if m.category in ('main_engine', 'maneuver_thruster')},
            '$.propulsion_timing', '推进时间适配表不完整')
    catalog_value = catalog.to_dict()
    catalog_value['id'] += '.timing'
    for m in catalog_value['modules']:
        if m['id'] in timing:
            m['capability']['response_time_s'] = timing[m['id']]
    catalog = ModulePrototypeCatalog.parse(catalog_value)
    plan = ps.clone(source)
    for module in plan['modules']:
        # The known migration changes only engine prototype versions, not IDs,
        # module positions, hosts, rotations or weapon group membership.
        target = next(m for m in catalog.modules if m.reference.id == module['prototype']['id'])
        module['prototype'] = target.reference.to_dict()
    catalog, plan, fuel_manifest = compile_tactical_fuel_resources(catalog, OutfitPlanInput.parse(plan))
    snapshot = build_derived_ship_snapshot(hull, compile_outfit(plan, hull, catalog, doc._coating_catalog))
    ps.obj(deployment, 'id version crew fuel_units height_layer control_mode active_remote_core_instance_id', '$.deployment')
    config = SortieConfigurationInput.parse(dict(schema='gaotian.ship/v1alpha1', kind='SortieConfiguration',
        name='战前准备人员与部署技术预设', fixture_level='contract_fixture',
        outfit_plan=dict(id=plan.id, version=plan.version), bulk_cargo=[], **deployment))
    sortie = compile_sortie_configuration(snapshot, config)
    legacy = initialize_ship_instance_snapshot(snapshot, sortie, embed_design_state=True)
    runtime = compile_runtime_ship_parameters(snapshot, sortie, legacy)
    model = build_tactical_ship_model(runtime, snapshot)
    table = compile_snapshot_contributions(ship_id, snapshot, catalog)
    seed = ps.sf.ShipSeed(table, ps.sf.MotionModel.from_legacy(model), initialize_tactical_motion_state(model),
        devices=device_seed(snapshot, legacy, table), resources=resource_seed(snapshot, legacy, table),
        command=command_seed(snapshot, legacy, sortie, table))
    rules = {}
    ps.need(type(policy['modules']) is list, '$.policy.modules', '需要原型规则列表')
    for rule in policy['modules']:
        ps.obj(rule, 'prototype prototype_sha256 binding', '$.policy.modules')
        ref = rule['prototype']
        reference = ResourceReference.parse(ref, '$.policy.prototype')
        key = reference.id, reference.version
        ps.need(key not in rules, '$.policy.prototype', '重复原型规则')
        original = doc._module_catalog.module(reference)
        ps.need(canonical_sha256(original) == rule['prototype_sha256'], '$.policy.prototype', '原型规则内容不匹配')
        ps.need(type(rule['binding']) is dict and 'module_id' not in rule['binding'], '$.policy.binding', '规则不能覆盖模块实例身份')
        rules[key] = rule['binding']
    definition = dict(interface=ps.RESOURCE_INTERFACE, id=policy['id'], version=policy['version'],
        source_seed_sha256=canonical_sha256(asdict(seed)), **{k: policy[k] for k in ('goods', 'projectiles', 'recipes', 'fire_control')},
        holds=[], magazines=[], weapons=[])
    groups = {'cargo_hold': 'holds', 'ammunition_magazine': 'magazines', 'weapon': 'weapons'}
    if any(d.filling for d in hull.decks):
        definition['interface'] = ps.FILLING_RESOURCE_INTERFACE
        definition['filling_holds'] = [dict(deck_id=d.id, configuration=d.filling.config.to_dict(),
            capacity_cm3=d.filling.cargo_capacity_cm3, policy=ps.FILLING_CAPACITY_POLICY)
            for d in hull.decks if d.filling and d.filling.config.id == 'gtw.filling.rack']
    if policy['interface'] in (dc.POLICY_INTERFACE, *dc.FIRE_POLICY_INTERFACES):
        definition['interface'] = dc.RESOURCE_INTERFACE
        definition.setdefault('filling_holds', [])
        definition['damage_controls'] = []
        groups['damage_control'] = 'damage_controls'
        if policy['interface'] in dc.FIRE_POLICY_INTERFACES:
            definition['interface'] = dc.FIRE_RESOURCE_INTERFACE
            definition['continuous_damage'] = policy['continuous_damage']
        if policy['interface'] in dc.REPAIR_POLICY_INTERFACES:
            definition['interface'] = dc.REPAIR_RESOURCE_INTERFACE
            definition['repair'] = policy['repair']
        if policy['interface'] in dc.FUEL_POLICY_INTERFACES:
            definition.update(interface=fuel.RESOURCE_INTERFACE,fuel=policy['fuel'],fuel_tanks=fuel.definitions(snapshot,policy['fuel']))
    if policy['interface']==ignition.POLICY_INTERFACE:
        definition.update(interface=ignition.RESOURCE_INTERFACE, ignition=policy['ignition'],
            ignition_decks=ignition.definitions(snapshot,policy['ignition']))
    for m in source_outfit.instances:
        if m.prototype.category in groups:
            key = m.prototype.reference.id, m.prototype.reference.version
            ps.need(key in rules, '$.modules.'+m.id, '此原型尚未绑定战术容量或武器配方')
            definition[groups[m.prototype.category]].append(dict(module_id=m.id, **rules[key]))
    pack = ps.compile_resources(seed, definition)
    enabled = policy['enabled_recipe_ids']
    ps.need(type(enabled) is list and all(type(r) is str for r in enabled) and len(enabled) == len(set(enabled))
        and set(enabled) <= {r['id'] for r in definition['recipes']}, '$.enabled_recipe_ids', '启用配方不匹配')
    archive = dict(interface=DESIGN_INTERFACE, ship_id=ship_id, document=saved_document,
        deployment=deployment, policy=policy, catalog_dependencies_sha256=outfit_documents.catalog_hash(index),
        source_document_sha256=canonical_sha256(saved_document), source_outfit_sha256=canonical_sha256(source_outfit),
        snapshot_sha256=snapshot.source_sha256, resources_sha256=pack.source_sha256, fuel_projection=fuel_manifest)
    return PreparedDesign(ps.encode(archive), snapshot, sortie, pack)


def restore_design(archive, index):
    value = ps.clone(archive)
    ps.obj(value, 'interface ship_id document deployment policy catalog_dependencies_sha256 source_document_sha256 '
        'source_outfit_sha256 snapshot_sha256 resources_sha256 fuel_projection', '$.design')
    ps.need(value['interface'] == DESIGN_INTERFACE, '$.design.interface', '不支持的设计绑定版本')
    result = compile_design(value['document'], index, value['deployment'], value['policy'], ship_id=value['ship_id'])
    ps.need(result.archive() == value, '$.design', '设计、目录或资源政策已变化，不能自动重绑')
    return result


def new_record(design, instance_id):
    """New empty inventory uses the existing P1a/P3 contracts, not a second save format."""
    return dict(interface=ts.SHIP_INTERFACE, ship_id=design.resources.seed.contributions.ship_id,
        design_sha256=design.snapshot.source_sha256, resources=design.resources.definition(),
        state=ps.fresh_instance(design.resources, instance_id).to_dict(),
        armor=[dict(deck_id=d, deck_level=l, region_id=r, edge_index=e, durability=hp)
               for d, l, r, e, hp in design.snapshot.hull.local_armor_durability_proxy])


def validate_record(record, design):
    value = ps.clone(record)
    ps.obj(value, 'interface ship_id design_sha256 resources state armor', '$.ship')
    reference = new_record(design, value['state']['instance_id'])
    ps.need(all(value[k] == reference[k] for k in ('interface', 'ship_id', 'design_sha256', 'resources')),
            '$.ship', '准备中的舰船与设计不匹配')
    value['state'] = ps.parse_instance(value['state'], design.resources).to_dict()
    ps.need(all(w['reload'] is None for w in value['state']['weapons']), '$.reload', '先完成战后结算再准备')
    dc.require_settled(value['state'])
    ps.need(type(value['armor']) is list and len(value['armor']) == len(reference['armor']), '$.armor', '装甲记录缺失')
    keys = ('deck_id', 'deck_level', 'region_id', 'edge_index')
    maxima = {tuple(row[k] for k in keys): row['durability'] for row in reference['armor']}
    seen = set()
    for row in value['armor']:
        ps.obj(row, 'deck_id deck_level region_id edge_index durability', '$.armor')
        ps.identifier(row['deck_id'], '$.deck_id'); ps.identifier(row['region_id'], '$.region_id')
        ps.integer(row['deck_level'], '$.deck_level'); ps.integer(row['edge_index'], '$.edge_index')
        key = tuple(row[k] for k in keys)
        ps.need(key in maxima and key not in seen, '$.armor', '装甲身份不匹配')
        ps.number(row['durability'], '$.armor.durability', maximum=maxima[key]); seen.add(key)
    return value


def parse_supply(value, goods):
    v = ps.clone(value)
    ps.obj(v, 'interface supply_id revision ammunition_resources cargo'+(' fuel_units' if v.get('interface')==fuel.SUPPLY_INTERFACE else ''), '$.supply')
    ps.need(v['interface'] in (SUPPLY_INTERFACE,fuel.SUPPLY_INTERFACE), '$.supply.interface', '不支持的供给版本')
    if 'fuel_units' in v:ps.number(v['fuel_units'],'$.supply.fuel_units',maximum=ps.MAX_INT)
    ps.identifier(v['supply_id'], '$.supply_id'); ps.integer(v['revision'], '$.supply.revision')
    ps.integer(v['ammunition_resources'], '$.supply.ammunition_resources')
    rows = ps.rows(v['cargo'], 'good_id', '$.supply.cargo')
    ps.need(set(rows) <= {g['id'] for g in goods}, '$.supply.cargo', '供给中存在未知货物')
    for row in rows.values():
        ps.obj(row, 'good_id quantity', '$.supply.cargo'); ps.integer(row['quantity'], '$.supply.quantity')
    v['cargo'] = [rows[k] for k in sorted(rows)]
    return v


def new_draft(preparation_id, ships, supply, *, supply_goods=None):
    """ships is a sequence of (PreparedDesign, P3 record). Default is keep all."""
    ps.identifier(preparation_id, '$.preparation_id')
    ps.need(0 < len(ships) <= 16, '$.ships', '需要 1—16 艘准备舰船')
    goods, rows, ids = {}, [], set()
    for design, raw in ships:
        record = validate_record(raw, design)
        state = record['state']
        ps.need(state['instance_id'] not in ids, '$.ships', '重复准备舰船')
        ids.add(state['instance_id'])
        for good in design.resources.definition()['goods']:
            ps.need(good['id'] not in goods or goods[good['id']] == good, '$.goods', '跨舰货物定义不一致')
            goods[good['id']] = good
        rows.append(dict(instance_id=state['instance_id'], revision=state['revision'],
            record_sha256=canonical_sha256(record), design_sha256=canonical_sha256(design.archive()),
            magazines=state['magazines'], cargo=state['cargo'],
            weapons=[dict(module_id=w['module_id'], action='keep', recipe_id=None, batches=0) for w in state['weapons']]))
    dc_version = any(d.resources.definition()['interface'] in dc.RESOURCE_INTERFACES for d, _ in ships)
    fuel_version=any('fuel_tanks' in r['state'] for _,r in ships)
    if dc_version:
        states = {r['state']['instance_id']: r['state'] for _, r in ships}
        for row in rows:
            row['damage_controls'] = [dict(module_id=d['module_id'], prepare=False)
                                      for d in states[row['instance_id']].get('damage_controls', ())]
            if fuel_version:
                row['fuel_tanks']=[dict(tank_id=t['tank_id'],quantity_units=t['quantity_units']) for t in states[row['instance_id']].get('fuel_tanks',())]
    if supply_goods is not None:
        known = {g['id']: g for g in supply_goods}
        ps.need(all(known.get(k) == v for k,v in goods.items()), '$.goods', '舰内货物与供给定义不匹配')
    supply = parse_supply(supply, list(goods.values()) if supply_goods is None else supply_goods)
    return dict(interface=fuel.DRAFT_INTERFACE if fuel_version else dc.DRAFT_INTERFACE if dc_version else DRAFT_INTERFACE, preparation_id=preparation_id, revision=0, swap_policy=SWAP_POLICY,
        supply_id=supply['supply_id'], supply_revision=supply['revision'], supply_sha256=canonical_sha256(supply), ships=rows)


def validate_draft(value, ships, supply, *, supply_goods=None):
    """Strict stale-state/selection boundary; feasibility and spending are X1a.2."""
    v = ps.clone(value)
    ps.obj(v, 'interface preparation_id revision swap_policy supply_id supply_revision supply_sha256 ships', '$.draft')
    ps.integer(v['revision'], '$.draft.revision')
    ps.integer(v['supply_revision'], '$.supply_revision')
    baseline = new_draft(v['preparation_id'], ships, supply, supply_goods=supply_goods)
    ps.need(all(v[k] == baseline[k] for k in ('interface', 'swap_policy', 'supply_id', 'supply_revision', 'supply_sha256')),
            '$.supply', '供给或准备政策已变化，请重新核对')
    actual = ps.rows(v['ships'], 'instance_id', '$.ships')
    expected = {r['instance_id']: r for r in baseline['ships']}
    ps.need(set(actual) == set(expected), '$.ships', '参战实例集合已变化')
    designs = {r['state']['instance_id']: d for d, r in ships}
    for key, row in actual.items():
        ps.obj(row, 'instance_id revision record_sha256 design_sha256 magazines cargo weapons' +
            (' damage_controls' if v['interface'] in (dc.DRAFT_INTERFACE,fuel.DRAFT_INTERFACE) else '')+
            (' fuel_tanks' if v['interface']==fuel.DRAFT_INTERFACE else ''), '$.ships')
        ps.integer(row['revision'], '$.ships.revision')
        ps.need(all(row[k] == expected[key][k] for k in ('revision', 'record_sha256', 'design_sha256')),
                '$.ships.'+key, '舰船已有新的战损、库存或设计版本，准备草稿不能覆盖')
        definition = designs[key].resources.definition()
        if v['interface']==fuel.DRAFT_INTERFACE:
            tanks=ps.rows(row['fuel_tanks'],'tank_id','$.fuel_tanks')
            ps.need(set(tanks)=={t['tank_id'] for t in definition.get('fuel_tanks',())},'$.fuel_tanks','Fuel storage identity mismatch')
            for t in tanks.values():
                ps.obj(t,'tank_id quantity_units','$.fuel_tanks');ps.number(t['quantity_units'],'$.fuel.quantity',maximum=ps.MAX_INT)
            row['fuel_tanks']=[tanks[k] for k in sorted(tanks)]
        magazines = ps.rows(row['magazines'], 'module_id', '$.magazines')
        ps.need(set(magazines) == {m['module_id'] for m in definition['magazines']}, '$.magazines', '弹药库身份不匹配')
        for m in magazines.values():
            ps.obj(m, 'module_id quantity', '$.magazines'); ps.integer(m['quantity'], '$.magazines.quantity')
        cargo = ps.rows(row['cargo'], 'good_id', '$.cargo')
        ps.need(set(cargo) <= {g['id'] for g in definition['goods']}, '$.cargo', '未知货物')
        for c in cargo.values():
            ps.obj(c, 'good_id quantity', '$.cargo'); ps.integer(c['quantity'], '$.cargo.quantity')
        weapons = ps.rows(row['weapons'], 'module_id', '$.weapons')
        ps.need(set(weapons) == {w['module_id'] for w in definition['weapons']}, '$.weapons', '武器身份不匹配')
        enabled = designs[key].archive()['policy']['enabled_recipe_ids']
        for w in definition['weapons']:
            choice = weapons[w['module_id']]
            ps.obj(choice, 'module_id action recipe_id batches', '$.weapons')
            ps.integer(choice['batches'], '$.weapons.batches', maximum=10000)
            ps.need(choice['action'] in ('keep', 'preload', 'discard_and_preload'), '$.weapons.action', '不支持的战前武器动作')
            if choice['action'] == 'keep':
                ps.need(choice['recipe_id'] is None and choice['batches'] == 0, '$.weapons', '保留现状不能隐含装填')
            else:
                ps.need(type(choice['recipe_id']) is str and choice['recipe_id'] in w['recipe_ids']
                    and choice['recipe_id'] in enabled and choice['batches'] > 0, '$.weapons', '弹种未启用、不兼容或批次数无效')
        if v['interface'] in (dc.DRAFT_INTERFACE,fuel.DRAFT_INTERFACE):
            choices = ps.rows(row['damage_controls'], 'module_id', '$.damage_controls')
            ps.need(set(choices) == {d['module_id'] for d in definition.get('damage_controls', ())},
                    '$.damage_controls', '损管设备身份不匹配')
            for choice in choices.values():
                ps.obj(choice, 'module_id prepare', '$.damage_controls')
                ps.need(type(choice['prepare']) is bool, '$.damage_controls.prepare', '需要明确的准备选择')
            row['damage_controls'] = [choices[k] for k in sorted(choices)]
        for field, items in (('magazines', magazines), ('cargo', cargo), ('weapons', weapons)):
            row[field] = [items[k] for k in sorted(items)]
    v['ships'] = [actual[k] for k in sorted(actual)]
    return v
