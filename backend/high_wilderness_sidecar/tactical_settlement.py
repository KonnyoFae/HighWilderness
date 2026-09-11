"""P3 versioned combat instances and atomic local all-ship settlement store.

Only ending/export/entry paths parse these records. No storage runs in a fixed
step. Pending results are durable before confirmation; commits compare revisions.
"""
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import sqlite3
from uuid import uuid4

from . import persistent_ship as ps, tactical_gunnery as tg
from .tactical_damage import DamageState

SHIP_INTERFACE = 'gaotian.persistent-combat-ship/p3-v1'
RESULT_INTERFACE = 'gaotian.battle-settlement/p3-v1'


def armor_record(battle, index, values):
    return [dict(deck_id=e.key[0], deck_level=e.key[1], region_id=e.key[2], edge_index=e.key[3], durability=value)
            for e, value in zip(battle.damage.edges[index], values)]


def combat_record(battle, index, state, armor):
    binding = battle.inventory.prepared.bindings[index]
    return dict(interface=SHIP_INTERFACE, ship_id=battle.session.world.ships[index].ship_id,
        design_sha256=binding.resources.seed.contributions.snapshot_sha256,
        resources=binding.resources.definition(), state=state,
        armor=armor_record(battle, index, armor))


def parse_record(record, template, index):
    """Strict boundary: exact resource/design and every stable armor edge."""
    v = ps.clone(record)
    ps.obj(v, 'interface ship_id design_sha256 resources state armor', '$.ship')
    seed = template.session._seeds[index]
    ps.need(v['interface'] == SHIP_INTERFACE and v['ship_id'] == seed.contributions.ship_id
        and v['design_sha256'] == seed.contributions.snapshot_sha256, '$.ship', '存档设计或舰船身份不匹配')
    pack = ps.compile_resources(seed, v['resources'])
    reference = template.inventory.prepared.bindings[index].resources.definition()
    ps.need(all(pack.definition()[k] == reference[k] for k in ('weapons', 'recipes', 'projectiles', 'fire_control')),
            '$.resources', '当前战术入口仅支持已接通的普通炮资源版本')
    instance = ps.parse_instance(v['state'], pack)
    edges = template.damage.edges[index]
    ps.need(type(v['armor']) is list and len(v['armor']) == len(edges), '$.armor', '装甲边记录缺失')
    by_key = {}
    for row in v['armor']:
        ps.obj(row, 'deck_id deck_level region_id edge_index durability', '$.armor')
        ps.identifier(row['deck_id'], '$.deck_id'); ps.identifier(row['region_id'], '$.region_id')
        ps.integer(row['deck_level'], '$.deck_level'); ps.integer(row['edge_index'], '$.edge_index')
        key = row['deck_id'], row['deck_level'], row['region_id'], row['edge_index']
        ps.need(key not in by_key, '$.armor', '重复装甲边')
        by_key[key] = row['durability']
    ps.need(set(by_key) == {e.key for e in edges}, '$.armor', '装甲边身份与设计不一致')
    armor = tuple(ps.number(by_key[e.key], '$.armor.durability', maximum=e.maximum) for e in edges)
    ps.need(all(w['reload'] is None for w in v['state']['weapons']), '$.reload', '战后存档不得含未结算装填')
    from .damage_control_resources import require_settled
    require_settled(v['state'])
    return ps.InstanceBinding(pack, instance), armor


