"""Aggregates CommandSpec declarations from every domain module."""

from __future__ import annotations

import importlib

from rig_meta_factory.cli import CommandSpec

_MODULES = (
    "gate_d",
    "templates",
    "factories",
    "owners",
    "backports",
    "mailbox",
    "lead",
    "tac_bridge",
)


def all_specs() -> list[CommandSpec]:
    specs: list[CommandSpec] = []
    for mod_name in _MODULES:
        try:
            mod = importlib.import_module(f"rig_meta_factory.{mod_name}")
        except ModuleNotFoundError as exc:
            if exc.name == f"rig_meta_factory.{mod_name}":
                continue  # module not built yet
            raise
        specs.extend(mod.command_specs())
    return specs
