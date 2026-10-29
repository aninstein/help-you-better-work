#!/usr/bin/env python3
"""Build the consolidated evaluation and self-update report from saved run JSONs."""
from __future__ import annotations

import html
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "evaluation" / "reports" / "iterations"
OUT_MD = ROOT / "evaluation" / "reports" / "综合评测与自更新轮次报告.md"
OUT_HTML = ROOT / "evaluation" / "reports" / "综合评测与自更新轮次报告.html"

ROUND_NOTES = {
    "round-00": ("基线", "初始全量基线；暴露出评测覆盖已扩展、但实时数据及复杂对话链路尚未稳定。"),
    "round-01": ("全量迭代", "修正实时评测 runner 与新会话/问题解析流程；补齐评测状态、答案与工具工作记录采集。"),
    "round-02": ("全量迭代", "继续校准答案状态、提示约束及评测隔离；验证公司应用、披露和在线查询场景进入统一全量评测。"),
    "round-03": ("全量迭代", "完善中文回答、提示/禁止边界、工具调用策略和多问题处理；全量分升至 72.7。"),
    "round-04": ("全量迭代", "扩展内部应用连接器及检索证据流程，修正多轮提示与场景路由；全量分升至 74.4。"),
    "round-05": ("全量迭代", "增强天气与公开公司数据检索、来源筛选及回答时限；识别天气/SEC 检索挂起和证据链缺失。"),
    "round-06": ("全量迭代", "修正追问/回顾中的约束误判，增强多问题拆解、公司应用调用和检索后最终回答生成；达到阶段最高 78.2。"),
    "round-07-weather": ("定向诊断：天气", "验证 Open-Meteo 实时预报、来源引用和城市消歧；杭州西湖地理消歧仍曾落到美国同名地点。"),
    "round-07-sec": ("定向诊断：SEC", "SEC 公司匹配检索仍挂起；发现 Alphabet 多股票类别造成重复 CIK 匹配并做去重。"),
    "round-08-sec": ("定向诊断：SEC", "SEC 源检索逻辑继续验证；结果仍在生成阶段，问题定位转向答案发布链路。"),
    "round-09-debug": ("定向诊断：披露", "为公开检索增加受控诊断日志，确认检索结果和 SEC 验证已经返回，评测端仍未收到最终回答。"),
    "round-10-debug": ("定向诊断：披露", "复核工具追踪与响应状态；进一步缩小问题为检索结束后的答复生成/发布等待。"),
    "round-11-debug": ("定向诊断：披露", "日志显示候选来源读取、支持性检查和 SEC 验证成功，评测观察到的问题仍处于 generating。"),
    "round-12-sec": ("定向诊断：SEC", "在来源读取和发布链路调整后复测；多数题仍只有确认语，最终回答未及时完成。"),
    "round-13-sec": ("定向诊断：SEC", "部分 SEC 直问开始满足工具证据要求，但生成/验证仍有挂起；多问题来源引用仍有缺口。"),
    "round-14-sec": ("定向诊断：SEC", "增加检索后生成窗口并在已完成来源上重试最终回答；定向分升至 69.7，直接问答和多问有改善，提示证据及快速连发仍弱。"),
    "round-15": ("全量复测", "结合天气地点规范化、SEC 去重、检索后最终回答重试和生成时限调整复测，取得 81.0/100，首次越过 80 分目标。"),
}


