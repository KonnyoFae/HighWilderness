"""Adaptive wall-time mapping, service accounting and unchanged control rules."""
from pathlib import Path
from dataclasses import replace
import unittest

from backend.high_wilderness_sidecar import simplified_flight as sf
from backend.high_wilderness_sidecar.tactical_scheduler import TacticalScheduler, ScheduledControl, SchedulerLimits, QUANTA
from backend.high_wilderness_sidecar.tactical_speed import SpeedPolicy
from tools import test_tactical_scheduler as existing
from 高天荒野舰艇数据契约 import ContractError


class SpeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.sample=sf.build_sample_session(Path(__file__).resolve().parents[1],with_command=True)

    def make(self, policy=SpeedPolicy(mode='adaptive'), cost_ns=0, **kwargs):
        clock=existing.Clock();cost=[cost_ns]
        session=sf.SimplifiedFlightSession(self.sample._seeds,self.sample._profile,direct_ship_id=self.sample._direct)
        q=TacticalScheduler(session,clock=clock,speed_policy=policy,project=lambda *_:clock.advance(cost[0]),**kwargs)
        return q,clock,cost

    def drain(self,q):
        events=q.read_events(q.world.epoch,after_sequence=q.status.acknowledged_event_sequence)
        if events:q.acknowledge_events(q.world.epoch,events[-1].sequence)

    def run_for(self,q,clock,ns,*,overhead=False):
        end=clock.now+ns;publish=read=clock.now;rows=[]
        while clock.now<end and q.status.running:
            start=clock.now;work=q._speed.step_work_ns;q.pump()
            if overhead and clock.now>=publish:clock.advance(4_000_000);publish=clock.now+66_666_667
            q.observe_work(start,work)
            if overhead and clock.now>=read:
                start=clock.now;work=q._speed.step_work_ns;clock.advance(8_000_000);q.observe_work(start,work);read=clock.now+100_000_000
            self.drain(q);rows.append((q.status.fixed_step,q.status.speed.effective,q.status.debt_quanta,q.status.generation))
            clock.advance(2_000_000)
        return rows

    def test_fixed_half_and_fractional_mapping_preserve_every_quantum(self):
        for rate in (.5,.333,1.):
            q,clock,_=self.make(SpeedPolicy(fixed_rate=rate));q.resume()
            self.run_for(q,clock,2*QUANTA);q.pump()
            self.assertEqual(q.world.fixed_step,int(120*rate))
            expected=clock.now*60*round(rate*1000)//1000-q.world.fixed_step*QUANTA
            self.assertEqual(q.status.debt_quanta,expected)
            self.assertEqual(q.status.speed.revision,0)

    def test_sustained_load_slows_without_debt_growth_or_input_generation_changes(self):
        q,clock,_=self.make(cost_ns=40_000_000);q.resume()
        rows=self.run_for(q,clock,10*QUANTA,overhead=True)
        self.assertTrue(q.status.running);self.assertGreater(q.world.fixed_step,60)
        self.assertTrue(.05<q.status.speed.effective<.4)
        self.assertLess(max(r[2] for r in rows),15*QUANTA)
        self.assertLess(q.status.debt_quanta,2*QUANTA)
        self.assertEqual({r[3] for r in rows},{0})
        self.assertGreater(q.status.speed.nonstep_utilization,.05)

    def test_publish_and_request_cost_reduce_rate_even_with_fast_steps(self):
        normal,clock,_=self.make(cost_ns=9_000_000);normal.resume();self.run_for(normal,clock,5*QUANTA)
        loaded,clock,_=self.make(cost_ns=9_000_000);loaded.resume()
        end=5*QUANTA
        while clock.now<end and loaded.status.running:
            loaded.pump();self.drain(loaded)
            start=clock.now;work=loaded._speed.step_work_ns;clock.advance(8_000_000);loaded.observe_work(start,work)
            clock.advance(2_000_000)
        self.assertTrue(loaded.status.running)
        self.assertLess(loaded.status.speed.effective,normal.status.speed.effective)
        self.assertGreater(loaded.status.speed.nonstep_utilization,.2)

    def test_short_spike_and_recovery_are_bounded_and_recovery_is_slow(self):
        q,clock,cost=self.make(cost_ns=5_000_000);q.resume();self.run_for(q,clock,2*QUANTA)
        before=q.world.fixed_step;cost[0]=80_000_000
        while q.world.fixed_step==before:clock.advance(2_000_000);q.pump()
        self.assertTrue(q.status.running);self.assertGreaterEqual(q.status.speed.effective,.5)
        cost[0]=40_000_000;self.run_for(q,clock,3*QUANTA);slow=q.status.speed.effective
        cost[0]=3_000_000;self.run_for(q,clock,QUANTA)
        self.assertLessEqual(q.status.speed.effective,slow+.061)
        self.run_for(q,clock,20*QUANTA)
        self.assertEqual(q.status.speed.effective,1.)

    def test_rate_change_retains_receipts_and_duplicate_executes_once(self):
        q,clock,_=self.make(cost_ns=50_000_000);q.resume()
        request=ScheduledControl(q.world.epoch,0,1,q._session._direct,4,existing.command())
        self.assertEqual(q.submit(request).status,'accepted')
        self.run_for(q,clock,2*QUANTA)
        receipt=q.query(q.world.epoch,1).receipt
        self.assertEqual((receipt.status,receipt.resolved_at_step),('executed',5))
        self.assertEqual(q.submit(request),receipt);self.assertEqual(q.status.generation,0)
        self.assertGreater(q.status.speed.revision,0)
        q.acknowledge_input(q.world.epoch,1)
        with self.assertRaises(ContractError):q.submit(request)

    def test_frequent_load_changes_preserve_all_accrued_time(self):
        q,clock,cost=self.make(cost_ns=5_000_000);q.resume();rows=[]
        for duration in (5,40,8,60,5,35,10,50)*2:
            cost[0]=duration*1_000_000
            rows.extend(self.run_for(q,clock,500_000_000,overhead=True))
        self.assertTrue(q.status.running)
        self.assertLess(max(r[2] for r in rows),15*QUANTA)
        self.assertEqual({r[3] for r in rows},{0})
        accrued=sum(rate*ns*60 for rate,ns in q._speed.rate_wall_ns.items())
        self.assertEqual(accrued,(q.world.fixed_step*QUANTA+q.status.debt_quanta)*1000+q._rate_remainder)
        self.assertEqual(sum(q._speed.rate_wall_ns.values()),q._speed.wall_ns)

    def test_pause_excludes_wall_time_and_retains_learned_rate_and_debt(self):
        q,clock,_=self.make(cost_ns=40_000_000);q.resume();self.run_for(q,clock,2*QUANTA)
        q.pause();before=q.status;clock.advance(100*QUANTA);q.resume()
        after=q.status
        self.assertEqual(after.debt_quanta,before.debt_quanta)
        self.assertEqual(after.speed.active_wall_s,before.speed.active_wall_s)
        self.assertEqual(after.speed.effective,before.speed.effective)
        self.assertEqual(after.generation,before.generation)

    def test_stall_clock_failure_and_output_pressure_still_pause(self):
        q,clock,_=self.make();q.resume();clock.advance(QUANTA);q.pump()
        self.assertEqual(q.status.pause_reason,'overload');self.assertEqual(q.status.debt_quanta,60*QUANTA)
        self.assertEqual(q.world.fixed_step,0)
        q,clock,_=self.make();q.resume();clock.advance(1);q.pump();clock.now-=1
        with self.assertRaises(ContractError):q.pump()
        self.assertEqual(q.status.pause_reason,'clock_error')
        q,clock,_=self.make(limits=SchedulerLimits(reliable_events=1));q.resume()
        q.submit(ScheduledControl(q.world.epoch,0,1,q._session._direct,0,existing.command()))
        clock.advance(40_000_000);q.pump()
        clock.advance(20_000_000);q.pump()
        self.assertEqual(q.status.pause_reason,'output_backpressure')

    def test_no_step_reentrancy_or_failed_commit_is_hidden_by_slowdown(self):
        q,clock,_=self.make();q.resume();world=q.world
        def fail(*args,**kwargs):raise RuntimeError('failed step')
        q._stepper=fail;clock.advance(20_000_000)
        with self.assertRaises(RuntimeError):q.pump()
        self.assertIs(q.world,world);self.assertEqual(q.status.pause_reason,'step_failed')

    def test_policy_rejects_invalid_rates_and_unbounded_settings(self):
        for kwargs in ({'mode':'skip'},{'fixed_rate':0},{'fixed_rate':1.1},{'fixed_rate':float('nan')},
            {'utilization':1},{'window_ns':0},{'minimum_rate':.0001},{'step_alpha':True}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):SpeedPolicy(**kwargs)


if __name__=='__main__':unittest.main()
