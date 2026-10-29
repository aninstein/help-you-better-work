#!/usr/bin/env python3
"""Prompt-only evaluation loop. Analysis and proposed text are supplied by the agent."""

from __future__ import annotations

import argparse
import difflib
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from report import render, render_run

ROOT = Path(__file__).resolve().parents[4]
ALLOWED = {"backend/app/agent/prompts/memory.md"}
KINDS = {"memory_prompt", "core_prompt", "code", "context", "evaluation", "infra"}


def environment_hashes() -> dict[str, str]:
    """Freeze model overrides between CLI invocations without saving secrets."""
    return {
        key: sha(value.encode())
        for key, value in os.environ.items()
        if key.startswith(("MODEL_", "OLLAMA_", "OPENAI_", "STT_", "TTS_"))
        or key in {"PRIMARY_MODEL", "DUET_DATA_DIR"}
    }


def now() -> str:
    return datetime.now(UTC).isoformat()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def require(condition: Any, message: str) -> None:
    if not condition:
        raise ValueError(message)


def fingerprint(path: Path) -> str:
    if path.is_symlink():
        return "symlink:" + sha(os.readlink(path).encode())
    return sha(path.read_bytes())


def snapshot(root: Path, memory_roots: list[str]) -> dict[str, str]:
    """Hash source/configuration and actual memory without persisting their contents."""
    names = (
        subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root,
        )
        .decode()
        .split("\0")
    )
    paths = {root / name for name in names if name}
    # .env files are usually ignored by git; freeze configuration without exposing values.
    for directory in (root, root / "backend"):
        paths.update(directory.glob(".env*"))
    result = {}
    for path in sorted(paths):
        relative = path.relative_to(root).as_posix()
        if relative in ALLOWED or relative.startswith(
            ("evaluation/reports/", "evaluation/.runtime/")
        ):
            continue
        if path.is_file() or path.is_symlink():
            result[relative] = fingerprint(path)
    for location in memory_roots:
        base = Path(location)
        if base.is_symlink():
            result["memory:" + str(base)] = fingerprint(base)
        elif base.exists():
            for path in sorted(base.rglob("*")):
                if path.is_file() or path.is_symlink():
                    result["memory:" + str(path)] = fingerprint(path)
    return result


def prompt_path(root: Path, name: str) -> Path:
    require(name in ALLOWED, f"不在 prompt 白名单：{name}")
    path = root.resolve() / name
    require(path.is_file(), f"prompt 不存在或没有运行时入口：{name}")
    require(path.resolve() == path.absolute(), f"禁止软链接 prompt：{name}")
    return path


def guard(root: Path, state: dict[str, Any], run: Path | None = None) -> None:
    try:
        _check_boundary(root, state)
    except (ValueError, OSError) as exc:
        if run is not None:
            state["status"] = "error"
            state["stop_reason"] = "修改边界检查失败：" + str(exc)
            write(run / "boundary-error.json", {"at": now(), "error": str(exc)})
            save(run, state)
        raise


def _check_boundary(root: Path, state: dict[str, Any]) -> None:
    require(
        environment_hashes() == state["environment_hashes"],
        "模型或数据环境变量发生变化",
    )
    current = snapshot(root, state["memory_roots"])
    changed = sorted(
        key
        for key in current.keys() | state["protected"].keys()
        if current.get(key) != state["protected"].get(key)
    )
    require(
        not changed, "保护范围发生变化，停止并保留用户工作：" + ", ".join(changed[:20])
    )
    for name, digest in state["prompt_hashes"].items():
        require(
            fingerprint(prompt_path(root, name)) == digest, f"prompt 被外部修改：{name}"
        )


