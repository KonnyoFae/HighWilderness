"""Generate the current fixed-lifetime flight catalog from the published 5g base."""
from pathlib import Path
import json
from backend.high_wilderness_sidecar.missile_flight_catalog import normalize

ROOT = Path(__file__).resolve().parents[1]


def build():
    directory = ROOT / 'contracts/web_bridge/fixtures'
    value = normalize(json.loads((directory / 'tactical-missile-flight.5g.json').read_text(encoding='utf-8')))
    value['description'] = '5j 固定无动力寿命及在途换层：火箭 30 秒、涡喷 45 秒、拦截弹 10 秒；最低上爬总速度 50 米/秒，常规/拦截爬降角初值 30/45 度。'
    (directory / 'tactical-missile-flight.5j.json').write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    build()
