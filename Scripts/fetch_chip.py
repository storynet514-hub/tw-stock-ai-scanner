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
資料定義
============================================================

本程式的「主力買賣超」：

CMoney「主力進出」頁面的：

「買賣超」

單位：
張

正數 = 主力買超
負數 = 主力賣超

主力 5 日：
最近 5 個交易日每日「買賣超」加總。

主力 10 日：
最近 10 個交易日每日「買賣超」加總。

主力 20 日：
最近 20 個交易日每日「買賣超」加總。

============================================================
絕對禁止
============================================================

1. 不使用「20日集中」
2. 不使用「5日集中」
3. 不使用「家數差」
4. 不用文字位置猜測第二個數字
5. 不把其他欄位當成主力買賣超
6. 不因為 HTTP 200 就認定取得的是正確資料
7. 不足 20 筆時禁止硬算 main_force_20d

============================================================
V5.1 修正
============================================================

V5.0 最大問題：

parse_text_fallback() 會：

日期
↓
往後找數字
↓
拿第二個數字
↓
直接當 main_force

這有可能把：

收盤價
家數差
5日集中
20日集中

等其他數字誤認成主力買賣超。

V5.1：

只接受 HTML table 中：

日期
收盤價
買賣超
家數差
5日集中
20日集中

所對應的「買賣超」欄位。

如果無法確認欄位：

直接拒絕該批資料。

============================================================
輸出
============================================================

Data/chip.json

每檔股票包含：

main_force_1d
main_force_5d
main_force_10d
main_force_20d

以及：

history

history 每筆：

date
main_force
source_column

============================================================
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

VERSION = "V5.1"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

CHIP_FILE = DATA_DIR / "chip.json"

REQUEST_TIMEOUT = 30

REQUEST_DELAY = 0.30

EXTENSION_DELAY = 0.15

MIN_HISTORY = 20

MAX_EXTENSION_REQUESTS = 30


# ============================================================
# 固定診斷股票
# ============================================================

DIAGNOSTIC_SYMBOLS = {
    "2337",
    "2426",
    "2368",
    "3081",
}


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
# HTTP Headers
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
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
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

    items = data.get(
        "items",
        []
    )

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

        symbol = str(symbol).strip().upper()

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
        f"Universe 股票數量：{len(stocks)}"
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

DATE_PATTERNS = [
    r"\d{4}/\d{1,2}/\d{1,2}",
    r"\d{4}-\d{1,2}-\d{1,2}",
]


def normalize_date(text):

    if text is None:
        return None

    text = str(text).strip()

    for pattern in DATE_PATTERNS:

        if re.fullmatch(
            pattern,
            text
        ):

            return text.replace(
                "-",
                "/"
            )

    return None


# ============================================================
# Header normalization
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
# 嚴格判斷「買賣超」
# ============================================================

def is_exact_main_force_header(text):

    header = normalize_header(text)

    return header == "買賣超"


# ============================================================
# Table parser
# ============================================================

