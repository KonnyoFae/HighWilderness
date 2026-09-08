"""Structural proof for in-process immutable resource graphs; no game imports."""
from dataclasses import fields, is_dataclass
from decimal import Decimal
from functools import lru_cache


@lru_cache(maxsize=256)
def _frozen_fields(cls):
    return tuple(field.name for field in fields(cls)) if is_dataclass(cls) and cls.__dataclass_params__.frozen else None


def require_deeply_immutable(value, *, _verified=None):
    """Reject mutable containers/classes, including nested frozen-dataclass fields.

    This concerns ordinary application writes, not hostile object.__setattr__ calls.
    Retained references are safe only after the entire graph has passed this check.
    """
    seen = set()

    def visit(item, path):
        if item is None or isinstance(item, (str, int, float, bool, bytes, Decimal)):
            return
        if _verified is not None and _verified.get(id(item)) is item:
            return
        if id(item) in seen:
            return
        seen.add(id(item))
        if isinstance(item, (tuple, frozenset)):
            for index, child in enumerate(item):
                visit(child, f"{path}[{index}]")
        else:
            names = _frozen_fields(type(item))
            if names is None:
                raise TypeError(f"Mutable or unsupported resource at {path}: {type(item).__name__}")
            for name in names:
                visit(getattr(item, name), f"{path}.{name}")
        if _verified is not None:
            _verified[id(item)] = item

    visit(value, "$")
