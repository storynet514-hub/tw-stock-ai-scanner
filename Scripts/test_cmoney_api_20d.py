#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CMoney API 20D 探測器
TEST-20D-API-V3.0

目的：
1. 不再盲猜大量 API endpoint
2. 追蹤 CMoney Forum Ocean Service
3. 從 Nuxt JS 找出實際 API 呼叫
4. 實際呼叫候選 endpoint
5. 分析 response 是否真的包含 >=20 個交易日期
6. 同一資料結構必須包含主力買賣超相關欄位
7. 確認後才標記 true_20d = True

固定測試：
2337 旺宏
2426 鼎元
2368 金像電
3081 聯亞
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests


VERSION = "TEST-20D-API-V3.0"

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "Data" / "test_cmoney_api_20d.json"

BASE = "https://www.cmoney.tw"

TIMEOUT = 25

STOCKS = [
    ("2337", "旺宏"),
    ("2426", "鼎元"),
    ("2368", "金像電"),
    ("3081", "聯亞"),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Referer": BASE + "/",
}

DATE_RE = re.compile(
    r"(?:19|20)\d{2}[-/.]\d{1,2}[-/.]\d{1,2}"
)

FORCE_RE = re.compile(
    r"""
    主力
    |主力買賣超
    |主力買超
    |主力賣超
    |買賣超
    |net[_-]?buy
    |net[_-]?sell
    |net[_-]?buy[_-]?sell
    |main[_-]?force
    |mainForce
    |buy[_-]?sell
    |buySell
    """,
    re.I | re.X,
)

OCEAN_KEYWORDS = (
    "API_FORUM_OCEAN_SERVICE",
    "SERVICE_FORUM_OCEAN_SERVICE",
    "forumOceanService",
)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def normalize_date(value):
    if value is None:
        return None

    text = str(value).strip()

    m = DATE_RE.fullmatch(text)

    if not m:
        return None

    return (
        text
        .replace("-", "/")
        .replace(".", "/")
    )


def extract_dates_from_text(text):
    dates = []

    for value in DATE_RE.findall(text or ""):
        value = normalize_date(value)

        if value and value not in dates:
            dates.append(value)

    return dates


def make_absolute_url(value, base_url):
    if not value:
        return None

    value = (
        value.strip()
        .strip("\"'")
        .strip("`")
        .replace("\\/", "/")
    )

    if value.startswith("//"):
        return "https:" + value

    if value.startswith("/"):
        return urljoin(base_url, value)

    if value.startswith("http://"):
        return value

    if value.startswith("https://"):
        return value

    return None


def is_cmoney_url(url):
    try:
        host = urlparse(url).netloc.lower()
        return host == "www.cmoney.tw" or host.endswith(".cmoney.tw")
    except Exception:
        return False


def extract_script_urls(html, page_url):
    results = []

    pattern = re.compile(
        r'<script[^>]+src=["\']([^"\']+)["\']',
        re.I,
    )

    for raw in pattern.findall(html):
        url = make_absolute_url(raw, page_url)

        if url and url not in results:
            results.append(url)

    return results


def extract_runtime_context(text):
    contexts = []

    for keyword in OCEAN_KEYWORDS:

        start = 0

        while True:

            pos = text.find(keyword, start)

            if pos < 0:
                break

            context = text[
                max(0, pos - 1200):
                min(len(text), pos + 3500)
            ]

            context = re.sub(
                r"\s+",
                " ",
                context,
            )

            if context not in contexts:
                contexts.append(context)

            start = pos + len(keyword)

    return contexts[:50]


def extract_urls_from_js(js, js_url):

    urls = []

    patterns = [
        r'https?://[^\s"\'`<>\\]+',
        r'/(?:api|service|forum|graphql|TickDataService)[^\s"\'`<>\\]*',
    ]

    for pattern in patterns:

        for raw in re.findall(
            pattern,
            js,
            flags=re.I,
        ):

            url = make_absolute_url(
                raw,
                js_url,
            )

            if not url:
                continue

            if not is_cmoney_url(url):
                continue

            if url not in urls:
                urls.append(url)

    return urls