def parse_main_force_tables(
    html
):
    """
    只從 HTML table 解析。

    必須同時找到：

    日期
    買賣超

    而且買賣超必須是獨立欄位。

    不使用文字 fallback。
    """

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    tables = soup.find_all("table")

    best_result = []

    best_metadata = None

    for table_index, table in enumerate(
        tables
    ):

        rows = table.find_all("tr")

        if not rows:
            continue

        header_info = None

        # ----------------------------------------------------
        # 找真正 header
        # ----------------------------------------------------

        for header_row_index, tr in enumerate(
            rows[:20]
        ):

            cells = tr.find_all(
                ["th", "td"]
            )

            if not cells:
                continue

            headers = [
                normalize_header(
                    cell.get_text(
                        " ",
                        strip=True
                    )
                )
                for cell in cells
            ]

            date_index = -1
            force_index = -1

            for index, header in enumerate(
                headers
            ):

                if (
                    date_index < 0
                    and (
                        header == "日期"
                        or "日期" in header
                    )
                ):
                    date_index = index

                if (
                    force_index < 0
                    and is_exact_main_force_header(
                        header
                    )
                ):
                    force_index = index

            # ------------------------------------------------
            # 必須真的同時有：
            # 日期 + 買賣超
            # ------------------------------------------------

            if (
                date_index >= 0
                and force_index >= 0
            ):

                header_info = {
                    "header_row_index":
                        header_row_index,
                    "date_index":
                        date_index,
                    "force_index":
                        force_index,
                    "headers":
                        headers,
                }

                break

        if header_info is None:
            continue

        date_index = header_info[
            "date_index"
        ]

        force_index = header_info[
            "force_index"
        ]

        headers = header_info[
            "headers"
        ]

        result = []

        # ----------------------------------------------------
        # 解析資料列
        # ----------------------------------------------------

        start_index = (
            header_info[
                "header_row_index"
            ] + 1
        )

        for tr in rows[start_index:]:

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

            force_text = values[
                force_index
            ]

            force_value = parse_number(
                force_text
            )

            if force_value is None:
                continue

            # ------------------------------------------------
            # 保存額外欄位供診斷
            # ------------------------------------------------

            row_data = {
                "date": date_text,
                "main_force":
                    force_value,
                "source_column":
                    "買賣超",
            }

            # 收盤價
            if len(values) > 1:

                # 不是依位置當來源，
                # 只是診斷資訊
                row_data[
                    "raw_row"
                ] = values

            result.append(
                row_data
            )

        if result:

            # 優先選資料筆數最多的正確 table
            if len(result) > len(
                best_result
            ):

                best_result = result

                best_metadata = {
                    "table_index":
                        table_index,
                    "headers":
                        headers,
                    "date_index":
                        date_index,
                    "force_index":
                        force_index,
                }

    if not best_result:

        return [], None

    return (
        clean_history(
            best_result
        ),
        best_metadata
    )


# ============================================================
# Clean history
# ============================================================

def clean_history(rows):

    unique = {}

    for row in rows:

        date = row.get("date")

        value = row.get(
            "main_force"
        )

        if not date:
            continue

        if value is None:
            continue

        try:

            datetime.strptime(
                date,
                "%Y/%m/%d"
            )

        except Exception:

            continue

        # 同一天若重複：
        # 保留最後一次
        unique[date] = {
            "date": date,
            "main_force":
                float(value),
            "source_column":
                "買賣超",
        }

    result = list(
        unique.values()
    )

    result.sort(
        key=lambda x:
            datetime.strptime(
                x["date"],
                "%Y/%m/%d"
            ),
        reverse=True
    )

    return result


# ============================================================
# Request
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

    return (
        response.text,
        response.url
    )


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

            html, final_url = request_url(
                session,
                url
            )

            return (
                html,
                final_url
            )

        except Exception as exc:

            last_error = exc

    if last_error:

        raise last_error

    raise RuntimeError(
        "無法取得 CMoney 頁面"
    )


# ============================================================
# 發現延伸 URL
# ============================================================

def discover_more_urls(
    html,
    base_url,
    symbol
):
    """
    只尋找 CMoney 自己頁面暴露出的延伸 URL。

    不自行猜 API。

    這些 URL 後續仍然必須經過：
    日期 + 買賣超 table 驗證。
    """

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    urls = []

    def add_url(value):

        if not value:
            return

        value = str(
            value
        ).strip()

        if value.startswith(
            "javascript:"
        ):
            return

        if value.startswith("#"):
            return

        absolute = urljoin(
            base_url,
            value
        )

        parsed = urlparse(
            absolute
        )

        host = parsed.netloc.lower()

        if (
            "cmoney.tw" not in host
        ):
            return

        # 必須跟這檔股票有關
        if (
            symbol not in absolute
            and "main-force"
            not in absolute
        ):
            return

        if absolute not in urls:

            urls.append(
                absolute
            )

    # --------------------------------------------------------
    # a / link
    # --------------------------------------------------------

    for tag in soup.find_all(
        ["a", "link"]
    ):

        href = tag.get("href")

        text = tag.get_text(
            " ",
            strip=True
        )

        if not href:
            continue

        lower_href = href.lower()

        if (
            "查看更多" in text
            or "更多" in text
            or "more" in lower_href
            or "page" in lower_href
            or "offset" in lower_href
            or "limit" in lower_href
            or "main-force" in lower_href
        ):

            add_url(href)

    # --------------------------------------------------------
    # data-* attributes
    # --------------------------------------------------------

    for tag in soup.find_all(True):

        for attr, value in tag.attrs.items():

            attr_lower = str(
                attr
            ).lower()

            if not any(
                key in attr_lower
                for key in [
                    "url",
                    "href",
                    "api",
                    "endpoint",
                    "load",
                    "more",
                    "next",
                    "page",
                ]
            ):
                continue

            if isinstance(
                value,
                list
            ):
                value = " ".join(
                    str(v)
                    for v in value
                )

            add_url(value)

    return urls


# ============================================================
# 驗證延伸頁面
# ============================================================

def fetch_extension_page(
    session,
    url
):

    try:

        response = session.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:
            return []

        html = response.text

        if not html:
            return []

        history, metadata = (
            parse_main_force_tables(
                html
            )
        )

        # ----------------------------------------------------
        # 沒有「日期 + 買賣超」
        # 直接拒絕
        # ----------------------------------------------------

        if not metadata:

            return []

        return history

    except Exception:

        return []


# ============================================================
# 取得至少 20 個交易日
# ============================================================

def fetch_20d_history(
    session,
    symbol
):

    html, page_url = request_page(
        session,
        symbol
    )

    history, metadata = (
        parse_main_force_tables(
            html
        )
    )

    # --------------------------------------------------------
    # 首頁結果
    # --------------------------------------------------------

    log(
        f"   CMoney 首頁有效「買賣超」："
        f"{len(history)} 筆"
    )

    if metadata:

        log(
            "   ✓ 已確認資料來源欄位：買賣超"
        )

        if symbol in DIAGNOSTIC_SYMBOLS:

            log(
                "   表頭："
                + " | ".join(
                    metadata[
                        "headers"
                    ]
                )
            )

    else:

        log(
            "   ⚠️ 首頁沒有找到"
            "「日期 + 買賣超」表格"
        )

    # --------------------------------------------------------
    # 首頁已足夠
    # --------------------------------------------------------

    if len(history) >= MIN_HISTORY:

        return history[:MIN_HISTORY]

    # --------------------------------------------------------
    # 尋找 CMoney 真正暴露的延伸 URL
    # --------------------------------------------------------

    more_urls = discover_more_urls(
        html,
        page_url,
        symbol
    )

    log(
        f"   發現 CMoney 延伸 URL："
        f"{len(more_urls)}"
    )

    if not more_urls:

        raise RuntimeError(
            "CMoney 頁面只有 "
            f"{len(history)} 筆「買賣超」，"
            "且沒有找到可驗證的歷史資料延伸來源。"
        )

    # --------------------------------------------------------
    # 逐個驗證延伸 URL
    # --------------------------------------------------------

    seen_urls = set()

    seen_dates = {
        row["date"]
        for row in history
    }

    request_count = 0

    for url in more_urls:

        if request_count >= (
            MAX_EXTENSION_REQUESTS
        ):
            break

        if url in seen_urls:
            continue

        seen_urls.add(url)

        request_count += 1

        page_history = (
            fetch_extension_page(
                session,
                url
            )
        )

        if not page_history:

            time.sleep(
                EXTENSION_DELAY
            )

            continue

        before = len(
            history
        )

        for row in page_history:

            date = row.get(
                "date"
            )

            if not date:
                continue

            if date in seen_dates:
                continue

            history.append({
                "date": date,
                "main_force":
                    float(
                        row[
                            "main_force"
                        ]
                    ),
                "source_column":
                    "買賣超",
            })

            seen_dates.add(date)

        history = clean_history(
            history
        )

        added = (
            len(history)
            - before
        )

        if added > 0:

            log(
                f"   ✓ 延伸頁面確認「買賣超」"
                f"，新增 {added} 筆"
            )

            log(
                f"   目前有效歷史："
                f"{len(history)} 筆"
            )

        if len(history) >= MIN_HISTORY:

            log(
                "   ✓ 已取得至少 20 個"
                "交易日的「買賣超」"
            )

            return history[:MIN_HISTORY]

        time.sleep(
            EXTENSION_DELAY
        )

    # --------------------------------------------------------
    # 最終驗證
    # --------------------------------------------------------

    history = clean_history(
        history
    )

    if len(history) < MIN_HISTORY:

        raise RuntimeError(
            "CMoney 真正可驗證的"
            "「買賣超」只有 "
            f"{len(history)} 筆，"
            f"不足 {MIN_HISTORY} 筆。"
            "禁止使用其他欄位補足。"
        )

    return history[:MIN_HISTORY]


