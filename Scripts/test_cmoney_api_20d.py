#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
test_cmoney_api_20d.py
TEST-20D-API-V1.0

============================================================
目的
============================================================

本程式不是正式 fetch_chip.py。

本程式只用於：

1. 測試 CMoney 是否存在主力 20D 歷史資料
2. 探測 HTML 內嵌的 API / AJAX / JSON
3. 找出可能提供主力歷史資料的 endpoint
4. 判斷 endpoint 是否真的回傳：
   日期 + 主力買賣超

============================================================
固定測試股票
============================================================

3081  聯亞
2337  旺宏
2368  金像電
2426  鼎元

============================================================
重要限制
============================================================

絕不：

- 修改 fetch_chip.py
- 修改 index.html
- 修改 chip.json
- 把「20日集中」當成「主力20日買賣超」
- 把任何不確定的數字當成 20D
- 修改正式資料

============================================================
輸出
============================================================

Data/test_cmoney_api_20d.json

============================================================
判定

真正成功必須找到：

日期序列 >= 20 筆

且資料結構可以確認：

date
main_force / buy_sell / 買賣超

否則只列為「發現線索」，不視為成功。
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


# ============================================================
# 基本設定
# ============================================================

VERSION = "TEST-20D-API-V1.0"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

OUTPUT_FILE = DATA_DIR / "test_cmoney_api_20d.json"

REQUEST_TIMEOUT = 30

REQUEST_DELAY = 0.5

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
# CMoney URL
# ============================================================

CMONEY_URL = (
    "https://www.cmoney.tw/forum/stock/"
    "{symbol}?s=main-force"
)

CMONEY_MOBILE_URL = (
    "https://mobile.cmoney.tw/forum/stock/"
    "{symbol}?s=main-force"
)


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
        "application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8"
    ),
    "Accept-Language": (
        "zh-TW,zh;q=0.9,en;q=0.8"
    ),
    "Connection": "keep-alive",
}


# ============================================================
# Log
# ============================================================

def log(message=""):
    print(message, flush=True)


def section(title):
    log("")
    log("=" * 72)
    log(title)
    log("=" * 72)


# ============================================================
# URL / API 判斷
# ============================================================

def looks_like_api_url(url):
    if not url:
        return False

    lower = url.lower()

    keywords = [
        "api",
        "ajax",
        "json",
        "service",
        "ocean",
        "forum",
        "stock",
        "main-force",
        "main_force",
        "buy",
        "sell",
        "chip",
        "history",
    ]

    return any(
        keyword in lower
        for keyword in keywords
    )


# ============================================================
# 日期解析
# ============================================================

DATE_PATTERNS = [
    r"\d{4}/\d{1,2}/\d{1,2}",
    r"\d{4}-\d{1,2}-\d{1,2}",
]


def normalize_date(value):

    if value is None:
        return None

    text = str(value).strip()

    for pattern in DATE_PATTERNS:

        match = re.fullmatch(
            pattern,
            text
        )

        if match:
            return text.replace(
                "-",
                "/"
            )

    return None


# ============================================================
# HTML Table 日期統計
# ============================================================

def inspect_tables(soup):

    tables = soup.find_all("table")

    table_info = []

    for table_index, table in enumerate(
        tables
    ):

        rows = table.find_all("tr")

        headers = []

        if rows:

            first_cells = rows[0].find_all(
                ["th", "td"]
            )

            headers = [
                cell.get_text(
                    " ",
                    strip=True
                )
                for cell in first_cells
            ]

        dates = []

        for tr in rows:

            cells = tr.find_all(
                ["th", "td"]
            )

            values = [
                cell.get_text(
                    " ",
                    strip=True
                )
                for cell in cells
            ]

            for value in values:

                date = normalize_date(
                    value
                )

                if date:
                    dates.append(date)

        unique_dates = list(
            dict.fromkeys(dates)
        )

        table_info.append({
            "table_index": table_index,
            "headers": headers,
            "row_count": len(rows),
            "date_count": len(unique_dates),
            "dates": unique_dates[:30],
        })

    return table_info


