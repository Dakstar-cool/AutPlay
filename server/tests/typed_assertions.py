"""Explicit preconditions for fixture facts before exercising their mutations."""

from __future__ import annotations

from dataclasses import replace
from types import MethodType
from typing import TYPE_CHECKING

from starlette.routing import BaseRoute, Mount, Route, WebSocketRoute

if TYPE_CHECKING:
    from _typeshed import DataclassInstance


def present[T](value: T | None) -> T:
    assert value is not None
    return value


def require_instance[T](value: object, expected: type[T]) -> T:
    assert isinstance(value, expected)
    return value


def replaced[T: DataclassInstance](value: T, **changes: object) -> T:
    """Exercise dynamic field names, including deliberately invalid fixture values."""
    return replace(value, **changes)


def set_field(value: object, name: str, replacement: object) -> None:
    """Exercise a rejected assignment through the object's actual setter."""
    setattr(value, name, replacement)


def route_path(route: BaseRoute) -> str:
    assert isinstance(route, (Route, Mount, WebSocketRoute))
    return route.path


def method_owner(method: object) -> object:
    return require_instance(method, MethodType).__self__
