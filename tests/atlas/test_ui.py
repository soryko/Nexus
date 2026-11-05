"""Browser tests for the Atlas viewer.

Deselected by default (``-m 'not ui'``) so a plain ``pytest`` needs no browser tooling. The CI
``ui`` job installs the ``atlas-ui`` group and Chromium, selects ``-m ui``, and fails on any
skip: a skipped browser test is not a passing one.
"""
from __future__ import annotations

import os
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from nexus_memory.atlas.binding import open_binding
from nexus_memory.atlas.server import AtlasServer

pytestmark = pytest.mark.ui

# The enrolled-project fixture points HOME at a disposable directory, and Playwright looks for its
# browsers under HOME. Resolve the real cache now, at collection, before any fixture runs.
if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
    _cache = "Library/Caches" if sys.platform == "darwin" else ".cache"
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(Path.home() / _cache / "ms-playwright")


def expect(locator):
    """Playwright's auto-waiting assertion, imported late so collection works without Playwright."""
    return pytest.importorskip("playwright.sync_api").expect(locator)


@contextmanager
def _served(enrolled):
    server = AtlasServer(open_binding(enrolled.checkout, enrolled.resolve))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.fixture
def browser_page():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(5000)
        yield page
        browser.close()


def test_launch_secret_signs_in_is_stripped_and_the_overview_is_shown(enrolled, browser_page) -> None:
    enrolled.record("A decision about queues", "k-d", kind="decision")
    with _served(enrolled) as server:
        page = browser_page
        page.goto(f"http://127.0.0.1:{server.port}/#k={server.launch_secret}")
        expect(page.locator('#status')).to_contain_text('active memories')
        assert page.locator("#status").inner_text() == "2 active memories"
        assert sorted(page.locator("#groups li").all_inner_texts()) == ["decision: 1", "observation: 1"]
        assert "#k=" not in page.url and server.launch_secret not in page.url
        assert server.launch_secret not in page.content()


def test_a_page_without_the_secret_shows_nothing_from_the_project(enrolled, browser_page) -> None:
    enrolled.record("Private project text", "k-p", kind="decision")
    with _served(enrolled) as server:
        page = browser_page
        page.goto(f"http://127.0.0.1:{server.port}/")
        expect(page.locator('#status')).to_contain_text('Relaunch')
        assert "Private project text" not in page.content()
        assert page.locator("#groups li").count() == 0


# ---- 5b: overview, search and list ---------------------------------------------------------------

def _corpus(enrolled) -> dict[str, str]:
    ids = {}
    for n in range(45):
        ids[f"note{n:02d}"] = enrolled.record(f"Caption note {n:02d} about retention", f"k-n{n}", kind="observation",
                                              tags=["queue"] if n % 2 == 0 else [])
    ids["unlinked"] = enrolled.record("Blank slate procedure with no tags or links", "k-u", kind="procedure")
    ids["hostile"] = enrolled.record("<img src=x onerror=window.pwned=1><script>window.pwned=1</script>",
                                     "k-hostile", kind="decision")
    return ids


def _open(page, server):
    page.goto(f"http://127.0.0.1:{server.port}/#k={server.launch_secret}")
    expect(page.locator('#results li').first).to_be_visible()


def test_groups_show_exact_counts_and_open_a_paged_list(enrolled, browser_page) -> None:
    _corpus(enrolled)
    with _served(enrolled) as server:
        page = browser_page
        _open(page, server)
        assert page.locator("#status").inner_text() == "48 active memories"        # 45 + 2 + the fixture's own
        groups = sorted(page.locator("#groups li").all_inner_texts())
        assert groups == ["decision: 1", "observation: 46", "procedure: 1"]
        sizes = [float(page.locator("#groups button").nth(i).evaluate("e => parseFloat(getComputedStyle(e).fontSize)"))
                 for i in range(3)]
        assert max(sizes) / min(sizes) <= 1.7                                        # the visual range is capped
        page.get_by_role("button", name="observation: 46").click()
        expect(page.locator('#count')).to_contain_text('of 46 matching')
        assert page.locator("#results li").count() == 20
        assert "Showing 1–20 of 46 matching (48 in this project)." == page.locator("#count").inner_text()
        page.get_by_role("button", name="Next page").click()
        expect(page.locator('#page')).to_have_text('Page 2')
        page.get_by_role("button", name="Next page").click()
        expect(page.locator('#page')).to_have_text('Page 3')
        assert page.locator("#results li").count() == 6
        assert page.get_by_role("button", name="Next page").is_disabled()
        page.get_by_role("button", name="Previous page").click()
        expect(page.locator('#page')).to_have_text('Page 2')


def test_search_beyond_page_one_and_open_a_memory_by_keyboard_alone(enrolled, browser_page) -> None:
    ids = _corpus(enrolled)
    with _served(enrolled) as server:
        page = browser_page
        _open(page, server)
        page.locator("#q").focus()
        page.keyboard.type("retention")
        page.keyboard.press("Enter")
        expect(page.locator('#count')).to_contain_text('of 45 matching')
        for expected in ("Page 2", "Page 3"):
            page.locator("#next").focus()
            page.keyboard.press("Enter")
            expect(page.locator('#page')).to_have_text(expected)
        last = page.locator("#results li button").last
        last.focus()
        page.keyboard.press("Enter")
        expect(page.locator('#memory')).to_be_visible()
        assert page.evaluate("document.activeElement.id") == "inspector"
        body = page.locator("#memory-body").inner_text()
        assert body.startswith("Caption note ") and body.endswith("about retention")
        assert page.locator("#memory-title").inner_text() == body
        assert ids["note00"] in page.locator("#memory-meta").inner_text() or "Memory ID" in page.locator("#memory-meta").inner_text()


def test_blank_unlinked_and_hostile_memories_are_usable_and_inert(enrolled, browser_page) -> None:
    ids = _corpus(enrolled)
    with _served(enrolled) as server:
        page = browser_page
        _open(page, server)
        page.get_by_role("button", name="procedure: 1").click()
        expect(page.locator('#count')).to_contain_text('of 1 matching')
        page.locator("#results li button").first.click()
        expect(page.locator('#memory')).to_be_visible()
        assert page.locator("#memory-body").inner_text() == "Blank slate procedure with no tags or links"
        assert "none" in page.locator("#memory-meta").inner_text()
        page.get_by_role("button", name="Reset filters").click()
        expect(page.locator('#results li').nth(1)).to_be_visible()
        page.get_by_role("button", name="decision: 1").click()
        expect(page.locator('#count')).to_contain_text('of 1 matching')
        page.locator("#results li button").first.click()
        expect(page.locator('#memory-body')).to_contain_text('onerror')
        assert page.evaluate("window.pwned") is None
        assert page.locator("#memory-body img, #memory-body script, #results img, #results script").count() == 0
        assert "<script>" in page.locator("#memory-body").inner_text()


def test_filters_are_visible_and_one_reset_clears_them(enrolled, browser_page) -> None:
    _corpus(enrolled)
    with _served(enrolled) as server:
        page = browser_page
        _open(page, server)
        assert page.locator("#reset").is_hidden()
        assert page.locator("#filters").inner_text() == "No filters: newest memories first."
        page.locator("#tag").fill("queue")
        page.locator("#q").fill("retention")
        page.locator("#kinds input[value=observation]").check()
        page.get_by_role("button", name="Search").click()
        expect(page.locator('#count')).to_contain_text('of 23 matching')
        text = page.locator("#filters").inner_text()
        assert "text “retention”" in text and "tag “queue”" in text and "kind observation" in text
        page.get_by_role("button", name="Reset filters").click()
        expect(page.locator('#count')).to_contain_text('matching (48 in this project)')
        assert page.locator("#filters").inner_text() == "No filters: newest memories first."
        assert page.locator("#q").input_value() == "" and page.locator("#tag").input_value() == ""
        assert page.locator("#kinds input:checked").count() == 0
        assert "of 48 matching" in page.locator("#count").inner_text()


# ---- 5c: graph, budgets and placement --------------------------------------------------------------

import sqlite3 as _sqlite3

_REPO, _COMMIT = "repo-one", "a" * 40


def _cite(enrolled, memory_id: str, path: str) -> None:
    with _sqlite3.connect(enrolled.database) as db:
        revision = db.execute("SELECT current_revision_id FROM memories WHERE memory_id=?", (memory_id,)).fetchone()[0]
        db.execute("INSERT OR IGNORE INTO repositories VALUES(?,?,?,?,?,?)",
                   (enrolled.project_id, "local", _REPO, "sha1", None, "2026-11-05T00:00:00Z"))
        db.execute("INSERT INTO revision_references VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (enrolled.project_id, "local", memory_id, revision, _REPO, _COMMIT, path, "c" * 40, "blob",
                    "100644", "2026-11-05T00:00:00Z"))


def _dense(enrolled) -> str:
    """One memory citing 120 files, one of which (src/hub.py) is cited by 120 other memories."""
    focus = enrolled.record("unique-m-token the memory under study", "k-m", kind="decision")
    _cite(enrolled, focus, "src/hub.py")
    for n in range(119):
        _cite(enrolled, focus, f"src/f{n:03d}.py")
    for n in range(120):
        _cite(enrolled, enrolled.record(f"Citer {n:03d} of the hub file", f"k-c{n}", kind="observation"), "src/hub.py")
    return focus


def _positions(page) -> dict[str, tuple[str, str]]:
    return {n.get_attribute("data-node-id"): (n.get_attribute("data-x"), n.get_attribute("data-y"))
            for n in page.locator("[data-node-id]").all()}


def _open_focus(page, server) -> None:
    _open(page, server)
    page.locator("#q").fill("unique-m-token")
    page.get_by_role("button", name="Search").click()
    expect(page.locator("#count")).to_contain_text("1 matching")
    page.locator("#results li button").first.click()
    expect(page.locator("#graph")).to_be_visible()
    expect(page.locator("[data-node-id]").nth(1)).to_be_visible()


