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
        page.wait_for_function("document.getElementById('status').textContent.includes('active memories')")
        assert page.locator("#status").inner_text() == "2 active memories"
        assert sorted(page.locator("#groups li").all_inner_texts()) == ["decision: 1", "observation: 1"]
        assert "#k=" not in page.url and server.launch_secret not in page.url
        assert server.launch_secret not in page.content()


def test_a_page_without_the_secret_shows_nothing_from_the_project(enrolled, browser_page) -> None:
    enrolled.record("Private project text", "k-p", kind="decision")
    with _served(enrolled) as server:
        page = browser_page
        page.goto(f"http://127.0.0.1:{server.port}/")
        page.wait_for_function("document.getElementById('status').textContent.includes('Relaunch')")
        assert "Private project text" not in page.content()
        assert page.locator("#groups li").count() == 0
