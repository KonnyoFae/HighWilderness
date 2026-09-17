"""5c immutable missile recipes and individually identified logistics stores.

The catalog owns recipes; a unit's exact model/warhead plus the enclosing resource
fingerprint preserves its original cost. Flight/seeker behavior is added in 5e–5g.
"""
from math import ceil
from . import persistent_ship as ps

INTERFACE = 'gaotian.missile-logistics/5c-v1'
INSTANCE_INTERFACE = 'gaotian.persistent-ship/5c-v1'
STATE_INTERFACE = 'gaotian.missile-stores/5c-v1'
RESOURCE_INTERFACE = 'gaotian.persistent-ship-resources/5c-v1'
POLICY_INTERFACE = 'gaotian.battle-preparation-policy/5c-v1'


def recipe(profile, model_id, warhead_id):
    model = next(m for m in profile['models'] if m['id'] == model_id)
    warhead = next(w for w in profile['warheads'] if w['id'] == warhead_id)
    ps.need(warhead_id in model['warhead_ids'], '$.missiles.warhead_id', '该导弹不支持所选战斗部')
    result = {c['good_id']: c['quantity'] for c in model['cargo_costs']}
    result[warhead['good_id']] = result.get(warhead['good_id'], 0) + model['warhead_material_quantity']
    return result


def duration(profile, spec, model_id, kind):
    model = next(m for m in profile['models'] if m['id'] == model_id)
    if kind == 'assemble': return model['assembly_steps']
    if kind == 'load_raw': return ceil(model['raw_reload_steps'] * spec['raw_reload_multiplier'])
    if kind == 'load_ready': return ceil(model['ready_reload_steps'] * spec['ready_reload_multiplier'])
    return spec['unload_steps' if kind in ('unload','swap') else 'dismantle_steps']


