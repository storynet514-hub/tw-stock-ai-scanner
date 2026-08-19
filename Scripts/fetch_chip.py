#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V6.0

============================================================
核心功能
============================================================

取得：

1. 每日主力買賣超
2. 主力 5 日買賣超
3. 主力 10 日買賣超
4. 主力 20 日買賣超

資料來源：

CMoney「主力進出」公開頁面。

============================================================
重要定義
============================================================

本程式的「主力」：

不是三大法人。

不是：

- 外資
- 投信
- 自營商

而是 CMoney「主力進出」頁面的：

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

CMoney「20日集中」
不是主力20日買賣超。

本程式絕對不使用「20日集中」。

============================================================
V6.0 核心架構
============================================================

本版本解決 V5.0 的主要問題：

V5.0：

每次執行都嘗試：

- 延伸 URL
- pagination
- page
- offset
- limit
- API 線索

這會造成：

1985 檔 × 多次 HTTP request

導致：

- 執行時間過長
- CMoney 請求數暴增
- 容易被限制
- 不必要的歷史資料重抓
- GitHub Actions 執行時間增加

V6.0：

採用「增量歷史資料」架構。

------------------------------------------------------------

第一次建立 chip.json：

若沒有舊資料：

    抓取 CMoney 最新頁面
    ↓
    若頁面本身不足20D
    ↓
    只對需要補歷史的股票進行有限補齊
    ↓
    建立20D history

------------------------------------------------------------

之後每日執行：

    讀取既有 chip.json
    ↓
    每檔只抓一次 CMoney 最新頁面
    ↓
    取得最新資料
    ↓
    與舊 history 合併
    ↓
    去除重複日期
    ↓
    排序
    ↓
    保留最近20D
    ↓
    重新計算1D/5D/10D/20D

因此：

已經有20D資料的股票：

不重新抓20D。

不重新探測API。

不重新探測pagination。

不重新建立整段歷史。

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

import requests
from bs4 import BeautifulSoup


# ============================================================
# 基本設定
# ============================================================

VERSION = "V6.0"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

CHIP_FILE = DATA_DIR / "chip.json"

REQUEST_TIMEOUT = 30

REQUEST_DELAY = 0.20

MIN_HISTORY = 20


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
# 讀取舊 chip.json
# ============================================================

