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

注意：

CMoney 頁面中的：

「5日集中」
「20日集中」
「家數差」

全部不使用。

本程式絕對不使用「20日集中」計算 main_force_20d。

============================================================
V5.1 修正
============================================================

V5.0 問題：

CMoney 首頁通常只顯示約 10 筆。

舊版錯誤地大量猜測：

?page=2
?page=3
offset=10
offset=20
limit=20

這些參數可能只是重新取得同一批資料。

因此 V5.1：

1. 先取得主頁
2. 解析主力買賣超
3. 從 HTML 真正尋找：
   - 查看更多
   - data-* 屬性
   - href
   - onclick
   - next / more / pagination
4. 只接受「實際新增日期」的延伸資料
5. 每次延伸後重新去重
6. 若延伸頁完全沒有新增日期，不再無限重複請求
7. 至少取得 20 個交易日才計算 20D
8. 不使用 API endpoint 探測
9. 不使用 5日集中
10. 不使用 20日集中
11. 不使用家數差

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
至少最近 20 個交易日主力買賣超
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

REQUEST_DELAY = 0.20

MIN_HISTORY = 20

MAX_MORE_ROUNDS = 8


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

        seen.add(
            symbol
        )

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

        # 不再用模糊的第二個數字判定。
        # 只接受日期後面具有合理表格結構的資料。
        for j in range(
            i + 1,
            min(
                i + 6,
                len(lines)
            )
        ):

            number = parse_number(
                lines[j]
            )

            if number is None:
                continue

            # 只作為最後 fallback。
            # 若下一行也是數字，通常：
            # 收盤價 → 買賣超
            if j + 1 < len(lines):

                force_value = parse_number(
                    lines[j + 1]
                )

                if force_value is not None:

                    result.append({
                        "date": date_text,
                        "main_force":
                            force_value
                    })

                    break

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
# URL 判定
# ============================================================

def is_cmoney_url(
    url
):

    try:

        parsed = urlparse(
            url
        )

        host = (
            parsed.netloc
            or ""
        ).lower()

        return (
            "cmoney.tw" in host
        )

    except Exception:

        return False


# ============================================================
# 從 HTML 找真正的查看更多
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

    def add_url(
        raw
    ):

        if not raw:
            return

        raw = str(
            raw
        ).strip()

        raw = (
            raw
            .replace(
                "\\/",
                "/"
            )
            .replace(
                "&amp;",
                "&"
            )
        )

        if raw.startswith(
            "javascript:"
        ):
            return

        if raw.startswith(
            "#"
        ):
            return

        absolute = urljoin(
            base_url,
            raw
        )

        if not is_cmoney_url(
            absolute
        ):
            return

        if absolute not in urls:

            urls.append(
                absolute
            )

    # --------------------------------------------------------
    # 1. href
    # --------------------------------------------------------

    for tag in soup.find_all(
        True
    ):

        href = tag.get(
            "href"
        )

        if not href:
            continue

        text = tag.get_text(
            " ",
            strip=True
        )

        href_lower = href.lower()

        if (
            "查看更多" in text
            or text.strip() == "更多"
            or text.strip() == "下一頁"
            or text.strip().lower() == "more"
            or "main-force" in href_lower
        ):

            add_url(
                href
            )

    # --------------------------------------------------------
    # 2. data-* 屬性
    # --------------------------------------------------------

    for tag in soup.find_all(
        True
    ):

        for attr, value in list(
            tag.attrs.items()
        ):

            if not str(
                attr
            ).lower().startswith(
                "data-"
            ):
                continue

            if isinstance(
                value,
                list
            ):
                value = " ".join(
                    value
                )

            value = str(
                value
            ).strip()

            if not value:
                continue

            lower = value.lower()

            if (
                "main-force" in lower
                or "main_force" in lower
                or "mainforce" in lower
                or "more" in lower
                or "next" in lower
                or "page" in lower
            ):

                # URL
                if (
                    value.startswith(
                        "/"
                    )
                    or value.startswith(
                        "http"
                    )
                ):

                    add_url(
                        value
                    )

    # --------------------------------------------------------
    # 3. onclick / data-action
    # --------------------------------------------------------

    for tag in soup.find_all(
        True
    ):

        for attr in [
            "onclick",
            "data-action",
            "data-url",
            "data-href",
            "data-link",
        ]:

            value = tag.get(
                attr
            )

            if not value:
                continue

            value = str(
                value
            )

            # 引號內 URL
            matches = re.findall(
                r"""['"]([^'"]+)['"]""",
                value
            )

            for match in matches:

                if (
                    "cmoney.tw" in match
                    or match.startswith(
                        "/"
                    )
                ):

                    add_url(
                        match
                    )

    # --------------------------------------------------------
    # 4. HTML 裡直接存在的完整 URL
    # --------------------------------------------------------

    absolute_urls = re.findall(
        r'https?://[^"\'\s<>]+',
        html,
        flags=re.I
    )

    for raw in absolute_urls:

        raw = raw.rstrip(
            " )]},;'\""
        )

        lower = raw.lower()

        if (
            "cmoney.tw" in lower
            and (
                "main-force" in lower
                or "main_force" in lower
                or "mainforce" in lower
            )
        ):

            add_url(
                raw
            )

    # --------------------------------------------------------
    # 5. 相對路徑
    # --------------------------------------------------------

    relative_urls = re.findall(
        r"""["'](/[^"']*(?:main-force|main_force|mainforce)[^"']*)["']""",
        html,
        flags=re.I
    )

    for raw in relative_urls:

        add_url(
            raw
        )

    # --------------------------------------------------------
    # 去除與主頁完全相同的 URL
    # --------------------------------------------------------

    normalized_base = base_url.rstrip(
        "/"
    )

    urls = [
        url
        for url in urls
        if url.rstrip(
            "/"
        ) != normalized_base
    ]

    return list(
        dict.fromkeys(
            urls
        )
    )


