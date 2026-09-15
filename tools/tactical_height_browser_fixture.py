"""Isolated UI verification host: double-speed clock and a passive test enemy.

Uses the real preparation, scheduler, ship model, orders, damage and settlement.
Only the external clock and enemy automatic target selection differ from the app.
No test capabilities are added to the production protocol.
"""
import argparse
from pathlib import Path
import sys
from time import monotonic_ns
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settlement-dir',required=True,type=Path)
    args=parser.parse_args()
    original=RealtimeViewService._attach
    def attach(self,battle,geometry,key=None):
        battle.enemy_fire=False
        self.clock=lambda:monotonic_ns()*2
        return original(self,battle,geometry,key)
    RealtimeViewService._attach=attach
    server=SidecarServer('backend.e3bbrowser',settlement_dir=args.settlement_dir,recovery_dir=args.settlement_dir/'editor')
    raise SystemExit(server.serve(sys.stdin.buffer,sys.stdout.buffer))
