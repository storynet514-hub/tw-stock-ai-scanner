#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CMoney API 20D 探測器
TEST-20D-API-V4.0

核心目的
========

V3 的問題：
1. 從 axios/fetch 附近猜 URL
2. 自己猜 symbol / stockId / stockNo 等參數
3. 沒有真正追蹤 forumOceanService 的 method
4. response 中只要任何地方出現「買賣超」就可能誤判

V4 改為：

1. 抓取 CMoney Forum 個股主力頁
2. 找出 API_FORUM_OCEAN_SERVICE / forumOceanService
3. 追蹤 service configuration
4. 找出 forumOceanService 實際 method 呼叫
5. 從 source code 擷取：
   - method
   - URL
   - query
   - body
   - arguments
6. 只有 source-trace 得到的 request 才列為高優先候選
7. 實際呼叫 response
8. 對 JSON 做「同一資料結構」分析
9. 日期 >= 20 且同一 record structure
   同時存在主力買賣超欄位才判定 True

固定測試：
2337 旺宏
2426 鼎元
2368 金像電
3081 聯亞

注意：
這是「探測器」，不是正式資料抓取程式。
任何 API 都必須經過實際 response 驗證才可以進正式系統。
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import (
    parse_qs,
    urlencode,
    urljoin,
    urlparse,
    urlunparse,
)

import requests