# ============================================================
# 計算 1 / 5 / 10 / 20
# ============================================================

def calculate_periods(
    history
):

    if not history:

        raise RuntimeError(
            "history 為空"
        )

    values = [
        float(
            row[
                "main_force"
            ]
        )
        for row in history
        if row.get(
            "main_force"
        ) is not None
    ]

    result = {
        "main_force_1d": None,
        "main_force_5d": None,
        "main_force_10d": None,
        "main_force_20d": None,
        "history_count": len(
            values
        ),
    }

    if len(values) >= 1:

        result[
            "main_force_1d"
        ] = round(
            sum(
                values[:1]
            ),
            2
        )

    if len(values) >= 5:

        result[
            "main_force_5d"
        ] = round(
            sum(
                values[:5]
            ),
            2
        )

    if len(values) >= 10:

        result[
            "main_force_10d"
        ] = round(
            sum(
                values[:10]
            ),
            2
        )

    if len(values) >= 20:

        result[
            "main_force_20d"
        ] = round(
            sum(
                values[:20]
            ),
            2
        )

    return result


# ============================================================
# 計算驗證
# ============================================================

def verify_period_calculation(
    history,
    periods
):

    values = [
        float(
            row[
                "main_force"
            ]
        )
        for row in history
    ]

    expected = {
        "main_force_1d":
            round(
                sum(values[:1]),
                2
            )
            if len(values) >= 1
            else None,

        "main_force_5d":
            round(
                sum(values[:5]),
                2
            )
            if len(values) >= 5
            else None,

        "main_force_10d":
            round(
                sum(values[:10]),
                2
            )
            if len(values) >= 10
            else None,

        "main_force_20d":
            round(
                sum(values[:20]),
                2
            )
            if len(values) >= 20
            else None,
    }

    for key, expected_value in expected.items():

        actual = periods.get(key)

        if actual != expected_value:

            raise RuntimeError(
                f"{key} 計算驗證失敗："
                f"actual={actual}, "
                f"expected={expected_value}"
            )

    # --------------------------------------------------------
    # 20D 必須真的有 20 筆
    # --------------------------------------------------------

    if periods.get(
        "main_force_20d"
    ) is not None:

        if len(values) < 20:

            raise RuntimeError(
                "main_force_20d 存在，"
                "但 history 不足 20 筆"
            )


# ============================================================
# Status
# ============================================================

