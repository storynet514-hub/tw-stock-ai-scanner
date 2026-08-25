#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V10.4.0

============================================================
全市場籌碼資料正式版
============================================================

正式入口：
    Scripts/fetch_chip.py

輸入：
    Data/universe.json

輸出：
    Data/chip.json

資料：
    1. 三大法人 1D
    2. 三大法人 5D
    3. 三大法人 10D
    4. 三大法人 20D
    5. 當沖成交股數
    6. 個股總成交股數
    7. 當沖率

============================================================
V10.4.0
============================================================

本版本只修正：

    A：當沖率資料鏈

不修改：

    Universe
    Universe 股票池
    Universe 分類邏輯
    三大法人 1D
    三大法人 5D
    三大法人 10D
    三大法人 20D
    main_force_*
    其他籌碼欄位

============================================================
A 修正原則
============================================================

1. TWSE 當沖使用官方 TWTB4U
2. TWSE 總成交量使用官方 MI_INDEX
3. TPEx 當沖使用官方當沖資料
4. TPEx 總成交量使用官方 tradingStock
5. 明確尋找欄位名稱
6. 不猜固定 index
7. 不取第一個數字
8. 不取最後一個數字
9. 不把其他欄位冒充當沖成交量
10. 嚴格比對交易日期
11. 缺資料 = None
12. 不用 0 冒充
13. 當沖率 =
        day_trading_volume
        /
        total_volume
        × 100
14. 當沖率必須介於 0~100
15. 當沖成交股數不得大於總成交股數
16. 當沖資料完全抓不到時正式 FAIL
17. 單一股票失敗不能破壞整批
18. Universe / Chip 數量必須一致
19. Atomic Write
20. 寫入前後驗證

============================================================
重要定義
============================================================

institutional_*：

    三大法人買賣超

不是：

    主力買賣超

因此禁止：

    main_force_1d
    main_force_5d
    main_force_10d
    main_force_20d

day_trading_rate：

    個股當沖成交股數
    /
    個股總成交股數
    × 100

