"""The stateful shell: file storage, per-workspace locking, guarded writes.

One JSON file per workspace holds its metadata and every page. Writes follow the
copy -> edit -> batch -> overwrite pattern:

  1. take the workspace's transaction lock,
  2. load + deserialize (a fresh in-memory copy),
  3. apply the pure-core mutation(s) in memory,
  4. serialize to a temp file and copy it over the destination.

Each workspace has two locks because neither prevents the other's failure: the
transaction lock serializes writers over steps 1-4, and the readers-writer lock keeps
a reader out of step 4's copy, which readers reach without the transaction lock. They
never nest in a cycle: the readers-writer lock is taken and released inside the other.

Concurrency is in-process. Cross-process locking is a documented later step.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, final

from . import cleanup, commands, fsm, render, render_html
from .errors import ConflictError, PastaError, IllegalCommandError, NotFoundError, ValidationError
from .ids import IdFactory, RevisionFactory, default_id_factory, default_revision_factory, new_id
from .model import Page, Workspace
from .pagetypes import (
    ADD_LINK,
    BLOCK_ARRAY,
    COMPOUND,
    LIST,
    TRANSITION,
    CommandSpec,
    PageType,
    RefCheck,
    collect_ref_ids,
    get_page_type,
    is_auto_child_type,
    registered_tags,
)
from .rwlock import ReadWriteLock
from .serialize import workspace_from_dict, workspace_to_dict

# The search prefix that switches a query from text matching to page-id resolution.
ID_QUERY_PREFIX = "id:"

# Subdirectory for pre-prune backups. Not beside the live files: list_workspaces globs
# "*.json" non-recursively, so a backup there would be listed as a duplicate workspace.
BACKUP_DIRNAME = "backups"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CreatePageResult:
    page: Page
    children: list[Page] = field(default_factory=list)   # auto-created pinned children, if any


@final
class Store:
    def __init__(self, root: str | os.PathLike[str], id_factory: IdFactory = default_id_factory,
                 revision_factory: RevisionFactory = default_revision_factory) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._id_factory = id_factory
        self._revision_factory = revision_factory
        self._transaction_locks: dict[str, threading.Lock] = {}
        self._rw_locks: dict[str, ReadWriteLock] = {}
        self._locks_guard = threading.Lock()

    def _next_revision(self, current: str | None) -> str:
        """A fresh status-revision token that differs from `current`, so a stale token a caller
        still holds can never accidentally re-match the page after it moved."""
        token = self._revision_factory()
        while token == current:
            token = self._revision_factory()
        return token

    # --- paths & locks -------------------------------------------------------
    def _path_for(self, workspace_id: str) -> Path:
        # ":" is not a legal Windows filename character; the true id lives in the file.
        return self.root / f"{workspace_id.replace(':', '_')}.json"

    @staticmethod
    def _id_for_path(path: Path) -> str:
        """The inverse of _path_for, so the listing can lock a file before parsing its id."""
        return path.stem.replace("_", ":", 1)

    def _transaction_lock_for(self, workspace_id: str) -> threading.Lock:
        """Serializes write transactions, so no update is lost. Readers never take it."""
        with self._locks_guard:
            lock = self._transaction_locks.get(workspace_id)
            if lock is None:
                lock = threading.Lock()
                self._transaction_locks[workspace_id] = lock
            return lock

    def _rw_lock_for(self, workspace_id: str) -> ReadWriteLock:
        """Keeps a reader out of the copy that replaces a workspace file.

        Held per file operation, not per transaction. One lock per workspace covers
        every file it is stored in, backups included.
        """
        with self._locks_guard:
            lock = self._rw_locks.get(workspace_id)
            if lock is None:
                lock = ReadWriteLock()
                self._rw_locks[workspace_id] = lock
            return lock

    # --- low-level persistence ----------------------------------------------
    def _write_file(self, path: Path, workspace: Workspace) -> None:
        text = json.dumps(workspace_to_dict(workspace), indent=2, ensure_ascii=False)
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
        with open(tmp, "w", encoding="utf-8") as handle:
            _ = handle.write(text)
        with self._rw_lock_for(workspace.id).write():  # atomic replace but guarded with the lock
            try:
                os.replace(tmp, path)
            except Exception:
                time.sleep(0.1) # Permission error moving the tmp file can be retried.
                os.replace(tmp, path)

    def _touch_and_save(self, workspace: Workspace) -> None:
        workspace.updated_at = _now()
        self._write_file(self._path_for(workspace.id), workspace)

    def write_backup(self, workspace: Workspace, now: datetime) -> Path:
        """Write a full copy of `workspace` under `backups/` and return its path.

        The timestamp is colon-free because ':' is not a legal Windows filename
        character - the same reason _path_for maps 'ws:' to 'ws_'.
        """
        token = workspace.id.replace(":", "_")
        stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        directory = self.root / BACKUP_DIRNAME / token
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{stamp}.json"
        self._write_file(path, workspace)
        return path

    def load_workspace(self, workspace_id: str) -> Workspace:
        path = self._path_for(workspace_id)
        with self._rw_lock_for(workspace_id).read():
            if not path.exists():
                raise NotFoundError(f"Workspace '{workspace_id}' not found.")
            text = path.read_text(encoding="utf-8")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PastaError(f"Workspace '{workspace_id}' file is corrupt: {exc}") from exc
        return workspace_from_dict(data)

    # --- workspace operations ------------------------------------------------
    def list_workspaces(self) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                with self._rw_lock_for(self._id_for_path(path)).read():
                    text = path.read_text(encoding="utf-8")
                data = json.loads(text)
            except (json.JSONDecodeError, OSError):
                continue  # skip unreadable/half-written files rather than fail the whole listing
            result.append({"id": data["id"], "name": data["name"], "status": data.get("status", "active")})
        return result

    def create_workspace(self, name: str) -> Workspace:
        if not name or not name.strip():
            raise ValidationError("Workspace name must be a non-empty string.")
        workspace = Workspace(id=new_id("ws"), name=name, created_at=_now(), updated_at=_now())
        with self._transaction_lock_for(workspace.id):
            self._write_file(self._path_for(workspace.id), workspace)
        return workspace

    # --- page reads ----------------------------------------------------------
    def get_page(self, workspace_id: str, page_id: str) -> Page:
        workspace = self.load_workspace(workspace_id)
        page = workspace.get_page(page_id)
        if page is None:
            raise NotFoundError(f"Page '{page_id}' not found in workspace '{workspace_id}'.")
        return page

    def tree(self, workspace_id: str, include_archived: bool = False) -> dict[str, Any]:
        """An ordered outline of the page tree - structure/metadata only, no body content.

        Archived pages (and their subtrees) are hidden unless `include_archived`, in which case
        they are shown flagged `archived: true`.
        """
        workspace = self.load_workspace(workspace_id)

        def ordered(ids: list[str]) -> list[str]:
            """Stable partition: archived pages sink below non-archived ones at this level, with each
            group's explicit reorder/reparent order preserved (`sorted` is stable, `False < True`)."""
            return sorted(ids, key=lambda page_id: workspace.pages[page_id].archived)

        def node(page_id: str) -> dict[str, Any] | None:
            page = workspace.pages[page_id]
            if page.archived and not include_archived:
                return None
            entry: dict[str, Any] = {
                "id": page.id,
                "title": page.title,
                "type": page.type,
                "status": page.status,
                "children": [child for child in map(node, ordered(page.child_ids)) if child is not None],
            }
            if page.archived:
                entry["archived"] = True
            return entry

        return {
            "workspaceId": workspace.id,
            "name": workspace.name,
            "nodes": [root for root in map(node, ordered(workspace.root_page_ids)) if root is not None],
        }

    def outline(self, workspace_id: str, page_id: str) -> dict[str, Any]:
        """A page's section tree (key, name, order, field kinds) - structure, no body."""
        page = self.get_page(workspace_id, page_id)
        page_type = get_page_type(page.type)
        if page_type is None:
            raise PastaError(f"Page '{page_id}' has unregistered type '{page.type}'.")
        return {
            "pageId": page.id,
            "type": page.type,
            "title": page.title,
            "sections": [
                {
                    "key": section.key,
                    "name": section.name,
                    "order": order,
                    "fields": [{"key": f.key, "kind": f.kind} for f in section.fields],
                }
                for order, section in enumerate(page_type.sections)
            ],
        }

    def render_markdown(
        self, workspace_id: str, page_id: str | None = None, show_archived: bool = False,
        escape_plain_text: bool = False,
    ) -> str:
        """Render one page, or the whole (non-archived) workspace tree, to Markdown.

        `show_archived` only flows onto inline-ref links (as `?archived=true`) so following a ref
        keeps the archived view; it does not change which pages the tree render includes.
        `escape_plain_text` markdown-escapes every plain-text leaf for the web HTML view; it defaults
        off so the MCP render path (renderPage) returns Markdown unchanged. The whole-tree render is
        MCP-only (no web route renders it), so it always renders unescaped.
        """
        workspace = self.load_workspace(workspace_id)
        if page_id is None:
            return render.render_tree(workspace, show_archived)
        page = workspace.get_page(page_id)
        if page is None:
            raise NotFoundError(f"Page '{page_id}' not found in workspace '{workspace_id}'.")
        page_type = get_page_type(page.type)
        if page_type is None:
            raise PastaError(f"Page '{page_id}' has unregistered type '{page.type}'.")
        ref_context = render.build_ref_context(workspace, show_archived, escape_plain_text)
        return render.render_page(page, page_type, ref_context=ref_context)

    def render_html(self, workspace_id: str, page_id: str, show_archived: bool = False) -> str:
        """One page as structured HTML for the web view. Its Markdown sibling, `render_markdown`,
        is what the renderPage MCP tool returns and is unchanged.

        `show_archived` reaches the child and reference lists exactly as it does on the Markdown
        path: it decides whether an archived target is hidden or flagged, and rides onto the links
        as a query parameter.
        """
        workspace = self.load_workspace(workspace_id)
        page = workspace.get_page(page_id)
        if page is None:
            raise NotFoundError(f"Page '{page_id}' not found in workspace '{workspace_id}'.")
        page_type = get_page_type(page.type)
        if page_type is None:
            raise PastaError(f"Page '{page_id}' has unregistered type '{page.type}'.")
        ref_context = render.build_ref_context(workspace, show_archived, escape_plain_text=True)
        return render_html.render_page_html(page, page_type, ref_context)

    def search(self, workspace_id: str, query: str, limit: int = 20) -> dict[str, Any]:
        """Rank live pages by a case-insensitive word-prefix match of `query`'s terms against
        their content. A page is live when neither it nor any ancestor is archived, which is the
        same subtree rule `tree` applies - so the two agree on what exists. An `id:<token>` query
        instead resolves `token` against every page id - archived pages included, since an id you
        hold may since have been archived."""
        workspace = self.load_workspace(workspace_id)
        query = query.strip()
        best: dict[str, dict[str, Any]] = {}

        def add(page: Page, score: int, snippet: str) -> None:
            prior = best.get(page.id)
            if prior is None or score > prior["score"]:
                best[page.id] = {"pageId": page.id, "title": page.title, "type": page.type,
                                 "archived": page.archived, "score": score, "snippet": snippet}

        token = query[len(ID_QUERY_PREFIX):].strip().lower() \
            if query.lower().startswith(ID_QUERY_PREFIX) else ""
        if token:                                      # page-id resolution, archived included
            for page in workspace.pages.values():
                if token in page.id.lower():
                    add(page, 10_001 if token == page.id.lower() else 10_000, f"id: {page.id}")
        else:
            terms = [term.lower() for term in query.split() if term]
            for page in workspace.pages.values():
                page_type = get_page_type(page.type)
                if page_type is None or not terms or self._archived_in_ancestry(workspace, page):
                    continue
                text = render.page_text(page, page_type)
                words = text.lower().split()
                score = sum(1 for word in words for term in terms if word.startswith(term))
                if score:
                    add(page, score, self._snippet(text, terms))

        ranked = sorted(best.values(), key=lambda hit: -hit["score"])[: max(1, limit)]
        return {"workspaceId": workspace.id, "query": query, "hits": ranked}

    @staticmethod
    def _archived_in_ancestry(workspace: Workspace, page: Page) -> bool:
        """True if `page` itself or any ancestor is archived - the subtree rule `tree` applies.

        Archiving sets the flag on the target page and cascades onto its PINNED children only, so
        an ordinary descendant of an archived page keeps `archived = False` and has to be judged
        by walking up. A dangling `parent_id` ends the walk (treated as no archived ancestor), and
        `seen` makes a corrupted parent cycle terminate instead of hanging the search.
        """
        seen: set[str] = set()
        current: Page | None = page
        while current is not None and current.id not in seen:
            if current.archived:
                return True
            seen.add(current.id)
            current = workspace.pages.get(current.parent_id) if current.parent_id else None
        return False

    @staticmethod
    def _snippet(text: str, terms: list[str], width: int = 140) -> str:
        lower = text.lower()
        found = [pos for pos in (lower.find(term) for term in terms) if pos != -1]
        idx = min(found) if found else 0
        start = max(0, idx - width // 4)
        chunk = text[start:start + width].strip()
        return ("…" if start > 0 else "") + chunk + ("…" if start + width < len(text) else "")

    # --- self-direction (next / attention) ----------------------------------
    @staticmethod
    def _awaits_human(element: dict[str, Any]) -> bool:
        """The model's awaiting-a-human predicate: an escalated, still-open question."""
        return bool(element.get("needsHuman")) and element.get("status") == "open"

    @staticmethod
    def _page_attention(page: Page, page_type: PageType) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for section in page_type.sections:
            for field_spec in section.fields:
                if field_spec.kind != LIST:
                    continue
                for element in page.sections.get(section.key, {}).get(field_spec.key, []):
                    if Store._awaits_human(element):
                        items.append({
                            "pageId": page.id, "pageTitle": page.title,
                            "sectionKey": section.key, "field": field_spec.key,
                            "itemId": element.get("id"), "status": element.get("status"),
                        })
        return items

    def _subtree_pages(self, workspace: Workspace, page_id: str | None) -> list[Page]:
        if page_id is None:
            return list(workspace.pages.values())
        root = workspace.get_page(page_id)
        if root is None:
            raise NotFoundError(f"Page '{page_id}' not found in workspace '{workspace.id}'.")
        collected: list[Page] = []

        def walk(page: Page) -> None:
            collected.append(page)
            for child_id in page.child_ids:
                child = workspace.pages.get(child_id)
                if child is not None:
                    walk(child)

        walk(root)
        return collected

    def next_actions(self, workspace_id: str, page_id: str | None = None) -> dict[str, Any]:
        """Partition the model-declared edges over a subtree (or the whole workspace) into
        `do` (agent edges to drive now), `blocked` (agent transitions with the unmet precondition to
        fix), `humanGates` (sign-off transitions - stop), and `attention` (items awaiting a human).

        `do` holds two edge shapes, each carrying a singular `command` - the same key `blocked`,
        `humanGates` and `attention` use, so all four lists read alike: a status TRANSITION
        (`kind='transition'`, `command=<event>`) and a stage-relevant field setter (`kind='field'`
        with section/field/instruction inline - the field setters whose field must be authored to
        advance this stage; see `commands.field_setter_edges`).

        A field is one edge naming one command, because a field's whole authoring content is
        reachable in a single command: a blocks field takes an array of kinded blocks, and a list
        add carries the blocks its element is created holding."""
        workspace = self.load_workspace(workspace_id)
        do: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        human_gates: list[dict[str, Any]] = []
        attention: list[dict[str, Any]] = []

        for page in self._subtree_pages(workspace, page_id):
            if page.archived:
                continue
            page_type = get_page_type(page.type)
            if page_type is None:
                continue
            allowed = fsm.allowed_events(page_type.fsm, page.status)
            legal = commands.legal_commands(page, page_type)
            # Events barred by this page's PARENT not having reached the required stage. Nothing
            # authored here can clear one, so the content they require is not this page's work yet
            # and its setters are withheld from `do` (see the field_setter_edges call below).
            parent_blocked: set[str] = set()
            for command in page_type.commands:
                if command.kind not in (TRANSITION, COMPOUND) or command.event is None:
                    continue
                if command.event not in allowed:
                    continue                        # not available from the current status
                parent_reason = self._parent_guard_failure(workspace, page, command)
                if parent_reason is not None:
                    parent_blocked.add(command.event)
                # Same precedence as _first_guard_failure: child guards first, then parent.
                guard_reason = self._child_guard_failure(workspace, page, command) or parent_reason
                if command.agency == "human":
                    edge = {"pageId": page.id, "pageType": page.type, "command": command.name,
                            "statusRevisionToken": page.status_revision_token}
                    if guard_reason:
                        edge["blockedReason"] = guard_reason
                    human_gates.append(edge)
                elif legal.get(command.name) and guard_reason is None:
                    do.append({"pageId": page.id, "pageType": page.type,
                               "kind": "transition", "command": command.name,
                               "statusRevisionToken": page.status_revision_token})
                else:
                    unmet = commands.unmet_requirements(page, command)
                    if parent_reason is not None:
                        # A parent-state guard OUTRANKS unmet content here: while the parent has not
                        # unlocked this stage the content is not the fix, and its setters are absent
                        # from `do` - so naming the fields would point at work that is not due yet.
                        reason = parent_reason
                    elif unmet:
                        reason = "requires: " + ", ".join(f"{s}.{f}" for s, f in unmet)
                    else:
                        reason = guard_reason or "blocked"
                    blocked.append({"pageId": page.id, "pageType": page.type,
                                    "command": command.name, "reason": reason,
                                    "statusRevisionToken": page.status_revision_token})
            # Field setters whose field must be authored to advance this stage also enter `do`.
            do.extend(commands.field_setter_edges(page, page_type, parent_blocked))
            attention.extend(self._page_attention(page, page_type))

        return {"do": do, "blocked": blocked, "humanGates": human_gates, "attention": attention}

    def attention(self, workspace_id: str) -> dict[str, Any]:
        """Workspace-wide scan for element instances awaiting a human (escalated open questions)."""
        workspace = self.load_workspace(workspace_id)
        items: list[dict[str, Any]] = []
        for page in workspace.pages.values():
            if page.archived:
                continue
            page_type = get_page_type(page.type)
            if page_type is not None:
                items.extend(self._page_attention(page, page_type))
        return {"workspaceId": workspace.id, "attention": items}

    # --- page write transactions --------------------------------------------
    def create_page(
        self, workspace_id: str, type_tag: str, title: str, parent_id: str | None = None
    ) -> CreatePageResult:
        page_type = get_page_type(type_tag)
        if page_type is None:
            raise ValidationError(
                f"Unknown page type '{type_tag}'. Registered: {', '.join(sorted(registered_tags()))}."
            )
        with self._transaction_lock_for(workspace_id):
            workspace = self.load_workspace(workspace_id)
            if parent_id is not None and parent_id not in workspace.pages:
                raise NotFoundError(f"Parent page '{parent_id}' not found in workspace '{workspace_id}'.")
            page = commands.create_page(page_type, title, parent_id, self._id_factory)
            page.status_revision_token = self._revision_factory()
            workspace.pages[page.id] = page
            if parent_id is None:
                workspace.root_page_ids.append(page.id)
            else:
                workspace.pages[parent_id].child_ids.append(page.id)
            # Some types (feature-brief) create pinned children in the same commit.
            children = self._create_auto_children(workspace, page, page_type)
            self._touch_and_save(workspace)
            return CreatePageResult(page=page, children=children)

    def mutate_page_batch(
        self, workspace_id: str, page_id: str, batch: list[dict[str, Any]]
    ) -> tuple[Page, list[str | None]]:
        """Apply an ordered batch of commands to a page as a single atomic commit.

        Each command is decided against the state left by the previous one; if any is rejected the
        whole batch aborts and nothing is written (the error names the failing index + command).
        Every command must present the page's current `statusRevisionToken`; a status transition
        regenerates it, so a command after a transition carries a stale token and the batch aborts.
        """
        if not batch:
            raise ValidationError("mutatePageBatch requires at least one command.")
        with self._transaction_lock_for(workspace_id):
            workspace = self.load_workspace(workspace_id)
            page = workspace.get_page(page_id)
            if page is None:
                raise NotFoundError(f"Page '{page_id}' not found in workspace '{workspace_id}'.")
            if page.archived:
                raise IllegalCommandError(f"Page '{page_id}' is archived; unarchive it before mutating.")
            page_type = get_page_type(page.type)
            if page_type is None:
                raise PastaError(f"Page '{page_id}' has unregistered type '{page.type}'.")

            working = page
            created_ids: list[str | None] = []
            created_so_far: set[str] = set()
            for index, entry in enumerate(batch):
                command = entry.get("command")
                args = dict(entry.get("args") or {})
                presented_revision = args.pop("statusRevisionToken", None)
                command_spec = page_type.command(command) if command else None
                try:
                    if command is None:
                        raise ValidationError("Unknown command None.")
                    if presented_revision != working.status_revision_token:
                        raise ConflictError(
                            f"statusRevisionToken {presented_revision!r} does not match the page's "
                            f"current revision {working.status_revision_token!r}. Each command must "
                            f"carry the current token; a status transition regenerates it, so a batch "
                            f"may hold at most one transition and only as its final command."
                        )
                    if command_spec is not None:
                        self._check_ref(workspace, working, command_spec, args)
                        self._check_block_refs(workspace, working, command_spec, args)
                        self._check_inline_refs(workspace, command_spec, args)
                        self._check_guards(workspace, working, command_spec)
                        self._check_link(workspace, workspace_id, working, command_spec, args)
                    status_before = working.status
                    result = commands.apply_command(
                        working, page_type, command, args, self._id_factory,
                        batch_context=commands.BatchContext(frozenset(created_so_far)),
                    )
                except PastaError as exc:
                    raise type(exc)(
                        f"Batch aborted at command {index} ('{command}'): {exc}"
                    ) from exc
                working = result.page
                # A status change is only ever a transition, so regenerate the stamp when it moves.
                if working.status != status_before:
                    working.status_revision_token = self._next_revision(working.status_revision_token)
                created_ids.append(result.created_id)
                if result.created_id is not None:
                    created_so_far.update(result.created_ids)

            workspace.pages[page_id] = working
            self._touch_and_save(workspace)
            return working, created_ids

    # --- scheduled cleanup ---------------------------------------------------
    def cleanup_workspace(
        self, workspace_id: str, now: datetime | None = None
    ) -> cleanup.SweepReport:
        """Stamp, back up and prune one workspace as a single atomic commit.

        The whole pass runs inside the workspace lock, so nothing can land between the
        backup and the deletion it protects. A backup that fails to write aborts the
        pass - no stamps either, leaving the workspace as it was.
        """
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValidationError("cleanup_workspace requires a timezone-aware datetime.")
        with self._transaction_lock_for(workspace_id):
            workspace = self.load_workspace(workspace_id)
            sweep = cleanup.classify(workspace, now)
            if not (sweep.stamp or sweep.clear or sweep.prune):
                return cleanup.SweepReport(workspace_id, 0, 0, [], None, None)

            backup: str | None = None
            if sweep.prune:
                # Written before any mutation: a prune only happens on top of a backup.
                try:
                    backup = str(self.write_backup(workspace, now))
                except OSError as exc:
                    return cleanup.SweepReport(workspace_id, 0, 0, [], None, str(exc))

            for page_id in sweep.clear:
                workspace.pages[page_id].expires_at = None
            for page_id, expiry in sweep.stamp.items():
                workspace.pages[page_id].expires_at = expiry
            pruned: list[str] = []
            for root_id in sweep.prune:
                pruned.extend(sorted(cleanup.delete_subtree(workspace, root_id)))
            self._touch_and_save(workspace)
            return cleanup.SweepReport(
                workspace_id, len(sweep.stamp), len(sweep.clear), pruned, backup, None
            )

    # --- archiving -----------------------------------------------------------
    def _set_page_archived(self, workspace_id: str, page_id: str, archived: bool) -> Page:
        with self._transaction_lock_for(workspace_id):
            workspace = self.load_workspace(workspace_id)
            page = workspace.get_page(page_id)
            if page is None:
                raise NotFoundError(f"Page '{page_id}' not found in workspace '{workspace_id}'.")
            if self._is_pinned_child(workspace, page):
                raise IllegalCommandError(
                    f"Page '{page_id}' is a pinned auto-created child; archive/unarchive its parent instead."
                )
            page.archived = archived
            # A pinned child's archived state mirrors its parent's: cascade onto this page's pinned children.
            parent_type = get_page_type(page.type)
            for child_id in page.child_ids:
                child = workspace.pages.get(child_id)
                if child is not None and is_auto_child_type(parent_type, child.type):
                    child.archived = archived
            self._touch_and_save(workspace)
            return page

    def archive_page(self, workspace_id: str, page_id: str) -> Page:
        return self._set_page_archived(workspace_id, page_id, True)

    def unarchive_page(self, workspace_id: str, page_id: str) -> Page:
        return self._set_page_archived(workspace_id, page_id, False)

    # --- direct status override ---------------------------------------------
    def set_page_status(self, workspace_id: str, page_id: str, status: str) -> Page:
        """Set a page's lifecycle status directly to any valid state of its type's FSM, bypassing
        the modelled transition guards. This is a human admin override (the web page view's state
        dropdown), not a modelled FSM edge - it exists so a person can correct a page's state from
        the browser. Rejects a missing page, an unregistered type, and a status that isn't one of
        the type's declared FSM states. Returns the updated page.
        """
        with self._transaction_lock_for(workspace_id):
            workspace = self.load_workspace(workspace_id)
            page = workspace.get_page(page_id)
            if page is None:
                raise NotFoundError(f"Page '{page_id}' not found in workspace '{workspace_id}'.")
            page_type = get_page_type(page.type)
            if page_type is None:
                raise PastaError(f"Page '{page_id}' has unregistered type '{page.type}'.")
            if not fsm.is_valid_status(page_type.fsm, status):
                raise ValidationError(
                    f"'{status}' is not a valid state for page type '{page.type}'. " +
                    f"Valid states: {', '.join(page_type.fsm.states)}."
                )
            page.status = status
            # A direct status edit regenerates the stamp too, so an out-of-band move invalidates held tokens.
            page.status_revision_token = self._next_revision(page.status_revision_token)
            self._touch_and_save(workspace)
            return page

    # --- page-tree structure (reparent / reorder / rename) ------------------
    @staticmethod
    def _sibling_ids(workspace: Workspace, parent_id: str | None) -> list[str]:
        """The ordered sibling list for `parent_id`: the workspace root list, or a parent's children.

        Returns the live list (mutating it edits the tree); it includes archived siblings.
        """
        return workspace.root_page_ids if parent_id is None else workspace.pages[parent_id].child_ids

    @staticmethod
    def _is_pinned_child(workspace: Workspace, page: Page) -> bool:
        """Whether `page` is an auto-created pinned child - its parent's type declares its type as an
        auto-child. Derived from the type registry (no field on the Page). A pinned page cannot be
        reparented, reordered, or archived/unarchived on its own."""
        if page.parent_id is None:
            return False
        parent = workspace.pages.get(page.parent_id)
        return parent is not None and is_auto_child_type(get_page_type(parent.type), page.type)

    def reparent_page(
        self, workspace_id: str, page_id: str, new_parent_id: str | None = None
    ) -> tuple[Page, list[str]]:
        """Move `page_id` under `new_parent_id` (or to top level when None), appended to the new
        siblings. Rejects a missing page/parent and a cycle (the new parent being the page itself or
        one of its descendants). Sibling titles are not reserved, so a same-titled sibling is fine.
        Positioning is reorder_page's job. Returns the moved page and the resulting sibling id order.
        """
        with self._transaction_lock_for(workspace_id):
            workspace = self.load_workspace(workspace_id)
            page = workspace.get_page(page_id)
            if page is None:
                raise NotFoundError(f"Page '{page_id}' not found in workspace '{workspace_id}'.")
            if self._is_pinned_child(workspace, page):
                raise IllegalCommandError(
                    f"Page '{page_id}' is a pinned auto-created child; it cannot be reparented."
                )
            if new_parent_id is not None:
                if new_parent_id not in workspace.pages:
                    raise NotFoundError(
                        f"New parent '{new_parent_id}' not found in workspace '{workspace_id}'."
                    )
                # A page cannot become its own ancestor: the new parent must lie outside its subtree.
                subtree_ids = {member.id for member in self._subtree_pages(workspace, page_id)}
                if new_parent_id in subtree_ids:
                    raise ConflictError(
                        f"Cannot reparent '{page_id}' under '{new_parent_id}': that would create a " +
                        f"cycle (the new parent is the page itself or one of its descendants)."
                    )
            self._sibling_ids(workspace, page.parent_id).remove(page_id)
            page.parent_id = new_parent_id
            new_siblings = self._sibling_ids(workspace, new_parent_id)
            new_siblings.append(page_id)
            self._touch_and_save(workspace)
            return page, list(new_siblings)

    def reorder_page(
        self, workspace_id: str, page_id: str, to_index: int, preceding_id: str | None = None
    ) -> tuple[Page, list[str]]:
        """Move `page_id` to an anchored position among its CURRENT siblings, mirroring block/element
        reorder via the same guard: the sibling now before `to_index` must equal `preceding_id`
        (None iff `to_index` is 0), else a stale-read ConflictError. Operates on the stored sibling
        order (archived siblings included). Returns the moved page and the resulting sibling id order.
        """
        with self._transaction_lock_for(workspace_id):
            workspace = self.load_workspace(workspace_id)
            page = workspace.get_page(page_id)
            if page is None:
                raise NotFoundError(f"Page '{page_id}' not found in workspace '{workspace_id}'.")
            if self._is_pinned_child(workspace, page):
                raise IllegalCommandError(
                    f"Page '{page_id}' is a pinned auto-created child; it cannot be reordered."
                )
            siblings = self._sibling_ids(workspace, page.parent_id)
            context = ("the top-level pages" if page.parent_id is None
                       else f"the children of '{page.parent_id}'")
            siblings.remove(page_id)
            slot = commands.resolve_anchored_slot(siblings, to_index, preceding_id, context)
            siblings.insert(slot, page_id)
            self._touch_and_save(workspace)
            return page, list(siblings)

    def rename_page(self, workspace_id: str, page_id: str, title: str) -> Page:
        """Change a page's title. Sibling titles are not reserved anywhere (create/reparent don't
        reserve them either): a title is a display label, never an identifier (pages are addressed
        only by id), so a duplicate cannot dangle a reference or break a lookup. Rejects a missing
        page and a blank title; permitted on archived and pinned pages, since a rename alters no tree
        structure or lifecycle state. Returns the renamed page.
        """
        if not title or not title.strip():
            raise ValidationError("Page title must be a non-empty string.")
        with self._transaction_lock_for(workspace_id):
            workspace = self.load_workspace(workspace_id)
            page = workspace.get_page(page_id)
            if page is None:
                raise NotFoundError(f"Page '{page_id}' not found in workspace '{workspace_id}'.")
            page.title = title
            self._touch_and_save(workspace)
            return page

    # --- page reference graph (link / unlink) --------------------------------
    @staticmethod
    def _link_source(workspace: Workspace, workspace_id: str, from_id: str, to_id: str) -> Page:
        """The source page for a link/unlink, with both endpoints validated to exist and the source
        (which a link mutates) required to be non-archived. The target may be archived."""
        source = workspace.get_page(from_id)
        if source is None:
            raise NotFoundError(f"Page '{from_id}' not found in workspace '{workspace_id}'.")
        if to_id not in workspace.pages:
            raise NotFoundError(f"Page '{to_id}' not found in workspace '{workspace_id}'.")
        if source.archived:
            raise IllegalCommandError(f"Page '{from_id}' is archived; unarchive it before linking.")
        return source

    @staticmethod
    def _validate_link(
        workspace: Workspace, workspace_id: str, source: Page, to_id: Any, role: Any
    ) -> str:
        """Validate adding a typed edge `source --role--> to_id`, returning the cleaned role. Rejects a
        missing target, an archived source, a self-link, an empty role, and a duplicate (to, role) edge.
        The target MAY be archived - references still resolve. Shared by `link_page` and the universal
        `addLink` page command, and it reads `source.links` directly so it is correct against the working
        copy mid-batch. The caller is responsible for the source existing."""
        if to_id not in workspace.pages:
            raise NotFoundError(f"Page '{to_id}' not found in workspace '{workspace_id}'.")
        if source.archived:
            raise IllegalCommandError(f"Page '{source.id}' is archived; unarchive it before linking.")
        role = role.strip() if isinstance(role, str) else role
        if source.id == to_id:
            raise ValidationError(f"A page cannot link to itself ('{source.id}').")
        if not role:
            raise ValidationError("Link 'role' must be a non-empty string.")
        if any(link["to"] == to_id and link["role"] == role for link in source.links):
            raise ConflictError(f"A '{role}' link from '{source.id}' to '{to_id}' already exists.")
        return role

    def link_page(
        self, workspace_id: str, from_id: str, to_id: str, role: str
    ) -> tuple[Page, list[dict[str, Any]]]:
        """Add a directed typed edge `from_id --role--> to_id` (a reference beyond the parent/child
        tree), stored on the source page. Rejects a missing endpoint, an archived source, a self-link,
        an empty role, and a duplicate (to, role) edge. The target MAY be archived - references still
        resolve. Returns the source page and its resulting outgoing links.
        """
        role = role.strip()
        with self._transaction_lock_for(workspace_id):
            workspace = self.load_workspace(workspace_id)
            source = workspace.get_page(from_id)
            if source is None:
                raise NotFoundError(f"Page '{from_id}' not found in workspace '{workspace_id}'.")
            role = self._validate_link(workspace, workspace_id, source, to_id, role)
            source.links.append({"to": to_id, "role": role})
            self._touch_and_save(workspace)
            return source, list(source.links)

    def unlink_page(
        self, workspace_id: str, from_id: str, to_id: str, role: str
    ) -> tuple[Page, list[dict[str, Any]]]:
        """Remove the directed edge `from_id --role--> to_id`. Rejects a missing endpoint, an archived
        source, and an edge that isn't present. Returns the source page and its resulting links.
        """
        role = role.strip()
        with self._transaction_lock_for(workspace_id):
            workspace = self.load_workspace(workspace_id)
            source = self._link_source(workspace, workspace_id, from_id, to_id)
            edge = next(
                (link for link in source.links if link["to"] == to_id and link["role"] == role), None
            )
            if edge is None:
                raise NotFoundError(f"No '{role}' link from '{from_id}' to '{to_id}' to remove.")
            source.links.remove(edge)
            self._touch_and_save(workspace)
            return source, list(source.links)

    def _set_workspace_status(self, workspace_id: str, status: str) -> Workspace:
        with self._transaction_lock_for(workspace_id):
            workspace = self.load_workspace(workspace_id)
            workspace.status = status
            self._touch_and_save(workspace)
            return workspace

    def archive_workspace(self, workspace_id: str) -> Workspace:
        return self._set_workspace_status(workspace_id, "archived")

    def unarchive_workspace(self, workspace_id: str) -> Workspace:
        return self._set_workspace_status(workspace_id, "active")

    # --- helpers -------------------------------------------------------------
    def _create_auto_children(
        self, workspace: Workspace, parent: Page, parent_type: PageType
    ) -> list[Page]:
        """Mint each `auto_children` page type as a pinned child of `parent`, same transaction."""
        children: list[Page] = []
        for spec in parent_type.auto_children:
            child_type = get_page_type(spec.type)
            if child_type is None:
                raise PastaError(
                    f"Page type '{parent_type.tag}' declares unknown auto-child '{spec.type}'."
                )
            child = commands.create_page(child_type, child_type.name, parent.id, self._id_factory)
            child.status_revision_token = self._revision_factory()
            workspace.pages[child.id] = child
            parent.child_ids.append(child.id)
            children.append(child)
        return children

    @staticmethod
    def _resolve_ref(workspace: Workspace, page: Page, ref: RefCheck, ref_value: Any,
                     command_name: str) -> None:
        """Enforce one cross-page ref: the referenced id must exist on the target page.

        A missing/None value is left to `apply_command`'s arg validation; a present-but-dangling
        id aborts here before anything is written. Shared by the command-level check and the
        per-block one, so the rule and its message live once.
        """
        if ref_value is None:
            return
        target = workspace.pages.get(page.parent_id) if page.parent_id and ref.scope == "parent" else None
        candidates = (target.sections.get(ref.section, {}).get(ref.field, []) if target else [])
        if ref_value not in {element.get("id") for element in candidates}:
            where = f"{ref.scope} page's {ref.section}.{ref.field}"
            raise ValidationError(
                f"Command '{command_name}': '{ref.arg}={ref_value}' does not reference an " +
                f"existing element in the {where} - the commit is aborted."
            )

    @staticmethod
    def _check_ref(workspace: Workspace, page: Page, command: CommandSpec, args: dict[str, Any]) -> None:
        """Enforce a command's cross-page ref - a list add naming an element on the parent."""
        if command.ref_check is not None:
            Store._resolve_ref(workspace, page, command.ref_check,
                               args.get(command.ref_check.arg), command.name)

    @staticmethod
    def _check_block_refs(workspace: Workspace, page: Page, command: CommandSpec,
                          args: dict[str, Any]) -> None:
        """Enforce every cross-page ref carried inside a block argument.

        A block kind declares its own ref_check, because the referencing argument lives in the
        block rather than flat on the command. Covers the array add and - through a list add's
        block arguments - blocks created together with their element, which the command-level
        check could never see: it reads one scalar arg and cannot reach into an array entry.
        """
        for arg in command.args:
            if arg.content != BLOCK_ARRAY or arg.block_kinds is None:
                continue
            for entry in args.get(arg.name) or []:
                if not isinstance(entry, dict):
                    continue                  # left for the grammar validation to reject
                block = next((block for block in arg.block_kinds
                              if block.kind == entry.get("kind")), None)
                if block is not None and block.ref_check is not None:
                    Store._resolve_ref(workspace, page, block.ref_check,
                                       entry.get(block.ref_check.arg), command.name)

    @staticmethod
    def _check_inline_refs(workspace: Workspace, command: CommandSpec, args: dict[str, Any]) -> None:
        """Enforce that every inline `{ref: pageId}` in the command's rich-text args exists.

        A `blocks`-field command carries its text as inline runs; a run may be a page reference
        `{ref: <pageId>}`. The run *grammar* is validated in the pure core, but a ref's *existence*
        is cross-page - so, like `_check_ref`, it is enforced here before anything is written. A
        dangling page-ref (referenced page absent from this workspace) aborts the whole commit. A
        ref to an archived page resolves: archived pages remain in `workspace.pages`.
        """
        for arg in command.args:
            if arg.content is None:
                continue
            for ref_id in collect_ref_ids(arg.content, args.get(arg.name), arg.block_kinds):
                if ref_id not in workspace.pages:
                    raise ValidationError(
                        f"Command '{command.name}': inline reference '{ref_id}' does not match " +
                        f"any page in this workspace - the commit is aborted."
                    )

    @staticmethod
    def _first_guard_failure(workspace: Workspace, page: Page, command: CommandSpec) -> str | None:
        """The message of the first cross-page guard `command` fails on `page`, or None if all pass.

        Checks the command's child-state guards (looking down at `page`'s children) and then its
        parent-state guards (looking up at `page`'s parent) - the two halves below, in that order.
        """
        return (Store._child_guard_failure(workspace, page, command)
                or Store._parent_guard_failure(workspace, page, command))

    @staticmethod
    def _child_guard_failure(workspace: Workspace, page: Page, command: CommandSpec) -> str | None:
        """The message of the first CHILD-state guard `command` fails on `page`, or None.

        Looks DOWN at `page`'s children. A failure says other pages' work is unfinished; it does not
        make authoring on THIS page premature, so it never suppresses a field setter.
        """
        for guard in command.guards:
            for child_id in page.child_ids:
                child = workspace.pages.get(child_id)
                if child is None or child.type != guard.child_type:
                    continue
                if guard.section is None or guard.field is None:
                    # page-status form: the child page's own status must be allowed
                    if child.status not in guard.allowed:
                        return (f"{guard.message} ('{child.id}' is '{child.status}')")
                    continue
                for element in child.sections.get(guard.section, {}).get(guard.field, []):
                    if element.get("status") not in guard.allowed:
                        return (f"{guard.message} ('{child.id}' has an item in "
                                f"status '{element.get('status')}')")
        return None

    @staticmethod
    def _parent_guard_failure(workspace: Workspace, page: Page, command: CommandSpec) -> str | None:
        """The message of the first PARENT-state guard `command` fails on `page`, or None.

        Looks UP at `page`'s parent. A failure says this page's stage has not been unlocked yet -
        nothing authored HERE can clear it - so `next_actions` also withholds the field setters that
        would only serve this transition (see `commands.field_setter_edges`).
        """
        for parent_guard in command.parent_guards:
            parent = workspace.pages.get(page.parent_id) if page.parent_id else None
            # Only constrain a page that actually hangs under a parent of the guarded type; a page
            # with no parent, or a parent of another type, is unconstrained.
            if parent is None or parent.type != parent_guard.parent_type:
                continue
            if parent.status not in parent_guard.required_statuses:
                return f"{parent_guard.message} ('{parent.id}' is '{parent.status}')"
        return None

    @staticmethod
    def _check_guards(workspace: Workspace, page: Page, command: CommandSpec) -> None:
        """Enforce a transition's child-state guards (e.g. `ship` needs all steps done)."""
        failure = Store._first_guard_failure(workspace, page, command)
        if failure is not None:
            raise IllegalCommandError(f"Cannot {command.name}: {failure}.")

    @staticmethod
    def _check_link(workspace: Workspace, workspace_id: str, page: Page, command: CommandSpec,
                    args: dict[str, Any]) -> None:
        """Cross-page precheck for the universal `addLink` command: validate the outgoing edge against
        the live workspace (and the working copy's links) before the pure core appends it, so it obeys
        exactly the same rules as the top-level `link` tool. A no-op for any other command."""
        if command.kind != ADD_LINK:
            return
        _ = Store._validate_link(workspace, workspace_id, page, args.get("toId"), args.get("role"))
