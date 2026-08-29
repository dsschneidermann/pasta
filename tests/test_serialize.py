"""Unit tests for pure serialization round-tripping."""

from src.model import Page, Workspace
from src.serialize import (
    page_from_dict,
    page_to_dict,
    workspace_from_dict,
    workspace_to_dict,
)


def _sample_page():
    return Page(
        id="architecture:1",
        type="architecture",
        title="Page title",
        status="stale",
        parent_id=None,
        child_ids=["bug-report:9"],
        sections={
            "summary": {"kind": "module", "body": "core"},
            "invariants": {"items": [{"id": "el1", "statement": "ordered"}]},
        },
    )


def test_page_roundtrip():
    page = _sample_page()
    assert page_from_dict(page_to_dict(page)) == page


def test_workspace_roundtrip():
    workspace = Workspace(
        id="ws:1",
        name="demo",
        status="active",
        root_page_ids=["architecture:1"],
        pages={"architecture:1": _sample_page()},
        created_at="2026-07-20T00:00:00+00:00",
        updated_at="2026-07-20T00:00:00+00:00",
    )
    restored = workspace_from_dict(workspace_to_dict(workspace))
    assert restored == workspace
    assert restored.pages["architecture:1"].sections["summary"]["kind"] == "module"


def test_page_links_roundtrip():
    page = _sample_page()
    page.links = [{"to": "bug-report:9", "role": "depends-on"},
                  {"to": "architecture:2", "role": "supersedes"}]
    assert page_from_dict(page_to_dict(page)) == page


def test_page_from_dict_defaults_missing_links():
    # a legacy page dict (written before links existed) loads with links == []
    data = page_to_dict(_sample_page())
    del data["links"]
    assert page_from_dict(data).links == []


def test_page_expires_at_round_trips():
    page = _sample_page()
    page.expires_at = "2026-08-18T12:00:00+00:00"
    assert page_from_dict(page_to_dict(page)).expires_at == "2026-08-18T12:00:00+00:00"


def test_page_expires_at_defaults_to_none_for_legacy_files():
    # a legacy page dict (written before the cleanup sweep existed) loads with None
    data = page_to_dict(_sample_page())
    del data["expires_at"]
    assert page_from_dict(data).expires_at is None


def test_page_status_revision_token_round_trips():
    page = _sample_page()
    page.status_revision_token = "042917"
    assert page_from_dict(page_to_dict(page)).status_revision_token == "042917"


def test_page_status_revision_token_defaults_to_none_for_legacy_files():
    # a legacy page dict (written before the status-revision stamp existed) loads with None
    data = page_to_dict(_sample_page())
    del data["status_revision_token"]
    assert page_from_dict(data).status_revision_token is None