def failures(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    items = {}
    for suite in report.get("suites", []):
        for case in [
            *suite.get("cases", []),
            {"id": "memory_chain", **suite.get("memory", {})},
        ]:
            for check in case.get("checks", []):
                key = f"{suite['id']}/{case['id']}/{check['name']}"
                items[key] = check
    return items


def fail_keys(report: dict[str, Any]) -> set[str]:
    return {key for key, check in failures(report).items() if not check.get("passed")}


def init(root: Path, args: Any) -> Path:
    catalog = read(root / "evaluation/catalog.json")
    suites = [
        s["id"] for s in catalog["suites"] if not args.suite or s["id"] == args.suite
    ]
    require(suites, "未知 suite")
    require(args.max_rounds > 0, "max-rounds 必须为正整数")
    run = (
        root
        / "evaluation/reports/evolution"
        / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    )
    run.mkdir(parents=True)
    configured_data = os.environ.get("DUET_DATA_DIR") or dotenv_values(
        root / ".env"
    ).get("DUET_DATA_DIR")
    actual_data = Path(configured_data) if configured_data else root / "backend/data"
    if not actual_data.is_absolute():
        actual_data = root / "backend" / actual_data
    memory_roots = sorted(
        {str((root / "backend/data").resolve()), str(actual_data.resolve())}
    )
    state = {
        "schema_version": "1.0",
        "template_version": "1.0",
        "created_at": now(),
        "root": str(root),
        "suites": suites,
        "max_rounds": args.max_rounds,
        "status": "ready",
        "stop_reason": "",
        "rounds": [],
        "effective_round": None,
        "pending": None,
        "ledger": {},
        "memory_roots": memory_roots,
        "protected": snapshot(root, memory_roots),
        "environment_hashes": environment_hashes(),
        "prompt_hashes": {
            name: fingerprint(prompt_path(root, name)) for name in sorted(ALLOWED)
        },
        "limitations": [
            "核心 prompt 嵌于 voice_agent.py，属于代码，只报告不修改。",
            "自动检查通过不代表人工语言质量量表已通过。",
        ],
    }
    save(run, state)
    return run


def save(run: Path, state: dict[str, Any]) -> None:
    write(run / "state.json", state)
    render_run(run, state)


def live_evaluation(root: Path, suite_ids: list[str], timeout: float) -> dict[str, Any]:
    """Reuse the exact evaluator; instrument evidence in memory, never rewrite source."""
    scripts = root / "evaluation/scripts"
    sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location(
        "evolution_live_runner", scripts / "run_live.py"
    )
    require(spec and spec.loader, "无法加载现有评测器")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    class CapturingApi(runner.Api):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.evidence: dict[str, Any] = {}

        def request(
            self, method: str, path: str, role: str | None = None, payload: Any = None
        ) -> Any:
            response = super().request(method, path, role, payload)
            if method == "GET" and (
                re.fullmatch(r"/api/v1/sessions/[^/?]+", path)
                or path.startswith("/api/v1/wiki/document?")
            ):
                # Auth responses/tokens are never captured. Last snapshot per role/session.
                self.evidence[f"{role} {path}"] = response
            return response

    original_case = runner.run_case
    original_memory = runner.run_memory_chain

    def observed_case(api: Any, session: str, case: Any, limit: float) -> Any:
        api.evidence = {}
        try:
            value = original_case(api, session, case, limit)
        except Exception as exc:  # noqa: BLE001 - retain per-case evidence, matching run_live
            value = {
                "id": case["id"],
                "mode": case["mode"],
                "score": 0,
                "checks": [{"name": "runner_error", "passed": False, "weight": 100}],
                "observed": {"runner_error": str(exc)},
                "expected": case["expected"],
                "manual_rubric": case["manual_rubric"],
            }
        value["inputs"] = case
        value["observed"]["full_evidence"] = api.evidence.copy()
        value["observed"]["full_capture"] = True
        return value

    def observed_memory(api: Any, session: str, chain: Any, limit: float) -> Any:
        api.evidence = {}
        try:
            value = original_memory(api, session, chain, limit)
        except Exception as exc:  # noqa: BLE001 - retain memory-chain evidence
            value = {
                "score": 0,
                "checks": [
                    {"name": "memory_runner_error", "passed": False, "weight": 100}
                ],
                "observed": {"runner_error": str(exc)},
            }
        value["inputs"] = chain
        value["observed"]["full_evidence"] = api.evidence.copy()
        value["observed"]["full_capture"] = True
        return value

    runner.run_case = observed_case
    runner.run_memory_chain = observed_memory
    process, temporary = None, None
    started = now()
    try:
        process, temporary, base_url = runner.start_isolated_service(timeout)
        # start_isolated_service sets DUET_DATA_DIR to its TemporaryDirectory.
        protected_memory = {
            str(root / "backend/data"),
            os.environ.get("DUET_DATA_DIR", ""),
        }
        require(
            str(Path(temporary.name).resolve()) not in protected_memory,
            "隔离路径不能等于实际数据",
        )
        api = CapturingApi(base_url, timeout)
        health = api.request("GET", "/healthz")
        api.login()
        suites = []
        catalog = read(root / "evaluation/catalog.json")
        for suite in catalog["suites"]:
            if suite["id"] in suite_ids:
                print(f"评测 {suite['id']}", flush=True)
                suites.append(runner.run_suite(api, suite, timeout, isolated=True))
        by_mode, by_domain = defaultdict(list), defaultdict(list)
        for suite in suites:
            by_domain[suite["domain"]].append(suite["automatic_score"])
            for case in suite["cases"]:
                by_mode[case["mode"]].append(case["score"])
        mean = lambda values: round(sum(values) / len(values), 1)
        return {
            "schema_version": "1.0",
            "title": catalog["title"],
            "started_at": started,
            "finished_at": now(),
            "base_url": base_url,
            "isolated_run": True,
            "service": {
                "pid": process.pid,
                "health": health,
                "fresh_process": True,
                "data_dir": temporary.name,
                "stopped_after_run": True,
            },
            "suite_count": len(suites),
            "automatic_score": mean([s["automatic_score"] for s in suites]),
            "by_mode": {k: mean(v) for k, v in sorted(by_mode.items())},
            "by_domain": {k: mean(v) for k, v in sorted(by_domain.items())},
            "suites": suites,
            "score_note": "沿用原评测器评分；全部自动检查通过才标记题目通过，人工量表待复核。",
        }
    finally:
        runner.stop_isolated_service(process, temporary)
        runner.run_case, runner.run_memory_chain = original_case, original_memory
        sys.path.remove(str(scripts))


def rollback(root: Path, state: dict[str, Any], change: dict[str, Any]) -> None:
    # Check all targets first. Never overwrite unrelated/concurrent user edits.
    for item in change["changes"]:
        require(
            fingerprint(prompt_path(root, item["path"])) == sha(item["after"].encode()),
            "回退遇到并发修改，请人工处理：" + item["path"],
        )
    for item in change["changes"]:
        prompt_path(root, item["path"]).write_bytes(item["before"].encode())
        state["prompt_hashes"][item["path"]] = sha(item["before"].encode())
    change["rolled_back"] = True


def evaluate(root: Path, run: Path, state: dict[str, Any], timeout: float) -> None:
    require(state["status"] not in {"finished", "error"}, "运行已结束，请新建运行")
    require(
        state["pending"] is not None or not state["rounds"],
        "已有评测，请先分析并应用补丁",
    )
    guard(root, state, run)
    index = len(state["rounds"])
    folder = run / f"round-{index:02d}"
    folder.mkdir(exist_ok=True)
    change = read(folder / "change.json") if state["pending"] is not None else None
    entry = {"index": index, "started_at": now(), "accepted": False, "error": ""}
    state["status"] = "evaluating"
    save(run, state)
    try:
        result = live_evaluation(root, state["suites"], timeout)
        guard(root, state, run)
        result["failure_keys"] = sorted(fail_keys(result))
        result["prompt_hashes"] = state["prompt_hashes"].copy()
        write(folder / "report.json", result)
        current = failures(result)
        previous = (
            read(run / f"round-{state['effective_round']:02d}/report.json")
            if state["effective_round"] is not None
            else None
        )
        regressions = (
            sorted(
                key
                for key, check in failures(previous).items()
                if check.get("passed") and not current.get(key, {}).get("passed")
            )
            if previous
            else []
        )
        errors = [key for key in fail_keys(result) if key.endswith("runner_error")]
        entry.update(
            score=result["automatic_score"],
            regressions=regressions,
            previous_effective=state["effective_round"],
        )
        decreased = previous and result["automatic_score"] < previous["automatic_score"]
        entry["accepted"] = not (regressions or decreased or errors)
        if errors:
            entry["error"] = "评测链路错误：" + ", ".join(errors)
            state["stop_reason"] = entry["error"]
        if change:
            if not entry["accepted"]:
                rollback(root, state, change)
            change["accepted"] = entry["accepted"]
            write(folder / "change.json", change)
        if entry["accepted"]:
            state["effective_round"] = index
        targeted = set(change.get("targeted_keys", [])) if change else set()
        for key in state["ledger"].keys() | current.keys():
            item = state["ledger"].setdefault(
                key, {"streak": 0, "attempts": 0, "history": []}
            )
            failed = key in fail_keys(result)
            if index:
                item["streak"] = item["streak"] + 1 if failed else 0
            if key in targeted:
                item["attempts"] += 1
            item["failed"] = failed
            item["history"].append(
                {"round": index, "failed": failed, "targeted": key in targeted}
            )
            item["frozen"] = item["streak"] >= 3 and failed
        entry["finished_at"] = now()
        state["rounds"].append(entry)
        state["pending"] = None
        state["status"] = "error" if errors else "needs_analysis"
        render(result, folder / "report.html", state=state, run=run)
        save(run, state)
        require(not errors, entry["error"])
    except BaseException as exc:
        if state["rounds"] and state["rounds"][-1]["index"] == index:
            raise
        entry.update(error=f"{type(exc).__name__}: {exc}", finished_at=now())
        if change:
            try:
                rollback(root, state, change)
            except ValueError as rollback_error:
                entry["error"] += "; " + str(rollback_error)
            write(folder / "change.json", change)
        write(folder / "error.json", entry)
        state["rounds"].append(entry)
        state["pending"] = None
        state["status"] = "error"
        state["stop_reason"] = entry["error"]
        save(run, state)
        raise


def record(
    root: Path, run: Path, state: dict[str, Any], analysis: dict[str, Any]
) -> None:
    require(state["rounds"] and state["pending"] is None, "先完成一次评测")
    require(state["status"] != "finished", "运行已结束")
    guard(root, state, run)
    folder = run / f"round-{state['rounds'][-1]['index']:02d}"
    require((folder / "report.json").is_file(), "本轮无有效评测结果，保留错误并 finish")
    missing = fail_keys(read(folder / "report.json"))
    require(analysis.get("summary", "").strip(), "缺少分析 summary")
    require(isinstance(analysis.get("issues"), list), "issues 必须为数组")
    covered, ids = set(), set()
    for issue in analysis["issues"]:
        for field in ("id", "title", "diagnosis", "hypothesis", "action", "reason"):
            require(
                isinstance(issue.get(field), str) and issue[field].strip(),
                "缺少问题字段：" + field,
            )
        require(issue["id"] not in ids, "重复问题 ID")
        ids.add(issue["id"])
        require(issue.get("kind") in KINDS, "非法问题类型")
        evidence = issue.get("evidence")
        require(
            isinstance(evidence, list) and evidence and set(evidence) <= missing,
            "证据必须引用当前失败的 failure_keys",
        )
        covered.update(evidence)
    require(
        covered == missing,
        "分析遗漏失败检查点：" + ", ".join(sorted(missing - covered)),
    )
    write(folder / "analysis.json", analysis)
    state["rounds"][-1]["analyzed"] = True
    for issue in analysis["issues"]:
        for key in issue["evidence"]:
            state["ledger"][key]["analysis"] = issue
    if state["status"] != "error":
        state["status"] = "analyzed"
    save(run, state)


def apply(root: Path, run: Path, state: dict[str, Any], patch: dict[str, Any]) -> None:
    require(
        state["status"] == "analyzed" and state["pending"] is None,
        "必须先分析当前评测，且不能有待测候选",
    )
    require(len(state["rounds"]) <= state["max_rounds"], "优化轮次预算已耗尽")
    guard(root, state, run)
    latest = run / f"round-{state['rounds'][-1]['index']:02d}"
    analysis = read(latest / "analysis.json")
    issues = {issue["id"]: issue for issue in analysis["issues"]}
    changes, names, targeted = [], set(), set()
    require(
        isinstance(patch.get("changes"), list) and patch["changes"],
        "补丁必须包含 changes",
    )
    for item in patch["changes"]:
        name = item["path"]
        path = prompt_path(root, name)
        require(name not in names, "同一文件不能出现两次")
        names.add(name)
        before = path.read_bytes()
        require(sha(before) == item["before_sha256"], "旧 prompt SHA256 不匹配")
        content = item.get("content")
        require(
            isinstance(content, str) and content.strip() and content.encode() != before,
            "新 prompt 必须非空且发生变化",
        )
        require(item.get("issue_ids"), "缺少关联问题")
        reasons = []
        for issue_id in item["issue_ids"]:
            issue = issues.get(issue_id)
            require(
                issue and issue["kind"] == "memory_prompt",
                "当前仅可修改 memory_prompt 问题",
            )
            for key in issue["evidence"]:
                require(
                    not state["ledger"][key].get("frozen"), "三轮未解决，冻结：" + key
                )
                targeted.add(key)
            reasons.append(issue)
        after = content
        changes.append(
            {
                "path": name,
                "before": before.decode(),
                "after": after,
                "before_sha256": sha(before),
                "after_sha256": sha(after.encode()),
                "issues": reasons,
                "diff": "".join(
                    difflib.unified_diff(
                        before.decode().splitlines(True),
                        after.splitlines(True),
                        fromfile=name + " (before)",
                        tofile=name + " (after)",
                    )
                ),
            }
        )
    index = len(state["rounds"])
    change = {
        "round": index,
        "created_at": now(),
        "changes": changes,
        "targeted_keys": sorted(targeted),
        "accepted": None,
        "rolled_back": False,
    }
    # Write-ahead evidence makes an interrupted candidate recoverable.
    write(run / f"round-{index:02d}/change.json", change)
    state["pending"] = index
    state["status"] = "candidate"
    for item in changes:
        prompt_path(root, item["path"]).write_bytes(item["after"].encode())
        state["prompt_hashes"][item["path"]] = item["after_sha256"]
    save(run, state)


def finish(root: Path, run: Path, state: dict[str, Any], reason: str) -> None:
    require(reason.strip(), "必须说明停止原因")
    require(state["status"] != "finished", "运行已经结束")
    guard(root, state, run)
    if state["pending"] is not None:
        folder = run / f"round-{state['pending']:02d}"
        change = read(folder / "change.json")
        rollback(root, state, change)
        change["cancelled_reason"] = reason
        write(folder / "change.json", change)
        state["pending"] = None
    require(state["rounds"], "没有基线，不能声称完成自进化")
    require(
        state["status"] == "error" or state["rounds"][-1].get("analyzed"),
        "请先记录本轮分析",
    )
    state.update(status="finished", stop_reason=reason, finished_at=now())
    save(run, state)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    initial = commands.add_parser("init")
    selection = initial.add_mutually_exclusive_group()
    selection.add_argument("--suite")
    selection.add_argument("--all", action="store_true")
    initial.add_argument("--max-rounds", type=int, default=3)
    for name in ("evaluate", "record", "apply", "finish"):
        command = commands.add_parser(name)
        command.add_argument("--run", type=Path, required=True)
        if name == "evaluate":
            command.add_argument("--timeout", type=float, default=90)
        elif name == "record":
            command.add_argument("--analysis", type=Path, required=True)
        elif name == "apply":
            command.add_argument("--patch", type=Path, required=True)
        else:
            command.add_argument("--reason", required=True)
    rendering = commands.add_parser("render")
    rendering.add_argument("--report", type=Path, required=True)
    rendering.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "init":
            print(init(ROOT, args))
        elif args.command == "render":
            render(read(args.report), args.output)
            print(args.output)
        else:
            run = args.run.resolve()
            require(
                run.is_relative_to((ROOT / "evaluation/reports/evolution").resolve()),
                "运行目录超出报告范围",
            )
            runtime = ROOT / "evaluation/.runtime"
            runtime.mkdir(exist_ok=True)
            # One mutation/evaluation at a time across runs in this checkout.
            with (runtime / "evolution.lock").open("w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                state = read(run / "state.json")
                require(state["root"] == str(ROOT), "工作区不匹配")
                if args.command == "evaluate":
                    require(args.timeout > 0, "timeout 必须大于零")
                    evaluate(ROOT, run, state, args.timeout)
                elif args.command == "record":
                    record(ROOT, run, state, read(args.analysis))
                elif args.command == "apply":
                    apply(ROOT, run, state, read(args.patch))
                else:
                    finish(ROOT, run, state, args.reason)
                print(run / "index.html")
        return 0
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - CLI error boundary
        print(f"停止：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
