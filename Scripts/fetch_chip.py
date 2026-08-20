#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V5.2

============================================================
本版本用途
============================================================

目前為 CMoney「主力買賣超 20D」驗證版。

注意：
本版本刻意「不讀 universe.json」。

固定測試 4 檔：

2337 旺宏
2426 鼎元
2368 金像電
3081 艾訊

因此不會再出現：

Universe 股票數量：1985

============================================================
核心定義
============================================================

主力：

CMoney「主力進出」頁面的：

「買賣超」

單位：

張

正數：
主力買超

負數：
主力賣超

============================================================
禁止
============================================================

絕對禁止使用：

5日集中
20日集中
家數差
買超家數
賣超家數
其他籌碼欄位

作為：

main_force_1d
main_force_5d
main_force_10d
main_force_20d

============================================================
20D 定義
============================================================

最近 20 個交易日：

每日「買賣超」

逐日加總。

例如：

D1 + D2 + ... + D20

才是：

main_force_20d

============================================================
重要
============================================================

CMoney 主力進出目前 HTML 首頁通常只有 10 筆。

所以：

不能看到 10 筆就自行複製。
不能拿 20日集中代替。
不能拿 5日集中代替。
不能拿家數差代替。

本版本只接受「真正的買賣超」。

如果 API / 延伸資料無法取得另外 10 筆：

該股票直接標記 insufficient。

============================================================
目前測試股票
============================================================

2337 旺宏
2426 鼎元
2368 金像電
3081 艾訊

============================================================
輸出
============================================================

Data/chip.json

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
# Version
# ============================================================

VERSION = "V5.2"


# ============================================================
# Project paths
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

CHIP_FILE = DATA_DIR / "chip.json"


# ============================================================
# CMoney
# ============================================================

CMONEY_URL = (
    "https://www.cmoney.tw/forum/stock/"
    "{symbol}?s=main-force"
)

CMONEY_API_HOSTS = (
    "api.cmoney.tw",
    "www.cmoney.tw",
    "mobile.cmoney.tw",
)


# ============================================================
# Fixed test universe
# ============================================================

TEST_STOCKS = [
    {
        "symbol": "2337",
        "name": "旺宏",
        "market": "TW",
    },
    {
        "symbol": "2426",
        "name": "鼎元",
        "market": "TW",
    },
    {
        "symbol": "2368",
        "name": "金像電",
        "market": "TW",
    },
    {
        "symbol": "3081",
        "name": "艾訊",
        "market": "TWO",
    },
]


# ============================================================
# Settings
# ============================================================

REQUEST_TIMEOUT = 30

REQUEST_DELAY = 0.25

MIN_HISTORY = 20

MAX_API_CANDIDATES = 80


# ============================================================
# Headers
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
        "image/avif,image/webp,"
        "*/*;q=0.8"
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
# Number parser
# ============================================================

def parse_number(value):

    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    text = (
        text
        .replace(",", "")
        .replace("張", "")
        .replace("%", "")
        .replace("+", "+")
    )

    if text.upper() in {
        "-",
        "--",
        "—",
        "－",
        "N/A",
        "NA",
        "NULL",
        "NONE",
    }:
        return None

    match = re.search(
        r"[-+]?\d+(?:\.\d+)?",
        text,
    )

    if not match:
        return None

    try:
        return float(match.group(0))
    except Exception:
        return None


# ============================================================
# Date parser
# ============================================================

def normalize_date(value):

    if value is None:
        return None

    text = str(value).strip()

    # 2026/08/20
    match = re.fullmatch(
        r"(\d{4})/(\d{1,2})/(\d{1,2})",
        text,
    )

    if match:

        y, m, d = match.groups()

        try:

            dt = datetime(
                int(y),
                int(m),
                int(d),
            )

            return dt.strftime(
                "%Y/%m/%d"
            )

        except Exception:
            return None

    # 2026-08-20
    match = re.fullmatch(
        r"(\d{4})-(\d{1,2})-(\d{1,2})",
        text,
    )

    if match:

        y, m, d = match.groups()

        try:

            dt = datetime(
                int(y),
                int(m),
                int(d),
            )

            return dt.strftime(
                "%Y/%m/%d"
            )

        except Exception:
            return None

    # 民國日期 115/08/20
    match = re.fullmatch(
        r"(\d{2,3})/(\d{1,2})/(\d{1,2})",
        text,
    )

    if match:

        y, m, d = match.groups()

        try:

            year = int(y) + 1911

            dt = datetime(
                year,
                int(m),
                int(d),
            )

            return dt.strftime(
                "%Y/%m/%d"
            )

        except Exception:
            return None

    return None


