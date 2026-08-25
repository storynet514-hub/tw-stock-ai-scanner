#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V10.1.0

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

核心架構：
    Universe 是唯一股票池。
    fetch_chip 不重新分類 Universe。

重要原則：
    - 不猜 ETF
    - 不猜 Bond
    - 不重新判斷 instrument type
    - 完全保留 Universe 身份資訊
    - 不產生 main_force_*
    - 不用倍率估算主力
    - 缺資料 = None
    - 不用 0 冒充
    - 不用 row 最後一個數字猜欄位
    - 不用固定 index 猜當沖欄位
    - 20D 必須是真正 20 個交易日
    - 當沖率 = 當沖成交股數 / 總成交股數 × 100
    - Universe / Chip 數量必須一致
    - 寫入前後驗證
    - Atomic Write

資料來源：
    TWSE 官方
    TPEx 官方

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

VERSION = "V10.1.0"


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

TPEX_VOLUME_WORKERS = 4


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": (
        "application/json, "
        "text/javascript, "
        "text/plain, "
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


# ============================================================
# Basic helpers
# ============================================================

def clean_code(value: Any) -> str:

    if value is None:
        return ""

    return (
        str(value)
        .strip()
        .upper()
        .replace(".TW", "")
        .replace(".TWO", "")
    )


def clean_name(value: Any) -> str:

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
        text = text.replace(
            char,
            "",
        )

    return text.strip()


def roc_date(
    date_obj: datetime,
) -> str:

    roc_year = date_obj.year - 1911

    return (
        f"{roc_year:03d}/"
        f"{date_obj.month:02d}/"
        f"{date_obj.day:02d}"
    )


def yyyymmdd(
    date_obj: datetime,
) -> str:

    return date_obj.strftime(
        "%Y%m%d"
    )


# ============================================================
# Symbol validation
# ============================================================

def is_valid_symbol(
    code: str,
) -> bool:

    """
    这里只驗證「代號格式」。

    絕對不根據代號推測：
        ETF
        Bond
        Stock

    身份由 Universe 決定。
    """

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
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
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
                f"      HTTP {response.status_code} "
                f"{url}"
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
# Generic table helpers
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


def find_column(
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


# ============================================================
# HTML parser
# ============================================================

class TableParser(
    HTMLParser
):

    def __init__(self):

        super().__init__(
            convert_charrefs=True
        )

        self.rows: List[List[str]] = []

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
            tag in {"td", "th"}
            and self.current_row is not None
        ):

            self.current_cell = []

    def handle_data(
        self,
        data,
    ):

        if self.current_cell is not None:

            self.current_cell.append(
                data
            )

    def handle_endtag(
        self,
        tag,
    ):

        tag = tag.lower()

        if (
            tag in {"td", "th"}
            and self.current_row is not None
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
            f"❌ 找不到：{UNIVERSE_FILE}"
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
            f"❌ Universe JSON 解析失敗：{exc}"
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
            "❌ Universe stocks 不是 object"
        )

        return []

    declared_count = data.get(
        "universe_count"
    )

    try:

        declared_count = (
            int(declared_count)
            if declared_count is not None
            else None
        )

    except Exception:

        log(
            "❌ universe_count 無法解析"
        )

        return []

    source_count = len(
        stocks
    )

    if (
        declared_count is not None
        and declared_count != source_count
    ):

        log(
            "❌ Universe header 數量錯誤"
        )

        log(
            f"   header = {declared_count}"
        )

        log(
            f"   stocks = {source_count}"
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
                f"❌ stocks[{key}] 不是 object"
            )

            return []

        symbol = clean_code(
            raw.get(
                "symbol",
                key,
            )
        )

        if not is_valid_symbol(
            symbol
        ):

            log(
                f"❌ 無效代號：{symbol}"
            )

            return []

        if symbol in seen:

            log(
                f"❌ Universe 重複代號："
                f"{symbol}"
            )

            return []

        seen.add(
            symbol
        )

        market = str(
            raw.get(
                "market",
                "",
            )
        ).strip().upper()

        if market not in {
            "TWSE",
            "TPEX",
        }:

            log(
                f"❌ {symbol} market 無效："
                f"{market}"
            )

            return []

        name = clean_name(
            raw.get(
                "name",
                symbol,
            )
        )

        if not name:

            log(
                f"❌ {symbol} name 為空"
            )

            return []

        full_symbol = str(
            raw.get(
                "full_symbol",
                "",
            )
        ).strip()

        if not full_symbol:

            suffix = (
                ".TWO"
                if market == "TPEX"
                else ".TW"
            )

            full_symbol = (
                f"{symbol}{suffix}"
            )

        # ----------------------------------------------------
        # 最重要：
        # 不再自行推測 type。
        # ----------------------------------------------------

        raw_type = str(
            raw.get(
                "type",
                "",
            )
        ).strip()

        instrument_type = str(
            raw.get(
                "instrument_type",
                "",
            )
        ).strip()

        status = str(
            raw.get(
                "status",
                "",
            )
        ).strip()

        if not raw_type:

            log(
                f"❌ {symbol} Universe 缺少 type"
            )

            return []

        securities.append(
            {
                "symbol": symbol,
                "full_symbol": full_symbol,
                "name": name,
                "market": market,
                "type": raw_type,
                "instrument_type": instrument_type,
                "status": status,
            }
        )

    if len(securities) != source_count:

        log(
            "❌ Universe 解析後數量不一致"
        )

        return []

    # --------------------------------------------------------
    # Statistics
    # --------------------------------------------------------

    type_counts: Dict[str, int] = {}

    instrument_counts: Dict[str, int] = {}

    market_counts: Dict[str, int] = {}

    for item in securities:

        type_name = item["type"]

        instrument_name = (
            item["instrument_type"]
            or "UNKNOWN"
        )

        market_name = item["market"]

        type_counts[type_name] = (
            type_counts.get(
                type_name,
                0,
            ) + 1
        )

        instrument_counts[
            instrument_name
        ] = (
            instrument_counts.get(
                instrument_name,
                0,
            ) + 1
        )

        market_counts[market_name] = (
            market_counts.get(
                market_name,
                0,
            ) + 1
        )

    log("")
    log(
        f"✓ Universe："
        f"{len(securities)}"
    )

    log(
        f"✓ Type："
        f"{type_counts}"
    )

    log(
        f"✓ Instrument："
        f"{instrument_counts}"
    )

    log(
        f"✓ Market："
        f"{market_counts}"
    )

    return securities


