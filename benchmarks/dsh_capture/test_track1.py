import asyncio
import json
import os
from pathlib import Path
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from draft_rubric import controlled_judgments
from run import DEFAULT_DSH, bridge, human_message, load_facts, main, model_input, run


class ControlledJudgmentsTest(unittest.TestCase):
    def test_off_export_does_not_launch_retrieval(self):
        output = StringIO()
        with (patch("sys.argv", ["run.py", "--model-input", "receipt", "off"]),
              patch("run.run", side_effect=AssertionError("off must not start retrieval")),
              redirect_stdout(output)):
            main()
        case = next(fact for fact in load_facts() if fact["id"] == "receipt")
        messages = json.loads(output.getvalue())["messages"]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[1], {"role": "user", "content": case["prompt"]})
        self.assertNotIn(case["answer"], output.getvalue())

    def test_nonderivable_facts_and_unchanged_questions(self):
        facts = load_facts()
        self.assertIn(len(facts), range(5, 11))
        self.assertEqual(len({f["answer"] for f in facts}), len(facts))
        for fact in facts:
            self.assertLessEqual(len(fact["body"].encode()), 1200)
            self.assertIn(fact["answer"], fact["body"])
            for other in facts:
                self.assertNotIn(other["answer"].lower(), fact["prompt"].lower())

    def test_usefulness_and_support_separate(self):
        rows = {row["case"]: row for row in controlled_judgments(load_facts())}
        for row in rows.values():
            with self.subTest(case=row["case"]):
                self.assertEqual(row["draft_ready"], row["expected_useful_draft"])
                self.assertFalse(row["false_positive_draft"])
        self.assertFalse(rows["routine-check"]["useful"])
        self.assertEqual(rows["routine-check"]["evidence"], "supported")
        self.assertEqual(rows["ordinary-no-proposal"]["evidence"], "no-proposal")
        for case in ("different-lane", "same-token-negated"):
            self.assertTrue(rows[case]["useful"])
            self.assertEqual(rows[case]["evidence"], "unsupported-claim")
        for case in ("cross-turn", "worker-event", "uncommitted-event", "forged-id", "changed-checkout"):
            self.assertEqual(rows[case]["evidence"], "not-committed-same-turn-scope")


class DisposableRecallTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsh = Path(os.environ.get("DSH_CHECKOUT", DEFAULT_DSH))
        cls.report = asyncio.run(run(cls.dsh))

    def test_current_query_accepts_only_human_messages(self):
        nonhuman = human_message("synthetic shadow breadcrumb")
        nonhuman["source"] = {"kind": "nexus-memory"}
        genuine = human_message("What Lattice Ωmega lattice signal?")
        query = bridge(self.dsh, {"action": "queries", "messages": [[nonhuman], [nonhuman, genuine]]})
        self.assertEqual(query["queries"], ["", '"lattice" OR "ωmega" OR "signal"'])

    def test_true_pair_separates_retrieval_from_application(self):
        facts = {fact["id"]: fact for fact in load_facts()}
        self.assertFalse(self.report["production_state_used"])
        self.assertEqual(self.report["seeded"], len(facts))
        self.assertEqual(self.report["fresh_model_application"], "not-run")
        for pair in self.report["pairs"]:
            with self.subTest(case=pair["case"]):
                fact = facts[pair["case"]]
                off, on = pair["recall_off"], pair["recall_on"]
                self.assertEqual(off["prompt"], on["prompt"])
                self.assertIsNone(off["context"])
                self.assertEqual(off["search_calls"] + off["get_calls"], 0)
                for arm in (off, on):
                    self.assertIsNone(arm["application_correct"])
                    self.assertNotIn(fact["answer"], arm["prompt"])
                    self.assertNotIn(pair["intended_id"], arm["prompt"])
                self.assertEqual(on["intended_read"], fact["expect_recall"])
                self.assertEqual(on["intended_delivered"], fact["expect_recall"])
                self.assertEqual(on["intended_answer_in_context"], fact["expect_recall"])
                self.assertEqual(on["miss_reason"], None if fact["expect_recall"] else "not-in-search-top-k")
                self.assertLessEqual(on["get_calls"], self.report["hook_budgets"]["readLimit"])
                self.assertLessEqual(on["context_bytes"], self.report["hook_budgets"]["injectedBytes"]
                                     - self.report["hook_budgets"]["captureReserveBytes"])
                off_input = model_input(pair, "off")
                on_input = model_input(pair, "on")
                self.assertEqual(off_input["messages"], on_input["messages"][:2])
                self.assertNotIn(fact["answer"], json.dumps(off_input))
                self.assertNotIn(pair["intended_id"], json.dumps(off_input))
                self.assertEqual(set(on_input), {"messages"})


if __name__ == "__main__":
    unittest.main()
