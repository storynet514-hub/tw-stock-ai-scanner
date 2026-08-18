#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
test_cmoney_20d.py

用途：
    探測 CMoney 主力進出頁面是否存在：
    1. 超過 10 日的歷史資料
    2. 20 日主力買賣超資料
    3. 20 日統計相關 JSON / API / JavaScript 線索

注意：
    本程式是獨立測試工具。

    不修改：
    - index.html
    - fetch_chip.py
    - chip.json

只測試 4 檔：
    3081
    2337
    2368
    2426

輸出：
    - console
    - Data/test_cmoney_20d.json
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup


# ============================================================
# 基本設定
# ============================================================

VERSION = "TEST-20D-V1.0"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

OUTPUT_FILE = DATA_DIR / "test_cmoney_20d.json"

TEST_SYMBOLS = [
    "3081",
    "2337",
    "2368",
    "2426",
]

REQUEST_TIMEOUT = 30
REQUEST_DELAY = 0.5

CMONEY_URLS = [
    "https://www.cmoney.tw/forum/stock/{symbol}?s=main-force",
    "https://mobile.cmoney.tw/forum/stock/{symbol}?s=main-force",
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
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
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
# 取得頁面
# ============================================================

def request_page(session, symbol):

    errors = []

    for template in CMONEY_URLS:

        url = template.format(
            symbol=symbol
        )

        try:

            response = session.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            html = response.text

            if html:

                return {
                    "url": url,
                    "status_code": response.status_code,
                    "html": html,
                }

        except Exception as exc:

            errors.append(
                f"{url}: {exc}"
            )

    raise RuntimeError(
        "；".join(errors)
    )


# ============================================================
# 正規化
# ============================================================

def normalize(text):

    if text is None:
        return ""

    return (
        str(text)
        .replace("\n", "")
        .replace("\r", "")
        .replace("\t", "")
        .replace(" ", "")
        .replace("\u3000", "")
        .strip()
    )


# ============================================================
# 日期
# ============================================================

DATE_RE = re.compile(
    r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})"
)


def find_dates(text):

    dates = []

    for match in DATE_RE.finditer(text):

        date = (
            f"{match.group(1)}/"
            f"{int(match.group(2)):02d}/"
            f"{int(match.group(3)):02d}"
        )

        if date not in dates:

            dates.append(date)

    return dates


# ============================================================
# 數字
# ============================================================

NUMBER_RE = re.compile(
    r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?"
)


def extract_numbers(text):

    values = []

    for match in NUMBER_RE.finditer(text):

        raw = match.group(0)

        raw = raw.replace(",", "")

        try:

            values.append(
                float(raw)
            )

        except Exception:

            pass

    return values


# ============================================================
# Table 探測
# ============================================================

def inspect_tables(soup):

    tables = soup.find_all("table")

    output = []

    for table_index, table in enumerate(
        tables
    ):

        rows = table.find_all("tr")

        table_info = {
            "table_index": table_index,
            "row_count": len(rows),
            "headers": [],
            "rows": [],
            "contains_20": False,
            "contains_main_force": False,
        }

        for row_index, tr in enumerate(
            rows
        ):

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

            normalized = [
                normalize(value)
                for value in values
            ]

            if row_index == 0:

                table_info[
                    "headers"
                ] = normalized

            for value in normalized:

                if "20" in value:

                    table_info[
                        "contains_20"
                    ] = True

                if "買賣超" in value:

                    table_info[
                        "contains_main_force"
                    ] = True

            if values:

                table_info[
                    "rows"
                ].append(values)

        output.append(
            table_info
        )

    return output


# ============================================================
# 搜尋 HTML 內的 20D 關鍵字
# ============================================================

def inspect_keywords(html):

    keywords = [
        "20日",
        "20 日",
        "20day",
        "20Day",
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
    ]

    result = {}

    for keyword in keywords:

        result[keyword] = html.lower().count(
            keyword.lower()
        )

    return result


# ============================================================
# 搜尋 script / JSON 線索
# ============================================================

