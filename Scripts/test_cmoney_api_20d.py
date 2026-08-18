#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
CMoney API 20D 探測器
TEST-20D-API-V2.0

目的：
1. 探測 CMoney 個股頁面
2. 實際下載 Nuxt JavaScript bundles
3. 從 JS 中尋找 API_FORUM_OCEAN_SERVICE
4. 尋找 API endpoint / path / fetch / axios / request 線索
5. 實際測試候選 endpoint
6. 判斷是否存在真正的「主力 20D 歷史資料」

固定測試：
3081 聯亞
2337 旺宏
2368 金像電
2426 鼎元

重要：
- 不修改正式 fetch_chip.py
- 不修改 index.html
- 不修改正式 chip.json
- 不把「20日集中」當成主力20D
- 不把 JS bundle 本身誤判成 API
- 沒有 >=20 個不同交易日期 + 主力買賣超欄位，不判定成功

輸出：
Data/test_cmoney_api_20d.json
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


# ============================================================
# 基本設定
# ============================================================

VERSION = "TEST-20D-API-V2.0"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"
OUTPUT_FILE = DATA_DIR / "test_cmoney_api_20d.json"

REQUEST_TIMEOUT = 30
REQUEST_DELAY = 0.35

MAX_JS_FILES = 80
MAX_CANDIDATE_URLS = 100

TEST_STOCKS = [
    {
        "symbol": "3081",
        "name": "聯亞",
    },
    {
        "symbol": "2337",
        "name": "旺宏",
    },
    {
        "symbol": "2368",
        "name": "金像電",
    },
    {
        "symbol": "2426",
        "name": "鼎元",
    },
]


# ============================================================
# URL
# ============================================================

CMONEY_URL = (
    "https://www.cmoney.tw/forum/stock/"
    "{symbol}?s=main-force"
)

CMONEY_ORIGIN = "https://www.cmoney.tw"


# ============================================================
# HTTP
# ============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}


# ============================================================
# Log
# ============================================================

def log(message=""):
    print(message, flush=True)


def section(title):
    log("")
    log("=" * 76)
    log(title)
    log("=" * 76)


# ============================================================
# 日期
# ============================================================

DATE_REGEX = re.compile(
    r"\b(?:"
    r"\d{4}[/-]\d{1,2}[/-]\d{1,2}"
    r"|"
    r"\d{4}\.\d{1,2}\.\d{1,2}"
    r")\b"
)


def normalize_date(value):
    if value is None:
        return None

    text = str(value).strip()

    match = DATE_REGEX.fullmatch(text)

    if not match:
        return None

    text = text.replace("-", "/")
    text = text.replace(".", "/")

    return text


def extract_dates(text):
    if not text:
        return []

    found = DATE_REGEX.findall(text)

    result = []

    seen = set()

    for value in found:
        normalized = normalize_date(value)

        if not normalized:
            continue

        if normalized in seen:
            continue

        seen.add(normalized)
        result.append(normalized)

    return result


# ============================================================
# 主力欄位辨識
# ============================================================

FORCE_KEYWORDS = [
    "買賣超",
    "主力買賣超",
    "主力",
    "main_force",
    "mainforce",
    "main-force",
    "mainForce",
    "main_force_buy_sell",
    "mainForceBuySell",
    "buy_sell",
    "buySell",
    "buysell",
    "net_buy",
    "netBuy",
    "netbuy",
]


def contains_force_keyword(text):
    if not text:
        return False

    lower = text.lower()

    for keyword in FORCE_KEYWORDS:
        if keyword.lower() in lower:
            return True

    return False


# ============================================================
# API URL 判斷
# ============================================================

API_HINTS = [
    "api",
    "ajax",
    "service",
    "ocean",
    "json",
    "graphql",
    "main-force",
    "main_force",
    "mainforce",
    "buy",
    "sell",
    "chip",
    "history",
]


def looks_like_api(text):
    if not text:
        return False

    lower = text.lower()

    return any(
        hint in lower
        for hint in API_HINTS
    )


# ============================================================
# URL 清理
# ============================================================

