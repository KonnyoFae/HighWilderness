"""Isolated OP1 saved custom ship plus an unrelated legacy testing ship."""
import argparse
from pathlib import Path

from tools.prepare_ignition_ui_fixture import prepare
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar import tactical_settlement as st


def prepare_return(directory):
    prepare(directory)
    battle, _, _ = RealtimeViewService('backend.op1.fixture')._template()
    battle.withdraw()
    result = st.capture(battle)
    store = st.SettlementStore(directory / 'store')
    store.stage(result)
    store.save(result['settlement_id'])
    assert store.list()['ships'][0]['can_deploy']


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    prepare_return(parser.parse_args().out)
