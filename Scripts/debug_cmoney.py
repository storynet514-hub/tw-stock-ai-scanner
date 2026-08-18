#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
debug_cmoney.py V1.0

============================================================
目的
============================================================

上一層 debug_universe.py 已確認：

2337 旺宏
2426 鼎元

都能：

1. 存在於 Data/universe.json
2. 通過 Universe 篩選
3. 成功進入 fetch_chip.py Universe

因此本程式不再檢查 Universe。

本程式只檢查：

fetch_chip.py
    ↓
CMoney HTTP Request
    ↓
HTTP Response
    ↓
HTML
    ↓
HTML table
    ↓
table header
    ↓
「買賣超」欄位
    ↓
2337 / 2426 主力資料

============================================================
重要
============================================================

本程式：

✓ 只測試 2337
✓ 只測試 2426
✓ 不修改 chip.json
✓ 不修改 prices.json
✓ 不修改 universe.json
✓ 不跑全市場
✓ 不計算選股條件

============================================================
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup


# ============================================================
# 基本設定
# ============================================================

VERSION = "V1.0"

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG_DIR = BASE_DIR / "Data" / "debug_cmoney"

REQUEST_TIMEOUT = 30

REQUEST_DELAY = 1.0

TARGETS = [
    {
        "symbol": "2337",
        "name": "旺宏",
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
# HTTP Header
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
# Header normalize
# ============================================================

def normalize_header(text):

    if text is None:
        return ""

    text = str(text)

    text = text.replace(
        "\n",
        ""
    )

    text = text.replace(
        "\r",
        ""
    )

    text = text.replace(
        " ",
        ""
    )

    text = text.replace(
        "\u3000",
        ""
    )

    return text.strip()


# ============================================================
# 判斷買賣超 Header
# ============================================================

def is_main_force_header(text):

    header = normalize_header(text)

    if header == "買賣超":
        return True

    if (
        "買賣超" in header
        and "家數" not in header
        and "集中" not in header
    ):
        return True

    return False


# ============================================================
# 日期
# ============================================================

def normalize_date(text):

    if text is None:
        return None

    text = str(text).strip()

    patterns = [
        r"\d{4}/\d{1,2}/\d{1,2}",
        r"\d{4}-\d{1,2}-\d{1,2}",
    ]

    for pattern in patterns:

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
# 數字
# ============================================================

def parse_number(text):

    if text is None:
        return None

    text = str(text).strip()

    if not text:
        return None

    text = text.replace(
        ",",
        ""
    )

    text = text.replace(
        "張",
        ""
    )

    text = text.replace(
        "%",
        ""
    )

    if text.upper() in {
        "N/A",
        "NA",
        "NONE",
        "NULL",
        "-",
        "--",
        "－",
        "—",
        "無",
    }:
        return None

    match = re.search(
        r"[-+]?\d+(?:\.\d+)?",
        text
    )

    if not match:
        return None

    try:

        return float(
            match.group(0)
        )

    except Exception:

        return None


# ============================================================
# HTTP Request
# ============================================================

def request_cmoney(
    session,
    symbol
):

    urls = [
        (
            "desktop",
            CMONEY_URL.format(
                symbol=symbol
            )
        ),
        (
            "mobile",
            CMONEY_MOBILE_URL.format(
                symbol=symbol
            )
        ),
    ]

    last_error = None

    for source, url in urls:

        section(
            f"{symbol} HTTP Request - {source}"
        )

        log(
            f"URL：{url}"
        )

        try:

            started = time.time()

            response = session.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True
            )

            elapsed = (
                time.time()
                - started
            )

            log(
                f"HTTP Status："
                f"{response.status_code}"
            )

            log(
                f"Final URL："
                f"{response.url}"
            )

            log(
                f"Content-Type："
                f"{response.headers.get('Content-Type')}"
            )

            log(
                f"Content-Length："
                f"{response.headers.get('Content-Length')}"
            )

            log(
                f"Response bytes："
                f"{len(response.content)}"
            )

            log(
                f"耗時："
                f"{elapsed:.2f} 秒"
            )

            response.raise_for_status()

            if not response.text:

                log(
                    "❌ Response body 是空的"
                )

                continue

            log(
                f"HTML 字元數："
                f"{len(response.text)}"
            )

            return response.text, source, response

        except Exception as exc:

            last_error = exc

            log(
                f"❌ Request 失敗：{exc}"
            )

    if last_error:
        raise last_error

    raise RuntimeError(
        "CMoney 所有 URL 都無法取得資料"
    )


# ============================================================
# HTML 基本資訊
# ============================================================

def inspect_html(
    html,
    symbol
):

    section(
        f"{symbol} HTML 基本檢查"
    )

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    title = soup.find("title")

    if title:

        log(
            "Page Title："
            + title.get_text(
                " ",
                strip=True
            )
        )

    else:

        log(
            "Page Title：<不存在>"
        )

    log(
        f"HTML <table> 數量："
        f"{len(soup.find_all('table'))}"
    )

    log(
        f"HTML <tr> 數量："
        f"{len(soup.find_all('tr'))}"
    )

    log(
        f"HTML <th> 數量："
        f"{len(soup.find_all('th'))}"
    )

    log(
        f"HTML <td> 數量："
        f"{len(soup.find_all('td'))}"
    )

    # --------------------------------------------------------
    # 搜尋關鍵字
    # --------------------------------------------------------

    text = soup.get_text(
        "\n",
        strip=True
    )

    keywords = [
        "主力",
        "買賣超",
        "家數差",
        "5日集中",
        "20日集中",
        "收盤價",
    ]

    log("")

    log("關鍵字搜尋：")

    for keyword in keywords:

        count = text.count(
            keyword
        )

        if count > 0:

            log(
                f"✓ {keyword}："
                f"{count} 次"
            )

        else:

            log(
                f"✗ {keyword}："
                f"0 次"
            )


# ============================================================
# Table 詳細檢查
# ============================================================

def inspect_tables(
    soup,
    symbol
):

    section(
        f"{symbol} Table 結構檢查"
    )

    tables = soup.find_all(
        "table"
    )

    if not tables:

        log(
            "❌ 找不到任何 HTML table"
        )

        return []

    all_table_info = []

    for table_index, table in enumerate(
        tables
    ):

        log("")
        log(
            f"--- TABLE #{table_index} ---"
        )

        rows = table.find_all(
            "tr"
        )

        log(
            f"Rows：{len(rows)}"
        )

        found_force_header = False

        for row_index, tr in enumerate(
            rows[:10]
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

            headers = [
                normalize_header(
                    value
                )
                for value in values
            ]

            log(
                f"Row {row_index}: "
                f"{headers}"
            )

            for column_index, header in enumerate(
                headers
            ):

                if is_main_force_header(
                    header
                ):

                    found_force_header = True

                    log(
                        "   ⭐ 找到「買賣超」"
                        f"欄位 index={column_index}"
                    )

        if found_force_header:

            log(
                "✓ 此 Table 疑似為主力資料表"
            )

        else:

            log(
                "✗ 此 Table 沒有明確「買賣超」Header"
            )

        all_table_info.append({
            "table_index": table_index,
            "rows": rows,
            "has_main_force_header":
                found_force_header,
        })

    return all_table_info


# ============================================================
# 依 Header 解析
# ============================================================

def parse_by_header(
    soup,
    symbol
):

    section(
        f"{symbol} Header-based Parser"
    )

    tables = soup.find_all(
        "table"
    )

    if not tables:

        log(
            "❌ 沒有 table"
        )

        return []

    results = []

    for table_index, table in enumerate(
        tables
    ):

        rows = table.find_all(
            "tr"
        )

        if not rows:
            continue

        header_index = None
        headers = []

        # ----------------------------------------------------
        # 找 Header
        # ----------------------------------------------------

        for row_index, tr in enumerate(
            rows[:10]
        ):

            cells = tr.find_all(
                ["th", "td"]
            )

            if not cells:
                continue

            current_headers = [
                normalize_header(
                    cell.get_text(
                        " ",
                        strip=True
                    )
                )
                for cell in cells
            ]

            has_date = any(
                (
                    h == "日期"
                    or "日期" in h
                )
                for h in current_headers
            )

            has_force = any(
                is_main_force_header(h)
                for h in current_headers
            )

            if has_date and has_force:

                header_index = row_index
                headers = current_headers

                break

        if header_index is None:

            continue

        # ----------------------------------------------------
        # 欄位 index
        # ----------------------------------------------------

        date_index = None
        force_index = None

        for index, header in enumerate(
            headers
        ):

            if (
                date_index is None
                and (
                    header == "日期"
                    or "日期" in header
                )
            ):

                date_index = index

            if (
                force_index is None
                and is_main_force_header(
                    header
                )
            ):

                force_index = index

        log(
            f"Table #{table_index}"
        )

        log(
            f"Header：{headers}"
        )

        log(
            f"日期欄 index："
            f"{date_index}"
        )

        log(
            f"買賣超欄 index："
            f"{force_index}"
        )

        if (
            date_index is None
            or force_index is None
        ):

            continue

        # ----------------------------------------------------
        # 解析資料列
        # ----------------------------------------------------

        for row_index, tr in enumerate(
            rows
        ):

            if row_index <= header_index:
                continue

            cells = tr.find_all(
                ["th", "td"]
            )

            if len(cells) <= max(
                date_index,
                force_index
            ):
                continue

            values = [
                cell.get_text(
                    " ",
                    strip=True
                )
                for cell in cells
            ]

            date = normalize_date(
                values[date_index]
            )

            force = parse_number(
                values[force_index]
            )

            if not date:
                continue

            if force is None:
                continue

            record = {
                "date": date,
                "main_force": force,
            }

            results.append(
                record
            )

            log(
                f"✓ DATA "
                f"date={date} "
                f"main_force={force}"
            )

            if len(results) >= 15:

                break

        if results:

            break

    if results:

        log("")
        log(
            f"✓ Header Parser 成功："
            f"{len(results)} 筆"
        )

    else:

        log("")
        log(
            "❌ Header Parser 沒有取得任何資料"
        )

    return results


# ============================================================
# 顯示可能的主力文字
# ============================================================

def inspect_text_context(
    soup,
    symbol
):

    section(
        f"{symbol} 文字結構檢查"
    )

    text = soup.get_text(
        "\n",
        strip=True
    )

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    keywords = [
        "買賣超",
        "主力進出",
        "家數差",
    ]

    hit_count = 0

    for index, line in enumerate(
        lines
    ):

        if any(
            keyword in line
            for keyword in keywords
        ):

            hit_count += 1

            log("")
            log(
                f"--- 命中 #{hit_count} "
                f"line={index} ---"
            )

            start = max(
                0,
                index - 3
            )

            end = min(
                len(lines),
                index + 8
            )

            for j in range(
                start,
                end
            ):

                log(
                    f"[{j}] {lines[j]}"
                )

            if hit_count >= 10:

                break

    if hit_count == 0:

        log(
            "❌ 找不到主力相關文字"
        )


# ============================================================
# 儲存 HTML
# ============================================================

def save_debug_html(
    html,
    symbol,
    source
):

    DEBUG_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    filename = (
        f"{symbol}_{source}.html"
    )

    path = (
        DEBUG_DIR
        / filename
    )

    with path.open(
        "w",
        encoding="utf-8"
    ) as f:

        f.write(html)

    log(
        f"✓ HTML 已保存："
        f"{path}"
    )

    log(
        f"HTML 大小："
        f"{path.stat().st_size / 1024:.1f} KB"
    )

    return path


# ============================================================
# 單檔 Debug
# ============================================================

def debug_stock(
    session,
    target
):

    symbol = target[
        "symbol"
    ]

    name = target[
        "name"
    ]

    section(
        f"開始 Debug："
        f"{symbol} {name}"
    )

    try:

        html, source, response = request_cmoney(
            session,
            symbol
        )

        # ----------------------------------------------------
        # 儲存原始 HTML
        # ----------------------------------------------------

        save_debug_html(
            html,
            symbol,
            source
        )

        # ----------------------------------------------------
        # HTML 基本資訊
        # ----------------------------------------------------

        inspect_html(
            html,
            symbol
        )

        soup = BeautifulSoup(
            html,
            "html.parser"
        )

        # ----------------------------------------------------
        # Table
        # ----------------------------------------------------

        inspect_tables(
            soup,
            symbol
        )

        # ----------------------------------------------------
        # Header parser
        # ----------------------------------------------------

        results = parse_by_header(
            soup,
            symbol
        )

        # ----------------------------------------------------
        # 文字上下文
        # ----------------------------------------------------

        inspect_text_context(
            soup,
            symbol
        )

        # ----------------------------------------------------
        # 最終
        # ----------------------------------------------------

        section(
            f"{symbol} 最終結果"
        )

        if results:

            log(
                f"✓ CMoney Request 成功"
            )

            log(
                f"✓ HTML 成功取得"
            )

            log(
                f"✓ Header Parser 成功"
            )

            log(
                f"✓ 取得資料："
                f"{len(results)} 筆"
            )

            log("")

            log(
                "最近資料："
            )

            for row in results[:10]:

                log(
                    f"  "
                    f"{row['date']}  "
                    f"主力買賣超="
                    f"{row['main_force']} 張"
                )

            return {
                "symbol": symbol,
                "success": True,
                "source": source,
                "rows": results,
                "error": None,
            }

        else:

            log(
                "❌ HTTP 可能成功"
            )

            log(
                "❌ 但 Parser 沒有取得主力資料"
            )

            log(
                "→ 下一步需要根據保存的 HTML "
                "修正 Parser"
            )

            return {
                "symbol": symbol,
                "success": False,
                "source": source,
                "rows": [],
                "error": (
                    "HTTP 成功但 Parser "
                    "找不到主力資料"
                ),
            }

    except Exception as exc:

        log(
            f"❌ {symbol} Debug 失敗："
            f"{exc}"
        )

        return {
            "symbol": symbol,
            "success": False,
            "source": None,
            "rows": [],
            "error": str(exc),
        }


# ============================================================
# Main
# ============================================================

def main():

    start_time = time.time()

    log("")
    log("=" * 72)
    log(
        f"台股 AI 選股系統 "
        f"debug_cmoney.py {VERSION}"
    )
    log("=" * 72)

    log(
        "目的："
        "確認 CMoney Request / HTML / Parser"
    )

    log(
        "測試股票：2337 旺宏、2426 鼎元"
    )

    log(
        "不修改 chip.json"
    )

    log(
        "不跑全市場"
    )

    session = requests.Session()

    results = []

    # ========================================================
    # 逐檔
    # ========================================================

    for index, target in enumerate(
        TARGETS,
        start=1
    ):

        log("")
        log(
            f"測試進度："
            f"{index}/{len(TARGETS)}"
        )

        result = debug_stock(
            session,
            target
        )

        results.append(
            result
        )

        if index < len(TARGETS):

            time.sleep(
                REQUEST_DELAY
            )

    # ========================================================
    # 總結
    # ========================================================

    section(
        "最終 Debug 結論"
    )

    success_count = sum(
        1
        for result in results
        if result["success"]
    )

    fail_count = (
        len(results)
        - success_count
    )

    for result in results:

        symbol = result[
            "symbol"
        ]

        if result["success"]:

            log(
                f"✓ {symbol} "
                f"CMoney Request / Parser 成功"
            )

        else:

            log(
                f"❌ {symbol} "
                f"CMoney Request / Parser 失敗"
            )

            log(
                f"   原因："
                f"{result['error']}"
            )

    log("")
    log(
        f"成功：{success_count}"
    )

    log(
        f"失敗：{fail_count}"
    )

    log(
        f"Debug HTML："
        f"{DEBUG_DIR}"
    )

    elapsed = (
        time.time()
        - start_time
    )

    log(
        f"總耗時："
        f"{elapsed:.1f} 秒"
    )

    # ========================================================
    # 判斷
    # ========================================================

    if success_count == len(TARGETS):

        log("")
        log(
            "================================================"
        )
        log(
            "✓✓✓ CMoney Request / Parser 驗證通過"
        )
        log(
            "→ 下一步可以回到 fetch_chip.py"
        )
        log(
            "→ 再做全市場測試"
        )
        log(
            "================================================"
        )

        return 0

    if success_count > 0:

        log("")
        log(
            "================================================"
        )
        log(
            "⚠️ CMoney 部分股票成功"
        )
        log(
            "→ 需要比較成功與失敗股票的 HTML"
        )
        log(
            "================================================"
        )

        return 1

    log("")
    log(
        "================================================"
    )
    log(
        "❌ CMoney Request / Parser 尚未通過"
    )
    log(
        "→ 不應直接跑 1985 檔"
    )
    log(
        "→ 先根據 Data/debug_cmoney/*.html 修正"
    )
    log(
        "================================================"
    )

    return 1


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
