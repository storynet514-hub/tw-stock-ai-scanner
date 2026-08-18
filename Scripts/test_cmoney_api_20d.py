#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CMoney API 20D 探測器
TEST-20D-API-V5.0

V5 核心策略：

1. 取得 CMoney 股票頁
2. 找出所有 Nuxt JS
3. 鎖定 Ocean Service 相關 JS
4. 不再只搜尋 axios/fetch
5. 擷取 Ocean Service definition 周邊完整原始碼
6. 從 definition / method / request builder 還原 API 線索
7. 建立 source-derived request candidates
8. 實際呼叫候選 request
9. 分析 response：
   - >=20 個不同交易日期
   - 同一 structured response 存在主力買賣超欄位
10. 符合才 true_20d=True

固定測試：
2337 旺宏
2426 鼎元
2368 金像電
3081 聯亞

注意：
3081 = 聯亞
3088 = 艾訊
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests


VERSION = "TEST-20D-API-V5.0"

ROOT = Path(__file__).resolve().parent.parent

OUT = (
    ROOT
    / "Data"
    / "test_cmoney_api_20d.json"
)

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
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": (
        "application/json,"
        "text/plain,"
        "*/*"
    ),
    "Accept-Language": (
        "zh-TW,zh;q=0.9,en;q=0.8"
    ),
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
    |dealer
    |institution
    """,
    re.I | re.X,
)

OCEAN_KEYWORDS = (
    "API_FORUM_OCEAN_SERVICE",
    "SERVICE_FORUM_OCEAN_SERVICE",
    "forumOceanService",
    "ForumOceanService",
    "oceanService",
    "OceanService",
)


def now_iso():
    return datetime.now(
        timezone.utc
    ).isoformat()


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

    for value in DATE_RE.findall(
        text or ""
    ):

        value = normalize_date(value)

        if value and value not in dates:
            dates.append(value)

    return dates


def make_absolute_url(
    value,
    base_url,
):

    if not value:
        return None

    value = (
        value
        .strip()
        .strip("\"'")
        .strip("`")
        .replace("\\/", "/")
    )

    if value.startswith("//"):
        return "https:" + value

    if value.startswith("/"):
        return urljoin(
            base_url,
            value,
        )

    if value.startswith("http://"):
        return value

    if value.startswith("https://"):
        return value

    return None


def is_cmoney_url(url):

    try:

        host = (
            urlparse(url)
            .netloc
            .lower()
        )

        return (
            host == "www.cmoney.tw"
            or host.endswith(
                ".cmoney.tw"
            )
        )

    except Exception:
        return False


def extract_script_urls(
    html,
    page_url,
):

    results = []

    pattern = re.compile(
        r'<script[^>]+src=["\']'
        r'([^"\']+)'
        r'["\']',
        re.I,
    )

    for raw in pattern.findall(
        html
    ):

        url = make_absolute_url(
            raw,
            page_url,
        )

        if (
            url
            and url not in results
        ):

            results.append(url)

    return results


def find_keyword_contexts(
    text,
    keywords,
    radius=3000,
    limit=100,
):

    results = []

    lower = text.lower()

    for keyword in keywords:

        keyword_lower = keyword.lower()

        start = 0

        while True:

            pos = lower.find(
                keyword_lower,
                start,
            )

            if pos < 0:
                break

            context = text[
                max(
                    0,
                    pos - radius,
                ):
                min(
                    len(text),
                    pos + radius,
                )
            ]

            context = re.sub(
                r"\s+",
                " ",
                context,
            )

            results.append(
                {
                    "keyword": keyword,
                    "position": pos,
                    "context": context,
                }
            )

            start = (
                pos
                + len(keyword_lower)
            )

            if len(results) >= limit:
                return results

    return results


def extract_ocean_contexts(text):

    return find_keyword_contexts(
        text,
        OCEAN_KEYWORDS,
        radius=3500,
        limit=100,
    )


def extract_urls_from_text(
    text,
    base_url,
):

    urls = []

    patterns = [

        r'https?://[^\s"\'`<>\\]+',

        r'["\']'
        r'/(?:api|service|forum|graphql|'
        r'TickDataService|'
        r'api/[^"\']+)'
        r'["\']',

        r'["\']'
        r'(?:api|service)/'
        r'[^"\']+'
        r'["\']',
    ]

    for pattern in patterns:

        for raw in re.findall(
            pattern,
            text,
            flags=re.I,
        ):

            raw = raw.strip(
                "\"'"
            )

            url = make_absolute_url(
                raw,
                base_url,
            )

            if not url:
                continue

            if not is_cmoney_url(url):
                continue

            if url not in urls:
                urls.append(url)

    return urls


def extract_function_candidates(
    text,
):

    results = []

    patterns = [

        r'(?:async\s+)?function\s+'
        r'([A-Za-z_$][\w$]*)\s*'
        r'\(([^)]*)\)',

        r'([A-Za-z_$][\w$]*)\s*:\s*'
        r'(?:async\s*)?function\s*'
        r'\(([^)]*)\)',

        r'([A-Za-z_$][\w$]*)\s*=\s*'
        r'(?:async\s*)?'
        r'\(([^)]*)\)\s*=>',

        r'([A-Za-z_$][\w$]*)\s*=\s*'
        r'(?:async\s*)?'
        r'([A-Za-z_$][\w$]*)',
    ]

    for pattern in patterns:

        for match in re.finditer(
            pattern,
            text,
            flags=re.I,
        ):

            name = match.group(1)

            args = (
                match.group(2)
                if match.lastindex >= 2
                else ""
            )

            results.append(
                {
                    "name": name,
                    "args": args,
                    "position": match.start(),
                }
            )

    unique = {}

    for item in results:

        key = (
            item["name"],
            item["position"],
        )

        unique[key] = item

    return list(
        unique.values()
    )


def extract_request_patterns(
    text,
    base_url,
):

    results = []

    patterns = [

        # axios.get/post/etc
        re.compile(
            r'(?:axios|\$axios)'
            r'\s*\.\s*'
            r'(get|post|put|delete|patch)'
            r'\s*\(',
            re.I,
        ),

        # request.get/post/etc
        re.compile(
            r'(?:request|service|api|client)'
            r'\s*\.\s*'
            r'(get|post|put|delete|patch)'
            r'\s*\(',
            re.I,
        ),

        # fetch(...)
        re.compile(
            r'\bfetch\s*\(',
            re.I,
        ),

        # $fetch(...)
        re.compile(
            r'\$fetch\s*\(',
            re.I,
        ),

        # .request(...)
        re.compile(
            r'\.request\s*\(',
            re.I,
        ),

        # method: "GET"
        re.compile(
            r'\bmethod\s*:\s*["\']'
            r'(GET|POST|PUT|DELETE|PATCH)'
            r'["\']',
            re.I,
        ),
    ]

    for pattern in patterns:

        for match in pattern.finditer(
            text
        ):

            start = max(
                0,
                match.start() - 1500,
            )

            end = min(
                len(text),
                match.start() + 5000,
            )

            context = text[
                start:end
            ]

            context = re.sub(
                r"\s+",
                " ",
                context,
            )

            urls = extract_urls_from_text(
                context,
                base_url,
            )

            method = "GET"

            if match.lastindex:

                value = (
                    match.group(1)
                    or ""
                ).upper()

                if value in {
                    "GET",
                    "POST",
                    "PUT",
                    "DELETE",
                    "PATCH",
                }:

                    method = value

            results.append(
                {
                    "method": method,
                    "position": match.start(),
                    "urls": urls[:50],
                    "context": context[:6000],
                }
            )

    return results


def extract_string_literals(
    text,
):

    results = []

    pattern = re.compile(
        r'["\']([^"\']{2,500})["\']'
    )

    for match in pattern.finditer(
        text
    ):

        value = match.group(1)

        lower = value.lower()

        if any(
            keyword in lower
            for keyword in (
                "/api/",
                "api/",
                "/service/",
                "tickdataservice",
                "graphql",
                "chip",
                "force",
                "main",
                "ocean",
            )
        ):

            results.append(
                {
                    "value": value,
                    "position": match.start(),
                }
            )

    return results


def analyze_json_structure(
    payload
):

    dates = []
    force_fields = []
    arrays = []

    def walk(
        value,
        path="$",
        depth=0,
    ):

        if depth > 25:
            return

        if isinstance(
            value,
            dict,
        ):

            for key, child in value.items():

                key_text = str(key)

                if FORCE_RE.search(
                    key_text
                ):

                    force_fields.append(
                        f"{path}/{key_text}"
                    )

                normalized = re.sub(
                    r"[_\-\s]",
                    "",
                    key_text,
                ).lower()

                if normalized in {
                    "date",
                    "tradedate",
                    "datetime",
                    "tradingdate",
                    "dealdate",
                    "transdate",
                }:

                    if isinstance(
                        child,
                        str,
                    ):

                        date = normalize_date(
                            child
                        )

                        if date:
                            dates.append(
                                date
                            )

                    elif isinstance(
                        child,
                        list,
                    ):

                        for item in child:

                            date = normalize_date(
                                item
                            )

                            if date:
                                dates.append(
                                    date
                                )

                walk(
                    child,
                    (
                        f"{path}/{key_text}"
                    ),
                    depth + 1,
                )

        elif isinstance(
            value,
            list,
        ):

            if len(value) >= 10:

                arrays.append(
                    {
                        "path": path,
                        "length": len(value),
                    }
                )

            for index, child in enumerate(
                value[:500]
            ):

                walk(
                    child,
                    f"{path}[{index}]",
                    depth + 1,
                )

        elif isinstance(
            value,
            str,
        ):

            date = normalize_date(
                value
            )

            if date:
                dates.append(
                    date
                )

    walk(payload)

    dates = list(
        dict.fromkeys(dates)
    )

    force_fields = list(
        dict.fromkeys(
            force_fields
        )
    )

    return {
        "date_count": len(dates),
        "dates": dates[:200],
        "force_fields": force_fields[:200],
        "arrays": arrays[:200],
        "has_20_dates": (
            len(dates) >= 20
        ),
        "has_force_field": bool(
            force_fields
        ),
        "true_20d": (
            len(dates) >= 20
            and bool(force_fields)
        ),
    }


def build_parameter_sets(
    symbol
):

    return [

        {"symbol": symbol},

        {"stockId": symbol},

        {"stockNo": symbol},

        {"stockCode": symbol},

        {"code": symbol},

        {"ticker": symbol},

        {"id": symbol},

        {"stock": symbol},

        {"stock_id": symbol},

        {"stock_code": symbol},

    ]


def test_endpoint(
    session,
    url,
    method,
    symbol,
):

    results = []

    params_list = (
        build_parameter_sets(
            symbol
        )
    )

    for params in params_list:

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

                "status": (
                    response.status_code
                ),

                "content_type": (
                    response.headers.get(
                        "content-type",
                        "",
                    )
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
                    in item[
                        "content_type"
                    ].lower()

                    or text.lstrip().startswith(
                        "{"
                    )

                    or text.lstrip().startswith(
                        "["
                    )
                )
            ):

                try:

                    payload = (
                        response.json()
                    )

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

            results.append(
                item
            )

        except Exception as exc:

            results.append(
                {
                    "url": url,
                    "method": method,
                    "params": params,
                    "error": str(exc)[:1000],
                }
            )

        time.sleep(
            0.15
        )

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

        "strategy": (
            "Source Trace → "
            "Ocean Service → "
            "Definition → "
            "Method → "
            "Request → "
            "Response Structure"
        ),

        "stocks": [],
    }

    print("=" * 76)

    print(
        "台股 AI 選股系統 "
        "CMoney API 20D 探測器 V5"
    )

    print("=" * 76)

    print(
        "測試策略："
    )

    print(
        "Source Trace → Ocean Service → "
        "Definition → Method → Request → "
        "Response Structure"
    )

    print()

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

            "service_definitions": [],

            "service_methods": [],

            "request_candidates": [],

            "tests": [],
        }

        try:

            response = session.get(
                page_url,
                headers=HEADERS,
                timeout=TIMEOUT,
            )

            print(
                f"HTTP："
                f"{response.status_code}"
            )

            if (
                response.status_code
                != 200
            ):

                stock[
                    "error"
                ] = (
                    "stock page HTTP "
                    f"{response.status_code}"
                )

                report[
                    "stocks"
                ].append(stock)

                continue

            html = response.text

            html_dates = (
                extract_dates_from_text(
                    html
                )
            )

            ocean_contexts = (
                extract_ocean_contexts(
                    html
                )
            )

            stock[
                "html"
            ] = {

                "status": (
                    response.status_code
                ),

                "bytes": len(
                    response.content
                ),

                "dates": html_dates,

                "date_count": len(
                    html_dates
                ),

                "ocean_contexts": (
                    ocean_contexts[:30]
                ),
            }

            scripts = (
                extract_script_urls(
                    html,
                    page_url,
                )
            )

            print(
                f"發現 JavaScript："
                f"{len(scripts)} 個"
            )

            all_candidates = []

            all_request_candidates = []

            for index, script_url in enumerate(
                scripts[:120],
                1,
            ):

                try:

                    js_response = (
                        session.get(
                            script_url,
                            headers=HEADERS,
                            timeout=TIMEOUT,
                        )
                    )

                    if (
                        js_response.status_code
                        != 200
                    ):

                        continue

                    js = (
                        js_response.text
                    )

                    ocean = (
                        extract_ocean_contexts(
                            js
                        )
                    )

                    if not ocean:
                        continue

                    urls = (
                        extract_urls_from_text(
                            js,
                            script_url,
                        )
                    )

                    request_patterns = (
                        extract_request_patterns(
                            js,
                            script_url,
                        )
                    )

                    strings = (
                        extract_string_literals(
                            js
                        )
                    )

                    functions = (
                        extract_function_candidates(
                            js
                        )
                    )

                    evidence = {

                        "script": script_url,

                        "bytes": len(
                            js_response.content
                        ),

                        "ocean_context_count": (
                            len(ocean)
                        ),

                        "ocean_contexts": (
                            ocean[:50]
                        ),

                        "urls": urls[:100],

                        "request_patterns": (
                            request_patterns[:100]
                        ),

                        "strings": (
                            strings[:200]
                        ),

                        "functions": (
                            functions[:200]
                        ),
                    }

                    stock[
                        "js_evidence"
                    ].append(
                        evidence
                    )

                    all_candidates.extend(
                        urls
                    )

                    # --------------------------------------------------
                    # V5 核心：
                    # Ocean context 本身也視為 request source。
                    # 即使 request pattern parser 沒抓到，
                    # 也保留 context 給下一層分析。
                    # --------------------------------------------------

                    for ocean_item in ocean:

                        context = (
                            ocean_item[
                                "context"
                            ]
                        )

                        context_urls = (
                            extract_urls_from_text(
                                context,
                                script_url,
                            )
                        )

                        context_requests = (
                            extract_request_patterns(
                                context,
                                script_url,
                            )
                        )

                        if context_urls:

                            all_candidates.extend(
                                context_urls
                            )

                        if context_requests:

                            all_request_candidates.extend(
                                context_requests
                            )

                        stock[
                            "service_definitions"
                        ].append(
                            {
                                "script": script_url,
                                "keyword": (
                                    ocean_item[
                                        "keyword"
                                    ]
                                ),
                                "position": (
                                    ocean_item[
                                        "position"
                                    ]
                                ),
                                "context": context,
                                "urls": context_urls[:50],
                                "requests": (
                                    context_requests[:50]
                                ),
                            }
                        )

                    print()

                    print(
                        f"★ JS {index}: "
                        f"{script_url}"
                    )

                    print(
                        "   Ocean context："
                        f"{len(ocean)}"
                    )

                    print(
                        "   Request pattern："
                        f"{len(request_patterns)}"
                    )

                    print(
                        "   Candidate URL："
                        f"{len(urls)}"
                    )

                except Exception as exc:

                    stock[
                        "js_evidence"
                    ].append(
                        {
                            "script": script_url,
                            "error": repr(exc),
                        }
                    )

                time.sleep(
                    0.05
                )

            all_candidates = list(
                dict.fromkeys(
                    all_candidates
                )
            )

            all_request_candidates = (
                list(
                    {
                        (
                            item.get(
                                "method",
                                "GET"
                            ),
                            tuple(
                                item.get(
                                    "urls",
                                    []
                                )
                            ),
                            item.get(
                                "position",
                                -1
                            ),
                        ): item
                        for item
                        in all_request_candidates
                    }.values()
                )
            )

            stock[
                "service_methods"
            ] = all_request_candidates[
                :300
            ]

            print()
            print(
                "================================"
            )

            print(
                "Source Trace 結果"
            )

            print(
                f"Ocean JS："
                f"{len(stock['js_evidence'])}"
            )

            print(
                "Service definition："
                f"{len(stock['service_definitions'])}"
            )

            print(
                "Request pattern："
                f"{len(all_request_candidates)}"
            )

            print(
                "Candidate URL："
                f"{len(all_candidates)}"
            )

            print(
                "================================"
            )

            # ----------------------------------------------------------
            # 建立 request candidates
            #
            # 來源優先級：
            #
            # 1. Ocean context URL
            # 2. request pattern URL
            # 3. JS URL
            #
            # 不再單純因為 /api/ 就直接測試。
            # ----------------------------------------------------------

            request_map = {}

            for item in all_request_candidates:

                method = (
                    item.get(
                        "method",
                        "GET",
                    )
                )

                for url in item.get(
                    "urls",
                    [],
                ):

                    key = (
                        method,
                        url,
                    )

                    request_map[
                        key
                    ] = {

                        "method": method,

                        "url": url,

                        "source": (
                            "request_pattern"
                        ),

                        "context": (
                            item.get(
                                "context",
                                "",
                            )
                        ),
                    }

            # Ocean definitions

            for definition in (
                stock[
                    "service_definitions"
                ]
            ):

                for url in definition.get(
                    "urls",
                    [],
                ):

                    key = (
                        "GET",
                        url,
                    )

                    if key not in request_map:

                        request_map[
                            key
                        ] = {

                            "method": "GET",

                            "url": url,

                            "source": (
                                "ocean_definition"
                            ),

                            "context": (
                                definition.get(
                                    "context",
                                    "",
                                )
                            ),
                        }

            stock[
                "request_candidates"
            ] = list(
                request_map.values()
            )[:500]

            print(
                "Source-derived Request："
                f"{len(stock['request_candidates'])}"
            )

            # ----------------------------------------------------------
            # 如果仍然沒有 request，
            # 不代表 API 不存在。
            #
            # V5 會輸出 Ocean definition，
            # 下一版可以針對實際 definition
            # 做精準 parser。
            # ----------------------------------------------------------

            for candidate in (
                stock[
                    "request_candidates"
                ][:100]
            ):

                endpoint = candidate[
                    "url"
                ]

                method = candidate[
                    "method"
                ]

                print()
                print(
                    "[SOURCE API]"
                )

                print(
                    f"{method} "
                    f"{endpoint}"
                )

                test_results = (
                    test_endpoint(
                        session,
                        endpoint,
                        method,
                        symbol,
                    )
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
                        "winning_source"
                    ] = candidate.get(
                        "source"
                    )

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
                "Ocean JS："
                f"{len(stock['js_evidence'])}"
            )

            print(
                "Service definition："
                f"{len(stock['service_definitions'])}"
            )

            print(
                "Source Request："
                f"{len(stock['request_candidates'])}"
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
        ].append(
            stock
        )

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
        "CMoney API 20D V5 最終判定"
    )

    print(
        f"真正20D API："
        f"{true_count}/{len(STOCKS)}"
    )

    for stock in report[
        "stocks"
    ]:

        print(
            f"  "
            f"{stock['symbol']} "
            f"{stock['name']}："
            f"{stock.get('true_20d', False)}"
        )

    print()

    print(
        f"結果已寫入：{OUT}"
    )

    print("=" * 76)


if __name__ == "__main__":
    main()