# ============================================================
# HTML 內所有 URL
# ============================================================

def extract_urls(
    soup,
    base_url
):

    found = []

    # --------------------------------------------------------
    # href
    # --------------------------------------------------------

    for tag in soup.find_all(
        href=True
    ):

        href = tag.get(
            "href"
        )

        if not href:
            continue

        full = urljoin(
            base_url,
            href
        )

        found.append(full)

    # --------------------------------------------------------
    # src
    # --------------------------------------------------------

    for tag in soup.find_all(
        src=True
    ):

        src = tag.get(
            "src"
        )

        if not src:
            continue

        full = urljoin(
            base_url,
            src
        )

        found.append(full)

    # --------------------------------------------------------
    # HTML 原始文字 URL
    # --------------------------------------------------------

    raw = str(soup)

    regex_urls = re.findall(
        r'https?://[^"\']+',
        raw
    )

    found.extend(
        regex_urls
    )

    # --------------------------------------------------------
    # 去重
    # --------------------------------------------------------

    result = []

    seen = set()

    for url in found:

        url = (
            url
            .replace(
                "\\/",
                "/"
            )
            .replace(
                "&amp;",
                "&"
            )
        )

        if url in seen:
            continue

        seen.add(url)

        result.append(url)

    return result


# ============================================================
# API 關鍵字上下文
# ============================================================

def extract_keyword_context(
    html,
    keyword,
    limit=10
):

    contexts = []

    lower = html.lower()

    target = keyword.lower()

    start = 0

    while len(contexts) < limit:

        index = lower.find(
            target,
            start
        )

        if index < 0:
            break

        left = max(
            0,
            index - 300
        )

        right = min(
            len(html),
            index + len(keyword) + 500
        )

        snippet = html[
            left:right
        ]

        snippet = re.sub(
            r"\s+",
            " ",
            snippet
        )

        contexts.append(
            snippet[:800]
        )

        start = (
            index
            + len(keyword)
        )

    return contexts


# ============================================================
# Script / API 線索
# ============================================================

def inspect_scripts(
    soup,
    base_url
):

    scripts = soup.find_all(
        "script"
    )

    results = []

    for index, script in enumerate(
        scripts,
        start=1
    ):

        src = script.get(
            "src"
        )

        content = script.string or script.get_text(
            " ",
            strip=False
        )

        if src:

            full_src = urljoin(
                base_url,
                src
            )

            if looks_like_api_url(
                full_src
            ):

                results.append({
                    "type": "script_src",
                    "script_index": index,
                    "url": full_src,
                })

        if content:

            lower = content.lower()

            keywords = [
                "api",
                "ajax",
                "fetch(",
                "$.ajax",
                "$.get",
                "$.post",
                "axios",
                "main-force",
                "main_force",
                "api_forum_ocean_service",
                "20d",
                "20日",
                "買賣超",
            ]

            matched = [
                keyword
                for keyword in keywords
                if keyword.lower() in lower
            ]

            if matched:

                # --------------------------------------------
                # 擷取 URL 字串
                # --------------------------------------------

                urls = re.findall(
                    r'https?://[^"\']+',
                    content
                )

                # --------------------------------------------
                # 擷取可能 endpoint
                # --------------------------------------------

                paths = re.findall(
                    r'["\']([^"\']*(?:api|ajax|service|ocean)[^"\']*)["\']',
                    content,
                    flags=re.IGNORECASE
                )

                results.append({
                    "type": "inline_script",
                    "script_index": index,
                    "matched_keywords": matched,
                    "urls": urls[:30],
                    "paths": paths[:50],
                    "content_length": len(content),
                })

    return results


# ============================================================
# 解析 JSON
# ============================================================

