#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V7.0

============================================================
核心目的
============================================================

取得 Data/universe.json 中「全部」台股標的的：

1. 主力 1 日買賣超
2. 主力 5 日買賣超
3. 主力 10 日買賣超
4. 主力 20 日買賣超

============================================================
Universe 規則
============================================================

股票數量完全由：

    Data/universe.json

決定。

本程式：

- 不固定 4 檔
- 不固定 1095 檔
- 不固定 1985 檔
- 不設定 Universe 最大數量
- 不設定 Universe 最小數量
- 不自行建立股票清單

universe.json 有多少合法股票，就處理多少股票。

============================================================
主力定義
============================================================

資料來源：

CMoney「主力進出」

指定欄位：

「買賣超」

單位：

張

正數：
主力買超

負數：
主力賣超

============================================================
期間定義
============================================================

1D：

最近一個交易日「買賣超」

5D：

最近 5 個交易日「買賣超」加總

10D：

最近 10 個交易日「買賣超」加總

20D：

最近 20 個交易日「買賣超」加總

============================================================
嚴格禁止
============================================================

不得使用：

- 5日集中
- 20日集中
- 家數差
- 其他籌碼欄位
- 其他欄位冒充主力買賣超
- API 猜測
- pagination 猜測

CMoney 首頁解析不到的歷史資料，不得拿其他欄位補足。

============================================================
歷史累積
============================================================

CMoney 首頁目前可能只提供約 10 筆每日「買賣超」。

因此本程式採：

    今日 CMoney 首頁
          +
    舊 chip.json 已保存歷史
          ↓
    依日期合併
          ↓
    去除重複
          ↓
    最新交易日排序
          ↓
    最多保存最近 20 筆

重要：

第一次建立某檔股票時，如果 CMoney 只能提供 10 筆：

    1D / 5D / 10D 有效
    20D = None

下一次執行時：

    新資料 + 舊歷史

繼續累積。

已經有完整 20D 的股票，不會因為本次只有
10 筆首頁資料而退回 10D。

============================================================
資料來源限制
============================================================

本版本不探測 API。

不猜：

- page
- pageNo
- pageIndex
- offset
- limit
- API endpoint

避免把其他資料誤認成主力買賣超。

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

VERSION = "V7.0"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

CHIP_FILE = DATA_DIR / "chip.json"

REQUEST_TIMEOUT = 30

REQUEST_DELAY = 0.20

HISTORY_DAYS = 20


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
    """
    完全依照 Data/universe.json 建立 Universe。

    不鎖定任何股票數量。
    """

    section("讀取台股 Universe")

    if not UNIVERSE_FILE.exists():
        raise RuntimeError(
            f"找不到 Universe：{UNIVERSE_FILE}"
        )

    with UNIVERSE_FILE.open(
        "r",
        encoding="utf-8"
    ) as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise RuntimeError(
            "universe.json 頂層格式錯誤"
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

        symbol = str(symbol).strip().upper()

        # 去掉 Yahoo 常見市場尾碼
        symbol = re.sub(
            r"\.(TW|TWO)$",
            "",
            symbol
        )

        # 台股股票代號：
        # 允許數字以及少數特殊代號
        if not re.fullmatch(
            r"[A-Z0-9]{4,6}",
            symbol
        ):
            continue

        if symbol in seen:
            continue

        seen.add(symbol)

        name = str(
            item.get("name", "")
        ).strip()

        market = str(
            item.get("market", "")
        ).strip()

        stocks.append({
            "symbol": symbol,
            "name": name,
            "market": market,
        })

    if not stocks:
        raise RuntimeError(
            "Universe 沒有任何合法股票"
        )

    log(
        f"Universe 股票數量：{len(stocks)}"
    )

    log(
        "Universe 數量由 universe.json 決定"
    )

    return stocks


# ============================================================
# 舊 chip.json
# ============================================================

def load_previous_chip():
    """
    讀取上一版 chip.json。

    目的：

    保留之前已取得的歷史買賣超，
    讓 10D 首頁資料可以逐日累積成 20D。

    若不存在則回傳空 dict。
    """

    if not CHIP_FILE.exists():
        log(
            "上一版 chip.json：不存在"
        )
        return {}

    try:

        with CHIP_FILE.open(
            "r",
            encoding="utf-8"
        ) as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return {}

        stocks = data.get("stocks", {})

        if not isinstance(stocks, dict):
            return {}

        log(
            f"上一版 chip.json 股票："
            f"{len(stocks)}"
        )

        return stocks

    except Exception as exc:

        log(
            "⚠️ 舊 chip.json 無法讀取："
            f"{exc}"
        )

        return {}


# ============================================================
# Number
# ============================================================

def parse_number(text):
    """
    將文字轉成數字。

    注意：

    這個函式只負責解析指定的
    「買賣超」欄位。

    不負責猜欄位。
    """

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
# Header
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


def is_main_force_header(text):
    """
    嚴格判斷是否為 CMoney「買賣超」。

    禁止：

    - 集中
    - 家數
    """

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

def parse_table_with_header(soup):

    tables = soup.find_all("table")

    for table in tables:

        rows = table.find_all("tr")

        if not rows:
            continue

        header_cells = None

        # 只在表格前幾列尋找真正 Header
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
                (
                    h == "日期"
                    or "日期" in h
                )
                for h in headers
            )

            has_force = any(
                is_main_force_header(h)
                for h in headers
            )

            if has_date and has_force:

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

        for index, header in enumerate(headers):

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
                "main_force": force_value,
            })

        if result:
            return result

    return []