# ============================================================
# Header normalization
# ============================================================

def normalize_header(value):

    if value is None:
        return ""

    return (
        str(value)
        .replace("\n", "")
        .replace("\r", "")
        .replace(" ", "")
        .replace("\u3000", "")
        .strip()
    )


# ============================================================
# ONLY accepted field
# ============================================================

def is_buy_sell_header(value):

    header = normalize_header(value)

    # 必須是「買賣超」
    if header == "買賣超":
        return True

    # 允許少量描述文字
    if "買賣超" in header:

        forbidden = [
            "家數",
            "集中",
            "買超家",
            "賣超家",
        ]

        for word in forbidden:

            if word in header:
                return False

        return True

    return False


# ============================================================
# Parse CMoney HTML table
# ============================================================

def parse_html_table(html):

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    tables = soup.find_all("table")

    for table in tables:

        rows = table.find_all("tr")

        if not rows:
            continue

        header_index = None
        headers = None

        # ----------------------------------------------------
        # 找真正的：
        # 日期 + 買賣超
        # ----------------------------------------------------

        for i, tr in enumerate(
            rows[:20]
        ):

            cells = tr.find_all(
                ["th", "td"]
            )

            if not cells:
                continue

            current_headers = [
                normalize_header(
                    c.get_text(
                        " ",
                        strip=True,
                    )
                )
                for c in cells
            ]

            has_date = any(
                (
                    h == "日期"
                    or "日期" in h
                )
                for h in current_headers
            )

            has_buy_sell = any(
                is_buy_sell_header(h)
                for h in current_headers
            )

            if (
                has_date
                and has_buy_sell
            ):

                header_index = i
                headers = current_headers

                break

        if (
            header_index is None
            or headers is None
        ):
            continue

        # ----------------------------------------------------
        # 找欄位
        # ----------------------------------------------------

        date_index = None
        buy_sell_index = None

        for i, header in enumerate(
            headers
        ):

            if (
                date_index is None
                and (
                    header == "日期"
                    or "日期" in header
                )
            ):

                date_index = i

            if (
                buy_sell_index is None
                and is_buy_sell_header(
                    header
                )
            ):

                buy_sell_index = i

        if (
            date_index is None
            or buy_sell_index is None
        ):
            continue

        # ----------------------------------------------------
        # Parse rows
        # ----------------------------------------------------

        result = []

        for tr in rows[
            header_index + 1:
        ]:

            cells = tr.find_all(
                ["th", "td"]
            )

            if len(cells) <= max(
                date_index,
                buy_sell_index,
            ):
                continue

            values = [
                c.get_text(
                    " ",
                    strip=True,
                )
                for c in cells
            ]

            date = normalize_date(
                values[date_index]
            )

            if not date:
                continue

            buy_sell = parse_number(
                values[buy_sell_index]
            )

            if buy_sell is None:
                continue

            result.append(
                {
                    "date": date,
                    "main_force": buy_sell,
                    "source_field": "買賣超",
                }
            )

        if result:

            return clean_history(
                result
            )

    return []


# ============================================================
# JSON recursive parser
#
# 目的：
# CMoney API 如果回傳 JSON，
# 不依賴固定 key 名稱，
# 但必須同時驗證：
#
# 日期
# 買賣超
#
# 且禁止集中 / 家數
# ============================================================

DATE_KEYS = {
    "date",
    "tradedate",
    "trade_date",
    "日期",
}