def clean_url(value, base_url):
    if not value:
        return None

    value = value.strip()

    value = value.strip(
        "\"'`"
    )

    value = value.replace(
        "\\/",
        "/"
    )

    value = value.replace(
        "&amp;",
        "&"
    )

    if value.startswith(
        "//"
    ):
        value = "https:" + value

    elif value.startswith(
        "/"
    ):
        value = urljoin(
            base_url,
            value
        )

    elif value.startswith(
        "./"
    ):
        value = urljoin(
            base_url,
            value
        )

    elif not (
        value.startswith("http://")
        or value.startswith("https://")
    ):
        return None

    return value


# ============================================================
# 從文字擷取 URL / path
# ============================================================

def extract_url_candidates(
    text,
    base_url
):
    if not text:
        return []

    found = []

    # 完整 URL
    absolute_urls = re.findall(
        r"https?://[^\s\"'<>\\]+",
        text,
        flags=re.IGNORECASE,
    )

    for value in absolute_urls:
        cleaned = clean_url(
            value,
            base_url
        )

        if cleaned:
            found.append(cleaned)

    # 以 / 開頭的 endpoint
    path_candidates = re.findall(
        r"""
        (?:
            ["'`]
        )
        (
            /[^"'`\\\s]{2,500}
        )
        (?:
            ["'`]
        )
        """,
        text,
        flags=re.VERBOSE,
    )

    for value in path_candidates:

        if not looks_like_api(value):
            continue

        cleaned = clean_url(
            value,
            base_url
        )

        if cleaned:
            found.append(cleaned)

    # 常見 service / API 字串
    quoted_candidates = re.findall(
        r"""
        ["'`]
        (
            [^"'`]{2,500}
            (?:
                api
                |ajax
                |service
                |ocean
                |main-force
                |main_force
            )
            [^"'`]{0,500}
        )
        ["'`]
        """,
        text,
        flags=re.IGNORECASE | re.VERBOSE,
    )

    for value in quoted_candidates:

        if value.startswith(
            "http://"
        ) or value.startswith(
            "https://"
        ) or value.startswith("/"):
            cleaned = clean_url(
                value,
                base_url
            )

            if cleaned:
                found.append(cleaned)

    result = []

    seen = set()

    for value in found:

        if value in seen:
            continue

        seen.add(value)
        result.append(value)

    return result


# ============================================================
# HTML Script
# ============================================================

def extract_script_urls(
    soup,
    page_url
):
    result = []

    seen = set()

    for script in soup.find_all(
        "script"
    ):

        src = script.get(
            "src"
        )

        if not src:
            continue

        full = clean_url(
            src,
            page_url
        )

        if not full:
            continue

        if full in seen:
            continue

        seen.add(full)
        result.append(full)

    return result


# ============================================================
# HTML 內嵌資料分析
# ============================================================

def inspect_html(
    html
):
    dates = extract_dates(
        html
    )

    return {
        "date_count": len(dates),
        "dates": dates[:100],
        "has_force_keyword": contains_force_keyword(
            html
        ),
        "api_forum_ocean_count": html.lower().count(
            "api_forum_ocean_service"
        ),
        "main_force_count": html.lower().count(
            "main-force"
        ),
        "20d_count": html.lower().count(
            "20d"
        ),
        "20day_count": html.count(
            "20日"
        ),
    }


# ============================================================
# JSON 深度分析
# ============================================================

