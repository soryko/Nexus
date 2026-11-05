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
