"""E3b worker-owned experiment service. Polls consume views, never drive time."""
from copy import deepcopy
from dataclasses import asdict
import json
from math import hypot
from pathlib import Path
from time import monotonic_ns

from 高天荒野舰艇数据契约 import canonical_sha256
from .simplified_flight import build_sample_session
from .tactical_scheduler import TacticalScheduler, ScheduledControl, require, count
from .tactical import render_static, RENDER_INTERFACE
from .tactical_scenario import build_two_ship_scenario, SCENARIO_ID

CAPABILITIES = tuple('tactical.realtime.'+s for s in ('create', 'read', 'resume', 'pause', 'control', 'close'))
INTERFACE = 'gaotian.realtime-view/e3b-v1alpha1'
VIEW_PERIOD_NS = 66_666_667
LEASE_NS = 2_000_000_000
MAX_RESPONSE_BYTES = 256*1024


class RealtimeViewService:
    def __init__(self, instance_id, root=None, *, clock=monotonic_ns):
        self.instance_id = instance_id
        self.root = Path(root) if root else Path(__file__).resolve().parents[2]
        self.clock = clock
        self.scheduler = None
        self.latest = self.geometry = self.digest = None
        self.last_publish = self.last_read = 0
        self.error = None
        self.last_closed = None

    @property
    def running(self):
        return self.scheduler is not None and self.scheduler.status.running

    def pause(self, reason='disconnected'):
        if self.scheduler is not None:
            try:
                self.scheduler.pause(reason)
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
            if self.clock()-self.last_publish >= VIEW_PERIOD_NS:
                self.publish()
        except Exception:
            # Authority steps may already have committed. Never replay them to
            # repair a failed display; keep the last valid view and expose status.
            self.error = '试航已暂停，请重新读取状态；已完成的模拟步不会重放。'
            self.pause()

    def publish(self):
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
                actual=s.engine.actual_output_percent) for s in direct.propulsion.engines], error=self.error)
        self._size(result)
        # Domain records contain immutable tuples. The bridge deliberately accepts
        # only JSON arrays/objects; conversion occurs here, never in fixed steps.
        return json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False))

    def dispatch(self, request, *, mode):
        require(request.get('session_id') is None and request.get('expected_revision') is None, 'Realtime uses scene scope')
        method, p = request['method'], request['params']
        if method == 'tactical.realtime.create':
            require(set(p) == {'scenario_id'} and p['scenario_id'] == SCENARIO_ID, 'Unknown realtime fixture')
            require(mode == 'tactical', 'Enter tactical mode first')
            # One fixed experiment per backend. A lost create response can be
            # retried to discover the same scene, never resetting its authority.
            if self.scheduler is not None:
                return self.read()
            session = build_sample_session(self.root, with_command=True)
            geometry = render_static(build_two_ship_scenario(self.root))
            require(tuple(s['derived_snapshot_sha256'] for s in geometry['ships']) ==
                tuple(s.contributions.snapshot_sha256 for s in session._seeds), 'View/authority design mismatch')
            self.scheduler = TacticalScheduler(session, clock=self.clock)
            self.geometry, self.digest = geometry, canonical_sha256(geometry)
            self.error = None
            self.last_read = self.clock()
            try:
                self.publish()
                return self.read()
            except Exception:
                self.scheduler = self.latest = self.geometry = self.digest = None
                raise
        require(method in CAPABILITIES, 'Unknown realtime method')
        fields = {'scene_id', 'known_static_sha256', 'ack_inputs', 'ack_events'} if method == 'tactical.realtime.read' else \
            {'scene_id', 'input'} if method == 'tactical.realtime.control' else {'scene_id'}
        require(set(p) == fields, 'Unknown or missing realtime fields')
        if method == 'tactical.realtime.close' and self.scheduler is None and p['scene_id'] == self.last_closed and self.last_closed is not None:
            return dict(closed=True)
        require(self.scheduler is not None and p['scene_id'] == self.scheduler.world.epoch, 'Realtime scene identity mismatch')
        q = self.scheduler
        if method == 'tactical.realtime.close':
            q.pause('mode_exit')
            self.last_closed = p['scene_id']
            self.scheduler = self.latest = self.geometry = self.digest = None
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
        if method == 'tactical.realtime.resume':
            require(not self.error, 'Read the committed scene after a display failure')
            self.last_read = self.clock()
            q.resume()
        elif method == 'tactical.realtime.pause':
            q.pause()
        elif method == 'tactical.realtime.control':
            value = p['input']
            # The transport supplies a current target from a read snapshot. No
            # retargeting/retry of a stale request, including after a lost reply.
            receipt = q.submit(value)
            return asdict(receipt)
        self.publish()
        return self.read(self.digest)