def test_neighborhood_starts_small_and_labels_what_is_omitted(enrolled, browser_page) -> None:
    _dense(enrolled)
    with _served(enrolled) as server:
        page = browser_page
        _open_focus(page, server)
        assert page.locator("[data-node-id]").count() == 26                      # the focus and 25 neighbors
        expect(page.locator("#graph-lists h3", has_text="files cited")).to_have_text("25 of 120 files cited")
        expect(page.locator("#graph-limits")).to_contain_text("Showing 26 of at most 100 nodes and 25 of 150 edges.")
        assert page.locator("[data-edge]").count() == 25
        assert page.locator(".node.focus").count() == 1


def test_repeated_expansion_never_leaves_the_budget_and_never_moves_a_node(enrolled, browser_page) -> None:
    _dense(enrolled)
    with _served(enrolled) as server:
        page = browser_page
        _open_focus(page, server)
        seen = _positions(page)
        for _ in range(8):
            button = page.get_by_role("button", name="Show more files cited")
            if button.count() == 0:
                break
            button.click()
            page.wait_for_timeout(150)
            assert page.locator("[data-node-id]").count() <= 100 and page.locator("[data-edge]").count() <= 150
            now = _positions(page)
            assert all(now[i] == p for i, p in seen.items()), "an existing node moved"
            seen = now
        assert page.locator("[data-node-id]").count() == 100
        expect(page.locator("#graph-limits")).to_contain_text("The graph limit is reached")
        assert page.get_by_role("button", name="Show more files cited").count() == 0
        # the lists still reach every item the graph could not draw
        assert page.locator("#graph-lists li").count() >= 100


def test_a_dense_file_hub_fills_the_budget_and_choosing_an_item_still_recenters(enrolled, browser_page) -> None:
    _dense(enrolled)
    with _served(enrolled) as server:
        page = browser_page
        _open_focus(page, server)
        page.locator("#graph-lists li", has_text="src/hub.py").get_by_role("button", name="Expand").click()
        expect(page.locator("#graph-lists h3", has_text="citing memories of src/hub.py")).to_contain_text("of 121")
        for _ in range(6):
            more = page.get_by_role("button", name="Show more citing memories")
            if more.count() == 0:
                break
            more.click()
            page.wait_for_timeout(150)
            assert page.locator("[data-node-id]").count() <= 100
        assert page.locator("[data-node-id]").count() == 100
        before = page.locator(".node.focus").get_attribute("data-node-id")
        page.locator("#graph-lists section", has_text="citing memories of src/hub.py").get_by_role("button", name="Open").last.click()
        expect(page.locator("#memory-body")).to_contain_text("Citer")
        expect(page.locator(".node.focus")).not_to_have_attribute("data-node-id", before)
        assert page.locator("[data-node-id]").count() <= 100 and page.locator("[data-edge]").count() <= 150


def test_pan_zoom_fit_and_progressive_labels(enrolled, browser_page) -> None:
    _dense(enrolled)
    with _served(enrolled) as server:
        page = browser_page
        _open_focus(page, server)
        world = page.locator("#world")
        other = page.locator(".node:not(.focus) text").first
        assert other.evaluate("e => getComputedStyle(e).display") == "none"       # names wait for focus, hover or close zoom
        for _ in range(3):
            page.get_by_role("button", name="Zoom in").click()
        assert float(world.get_attribute("data-k")) > 1.5
        assert other.evaluate("e => getComputedStyle(e).display") != "none"
        page.get_by_role("button", name="Fit").click()
        assert float(world.get_attribute("data-k")) <= 2
        start = world.get_attribute("transform")
        page.locator("#graph-canvas").focus()
        page.keyboard.press("ArrowLeft")
        assert world.get_attribute("transform") != start
        page.keyboard.press("0")
        page.get_by_role("button", name="Back to selection").click()
        assert page.locator("[data-node-id]").count() == 26                          # camera moves never change content


def test_graph_labels_are_text_and_hostile_names_stay_inert(enrolled, browser_page) -> None:
    focus = enrolled.record("unique-m-token study", "k-m", kind="decision")
    hostile = enrolled.record("<img src=x onerror=window.pwned=1><script>window.pwned=1</script>", "k-h", kind="observation")
    for memory in (focus, hostile):
        _cite(enrolled, memory, "src/hub.py")
    with _served(enrolled) as server:
        page = browser_page
        _open_focus(page, server)
        page.locator("#graph-lists li", has_text="src/hub.py").get_by_role("button", name="Expand").click()
        expect(page.locator("#graph-lists section", has_text="citing memories of src/hub.py")).to_contain_text("<img src=x")
        assert page.evaluate("window.pwned") is None
        assert page.locator("#graph svg img, #graph svg script, #graph-lists img, #graph-lists script").count() == 0
        labels = " ".join(page.locator(".node text").all_text_contents())
        assert "<img" in labels