def get_status(
    data
):

    if (
        data.get(
            "main_force_1d"
        ) is not None
        and data.get(
            "main_force_5d"
        ) is not None
        and data.get(
            "main_force_10d"
        ) is not None
        and data.get(
            "main_force_20d"
        ) is not None
    ):

        return "complete"

    if (
        data.get(
            "main_force_1d"
        ) is not None
    ):

        return "partial"

    return "insufficient"


# ============================================================
# 診斷輸出
# ============================================================

def log_diagnostic_history(
    symbol,
    history
):

    if symbol not in DIAGNOSTIC_SYMBOLS:
        return

    log("")
    log(
        f"   ===== {symbol} 原始「買賣超」"
        "診斷 ====="
    )

    for index, row in enumerate(
        history[:20],
        start=1
    ):

        log(
            f"   {index:02d}. "
            f"{row['date']}  "
            f"買賣超="
            f"{row['main_force']}"
        )

    log(
        "   ================================="
    )


# ============================================================
# Fetch all
# ============================================================

def fetch_all(
    stocks
):

    section(
        "開始取得 CMoney 主力買賣超"
    )

    total = len(stocks)

    log(
        f"待處理股票：{total}"
    )

    log(
        "資料來源：CMoney 主力進出"
    )

    log(
        "指定來源欄位：買賣超"
    )

    log(
        "禁止來源欄位："
        "5日集中 / 20日集中 / 家數差"
    )

    session = requests.Session()

    session.headers.update(
        HEADERS
    )

    results = {}

    complete = 0
    partial = 0
    insufficient = 0

    for index, stock in enumerate(
        stocks,
        start=1
    ):

        symbol = stock[
            "symbol"
        ]

        name = stock[
            "name"
        ]

        log("")
        log(
            f"[{index}/{total}] "
            f"{symbol} {name}"
        )

        record = {
            "symbol":
                symbol,

            "name":
                name,

            "market":
                stock[
                    "market"
                ],

            "source":
                "CMoney",

            "source_page":
                (
                    CMONEY_URL.format(
                        symbol=symbol
                    )
                ),

            "source_column":
                "買賣超",

            "main_force_1d":
                None,

            "main_force_5d":
                None,

            "main_force_10d":
                None,

            "main_force_20d":
                None,

            "history_count":
                0,

            "status":
                "insufficient",

            "history":
                [],

            "error":
                None,
        }

        try:

            history = fetch_20d_history(
                session,
                symbol
            )

            periods = calculate_periods(
                history
            )

            verify_period_calculation(
                history,
                periods
            )

            record.update(
                periods
            )

            record[
                "history"
            ] = history[:20]

            record[
                "status"
            ] = get_status(
                record
            )

            # ------------------------------------------------
            # 額外診斷
            # ------------------------------------------------

            log_diagnostic_history(
                symbol,
                history
            )

            # ------------------------------------------------
            # 統計
            # ------------------------------------------------

            status = record[
                "status"
            ]

            if status == "complete":

                complete += 1

            elif status == "partial":

                partial += 1

            else:

                insufficient += 1

            # ------------------------------------------------
            # Log
            # ------------------------------------------------

            log(
                f"   主力1日："
                f"{record['main_force_1d']}"
            )

            log(
                f"   主力5日："
                f"{record['main_force_5d']}"
            )

            log(
                f"   主力10日："
                f"{record['main_force_10d']}"
            )

            log(
                f"   主力20日："
                f"{record['main_force_20d']}"
            )

            log(
                f"   歷史筆數："
                f"{record['history_count']}"
            )

            log(
                f"   狀態："
                f"{record['status']}"
            )

        except Exception as exc:

            insufficient += 1

            record[
                "error"
            ] = str(exc)

            log(
                f"   ❌ 取得失敗："
                f"{exc}"
            )

        results[
            symbol
        ] = record

        time.sleep(
            REQUEST_DELAY
        )

    return (
        results,
        complete,
        partial,
        insufficient
    )


# ============================================================
# Validate
# ============================================================