VERSION = "TEST-20D-API-V4.0"

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
    "Accept": (
        "application/json,text/plain,*/*"
    ),
    "Accept-Language": (
        "zh-TW,zh;q=0.9,en;q=0.8"
    ),
    "Referer": BASE + "/",
    "Origin": BASE,
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
    |買超
    |賣超
    |net[_-]?buy
    |net[_-]?sell
    |net[_-]?buy[_-]?sell
    |main[_-]?force
    |mainForce
    |buy[_-]?sell
    |buySell
    |force
    """,
    re.I | re.X,
)

OCEAN_KEYWORDS = (
    "API_FORUM_OCEAN_SERVICE",
    "SERVICE_FORUM_OCEAN_SERVICE",
    "forumOceanService",
)

REQUEST_KEYWORDS = (
    "$axios",
    "axios",
    "fetch(",
    "$fetch",
    ".get(",
    ".post(",
    ".put(",
)

STOCK_ARGUMENT_KEYS = (
    "symbol",
    "stockid",
    "stockno",
    "stockcode",
    "code",
    "ticker",
    "commkey",
    "stockkey",
    "id",
    "sid",
    "stock",
)


# ============================================================
# 基本工具
# ============================================================

def now_iso():
    return datetime.now(timezone.utc).isoformat()


def clean_js(text):
    if not text:
        return ""

    text = text.replace(
        "\\/",
        "/",
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def normalize_date(value):
    if value is None:
        return None

    text = str(value).strip()

    m = DATE_RE.fullmatch(text)

    if not m:
        return None

    parts = re.split(
        r"[-/.]",
        text,
    )

    if len(parts) != 3:
        return None

    try:
        year = int(parts[0])
        month = int(parts[1])
        day = int(parts[2])

        if not (
            1900 <= year <= 2100
            and 1 <= month <= 12
            and 1 <= day <= 31
        ):
            return None

        return (
            f"{year:04d}/"
            f"{month:02d}/"
            f"{day:02d}"
        )

    except Exception:
        return None


def extract_dates_from_text(text):
    dates = []

    for value in DATE_RE.findall(
        text or ""
    ):

        date = normalize_date(
            value
        )

        if date and date not in dates:
            dates.append(date)

    return dates


def make_absolute_url(
    value,
    base_url,
):
    if not value:
        return None

    value = (
        str(value)
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

    if value.startswith(
        "http://"
    ):
        return value

    if value.startswith(
        "https://"
    ):
        return value

    return None


def is_cmoney_url(url):
    try:
        host = urlparse(
            url
        ).netloc.lower()

        return (
            host == "www.cmoney.tw"
            or host.endswith(
                ".cmoney.tw"
            )
        )

    except Exception:
        return False


def dedupe(items):
    result = []

    seen = set()

    for item in items:

        key = json.dumps(
            item,
            ensure_ascii=False,
            sort_keys=True,
        ) if isinstance(
            item,
            (dict, list),
        ) else str(item)

        if key in seen:
            continue

        seen.add(key)
        result.append(item)

    return result


# ============================================================
# HTML / JS
# ============================================================

def extract_script_urls(
    html,
    page_url,
):
    results = []

    pattern = re.compile(
        r'<script[^>]+src=["\']([^"\']+)["\']',
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


def extract_runtime_context(
    text,
):
    contexts = []

    lowered = text.lower()

    for keyword in OCEAN_KEYWORDS:

        start = 0

        key_lower = keyword.lower()

        while True:

            pos = lowered.find(
                key_lower,
                start,
            )

            if pos < 0:
                break

            context = text[
                max(
                    0,
                    pos - 3000,
                ):
                min(
                    len(text),
                    pos + 8000,
                )
            ]

            context = clean_js(
                context
            )

            if context not in contexts:
                contexts.append(
                    context
                )

            start = (
                pos
                + len(key_lower)
            )

    return contexts[:50]


def find_matching_bracket(
    text,
    start,
    opening="{",
    closing="}",
):
    if (
        start < 0
        or start >= len(text)
        or text[start] != opening
    ):
        return -1

    depth = 0

    quote = None
    escaped = False

    for i in range(
        start,
        len(text),
    ):

        ch = text[i]

        if quote:

            if escaped:
                escaped = False
                continue

            if ch == "\\":
                escaped = True
                continue

            if ch == quote:
                quote = None

            continue

        if ch in (
            '"',
            "'",
            "`",
        ):
            quote = ch
            continue

        if ch == opening:
            depth += 1

        elif ch == closing:
            depth -= 1

            if depth == 0:
                return i

    return -1


def extract_balanced(
    text,
    start,
):
    if start < 0:
        return ""

    opening = text[start]

    pairs = {
        "{": "}",
        "[": "]",
        "(": ")",
    }

    closing = pairs.get(
        opening
    )

    if not closing:
        return ""

    end = find_matching_bracket(
        text,
        start,
        opening,
        closing,
    )

    if end < 0:
        return ""

    return text[
        start:end + 1
    ]


def extract_urls_from_js(
    js,
    js_url,
):
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

            raw = (
                raw
                .rstrip(
                    ".,;:)"
                )
            )

            url = make_absolute_url(
                raw,
                js_url,
            )

            if not url:
                continue

            if not is_cmoney_url(
                url
            ):
                continue

            if url not in urls:
                urls.append(
                    url
                )

    return urls


# ============================================================
# Ocean Service 追蹤
# ============================================================

def find_ocean_positions(js):
    positions = []

    lowered = js.lower()

    for keyword in OCEAN_KEYWORDS:

        key = keyword.lower()

        start = 0

        while True:

            pos = lowered.find(
                key,
                start,
            )

            if pos < 0:
                break

            positions.append(
                {
                    "keyword": keyword,
                    "position": pos,
                }
            )

            start = (
                pos
                + len(key)
            )

    return sorted(
        positions,
        key=lambda x: x[
            "position"
        ],
    )


def extract_service_definitions(
    js,
):
    """
    嘗試找：

    forumOceanService: ...
    forumOceanService = ...
    forumOceanService(...)
    """

    results = []

    patterns = [
        r"""
        forumOceanService
        \s*:
        """,
        r"""
        forumOceanService
        \s*=
        """,
        r"""
        forumOceanService
        \s*\
        \(
        """,
    ]

    for pattern in patterns:

        for match in re.finditer(
            pattern,
            js,
            flags=re.I | re.X,
        ):

            start = match.start()

            context = js[
                max(
                    0,
                    start - 2000,
                ):
                min(
                    len(js),
                    start + 6000,
                )
            ]

            results.append(
                {
                    "position": start,
                    "match": match.group(0),
                    "context": clean_js(
                        context
                    ),
                }
            )

    return dedupe(
        results
    )[:100]


def extract_ocean_contexts(
    js,
):
    contexts = []

    positions = find_ocean_positions(
        js
    )

    for item in positions:

        pos = item[
            "position"
        ]

        context = js[
            max(
                0,
                pos - 5000,
            ):
            min(
                len(js),
                pos + 12000,
            )
        ]

        contexts.append(
            {
                "keyword": item[
                    "keyword"
                ],
                "position": pos,
                "context": clean_js(
                    context
                ),
            }
        )

    return contexts[:50]


# ============================================================
# Request Source Trace
# ============================================================

def extract_request_calls(
    js,
    js_url,
):
    """
    找出 axios/fetch 呼叫，
    同時保留完整 source context。

    這裡不再假設 URL 一定是 literal。
    """

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
        (?:
            \.
            \s*
            (get|post|put|delete|request)
        )?
        \s*
        \(
        """,
        re.I | re.X,
    )

    for match in pattern.finditer(
        js
    ):

        method = (
            match.group(1)
            or "GET"
        ).upper()

        start = match.start()

        open_pos = js.find(
            "(",
            match.start(),
        )

        argument_text = ""

        if open_pos >= 0:

            balanced = (
                extract_balanced(
                    js,
                    open_pos,
                )
            )

            if balanced:

                argument_text = balanced[
                    1:-1
                ]

        context = js[
            max(
                0,
                start - 2500,
            ):
            min(
                len(js),
                start + 7000,
            )
        ]

        context = clean_js(
            context
        )

        urls = extract_urls_from_js(
            argument_text,
            js_url,
        )

        if not urls:
            urls = extract_urls_from_js(
                context,
                js_url,
            )

        results.append(
            {
                "method": method,
                "position": start,
                "argument": clean_js(
                    argument_text
                )[:8000],
                "urls": urls[:50],
                "context": context[
                    :9000
                ],
                "ocean_related": (
                    any(
                        keyword.lower()
                        in context.lower()
                        for keyword
                        in OCEAN_KEYWORDS
                    )
                ),
            }
        )

    return results