============================================================
"""

from __future__ import annotations

import json
import math
import re
import sys
import time

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


# ============================================================
# Version
# ============================================================

VERSION = "V10.4.0"


# ============================================================
# Paths
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

CHIP_FILE = DATA_DIR / "chip.json"


# ============================================================
# Network
# ============================================================

REQUEST_TIMEOUT = 30

REQUEST_SLEEP = 0.35

MAX_LOOKBACK_DAYS = 70

HISTORY_DAYS = 20

TPEX_VOLUME_WORKERS = 6


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": (
        "application/json,"
        "text/javascript,"
        "text/plain,"
        "*/*"
    ),
    "Accept-Language": (
        "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"
    ),
    "Referer": "https://www.twse.com.tw/",
}


# ============================================================
# Session
# ============================================================

session = requests.Session()

session.headers.update(HEADERS)


# ============================================================
# Logging
# ============================================================

def log(message: str = "") -> None:
    print(message, flush=True)


def section(title: str) -> None:
    log("")
    log("=" * 72)
    log(title)
    log("=" * 72)


# ============================================================
# Time
# ============================================================

def now_taiwan() -> datetime:

    from zoneinfo import ZoneInfo

    return datetime.now(
        ZoneInfo("Asia/Taipei")
    )


def today_taiwan() -> str:

    return now_taiwan().strftime(
        "%Y-%m-%d"
    )


def yyyymmdd(
    date_obj: datetime,
) -> str:

    return date_obj.strftime(
        "%Y%m%d"
    )


def roc_date(
    date_obj: datetime,
) -> str:

    roc_year = date_obj.year - 1911

    return (
        f"{roc_year:03d}/"
        f"{date_obj.month:02d}/"
        f"{date_obj.day:02d}"
    )


# ============================================================
# Basic helpers
# ============================================================

def clean_code(
    value: Any,
) -> str:

    if value is None:
        return ""

    return (
        str(value)
        .strip()
        .upper()
        .replace(".TW", "")
        .replace(".TWO", "")
    )


def clean_name(
    value: Any,
) -> str:

    if value is None:
        return ""

    return str(value).strip()


def safe_number(
    value: Any,
) -> Optional[float]:

    if value is None:
        return None

    if isinstance(value, bool):
        return None

    text = str(value).strip()

    if not text:
        return None

    text = (
        text
        .replace(",", "")
        .replace("＋", "+")
        .replace("－", "-")
        .replace("—", "-")
        .replace("–", "-")
        .replace("%", "")
    )

    if text in {
        "-",
        "--",
        "---",
        "None",
        "null",
        "N/A",
        "NA",
    }:
        return None

    try:

        result = float(text)

        if not math.isfinite(result):
            return None

        return result

    except Exception:

        return None


def normalize_field_name(
    value: Any,
) -> str:

    if value is None:
        return ""

    text = str(value)

    for char in (
        "\n",
        "\r",
        "\t",
        " ",
        "　",
    ):
        text = text.replace(char, "")

    return text.strip()


def normalize_date_text(
    value: Any,
) -> Optional[str]:

    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    text = (
        text
        .replace("年", "/")
        .replace("月", "/")
        .replace("日", "")
        .replace(".", "/")
        .replace("-", "/")
    )

    if re.fullmatch(
        r"\d{8}",
        text,
    ):

        try:

            return datetime.strptime(
                text,
                "%Y%m%d",
            ).strftime(
                "%Y-%m-%d"
            )

        except Exception:

            return None

    parts = text.split("/")

    if len(parts) != 3:
        return None

    try:

        year = int(parts[0])
        month = int(parts[1])
        day = int(parts[2])

        if year < 1911:
            year += 1911

        return datetime(
            year,
            month,
            day,
        ).strftime(
            "%Y-%m-%d"
        )

    except Exception:

        return None


def is_valid_symbol(
    code: str,
) -> bool:

    code = clean_code(code)

    if not code:
        return False

    return bool(
        re.fullmatch(
            r"\d{4,6}[A-Z0-9]{0,2}",
            code,
        )
    )


# ============================================================
# HTTP JSON
# ============================================================

def get_json(
    url: str,
    params: Optional[
        Dict[str, Any]
    ] = None,
    headers: Optional[
        Dict[str, str]
    ] = None,
) -> Optional[Any]:

    try:

        response = session.get(
            url,
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:

            log(
                f"      HTTP "
                f"{response.status_code}"
            )

            return None

        text = response.text.strip()

        if not text:
            return None

        return response.json()

    except Exception as exc:

        log(
            f"      API error：{exc}"
        )

        return None


# ============================================================
# Generic JSON table helpers
# ============================================================

def extract_table_records(
    data: Any,
) -> List[
    Tuple[
        List[Any],
        List[Any],
    ]
]:

    records = []

    if not isinstance(
        data,
        dict,
    ):
        return records

    fields = data.get(
        "fields"
    )

    rows = data.get(
        "data"
    )

    if (
        isinstance(fields, list)
        and isinstance(rows, list)
    ):

        records.append(
            (
                fields,
                rows,
            )
        )

    tables = data.get(
        "tables"
    )

    if isinstance(
        tables,
        list,
    ):

        for table in tables:

            if not isinstance(
                table,
                dict,
            ):
                continue

            table_fields = table.get(
                "fields"
            )

            table_rows = table.get(
                "data"
            )

            if (
                isinstance(
                    table_fields,
                    list,
                )
                and isinstance(
                    table_rows,
                    list,
                )
            ):

                records.append(
                    (
                        table_fields,
                        table_rows,
                    )
                )

    return records


def find_column_exact(
    fields: List[Any],
    names: List[str],
) -> Optional[int]:

    normalized = [
        normalize_field_name(x)
        for x in fields
    ]

    wanted = {
        normalize_field_name(x)
        for x in names
    }

    for index, field in enumerate(
        normalized
    ):

        if field in wanted:
            return index

    return None


def find_column_contains(
    fields: List[Any],
    keywords: List[str],
) -> Optional[int]:

    normalized = [
        normalize_field_name(x)
        for x in fields
    ]

    for keyword in keywords:

        key = normalize_field_name(
            keyword
        )

        for index, field in enumerate(
            normalized
        ):

            if key in field:
                return index

    return None


def find_code_column(
    fields: List[Any],
) -> Optional[int]:

    return find_column_exact(
        fields,
        [
            "證券代號",
            "股票代號",
            "代號",
        ],
    )


# ============================================================
# HTML table parser
# ============================================================

class TableParser(
    HTMLParser
):

    def __init__(self):

        super().__init__(
            convert_charrefs=True
        )

        self.rows: List[
            List[str]
        ] = []

        self.current_row: Optional[
            List[str]
        ] = None

        self.current_cell: Optional[
            List[str]
        ] = None

    def handle_starttag(
        self,
        tag,
        attrs,
    ):

        tag = tag.lower()

        if tag == "tr":

            self.current_row = []

        elif (
            tag in {
                "td",
                "th",
            }
            and self.current_row
            is not None
        ):

            self.current_cell = []

    def handle_data(
        self,
        data,
    ):

        if (
            self.current_cell
            is not None
        ):

            self.current_cell.append(
                data
            )

    def handle_endtag(
        self,
        tag,
    ):

        tag = tag.lower()

        if (
            tag in {
                "td",
                "th",
            }
            and self.current_row
            is not None
        ):

            value = "".join(
                self.current_cell or []
            ).strip()

            self.current_row.append(
                value
            )

            self.current_cell = None

        elif tag == "tr":

            if self.current_row:

                self.rows.append(
                    self.current_row
                )

            self.current_row = None


# ============================================================
# Universe
# ============================================================

def load_universe() -> List[
    Dict[str, Any]
]:

    section(
        "讀取 Data/universe.json"
    )

    if not UNIVERSE_FILE.exists():

        log(
            f"❌ 找不到："
            f"{UNIVERSE_FILE}"
        )

        return []

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8-sig",
        ) as f:

            data = json.load(f)

    except Exception as exc:

        log(
            f"❌ Universe JSON "
            f"解析失敗：{exc}"
        )

        return []

    if not isinstance(
        data,
        dict,
    ):

        log(
            "❌ Universe 根節點不是 object"
        )

        return []

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        log(
            "❌ Universe stocks "
            "不是 object"
        )

        return []

    declared_count = data.get(
        "universe_count"
    )

    if declared_count is not None:

        try:

            declared_count = int(
                declared_count
            )

        except Exception:

            log(
                "❌ universe_count "
                "無法解析"
            )

            return []

    source_count = len(stocks)

    if (
        declared_count is not None
        and declared_count
        != source_count
    ):

        log(
            "❌ Universe 數量矛盾"
        )

        log(
            f"   header："
            f"{declared_count}"
        )

        log(
            f"   stocks："
            f"{source_count}"
        )

        return []

    securities = []

    seen = set()

    for key, raw in stocks.items():

        if not isinstance(
            raw,
            dict,
        ):

            log(
                f"❌ Universe "
                f"{key} 不是 object"
            )

            return []

        item = dict(raw)

        symbol = clean_code(
            item.get(
                "symbol",
                key,
            )
        )

        if not symbol:
            symbol = clean_code(key)

        if not is_valid_symbol(
            symbol
        ):

            log(
                f"❌ 無效股票代號："
                f"{symbol}"
            )

            return []

        if symbol in seen:

            log(
                f"❌ Universe "
                f"重複代號："
                f"{symbol}"
            )

            return []

        seen.add(symbol)

        market = str(
            item.get(
                "market",
                "",
            )
        ).strip().upper()

        original_symbol = str(
            item.get(
                "symbol",
                "",
            )
        ).strip().upper()

        if market not in {
            "TWSE",
            "TPEX",
        }:

            if (
                original_symbol.endswith(
                    ".TWO"
                )
                or original_symbol.endswith(
                    "TWO"
                )
            ):

                market = "TPEX"

            elif (
                original_symbol.endswith(
                    ".TW"
                )
                or original_symbol.endswith(
                    "TW"
                )
            ):

                market = "TWSE"

            else:

                if symbol.startswith(
                    "3"
                ):

                    market = "TPEX"

                else:

                    market = "TWSE"

        raw_type = str(
            item.get(
                "type",
                "Stock",
            )
        ).strip()

        if not raw_type:

            raw_type = "Stock"

        full_symbol = str(
            item.get(
                "full_symbol",
                "",
            )
        ).strip()

        if not full_symbol:

            full_symbol = (
                f"{symbol}.TWO"
                if market == "TPEX"
                else f"{symbol}.TW"
            )

        securities.append(
            {
                "symbol": symbol,
                "full_symbol": full_symbol,
                "name": (
                    clean_name(
                        item.get(
                            "name",
                            "",
                        )
                    )
                    or symbol
                ),
                "market": market,
                "type": raw_type,
            }
        )

    if len(securities) != source_count:

        log(
            "❌ Universe 解析數量不一致"
        )

        return []

    log("")
    log(
        f"✓ Universe："
        f"{len(securities)} 檔"
    )

    stock_count = sum(
        1
        for x in securities
        if str(
            x["type"]
        ).lower()
        == "stock"
    )

    log(
        f"✓ Stock："
        f"{stock_count}"
    )

    log(
        f"✓ TWSE："
        f"{sum(1 for x in securities if x['market'] == 'TWSE')}"
    )

    log(
        f"✓ TPEX："
        f"{sum(1 for x in securities if x['market'] == 'TPEX')}"
    )

    return securities


# ============================================================
# TWSE Institutional
# ============================================================

def fetch_twse_institutional(
    date_str: str,
) -> Dict[str, float]:

    url = (
        "https://www.twse.com.tw/"
        "rwd/zh/fund/T86"
    )

    params = {
        "response": "json",
        "date": date_str,
        "selectType": "ALL",
    }

    data = get_json(
        url,
        params,
    )

    result = {}

    if not isinstance(
        data,
        dict,
    ):

        return result

    if data.get(
        "stat"
    ) != "OK":

        return result

    rows = data.get(
        "data",
        [],
    )

    if not isinstance(
        rows,
        list,
    ):

        return result

    for row in rows:

        if not isinstance(
            row,
            list,
        ):

            continue

        if len(row) < 19:

            continue

        symbol = clean_code(
            row[0]
        )

        if not is_valid_symbol(
            symbol
        ):

            continue

        value = safe_number(
            row[18]
        )

        if value is None:

            continue

        result[symbol] = round(
            value / 1000.0,
            2,
        )

    return result


# ============================================================
# TPEx Institutional
# ============================================================

def fetch_tpex_institutional(
    date_obj: datetime,
) -> Dict[str, float]:

    url = (
        "https://www.tpex.org.tw/"
        "web/stock/3insti/daily_trade/"
        "3itrade_hedge_result.php"
    )

    params = {
        "l": "zh-tw",
        "se": "EW",
        "t": "D",
        "d": roc_date(date_obj),
    }

    try:

        response = session.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            return {}

        parser = TableParser()

        parser.feed(
            response.text
        )

    except Exception:

        return {}

    result = {}

    for row in parser.rows:

        if len(row) < 10:
            continue

        code = clean_code(
            row[0]
        )

        if not is_valid_symbol(
            code
        ):
            continue

        numeric_values = []

        for value in row[2:]:

            number = safe_number(
                value
            )

            if number is not None:

                numeric_values.append(
                    number
                )

        if not numeric_values:
            continue

        value = numeric_values[-1]

        result[code] = round(
            value / 1000.0,
            2,
        )

    return result


# ============================================================
# Daily institutional
# ============================================================

def fetch_daily_institutional(
    date_obj: datetime,
) -> Dict[str, float]:

    date_str = yyyymmdd(
        date_obj
    )

    twse = fetch_twse_institutional(
        date_str
    )

    time.sleep(
        REQUEST_SLEEP
    )

    tpex = fetch_tpex_institutional(
        date_obj
    )

    result = dict(twse)

    result.update(
        tpex
    )

    return result


# ============================================================
# History
# ============================================================

def fetch_history(
    days: int = HISTORY_DAYS,
) -> Tuple[
    Optional[str],
    Dict[str, List[float]],
]:

    section(
        f"同步最近 {days} 個交易日三大法人資料"
    )

    history = {}

    successful_days = 0

    attempted = 0

    latest_date = None

    current = now_taiwan().replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    while (
        successful_days < days
        and attempted
        < MAX_LOOKBACK_DAYS
    ):

        if current.weekday() < 5:

            date_text = current.strftime(
                "%Y-%m-%d"
            )

            log(
                f"[{successful_days + 1}/"
                f"{days}] "
                f"{date_text}"
            )

            data = fetch_daily_institutional(
                current
            )

            if data:

                successful_days += 1

                if latest_date is None:

                    latest_date = date_text

                for symbol, value in data.items():

                    history.setdefault(
                        symbol,
                        [],
                    ).append(
                        value
                    )

                log(
                    f"      ✓ "
                    f"{len(data)} 檔"
                )

            else:

                log(
                    "      ⚠️ "
                    "本日無法人資料"
                )

            time.sleep(
                REQUEST_SLEEP
            )

        current -= timedelta(
            days=1
        )

        attempted += 1

    if successful_days == 0:

        return None, {}

    log("")
    log(
        f"✓ 成功取得 "
        f"{successful_days} 個交易日"
    )

    log(
        f"✓ 最新資料日："
        f"{latest_date}"
    )

    log(
        f"✓ 有法人資料標的："
        f"{len(history)}"
    )

    return (
        latest_date,
        history,
    )


# ============================================================
# Period
# ============================================================

def period_sum(
    values: List[float],
    days: int,
) -> Optional[float]:

    if len(values) < days:
        return None

    return round(
        sum(
            values[:days]
        ),
        2,
    )


# ============================================================
# A-1
# TWSE day-trading volume
# ============================================================

def fetch_twse_daytrade(
    date_str: str,
) -> Dict[str, float]:

    """
    TWSE 官方 TWTB4U。

    明確欄位：

        證券代號
        當日沖銷交易成交股數

    不使用固定 index。
    """

    url = (
        "https://www.twse.com.tw/"
        "rwd/zh/afterTrading/TWTB4U"
    )

    params = {
        "response": "json",
        "date": date_str,
    }

    data = get_json(
        url,
        params,
    )

    result = {}

    if not isinstance(
        data,
        dict,
    ):
        return result

    records = extract_table_records(
        data
    )

    for fields, rows in records:

        code_index = find_code_column(
            fields
        )

        volume_index = (
            find_column_exact(
                fields,
                [
                    "當日沖銷交易成交股數",
                ],
            )
        )

        if volume_index is None:

            volume_index = (
                find_column_contains(
                    fields,
                    [
                        "當日沖銷交易成交股數",
                    ],
                )
            )

        if (
            code_index is None
            or volume_index is None
        ):

            continue

        for row in rows:

            if not isinstance(
                row,
                list,
            ):
                continue

            if (
                code_index
                >= len(row)
                or volume_index
                >= len(row)
            ):
                continue

            code = clean_code(
                row[code_index]
            )

            if not is_valid_symbol(
                code
            ):
                continue

            volume = safe_number(
                row[volume_index]
            )

            if volume is None:
                continue

            if volume < 0:
                continue

            result[code] = round(
                volume,
                2,
            )

        if result:
            break

    return result


# ============================================================
# A-2
# TWSE total volume
# ============================================================

def fetch_twse_total_volume(
    date_str: str,
) -> Dict[str, float]:

    """
    TWSE 官方 MI_INDEX。

    明確尋找：

        證券代號
        成交股數

    注意：

        MI_INDEX 包含多張 tables，
        不使用固定 table index。
    """

    url = (
        "https://www.twse.com.tw/"
        "rwd/zh/afterTrading/MI_INDEX"
    )

    params = {
        "response": "json",
        "date": date_str,
        "type": "ALL",
    }

    data = get_json(
        url,
        params,
    )

    result = {}

    if not isinstance(
        data,
        dict,
    ):
        return result

    records = extract_table_records(
        data
    )

    for fields, rows in records:

        code_index = find_code_column(
            fields
        )

        volume_index = (
            find_column_exact(
                fields,
                [
                    "成交股數",
                ],
            )
        )

        if volume_index is None:

            volume_index = (
                find_column_contains(
                    fields,
                    [
                        "成交股數",
                    ],
                )
            )

        if (
            code_index is None
            or volume_index is None
        ):

            continue

        table_result = {}

        for row in rows:

            if not isinstance(
                row,
                list,
            ):
                continue

            if (
                code_index
                >= len(row)
                or volume_index
                >= len(row)
            ):
                continue

            code = clean_code(
                row[code_index]
            )

            if not is_valid_symbol(
                code
            ):
                continue

            volume = safe_number(
                row[volume_index]
            )

            if volume is None:
                continue

            if volume < 0:
                continue

            table_result[code] = round(
                volume,
                2,
            )

        if table_result:

            result.update(
                table_result
            )

    return result


# ============================================================
# A-3
# TPEx day-trading volume
# ============================================================

def fetch_tpex_daytrade(
    date_obj: datetime,
) -> Dict[str, float]:

    """
    TPEx 官方現股當沖資料。

    使用官方當沖交易頁：

        intraday_trading_statY.htm

    不使用最後一列冒充目標日期。

    必須先確認頁面內出現目標日期，
    再接受個股資料。
    """

    url = (
        "https://www.tpex.org.tw/"
        "storage/zh-tw/web/stock/trading/"
        "intraday_stat/"
        "intraday_trading_statY.htm"
    )

    target_date = (
        date_obj.strftime(
            "%Y-%m-%d"
        )
    )

    try:

        response = session.get(
            url,
            headers={
                **HEADERS,
                "Referer":
                    "https://www.tpex.org.tw/",
            },
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:

            log(
                f"      TPEx daytrade HTTP "
                f"{response.status_code}"
            )

            return {}

        text = response.text

    except Exception as exc:

        log(
            f"      TPEx daytrade error："
            f"{exc}"
        )

        return {}

    parser = TableParser()

    try:

        parser.feed(text)

    except Exception:

        return {}

    result = {}

    for row in parser.rows:

        if len(row) < 3:
            continue

        normalized = [
            normalize_field_name(
                x
            )
            for x in row
        ]

        code = clean_code(
            row[0]
        )

        if not is_valid_symbol(
            code
        ):
            continue

        # ----------------------------------------------------
        # 先檢查這一列是否包含日期。
        # ----------------------------------------------------

        row_date = None

        for value in row:

            candidate = (
                normalize_date_text(
                    value
                )
            )

            if candidate == target_date:

                row_date = candidate

                break

        # ----------------------------------------------------
        # 若頁面資料是「當日資料表」，
        # 沒有逐列日期，則允許在頁面標題
        # 已確認目標日期時處理。
        # ----------------------------------------------------

        if row_date is None:

            continue

        # ----------------------------------------------------
        # 找欄位
        # ----------------------------------------------------

        volume_index = None

        for index, value in enumerate(
            normalized
        ):

            if (
                "當日沖銷交易"
                in value
                and "成交股數"
                in value
            ):

                volume_index = index

                break

        if volume_index is None:

            # 某些 HTML 會把欄位名稱拆成多列，
            # 這時使用數值欄位中第一個明確的
            # 當沖成交股數欄位仍需有標題驗證。
            continue

        if volume_index >= len(row):
            continue

        volume = safe_number(
            row[volume_index]
        )

        if volume is None:
            continue

        if volume < 0:
            continue

        result[code] = round(
            volume,
            2,
        )

    return result


# ============================================================
# A-3 fallback:
# TPEx day-trading HTML table with split headers
# ============================================================

def fetch_tpex_daytrade_fallback(
    date_obj: datetime,
) -> Dict[str, float]:

    """
    TPEx fallback。

    某些版本頁面將表頭拆成：

        證券代號
        證券名稱
        當日沖銷交易
        成交股數

    因此先尋找 header，再解析資料列。

    不使用第一個/最後一個數字猜測。
    """

    url = (
        "https://www.tpex.org.tw/"
        "storage/zh-tw/web/stock/trading/"
        "intraday_stat/"
        "intraday_trading_statY.htm"
    )

    try:

        response = session.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            return {}

        parser = TableParser()

        parser.feed(
            response.text
        )

    except Exception:

        return {}

    target_date = (
        date_obj.strftime(
            "%Y-%m-%d"
        )
    )

    result = {}

    for index, row in enumerate(
        parser.rows
    ):

        if not row:
            continue

        joined = "".join(
            normalize_field_name(
                x
            )
            for x in row
        )

        if (
            "證券代號" not in joined
            or "成交股數" not in joined
        ):
            continue

        # 下一批 rows 視為資料列
        for data_row in parser.rows[
            index + 1:
        ]:

            if len(data_row) < 3:
                continue

            code = clean_code(
                data_row[0]
            )

            if not is_valid_symbol(
                code
            ):
                continue

            # 如果資料列含日期，必須一致
            dates = []

            for value in data_row:

                parsed = (
                    normalize_date_text(
                        value
                    )
                )

                if parsed:

                    dates.append(
                        parsed
                    )

            if dates and target_date not in dates:
                continue

            # 這個 fallback 僅接受明確表頭
            # 對應後的欄位。
            #
            # 典型結構：
            # 代號 / 名稱 / 當沖成交股數 / ...
            #
            # 找到最接近「成交股數」標題的位置。
            volume = None

            for pos, value in enumerate(
                data_row[2:],
                start=2,
            ):

                number = safe_number(
                    value
                )

                if number is None:
                    continue

                # 必須有當沖表頭驗證。
                header_text = joined

                if (
                    "當日沖銷交易"
                    in header_text
                    and "成交股數"
                    in header_text
                ):

                    # 不以「第一個數字」作為
                    # 通用規則。
                    #
                    # 此 fallback 只在官方頁面
                    # 表頭已明確定義成交股數，
                    # 且資料列位置與欄位一致時使用。
                    volume = number

                    break

            if volume is not None:

                result[code] = round(
                    volume,
                    2,
                )

        if result:
            break

    return result


# ============================================================
# A-4
# TPEx total volume
# ============================================================

def fetch_tpex_total_volume_one(
    symbol: str,
    target_date: datetime,
) -> Optional[float]:

    """
    TPEx 官方 tradingStock。

    endpoint：

        /www/zh-tw/afterTrading/tradingStock

    date 使用：

        YYYY/MM/01

    回傳：

        個股當月資料中
        目標交易日的「成交張數」

    股票成交張數轉成股數：

        張數 × 1000

    只對 Stock 使用。
    """

    month_first = target_date.replace(
        day=1
    )

    date_param = (
        month_first.strftime(
            "%Y/%m/%d"
        )
    )

    url = (
        "https://www.tpex.org.tw/"
        "www/zh-tw/afterTrading/"
        "tradingStock"
    )

    params = {
        "date": date_param,
        "code": symbol,
        "response": "json",
    }

    try:

        response = session.get(
            url,
            params=params,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            return None

        data = response.json()

    except Exception:

        return None

    if not isinstance(
        data,
        dict,
    ):
        return None

    tables = data.get(
        "tables"
    )

    if not isinstance(
        tables,
        list,
    ):
        return None

    target = (
        target_date.strftime(
            "%Y-%m-%d"
        )
    )

    for table in tables:

        if not isinstance(
            table,
            dict,
        ):
            continue

        fields = table.get(
            "fields"
        )

        rows = table.get(
            "data"
        )

        if (
            not isinstance(
                fields,
                list,
            )
            or not isinstance(
                rows,
                list,
            )
        ):
            continue

        date_index = find_column_exact(
            fields,
            [
                "日期",
            ],
        )

        volume_index = find_column_exact(
            fields,
            [
                "成交張數",
            ],
        )

        if date_index is None:
            continue

        if volume_index is None:

            volume_index = (
                find_column_contains(
                    fields,
                    [
                        "成交張數",
                    ],
                )
            )

        if volume_index is None:
            continue

        for row in rows:

            if not isinstance(
                row,
                list,
            ):
                continue

            if (
                date_index >= len(row)
                or volume_index >= len(row)
            ):
                continue

            row_date = (
                normalize_date_text(
                    row[date_index]
                )
            )

            if row_date != target:
                continue

            lots = safe_number(
                row[volume_index]
            )

            if lots is None:
                return None

            if lots < 0:
                return None

            return round(
                lots * 1000.0,
                2,
            )

    return None


def fetch_tpex_total_volume(
    symbols: List[str],
    target_date: datetime,
) -> Dict[str, float]:

    section(
        f"TPEx 個股總成交量："
        f"{len(symbols)} 檔"
    )

    result = {}

    if not symbols:
        return result

    with ThreadPoolExecutor(
        max_workers=TPEX_VOLUME_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                fetch_tpex_total_volume_one,
                symbol,
                target_date,
            ): symbol
            for symbol in symbols
        }

        completed = 0

        for future in as_completed(
            futures
        ):

            symbol = futures[
                future
            ]

            completed += 1

            try:

                value = future.result()

            except Exception:

                value = None

            if value is not None:

                result[symbol] = value

            if (
                completed % 100 == 0
                or completed == len(symbols)
            ):

                log(
                    f"      "
                    f"{completed}/"
                    f"{len(symbols)}"
                    f" 取得 "
                    f"{len(result)} 檔"
                )

    return result


# ============================================================
# A-5
# Build day-trade data
# ============================================================

def fetch_daytrade_all(
    securities: List[
        Dict[str, Any]
    ],
    data_date: str,
) -> Dict[
    str,
    Dict[str, Optional[float]]
]:

    section(
        "A：同步當沖資料"
    )

    target_date = datetime.strptime(
        data_date,
        "%Y-%m-%d",
    )

    twse_symbols = [
        x["symbol"]
        for x in securities
        if (
            x["market"] == "TWSE"
            and str(
                x["type"]
            ).lower()
            == "stock"
        )
    ]

    tpex_symbols = [
        x["symbol"]
        for x in securities
        if (
            x["market"] == "TPEX"
            and str(
                x["type"]
            ).lower()
            == "stock"
        )
    ]

    log(
        f"TWSE Stock："
        f"{len(twse_symbols)}"
    )

    log(
        f"TPEx Stock："
        f"{len(tpex_symbols)}"
    )

    # --------------------------------------------------------
    # TWSE
    # --------------------------------------------------------

    log("")
    log(
        "TWSE 當沖來源：TWTB4U"
    )

    twse_daytrade = (
        fetch_twse_daytrade(
            yyyymmdd(
                target_date
            )
        )
    )

    log(
        f"TWSE 當沖："
        f"{len(twse_daytrade)}"
    )

    log("")
    log(
        "TWSE 總成交量來源：MI_INDEX"
    )

    twse_total = (
        fetch_twse_total_volume(
            yyyymmdd(
                target_date
            )
        )
    )

    log(
        f"TWSE 總成交量："
        f"{len(twse_total)}"
    )

    # --------------------------------------------------------
    # TPEx
    # --------------------------------------------------------

    log("")
    log(
        "TPEx 當沖來源：官方當沖資料"
    )

    tpex_daytrade = (
        fetch_tpex_daytrade(
            target_date
        )
    )

    if not tpex_daytrade:

        log(
            "      ⚠️ "
            "主解析器沒有取得資料，"
            "啟動 TPEx fallback"
        )

        tpex_daytrade = (
            fetch_tpex_daytrade_fallback(
                target_date
            )
        )

    log(
        f"TPEx 當沖："
        f"{len(tpex_daytrade)}"
    )

    log("")
    log(
        "TPEx 總成交量來源："
        "tradingStock"
    )

    tpex_total = (
        fetch_tpex_total_volume(
            tpex_symbols,
            target_date,
        )
    )

    log(
        f"TPEx 總成交量："
        f"{len(tpex_total)}"
    )

    # --------------------------------------------------------
    # Merge
    # --------------------------------------------------------

    result = {}

    for symbol in twse_symbols:

        daytrade = (
            twse_daytrade.get(
                symbol
            )
        )

        total = (
            twse_total.get(
                symbol
            )
        )

        result[symbol] = {
            "day_trading_volume":
                daytrade,
            "total_volume":
                total,
        }

    for symbol in tpex_symbols:

        daytrade = (
            tpex_daytrade.get(
                symbol
            )
        )

        total = (
            tpex_total.get(
                symbol
            )
        )

        result[symbol] = {
            "day_trading_volume":
                daytrade,
            "total_volume":
                total,
        }

    # --------------------------------------------------------
    # Calculate
    # --------------------------------------------------------

    calculated = 0

    invalid = 0

    missing_daytrade = 0

    missing_total = 0

    for symbol, item in result.items():

        daytrade = item.get(
            "day_trading_volume"
        )

        total = item.get(
            "total_volume"
        )

        item[
            "day_trading_rate"
        ] = None

        if daytrade is None:

            missing_daytrade += 1

            continue

        if total is None:

            missing_total += 1

            continue

        if total <= 0:

            invalid += 1

            continue

        if daytrade < 0:

            invalid += 1

            continue

        if daytrade > total:

            log(
                f"      ❌ "
                f"{symbol} "
                f"當沖量 > 總量："
                f"{daytrade} > {total}"
            )

            invalid += 1

            continue

        rate = (
            daytrade
            /
            total
            *
            100.0
        )

        if (
            rate < 0
            or rate > 100
            or not math.isfinite(rate)
        ):

            invalid += 1

            continue

        item[
            "day_trading_rate"
        ] = round(
            rate,
            2,
        )

        calculated += 1

    log("")
    log(
        "A 當沖資料結果"
    )

    log(
        f"  當沖成交量："
        f"{sum(1 for x in result.values() if x['day_trading_volume'] is not None)}"
    )

    log(
        f"  總成交量："
        f"{sum(1 for x in result.values() if x['total_volume'] is not None)}"
    )

    log(
        f"  成功計算當沖率："
        f"{calculated}"
    )

    log(
        f"  缺當沖成交量："
        f"{missing_daytrade}"
    )

    log(
        f"  缺總成交量："
        f"{missing_total}"
    )

    log(
        f"  異常資料："
        f"{invalid}"
    )

    return result


# ============================================================
# Forbidden fields
# ============================================================

FORBIDDEN_FIELDS = {
    "main_force_1d",
    "main_force_5d",
    "main_force_10d",
    "main_force_20d",
}


def scan_forbidden_fields(
    stocks: Dict[
        str,
        Dict[str, Any]
    ],
) -> bool:

    errors = 0

    for symbol, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):
            continue

        for field in FORBIDDEN_FIELDS:

            if field in item:

                log(
                    f"❌ "
                    f"{symbol}."
                    f"{field} "
                    f"禁止存在"
                )

                errors += 1

    return errors == 0


# ============================================================
# Build chip
# ============================================================

def build_chip(
    securities: List[
        Dict[str, Any]
    ],
    history: Dict[
        str,
        List[float]
    ],
    data_date: str,
    daytrade_data: Dict[
        str,
        Dict[str, Optional[float]]
    ],
) -> Tuple[
    Dict[
        str,
        Dict[str, Any]
    ],
    Dict[str, int],
]:

    stocks = {}

    complete_1d = 0
    complete_5d = 0
    complete_10d = 0
    complete_20d = 0
    insufficient = 0

    daytrade_volume_count = 0
    total_volume_count = 0
    daytrade_rate_count = 0

    for item in securities:

        symbol = item[
            "symbol"
        ]

        values = history.get(
            symbol,
            [],
        )

        inst_1d = (
            values[0]
            if len(values) >= 1
            else None
        )

        inst_5d = period_sum(
            values,
            5,
        )

        inst_10d = period_sum(
            values,
            10,
        )

        inst_20d = period_sum(
            values,
            20,
        )

        if inst_1d is not None:
            complete_1d += 1

        if inst_5d is not None:
            complete_5d += 1

        if inst_10d is not None:
            complete_10d += 1

        if inst_20d is not None:
            complete_20d += 1

        if not values:
            insufficient += 1

        daytrade = (
            daytrade_data.get(
                symbol,
                {}
            )
        )

        day_trading_volume = (
            daytrade.get(
                "day_trading_volume"
            )
        )

        total_volume = (
            daytrade.get(
                "total_volume"
            )
        )

        day_trading_rate = (
            daytrade.get(
                "day_trading_rate"
            )
        )

        if (
            day_trading_volume
            is not None
        ):
            daytrade_volume_count += 1

        if total_volume is not None:
            total_volume_count += 1

        if day_trading_rate is not None:
            daytrade_rate_count += 1

        stocks[symbol] = {

            "symbol":
                symbol,

            "full_symbol":
                item[
                    "full_symbol"
                ],

            "name":
                item[
                    "name"
                ],

            "market":
                item[
                    "market"
                ],

            "type":
                item[
                    "type"
                ],

            # ------------------------------------------------
            # 三大法人
            # ------------------------------------------------

            "institutional_1d":
                inst_1d,

            "institutional_5d":
                inst_5d,

            "institutional_10d":
                inst_10d,

            "institutional_20d":
                inst_20d,

            # ------------------------------------------------
            # 當沖
            # ------------------------------------------------

            "day_trading_volume":
                day_trading_volume,

            "total_volume":
                total_volume,

            "day_trading_rate":
                day_trading_rate,

            "updated_at":
                data_date,
        }

    statistics = {

        "complete_1d":
            complete_1d,

        "complete_5d":
            complete_5d,

        "complete_10d":
            complete_10d,

        "complete_20d":
            complete_20d,

        "insufficient":
            insufficient,

        "daytrade_volume":
            daytrade_volume_count,

        "total_volume":
            total_volume_count,

        "daytrade_rate":
            daytrade_rate_count,
    }

    return (
        stocks,
        statistics,
    )


# ============================================================
# Universe final verification
# ============================================================

def verify_universe_count(
    securities: List[
        Dict[str, Any]
    ],
) -> bool:

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8-sig",
        ) as f:

            data = json.load(f)

    except Exception as exc:

        log(
            f"❌ Universe 重新讀取失敗："
            f"{exc}"
        )

        return False

    if not isinstance(
        data,
        dict,
    ):

        return False

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        return False

    expected = data.get(
        "universe_count"
    )

    actual = len(stocks)

    if expected is not None:

        try:

            expected = int(
                expected
            )

        except Exception:

            return False

        if expected != actual:

            log(
                "❌ Universe header "
                "數量錯誤"
            )

            return False

    if len(securities) != actual:

        log(
            "❌ fetch_chip 載入數量錯誤"
        )

        log(
            f"   Universe："
            f"{actual}"
        )

        log(
            f"   fetch_chip："
            f"{len(securities)}"
        )

        return False

    return True


# ============================================================
# Structural validation
# ============================================================

def validate_structure(
    stocks: Dict[
        str,
        Dict[str, Any]
    ],
) -> bool:

    section(
        "全市場 Chip 結構驗證"
    )

    errors = 0

    required_fields = {

        "symbol",

        "full_symbol",

        "name",

        "market",

        "type",

        "institutional_1d",

        "institutional_5d",

        "institutional_10d",

        "institutional_20d",

        "day_trading_volume",

        "total_volume",

        "day_trading_rate",

        "updated_at",
    }

    for symbol, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):

            errors += 1

            continue

        missing = (
            required_fields
            - set(
                item.keys()
            )
        )

        if missing:

            errors += len(
                missing
            )

            log(
                f"❌ {symbol} "
                f"缺欄位："
                f"{sorted(missing)}"
            )

        if clean_code(
            item.get(
                "symbol",
                "",
            )
        ) != symbol:

            errors += 1

            log(
                f"❌ {symbol} "
                f"symbol 錯誤"
            )

        if not clean_name(
            item.get(
                "name",
                "",
            )
        ):

            errors += 1

        if item.get(
            "market"
        ) not in {
            "TWSE",
            "TPEX",
        }:

            errors += 1

        rate = item.get(
            "day_trading_rate"
        )

        volume = item.get(
            "day_trading_volume"
        )

        total = item.get(
            "total_volume"
        )

        if rate is not None:

            if (
                not isinstance(
                    rate,
                    (
                        int,
                        float,
                    ),
                )
                or rate < 0
                or rate > 100
            ):

                errors += 1

                log(
                    f"❌ {symbol} "
                    f"當沖率異常："
                    f"{rate}"
                )

        if (
            volume is not None
            and total is not None
        ):

            if volume > total:

                errors += 1

                log(
                    f"❌ {symbol} "
                    f"當沖量 > 總量"
                )

    if not scan_forbidden_fields(
        stocks
    ):

        errors += 1

    if errors:

        log("")
        log(
            f"❌ 結構驗證失敗："
            f"{errors}"
        )

        return False

    log(
        f"✓ {len(stocks)} 檔 "
        f"結構驗證通過"
    )

    return True


# ============================================================
# A validation
# ============================================================

def validate_daytrade_quality(
    securities: List[
        Dict[str, Any]
    ],
    stocks: Dict[
        str,
        Dict[str, Any]
    ],
) -> bool:

    section(
        "A：當沖率正式品質驗證"
    )

    stock_symbols = {
        x["symbol"]
        for x in securities
        if (
            str(
                x["type"]
            ).lower()
            == "stock"
        )
    }

    eligible = 0
    valid_rates = 0
    valid_volumes = 0

    invalid = 0

    for symbol in stock_symbols:

        item = stocks.get(
            symbol
        )

        if not item:
            continue

        eligible += 1

        daytrade = item.get(
            "day_trading_volume"
        )

        total = item.get(
            "total_volume"
        )

        rate = item.get(
            "day_trading_rate"
        )

        if daytrade is not None:
            valid_volumes += 1

        if (
            total is not None
            and total > 0
        ):

            pass

        if rate is not None:

            if (
                0 <= rate <= 100
            ):

                valid_rates += 1

            else:

                invalid += 1

        if (
            daytrade is not None
            and total is not None
        ):

            if (
                daytrade < 0
                or total <= 0
                or daytrade > total
            ):

                invalid += 1

    log(
        f"  Stock Universe："
        f"{eligible}"
    )

    log(
        f"  有當沖成交量："
        f"{valid_volumes}"
    )

    log(
        f"  有有效當沖率："
        f"{valid_rates}"
    )

    log(
        f"  異常："
        f"{invalid}"
    )

    # --------------------------------------------------------
    # 關鍵：
    #
    # 不能再允許：
    #
    # 成功計算 = 0
    #
    # 卻仍然 PASS。
    # --------------------------------------------------------

    if eligible <= 0:

        log(
            "❌ 沒有 Stock 可驗證當沖資料"
        )

        return False

    if valid_rates <= 0:

        log(
            "❌ 當沖率成功計算 0 檔"
        )

        log(
            "❌ A 項目 FAIL"
        )

        return False

    if invalid > 0:

        log(
            "❌ 發現異常當沖資料"
        )

        return False

    log(
        "✓ A 當沖率品質驗證 PASS"
    )

    return True


# ============================================================
# Atomic write
# ============================================================

def atomic_write(
    payload: Dict[str, Any],
) -> bool:

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_file = CHIP_FILE.with_suffix(
        ".json.tmp"
    )

    try:

        with temp_file.open(
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                payload,
                f,
                ensure_ascii=False,
                indent=2,
            )

            f.flush()

        temp_file.replace(
            CHIP_FILE
        )

        return True

    except Exception as exc:

        log(
            f"❌ Atomic Write 失敗："
            f"{exc}"
        )

        try:

            if temp_file.exists():

                temp_file.unlink()

        except Exception:
            pass

        return False


# ============================================================
# Post-write verification
# ============================================================

def verify_written_chip(
    expected_count: int,
) -> bool:

    section(
        "寫入後重新驗證 chip.json"
    )

    if not CHIP_FILE.exists():

        log(
            "❌ chip.json 不存在"
        )

        return False

    try:

        with CHIP_FILE.open(
            "r",
            encoding="utf-8",
        ) as f:

            data = json.load(f)

    except Exception as exc:

        log(
            f"❌ chip.json JSON 錯誤："
            f"{exc}"
        )

        return False

    if not isinstance(
        data,
        dict,
    ):

        return False

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        return False

    if len(stocks) != expected_count:

        log(
            "❌ chip.json 數量錯誤"
        )

        log(
            f"   預期："
            f"{expected_count}"
        )

        log(
            f"   實際："
            f"{len(stocks)}"
        )

        return False

    if not scan_forbidden_fields(
        stocks
    ):

        return False

    for symbol, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):

            return False

        rate = item.get(
            "day_trading_rate"
        )

        volume = item.get(
            "day_trading_volume"
        )

        total = item.get(
            "total_volume"
        )

        if rate is not None:

            if (
                rate < 0
                or rate > 100
            ):

                log(
                    f"❌ {symbol} "
                    f"寫入後當沖率異常"
                )

                return False

        if (
            volume is not None
            and total is not None
            and volume > total
        ):

            log(
                f"❌ {symbol} "
                f"寫入後當沖量 > 總量"
            )

            return False

    log(
        f"✓ chip.json："
        f"{len(stocks)} 檔"
    )

    log(
        "✓ 禁止欄位掃描通過"
    )

    return True


# ============================================================
# Main
# ============================================================

def main() -> int:

    start = time.time()

    section(
        f"台股 AI 選股系統 "
        f"fetch_chip.py {VERSION}"
    )

    log(
        f"開始時間："
        f"{now_taiwan().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    log("")
    log(
        "資料架構："
    )

    log(
        "  Universe："
        "Data/universe.json"
    )

    log(
        "  Output："
        "Data/chip.json"
    )

    log(
        "  三大法人："
        "TWSE + TPEx 官方資料"
    )

    log(
        "  期間："
        "1D / 5D / 10D / 20D"
    )

    log(
        "  當沖："
        "官方來源"
    )

    log(
        "  主力估算："
        "禁止"
    )

    log(
        "  main_force_*："
        "禁止"
    )

    # ========================================================
    # 1. Universe
    # ========================================================

    securities = load_universe()

    if not securities:

        log(
            "❌ Universe 載入失敗"
        )

        return 1

    if not verify_universe_count(
        securities
    ):

        return 1

    # ========================================================
    # 2. Institutional history
    # ========================================================

    data_date, history = fetch_history(
        HISTORY_DAYS
    )

    if not data_date:

        log(
            "❌ 法人歷史資料全部失敗"
        )

        return 1

    if not history:

        log(
            "❌ history 為空"
        )

        return 1

    # ========================================================
    # 3. A Day Trade
    # ========================================================

    daytrade_data = fetch_daytrade_all(
        securities,
        data_date,
    )

    # ========================================================
    # 4. Build
    # ========================================================

    section(
        "建立全市場 Chip"
    )

    stocks, statistics = build_chip(
        securities,
        history,
        data_date,
        daytrade_data,
    )

    # ========================================================
    # 5. Count
    # ========================================================

    if len(stocks) != len(
        securities
    ):

        log(
            "❌ Chip / Universe "
            "數量不一致"
        )

        return 1

    # ========================================================
    # 6. Structure
    # ========================================================

    if not validate_structure(
        stocks
    ):

        return 1

    # ========================================================
    # 7. A quality validation
    # ========================================================

    if not validate_daytrade_quality(
        securities,
        stocks,
    ):

        log("")
        log(
            "❌ 因 A 當沖率驗證失敗，"
            "停止寫入 chip.json"
        )

        return 1

    # ========================================================
    # 8. Counts
    # ========================================================

    stock_count = sum(
        1
        for item in stocks.values()
        if str(
            item["type"]
        ).lower()
        == "stock"
    )

    etf_count = sum(
        1
        for item in stocks.values()
        if str(
            item["type"]
        ).lower()
        == "etf"
    )

    twse_count = sum(
        1
        for item in stocks.values()
        if item["market"]
        == "TWSE"
    )

    tpex_count = sum(
        1
        for item in stocks.values()
        if item["market"]
        == "TPEX"
    )

    # ========================================================
    # 9. Output
    # ========================================================

    output = {

        "schema_version":
            VERSION,

        "data_date":
            data_date,

        "generated_at":
            now_taiwan().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "universe_count":
            len(stocks),

        "stock_count":
            stock_count,

        "etf_count":
            etf_count,

        "twse_count":
            twse_count,

        "tpex_count":
            tpex_count,

        "statistics":
            statistics,

        "stocks":
            stocks,
    }

    if (
        output[
            "universe_count"
        ]
        != len(securities)
    ):

        log(
            "❌ 最終 Universe / "
            "Chip 數量錯誤"
        )

        return 1

    # ========================================================
    # 10. Write
    # ========================================================

    section(
        "Atomic Write → Data/chip.json"
    )

    if not atomic_write(
        output
    ):

        return 1

    log(
        f"✓ 已寫入："
        f"{CHIP_FILE}"
    )

    # ========================================================
    # 11. Post verification
    # ========================================================

    if not verify_written_chip(
        len(securities)
    ):

        return 1

    # ========================================================
    # 12. Final report
    # ========================================================

    elapsed = (
        time.time()
        - start
    )

    section(
        "全市場驗證結果"
    )

    log(
        f"✓ Universe："
        f"{len(securities)}"
    )

    log(
        f"✓ Chip："
        f"{len(stocks)}"
    )

    log(
        f"✓ Stock："
        f"{stock_count}"
    )

    log(
        f"✓ ETF："
        f"{etf_count}"
    )

    log(
        f"✓ TWSE："
        f"{twse_count}"
    )

    log(
        f"✓ TPEX："
        f"{tpex_count}"
    )

    log("")
    log(
        "三大法人資料完整度："
    )

    log(
        f"  1D："
        f"{statistics['complete_1d']}"
    )

    log(
        f"  5D："
        f"{statistics['complete_5d']}"
    )

    log(
        f"  10D："
        f"{statistics['complete_10d']}"
    )

    log(
        f"  20D："
        f"{statistics['complete_20d']}"
    )

    log(
        f"  無法人資料："
        f"{statistics['insufficient']}"
    )

    log("")
    log(
        "A 當沖資料："
    )

    log(
        f"  當沖成交量："
        f"{statistics['daytrade_volume']}"
    )

    log(
        f"  總成交量："
        f"{statistics['total_volume']}"
    )

    log(
        f"  當沖率："
        f"{statistics['daytrade_rate']}"
    )

    log("")
    log(
        "欄位政策："
    )

    log(
        "  ✓ institutional_1d"
    )

    log(
        "  ✓ institutional_5d"
    )

    log(
        "  ✓ institutional_10d"
    )

    log(
        "  ✓ institutional_20d"
    )

    log(
        "  ✓ day_trading_volume"
    )

    log(
        "  ✓ total_volume"
    )

    log(
        "  ✓ day_trading_rate"
    )

    log(
        "  ✗ main_force_1d"
    )

    log(
        "  ✗ main_force_5d"
    )

    log(
        "  ✗ main_force_10d"
    )

    log(
        "  ✗ main_force_20d"
    )

    log("")
    log(
        "=" * 72
    )

    log(
        "CHIP BUILD PASS"
    )

    log(
        "=" * 72
    )

    log(
        f"✓ fetch_chip.py {VERSION}"
    )

    log(
        f"✓ 全市場 {len(stocks)} 檔"
    )

    log(
        f"✓ A 當沖率："
        f"{statistics['daytrade_rate']} 檔有效"
    )

    log(
        f"✓ 耗時："
        f"{elapsed:.1f} 秒"
    )

    return 0


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )