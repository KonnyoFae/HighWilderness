from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from backend.high_wilderness_sidecar import simplified_flight as sf
from backend.high_wilderness_sidecar.tactical_scheduler import (
    TacticalScheduler, ScheduledControl, SchedulerLimits, DomainBatch, QUANTA,
)
from backend.high_wilderness_sidecar.tactical_devices import DeviceOperation
from backend.high_wilderness_sidecar import tactical_checkpoint as checkpoint
from tools.test_simplified_flight import command
from 高天荒野舰艇数据契约 import ContractError


class Clock:
    def __init__(self): self.now = 0
    def __call__(self): return self.now
    def advance(self, ns): self.now += ns


class SchedulerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample = sf.build_sample_session(Path(__file__).resolve().parents[1], with_command=True)

    def session(self):
        return sf.SimplifiedFlightSession(self.sample._seeds, self.sample._profile, direct_ship_id=self.sample._direct)

    def scheduler(self, **kwargs):
        clock = Clock()
        return TacticalScheduler(self.session(), clock=clock, **kwargs), clock

    def request(self, q, *, step=None, control=None, sequence=None):
        status = q.status
        return ScheduledControl(status.epoch, status.generation,
            status.highest_input_sequence+1 if sequence is None else sequence, self.sample._direct,
            status.fixed_step if step is None else step, command() if control is None else control)

    def tick(self, q, clock, steps=1):
        clock.advance((steps*QUANTA+59)//60)
        return q.pump()

    def drain(self, q):
        status = q.status
        events = q.read_events(status.epoch, after_sequence=status.acknowledged_event_sequence)
        if events: q.acknowledge_events(status.epoch, events[-1].sequence)
        return events

    def test_exact_sixty_steps_and_paused_wall_time_excluded(self):
        q, clock = self.scheduler(); clock.advance(10*QUANTA)
        self.assertEqual(q.pump(), 0)
        q.resume()
        for n in range(1, 1001):
            clock.advance(1_000_000); q.pump()
        self.assertEqual(q.world.fixed_step, 60)
        self.assertEqual(q.status.debt_quanta, 0)
        q.pause(); clock.advance(100*QUANTA); q.resume()
        self.assertEqual(q.pump(), 0)
        self.assertEqual(self.tick(q, clock), 1)

    def test_jitter_and_batching_match_direct_fixed_step_world(self):
        q, clock = self.scheduler(); oracle = self.session(); q.resume()
        future = {0:command(), 25:command('translation.reverse'), 48:command('yaw.clockwise', yaw=100),
            80:sf.directional_control()}
        events = []
        for _ in range(150):
            n = q.world.fixed_step
            # Poll pattern has no skipped input boundary: enqueue ahead within
            # the published six-step window, with one accepted request per step.
            for step in sorted(tuple(future)):
                if n <= step <= n+6:
                    q.submit(self.request(q, step=step, control=future.pop(step)))
            before = q.world.fixed_step
            clock.advance((7, 21, 11, 40, 3)[_ % 5]*1_000_000)
            q.pump()
            for step in range(before, q.world.fixed_step):
                choices = {0:command(), 25:command('translation.reverse'), 48:command('yaw.clockwise', yaw=100),
                    80:sf.directional_control()}
                result = oracle.step(choices.get(step))
                if result.events: events.append(result)
            self.assertEqual(q.world.ships, oracle.world.ships)
            self.assertEqual(tuple(e.result for e in self.drain(q)), tuple(events)); events.clear()
        self.assertTrue(q.status.running)

    def test_future_request_receipts_duplicate_conflict_and_no_repeat_execution(self):
        q, clock = self.scheduler(); q.resume()
        req = self.request(q, step=2)
        self.assertEqual(q.submit(req).status, 'accepted')
        self.assertEqual(q.submit(req).status, 'accepted')
        self.tick(q, clock, 2)
        self.assertEqual(q.query(req.epoch, 1).status, 'accepted')
        self.tick(q, clock)
        receipt = q.submit(req)
        self.assertEqual((receipt.status, receipt.resolved_at_step), ('executed', 3))
        with self.assertRaises(ContractError): q.submit(replace(req, control=sf.directional_control()))
        q.acknowledge_input(req.epoch, 1)
        self.assertEqual(q.query(req.epoch, 1).status, 'expired')
        with self.assertRaises(ContractError): q.submit(req)
        self.assertEqual(q.query(req.epoch, 2).status, 'not_received')

    def test_invalid_requests_do_not_consume_sequence_or_mutate_world(self):
        q, clock = self.scheduler(); q.resume(); req = self.request(q)
        for bad in (replace(req, epoch='foreign'), replace(req, generation=9), replace(req, sequence=2),
            replace(req, ship_id='ship.web.red'), replace(req, target_step=7), replace(req, target_step=True), replace(req, control=None)):
            with self.assertRaises(ContractError): q.submit(bad)
            self.assertEqual(q.status.highest_input_sequence, 0)
        value = req.to_dict(); value['extra'] = True
        with self.assertRaises(ContractError): q.submit(value)
        self.assertEqual(q.submit(req).status, 'accepted')
        with self.assertRaises(ContractError): q.submit(self.request(q))

    def test_pause_disconnect_and_mode_exit_cancel_and_neutralize_old_controls(self):
        for reason in ('manual', 'disconnected', 'mode_exit'):
            q, clock = self.scheduler(); q.resume(); q.submit(self.request(q)); self.tick(q, clock)
            req = self.request(q, step=q.world.fixed_step+3)
            q.submit(req); before = q.world
            q.pause(reason)
            self.assertIs(q.world, before)
            self.assertEqual(q.query(req.epoch, req.sequence).receipt.reason, reason)
            clock.advance(10*QUANTA); q.resume()
            with self.assertRaises(ContractError): q.submit(replace(req, sequence=req.sequence+1))
            self.tick(q, clock)
            self.assertEqual(q.world.ships[0].control, sf.directional_control())
            self.assertEqual(q.submit(req).status, 'cancelled')
            q.submit(self.request(q)); self.tick(q, clock)
            self.assertEqual(q.world.ships[0].control, command())

    def test_backlog_is_retained_and_explicit_paused_recovery_is_bounded(self):
        q, clock = self.scheduler(); q.resume()
        req = self.request(q, step=4); q.submit(req)
        clock.advance(300_000_000)
        self.assertEqual(q.pump(), 0)
        self.assertEqual(q.status.pause_reason, 'overload')
        self.assertEqual(q.status.debt_quanta, 18*QUANTA)
        self.assertEqual(q.query(req.epoch, 1).status, 'cancelled')
        with self.assertRaises(ContractError): q.resume()
        clock.advance(100*QUANTA)
        self.assertEqual(q.recover_debt(), 4)
        self.assertEqual(q.status.debt_quanta, 14*QUANTA)
        q.resume()
        counts = [q.pump() for _ in range(4)]
        self.assertEqual(counts, [4, 4, 4, 2])
        self.assertEqual((q.world.fixed_step, q.status.debt_quanta), (18, 0))

    def test_step_batch_and_compute_budget_limit_without_skipping_steps(self):
        clock = Clock()
        q = TacticalScheduler(self.session(), clock=clock, project=lambda *_: clock.advance(5_000_000))
        q.resume(); clock.advance(100_000_000)
        self.assertEqual(q.pump(), 2)
        self.assertEqual(q.world.fixed_step, 2)
        self.assertEqual(q.status.debt_quanta, 60*110_000_000-2*QUANTA)

    def test_queue_receipt_capacity_and_acknowledgement_are_bounded(self):
        q, clock = self.scheduler(limits=SchedulerLimits(pending_inputs=1, retained_receipts=1))
        q.resume(); req = self.request(q); q.submit(req)
        with self.assertRaises(ContractError): q.submit(self.request(q, step=1))
        with self.assertRaises(ContractError): q.acknowledge_input(req.epoch, 1)
        self.tick(q, clock)
        with self.assertRaises(ContractError): q.submit(self.request(q))
        q.acknowledge_input(req.epoch, 1); q.submit(self.request(q))
        self.assertEqual((q.status.pending_inputs, q.status.retained_receipts), (1, 1))

    def test_reliable_backpressure_pauses_before_commit_and_does_not_drop_events(self):
        q, clock = self.scheduler(limits=SchedulerLimits(reliable_events=1))
        q.resume(); q.submit(self.request(q)); self.tick(q, clock, 2)
        self.assertEqual(q.status.reliable_events, 1)
        before = q.world
        req = self.request(q); q.submit(req)
        self.tick(q, clock)
        self.assertIs(q.world, before)
        self.assertEqual(q.status.pause_reason, 'output_backpressure')
        self.assertEqual(q.query(req.epoch, req.sequence).status, 'cancelled')
        with self.assertRaises(ContractError): q.resume()
        with self.assertRaises(ContractError): q.acknowledge_events(q.world.epoch, 1)
        with self.assertRaises(ContractError): q.read_events(q.world.epoch, after_sequence=1)
        first = q.read_events(q.world.epoch, after_sequence=0)
        self.assertEqual(first, q.read_events(q.world.epoch, after_sequence=0))
        q.acknowledge_events(q.world.epoch, first[-1].sequence)
        with self.assertRaises(ContractError): q.read_events(q.world.epoch, after_sequence=0)
        q.resume(); q.pump()
        self.assertEqual(q.world.fixed_step, before.fixed_step+1)

    def test_step_failure_cancels_future_and_keeps_failed_request_queryable(self):
        def fail(*_): raise RuntimeError('projection fixture')
        q, clock = self.scheduler(project=fail); q.resume()
        first = self.request(q); q.submit(first)
        second = self.request(q, step=1); q.submit(second)
        before = q.world
        with self.assertRaises(RuntimeError): self.tick(q, clock)
        self.assertIs(q.world, before)
        self.assertEqual(q.query(first.epoch, 1).status, 'failed')
        self.assertEqual(q.query(first.epoch, 2).status, 'cancelled')
        self.assertEqual(q.status.reliable_events, 0)
        self.assertEqual(q.status.debt_quanta//QUANTA, 1)

    def test_actual_cic_loss_cancels_future_inputs_without_stopping_falling_physics(self):
        hp = next(m.maximum_durability_points for m in self.sample._seeds[0].devices.modules if m.instance_id == 'cic')
        def domain(world):
            return DomainBatch(device_operations=(DeviceOperation(world.epoch, self.sample._direct,
                'cic', 1, 'damage', hp, 2, 'closing'),)) if world.fixed_step == 1 else DomainBatch()
        q, clock = self.scheduler(domain_inputs=domain); q.resume()
        q.submit(self.request(q)); queued = self.request(q, step=3); q.submit(queued)
        self.tick(q, clock, 2)
        self.assertEqual(q.query(queued.epoch, 2).receipt.reason, 'authority_lost')
        self.assertEqual(q.world.ships[0].command.lifecycle.physical_status, 'falling')
        self.assertTrue(q.status.running)
        with self.assertRaises(ContractError): q.submit(self.request(q))
        self.tick(q, clock)
        self.assertEqual(q.world.fixed_step, 3)

    def test_clock_rollback_is_latched_and_no_step_commits(self):
        q, clock = self.scheduler(); q.resume(); self.tick(q, clock)
        before = q.world; clock.now -= 1
        with self.assertRaises(ContractError): q.pump()
        self.assertIs(q.world, before)
        self.assertEqual(q.status.pause_reason, 'clock_error')
        clock.now += QUANTA
        q.pause('manual')
        with self.assertRaises(ContractError): q.resume()

    def test_owner_reentrancy_and_external_session_mutation_are_rejected(self):
        clock = Clock(); session = self.session()
        q = TacticalScheduler(session, clock=clock, project=lambda *_: q.pause())
        q.resume(); before = q.world
        with self.assertRaises(ContractError): self.tick(q, clock)
        self.assertIs(q.world, before)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.assertRaises(ContractError): pool.submit(q.pump).result()
        session.step()
        with self.assertRaises(ContractError): q.pump()

    def test_loaded_scene_uses_new_identity_and_starts_paused_without_old_input_queue(self):
        s = self.session(); s.step(command())
        loaded = checkpoint.loads(checkpoint.dumps(s), s._seeds, s._profile, direct_ship_id=s._direct)
        clock = Clock(); q = TacticalScheduler(loaded, clock=clock)
        self.assertFalse(q.status.running)
        q.resume()
        with self.assertRaises(ContractError): q.submit(replace(self.request(q), epoch=s.world.epoch))
        self.tick(q, clock)
        self.assertEqual(q.world.ships[0].control, sf.directional_control())

    def test_stable_pumps_do_not_hash_or_reallocate(self):
        q, clock = self.scheduler(); q.resume(); q.submit(self.request(q))
        for _ in range(150): self.tick(q, clock); self.drain(q)
        with patch.object(sf, 'canonical_sha256', side_effect=AssertionError('hot fingerprint')), \
             patch('backend.high_wilderness_sidecar.tactical_resources_runtime._allocate_power', side_effect=AssertionError('hot resource')):
            for _ in range(60): self.tick(q, clock)


if __name__ == '__main__': unittest.main()
