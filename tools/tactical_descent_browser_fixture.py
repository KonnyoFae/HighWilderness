"""Isolated 2d verification: 2x wall clock, passive enemy, real tank destruction.

At the fifth fixed step the domain fixture destroys all ships' lift tanks. All
following progress, repair, resource accounting, layer changes and settlement run
through the production owner. No test command is added to the product bridge.
"""
import argparse
from pathlib import Path
import sys
from time import monotonic_ns
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.tactical_scheduler import DomainBatch
from backend.high_wilderness_sidecar.tactical_devices import DeviceOperation


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settlement-dir',required=True,type=Path)
    args=parser.parse_args()
    original=RealtimeViewService._attach
    def attach(self,battle,geometry,key=None):
        battle.enemy_fire=False
        self.clock=lambda:monotonic_ns()*2
        result=original(self,battle,geometry,key)
        def domains(world):
            if world.fixed_step!=4:return DomainBatch()
            operations=[]
            for n,ship in enumerate(world.ships):
                for mid,_ in battle.session._command_kernels[n].lift:
                    module=ship.devices.modules[battle._indices[n][mid]]
                    operations.append(DeviceOperation(world.epoch,ship.ship_id,mid,module.sequence+1,
                        'damage',module.durability_points,5,'closing'))
            return DomainBatch(device_operations=tuple(operations))
        self.scheduler._domains=domains
        return result
    RealtimeViewService._attach=attach
    server=SidecarServer('backend.e3bbrowser',settlement_dir=args.settlement_dir,recovery_dir=args.settlement_dir/'editor')
    raise SystemExit(server.serve(sys.stdin.buffer,sys.stdout.buffer))