def extract_call_sites(js, js_url):

    results = []

    pattern = re.compile(
        r"""
        (?:
            \$axios
            |
            axios
            |
            fetch
            |
            \$fetch
        )
        \s*
        (?:\.\s*(get|post|put|delete))?
        \s*\(
        """,
        re.I | re.X,
    )

    for match in pattern.finditer(js):

        method = (
            match.group(1)
            or "GET"
        ).upper()

        start = max(
            0,
            match.start() - 1000,
        )

        end = min(
            len(js),
            match.start() + 3500,
        )

        context = js[start:end]

        context = re.sub(
            r"\s+",
            " ",
            context,
        )

        urls = extract_urls_from_js(
            context,
            js_url,
        )

        results.append(
            {
                "method": method,
                "urls": urls,
                "context": context[:4500],
            }
        )

    return results


def contains_force_field(key):

    return bool(
        FORCE_RE.search(
            str(key)
        )
    )


def analyze_json_structure(payload):

    dates = []
    force_fields = []
    arrays = []

    def walk(value, path="$", depth=0):

        if depth > 20:
            return

        if isinstance(value, dict):

            for key, child in value.items():

                key_text = str(key)

                if contains_force_field(
                    key_text
                ):

                    force_fields.append(
                        f"{path}/{key_text}"
                    )

                normalized_key = re.sub(
                    r"[_\-\s]",
                    "",
                    key_text,
                ).lower()

                if normalized_key in (
                    "date",
                    "tradedate",
                    "datetime",
                    "tradingdate",
                ):

                    if isinstance(
                        child,
                        str,
                    ):

                        date = normalize_date(
                            child
                        )

                        if date:
                            dates.append(date)

                    elif isinstance(
                        child,
                        list,
                    ):

                        for item in child:

                            date = normalize_date(
                                item
                            )

                            if date:
                                dates.append(date)

                walk(
                    child,
                    f"{path}/{key_text}",
                    depth + 1,
                )

        elif isinstance(value, list):

            if len(value) >= 10:

                arrays.append(
                    {
                        "path": path,
                        "length": len(value),
                    }
                )

            for index, child in enumerate(
                value[:250]
            ):

                walk(
                    child,
                    f"{path}[{index}]",
                    depth + 1,
                )

        elif isinstance(value, str):

            date = normalize_date(
                value
            )

            if date:
                dates.append(date)

    walk(payload)

    dates = list(
        dict.fromkeys(dates)
    )

    force_fields = list(
        dict.fromkeys(force_fields)
    )

    return {
        "date_count": len(dates),
        "dates": dates[:100],
        "force_fields": force_fields[:100],
        "arrays": arrays[:100],
        "has_20_dates": len(dates) >= 20,
        "has_force_field": bool(
            force_fields
        ),
        "true_20d": (
            len(dates) >= 20
            and bool(force_fields)
        ),
    }


def build_parameter_sets(symbol):

    return [
        {"symbol": symbol},
        {"stockId": symbol},
        {"stockNo": symbol},
        {"stockCode": symbol},
        {"code": symbol},
        {"ticker": symbol},
        {"id": symbol},
    ]