def load_runs():
    out = []
    for p in sorted(RUNS.glob("*/dual-channel-eval-*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        d["_path"] = p
        d["_folder"] = p.parent.name
        out.append(d)
    return out


def rel(p: Path) -> str:
    return p.relative_to(ROOT / "evaluation" / "reports").as_posix()


def inline(text: str) -> str:
    safe = html.escape(text)
    safe = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<a href='\2'>\1</a>", safe)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)


def esc(value: object) -> str:
    return html.escape(str(value))


def bar(label: str, score: float, delta: str = "") -> str:
    width = max(0, min(100, float(score)))
    delta_html = f"<small class='bar-delta'>{esc(delta)}</small>" if delta else ""
    return (
        f"<div class='bar-row'><div class='bar-label'>{esc(label)}</div>"
        f"<div class='bar-track' role='meter' aria-label='{esc(label)}' aria-valuemin='0' aria-valuemax='100' aria-valuenow='{width:.1f}'>"
        f"<div class='bar-fill' style='width:{width:.1f}%'></div></div>"
        f"<div class='bar-value'>{width:.1f}{delta_html}</div></div>"
    )


def report_html(runs, full, targeted, best, latest_full) -> str:
    from datetime import datetime, timezone

    latest_score = float(latest_full.get("automatic_score", 0)) if latest_full else 0
    initial_score = float(full[0].get("automatic_score", 0)) if full else 0
    score_delta = latest_score - initial_score
    initial_domains = full[0].get("by_domain", {}) if full else {}
    prev_full = full[-2] if len(full) > 1 else None
    prev_domains = prev_full.get("by_domain", {}) if prev_full else {}
    modes = latest_full.get("by_mode", {}) if latest_full else {}
    domains = latest_full.get("by_domain", {}) if latest_full else {}
    weakest_domains = sorted(domains.items(), key=lambda item: item[1])
    domain_rows_list = []
    for name, score in sorted(domains.items(), key=lambda item: item[1]):
        if name in initial_domains:
            delta = float(score) - float(initial_domains[name])
            delta_cell = f"<td class='{'negative' if delta < 0 else 'positive'}'>{delta:+.1f}</td>"
        else:
            delta_cell = "<td>—</td>"
        start_score = float(initial_domains.get(name, score))
        domain_rows_list.append(
            f"<tr><td>{esc(name)}</td><td>{start_score:.1f}</td><td>{float(score):.1f}</td>"
            f"{delta_cell}</tr>"
        )
    domain_rows = "".join(domain_rows_list)

    full_rows = []
    prior = None
    for d in full:
        folder = d["_folder"]
        score = float(d.get("automatic_score", 0))
        mode = d.get("by_mode", {})
        title, note = ROUND_NOTES.get(folder, ("全量迭代", "见本轮逐题报告。"))
        base = d["_path"].with_suffix("")
        json_link = rel(d["_path"])
        html_link = rel(base.with_suffix(".html"))
        round_class = "current-round" if d is latest_full else ""
        delta = "—" if prior is None else f"{score-prior:+.1f}"
        full_rows.append(
            f"<tr class='{round_class}'><td><strong>{esc(folder)}</strong>"
            f"{' <span class=badge>最新</span>' if d is latest_full else ''}</td>"
            f"<td>{esc(d.get('started_at','未知'))}</td><td><strong>{score:.1f}</strong></td>"
            f"<td class='{'positive' if prior is not None and score >= prior else 'negative' if prior is not None else ''}'>{delta}</td>"
            f"<td>{float(mode.get('direct',0)):.1f} / {float(mode.get('hinter_hint',0)):.1f} / {float(mode.get('multi_question',0)):.1f} / {float(mode.get('rapid_burst',0)):.1f}</td>"
            f"<td><details class='note-details'><summary>{esc(title)} · 更新摘要</summary><p>{esc(note)}</p></details></td>"
            f"<td><a href='{esc(json_link)}'>JSON</a><span class='sep'>·</span><a href='{esc(html_link)}'>逐题报告</a></td></tr>"
        )
        prior = score

    target_rows = []
    for d in targeted:
        suite = d.get("suites", [{}])[0]
        title, note = ROUND_NOTES.get(d["_folder"], ("定向诊断", "见本轮逐题报告。"))
        base = d["_path"].with_suffix("")
        target_rows.append(
            f"<tr><td><strong>{esc(d['_folder'])}</strong></td><td>{esc(suite.get('domain','—'))}</td>"
            f"<td>{float(d.get('automatic_score',0)):.1f}</td><td>{esc(d.get('started_at','未知'))}</td>"
            f"<td><details class='note-details'><summary>{esc(title)} · 诊断摘要</summary><p>{esc(note)}</p></details></td>"
            f"<td><a href='{esc(rel(d['_path']))}'>JSON</a><span class='sep'>·</span><a href='{esc(rel(base.with_suffix('.html')))}'>逐题报告</a></td></tr>"
        )

    weak_suites = sorted(latest_full.get("suites", []) if latest_full else [], key=lambda s: s.get("automatic_score", 0))[:6]
    weak_cards = []
    for suite in weak_suites:
        failed = sorted({
            check.get("name", "check")
            for case in suite.get("cases", [])
            for check in case.get("checks", [])
            if not check.get("passed")
        })
        case_rows = "".join(
            "<tr>" + f"<td>{esc(case.get('id','—'))}</td><td>{esc(case.get('mode','—'))}</td>"
            + f"<td>{float(case.get('score',0)):.0f}</td><td>{', '.join(esc(ch.get('name','')) for ch in case.get('checks',[]) if not ch.get('passed')) or '全部通过'}</td></tr>"
            for case in suite.get("cases", [])
        )
        weak_cards.append(
            f"<details class='weak-card'><summary><span>{esc(suite.get('title',suite.get('id','')))}</span>"
            f"<span class='weak-score'>{float(suite.get('automatic_score',0)):.1f}<small>/100</small></span></summary>"
            f"<p class='meta'>{esc(suite.get('domain',''))} · 主要失败项：{', '.join(esc(x) for x in failed) or '无'}</p>"
            f"<div class='table-wrap'><table><thead><tr><th>题目</th><th>模式</th><th>分数</th><th>未通过判据</th></tr></thead><tbody>{case_rows}</tbody></table></div></details>"
        )

    history_links = "".join(
        f"<a class='round-link' href='#{esc(d['_folder'])}'>{esc(d['_folder'])}</a>"
        for d in full
    )
    update_cards = "".join(
        f"<article class='update-card' id='{esc(d['_folder'])}'><div class='update-heading'><span>{esc(d['_folder'])}</span>"
        f"<strong>{float(d.get('automatic_score',0)):.1f}</strong><small>/100</small></div>"
        f"<p>{esc(ROUND_NOTES.get(d['_folder'], ('全量迭代','见逐题报告。'))[1])}</p></article>"
        for d in full
    )
    latest_time = esc(latest_full.get("finished_at", latest_full.get("started_at", "未知"))) if latest_full else "—"
    score_note = "已超过目标" if latest_score > 80 else "尚未达到目标"
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>综合评测与自更新轮次报告</title>
<style>
:root {{ color-scheme:light; --ink:#1e293b; --muted:#64748b; --line:#dbe4ee; --panel:#fff; --bg:#f7fafc; --good:#138a5b; --bad:#c2410c; --accent:#2563eb; --soft:#edf4ff; --shadow:0 8px 24px rgba(30,41,59,.045); }}
* {{ box-sizing:border-box }} html {{ scroll-behavior:smooth; scroll-padding-top:80px }} body {{ margin:0; background:var(--bg); color:var(--ink); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif; line-height:1.52 }}
a {{ color:#1d4ed8; text-decoration:none }} a:hover {{ text-decoration:underline }} a:focus-visible,summary:focus-visible {{ outline:3px solid #93c5fd; outline-offset:3px; border-radius:4px }}
main {{ max-width:1600px; margin:auto; padding:32px }} h1 {{ margin:0 0 8px; font-size:32px; letter-spacing:-.035em }} h2 {{ margin:0 0 16px; font-size:19px; letter-spacing:-.02em }} h3 {{ margin:28px 0 12px }} p {{ margin:8px 0 }} .meta,.note {{ color:var(--muted) }} .eyebrow {{ margin:0 0 8px; color:#476486; font-size:12px; font-weight:700; letter-spacing:.1em; text-transform:uppercase }}
.topbar {{ display:flex; align-items:center; justify-content:space-between; gap:20px; padding-bottom:20px; border-bottom:1px solid var(--line) }} .topbar .meta {{ margin:4px 0 0; font-size:13px }} .brand-mark {{ display:flex; gap:8px; align-items:center; color:#64748b; font-size:12px; white-space:nowrap }} .brand-mark i {{ width:9px; height:9px; border-radius:50%; background:var(--accent); box-shadow:0 0 0 4px #dbeafe }}
.hero {{ display:grid; grid-template-columns:minmax(0,1fr) auto; gap:24px; align-items:center; margin:24px 0 18px; padding:24px 28px; background:linear-gradient(115deg,#fff 0%,#fff 58%,#f0f6ff 100%); border:1px solid var(--line); border-radius:16px; box-shadow:var(--shadow) }} .hero-copy p {{ max-width:760px; color:var(--muted) }} .score {{ min-width:220px; padding:15px 20px; display:grid; grid-template-columns:auto auto; align-items:end; column-gap:6px; background:var(--panel); border:1px solid var(--line); border-radius:14px; text-align:right }} .score span:first-child {{ grid-column:1/-1; color:var(--muted); text-align:left; font-size:12px }} .score b {{ font-size:46px; line-height:1.1; color:var(--accent); font-variant-numeric:tabular-nums }} .score span:last-child {{ padding-bottom:5px; color:var(--muted) }} .goal {{ display:inline-flex; margin-top:8px; align-items:center; gap:7px; color:var(--good); font-size:13px; font-weight:650 }} .goal-dot {{ width:7px; height:7px; border-radius:50%; background:currentColor }}
.stat-grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin:18px 0 24px }} .stat {{ padding:16px 18px; background:var(--panel); border:1px solid var(--line); border-radius:12px }} .stat span {{ display:block; color:var(--muted); font-size:12px }} .stat strong {{ display:block; margin-top:4px; font-size:23px; font-variant-numeric:tabular-nums }} .stat em {{ color:var(--muted); font-size:12px; font-style:normal }}
.nav {{ position:sticky; top:0; z-index:5; display:flex; gap:7px; overflow:auto; margin:0 -4px 22px; padding:9px 4px; background:rgba(247,250,252,.94); backdrop-filter:blur(12px); border-bottom:1px solid var(--line) }} .nav a {{ flex:none; padding:7px 11px; color:#475569; font-size:13px; border-radius:8px }} .nav a:hover {{ background:#eaf1fa; text-decoration:none }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr)); gap:18px }} .card {{ min-width:0; padding:20px; background:var(--panel); border:1px solid var(--line); border-radius:14px; box-shadow:var(--shadow) }} .card h2 {{ margin-bottom:18px }} .bar-row {{ display:grid; grid-template-columns:minmax(110px,150px) minmax(70px,1fr) 74px; align-items:center; gap:12px; margin:13px 0 }} .bar-label {{ overflow:hidden; color:#334155; font-size:13px; text-overflow:ellipsis; white-space:nowrap }} .bar-value {{ text-align:right; font-size:13px; font-variant-numeric:tabular-nums }} .bar-delta {{ display:block; color:var(--muted); font-size:11px }} .bar-track {{ height:10px; overflow:hidden; background:#e6edf5; border-radius:99px }} .bar-fill {{ height:100%; background:linear-gradient(90deg,#60a5fa,#2563eb); border-radius:inherit }}
.section {{ margin-top:30px }} .section-head {{ display:flex; justify-content:space-between; align-items:end; gap:12px; margin-bottom:12px }} .section-head h2 {{ margin:0 }} .section-head p {{ margin:0; color:var(--muted); font-size:13px }} .table-wrap {{ overflow:auto; border:1px solid var(--line); border-radius:11px; background:var(--panel); box-shadow:var(--shadow) }} table {{ width:100%; border-collapse:collapse; font-size:13px }} th,td {{ min-width:105px; padding:11px 12px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top }} th {{ position:sticky; top:49px; z-index:1; background:#edf4ff; color:#334155; font-weight:650; white-space:nowrap }} tbody tr:last-child td {{ border-bottom:0 }} tbody tr:hover {{ background:#f8fbff }} .current-round {{ background:#f2f7ff }} .positive {{ color:var(--good); font-weight:650 }} .negative {{ color:var(--bad); font-weight:650 }} .badge {{ display:inline-block; margin-left:6px; padding:2px 7px; color:#1d4ed8; background:#dbeafe; border-radius:999px; font-size:10px; vertical-align:middle }} .sep {{ margin:0 6px; color:#94a3b8 }} .note-details summary {{ color:#334155; cursor:pointer }} .note-details p {{ max-width:460px; color:var(--muted); font-size:12px }}
.callout {{ margin:14px 0 0; padding:12px 14px; color:#7c2d12; background:#fff7ed; border:1px solid #fed7aa; border-radius:10px; font-size:13px }} .round-links {{ display:flex; flex-wrap:wrap; gap:7px; margin:10px 0 16px }} .round-link {{ padding:5px 9px; color:#334155; background:#eef2f7; border-radius:7px; font-size:12px }}
.update-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:11px }} .update-card {{ padding:14px 16px; background:var(--panel); border:1px solid var(--line); border-radius:12px }} .update-heading {{ display:flex; align-items:baseline; gap:5px }} .update-heading span {{ margin-right:auto; color:#475569; font-size:12px; font-weight:650 }} .update-heading strong {{ color:var(--accent); font-size:20px; font-variant-numeric:tabular-nums }} .update-heading small {{ color:var(--muted) }} .update-card p {{ margin:8px 0 0; color:var(--muted); font-size:12px }}
.finding-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:12px }} .finding {{ padding:16px; background:var(--panel); border:1px solid var(--line); border-radius:12px }} .finding h3 {{ margin:0 0 7px; font-size:15px }} .finding p {{ color:var(--muted); font-size:13px }} .finding .score-line {{ color:var(--accent); font-weight:700; font-variant-numeric:tabular-nums }}
.weak-list {{ display:grid; gap:9px }} .weak-card {{ padding:0 15px; background:var(--panel); border:1px solid var(--line); border-radius:11px }} .weak-card summary {{ display:flex; justify-content:space-between; align-items:center; gap:14px; padding:13px 0; cursor:pointer; font-weight:650 }} .weak-score {{ color:var(--bad); white-space:nowrap; font-variant-numeric:tabular-nums }} .weak-score small {{ color:var(--muted); font-weight:400 }} .weak-card .meta {{ font-size:12px }} .weak-card .table-wrap {{ margin:12px 0; box-shadow:none }} .weak-card th {{ position:static }}
.loop-grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px }} .loop-step {{ padding:16px; background:var(--panel); border:1px solid var(--line); border-radius:12px }} .loop-step strong {{ display:block; margin-bottom:6px; color:#1d4ed8 }} .loop-step p {{ margin:0; color:var(--muted); font-size:13px }} footer {{ margin-top:34px; padding:16px 0; border-top:1px solid var(--line); color:var(--muted); font-size:12px }}
@media(max-width:760px) {{ main {{ padding:18px 14px }} h1 {{ font-size:26px }} .topbar {{ align-items:flex-start }} .brand-mark {{ display:none }} .hero {{ grid-template-columns:1fr; padding:20px }} .score {{ width:max-content; min-width:190px }} .stat-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)) }} .bar-row {{ grid-template-columns:minmax(92px,115px) minmax(48px,1fr) 64px; gap:8px }} .card {{ padding:16px }} .loop-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)) }} }}
@media(max-width:420px) {{ .loop-grid {{ grid-template-columns:1fr }} .section-head {{ align-items:flex-start; flex-direction:column }} .stat {{ padding:13px }} }} @media(prefers-reduced-motion:reduce) {{ html {{ scroll-behavior:auto }} * {{ animation-duration:.01ms!important; transition-duration:.01ms!important }} }}
</style></head><body><main>
<header class="topbar"><div><p class="eyebrow">DUET · QUALITY REVIEW</p><h1>综合评测与自更新轮次报告</h1><p class="meta">覆盖 24 个场景 · 最近完整轮次 {esc(latest_full.get('started_at','—') if latest_full else '—')} · 由评测 JSON 汇总生成</p></div><div class="brand-mark"><i></i>评测档案</div></header>
<section class="hero"><div class="hero-copy"><p class="eyebrow">FULL EVALUATION · {esc(latest_full.get('_folder','—') if latest_full else '—')}</p><h2>目标分数 {esc(score_note)}</h2><p>完整全量最高分为 {latest_score:.1f}。这份报告汇总全量轮次、定向诊断和逐题证据，便于追踪改动对不同场景的影响。</p><div class="goal"><span class="goal-dot"></span>{'已达到完整全量分 >80 的目标' if latest_score > 80 else '完整全量分仍未超过 80'}</div></div><div class="score"><span>最高完整全量分</span><b>{latest_score:.1f}</b><span>/ 100</span></div></section>
<section class="stat-grid"><div class="stat"><span>相较首轮全量基线</span><strong>{score_delta:+.1f}</strong><em>{initial_score:.1f} → {latest_score:.1f}</em></div><div class="stat"><span>完整全量轮次</span><strong>{len(full)}</strong><em>每轮 24 个套件</em></div><div class="stat"><span>定向诊断轮次</span><strong>{len(targeted)}</strong><em>用于定位局部问题</em></div><div class="stat"><span>最近一轮增减</span><strong>{(latest_score - float(prev_full.get('automatic_score',0)) if prev_full else 0):+.1f}</strong><em>{esc(prev_full.get('_folder','无前轮') if prev_full else '无前轮')} → {esc(latest_full.get('_folder','—') if latest_full else '—')}</em></div></section>
<nav class="nav" aria-label="报告章节"><a href="#scores">分数概览</a><a href="#full-runs">全量轮次</a><a href="#diagnostics">定向诊断</a><a href="#updates">自更新记录</a><a href="#follow-up">后续重点</a><a href="#weak-suites">低分套件</a><a href="#method">评分说明</a></nav>
<section class="grid" id="scores"><article class="card"><h2>按交互模式</h2>{''.join(bar(name, score) for name, score in modes.items())}<p class="note">最高项为阻断题处理；多问题模式当前需要优先改善。</p></article><article class="card"><h2>按场景群 · 最近全量轮次</h2>{''.join(bar(name, score, f"{float(score)-float(prev_domains[name]):+.1f} 较前轮" if name in prev_domains else "") for name,score in weakest_domains)}<p class="note">公司内部应用低于上一轮全量分，详见领域对比。</p></article></section>
<section class="section" id="full-runs"><div class="section-head"><h2>完整全量轮次</h2><p>分数按 24 个套件汇总；逐题报告保留每个判据、工具轨迹和答案摘录。</p></div><div class="table-wrap"><table><thead><tr><th>轮次</th><th>开始时间</th><th>总分</th><th>变化</th><th>direct / hint / 多问 / 连发</th><th>更新摘要</th><th>详情</th></tr></thead><tbody>{''.join(full_rows)}</tbody></table></div></section>
<section class="grid section"><article class="card"><h2>领域分数对比</h2><p class="note">{esc(full[0]['_folder'] if full else '—')} → {esc(latest_full.get('_folder','—') if latest_full else '—')}</p><div class="table-wrap"><table><thead><tr><th>领域</th><th>起始分</th><th>最近分</th><th>变化</th></tr></thead><tbody>{domain_rows}</tbody></table></div>{''.join(f"<p class='callout'>留意：公司内部应用较上一轮全量下降 {float(prev_domains.get('公司内部应用',domains.get('公司内部应用',0)))-float(domains.get('公司内部应用',0)):.1f} 分（{float(prev_domains.get('公司内部应用',domains.get('公司内部应用',0))):.1f} → {float(domains.get('公司内部应用',0)):.1f}）。</p>" for _ in [0] if prev_domains.get('公司内部应用',100)-domains.get('公司内部应用',100)>5)}</article><article class="card"><h2>主要评测覆盖</h2><div class="finding-grid"><div class="finding"><h3>在线查询</h3><p>天气预报、公开网页与实时来源引用。</p></div><div class="finding"><h3>公开公司信息</h3><p>公告披露、SEC 文件与上市公司财报检索。</p></div><div class="finding"><h3>内部应用</h3><p>通过只读连接器检索内部应用中的授权数据。</p></div><div class="finding"><h3>多轮交互</h3><p>direct、提示、阻断、多问题和快速连发。</p></div></div></article></section>
<section class="section" id="diagnostics"><div class="section-head"><h2>定向诊断轮次</h2><p>样本较少，用于定位故障；不与全量分直接比较。</p></div><div class="table-wrap"><table><thead><tr><th>轮次</th><th>场景</th><th>分数</th><th>开始时间</th><th>诊断与更新摘要</th><th>详情</th></tr></thead><tbody>{''.join(target_rows)}</tbody></table></div></section>
<section class="section" id="updates"><div class="section-head"><h2>自更新轮次说明</h2><p>每轮记录评测发现、实现更新和复测变化；定向试跑单独列出。</p></div><div class="round-links">{history_links}</div><div class="update-grid">{update_cards}</div></section>
<section class="section" id="follow-up"><div class="section-head"><h2>后续改进重点</h2><p>总分已达到目标，以下场景仍有明确的逐题证据缺口。</p></div><div class="finding-grid"><article class="finding"><h3>上市公司财报 · {float(domains.get('上市公司财报',0)):.1f}</h3><p>多问题和连发回答未稳定完成工具检索、引用来源并覆盖各子问题。</p><p class="score-line">多问题 28 · 连发 44</p></article><article class="finding"><h3>公司内部应用 · {float(domains.get('公司内部应用',0)):.1f}</h3><p>多问题/连发中的连接器调用、记录读取和答案引用还未稳定通过。</p><p class="score-line">多问题 49 · 连发 54</p></article><article class="finding"><h3>在线查询 · {float(domains.get('在线信息查询',0)):.1f}</h3><p>天气连发仍有最终状态、完整答案或来源证据不足的问题。</p><p class="score-line">连发 59</p></article><article class="finding"><h3>公开披露 · {float(domains.get('公开披露信息',0)):.1f}</h3><p>披露连发场景未稳定执行检索并在答案中提供来源证据。</p><p class="score-line">连发 53</p></article></div></section>
<section class="section" id="weak-suites"><div class="section-head"><h2>最近全量轮次的低分套件</h2><p>展开查看题目分数与未通过的自动判据。</p></div><div class="weak-list">{''.join(weak_cards)}</div></section>
<section class="section" id="method"><div class="section-head"><h2>评测与更新闭环</h2><p>使用同一套完整套件核验改动收益和回归。</p></div><div class="loop-grid"><div class="loop-step"><strong>发现</strong><p>按总分、模式、领域和逐题判据定位失败；检查工具轨迹、来源与回答状态。</p></div><div class="loop-step"><strong>更新</strong><p>针对路由、工具检索、证据校验或答案发布链路改实现，并记录改动重点。</p></div><div class="loop-step"><strong>复测</strong><p>先做定向诊断，再跑完整 24 套件；定向分不计作全量进步。</p></div><div class="loop-step"><strong>退出条件</strong><p>{'已达到：完整全量分 >80。' if latest_score > 80 else '完整全量分需要超过 80。'}</p></div></div><p class="note">自动分按目录中预设的状态、拆题、关键词、工具/来源证据和隔离判据计算。语言质量仍需结合每题人工复核清单检查。最近报告时间：{latest_time}</p></section>
<footer>双通道 Agent 评测档案 · 全量 {len(full)} 轮 · 定向 {len(targeted)} 轮 · 汇总数据来自 evaluation/reports/iterations 中的 JSON。</footer>
</main></body></html>"""


def main():
    runs = load_runs()
    full = [d for d in runs if len(d.get("suites", [])) >= 24]
    targeted = [d for d in runs if 0 < len(d.get("suites", [])) < 24]
    best = max(full, key=lambda d: d.get("automatic_score", -1), default=None)
    latest_full = max(full, key=lambda d: d.get("started_at", ""), default=None)
    running = False
    # The live process is tracked in this report when a run directory has no JSON yet.
    if not any(d["_folder"] == "round-15" for d in runs):
        proc = subprocess.run(["pgrep", "-f", r"evaluation/scripts/run_live.py --all --timeout 180 --report-dir evaluation/reports/iterations/round-15"], capture_output=True, text=True)
        running = bool(proc.stdout.strip())

    lines = [
        "# 综合评测与自更新轮次报告", "",
        "> 由 `evaluation/scripts/build_iteration_report.py` 根据已保存的机器评测 JSON 生成；重新运行脚本可刷新轮次数据。", "",
        "## 总览", "",
    ]
    if best:
        lines.append(f"- **历史最高完整全量分：{best['automatic_score']:.1f}/100**（{best['_folder']}，{best.get('started_at','未知时间')}）。")
    if latest_full:
        lines.append(f"- **最近完成的完整全量分：{latest_full['automatic_score']:.1f}/100**（{latest_full['_folder']}，{latest_full.get('started_at','未知时间')}）。")
    if best and best.get("automatic_score", 0) > 80:
        lines.append(f"- **目标：完整全量分 >80。** 已达到，最高全量分为 {best['automatic_score']:.1f}；定向场景分仅用于诊断，不计作全量分。")
    else:
        lines.append("- **目标：完整全量分 >80。** 已完成轮次尚未达到目标；定向场景分仅用于诊断，不计作全量分。")
    lines.append("- **评测覆盖：** 24 个套件，包含公司内部应用、公开披露、上市公司财报、在线天气/网页查询，以及差旅、酒店、医院、金融客服、学习、会议和通用客服等场景；交互模式覆盖 direct、hinter_block、hinter_hint、multi_question、rapid_burst。")
    if running:
        lines.append("- **进行中：** 第 15 轮完整全量复测已启动，结果尚未落盘；完成后再次运行本脚本即可自动纳入。")
    lines += ["", "## 完整全量轮次", "", "| 轮次 | 时间 | 总分 | direct | block | hint | 多问题 | 连发 | 相对上一轮 | 结果 | 本轮更新与结论 |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|"]
    prev = None
    for d in full:
        mode = d.get("by_mode", {})
        score = d.get("automatic_score", 0)
        delta = "—" if prev is None else f"{score-prev:+.1f}"
        title, note = ROUND_NOTES.get(d["_folder"], ("全量迭代", "见本轮详细机器报告。"))
        base = d["_path"].with_suffix("")
        htmlp = base.with_suffix(".html")
        links = f"[JSON]({rel(d['_path'])}) · [HTML]({rel(htmlp)})" if htmlp.exists() else f"[JSON]({rel(d['_path'])})"
        lines.append(f"| {d['_folder']} | {d.get('started_at','未知')} | **{score:.1f}** | {mode.get('direct','—')} | {mode.get('hinter_block','—')} | {mode.get('hinter_hint','—')} | {mode.get('multi_question','—')} | {mode.get('rapid_burst','—')} | {delta} | {links} | {title}：{note} |")
        prev = score
    if full:
        earliest, latest = full[0], full[-1]
        first_domains = earliest.get("by_domain", {})
        latest_domains = latest.get("by_domain", {})
        lines += ["", f"### 领域分数对比（{earliest['_folder']} → {latest['_folder']}）", "", "| 领域 | 起始全量分 | 最近全量分 | 变化 |", "|---|---:|---:|---:|"]
        for domain in sorted(latest_domains, key=lambda x: latest_domains[x]):
            now = latest_domains[domain]
            old = first_domains.get(domain)
            change = f"{now-old:+.1f}" if old is not None else "新纳入/口径变化"
            old_s = f"{old:.1f}" if old is not None else "—"
            lines.append(f"| {domain} | {old_s} | {now:.1f} | {change} |")
        if len(full) >= 2:
            prior_domains = full[-2].get("by_domain", {})
            dropped = [(domain, val, prior_domains[domain]) for domain, val in latest_domains.items() if domain in prior_domains and val < prior_domains[domain] - 5]
            if dropped:
                lines += ["", "> 注意：以下领域较上一完整全量轮次下降超过 5 分，虽然总分已越过目标，建议后续复测时优先关注：" ]
                lines.extend(f"> - {domain}：{prior:.1f} → {now:.1f}（{now-prior:+.1f}）" for domain, now, prior in dropped)
    lines += ["", "## 定向诊断轮次", "", "这些单场景分数用于定位缺陷，样本量远少于全量套件，不能与全量分数直接比较，也不用于宣称达到目标。", "", "| 轮次 | 场景 | 分数 | 时间 | 主要发现/更新 | 结果 |", "|---|---|---:|---|---|---|"]
    for d in targeted:
        title, note = ROUND_NOTES.get(d["_folder"], ("定向诊断", "见本轮详细机器报告。"))
        base = d["_path"].with_suffix(""); htmlp = base.with_suffix(".html")
        links = f"[JSON]({rel(d['_path'])}) · [HTML]({rel(htmlp)})" if htmlp.exists() else f"[JSON]({rel(d['_path'])})"
        lines.append(f"| {d['_folder']} | {d.get('suites',[{}])[0].get('domain','—')} | {d.get('automatic_score',0):.1f} | {d.get('started_at','未知')} | {title}：{note} | {links} |")
    lines += ["", "## 当前评测结论", "", "### 已取得的进展", "", "- 已把评测从早期以机票为主扩展到 24 个套件，纳入天气在线查询、公司应用只读数据、公开披露文件及上市公司财报，并覆盖不同对话模式。", "- 最新完整全量分达到 81.0/100，首次超过 80 分目标；相较首个完整全量基线 40.4 分提高 40.6 分。", "- 第 15 轮模式分为 direct 91.0、hinter_block 100.0、hinter_hint 95.0、multi_question 75.0、rapid_burst 88.8；多问题模式是当前最低模式。", "- 天气使用 Open-Meteo 实时数据，公开公司披露检索使用 SEC 官方来源；评测同时检查工具轨迹、来源和答案证据。", "", "### 后续最值得改进的领域", "", "- 上市公司财报 61.2：财报多问题 28 分、连发 44 分，主要缺少已完成的工具调用、来源和逐项答案证据。", "- 公司内部应用 70.6：多问题 49 分、连发 54 分；连接器执行、读取记录以及答案引用证据未稳定满足判据。", "- 在线信息查询 72.4：天气连发 59 分，存在最终状态、完整回答或来源证据不足。", "- 公开披露信息 74.2：披露连发 53 分，未稳定完成检索并引用来源；直接问答和多问题得分明显更高。", "- 因为全量总分已达标，本轮达到用户设定的退出条件；如继续迭代，应优先修复以上财报、内部应用、天气和披露的多问题/连发工具证据链，并再次用完整 24 套件检查回归。", "", "### 第 15 轮低分套件及失败判据", "", "| 套件 | 分数 | 主要失败项 |", "|---|---:|---|"]
    if latest_full:
        weak = sorted(latest_full.get("suites", []), key=lambda s: s.get("automatic_score", 0))[:5]
        for s in weak:
            failed = sorted({check.get("name") for case in s.get("cases", []) for check in case.get("checks", []) if not check.get("passed")})
            lines.append(f"| {s.get('id','—')} | {s.get('automatic_score',0):.1f} | {', '.join(failed) if failed else '无失败判据'} |")
    goal_note = "已达到目标：最新完整全量分超过 80。" if best and best.get("automatic_score", 0) > 80 else f"未达到目标：当前最高完整全量分为 {best.get('automatic_score', 0):.1f}，需新一轮全量成绩超过 80。" if best else "未达到目标：尚无完整全量成绩。"
    lines += ["", "## 自更新闭环", "", "| 阶段 | 操作 | 记录方式 |", "|---|---|---|", "| 发现 | 按总分、模式、领域和逐题 checks 定位失败；查看工具调用、来源、状态和最终回答 | 每轮 JSON/HTML 与本报告逐轮记录 |", "| 更新 | 修改 runner、场景路由、工具检索、证据校验或回答生成/发布链路 | 记录改动重点及对应复测；定向分不计作全量进步 |", "| 复测 | 用完整 24 套件检查改动收益及回归 | 全量轮次纳入总分表；定向轮次单列 |", f"| 退出条件 | 完整全量自动分 >80 | {goal_note} |", "", "## 报告说明", "", "- 自动评分按评测目录中的预设 checks 计算，报告不等同于人工逐条核验。每轮机器 HTML 展示逐题观测、判据和答案摘录。", "- 本报告按文件夹/时间归档 JSON 与 HTML；轮次更新摘要基于该阶段的诊断与代码迭代记录整理，多个修复可能跨轮次共同生效。", "- 全量结果按套件数量识别（24 套件）；所有少于 24 套件的运行列为定向诊断。", ""]
    md = "\n".join(lines)
    OUT_MD.write_text(md, encoding="utf-8")

    OUT_HTML.write_text(report_html(runs, full, targeted, best, latest_full), encoding="utf-8")
    print(f"Wrote {OUT_MD.relative_to(ROOT)}")
    print(f"Wrote {OUT_HTML.relative_to(ROOT)}")
    print(f"Full runs: {len(full)}; targeted runs: {len(targeted)}; best full score: {best.get('automatic_score') if best else 'n/a'}")


if __name__ == "__main__":
    main()