def inspect_json(
    payload
):
    date_values = []
    force_keys = []
    array_paths = []

    date_key_names = {
        "date",
        "日期",
        "day",
        "trade_date",
        "tradedate",
        "tradeDate",
        "datetime",
        "time",
        "dateTime",
    }

    force_key_names = {
        "買賣超",
        "主力買賣超",
        "main_force",
        "mainforce",
        "mainForce",
        "main-force",
        "main_force_buy_sell",
        "mainForceBuySell",
        "buy_sell",
        "buySell",
        "buysell",
        "net_buy",
        "netBuy",
        "netbuy",
    }

    def walk(
        value,
        path=""
    ):

        if isinstance(
            value,
            dict
        ):

            for key, child in value.items():

                key_text = str(
                    key
                )

                normalized = (
                    key_text
                    .replace("_", "")
                    .replace("-", "")
                    .replace(" ", "")
                    .lower()
                )

                if (
                    key_text in date_key_names
                    or normalized in {
                        "date",
                        "tradedate",
                        "datetime",
                    }
                ):

                    if isinstance(
                        child,
                        str
                    ):
                        normalized_date = normalize_date(
                            child
                        )

                        if normalized_date:
                            date_values.append(
                                normalized_date
                            )

                    elif isinstance(
                        child,
                        list
                    ):

                        for item in child[:200]:
                            normalized_date = normalize_date(
                                item
                            )

                            if normalized_date:
                                date_values.append(
                                    normalized_date
                                )

                if (
                    key_text in force_key_names
                    or normalized in {
                        "mainforce",
                        "mainforcebuysell",
                        "buysell",
                        "netbuy",
                    }
                ):
                    force_keys.append(
                        f"{path}/{key_text}"
                    )

                walk(
                    child,
                    f"{path}/{key_text}"
                )

        elif isinstance(
            value,
            list
        ):

            if len(value) >= 10:
                array_paths.append({
                    "path": path,
                    "length": len(value),
                })

            for index, child in enumerate(
                value[:200]
            ):
                walk(
                    child,
                    f"{path}[{index}]"
                )

        elif isinstance(
            value,
            str
        ):

            normalized_date = normalize_date(
                value
            )

            if normalized_date:
                date_values.append(
                    normalized_date
                )

    walk(
        payload
    )

    dates = list(
        dict.fromkeys(
            date_values
        )
    )

    keys = list(
        dict.fromkeys(
            force_keys
        )
    )

    return {
        "date_count": len(dates),
        "dates": dates[:100],
        "force_keys": keys[:100],
        "array_paths": array_paths[:100],
        "has_force_keyword": bool(keys),
    }


# ============================================================
# JS 內容探測
# ============================================================

def inspect_js(
    js,
    js_url
):
    lower = js.lower()

    api_ocean_positions = []

    start = 0

    needle = "api_forum_ocean_service"

    while True:

        position = lower.find(
            needle,
            start
        )

        if position < 0:
            break

        left = max(
            0,
            position - 1200
        )

        right = min(
            len(js),
            position + 2500
        )

        context = js[
            left:right
        ]

        context = re.sub(
            r"\s+",
            " ",
            context
        )

        api_ocean_positions.append(
            context[:4000]
        )

        start = (
            position
            + len(needle)
        )

    urls = extract_url_candidates(
        js,
        js_url
    )

    # --------------------------------------------------------
    # 特別抓 API / service / ocean 周圍字串
    # --------------------------------------------------------

    endpoint_contexts = []

    endpoint_pattern = re.compile(
        r"""
        ["'`]
        (
            [^"'`]{0,1000}
            (?:
                /api/
                |/api
                |api/
                |service/
                |ocean
                |ajax
                |main-force
                |main_force
                |mainforce
            )
            [^"'`]{0,1000}
        )
        ["'`]
        """,
        flags=re.IGNORECASE | re.VERBOSE,
    )

    for match in endpoint_pattern.finditer(
        js
    ):

        value = match.group(
            1
        )

        if len(value) <= 2500:
            endpoint_contexts.append(
                value
            )

    endpoint_contexts = list(
        dict.fromkeys(
            endpoint_contexts
        )
    )

    return {
        "js_url": js_url,
        "size": len(js),
        "api_forum_ocean_count": lower.count(
            "api_forum_ocean_service"
        ),
        "main_force_count": lower.count(
            "main-force"
        ),
        "main_force_underscore_count": lower.count(
            "main_force"
        ),
        "20d_count": lower.count(
            "20d"
        ),
        "20day_count": lower.count(
            "20日"
        ),
        "buy_sell_count": lower.count(
            "buy_sell"
        ),
        "force_keyword": contains_force_keyword(
            js
        ),
        "api_ocean_contexts": api_ocean_positions[:20],
        "urls": urls[:200],
        "endpoint_strings": endpoint_contexts[:200],
    }


# ============================================================
# 建立候選 API
# ============================================================