def validate(
    results,
    total,
    complete,
    partial,
    insufficient
):

    section(
        "籌碼資料驗證"
    )

    valid_1d = 0
    valid_5d = 0
    valid_10d = 0
    valid_20d = 0

    for symbol, record in results.items():

        history = record.get(
            "history",
            []
        )

        # ----------------------------------------------------
        # history 日期不能重複
        # ----------------------------------------------------

        dates = [
            row.get("date")
            for row in history
        ]

        if len(dates) != len(
            set(dates)
        ):

            raise RuntimeError(
                f"{symbol} history "
                "存在重複日期"
            )

        # ----------------------------------------------------
        # source_column 必須全部是買賣超
        # ----------------------------------------------------

        for row in history:

            if row.get(
                "source_column"
            ) != "買賣超":

                raise RuntimeError(
                    f"{symbol} history "
                    "發現非「買賣超」來源"
                )

        # ----------------------------------------------------
        # 統計
        # ----------------------------------------------------

        if record.get(
            "main_force_1d"
        ) is not None:

            valid_1d += 1

        if record.get(
            "main_force_5d"
        ) is not None:

            valid_5d += 1

        if record.get(
            "main_force_10d"
        ) is not None:

            valid_10d += 1

        if record.get(
            "main_force_20d"
        ) is not None:

            valid_20d += 1

        # ----------------------------------------------------
        # complete 必須 20 筆
        # ----------------------------------------------------

        if record.get(
            "status"
        ) == "complete":

            if len(history) < 20:

                raise RuntimeError(
                    f"{symbol} status=complete "
                    "但 history < 20"
                )

            if record.get(
                "main_force_20d"
            ) is None:

                raise RuntimeError(
                    f"{symbol} complete "
                    "但缺少 20D"
                )

    log(
        f"Universe：{total}"
    )

    log(
        f"完整：{complete}"
    )

    log(
        f"部分：{partial}"
    )

    log(
        f"不足：{insufficient}"
    )

    log(
        f"主力1日有效：{valid_1d}"
    )

    log(
        f"主力5日有效：{valid_5d}"
    )

    log(
        f"主力10日有效：{valid_10d}"
    )

    log(
        f"主力20日有效：{valid_20d}"
    )

    if not results:

        raise RuntimeError(
            "沒有任何股票資料"
        )

    if valid_20d == 0:

        raise RuntimeError(
            "本次完全沒有取得有效"
            "主力20日資料"
        )

    if valid_20d < total:

        log(
            "⚠️ 部分股票尚未取得20D"
        )

    else:

        log(
            "✓ 全部股票20D有效"
        )


# ============================================================
# Save
# ============================================================

