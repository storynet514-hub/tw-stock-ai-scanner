#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V5.1

============================================================
核心功能
============================================================

取得台股 Universe：

1. 每日主力買賣超
2. 主力 5 日買賣超
3. 主力 10 日買賣超
4. 主力 20 日買賣超

============================================================
重要定義
============================================================

本程式的「主力」：

CMoney「主力進出」頁面的：

「買賣超」

單位：
張

正數 = 主力買超
負數 = 主力賣超

主力 5 日：
最近 5 個交易日每日主力買賣超加總。

主力 10 日：
最近 10 個交易日每日主力買賣超加總。

主力 20 日：
最近 20 個交易日每日主力買賣超加總。

============================================================
明確排除
============================================================

以下資料不屬於本程式的計算指標：

- 5日集中
- 20日集中
- 家數差

特別注意：

CMoney 頁面中的「20日集中」
不是主力 20 日買賣超。

main_force_20d 必須由：

最近 20 個交易日
「每日買賣超」
逐日加總

取得。

============================================================
V5.1
============================================================

V5.1 基於 V5.0 架構：

1. 保留 CMoney 網頁抓取
2. 不進行 API endpoint 探測
3. 保留歷史資料延伸取得
4. 至少取得 20 個交易日
5. 保留 5D
6. 保留 10D
7. 保留 20D
8. 不使用 5日集中
9. 不使用 20日集中
10. 不使用家數差

============================================================
輸出
============================================================

Data/chip.json

包含：

main_force_1d
main_force_5d
main_force_10d
main_force_20d

history：
最近至少 20 個交易日主力買賣超
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

VERSION = "V5.1"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

CHIP_FILE = DATA_DIR / "chip.json"

REQUEST_TIMEOUT = 30

REQUEST_DELAY = 0.20

MIN_HISTORY = 20

MAX_FETCH_ROUNDS = 6


# ============================================================
# CMoney
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
# User-Agent
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
# Universe
# ============================================================

def load_universe():

    section("讀取台股 Universe")

    if not UNIVERSE_FILE.exists():
        raise RuntimeError(
            f"找不到：{UNIVERSE_FILE}"
        )

    with UNIVERSE_FILE.open(
        "r",
        encoding="utf-8"
    ) as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise RuntimeError(
            "universe.json 格式錯誤"
        )

    items = data.get("items", [])

    if not isinstance(items, list):
        raise RuntimeError(
            "universe.json items 不是 list"
        )

    stocks = []

    seen = set()

    for item in items:

        if not isinstance(item, dict):
            continue

        symbol = item.get("code")

        if symbol is None:
            symbol = item.get("symbol")

        if symbol is None:
            continue

        symbol = str(
            symbol
        ).strip().upper()

        symbol = re.sub(
            r"\.(TW|TWO)$",
            "",
            symbol
        )

        if not re.fullmatch(
            r"[A-Z0-9]{4,6}",
            symbol
        ):
            continue

        if symbol in seen:
            continue

        seen.add(symbol)

        stocks.append({
            "symbol": symbol,
            "name": str(
                item.get(
                    "name",
                    ""
                )
            ).strip(),
            "market": str(
                item.get(
                    "market",
                    ""
                )
            ).strip(),
        })

    if not stocks:
        raise RuntimeError(
            "Universe 沒有任何合法股票"
        )

    log(
        f"Universe 股票數量："
        f"{len(stocks)}"
    )

    return stocks


# ============================================================
# Number
# ============================================================

def parse_number(text):

    if text is None:
        return None

    text = str(text).strip()

    if not text:
        return None

    text = text.replace(",", "")
    text = text.replace("張", "")
    text = text.replace("%", "")

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
# Date
# ============================================================

