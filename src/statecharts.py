"""Concrete ``StateChart`` classes for each registered page type.

A page type builds its status machine when it is declared; this module serves that machine
under a stable, importable name so tools that reference one by import path - notably the
``statemachine-diagram`` Sphinx directive used by the documentation site - can find it. Only
page-status machines are served, because only they are diagrammed: an element machine is
reached through the field that declares it and needs no importable address.
"""

from __future__ import annotations

from .fsm import machine_class
from .pagetypes._registry import REGISTRY

# The documentation site publishes the production types, so these lookups read the production
# registry directly rather than whichever one is in play.


def __getattr__(name: str):
    """The page-status machine bound under `name`, derived from the registry.

    Resolved per call against the live ``REGISTRY`` rather than a snapshot taken at
    import, so an HMR reload of the page types cannot leave a stale class bound here.
    """
    for page_type in REGISTRY.values():
        if f"{page_type.fsm.name}Machine" == name:
            return machine_class(page_type.fsm)
    raise AttributeError(f"No page-status machine is bound as {name!r}.")

