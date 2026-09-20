"""F5 fixtures around the production wall-clock scheduler and stdio server.

Only this test child installs observers and incoming-shell fixtures. No clock,
simulation rate, ammunition replenishment or overload limit is patched.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch

from backend.high_wilderness_sidecar import server
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from tools.realtime_longrun_probe import ObservedView, MeasuredQueue
from tools.profile_tactical_fire_control import ROOT, build, stats


class CombatView(ObservedView):
    case = 'saved'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.steps = []; self.publications = []; self.reads = []
        self.fire_work = Counter(); self.prediction_work = Counter()
        self.work_max = Counter(); self.peak_projectiles = self.waves = 0
        self.fixture = None

    def _template(self):
        template = super()._template()
        if self.case == 'saved':
            fixture = json.loads((ROOT/'artifacts/tactical-fire-control-f1f2-20260920/fixture.json').read_text(encoding='utf-8'))
            b, geometry = build(fixture, ResourceIndex(ROOT), SimpleNamespace(_template=lambda: template),
                                speed=100, stock='saved', distance=16000)
        else:
            from tools.test_tactical_interception import PointDefenseTests
            from backend.high_wilderness_sidecar import prepared_deployment
            from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
            constructor=RealtimeViewService.__init__
            def isolated(view,*args,**kwargs):
                kwargs['settlement_dir']=self.fixture_directory
                constructor(view,*args,**kwargs)
            with patch.object(RealtimeViewService,'__init__',isolated):
                PointDefenseTests.setUpClass()
            self.fixture = PointDefenseTests()
            captured = []; original = prepared_deployment.build
            def capture(*args, **kwargs):
                result = original(*args, **kwargs); captured.append(result[1]); return result
            with patch.object(prepared_deployment, 'build', capture):
                b = self.fixture.battle()
            geometry = captured[-1]
        return b, template[1], geometry

    def _attach(self, battle, geometry, key=None):
        result = super()._attach(battle, geometry, key)
        original = self.scheduler._stepper
        def measured(*args, **kwargs):
            # Repeated declared 12-shell volleys, actual 75 mm physics at 900 m/s.
            # 5-second spacing preserves finite 2,000-round CIWS ready stock.
            if self.fixture and battle.session.world.fixed_step % 300 == 0:
                for n in range(12):
                    self.fixture.incoming(battle, distance=1000+n*100, speed=900)
                self.waves += 1
            start = perf_counter(); value = original(*args, **kwargs)
            self.steps.append(perf_counter()-start)
            self.peak_projectiles = max(self.peak_projectiles, len(battle.projectiles))
            self.fire_work.update({k:v for k,v in battle.fire_control_metrics.items() if k != 'step'})
            self.prediction_work.update(battle.point_defense.prediction_metrics)
            for k,v in battle.fire_control_metrics.items():
                if k != 'step': self.work_max[k] = max(self.work_max[k], v)
            return value
        self.scheduler._stepper = measured
        return result

    def publish(self):
        start = perf_counter(); result = super().publish()
        self.publications.append(perf_counter()-start); return result

    def read(self, known=None):
        start = perf_counter(); result = super().read(known)
        self.reads.append(perf_counter()-start); return result

    def summary(self):
        b = self.gunnery
        return dict(case=self.case, step=stats(self.steps), publish=stats(self.publications), read=stats(self.reads),
            measured_steps=len(self.steps), peak_projectiles=self.peak_projectiles, incoming_waves=self.waves,
            shots=sum(s.shots for s in b.states), intercepted=b.point_defense.kills,
            ending=b.ending, fire_work=self.fire_work, fire_work_max=self.work_max, prediction_work=self.prediction_work,
            inventory=[i._value for i in b.inventory.inventories])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--case',choices=('saved','ciws'),required=True)
    args=parser.parse_args()
    server.Queue=MeasuredQueue; server.RealtimeViewService=CombatView; CombatView.case=args.case
    CombatView.fixture_directory=args.out/'fixture-store'
    with (args.out/'probe.jsonl').open('x',encoding='utf-8') as stream:
        CombatView.output=stream
        instance=server.SidecarServer('backend.e3clongrun',recovery_dir=args.out/'recovery',settlement_dir=args.out/'store')
        try:
            return instance.serve(sys.stdin.buffer,sys.stdout.buffer)
        finally:
            if instance.realtime.gunnery is not None:
                (args.out/'runtime.json').write_text(json.dumps(instance.realtime.summary(),ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__': raise SystemExit(main())