def build_candidates(
    page_url,
    js_reports
):
    candidates = []

    seen = set()

    def add(
        value,
        source
    ):

        if not value:
            return

        cleaned = clean_url(
            value,
            page_url
        )

        if not cleaned:
            return

        parsed = urlparse(
            cleaned
        )

        # ----------------------------------------------------
        # 只測 HTTP(S)
        # ----------------------------------------------------

        if parsed.scheme not in {
            "http",
            "https",
        }:
            return

        # ----------------------------------------------------
        # JS / CSS / image 不當 endpoint
        # ----------------------------------------------------

        lower_path = parsed.path.lower()

        blocked_extensions = (
            ".js",
            ".css",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".svg",
            ".ico",
            ".woff",
            ".woff2",
            ".ttf",
        )

        if lower_path.endswith(
            blocked_extensions
        ):
            return

        # ----------------------------------------------------
        # 排除明顯頁面
        # ----------------------------------------------------

        if (
            parsed.path == "/forum/"
            and not parsed.query
        ):
            return

        key = cleaned

        if key in seen:
            return

        seen.add(key)

        candidates.append({
            "url": cleaned,
            "source": source,
        })

    # --------------------------------------------------------
    # JS 中所有 URL
    # --------------------------------------------------------

    for report in js_reports:

        for url in report.get(
            "urls",
            []
        ):

            if looks_like_api(
                url
            ):
                add(
                    url,
                    "js_url"
                )

        # ----------------------------------------------------
        # endpoint 字串
        # ----------------------------------------------------

        for value in report.get(
            "endpoint_strings",
            []
        ):

            # 可能是一整段帶參數字串
            direct = clean_url(
                value,
                page_url
            )

            if direct:
                add(
                    direct,
                    "js_endpoint"
                )

            # 再從字串中抽 URL
            for url in extract_url_candidates(
                value,
                page_url
            ):
                add(
                    url,
                    "js_endpoint_extract"
                )

    # --------------------------------------------------------
    # 優先排序：
    # ocean / API / service / main-force
    # --------------------------------------------------------

    def score(item):
        value = item["url"].lower()

        score_value = 0

        if "api_forum_ocean_service" in value:
            score_value += 100

        if "ocean" in value:
            score_value += 60

        if "/api/" in value:
            score_value += 50

        if "api" in value:
            score_value += 30

        if "service" in value:
            score_value += 30

        if "ajax" in value:
            score_value += 25

        if "main-force" in value:
            score_value += 20

        if "main_force" in value:
            score_value += 20

        if "buy" in value:
            score_value += 10

        if "sell" in value:
            score_value += 10

        return score_value

    candidates.sort(
        key=score,
        reverse=True
    )

    return candidates[
        :MAX_CANDIDATE_URLS
    ]


# ============================================================
# 實際測試 Endpoint
# ============================================================

def test_endpoint(
    session,
    url,
    stock_symbol,
    referer
):
    result = {
        "url": url,
        "status_code": None,
        "content_type": "",
        "size": 0,
        "json": False,
        "date_count": 0,
        "dates": [],
        "has_force_key": False,
        "force_keys": [],
        "array_paths": [],
        "confirmed_20d": False,
        "error": None,
    }

    try:

        response = session.get(
            url,
            headers={
                **HEADERS,
                "Referer": referer,
            },
            timeout=REQUEST_TIMEOUT,
        )

        result[
            "status_code"
        ] = response.status_code

        result[
            "content_type"
        ] = response.headers.get(
            "content-type",
            ""
        )

        result[
            "size"
        ] = len(
            response.content
        )

        if response.status_code != 200:
            return result

        text = response.text

        # ----------------------------------------------------
        # JSON
        # ----------------------------------------------------

        try:

            payload = response.json()

            result[
                "json"
            ] = True

            inspected = inspect_json(
                payload
            )

            result[
                "date_count"
            ] = inspected[
                "date_count"
            ]

            result[
                "dates"
            ] = inspected[
                "dates"
            ]

            result[
                "has_force_key"
            ] = inspected[
                "has_force_keyword"
            ]

            result[
                "force_keys"
            ] = inspected[
                "force_keys"
            ]

            result[
                "array_paths"
            ] = inspected[
                "array_paths"
            ]

        except Exception:

            # ------------------------------------------------
            # 非 JSON
            # ------------------------------------------------

            dates = extract_dates(
                text
            )

            result[
                "date_count"
            ] = len(dates)

            result[
                "dates"
            ] = dates[:100]

            result[
                "has_force_key"
            ] = contains_force_keyword(
                text
            )

        # ----------------------------------------------------
        # 真正 20D 判定
        # ----------------------------------------------------

        result[
            "confirmed_20d"
        ] = (
            result["date_count"] >= 20
            and result["has_force_key"]
        )

        return result

    except Exception as exc:

        result[
            "error"
        ] = str(exc)

        return result