def normalize_date(text):

    if text is None:
        return None

    text = str(text).strip()

    patterns = [
        r"(\d{4})/(\d{1,2})/(\d{1,2})",
        r"(\d{4})-(\d{1,2})-(\d{1,2})",
        r"(\d{4})(\d{2})(\d{2})",
    ]

    for pattern in patterns:

        match = re.fullmatch(
            pattern,
            text
        )

        if match:

            year, month, day = (
                match.groups()
            )

            return (
                f"{int(year):04d}/"
                f"{int(month):02d}/"
                f"{int(day):02d}"
            )

    return None


# ============================================================
# Header
# ============================================================

def normalize_header(text):

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


def is_main_force_header(text):

    header = normalize_header(text)

    # 只認真正的「買賣超」
    #
    # 不把：
    # 家數差
    # 5日集中
    # 20日集中
    #
    # 當成主力買賣超。

    return header == "買賣超"


# ============================================================
# HTTP
# ============================================================

def request_url(
    session,
    url
):

    response = session.get(
        url,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    if not response.text:
        raise RuntimeError(
            "CMoney 回傳空白內容"
        )

    return response.text


def request_page(
    session,
    symbol
):

    urls = [
        CMONEY_URL.format(
            symbol=symbol
        ),
        CMONEY_MOBILE_URL.format(
            symbol=symbol
        ),
    ]

    last_error = None

    for url in urls:

        try:

            html = request_url(
                session,
                url
            )

            return html, url

        except Exception as exc:

            last_error = exc

    if last_error:
        raise last_error

    raise RuntimeError(
        "無法取得 CMoney 頁面"
    )


# ============================================================
# Table Parser
# ============================================================

def parse_table_with_header(
    soup
):

    tables = soup.find_all(
        "table"
    )

    for table in tables:

        rows = table.find_all(
            "tr"
        )

        if not rows:
            continue

        header_index = None

        headers = None

        # ----------------------------------------------------
        # 找真正的資料表頭
        # ----------------------------------------------------

        for index, tr in enumerate(
            rows[:15]
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

            has_date = (
                "日期" in current_headers
            )

            has_force = any(
                is_main_force_header(
                    h
                )
                for h in current_headers
            )

            if (
                has_date
                and has_force
            ):

                header_index = index

                headers = (
                    current_headers
                )

                break

        if (
            header_index is None
            or headers is None
        ):
            continue

        # ----------------------------------------------------
        # 找欄位
        # ----------------------------------------------------

        date_index = -1

        force_index = -1

        for index, header in enumerate(
            headers
        ):

            if (
                date_index < 0
                and header == "日期"
            ):
                date_index = index

            if (
                force_index < 0
                and is_main_force_header(
                    header
                )
            ):
                force_index = index

        if (
            date_index < 0
            or force_index < 0
        ):
            continue

        result = []

        # ----------------------------------------------------
        # 解析資料列
        # ----------------------------------------------------

        for tr in rows[
            header_index + 1:
        ]:

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

            date_text = normalize_date(
                values[date_index]
            )

            if not date_text:
                continue

            force_value = parse_number(
                values[force_index]
            )

            if force_value is None:
                continue

            result.append({
                "date": date_text,
                "main_force": force_value,
            })

        if result:
            return result

    return []


# ============================================================
# Text fallback
# ============================================================

def parse_text_fallback(
    soup
):

    """
    僅作為 HTML table 解析失敗時的備援。

    預期資料結構：

    日期
    收盤價
    買賣超
    ...

    因此只取日期後最前面的兩個數字：

    第一個 = 收盤價
    第二個 = 買賣超

    不使用後面的：

    家數差
    5日集中
    20日集中
    """

    text = soup.get_text(
        "\n",
        strip=True
    )

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    result = []

    for i, line in enumerate(
        lines
    ):

        date_text = normalize_date(
            line
        )

        if not date_text:
            continue

        numeric_values = []

        for j in range(
            i + 1,
            min(
                i + 8,
                len(lines)
            )
        ):

            number = parse_number(
                lines[j]
            )

            if number is not None:
                numeric_values.append(
                    number
                )

            if len(
                numeric_values
            ) >= 2:
                break

        if len(
            numeric_values
        ) < 2:
            continue

        result.append({
            "date": date_text,
            "main_force":
                numeric_values[1],
        })

    return result


# ============================================================
# Parse main force
# ============================================================

def parse_main_force_table(
    html
):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    result = parse_table_with_header(
        soup
    )

    if result:
        return clean_history(
            result
        )

    result = parse_text_fallback(
        soup
    )

    return clean_history(
        result
    )


# ============================================================
# Clean history
# ============================================================

def clean_history(
    rows
):

    unique = {}

    for row in rows or []:

        if not isinstance(
            row,
            dict
        ):
            continue

        date = normalize_date(
            row.get("date")
        )

        value = parse_number(
            row.get("main_force")
        )

        if (
            date is None
            or value is None
        ):
            continue

        try:

            datetime.strptime(
                date,
                "%Y/%m/%d"
            )

        except Exception:

            continue

        unique[date] = value

    result = [
        {
            "date": date,
            "main_force": value,
        }
        for date, value in unique.items()
    ]

    # 最新 → 最舊
    result.sort(
        key=lambda x: x["date"],
        reverse=True
    )

    return result


# ============================================================
# 尋找下一頁 / 更多資料
#
# 注意：
#
# 這裡只使用網頁中實際存在的連結。
#
# 不做 API endpoint 探測。
# ============================================================

def find_next_urls(
    html,
    base_url
):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    urls = []

    keywords = (
        "查看更多",
        "更多",
        "下一頁",
        "下一页",
        "下一頁",
        "Next",
        "next",
    )

    # --------------------------------------------------------
    # <a href="">
    # --------------------------------------------------------

    for anchor in soup.find_all(
        "a",
        href=True
    ):

        text = anchor.get_text(
            " ",
            strip=True
        )

        href = anchor.get(
            "href",
            ""
        )

        if not href:
            continue

        is_candidate = (
            any(
                keyword in text
                for keyword in keywords
            )
            or "page=" in href
            or "pageNo=" in href
            or "pageIndex=" in href
            or "offset=" in href
        )

        if not is_candidate:
            continue

        full_url = urljoin(
            base_url,
            href
        )

        if full_url not in urls:
            urls.append(
                full_url
            )

    # --------------------------------------------------------
    # rel="next"
    # --------------------------------------------------------

    for link in soup.find_all(
        "link",
        href=True
    ):

        rel = link.get(
            "rel",
            []
        )

        if isinstance(
            rel,
            str
        ):
            rel = [rel]

        if "next" in [
            str(x).lower()
            for x in rel
        ]:

            full_url = urljoin(
                base_url,
                link["href"]
            )

            if full_url not in urls:
                urls.append(
                    full_url
                )

    return urls


# ============================================================
# Pagination fallback
#
# 僅使用已存在頁面 URL 的 query 參數。
# 不建立 CMoney API endpoint。
# ============================================================

def build_page_urls(
    base_url
):

    urls = []

    separator = (
        "&"
        if "?" in base_url
        else "?"
    )

    for page in range(
        2,
        MAX_FETCH_ROUNDS + 2
    ):

        urls.append(
            f"{base_url}"
            f"{separator}"
            f"page={page}"
        )

    return urls


# ============================================================
# 取得至少 20D
# ============================================================

def fetch_20d_history(
    session,
    symbol
):

    # --------------------------------------------------------
    # 第一輪：主頁
    # --------------------------------------------------------

    html, page_url = request_page(
        session,
        symbol
    )

    history = clean_history(
        parse_main_force_table(
            html
        )
    )

    log(
        f"   首頁取得："
        f"{len(history)} 筆"
    )

    if len(history) >= MIN_HISTORY:

        return history[
            :MIN_HISTORY
        ]

    # --------------------------------------------------------
    # 建立候選延伸頁面
    # --------------------------------------------------------

    candidates = []

    candidates.extend(
        find_next_urls(
            html,
            page_url
        )
    )

    candidates.extend(
        build_page_urls(
            page_url
        )
    )

    # 去重
    candidates = list(
        dict.fromkeys(
            candidates
        )
    )

    visited = set(
        [page_url]
    )

    # --------------------------------------------------------
    # 最多 MAX_FETCH_ROUNDS 輪
    # --------------------------------------------------------

    rounds = 0

    for url in candidates:

        if len(history) >= MIN_HISTORY:
            break

        if url in visited:
            continue

        visited.add(url)

        if rounds >= (
            MAX_FETCH_ROUNDS
        ):
            break

        rounds += 1

        try:

            html_next = request_url(
                session,
                url
            )

            next_rows = (
                parse_main_force_table(
                    html_next
                )
            )

            if not next_rows:
                continue

            before = len(
                history
            )

            combined = (
                history
                + next_rows
            )

            history = clean_history(
                combined
            )

            log(
                f"   延伸第 {rounds} 輪："
                f"{before} → "
                f"{len(history)} 筆"
            )

        except Exception as exc:

            log(
                f"   延伸資料失敗："
                f"{exc}"
            )

        time.sleep(
            REQUEST_DELAY
        )

    history = clean_history(
        history
    )

    # --------------------------------------------------------
    # 最終驗證
    # --------------------------------------------------------

    if len(history) < MIN_HISTORY:

        raise RuntimeError(
            "CMoney 可取得的有效主力資料只有 "
            f"{len(history)} 筆，"
            f"不足 {MIN_HISTORY} 個交易日"
        )

    return history[
        :MIN_HISTORY
    ]


# ============================================================
# 計算 N 日
# ============================================================

def calculate_period(
    history,
    days
):

    if len(history) < days:
        return None

    values = [
        float(
            row["main_force"]
        )
        for row in history[
            :days
        ]
    ]

    total = sum(
        values
    )

    if total.is_integer():
        return int(total)

    return round(
        total,
        2
    )


# ============================================================
# 建立單檔結果
# ============================================================

def build_stock_result(
    stock,
    history
):

    history = clean_history(
        history
    )

    result = {
        "symbol": stock[
            "symbol"
        ],

        "name": stock.get(
            "name",
            ""
        ),

        "market": stock.get(
            "market",
            ""
        ),

        "status": "insufficient",

        "history_count": len(
            history
        ),

        "main_force_1d": None,

        "main_force_5d": None,

        "main_force_10d": None,

        "main_force_20d": None,

        "history": history[
            :MIN_HISTORY
        ],

        "error": None,
    }

    if not history:

        result["error"] = (
            "沒有取得主力買賣超"
        )

        return result

    # --------------------------------------------------------
    # 1D
    # --------------------------------------------------------

    result[
        "main_force_1d"
    ] = history[0][
        "main_force"
    ]

    # --------------------------------------------------------
    # 5D
    # --------------------------------------------------------

    result[
        "main_force_5d"
    ] = calculate_period(
        history,
        5
    )

    # --------------------------------------------------------
    # 10D
    # --------------------------------------------------------

    result[
        "main_force_10d"
    ] = calculate_period(
        history,
        10
    )

    # --------------------------------------------------------
    # 20D
    # --------------------------------------------------------

    result[
        "main_force_20d"
    ] = calculate_period(
        history,
        20
    )

    # --------------------------------------------------------
    # 狀態
    # --------------------------------------------------------

    if (
        result[
            "main_force_1d"
        ] is not None
        and result[
            "main_force_5d"
        ] is not None
        and result[
            "main_force_10d"
        ] is not None
        and result[
            "main_force_20d"
        ] is not None
        and len(history) >= MIN_HISTORY
    ):

        result[
            "status"
        ] = "complete"

    elif (
        result[
            "main_force_1d"
        ] is not None
    ):

        result[
            "status"
        ] = "partial"

    return result


# ============================================================
# 單檔抓取
# ============================================================

def fetch_stock(
    session,
    stock
):

    symbol = stock[
        "symbol"
    ]

    name = stock.get(
        "name",
        ""
    )

    log(
        f"[{symbol}] {name}"
    )

    try:

        history = fetch_20d_history(
            session,
            symbol
        )

        result = build_stock_result(
            stock,
            history
        )

        log(
            "   1D  = "
            f"{result['main_force_1d']}"
        )

        log(
            "   5D  = "
            f"{result['main_force_5d']}"
        )

        log(
            "   10D = "
            f"{result['main_force_10d']}"
        )

        log(
            "   20D = "
            f"{result['main_force_20d']}"
        )

        log(
            "   history = "
            f"{result['history_count']}"
        )

        log(
            "   status = "
            f"{result['status']}"
        )

        return result

    except Exception as exc:

        log(
            f"   ✗ 失敗：{exc}"
        )

        return {
            "symbol": symbol,
            "name": name,
            "market": stock.get(
                "market",
                ""
            ),
            "status": "insufficient",
            "history_count": 0,
            "main_force_1d": None,
            "main_force_5d": None,
            "main_force_10d": None,
            "main_force_20d": None,
            "history": [],
            "error": str(exc),
        }


# ============================================================
# 驗證
# ============================================================

def validate_results(
    results
):

    if not results:

        raise RuntimeError(
            "沒有任何股票結果"
        )

    complete = 0

    valid_5d = 0

    valid_10d = 0

    valid_20d = 0

    for symbol, data in results.items():

        if data.get(
            "status"
        ) == "complete":

            complete += 1

        if data.get(
            "main_force_5d"
        ) is not None:

            valid_5d += 1

        if data.get(
            "main_force_10d"
        ) is not None:

            valid_10d += 1

        if data.get(
            "main_force_20d"
        ) is not None:

            valid_20d += 1

        # ----------------------------------------------------
        # 20D 必須能由 history 重算
        # ----------------------------------------------------

        history = data.get(
            "history",
            []
        )

        if (
            data.get(
                "status"
            ) == "complete"
            and len(history) >= 20
        ):

            expected = round(
                sum(
                    float(
                        row[
                            "main_force"
                        ]
                    )
                    for row in history[
                        :20
                    ]
                ),
                2
            )

            actual = round(
                float(
                    data[
                        "main_force_20d"
                    ]
                ),
                2
            )

            if expected != actual:

                raise RuntimeError(
                    f"{symbol} "
                    "main_force_20d "
                    "重新加總驗證失敗："
                    f"{actual} != {expected}"
                )

    section("資料驗證")

    log(
        f"總股票：{len(results)}"
    )

    log(
        f"完整：{complete}"
    )

    log(
        f"5D 有效：{valid_5d}"
    )

    log(
        f"10D 有效：{valid_10d}"
    )

    log(
        f"20D 有效：{valid_20d}"
    )

    if valid_20d == 0:

        raise RuntimeError(
            "本次沒有任何有效 20D 主力資料"
        )

    return {
        "complete": complete,
        "valid_5d": valid_5d,
        "valid_10d": valid_10d,
        "valid_20d": valid_20d,
    }


# ============================================================
# 寫入 chip.json
# ============================================================

def save_chip(
    results,
    stats
):

    output = {
        "version": VERSION,

        "generated_at":
            datetime.now().isoformat(),

        "source":
            "CMoney main-force",

        "periods": [
            5,
            10,
            20
        ],

        "definition": {
            "main_force":
                "CMoney 主力進出之買賣超",

            "main_force_5d":
                "最近5個交易日每日主力買賣超加總",

            "main_force_10d":
                "最近10個交易日每日主力買賣超加總",

            "main_force_20d":
                "最近20個交易日每日主力買賣超加總",

            "excluded": [
                "5日集中",
                "20日集中",
                "家數差"
            ],

            "unit":
                "張",

            "positive":
                "主力買超",

            "negative":
                "主力賣超",
        },

        "statistics": stats,

        "stocks": results,
    }

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    temp_file = (
        CHIP_FILE.with_suffix(
            ".json.tmp"
        )
    )

    with temp_file.open(
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2
        )

    # --------------------------------------------------------
    # 寫入後立即重新讀取驗證 JSON
    # --------------------------------------------------------

    with temp_file.open(
        "r",
        encoding="utf-8"
    ) as f:

        verify = json.load(f)

    if not isinstance(
        verify.get(
            "stocks"
        ),
        dict
    ):

        raise RuntimeError(
            "chip.json stocks 格式驗證失敗"
        )

    if len(
        verify["stocks"]
    ) != len(results):

        raise RuntimeError(
            "chip.json 股票數量驗證失敗"
        )

    temp_file.replace(
        CHIP_FILE
    )

    log(
        f"✓ 已寫入：{CHIP_FILE}"
    )


# ============================================================
# Main
# ============================================================

def main():

    start_time = time.time()

    section(
        f"台股 AI 選股系統 "
        f"fetch_chip.py {VERSION}"
    )

    log(
        "資料來源：CMoney 主力進出"
    )

    log(
        "主力定義：每日買賣超"
    )

    log(
        "5D：保留"
    )

    log(
        "10D：保留"
    )

    log(
        "20D：保留"
    )

    log(
        "5日集中：不使用"
    )

    log(
        "20日集中：不使用"
    )

    log(
        "家數差：不使用"
    )

    log(
        "API endpoint 探測：停用"
    )

    log(
        "歷史資料：至少 20 個交易日"
    )

    # --------------------------------------------------------
    # Universe
    # --------------------------------------------------------

    stocks = load_universe()

    # --------------------------------------------------------
    # Session
    # --------------------------------------------------------

    session = requests.Session()

    session.headers.update(
        HEADERS
    )

    results = {}

    # --------------------------------------------------------
    # 逐檔抓取
    # --------------------------------------------------------

    for index, stock in enumerate(
        stocks,
        start=1
    ):

        log("")

        log(
            f"========== "
            f"{index}/{len(stocks)} "
            f"=========="
        )

        result = fetch_stock(
            session,
            stock
        )

        results[
            stock["symbol"]
        ] = result

        time.sleep(
            REQUEST_DELAY
        )

    # --------------------------------------------------------
    # 驗證
    # --------------------------------------------------------

    stats = validate_results(
        results
    )

    # --------------------------------------------------------
    # 寫入
    # --------------------------------------------------------

    save_chip(
        results,
        stats
    )

    # --------------------------------------------------------
    # 完成
    # --------------------------------------------------------

    elapsed = (
        time.time()
        - start_time
    )

    section(
        "fetch_chip.py V5.1 完成"
    )

    log(
        f"總股票：{len(results)}"
    )

    log(
        f"完整：{stats['complete']}"
    )

    log(
        f"5D：{stats['valid_5d']}"
    )

    log(
        f"10D：{stats['valid_10d']}"
    )

    log(
        f"20D：{stats['valid_20d']}"
    )

    log(
        "排除："
        "5日集中 / "
        "20日集中 / "
        "家數差"
    )

    log(
        f"耗時：{elapsed:.2f} 秒"
    )

    log(
        f"輸出：{CHIP_FILE}"
    )

    return 0


# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":

    try:

        sys.exit(
            main()
        )

    except KeyboardInterrupt:

        log("")
        log(
            "使用者中止程式"
        )

        sys.exit(130)

    except Exception as exc:

        log("")
        log(
            "❌ fetch_chip.py 執行失敗："
        )

        log(
            str(exc)
        )

        sys.exit(1)
