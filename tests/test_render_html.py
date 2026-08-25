"""Unit tests for the structured HTML renderer (src.render_html)."""

from src.commands import apply_command, create_page
from src.model import Page
from src.pagetypes import LIST, SCALAR, FieldSpec, FSMSpec, PageType, get_page_type
from src.render import RefContext
from src.render_html import (_children_html, _field_html, _list_html, _references_html,
                             _text_html, element_view, render_page_html)

CHILD = get_page_type("test-child")
FIELDS = get_page_type("test-fields")
LIFECYCLE = get_page_type("test-lifecycle")
BLOCKS_TYPE = get_page_type("test-blocks")


def _spec(page_type, section, field):
    return page_type.field_spec(section, field)


def _counter():
    state = {"n": 0}

    def factory(prefix: str) -> str:
        state["n"] += 1
        return f"{prefix}:{state['n']}" if prefix else f"el{state['n']}"

    return factory


def _context(show_archived=False, archived=()):
    return RefContext(
        workspace_id="ws:demo",
        titles={"a:1": "Alpha", "b:2": "Beta"},
        types={"a:1": "test-fields", "b:2": "test-fields"},
        statuses={"a:1": "active", "b:2": "active"},
        show_archived=show_archived,
        archived_ids=frozenset(archived),
        escape_plain_text=True,
    )


def _page(**kwargs):
    base = dict(id="p:1", type="test-fields", title="P", status="active")
    base.update(kwargs)
    return Page(**base)


def test_element_view_heads_on_the_declared_name_field():
    spec = _spec(LIFECYCLE, "parts", "items")      # element_fields ('name',)
    view = element_view({"id": "c1", "name": "src/render_html.py"}, 1, spec)
    assert view.title == "src/render_html.py"
    assert view.index == 1 and view.element_id == "c1"
    assert view.rows == ()                          # the heading field is never also a row


def test_element_view_gives_no_heading_when_the_type_declares_none():
    spec = _spec(FIELDS, "items", "items")          # element_fields ('text', 'note', 'flagged')
    view = element_view({"id": "el1", "text": "Short enough to look like a title",
                         "note": "a note"}, 1, spec)
    assert view.title is None
    assert view.rows == (("text", "Short enough to look like a title"),
                         ("note", "a note"), ("flagged", None))


def test_element_view_shape_does_not_move_with_the_length_of_a_value():
    # The defect this rule replaced: a short value was promoted to a heading and disappeared from
    # the rows, so two elements of ONE list rendered with different structure.
    spec = _spec(FIELDS, "items", "items")
    short = element_view({"id": "el2", "text": "short"}, 1, spec)
    sprawling = element_view({"id": "el3", "text": "x" * 400}, 2, spec)
    assert short.title is sprawling.title is None
    assert [key for key, _ in short.rows] == [key for key, _ in sprawling.rows]


def test_element_view_heads_on_a_value_no_length_rule_would_accept():
    # 400 characters and a newline: neither length nor shape is consulted, only the declaration.
    spec = _spec(LIFECYCLE, "parts", "items")
    value = "line one" + chr(10) + "y" * 400
    view = element_view({"id": "c2", "name": value}, 1, spec)
    assert view.title == value
    assert view.rows == ()


def test_element_view_empty_heading_value_leaves_the_ordinal_and_no_row():
    spec = _spec(LIFECYCLE, "parts", "items")
    view = element_view({"id": "c3", "name": "   "}, 1, spec)
    assert view.title is None
    assert view.rows == ()


def test_element_view_never_heads_on_an_undeclared_field():
    # (text, answer, needsHuman, status): no declared heading field, so nothing is promoted - in
    # particular not `needsHuman`, which a scan for the first short value would title "True".
    spec = _spec(LIFECYCLE, "questions", "items")
    view = element_view({"id": "q1", "text": "y" * 400, "answer": "",
                         "needsHuman": True, "status": "open"}, 1, spec)
    assert view.title is None
    assert view.rows[0] == ("text", "y" * 400)
    assert ("needsHuman", "True") in view.rows


def test_element_view_reads_status_and_checkbox_from_the_element_fsm():
    spec = _spec(CHILD, "steps", "items")           # element_fields ('text', 'status'), todo/done
    done = element_view({"id": "s1", "text": "Write it", "status": "done"}, 1, spec)
    todo = element_view({"id": "s2", "text": "Ship it", "status": "todo"}, 2, spec)
    assert (done.check, done.status) == ("done", "done")
    assert (todo.check, todo.status) == ("todo", "todo")
    # status is a chip, never a row; `text` is a row because the type declares no heading
    assert done.rows == (("text", "Write it"),) and todo.rows == (("text", "Ship it"),)


def test_element_view_keeps_an_undeclared_key_after_the_declared_ones():
    spec = _spec(FIELDS, "items", "items")
    view = element_view({"id": "el4", "text": "t", "note": "n", "extra": "kept"}, 1, spec)
    assert view.rows == (("text", "t"), ("note", "n"), ("flagged", None), ("extra", "kept"))


def test_text_html_escapes_and_splits_on_blank_lines():
    out = _text_html("first <b> line\nstill first\n\nsecond & last")
    assert out == "<p>first &lt;b&gt; line still first</p><p>second &amp; last</p>"


def test_text_html_of_blank_input_is_empty():
    assert _text_html("   \n\n ") == ""


def test_scalar_field_keeps_its_label_when_empty():
    spec = _spec(FIELDS, "basics", "label")
    filled = _field_html(spec, "alpha", None)
    empty = _field_html(spec, None, None)
    assert "<dt>label</dt><dd>alpha</dd>" in filled
    assert "<dt>label</dt>" in empty and 'class="empty"' in empty and "None." in empty


def test_scalar_url_field_renders_a_clickable_link():
    spec = FieldSpec(key="url", kind=SCALAR)
    out = _field_html(spec, "https://example.com/pr/1", None)
    assert ('<dt>url</dt><dd><a href="https://example.com/pr/1" target="_blank" '
            'rel="noopener noreferrer">https://example.com/pr/1</a></dd>') in out


def test_empty_scalar_url_field_keeps_the_none_fallback():
    spec = FieldSpec(key="url", kind=SCALAR)
    out = _field_html(spec, "", None)
    assert "<dt>url</dt>" in out and 'class="empty"' in out and "None." in out
    assert "<a " not in out


def test_scalar_url_value_is_escaped_in_both_the_href_and_the_text():
    spec = FieldSpec(key="url", kind=SCALAR)
    out = _field_html(spec, 'https://x/?a=1&b=2"><script>', None)
    assert "&amp;" in out and "&quot;" in out and "&lt;script&gt;" in out
    assert "<script>" not in out


def test_a_scalar_field_not_named_url_stays_plain_text():
    spec = FieldSpec(key="label", kind=SCALAR)
    out = _field_html(spec, "https://example.com", None)
    assert "<dt>label</dt><dd>https://example.com</dd>" in out
    assert "<a " not in out


def test_element_row_named_url_renders_a_clickable_link():
    spec = FieldSpec(key="commits", kind=LIST, element_fields=("sha", "url"))
    out = _list_html(spec, [{"id": "e1", "sha": "abc123", "url": "https://example.com/c/abc"}], None)
    assert ('<dt>url</dt><dd><p><a href="https://example.com/c/abc" target="_blank" '
            'rel="noopener noreferrer">https://example.com/c/abc</a></p></dd>') in out
    assert "<dt>sha</dt><dd><p>abc123</p></dd>" in out       # a non-url row stays plain text


def test_element_row_named_url_still_resolves_a_page_id_to_a_titled_link():
    spec = FieldSpec(key="commits", kind=LIST, element_fields=("sha", "url"))
    out = _list_html(spec, [{"id": "e1", "sha": "abc", "url": "a:1"}], _context())
    assert '<a href="/ws:demo/page/a:1">Alpha</a>' in out       # page-id resolution preserved


def test_prose_field_renders_paragraphs_and_an_empty_fallback():
    spec = _spec(FIELDS, "basics", "body")
    assert "<p>The body.</p>" in _field_html(spec, "The body.", None)
    assert '<p class="empty">None.</p>' in _field_html(spec, "", None)