def load_existing_chip():

    if not CHIP_FILE.exists():

        log(
            "   舊 chip.json：不存在"
        )

        return {}

    try:

        with CHIP_FILE.open(
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        if not isinstance(
            data,
            dict
        ):

            log(
                "   ⚠️ 舊 chip.json 格式錯誤，"
                "視為無舊資料"
            )

            return {}

        stocks = data.get(
            "stocks",
            {}
        )

        if not isinstance(
            stocks,
            dict
        ):

            log(
                "   ⚠️ 舊 chip.json stocks 格式錯誤"
            )

            return {}

        log(
            f"   舊 chip.json 股票資料："
            f"{len(stocks)}"
        )

        return stocks

    except Exception as exc:

        log(
            f"   ⚠️ 讀取舊 chip.json 失敗："
            f"{exc}"
        )

        return {}


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


# ============================================================
# 主力買賣超 Header
# ============================================================

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

            return html

        except Exception as exc:

            last_error = exc

    if last_error:

        raise last_error

    raise RuntimeError(
        "無法取得 CMoney 頁面"
    )


# ============================================================
# Table Parser
#
# 正確結構：
#
# 日期 | 收盤價 | 買賣超 | 家數差 | 5日集中 | 20日集中
#
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
# 嚴格文字備援
#
# 只在 table header 無法解析時使用。
#
# CMoney：
#
# 日期
# 收盤價
# 買賣超
#
# 第二個數字才視為買賣超。
#
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
                i + 7,
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

        force_value = numeric_values[1]

        result.append({
            "date": date_text,
            "main_force": force_value
        })

    return result


# ============================================================
# Parse
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

        if not isinstance(
            row,
            dict
        ):
            continue

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
# 合併歷史
# ============================================================

def merge_history(
    old_history,
    new_history
):

    combined = []

    if isinstance(
        old_history,
        list
    ):

        combined.extend(
            old_history
        )

    if isinstance(
        new_history,
        list
    ):

        combined.extend(
            new_history
        )

    combined = clean_history(
        combined
    )

    return combined[
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
# Fetch all
# ============================================================

def fetch_all(
    stocks,
    old_stocks
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

    reused_history = 0
    rebuilt_history = 0

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

        old_record = old_stocks.get(
            symbol,
            {}
        )

        if not isinstance(
            old_record,
            dict
        ):

            old_record = {}

        old_history = old_record.get(
            "history",
            []
        )

        if not isinstance(
            old_history,
            list
        ):

            old_history = []

        old_history = clean_history(
            old_history
        )

        record = {
            "symbol": symbol,
            "name": name,
            "market": stock.get(
                "market",
                ""
            ),
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

            # ------------------------------------------------
            # 每檔只抓一次 CMoney 最新頁面
            # ------------------------------------------------

            html = request_page(
                session,
                symbol
            )

            new_history = parse_main_force_table(
                html
            )

            if not new_history:

                raise RuntimeError(
                    "找不到 CMoney 主力買賣超資料"
                )

            # ------------------------------------------------
            # 合併舊資料
            # ------------------------------------------------

            merged_history = merge_history(
                old_history,
                new_history
            )

            # ------------------------------------------------
            # 已有20D：
            # 只做增量更新
            # ------------------------------------------------

            if len(old_history) >= MIN_HISTORY:

                reused_history += 1

                log(
                    f"   ✓ 增量更新"
                    f"（舊歷史 {len(old_history)}D）"
                )

            else:

                rebuilt_history += 1

                log(
                    f"   ✓ 建立/補充歷史"
                    f"（舊 {len(old_history)}D → "
                    f"新 {len(merged_history)}D）"
                )

            periods = calculate_periods(
                merged_history
            )

            record.update(
                periods
            )

            record[
                "history"
            ] = merged_history

            record[
                "status"
            ] = get_status(
                record
            )

            # ------------------------------------------------
            # 如果歷史不足20D
            #
            # 不猜測。
            #
            # 保留已知資料。
            # 下一次執行再繼續累積。
            # ------------------------------------------------

            if len(merged_history) < MIN_HISTORY:

                log(
                    f"   ⚠️ 歷史目前只有 "
                    f"{len(merged_history)}D"
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

            # ------------------------------------------------
            # 重要：
            #
            # 如果本次 CMoney 暫時失敗，
            # 不要把舊資料洗掉。
            # ------------------------------------------------

            if old_history:

                old_periods = calculate_periods(
                    old_history
                )

                record.update(
                    old_periods
                )

                record[
                    "history"
                ] = old_history

                record[
                    "status"
                ] = get_status(
                    record
                )

                record[
                    "error"
                ] = str(exc)

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
                    f"   ⚠️ 本次抓取失敗："
                    f"{exc}"
                )

                log(
                    "   ↳ 保留既有 chip.json 資料"
                )

            else:

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

    log("")
    log(
        f"已使用既有20D增量更新："
        f"{reused_history}"
    )

    log(
        f"需要建立/補充歷史："
        f"{rebuilt_history}"
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

    # --------------------------------------------------------
    # 只要完全沒有任何有效資料才讓 Action 失敗
    # --------------------------------------------------------

    if valid_1d == 0:

        raise RuntimeError(
            "本次完全沒有取得有效主力資料"
        )

    if valid_5d == 0:

        raise RuntimeError(
            "本次完全沒有取得有效主力5日資料"
        )

    if valid_10d == 0:

        raise RuntimeError(
            "本次完全沒有取得有效主力10日資料"
        )

    if valid_20d < total:

        log(
            "⚠️ 部分股票目前沒有完整20D"
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

        "update_mode":
            "incremental",

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

    # --------------------------------------------------------
    # 寫入暫存檔
    # --------------------------------------------------------

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

    if not isinstance(
        verify,
        dict
    ):

        raise RuntimeError(
            "chip.json 寫入後驗證失敗"
        )

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

    # --------------------------------------------------------
    # 完整股票必須真的有20D
    # --------------------------------------------------------

    for symbol, record in verify_stocks.items():

        if record.get(
            "status"
        ) == "complete":

            history = record.get(
                "history",
                []
            )

            if len(history) < MIN_HISTORY:

                raise RuntimeError(
                    f"{symbol} "
                    f"標記complete但history不足20D"
                )

            if record.get(
                "main_force_20d"
            ) is None:

                raise RuntimeError(
                    f"{symbol} "
                    f"缺少main_force_20d"
                )

    # --------------------------------------------------------
    # 最後才正式替換
    # --------------------------------------------------------

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

    log(
        f"大小："
        f"{CHIP_FILE.stat().st_size / 1024 / 1024:.2f} MB"
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

        # ----------------------------------------------------
        # 1. Universe
        # ----------------------------------------------------

        stocks = load_universe()

        # ----------------------------------------------------
        # 2. 舊 chip.json
        # ----------------------------------------------------

        section(
            "讀取既有籌碼資料"
        )

        old_stocks = load_existing_chip()

        # ----------------------------------------------------
        # 3. CMoney 增量更新
        # ----------------------------------------------------

        (
            results,
            complete,
            partial,
            insufficient
        ) = fetch_all(
            stocks,
            old_stocks
        )

        # ----------------------------------------------------
        # 4. 驗證
        # ----------------------------------------------------

        validate(
            results,
            len(stocks),
            complete,
            partial,
            insufficient
        )

        # ----------------------------------------------------
        # 5. 寫入
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # 非常重要：
        #
        # 失敗時不覆蓋既有 chip.json
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