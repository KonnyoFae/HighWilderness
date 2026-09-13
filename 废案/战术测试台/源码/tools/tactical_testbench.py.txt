"""Local test-workspace preparation; never uses the player's default store."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / '.local' / 'testbench'
SCENARIOS = {
    'roundtrip': ('完整交战与返回', '完好舰船，测试准备、交战、保存、重启和再次入战。'),
    'gunnery': ('火炮与弹药', '普通弹、穿甲弹和燃烧弹均可选择，核对炮向、命中及换弹成本。'),
    'damage-control': ('防火与损管', '防火填充舰，预设货舱受损和一处火情，用于观察灭火与维修。'),
    'fuel': ('燃料与货舱', '灵烷填充舰，预装独立储油槽，核对零推进油耗和战后余量。'),
}


def run_path(root: Path, run_id: str) -> Path:
    if not re.fullmatch(r'run-[0-9]{8}-[0-9]{6}-[a-f0-9]{8}', run_id):
        raise ValueError('无效的测试记录编号')
    path = root / run_id
    if path.resolve().parent != root.resolve():
        raise ValueError('测试记录不能指向独立目录以外')
    return path


def read_run(root: Path, run_id: str) -> dict:
    path = run_path(root, run_id)
    value = json.loads((path / 'run.json').read_text(encoding='utf-8'))
    if value.get('id') != run_id or value.get('scenario') not in SCENARIOS:
        raise ValueError('测试记录不匹配')
    if not (path / 'store' / 'settlements.sqlite3').is_file():
        raise ValueError('测试存档缺失，不能用空存档继续')
    return value


def list_runs(root: Path) -> list[dict]:
    result = []
    for path in sorted(root.glob('run-*'), reverse=True):
        try:
            result.append(read_run(root, path.name))
        except (ValueError, OSError):
            continue
    return result[:50]


def create_run(root: Path, scenario: str) -> dict:
    if scenario not in SCENARIOS:
        raise ValueError('未知测试场景')
    from backend.high_wilderness_sidecar import battle_preparation as bp, outfit_documents
    from backend.high_wilderness_sidecar.server import SidecarServer
    from tools.test_battle_preparation import fixture
    from tools.test_deck_filling import filled

    now = datetime.now(timezone.utc)
    run_id = 'run-' + now.strftime('%Y%m%d-%H%M%S-') + uuid4().hex[:8]
    path = run_path(root, run_id)
    path.mkdir(parents=True, exist_ok=False)
    server = SidecarServer('backend.testbench.setup', recovery_dir=path/'recovery', settlement_dir=path/'store')
    document, deployment, _ = fixture(server.editor.index)
    if scenario in ('damage-control', 'fuel'):
        hull = filled(document['hull_binding']['hull'], 'fireproof' if scenario == 'damage-control' else 'spirit_fuel')
        document = dict(document, hull_binding=outfit_documents.bind(hull, server.editor.index))
    document['outfit']['name'] = SCENARIOS[scenario][0] + ' · 测试舰'
    policy = server.preparation.policy
    design = bp.compile_design(document, server.editor.index, deployment, policy, ship_id='ship.testbench.player')
    instance_id = 'instance.testbench.player'
    server.preparation.store.create_ship(design, instance_id)
    server.preparation.provision()
    # Explicit initial test stock, not a preparation transaction or a claimed combat result.
    with server.preparation.store.connection() as db:
        record = server.preparation.store._decode(*db.execute('SELECT payload,digest FROM ships WHERE id=?', (instance_id,)).fetchone())
        state = record['state']
        for magazine in state['magazines']:
            magazine['quantity'] = 50
        state['cargo'] = [dict(good_id=key, quantity=5) for key in
                          ('cargo.special_alloy', 'cargo.high_energy_fuel', 'cargo.engineering_parts')]
        for device in state.get('damage_controls', []):
            device['quantity_units'] = 100000
        if scenario == 'damage-control':
            state['hull_integrity_fraction'] = .99
            for module in state['modules']:
                if module['module_id'] == 'custom.cargo':
                    module['durability_points'] = 50
            state['fires'] = [dict(module_id='custom.cargo', intensity_units=1000, remaining_steps=3600)]
        if scenario == 'fuel':
            for tank in state['fuel_tanks']:
                if tank['tank_id'].startswith('tank.filling.'):
                    tank['quantity_units'] = 120
            state['fuel_units'] = sum(t['quantity_units'] for t in state['fuel_tanks'])
        record = bp.validate_record(record, design)
        server.preparation.store._write_ship(db, record)
    (path/'outfit.json').write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding='utf-8')
    (path/'entry.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    value = dict(id=run_id, scenario=scenario, name=SCENARIOS[scenario][0], created_at=now.isoformat(),
                 description=SCENARIOS[scenario][1], directory=str(path.resolve()),
                 fixture_note='初始弹药、货物、损管资源及指定伤势为测试预设；进入游戏后使用真实规则与有限库存。')
    # Publish only after successful compilation and persistence.
    (path/'run.json').write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    return value


def add_note(root: Path, run_id: str, note: str) -> dict:
    read_run(root, run_id)
    if not note.strip() or len(note) > 8000:
        raise ValueError('请填写 1—8000 字的问题说明')
    value = dict(time=datetime.now(timezone.utc).isoformat(), note=note.strip())
    with (run_path(root, run_id)/'notes.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + '\n')
    return dict(saved=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['list', 'create', 'show', 'note'])
    parser.add_argument('--scenario', choices=SCENARIOS)
    parser.add_argument('--run-id')
    args = parser.parse_args()
    if args.action == 'list':
        result = list_runs(RUNS)
    elif args.action == 'create':
        result = create_run(RUNS, args.scenario)
    elif args.action == 'show':
        result = read_run(RUNS, args.run_id or '')
    else:
        import sys
        result = add_note(RUNS, args.run_id or '', sys.stdin.read(32001))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