def test_plain_text_leaves_are_html_escaped_not_markdown_rendered():
    spec = _spec(FIELDS, "basics", "body")
    out = _field_html(spec, "- not a list <script>x</script>", None)
    assert "&lt;script&gt;" in out and "<script>" not in out
    assert "<ul>" not in out and "<li>" not in out


def test_list_html_gives_each_element_an_ordinal_title_and_rows():
    spec = _spec(FIELDS, "items", "items")
    out = _list_html(spec, [
        {"id": "el1", "text": "First item", "note": "a note"},
        {"id": "el2", "text": "Second item"},
    ], None)
    assert out.count('<li class="element"') == 2
    assert 'id="element-el1"' in out and 'id="element-el2"' in out
    # test-fields declares no heading field, so `text` is a labelled row on every element
    assert '<h3 class="element-title">' not in out
    assert "<dt>text</dt><dd><p>First item</p></dd>" in out
    assert "<dt>note</dt><dd><p>a note</p></dd>" in out
    assert '<dd class="empty">&mdash;</dd>' in out       # flagged: declared, empty


def test_list_html_ordinal_links_to_the_element_anchor():
    spec = _spec(FIELDS, "items", "items")
    out = _list_html(spec, [{"id": "el1", "text": "First"}, {"id": "el2", "text": "Second"}], None)
    assert '<a class="element-index" href="#element-el1">1</a>' in out
    assert '<a class="element-index" href="#element-el2">2</a>' in out


def test_list_html_shows_a_checkbox_and_status_chip_when_the_field_has_an_element_fsm():
    spec = _spec(CHILD, "steps", "items")
    out = _list_html(spec, [{"id": "s1", "text": "Write it", "status": "done"}], None)
    assert 'data-check="done"' in out
    assert '<span class="element-status">done</span>' in out


def test_list_html_omits_the_checkbox_for_a_field_with_no_element_fsm():
    spec = _spec(FIELDS, "items", "items")
    out = _list_html(spec, [{"id": "el1", "text": "Plain"}], None)
    assert "element-check" not in out and "element-status" not in out


def test_list_html_escapes_element_text():
    spec = _spec(FIELDS, "items", "items")
    out = _list_html(spec, [{"id": "el1", "text": "a <b> & c"}], None)
    assert "a &lt;b&gt; &amp; c" in out and "<b>" not in out


def test_list_html_omits_the_anchor_when_an_element_has_no_id():
    spec = _spec(FIELDS, "items", "items")
    out = _list_html(spec, [{"text": "No id"}], None)
    assert 'id="element-"' not in out and '<li class="element">' in out
    assert '<span class="element-index">1</span>' in out      # a plain ordinal, not a link


def test_blocks_field_keeps_the_markdown_to_html_pipeline():
    factory = _counter()
    page = create_page(BLOCKS_TYPE, "Doc", None, factory)
    page = apply_command(page, BLOCKS_TYPE, "addBody",
                         {"blocks": [{"kind": "heading", "level": 2, "inlines": ["Overview"]}]}, factory).page
    page = apply_command(page, BLOCKS_TYPE, "addBody",
                         {"blocks": [{"kind": "code", "language": "py", "source": "x = 1"}]}, factory).page
    spec = _spec(BLOCKS_TYPE, "body", "body")
    out = _field_html(spec, page.sections["body"]["body"], None)
    assert "<h2>Overview</h2>" in out
    assert "<code" in out and "x = 1" in out


def test_empty_blocks_field_renders_the_none_fallback():
    spec = _spec(BLOCKS_TYPE, "body", "body")
    assert '<p class="empty">None.</p>' in _field_html(spec, [], None)


def test_children_html_links_each_child_with_its_type_and_status():
    out = _children_html(_page(child_ids=["a:1"]), _context())
    assert '<a href="/ws:demo/page/a:1">Alpha</a>' in out
    assert '<span class="link-type">test-fields&nbsp;·</span>' in out
    assert '<span class="link-status">active</span>' in out


