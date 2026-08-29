"""Integration tests for the FastAPI HTML routes (src.server web layer).

The routes resolve the module-global STORE at call time, so the fixture repoints it at a
per-test temp store (same pattern as the MCP tests).

The generic web-render tests use the test-fields fixture. The model-toggle tests keep a production
type (architecture): the toggle points at that page type's generated Sphinx doc, and only
production types are documentable - a fixture has no doc to link to.
"""

import pytest
from fastapi.testclient import TestClient

import src.server as server
from src.errors import ValidationError
from src.store import Store


@pytest.fixture
def client(tmp_path):
    server.STORE = Store(tmp_path)
    return TestClient(server.app)


def test_index_lists_workspaces(client):
    workspace = server.STORE.create_workspace("demo")
    response = client.get("/")
    assert response.status_code == 200
    assert "demo" in response.text
    assert f"/{workspace.id}" in response.text            # link to the workspace tree page


def test_tree_route_links_pages_workspace_scoped(client):
    workspace = server.STORE.create_workspace("demo")
    page = server.STORE.create_page(workspace.id, "test-fields", "Page title").page
    part = workspace.id.removeprefix("ws:")
    response = client.get(f"/ws:{part}")
    assert response.status_code == 200
    assert "Page title" in response.text
    assert f"/{workspace.id}/page/{page.id}" in response.text   # workspace-scoped /page link


def test_page_view_renders_body_and_sidebar(client):
    workspace = server.STORE.create_workspace("demo")
    page = server.STORE.create_page(workspace.id, "test-fields", "Page title").page
    server.STORE.mutate_page_batch(workspace.id, page.id, [
        {"command": "setBody", "args": {"statusRevisionToken": page.status_revision_token, "text": "The body."}}
    ])
    part = workspace.id.removeprefix("ws:")
    response = client.get(f"/ws:{part}/page/{page.id}")
    assert response.status_code == 200
    assert "The body." in response.text             # the rendered page body
    assert 'id="sidebar"' in response.text               # the nav sidebar layout


def test_page_view_has_model_toggle_to_page_type_doc(client):
    workspace = server.STORE.create_workspace("demo")
    page = server.STORE.create_page(workspace.id, "test-fields", "Page title").page
    part = workspace.id.removeprefix("ws:")
    response = client.get(f"/ws:{part}/page/{page.id}")
    assert response.status_code == 200
    assert 'id="show-model"' in response.text                            # the Model toggle button
    # The iframe points at THIS page's type-AND-state doc (a new test-fields page is `current`),
    # and defers loading via data-src (not src).
    assert 'data-src="/sphinx/page-types/test-fields-active.html"' in response.text
    assert 'id="model-view"' in response.text and "<iframe" in response.text


def test_model_toggle_absent_on_index_and_tree(client):
    # The toggle lives in _nav.html (page view only), never in _base.html-derived views.
    workspace = server.STORE.create_workspace("demo")
    server.STORE.create_page(workspace.id, "test-fields", "Page title")
    part = workspace.id.removeprefix("ws:")
    assert 'id="show-model"' not in client.get("/").text                 # workspace index
    assert 'id="show-model"' not in client.get(f"/ws:{part}").text       # workspace tree


def test_page_view_shows_archive_button(client):
    workspace = server.STORE.create_workspace("demo")
    page = server.STORE.create_page(workspace.id, "test-fields", "Page title").page
    part = workspace.id.removeprefix("ws:")
    response = client.get(f"/ws:{part}/page/{page.id}")
    assert response.status_code == 200
    assert "Archive page" in response.text                              # not-yet-archived label
    assert f'action="/{workspace.id}/page/{page.id}/archive"' in response.text


def test_archive_route_archives_and_flips_button(client):
    workspace = server.STORE.create_workspace("demo")
    page = server.STORE.create_page(workspace.id, "test-fields", "Page title").page
    part = workspace.id.removeprefix("ws:")

    # POST the Archive button -> 303 redirect -> archived view (TestClient follows the redirect).
    response = client.post(f"/ws:{part}/page/{page.id}/archive")
    assert response.status_code == 202
    assert server.STORE.get_page(workspace.id, page.id).archived is True


