"""Pure domain dataclasses: `Workspace` and `Page`.

Field *values* are plain JSON-able Python (str / None / list[dict]); the *shape*
of a page's sections is defined by its page type (`src/pagetypes/`), not here. That
keeps this model tiny and makes serialization a near-identity mapping.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Page:
    id: str
    type: str                                   # page-type tag, e.g. "architecture"
    title: str
    status: str                                 # current FSM state value, e.g. "draft"
    parent_id: str | None = None
    child_ids: list[str] = field(default_factory=list)
    # sections[sectionKey][fieldKey] = value (scalar/prose str/None, or list[dict] for lists)
    sections: dict[str, dict[str, Any]] = field(default_factory=dict)
    archived: bool = False                       # hidden from default tree views; cannot be mutated
    links: list[dict[str, Any]] = field(default_factory=list)  # outgoing typed edges: [{"to": pageId, "role": str}]
    # UTC ISO-8601 instant after which the cleanup sweep deletes this page, or None.
    # Written and cleared only by the sweep, never by a page-type command.
    expires_at: str | None = None
    # A short optimistic-concurrency stamp on the lifecycle status: created with the page and
    # regenerated on every status transition. Written only by the store, never by a page-type
    # command; None on a page created before the feature, until its first transition.
    status_revision_token: str | None = None

    def copy(self) -> "Page":
        """A deep copy - the pure command path edits a copy, never the input."""
        return Page(
            id=self.id,
            type=self.type,
            title=self.title,
            status=self.status,
            parent_id=self.parent_id,
            child_ids=list(self.child_ids),
            sections=copy.deepcopy(self.sections),
            archived=self.archived,
            links=[dict(link) for link in self.links],
            expires_at=self.expires_at,
            status_revision_token=self.status_revision_token,
        )


@dataclass
class Workspace:
    id: str
    name: str
    status: str = "active"                       # "active" | "archived" (archive deferred)
    root_page_ids: list[str] = field(default_factory=list)
    pages: dict[str, Page] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    guidance_config: dict[str, str] = field(default_factory=dict)

    def get_page(self, page_id: str) -> Page | None:
        return self.pages.get(page_id)
