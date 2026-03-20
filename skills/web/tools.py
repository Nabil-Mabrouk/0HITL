import html
import json
import re
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx

from core.tools import tool


DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_CHARS = 12000
SEARCH_RESULT_LIMIT = 5
ALLOWED_SCHEMES = {"http", "https"}


class HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._ignored_depth = 0
        self._in_title = False
        self._title_parts: list[str] = []
        self._text_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        del attrs
        tag_name = (tag or "").lower()
        if tag_name in {"script", "style", "noscript"}:
            self._ignored_depth += 1
        elif tag_name == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        tag_name = (tag or "").lower()
        if tag_name in {"script", "style", "noscript"} and self._ignored_depth > 0:
            self._ignored_depth -= 1
        elif tag_name == "title":
            self._in_title = False

    def handle_data(self, data):
        value = re.sub(r"\s+", " ", data or "").strip()
        if not value:
            return

        if self._in_title:
            self._title_parts.append(value)
            return

        if self._ignored_depth == 0:
            self._text_parts.append(value)

    @property
    def title(self) -> str:
        return " ".join(self._title_parts).strip()

    @property
    def text(self) -> str:
        return " ".join(self._text_parts).strip()


class HTMLLinkExtractor(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self._current_href: Optional[str] = None
        self._current_text_parts: list[str] = []
        self.links: list[dict] = []

    def handle_starttag(self, tag, attrs):
        if (tag or "").lower() != "a":
            return
        attr_map = {key.lower(): value for key, value in attrs if key and value}
        href = attr_map.get("href")
        if not href:
            return
        absolute_url = urljoin(self.base_url, href)
        parsed = urlparse(absolute_url)
        if parsed.scheme not in ALLOWED_SCHEMES:
            return
        self._current_href = absolute_url
        self._current_text_parts = []

    def handle_data(self, data):
        if not self._current_href:
            return
        value = re.sub(r"\s+", " ", data or "").strip()
        if value:
            self._current_text_parts.append(value)

    def handle_endtag(self, tag):
        if (tag or "").lower() != "a" or not self._current_href:
            return

        anchor_text = " ".join(self._current_text_parts).strip() or self._current_href
        self.links.append({"url": self._current_href, "text": anchor_text})
        self._current_href = None
        self._current_text_parts = []


def _validate_url(url: str) -> str:
    clean_url = (url or "").strip()
    if not clean_url:
        raise ValueError("URL is required.")

    parsed = urlparse(clean_url)
    if parsed.scheme not in ALLOWED_SCHEMES or not parsed.netloc:
        raise ValueError("Only absolute http(s) URLs are supported.")

    return clean_url


def _truncate_text(text: str, max_chars: int) -> str:
    clean_text = (text or "").strip()
    if len(clean_text) <= max_chars:
        return clean_text
    return clean_text[: max(max_chars - 3, 0)].rstrip() + "..."


def _strip_html_fragment(raw_html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw_html or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_search_result_url(raw_url: str) -> str:
    absolute_url = urljoin("https://duckduckgo.com", raw_url or "")
    parsed = urlparse(absolute_url)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        redirected = parse_qs(parsed.query).get("uddg")
        if redirected:
            return unquote(redirected[0])
    return absolute_url


async def _request_url(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
) -> tuple[str, str, int, str]:
    request_headers = {
        "User-Agent": "0-HITL/0.1 (+local assistant; read-only web skill)",
        "Accept": "text/html,application/json,text/plain;q=0.9,*/*;q=0.8",
    }
    if headers:
        request_headers.update(headers)

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        headers=request_headers,
    ) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        return str(response.url), content_type, response.status_code, response.text


def _extract_html_content(raw_html: str) -> tuple[str, str]:
    parser = HTMLTextExtractor()
    parser.feed(raw_html or "")
    title = parser.title or ""
    text = parser.text or _strip_html_fragment(raw_html)
    return title, text


def _format_search_results(query: str, results: list[dict]) -> str:
    if not results:
        return f"No search results found for '{query}'."

    lines = [f"Search results for '{query}':"]
    for index, item in enumerate(results, start=1):
        lines.append(f"{index}. {item['title']}")
        lines.append(f"URL: {item['url']}")
        if item.get("snippet"):
            lines.append(f"Snippet: {item['snippet']}")
        lines.append("")
    return "\n".join(lines).strip()


@tool
async def search_web(query: str, limit: int = SEARCH_RESULT_LIMIT):
    """Searches the web in read-only mode and returns a compact list of relevant results."""
    clean_query = re.sub(r"\s+", " ", (query or "").strip())
    if not clean_query:
        return "Error: Search query is required."

    safe_limit = max(1, min(int(limit or SEARCH_RESULT_LIMIT), 10))

    try:
        _, _, _, raw_html = await _request_url(
            "https://html.duckduckgo.com/html/",
            params={"q": clean_query},
        )
    except Exception as exc:
        return f"Error performing web search: {exc}"

    anchors = re.findall(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        raw_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    snippets = re.findall(
        r'<(?:a|div)[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|div)>',
        raw_html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    results = []
    for index, (href, title_html) in enumerate(anchors[:safe_limit]):
        title = _strip_html_fragment(title_html) or "Untitled result"
        snippet = _strip_html_fragment(snippets[index]) if index < len(snippets) else ""
        results.append(
            {
                "title": title,
                "url": _normalize_search_result_url(href),
                "snippet": snippet,
            }
        )

    return _format_search_results(clean_query, results)


@tool
async def fetch_url(url: str, max_chars: int = DEFAULT_MAX_CHARS):
    """Fetches an HTTP(S) URL and returns a structured preview of the response."""
    try:
        clean_url = _validate_url(url)
    except ValueError as exc:
        return f"Error: {exc}"

    safe_max_chars = max(200, min(int(max_chars or DEFAULT_MAX_CHARS), 40000))

    try:
        final_url, content_type, status_code, body = await _request_url(clean_url)
    except Exception as exc:
        return f"Error fetching URL: {exc}"

    preview = ""
    title = ""
    lowered_content_type = content_type.lower()

    if "html" in lowered_content_type:
        title, text = _extract_html_content(body)
        preview = _truncate_text(text, safe_max_chars)
    elif "json" in lowered_content_type:
        try:
            preview = _truncate_text(
                json.dumps(json.loads(body), indent=2, ensure_ascii=False),
                safe_max_chars,
            )
        except json.JSONDecodeError:
            preview = _truncate_text(body, safe_max_chars)
    else:
        preview = _truncate_text(body, safe_max_chars)

    lines = [
        f"URL: {final_url}",
        f"Status: {status_code}",
        f"Content-Type: {content_type or 'unknown'}",
    ]
    if title:
        lines.append(f"Title: {title}")
    lines.append("")
    lines.append(preview or "[empty response body]")
    return "\n".join(lines).strip()


@tool
async def extract_page_text(url: str, max_chars: int = DEFAULT_MAX_CHARS):
    """Fetches an HTML page and extracts its main readable text content."""
    try:
        clean_url = _validate_url(url)
    except ValueError as exc:
        return f"Error: {exc}"

    safe_max_chars = max(200, min(int(max_chars or DEFAULT_MAX_CHARS), 40000))

    try:
        final_url, content_type, _, body = await _request_url(clean_url)
    except Exception as exc:
        return f"Error fetching URL: {exc}"

    if "html" not in content_type.lower():
        return f"Error: URL '{final_url}' did not return HTML content."

    title, text = _extract_html_content(body)
    lines = [f"URL: {final_url}"]
    if title:
        lines.append(f"Title: {title}")
    lines.append("")
    lines.append(_truncate_text(text, safe_max_chars) or "[no readable text extracted]")
    return "\n".join(lines).strip()


@tool
async def extract_links(url: str, same_domain_only: bool = False, limit: int = 20):
    """Fetches an HTML page and extracts visible HTTP links from it."""
    try:
        clean_url = _validate_url(url)
    except ValueError as exc:
        return f"Error: {exc}"

    safe_limit = max(1, min(int(limit or 20), 100))

    try:
        final_url, content_type, _, body = await _request_url(clean_url)
    except Exception as exc:
        return f"Error fetching URL: {exc}"

    if "html" not in content_type.lower():
        return f"Error: URL '{final_url}' did not return HTML content."

    extractor = HTMLLinkExtractor(final_url)
    extractor.feed(body)

    base_netloc = urlparse(final_url).netloc.lower()
    unique_links = []
    seen_urls = set()
    for item in extractor.links:
        link_url = item["url"]
        if same_domain_only and urlparse(link_url).netloc.lower() != base_netloc:
            continue
        if link_url in seen_urls:
            continue
        seen_urls.add(link_url)
        unique_links.append(item)
        if len(unique_links) >= safe_limit:
            break

    if not unique_links:
        return f"No HTTP links found on '{final_url}'."

    lines = [f"Links found on {final_url}:"]
    for index, item in enumerate(unique_links, start=1):
        lines.append(f"{index}. {item['text']}")
        lines.append(f"URL: {item['url']}")
    return "\n".join(lines).strip()