def test_endpoint(
    session,
    url,
    method,
    symbol,
):

    results = []

    parameter_sets = build_parameter_sets(
        symbol
    )

    for params in parameter_sets:

        try:

            if method == "POST":

                response = session.post(
                    url,
                    json=params,
                    headers=HEADERS,
                    timeout=TIMEOUT,
                )

            else:

                response = session.get(
                    url,
                    params=params,
                    headers=HEADERS,
                    timeout=TIMEOUT,
                )

            item = {
                "url": response.url,
                "method": method,
                "params": params,
                "status": response.status_code,
                "content_type": response.headers.get(
                    "content-type",
                    "",
                ),
                "bytes": len(
                    response.content
                ),
            }

            text = response.text

            if (
                response.status_code == 200
                and (
                    "json"
                    in item["content_type"].lower()
                    or text.lstrip().startswith(
                        "{"
                    )
                    or text.lstrip().startswith(
                        "["
                    )
                )
            ):

                try:

                    payload = response.json()

                    analysis = (
                        analyze_json_structure(
                            payload
                        )
                    )

                    item[
                        "payload_analysis"
                    ] = analysis

                    if analysis[
                        "true_20d"
                    ]:

                        results.append(
                            item
                        )

                        return results

                except Exception as exc:

                    item[
                        "json_error"
                    ] = repr(exc)

            results.append(item)

        except Exception as exc:

            results.append(
                {
                    "url": url,
                    "method": method,
                    "params": params,
                    "error": str(exc)[:1000],
                }
            )

        time.sleep(0.15)

    return results