def find_ocean_related_calls(
    js,
    js_url,
):
    calls = extract_request_calls(
        js,
        js_url,
    )

    return [
        call
        for call in calls
        if call.get(
            "ocean_related"
        )
    ]


def extract_method_calls(
    js,
):
    """
    找：

    forumOceanService.xxx(
    forumOceanService["xxx"](
    """

    results = []

    patterns = [
        re.compile(
            r"""
            forumOceanService
            \s*\.\s*
            ([A-Za-z_$][\w$]*)
            \s*\(
            """,
            re.I | re.X,
        ),
        re.compile(
            r"""
            forumOceanService
            \s*\[
            \s*["']([^"']+)["']
            \s*\]
            \s*\(
            """,
            re.I | re.X,
        ),
    ]

    for pattern in patterns:

        for match in pattern.finditer(
            js
        ):

            method_name = (
                match.group(1)
            )

            start = match.start()

            open_pos = js.find(
                "(",
                match.start(),
            )

            args = ""

            if open_pos >= 0:

                balanced = (
                    extract_balanced(
                        js,
                        open_pos,
                    )
                )

                if balanced:
                    args = balanced[
                        1:-1
                    ]

            context = js[
                max(
                    0,
                    start - 3000,
                ):
                min(
                    len(js),
                    start + 7000,
                )
            ]

            results.append(
                {
                    "method_name": method_name,
                    "position": start,
                    "arguments": clean_js(
                        args
                    )[:6000],
                    "context": clean_js(
                        context
                    )[:9000],
                }
            )

    return dedupe(
        results
    )[:200]