# ============================================================
# 單一股票
# ============================================================

def test_stock(
    session,
    stock
):
    symbol = stock[
        "symbol"
    ]

    name = stock[
        "name"
    ]

    page_url = CMONEY_URL.format(
        symbol=symbol
    )

    section(
        f"{symbol} {name}：CMoney 20D V2 探測"
    )

    result = {
        "symbol": symbol,
        "name": name,
        "page_url": page_url,
        "page_status": None,
        "html_size": 0,
        "html_inspection": {},
        "script_count": 0,
        "js_downloaded": 0,
        "js_reports": [],
        "candidate_urls": [],
        "api_tests": [],
        "confirmed_20d_api": False,
    }

    # --------------------------------------------------------
    # Page
    # --------------------------------------------------------

    response = session.get(
        page_url,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
    )

    result[
        "page_status"
    ] = response.status_code

    result[
        "html_size"
    ] = len(
        response.content
    )

    log(
        f"✓ HTTP：{response.status_code}"
    )

    log(
        f"✓ URL：{page_url}"
    )

    log(
        f"✓ HTML："
        f"{len(response.content):,} bytes"
    )

    if response.status_code != 200:
        result[
            "error"
        ] = f"HTTP {response.status_code}"

        return result

    html = response.text

    html_info = inspect_html(
        html
    )

    result[
        "html_inspection"
    ] = html_info

    log(
        f"HTML 日期數量："
        f"{html_info['date_count']}"
    )

    log(
        f"20日文字："
        f"{html_info['20day_count']}"
    )

    log(
        f"20D文字："
        f"{html_info['20d_count']}"
    )

    log(
        f"API_FORUM_OCEAN_SERVICE："
        f"{html_info['api_forum_ocean_count']}"
    )

    # --------------------------------------------------------
    # Script URLs
    # --------------------------------------------------------

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    script_urls = extract_script_urls(
        soup,
        page_url
    )

    result[
        "script_count"
    ] = len(script_urls)

    log(
        f"發現 JavaScript："
        f"{len(script_urls)} 個"
    )

    # --------------------------------------------------------
    # 優先分析 Nuxt JS
    # --------------------------------------------------------

    prioritized = []

    for js_url in script_urls:

        lower = js_url.lower()

        score = 0

        if "_nuxt" in lower:
            score += 100

        if ".modern.js" in lower:
            score += 50

        if "app" in lower:
            score += 20

        prioritized.append(
            (
                score,
                js_url,
            )
        )

    prioritized.sort(
        reverse=True
    )

    prioritized = prioritized[
        :MAX_JS_FILES
    ]

    # --------------------------------------------------------
    # Download / inspect JS
    # --------------------------------------------------------

    for index, (_, js_url) in enumerate(
        prioritized,
        start=1
    ):

        log(
            f"[JS {index}/{len(prioritized)}] "
            f"{js_url}"
        )

        try:

            js_response = session.get(
                js_url,
                headers={
                    **HEADERS,
                    "Referer": page_url,
                },
                timeout=REQUEST_TIMEOUT,
            )

            if js_response.status_code != 200:

                log(
                    f"   HTTP："
                    f"{js_response.status_code}"
                )

                continue

            content_type = js_response.headers.get(
                "content-type",
                ""
            ).lower()

            js_text = js_response.text

            # ------------------------------------------------
            # 只分析 JS
            # ------------------------------------------------

            if (
                "javascript" not in content_type
                and "text/" not in content_type
                and not js_url.lower().endswith(".js")
            ):
                continue

            report = inspect_js(
                js_text,
                js_url
            )

            result[
                "js_reports"
            ].append(
                report
            )

            result[
                "js_downloaded"
            ] += 1

            interesting = (
                report[
                    "api_forum_ocean_count"
                ] > 0
                or report[
                    "force_keyword"
                ]
                or report[
                    "20d_count"
                ] > 0
            )

            if interesting:

                log(
                    "   ★ 發現重要線索："
                    f"API_OCEAN="
                    f"{report['api_forum_ocean_count']} "
                    f"20D="
                    f"{report['20d_count']} "
                    f"主力="
                    f"{report['force_keyword']} "
                    f"URL="
                    f"{len(report['urls'])} "
                    f"Endpoint="
                    f"{len(report['endpoint_strings'])}"
                )

                for context in report[
                    "api_ocean_contexts"
                ][:3]:

                    log(
                        "   API_FORUM_OCEAN_SERVICE "
                        "上下文："
                    )

                    log(
                        "   "
                        + context[:1000]
                    )

            time.sleep(
                REQUEST_DELAY
            )

        except Exception as exc:

            log(
                f"   ⚠️ JS 下載失敗："
                f"{exc}"
            )

    # --------------------------------------------------------
    # Candidate endpoints
    # --------------------------------------------------------

    candidates = build_candidates(
        page_url,
        result["js_reports"]
    )

    result[
        "candidate_urls"
    ] = candidates

    section(
        f"{symbol}：API Endpoint 實際測試"
    )

    log(
        f"候選 endpoint："
        f"{len(candidates)}"
    )

    for item in candidates:

        log(
            f"   {item['url']}"
        )

    # --------------------------------------------------------
    # Test endpoints
    # --------------------------------------------------------

    for index, item in enumerate(
        candidates,
        start=1
    ):

        api_url = item[
            "url"
        ]

        log(
            ""
        )

        log(
            f"[API {index}/{len(candidates)}]"
        )

        log(
            f"測試：{api_url}"
        )

        api_result = test_endpoint(
            session,
            api_url,
            symbol,
            page_url,
        )

        api_result[
            "source"
        ] = item[
            "source"
        ]

        result[
            "api_tests"
        ].append(
            api_result
        )

        if api_result[
            "status_code"
        ] == 200:

            log(
                f"   HTTP：200"
            )

            log(
                f"   Content-Type："
                f"{api_result['content_type']}"
            )

            log(
                f"   大小："
                f"{api_result['size']:,} bytes"
            )

            log(
                f"   JSON："
                f"{api_result['json']}"
            )

            log(
                f"   日期："
                f"{api_result['date_count']}"
            )

            log(
                f"   主力欄位："
                f"{api_result['has_force_key']}"
            )

            if api_result[
                "confirmed_20d"
            ]:

                result[
                    "confirmed_20d_api"
                ] = True

                log(
                    ""
                )

                log(
                    "   ★★★★★"
                )

                log(
                    "   ★ 找到真正疑似主力 20D API ★"
                )

                log(
                    "   ★★★★★"
                )

                log(
                    f"   日期數量："
                    f"{api_result['date_count']}"
                )

                log(
                    f"   主力欄位："
                    f"{api_result['force_keys'][:20]}"
                )

        elif api_result[
            "error"
        ]:

            log(
                f"   ⚠️ "
                f"{api_result['error']}"
            )

        else:

            log(
                f"   HTTP："
                f"{api_result['status_code']}"
            )

        time.sleep(
            REQUEST_DELAY
        )

    return result