def inspect_json_payload(
    payload
):

    result = {
        "is_json": False,
        "date_keys": [],
        "force_keys": [],
        "sample_dates": [],
        "possible_history_arrays": [],
    }

    if not isinstance(
        payload,
        (dict, list)
    ):
        return result

    result["is_json"] = True

    date_key_names = {
        "date",
        "日期",
        "day",
        "trade_date",
        "tradedate",
        "datetime",
        "time",
    }

    force_key_names = {
        "買賣超",
        "main_force",
        "mainforce",
        "main_force_buy_sell",
        "buy_sell",
        "buysell",
        "net_buy",
        "netbuy",
        "mainforcebuysell",
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
                    .replace(
                        "_",
                        ""
                    )
                    .replace(
                        "-",
                        ""
                    )
                    .replace(
                        " ",
                        ""
                    )
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

                    result[
                        "date_keys"
                    ].append(
                        f"{path}/{key_text}"
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

                    result[
                        "force_keys"
                    ].append(
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

            if len(value) >= 5:

                result[
                    "possible_history_arrays"
                ].append({
                    "path": path,
                    "length": len(value),
                })

            for index, child in enumerate(
                value[:100]
            ):

                walk(
                    child,
                    f"{path}[{index}]"
                )

        elif isinstance(
            value,
            str
        ):

            date = normalize_date(
                value
            )

            if date:

                result[
                    "sample_dates"
                ].append(
                    date
                )

    walk(payload)

    # 去重
    result[
        "date_keys"
    ] = list(
        dict.fromkeys(
            result["date_keys"]
        )
    )

    result[
        "force_keys"
    ] = list(
        dict.fromkeys(
            result["force_keys"]
        )
    )

    result[
        "sample_dates"
    ] = list(
        dict.fromkeys(
            result["sample_dates"]
        )
    )

    return result


# ============================================================
# 嘗試 GET API
#
# 注意：
# 只測試明確出現在 HTML / Script 中的 URL。
# 不自行猜測不存在的 endpoint。
# ============================================================

def test_candidate_url(
    session,
    url,
    stock_symbol
):

    result = {
        "url": url,
        "status_code": None,
        "content_type": None,
        "size": 0,
        "json": False,
        "date_count": 0,
        "has_main_force_key": False,
        "has_20d_signal": False,
        "error": None,
    }

    try:

        response = session.get(
            url,
            headers={
                **HEADERS,
                "Referer": CMONEY_URL.format(
                    symbol=stock_symbol
                ),
            },
            timeout=REQUEST_TIMEOUT
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

            inspected = inspect_json_payload(
                payload
            )

            result[
                "date_count"
            ] = len(
                inspected[
                    "sample_dates"
                ]
            )

            result[
                "has_main_force_key"
            ] = bool(
                inspected[
                    "force_keys"
                ]
            )

            result[
                "has_20d_signal"
            ] = (
                result["date_count"] >= 20
                and result["has_main_force_key"]
            )

        except Exception:

            # ------------------------------------------------
            # 非 JSON
            # ------------------------------------------------

            dates = re.findall(
                r"\d{4}[/-]\d{1,2}[/-]\d{1,2}",
                text
            )

            unique_dates = list(
                dict.fromkeys(
                    dates
                )
            )

            result[
                "date_count"
            ] = len(
                unique_dates
            )

            lower = text.lower()

            result[
                "has_main_force_key"
            ] = (
                "main_force" in lower
                or "mainforce" in lower
                or "買賣超" in text
            )

            result[
                "has_20d_signal"
            ] = (
                len(unique_dates) >= 20
                and result[
                    "has_main_force_key"
                ]
            )

    except Exception as exc:

        result[
            "error"
        ] = str(exc)

    return result


# ============================================================
# 單一股票測試
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

    section(
        f"測試 {symbol} {name}"
    )

    url = CMONEY_URL.format(
        symbol=symbol
    )

    result = {
        "symbol": symbol,
        "name": name,
        "page_url": url,
        "http_status": None,
        "html_size": 0,
        "html_date_count": 0,
        "table_count": 0,
        "tables": [],
        "candidate_urls": [],
        "script_api_clues": [],
        "api_tests": [],
        "confirmed_20d_api": False,
    }

    # --------------------------------------------------------
    # 取得主頁
    # --------------------------------------------------------

    response = session.get(
        url,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT
    )

    result[
        "http_status"
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
        f"✓ URL：{url}"
    )

    log(
        f"✓ HTML："
        f"{len(response.content):,} bytes"
    )

    if response.status_code != 200:

        result[
            "error"
        ] = (
            f"HTTP {response.status_code}"
        )

        return result

    html = response.text

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    # --------------------------------------------------------
    # HTML 日期
    # --------------------------------------------------------

    dates = re.findall(
        r"\d{4}[/-]\d{1,2}[/-]\d{1,2}",
        html
    )

    unique_dates = list(
        dict.fromkeys(
            dates
        )
    )

    result[
        "html_date_count"
    ] = len(
        unique_dates
    )

    log(
        f"HTML 日期數量："
        f"{len(unique_dates)}"
    )

    log(
        f"是否 >= 20 日："
        f"{len(unique_dates) >= 20}"
    )

    # --------------------------------------------------------
    # Tables
    # --------------------------------------------------------

    tables = inspect_tables(
        soup
    )

    result[
        "table_count"
    ] = len(tables)

    result[
        "tables"
    ] = tables

    log(
        f"HTML Table 數量："
        f"{len(tables)}"
    )

    for table in tables:

        headers = table[
            "headers"
        ]

        if any(
            "20" in header
            for header in headers
        ):

            log(
                "✓ 發現包含「20」的 Table 欄位："
            )

            for column, header in enumerate(
                headers
            ):

                if "20" in header:

                    log(
                        f"   table="
                        f"{table['table_index']} "
                        f"column={column} "
                        f"header={header}"
                    )

    # --------------------------------------------------------
    # 關鍵字
    # --------------------------------------------------------

    keywords = [
        "20日",
        "20D",
        "20d",
        "20",
        "買賣超",
        "主力",
        "main-force",
        "main_force",
        "api",
        "ajax",
        "json",
        "API_FORUM_OCEAN_SERVICE",
    ]

    keyword_counts = {}

    lower_html = html.lower()

    for keyword in keywords:

        if keyword.isascii():

            count = lower_html.count(
                keyword.lower()
            )

        else:

            count = html.count(
                keyword
            )

        keyword_counts[
            keyword
        ] = count

    log(
        "關鍵字命中："
    )

    for keyword, count in keyword_counts.items():

        if count:

            log(
                f"   {keyword}: {count}"
            )

    # --------------------------------------------------------
    # Script
    # --------------------------------------------------------

    script_clues = inspect_scripts(
        soup,
        url
    )

    result[
        "script_api_clues"
    ] = script_clues

    log(
        f"Script/API 線索："
        f"{len(script_clues)}"
    )

    # --------------------------------------------------------
    # URL
    # --------------------------------------------------------

    urls = extract_urls(
        soup,
        url
    )

    candidates = []

    seen = set()

    for candidate in urls:

        if not looks_like_api_url(
            candidate
        ):
            continue

        if candidate in seen:
            continue

        seen.add(candidate)

        candidates.append(
            candidate
        )

    # 從 inline script 再補 paths
    for clue in script_clues:

        for path in clue.get(
            "paths",
            []
        ):

            if (
                "api" not in path.lower()
                and "ajax" not in path.lower()
                and "service" not in path.lower()
                and "ocean" not in path.lower()
            ):
                continue

            if path.startswith(
                "http://"
            ) or path.startswith(
                "https://"
            ):

                candidate = path

            elif path.startswith(
                "/"
            ):

                candidate = urljoin(
                    url,
                    path
                )

            else:

                continue

            if candidate not in seen:

                seen.add(candidate)

                candidates.append(
                    candidate
                )

    # --------------------------------------------------------
    # 限制候選數
    #
    # 防止 CMoney 頁面中的大量無關 API 被全部呼叫。
    # --------------------------------------------------------

    candidates = candidates[:40]

    result[
        "candidate_urls"
    ] = candidates

    log(
        f"候選 API URL："
        f"{len(candidates)}"
    )

    for candidate in candidates:

        log(
            f"   {candidate[:300]}"
        )

    # --------------------------------------------------------
    # 實際測試候選 endpoint
    # --------------------------------------------------------

    section(
        f"{symbol} API 實際測試"
    )

    for candidate in candidates:

        log(
            f"測試："
            f"{candidate[:300]}"
        )

        api_result = test_candidate_url(
            session,
            candidate,
            symbol
        )

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
                f"   日期數量："
                f"{api_result['date_count']}"
            )

            log(
                f"   主力欄位："
                f"{api_result['has_main_force_key']}"
            )

            log(
                f"   是否疑似真正20D："
                f"{api_result['has_20d_signal']}"
            )

            if api_result[
                "has_20d_signal"
            ]:

                result[
                    "confirmed_20d_api"
                ] = True

                log(
                    "   ★ 發現疑似真正 20D API"
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
# 最終判定
# ============================================================

def final_summary(
    results
):

    section(
        "CMoney API 20D 探測結果"
    )

    confirmed = 0

    for result in results:

        symbol = result[
            "symbol"
        ]

        name = result[
            "name"
        ]

        html_dates = result[
            "html_date_count"
        ]

        api_count = len(
            result[
                "api_tests"
            ]
        )

        is_confirmed = result[
            "confirmed_20d_api"
        ]

        if is_confirmed:
            confirmed += 1

        log(
            f"{symbol} {name}: "
            f"HTML日期={html_dates}, "
            f"候選API={api_count}, "
            f"20D API確認={is_confirmed}"
        )

    log("")
    log(
        f"真正疑似20D API："
        f"{confirmed}/{len(results)}"
    )

    log("")
    log(
        "判定規則："
    )

    log(
        "1. HTML >= 20 筆日期，"
        "只能代表頁面可能存在歷史資料。"
    )

    log(
        "2. 出現「20日集中」不代表存在主力20D買賣超。"
    )

    log(
        "3. 必須找到 API / JSON / AJAX 實際資料。"
    )

    log(
        "4. API 必須同時具備足夠日期與主力買賣超欄位。"
    )

    log(
        "5. 沒有確認前，不修改正式 fetch_chip.py。"
    )

    if confirmed == len(results):

        log("")
        log(
            "✓ 四檔皆找到疑似20D API"
        )

    elif confirmed > 0:

        log("")
        log(
            "⚠️ 部分股票找到疑似20D API"
        )

    else:

        log("")
        log(
            "⚠️ 尚未確認真正主力20D API"
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
            "CMoney 主力20D API探測"
        ),
        "test_stocks": TEST_STOCKS,
        "results": results,
        "elapsed_seconds": round(
            elapsed,
            2
        ),
    }

    temp = OUTPUT_FILE.with_suffix(
        ".json.tmp"
    )

    with temp.open(
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2
        )

    # 寫入後驗證
    with temp.open(
        "r",
        encoding="utf-8"
    ) as f:

        verify = json.load(f)

    if not isinstance(
        verify,
        dict
    ):

        raise RuntimeError(
            "測試結果 JSON 驗證失敗"
        )

    temp.replace(
        OUTPUT_FILE
    )

    log("")
    log(
        f"✓ 測試結果已寫入："
        f"{OUTPUT_FILE}"
    )


# ============================================================
# Main
# ============================================================

def main():

    start = time.time()

    log("")
    log("=" * 72)
    log(
        "台股 AI 選股系統 "
        f"CMoney API 20D 探測器 {VERSION}"
    )
    log("=" * 72)

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
        log("=" * 72)
        log(
            "✓ CMoney API 20D 探測完成"
        )
        log("=" * 72)

        log(
            f"耗時："
            f"{elapsed:.1f} 秒"
        )

        log(
            f"結果："
            f"{OUTPUT_FILE}"
        )

        # ----------------------------------------------------
        # 探測器本身只要成功執行即可 exit 0。
        #
        # 即使找不到 20D API，也不能讓 GitHub Action
        # 誤判成程式錯誤。
        # ----------------------------------------------------

        return 0

    except Exception as exc:

        log("")
        log("=" * 72)
        log(
            "❌ CMoney API 20D 探測器執行失敗"
        )
        log("=" * 72)

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