# ============================================================
# Universe final verification
# ============================================================

def verify_universe(
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
            f"❌ Universe 重讀失敗：{exc}"
        )

        return False

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        return False

    if len(stocks) != len(
        securities
    ):

        log(
            "❌ Universe / fetch_chip "
            "數量不一致"
        )

        return False

    for item in securities:

        symbol = item["symbol"]

        raw = stocks.get(
            symbol
        )

        if not isinstance(
            raw,
            dict,
        ):

            log(
                f"❌ Universe 缺少："
                f"{symbol}"
            )

            return False

        if str(
            raw.get(
                "type",
                "",
            )
        ).strip() != item["type"]:

            log(
                f"❌ {symbol} type 被改變"
            )

            return False

        if str(
            raw.get(
                "instrument_type",
                "",
            )
        ).strip() != item[
            "instrument_type"
        ]:

            log(
                f"❌ {symbol} "
                f"instrument_type 被改變"
            )

            return False

    log(
        "✓ Universe identity "
        "100% 保留"
    )

    return True


# ============================================================
# TWSE institutional
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

    result: Dict[str, float] = {}

    if not isinstance(
        data,
        dict,
    ):

        return result

    if data.get(
        "stat"
    ) != "OK":

        return result

    fields = data.get(
        "fields",
        []
    )

    rows = data.get(
        "data",
        []
    )

    if not isinstance(
        rows,
        list,
    ):

        return result

    code_index = find_column(
        fields,
        [
            "證券代號",
            "股票代號",
        ],
    )

    total_net_index = find_column(
        fields,
        [
            "三大法人買賣超股數",
            "三大法人買賣超",
        ],
    )

    # 官方目前通常為 row[18]，
    # 但只有找不到表頭時才使用官方已知結構。
    if total_net_index is None:
        total_net_index = 18

    if code_index is None:
        code_index = 0

    for row in rows:

        if not isinstance(
            row,
            list,
        ):

            continue

        if (
            code_index >= len(row)
            or total_net_index >= len(row)
        ):

            continue

        symbol = clean_code(
            row[code_index]
        )

        if not is_valid_symbol(
            symbol
        ):

            continue

        value = safe_number(
            row[total_net_index]
        )

        if value is None:
            continue

        result[symbol] = round(
            value / 1000.0,
            2,
        )

    return result