def test_children_html_hides_an_archived_child_then_flags_it_when_shown():
    page = _page(child_ids=["a:1", "b:2"])
    hidden = _children_html(page, _context(archived=["b:2"]))
    assert "Beta" not in hidden and "Alpha" in hidden
    shown = _children_html(page, _context(show_archived=True, archived=["b:2"]))
    assert "Beta" in shown and 'class="archived-flag"' in shown
    assert "/page/b:2?archived=true" in shown


def test_children_html_is_the_none_fallback_when_there_are_none():
    assert '<p class="empty">None.</p>' in _children_html(_page(), _context())


def test_children_html_falls_back_to_a_bare_id_without_a_context():
    out = _children_html(_page(child_ids=["a:1"]), None)
    assert "a:1" in out and "<a " not in out


def test_references_html_carries_the_edge_role():
    page = _page(links=[{"to": "a:1", "role": "depends-on"}])
    out = _references_html(page, _context())
    assert '<a href="/ws:demo/page/a:1">Alpha</a>' in out
    assert '<span class="link-role">depends-on</span>' in out


def test_render_page_html_emits_header_sections_and_both_link_lists():
    factory = _counter()
    page = create_page(FIELDS, "Page title", None, factory)
    page = apply_command(page, FIELDS, "setBody", {"text": "The body."}, factory).page
    page = apply_command(page, FIELDS, "addItem", {"text": "Item text."}, factory).page
    out = render_page_html(page, FIELDS)
    assert '<h1 class="page-title">Page title</h1>' in out
    assert '<span class="page-type">test-fields&nbsp;·&nbsp;</span>' in out
    assert '<span class="page-status">active</span>' in out
    assert 'id="section-basics"' in out and 'id="section-items"' in out
    assert 'id="section-references"' in out and 'id="section-children"' in out
    assert "Item text." in out and "<p>The body.</p>" in out


def test_render_page_html_shows_every_declared_section_even_when_empty():
    factory = _counter()
    page = create_page(FIELDS, "Empty", None, factory)
    out = render_page_html(page, FIELDS)
    assert "Basics" in out and "Items" in out
    assert out.count('<p class="empty">None.</p>') >= 2


def test_render_page_html_contents_strip_counts_list_elements():
    factory = _counter()
    page = create_page(FIELDS, "Counted", None, factory)
    page = apply_command(page, FIELDS, "addItem", {"text": "one"}, factory).page
    page = apply_command(page, FIELDS, "addItem", {"text": "two"}, factory).page
    out = render_page_html(page, FIELDS)
    assert '<nav class="page-contents"' in out
    assert '<a href="#section-items">Items<span class="count">2</span></a>' in out
    assert '<a href="#section-basics">Basics</a>' in out          # no badge, no list elements


def test_render_page_html_escapes_the_page_title():
    factory = _counter()
    page = create_page(FIELDS, "a <b> & c", None, factory)
    out = render_page_html(page, FIELDS)
    assert "a &lt;b&gt; &amp; c" in out and "<b>" not in out


def test_list_element_page_id_heading_becomes_a_titled_link():
    spec = _spec(LIFECYCLE, "parts", "items")                   # declares a `name` heading
    out = _list_html(spec, [{"id": "el1", "name": "a:1"}], _context())
    assert '<a href="/ws:demo/page/a:1">Alpha</a>' in out       # the title, not the raw id
    assert "a:1</h3>" not in out                                # the raw id is not the heading


def test_list_element_page_id_row_value_becomes_a_titled_link():
    spec = _spec(FIELDS, "items", "items")
    out = _list_html(spec, [{"id": "el1", "text": "Depends", "note": "b:2"}], _context())
    assert '<a href="/ws:demo/page/b:2">Beta</a>' in out


def test_list_element_value_that_is_not_a_page_id_stays_text():
    spec = _spec(FIELDS, "items", "items")
    out = _list_html(spec, [{"id": "el1", "text": "z:9", "note": "plain"}], _context())
    assert 'href="/ws:demo' not in out          # no page link; the ordinal self-link remains
    assert "z:9" in out and "plain" in out