def capture(battle):
    battle._guard()
    ps.need(battle.ending is not None and battle.damage is not None, '$.ending', '结束交战后才能结算')
    ps.need(not battle.projectiles and all(i._settlement is not None and not i._due for i in battle.inventory.inventories),
            '$.ending', '战斗活动过程尚未冻结')
    rows = []
    for n, (ship, binding, inv) in enumerate(zip(battle.session.world.ships, battle.inventory.prepared.bindings, battle.inventory.inventories)):
        seed = binding.resources.seed
        before = binding.instance.to_dict()
        value = ps.clone(before)
        value.update(hull_integrity_fraction=ship.motion.hull_integrity_fraction, fuel_units=ship.motion.fuel_units,
            modules=[dict(module_id=m.instance_id, durability_points=h.durability_points, operating_mode=mode)
                for m, h, mode in zip(seed.devices.modules, ship.devices.modules, ship.resources.modes)],
            crew=[dict(crew_type=k, count=count) for k, count in ship.resources.crew],
            power_policy=ship.resources.policy.to_dict(),
            engine_latches=[e.instance_id for e, latched in zip(seed.contributions.engines, ship.resources.latched) if latched])
        lifecycle = ship.command.lifecycle
        reasons = set(lifecycle.failure_causes)
        if ship.command.loss_reason: reasons.add(ship.command.loss_reason)
        if lifecycle.exit_reason: reasons.add(lifecycle.exit_reason)
        status = 'destroyed' if value['hull_integrity_fraction'] <= 0 else 'withdrawn' if lifecycle.physical_status == 'exited' else 'disabled' if reasons else 'available'
        value['service'] = dict(status=status, reasons=sorted(reasons))
        if inv._fuel_tanks:
            value['fuel_tanks']=ps.clone(inv._value['fuel_tanks'])
        value = inv.snapshot(ps.parse_instance(value, binding.resources)).to_dict()
        value['revision'] += 1
        value = ps.parse_instance(value, binding.resources).to_dict()
        initial_armor = getattr(battle, 'entry_armor', battle.damage.initial.armor)[n]
        rows.append(dict(before=combat_record(battle, n, before, initial_armor),
            after=combat_record(battle, n, value, battle.damage_state.armor[n]),
            capacity_before=ps.inventory_summary(binding.instance, binding.resources),
            capacity_after=ps.inventory_summary(ps.parse_instance(value, binding.resources), binding.resources),
            changes=inv.changes(), module_names={m.id: m.prototype.name for m in seed.resources.modules}))
    return ps.clone(dict(interface=RESULT_INTERFACE, settlement_id='settlement.'+battle.session.world.epoch,
        scene_id=battle.session.world.epoch, reason=battle.ending['reason'], fixed_step=battle.ending['step'],
        removed_projectiles=battle.ending['removed_projectiles'], ships=rows))


def redeploy(record, template, scenario):
    """P3 two-ship deployment: retained blue instance and a fresh enemy.

    Pose, helm, actuator phase/governors, target, lock and shells are scene facts.
    Durability, inventory, cooldown and resource fault latches are retained.
    """
    binding, armor = parse_record(record, template, 0)
    v = binding.instance.to_dict()
    ps.need(v['service']['status'] == 'available', '$.service', '这艘舰船已失去出航能力，需后续维修或救援')
    seed = binding.resources.seed
    ps.need(v['fuel_units'] == seed.motion.fuel_units, '$.fuel', '当前部署模型不支持变化后的燃料质量')
    modules = {m['module_id']: m for m in v['modules']}
    loaded = replace(seed, motion=replace(seed.motion, hull_integrity_fraction=v['hull_integrity_fraction'], fuel_units=v['fuel_units']),
        devices=replace(seed.devices, initial_durability_points=tuple(modules[m.instance_id]['durability_points'] for m in seed.devices.modules)),
        resources=replace(seed.resources, modes=tuple(modules[m.id]['operating_mode'] for m in seed.resources.modules),
            crew=tuple((c['crew_type'], c['count']) for c in v['crew']), policy=ps.RuntimePowerPolicyInput.parse(v['power_policy'], '$.power_policy')),
        command=replace(seed.command, wounded_aboard=v['wounded_aboard']))
    session = ps.sf.SimplifiedFlightSession((loaded, template.session._seeds[1]), template.session._profile,
        direct_ship_id=seed.contributions.ship_id, initial_resource_latches=((seed.contributions.ship_id, tuple(v['engine_latches'])),))
    ps.need(session.world.ships[0].authority_allowed, '$.service', '当前状态无法重新部署；原有战损已保留')
    enemy = template.inventory.prepared.bindings[1]
    battle = tg.GunneryBattle(session, scenario, template.config, damage_enabled=True,
        instance_bindings=(binding, enemy))
    battle.damage_state = DamageState((armor, battle.damage.initial.armor[1]))
    battle.entry_armor = battle.damage_state.armor
    return battle


def validate_result(value):
    v = ps.clone(value)
    ps.obj(v, 'interface settlement_id scene_id reason fixed_step removed_projectiles ships', '$.settlement')
    ps.need(v['interface'] == RESULT_INTERFACE, '$.interface', '不支持的结算版本')
    ps.identifier(v['settlement_id'], '$.settlement_id'); ps.identifier(v['scene_id'], '$.scene_id')
    ps.need(v['settlement_id'] == 'settlement.'+v['scene_id'], '$.settlement_id', '结算身份不匹配')
    ps.need(v['reason'] in ('withdrawal', 'victory', 'defeat', 'draw'), '$.reason', '非法结束原因')
    ps.integer(v['fixed_step'], '$.fixed_step'); ps.integer(v['removed_projectiles'], '$.removed_projectiles')
    ps.need(type(v['ships']) is list and 1 <= len(v['ships']) <= 16, '$.ships', '非法结算舰船列表')
    ids = set()
    for row in v['ships']:
        ps.obj(row, 'before after capacity_before capacity_after changes module_names', '$.ships')
        a, b = row['before']['state'], row['after']['state']
        ps.identifier(a['instance_id'], '$.instance_id')
        ps.integer(a['revision'], '$.revision'); ps.integer(b['revision'], '$.revision')
        ps.need(a['instance_id'] == b['instance_id'] and a['instance_id'] not in ids and b['revision'] == a['revision']+1
            and a['resources_sha256'] == b['resources_sha256'], '$.ships', '重复舰船或非法版本变动')
        ids.add(a['instance_id'])
        for record in (row['before'], row['after']):
            ps.obj(record, 'interface ship_id design_sha256 resources state armor', '$.ship')
            ps.need(record['interface'] == SHIP_INTERFACE, '$.ship.interface', '不支持的舰船实例版本')
        ps.need(all(row['before'][k] == row['after'][k] for k in ('ship_id', 'design_sha256', 'resources')), '$.ship', '结算不得更换舰船设计或资源定义')
        start, end = tg.ti.InventorySession._totals(a), tg.ti.InventorySession._totals(b)
        changes = {}
        ps.need(type(row['changes']) is list, '$.changes', '资源变动摘要缺失')
        for change in row['changes']:
            ps.obj(change, 'resource reason delta', '$.changes')
            ps.need(type(change['resource']) is str and change['reason'] in tg.ti.REASONS, '$.changes', '不支持的资源变动')
            amount = (ps.number if change['resource'].startswith('fuel:') else ps.integer)(change['delta'], '$.delta', -ps.MAX_INT)
            changes[change['resource']] = changes.get(change['resource'], 0)+amount
        ps.need(all(end.get(k, 0)-start.get(k, 0) == changes.get(k, 0) for k in set(start)|set(end)|set(changes)),
                '$.changes', '战前、战后资源与变动摘要无法对账')
    return v


