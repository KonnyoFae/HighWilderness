"""Single selection point for newly imported designs; archives keep their policy."""
import json
from pathlib import Path

CURRENT_PATH = Path('contracts/web_bridge/fixtures/h5d-preparation-policy.v8.json')


def load_current(root):
    return json.loads((root / CURRENT_PATH).read_text(encoding='utf-8'))