BUY_SELL_KEYS = {
    "buy_sell",
    "buy_sell_net",
    "buysell",
    "buysellnet",
    "netbuy",
    "net_buy",
    "買賣超",
}

FORBIDDEN_KEYS = {
    "5日集中",
    "20日集中",
    "家數差",
    "buyhouse",
    "sellhouse",
    "house",
    "concentration",
    "5dayconcentration",
    "20dayconcentration",
}


def normalized_key(value):

    if value is None:
        return ""

    return (
        str(value)
        .strip()
        .lower()
        .replace(" ", "")
        .replace("_", "")
        .replace("-", "")
    )


def is_date_key(key):

    normalized = normalized_key(key)

    if normalized in {
        "date",
        "tradedate",
        "tradingdate",
        "日期",
    }:
        return True

    return False


def is_buy_sell_key(key):

    normalized = normalized_key(key)

    if normalized in {
        "buysell",
        "buysellnet",
        "netbuy",
        "buy_sell",
        "buy_sell_net",
        "買賣超",
    }:
        return True

    if "buysell" in normalized:
        return True

    if "買賣超" in str(key):
        return True

    return False


def is_forbidden_key(key):

    text = str(key)

    normalized = normalized_key(key)

    if text in FORBIDDEN_KEYS:
        return True

    if "集中" in text:
        return True

    if "家數" in text:
        return True

    if "concentration" in normalized:
        return True

    if "house" in normalized:
        return True

    return False


def recursive_find_rows(obj):

    result = []

    if isinstance(
        obj,
        list,
    ):

        for item in obj:

            result.extend(
                recursive_find_rows(item)
            )

        return result

    if not isinstance(
        obj,
        dict,
    ):
        return result

    date_value = None
    buy_sell_value = None

    has_forbidden = False

    for key, value in obj.items():

        if is_forbidden_key(key):

            has_forbidden = True

        if is_date_key(key):

            date_value = value

        if is_buy_sell_key(key):

            buy_sell_value = value

    if (
        date_value is not None
        and buy_sell_value is not None
        and not has_forbidden
    ):

        date = normalize_date(
            date_value
        )

        value = parse_number(
            buy_sell_value
        )

        if (
            date is not None
            and value is not None
        ):

            result.append(
                {
                    "date": date,
                    "main_force": value,
                    "source_field": "買賣超",
                }
            )

    for value in obj.values():

        if isinstance(
            value,
            (dict, list),
        ):

            result.extend(
                recursive_find_rows(value)
            )

    return result


def parse_json_response(text):

    try:

        data = json.loads(text)

    except Exception:

        return []

    rows = recursive_find_rows(
        data
    )

    return clean_history(
        rows
    )


# ============================================================
# Clean history
# ============================================================

def clean_history(rows):

    unique = {}

    for row in rows:

        date = row.get(
            "date"
        )

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
                "%Y/%m/%d",
            )

        except Exception:

            continue

        unique[date] = float(
            value
        )

    result = []

    for date, value in unique.items():

        result.append(
            {
                "date": date,
                "main_force": value,
                "source_field": "買賣超",
            }
        )

    result.sort(
        key=lambda x:
            datetime.strptime(
                x["date"],
                "%Y/%m/%d",
            ),
        reverse=True,
    )

    return result


# ============================================================
# Extract possible API URLs
# ============================================================