class SettlementStore:
    """SQLite rollback journal: pending result and all-ship commit are separate
    durable transactions. Exact retry is a read; stale revision never overwrites.
    Connections are operation-local because construction and work use different threads.
    """
    def __init__(self, directory):
        self.directory = Path(directory)

    @contextmanager
    def connection(self):
        conn = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.directory/'settlements.sqlite3', timeout=5, isolation_level=None)
            conn.execute('PRAGMA synchronous=FULL')
            version = conn.execute('PRAGMA user_version').fetchone()[0]
            ps.need(version in (0, 1), '$.store', '存档仓库版本不受支持')
            conn.execute('CREATE TABLE IF NOT EXISTS results (id TEXT PRIMARY KEY, payload TEXT NOT NULL, digest TEXT NOT NULL, committed INTEGER NOT NULL DEFAULT 0)')
            conn.execute('CREATE TABLE IF NOT EXISTS ships (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS battle_instance_claims (instance_id TEXT PRIMARY KEY, scene_id TEXT NOT NULL)')
            conn.execute('PRAGMA user_version=1')
            conn.execute('BEGIN IMMEDIATE')
            yield conn
            conn.commit()
        except (OSError, sqlite3.Error) as exc:
            if conn is not None: conn.rollback()
            raise ps.ContractError('settlement.storage', '$.store', '战后保存失败，结果已保留，请重试：'+str(exc)) from exc
        except BaseException:
            if conn is not None: conn.rollback()
            raise
        finally:
            if conn is not None: conn.close()

    @staticmethod
    def _encoded(value):
        payload = ps.encode(value)
        ps.decode(payload)  # byte bound and exact JSON domain
        return payload, sha256(payload.encode('utf-8')).hexdigest()

    @staticmethod
    def _decode(payload, digest):
        ps.need(sha256(payload.encode('utf-8')).hexdigest() == digest, '$.store', '存档内容校验失败，未载入旧状态')
        return ps.decode(payload)

    def stage(self, result):
        result = validate_result(result)
        payload, digest = self._encoded(result)
        with self.connection() as db:
            old = db.execute('SELECT digest FROM results WHERE id=?', (result['settlement_id'],)).fetchone()
            ps.need(old is None or old[0] == digest, '$.settlement_id', '同一结算身份对应了不同结果')
            if old is None:
                db.execute('INSERT INTO results(id,payload,digest) VALUES (?,?,?)', (result['settlement_id'], payload, digest))
        return result['settlement_id']

    def read(self, settlement_id):
        ps.identifier(settlement_id, '$.settlement_id')
        with self.connection() as db:
            row = db.execute('SELECT payload,digest,committed FROM results WHERE id=?', (settlement_id,)).fetchone()
            ps.need(row is not None, '$.settlement_id', '找不到结算记录')
            return dict(result=validate_result(self._decode(row[0], row[1])), saved=bool(row[2]))

    def _write_ship(self, db, record):
        payload, digest = self._encoded(record)
        state = record['state']
        db.execute('INSERT INTO ships VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload,digest=excluded.digest',
                   (state['instance_id'], state['revision'], payload, digest))

    def save(self, settlement_id):
        ps.identifier(settlement_id, '$.settlement_id')
        with self.connection() as db:
            row = db.execute('SELECT payload,digest,committed FROM results WHERE id=?', (settlement_id,)).fetchone()
            ps.need(row is not None, '$.settlement_id', '请先恢复或准备本场结算')
            result = validate_result(self._decode(row[0], row[1]))
            if not row[2]:
                for ship in result['ships']:
                    before = ship['before']['state']
                    claim = db.execute('SELECT scene_id FROM battle_instance_claims WHERE instance_id=?', (before['instance_id'],)).fetchone()
                    ps.need(claim is None or claim[0] == result['scene_id'], '$.scene_id', '舰船正被另一场战斗使用，不能覆盖')
                    old = db.execute('SELECT revision,payload,digest FROM ships WHERE id=?', (before['instance_id'],)).fetchone()
                    ps.need(old is None and before['revision'] == 0 or old is not None and old[0] == before['revision']
                        and self._decode(old[1], old[2]) == ship['before'], '$.revision', '舰船已有更新的结算；本场结果保留，不能覆盖其他版本')
                for ship in result['ships']:
                    self._write_ship(db, ship['after'])
                    db.execute('DELETE FROM battle_instance_claims WHERE instance_id=? AND scene_id=?',
                        (ship['before']['state']['instance_id'], result['scene_id']))
                db.execute('UPDATE results SET committed=1 WHERE id=?', (settlement_id,))
        return dict(result=result, saved=True)

    def load_ship(self, instance_id, revision):
        ps.identifier(instance_id, '$.instance_id'); ps.integer(revision, '$.revision', 1)
        with self.connection() as db:
            row = db.execute('SELECT revision,payload,digest FROM ships WHERE id=?', (instance_id,)).fetchone()
            ps.need(row is not None and row[0] == revision, '$.revision', '舰船记录已变化，请刷新列表')
            for payload, digest in db.execute('SELECT payload,digest FROM results WHERE committed=0'):
                result = self._decode(payload, digest)
                ps.need(all(s['before']['state']['instance_id'] != instance_id for s in result['ships']), '$.settlement', '这艘舰船还有未保存的战后结果，请先处理结算')
            return self._decode(row[1], row[2])

    def list(self):
        with self.connection() as db:
            results, ships, blocked = [], [], set()
            for key, payload, digest, committed in db.execute('SELECT id,payload,digest,committed FROM results ORDER BY rowid DESC'):
                r = self._decode(payload, digest)
                if not committed:
                    blocked.update(s['before']['state']['instance_id'] for s in r['ships'])
                if len(results) < 30 or not committed:
                    results.append(dict(settlement_id=key, saved=bool(committed), reason=r['reason'], fixed_step=r['fixed_step']))
            for payload, digest in db.execute("SELECT payload,digest FROM ships WHERE json_extract(payload, '$.ship_id')='ship.web.blue' ORDER BY rowid DESC LIMIT 100"):
                r = self._decode(payload, digest); s = r['state']
                if r['ship_id'] == 'ship.web.blue':
                    ships.append(dict(instance_id=s['instance_id'], revision=s['revision'], hull_integrity=s['hull_integrity_fraction'],
                        service=s['service'], can_deploy=s['service']['status'] == 'available' and s['instance_id'] not in blocked))
            return dict(results=results, ships=ships)
