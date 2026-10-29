"""Search providers and bounded public-page retrieval; no model-specific search API."""

import asyncio
import ipaddress
import os
import re
import socket
import xml.etree.ElementTree as ET
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from app.agent.tool_config import ToolConfig


class PageText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hidden = 0
        self.parts: list[str] = []
        self.main_parts: list[str] = []
        self.main_depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in {"main", "article"}:
            self.main_depth += 1
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"main", "article"}:
            self.main_depth = max(0, self.main_depth - 1)
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data: str) -> None:
        if not self.hidden and data.strip():
            self.parts.append(data.strip())
            if self.main_depth:
                self.main_parts.append(data.strip())


def rank_results(query: str, items: list[dict]) -> list[dict]:
    stop = set(
        "the what is are in of for and me please search web look up current today latest "
        "query ok okay well um uh hmm can could you tell how much would like know".split()
    )
    terms = set(re.findall(r"[a-z0-9]{2,}", query.casefold())) - stop
    for phrase in re.findall(r"[\u3400-\u9fff]{2,}", query):
        terms.update(phrase[i : i + 2] for i in range(len(phrase) - 1))
    ranked = []
    for item in items:
        text = " ".join(str(item.get(k, "")) for k in ["title", "snippet"]).casefold()
        score = sum(
            bool(re.search(r"\b" + re.escape(t) + r"\b", text)) if t.isascii() else t in text
            for t in terms
        )
        if score or not terms:
            ranked.append((score, item))
    return [item for _, item in sorted(ranked, key=lambda pair: -pair[0])]


def focus_content(content: str, query: str, limit: int = 8000) -> str:
    if len(content) <= limit:
        return content
    chunks = [{"title": content[i : i + 1500], "index": i} for i in range(0, len(content), 1500)]
    best = rank_results(query, chunks)[: max(1, limit // 1500)]
    return "\n[…]\n".join(p["title"] for p in sorted(best, key=lambda p: p["index"]))[:limit]


async def current_weather_source(client: httpx.AsyncClient, query: str) -> dict | None:
    """Discover a dated weather source instead of mistaking climate pages for live data."""
    if re.search(r"\b(?:yesterday|historical|last)\b|\d{4}-\d{2}|昨天|历史|去年", query, re.I):
        return None
    match = re.match(
        r"^(.+?)\s+(?:(?:current|day|daytime|today|latest|local)\s+)*(?:weather|forecast)\b",
        query,
        re.I,
    )
    if not match:
        match = re.match(
            r"^(?:(?:current|today|local)\s+)*(?:weather|forecast)\s+(?:in\s+|for\s+)?"
            r"(.+?)(?:\s+(?:today|now|current|daytime))?$",
            query,
            re.I,
        )
        if not match:
            match = re.match(
                r"^(.{1,24}?)(?:的)?(?:今天|明天|实时|当前)?(?:天气|气温|天气预报)", query
            )
            if not match:
                return None
    location = re.sub(r"(?:的)?(?:今天|明天|实时|当前)$", "", match[1].strip())
    canonical_cities = {
        "hangzhou": "Hangzhou",
        "shanghai": "Shanghai",
        "beijing": "Beijing",
        "shenzhen": "Shenzhen",
        "guangzhou": "Guangzhou",
        "chengdu": "Chengdu",
        "singapore": "Singapore",
        "上海": "Shanghai",
        "北京": "Beijing",
        "杭州": "Hangzhou",
        "深圳": "Shenzhen",
        "广州": "Guangzhou",
        "成都": "Chengdu",
        "新加坡": "Singapore",
    }
    location = next(
        (value for key, value in canonical_cities.items() if key in location.casefold()),
        location,
    )
    try:
        async with asyncio.timeout(5):
            response = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": location, "count": 1, "language": "en"},
            )
            response.raise_for_status()
            places = response.json().get("results", [])
            if not places:
                return None
            place = places[0]
            url = "https://api.open-meteo.com/v1/forecast?" + urlencode(
                {
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    "current": (
                        "temperature_2m,apparent_temperature,precipitation,"
                        "weather_code,wind_speed_10m,wind_gusts_10m"
                    ),
                    "hourly": "temperature_2m,precipitation_probability,wind_gusts_10m",
                    "daily": (
                        "temperature_2m_max,temperature_2m_min,"
                        "precipitation_probability_max,wind_gusts_10m_max"
                    ),
                    "timezone": "auto",
                    "forecast_days": 2 if re.search(r"\btomorrow\b|明天", query, re.I) else 1,
                }
            )
            return {
                "title": f"{place['name']}, {place.get('country', '')}: current weather today",
                "url": url,
                "snippet": "Open-Meteo current model conditions and today's dated forecast. "
                + query,
                "source_kind": "current_weather",
            }
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError):
        return None


