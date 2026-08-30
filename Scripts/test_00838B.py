#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
00838B TPEx ETF Official Price Diagnostic V4

Purpose:
1. Diagnose the official TPEx ETF historical-data page.
2. Do not use stk_wn1430_result.php.
3. Do not use the normal OTC stock quote endpoint.
4. Do not modify the production price pipeline.
5. Do not assume that the historical page itself is a JSON API.
6. Inspect the actual HTML page, forms, scripts, links, and data clues.
7. Search for 00838B.
8. If an official data endpoint is exposed by the page, test it.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, timedelta
from html import unescape
from urllib.parse import parse_qs, urljoin, urlparse

import requests


SYMBOL = "00838B"

BASE_URL = "https://www.tpex.org.tw"

HISTORICAL_PAGE = (
    "https://www.tpex.org.tw/zh-tw/product/etf/info/historical/day.html"
)

OLD_HISTORICAL_PAGE = (
    "https://www.tpex.org.tw/web/etf/historical/etf_statistics.php"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Referer": BASE_URL + "/",
}

TIMEOUT = 30


def roc_date(value: date) -> str:
    return f"{value.year - 1911:03d}/{value.month:02d}/{value.day:02d}"


def normalize_text(value) -> str:
    if value is None:
        return ""

    text = str(value)

    replacements = {
        "\ufeff": "",
        "\u3000": " ",
        "\xa0": " ",
        "\r": " ",
        "\n": " ",
        "\t": " ",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    return " ".join(text.split()).strip()


def request_page(url: str, params=None):
    print()
    print("=" * 80)
    print("HTTP REQUEST")
    print("=" * 80)
    print(f"URL: {url}")

    if params:
        print("PARAMS:")
        for key, value in params.items():
            print(f"  {key} = {value}")

    try:
        response = requests.get(
            url,
            params=params,
            headers=HEADERS,
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        print(f"REQUEST ERROR: {exc}")
        return None

    print()
    print("HTTP RESPONSE")
    print(f"status_code: {response.status_code}")
    print(
        "content_type: "
        f"{response.headers.get('Content-Type', '')}"
    )
    print(f"content_length: {len(response.content)}")
    print(f"final_url: {response.url}")

    if response.status_code != 200:
        print("ERROR: HTTP status is not 200")
        return None

    return response


def decode_response(response) -> str:
    """
    Decode the response using the server encoding when available.
    TPEx pages are UTF-8 in the current site.
    """
    content_type = response.headers.get("Content-Type", "").lower()

    if "charset=" in content_type:
        charset = content_type.split("charset=", 1)[1]
        charset = charset.split(";", 1)[0].strip()

        try:
            return response.content.decode(charset, errors="replace")
        except LookupError:
            pass

    try:
        return response.content.decode("utf-8", errors="replace")
    except Exception:
        return response.text


def print_page_identity(html: str):
    print()
    print("=" * 80)
    print("PAGE IDENTITY")
    print("=" * 80)

    title_match = re.search(
        r"<title[^>]*>(.*?)</title>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if title_match:
        title = normalize_text(
            unescape(title_match.group(1))
        )
        print(f"title: {title}")
    else:
        print("title: NOT FOUND")

    charset_match = re.search(
        r"<meta[^>]+charset=[\"']?([^\"' >]+)",
        html,
        flags=re.IGNORECASE,
    )

    if charset_match:
        print(f"charset: {charset_match.group(1)}")
    else:
        print("charset: NOT FOUND")


def extract_links(html: str, base_url: str):
    links = []

    pattern = re.compile(
        r"<a\b[^>]*href\s*=\s*[\"']([^\"']+)[\"'][^>]*>"
        r"(.*?)"
        r"</a>",
        flags=re.IGNORECASE | re.DOTALL,
    )

    for match in pattern.finditer(html):
        href = unescape(match.group(1)).strip()
        text = normalize_text(
            re.sub(
                r"<[^>]+>",
                " ",
                match.group(2),
            )
        )

        absolute = urljoin(base_url, href)

        links.append(
            {
                "text": text,
                "href": href,
                "url": absolute,
            }
        )

    return links


def extract_forms(html: str, base_url: str):
    forms = []

    form_pattern = re.compile(
        r"<form\b([^>]*)>(.*?)</form>",
        flags=re.IGNORECASE | re.DOTALL,
    )

    for form_match in form_pattern.finditer(html):
        attributes = form_match.group(1)
        body = form_match.group(2)

        action_match = re.search(
            r"\baction\s*=\s*[\"']([^\"']*)[\"']",
            attributes,
            flags=re.IGNORECASE,
        )

        method_match = re.search(
            r"\bmethod\s*=\s*[\"']([^\"']*)[\"']",
            attributes,
            flags=re.IGNORECASE,
        )

        action = (
            action_match.group(1).strip()
            if action_match
            else ""
        )

        method = (
            method_match.group(1).strip().upper()
            if method_match
            else "GET"
        )

        fields = []

        input_pattern = re.compile(
            r"<input\b([^>]*)>",
            flags=re.IGNORECASE | re.DOTALL,
        )

        for input_match in input_pattern.finditer(body):
            attrs = input_match.group(1)

            name_match = re.search(
                r"\bname\s*=\s*[\"']([^\"']*)[\"']",
                attrs,
                flags=re.IGNORECASE,
            )

            value_match = re.search(
                r"\bvalue\s*=\s*[\"']([^\"']*)[\"']",
                attrs,
                flags=re.IGNORECASE,
            )

            type_match = re.search(
                r"\btype\s*=\s*[\"']([^\"']*)[\"']",
                attrs,
                flags=re.IGNORECASE,
            )

            if not name_match:
                continue

            fields.append(
                {
                    "name": name_match.group(1),
                    "value": (
                        value_match.group(1)
                        if value_match
                        else ""
                    ),
                    "type": (
                        type_match.group(1)
                        if type_match
                        else ""
                    ),
                }
            )

        select_pattern = re.compile(
            r"<select\b([^>]*)>(.*?)</select>",
            flags=re.IGNORECASE | re.DOTALL,
        )

        for select_match in select_pattern.finditer(body):
            attrs = select_match.group(1)
            select_body = select_match.group(2)

            name_match = re.search(
                r"\bname\s*=\s*[\"']([^\"']*)[\"']",
                attrs,
                flags=re.IGNORECASE,
            )

            if not name_match:
                continue

            options = []

            option_pattern = re.compile(
                r"<option\b([^>]*)>(.*?)</option>",
                flags=re.IGNORECASE | re.DOTALL,
            )

            for option_match in option_pattern.finditer(
                select_body
            ):
                option_attrs = option_match.group(1)
                option_text = normalize_text(
                    re.sub(
                        r"<[^>]+>",
                        " ",
                        option_match.group(2),
                    )
                )

                option_value_match = re.search(
                    r"\bvalue\s*=\s*[\"']([^\"']*)[\"']",
                    option_attrs,
                    flags=re.IGNORECASE,
                )

                options.append(
                    {
                        "value": (
                            option_value_match.group(1)
                            if option_value_match
                            else ""
                        ),
                        "text": option_text,
                    }
                )

            fields.append(
                {
                    "name": name_match.group(1),
                    "type": "select",
                    "options": options,
                }
            )

        forms.append(
            {
                "action": urljoin(base_url, action),
                "method": method,
                "fields": fields,
            }
        )

    return forms


def extract_scripts(html: str, base_url: str):
    scripts = []

    pattern = re.compile(
        r"<script\b([^>]*)>(.*?)</script>",
        flags=re.IGNORECASE | re.DOTALL,
    )

    for match in pattern.finditer(html):
        attrs = match.group(1)
        body = match.group(2)

        src_match = re.search(
            r"\bsrc\s*=\s*[\"']([^\"']+)[\"']",
            attrs,
            flags=re.IGNORECASE,
        )

        src = ""

        if src_match:
            src = urljoin(
                base_url,
                unescape(src_match.group(1)),
            )

        scripts.append(
            {
                "src": src,
                "body": body,
            }
        )

    return scripts


def extract_data_candidates(html: str, base_url: str):
    """
    Find URLs that look like data/API/download endpoints.

    This is deliberately heuristic. We do not assume an endpoint.
    """

    candidates = set()

    url_patterns = [
        r"""["']([^"']+\.(?:json|csv|ashx|php|jsp)(?:\?[^"']*)?)["']""",
        r"""["']([^"']*(?:api|ajax|query|search|download|export|history|historical)[^"']*)["']""",
    ]

    for pattern in url_patterns:
        for match in re.finditer(
            pattern,
            html,
            flags=re.IGNORECASE,
        ):
            raw = unescape(match.group(1)).strip()

            if not raw:
                continue

            if raw.startswith("javascript:"):
                continue

            absolute = urljoin(base_url, raw)

            parsed = urlparse(absolute)

            if parsed.scheme not in ("http", "https"):
                continue

            candidates.add(absolute)

    return sorted(candidates)


def print_forms(forms):
    print()
    print("=" * 80)
    print("FORMS")
    print("=" * 80)

    if not forms:
        print("No forms found.")
        return

    for index, form in enumerate(forms):
        print()
        print(f"FORM [{index}]")
        print(f"method: {form['method']}")
        print(f"action: {form['action']}")

        for field in form["fields"]:
            print(
                f"  field: {field.get('name', '')}"
                f" type={field.get('type', '')}"
                f" value={field.get('value', '')}"
            )

            options = field.get("options")

            if options:
                for option in options[:20]:
                    print(
                        "    option: "
                        f"value={option['value']} "
                        f"text={option['text']}"
                    )


def print_relevant_links(links):
    print()
    print("=" * 80)
    print("RELEVANT LINKS")
    print("=" * 80)

    keywords = (
        "csv",
        "download",
        "export",
        "query",
        "search",
        "historical",
        "history",
        "daily",
        "day",
        "etf",
        "api",
        "ajax",
    )

    selected = []

    for link in links:
        combined = (
            f"{link['text']} "
            f"{link['href']} "
            f"{link['url']}"
        ).lower()

        if any(
            keyword in combined
            for keyword in keywords
        ):
            selected.append(link)

    if not selected:
        print("No obvious data links found.")
        return

    seen = set()

    for link in selected:
        key = link["url"]

        if key in seen:
            continue

        seen.add(key)

        print()
        print(f"text: {link['text']}")
        print(f"href: {link['href']}")
        print(f"url:  {link['url']}")


def print_data_candidates(candidates):
    print()
    print("=" * 80)
    print("DATA / API CANDIDATES")
    print("=" * 80)

    if not candidates:
        print("No obvious API/data endpoint found.")
        return

    for index, url in enumerate(
        candidates,
        start=1,
    ):
        print(f"{index:02d}. {url}")


def search_symbol_in_html(
    html: str,
    symbol: str,
):
    print()
    print("=" * 80)
    print("SYMBOL SEARCH IN PAGE HTML")
    print("=" * 80)

    upper_html = html.upper()
    upper_symbol = symbol.upper()

    positions = []

    start = 0

    while True:
        position = upper_html.find(
            upper_symbol,
            start,
        )

        if position < 0:
            break

        positions.append(position)
        start = position + len(upper_symbol)

    print(
        f"symbol: {symbol}"
    )
    print(
        f"matches_in_html: {len(positions)}"
    )

    for index, position in enumerate(
        positions[:20],
        start=1,
    ):
        left = max(0, position - 250)
        right = min(
            len(html),
            position + len(symbol) + 250,
        )

        snippet = normalize_text(
            html[left:right]
        )

        print()
        print(
            f"MATCH [{index}]"
        )
        print(snippet)


def inspect_script_sources(
    scripts,
    base_url: str,
):
    print()
    print("=" * 80)
    print("SCRIPT SOURCES")
    print("=" * 80)

    external = [
        script["src"]
        for script in scripts
        if script.get("src")
    ]

    if not external:
        print("No external scripts found.")
        return

    seen = set()

    for src in external:
        if src in seen:
            continue

        seen.add(src)
        print(src)


def inspect_inline_scripts(
    scripts,
):
    print()
    print("=" * 80)
    print("INLINE DATA-REQUEST CLUES")
    print("=" * 80)

    patterns = (
        "ajax",
        "$.get",
        "$.post",
        "fetch(",
        "axios",
        "XMLHttpRequest",
        ".csv",
        ".json",
        "download",
        "export",
        "api/",
        "api.",
        "query",
        "historical",
        "etf",
    )

    found_any = False

    for index, script in enumerate(scripts):
        body = script.get("body", "")

        if not body.strip():
            continue

        body_lower = body.lower()

        hits = [
            pattern
            for pattern in patterns
            if pattern.lower() in body_lower
        ]

        if not hits:
            continue

        found_any = True

        print()
        print(
            f"SCRIPT [{index}] "
            f"hits={', '.join(hits)}"
        )

        lines = body.splitlines()

        printed = 0

        for line_number, line in enumerate(
            lines,
            start=1,
        ):
            line_lower = line.lower()

            if any(
                pattern.lower() in line_lower
                for pattern in patterns
            ):
                print(
                    f"  {line_number:04d}: "
                    f"{line.strip()[:500]}"
                )

                printed += 1

                if printed >= 30:
                    break

    if not found_any:
        print(
            "No obvious inline data-request "
            "code found."
        )


def request_candidate(
    url: str,
):
    """
    Only inspect candidate endpoints.
    Never treat an endpoint as valid solely
    because HTTP status is 200.
    """

    print()
    print("=" * 80)
    print("TEST CANDIDATE ENDPOINT")
    print("=" * 80)
    print(url)

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        print(
            f"REQUEST ERROR: {exc}"
        )
        return

    content_type = response.headers.get(
        "Content-Type",
        "",
    )

    print(
        f"status_code: "
        f"{response.status_code}"
    )
    print(
        f"content_type: "
        f"{content_type}"
    )
    print(
        f"content_length: "
        f"{len(response.content)}"
    )

    if response.status_code != 200:
        return

    text = decode_response(response)

    stripped = text.lstrip()

    if (
        stripped.startswith("{")
        or stripped.startswith("[")
    ):
        try:
            payload = response.json()

            print(
                "JSON: YES"
            )

            print(
                f"root_type: "
                f"{type(payload).__name__}"
            )

            find_symbol_in_object(
                payload,
                SYMBOL,
            )

            return
        except Exception:
            pass

    print(
        "JSON: NO"
    )

    upper = text.upper()

    if SYMBOL.upper() in upper:
        print(
            f"SYMBOL {SYMBOL} FOUND "
            "IN RESPONSE TEXT"
        )

        position = upper.find(
            SYMBOL.upper()
        )

        left = max(
            0,
            position - 300,
        )

        right = min(
            len(text),
            position + len(SYMBOL) + 500,
        )

        print()
        print(
            normalize_text(
                text[left:right]
            )
        )
    else:
        print(
            f"SYMBOL {SYMBOL} NOT FOUND "
            "IN RESPONSE TEXT"
        )


def find_symbol_in_object(
    value,
    symbol: str,
    path="root",
):
    """
    Recursive exact-symbol search for JSON.
    """

    target = symbol.upper()

    if isinstance(value, dict):
        for key, item in value.items():
            current_path = (
                f"{path}.{key}"
            )

            if isinstance(item, str):
                if (
                    normalize_text(item).upper()
                    == target
                ):
                    print(
                        "EXACT SYMBOL MATCH:"
                        f" {current_path}"
                        f" = {item}"
                    )

            find_symbol_in_object(
                item,
                symbol,
                current_path,
            )

        return

    if isinstance(value, list):
        for index, item in enumerate(value):
            current_path = (
                f"{path}[{index}]"
            )

            find_symbol_in_object(
                item,
                symbol,
                current_path,
            )

        return


def diagnose_page(
    url: str,
    params=None,
):
    response = request_page(
        url,
        params=params,
    )

    if response is None:
        return None

    html = decode_response(response)

    print_page_identity(
        html
    )

    print()
    print("=" * 80)
    print("HTML VALIDATION")
    print("=" * 80)

    if "<html" in html.lower():
        print(
            "HTML document: YES"
        )
    else:
        print(
            "HTML document: NO"
        )

    if "<table" in html.lower():
        print(
            "table elements: YES"
        )
    else:
        print(
            "table elements: NO"
        )

    if "application/json" in (
        response.headers
        .get("Content-Type", "")
        .lower()
    ):
        print(
            "server claims JSON: YES"
        )
    else:
        print(
            "server claims JSON: NO"
        )

    links = extract_links(
        html,
        response.url,
    )

    forms = extract_forms(
        html,
        response.url,
    )

    scripts = extract_scripts(
        html,
        response.url,
    )

    candidates = extract_data_candidates(
        html,
        response.url,
    )

    print_forms(
        forms
    )

    print_relevant_links(
        links
    )

    print_data_candidates(
        candidates
    )

    search_symbol_in_html(
        html,
        SYMBOL,
    )

    inspect_script_sources(
        scripts,
        response.url,
    )

    inspect_inline_scripts(
        scripts,
    )

    return {
        "response": response,
        "html": html,
        "links": links,
        "forms": forms,
        "scripts": scripts,
        "candidates": candidates,
    }


def main():
    print("=" * 80)
    print(
        "00838B TPEx ETF OFFICIAL "
        "PRICE DIAGNOSTIC V4"
    )
    print("=" * 80)

    print(
        f"TEST SYMBOL: {SYMBOL}"
    )

    print(
        "SOURCE: TPEx OFFICIAL"
    )

    print(
        "DATA TYPE: ETF HISTORICAL "
        "PRICE"
    )

    print()
    print(
        "Yahoo: NO"
    )

    print(
        "Universe: NO"
    )

    print(
        "Production pipeline: NO"
    )

    print(
        "fetch_prices.py: NOT MODIFIED"
    )

    print()
    print(
        "IMPORTANT:"
    )

    print(
        "The old etf_statistics.php "
        "URL is a page, not assumed JSON."
    )

    print()
    print("=" * 80)
    print(
        "CURRENT TPEx ETF DAILY "
        "HISTORICAL PAGE"
    )
    print("=" * 80)

    result = diagnose_page(
        HISTORICAL_PAGE
    )

    if result is None:
        print()
        print(
            "CURRENT PAGE REQUEST FAILED."
        )
        return 1

    candidates = result["candidates"]

    print()
    print("=" * 80)
    print(
        "OLD TPEx ETF PAGE CHECK"
    )
    print("=" * 80)

    old_result = diagnose_page(
        OLD_HISTORICAL_PAGE
    )

    if old_result is None:
        print(
            "Old page request failed."
        )

    all_candidates = set(
        candidates
    )

    if old_result:
        all_candidates.update(
            old_result["candidates"]
        )

    print()
    print("=" * 80)
    print(
        "OFFICIAL DATA CANDIDATE TEST"
    )
    print("=" * 80)

    candidate_list = sorted(
        all_candidates
    )

    if not candidate_list:
        print(
            "No data/API candidate "
            "was exposed directly "
            "in the HTML."
        )
    else:
        print(
            f"candidate_count: "
            f"{len(candidate_list)}"
        )

        for url in candidate_list[:30]:
            request_candidate(
                url
            )

    print()
    print("=" * 80)
    print("FINAL RESULT")
    print("=" * 80)

    print(
        "This diagnostic does NOT "
        "modify fetch_prices.py."
    )

    print(
        "This diagnostic does NOT "
        "modify Universe."
    )

    print(
        "This diagnostic does NOT "
        "use stk_wn1430_result.php."
    )

    print(
        "This diagnostic does NOT "
        "use the normal OTC stock "
        "quote endpoint."
    )

    print()

    if SYMBOL.upper() in (
        result["html"].upper()
    ):
        print(
            f"FOUND {SYMBOL} "
            "IN CURRENT TPEx PAGE HTML."
        )
    else:
        print(
            f"{SYMBOL} NOT FOUND "
            "IN CURRENT TPEx PAGE HTML."
        )

    print()
    print(
        "NEXT STEP:"
    )

    print(
        "Use the printed form/action/"
        "script/data candidates to identify "
        "the actual TPEx ETF historical "
        "data request."
    )

    print()
    print(
        "The test intentionally exits "
        "with code 0 after completing "
        "the endpoint diagnosis."
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