def extract_api_urls(
    html,
    symbol,
):

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    candidates = set()

    # --------------------------------------------------------
    # script src
    # --------------------------------------------------------

    for tag in soup.find_all(
        "script"
    ):

        src = tag.get("src")

        if src:

            absolute = urljoin(
                CMONEY_URL.format(
                    symbol=symbol
                ),
                src,
            )

            if (
                "cmoney.tw"
                in urlparse(
                    absolute
                ).netloc
            ):

                candidates.add(
                    absolute
                )

    # --------------------------------------------------------
    # href
    # --------------------------------------------------------

    for tag in soup.find_all(
        ["a", "link"]
    ):

        href = tag.get("href")

        if not href:
            continue

        absolute = urljoin(
            CMONEY_URL.format(
                symbol=symbol
            ),
            href,
        )

        if (
            "cmoney.tw"
            in urlparse(
                absolute
            ).netloc
        ):

            candidates.add(
                absolute
            )

    # --------------------------------------------------------
    # raw URLs
    # --------------------------------------------------------

    patterns = [
        r'https?://[^"\'>\s]+',
        r'(?:"|\')(/[^"\']{1,300})(?:"|\')',
    ]

    for pattern in patterns:

        matches = re.findall(
            pattern,
            html,
            flags=re.I,
        )

        for raw in matches:

            raw = (
                raw
                .replace(
                    "\\/",
                    "/",
                )
                .rstrip(
                    "'\" );},"
                )
            )

            if raw.startswith(
                "/"
            ):

                raw = urljoin(
                    CMONEY_URL.format(
                        symbol=symbol
                    ),
                    raw,
                )

            if not raw.startswith(
                "http"
            ):
                continue

            if not any(
                host in urlparse(
                    raw
                ).netloc
                for host in CMONEY_API_HOSTS
            ):
                continue

            candidates.add(
                raw
            )

    # --------------------------------------------------------
    # 只保留可能和籌碼相關的 URL
    # --------------------------------------------------------

    keywords = [
        "force",
        "main",
        "chip",
        "stock",
        "trade",
        "history",
        "api",
        "ocean",
        "service",
    ]

    filtered = []

    for url in candidates:

        lower = url.lower()

        if any(
            key in lower
            for key in keywords
        ):

            filtered.append(
                url
            )

    return list(
        dict.fromkeys(
            filtered
        )
    )


# ============================================================
# Fetch main page
# ============================================================