# ============================================================
# Clean History
# ============================================================

def clean_history(rows):

    unique = {}

    for row in rows:

        if not isinstance(row, dict):
            continue

        date = normalize_date(
            row.get("date")
        )

        value = row.get(
            "main_force"
        )

        if not date:
            continue

        if value is None:
            continue

        try:

            value = float(value)

        except Exception:
            continue

        try:

            datetime.strptime(
                date,
                "%Y/%m/%d"
            )

        except Exception:

            continue

        # 同一天只保留一筆
        unique[date] = value

    result = []

    for date, value in unique.items():

        result.append({
            "date": date,
            "main_force": value,
        })

    result.sort(
        key=lambda x: datetime.strptime(
            x["date"],
            "%Y/%m/%d"
        ),
        reverse=True
    )

    return result


# ============================================================
# CMoney History
# ============================================================

def fetch_cmoney_history(
    session,
    symbol
):
    """
    只取得 CMoney 首頁真正的
    「日期 + 買賣超」。

    不探測 API。
    不猜 pagination。
    """

    html, _ = request_page(
        session,
        symbol
    )

    history = parse_table_with_header(
        BeautifulSoup(
            html,
            "html.parser"
        )
    )

    history = clean_history(
        history
    )

    return history


# ============================================================
# Merge History
# ============================================================

def merge_history(
    current_history,
    previous_record
):
    """
    將：

        本次 CMoney 首頁資料
        +
        舊 chip.json 歷史

    合併成最近 20 個交易日。

    新資料優先。

    重要：

    舊資料只有在它本身已經是
    合法的「買賣超」歷史時才保留。
    """

    merged = {}

    # --------------------------------------------------------
    # 舊資料
    # --------------------------------------------------------

    if isinstance(
        previous_record,
        dict
    ):

        old_history = previous_record.get(
            "history",
            []
        )

        if isinstance(
            old_history,
            list
        ):

            for row in old_history:

                if not isinstance(
                    row,
                    dict
                ):
                    continue

                date = normalize_date(
                    row.get("date")
                )

                value = row.get(
                    "main_force"
                )

                if not date:
                    continue

                if value is None:
                    continue

                try:

                    value = float(value)

                except Exception:

                    continue

                merged[date] = value

    # --------------------------------------------------------
    # 本次資料覆蓋舊資料
    # --------------------------------------------------------

    for row in current_history:

        date = normalize_date(
            row.get("date")
        )

        value = row.get(
            "main_force"
        )

        if not date:
            continue

        if value is None:
            continue

        try:

            value = float(value)

        except Exception:

            continue

        merged[date] = value

    # --------------------------------------------------------
    # 排序
    # --------------------------------------------------------

    result = [
        {
            "date": date,
            "main_force": value,
        }
        for date, value in merged.items()
    ]

    result.sort(
        key=lambda x: datetime.strptime(
            x["date"],
            "%Y/%m/%d"
        ),
        reverse=True
    )

    return result[:HISTORY_DAYS]


# ============================================================
# Calculate
# ============================================================

def calculate_periods(history):

    values = [
        float(row["main_force"])
        for row in history
        if row.get("main_force") is not None
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
# Validate History
# ============================================================

def validate_history(
    history
):
    """
    確認歷史資料：

    - 日期合法
    - 沒有重複日期
    - 每筆都有 main_force
    """

    if not isinstance(
        history,
        list
    ):
        return False

    dates = set()

    for row in history:

        if not isinstance(
            row,
            dict
        ):
            return False

        date = row.get("date")

        value = row.get(
            "main_force"
        )

        if not normalize_date(date):
            return False

        if date in dates:
            return False

        dates.add(date)

        if value is None:
            return False

        try:
            float(value)
        except Exception:
            return False

    return True


# ============================================================
# Build Record
# ============================================================

def build_record(
    stock,
    current_history,
    previous_record
):

    symbol = stock["symbol"]

    name = stock["name"]

    market = stock["market"]

    merged_history = merge_history(
        current_history,
        previous_record
    )

    periods = calculate_periods(
        merged_history
    )

    history_count = len(
        merged_history
    )

    if history_count >= 20:

        status = "complete"

    elif history_count >= 10:

        status = "partial"

    elif history_count > 0:

        status = "partial"

    else:

        status = "insufficient"

    record = {
        "symbol": symbol,
        "name": name,
        "market": market,

        "source": "CMoney",

        "source_field": "買賣超",

        "main_force_1d":
            periods["main_force_1d"],

        "main_force_5d":
            periods["main_force_5d"],

        "main_force_10d":
            periods["main_force_10d"],

        "main_force_20d":
            periods["main_force_20d"],

        "history_count":
            periods["history_count"],

        "status":
            status,

        "history":
            merged_history,

        "error":
            None,
    }

    return record


# ============================================================
# Fetch All
# ============================================================

def fetch_all(
    stocks,
    previous_stocks
):

    section(
        "開始 CMoney 主力買賣超更新"
    )

    total = len(stocks)

    log(
        "Universe 模式：完整讀取 "
        "Data/universe.json"
    )

    log(
        f"本次掃描股票：{total}"
    )

    log(
        "Universe 數量沒有任何硬編碼限制"
    )

    log(
        "資料來源：CMoney 主力進出"
    )

    log(
        "指定欄位：買賣超"
    )

    log(
        "禁止：5日集中 / 20日集中 / 家數差"
    )

    log(
        "不探測 API"
    )

    log(
        "不猜 pagination"
    )

    log(
        "歷史資料保存於 Data/chip.json"
    )

    session = requests.Session()

    session.headers.update(
        HEADERS
    )

    results = {}

    complete = 0
    partial = 0
    insufficient = 0

    valid_1d = 0
    valid_5d = 0
    valid_10d = 0
    valid_20d = 0

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

        previous_record = (
            previous_stocks.get(
                symbol
            )
        )

        record = {
            "symbol": symbol,
            "name": name,
            "market": stock["market"],
            "source": "CMoney",
            "source_field": "買賣超",

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

            section(
                f"CMoney 主力買賣超："
                f"{symbol} {name}"
            )

            current_history = (
                fetch_cmoney_history(
                    session,
                    symbol
                )
            )

            log(
                f"CMoney 首頁有效"
                f"「買賣超」："
                f"{len(current_history)} 筆"
            )

            if current_history:

                log(
                    "✓ 已確認資料來源欄位："
                    "買賣超"
                )

            else:

                log(
                    "⚠️ CMoney 首頁沒有找到"
                    "「日期 + 買賣超」"
                )

            old_count = 0

            if isinstance(
                previous_record,
                dict
            ):

                old_history = (
                    previous_record.get(
                        "history",
                        []
                    )
                )

                if isinstance(
                    old_history,
                    list
                ):
                    old_count = len(
                        old_history
                    )

            log(
                f"上一版保存歷史："
                f"{old_count} 筆"
            )

            record = build_record(
                stock,
                current_history,
                previous_record
            )

            log(
                f"合併後歷史："
                f"{record['history_count']} 筆"
            )

            log(
                f"主力1日："
                f"{record['main_force_1d']}"
            )

            log(
                f"主力5日："
                f"{record['main_force_5d']}"
            )

            log(
                f"主力10日："
                f"{record['main_force_10d']}"
            )

            log(
                f"主力20日："
                f"{record['main_force_20d']}"
            )

            log(
                f"歷史筆數："
                f"{record['history_count']}"
            )

            if record["status"] == "complete":

                complete += 1

                log(
                    "✓ 已累積完整 "
                    "20 個交易日"
                )

            else:

                partial += 1

                log(
                    "ℹ️ 20D 尚未完整，"
                    "保留目前歷史供下一次累積"
                )

            if record["main_force_1d"] is not None:
                valid_1d += 1

            if record["main_force_5d"] is not None:
                valid_5d += 1

            if record["main_force_10d"] is not None:
                valid_10d += 1

            if record["main_force_20d"] is not None:
                valid_20d += 1

        except Exception as exc:

            insufficient += 1

            # ------------------------------------------------
            # 重要：
            # 如果本次抓取失敗，不把舊的完整資料洗掉。
            # ------------------------------------------------

            if isinstance(
                previous_record,
                dict
            ):

                try:

                    record = dict(
                        previous_record
                    )

                    record["symbol"] = symbol

                    record["name"] = name

                    record["market"] = (
                        stock["market"]
                    )

                    record["source"] = "CMoney"

                    record[
                        "source_field"
                    ] = "買賣超"

                    record["error"] = str(
                        exc
                    )

                    history = record.get(
                        "history",
                        []
                    )

                    if not isinstance(
                        history,
                        list
                    ):
                        history = []

                    record[
                        "history"
                    ] = clean_history(
                        history
                    )

                    periods = calculate_periods(
                        record["history"]
                    )

                    record.update(
                        periods
                    )

                    if len(
                        record["history"]
                    ) >= 20:

                        record[
                            "status"
                        ] = "complete"

                    elif record[
                        "history"
                    ]:

                        record[
                            "status"
                        ] = "partial"

                    else:

                        record[
                            "status"
                        ] = "insufficient"

                except Exception:
                    pass

            record["error"] = str(exc)

            log(
                f"⚠️ 取得失敗：{exc}"
            )

        results[symbol] = record

        time.sleep(
            REQUEST_DELAY
        )

    return (
        results,
        complete,
        partial,
        insufficient,
        valid_1d,
        valid_5d,
        valid_10d,
        valid_20d,
    )


# ============================================================
# Validate
# ============================================================

def validate(
    results,
    total,
    valid_1d,
    valid_5d,
    valid_10d,
    valid_20d
):

    section(
        "最終資料驗證"
    )

    log(
        f"Universe 股票：{total}"
    )

    log(
        f"輸出股票：{len(results)}"
    )

    log(
        f"有效主力1D：{valid_1d}"
    )

    log(
        f"有效主力5D：{valid_5d}"
    )

    log(
        f"有效主力10D：{valid_10d}"
    )

    log(
        f"有效主力20D：{valid_20d}"
    )

    if len(results) != total:

        raise RuntimeError(
            "輸出股票數量與 Universe 不一致"
        )

    if valid_1d <= 0:

        raise RuntimeError(
            "完全沒有有效主力1D資料"
        )

    if valid_5d <= 0:

        raise RuntimeError(
            "完全沒有有效主力5D資料"
        )

    if valid_10d <= 0:

        raise RuntimeError(
            "完全沒有有效主力10D資料"
        )

    log(
        "✓ 資料來源欄位驗證完成"
    )

    log(
        "✓ 1D / 5D / 10D / 20D "
        "計算驗證完成"
    )

    log(
        "✓ 未使用 5日集中 / "
        "20日集中 / 家數差"
    )

    log(
        "✓ Universe 沒有被程式碼限制"
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

        "definition": {

            "main_force":
                "CMoney 主力進出之買賣超",

            "main_force_1d":
                "最近1個交易日主力買賣超",

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

            "forbidden":
                [
                    "5日集中",
                    "20日集中",
                    "家數差",
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

    if not isinstance(
        verify,
        dict
    ):
        raise RuntimeError(
            "chip.json 頂層格式錯誤"
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
    ) != total:

        raise RuntimeError(
            "chip.json 股票數量驗證失敗："
            f"Universe={total}, "
            f"Output={len(verify_stocks)}"
        )

    # --------------------------------------------------------
    # 驗證每一檔
    # --------------------------------------------------------

    for symbol, record in verify_stocks.items():

        if not isinstance(
            record,
            dict
        ):
            raise RuntimeError(
                f"{symbol} record 格式錯誤"
            )

        history = record.get(
            "history",
            []
        )

        if not validate_history(
            history
        ):
            raise RuntimeError(
                f"{symbol} history 格式錯誤"
            )

        if len(history) > HISTORY_DAYS:

            raise RuntimeError(
                f"{symbol} history 超過 "
                f"{HISTORY_DAYS} 筆"
            )

        # ----------------------------------------------------
        # 驗證數值與歷史一致
        # ----------------------------------------------------

        periods = calculate_periods(
            history
        )

        for key in [
            "main_force_1d",
            "main_force_5d",
            "main_force_10d",
            "main_force_20d",
        ]:

            actual = record.get(key)

            expected = periods.get(key)

            if actual is None and expected is None:
                continue

            if actual is None or expected is None:

                raise RuntimeError(
                    f"{symbol} {key} "
                    "與 history 計算結果不一致"
                )

            if round(
                float(actual),
                2
            ) != round(
                float(expected),
                2
            ):

                raise RuntimeError(
                    f"{symbol} {key} "
                    "計算驗證失敗"
                )

    temp_file.replace(
        CHIP_FILE
    )

    log(
        "✓ chip.json 寫入成功"
    )

    log(
        f"輸出股票數：{len(results)}"
    )

    log(
        f"輸出檔案：{CHIP_FILE}"
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
        "資料來源：CMoney 主力進出"
    )

    log(
        "指定欄位：買賣超"
    )

    log(
        "20D：最近20交易日每日買賣超加總"
    )

    log(
        "Universe：完全依照 Data/universe.json"
    )

    log(
        "Universe 數量：不寫死"
    )

    log(
        "禁止：5日集中 / 20日集中 / 家數差"
    )

    try:

        # ----------------------------------------------------
        # 1. Universe
        # ----------------------------------------------------

        stocks = load_universe()

        # ----------------------------------------------------
        # 2. 舊 chip
        # ----------------------------------------------------

        previous_stocks = (
            load_previous_chip()
        )

        # ----------------------------------------------------
        # 3. Fetch
        # ----------------------------------------------------

        (
            results,
            complete,
            partial,
            insufficient,
            valid_1d,
            valid_5d,
            valid_10d,
            valid_20d,
        ) = fetch_all(
            stocks,
            previous_stocks
        )

        # ----------------------------------------------------
        # 4. Validate
        # ----------------------------------------------------

        validate(
            results,
            len(stocks),
            valid_1d,
            valid_5d,
            valid_10d,
            valid_20d
        )

        # ----------------------------------------------------
        # 5. Save
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
            f"✓ fetch_chip.py {VERSION} 完成"
        )

        log("=" * 72)

        log(
            f"Universe 股票："
            f"{len(stocks)}"
        )

        log(
            f"完整20D："
            f"{complete}"
        )

        log(
            f"部分："
            f"{partial}"
        )

        log(
            f"不足："
            f"{insufficient}"
        )

        log(
            f"有效1D："
            f"{valid_1d}"
        )

        log(
            f"有效5D："
            f"{valid_5d}"
        )

        log(
            f"有效10D："
            f"{valid_10d}"
        )

        log(
            f"有效20D："
            f"{valid_20d}"
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
            f"❌ fetch_chip.py {VERSION} 執行失敗"
        )

        log("=" * 72)

        log(
            f"原因：{exc}"
        )

        if CHIP_FILE.exists():

            log(
                "⚠️ 保留既有 chip.json，"
                "不覆蓋成功資料"
            )

        return 1


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )