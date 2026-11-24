"""Controlled authored draft/evidence judgments, not a production validator."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Event:
    seq: int
    kind: str
    text: str
    session: str = "root"
    turn: int = 1
    checkout: str = "synthetic-nexus"
    committed: bool = True


ALLOWED = {"human-decision", "bounded-file-read", "verification"}
ROUTINE = (
    ("routine-check", "verification", "The requested pytest check exited 0.",
     "A standalone passing check is not a reusable lesson."),
    ("routine-progress", "bounded-file-read", "The existing Atlas page was inspected.",
     "Routine work already discoverable from the checkout."),
    ("plan-only", "assistant-plan", "Tomorrow we could try adding a different pager.",
     "No decision, observation, or verification was established."),
    ("recalled-only", "memory-recall", "Older recalled text suggests using a special lane.",
     "Recalled text alone is not newly established evidence."),
)


def evaluate(events, refs, evidence_review):
    """Returns an evidence reason; semantic review is independent of citation validity."""
    if not refs:
        return "no-evidence"
    index = {event.seq: event for event in events}
    for ref in refs:
        event = index.get(ref)
        if (event is None or not event.committed or event.session != "root"
                or event.turn != 1 or event.checkout != "synthetic-nexus"):
            return "not-committed-same-turn-scope"
        if event.kind not in ALLOWED:
            return "ineligible-evidence-kind"
    if evidence_review is None:
        return "unsupported-claim"
    return "supported"


def controlled_judgments(facts):
    """Run independently authored usefulness and evidence labels through the rubric.

    Evidence labels are fixture adjudications, not inferred from an ID or by NLP.
    """
    rows = []

    def add(case, draft, events, refs, usefulness, usefulness_reason, evidence_review, eligible):
        evidence = evaluate(events, refs, evidence_review) if draft else "no-proposal"
        ready = bool(draft and usefulness and evidence == "supported")
        rows.append(dict(case=case, proposed=bool(draft), claim=draft, useful=usefulness,
                         usefulness_reason=usefulness_reason, evidence=evidence,
                         cited_events=refs, evidence_text=[event.text for event in events],
                         evidence_review=evidence_review,
                         draft_ready=ready, expected_useful_draft=eligible,
                         false_positive_draft=ready and not eligible))

    for fact in facts:
        event = Event(11, fact["event_type"], fact["evidence"])
        add(fact["id"], fact["body"], [event], [11], True, fact["usefulness"],
            event.text, True)
    for case, kind, body, reason in ROUTINE:
        event = Event(11, kind, body)
        add(case, body, [event], [11], False, reason, body, False)
    add("ordinary-no-proposal", None, [], [], None, None, None, False)

    fact = facts[0]
    # Both wrong-value and same-value-but-negated controls have valid event IDs.
    for case, text in [
        ("different-lane", "The violet receipt banner must use wren-12, not juniper-43."),
        ("same-token-negated", "The lead explicitly rejected juniper-43 for violet receipt banners."),
    ]:
        add(case, fact["body"], [Event(11, "human-decision", text)], [11], True,
            fact["usefulness"], None, False)
    for case, event, ref in [
        ("forged-id", Event(11, fact["event_type"], fact["evidence"]), 999),
        ("cross-turn", Event(11, fact["event_type"], fact["evidence"], turn=2), 11),
        ("worker-event", Event(11, fact["event_type"], fact["evidence"], session="worker"), 11),
        ("uncommitted-event", Event(11, fact["event_type"], fact["evidence"], committed=False), 11),
        ("changed-checkout", Event(11, fact["event_type"], fact["evidence"], checkout="other"), 11),
    ]:
        add(case, fact["body"], [event], [ref], True, fact["usefulness"], event.text, False)
    return rows