def trace_service_chain(
    js,
    js_url,
):
    """
    把 Ocean service 相關證據全部集中起來。
    """

    ocean_contexts = (
        extract_ocean_contexts(
            js
        )
    )

    definitions = (
        extract_service_definitions(
            js
        )
    )

    service_methods = (
        extract_method_calls(
            js
        )
    )

    request_calls = (
        find_ocean_related_calls(
            js,
            js_url,
        )
    )

    candidate_urls = []

    for context in ocean_contexts:

        candidate_urls.extend(
            extract_urls_from_js(
                context[
                    "context"
                ],
                js_url,
            )
        )

    for definition in definitions:

        candidate_urls.extend(
            extract_urls_from_js(
                definition[
                    "context"
                ],
                js_url,
            )
        )

    for method in service_methods:

        candidate_urls.extend(
            extract_urls_from_js(
                method[
                    "context"
                ],
                js_url,
            )
        )

    for call in request_calls:

        candidate_urls.extend(
            call.get(
                "urls",
                []
            )
        )

    return {
        "ocean_contexts": ocean_contexts,
        "definitions": definitions,
        "service_methods": service_methods,
        "request_calls": request_calls,
        "candidate_urls": dedupe(
            candidate_urls
        )[:200],
    }


# ============================================================
# URL / Request 建構
# ============================================================

def replace_stock_tokens(
    text,
    symbol,
):
    if not text:
        return text

    replacements = {
        "STOCK": symbol,
        "stock": symbol,
        "SYMBOL": symbol,
        "symbol": symbol,
        "STOCKID": symbol,
        "stockId": symbol,
        "STOCKNO": symbol,
        "stockNo": symbol,
        "STOCKCODE": symbol,
        "stockCode": symbol,
        "CODE": symbol,
        "code": symbol,
        "TICKER": symbol,
        "ticker": symbol,
    }

    for old, new in replacements.items():

        text = text.replace(
            "${" + old + "}",
            new,
        )

    return text


def substitute_symbol_in_url(
    url,
    symbol,
):
    if not url:
        return url

    parsed = urlparse(
        url
    )

    query = parse_qs(
        parsed.query,
        keep_blank_values=True,
    )

    changed = False

    for key in list(
        query.keys()
    ):

        key_lower = key.lower()

        if any(
            token in key_lower
            for token in (
                "stock",
                "symbol",
                "ticker",
                "code",
                "commkey",
            )
        ):

            query[key] = [
                symbol
            ]

            changed = True

    path = parsed.path

    path = re.sub(
        r"""
        (stock(?:id|no|code)?|
         symbol|
         ticker|
         commkey)
        /
        [A-Za-z0-9_.-]+
        """,
        lambda m: (
            m.group(0).split(
                "/"
            )[0]
            + "/"
            + symbol
        ),
        path,
        flags=re.I | re.X,
    )

    if (
        not changed
        and symbol not in path
    ):
        pass

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            path,
            parsed.params,
            urlencode(
                query,
                doseq=True,
            ),
            parsed.fragment,
        )
    )


def build_source_candidates(
    trace,
    symbol,
):
    """
    只建立「有 source 證據」的候選。

    V3 那種大量自行猜參數的方式
    在這裡不再作為第一層測試。
    """

    candidates = []

    for call in trace.get(
        "request_calls",
        [],
    ):

        urls = call.get(
            "urls",
            []
        )

        for url in urls:

            url = substitute_symbol_in_url(
                url,
                symbol,
            )

            candidates.append(
                {
                    "url": url,
                    "method": call.get(
                        "method",
                        "GET",
                    ),
                    "source": "request_call",
                    "argument": call.get(
                        "argument",
                        "",
                    ),
                    "context": call.get(
                        "context",
                        "",
                    ),
                }
            )

    for method in trace.get(
        "service_methods",
        [],
    ):

        urls = extract_urls_from_js(
            method.get(
                "context",
                ""
            ),
            BASE,
        )

        for url in urls:

            url = substitute_symbol_in_url(
                url,
                symbol,
            )

            candidates.append(
                {
                    "url": url,
                    "method": "GET",
                    "source": (
                        "service_method"
                    ),
                    "service_method": (
                        method.get(
                            "method_name"
                        )
                    ),
                    "arguments": (
                        method.get(
                            "arguments",
                            "",
                        )
                    ),
                    "context": method.get(
                        "context",
                        "",
                    ),
                }
            )

    candidates = dedupe(
        candidates
    )

    return candidates[:200]


