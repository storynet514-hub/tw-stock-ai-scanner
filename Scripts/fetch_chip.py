#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V5.0.1

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

「20日集中」

不是主力 20 日買賣超。

本程式絕對不使用「20日集中」
計算 main_force_20d。

============================================================
V5.0.1 修正
============================================================

V5.0 原版的延伸 URL 發現邏輯過於寬鬆。

可能把：

?s=insider-transactions

等非主力資料頁面當成主力延伸資料。

另外：

page / offset / limit 等 pagination
有些會 HTTP 200，
但實際回傳的仍然是同一批資料。

V5.0.1：

1. 延伸 URL 必須屬於主力資料。
2. 排除 insider-transactions。
3. 排除非 main-force 查詢。
4. 驗證延伸頁是否真的新增日期。
5. 完全重複的頁面不再加入。
6. 保留 V5.0 原本計算公式。
7. 保留 chip.json 結構。

============================================================
輸出
============================================================

Data/chip.json
"""

from __future__ import annotations

import json
import re
import sys
import time

from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs, urlencode

import requests
from bs4 import BeautifulSoup


# ============================================================
# 基本設定
# ============================================================

VERSION = "V5.0.1"

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
        return float(match.group(0))
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
# Header
# ============================================================

def normalize_header(text):

    if text is None:
        return ""

    text = str(text)

    text = text.replace("\n", "")
    text = text.replace("\r", "")
    text = text.replace(" ", "")
    text = text.replace("\u3000", "")

    return text.strip()


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
# URL 安全驗證
# ============================================================

def is_valid_main_force_url(
    url,
    symbol
):

    try:
        parsed = urlparse(url)

        host = parsed.netloc.lower()

        path = parsed.path.lower()

        query = parse_qs(
            parsed.query
        )

    except Exception:
        return False

    # --------------------------------------------------------
    # 只能是 CMoney
    # --------------------------------------------------------

    if not (
        host.endswith("cmoney.tw")
        or host.endswith("cmoney.tw.")
    ):
        return False

    # --------------------------------------------------------
    # URL 必須包含指定股票
    # --------------------------------------------------------

    if f"/stock/{symbol.lower()}" not in path:
        return False

    # --------------------------------------------------------
    # 明確排除其他籌碼頁
    # --------------------------------------------------------

    forbidden_keywords = [
        "insider-transactions",
        "institutional",
        "institution",
        "foreign",
        "dealer",
        "margin",
        "short",
        "revenue",
        "fundamental",
        "financial",
        "valuation",
    ]

    lowered = url.lower()

    for keyword in forbidden_keywords:

        if keyword in lowered:
            return False

    # --------------------------------------------------------
    # query 必須是主力資料
    # --------------------------------------------------------

    if "s" in query:

        values = [
            str(v).lower()
            for v in query["s"]
        ]

        if not any(
            value == "main-force"
            for value in values
        ):
            return False

    # --------------------------------------------------------
    # 如果 URL 沒有 s=，
    # 仍允許同一主力頁的 pagination。
    # --------------------------------------------------------

    if "s" not in query:

        if (
            "main-force" not in lowered
            and "main_force" not in lowered
        ):
            return False

    return True


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

    tables = soup.find_all("table")

    for table in tables:

        rows = table.find_all("tr")

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
                is_main_force_header(h)
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

    for i, line in enumerate(lines):

        date_text = normalize_date(line)

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

            if len(numeric_values) >= 2:
                break

        if len(numeric_values) < 2:
            continue

        result.append({
            "date": date_text,
            "main_force": numeric_values[1]
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
        return clean_history(rows)

    rows = parse_text_fallback(
        soup
    )

    if rows:
        return clean_history(rows)

    return []


# ============================================================
# Clean history
# ============================================================

def clean_history(
    rows
):

    unique = {}

    for row in rows:

        date = row.get("date")

        value = row.get("main_force")

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

        unique[date] = float(value)

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
# URL 參數處理
# ============================================================

def add_query_parameter(
    base_url,
    key,
    value
):

    parsed = urlparse(base_url)

    query = parse_qs(
        parsed.query,
        keep_blank_values=True
    )

    query[key] = [str(value)]

    new_query = urlencode(
        query,
        doseq=True
    )

    return parsed._replace(
        query=new_query
    ).geturl()


# ============================================================
# 找「查看更多」線索
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

        if not is_valid_main_force_url(
            absolute,
            symbol
        ):
            return

        if absolute not in urls:
            urls.append(absolute)

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

        href = tag.get("href")

        if not href:
            continue

        lowered_href = href.lower()

        if (
            "更多" in text
            or "查看更多" in text
            or "more" in lowered_href
            or "page" in lowered_href
            or "offset" in lowered_href
            or "limit" in lowered_href
        ):
            add_url(href)

    # --------------------------------------------------------
    # HTML 中的 URL
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
                symbol.lower() in raw.lower()
                or "main-force" in raw.lower()
                or "main_force" in raw.lower()
            ):
                add_url(raw)

    return urls


# ============================================================
# 建立 pagination URL
# ============================================================

def build_pagination_urls(
    base_url,
    symbol
):

    candidates = []

    # --------------------------------------------------------
    # 基本安全檢查
    # --------------------------------------------------------

    if not is_valid_main_force_url(
        base_url,
        symbol
    ):
        return []

    # --------------------------------------------------------
    # page / pageNo / pageIndex / p
    # --------------------------------------------------------

    for key in [
        "page",
        "pageNo",
        "pageIndex",
        "p"
    ]:

        for value in [
            2,
            3,
            4
        ]:

            url = add_query_parameter(
                base_url,
                key,
                value
            )

            if is_valid_main_force_url(
                url,
                symbol
            ):
                candidates.append(url)

    # --------------------------------------------------------
    # offset
    # --------------------------------------------------------

    for offset in [
        10,
        20,
        30,
        40,
        50
    ]:

        url = add_query_parameter(
            base_url,
            "offset",
            offset
        )

        if is_valid_main_force_url(
            url,
            symbol
        ):
            candidates.append(url)

    # --------------------------------------------------------
    # limit
    # --------------------------------------------------------

    for limit in [
        20,
        30,
        50,
        100
    ]:

        url = add_query_parameter(
            base_url,
            "limit",
            limit
        )

        if is_valid_main_force_url(
            url,
            symbol
        ):
            candidates.append(url)

    return list(
        dict.fromkeys(candidates)
    )


# ============================================================
# 判斷延伸資料是否真的新增
# ============================================================

def merge_new_history(
    history,
    page_history
):

    if not page_history:
        return history, 0

    before_dates = {
        row["date"]
        for row in history
    }

    new_rows = []

    for row in page_history:

        date = row.get("date")

        if not date:
            continue

        if date in before_dates:
            continue

        new_rows.append(row)

    if not new_rows:
        return history, 0

    merged = history + new_rows

    merged = clean_history(merged)

    return merged, len(new_rows)


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

    log(
        f"   首頁歷史筆數："
        f"{len(history)}"
    )

    if len(history) >= MIN_HISTORY:

        return history[:MIN_HISTORY]

    # --------------------------------------------------------
    # 第一階段：
    # 找真正的 main-force 延伸 URL
    # --------------------------------------------------------

    more_urls = discover_more_urls(
        html,
        page_url,
        symbol
    )

    log(
        f"   發現有效主力延伸 URL："
        f"{len(more_urls)}"
    )

    # --------------------------------------------------------
    # 第二階段：
    # 安全組合 URL
    # --------------------------------------------------------

    urls_to_test = []

    for url in more_urls:

        if is_valid_main_force_url(
            url,
            symbol
        ):
            if url not in urls_to_test:
                urls_to_test.append(url)

    pagination_urls = build_pagination_urls(
        page_url,
        symbol
    )

    for url in pagination_urls:

        if url not in urls_to_test:
            urls_to_test.append(url)

    # --------------------------------------------------------
    # 防止重複請求
    # --------------------------------------------------------

    urls_to_test = list(
        dict.fromkeys(
            urls_to_test
        )
    )

    seen_dates = {
        row["date"]
        for row in history
    }

    # --------------------------------------------------------
    # 逐頁嘗試
    # --------------------------------------------------------

    for round_index, url in enumerate(
        urls_to_test,
        start=1
    ):

        if round_index > MAX_FETCH_ROUNDS * 10:
            break

        if not is_valid_main_force_url(
            url,
            symbol
        ):
            log(
                f"   ❌ 略過非主力 URL："
                f"{url}"
            )
            continue

        try:

            response = session.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT
            )

            if response.status_code != 200:
                continue

            page_history = parse_main_force_table(
                response.text
            )

            if not page_history:
                continue

            # ------------------------------------------------
            # 關鍵修正：
            # 必須真的新增日期才算取得新資料
            # ------------------------------------------------

            history, added = merge_new_history(
                history,
                page_history
            )

            if added > 0:

                log(
                    f"   ✓ 延伸取得 {added} 筆"
                )

                log(
                    f"   目前歷史："
                    f"{len(history)} 筆"
                )

                seen_dates = {
                    row["date"]
                    for row in history
                }

            else:

                log(
                    "   ↪ 重複頁面，"
                    "不加入歷史："
                    f"{url}"
                )

            # ------------------------------------------------
            # 已經足夠
            # ------------------------------------------------

            if len(history) >= MIN_HISTORY:

                log(
                    "   ✓ 已取得至少 "
                    "20 個交易日"
                )

                return history[:MIN_HISTORY]

        except Exception as exc:

            log(
                f"   ↪ 延伸頁失敗："
                f"{exc}"
            )

        time.sleep(0.10)

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

    return history[:MIN_HISTORY]


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
        "history_count": len(values),
    }

    if len(values) >= 1:

        result["main_force_1d"] = round(
            sum(values[:1]),
            2
        )

    if len(values) >= 5:

        result["main_force_5d"] = round(
            sum(values[:5]),
            2
        )

    if len(values) >= 10:

        result["main_force_10d"] = round(
            sum(values[:10]),
            2
        )

    if len(values) >= 20:

        result["main_force_20d"] = round(
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

    if (
        data.get("main_force_1d") is not None
        and data.get("main_force_5d") is not None
        and data.get("main_force_10d") is not None
        and data.get("main_force_20d") is not None
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

    total = len(stocks)

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

        symbol = stock["symbol"]
        name = stock["name"]

        log(
            f"[{index}/{total}] "
            f"{symbol} {name}"
        )

        record = {
            "symbol": symbol,
            "name": name,
            "market": stock["market"],
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

            record.update(periods)

            record["history"] = history[:20]

            record["status"] = get_status(
                record
            )

            status = record["status"]

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

            record["error"] = str(exc)

            log(
                f"   ⚠️ 取得失敗："
                f"{exc}"
            )

        results[symbol] = record

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

    log(f"Universe：{total}")
    log(f"完整：{complete}")
    log(f"部分：{partial}")
    log(f"不足：{insufficient}")

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

    if len(verify_stocks) != len(
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
            "✓ fetch_chip.py "
            f"{VERSION} 執行完成"
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
            f"❌ fetch_chip.py "
            f"{VERSION} 執行失敗"
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