def inspect_scripts(soup):

    scripts = soup.find_all("script")

    matches = []

    patterns = [
        r"https?://[^\"']+",
        r"/api/[^\"']+",
        r"api[^\"']*",
        r"ajax[^\"']*",
        r"json[^\"']*",
        r"main-force[^\"']*",
        r"main_force[^\"']*",
        r"20d[^\"']*",
        r"20day[^\"']*",
        r"20日[^\"']*",
        r"買賣超[^\"']*",
    ]

    for script_index, script in enumerate(
        scripts
    ):

        content = script.string

        if content is None:

            content = script.get_text()

        if not content:

            continue

        for pattern in patterns:

            found = re.findall(
                pattern,
                content,
                flags=re.IGNORECASE
            )

            for item in found:

                item = item.strip()

                if item and item not in matches:

                    matches.append(
                        {
                            "script_index": script_index,
                            "pattern": pattern,
                            "value": item[:500],
                        }
                    )

    return matches


# ============================================================
# 探測是否真的超過 10 個日期
# ============================================================

def inspect_history_depth(
    soup,
    html
):

    dates = find_dates(html)

    unique_dates = sorted(
        set(dates),
        reverse=True
    )

    return {
        "date_count_in_html": len(
            unique_dates
        ),
        "dates": unique_dates[:50],
        "has_11_or_more_dates": (
            len(unique_dates) >= 11
        ),
        "has_20_or_more_dates": (
            len(unique_dates) >= 20
        ),
    }


# ============================================================
# 尋找 table 中可能的 20 日欄位
# ============================================================

def find_20_columns(
    tables
):

    results = []

    for table in tables:

        headers = table.get(
            "headers",
            []
        )

        for index, header in enumerate(
            headers
        ):

            if "20" in header:

                results.append({
                    "table_index": table[
                        "table_index"
                    ],
                    "column_index": index,
                    "header": header,
                })

    return results


# ============================================================
# 分析單一股票
# ============================================================

def analyze_stock(
    session,
    symbol
):

    section(
        f"測試股票：{symbol}"
    )

    result = {
        "symbol": symbol,
        "success": False,
        "url": None,
        "status_code": None,
        "html_length": 0,
        "history_depth": {},
        "keywords": {},
        "tables": [],
        "twenty_columns": [],
        "script_matches": [],
        "error": None,
    }

    try:

        page = request_page(
            session,
            symbol
        )

        html = page["html"]

        result["success"] = True
        result["url"] = page["url"]
        result["status_code"] = page[
            "status_code"
        ]
        result["html_length"] = len(
            html
        )

        log(
            f"✓ HTTP："
            f"{page['status_code']}"
        )

        log(
            f"✓ URL："
            f"{page['url']}"
        )

        log(
            f"✓ HTML："
            f"{len(html):,} bytes"
        )

        soup = BeautifulSoup(
            html,
            "html.parser"
        )

        # ----------------------------------------------------
        # 1. 歷史日期深度
        # ----------------------------------------------------

        history_depth = inspect_history_depth(
            soup,
            html
        )

        result[
            "history_depth"
        ] = history_depth

        log(
            f"HTML 日期數量："
            f"{history_depth['date_count_in_html']}"
        )

        log(
            f"是否 >= 11 日："
            f"{history_depth['has_11_or_more_dates']}"
        )

        log(
            f"是否 >= 20 日："
            f"{history_depth['has_20_or_more_dates']}"
        )

        # ----------------------------------------------------
        # 2. Table
        # ----------------------------------------------------

        tables = inspect_tables(
            soup
        )

        result[
            "tables"
        ] = tables

        log(
            f"HTML Table 數量："
            f"{len(tables)}"
        )

        # ----------------------------------------------------
        # 3. 20 日欄位
        # ----------------------------------------------------

        twenty_columns = find_20_columns(
            tables
        )

        result[
            "twenty_columns"
        ] = twenty_columns

        if twenty_columns:

            log(
                "✓ 發現包含「20」的 Table 欄位："
            )

            for item in twenty_columns:

                log(
                    f"   table="
                    f"{item['table_index']} "
                    f"column="
                    f"{item['column_index']} "
                    f"header="
                    f"{item['header']}"
                )

        else:

            log(
                "⚠️ 未在 Table header 找到 20 日欄位"
            )

        # ----------------------------------------------------
        # 4. 關鍵字
        # ----------------------------------------------------

        keywords = inspect_keywords(
            html
        )

        result[
            "keywords"
        ] = keywords

        log(
            "關鍵字命中："
        )

        for keyword, count in keywords.items():

            if count:

                log(
                    f"   {keyword}: {count}"
                )

        # ----------------------------------------------------
        # 5. Script / API 線索
        # ----------------------------------------------------

        script_matches = inspect_scripts(
            soup
        )

        result[
            "script_matches"
        ] = script_matches

        log(
            f"Script/API 線索："
            f"{len(script_matches)}"
        )

        for item in script_matches[:30]:

            log(
                "   "
                f"[script {item['script_index']}] "
                f"{item['value'][:300]}"
            )

        # ----------------------------------------------------
        # 6. 顯示前幾個 Table header
        # ----------------------------------------------------

        log("")
        log(
            "Table Headers："
        )

        for table in tables:

            headers = table.get(
                "headers",
                []
            )

            if headers:

                log(
                    f"   table "
                    f"{table['table_index']}: "
                    f"{headers}"
                )

        # ----------------------------------------------------
        # 7. 顯示前 3 列
        # ----------------------------------------------------

        log("")
        log(
            "Table 前 3 筆資料："
        )

        for table in tables:

            rows = table.get(
                "rows",
                []
            )

            if not rows:
                continue

            log(
                f"   table "
                f"{table['table_index']}:"
            )

            for row in rows[:4]:

                log(
                    f"      {row}"
                )

    except Exception as exc:

        result[
            "error"
        ] = str(exc)

        log(
            f"❌ 測試失敗：{exc}"
        )

    return result


