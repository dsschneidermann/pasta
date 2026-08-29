"""Pure serialization: Workspace/Page <-> plain JSON-able dict.

Field values are already JSON-able, so this is a near-identity mapping; it exists
to keep an explicit, stable on-disk shape independent of the dataclass internals.
"""

from __future__ import annotations

from typing import Any

from .model import Page, Workspace


def page_to_dict(page: Page) -> dict[str, Any]:
    return {
        "id": page.id,
        "type": page.type,
        "title": page.title,
        "status": page.status,
        "parent_id": page.parent_id,
        "child_ids": list(page.child_ids),
        "sections": page.sections,
        "archived": page.archived,
        "links": [dict(link) for link in page.links],
        "expires_at": page.expires_at,
        "status_revision_token": page.status_revision_token,
    }


def page_from_dict(data: dict[str, Any]) -> Page:
    return Page(
        id=data["id"],
        type=data["type"],
        title=data["title"],
        status=data["status"],
        parent_id=data.get("parent_id"),
        child_ids=list(data.get("child_ids", [])),
        sections=data.get("sections", {}),
        archived=data.get("archived", False),
        links=[dict(link) for link in data.get("links", [])],
        expires_at=data.get("expires_at"),
        status_revision_token=data.get("status_revision_token"),
    )


def workspace_to_dict(workspace: Workspace) -> dict[str, Any]:
    return {
        "id": workspace.id,
        "name": workspace.name,
        "status": workspace.status,
        "root_page_ids": list(workspace.root_page_ids),
        "created_at": workspace.created_at,
        "updated_at": workspace.updated_at,
        "pages": {page_id: page_to_dict(page) for page_id, page in workspace.pages.items()},
    }


def workspace_from_dict(data: dict[str, Any]) -> Workspace:
    return Workspace(
        id=data["id"],
        name=data["name"],
        status=data.get("status", "active"),
        root_page_ids=list(data.get("root_page_ids", [])),
        pages={page_id: page_from_dict(page_data) for page_id, page_data in data.get("pages", {}).items()},
        created_at=data.get("created_at", ""),
        updated_at=data.get("updated_at", ""),
    )