# ============================================================
# 最終摘要
# ============================================================

def final_summary(
    results
):

    section(
        "CMoney API 20D V2 最終判定"
    )

    confirmed_count = 0

    for result in results:

        symbol = result[
            "symbol"
        ]

        name = result[
            "name"
        ]

        html_dates = result[
            "html_inspection"
        ].get(
            "date_count",
            0
        )

        js_count = result[
            "js_downloaded"
        ]

        candidate_count = len(
            result[
                "candidate_urls"
            ]
        )

        api_tests = result[
            "api_tests"
        ]

        confirmed = result[
            "confirmed_20d_api"
        ]

        if confirmed:
            confirmed_count += 1

        log(
            f"{symbol} {name}"
        )

        log(
            f"  HTML日期：{html_dates}"
        )

        log(
            f"  JS下載：{js_count}"
        )

        log(
            f"  Candidate API："
            f"{candidate_count}"
        )

        log(
            f"  API實測："
            f"{len(api_tests)}"
        )

        log(
            f"  真正20D："
            f"{confirmed}"
        )

        # 找出最佳候選
        confirmed_items = [
            item
            for item in api_tests
            if item.get(
                "confirmed_20d"
            )
        ]

        if confirmed_items:

            for item in confirmed_items:

                log(
                    "  ★ endpoint："
                    + item[
                        "url"
                    ]
                )

    log("")
    log(
        f"真正20D API："
        f"{confirmed_count}/"
        f"{len(results)}"
    )

    log("")
    log(
        "V2 判定規則："
    )

    log(
        "1. HTML 出現「20日集中」≠ 主力20D"
    )

    log(
        "2. JS 出現 API_FORUM_OCEAN_SERVICE "
        "≠ API 已確認"
    )

    log(
        "3. 必須實際呼叫 endpoint"
    )

    log(
        "4. 必須取得至少20個不同日期"
    )

    log(
        "5. 同一資料結構中必須存在主力買賣超相關欄位"
    )

    log(
        "6. 未滿足上述條件，一律 False"
    )

    if confirmed_count == 0:

        log("")
        log(
            "⚠️ V2 仍未確認真正主力20D API"
        )

    elif confirmed_count < len(results):

        log("")
        log(
            "⚠️ 部分股票確認到主力20D API"
        )

    else:

        log("")
        log(
            "✓ 四檔全部確認主力20D API"
        )