def test_unarchive_route_restores_page(client):
    workspace = server.STORE.create_workspace("demo")
    page = server.STORE.create_page(workspace.id, "test-fields", "Page title").page
    server.STORE.archive_page(workspace.id, page.id)
    part = workspace.id.removeprefix("ws:")

    response = client.post(f"/ws:{part}/page/{page.id}/unarchive")
    assert response.status_code == 202
    assert server.STORE.get_page(workspace.id, page.id).archived is False


def test_page_view_shows_state_dropdown_next_to_archive(client):
    # test-flow's FSM is draft -> open -> closed; a fresh page is `draft`.
    workspace = server.STORE.create_workspace("demo")
    page = server.STORE.create_page(workspace.id, "test-flow", "Page title").page
    part = workspace.id.removeprefix("ws:")
    response = client.get(f"/ws:{part}/page/{page.id}")
    assert response.status_code == 200
    # The dropdown posts to the new /status route and offers every FSM state, current one selected.
    assert f'action="/{workspace.id}/page/{page.id}/status"' in response.text
    assert 'id="status-select"' in response.text and 'name="status"' in response.text
    assert 'value="draft" selected' in response.text          # current state preselected
    assert 'value="open"' in response.text and 'value="closed"' in response.text


def test_state_dropdown_absent_on_index_and_tree(client):
    # Like the Archive/Model controls, the dropdown lives only in the single-page view.
    workspace = server.STORE.create_workspace("demo")
    server.STORE.create_page(workspace.id, "test-flow", "Page title")
    part = workspace.id.removeprefix("ws:")
    assert 'id="status-select"' not in client.get("/").text
    assert 'id="status-select"' not in client.get(f"/ws:{part}").text


def test_status_route_sets_state_bypassing_fsm(client):
    # draft -> closed is NOT a single legal FSM transition (draft -> open -> closed); the direct
    # override sets it anyway, proving it bypasses the modelled transition guards.
    workspace = server.STORE.create_workspace("demo")
    page = server.STORE.create_page(workspace.id, "test-flow", "Page title").page
    assert page.status == "draft"
    part = workspace.id.removeprefix("ws:")

    response = client.post(f"/ws:{part}/page/{page.id}/status", data={"status": "closed"})
    assert response.status_code == 202
    assert server.STORE.get_page(workspace.id, page.id).status == "closed"


def test_set_page_status_rejects_unknown_state(client):
    workspace = server.STORE.create_workspace("demo")
    page = server.STORE.create_page(workspace.id, "test-flow", "Page title").page
    with pytest.raises(ValidationError):
        server.STORE.set_page_status(workspace.id, page.id, "not-a-state")


def test_reloader_websocket_sends_refresh(client):
    with client.websocket_connect("/ws/reloader") as websocket:
        assert websocket.receive_json() == {"refresh": 0}   # heartbeat on connect


def test_responses_disable_caching(client):
    # No-cache is stamped by middleware, so it covers both HTML routes and - the real target -
    # the /static mount where image/asset files are served.
    assert client.get("/").headers["cache-control"] == "no-cache, no-store, must-revalidate"


def test_page_view_renders_the_structured_body(client):
    workspace = server.STORE.create_workspace("demo")
    page = server.STORE.create_page(workspace.id, "test-fields", "Page title").page
    server.STORE.mutate_page_batch(workspace.id, page.id, [
        {"command": "setBody", "args": {"statusRevisionToken": page.status_revision_token, "text": "The body."}},
        {"command": "addItem", "args": {"statusRevisionToken": page.status_revision_token, "text": "Item one", "note": "a note"}},
    ])
    part = workspace.id.removeprefix("ws:")
    response = client.get(f"/ws:{part}/page/{page.id}")
    assert response.status_code == 200
    assert '<article class="pasta-page">' in response.text
    assert "<dt>text</dt><dd><p>Item one</p></dd>" in response.text
    assert "<dt>note</dt><dd><p>a note</p></dd>" in response.text
    assert '<nav class="page-contents"' in response.text
    assert "The body." in response.text                 # the prose field still reaches the page
    assert 'id="sidebar"' in response.text              # the Markdown-rendered nav is untouched
