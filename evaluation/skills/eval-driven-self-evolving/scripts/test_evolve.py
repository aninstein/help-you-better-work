"""Behavioral checks in a disposable repository; no model or actual Wiki writes."""

from __future__ import annotations

import argparse
import importlib.machinery
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import evolve
import report


class LoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / ".gitignore").write_text(
            "evaluation/reports/\nevaluation/.runtime/\nbackend/data/\n"
        )
        self.prompt = self.root / next(iter(evolve.ALLOWED))
        self.prompt.parent.mkdir(parents=True)
        self.prompt.write_text("original prompt\n")
        (self.root / "evaluation").mkdir()
        evolve.write(
            self.root / "evaluation/catalog.json", {"suites": [{"id": "suite"}]}
        )
        self.source = self.root / "backend/app/agent/voice_agent.py"
        self.source.write_text('SYSTEM = "immutable core"\n')
        self.memory = self.root / "backend/data/wiki/context.md"
        self.memory.parent.mkdir(parents=True)
        self.memory.write_text("actual memory must stay intact")
        self.run = evolve.init(
            self.root, argparse.Namespace(suite="suite", max_rounds=4)
        )
        self.state = evolve.read(self.run / "state.json")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def result(self, failed: bool = True, regression: bool = False) -> dict:
        value = {
            "suite_count": 1,
            "automatic_score": 70 if failed else 100,
            "by_mode": {"direct": 70},
            "by_domain": {"test": 70},
            "suites": [
                {
                    "id": "suite",
                    "title": "场景 <script>",
                    "domain": "test",
                    "cases": [
                        {
                            "id": "case",
                            "mode": "direct",
                            "score": 70,
                            "checks": [
                                {
                                    "name": "retention",
                                    "passed": not failed,
                                    "weight": 30,
                                },
                                {
                                    "name": "isolation",
                                    "passed": not regression,
                                    "weight": 70,
                                },
                            ],
                            "observed": {
                                "answer_excerpt": "</pre><script>alert(1)</script>"
                            },
                        }
                    ],
                    "memory": {
                        "score": 100,
                        "checks": [{"name": "recall", "passed": True, "weight": 100}],
                        "observed": {},
                    },
                }
            ],
        }
        return value

    def evaluate(self, value: dict | None = None) -> None:
        with patch.object(
            evolve, "live_evaluation", return_value=value or self.result()
        ):
            evolve.evaluate(self.root, self.run, self.state, 1)

    def analyze(self, kind: str = "memory_prompt") -> None:
        latest = evolve.read(
            self.run / f"round-{len(self.state['rounds']) - 1:02d}/report.json"
        )
        keys = sorted(evolve.fail_keys(latest))
        issues = (
            [
                {
                    "id": "memory",
                    "kind": kind,
                    "title": "retention",
                    "evidence": keys,
                    "diagnosis": "stored preference absent",
                    "hypothesis": "clarify confirmed facts",
                    "action": "edit prompt"
                    if kind == "memory_prompt"
                    else "report only",
                    "reason": "needs explicit consolidation rule",
                }
            ]
            if keys
            else []
        )
        evolve.record(
            self.root, self.run, self.state, {"summary": "analysis", "issues": issues}
        )

    def proposal(self, text: str = "improved prompt\n") -> dict:
        return {
            "changes": [
                {
                    "path": next(iter(evolve.ALLOWED)),
                    "before_sha256": evolve.sha(self.prompt.read_bytes()),
                    "content": text,
                    "issue_ids": ["memory"],
                }
            ]
        }

    def test_prompt_only_round_and_finish(self) -> None:
        self.evaluate()
        self.analyze()
        evolve.apply(self.root, self.run, self.state, self.proposal())
        with self.assertRaisesRegex(ValueError, "必须先分析"):
            evolve.apply(self.root, self.run, self.state, self.proposal("another"))
        self.evaluate(self.result(failed=False))
        self.analyze()
        evolve.finish(self.root, self.run, self.state, "all automatic checks passed")
        self.assertEqual(self.state["effective_round"], 1)
        self.assertEqual(self.prompt.read_text(), "improved prompt\n")
        self.assertEqual(self.source.read_text(), 'SYSTEM = "immutable core"\n')
        self.assertEqual(self.memory.read_text(), "actual memory must stay intact")
        self.assertTrue((self.run / "index.html").exists())
        self.assertEqual(self.state["ledger"]["suite/case/retention"]["attempts"], 1)

    def test_source_and_actual_memory_changes_detected(self) -> None:
        self.source.write_text("external user change")
        with self.assertRaisesRegex(ValueError, "保护范围"):
            evolve.guard(self.root, self.state)
        self.source.write_text('SYSTEM = "immutable core"\n')
        self.memory.write_text("changed context")
        with self.assertRaisesRegex(ValueError, "保护范围"):
            evolve.guard(self.root, self.state)

    def test_environment_changes_stop_and_write_boundary_report(self) -> None:
        with (
            patch.dict("os.environ", {"MODEL_TEMPERATURE": "0.1234"}),
            self.assertRaisesRegex(ValueError, "环境变量"),
        ):
            evolve.guard(self.root, self.state, self.run)
        self.assertEqual(self.state["status"], "error")
        self.assertTrue((self.run / "boundary-error.json").exists())
        self.assertIn("修改边界检查失败", (self.run / "index.html").read_text())

    def test_out_of_scope_stale_and_symlink_rejected(self) -> None:
        self.evaluate()
        self.analyze()
        for forbidden in (
            "backend/app/agent/voice_agent.py",
            "backend/data/wiki/context.md",
            "backend/app/agent/prompts/core.md",
            "../memory.md",
        ):
            proposal = self.proposal()
            proposal["changes"][0]["path"] = forbidden
            with self.assertRaisesRegex(ValueError, "白名单"):
                evolve.apply(self.root, self.run, self.state, proposal)
        proposal = self.proposal()
        proposal["changes"][0]["before_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "SHA256"):
            evolve.apply(self.root, self.run, self.state, proposal)
        self.prompt.unlink()
        self.prompt.symlink_to(self.memory)
        with self.assertRaisesRegex(ValueError, "软链接"):
            evolve.prompt_path(self.root, next(iter(evolve.ALLOWED)))

    def test_core_prompt_is_report_only(self) -> None:
        self.evaluate()
        self.analyze("core_prompt")
        with self.assertRaisesRegex(ValueError, "仅可修改 memory_prompt"):
            evolve.apply(self.root, self.run, self.state, self.proposal())
        evolve.finish(
            self.root,
            self.run,
            self.state,
            "core prompt requires code change; report only",
        )
        self.assertEqual(self.prompt.read_text(), "original prompt\n")

    def test_regression_rolls_back_even_if_aggregate_score_rises(self) -> None:
        self.evaluate()
        self.analyze()
        evolve.apply(self.root, self.run, self.state, self.proposal())
        candidate = self.result(failed=False, regression=True)
        candidate["automatic_score"] = 95
        self.evaluate(candidate)
        self.assertEqual(self.prompt.read_text(), "original prompt\n")
        self.assertEqual(self.state["effective_round"], 0)
        self.assertTrue(evolve.read(self.run / "round-01/change.json")["rolled_back"])
        page = (self.run / "index.html").read_text()
        self.assertIn("suite/case/retention", page)
        self.assertIn("当前有效版本 R00", page)

    def test_three_failed_rounds_freeze_evidence_even_with_new_issue_id(self) -> None:
        self.evaluate()
        for number in range(3):
            self.analyze()
            evolve.apply(
                self.root, self.run, self.state, self.proposal(f"attempt {number}\n")
            )
            self.evaluate()
        self.analyze()
        with self.assertRaisesRegex(ValueError, "三轮未解决"):
            evolve.apply(self.root, self.run, self.state, self.proposal("fourth"))
        ledger = self.state["ledger"]["suite/case/retention"]
        self.assertTrue(ledger["frozen"])
        self.assertEqual((ledger["streak"], ledger["attempts"]), (3, 3))
        self.assertIn("三轮未解", (self.run / "index.html").read_text())

    def test_analysis_must_cover_failures_and_budget_enforced(self) -> None:
        self.evaluate()
        with self.assertRaisesRegex(ValueError, "遗漏"):
            evolve.record(
                self.root, self.run, self.state, {"summary": "incomplete", "issues": []}
            )
        self.state["max_rounds"] = 1
        self.analyze()
        evolve.apply(self.root, self.run, self.state, self.proposal())
        self.evaluate()
        self.analyze()
        with self.assertRaisesRegex(ValueError, "预算"):
            evolve.apply(self.root, self.run, self.state, self.proposal("another"))

    def test_failed_startup_preserves_evidence_and_rolls_back(self) -> None:
        self.evaluate()
        self.analyze()
        evolve.apply(self.root, self.run, self.state, self.proposal())
        with (
            patch.object(
                evolve, "live_evaluation", side_effect=RuntimeError("service failed")
            ),
            self.assertRaisesRegex(RuntimeError, "service failed"),
        ):
            evolve.evaluate(self.root, self.run, self.state, 1)
        self.assertEqual(self.prompt.read_text(), "original prompt\n")
        self.assertEqual(self.state["status"], "error")
        self.assertIn("service failed", (self.run / "index.html").read_text())
        self.assertTrue((self.run / "round-01/error.json").exists())

    def test_render_escapes_data_and_discloses_truncation(self) -> None:
        output = self.root / "report.html"
        report.render(self.result(), output)
        document = output.read_text()
        self.assertNotIn("<script>alert(1)</script>", document)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", document)
        self.assertIn("历史摘录，完整返回未采集", document)
        self.assertIn("suite/case/retention", document)
        self.assertIn("历史报告尚未归因", document)
        self.assertNotIn("@@", document)
        self.assertIn('content="eval-driven-self-evolving/1.0"', document)
        captured = {
            "full_capture": True,
            "full_evidence": {
                "talker /api/v1/sessions/a": {
                    "answers": [
                        {"id": "one", "status": "completed", "text": "x" * 3000 + "END"}
                    ]
                }
            },
        }
        self.assertIn("x" * 3000 + "END", report.full_answers(captured))
        self.assertNotIn("历史摘录", report.full_answers(captured))

    def test_live_adapter_captures_complete_answers_and_cleans_up(self) -> None:
        case = {"id": "case", "mode": "direct", "expected": {}, "manual_rubric": []}
        evolve.write(
            self.root / "evaluation/catalog.json",
            {
                "title": "adapter test",
                "suites": [{"id": "suite", "cases": [case], "memory_chain": {}}],
            },
        )
        fixture = self.result()
        stopped = []

        class FakeApi:
            def __init__(self, *args, **kwargs):
                pass

            def request(self, method, path, role=None, payload=None):
                if path == "/api/v1/auth/demo":
                    return {"token": "SECRET_AUTH_TOKEN"}
                if path.startswith("/api/v1/sessions/"):
                    return {
                        "answers": [
                            {
                                "id": "a",
                                "status": "completed",
                                "text": "x" * 3000 + "END",
                            }
                        ]
                    }
                return {"status": "ok"}

            def login(self):
                self.request("POST", "/api/v1/auth/demo")

        class FakeLoader:
            def create_module(self, spec):
                return None

            def exec_module(self, module):
                module.Api = FakeApi
                module.start_isolated_service = lambda timeout: (
                    SimpleNamespace(pid=1234),
                    SimpleNamespace(name=str(self_root / "isolated")),
                    "http://127.0.0.1:1",
                )
                module.stop_isolated_service = lambda process, temporary: (
                    stopped.append(process.pid)
                )

                def fake_case(api, session, data, limit):
                    api.request("GET", "/api/v1/sessions/" + session, "talker")
                    return json.loads(json.dumps(fixture["suites"][0]["cases"][0]))

                def fake_memory(api, session, chain, limit):
                    api.request("GET", "/api/v1/sessions/new-session", "talker")
                    return json.loads(json.dumps(fixture["suites"][0]["memory"]))

                def fake_suite(api, suite, timeout, isolated):
                    assert isolated
                    value = json.loads(json.dumps(fixture["suites"][0]))
                    value["automatic_score"] = 70
                    value["cases"] = [
                        module.run_case(api, "session", suite["cases"][0], timeout)
                    ]
                    value["memory"] = module.run_memory_chain(
                        api, "session", {}, timeout
                    )
                    return value

                module.run_case = fake_case
                module.run_memory_chain = fake_memory
                module.run_suite = fake_suite

        self_root = self.root
        spec = importlib.machinery.ModuleSpec("fake_runner", FakeLoader())
        with patch.object(
            evolve.importlib.util, "spec_from_file_location", return_value=spec
        ):
            value = evolve.live_evaluation(self.root, ["suite"], 1)
        self.assertEqual(stopped, [1234])
        self.assertTrue(value["service"]["fresh_process"])
        captured = value["suites"][0]["cases"][0]["observed"]
        self.assertTrue(captured["full_capture"])
        self.assertIn("x" * 3000 + "END", report.full_answers(captured))
        self.assertNotIn("SECRET_AUTH_TOKEN", json.dumps(value))


if __name__ == "__main__":
    unittest.main()
