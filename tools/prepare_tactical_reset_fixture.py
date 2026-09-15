"""Create multiple real pending results in an explicitly isolated test store."""
import argparse
from pathlib import Path
from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
from backend.high_wilderness_sidecar.tactical_scenario import SCENARIO_ID


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    out = Path(args.out).resolve()
    if (out/'store'/'settlements.sqlite3').exists():
        raise RuntimeError('Use a fresh isolated fixture directory')
    out.mkdir(parents=True, exist_ok=True)
    for index in range(3):
        live = RealtimeViewService(f'backend.resetfixture{index}', settlement_dir=out/'store')
        live.dispatch(dict(method='tactical.realtime.create', params=dict(scenario_id=SCENARIO_ID)), mode='tactical')
        live.gunnery.withdraw(); live.publish()
    print('Created 3 independent pending battle results in the isolated fixture store.')


if __name__ == '__main__': main()
