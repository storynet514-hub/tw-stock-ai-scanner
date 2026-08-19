#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V5.1

============================================================
V5.1 效能優化版
============================================================

基準版本：
fetch_chip.py V5.0

V5.0 已驗證：
- 全市場約 1985 檔
- CMoney 主力進出
- 成功取得 1D / 5D / 10D / 20D
- 20D 使用「買賣超」加總
- 不使用「20日集中」

V5.1 只做效能優化，不改核心資料定義。

============================================================
核心資料
============================================================

main_force_1d
main_force_5d
main_force_10d
main_force_20d

單位：
張

正數：
主力買超

負數：
主力賣超

============================================================
20D 定義
============================================================

主力20日：

最近20個交易日：

每日「買賣超」

加總。

絕對不使用：

CMoney「20日集中」

============================================================
V5.1 效能優化
============================================================

1. 多執行緒並行處理股票
2. 每支股票取得20D後立即停止
3. 優先使用頁面發現的延伸 URL
4. 減少無效 pagination 探測
5. 保留既有 chip.json
6. 若既有資料已有完整20D，可直接沿用
7. 單一股票失敗不影響其他股票
8. 最後一次性安全寫入 chip.json

============================================================
重要
============================================================

正式模式：

1985 檔全部掃描。

測試模式：

將 TEST_MODE 改成 True，
並設定 TEST_LIMIT = 5。

預設：

TEST_MODE = False

因此正式執行仍然是全市場。

