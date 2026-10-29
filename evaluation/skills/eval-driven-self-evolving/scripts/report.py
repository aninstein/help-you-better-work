"""Stable, escaped, dependency-free report renderer shared by all loop stages."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

TEMPLATE = Path(__file__).resolve().parents[1] / "assets/report.html"
STATUS = {
    "ready": "等待基线",
    "evaluating": "正在评测",
    "needs_analysis": "等待归因",
    "analyzed": "已完成归因",
    "candidate": "候选待复测",
    "error": "运行异常",
    "finished": "已结束",
}
KINDS = {
    "memory_prompt": "记忆存储 prompt",
    "core_prompt": "核心 prompt · 只报告",
    "code": "代码 · 只报告",
    "context": "实际记忆 · 只报告",
    "evaluation": "评测协议 · 只报告",
    "infra": "基础设施 · 只报告",
}


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def local_time(value: str) -> str:
    if not value:
        return "尚未运行"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    if parsed.tzinfo is None:
        return value
    return parsed.astimezone(ZoneInfo("Asia/Shanghai")).strftime(
        "%Y-%m-%d %H:%M:%S UTC+8"
    )


def dump(value: Any) -> str:
    return "<pre>" + esc(json.dumps(value, ensure_ascii=False, indent=2)) + "</pre>"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def badge(text: str, tone: str = "neutral") -> str:
    return f'<span class="tag {tone}">{esc(text)}</span>'


def cases_of(result: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return [
        (suite, case)
        for suite in result.get("suites", [])
        for case in [
            *suite.get("cases", []),
            {"id": "memory_chain", "mode": "memory_chain", **suite.get("memory", {})},
        ]
    ]


def passed(case: dict[str, Any]) -> bool:
    return bool(case.get("checks")) and all(
        c.get("passed") is True for c in case["checks"]
    )


def bars(values: dict[str, Any]) -> str:
    if not values:
        return '<p class="empty">暂无有效数据</p>'
    rows = []
    for label, score in values.items():
        score = float(score)
        width = max(0, min(100, score))
        rows.append(
            f'<div class="bar"><span>{esc(label)}</span><div class="track" role="img" '
            f'aria-label="{esc(label)}：{score:.1f} / 100"><div class="fill" '
            f'style="width:{width:.1f}%"></div></div><span class="number">{score:.1f}</span></div>'
        )
    return "".join(rows)


def full_answers(observed: dict[str, Any]) -> str:
    evidence = observed.get("full_evidence", {})
    answers = {}
    # Prefer the Hinter snapshot for pending/failed draft evidence, Talker for published text.
    for key, snapshot in sorted(evidence.items()):
        for answer in snapshot.get("answers", []):
            identity = (
                key.split("/api/v1/sessions/")[-1],
                answer.get("id", str(len(answers))),
            )
            answers[identity] = answer
    if answers:
        return "".join(
            f'<p class="label">返回 {index} · {esc(answer.get("status", "未知状态"))}</p>'
            f'<div class="answer">{esc(answer.get("text") or "（返回文本为空）")}</div>'
            for index, answer in enumerate(answers.values(), 1)
        )
    if observed.get("full_capture"):
        return '<p class="empty">本题未获得 Answer；请对照状态、错误与原始快照。</p>'
    text = observed.get("answer_excerpt", observed.get("recall_answer_excerpt", ""))
    return (
        badge("历史摘录，完整返回未采集", "warn")
        + f'<div class="answer">{esc(text or "（无返回文本）")}</div>'
    )


def case_html(suite: dict[str, Any], case: dict[str, Any]) -> str:
    ok = passed(case)
    checks = case.get("checks", [])
    error = any(c.get("name", "").endswith("runner_error") for c in checks)
    status = "自动通过" if ok else "运行错误" if error else "未通过"
    observed = case.get("observed", {})
    check_rows = "".join(
        f"<tr><td>{esc(c.get('name', ''))}</td><td>{badge('通过' if c.get('passed') else '未通过', 'pass' if c.get('passed') else 'fail')}</td>"
        f'<td class="number">{esc(c.get("weight", 0))}</td></tr>'
        for c in checks
    )
    rubric = case.get("manual_rubric", [])
    if not rubric:
        rubric = ["复核记忆是否准确、是否来自已确认内容、是否保持角色隔离。"]
    return (
        f'<details class="case" data-status="{"pass" if ok else "fail"}"><summary>'
        + badge(status, "pass" if ok else "fail")
        + f"<span><strong>{esc(suite.get('title', suite['id']))} · {esc(case.get('mode', ''))}</strong>"
        f'<span class="case-meta"> · {esc(case.get("score", "—"))} / 100</span><br>'
        f'<span class="case-meta">{esc(suite["id"])}/{esc(case["id"])}</span></span></summary>'
        '<div class="case-body"><div class="grid"><div><h3>题目输入与私有提示</h3>'
        + (
            dump(case["inputs"])
            if "inputs" in case
            else '<p class="muted">历史报告未保存原始输入，请对照题库；此处不以当前题库冒充历史输入。</p>'
        )
        + "</div><div><h3>期望与自动检查</h3>"
        + dump(case.get("expected", {}))
        + '<table class="checks"><thead><tr><th>检查点</th><th>结果</th><th>权重</th></tr></thead>'
        f"<tbody>{check_rows}</tbody></table></div></div><h3>实际返回内容</h3>"
        + full_answers(observed)
        + "<details><summary>原始观察与完整快照</summary>"
        + dump(observed)
        + "</details>"
        + "<details><summary>人工量表 · 待复核</summary><ul>"
        + "".join("<li>" + esc(item) + "</li>" for item in rubric)
        + "</ul></details></div></details>"
    )


def trend(state: dict[str, Any]) -> str:
    rounds = state.get("rounds", [])
    scored = [item for item in rounds if "score" in item]
    if not scored:
        return '<p class="empty">尚无评测分数</p>'
    points, accepted_points, labels = [], [], []
    effective = None
    for position, item in enumerate(scored):
        x = 35 + position * 530 / max(1, len(scored) - 1)
        y = 110 - max(0, min(100, item["score"])) * 0.85
        if item.get("accepted"):
            effective = item["score"]
        if effective is not None:
            accepted_points.append(f"{x:.1f},{110 - effective * 0.85:.1f}")
        tone = "point" if item.get("accepted") else "rejected"
        points.append(
            f'<circle class="{tone}" cx="{x:.1f}" cy="{y:.1f}" r="4"><title>'
            f"R{item['index']:02d}: {item['score']}，{'接受' if item.get('accepted') else '回退或错误'}</title></circle>"
        )
        labels.append(
            f'<text x="{x:.1f}" y="135" text-anchor="middle">R{item["index"]:02d}</text>'
        )
    rows = "".join(
        f'<tr><td>R{item["index"]:02d}</td><td class="number">{esc(item.get("score", "—"))}</td>'
        f"<td>{badge('接受' if item.get('accepted') else '回退 / 错误', 'pass' if item.get('accepted') else 'fail')}</td>"
        f"<td>{esc(item.get('error', ''))}</td></tr>"
        for item in rounds
    )
    return (
        '<svg class="spark" viewBox="0 0 600 145" role="img" aria-label="各轮候选分数与有效版本分数趋势">'
        '<path class="axis" d="M25 25 H575 M25 67 H575 M25 110 H575"/>'
        f'<polyline class="line" points="{" ".join(accepted_points)}"/>{"".join(points + labels)}</svg>'
        '<p class="muted">实心点：接受的版本；空心点：未接受候选；折线：有效版本。下表提供精确分数。</p>'
        '<div class="table-wrap"><table><thead><tr><th>轮次</th><th>候选分数</th><th>处理</th><th>异常</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div>"
    )


def issue_html(issue: dict[str, Any]) -> str:
    return (
        '<div class="issue"><h3>'
        + esc(issue.get("title", "待分析"))
        + "</h3>"
        + badge(KINDS.get(issue.get("kind"), "待归因"), "warn")
        + "<p>"
        + esc(issue.get("diagnosis", ""))
        + "</p>"
        + '<p><span class="label">验证假设：</span>'
        + esc(issue.get("hypothesis", ""))
        + "</p>"
        + '<p><span class="label">处理：</span>'
        + esc(issue.get("action", ""))
        + "</p>"
        + '<p><span class="label">原因 / 后续：</span>'
        + esc(issue.get("reason", ""))
        + "</p>"
        + "<details><summary>关联失败证据</summary>"
        + dump(issue.get("evidence", []))
        + "</details></div>"
    )


def evolution(state: dict[str, Any], run: Path | None) -> str:
    if run is None:
        return '<p class="empty">这是单次历史评测的离线重渲染，未执行自进化轮次。</p>'
    parts = []
    for item in state.get("rounds", []):
        folder = run / f"round-{item['index']:02d}"
        analysis, change = load(folder / "analysis.json"), load(folder / "change.json")
        heading = f"R{item['index']:02d} · " + (
            "基线" if item["index"] == 0 else "prompt 优化与复测"
        )
        content = f'<div class="round"><div class="round-heading"><h3>{heading}</h3>'
        content += (
            badge(
                "有效" if item.get("accepted") else "未接受 / 异常",
                "pass" if item.get("accepted") else "fail",
            )
            + "</div>"
        )
        content += f'<p class="muted">{esc(local_time(item.get("started_at", "")))} · 分数 {esc(item.get("score", "—"))}</p>'
        if (folder / "report.json").exists():
            # Relative links work both from index.html and nested round report.html.
            content += f'<p><a href="@@ROOT_LINK@@round-{item["index"]:02d}/report.html">逐题 HTML</a> · <a href="@@ROOT_LINK@@round-{item["index"]:02d}/report.json">原始 JSON</a></p>'
        if item.get("error"):
            content += '<p class="fail">' + esc(item["error"]) + "</p>"
        content += (
            "<p>"
            + esc(analysis.get("summary", "等待当前 agent 完成本轮归因。"))
            + "</p>"
        )
        content += "".join(issue_html(issue) for issue in analysis.get("issues", []))
        for patch in change.get("changes", []):
            content += "<details><summary>改动 · " + esc(patch["path"]) + "</summary>"
            content += (
                '<p class="muted">'
                + esc(patch["before_sha256"])
                + " → "
                + esc(patch["after_sha256"])
                + "</p>"
            )
            content += "<pre>" + esc(patch["diff"]) + "</pre></details>"
        if change.get("rolled_back"):
            content += (
                '<p class="warn">已回退该轮 prompt，当前有效版本保持上一接受轮次。</p>'
            )
        if item.get("regressions"):
            content += (
                "<details><summary>回退的检查点</summary>"
                + dump(item["regressions"])
                + "</details>"
            )
        parts.append(content + "</div>")
    if state.get("pending") is not None:
        pending = load(run / f"round-{state['pending']:02d}/change.json")
        parts.append(
            '<div class="round"><h3>候选已应用，等待重启复测</h3><p class="warn">尚无新分数，不代表已验证。</p>'
            + "".join(
                "<details><summary>"
                + esc(p["path"])
                + "</summary><pre>"
                + esc(p["diff"])
                + "</pre></details>"
                for p in pending.get("changes", [])
            )
            + "</div>"
        )
    return (
        '<div class="timeline">' + "".join(parts) + "</div>"
        if parts
        else '<p class="empty">尚未运行基线</p>'
    )


def unresolved(state: dict[str, Any]) -> str:
    ledger = state.get("ledger", {})
    # A rejected candidate may hide a failure of the effective version. Include both views.
    selected = {
        key: item
        for key, item in ledger.items()
        if item.get("failed") or item.get("effective_failed")
    }
    if not selected:
        return (
            '<p class="empty">暂无未解记录；未运行或未归因时不能据此声称质量通过。</p>'
        )
    rows = []
    for key, item in sorted(
        selected.items(), key=lambda pair: (-bool(pair[1].get("frozen")), pair[0])
    ):
        issue = item.get("analysis", {})
        mode = (
            "三轮未解 · 已冻结"
            if item.get("frozen")
            else KINDS.get(issue.get("kind"), "待归因")
        )
        reason = issue.get("reason", "尚未分析，不能推断根因。")
        if item.get("frozen"):
            reason = "连续三个优化轮次仍未通过，不再自动针对该项改写。" + reason
        rows.append(
            "<tr><td><code>"
            + esc(key)
            + "</code></td><td>"
            + badge(mode, "warn")
            + f"</td><td>{item.get('streak', 0)} / {item.get('attempts', 0)}</td><td>"
            + esc(reason)
            + "</td></tr>"
        )
    return (
        '<p class="muted">包括最后候选与当前有效版本的失败项；次数为连续失败轮数 / 针对性修改次数。</p>'
        '<div class="table-wrap"><table><thead><tr><th>失败证据</th><th>处理边界</th><th>轮数 / 修改</th><th>原因与后续</th></tr></thead><tbody>'
        + "".join(rows)
        + "</tbody></table></div>"
    )


def render(
    result: dict[str, Any],
    output: Path,
    *,
    state: dict[str, Any] | None = None,
    run: Path | None = None,
    current_note: str = "本文件对应单次评测",
) -> None:
    state = state or {}
    cases = cases_of(result)
    if run is None and not state.get("ledger"):
        state = dict(state)
        state["ledger"] = {
            f"{suite['id']}/{case['id']}/{check['name']}": {
                "failed": True,
                "analysis": {
                    "reason": "历史报告尚未归因；此处展示原始失败检查点，不推断根因。"
                },
            }
            for suite, case in cases
            for check in case.get("checks", [])
            if not check.get("passed")
        }
    successes = sum(passed(case) for _, case in cases)
    total = len(cases)
    score = result.get("automatic_score", "—")
    status = STATUS.get(state.get("status"), "历史评测 · 离线重渲染")
    overview = (
        '<div class="banner">'
        + esc(status)
        + " · "
        + esc(state.get("stop_reason") or current_note)
        + "</div>"
    )
    metrics = [
        ("自动证据分 / 100", score),
        ("自动通过题目", f"{successes} / {total}"),
        ("未通过 / 错误", total - successes),
        (
            "优化轮次 / 预算",
            f"{max(0, len(state.get('rounds', [])) - 1)} / {state.get('max_rounds', '—')}",
        ),
    ]
    overview += (
        '<div class="metrics">'
        + "".join(
            '<div class="metric"><span>'
            + esc(label)
            + "</span><b>"
            + esc(value)
            + "</b></div>"
            for label, value in metrics
        )
        + "</div>"
    )
    overview += (
        '<p class="muted">'
        + esc(result.get("score_note", "尚无有效实跑结果。"))
        + "</p>"
    )
    overview += '<p class="scope">修改范围：独立 prompt 白名单；核心 prompt 仍嵌在代码中，只报告。实际 context / Wiki / 源码保持只读。完整返回与私有提示仅供本地评测审阅。</p>'
    score_trend = trend(state)
    if run is None and result.get("automatic_score") is not None:
        score_trend = bars({"单次自动分": result["automatic_score"]})
        score_trend += '<p class="muted">历史单次评测，未执行自进化；没有轮次趋势。</p>'
    charts = (
        '<div class="grid"><div class="panel"><h2>各轮分数</h2>'
        + score_trend
        + '</div><div class="panel"><h2>交互模式</h2>'
        + bars(result.get("by_mode", {}))
        + "</div></div>"
    )
    charts += (
        '<div class="panel" style="margin-top:22px"><h2>场景分布</h2>'
        + bars(result.get("by_domain", {}))
        + "</div>"
    )
    values = {
        "META": esc(
            f"{result.get('suite_count', 0)} 个场景包 · {local_time(result.get('finished_at', state.get('created_at', '')))} · {status}"
        ),
        "OVERVIEW": overview,
        "TREND": charts,
        "EVOLUTION": evolution(state, run),
        "ISSUES": unresolved(state),
        "CASE_NOTE": esc(current_note),
        "CASES": "".join(case_html(suite, case) for suite, case in cases),
        "FOOTER": esc(
            f"服务：{result.get('base_url', '未启动')} · 人工复核状态：待复核"
        ),
    }
    template = TEMPLATE.read_text(encoding="utf-8")
    # Substitute once, so report data containing marker-like text stays inert.
    document = re.sub(r"@@([A-Z_]+)@@", lambda match: values[match[1]], template)
    prefix = "" if run is None or output.parent.resolve() == run.resolve() else "../"
    document = document.replace('href="@@ROOT_LINK@@', 'href="' + prefix)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")


def render_run(run: Path, state: dict[str, Any]) -> None:
    effective = state.get("effective_round")
    report = (
        load(run / f"round-{effective:02d}/report.json")
        if effective is not None
        else {}
    )
    view_state = json.loads(json.dumps(state))
    # Include effective failures even when the most recent rejected candidate passed them.
    effective_keys = set()
    for suite, case in cases_of(report):
        for check in case.get("checks", []):
            if not check.get("passed"):
                effective_keys.add(f"{suite['id']}/{case['id']}/{check['name']}")
    for key in effective_keys:
        view_state["ledger"].setdefault(key, {})["effective_failed"] = True
    note = (
        f"当前有效版本 R{effective:02d}；逐题展示该版本证据，候选结果见轮次记录"
        if effective is not None
        else "尚无有效版本；运行异常与候选证据见轮次记录"
    )
    render(report, run / "index.html", state=view_state, run=run, current_note=note)
    # Refresh all round reports after analysis/finish so standalone files include the evolution log.
    for item in state.get("rounds", []):
        folder = run / f"round-{item['index']:02d}"
        if (folder / "report.json").is_file():
            render(
                load(folder / "report.json"),
                folder / "report.html",
                state=view_state,
                run=run,
                current_note=f"该文件为 R{item['index']:02d} 候选证据；是否生效见轮次处理状态",
            )
