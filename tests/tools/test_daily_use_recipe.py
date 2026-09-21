"""The documented daily-use routine, executed as a user would execute it.

`docs/daily-use.md` teaches a four-part routine -- capture, consult, verify, correct -- and
the canonical requests under `examples/daily-use/` are the exact payloads it tells a reader
to send. This module runs *those files*. It does not restate their contents: a test carrying
its own copy of the payload passes while the file a reader actually opens drifts away from
it, which is the one failure a tutorial test exists to prevent.

What this establishes is mechanical, and the boundary matters:

  * The published requests are well-formed against the live tool schemas, and the routine
    they describe -- record, find again in a later session, read in full, correct -- runs
    end to end against a real server over a real stdio transport.
  * A memory written by one process is found by a later one. Every step here opens its own
    server process and lets it close, so nothing is carried in memory between steps; the
    database on disk is the only thing that survives, which is what "a later session" means.
  * A correction replaces the current revision and the superseded one stays readable.
  * A different launch scope cannot see any of it.

What it does NOT establish: that an agent follows the routine unprompted, that consulting
the store improves a task, or that the advisory search/get budget in the guide is the right
one. Those are questions for use, not for a test. Nothing here touches a user's database,
a model endpoint, or the network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "examples" / "daily-use"
RECORD_REQUEST = EXAMPLES / "record.json"
SEARCH_REQUEST = EXAMPLES / "search.json"

# The tutorial's scope. `revise` needs a key of its own: reusing the record key would be a
# different command under the same key, which is an idempotency conflict by design.
NAMESPACE = "daily-use-recipe"
ACTOR = "local"
REVISE_KEY = "p1-tutorial-runtime-001-revised"


def _request(path: Path) -> dict:
    """The canonical request a reader is told to send, read from the file they would open."""
    assert path.exists(), (
        f"missing canonical request {path.relative_to(REPO)} -- docs/daily-use.md tells a "
        f"reader to send this file, so it has to exist and this test has to run it"
    )
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def server() -> str:
    """The `nexus-memory` beside the interpreter running the tests.

    Deliberately not skipped when absent. A skip here reads as a pass in every summary that
    counts outcomes, and the thing that would be silently unproven is the whole module.
    """
    from nexus_memory.install_check import default_server

    command = default_server(sys.executable)
    assert command.exists(), (
        f"no server at {command} -- these tests drive a real one over stdio; install the "
        f"package into the environment running pytest (`uv sync --frozen`)"
    )
    return str(command)


@pytest.fixture
def db(tmp_path: Path) -> str:
    """A database of this test's own. Never a user's, never a shared one."""
    return str(tmp_path / "daily-use.sqlite3")


def _session(server: str, db: str, calls, namespace: str = NAMESPACE) -> list[dict]:
    """One server process: start, make the calls, close. Returns the structured results.

    Each invocation is a separate process, so two invocations are two sessions in the sense
    the guide uses the word.
    """
    from nexus_memory.install_check import _attempt

    return _attempt(server, db, namespace, ACTOR, calls)


@pytest.fixture
def written(server: str, db: str) -> tuple[str, dict, dict]:
    """The canonical record request, sent once, in a process that then exits."""
    request = _request(RECORD_REQUEST)
    receipt = _session(server, db, [("record", request)])[0]
    assert receipt.get("memory_id"), f"record returned no memory_id: {receipt}"
    return db, request, receipt


class TestCanonicalRequestsAreWellFormed:
    """The published payloads match the tools they are addressed to."""

    def test_record_request_carries_the_required_fields(self) -> None:
        request = _request(RECORD_REQUEST)
        # `record` requires exactly these two; the rest of the example is illustrative.
        assert request["content"].strip(), "the example would teach recording empty text"
        assert request["idempotency_key"].strip()
        assert request["kind"] in {
            "observation", "decision", "constraint", "procedure", "failure",
        }, f"unknown kind {request['kind']!r} -- the example would be refused at the tool"

    def test_search_request_can_match_the_record_request(self) -> None:
        """The two files are a pair, and a tutorial whose search misses is worse than none."""
        record, search = _request(RECORD_REQUEST), _request(SEARCH_REQUEST)
        missing = set(search.get("tags_all", [])) - set(record.get("tags", []))
        assert not missing, (
            f"search.json filters on tags {sorted(missing)} that record.json does not set, "
            f"so the documented search cannot return the documented memory"
        )
        body = record["content"].casefold()
        assert any(term in body for term in search["query"].casefold().split()), (
            "no query term appears in the recorded body; the hit would be luck, not the lesson"
        )


class TestGuideDoesNotDriftFromTheRequests:
    """The JSON in the guide is a copy. Copies drift; this is the thing that notices.

    `docs/daily-use.md` shows the payloads inline so it can be read straight through, which
    means the file a reader opens and the file this suite executes are two artefacts that
    have to agree. Nothing else in the repository checks that they do.
    """

    @staticmethod
    def _blocks() -> list[dict]:
        guide = (REPO / "docs" / "daily-use.md").read_text(encoding="utf-8")
        out, fence = [], False
        buf: list[str] = []
        for line in guide.splitlines():
            if line.strip() == "```json":
                fence, buf = True, []
                continue
            if fence and line.strip() == "```":
                fence = False
                try:
                    out.append(json.loads("\n".join(buf)))
                except ValueError as exc:            # a malformed block is a reader's dead end
                    raise AssertionError(
                        f"a ```json block in docs/daily-use.md is not valid JSON: {exc}"
                    ) from None
                continue
            if fence:
                buf.append(line)
        assert out, "no ```json blocks found in docs/daily-use.md"
        return out

    def test_every_json_block_in_the_guide_parses(self) -> None:
        self._blocks()                                # the assertion lives in the helper

    def test_the_guide_shows_the_canonical_requests_unchanged(self) -> None:
        blocks = self._blocks()
        for name, path in (("record", RECORD_REQUEST), ("search", SEARCH_REQUEST)):
            want = _request(path)
            assert want in blocks, (
                f"docs/daily-use.md does not show examples/daily-use/{name}.json as it is on "
                f"disk. The guide and the executed request have drifted, so following the "
                f"page no longer sends what this suite proves works.\n"
                f"  on disk: {want}\n"
                f"  in guide: {[b for b in blocks if set(b) & set(want)] or blocks}"
            )


class TestRoutineSurvivesTheSession:
    """Written in one process, found and read in later ones."""

    def test_a_later_session_finds_and_reads_the_memory(
        self, server: str, written: tuple[str, dict, dict]
    ) -> None:
        db, request, receipt = written

        # A second process. The first is gone; only the file on disk connects them.
        hits = _session(server, db, [("search", _request(SEARCH_REQUEST))])[0]["hits"]
        assert [h["memory_id"] for h in hits] == [receipt["memory_id"]], (
            f"the documented search did not return exactly the documented memory: {hits}"
        )

        # A third process, to read the body in full. Excerpts are not the content.
        memory = _session(server, db, [("get", {"memory_id": receipt["memory_id"]})])[0]
        assert memory["content"] == request["content"], "the stored text is not what was sent"
        assert memory["revision_id"] == receipt["revision_id"]
        assert tuple(memory["tags"]) == tuple(sorted(request["tags"]))

    def test_the_receipt_replays_without_writing_again(
        self, server: str, written: tuple[str, dict, dict]
    ) -> None:
        """Same key, same arguments -> the original receipt, and no second memory."""
        db, request, receipt = written
        again, status = _session(server, db, [("record", request), ("status", {})])
        assert again == receipt, f"a retry minted a different receipt:\n{receipt}\n{again}"
        assert status["active_memories"] == 1, "the retry created a second memory"
        assert status["latest_durable_seq"] == receipt["durable_seq"], (
            "the retry advanced the durable sequence, so it was not a replay"
        )


class TestCorrection:
    """`revise` replaces the whole revision; history keeps what it replaced."""

    def test_revising_moves_the_head_and_keeps_the_old_revision_readable(
        self, server: str, written: tuple[str, dict, dict]
    ) -> None:
        db, request, receipt = written
        corrected = (
            request["content"]
            + "\nCorrected 2026-09-21: build the runtime from a checkout of its own, so a "
            "later `git checkout` in the development tree cannot change what an installed "
            "environment imports."
        )

        # Every field that should survive is resent. Omitted optional fields reset to their
        # defaults -- `revise` replaces, it does not patch -- so dropping `tags` here would
        # quietly strip them and the documented search would stop matching.
        revised = _session(server, db, [(
            "revise",
            {
                "memory_id": receipt["memory_id"],
                "expected_revision_id": receipt["revision_id"],
                "content": corrected,
                "kind": request["kind"],
                "tags": request["tags"],
                "idempotency_key": REVISE_KEY,
            },
        )])[0]
        assert revised["revision_id"] != receipt["revision_id"]

        current, old, chain = _session(server, db, [
            ("get", {"memory_id": receipt["memory_id"]}),
            ("get", {"memory_id": receipt["memory_id"],
                     "revision_id": receipt["revision_id"]}),
            ("history", {"memory_id": receipt["memory_id"]}),
        ])
        assert current["content"] == corrected, "get returned something other than the head"
        assert current["revision_id"] == revised["revision_id"]
        assert old["content"] == request["content"], (
            "the superseded revision no longer reads as it was written"
        )
        assert [e["revision_id"] for e in chain["entries"]] == [
            revised["revision_id"], receipt["revision_id"],
        ], f"the revision chain is not newest-first over both revisions: {chain}"

    def test_a_stale_expected_revision_is_refused(
        self, server: str, written: tuple[str, dict, dict]
    ) -> None:
        """The guide tells a reader to reconcile on conflict. That depends on a refusal."""
        db, request, receipt = written
        _session(server, db, [(
            "revise",
            {"memory_id": receipt["memory_id"],
             "expected_revision_id": receipt["revision_id"],
             "content": request["content"] + "\nfirst correction",
             "kind": request["kind"], "tags": request["tags"],
             "idempotency_key": REVISE_KEY},
        )])

        # The head has moved. The original revision id is now stale.
        stale = _session(server, db, [(
            "revise",
            {"memory_id": receipt["memory_id"],
             "expected_revision_id": receipt["revision_id"],
             "content": request["content"] + "\nsecond correction, from a stale head",
             "kind": request["kind"], "tags": request["tags"],
             "idempotency_key": REVISE_KEY + "-stale"},
        )])[0]
        # Asserting the refusal CLASS, not merely the absence of a receipt. "no receipt" is
        # also what a malformed request or a transport fault returns, and either would let
        # this test pass while proving nothing about compare-and-swap.
        assert "revision_conflict" in stale.get("text", ""), (
            f"expected a revision_conflict refusal; a stale write that is accepted is a "
            f"silent overwrite, and any other failure means this test proved nothing: {stale}"
        )

        survived = _session(server, db, [("get", {"memory_id": receipt["memory_id"]})])[0]
        assert survived["content"].endswith("first correction"), (
            "the stale write reached the store"
        )


class TestScopeIsolation:
    """A different launch scope is a different store, not a filter over a shared one."""

    def test_another_namespace_cannot_see_the_memory(
        self, server: str, written: tuple[str, dict, dict]
    ) -> None:
        db, _request_body, receipt = written

        # Same database file, same query, different --namespace.
        hits = _session(
            server, db, [("search", _request(SEARCH_REQUEST))], namespace="someone-else",
        )[0]["hits"]
        assert hits == [], f"another scope saw into this one: {hits}"

        # And it cannot reach it by exact identifier either, which is the stronger claim:
        # not being ranked is not the same as not being readable.
        other = _session(
            server, db, [("get", {"memory_id": receipt["memory_id"]})],
            namespace="someone-else",
        )[0]
        assert "content" not in other, f"another scope read the memory by id: {other}"
        assert "not_found" in other.get("text", ""), (
            f"expected not_found for an id outside the scope; a different failure would "
            f"leave the isolation claim untested: {other}"
        )