async def sec_filing_sources(client: httpx.AsyncClient, query: str, count: int) -> list[dict]:
    """Find the requested annual filing from SEC's company and submissions indexes."""
    year_match = re.search(r"\b(20\d\d)\b", query)
    year = year_match.group(1) if year_match else ""
    words = re.findall(r"[a-z0-9]+", query.casefold())
    ignored = {"sec", "filing", "form", "10", "k", "fy", "annual", "report"}
    company_words = [word for word in words if word not in ignored and not word.isdigit()]
    aliases = {"google": "alphabet"}
    company_words = [aliases.get(word, word) for word in company_words]
    if not company_words:
        return []
    headers = {"User-Agent": "DuetAI/0.3 (public SEC filing lookup)"}
    try:
        async with asyncio.timeout(12):
            tickers_response = await client.get(
                "https://www.sec.gov/files/company_tickers.json", headers=headers
            )
            tickers_response.raise_for_status()
            companies = tickers_response.json().values()
            matches = []
            for company in companies:
                title_words = set(re.findall(r"[a-z0-9]+", company.get("title", "").casefold()))
                if all(word in title_words for word in company_words):
                    matches.append(company)
            # Several share classes can point to the same issuer/CIK.
            unique_matches = {int(item["cik_str"]): item for item in matches}
            # Ambiguous company names are not safe for a financial answer.
            if len(unique_matches) != 1:
                return []
            cik, company = next(iter(unique_matches.items()))
            submissions_response = await client.get(
                f"https://data.sec.gov/submissions/CIK{cik:010d}.json", headers=headers
            )
            submissions_response.raise_for_status()
            recent = submissions_response.json().get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            report_dates = recent.get("reportDate", [])
            filed_dates = recent.get("filingDate", [])
            accessions = recent.get("accessionNumber", [])
            documents = recent.get("primaryDocument", [])
            found = []
            for index, form in enumerate(forms):
                if form != "10-K" or index >= len(accessions) or index >= len(documents):
                    continue
                report_date = report_dates[index] if index < len(report_dates) else ""
                filed_date = filed_dates[index] if index < len(filed_dates) else ""
                if year and not report_date.startswith(year):
                    continue
                accession = accessions[index]
                accession_path = accession.replace("-", "")
                document = documents[index]
                found.append(
                    {
                        "title": f"{company['title']} Form 10-K for {report_date or year}",
                        "url": (
                            f"https://www.sec.gov/Archives/edgar/data/{cik}/"
                            f"{accession_path}/{document}"
                        ),
                        "snippet": (
                            f"Official SEC Form 10-K; reporting date {report_date}; "
                            f"filing date {filed_date}; company CIK {cik}."
                        ),
                        "channel": "sec_submissions",
                        "source_kind": "sec_filing",
                    }
                )
            return found[:count]
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError):
        return []


async def search_web(
    config: ToolConfig, query: str, max_results: int = 5, domains: list[str] | None = None
) -> dict:
    if not query.strip() or len(query) > 2000:
        raise ValueError("query must contain 1-2000 characters")
    count = max(1, min(max_results, 10))
    domains = domains or []
    if any(not re.fullmatch(r"[a-zA-Z0-9.-]+", d) for d in domains):
        raise ValueError("domains must be host names")
    scoped = query + (" (" + " OR ".join(f"site:{d}" for d in domains) + ")" if domains else "")
    async with httpx.AsyncClient(timeout=config.timeout_seconds, follow_redirects=True) as client:
        # Weather is a structured live API lookup, not a reason to wait on a
        # general-purpose search index. For mixed forecast/alert queries the
        # caller can request both the API and official alert domains.
        weather = (
            await current_weather_source(client, query)
            if not domains or "api.open-meteo.com" in domains
            else None
        )
        sec_results = (
            await sec_filing_sources(client, query, count)
            if any(d.lower() == "sec.gov" for d in domains)
            else []
        )
        if sec_results:
            items = sec_results
        elif config.web_search_provider == "brave":
            key = os.getenv("BRAVE_SEARCH_API_KEY", "")
            if not key:
                raise ValueError("Configure BRAVE_SEARCH_API_KEY for the brave search provider")
            response = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": scoped, "count": count},
                headers={"X-Subscription-Token": key},
            )
            response.raise_for_status()
            items = [
                {
                    "title": x.get("title", ""),
                    "url": x["url"],
                    "snippet": unescape(re.sub(r"<[^>]+>", "", x.get("description", ""))),
                }
                for x in response.json().get("web", {}).get("results", [])
            ]
        elif config.web_search_provider == "searxng":
            if not config.searxng_url.startswith(("http://", "https://")):
                raise ValueError("Configure searxng_url for the searxng provider")
            response = await client.get(
                config.searxng_url.rstrip("/") + "/search", params={"q": scoped, "format": "json"}
            )
            response.raise_for_status()
            items = [
                {"title": x.get("title", ""), "url": x["url"], "snippet": x.get("content", "")}
                for x in response.json().get("results", [])
            ]
        else:

            async def rss(path: str) -> list[dict]:
                response = await client.get(
                    "https://www.bing.com/" + path,
                    params={
                        "q": scoped,
                        "format": "RSS",
                    },
                    headers={"User-Agent": "Mozilla/5.0 (compatible; DuetAI/0.3)"},
                )
                response.raise_for_status()
                found = []
                for x in ET.fromstring(response.content).findall("./channel/item"):
                    url = x.findtext("link", "")
                    parsed = urlsplit(url)
                    if (
                        parsed.hostname in {"www.bing.com", "bing.com"}
                        and parsed.path == "/news/apiclick.aspx"
                    ):
                        url = parse_qs(parsed.query).get("url", [url])[0]
                    found.append(
                        {
                            "title": x.findtext("title", ""),
                            "url": url,
                            "snippet": unescape(
                                re.sub(r"<[^>]+>", "", x.findtext("description", ""))
                            ),
                            "channel": "news" if path.startswith("news") else "web",
                            "published_at": x.findtext("pubDate") or None,
                        }
                    )
                return found

            try:
                async with asyncio.timeout(min(config.timeout_seconds, 10)):
                    channels = await asyncio.gather(
                        rss("search"), rss("news/search"), return_exceptions=True
                    )
            except TimeoutError:
                channels = [TimeoutError(), TimeoutError()]
            items = [item for result in channels if isinstance(result, list) for item in result]
            if (
                not items
                and not weather
                and all(isinstance(result, Exception) for result in channels)
            ):
                raise ValueError("search_provider_unavailable")
            seen = set()
            items = [
                item
                for item in rank_results(scoped, items)
                if not (item["url"] in seen or seen.add(item["url"]))
            ]
        if weather:
            items = [weather, *items]
    items = [x for x in items if x["url"].startswith(("http://", "https://"))]
    if domains:
        items = [
            x
            for x in items
            if any(
                (urlsplit(x["url"]).hostname or "").lower() == d.lower()
                or (urlsplit(x["url"]).hostname or "").lower().endswith("." + d.lower())
                for d in domains
            )
        ]
    return {
        "ok": True,
        "provider": config.web_search_provider,
        "query": query,
        "results": items[:count],
        "note": "Search snippets; open sources to verify details.",
    }


async def check_public_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
        raise ValueError("Expected a public http(s) URL without credentials")
    addresses = await asyncio.to_thread(
        socket.getaddrinfo,
        parsed.hostname,
        parsed.port or (443 if parsed.scheme == "https" else 80),
    )
    if not addresses or any(not ipaddress.ip_address(x[4][0]).is_global for x in addresses):
        raise ValueError("Public web retrieval cannot access local/private network addresses")


async def fetch_web(config: ToolConfig, url: str) -> dict:
    is_sec = (urlsplit(url).hostname or "").lower().endswith("sec.gov")
    max_bytes = 1_000_000 if not is_sec else 4_000_000
    max_chars = config.max_output_chars if not is_sec else max(config.max_output_chars, 400_000)
    async with httpx.AsyncClient(timeout=config.timeout_seconds, trust_env=False) as client:
        for _ in range(6):
            await check_public_url(url)
            async with client.stream("GET", url, headers={"User-Agent": "DuetAI/0.3"}) as response:
                if response.is_redirect:
                    url = str(response.url.join(response.headers["location"]))
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if not any(t in content_type for t in ["text/", "json", "xml", "html"]):
                    raise ValueError("Only text/HTML pages are supported; PDF needs another tool")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk[: max_bytes - len(data)])
                    if len(data) >= max_bytes:
                        break
                text = data.decode(response.encoding or "utf-8", errors="replace")
                if "html" in content_type:
                    parser = PageText()
                    parser.feed(text)
                    text = "\n".join(parser.main_parts or parser.parts)
                return {
                    "ok": True,
                    "url": url,
                    "content": text[:max_chars],
                    "truncated": len(text) > max_chars or len(data) >= max_bytes,
                }
    raise ValueError("Too many page redirects")
