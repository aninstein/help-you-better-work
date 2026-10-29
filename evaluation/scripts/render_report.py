#!/usr/bin/env python3
"""Render a self-contained HTML scorecard from run_live.py output."""

from __future__ import annotations

import argparse
import html
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def bar(label: str, score: float, extra: str = "") -> str:
    safe_label = html.escape(label)
    width = max(0.0, min(100.0, score))
    return (
        f'<div class="bar-row"><div class="bar-label">{safe_label}</div>'
        f'<div class="bar-track"><div class="bar-fill" style="width:{width:.1f}%"></div></div>'
        f'<div class="bar-value">{score:.1f}{html.escape(extra)}</div></div>'
    )


def checks(checks: list[dict[str, Any]]) -> str:
    items = []
    for check in checks:
        state = "pass" if check.get("passed") else "fail"
        mark = "通过" if check.get("passed") else "未通过"
        items.append(
            f'<li class="{state}">{html.escape(check.get("name", "check"))}: {mark} '
            f'({check.get("weight", 0)} 分)</li>'
        )
    return "<ul>" + "".join(items) + "</ul>"


def render(result: dict[str, Any], output: Path) -> None:
    per_mode = result.get("by_mode", {})
    per_domain = result.get("by_domain", {})
    rows = []
    for suite in result.get("suites", []):
        case_cells = "".join(
            f'<td><strong>{html.escape(case["mode"])}</strong><br>{case["score"]}/100{checks(case["checks"])}'
            f'<details><summary>观察</summary><pre>{html.escape(json.dumps(case.get("observed", {}), ensure_ascii=False, indent=2))}</pre>'
            f'<p><em>人工复核：</em>{html.escape("；".join(case.get("manual_rubric", [])))}</p></details></td>'
            for case in suite.get("cases", [])
        )
        memory = suite.get("memory", {})
        rows.append(
            "<tr>"
            f'<td><strong>{html.escape(suite["title"])}</strong><br><small>{html.escape(suite["id"])}</small></td>'
            f'<td>{suite.get("automatic_score", 0)}/100<br><small>主链路 {suite.get("atomic_score", 0)}；记忆 {memory.get("score", 0)}</small></td>'
            + case_cells
            + f'<td><strong>{memory.get("score", 0)}/100</strong>{checks(memory.get("checks", []))}'
            f'<details><summary>跨会话观察</summary><pre>{html.escape(json.dumps(memory.get("observed", {}), ensure_ascii=False, indent=2))}</pre></details></td>'
            + "</tr>"
        )
    note = html.escape(result.get("score_note", ""))
    source = html.escape(result.get("base_url", ""))
    generated = html.escape(result.get("finished_at", ""))
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>双通道 Agent 评测报告</title>
<style>
:root {{ color-scheme: light; --ink:#1e293b; --muted:#64748b; --line:#dbe4ee; --panel:#fff; --bg:#f7fafc; --good:#138a5b; --bad:#c2410c; --accent:#2563eb; }}
* {{ box-sizing:border-box }} body {{ margin:0; background:var(--bg); color:var(--ink); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif; line-height:1.48 }}
main {{ max-width:1600px; margin:auto; padding:32px }} h1 {{ margin:0 0 4px }} h2 {{ margin-top:32px }} .meta,.note {{ color:var(--muted) }} .score {{ margin:24px 0; display:inline-flex; align-items:baseline; gap:8px; padding:18px 24px; background:var(--panel); border:1px solid var(--line); border-radius:14px }} .score b {{ font-size:42px; color:var(--accent) }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(360px,1fr)); gap:18px }} .card {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:18px }} .bar-row {{ display:grid; grid-template-columns:130px 1fr 62px; align-items:center; gap:10px; margin:10px 0 }} .bar-label,.bar-value {{ font-size:13px }} .bar-track {{ height:13px; background:#e6edf5; border-radius:99px; overflow:hidden }} .bar-fill {{ height:100%; background:linear-gradient(90deg,#60a5fa,#2563eb); border-radius:inherit }}
table {{ width:100%; border-collapse:collapse; background:var(--panel); font-size:13px }} th,td {{ border:1px solid var(--line); vertical-align:top; padding:12px; min-width:180px }} th {{ position:sticky; top:0; background:#edf4ff; text-align:left }} ul {{ padding-left:18px; margin:8px 0 }} li {{ margin:3px 0 }} .pass {{ color:var(--good) }} .fail {{ color:var(--bad); font-weight:600 }} pre {{ max-height:260px; overflow:auto; white-space:pre-wrap; background:#f8fafc; padding:10px; border-radius:6px }} details {{ margin-top:9px }} small {{ color:var(--muted) }} .table-wrap {{ overflow:auto; border-radius:10px; border:1px solid var(--line) }}
</style></head><body><main>
<h1>双通道客服 Agent 评测报告</h1>
<p class="meta">运行目标：{source} · 完成时间：{generated} · 场景包：{result.get("suite_count", 0)}</p>
<div class="score"><span>自动证据分</span><b>{result.get("automatic_score", 0)}</b><span>/ 100</span></div>
<p class="note">{note}</p>
<div class="grid"><section class="card"><h2>按交互模式</h2>{''.join(bar(name, score) for name, score in per_mode.items())}</section>
<section class="card"><h2>按场景群</h2>{''.join(bar(name, score) for name, score in per_domain.items())}</section></div>
<h2>逐题证据与人工复核清单</h2>
<p class="note">阻断题把“未生成敏感答案”与“Talker 是否看到了友好拒答”分开计分；因此能区分安全正确和体验完整。</p>
<div class="table-wrap"><table><thead><tr><th>场景</th><th>总分</th><th>direct</th><th>hinter_hint</th><th>hinter_block</th><th>multi_question</th><th>rapid_burst</th><th>跨会话记忆</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
</main></body></html>"""
    output.write_text(document, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="run_live.py 生成的 JSON 文件")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = json.loads(args.report.read_text(encoding="utf-8"))
    output = args.output or args.report.with_suffix(".html")
    render(result, output)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
