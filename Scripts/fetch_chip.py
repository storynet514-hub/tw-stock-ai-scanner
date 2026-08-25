#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V10.3.0

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
V10.3.0
============================================================

本版本只修正 A：

    「當沖率資料鏈」

不修改：

    Universe
    Universe 股票分類
    三大法人 1D
    三大法人 5D
    三大法人 10D
    三大法人 20D
    main_force_*
    
============================================================
A 修正原則
============================================================

1. 當沖成交股數必須有明確欄位
2. 總成交股數必須有明確欄位
3. 兩個來源必須對應同一交易日
4. 不猜固定 index
5. 不取第一個數字
6. 不取最後一個數字
7. TPEx 不取最後一列冒充目標日期
8. 缺日期 = None
9. 缺資料 = None
10. 不用 0 補資料
11. 當沖率 = 當沖成交股數 / 總成交股數 × 100
12. 當沖率必須介於 0~100
13. 當沖成交量不得大於總成交量
14. 寫入前後驗證
15. Universe / Chip 數量必須一致
16. Atomic Write

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

VERSION = "V10.3.0"


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
    return now_taiwan().strftime("%Y-%m-%d")


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
            ).strftime("%Y-%m-%d")

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
        ).strftime("%Y-%m-%d")

    except Exception:

        return None


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

    return date_obj.strftime("%Y%m%d")


