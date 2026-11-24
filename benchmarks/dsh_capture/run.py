"""Track 1 synthetic draft rubric and real disposable Nexus lexical recall."""
from __future__ import annotations

import argparse
import asyncio
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from time import perf_counter_ns

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from draft_rubric import controlled_judgments

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_DSH = Path.home() / "Repos/deepseek-harness"
HOOK_DIR = ".agents/integrations/nexus-memory"
SYSTEM = (
    "Answer the question about the synthetic Nexus rehearsal in one sentence. "
    "If the specific assignment cannot be established from the available evidence, "
    "say you don't know. Do not guess or claim independent verification."
)


def load_facts():
    return json.loads((HERE / "facts.json").read_text(encoding="utf-8"))


def bridge(dsh, payload):
    result = subprocess.run(
        ["node", str(HERE / "hook_bridge.mjs"), str((dsh / HOOK_DIR).resolve())],
        input=json.dumps(payload), capture_output=True, text=True, timeout=15,
    )
    if result.returncode:
        raise RuntimeError(f"DSH policy bridge failed ({result.returncode}): {result.stderr.strip()}")
    return json.loads(result.stdout)


def human_message(text):
    return {"role": "user", "source": {"kind": "user"},
            "content": [{"type": "text", "text": text}]}


async def call(session, name, **args):
    response = await session.call_tool(name, args, read_timeout_seconds=10)
    if response.is_error or not isinstance(response.structured_content, dict):
        raise RuntimeError(f"Disposable Nexus {name} failed: {response.content!r}")
    return response.structured_content


def elapsed_ms(start):
    return round((perf_counter_ns() - start) / 1e6, 3)


def arm(prompt, context=None, **recall):
    return {"prompt": prompt, "context": context, "application_correct": None,
            "model_input_tokens": None, **recall}


