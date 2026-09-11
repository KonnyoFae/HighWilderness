"""E3b worker-owned experiment service. Polls consume views, never drive time."""
from copy import deepcopy
from dataclasses import asdict
import json
from math import hypot
from pathlib import Path
from time import monotonic_ns
from uuid import uuid4
from . import tactical_settlement as settlement

from 高天荒野舰艇数据契约 import canonical_sha256
from .simplified_flight import build_sample_session
from .tactical_scheduler import TacticalScheduler, ScheduledControl, require, count
from .tactical import render_static, RENDER_INTERFACE
from .tactical_scenario import build_two_ship_scenario, SCENARIO_ID
from .tactical_gunnery import GunneryBattle, prepare_trial_session

CAPABILITIES = tuple('tactical.realtime.'+s for s in ('create', 'read', 'resume', 'pause', 'control', 'gun', 'damage_control', 'withdraw', 'close', 'settlements', 'settlement', 'save', 'deploy', 'deploy_prepared', 'prepared_entry'))
INTERFACE = 'gaotian.realtime-view/e3b-v1alpha1'
VIEW_PERIOD_NS = 66_666_667
LEASE_NS = 2_000_000_000
MAX_RESPONSE_BYTES = 256*1024


class RealtimeViewService:
    def __init__(self, instance_id, root=None, *, clock=monotonic_ns, settlement_dir=None):
        self.instance_id = instance_id
        self.root = Path(root) if root else Path(__file__).resolve().parents[2]
        self.clock = clock
        self.scheduler = None
        self.gunnery = None
        self.latest = self.geometry = self.digest = None
        self.last_publish = self.last_read = 0
        self.error = None
        self.last_closed = None
        self.store = settlement.SettlementStore(settlement_dir or self.root/'.local/tactical')
        self._result = None
        self._result_saved = False
        self._save_error = None
        self._deployment_key = None
        self.preparation_store = None
        self._prepared_lease = None

    def deploy_prepared(self,p):
        from . import prepared_deployment as deployment, prepared_launch_store as launches, battle_preparation as bp
        ps=settlement.ps
        ps.obj(p,'preparation_id launch_id direct_instance_id','$.params')
        for key in p:ps.identifier(p[key],'$.'+key)
        digest=canonical_sha256(p);key=('prepared',p['launch_id'],digest)
        if self.scheduler is not None and self._deployment_key==key:return self.read()
        require(self.scheduler is None or self.gunnery.ending is not None and self._result_saved,'请先结束并保存当前交战，再进入准备舰船交战')
        store=self.preparation_store
        require(store is not None,'准备仓库未接线')
        if self._prepared_lease is not None:self._prepared_lease.close();self._prepared_lease=None
        launches.recover(store)
        lease=launches.BattleLease(store.directory)
        require(lease.file is not None,'准备舰船正由另一个后台使用')
        claimed=False;scene=None
        try:
            with store.connection() as db:
                launches.setup(db)
                old=db.execute('SELECT status FROM prepared_launches WHERE id=?',(p['launch_id'],)).fetchone()
                require(old is None,'该次入战已结束或中断，请从准备页重新发起')
                raw=db.execute('SELECT payload,digest FROM preparations WHERE id=?',(p['preparation_id'],)).fetchone()
                require(raw is not None,'请先保存战前准备')
                receipt=store._decode(*raw);ships=[]
                for row in receipt['ships']:
                    record=row['after'];identity=record['state']['instance_id']
                    archive=db.execute('SELECT payload,digest FROM preparation_designs WHERE id=?',(identity,)).fetchone()
                    require(archive is not None,'已保存的设计绑定缺失')
                    design=bp.restore_design(store._decode(*archive),store.index)
                    ships.append((design,bp.validate_record(record,design)))
            template,scenario,_=self._template()
            battle,geometry=deployment.build(ships,p['direct_instance_id'],template,scenario)
            scene=battle.session.world.epoch
            launches.claim(store,p['launch_id'],digest,scene,[r for _,r in ships]);claimed=True
            result=self._attach(battle,geometry,key)
            self._prepared_lease=lease
            return result
        except BaseException:
            try:
                if claimed:launches.rollback_failed_attach(store,p['launch_id'],scene)
            finally:lease.close()
            raise

    @property
    def running(self):
        return self.scheduler is not None and self.scheduler.status.running

    def pause(self, reason='disconnected'):
        if self.scheduler is not None:
            try:
                self.scheduler.pause(reason)
                if self.gunnery is not None:
                    self.gunnery.suspend()
                self.publish()
            except Exception:
                self.error = '试航已暂停，显示状态需要重新读取。'

    def tick(self):
        if self.scheduler is None:
            return
        try:
            if self.running and self.clock()-self.last_read > LEASE_NS:
                self.pause('disconnected')
            self.scheduler.pump()
            if not self.running and self.gunnery is not None:
                self.gunnery.suspend()
            if self.gunnery.ending is not None and self._result is None or self.clock()-self.last_publish >= VIEW_PERIOD_NS:
                self.publish()
        except Exception:
            # Authority steps may already have committed. Never replay them to
            # repair a failed display; keep the last valid view and expose status.
            self.error = '试航已暂停，请重新读取状态；已完成的模拟步不会重放。'
            self.pause()

    def _prepare_result(self):
        if self.gunnery.ending is None or self._result is not None:
            return
        self._result = settlement.capture(self.gunnery)
        try:
            self.store.stage(self._result)
        except settlement.ps.ContractError as exc:
            self._save_error = exc.message

    def _template(self):
        session = build_sample_session(self.root, with_command=True)
        scenario = build_two_ship_scenario(self.root)
        geometry = render_static(scenario)
        require(tuple(s['derived_snapshot_sha256'] for s in geometry['ships']) ==
            tuple(s.contributions.snapshot_sha256 for s in session._seeds), 'View/authority design mismatch')
        config = json.loads((self.root/'contracts/web_bridge/fixtures/p2a-gunnery.json').read_text(encoding='utf-8'))
        session = prepare_trial_session(session, config)
        battle = GunneryBattle(session, scenario, config, damage_enabled=True, instance_prefix='instance.p3.'+uuid4().hex+'.')
        return battle, scenario, geometry

    def _attach(self, battle, geometry, key=None):
        geometry = deepcopy(geometry)
        if battle.damage is not None:
            for ship, durability in zip(geometry['ships'], battle.damage.structural_durability):
                ship['structural_durability'] = dict(policy_id=durability.policy_id, maximum_points=durability.maximum_points)
        scheduler = TacticalScheduler(battle.session, clock=self.clock, stepper=battle.step, stop_when=lambda: battle.ending is not None)
        digest = canonical_sha256(geometry)
        previous = dict(self.__dict__)
        try:
            self.gunnery, self.scheduler = battle, scheduler
            self.geometry, self.digest = geometry, digest
            self.error = self._result = self._save_error = None
            self._result_saved, self._deployment_key = False, key
            self.last_read = self.clock()
            self.publish()
            return self.read()
        except Exception:
            self.__dict__.update(previous)
            raise

    def publish(self):
        self._prepare_result()
        q = self.scheduler
        world = q.world
        ships = []
        for seed, ship in zip(q._session._seeds, world.ships):
            m = ship.motion
            ships.append(dict(id=ship.ship_id, position_m=m.position_world_m.to_list(), heading_rad=m.heading_rad,
                velocity_mps=m.velocity_world_mps.to_list(), speed_mps=hypot(m.velocity_world_mps.x, m.velocity_world_mps.y),
                yaw_rate_radps=m.yaw_rate_radps, height_layer=m.height_layer, hull_integrity=m.hull_integrity_fraction,
                physical_status=ship.command.lifecycle.physical_status, command_status=ship.command.lifecycle.command_status,
                modules=[dict(id=d.instance_id, durability=v.durability_points) for d,v in zip(seed.devices.modules,ship.devices.modules)]))
        view = dict(interface=RENDER_INTERFACE, backend_instance_id=self.instance_id, scene_id=world.epoch,
            authority_interface='gaotian.simplified-flight-experiment/v1alpha1', paused=not q.status.running,
            fixed_step=world.fixed_step, fixed_step_s=1/60, time_s=world.fixed_step/60,
            static_sha256=self.digest, static=None, ships=ships, events=[])
        if self.gunnery is not None:
            view['gunnery'] = self.gunnery.view()
        self._size(view)
        self.latest, self.last_publish = view, self.clock()

    @staticmethod
    def _size(value):
        require(len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')) <= MAX_RESPONSE_BYTES,
            'Realtime response exceeds byte budget')

    def read(self, known=None):
        q = self.scheduler
        status = q.status
        view = deepcopy(self.latest)
        if known != self.digest:
            view['static'] = deepcopy(self.geometry)
        receipts = [asdict(q.query(status.epoch, seq)) for seq in q._records]
        events = [asdict(e) for e in q.read_events(status.epoch, after_sequence=status.acknowledged_event_sequence, limit=16)]
        direct = next(s for s in q.world.ships if s.ship_id == q._session._direct)
        result = dict(interface=INTERFACE, status=asdict(status), view=view, receipts=receipts, events=events,
            direct_ship_id=direct.ship_id, available=direct.authority_allowed, loss_reason=direct.command.loss_reason,
            engines=[dict(id=s.engine.actuator_instance_id, phase=s.engine.phase, target=s.engine.target_output_percent,
                actual=s.engine.actual_output_percent) for s in direct.propulsion.engines], error=self.error,
            settlement=None if self._result is None else dict(result=self._result, saved=self._result_saved, error=self._save_error))
        self._size(result)
        # Domain records contain immutable tuples. The bridge deliberately accepts
        # only JSON arrays/objects; conversion occurs here, never in fixed steps.
        return json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False))

    def dispatch(self, request, *, mode):
        require(request.get('session_id') is None and request.get('expected_revision') is None, 'Realtime uses scene scope')
        method, p = request['method'], request['params']
        if method == 'tactical.realtime.deploy_prepared':
            require(mode=='tactical','请先进入战术视角')
            return self.deploy_prepared(p)
        if method == 'tactical.realtime.prepared_entry':
            require(set(p)=={'launch_id'},'需要入战请求身份')
            settlement.ps.identifier(p['launch_id'],'$.launch_id')
            if self.scheduler is not None and self._deployment_key and self._deployment_key[0]=='prepared' and self._deployment_key[1]==p['launch_id']:
                return dict(scene=self.read())
            return dict(scene=None)
        if method == 'tactical.realtime.create':
            require(set(p) == {'scenario_id'} and p['scenario_id'] == SCENARIO_ID, 'Unknown realtime fixture')
            require(mode == 'tactical', 'Enter tactical mode first')
            # One fixed experiment per backend. A lost create response can be
            # retried to discover the same scene, never resetting its authority.
            if self.scheduler is not None:
                return self.read()
            battle, scenario, geometry = self._template()
            return self._attach(battle, geometry)
        require(method in CAPABILITIES, 'Unknown realtime method')
        if method in ('tactical.realtime.settlements', 'tactical.realtime.settlement', 'tactical.realtime.save', 'tactical.realtime.deploy'):
            require(mode == 'tactical', 'Enter tactical mode first')
            if method == 'tactical.realtime.settlements':
                require(not p, 'Unexpected library fields')
                result = self.store.list(); self._size(result)
                return result
            if method in ('tactical.realtime.settlement', 'tactical.realtime.save'):
                require(set(p) == {'settlement_id'}, 'Expected settlement identity')
                identity = p['settlement_id']
                if method == 'tactical.realtime.save':
                    if self._result is not None and self._result['settlement_id'] == identity:
                        self.store.stage(self._result)  # retry after initial staging failure
                    result = self.store.save(identity)
                    if self._result is not None and self._result['settlement_id'] == identity:
                        self._result_saved, self._save_error = True, None
                        self.gunnery.ending['saved'] = True
                        self.publish()
                else:
                    result = self.store.read(identity)
                self._size(result)
                return result
            require(set(p) == {'instance_id', 'revision', 'launch_id'}, 'Expected deployment identity')
            settlement.ps.identifier(p['launch_id'], '$.launch_id')
            settlement.ps.identifier(p['instance_id'], '$.instance_id')
            settlement.ps.integer(p['revision'], '$.revision', 1)
            key = (p['instance_id'], p['revision'], p['launch_id'])
            if self.scheduler is not None and self._deployment_key == key:
                return self.read()
            require(self.scheduler is None or self.gunnery.ending is not None and self._result_saved,
                    '请先结束并保存当前交战，再进入下一场')
            record = self.store.load_ship(p['instance_id'], p['revision'])
            template, scenario, geometry = self._template()
            battle = settlement.redeploy(record, template, scenario)
            return self._attach(battle, geometry, key)
        fields = {'scene_id', 'known_static_sha256', 'ack_inputs', 'ack_events'} if method == 'tactical.realtime.read' else \
            {'scene_id', 'input'} if method in ('tactical.realtime.control', 'tactical.realtime.gun', 'tactical.realtime.damage_control') else {'scene_id'}
        require(set(p) == fields, 'Unknown or missing realtime fields')
        if method == 'tactical.realtime.close' and self.scheduler is None and p['scene_id'] == self.last_closed and self.last_closed is not None:
            return dict(closed=True)
        require(self.scheduler is not None and p['scene_id'] == self.scheduler.world.epoch, 'Realtime scene identity mismatch')
        q = self.scheduler
        if method == 'tactical.realtime.close':
            require(self._prepared_lease is None or self._result_saved,'请先结束交战并保存结果，再返回战前准备')
            require(self.gunnery.ending is None or self._result_saved, '请先保存战后结算；失败时可重试，重启后也可从结算列表恢复')
            q.pause('mode_exit')
            self.last_closed = p['scene_id']
            self.scheduler = self.gunnery = self.latest = self.geometry = self.digest = None
            if self._prepared_lease is not None:self._prepared_lease.close();self._prepared_lease=None
            return dict(closed=True)
        if method == 'tactical.realtime.read':
            require(type(p['ack_inputs']) is list and len(p['ack_inputs']) <= 256 and all(count(s,1) for s in p['ack_inputs']), 'Invalid receipt acknowledgements')
            # Validate the entire acknowledgement batch before releasing records.
            require(all(q.query(p['scene_id'],s).status not in ('accepted','not_received') for s in p['ack_inputs']), 'Nonterminal acknowledgement')
            q.acknowledge_events(p['scene_id'], p['ack_events'])
            for s in p['ack_inputs']: q.acknowledge_input(p['scene_id'],s)
            self.last_read = self.clock()
            if self.error:
                self.publish()  # rebuild display from committed authority, no step
                self.error = None
            return self.read(p['known_static_sha256'])
        require(mode == 'tactical' or method == 'tactical.realtime.pause', 'Enter tactical mode first')
        if method == 'tactical.realtime.withdraw':
            self.gunnery.withdraw()
            q.pause('battle_finished')
        elif method == 'tactical.realtime.resume':
            require(self.gunnery.ending is None, 'Battle has ended; create a new scene')
            require(not self.error, 'Read the committed scene after a display failure')
            self.last_read = self.clock()
            q.resume()
        elif method == 'tactical.realtime.pause':
            q.pause()
            self.gunnery.suspend()
        elif method == 'tactical.realtime.gun':
            value = p['input']
            require(type(value) is dict, 'Invalid gun input')
            # An exact already-committed retry may be queried after a pause; new
            # commands must use the current running input generation.
            retry = value == self.gunnery._last
            require(retry or q.status.running and value.get('generation') == q.status.generation and
                next(s for s in q.world.ships if s.ship_id == q._session._direct).authority_allowed,
                'Gun command is paused, obsolete or lacks direct control')
            self.gunnery.submit(value)
        elif method == 'tactical.realtime.damage_control':
            value = p['input']
            require(type(value) is dict, 'Invalid damage-control input')
            retry = value == self.gunnery.fire.player_last
            require(retry or q.status.running and value.get('generation') == q.status.generation and
                    next(s for s in q.world.ships if s.ship_id == q._session._direct).authority_allowed,
                    '损管命令需要运行中的当前场景及旗舰控制权')
            self.gunnery.fire.submit_player(value)
        elif method == 'tactical.realtime.control':
            value = p['input']
            # The transport supplies a current target from a read snapshot. No
            # retargeting/retry of a stale request, including after a lost reply.
            receipt = q.submit(value)
            return asdict(receipt)
        self.publish()
        return self.read(self.digest)
