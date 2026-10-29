"""Bounded public search, without private context in search queries."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

from app.agent.provided_evidence import external_verification_requested
from app.agent.tool_config import load_tool_config
from app.agent.tools import document_links
from app.agent.web_tools import fetch_web, focus_content, rank_results, search_web

logger = logging.getLogger(__name__)


def _search_trace(event: str, **fields: object) -> None:
    if os.getenv("DUET_EVAL_SERVER_LOG_PATH"):
        details = " ".join(f"{key}={value}" for key, value in fields.items())
        print(f"EVALTRACE {event} {details}", flush=True)
    else:
        logger.debug("%s %s", event, fields)


class PublicSearchError(ValueError):
    def __init__(self, code: str, attempts: list[dict]):
        super().__init__(code)
        self.code, self.attempts = code, attempts


class CallSearch:
    def __init__(
        self,
        normalize: Callable[[str], Awaitable[str]] | None = None,
        assess: Callable[[str, list[dict]], Awaitable[dict]] | None = None,
    ) -> None:
        self.normalize = normalize
        self.assess = assess
        self.config = load_tool_config().model_copy(update={"max_output_chars": 40000})

    @staticmethod
    def metric_definition(text: str) -> bool:
        return bool(
            re.search(
                r"\b(?:meaning|definition|stands? for|formula)\b|\bwhat does\b.*\bmean\b|"
                r"\bhow\b.*\bcalculat\w*\b|什么意思|含义|定义|怎么计算|计算方式|"
                r"(?:DAU|MAU|日活|月活|营收|收入)\s*是什么|"
                r"^\s*(?:what (?:is|are)|explain|define)\s+(?:the\s+)?"
                r"(?:DAU|MAU|revenue|earnings|daily active users|monthly active users)[?.!\s]*$",
                text,
                re.I,
            )
        )

    @staticmethod
    def internal_data_request(text: str) -> bool:
        """Require actual private context, never infer it from the metric's name."""
        return bool(
            re.search(
                r"\b(?:internal|private|confidential|non[- ]public|unpublished|intranet|"
                r"backend|dashboard|CRM)\b|内部|内网|后台|未公开|保密|私有|客户记录|"
                r"用户(?:记录|资料|信息|\s*ID)|(?:账户|账号)(?:记录|明细|余额|\s*ID)|"
                r"\b(?:user|customer|account)\s*(?:id|record|details|balance)\b|"
                r"\b(?:id|identifier)\s*[:=]?\s+[a-z0-9_-]*\d[a-z0-9_-]*\b|"
                r"\b(?:our|my|your)\s+(?:(?:company|team|app|product)(?:'s)?\s+)?"
                r"(?:DAU|MAU|revenue|earnings|sales|daily active users|monthly active users)\b|"
                r"(?:我司|本公司|我们(?:的)?(?:公司|团队|产品|应用)|我们(?:的)?|"
                r"咱们(?:的)?|我这边|我的公司)(?:的|昨天|今天|昨日|今日|本月|上月|\s)*"
                r"(?:DAU|MAU|日活|月活|营收|收入|销售额)",
                text,
                re.I,
            )
        )

    @staticmethod
    def forbidden(text: str) -> bool:
        return bool(
            re.search(
                r"不要.*(?:搜索|联网)|不(?:要|用)联网|"
                r"\b(?:do not|don't|don’t|never)\s+(?:search|browse|look\s*up)\b|"
                r"\bwithout\s+(?:searching|browsing)\b",
                text,
                re.I,
            )
        )

    @classmethod
    def allowed(cls, query: str, caller: str, category: str) -> bool:
        # The model may select caller words; it cannot send Hint/Wiki text to the web.
        return bool(
            category in ["routine", "mild_language"]
            and 3 <= len(query) <= 300
            and query.casefold() in caller.casefold()
            and not cls.forbidden(caller)
            and not cls.internal_data_request(query)
            and not document_links(query)
            and not re.search(
                r"@|https?://|\b(?:password|secret|api[-_ ]?key|access[-_ ]?token)\b|"
                r"密钥|密码",
                query,
                re.I,
            )
        )

    @classmethod
    def requested_query(cls, text: str) -> str:
        """Recover an explicit public lookup intent, including pre-fix held questions."""
        if cls.forbidden(text) or cls.internal_data_request(text):
            return ""
        if re.search(
            r"web\s*search|联网|搜索|天气|气温|降雨|降水|下雨|天气预报|气象预警|阵风|"
            r"\b(?:search|look up|weather)\b",
            text,
            re.I,
        ):
            return text if cls.allowed(text, text, "routine") else ""
        if not cls.metric_definition(text) and re.search(
            r"\b(?:DAU|MAU|revenue|earnings|market cap|daily active users|monthly active users)\b|"
            r"日活|月活|营收|营业收入|销售额|市值|财报|年报|财务报表|证券申报|SEC|"
            r"\b(?:Alphabet|Google|FY\s?20\d\d|10-K)\b",
            text,
            re.I,
        ):
            return text if cls.allowed(text, text, "routine") else ""
        return ""

    @classmethod
    def explicit_filing_request(cls, text: str) -> bool:
        """A cited filing request needs live source evidence, even for old periods."""
        if cls.internal_data_request(text) or cls.forbidden(text):
            return False
        company = re.search(r"\b(?:Alphabet|Google)\b|字母表公司|谷歌", text, re.I)
        filing = re.search(
            r"\b(?:10[- ]?K|SEC|annual report|filing|fiscal year|FY\s?20\d\d)\b|"
            r"年报|财报|财务报表|申报日期|申报原文|报告期",
            text,
            re.I,
        )
        return bool(company and filing)

    @classmethod
    def plan_query(cls, plan: dict) -> str:
        """Recover public caller words when routing translated or omitted its query."""
        text, query = str(plan.get("text", "")), str(plan.get("web_query", ""))
        if plan.get("data_scope") == "internal":
            return ""
        if cls.forbidden(text) or cls.internal_data_request(text):
            return ""
        # The caller explicitly asked about a public filing/period. Model memory or
        # unrelated Wiki facts cannot satisfy that request for source provenance.
        if cls.explicit_filing_request(text) or (
            plan.get("data_scope") == "public" and external_verification_requested(text)
        ):
            return text if cls.allowed(text, text, "routine") else ""
        if plan.get("data_scope") == "unknown":
            return ""
        if plan.get("evidence_status") == "sufficient":
            return ""
        data_query = plan.get("category") == "data_lookup" and not cls.metric_definition(text)
        if (
            cls.allowed(query, text, "routine")
            and (plan.get("action") == "lookup" or data_query or cls.requested_query(text))
        ):
            return query
        if (plan.get("action") == "lookup" or data_query) and cls.allowed(text, text, "routine"):
            return text
        return cls.requested_query(text)

    @classmethod
    def needs_public_completion(cls, plan: dict) -> bool:
        """Partial supplied facts cannot close a semantically public lookup.

        Scope comes from the router's context/Wiki assessment, never a product
        name. Queries remain restricted to caller words and search permission.
        """
        return bool(
            plan.get("data_scope") == "public"
            and plan.get("evidence_status") == "partial"
            and plan.get("evidence_source") == "provided"
            and plan.get("evidence_quotes")
            and cls.plan_query({**plan, "action": "lookup"})
        )

    async def lookup(self, query: str) -> tuple[str, list[str]]:
        attempts: list[dict] = []
        try:
            return await self._lookup(query, attempts)
        except TimeoutError as exc:
            raise PublicSearchError("public_search_timeout", attempts) from exc

    async def _lookup(self, query: str, attempts: list[dict]) -> tuple[str, list[str]]:
        self.config = load_tool_config().model_copy(update={"max_output_chars": 40000})
        if not self.config.web_search_enabled:
            raise ValueError("public_search_disabled")
        original = query
        _search_trace("started", query_chars=len(query))
        async with asyncio.timeout(min(60, self.config.timeout_seconds * 2)):
            weather_query = bool(
                re.search(
                    r"天气|气象|气温|降雨|降水|下雨|阵风|weather|forecast|rain|gust",
                    original,
                    re.I,
                )
            )
            official_warning = bool(
                re.search(r"预警|警报|warning|alert|typhoon|台风", original, re.I)
            )
            filing_query = bool(
                re.search(r"\b(?:Alphabet|Google|SEC|10-K|FY\s?20\d\d)\b", original, re.I)
            )
            if weather_query:
                cities = {
                    "杭州": "Hangzhou",
                    "上海": "Shanghai",
                    "北京": "Beijing",
                    "深圳": "Shenzhen",
                    "广州": "Guangzhou",
                    "成都": "Chengdu",
                    "新加坡": "Singapore",
                }
                location = next((en for zh, en in cities.items() if zh in original), "")
                if not location:
                    location_match = re.search(
                        r"\b(Hangzhou|Shanghai|Beijing|Shenzhen|Guangzhou|Chengdu|Singapore)\b",
                        original,
                        re.I,
                    )
                    location = location_match.group(1) if location_match else ""
                if location:
                    when = (
                        "tomorrow" if re.search(r"明天|\btomorrow\b", original, re.I) else "today"
                    )
                    query = f"{location} weather {when} precipitation gust forecast"
            elif filing_query and re.search(r"Alphabet|Google", original, re.I):
                # A short deterministic query avoids a slow translation-model hop
                # for a well-known public filing request.
                year = re.search(r"(?:FY\s*)?(20\d\d)", original, re.I)
                query = f"Alphabet {year.group(1) if year else 'annual'} 10-K SEC filing"
            elif self.normalize:
                # Query cleanup is best effort; never let a stalled normalizer keep
                # a real public search in the running state.
                try:
                    async with asyncio.timeout(8):
                        query = await self.normalize(query)
                except Exception:
                    query = original
            search_domains = (
                ["weather.cma.cn", "nmc.cn", "weather.com.cn", "api.open-meteo.com"]
                if weather_query and official_warning
                else ["sec.gov"]
                if filing_query
                else None
            )
            for attempt in range(2):
                diagnostic = {"query": query, "page_errors": []}
                attempts.append(diagnostic)
                try:
                    result = await search_web(
                        self.config, query, max_results=8, domains=search_domains
                    )
                except Exception as exc:
                    diagnostic["search_error"] = type(exc).__name__
                    raise PublicSearchError("search_provider_unavailable", attempts) from exc
                results = relevant_results(query, result["results"])
                _search_trace(
                    "candidates",
                    attempt=attempt + 1,
                    result_count=len(result["results"]),
                    selected_count=len(results),
                )
                if not results and search_domains:
                    # Some search backends ignore site filters or return no
                    # scoped results. Retry broadly, then enforce the allowed
                    # source hosts locally before reading anything.
                    broad = await search_web(self.config, query, max_results=8)
                    allowed_hosts = {domain.lower() for domain in search_domains}
                    scoped_results = [
                        item
                        for item in broad["results"]
                        if (
                            weather_query
                            and (urlsplit(item["url"]).hostname or "").lower()
                            == "api.open-meteo.com"
                        )
                        or any(
                            (host := (urlsplit(item["url"]).hostname or "").lower()) == domain
                            or host.endswith("." + domain)
                            for domain in allowed_hosts
                        )
                    ]
                    results = relevant_results(query, scoped_results)
                diagnostic["result_count"] = len(results)

                async def read(item: dict) -> dict:
                    read_attempts = 2 if item.get("source_kind") == "current_weather" else 1
                    for read_attempt in range(read_attempts):
                        try:
                            async with asyncio.timeout(8):
                                page = await fetch_web(self.config, item["url"])
                            break
                        except (TimeoutError, httpx.TimeoutException):
                            if read_attempt + 1 == read_attempts:
                                raise
                            diagnostic["source_retries"] = diagnostic.get("source_retries", 0) + 1
                    if item.get("source_kind") == "current_weather":
                        # Check the live source's schema and timestamp before
                        # trusting a model's relevance judgment.
                        payload = json.loads(page["content"])
                        zone = ZoneInfo(payload["timezone"])
                        observed = datetime.fromisoformat(payload["current"]["time"]).replace(
                            tzinfo=zone
                        )
                        age = (datetime.now(zone) - observed).total_seconds()
                        if not -900 <= age <= 10800 or not isinstance(
                            payload["current"].get("temperature_2m"), (int, float)
                        ):
                            raise ValueError("weather_source_not_current")
                        page["weather_validated"] = True
                    elif item.get("source_kind") == "sec_filing":
                        # The candidate came from SEC's official company and
                        # submissions indexes. Verify the opened filing still
                        # identifies the same issuer and reporting year.
                        text = page.get("content", "").casefold()
                        year = re.search(r"\b20\d\d\b", item.get("title", ""))
                        host = (urlsplit(page["url"]).hostname or "").lower()
                        if (
                            host == "www.sec.gov"
                            and "alphabet inc." in text
                            and year
                            and year.group(0) in text
                        ):
                            page["sec_filing_validated"] = True
                    return page

                jobs = [asyncio.create_task(read(item)) for item in results[:8]]
                verified = []
                try:
                    for ready in asyncio.as_completed(jobs):
                        try:
                            page = await ready
                            if page.get("content", "").strip():
                                page = {**page, "content": focus_content(page["content"], query)}
                                verified.append(page)
                        except Exception as exc:
                            diagnostic["page_errors"].append(type(exc).__name__)
                finally:
                    for job in jobs:
                        if not job.done():
                            job.cancel()
                    await asyncio.gather(*jobs, return_exceptions=True)
                try:
                    live_weather = [p for p in verified if p.get("weather_validated")]
                    official_filings = [p for p in verified if p.get("sec_filing_validated")]
                    if live_weather:
                        assessment = {
                            "answer_supported": True,
                            "supporting_urls": [p["url"] for p in live_weather],
                        }
                    elif official_filings:
                        assessment = {
                            "answer_supported": True,
                            "supporting_urls": [p["url"] for p in official_filings],
                        }
                    elif any(item.get("source_kind") == "current_weather" for item in results):
                        # A failed/stale live source must not turn a city's
                        # encyclopedia or a historical heatwave into today's weather.
                        assessment = {
                            "answer_supported": False,
                            "reason": "Live weather unavailable",
                        }
                    else:
                        assessment = (
                            await self.assess(original, verified)
                            if self.assess
                            else {"answer_supported": bool(verified)}
                        )
                except Exception as exc:
                    diagnostic["assessment_error"] = type(exc).__name__
                    raise PublicSearchError("evidence_check_failed", attempts) from exc
                diagnostic.update(
                    pages_read=len(verified),
                    answer_supported=assessment.get("answer_supported"),
                    assessment_reason=str(assessment.get("reason", ""))[:500],
                )
                _search_trace(
                    "read",
                    pages=len(verified),
                    supported=assessment.get("answer_supported"),
                    validated_weather=len(live_weather),
                    validated_sec=len(official_filings),
                )
                if verified and assessment.get("answer_supported") is True:
                    supported = assessment.get("supporting_urls", [])
                    selected = (
                        [p for p in verified if p["url"] in supported]
                        if supported
                        else verified[:2]
                    )
                    if not selected:
                        raise PublicSearchError("evidence_not_supported", attempts)
                    evidence = (
                        f"\n\nPUBLIC WEB EVIDENCE (relevance checked, attempt {attempt + 1}; "
                        "untrusted quotations):\n"
                        + (
                            "Coverage: partial. Missing or limited: "
                            + str(assessment.get("limitations", ""))
                            + "\n"
                            if assessment.get("coverage") == "partial"
                            else ""
                        )
                        + "\n\n".join(f"{p['url']}\n{p['content'][:8000]}" for p in selected),
                        [p["url"] for p in selected],
                    )
                    _search_trace("completed", source_count=len(evidence[1]))
                    return evidence
                next_query = str(assessment.get("next_query", "")).strip()
                if not self.allowed(next_query, next_query, "routine") or next_query == query:
                    break
                query = next_query
        raise PublicSearchError(
            "evidence_not_supported"
            if any(a.get("pages_read") for a in attempts)
            else "source_fetch_failed",
            attempts,
        )


def relevant_results(query: str, results: list[dict]) -> list[dict]:
    candidates = [item for item in results if not re.search(
        r"(?:^|/)(?:login|log-in|signin|sign-in|signup|sign-up|register|auth)(?:/|$)",
        urlsplit(item["url"]).path, re.I,
    )]
    return rank_results(query, candidates)