def test_list_element_link_to_an_archived_page_is_flagged_before_the_link():
    spec = _spec(FIELDS, "items", "items")
    out = _list_html(spec, [{"id": "el1", "text": "b:2", "note": "a:1"}],
                     _context(archived=["b:2"]))
    assert "<strong class=\"archived-flag\">(A)</strong>" in out
    assert out.index("archived-flag") < out.index('<a href="/ws:demo/page/b:2"')
    # An element field is this page's own content, so it is flagged, never hidden.
    assert '<a href="/ws:demo/page/b:2">Beta</a>' in out
    assert out.count("archived-flag") == 1        # a:1 is not archived


def test_list_element_links_are_absent_without_a_context():
    spec = _spec(FIELDS, "items", "items")
    out = _list_html(spec, [{"id": "el1", "text": "a:1"}], None)
    assert "a:1" in out and '<a href="/ws:demo' not in out


def test_render_page_html_toc_is_its_child_list_alone():
    # A locally constructed page type, not a registered one: conftest puts the run in test mode,
    # which blocks resolving the production toc type but does not stop a test building its own.
    toc = PageType(tag="toc", name="Toc fixture", description="local toc for the render test",
                   sections=(), commands=(),
                   fsm=FSMSpec(name="LocalToc", initial="active", states=("active",)))
    page = Page(id="toc:1", type="toc", title="Contents", status="active", child_ids=["a:1"])
    out = render_page_html(page, toc, _context())
    assert '<h1 class="page-title">Contents</h1>' in out
    assert '<a href="/ws:demo/page/a:1">Alpha</a>' in out
    assert "section-references" not in out
    assert "section-children" not in out
    assert "page-contents" not in out


# --- block-bearing element fields --------------------------------------------
ELEMENT_BLOCKS = get_page_type("test-element-blocks")

CODE_BLOCK = {"id": "b1", "kind": "code", "language": "python", "source": "x = 1"}


def test_element_view_keeps_block_fields_out_of_the_rows():
    element = {"id": "e1", "text": "one", "snippet": [], "detail": [CODE_BLOCK], "status": "todo"}
    view = element_view(element, 1, _spec(ELEMENT_BLOCKS, "items", "items"))
    assert view.title is None                        # the type declares no heading field
    assert view.check == "todo"
    assert [label for label, _ in view.rows] == ["text"]   # blocks are never plain rows
    assert view.block_rows == (("snippet", ()), ("detail", (CODE_BLOCK,)))


def test_element_block_field_renders_through_the_markdown_pipeline():
    factory = _counter()
    page = create_page(ELEMENT_BLOCKS, "Plan", None, factory)
    added = apply_command(page, ELEMENT_BLOCKS, "addItem", {"text": "one"}, factory)
    page = apply_command(added.page, ELEMENT_BLOCKS, "addItemDetail",
                         {"itemId": added.created_id, "blocks": [{"kind": "code", "language": "python", "source": "x = 1"}]}, factory).page
    out = render_page_html(page, ELEMENT_BLOCKS, _context())
    assert '<dt>detail</dt><dd class="element-blocks">' in out
    assert "<code" in out and "x = 1" in out          # went through render_blocks + md2html
    # An empty declared block field keeps the same em-dash cell an empty plain row uses.
    assert '<dt>snippet</dt><dd class="empty">&mdash;</dd>' in out


def test_an_element_block_ref_resolves_to_a_titled_link():
    factory = _counter()
    page = create_page(ELEMENT_BLOCKS, "Plan", None, factory)
    added = apply_command(page, ELEMENT_BLOCKS, "addItem", {"text": "one"}, factory)
    page = apply_command(added.page, ELEMENT_BLOCKS, "addItemDetail",
                         {"itemId": added.created_id, "blocks": [{"kind": "paragraph", "inlines": ["see ", {"ref": "a:1"}]}]}, factory).page
    out = render_page_html(page, ELEMENT_BLOCKS, _context())
    assert '<a href="/ws:demo/page/a:1">Alpha</a>' in out