# ============================================================
# Response 結構分析
# ============================================================

def contains_force_field(
    key
):
    return bool(
        FORCE_RE.search(
            str(key)
        )
    )


def is_date_key(key):
    normalized = re.sub(
        r"[_\-\s]",
        "",
        str(key),
    ).lower()

    return normalized in {
        "date",
        "tradedate",
        "datetime",
        "tradingdate",
        "datevalue",
        "day",
        "tradingday",
    }


def is_force_key(key):
    return contains_force_field(
        key
    )


def analyze_record(
    record,
    path,
):
    if not isinstance(
        record,
        dict,
    ):
        return None

    dates = []

    force_fields = []

    for key, value in record.items():

        if is_date_key(
            key
        ):

            if isinstance(
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

            elif isinstance(
                value,
                list,
            ):

                for item in value:

                    date = normalize_date(
                        item
                    )

                    if date:
                        dates.append(
                            date
                        )

        if is_force_key(
            key
        ):

            force_fields.append(
                str(key)
            )

    return {
        "path": path,
        "dates": list(
            dict.fromkeys(
                dates
            )
        ),
        "force_fields": list(
            dict.fromkeys(
                force_fields
            )
        ),
        "date_count": len(
            set(dates)
        ),
        "has_force_field": bool(
            force_fields
        ),
    }


def analyze_same_structure_array(
    array,
    path,
):
    if not isinstance(
        array,
        list,
    ):
        return []

    records = []

    for index, item in enumerate(
        array[:1000]
    ):

        if not isinstance(
            item,
            dict,
        ):
            continue

        analysis = analyze_record(
            item,
            f"{path}[{index}]",
        )

        if analysis:
            records.append(
                analysis
            )

    if not records:
        return []

    # 判斷整個 array 是否為同型資料。
    signatures = {}

    for record in records:

        signature = tuple(
            sorted(
                record[
                    "force_fields"
                ]
            )
        )

        signatures.setdefault(
            signature,
            [],
        ).append(
            record
        )

    results = []

    for signature, group in signatures.items():

        if not signature:
            continue

        all_dates = []

        for record in group:

            all_dates.extend(
                record[
                    "dates"
                ]
            )

        unique_dates = list(
            dict.fromkeys(
                all_dates
            )
        )

        results.append(
            {
                "path": path,
                "record_count": len(
                    group
                ),
                "force_signature": (
                    list(signature)
                ),
                "date_count": len(
                    unique_dates
                ),
                "dates": unique_dates[
                    :100
                ],
                "has_20_dates": (
                    len(
                        unique_dates
                    ) >= 20
                ),
                "same_structure": True,
                "true_20d": (
                    len(
                        unique_dates
                    ) >= 20
                    and bool(
                        signature
                    )
                ),
            }
        )

    return results


def analyze_json_structure(
    payload
):
    """
    最重要的 V4 判定：

    不是：
        JSON 某處有日期
        +
        JSON 某處有主力

    而是：

        同一個 array /
        同一資料結構
        裡有 >=20 日期
        且有主力欄位
    """

    all_dates = []
    force_fields = []
    structural_candidates = []

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

            # 單筆 record
            record = analyze_record(
                value,
                path,
            )

            if record:

                if record[
                    "dates"
                ]:
                    all_dates.extend(
                        record[
                            "dates"
                        ]
                    )

                force_fields.extend(
                    record[
                        "force_fields"
                    ]
                )

            for key, child in value.items():

                child_path = (
                    f"{path}/{key}"
                )

                if isinstance(
                    child,
                    list,
                ) and len(
                    child
                ) >= 5:

                    structural_candidates.extend(
                        analyze_same_structure_array(
                            child,
                            child_path,
                        )
                    )

                walk(
                    child,
                    child_path,
                    depth + 1,
                )

        elif isinstance(
            value,
            list,
        ):

            if len(value) >= 5:

                structural_candidates.extend(
                    analyze_same_structure_array(
                        value,
                        path,
                    )
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
                all_dates.append(
                    date
                )

    walk(
        payload
    )

    all_dates = list(
        dict.fromkeys(
            all_dates
        )
    )

    force_fields = list(
        dict.fromkeys(
            force_fields
        )
    )

    structural_candidates = dedupe(
        structural_candidates
    )

    winners = [
        item
        for item
        in structural_candidates
        if item.get(
            "true_20d",
            False,
        )
    ]

    return {
        "global_date_count": len(
            all_dates
        ),
        "global_dates": all_dates[
            :100
        ],
        "global_force_fields": (
            force_fields[:100]
        ),
        "structural_candidates": (
            structural_candidates[:100]
        ),
        "winner_structures": (
            winners[:20]
        ),
        "true_20d": bool(
            winners
        ),
    }


# ============================================================
# Request 執行
# ============================================================

def build_request_variants(
    candidate,
    symbol,
):
    """
    不再像 V3 一樣盲猜七種參數。

    第一優先：
        source code 原始 request

    第二優先：
        URL 本身已含 stock token

    第三優先：
        少量保守變體。
    """

    url = candidate.get(
        "url"
    )

    method = candidate.get(
        "method",
        "GET",
    ).upper()

    variants = []

    variants.append(
        {
            "method": method,
            "url": substitute_symbol_in_url(
                url,
                symbol,
            ),
            "params": None,
            "json": None,
            "source": "source_url",
        }
    )

    parsed = urlparse(
        url
    )

    query = parse_qs(
        parsed.query,
        keep_blank_values=True,
    )

    query_variants = []

    if query:

        for key in list(
            query.keys()
        ):

            key_lower = key.lower()

            if any(
                token in key_lower
                for token in (
                    "stock",
                    "symbol",
                    "ticker",
                    "code",
                    "commkey",
                )
            ):

                q = {
                    k: list(v)
                    for k, v
                    in query.items()
                }

                q[key] = [
                    symbol
                ]

                query_variants.append(
                    q
                )

    for q in query_variants:

        clean_url = urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                urlencode(
                    q,
                    doseq=True,
                ),
                parsed.fragment,
            )
        )

        variants.append(
            {
                "method": method,
                "url": clean_url,
                "params": None,
                "json": None,
                "source": (
                    "source_query"
                ),
            }
        )

    # 如果 source method 的 argument 明確出現
    # stock/symbol 等 key，才建立 body variant。
    argument = (
        candidate.get(
            "argument",
            ""
        )
        or candidate.get(
            "arguments",
            ""
        )
    )

    if argument:

        lowered = argument.lower()

        if any(
            key in lowered
            for key in STOCK_ARGUMENT_KEYS
        ):

            for key in (
                "symbol",
                "stockId",
                "stockNo",
                "stockCode",
                "code",
                "ticker",
            ):

                variants.append(
                    {
                        "method": method,
                        "url": substitute_stock_tokens(
                            url,
                            symbol,
                        ),
                        "params": {
                            key: symbol
                        },
                        "json": None,
                        "source": (
                            "source_argument"
                        ),
                    }
                )

    return dedupe(
        variants
    )[:15]