# ============================================================
# 儲存
# ============================================================

def save_results(
    results,
    elapsed
):

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    output = {
        "schema_version": VERSION,
        "generated_at": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "purpose": (
            "CMoney 主力20D API 深度探測"
        ),
        "test_stocks": TEST_STOCKS,
        "results": results,
        "elapsed_seconds": round(
            elapsed,
            2
        ),
    }

    temp_file = OUTPUT_FILE.with_suffix(
        ".json.tmp"
    )

    with temp_file.open(
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            output,
            file,
            ensure_ascii=False,
            indent=2
        )

    # 寫入後驗證
    with temp_file.open(
        "r",
        encoding="utf-8"
    ) as file:

        verify = json.load(
            file
        )

    if not isinstance(
        verify,
        dict
    ):
        raise RuntimeError(
            "測試結果 JSON 驗證失敗"
        )

    temp_file.replace(
        OUTPUT_FILE
    )

    log("")
    log(
        f"✓ 結果已寫入："
        f"{OUTPUT_FILE}"
    )


# ============================================================
# Main
# ============================================================

def main():

    start = time.time()

    log("")
    log("=" * 76)
    log(
        "台股 AI 選股系統 "
        f"CMoney API 20D 探測器 {VERSION}"
    )
    log("=" * 76)

    log(
        "開始時間："
        + datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    log("")
    log(
        "固定測試股票："
    )

    for stock in TEST_STOCKS:

        log(
            f"  {stock['symbol']} "
            f"{stock['name']}"
        )

    session = requests.Session()

    results = []

    try:

        for stock in TEST_STOCKS:

            try:

                result = test_stock(
                    session,
                    stock
                )

            except Exception as exc:

                result = {
                    "symbol": stock[
                        "symbol"
                    ],
                    "name": stock[
                        "name"
                    ],
                    "error": str(exc),
                    "confirmed_20d_api": False,
                }

                log(
                    f"❌ {stock['symbol']} "
                    f"測試失敗：{exc}"
                )

            results.append(
                result
            )

            time.sleep(
                REQUEST_DELAY
            )

        elapsed = (
            time.time()
            - start
        )

        final_summary(
            results
        )

        save_results(
            results,
            elapsed
        )

        log("")
        log("=" * 76)
        log(
            "✓ CMoney API 20D V2 探測完成"
        )
        log("=" * 76)

        log(
            f"耗時："
            f"{elapsed:.1f} 秒"
        )

        log(
            f"結果："
            f"{OUTPUT_FILE}"
        )

        return 0

    except Exception as exc:

        log("")
        log("=" * 76)
        log(
            "❌ CMoney API 20D V2 執行失敗"
        )
        log("=" * 76)

        log(
            f"原因：{exc}"
        )

        return 1


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":
    sys.exit(
        main()
    )