def main():

    OUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    session = requests.Session()

    session.headers.update(
        HEADERS
    )

    report = {
        "version": VERSION,
        "started_at": now_iso(),
        "stocks": [],
    }

    print("=" * 76)
    print(
        "台股 AI 選股系統 "
        "CMoney API 20D 探測器 V3"
    )
    print("=" * 76)

    print(
        "固定測試："
    )

    for symbol, name in STOCKS:

        print(
            f"  {symbol} {name}"
        )

    for symbol, name in STOCKS:

        print()
        print("=" * 76)
        print(
            f"{symbol} {name}"
        )
        print("=" * 76)

        page_url = (
            f"{BASE}/forum/stock/"
            f"{symbol}?s=main-force"
        )

        stock = {
            "symbol": symbol,
            "name": name,
            "page_url": page_url,
            "true_20d": False,
            "html": {},
            "js_evidence": [],
            "candidate_urls": [],
            "call_sites": [],
            "tests": [],
        }

        try:

            response = session.get(
                page_url,
                headers=HEADERS,
                timeout=TIMEOUT,
            )

            print(
                f"HTTP：{response.status_code}"
            )

            if response.status_code != 200:

                stock["error"] = (
                    "stock page HTTP "
                    f"{response.status_code}"
                )

                report[
                    "stocks"
                ].append(stock)

                continue

            html = response.text

            stock["html"] = {
                "status": response.status_code,
                "bytes": len(
                    response.content
                ),
                "dates": extract_dates_from_text(
                    html
                ),
                "date_count": len(
                    extract_dates_from_text(
                        html
                    )
                ),
                "ocean_contexts": (
                    extract_runtime_context(
                        html
                    )
                ),
            }

            scripts = extract_script_urls(
                html,
                page_url,
            )

            print(
                f"發現 JavaScript："
                f"{len(scripts)} 個"
            )

            all_candidates = []
            all_calls = []

            for index, script_url in enumerate(
                scripts[:120],
                1,
            ):

                try:

                    js_response = session.get(
                        script_url,
                        headers=HEADERS,
                        timeout=TIMEOUT,
                    )

                    if (
                        js_response.status_code
                        != 200
                    ):
                        continue

                    js = js_response.text

                    has_ocean = any(
                        keyword.lower()
                        in js.lower()
                        for keyword
                        in OCEAN_KEYWORDS
                    )

                    if not has_ocean:
                        continue

                    urls = extract_urls_from_js(
                        js,
                        script_url,
                    )

                    calls = extract_call_sites(
                        js,
                        script_url,
                    )

                    all_candidates.extend(
                        urls
                    )

                    all_calls.extend(
                        calls
                    )

                    stock[
                        "js_evidence"
                    ].append(
                        {
                            "script": script_url,
                            "bytes": len(
                                js_response.content
                            ),
                            "ocean_context": (
                                extract_runtime_context(
                                    js
                                )[:10]
                            ),
                            "urls": urls[:100],
                            "calls": calls[:30],
                        }
                    )

                    print(
                        f"★ JS {index}: "
                        f"{script_url}"
                    )

                    print(
                        "   "
                        f"發現候選 URL："
                        f"{len(urls)}"
                    )

                except Exception:
                    pass

                time.sleep(0.05)

            all_candidates = list(
                dict.fromkeys(
                    all_candidates
                )
            )

            stock[
                "candidate_urls"
            ] = all_candidates[:200]

            stock[
                "call_sites"
            ] = all_calls[:150]

            print(
                "候選 endpoint："
                f"{len(all_candidates)}"
            )

            # 優先測試與 Ocean / service / chip / force
            # 相關的 endpoint。
            def score(url):

                text = url.lower()

                score = 0

                keywords = [
                    "ocean",
                    "forum",
                    "service",
                    "chip",
                    "force",
                    "main",
                    "api",
                ]

                for keyword in keywords:

                    if keyword in text:
                        score -= 10

                return score

            candidates = sorted(
                all_candidates,
                key=score,
            )

            tested_urls = set()

            for endpoint in candidates[:100]:

                if endpoint in tested_urls:
                    continue

                tested_urls.add(
                    endpoint
                )

                method = "GET"

                for call in all_calls:

                    if endpoint in call.get(
                        "urls",
                        [],
                    ):

                        method = call.get(
                            "method",
                            "GET",
                        )

                        break

                print()
                print(
                    f"[API]"
                    f" {endpoint}"
                )

                print(
                    f"METHOD：{method}"
                )

                test_results = test_endpoint(
                    session,
                    endpoint,
                    method,
                    symbol,
                )

                stock[
                    "tests"
                ].extend(
                    test_results
                )

                winner = next(
                    (
                        item
                        for item
                        in test_results
                        if item.get(
                            "payload_analysis",
                            {},
                        ).get(
                            "true_20d",
                            False,
                        )
                    ),
                    None,
                )

                if winner:

                    stock[
                        "true_20d"
                    ] = True

                    stock[
                        "winning_endpoint"
                    ] = endpoint

                    stock[
                        "winning_method"
                    ] = method

                    stock[
                        "winning_result"
                    ] = winner

                    print()
                    print(
                        "★★★★★★★★★★★★★★★★★★★★★★★★"
                    )

                    print(
                        "★ 真正 20D API 已確認"
                    )

                    print(
                        f"★ {method} "
                        f"{endpoint}"
                    )

                    print(
                        "★★★★★★★★★★★★★★★★★★★★★★★★"
                    )

                    break

            print()
            print(
                f"{symbol} {name}"
            )

            print(
                "HTML 日期："
                f"{stock['html']['date_count']}"
            )

            print(
                "JS Ocean 證據："
                f"{len(stock['js_evidence'])}"
            )

            print(
                "候選 URL："
                f"{len(stock['candidate_urls'])}"
            )

            print(
                "實際測試："
                f"{len(stock['tests'])}"
            )

            print(
                "真正20D："
                f"{stock['true_20d']}"
            )

        except Exception as exc:

            stock[
                "error"
            ] = repr(exc)

            print(
                "❌ 發生錯誤：",
                repr(exc),
            )

        report[
            "stocks"
        ].append(stock)

    true_count = sum(
        1
        for stock
        in report["stocks"]
        if stock.get(
            "true_20d",
            False,
        )
    )

    report[
        "final"
    ] = {
        "tested": len(STOCKS),
        "true_20d_count": true_count,
        "true_20d": (
            true_count > 0
        ),
        "rule": (
            "同一 structured response "
            "必須至少20個不同日期，"
            "且存在主力買賣超相關欄位"
        ),
    }

    report[
        "finished_at"
    ] = now_iso()

    OUT.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print()
    print("=" * 76)

    print(
        "CMoney API 20D V3 最終判定"
    )

    print(
        f"真正20D API："
        f"{true_count}/{len(STOCKS)}"
    )

    print(
        f"結果已寫入：{OUT}"
    )

    print("=" * 76)


if __name__ == "__main__":
    main()