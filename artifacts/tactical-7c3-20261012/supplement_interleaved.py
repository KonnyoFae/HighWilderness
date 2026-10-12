"""Supplementary drift check; independent-run timings remain the primary record."""
from hashlib import sha256
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools import profile_tactical_7c3 as current
from tools.tactical_7b_fixture import write


if __name__ == '__main__':
    source = ROOT / 'artifacts/tactical-7b-20261009/mixed-9v9'
    out = Path(__file__).resolve().parent / 'interleaved-mixed-9v9'
    if out.exists():
        raise ValueError('Choose a fresh output directory')
    current.validate_reference()
    out.mkdir()
    provenance = current.fingerprint()
    provenance.update(reference_head=current.REFERENCE_HEAD, reference_methods_ast_verified=True,
        runner_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
        tools_7c={p.relative_to(ROOT).as_posix(): sha256(p.read_bytes()).hexdigest() for p in (ROOT/'tools').glob('*7c*.py')},
        scope='Supplement only: alternates the first runner each step. Two live battles share physics caches; do not pool with independent AB/BA/AB measurements.')
    write(out/'source.json', provenance)
    with patch.object(current.baseline, 'implementation', current.implementation):
        current.baseline.interleaved(source, out)
