"""Bounded, step-local proofs for immutable internal objects.

Public parsers remain strict. Proofs never cross a step/session, never use an
object ID without a strong reference, and are discarded even on failed commits.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from 高天荒野舰艇只读资源验证 import require_deeply_immutable

_active = ContextVar("internal_step_proofs", default=None)


class StepProofs:
    def __init__(self):
        self.records = {}
        self.previews = {}
        self.immutable_nodes = {}
        self.hits = 0
        self.misses = 0


@contextmanager
def internal_step_proof_scope():
    proofs = StepProofs()
    token = _active.set(proofs)
    try:
        yield proofs
    finally:
        _active.reset(token)


def validate_internal_record(value, record_type, path="$"):
    """Parse once; unchanged immutable object gets the same validated result.

    Returning the parsed value preserves normalization in existing strict calls.
    No proof is installed until both parsing and immutable-graph checks succeed.
    """
    proofs = _active.get()
    if proofs is None:
        return record_type.parse(value.to_dict(), path)
    key = (record_type, id(value))
    cached = proofs.records.get(key)
    if cached is not None and cached[0] is value:
        proofs.hits += 1
        return cached[1]
    parsed = record_type.parse(value.to_dict(), path)
    require_deeply_immutable(value, _verified=proofs.immutable_nodes)
    require_deeply_immutable(parsed, _verified=proofs.immutable_nodes)
    proofs.records[key] = (value, parsed)
    proofs.records[(record_type, id(parsed))] = (parsed, parsed)
    proofs.misses += 1
    return parsed


def register_time_preview(preview, capability):
    """Called only at the end of the shared preview producer, never by parse."""
    proofs = _active.get()
    if proofs is not None:
        require_deeply_immutable((preview, capability), _verified=proofs.immutable_nodes)
        proofs.previews[id(preview)] = (preview, capability)
    return preview


def register_internal_result(value):
    """Producer-only: a typed result built after its constituent/chain checks.

    Do not call from external parse or arbitrary constructors. An equal clone does
    not inherit the proof; consumers fall back to strict parsing for such inputs.
    """
    proofs = _active.get()
    if proofs is not None:
        require_deeply_immutable(value, _verified=proofs.immutable_nodes)
        proofs.records[(type(value), id(value))] = (value, value)
    return value


def has_generated_time_preview(preview, capability):
    proofs = _active.get()
    cached = None if proofs is None else proofs.previews.get(id(preview))
    return cached is not None and cached[0] is preview and cached[1] is capability