# ============================================================
# TPEx institutional
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
        "d": roc_date(
            date_obj
        ),
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

    rows = parser.rows

    if not rows:
        return {}

    header_index = None

    code_index = None

    net_index = None

    # --------------------------------------------------------
    # 不再使用最後一個數字。
    # 先找真正表頭。
    # --------------------------------------------------------

    for index, row in enumerate(
        rows
    ):

        normalized = [
            normalize_field_name(
                x
            )
            for x in row
        ]

        has_code = any(
            "證券代號" in x
            or "股票代號" in x
            for x in normalized
        )

        has_net = any(
            "三大法人" in x
            and (
                "買賣超" in x
                or "買賣差額" in x
            )
            for x in normalized
        )

        if has_code:

            header_index = index

            for i, field in enumerate(
                normalized
            ):

                if (
                    "證券代號" in field
                    or "股票代號" in field
                ):

                    code_index = i
                    break

            if has_net:

                for i, field in enumerate(
                    normalized
                ):

                    if (
                        "三大法人" in field
                        and (
                            "買賣超" in field
                            or "買賣差額" in field
                        )
                    ):

                        net_index = i
                        break

            if (
                code_index is not None
                and net_index is not None
            ):

                break

    if (
        header_index is None
        or code_index is None
        or net_index is None
    ):

        return {}

    result: Dict[str, float] = {}

    for row in rows[
        header_index + 1:
    ]:

        if (
            code_index >= len(row)
            or net_index >= len(row)
        ):

            continue

        symbol = clean_code(
            row[code_index]
        )

        if not is_valid_symbol(
            symbol
        ):

            continue

        value = safe_number(
            row[net_index]
        )

        if value is None:
            continue

        result[symbol] = round(
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

    result = dict(
        twse
    )

    result.update(
        tpex
    )

    return result


# ============================================================
# Institutional history
# ============================================================

def fetch_history(
    days: int = HISTORY_DAYS,
) -> Tuple[
    Optional[str],
    List[str],
    Dict[
        str,
        Dict[str, float],
    ],
]:

    section(
        f"同步最近 {days} 個交易日三大法人"
    )

    trading_days: List[str] = []

    daily_data: Dict[
        str,
        Dict[str, float],
    ] = {}

    current = now_taiwan().replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    attempted = 0

    while (
        len(trading_days) < days
        and attempted < MAX_LOOKBACK_DAYS
    ):

        if current.weekday() < 5:

            date_text = current.strftime(
                "%Y-%m-%d"
            )

            log(
                f"[{len(trading_days) + 1}/"
                f"{days}] {date_text}"
            )

            data = fetch_daily_institutional(
                current
            )

            if data:

                trading_days.append(
                    date_text
                )

                daily_data[
                    date_text
                ] = data

                log(
                    f"      ✓ "
                    f"{len(data)} 檔"
                )

            else:

                log(
                    "      ⚠️ 無有效資料"
                )

            time.sleep(
                REQUEST_SLEEP
            )

        current -= timedelta(
            days=1
        )

        attempted += 1

    if len(
        trading_days
    ) == 0:

        return None, [], {}

    latest_date = trading_days[0]

    log("")
    log(
        f"✓ 實際交易日："
        f"{len(trading_days)}"
    )

    log(
        f"✓ 最新日期："
        f"{latest_date}"
    )

    return (
        latest_date,
        trading_days,
        daily_data,
    )


# ============================================================
# Period calculation
# ============================================================

def calculate_periods(
    symbol: str,
    trading_days: List[str],
    daily_data: Dict[
        str,
        Dict[str, float],
    ],
) -> Dict[str, Optional[float]]:

    values: List[float] = []

    for date_text in trading_days:

        day = daily_data.get(
            date_text,
            {}
        )

        if symbol not in day:

            break

        values.append(
            day[symbol]
        )

    result = {
        "institutional_1d":
            None,

        "institutional_5d":
            None,

        "institutional_10d":
            None,

        "institutional_20d":
            None,
    }

    if len(values) >= 1:

        result[
            "institutional_1d"
        ] = values[0]

    if len(values) >= 5:

        result[
            "institutional_5d"
        ] = round(
            sum(values[:5]),
            2,
        )

    if len(values) >= 10:

        result[
            "institutional_10d"
        ] = round(
            sum(values[:10]),
            2,
        )

    if len(values) >= 20:

        result[
            "institutional_20d"
        ] = round(
            sum(values[:20]),
            2,
        )

    return result


# ============================================================
# TWSE day trade
# ============================================================

def fetch_twse_daytrade(
    date_str: str,
) -> Dict[str, float]:

    """
    TWSE 官方 TWTB4U。

    不再使用：
        /historical/day-trading

    正式資料：
        /afterTrading/TWTB4U
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

        code_index = find_column(
            fields,
            [
                "證券代號",
                "股票代號",
            ],
        )

        volume_index = find_column(
            fields,
            [
                "當日沖銷交易成交股數",
                "當沖成交股數",
                "當日沖銷成交股數",
            ],
        )

        if (
            code_index is None
            or volume_index is None
        ):
            continue

        for row in rows:

            if (
                code_index >= len(row)
                or volume_index >= len(row)
            ):
                continue

            symbol = clean_code(
                row[code_index]
            )

            if not is_valid_symbol(
                symbol
            ):
                continue

            volume = safe_number(
                row[volume_index]
            )

            if volume is None:
                continue

            result[symbol] = round(
                volume,
                2,
            )

    return result


# ============================================================
# TWSE total volume
# ============================================================

def fetch_twse_total_volume(
    date_str: str,
) -> Dict[str, float]:

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

        code_index = find_column(
            fields,
            [
                "證券代號",
                "股票代號",
            ],
        )

        volume_index = find_column(
            fields,
            [
                "成交股數",
            ],
        )

        if (
            code_index is None
            or volume_index is None
        ):
            continue

        for row in rows:

            if (
                code_index >= len(row)
                or volume_index >= len(row)
            ):
                continue

            symbol = clean_code(
                row[code_index]
            )

            if not is_valid_symbol(
                symbol
            ):
                continue

            volume = safe_number(
                row[volume_index]
            )

            if volume is None:
                continue

            result[symbol] = volume

    return result


# ============================================================
# TPEx day trade
# ============================================================

def fetch_tpex_daytrade(
    date_obj: datetime,
) -> Dict[str, float]:

    """
    TPEx 官方現股當沖統計。

    只接受明確欄位：
        證券代號
        當日沖銷交易成交股數

    不取第一個數字。
    不取最後一個數字。
    """

    url = (
        "https://www.tpex.org.tw/"
        "storage/zh-tw/web/stock/trading/"
        "intraday_stat/intraday_trading_statY.htm"
    )

    try:

        response = session.get(
            url,
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

    rows = parser.rows

    header_index = None

    code_index = None

    volume_index = None

    for index, row in enumerate(
        rows
    ):

        normalized = [
            normalize_field_name(
                x
            )
            for x in row
        ]

        has_code = any(
            "證券代號" in x
            for x in normalized
        )

        has_volume = any(
            "當日沖銷交易成交股數" in x
            or "當沖成交股數" in x
            for x in normalized
        )

        if has_code and has_volume:

            header_index = index

            for i, field in enumerate(
                normalized
            ):

                if "證券代號" in field:

                    code_index = i

                if (
                    "當日沖銷交易成交股數"
                    in field
                    or "當沖成交股數"
                    in field
                ):

                    volume_index = i

            break

    if (
        header_index is None
        or code_index is None
        or volume_index is None
    ):

        return {}

    result = {}

    for row in rows[
        header_index + 1:
    ]:

        if (
            code_index >= len(row)
            or volume_index >= len(row)
        ):
            continue

        symbol = clean_code(
            row[code_index]
        )

        if not is_valid_symbol(
            symbol
        ):
            continue

        volume = safe_number(
            row[volume_index]
        )

        if volume is None:
            continue

        result[symbol] = round(
            volume,
            2,
        )

    return result


# ============================================================
# TPEx individual daily volume
# ============================================================

def fetch_tpex_stock_month(
    symbol: str,
    date_obj: datetime,
) -> Optional[float]:

    """
    TPEx 官方個股日成交資訊。

    Endpoint：
        /afterTrading/tradingStock

    回傳：
        最新交易日成交股數

    TPEx API 的「成交張數」轉成股數：
        張數 × 1000
    """

    url = (
        "https://www.tpex.org.tw/"
        "www/zh-tw/afterTrading/"
        "tradingStock"
    )

    month_date = date_obj.replace(
        day=1
    )

    params = {
        "date": month_date.strftime(
            "%Y/%m/%d"
        ),
        "code": symbol,
        "response": "json",
    }

    data = get_json(
        url,
        params,
    )

    if not isinstance(
        data,
        dict,
    ):
        return None

    records = extract_table_records(
        data
    )

    if not records:
        return None

    for fields, rows in records:

        volume_index = find_column(
            fields,
            [
                "成交張數",
            ],
        )

        date_index = find_column(
            fields,
            [
                "日期",
            ],
        )

        if (
            volume_index is None
            or date_index is None
        ):
            continue

        latest_date = None

        latest_volume = None

        for row in rows:

            if (
                date_index >= len(row)
                or volume_index >= len(row)
            ):
                continue

            row_date = str(
                row[date_index]
            ).strip()

            volume_lots = safe_number(
                row[volume_index]
            )

            if (
                not row_date
                or volume_lots is None
            ):
                continue

            latest_date = row_date

            latest_volume = volume_lots

        if latest_volume is not None:

            return round(
                latest_volume * 1000.0,
                2,
            )

    return None


def fetch_tpex_total_volume(
    symbols: List[str],
    date_obj: datetime,
) -> Dict[str, float]:

    section(
        "同步 TPEx 個股總成交股數"
    )

    result: Dict[str, float] = {}

    if not symbols:
        return result

    # --------------------------------------------------------
    # 只查真正需要計算當沖率的標的。
    # 不對 1011 檔全部盲打。
    # --------------------------------------------------------

    unique_symbols = sorted(
        set(symbols)
    )

    log(
        f"TPEx 需要查詢："
        f"{len(unique_symbols)} 檔"
    )

    with ThreadPoolExecutor(
        max_workers=TPEX_VOLUME_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                fetch_tpex_stock_month,
                symbol,
                date_obj,
            ): symbol
            for symbol in unique_symbols
        }

        for index, future in enumerate(
            as_completed(futures),
            start=1,
        ):

            symbol = futures[
                future
            ]

            try:

                value = future.result()

            except Exception:

                value = None

            if value is not None:

                result[symbol] = value

            if (
                index % 100 == 0
                or index == len(
                    unique_symbols
                )
            ):

                log(
                    f"      "
                    f"{index}/"
                    f"{len(unique_symbols)}"
                    f"  有效={len(result)}"
                )

    return result


# ============================================================
# Day trade combined
# ============================================================

def fetch_daytrade(
    data_date: str,
    securities: List[
        Dict[str, Any]
    ],
) -> Tuple[
    Dict[
        str,
        Dict[str, Optional[float]],
    ],
    Dict[str, int],
]:

    section(
        "同步當沖資料"
    )

    date_obj = datetime.strptime(
        data_date,
        "%Y-%m-%d",
    )

    date_str = yyyymmdd(
        date_obj
    )

    twse_daytrade = (
        fetch_twse_daytrade(
            date_str
        )
    )

    log(
        f"TWSE 當沖："
        f"{len(twse_daytrade)}"
    )

    time.sleep(
        REQUEST_SLEEP
    )

    twse_volume = (
        fetch_twse_total_volume(
            date_str
        )
    )

    log(
        f"TWSE 成交量："
        f"{len(twse_volume)}"
    )

    time.sleep(
        REQUEST_SLEEP
    )

    tpex_daytrade = (
        fetch_tpex_daytrade(
            date_obj
        )
    )

    log(
        f"TPEx 當沖："
        f"{len(tpex_daytrade)}"
    )

    # --------------------------------------------------------
    # 只對 TPEx 當沖標的取得總成交量
    # --------------------------------------------------------

    tpex_symbols = [
        symbol
        for symbol in tpex_daytrade.keys()
    ]

    tpex_volume = (
        fetch_tpex_total_volume(
            tpex_symbols,
            date_obj,
        )
    )

    log(
        f"TPEx 成交量："
        f"{len(tpex_volume)}"
    )

    result = {}

    for item in securities:

        symbol = item[
            "symbol"
        ]

        market = item[
            "market"
        ]

        daytrade_volume = None

        total_volume = None

        if market == "TWSE":

            daytrade_volume = (
                twse_daytrade.get(
                    symbol
                )
            )

            total_volume = (
                twse_volume.get(
                    symbol
                )
            )

        elif market == "TPEX":

            daytrade_volume = (
                tpex_daytrade.get(
                    symbol
                )
            )

            total_volume = (
                tpex_volume.get(
                    symbol
                )
            )

        rate = None

        if (
            daytrade_volume is not None
            and total_volume is not None
            and total_volume > 0
        ):

            calculated = (
                daytrade_volume
                /
                total_volume
                *
                100.0
            )

            if (
                math.isfinite(
                    calculated
                )
                and 0 <= calculated <= 100
            ):

                rate = round(
                    calculated,
                    2,
                )

        result[symbol] = {

            "day_trading_volume":
                daytrade_volume,

            "total_volume":
                total_volume,

            "day_trading_rate":
                rate,
        }

    statistics = {

        "twse_daytrade_source":
            len(twse_daytrade),

        "twse_total_volume_source":
            len(twse_volume),

        "tpex_daytrade_source":
            len(tpex_daytrade),

        "tpex_total_volume_source":
            len(tpex_volume),

        "day_trading_volume":
            sum(
                1
                for x in result.values()
                if x[
                    "day_trading_volume"
                ] is not None
            ),

        "total_volume":
            sum(
                1
                for x in result.values()
                if x[
                    "total_volume"
                ] is not None
            ),

        "day_trading_rate":
            sum(
                1
                for x in result.values()
                if x[
                    "day_trading_rate"
                ] is not None
            ),
    }

    return result, statistics


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
        Dict[str, Any],
    ],
) -> bool:

    for symbol, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):
            continue

        for field in FORBIDDEN_FIELDS:

            if field in item:

                log(
                    f"❌ {symbol}.{field} "
                    f"禁止存在"
                )

                return False

    return True


# ============================================================
# Build chip
# ============================================================

def build_chip(
    securities: List[
        Dict[str, Any]
    ],
    trading_days: List[str],
    daily_data: Dict[
        str,
        Dict[str, float],
    ],
    daytrade: Dict[
        str,
        Dict[str, Optional[float]],
    ],
    data_date: str,
) -> Tuple[
    Dict[
        str,
        Dict[str, Any],
    ],
    Dict[str, int],
]:

    stocks = {}

    complete_1d = 0

    complete_5d = 0

    complete_10d = 0

    complete_20d = 0

    daytrade_volume_count = 0

    total_volume_count = 0

    daytrade_rate_count = 0

    insufficient = 0

    for item in securities:

        symbol = item[
            "symbol"
        ]

        periods = calculate_periods(
            symbol,
            trading_days,
            daily_data,
        )

        if (
            periods[
                "institutional_1d"
            ]
            is not None
        ):
            complete_1d += 1

        if (
            periods[
                "institutional_5d"
            ]
            is not None
        ):
            complete_5d += 1

        if (
            periods[
                "institutional_10d"
            ]
            is not None
        ):
            complete_10d += 1

        if (
            periods[
                "institutional_20d"
            ]
            is not None
        ):
            complete_20d += 1

        if (
            periods[
                "institutional_1d"
            ]
            is None
        ):
            insufficient += 1

        dt = daytrade.get(
            symbol,
            {},
        )

        daytrade_volume = dt.get(
            "day_trading_volume"
        )

        total_volume = dt.get(
            "total_volume"
        )

        daytrade_rate = dt.get(
            "day_trading_rate"
        )

        if daytrade_volume is not None:
            daytrade_volume_count += 1

        if total_volume is not None:
            total_volume_count += 1

        if daytrade_rate is not None:
            daytrade_rate_count += 1

        # ----------------------------------------------------
        # 完全保留 Universe 身份
        # ----------------------------------------------------

        stocks[symbol] = {

            "symbol":
                symbol,

            "full_symbol":
                item["full_symbol"],

            "name":
                item["name"],

            "market":
                item["market"],

            "type":
                item["type"],

            "instrument_type":
                item["instrument_type"],

            "status":
                item["status"],

            "institutional_1d":
                periods[
                    "institutional_1d"
                ],

            "institutional_5d":
                periods[
                    "institutional_5d"
                ],

            "institutional_10d":
                periods[
                    "institutional_10d"
                ],

            "institutional_20d":
                periods[
                    "institutional_20d"
                ],

            "day_trading_volume":
                daytrade_volume,

            "total_volume":
                total_volume,

            "day_trading_rate":
                daytrade_rate,

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

        "day_trading_volume":
            daytrade_volume_count,

        "total_volume":
            total_volume_count,

        "day_trading_rate":
            daytrade_rate_count,

        "insufficient":
            insufficient,
    }

    return (
        stocks,
        statistics,
    )


# ============================================================
# Structural validation
# ============================================================

def validate_structure(
    stocks: Dict[
        str,
        Dict[str, Any],
    ],
    securities: List[
        Dict[str, Any]
    ],
) -> bool:

    section(
        "全市場 Chip 結構驗證"
    )

    errors = 0

    universe_map = {
        x["symbol"]: x
        for x in securities
    }

    required_fields = {
        "symbol",
        "full_symbol",
        "name",
        "market",
        "type",
        "instrument_type",
        "status",
        "institutional_1d",
        "institutional_5d",
        "institutional_10d",
        "institutional_20d",
        "day_trading_volume",
        "total_volume",
        "day_trading_rate",
        "updated_at",
    }

    if len(stocks) != len(
        securities
    ):

        log(
            "❌ Chip / Universe 數量不一致"
        )

        return False

    for symbol, item in stocks.items():

        if symbol not in universe_map:

            errors += 1

            log(
                f"❌ {symbol} 不在 Universe"
            )

            continue

        universe_item = (
            universe_map[symbol]
        )

        if not isinstance(
            item,
            dict,
        ):

            errors += 1

            continue

        missing = (
            required_fields
            - set(item.keys())
        )

        if missing:

            errors += len(missing)

            log(
                f"❌ {symbol} 缺欄位："
                f"{sorted(missing)}"
            )

            continue

        # ----------------------------------------------------
        # Identity
        # ----------------------------------------------------

        for field in (
            "full_symbol",
            "name",
            "market",
            "type",
            "instrument_type",
            "status",
        ):

            if (
                item[field]
                != universe_item[field]
            ):

                errors += 1

                log(
                    f"❌ {symbol}.{field} "
                    f"與 Universe 不一致"
                )

        # ----------------------------------------------------
        # Rate
        # ----------------------------------------------------

        rate = item[
            "day_trading_rate"
        ]

        if rate is not None:

            if (
                not isinstance(
                    rate,
                    (int, float),
                )
                or not math.isfinite(
                    float(rate)
                )
                or rate < 0
                or rate > 100
            ):

                errors += 1

                log(
                    f"❌ {symbol} "
                    f"當沖率非法："
                    f"{rate}"
                )

        dt_volume = item[
            "day_trading_volume"
        ]

        total_volume = item[
            "total_volume"
        ]

        if (
            dt_volume is not None
            and total_volume is not None
            and total_volume > 0
        ):

            expected_rate = round(
                (
                    dt_volume
                    /
                    total_volume
                )
                * 100.0,
                2,
            )

            if rate != expected_rate:

                errors += 1

                log(
                    f"❌ {symbol} "
                    f"當沖率錯誤"
                )

        # ----------------------------------------------------
        # Forbidden
        # ----------------------------------------------------

    if not scan_forbidden_fields(
        stocks
    ):

        return False

    if errors:

        log(
            f"❌ 結構驗證失敗："
            f"{errors}"
        )

        return False

    log(
        f"✓ {len(stocks)} 檔 "
        f"Chip 結構驗證通過"
    )

    return True


# ============================================================
# Source quality validation
# ============================================================

def validate_source_quality(
    securities: List[
        Dict[str, Any]
    ],
    statistics: Dict[str, int],
) -> bool:

    section(
        "資料來源品質驗證"
    )

    twse_count = sum(
        1
        for x in securities
        if x["market"] == "TWSE"
    )

    tpex_count = sum(
        1
        for x in securities
        if x["market"] == "TPEX"
    )

    # --------------------------------------------------------
    # TWSE 當沖 source 完全失敗直接停止。
    # --------------------------------------------------------

    if (
        twse_count > 0
        and statistics[
            "twse_daytrade_source"
        ] == 0
    ):

        log(
            "❌ TWSE 當沖來源為 0"
        )

        return False

    if (
        twse_count > 0
        and statistics[
            "twse_total_volume_source"
        ] == 0
    ):

        log(
            "❌ TWSE 成交量來源為 0"
        )

        return False

    # --------------------------------------------------------
    # TPEx：
    # 只要當沖來源完全失敗，就不能宣告完整成功。
    # --------------------------------------------------------

    if (
        tpex_count > 0
        and statistics[
            "tpex_daytrade_source"
        ] == 0
    ):

        log(
            "❌ TPEx 當沖來源為 0"
        )

        return False

    if (
        tpex_count > 0
        and statistics[
            "tpex_total_volume_source"
        ] == 0
        and statistics[
            "tpex_daytrade_source"
        ] > 0
    ):

        log(
            "❌ TPEx 成交量來源為 0"
        )

        return False

    log(
        "✓ 資料來源品質通過"
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
    securities: List[
        Dict[str, Any]
    ],
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
            f"❌ JSON 解析失敗："
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
            "❌ Chip 數量錯誤"
        )

        return False

    if not validate_structure(
        stocks,
        securities,
    ):

        return False

    log(
        f"✓ chip.json："
        f"{len(stocks)} 檔"
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
        f"開始："
        f"{now_taiwan().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    log("")
    log(
        "正式資料鏈："
    )

    log(
        "  Data/universe.json"
    )

    log(
        "        ↓"
    )

    log(
        "  Scripts/fetch_chip.py"
    )

    log(
        "        ↓"
    )

    log(
        "  Data/chip.json"
    )

    # ========================================================
    # 1. Universe
    # ========================================================

    securities = load_universe()

    if not securities:

        return 1

    if not verify_universe(
        securities
    ):

        return 1

    # ========================================================
    # 2. Institutional history
    # ========================================================

    (
        data_date,
        trading_days,
        daily_data,
    ) = fetch_history(
        HISTORY_DAYS
    )

    if (
        not data_date
        or len(trading_days)
        < HISTORY_DAYS
    ):

        log(
            "❌ 無法取得完整 20 個交易日"
        )

        log(
            "❌ 不覆蓋既有 chip.json"
        )

        return 1

    # ========================================================
    # 3. Day trading
    # ========================================================

    (
        daytrade,
        daytrade_statistics,
    ) = fetch_daytrade(
        data_date,
        securities,
    )

    # ========================================================
    # 4. Build
    # ========================================================

    section(
        "建立全市場 Chip"
    )

    (
        stocks,
        statistics,
    ) = build_chip(
        securities,
        trading_days,
        daily_data,
        daytrade,
        data_date,
    )

    statistics.update(
        daytrade_statistics
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
        stocks,
        securities,
    ):

        return 1

    # ========================================================
    # 7. Source quality
    # ========================================================

    if not validate_source_quality(
        securities,
        statistics,
    ):

        log(
            "❌ 資料來源品質未通過"
        )

        log(
            "❌ 不覆蓋既有 chip.json"
        )

        return 1

    # ========================================================
    # 8. Counts
    # ========================================================

    type_counts: Dict[str, int] = {}

    instrument_counts: Dict[
        str,
        int,
    ] = {}

    twse_count = 0

    tpex_count = 0

    for item in securities:

        type_name = item[
            "type"
        ]

        instrument_name = (
            item[
                "instrument_type"
            ]
            or "UNKNOWN"
        )

        type_counts[type_name] = (
            type_counts.get(
                type_name,
                0,
            ) + 1
        )

        instrument_counts[
            instrument_name
        ] = (
            instrument_counts.get(
                instrument_name,
                0,
            ) + 1
        )

        if item[
            "market"
        ] == "TWSE":

            twse_count += 1

        else:

            tpex_count += 1

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

        "type_counts":
            type_counts,

        "instrument_counts":
            instrument_counts,

        "twse_count":
            twse_count,

        "tpex_count":
            tpex_count,

        "trading_days":
            trading_days,

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
            "❌ 最終數量驗證失敗"
        )

        return 1

    # ========================================================
    # 10. Atomic Write
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
        len(securities),
        securities,
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
        "全市場 CHIP BUILD RESULT"
    )

    log(
        f"Universe："
        f"{len(securities)}"
    )

    log(
        f"Chip："
        f"{len(stocks)}"
    )

    log(
        f"TWSE："
        f"{twse_count}"
    )

    log(
        f"TPEX："
        f"{tpex_count}"
    )

    log("")
    log(
        "Universe Type："
    )

    for key, value in sorted(
        type_counts.items()
    ):

        log(
            f"  {key}: {value}"
        )

    log("")
    log(
        "Instrument Type："
    )

    for key, value in sorted(
        instrument_counts.items()
    ):

        log(
            f"  {key}: {value}"
        )

    log("")
    log(
        "三大法人完整度："
    )

    log(
        f"  1D  = "
        f"{statistics['complete_1d']}"
    )

    log(
        f"  5D  = "
        f"{statistics['complete_5d']}"
    )

    log(
        f"  10D = "
        f"{statistics['complete_10d']}"
    )

    log(
        f"  20D = "
        f"{statistics['complete_20d']}"
    )

    log("")
    log(
        "當沖完整度："
    )

    log(
        f"  當沖成交股數 = "
        f"{statistics['day_trading_volume']}"
    )

    log(
        f"  總成交股數 = "
        f"{statistics['total_volume']}"
    )

    log(
        f"  當沖率 = "
        f"{statistics['day_trading_rate']}"
    )

    log("")
    log(
        "禁止欄位："
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
        "============================================================"
    )

    log(
        "CHIP BUILD PASS"
    )

    log(
        "============================================================"
    )

    log(
        f"✓ fetch_chip.py {VERSION}"
    )

    log(
        f"✓ 全市場 {len(stocks)} 檔"
    )

    log(
        f"✓ 耗時：{elapsed:.1f} 秒"
    )

    return 0


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )