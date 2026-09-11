"""H1 hull draft commands. Draft shape validation never relaxes saved resource contracts."""
from copy import deepcopy
from math import isfinite

from 高天荒野舰艇数据契约 import (
    ContractError, HullBlueprintInput, HullRegionInput, DeckInput, ResourceReference,
)
from 高天荒野舰艇边缘填充 import HULL_FILLING_SCHEMA, NONE, configuration

HULL_EDIT_COMMAND_INTERFACE_ID = "gaotian.hull-edit-commands/v1alpha1"

MAX_DECKS = 64
MAX_REGIONS = 256
MAX_VERTICES = 4096


def fail(code, path, message):
    raise ContractError('editor.' + code, path, message)


def exact(value, keys, path='$.arguments'):
    if not isinstance(value, dict) or set(value) != set(keys):
        fail('invalid_arguments', path, '字段必须恰好为 ' + ', '.join(sorted(keys)))
    return value


def identity(value):
    ResourceReference.parse({'id': value, 'version': 1}, '$.arguments.id')
    return value


def integer(value, path):
    if type(value) is not int or not 0 <= value < 2**53:
        fail('invalid_arguments', path, '需要非负安全整数')
    return value


def point(value, *, require_grid=False):
    if not isinstance(value, list) or len(value) != 2 or any(type(x) not in (int, float) or abs(x) > 1e6 or not isfinite(x) for x in value):
        fail('invalid_arguments', '$.arguments.point_m', '坐标需要两个有限数值，绝对值不超过一百万米')
    if require_grid and any(x % 2.5 != 0 for x in value):
        fail('coordinate_off_grid', '$.arguments.point_m', '端点必须落在 2.5 米绘图网格上（五米安装格的半格）')
    return deepcopy(value)


def find(values, key):
    identity(key)
    for value in values:
        if value['id'] == key:
            return value
    fail('item_missing', '$.arguments', '找不到 ' + key)


# Parser-only witnesses let the existing parsers validate headers of empty containers.
# They are never returned, compiled or written into a draft.
_WITNESS_REGION = {'id': 'editor.validation.region', 'vertices_m': [[0, 0], [5, 0], [0, 5]],
    'edge_armor': [{'material': {'id': 'editor.validation.material', 'version': 1}, 'thickness_m': 0}] * 3}
_WITNESS_DECK = {'id': 'editor.validation.deck', 'level': 0, 'is_base': True,
    'structure_material': {'id': 'editor.validation.material', 'version': 1}, 'regions': [_WITNESS_REGION]}


def validate_hull_draft(source):
    """Accept empty deck/region lists; validate every other field with existing parsers."""
    if not isinstance(source, dict) or not isinstance(source.get('decks'), list):
        fail('invalid_draft', '$.decks', '草稿必须含甲板列表')
    header = deepcopy(source)
    with_filling = source.get('schema') == HULL_FILLING_SCHEMA
    header['decks'] = [deepcopy(_WITNESS_DECK)]
    if with_filling:
        header['decks'][0]['filling'] = dict(id=NONE, version=1)
    HullBlueprintInput.parse(header)
    if len(source['decks']) > MAX_DECKS:
        fail('draft_limit', '$.decks', '甲板超过 64 层')
    ids, levels = set(), set()
    for di, deck in enumerate(source['decks']):
        path = f'$.decks[{di}]'
        exact(deck, {'id', 'level', 'is_base', 'structure_material', 'regions'} | ({'filling'} if with_filling else set()), path)
        if not isinstance(deck['regions'], list) or len(deck['regions']) > MAX_REGIONS:
            fail('draft_limit', path + '.regions', '区域必须为不超过 256 项的列表')
        probe = deepcopy(deck); probe['regions'] = [_WITNESS_REGION]
        DeckInput.parse(probe, path, with_filling=with_filling)
        if deck['id'] in ids or deck['level'] in levels:
            fail('duplicate_deck', path, '甲板标识及层级必须唯一')
        ids.add(deck['id']); levels.add(deck['level'])
        region_ids = set()
        for ri, region in enumerate(deck['regions']):
            rpath = f'{path}.regions[{ri}]'
            HullRegionInput.parse(region, rpath)
            if len(region['vertices_m']) > MAX_VERTICES:
                fail('draft_limit', rpath, '区域端点超过 4096 个')
            for p in region['vertices_m']:
                point(p)
            if region['id'] in region_ids:
                fail('duplicate_region', rpath, '区域标识必须唯一')
            region_ids.add(region['id'])


def blank_hull(resource_id, name):
    identity(resource_id)
    source = {'schema': 'gaotian.ship/v1alpha1', 'kind': 'HullBlueprint', 'id': resource_id,
        'name': name, 'version': 1, 'fixture_level': 'canonical_blueprint_fixture',
        'grid': {'cell_size_m': 5, 'deck_height_m': 5, 'forward_axis': '+Y', 'symmetry_axis': 'Y', 'cic_origin_cell': [0, 0]}, 'decks': []}
    validate_hull_draft(source)
    return source


def apply_hull_edit(source, command, args):
    """Apply one command or a bounded batch to a detached candidate, all or nothing."""
    result = deepcopy(source)
    if command == 'hull.batch':
        exact(args, {'commands'})
        commands = args['commands']
        if not isinstance(commands, list) or not 1 <= len(commands) <= 32:
            fail('invalid_arguments', '$.arguments.commands', '复合命令需要 1—32 项')
        for item in commands:
            exact(item, {'command', 'arguments'})
            if item['command'] == 'hull.batch':
                fail('invalid_arguments', '$.arguments.commands', '复合命令不可嵌套')
            _apply(result, item['command'], item['arguments'])
            validate_hull_draft(result)
    else:
        _apply(result, command, args)
        validate_hull_draft(result)
    return result


def _apply(source, command, args):
    if not isinstance(command, str):
        fail('invalid_arguments', '$.command', '命令必须为字符串')
    fields = {
        'hull.add_deck': {'deck_id', 'level', 'material'},
        'hull.remove_deck': {'deck_id'}, 'hull.set_base_deck': {'deck_id'},
        'hull.add_region': {'deck_id', 'region'}, 'hull.remove_region': {'deck_id', 'region_id'},
        'hull.replace_region': {'deck_id', 'region'},
        'hull.move_vertex': {'deck_id', 'region_id', 'vertex_index', 'point_m'},
        'hull.insert_vertex': {'deck_id', 'region_id', 'edge_index', 'point_m'},
        'hull.remove_vertex': {'deck_id', 'region_id', 'vertex_index', 'merged_armor'},
        'hull.mirror_region': {'deck_id', 'source_region_id', 'target_region_id'},
        'hull.set_edge_armor': {'deck_id', 'region_id', 'edge_index', 'material', 'thickness_m'},
        'hull.set_structure_material': {'deck_id', 'material'}, 'hull.rename': {'name'},
        'hull.set_filling': {'deck_id', 'configuration'},
    }
    if command not in fields:
        fail('command_not_supported', '$.command', '未知船壳命令')
    exact(args, fields[command])
    if command == 'hull.rename':
        source['name'] = args['name']; return
    if command == 'hull.add_deck':
        deck_id = identity(args['deck_id']); integer(args['level'], '$.arguments.level')
        source['decks'].append({'id': deck_id, 'level': args['level'], 'is_base': not source['decks'],
            'structure_material': deepcopy(args['material']), 'regions': [],
            **({'filling': dict(id=NONE, version=1)} if source['schema'] == HULL_FILLING_SCHEMA else {})})
        return
    deck = find(source['decks'], args['deck_id'])
    if command == 'hull.set_filling':
        configuration(args['configuration'])
        source['schema'] = HULL_FILLING_SCHEMA
        for row in source['decks']:
            row.setdefault('filling', dict(id=NONE, version=1))
        deck['filling'] = deepcopy(args['configuration'])
        return
    if command == 'hull.remove_deck':
        source['decks'].remove(deck); return
    if command == 'hull.set_base_deck':
        for d in source['decks']: d['is_base'] = d is deck
        return
    if command == 'hull.set_structure_material':
        deck['structure_material'] = deepcopy(args['material']); return
    if command in ('hull.add_region', 'hull.replace_region'):
        HullRegionInput.parse(args['region'], '$.arguments.region')
        for p in args['region']['vertices_m']:
            point(p, require_grid=True)
        if command == 'hull.replace_region':
            original = find(deck['regions'], args['region']['id'])
            deck['regions'][deck['regions'].index(original)] = deepcopy(args['region'])
        else:
            deck['regions'].append(deepcopy(args['region']))
        return
    if command == 'hull.mirror_region':
        original = find(deck['regions'], args['source_region_id'])
        target_id = identity(args['target_region_id'])
        if target_id == original['id']:
            fail('mirror_target', '$.arguments.target_region_id', '镜像目标必须不同于来源')
        mirrored = deepcopy(original); mirrored['id'] = target_id
        mirrored['vertices_m'] = [point([-x, y], require_grid=True) for x, y in original['vertices_m']]
        target = next((r for r in deck['regions'] if r['id'] == target_id), None)
        if target is None: deck['regions'].append(mirrored)
        else: deck['regions'][deck['regions'].index(target)] = mirrored
        return
    region = find(deck['regions'], args['region_id'])
    if command == 'hull.remove_region':
        deck['regions'].remove(region); return
    key = 'vertex_index' if 'vertex_index' in args else 'edge_index'
    index = integer(args[key], '$.arguments.' + key)
    if index >= len(region['vertices_m']):
        fail('index_missing', '$.arguments.' + key, '端点或边不存在')
    if command == 'hull.move_vertex':
        region['vertices_m'][index] = point(args['point_m'], require_grid=True)
    elif command == 'hull.insert_vertex':
        region['vertices_m'].insert(index + 1, point(args['point_m'], require_grid=True))
        region['edge_armor'].insert(index + 1, deepcopy(region['edge_armor'][index]))
    elif command == 'hull.remove_vertex':
        if len(region['vertices_m']) <= 3:
            fail('minimum_vertices', '$.arguments.vertex_index', '闭合区域至少保留三个端点；可删除整个区域')
        # Replacing the incoming edge before removal handles vertex zero and wrap-around.
        region['edge_armor'][(index - 1) % len(region['vertices_m'])] = deepcopy(args['merged_armor'])
        region['vertices_m'].pop(index); region['edge_armor'].pop(index)
    elif command == 'hull.set_edge_armor':
        region['edge_armor'][index] = {'material': deepcopy(args['material']), 'thickness_m': args['thickness_m']}
