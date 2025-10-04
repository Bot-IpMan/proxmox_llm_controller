"""Minimal fallback implementations for a subset of Pydantic features.

This module provides just enough compatibility for the unit tests when the
real :mod:`pydantic` package is not installed.  It intentionally implements a
very small surface area – only the pieces that are exercised by the tests –
and therefore should not be used as a general purpose replacement.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional
import ipaddress


__all__ = [
    "BaseModel",
    "ConfigDict",
    "Field",
    "IPvAnyAddress",
    "ValidationError",
    "field_validator",
    "model_validator",
]


_UNSET = object()


class ValidationError(ValueError):
    """Exception raised when validation fails."""


@dataclass
class FieldInfo:
    default: Any = _UNSET
    default_factory: Optional[Callable[[], Any]] = None
    description: Optional[str] = None


def Field(*, default: Any = _UNSET, default_factory: Optional[Callable[[], Any]] = None, description: Optional[str] = None) -> FieldInfo:
    if default is not _UNSET and default_factory is not None:
        raise ValueError("Cannot specify both default and default_factory")
    return FieldInfo(default=default, default_factory=default_factory, description=description)


class ConfigDict(dict):
    """Simple stand-in used for annotating model configuration."""


class IPvAnyAddress(str):
    """String subclass that normalises IPv4/IPv6 addresses."""

    @classmethod
    def validate(cls, value: Any) -> "IPvAnyAddress":
        if isinstance(value, cls):
            return value
        if isinstance(value, (bytes, bytearray)):
            value = value.decode()
        if not isinstance(value, str):
            raise TypeError("value must be a string")
        try:
            addr = ipaddress.ip_address(value.strip())
        except ValueError as exc:  # pragma: no cover - defensive guard
            raise ValueError(str(exc)) from exc
        return cls(addr.compressed)


def field_validator(*fields: str):
    def decorator(fn: Callable) -> Callable:
        target = fn
        if isinstance(fn, (classmethod, staticmethod)):
            target = fn.__func__
        setattr(target, "__field_validators__", fields)
        return fn

    return decorator


def model_validator(*, mode: str):
    if mode != "after":
        raise NotImplementedError("Only 'after' model validators are supported")

    def decorator(fn: Callable) -> Callable:
        target = fn
        if isinstance(fn, (classmethod, staticmethod)):
            target = fn.__func__
        setattr(target, "__model_validator__", mode)
        return fn

    return decorator


class BaseModelMeta(type):
    def __new__(mcls, name: str, bases: Iterable[type], namespace: Dict[str, Any]):
        cls = super().__new__(mcls, name, bases, dict(namespace))

        fields: Dict[str, FieldInfo] = {}
        field_validators: Dict[str, List[Callable]] = {}
        model_validators: List[Callable] = []

        # inherit configuration from bases
        for base in reversed(cls.__mro__[1:]):
            if hasattr(base, "__fields__"):
                fields.update(getattr(base, "__fields__"))
            if hasattr(base, "__field_validators__"):
                for field, validators in getattr(base, "__field_validators__").items():
                    field_validators.setdefault(field, []).extend(validators)
            if hasattr(base, "__model_validators__"):
                model_validators.extend(getattr(base, "__model_validators__"))

        annotations: Mapping[str, Any] = {}
        for base in reversed(cls.__mro__[1:]):
            annotations.update(getattr(base, "__annotations__", {}))
        annotations.update(namespace.get("__annotations__", {}))

        for field_name, annotation in annotations.items():
            default = getattr(cls, field_name, _UNSET)
            if isinstance(default, FieldInfo):
                info = default
                # Replace class attribute with actual default for nicer repr/access
                if info.default is not _UNSET:
                    setattr(cls, field_name, info.default)
                else:
                    if hasattr(cls, field_name):
                        delattr(cls, field_name)
            elif default is not _UNSET:
                info = FieldInfo(default=default)
            else:
                info = FieldInfo()
            fields[field_name] = info

        for attr_name, attr in namespace.items():
            raw = attr
            if isinstance(attr, (classmethod, staticmethod)):
                raw = attr.__func__
            field_names = getattr(raw, "__field_validators__", None)
            if field_names:
                for field in field_names:
                    field_validators.setdefault(field, []).append(attr)
            if getattr(raw, "__model_validators__", None):
                model_validators.append(attr)

        cls.__fields__ = fields
        cls.__field_validators__ = field_validators
        cls.__model_validators__ = model_validators
        return cls


class BaseModel(metaclass=BaseModelMeta):
    __fields__: Dict[str, FieldInfo]
    __field_validators__: Dict[str, List[Callable]]
    __model_validators__: List[Callable]

    def __init__(self, **data: Any):
        errors: List[str] = []
        values: Dict[str, Any] = {}

        for field_name, info in self.__fields__.items():
            if field_name in data:
                value = data.pop(field_name)
            elif info.default is not _UNSET:
                value = info.default
            elif info.default_factory is not None:
                value = info.default_factory()
            else:
                errors.append(f"{field_name}: field required")
                continue

            try:
                value = self._run_field_validators(field_name, value)
            except ValidationError as exc:
                errors.append(f"{field_name}: {exc}")
                continue
            except ValueError as exc:
                errors.append(f"{field_name}: {exc}")
                continue

            values[field_name] = value

        # Accept extra fields without validation.
        for key, value in data.items():
            values[key] = value

        if errors:
            raise ValidationError("; ".join(errors))

        for key, value in values.items():
            setattr(self, key, value)

        self._run_model_validators()

    # ------------------------------------------------------------------
    def _run_field_validators(self, field_name: str, value: Any) -> Any:
        validators = self.__field_validators__.get(field_name, [])
        for validator in validators:
            bound = validator.__get__(None, self.__class__)
            value = bound(value)
        return value

    def _run_model_validators(self) -> None:
        for validator in self.__model_validators__:
            bound = validator.__get__(self, self.__class__)
            result = bound()
            if result is not None and result is not self:
                # Allow validators to return self or None for compatibility.
                for key, value in result.__dict__.items():
                    setattr(self, key, value)

    # Convenience helpers ------------------------------------------------
    def dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    def model_dump(self) -> Dict[str, Any]:
        return self.dict()

    def copy(self) -> "BaseModel":
        return self.__class__(**self.dict())

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        attrs = ", ".join(f"{k}={v!r}" for k, v in self.__dict__.items())
        return f"{self.__class__.__name__}({attrs})"