def validate_profile(value, goods, modules=None):
    ps.obj(value, 'interface id version balance_status models warheads launchers magazines', '$.missiles')
    ps.need(value['interface'] == INTERFACE and value['balance_status'] == 'prototype_unbalanced', '$.missiles', '不支持的导弹后勤规则')
    ps.identifier(value['id'], '$.missiles.id'); ps.integer(value['version'], '$.missiles.version', 1)
    models = ps.rows(value['models'], 'id', '$.missiles.models')
    heads = ps.rows(value['warheads'], 'id', '$.missiles.warheads')
    ps.need(bool(models) and bool(heads), '$.missiles', '需要导弹与战斗部目录')
    for head in heads.values():
        ps.obj(head, 'id name good_id detonation_multiplier', '$.missiles.warheads')
        ps.need(type(head['name']) is str and head['name'] and head['good_id'] in goods, '$.missiles.warheads', '战斗部材料缺失')
        ps.number(head['detonation_multiplier'], '$.detonation_multiplier', .000001, 100)
    for model in models.values():
        ps.obj(model, 'id version name size diameter_mm mass_g propulsion seeker durability_points warhead_scale warhead_ids default_warhead_id '
               'assembly_steps raw_reload_steps ready_reload_steps cargo_costs warhead_material_quantity detonation_resources', '$.missiles.models')
        ps.need(type(model['name']) is str and model['name'], '$.missiles.name', '需要型号名称')
        ps.need(model['size'] in ('small','medium'), '$.missiles.size', '当前只支持小型/中型战术导弹')
        ps.integer(model['diameter_mm'], '$.missiles.diameter_mm', 1, 500)
        ps.need((model['diameter_mm'] < 100) == (model['size'] == 'small'), '$.missiles.size', '弹径与尺寸档不符')
        ps.need(model['propulsion'] in ('rocket','turbojet','pending_interceptor') and
                model['seeker'] in ('active_radar','infrared','anti_radiation','radar_infrared','pending_interceptor'), '$.missiles', '未知动力或导引头')
        # Old passive_radar meant semi-active illumination: never reinterpret it.
        for k in ('version','mass_g','durability_points','assembly_steps','raw_reload_steps','ready_reload_steps','warhead_material_quantity','detonation_resources'):
            ps.integer(model[k], '$.missiles.'+k, 1, 10000000)
        ps.number(model['warhead_scale'], '$.missiles.warhead_scale', .000001, 1)
        ids = model['warhead_ids']
        ps.need(type(ids) is list and ids and all(type(k) is str for k in ids) and len(set(ids)) == len(ids)
                and set(ids) <= set(heads) and model['default_warhead_id'] in ids, '$.missiles.warheads', '无效战斗部引用')
        costs = ps.rows(model['cargo_costs'], 'good_id', '$.missiles.cargo_costs')
        ps.need(bool(costs) and set(costs) <= set(goods), '$.missiles.cargo_costs', '导弹原料缺失')
        for cost in costs.values():
            ps.obj(cost, 'good_id quantity', '$.missiles.cargo_costs'); ps.integer(cost['quantity'], '$.quantity', 1)
    all_ids = set()
    for group in ('launchers','magazines'):
        specs = ps.rows(value[group], 'module_id', '$.missiles.'+group)
        ps.need(not all_ids.intersection(specs), '$.missiles', '重复的导弹设备绑定')
        all_ids.update(specs)
        for mid, spec in specs.items():
            fields = 'module_id compatible_model_ids capacity_by_model default_model_id'
            fields += (' launcher_kind raw_reload_multiplier ready_reload_multiplier shot_interval_steps launch_delay_steps unload_steps '
                       'warhead_switch_in_battle auto_fire_default integrated_fire_control turret' if group == 'launchers'
                       else ' assembly_parallel dismantle_steps')
            ps.obj(spec, fields, '$.missiles.'+group)
            ids = spec['compatible_model_ids']
            ps.need(type(ids) is list and ids and all(type(k) is str for k in ids) and len(set(ids)) == len(ids)
                    and set(ids) <= set(models) and spec['default_model_id'] in ids, '$.missiles.compatible_model_ids', '未知或重复的导弹型号')
            ps.need(type(spec['capacity_by_model']) is dict and set(spec['capacity_by_model']) == set(ids), '$.missiles.capacity_by_model', '逐型容量表缺失')
            for cap in spec['capacity_by_model'].values(): ps.integer(cap, '$.missiles.capacity', 1, 1000)
            if group == 'launchers':
                ps.need(spec['launcher_kind'] in ('turret','vls','automatic_interceptor'), '$.launcher_kind', '未知发射器类型')
                ps.need(spec['launcher_kind'] == 'vls' or len(ids) == 1, '$.compatible_model_ids', '炮塔式发射器须绑定单一型号')
                for k in ('raw_reload_multiplier','ready_reload_multiplier'): ps.number(spec[k], '$.'+k, .001, 100)
                for k in ('shot_interval_steps','unload_steps'): ps.integer(spec[k], '$.'+k, 1, 1000000)
                ps.integer(spec['launch_delay_steps'], '$.launch_delay_steps', 0, 1000000)
                for k in ('warhead_switch_in_battle','auto_fire_default','integrated_fire_control'):
                    ps.need(type(spec[k]) is bool, '$.'+k, '需要明确开关')
                t = ps.obj(spec['turret'], 'minimum_mdeg maximum_mdeg slew_mdeg_per_s', '$.turret')
                ps.integer(t['minimum_mdeg'], '$.turret.minimum_mdeg', -360000, 360000)
                ps.integer(t['maximum_mdeg'], '$.turret.maximum_mdeg', -360000, 360000)
                ps.need(t['minimum_mdeg'] < t['maximum_mdeg'], '$.turret', '射界为空')
                ps.integer(t['slew_mdeg_per_s'], '$.turret.slew_mdeg_per_s', 1)
            else:
                ps.integer(spec['assembly_parallel'], '$.assembly_parallel', 1, 100)
                ps.integer(spec['dismantle_steps'], '$.dismantle_steps', 1, 1000000)
            if modules is not None:
                ps.need(mid in modules, '$.missiles.module_id', '导弹设备不存在')
                prototype = modules[mid].prototype
                ps.need(prototype.category == ('weapon' if group == 'launchers' else 'ammunition_magazine'), '$.missiles', '导弹设备类别不符')
                cap = prototype.capability.to_dict()
                ps.need(set(ids) == set(cap['compatible_munition_ids']), '$.missiles', '设备型号兼容表与原型不符')
                if group == 'launchers': ps.need(cap['weapon_class'] == 'missile_launcher', '$.missiles', '需要导弹发射器原型')
                ps.need(max(spec['capacity_by_model'].values()) <= cap['ready_round_capacity' if group == 'launchers' else 'capacity_units'], '$.missiles', '库存容量超过模块原型')


def fresh(profile):
    result = dict(interface=STATE_INTERFACE, next_serial=1, launchers=[], magazines=[])
    models = {m['id']:m for m in profile['models']}
    for group in ('launchers','magazines'):
        for spec in profile[group]:
            model_id = spec['default_model_id']
            row = dict(module_id=spec['module_id'], model_id=model_id, warhead_id=models[model_id]['default_warhead_id'])
            if group == 'launchers':
                row.update(auto_fire=spec['auto_fire_default'], ready=[], job=None, load_remaining=0, unload_remaining=0, cooldown_steps=0)
            else: row.update(stock=[], jobs=[], assembly_remaining=0)
            result[group].append(row)
    return result