def test_request(
    session,
    request,
    symbol,
):
    try:

        method = request.get(
            "method",
            "GET",
        ).upper()

        url = request.get(
            "url"
        )

        params = request.get(
            "params"
        )

        json_body = request.get(
            "json"
        )

        if method == "POST":

            response = session.post(
                url,
                params=params,
                json=json_body,
                headers=HEADERS,
                timeout=TIMEOUT,
            )

        elif method == "PUT":

            response = session.put(
                url,
                params=params,
                json=json_body,
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
            "request": request,
            "final_url": response.url,
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

                    item[
                        "confirmed"
                    ] = True

                    return item

            except Exception as exc:

                item[
                    "json_error"
                ] = repr(
                    exc
                )

        return item

    except Exception as exc:

        return {
            "request": request,
            "error": str(
                exc
            )[:1500],
        }


# ============================================================
# 主程式
# ============================================================

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
        "CMoney API 20D 探測器 V4"
    )
    print("=" * 76)

    print(
        "測試策略："
    )

    print(
        "Source Trace → "
        "Ocean Service → "
        "Method → "
        "Request → "
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
            "source_trace": [],
            "candidate_requests": [],
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

            if response.status_code != 200:

                stock[
                    "error"
                ] = (
                    "stock page HTTP "
                    f"{response.status_code}"
                )

                report[
                    "stocks"
                ].append(
                    stock
                )

                continue

            html = response.text

            html_dates = (
                extract_dates_from_text(
                    html
                )
            )

            stock[
                "html"
            ] = {
                "status": response.status_code,
                "bytes": len(
                    response.content
                ),
                "date_count": len(
                    html_dates
                ),
                "dates": html_dates[
                    :100
                ],
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

            all_traces = []

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

                    trace = (
                        trace_service_chain(
                            js,
                            script_url,
                        )
                    )

                    all_traces.append(
                        {
                            "script": script_url,
                            "bytes": len(
                                js_response.content
                            ),
                            "trace": trace,
                        }
                    )

                    evidence = {
                        "script": script_url,
                        "bytes": len(
                            js_response.content
                        ),
                        "ocean_context_count": len(
                            trace[
                                "ocean_contexts"
                            ]
                        ),
                        "definition_count": len(
                            trace[
                                "definitions"
                            ]
                        ),
                        "service_method_count": len(
                            trace[
                                "service_methods"
                            ]
                        ),
                        "request_call_count": len(
                            trace[
                                "request_calls"
                            ]
                        ),
                        "candidate_url_count": len(
                            trace[
                                "candidate_urls"
                            ]
                        ),
                    }

                    stock[
                        "js_evidence"
                    ].append(
                        evidence
                    )

                    print()
                    print(
                        f"★ JS {index}: "
                        f"{script_url}"
                    )

                    print(
                        "   Ocean context："
                        f"{len(trace['ocean_contexts'])}"
                    )

                    print(
                        "   Service definition："
                        f"{len(trace['definitions'])}"
                    )

                    print(
                        "   Service method："
                        f"{len(trace['service_methods'])}"
                    )

                    print(
                        "   Request call："
                        f"{len(trace['request_calls'])}"
                    )

                    print(
                        "   Candidate URL："
                        f"{len(trace['candidate_urls'])}"
                    )

                    # 印出最重要的 method
                    for method in trace[
                        "service_methods"
                    ][:20]:

                        print(
                            "   "
                            f"→ forumOceanService."
                            f"{method['method_name']}("
                            f"{method['arguments'][:180]}"
                            ")"
                        )

                    # 印出 request call
                    for call in trace[
                        "request_calls"
                    ][:20]:

                        print(
                            "   "
                            f"→ REQUEST "
                            f"{call['method']}"
                        )

                        if call[
                            "urls"
                        ]:

                            for url in call[
                                "urls"
                            ][:10]:

                                print(
                                    "      "
                                    f"{url}"
                                )

                except Exception as exc:

                    print(
                        "   JS 探測錯誤："
                        f"{str(exc)[:300]}"
                    )

                time.sleep(
                    0.05
                )

            stock[
                "source_trace"
            ] = all_traces

            # 建立 source-derived request
            candidates = []

            for trace_item in all_traces:

                trace = trace_item[
                    "trace"
                ]

                requests_found = (
                    build_source_candidates(
                        trace,
                        symbol,
                    )
                )

                candidates.extend(
                    requests_found
                )

            candidates = dedupe(
                candidates
            )

            stock[
                "candidate_requests"
            ] = candidates[
                :200
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
                f"{len(all_traces)}"
            )

            print(
                f"Source-derived Request："
                f"{len(candidates)}"
            )

            print(
                "================================"
            )

            tested = 0

            # 優先 source request
            for candidate in candidates[
                :100
            ]:

                variants = (
                    build_request_variants(
                        candidate,
                        symbol,
                    )
                )

                for request in variants:

                    tested += 1

                    print()
                    print(
                        f"[TEST {tested}] "
                        f"{request['method']} "
                        f"{request['url']}"
                    )

                    print(
                        "SOURCE："
                        f"{request['source']}"
                    )

                    result = test_request(
                        session,
                        request,
                        symbol,
                    )

                    stock[
                        "tests"
                    ].append(
                        result
                    )

                    analysis = result.get(
                        "payload_analysis",
                        {},
                    )

                    if analysis.get(
                        "true_20d",
                        False,
                    ):

                        stock[
                            "true_20d"
                        ] = True

                        stock[
                            "winning_request"
                        ] = request

                        stock[
                            "winning_result"
                        ] = result

                        print()
                        print(
                            "★★★★★★★★★★★★★★★★★★★★★★★★"
                        )

                        print(
                            "★ 真正 20D API 已確認"
                        )

                        print(
                            "★ 方法："
                            f"{request['method']}"
                        )

                        print(
                            "★ URL："
                            f"{request['url']}"
                        )

                        print(
                            "★ Source："
                            f"{request['source']}"
                        )

                        print(
                            "★ 日期數："
                            f"{analysis.get('global_date_count', 0)}"
                        )

                        print(
                            "★ Winner 結構："
                            f"{len(analysis.get('winner_structures', []))}"
                        )

                        print(
                            "★★★★★★★★★★★★★★★★★★★★★★★★"
                        )

                        break

                    time.sleep(
                        0.10
                    )

                if stock[
                    "true_20d"
                ]:
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
                "Source Request："
                f"{len(stock['candidate_requests'])}"
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
            ] = repr(
                exc
            )

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
        in report[
            "stocks"
        ]
        if stock.get(
            "true_20d",
            False,
        )
    )

    report[
        "final"
    ] = {
        "tested": len(
            STOCKS
        ),
        "true_20d_count": true_count,
        "true_20d": (
            true_count > 0
        ),
        "rule": (
            "必須在同一 structured "
            "response/data array 中，"
            "存在至少20個不同交易日期，"
            "且同一資料結構存在主力買賣超相關欄位"
        ),
        "method": (
            "V4 Source Trace First"
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
        "CMoney API 20D V4 最終判定"
    )

    print(
        f"真正20D API："
        f"{true_count}/"
        f"{len(STOCKS)}"
    )

    for stock in report[
        "stocks"
    ]:

        print(
            f"  {stock['symbol']} "
            f"{stock['name']}："
            f"{stock.get('true_20d', False)}"
        )

        if stock.get(
            "winning_request"
        ):

            winning = stock[
                "winning_request"
            ]

            print(
                "    URL："
                f"{winning.get('url')}"
            )

            print(
                "    METHOD："
                f"{winning.get('method')}"
            )

    print()

    print(
        f"結果已寫入："
        f"{OUT}"
    )

    print("=" * 76)


if __name__ == "__main__":
    main()