async def run(dsh):
    facts = load_facts()
    prompts = [fact["prompt"] for fact in facts]
    started = perf_counter_ns()
    policy = bridge(dsh, {"action": "queries", "messages": [[human_message(p)] for p in prompts]})
    query_batch_ms = elapsed_ms(started)
    config = policy["config"]
    if not all(policy["queries"]):
        raise RuntimeError("At least one intended human prompt produced no query")

    # No selectable destination: use this checkout's MCP server, only a new DB,
    # explicit synthetic namespace/actor and no home/profile instruction discovery.
    with tempfile.TemporaryDirectory(prefix="nexus-dsh-capture-") as directory:
        params = StdioServerParameters(
            command=sys.executable, cwd=directory,
            args=["-m", "nexus_memory.transport.mcp_server", "--no-instructions",
                  "--db", str(Path(directory) / "nexus.sqlite3"),
                  "--namespace", "dsh-capture-benchmark", "--actor", "synthetic"],
            env={"PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
        )
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer, read_timeout_seconds=10) as session:
                await session.initialize()
                receipts = []
                for index, fact in enumerate(facts, start=1):
                    receipts.append(await call(
                        session, "record", content=fact["body"], kind=fact["kind"],
                        tags=["synthetic"], idempotency_key=f"seed-{index}",
                        source_uri=f"synthetic:dsh-capture/event/{index}",
                    ))
                rows = []
                timings = []
                for query in policy["queries"]:
                    started = perf_counter_ns()
                    hits = (await call(session, "search", query=query, advanced=True,
                                       limit=config["hitLimit"]))["hits"]
                    records = [await call(session, "get", memory_id=hit["memory_id"],
                                          revision_id=hit["revision_id"])
                               for hit in hits[:config["readLimit"]]]
                    timings.append(elapsed_ms(started))
                    rows.append(dict(query=query, hits=hits, records=records))

    started = perf_counter_ns()
    contexts = bridge(dsh, {"action": "contexts", "rows": rows})
    context_batch_ms = elapsed_ms(started)
    pairs = []
    for fact, receipt, row, context, mcp_ms in zip(facts, receipts, rows, contexts, timings, strict=True):
        intended = receipt["memory_id"], receipt["revision_id"]
        hit_ids = [(hit["memory_id"], hit["revision_id"]) for hit in row["hits"]]
        read_ids = [(record["memory_id"], record["revision_id"]) for record in row["records"]]
        included_ids = [(item["memory_id"], item["revision_id"]) for item in context["included"]]
        hit_rank = hit_ids.index(intended) + 1 if intended in hit_ids else None
        intended_candidate = next((candidate for candidate in context["candidates"]
                                   if (candidate["memory_id"], candidate["revision_id"]) == intended), None)
        delivered = intended_candidate is not None
        text = context["message"]["content"][0]["text"] if context["message"] else None
        pairs.append(dict(
            case=fact["id"], expected_recall=fact["expect_recall"], intended_id=intended[0],
            recall_off=arm(fact["prompt"], search_calls=0, get_calls=0, hit_rank=None,
                           intended_read=False, intended_delivered=False, context_bytes=0,
                           mcp_read_ms=0),
            recall_on=arm(fact["prompt"], context=text, query=row["query"],
                          search_calls=1, get_calls=len(read_ids), hit_rank=hit_rank,
                          intended_read=intended in read_ids, intended_delivered=delivered,
                          intended_answer_in_context=delivered and fact["answer"] in intended_candidate["content"],
                          intended_truncated=intended_candidate["truncated"] if delivered else None,
                          unrelated_candidates=sum(item != intended for item in included_ids),
                          miss_reason=None if delivered else (
                              "not-in-search-top-k" if not hit_rank else
                              "outside-read-budget" if intended not in read_ids else "not-in-context"),
                          context_bytes=context["bytes"], mcp_read_ms=mcp_ms),
        ))
    rubric_started = perf_counter_ns()
    drafts = controlled_judgments(facts)
    return dict(
        mode="controlled-synthetic-judgments-and-seeded-retrieval", seeded=len(receipts),
        fresh_model_application="not-run", production_state_used=False,
        hook_checkout=str(dsh.resolve()), fingerprints={
            **policy["fingerprints"],
            **{f"{name}_sha256": sha256((HERE / name).read_bytes()).hexdigest()
               for name in ("facts.json", "hook_bridge.mjs", "run.py", "draft_rubric.py")},
        },
        hook_budgets={key: config[key] for key in (
            "queryInputBytes", "queryBytes", "queryWords", "hitLimit", "readLimit",
            "injectedBytes", "captureReserveBytes")},
        timing_ms=dict(query_bridge_batch=query_batch_ms, context_bridge_batch=context_batch_ms,
                       controlled_fixture_rubric=elapsed_ms(rubric_started)),
        draft_judgments=drafts, pairs=pairs,
        summary=dict(intended_search_hits=sum(p["recall_on"]["hit_rank"] is not None for p in pairs),
                     intended_deliveries=sum(p["recall_on"]["intended_delivered"] for p in pairs),
                     controlled_ready=sum(d["draft_ready"] for d in drafts),
                     controlled_false_positives=sum(d["false_positive_draft"] for d in drafts)),
    )


def model_input(pair, arm_name):
    """Answer-blind model payload (except the automatic candidate context in on)."""
    arm_data = pair["recall_" + arm_name]
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": arm_data["prompt"]}]
    if arm_data["context"] is not None:
        messages.append({"role": "user", "content": arm_data["context"]})
    return {"messages": messages}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsh-checkout", type=Path, default=Path(os.environ.get("DSH_CHECKOUT", DEFAULT_DSH)),
                        help="Trusted DSH source checkout (reads pure hook policy; never starts DSH)")
    parser.add_argument("--model-input", nargs=2, metavar=("CASE", "off|on"),
                        help="Emit only one arm's messages for an optional external fresh-model trial")
    args = parser.parse_args()
    if args.model_input:
        case, arm_name = args.model_input
        fact = next((row for row in load_facts() if row["id"] == case), None)
        if fact is None or arm_name not in {"off", "on"}:
            parser.error("--model-input requires a case from facts.json and off or on")
        if arm_name == "off":
            # This arm never launches Node or Nexus and cannot receive recall.
            report = model_input({"recall_off": arm(fact["prompt"])}, "off")
        else:
            result = asyncio.run(run(args.dsh_checkout))
            pair = next(row for row in result["pairs"] if row["case"] == case)
            report = model_input(pair, "on")
    else:
        report = asyncio.run(run(args.dsh_checkout))
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