# ============================================================
# 儲存
# ============================================================

def save_results(
    results
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
            "探測 CMoney 20D 主力歷史資料來源"
        ),
        "test_symbols": TEST_SYMBOLS,
        "results": results,
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

    with temp.open(
        "r",
        encoding="utf-8"
    ) as f:

        json.load(f)

    temp.replace(
        OUTPUT_FILE
    )

    log("")
    log(
        f"✓ 測試結果已寫入："
        f"{OUTPUT_FILE}"
    )


# ============================================================
# 總結
# ============================================================

def summarize(
    results
):

    section(
        "20D 探測結果總結"
    )

    for result in results:

        symbol = result[
            "symbol"
        ]

        if not result[
            "success"
        ]:

            log(
                f"{symbol}: ❌ HTTP/頁面失敗"
            )

            continue

        depth = result[
            "history_depth"
        ]

        twenty = result[
            "twenty_columns"
        ]

        log(
            f"{symbol}: "
            f"HTML日期="
            f"{depth.get('date_count_in_html', 0)}, "
            f">=20日="
            f"{depth.get('has_20_or_more_dates', False)}, "
            f"20日欄位="
            f"{len(twenty)}"
        )

    log("")
    log(
        "判定原則："
    )

    log(
        "1. 若 HTML 本身有 >=20 個交易日，"
        "代表資料可能已存在，只是目前 Parser 沒抓到。"
    )

    log(
        "2. 若 HTML 有 20 日欄位，"
        "必須確認它是否為「主力買賣超20日加總」。"
    )

    log(
        "3. 若 HTML 沒有 >=20 日資料，"
        "下一步才需要找 API / AJAX / JSON endpoint。"
    )

    log(
        "4. 絕不把「20日集中」當成「主力20日買賣超」。"
    )


# ============================================================
# Main
# ============================================================

def main():

    start = time.time()

    log("")
    log("=" * 72)
    log(
        f"台股 AI 選股系統 "
        f"CMoney 20D 探測器 {VERSION}"
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
        "測試股票："
        + ", ".join(
            TEST_SYMBOLS
        )
    )

    session = requests.Session()

    results = []

    for symbol in TEST_SYMBOLS:

        result = analyze_stock(
            session,
            symbol
        )

        results.append(
            result
        )

        time.sleep(
            REQUEST_DELAY
        )

    save_results(
        results
    )

    summarize(
        results
    )

    elapsed = (
        time.time()
        - start
    )

    log("")
    log("=" * 72)
    log(
        "✓ CMoney 20D 探測完成"
    )
    log(
        f"耗時：{elapsed:.1f} 秒"
    )
    log(
        f"結果：{OUTPUT_FILE}"
    )
    log("=" * 72)

    return 0


if __name__ == "__main__":

    sys.exit(
        main()
    )