def fetch_main_page(
    session,
    symbol,
):

    url = CMONEY_URL.format(
        symbol=symbol
    )

    response = session.get(
        url,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    if not response.text:
        raise RuntimeError(
            "CMoney 回傳空白頁面"
        )

    return (
        response.text,
        response.url,
    )


# ============================================================
# Test API candidate
# ============================================================

def test_api_candidate(
    session,
    url,
    symbol,
):

    # --------------------------------------------------------
    # 將 stock symbol 帶入可能的 API URL
    # --------------------------------------------------------

    candidates = [
        url,
        url.replace(
            "{symbol}",
            symbol,
        ),
    ]

    for candidate in candidates:

        try:

            response = session.get(
                candidate,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code != 200:
                continue

            text = response.text

            if not text:
                continue

            # ------------------------------------------------
            # JSON
            # ------------------------------------------------

            history = parse_json_response(
                text
            )

            if len(history) >= 1:

                return history

            # ------------------------------------------------
            # HTML table
            # ------------------------------------------------

            history = parse_html_table(
                text
            )

            if history:

                return history

        except Exception:

            continue

    return []


# ============================================================
# Build controlled API candidates
# ============================================================

def build_api_candidates(
    discovered_urls,
    symbol,
):

    candidates = []

    for url in discovered_urls:

        candidates.append(
            url
        )

        # ----------------------------------------------------
        # 如果 URL 本身有 query，
        # 不亂改原參數。
        # ----------------------------------------------------

        # ----------------------------------------------------
        # 常見 API 參數：
        # stockId / stockNo / code / symbol
        # ----------------------------------------------------

        separators = [
            "&",
            "?" if "?" not in url else "&",
        ]

        for separator in separators:

            for key in [
                "stockId",
                "stockNo",
                "code",
                "symbol",
                "stock",
            ]:

                candidates.append(
                    f"{url}"
                    f"{separator}"
                    f"{key}={symbol}"
                )

    # 去重
    candidates = list(
        dict.fromkeys(
            candidates
        )
    )

    return candidates[
        :MAX_API_CANDIDATES
    ]


# ============================================================
# Fetch 20D
# ============================================================

def fetch_20d(
    session,
    symbol,
):

    section(
        f"CMoney 20D 驗證：{symbol}"
    )

    html, page_url = fetch_main_page(
        session,
        symbol,
    )

    # --------------------------------------------------------
    # 第一層：
    # CMoney HTML
    # --------------------------------------------------------

    history = parse_html_table(
        html
    )

    log(
        f"CMoney 首頁有效「買賣超」："
        f"{len(history)} 筆"
    )

    if history:

        log(
            "✓ 已確認資料來源欄位：買賣超"
        )

    else:

        log(
            "⚠️ 首頁沒有找到「日期 + 買賣超」表格"
        )

    # --------------------------------------------------------
    # 如果已經 20D
    # --------------------------------------------------------

    if len(history) >= MIN_HISTORY:

        return history[
            :MIN_HISTORY
        ]

    # --------------------------------------------------------
    # 第二層：
    # 找 JS / API 線索
    # --------------------------------------------------------

    discovered_urls = extract_api_urls(
        html,
        symbol,
    )

    log(
        f"發現 CMoney 延伸 URL："
        f"{len(discovered_urls)}"
    )

    # --------------------------------------------------------
    # 第三層：
    # 嘗試真正 API
    # --------------------------------------------------------

    candidates = build_api_candidates(
        discovered_urls,
        symbol,
    )

    log(
        f"開始驗證可能的 API："
        f"{len(candidates)}"
    )

    merged = {
        row["date"]: row
        for row in history
    }

    success_api = 0

    for index, url in enumerate(
        candidates,
        start=1,
    ):

        try:

            api_history = test_api_candidate(
                session,
                url,
                symbol,
            )

            if not api_history:
                continue

            # ------------------------------------------------
            # 只接受「買賣超」
            # ------------------------------------------------

            before = len(
                merged
            )

            for row in api_history:

                if (
                    row.get(
                        "source_field"
                    )
                    != "買賣超"
                ):
                    continue

                date = row.get(
                    "date"
                )

                if not date:
                    continue

                merged[date] = {
                    "date": date,
                    "main_force":
                        float(
                            row[
                                "main_force"
                            ]
                        ),
                    "source_field":
                        "買賣超",
                }

            after = len(
                merged
            )

            if after > before:

                success_api += 1

                log(
                    f"   ✓ API #{index} "
                    f"新增 "
                    f"{after - before} 筆"
                )

                log(
                    f"   目前真正買賣超："
                    f"{after} 筆"
                )

            if len(
                merged
            ) >= MIN_HISTORY:

                break

        except Exception:

            continue

        time.sleep(
            0.05
        )

    # --------------------------------------------------------
    # 最終整理
    # --------------------------------------------------------

    history = clean_history(
        list(
            merged.values()
        )
    )

    log(
        f"最終可驗證「買賣超」："
        f"{len(history)} 筆"
    )

    if success_api:

        log(
            f"成功驗證 API："
            f"{success_api}"
        )

    # --------------------------------------------------------
    # 強制驗證
    # --------------------------------------------------------

    if len(history) < MIN_HISTORY:

        raise RuntimeError(
            "CMoney 真正可驗證的"
            "「買賣超」只有 "
            f"{len(history)} 筆，"
            f"不足 {MIN_HISTORY} 筆。"
            "禁止使用其他欄位補足。"
        )

    # --------------------------------------------------------
    # 最後 20 日
    # --------------------------------------------------------

    history = history[
        :MIN_HISTORY
    ]

    # --------------------------------------------------------
    # 再驗證日期唯一性
    # --------------------------------------------------------

    dates = [
        row["date"]
        for row in history
    ]

    if len(
        dates
    ) != len(
        set(dates)
    ):

        raise RuntimeError(
            "20D 日期出現重複，"
            "拒絕計算。"
        )

    # --------------------------------------------------------
    # 再驗證每一筆都是真正買賣超
    # --------------------------------------------------------

    for row in history:

        if row.get(
            "source_field"
        ) != "買賣超":

            raise RuntimeError(
                "發現非「買賣超」資料，"
                "拒絕計算20D。"
            )

    log(
        "✓ 20D 已通過嚴格來源驗證"
    )

    return history


# ============================================================
# Calculate
# ============================================================

def calculate_periods(
    history
):

    values = [
        float(
            row["main_force"]
        )
        for row in history
    ]

    result = {
        "main_force_1d": None,
        "main_force_5d": None,
        "main_force_10d": None,
        "main_force_20d": None,
    }

    if len(values) >= 1:

        result[
            "main_force_1d"
        ] = round(
            sum(
                values[:1]
            ),
            2,
        )

    if len(values) >= 5:

        result[
            "main_force_5d"
        ] = round(
            sum(
                values[:5]
            ),
            2,
        )

    if len(values) >= 10:

        result[
            "main_force_10d"
        ] = round(
            sum(
                values[:10]
            ),
            2,
        )

    if len(values) >= 20:

        result[
            "main_force_20d"
        ] = round(
            sum(
                values[:20]
            ),
            2,
        )

    return result


# ============================================================
# Create record
# ============================================================

def create_record(
    stock
):

    return {
        "symbol":
            stock["symbol"],

        "name":
            stock["name"],

        "market":
            stock["market"],

        "source":
            "CMoney",

        "source_field":
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


# ============================================================
# Fetch all
# ============================================================

def fetch_all():

    section(
        "開始 CMoney 主力買賣超 20D 測試"
    )

    log(
        "本版本為固定測試模式"
    )

    log(
        "不讀 universe.json"
    )

    log(
        "不跑全市場 Universe"
    )

    log(
        "固定測試："
        "2337 / 2426 / 2368 / 3081"
    )

    session = requests.Session()

    session.headers.update(
        HEADERS
    )

    results = {}

    complete = 0
    insufficient = 0

    total = len(
        TEST_STOCKS
    )

    for index, stock in enumerate(
        TEST_STOCKS,
        start=1,
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

        record = create_record(
            stock
        )

        try:

            history = fetch_20d(
                session,
                symbol,
            )

            periods = calculate_periods(
                history
            )

            record.update(
                periods
            )

            record[
                "history"
            ] = history

            record[
                "history_count"
            ] = len(
                history
            )

            if (
                record[
                    "main_force_20d"
                ]
                is not None
            ):

                record[
                    "status"
                ] = "complete"

                complete += 1

            else:

                record[
                    "status"
                ] = "insufficient"

                insufficient += 1

            log(
                f"   1D  = "
                f"{record['main_force_1d']}"
            )

            log(
                f"   5D  = "
                f"{record['main_force_5d']}"
            )

            log(
                f"   10D = "
                f"{record['main_force_10d']}"
            )

            log(
                f"   20D = "
                f"{record['main_force_20d']}"
            )

            log(
                f"   history = "
                f"{record['history_count']}"
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
        insufficient,
    )


# ============================================================
# Validate output
# ============================================================

def validate_results(
    results
):

    section(
        "最終資料驗證"
    )

    if len(
        results
    ) != len(
        TEST_STOCKS
    ):

        raise RuntimeError(
            "測試股票數量錯誤"
        )

    expected = {
        stock["symbol"]
        for stock in TEST_STOCKS
    }

    actual = set(
        results.keys()
    )

    if actual != expected:

        raise RuntimeError(
            "輸出股票與固定測試清單不一致"
        )

    for symbol, record in results.items():

        if record[
            "status"
        ] != "complete":

            log(
                f"⚠️ {symbol} "
                "尚未取得完整20D"
            )

            continue

        history = record[
            "history"
        ]

        if len(
            history
        ) != MIN_HISTORY:

            raise RuntimeError(
                f"{symbol} "
                "history不是20筆"
            )

        # ----------------------------------------------------
        # 每筆來源欄位
        # ----------------------------------------------------

        for row in history:

            if row.get(
                "source_field"
            ) != "買賣超":

                raise RuntimeError(
                    f"{symbol} "
                    "存在非買賣超資料"
                )

        # ----------------------------------------------------
        # 手算20D
        # ----------------------------------------------------

        expected_20d = round(
            sum(
                float(
                    row[
                        "main_force"
                    ]
                )
                for row in history
            ),
            2,
        )

        actual_20d = record[
            "main_force_20d"
        ]

        if expected_20d != actual_20d:

            raise RuntimeError(
                f"{symbol} "
                "20D 加總驗證失敗："
                f"{actual_20d} != "
                f"{expected_20d}"
            )

    log(
        "✓ 資料來源欄位驗證完成"
    )

    log(
        "✓ 20D 加總驗證完成"
    )


# ============================================================
# Save
# ============================================================

def save_chip(
    results,
    complete,
    insufficient,
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

        "mode":
            "FIXED_TEST_4_STOCKS",

        "universe_mode":
            "disabled",

        "universe_count":
            4,

        "definition": {

            "main_force":
                "CMoney 主力進出之買賣超",

            "main_force_1d":
                "最近1個交易日買賣超",

            "main_force_5d":
                "最近5個交易日每日買賣超加總",

            "main_force_10d":
                "最近10個交易日每日買賣超加總",

            "main_force_20d":
                "最近20個交易日每日買賣超加總",

            "unit":
                "張",

            "positive":
                "主力買超",

            "negative":
                "主力賣超",

            "forbidden":
                [
                    "5日集中",
                    "20日集中",
                    "家數差",
                ],
        },

        "statistics": {

            "complete":
                complete,

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
            indent=2,
        )

    # --------------------------------------------------------
    # Reload validation
    # --------------------------------------------------------

    with temp_file.open(
        "r",
        encoding="utf-8"
    ) as f:

        verify = json.load(
            f
        )

    if set(
        verify["stocks"].keys()
    ) != {
        stock["symbol"]
        for stock in TEST_STOCKS
    }:

        raise RuntimeError(
            "寫入後股票清單驗證失敗"
        )

    # --------------------------------------------------------
    # Complete stock validation
    # --------------------------------------------------------

    for symbol, record in verify[
        "stocks"
    ].items():

        if record[
            "status"
        ] != "complete":

            continue

        history = record[
            "history"
        ]

        if len(
            history
        ) != 20:

            raise RuntimeError(
                f"{symbol} "
                "寫入後history不足20筆"
            )

        if record[
            "main_force_20d"
        ] is None:

            raise RuntimeError(
                f"{symbol} "
                "缺少main_force_20d"
            )

    temp_file.replace(
        CHIP_FILE
    )

    log(
        "✓ chip.json 寫入成功"
    )

    log(
        f"輸出股票數："
        f"{len(results)}"
    )

    log(
        f"輸出檔案："
        f"{CHIP_FILE}"
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
        f"fetch_chip.py {VERSION}"
    )
    log("=" * 72)

    log(
        f"開始時間："
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    log(
        "資料來源：CMoney 主力進出"
    )

    log(
        "指定欄位：買賣超"
    )

    log(
        "20D：最近20交易日每日買賣超加總"
    )

    log(
        "禁止：5日集中 / 20日集中 / 家數差"
    )

    log(
        "Universe：固定4檔測試"
    )

    try:

        (
            results,
            complete,
            insufficient,
        ) = fetch_all()

        validate_results(
            results
        )

        save_chip(
            results,
            complete,
            insufficient,
        )

        elapsed = (
            time.time()
            - start
        )

        log("")
        log("=" * 72)

        log(
            f"✓ fetch_chip.py {VERSION} 完成"
        )

        log("=" * 72)

        log(
            f"測試股票："
            f"{len(TEST_STOCKS)}"
        )

        log(
            f"完整20D："
            f"{complete}"
        )

        log(
            f"不足20D："
            f"{insufficient}"
        )

        log(
            f"總耗時："
            f"{elapsed:.1f} 秒"
        )

        log(
            f"輸出："
            f"{CHIP_FILE}"
        )

        return 0

    except Exception as exc:

        log("")
        log("=" * 72)

        log(
            f"❌ fetch_chip.py {VERSION} 失敗"
        )

        log("=" * 72)

        log(
            f"原因：{exc}"
        )

        # ----------------------------------------------------
        # 重要：
        # 失敗時不要覆蓋既有 chip.json
        # ----------------------------------------------------

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