# ============================================================
# Symbol validation
# ============================================================

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

    fields = data.get("fields")
    rows = data.get("data")

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

    tables = data.get("tables")

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

            table_fields = table.get("fields")
            table_rows = table.get("data")

            if (
                isinstance(table_fields, list)
                and isinstance(table_rows, list)
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

            if key == field:
                return index

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

    stocks = data.get("stocks")

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

    try:

        declared_count = (
            int(declared_count)
            if declared_count is not None
            else None
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
        and declared_count != source_count
    ):

        log(
            "❌ Universe 數量矛盾"
        )

        log(
            f"   header = "
            f"{declared_count}"
        )

        log(
            f"   stocks = "
            f"{source_count}"
        )

        return []

    securities = []

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

        if not is_valid_symbol(symbol):

            log(
                f"❌ Universe 無效代號："
                f"{symbol}"
            )

            return []

        market = str(
            item.get(
                "market",
                "",
            )
        ).strip().upper()

        full_symbol = str(
            item.get(
                "full_symbol",
                "",
            )
        ).strip()

        if market not in {
            "TWSE",
            "TPEX",
        }:

            if (
                full_symbol.upper().endswith(
                    ".TWO"
                )
            ):

                market = "TPEX"

            elif (
                full_symbol.upper().endswith(
                    ".TW"
                )
            ):

                market = "TWSE"

            else:

                log(
                    f"❌ Universe "
                    f"{symbol} 缺少有效 market"
                )

                return []

        if not full_symbol:

            full_symbol = (
                f"{symbol}.TWO"
                if market == "TPEX"
                else f"{symbol}.TW"
            )

        sec_type = str(
            item.get(
                "type",
                "Stock",
            )
        ).strip()

        if sec_type not in {
            "Stock",
            "ETF",
        }:

            sec_type = "Stock"

        securities.append(
            {
                "symbol": symbol,
                "full_symbol": full_symbol,
                "name": clean_name(
                    item.get(
                        "name",
                        symbol,
                    )
                ) or symbol,
                "market": market,
                "type": sec_type,
                "instrument_type": item.get(
                    "instrument_type",
                    item.get(
                        "type",
                        sec_type,
                    ),
                ),
                "status": item.get(
                    "status"
                ),
            }
        )

    if len(securities) != source_count:

        log(
            "❌ Universe / "
            "fetch_chip 數量不一致"
        )

        return []

    if (
        declared_count is not None
        and len(securities) != declared_count
    ):

        log(
            "❌ Universe header / "
            "fetch_chip 數量不一致"
        )

        return []

    log(
        f"✓ Universe："
        f"{len(securities)} 檔"
    )

    log(
        f"✓ TWSE："
        f"{sum(x['market'] == 'TWSE' for x in securities)}"
    )

    log(
        f"✓ TPEX："
        f"{sum(x['market'] == 'TPEX' for x in securities)}"
    )

    log(
        f"✓ Stock："
        f"{sum(x['type'] == 'Stock' for x in securities)}"
    )

    log(
        f"✓ ETF："
        f"{sum(x['type'] == 'ETF' for x in securities)}"
    )

    return securities


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

    result = {}

    if not isinstance(
        data,
        dict,
    ):
        return result

    if data.get("stat") != "OK":
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

        if not is_valid_symbol(symbol):
            continue

        net = safe_number(
            row[18]
        )

        if net is None:
            continue

        result[symbol] = round(
            net / 1000.0,
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

        if len(row) < 2:
            continue

        symbol = clean_code(
            row[0]
        )

        if not is_valid_symbol(symbol):
            continue

        # 找到所有可解析數值。
        # TPEx 法人表最後一組為三大法人買賣超。
        candidates = []

        for value in row[1:]:

            number = safe_number(
                value
            )

            if number is not None:
                candidates.append(
                    number
                )

        if not candidates:
            continue

        net = candidates[-1]

        result[symbol] = round(
            net / 1000.0,
            2,
        )

    return result


# ============================================================
# Daily institutional
# ============================================================

def fetch_daily_institutional(
    date_obj: datetime,
) -> Dict[str, float]:

    result = fetch_twse_institutional(
        yyyymmdd(date_obj)
    )

    time.sleep(
        REQUEST_SLEEP
    )

    tpex = fetch_tpex_institutional(
        date_obj
    )

    result.update(tpex)

    return result


# ============================================================
# Institutional history
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
        and attempted < MAX_LOOKBACK_DAYS
    ):

        if current.weekday() < 5:

            date_text = current.strftime(
                "%Y-%m-%d"
            )

            log(
                f"[{successful_days + 1}/"
                f"{days}] {date_text}"
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
        f"✓ 歷史標的："
        f"{len(history)}"
    )

    return latest_date, history


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
        sum(values[:days]),
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
    嚴格取得 TWSE 當日沖銷交易成交股數。

    絕不：
        猜 index
        取第一個數字
        取最後一個數字
    """

    url = (
        "https://www.twse.com.tw/"
        "rwd/zh/trading/"
        "historical/day-trading"
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

    for fields, rows in extract_table_records(
        data
    ):

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

            if not is_valid_symbol(symbol):
                continue

            volume = safe_number(
                row[volume_index]
            )

            if volume is None:
                continue

            if volume < 0:
                continue

            result[symbol] = round(
                volume,
                2,
            )

    return result


# ============================================================
# A-2
# TWSE total volume
# ============================================================

def fetch_twse_total_volume(
    date_str: str,
) -> Dict[str, float]:

    """
    嚴格取得 TWSE 個股成交股數。

    必須：
        同一 table
        證券代號
        成交股數
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

    for fields, rows in extract_table_records(
        data
    ):

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

            if not is_valid_symbol(symbol):
                continue

            volume = safe_number(
                row[volume_index]
            )

            if volume is None:
                continue

            if volume < 0:
                continue

            result[symbol] = round(
                volume,
                2,
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
    嚴格取得 TPEx 當沖成交股數。

    必須找到：

        證券代號
        當日沖銷交易成交股數

    不使用：
        第一個數字
        最後一個數字
        固定 index
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
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:

            log(
                f"      TPEx 當沖 HTTP "
                f"{response.status_code}"
            )

            return {}

        parser = TableParser()

        parser.feed(
            response.text
        )

    except Exception as exc:

        log(
            f"      TPEx 當沖取得失敗："
            f"{exc}"
        )

        return {}

    rows = parser.rows

    target_date = date_obj.strftime(
        "%Y-%m-%d"
    )

    header_index = None
    code_index = None
    volume_index = None
    date_index = None

    # --------------------------------------------------------
    # 找 header
    # --------------------------------------------------------

    for index, row in enumerate(rows):

        normalized = [
            normalize_field_name(x)
            for x in row
        ]

        has_code = any(
            (
                "證券代號" in x
                or "股票代號" in x
            )
            for x in normalized
        )

        has_volume = any(
            (
                "當日沖銷交易成交股數" in x
                or "當沖成交股數" in x
                or "當日沖銷成交股數" in x
            )
            for x in normalized
        )

        if not (
            has_code
            and has_volume
        ):
            continue

        header_index = index

        for i, field in enumerate(
            normalized
        ):

            if (
                "證券代號" in field
                or "股票代號" in field
            ):

                code_index = i

            if (
                "當日沖銷交易成交股數"
                in field
                or "當沖成交股數"
                in field
                or "當日沖銷成交股數"
                in field
            ):

                volume_index = i

            if (
                "日期" in field
                or "交易日期" in field
            ):

                date_index = i

        break

    if (
        header_index is None
        or code_index is None
        or volume_index is None
    ):

        log(
            "      ⚠️ TPEx 當沖找不到 "
            "明確欄位"
        )

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

        # ----------------------------------------------------
        # 若資料表本身有日期欄，必須驗證
        # ----------------------------------------------------

        if date_index is not None:

            if date_index >= len(row):
                continue

            row_date = normalize_date_text(
                row[date_index]
            )

            if (
                row_date is not None
                and row_date != target_date
            ):
                continue

        symbol = clean_code(
            row[code_index]
        )

        if not is_valid_symbol(symbol):
            continue

        volume = safe_number(
            row[volume_index]
        )

        if volume is None:
            continue

        if volume < 0:
            continue

        result[symbol] = round(
            volume,
            2,
        )

    return result


# ============================================================
# A-4
# TPEx individual total volume
# ============================================================

def fetch_tpex_stock_volume(
    symbol: str,
    target_date: datetime,
) -> Optional[float]:

    """
    TPEx 個股日成交資訊。

    最重要規則：

        找 target_date 對應 row

    絕不：

        直接取最後一列。
    """

    url = (
        "https://www.tpex.org.tw/"
        "www/zh-tw/afterTrading/"
        "tradingStock"
    )

    month_date = target_date.replace(
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

    target_date_text = target_date.strftime(
        "%Y-%m-%d"
    )

    records = extract_table_records(
        data
    )

    for fields, rows in records:

        date_index = find_column(
            fields,
            [
                "日期",
                "交易日期",
            ],
        )

        volume_index = find_column(
            fields,
            [
                "成交張數",
            ],
        )

        if (
            date_index is None
            or volume_index is None
        ):
            continue

        for row in rows:

            if (
                date_index >= len(row)
                or volume_index >= len(row)
            ):
                continue

            row_date = normalize_date_text(
                row[date_index]
            )

            if row_date != target_date_text:
                continue

            volume_lots = safe_number(
                row[volume_index]
            )

            if volume_lots is None:
                continue

            if volume_lots < 0:
                continue

            return round(
                volume_lots * 1000.0,
                2,
            )

    return None


# ============================================================
# A-5
# TPEx total volume
# ============================================================

def fetch_tpex_total_volume(
    symbols: List[str],
    target_date: datetime,
) -> Dict[str, float]:

    section(
        "同步 TPEx 個股總成交股數"
    )

    result = {}

    unique_symbols = sorted(
        set(symbols)
    )

    if not unique_symbols:

        log(
            "TPEx 無需查詢"
        )

        return result

    log(
        f"TPEx 查詢："
        f"{len(unique_symbols)} 檔"
    )

    with ThreadPoolExecutor(
        max_workers=TPEX_VOLUME_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                fetch_tpex_stock_volume,
                symbol,
                target_date,
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
                or index == len(unique_symbols)
            ):

                log(
                    f"      "
                    f"{index}/"
                    f"{len(unique_symbols)} "
                    f"有效={len(result)}"
                )

    return result


# ============================================================
# A-6
# Combined day-trading data
# ============================================================

def fetch_daytrade(
    data_date: str,
    securities: List[
        Dict[str, Any]
    ],
) -> Tuple[
    Dict[
        str,
        Dict[str, Optional[float]]
    ],
    Dict[str, Any],
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

    # --------------------------------------------------------
    # TWSE
    # --------------------------------------------------------

    twse_daytrade = (
        fetch_twse_daytrade(
            date_str
        )
    )

    log(
        f"TWSE 當沖來源："
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
        f"TWSE 總成交量來源："
        f"{len(twse_volume)}"
    )

    time.sleep(
        REQUEST_SLEEP
    )

    # --------------------------------------------------------
    # TPEx
    # --------------------------------------------------------

    tpex_daytrade = (
        fetch_tpex_daytrade(
            date_obj
        )
    )

    log(
        f"TPEx 當沖來源："
        f"{len(tpex_daytrade)}"
    )

    time.sleep(
        REQUEST_SLEEP
    )

    tpex_symbols = sorted(
        set(
            tpex_daytrade.keys()
        )
    )

    tpex_volume = (
        fetch_tpex_total_volume(
            tpex_symbols,
            date_obj,
        )
    )

    log(
        f"TPEx 總成交量來源："
        f"{len(tpex_volume)}"
    )

    # --------------------------------------------------------
    # Build
    # --------------------------------------------------------

    result = {}

    for item in securities:

        symbol = item["symbol"]

        market = item["market"]

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

        # ----------------------------------------------------
        # A 核心驗證
        # ----------------------------------------------------

        if (
            daytrade_volume is not None
            and total_volume is not None
        ):

            # 當沖量不可能大於總成交量。
            if (
                total_volume > 0
                and daytrade_volume >= 0
                and daytrade_volume <= total_volume
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

            # ------------------------------------------------
            # A 日期證據
            # ------------------------------------------------

            "daytrade_data_date": (
                data_date
                if daytrade_volume is not None
                else None
            ),

            "total_volume_data_date": (
                data_date
                if total_volume is not None
                else None
            ),
        }

    statistics = {

        "data_date":
            data_date,

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
                if x["day_trading_volume"]
                is not None
            ),

        "total_volume":
            sum(
                1
                for x in result.values()
                if x["total_volume"]
                is not None
            ),

        "day_trading_rate":
            sum(
                1
                for x in result.values()
                if x["day_trading_rate"]
                is not None
            ),

        "rate_missing_volume":
            sum(
                1
                for x in result.values()
                if (
                    x["day_trading_volume"]
                    is not None
                    and
                    x["total_volume"]
                    is None
                )
            ),

        "rate_missing_daytrade":
            sum(
                1
                for x in result.values()
                if (
                    x["day_trading_volume"]
                    is None
                    and
                    x["total_volume"]
                    is not None
                )
            ),

        "rate_invalid":
            sum(
                1
                for x in result.values()
                if (
                    x["day_trading_rate"]
                    is None
                    and
                    x["day_trading_volume"]
                    is not None
                    and
                    x["total_volume"]
                    is not None
                )
            ),

        "rate_validated_date":
            sum(
                1
                for x in result.values()
                if (
                    x["day_trading_rate"]
                    is not None
                    and
                    x["daytrade_data_date"]
                    == data_date
                    and
                    x["total_volume_data_date"]
                    == data_date
                )
            ),
    }

    return (
        result,
        statistics,
    )


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
                    f"{symbol}.{field} "
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
    history: Dict[
        str,
        List[float]
    ],
    daytrade: Dict[
        str,
        Dict[str, Optional[float]]
    ],
    data_date: str,
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Dict[str, int]
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

        symbol = item["symbol"]

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

        if inst_1d is None:
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

        daytrade_data_date = dt.get(
            "daytrade_data_date"
        )

        total_volume_data_date = dt.get(
            "total_volume_data_date"
        )

        if daytrade_volume is not None:
            daytrade_volume_count += 1

        if total_volume is not None:
            total_volume_count += 1

        if daytrade_rate is not None:
            daytrade_rate_count += 1

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
                item.get(
                    "instrument_type"
                ),

            "status":
                item.get(
                    "status"
                ),

            "institutional_1d":
                inst_1d,

            "institutional_5d":
                inst_5d,

            "institutional_10d":
                inst_10d,

            "institutional_20d":
                inst_20d,

            "day_trading_volume":
                daytrade_volume,

            "total_volume":
                total_volume,

            "day_trading_rate":
                daytrade_rate,

            "daytrade_data_date":
                daytrade_data_date,

            "total_volume_data_date":
                total_volume_data_date,

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
# Structure validation
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
        "daytrade_data_date",
        "total_volume_data_date",
        "updated_at",
    }

    errors = 0

    for symbol, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):

            errors += 1

            log(
                f"❌ {symbol} "
                f"不是 object"
            )

            continue

        missing = (
            required_fields
            - set(item.keys())
        )

        if missing:

            errors += len(missing)

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

        if item.get(
            "type"
        ) not in {
            "Stock",
            "ETF",
        }:

            errors += 1

        # ----------------------------------------------------
        # A：當沖率數值驗證
        # ----------------------------------------------------

        rate = item.get(
            "day_trading_rate"
        )

        dt_volume = item.get(
            "day_trading_volume"
        )

        total_volume = item.get(
            "total_volume"
        )

        dt_date = item.get(
            "daytrade_data_date"
        )

        total_date = item.get(
            "total_volume_data_date"
        )

        if rate is not None:

            if not (
                isinstance(
                    rate,
                    (int, float),
                )
                and math.isfinite(rate)
                and 0 <= rate <= 100
            ):

                errors += 1

                log(
                    f"❌ {symbol} "
                    f"當沖率無效："
                    f"{rate}"
                )

            if (
                dt_volume is None
                or total_volume is None
            ):

                errors += 1

                log(
                    f"❌ {symbol} "
                    f"當沖率存在但來源不完整"
                )

            if (
                dt_date is None
                or total_date is None
                or dt_date != total_date
            ):

                errors += 1

                log(
                    f"❌ {symbol} "
                    f"當沖率來源日期不一致"
                )

            if (
                total_volume is not None
                and dt_volume is not None
                and dt_volume > total_volume
            ):

                errors += 1

                log(
                    f"❌ {symbol} "
                    f"當沖量 > 總成交量"
                )

    if not scan_forbidden_fields(
        stocks
    ):

        return False

    if errors:

        log(
            f"❌ 結構驗證失敗："
            f"{errors} 個錯誤"
        )

        return False

    log(
        f"✓ {len(stocks)} 檔 "
        f"結構驗證通過"
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
            f"❌ Chip 數量錯誤："
            f"{len(stocks)} / "
            f"{expected_count}"
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

        dt_volume = item.get(
            "day_trading_volume"
        )

        total_volume = item.get(
            "total_volume"
        )

        dt_date = item.get(
            "daytrade_data_date"
        )

        total_date = item.get(
            "total_volume_data_date"
        )

        if rate is not None:

            if not (
                math.isfinite(rate)
                and 0 <= rate <= 100
            ):

                log(
                    f"❌ {symbol} "
                    f"寫入後當沖率無效"
                )

                return False

            if (
                dt_volume is None
                or total_volume is None
                or dt_date != total_date
            ):

                log(
                    f"❌ {symbol} "
                    f"寫入後當沖來源不一致"
                )

                return False

    log(
        f"✓ chip.json："
        f"{len(stocks)} 檔"
    )

    log(
        "✓ 當沖率寫入後驗證通過"
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
        "正式入口：Scripts/fetch_chip.py"
    )

    log(
        "Universe：Data/universe.json"
    )

    log(
        "Output：Data/chip.json"
    )

    log(
        "A：當沖率資料鏈修正版"
    )

    # ========================================================
    # 1. Universe
    # ========================================================

    securities = load_universe()

    if not securities:

        return 1

    # ========================================================
    # 2. Institutional history
    # ========================================================

    data_date, history = fetch_history(
        HISTORY_DAYS
    )

    if not data_date:

        log(
            "❌ 沒有取得有效法人資料"
        )

        return 1

    if not history:

        log(
            "❌ history 為空"
        )

        return 1

    # ========================================================
    # 3. Day trade
    # ========================================================

    daytrade, daytrade_statistics = (
        fetch_daytrade(
            data_date,
            securities,
        )
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
        daytrade,
        data_date,
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
    # 7. Output
    # ========================================================

    stock_count = sum(
        item["type"] == "Stock"
        for item in stocks.values()
    )

    etf_count = sum(
        item["type"] == "ETF"
        for item in stocks.values()
    )

    twse_count = sum(
        item["market"] == "TWSE"
        for item in stocks.values()
    )

    tpex_count = sum(
        item["market"] == "TPEX"
        for item in stocks.values()
    )

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

        "daytrade_statistics":
            daytrade_statistics,

        "stocks":
            stocks,
    }

    # ========================================================
    # 8. Write
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
    # 9. Post verification
    # ========================================================

    if not verify_written_chip(
        len(securities)
    ):

        return 1

    # ========================================================
    # 10. Final report
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
        f"{len(securities)} 檔"
    )

    log(
        f"✓ Chip："
        f"{len(stocks)} 檔"
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
        "三大法人完整度："
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

    log("")
    log(
        "A：當沖資料品質："
    )

    log(
        f"  當沖成交股數："
        f"{daytrade_statistics['day_trading_volume']}"
    )

    log(
        f"  總成交股數："
        f"{daytrade_statistics['total_volume']}"
    )

    log(
        f"  有效當沖率："
        f"{daytrade_statistics['day_trading_rate']}"
    )

    log(
        f"  日期驗證通過："
        f"{daytrade_statistics['rate_validated_date']}"
    )

    log(
        f"  缺總成交量："
        f"{daytrade_statistics['rate_missing_volume']}"
    )

    log(
        f"  缺當沖量："
        f"{daytrade_statistics['rate_missing_daytrade']}"
    )

    log(
        f"  無效計算："
        f"{daytrade_statistics['rate_invalid']}"
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
        "  ✓ daytrade_data_date"
    )

    log(
        "  ✓ total_volume_data_date"
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
        f"✓ A 當沖率日期與來源驗證啟用"
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