def save_chip(
    results,
    total,
    complete,
    partial,
    insufficient
):

    section(
        "寫入 Data/chip.json"
    )

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    now = datetime.now()

    output = {

        "schema_version":
            VERSION,

        "generated_at":
            now.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "data_date":
            now.strftime(
                "%Y-%m-%d"
            ),

        "source":
            "CMoney",

        "source_page":
            "CMoney 主力進出",

        "source_column":
            "買賣超",

        "definition": {

            "main_force":
                "CMoney 主力進出頁面的「買賣超」",

            "main_force_1d":
                "最近1個交易日「買賣超」",

            "main_force_5d":
                "最近5個交易日「買賣超」加總",

            "main_force_10d":
                "最近10個交易日「買賣超」加總",

            "main_force_20d":
                "最近20個交易日「買賣超」加總",

            "NOT_main_force_5d":
                "CMoney 5日集中",

            "NOT_main_force_20d":
                "CMoney 20日集中",

            "NOT_main_force_house":
                "CMoney 家數差",

            "unit":
                "張",

            "positive":
                "主力買超",

            "negative":
                "主力賣超",
        },

        "universe_count":
            total,

        "statistics": {

            "complete":
                complete,

            "partial":
                partial,

            "insufficient":
                insufficient,
        },

        "stocks":
            results,
    }

    temp_file = CHIP_FILE.with_suffix(
        ".json.tmp"
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

    # ========================================================
    # 寫入後驗證
    # ========================================================

    with temp_file.open(
        "r",
        encoding="utf-8"
    ) as f:

        verify = json.load(f)

    verify_stocks = verify.get(
        "stocks"
    )

    if not isinstance(
        verify_stocks,
        dict
    ):

        raise RuntimeError(
            "chip.json stocks 格式錯誤"
        )

    if len(
        verify_stocks
    ) != len(
        results
    ):

        raise RuntimeError(
            "chip.json 股票數量驗證失敗"
        )

    for symbol, record in (
        verify_stocks.items()
    ):

        history = record.get(
            "history",
            []
        )

        # ----------------------------------------------------
        # 每一筆 history 都必須是買賣超
        # ----------------------------------------------------

        for row in history:

            if row.get(
                "source_column"
            ) != "買賣超":

                raise RuntimeError(
                    f"{symbol} "
                    "存在非法 source_column"
                )

        # ----------------------------------------------------
        # complete 必須完整 20D
        # ----------------------------------------------------

        if record.get(
            "status"
        ) == "complete":

            if len(history) < 20:

                raise RuntimeError(
                    f"{symbol} history "
                    "不足20筆"
                )

            if record.get(
                "main_force_20d"
            ) is None:

                raise RuntimeError(
                    f"{symbol} "
                    "缺少 main_force_20d"
                )

            # 再次驗證計算
            periods = calculate_periods(
                history
            )

            verify_period_calculation(
                history,
                periods
            )

            for key in [
                "main_force_1d",
                "main_force_5d",
                "main_force_10d",
                "main_force_20d",
            ]:

                if (
                    record.get(key)
                    != periods.get(key)
                ):

                    raise RuntimeError(
                        f"{symbol} "
                        f"{key} 寫入驗證失敗"
                    )

    temp_file.replace(
        CHIP_FILE
    )

    log(
        "✓ chip.json 建立成功"
    )

    log(
        f"股票：{len(results)}"
    )

    log(
        f"檔案：{CHIP_FILE}"
    )


# ============================================================
# Main
# ============================================================

def main():

    start_time = time.time()

    log("")
    log("=" * 72)

    log(
        "台股 AI 選股系統 "
        f"fetch_chip.py {VERSION}"
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
        "資料來源：CMoney 主力進出"
    )

    log(
        "指定欄位：買賣超"
    )

    log(
        "20D 定義："
        "最近20個交易日每日買賣超加總"
    )

    log(
        "禁止：5日集中 / 20日集中 / 家數差"
    )

    try:

        stocks = load_universe()

        (
            results,
            complete,
            partial,
            insufficient
        ) = fetch_all(
            stocks
        )

        validate(
            results,
            len(stocks),
            complete,
            partial,
            insufficient
        )

        save_chip(
            results,
            len(stocks),
            complete,
            partial,
            insufficient
        )

        elapsed = (
            time.time()
            - start_time
        )

        log("")
        log("=" * 72)

        log(
            f"✓ fetch_chip.py {VERSION} "
            "執行完成"
        )

        log("=" * 72)

        log(
            f"完整：{complete}"
        )

        log(
            f"部分：{partial}"
        )

        log(
            f"不足：{insufficient}"
        )

        log(
            f"總耗時："
            f"{elapsed:.1f} 秒"
        )

        log(
            f"輸出：{CHIP_FILE}"
        )

        return 0

    except Exception as exc:

        log("")
        log("=" * 72)

        log(
            f"❌ fetch_chip.py {VERSION} "
            "執行失敗"
        )

        log("=" * 72)

        log(
            f"原因：{exc}"
        )

        if CHIP_FILE.exists():

            log(
                "⚠️ 保留既有 chip.json"
            )

        return 1


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
