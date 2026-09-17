"""Concrete ``StateChart`` classes for each hand-authored test page type.

The fixture counterpart of ``statecharts``. Under test mode ``registered_pagetypes()`` hands back
the ``test-*`` fixtures, so doc generation runs over them and needs the same importable machine name
per type that the doc renderer resolves against. Binding them here rather than beside the production
types keeps that module's names to the types the documentation site publishes.
"""

from __future__ import annotations

from .fsm import machine_class
from .testtypes import TEST_REGISTRY


def _page_fsm(tag: str):
    page = TEST_REGISTRY.get(tag)
    if page is None:
        raise KeyError(f"Unknown page type {tag!r}.")
    return page.fsm


# Page-status machines (one per fixture). Element machines are not bound: only a page's status
# machine is resolved by qualname, for the diagram directive on its state pages.
TestFieldsMachine = machine_class(_page_fsm("test-fields"))
TestBlocksMachine = machine_class(_page_fsm("test-blocks"))
TestElementBlocksMachine = machine_class(_page_fsm("test-element-blocks"))
TestFlowMachine = machine_class(_page_fsm("test-flow"))
TestLifecycleMachine = machine_class(_page_fsm("test-lifecycle"))
TestChildMachine = machine_class(_page_fsm("test-child"))