def validate_state(state, profile):
    ps.obj(state, 'interface next_serial launchers magazines', '$.missile_stores')
    ps.need(state['interface'] == STATE_INTERFACE, '$.missile_stores.interface', '不支持的导弹库存版本')
    ps.integer(state['next_serial'], '$.missile_stores.next_serial', 1)
    models = {m['id']:m for m in profile['models']}
    seen = set()
    def unit(value, row):
        ps.obj(value, 'serial model_id warhead_id source', '$.missile.unit')
        serial = ps.integer(value['serial'], '$.missile.serial', 1, state['next_serial']-1)
        ps.need(serial not in seen, '$.missile.serial', '同一枚导弹不能同时存在于多个位置'); seen.add(serial)
        ps.need(value['model_id'] == row['model_id'] and value['warhead_id'] in models[row['model_id']]['warhead_ids']
                and value['source'] in ('raw','magazine'), '$.missile.unit', '弹体型号、战斗部或来源不符')
    for group in ('launchers','magazines'):
        specs = {s['module_id']:s for s in profile[group]}
        actual = ps.rows(state[group], 'module_id', '$.missile_stores.'+group)
        ps.need(set(actual) == set(specs), '$.missile_stores', '导弹设备身份不符')
        for mid, row in actual.items():
            spec = specs[mid]
            ps.obj(row, 'module_id model_id warhead_id'+(' auto_fire ready job load_remaining unload_remaining cooldown_steps' if group == 'launchers' else ' stock jobs assembly_remaining'), '$.missile_stores')
            ps.need(type(row['model_id']) is str and row['model_id'] in spec['compatible_model_ids'] and
                    row['warhead_id'] in models[row['model_id']]['warhead_ids'], '$.missile_stores', '设备选型无效')
            cap = spec['capacity_by_model'][row['model_id']]
            store = row['ready' if group == 'launchers' else 'stock']
            ps.need(type(store) is list and len(store) <= cap, '$.missile_stores', '导弹库存超出容量')
            for value in store: unit(value, row)
            if group=='magazines': ps.need(all(u['source']=='raw' for u in store), '$.missile.stock', '库内整装弹不能保留发射器转移标记')
            if group == 'launchers':
                ps.need(type(row['auto_fire']) is bool, '$.auto_fire', '需要明确自动发射开关')
                ps.integer(row['cooldown_steps'], '$.cooldown_steps')
                ps.integer(row['load_remaining'], '$.load_remaining', 0, cap)
                ps.integer(row['unload_remaining'], '$.unload_remaining', 0, cap)
                jobs = [] if row['job'] is None else [row['job']]
            else:
                ps.integer(row['assembly_remaining'], '$.assembly_remaining', 0, 1000)
                jobs = row['jobs']
                ps.need(type(jobs) is list and len(jobs) <= spec['assembly_parallel']+1, '$.jobs', '组装并发数超出限制')
            occupied = len(store)
            for job in jobs:
                ps.obj(job, 'kind unit remaining_work_steps total_work_steps return_magazine_id', '$.missile.job')
                ps.need(job['kind'] in (('load_raw','load_ready','unload','swap') if group == 'launchers' else ('assemble','dismantle')), '$.missile.job', '设备不能执行此作业')
                unit(job['unit'], row)
                if job['kind'] in ('load_raw','assemble','load_ready'):
                    ps.need(job['unit']['source']==('magazine' if job['kind']=='load_ready' else 'raw'), '$.missile.job.source', '作业与原料/整装弹来源不符')
                ps.integer(job['total_work_steps'], '$.total_work_steps', 1)
                ps.need(job['total_work_steps'] == duration(profile,spec,row['model_id'],job['kind']), '$.missile.job', '作业耗时与型号不符')
                ps.number(job['remaining_work_steps'], '$.remaining_work_steps', .000000001, job['total_work_steps'])
                dest = job['return_magazine_id']
                if dest is not None:
                    ps.identifier(dest, '$.return_magazine_id')
                    ps.need(job['kind'] in ('unload','swap') and dest in {s['module_id'] for s in profile['magazines']}, '$.missile.job', '无效的退库目标')
                occupied += 1
            ps.need(occupied <= cap, '$.missile_stores', '在制与整装弹合计超出容量')
            if group == 'magazines':
                ps.need(sum(j['kind']=='assemble' for j in jobs) <= spec['assembly_parallel'] and
                        sum(j['kind']=='dismantle' for j in jobs) <= 1, '$.missile.jobs', '工作台并发数超限')


def totals(state):
    """Distinct ledger resources for every model/warhead, module and work stage."""
    result = {}
    if not state: return result
    for group in ('launchers','magazines'):
        for row in state[group]:
            units = [('ready' if group=='launchers' else 'stock', u) for u in row['ready' if group=='launchers' else 'stock']]
            jobs = ([row['job']] if row['job'] else []) if group=='launchers' else row['jobs']
            units.extend((j['kind'],j['unit']) for j in jobs)
            for stage, u in units:
                key = 'missile:'+row['module_id']+':'+stage+':'+u['model_id']+':'+u['warhead_id']
                result[key] = result.get(key,0)+1
    return result


def explosives(profile, row):
    """Magazine explosion includes paid-for work in progress, exactly once."""
    models = {m['id']:m for m in profile['models']}
    heads = {h['id']:h for h in profile['warheads']}
    units = row['stock'] + [j['unit'] for j in row['jobs']]
    return sum(models[u['model_id']]['detonation_resources']*models[u['model_id']]['warhead_scale']*
               heads[u['warhead_id']]['detonation_multiplier'] for u in units)


def ledger_keys(profile):
    return {'missile:'+s['module_id']+':'+stage+':'+m+':'+h
            for group, stages in (('launchers',('ready','load_raw','load_ready','unload','swap')), ('magazines',('stock','assemble','dismantle')))
            for s in profile[group] for m in s['compatible_model_ids']
            for h in next(x['warhead_ids'] for x in profile['models'] if x['id']==m) for stage in stages}