============================================================
"""

from __future__ import annotations

import json
import re
import sys
import time

from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)

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


# ============================================================
# 測試設定
# ============================================================

# ------------------------------------------------------------
# 正式模式：
# False = 全市場
# ------------------------------------------------------------

TEST_MODE = False

# ------------------------------------------------------------
# 測試模式只抓前5檔
# ------------------------------------------------------------

TEST_LIMIT = 5


# ============================================================
# 效能設定
# ============================================================

# ------------------------------------------------------------
# 並行股票數量
#
# 8 是保守起始值。
#
# 如果 CMoney 沒有明顯限流，
# 後續可以再測 10 / 12。
# ------------------------------------------------------------

MAX_WORKERS = 8


# ------------------------------------------------------------
# HTTP timeout
# ------------------------------------------------------------

REQUEST_TIMEOUT = 20


# ------------------------------------------------------------
# 最少需要20個交易日
# ------------------------------------------------------------

MIN_HISTORY = 20


# ------------------------------------------------------------
# 每支股票最多嘗試多少延伸 URL
#
# V5.0 的：
# MAX_FETCH_ROUNDS * 10
# = 最多60次
#
# V5.1 改成明確限制。
# ------------------------------------------------------------

MAX_EXTENSION_REQUESTS = 18


# ------------------------------------------------------------
# 股票之間不再使用0.20秒串行等待。
#
# 並行模式下：
# WORKER_DELAY 控制同一 worker 在完成股票後
# 的短暫間隔。
# ------------------------------------------------------------

WORKER_DELAY = 0.05


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

    items = data.get(
        "items",
        []
    )

    if not isinstance(
        items,
        list
    ):

        raise RuntimeError(
            "universe.json items 不是 list"
        )

    stocks = []

    seen = set()

    for item in items:

        if not isinstance(
            item,
            dict
        ):
            continue

        symbol = item.get(
            "code"
        )

        if symbol is None:

            symbol = item.get(
                "symbol"
            )

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

    # --------------------------------------------------------
    # 測試模式
    # --------------------------------------------------------

    original_count = len(stocks)

    if TEST_MODE:

        stocks = stocks[:TEST_LIMIT]

        log(
            "⚠️ 測試模式已啟用"
        )

        log(
            f"測試數量：{len(stocks)}"
        )

    else:

        log(
            "✓ 正式模式：全市場掃描"
        )

    log(
        f"Universe 股票數量："
        f"{len(stocks)}"
    )

    if TEST_MODE:

        log(
            f"原始 Universe："
            f"{original_count}"
        )

    return stocks


# ============================================================
# Number
# ============================================================

def parse_number(text):

    if text is None:
        return None

    text = str(
        text
    ).strip()

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
# Date
# ============================================================

DATE_PATTERNS = [
    r"\d{4}/\d{1,2}/\d{1,2}",
    r"\d{4}-\d{1,2}-\d{1,2}",
]


def normalize_date(text):

    if text is None:
        return None

    text = str(
        text
    ).strip()

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
# Header
# ============================================================

def normalize_header(text):

    if text is None:
        return ""

    text = str(
        text
    )

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


def is_main_force_header(text):

    header = normalize_header(
        text
    )

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

            return (
                html,
                url
            )

        except Exception as exc:

            last_error = exc

    if last_error:
        raise last_error

    raise RuntimeError(
        "無法取得 CMoney 頁面"
    )


# ============================================================
# Table parser
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

        header_cells = None

        for tr in rows[:15]:

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

            has_date = any(
                h == "日期"
                or "日期" in h
                for h in headers
            )

            has_force = any(
                is_main_force_header(
                    h
                )
                for h in headers
            )

            if (
                has_date
                and has_force
            ):

                header_cells = cells

                break

        if header_cells is None:
            continue

        headers = [
            normalize_header(
                cell.get_text(
                    " ",
                    strip=True
                )
            )
            for cell in header_cells
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

        for tr in rows:

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
                "main_force": force_value
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
                numeric_values[1]
        })

    return result


# ============================================================
# Parse history
# ============================================================

def parse_main_force_table(
    html
):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    rows = parse_table_with_header(
        soup
    )

    if rows:

        return clean_history(
            rows
        )

    rows = parse_text_fallback(
        soup
    )

    if rows:

        return clean_history(
            rows
        )

    return []


# ============================================================
# Clean history
# ============================================================

def clean_history(
    rows
):

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
                "%Y/%m/%d"
            )

        except Exception:

            continue

        unique[date] = float(
            value
        )

    result = []

    for date, value in unique.items():

        result.append({
            "date": date,
            "main_force": value
        })

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
# 找查看更多線索
# ============================================================

def discover_more_urls(
    html,
    base_url,
    symbol
):

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

        if (
            value.startswith(
                "javascript:"
            )
            or value.startswith("#")
        ):
            return

        absolute = urljoin(
            base_url,
            value
        )

        parsed = urlparse(
            absolute
        )

        if "cmoney.tw" not in parsed.netloc:
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

        text = tag.get_text(
            " ",
            strip=True
        )

        href = tag.get(
            "href"
        )

        if (
            href
            and (
                "更多" in text
                or "查看更多" in text
                or "more" in href.lower()
                or "page" in href.lower()
                or "offset" in href.lower()
                or "limit" in href.lower()
            )
        ):

            add_url(
                href
            )

    # --------------------------------------------------------
    # HTML URL
    # --------------------------------------------------------

    patterns = [
        r'https?://[^"\']+',
        r'/(?:api|forum|service|stock)[^"\']+',
    ]

    for pattern in patterns:

        for raw in re.findall(
            pattern,
            html,
            flags=re.I
        ):

            raw = (
                raw
                .replace(
                    "\\/",
                    "/"
                )
                .rstrip(
                    " )},;'\""
                )
            )

            if (
                symbol in raw
                or "main-force" in raw
                or "chip" in raw.lower()
                or "force" in raw.lower()
            ):

                add_url(
                    raw
                )

    return urls


# ============================================================
# Pagination
# ============================================================

def build_pagination_urls(
    base_url
):

    candidates = []

    # --------------------------------------------------------
    # 常見 page
    # --------------------------------------------------------

    for key in [
        "page",
        "pageNo",
        "pageIndex",
        "p"
    ]:

        for value in [
            2,
            3
        ]:

            separator = (
                "&"
                if "?" in base_url
                else "?"
            )

            candidates.append(
                f"{base_url}"
                f"{separator}"
                f"{key}={value}"
            )

    # --------------------------------------------------------
    # offset
    # --------------------------------------------------------

    for offset in [
        10,
        20,
        30
    ]:

        separator = (
            "&"
            if "?" in base_url
            else "?"
        )

        candidates.append(
            f"{base_url}"
            f"{separator}"
            f"offset={offset}"
        )

    # --------------------------------------------------------
    # limit
    # --------------------------------------------------------

    for limit in [
        20,
        30,
        50
    ]:

        separator = (
            "&"
            if "?" in base_url
            else "?"
        )

        candidates.append(
            f"{base_url}"
            f"{separator}"
            f"limit={limit}"
        )

    return list(
        dict.fromkeys(
            candidates
        )
    )


# ============================================================
# 取得20D
# ============================================================

def fetch_20d_history(
    session,
    symbol,
    verbose=False
):

    # --------------------------------------------------------
    # 首頁
    # --------------------------------------------------------

    html, page_url = request_page(
        session,
        symbol
    )

    history = parse_main_force_table(
        html
    )

    if verbose:

        log(
            f"   首頁歷史筆數："
            f"{len(history)}"
        )

    # --------------------------------------------------------
    # 首頁直接已有20D
    # --------------------------------------------------------

    if len(history) >= MIN_HISTORY:

        return history[:MIN_HISTORY]

    # --------------------------------------------------------
    # 發現延伸 URL
    # --------------------------------------------------------

    more_urls = discover_more_urls(
        html,
        page_url,
        symbol
    )

    if verbose:

        log(
            f"   發現延伸 URL："
            f"{len(more_urls)}"
        )

    # --------------------------------------------------------
    # 優先使用真正發現的 URL
    # --------------------------------------------------------

    urls_to_test = []

    for url in more_urls:

        if url not in urls_to_test:

            urls_to_test.append(
                url
            )

    # --------------------------------------------------------
    # 不足才補常見 pagination
    # --------------------------------------------------------

    for url in build_pagination_urls(
        page_url
    ):

        if url not in urls_to_test:

            urls_to_test.append(
                url
            )

    # --------------------------------------------------------
    # 限制無效探測數量
    # --------------------------------------------------------

    urls_to_test = urls_to_test[
        :MAX_EXTENSION_REQUESTS
    ]

    seen_dates = {
        row["date"]
        for row in history
    }

    # --------------------------------------------------------
    # 延伸抓取
    # --------------------------------------------------------

    for url in urls_to_test:

        try:

            response = session.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT
            )

            if response.status_code != 200:
                continue

            page_history = (
                parse_main_force_table(
                    response.text
                )
            )

            if not page_history:
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

                history.append(
                    row
                )

                seen_dates.add(
                    date
                )

            history = clean_history(
                history
            )

            added = (
                len(history)
                - before
            )

            if verbose and added > 0:

                log(
                    f"   ✓ 延伸取得 "
                    f"{added} 筆"
                )

                log(
                    f"   目前歷史："
                    f"{len(history)} 筆"
                )

            # ------------------------------------------------
            # 20D 達成，立即停止
            # ------------------------------------------------

            if len(history) >= MIN_HISTORY:

                if verbose:

                    log(
                        "   ✓ 已取得至少20個交易日"
                    )

                return history[
                    :MIN_HISTORY
                ]

        except Exception:
            pass

    # --------------------------------------------------------
    # 最終判定
    # --------------------------------------------------------

    history = clean_history(
        history
    )

    if len(history) < MIN_HISTORY:

        raise RuntimeError(
            "CMoney 目前可取得的"
            f"主力買賣超只有 "
            f"{len(history)} 筆，"
            f"不足 {MIN_HISTORY} 個交易日。"
        )

    return history[
        :MIN_HISTORY
    ]


# ============================================================
# 計算1 / 5 / 10 / 20
# ============================================================

def calculate_periods(
    history
):

    values = [
        row["main_force"]
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
            sum(values[:1]),
            2
        )

    if len(values) >= 5:

        result[
            "main_force_5d"
        ] = round(
            sum(values[:5]),
            2
        )

    if len(values) >= 10:

        result[
            "main_force_10d"
        ] = round(
            sum(values[:10]),
            2
        )

    if len(values) >= 20:

        result[
            "main_force_20d"
        ] = round(
            sum(values[:20]),
            2
        )

    return result


# ============================================================
# Status
# ============================================================

def get_status(
    data
):

    if all(
        data.get(key) is not None
        for key in [
            "main_force_1d",
            "main_force_5d",
            "main_force_10d",
            "main_force_20d",
        ]
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
# 讀取既有完整 chip.json
# ============================================================

def load_existing_chip():

    if not CHIP_FILE.exists():

        return {}

    try:

        with CHIP_FILE.open(
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        stocks = data.get(
            "stocks",
            {}
        )

        if not isinstance(
            stocks,
            dict
        ):

            return {}

        return stocks

    except Exception:

        return {}


# ============================================================
# 判斷既有資料是否可直接使用
# ============================================================

def existing_record_is_complete(
    record
):

    if not isinstance(
        record,
        dict
    ):

        return False

    history = record.get(
        "history",
        []
    )

    if not isinstance(
        history,
        list
    ):

        return False

    if len(history) < 20:

        return False

    for key in [
        "main_force_1d",
        "main_force_5d",
        "main_force_10d",
        "main_force_20d",
    ]:

        if record.get(key) is None:

            return False

    return True


# ============================================================
# 單支股票 worker
# ============================================================

def process_stock(
    stock,
    existing_record=None
):

    symbol = stock[
        "symbol"
    ]

    name = stock[
        "name"
    ]

    record = {
        "symbol": symbol,
        "name": name,
        "market": stock[
            "market"
        ],
        "source": "CMoney",

        "main_force_1d": None,
        "main_force_5d": None,
        "main_force_10d": None,
        "main_force_20d": None,

        "history_count": 0,

        "status": "insufficient",

        "history": [],

        "error": None,
    }

    # --------------------------------------------------------
    # 如果既有 chip.json 已經有完整20D
    # 直接沿用，不重新抓。
    # --------------------------------------------------------

    if existing_record_is_complete(
        existing_record
    ):

        record = dict(
            existing_record
        )

        record[
            "symbol"
        ] = symbol

        record[
            "name"
        ] = name

        record[
            "market"
        ] = stock[
            "market"
        ]

        record[
            "_reused"
        ] = True

        return record

    # --------------------------------------------------------
    # 新抓取
    # --------------------------------------------------------

    session = requests.Session()

    session.headers.update(
        HEADERS
    )

    try:

        history = fetch_20d_history(
            session,
            symbol,
            verbose=False
        )

        periods = calculate_periods(
            history
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

        record[
            "_reused"
        ] = False

    except Exception as exc:

        record[
            "error"
        ] = str(exc)

        record[
            "_reused"
        ] = False

    finally:

        try:
            session.close()
        except Exception:
            pass

    time.sleep(
        WORKER_DELAY
    )

    return record


# ============================================================
# Fetch all - V5.1 並行版
# ============================================================

def fetch_all(
    stocks
):

    section(
        "開始取得主力買賣超"
    )

    total = len(
        stocks
    )

    log(
        f"待處理股票：{total}"
    )

    log(
        f"並行 Workers：{MAX_WORKERS}"
    )

    existing = load_existing_chip()

    if existing:

        log(
            f"既有 chip.json："
            f"{len(existing)} 檔"
        )

    else:

        log(
            "既有 chip.json：無可用資料"
        )

    results = {}

    complete = 0
    partial = 0
    insufficient = 0
    reused = 0

    completed_count = 0

    # --------------------------------------------------------
    # 建立工作
    # --------------------------------------------------------

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        future_map = {}

        for stock in stocks:

            symbol = stock[
                "symbol"
            ]

            old_record = existing.get(
                symbol
            )

            future = executor.submit(
                process_stock,
                stock,
                old_record
            )

            future_map[
                future
            ] = stock

        # ----------------------------------------------------
        # 哪一支完成就立即收結果
        # ----------------------------------------------------

        for future in as_completed(
            future_map
        ):

            stock = future_map[
                future
            ]

            symbol = stock[
                "symbol"
            ]

            name = stock[
                "name"
            ]

            completed_count += 1

            try:

                record = future.result()

            except Exception as exc:

                record = {
                    "symbol": symbol,
                    "name": name,
                    "market": stock[
                        "market"
                    ],
                    "source": "CMoney",

                    "main_force_1d": None,
                    "main_force_5d": None,
                    "main_force_10d": None,
                    "main_force_20d": None,

                    "history_count": 0,

                    "status": "insufficient",

                    "history": [],

                    "error": str(exc),

                    "_reused": False,
                }

            results[
                symbol
            ] = record

            status = record.get(
                "status"
            )

            if status == "complete":

                complete += 1

            elif status == "partial":

                partial += 1

            else:

                insufficient += 1

            if record.get(
                "_reused"
            ):

                reused += 1

            # ------------------------------------------------
            # 進度
            # ------------------------------------------------

            log(
                f"[{completed_count}/{total}] "
                f"{symbol} {name}"
                f" | "
                f"1D={record.get('main_force_1d')}"
                f" "
                f"5D={record.get('main_force_5d')}"
                f" "
                f"10D={record.get('main_force_10d')}"
                f" "
                f"20D={record.get('main_force_20d')}"
                f" "
                f"歷史={record.get('history_count', 0)}"
                f" "
                f"{'♻️沿用' if record.get('_reused') else ''}"
            )

    return (
        results,
        complete,
        partial,
        insufficient,
        reused
    )


# ============================================================
# Validate
# ============================================================

def validate(
    results,
    total,
    complete,
    partial,
    insufficient,
    reused
):

    section(
        "籌碼資料驗證"
    )

    valid_1d = 0
    valid_5d = 0
    valid_10d = 0
    valid_20d = 0

    for record in results.values():

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
        f"既有完整資料沿用：{reused}"
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
    insufficient,
    reused
):

    section(
        "寫入 Data/chip.json"
    )

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    now = datetime.now()

    # --------------------------------------------------------
    # 移除內部欄位
    # --------------------------------------------------------

    clean_results = {}

    for symbol, record in results.items():

        record = dict(
            record
        )

        record.pop(
            "_reused",
            None
        )

        clean_results[
            symbol
        ] = record

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

        "definition": {

            "main_force":
                "CMoney 主力進出之買賣超",

            "main_force_5d":
                "最近5個交易日主力買賣超加總",

            "main_force_10d":
                "最近10個交易日主力買賣超加總",

            "main_force_20d":
                "最近20個交易日主力買賣超加總",

            "NOT_main_force_20d":
                "CMoney 20日集中不是主力20日買賣超",

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

            "reused_existing":
                reused,
        },

        "performance": {

            "version":
                VERSION,

            "workers":
                MAX_WORKERS,

            "test_mode":
                TEST_MODE,

            "test_limit":
                TEST_LIMIT,
        },

        "stocks":
            clean_results,
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

    # --------------------------------------------------------
    # 寫入後驗證
    # --------------------------------------------------------

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

    for symbol, record in verify_stocks.items():

        history = record.get(
            "history",
            []
        )

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

    log(
        f"並行 Workers：{MAX_WORKERS}"
    )

    log(
        f"測試模式："
        f"{'ON' if TEST_MODE else 'OFF'}"
    )

    try:

        stocks = load_universe()

        (
            results,
            complete,
            partial,
            insufficient,
            reused
        ) = fetch_all(
            stocks
        )

        validate(
            results,
            len(stocks),
            complete,
            partial,
            insufficient,
            reused
        )

        save_chip(
            results,
            len(stocks),
            complete,
            partial,
            insufficient,
            reused
        )

        elapsed = (
            time.time()
            - start_time
        )

        log("")
        log("=" * 72)

        log(
            "✓ fetch_chip.py 執行完成"
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
            f"既有資料沿用：{reused}"
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
            "❌ fetch_chip.py 執行失敗"
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