# ============================================================
# 驗證延伸頁是否真的有新日期
# ============================================================

def merge_history(
    current,
    incoming
):

    current = clean_history(
        current
    )

    incoming = clean_history(
        incoming
    )

    existing_dates = {
        row["date"]
        for row in current
    }

    added = 0

    for row in incoming:

        date = row["date"]

        if date in existing_dates:
            continue

        current.append(
            row
        )

        existing_dates.add(
            date
        )

        added += 1

    current = clean_history(
        current
    )

    return (
        current,
        added
    )


# ============================================================
# 取得至少 20D
# ============================================================

def fetch_20d_history(
    session,
    symbol
):

    html, page_url = request_page(
        session,
        symbol
    )

    history = parse_main_force_table(
        html
    )

    if not history:

        raise RuntimeError(
            "CMoney 頁面無法解析"
            "主力買賣超資料"
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
    # 只從實際 HTML 找延伸入口
    # --------------------------------------------------------

    pending_urls = discover_more_urls(
        html,
        page_url,
        symbol
    )

    log(
        f"   找到歷史延伸入口："
        f"{len(pending_urls)} 個"
    )

    tested_urls = set()

    no_new_rounds = 0

    # --------------------------------------------------------
    # 延伸抓取
    # --------------------------------------------------------

    for round_index in range(
        1,
        MAX_MORE_ROUNDS + 1
    ):

        if len(history) >= MIN_HISTORY:
            break

        if not pending_urls:
            break

        current_batch = pending_urls[
            :10
        ]

        pending_urls = pending_urls[
            10:
        ]

        round_added = 0

        log(
            f"   歷史延伸第 "
            f"{round_index} 輪："
            f"{len(history)} 筆"
        )

        for url in current_batch:

            if url in tested_urls:
                continue

            tested_urls.add(
                url
            )

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

                old_count = len(
                    history
                )

                (
                    history,
                    added
                ) = merge_history(
                    history,
                    page_history
                )

                if added > 0:

                    round_added += added

                    log(
                        f"      ✓ 新增 "
                        f"{added} 筆"
                    )

                    log(
                        f"      歷史："
                        f"{len(history)} 筆"
                    )

                # ------------------------------------------------
                # 延伸頁自己可能還有下一個入口
                # ------------------------------------------------

                nested_urls = (
                    discover_more_urls(
                        response.text,
                        url,
                        symbol
                    )
                )

                for nested in nested_urls:

                    if (
                        nested
                        not in tested_urls
                        and nested
                        not in pending_urls
                    ):

                        pending_urls.append(
                            nested
                        )

                # ------------------------------------------------
                # 若這個 URL 完全沒有新增日期，
                # 不允許把它當成成功延伸。
                # ------------------------------------------------

                if added == 0:

                    if len(history) == old_count:
                        pass

            except Exception:
                continue

            time.sleep(
                0.10
            )

            if len(history) >= MIN_HISTORY:
                break

        if round_added == 0:

            no_new_rounds += 1

        else:

            no_new_rounds = 0

        # --------------------------------------------------------
        # 連續沒有新增資料，停止盲目重抓
        # --------------------------------------------------------

        if no_new_rounds >= 2:

            break

    # --------------------------------------------------------
    # 最終清理
    # --------------------------------------------------------

    history = clean_history(
        history
    )

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
# 計算 1 / 5 / 10 / 20
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

    required = [
        "main_force_1d",
        "main_force_5d",
        "main_force_10d",
        "main_force_20d",
    ]

    if all(
        data.get(
            key
        ) is not None
        for key in required
    ):

        return "complete"

    if data.get(
        "main_force_1d"
    ) is not None:

        return "partial"

    return "insufficient"


# ============================================================
# Fetch all
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

        log(
            f"[{index}/{total}] "
            f"{symbol} {name}"
        )

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

        try:

            history = fetch_20d_history(
                session,
                symbol
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

            status = record[
                "status"
            ]

            if status == "complete":

                complete += 1

            elif status == "partial":

                partial += 1

            else:

                insufficient += 1

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

        except Exception as exc:

            insufficient += 1

            record[
                "error"
            ] = str(exc)

            log(
                f"   ⚠️ 取得失敗："
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

        "schema_version": VERSION,

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

            "unit":
                "張",

            "positive":
                "主力買超",

            "negative":
                "主力賣超",

            "excluded_fields": [
                "家數差",
                "5日集中",
                "20日集中",
            ],
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

            # ------------------------------------------------
            # 再驗證 5D / 10D / 20D
            # ------------------------------------------------

            values = [
                item["main_force"]
                for item in history[:20]
            ]

            expected_5d = round(
                sum(values[:5]),
                2
            )

            expected_10d = round(
                sum(values[:10]),
                2
            )

            expected_20d = round(
                sum(values[:20]),
                2
            )

            if (
                record.get(
                    "main_force_5d"
                )
                != expected_5d
            ):

                raise RuntimeError(
                    f"{symbol} "
                    "5D 加總驗證失敗"
                )

            if (
                record.get(
                    "main_force_10d"
                )
                != expected_10d
            ):

                raise RuntimeError(
                    f"{symbol} "
                    "10D 加總驗證失敗"
                )

            if (
                record.get(
                    "main_force_20d"
                )
                != expected_20d
            ):

                raise RuntimeError(
                    f"{symbol} "
                    "20D 加總驗證失敗"
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