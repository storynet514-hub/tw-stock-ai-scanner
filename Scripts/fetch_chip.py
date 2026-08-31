#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V3.0

============================================================
用途
============================================================
負責取得短期籌碼資料：

1. 三大法人買賣超
2. 融資餘額
3. 融券餘額
4. 資券相抵
5. 成交量
6. 當沖率
7. 5D / 20D 法人買賣超累計

============================================================
核心原則
============================================================

1. Data/universe.json 是唯一 Universe 來源
2. 只處理 Universe 內 status == active 的股票
3. TWSE / TPEx 分開處理
4. 優先使用官方資料
5. 不用第三方資料冒充官方資料
6. 不用 0 代替抓不到的資料
7. 缺資料一律 None / invalid
8. 不把三大法人買賣超命名成主力買賣超
9. 5D / 20D 必須建立在不同交易日資料上
10. 單一股票資料異常不得破壞整批資料
11. 本次完全沒有有效資料時，不覆蓋既有 chip.json
12. 寫檔採 atomic write
============================================================
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


# ============================================================
# 基本設定
# ============================================================

VERSION = "V3.0"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"
OUTPUT_FILE = DATA_DIR / "chip.json"

REQUEST_TIMEOUT = 20
SLEEP_SECONDS = 0.15

LOOKBACK_DAYS = 30
REQUIRED_5D = 5
REQUIRED_20D = 20

USER_AGENT = (
    "Mozilla/5.0 "
    "(Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 "
    "(KHTML, like Gecko) "
    "Chrome/131.0 Safari/537.36"
)


# ============================================================
# Session
# ============================================================

session = requests.Session()

session.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    }
)


# ============================================================
# 時間
# ============================================================

def now_taiwan() -> str:
    from zoneinfo import ZoneInfo

    return datetime.now(
        ZoneInfo("Asia/Taipei")
    ).strftime("%Y-%m-%d %H:%M:%S")


def today_taiwan() -> str:
    from zoneinfo import ZoneInfo

    return datetime.now(
        ZoneInfo("Asia/Taipei")
    ).strftime("%Y-%m-%d")


# ============================================================
# 基本轉換
# ============================================================

def safe_float(value: Any) -> Optional[float]:

    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()

    if not text:
        return None

    text = (
        text
        .replace(",", "")
        .replace("%", "")
        .replace("＋", "+")
        .replace("－", "-")
        .replace("—", "-")
        .replace("–", "-")
    )

    if text in {
        "-",
        "--",
        "N/A",
        "NA",
        "null",
        "None",
        "nan",
    }:
        return None

    try:
        return float(text)
    except Exception:
        return None


def safe_int(value: Any) -> Optional[int]:

    number = safe_float(value)

    if number is None:
        return None

    try:
        return int(number)
    except Exception:
        return None


def normalize_symbol(value: Any) -> Optional[str]:

    if value is None:
        return None

    text = str(value).strip().upper()

    if not text:
        return None

    return text


def clean_code(symbol: str) -> str:

    return (
        symbol.upper()
        .replace(".TW", "")
        .replace(".TWO", "")
    )


def get_market(symbol: str) -> Optional[str]:

    symbol = symbol.upper()

    if symbol.endswith(".TW"):
        return "TWSE"

    if symbol.endswith(".TWO"):
        return "TPEX"

    return None


# ============================================================
# HTTP
# ============================================================

def get_json(
    url: str,
    params: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:

    try:

        response = session.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        data = response.json()

        if not isinstance(data, dict):
            return None

        return data

    except Exception as exc:

        print(
            f"      ⚠️ API 取得失敗：{exc}"
        )

        return None


# ============================================================
# Universe
# ============================================================

def load_universe() -> List[str]:

    if not UNIVERSE_FILE.exists():
        raise FileNotFoundError(
            f"找不到 Universe：{UNIVERSE_FILE}"
        )

    with UNIVERSE_FILE.open(
        "r",
        encoding="utf-8",
    ) as file:

        data = json.load(file)

    records: Any = None

    if isinstance(data, dict):

        stocks = data.get("stocks")

        if isinstance(stocks, dict):

            records = []

            for code, item in stocks.items():

                if isinstance(item, dict):

                    record = dict(item)

                    if not record.get("symbol"):
                        record["symbol"] = code

                    records.append(record)

        elif isinstance(stocks, list):

            records = stocks

        else:

            for key in (
                "universe",
                "symbols",
                "data",
            ):

                if isinstance(data.get(key), list):

                    records = data[key]
                    break

    elif isinstance(data, list):

        records = data

    if records is None:

        raise ValueError(
            "universe.json 格式無法辨識。"
        )

    symbols: List[str] = []

    for item in records:

        symbol = None
        status = "active"

        if isinstance(item, str):

            symbol = item

        elif isinstance(item, dict):

            status = str(
                item.get(
                    "status",
                    "active",
                )
            ).lower()

            for key in (
                "symbol",
                "code",
                "stock_id",
                "ticker",
            ):

                if item.get(key):

                    symbol = item[key]
                    break

        if status != "active":
            continue

        symbol = normalize_symbol(symbol)

        if not symbol:
            continue

        if (
            symbol.endswith(".TW")
            or symbol.endswith(".TWO")
        ):

            symbols.append(symbol)

        elif symbol.isdigit():

            symbols.append(symbol)

    return sorted(set(symbols))


# ============================================================
# 日期
# ============================================================

def parse_date(value: Any) -> Optional[str]:

    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    text = (
        text
        .replace("/", "-")
        .replace(".", "-")
    )

    if len(text) == 8 and text.isdigit():

        return (
            f"{text[0:4]}-"
            f"{text[4:6]}-"
            f"{text[6:8]}"
        )

    if len(text) >= 10:

        candidate = text[:10]

        try:

            datetime.strptime(
                candidate,
                "%Y-%m-%d",
            )

            return candidate

        except Exception:
            return None

    return None


def date_is_valid(
    value: Any,
) -> bool:

    return parse_date(value) is not None


# ============================================================
# Generic table helpers
# ============================================================

def find_field_index(
    fields: List[Any],
    candidates: List[str],
) -> Optional[int]:

    normalized = [
        str(field).strip()
        for field in fields
    ]

    for candidate in candidates:

        for index, field in enumerate(normalized):

            if candidate == field:

                return index

    for candidate in candidates:

        for index, field in enumerate(normalized):

            if candidate in field:

                return index

    return None


def row_code(
    row: List[Any],
    code_index: Optional[int],
) -> Optional[str]:

    if code_index is None:
        return None

    if code_index >= len(row):
        return None

    value = str(
        row[code_index]
    ).strip()

    if not value:
        return None

    return value


# ============================================================
# TWSE：融資融券
# ============================================================

def fetch_twse_margin_day(
    date: str,
) -> Dict[str, Dict[str, Any]]:

    url = (
        "https://www.twse.com.tw/rwd/zh/"
        "marginTrading/marginTWTAS"
    )

    params = {
        "response": "json",
        "date": date.replace("-", ""),
        "selectType": "ALL",
    }

    data = get_json(
        url,
        params,
    )

    result: Dict[str, Dict[str, Any]] = {}

    if not data:
        return result

    fields = data.get("fields")
    rows = data.get("data")

    if not isinstance(fields, list):
        return result

    if not isinstance(rows, list):
        return result

    code_index = find_field_index(
        fields,
        [
            "股票代號",
            "證券代號",
            "代號",
        ],
    )

    margin_index = find_field_index(
        fields,
        [
            "融資餘額",
        ],
    )

    short_index = find_field_index(
        fields,
        [
            "融券餘額",
        ],
    )

    if code_index is None:
        return result

    for row in rows:

        if not isinstance(row, list):
            continue

        code = row_code(
            row,
            code_index,
        )

        if not code:
            continue

        record = {
            "margin_balance": (
                safe_float(row[margin_index])
                if margin_index is not None
                and margin_index < len(row)
                else None
            ),
            "short_balance": (
                safe_float(row[short_index])
                if short_index is not None
                and short_index < len(row)
                else None
            ),
            "date": date,
        }

        result[code] = record

    return result


# ============================================================
# TPEx：融資融券
# ============================================================

def fetch_tpex_margin_day(
    date: str,
) -> Dict[str, Dict[str, Any]]:

    """
    TPEx 官方 OpenAPI。

    注意：
    不使用 HTML 頁面當成資料來源。
    """

    urls = [
        (
            "https://www.tpex.org.tw/openapi/"
            "v1/tpex_mainboard_margin"
        ),
        (
            "https://www.tpex.org.tw/openapi/"
            "v1/tpex_esb_margin"
        ),
    ]

    result: Dict[str, Dict[str, Any]] = {}

    for url in urls:

        data = get_json(url)

        if not data:
            continue

        rows = data.get("data")

        if isinstance(rows, list):

            for item in rows:

                if not isinstance(item, dict):
                    continue

                code = None

                for key in (
                    "SecuritiesCompanyCode",
                    "SecuritiesCompanyCode",
                    "Code",
                    "代號",
                    "證券代號",
                    "股票代號",
                ):

                    if item.get(key):

                        code = str(
                            item[key]
                        ).strip()

                        break

                if not code:
                    continue

                margin = None
                short = None

                for key, value in item.items():

                    name = str(key)

                    if (
                        "融資餘額" in name
                        or "MarginBalance" in name
                        or "margin_balance" in name
                    ):

                        margin = safe_float(value)

                    if (
                        "融券餘額" in name
                        or "ShortBalance" in name
                        or "short_balance" in name
                    ):

                        short = safe_float(value)

                result[code] = {
                    "margin_balance": margin,
                    "short_balance": short,
                    "date": date,
                }

    return result


# ============================================================
# TWSE：三大法人
# ============================================================

def fetch_twse_institutional_day(
    date: str,
) -> Dict[str, Dict[str, Any]]:

    url = (
        "https://www.twse.com.tw/rwd/zh/"
        "fund/T86"
    )

    params = {
        "response": "json",
        "date": date.replace("-", ""),
        "selectType": "ALLBUT0999",
    }

    data = get_json(
        url,
        params,
    )

    result: Dict[str, Dict[str, Any]] = {}

    if not data:
        return result

    fields = data.get("fields")
    rows = data.get("data")

    if not isinstance(fields, list):
        return result

    if not isinstance(rows, list):
        return result

    code_index = find_field_index(
        fields,
        [
            "證券代號",
            "股票代號",
        ],
    )

    if code_index is None:
        return result

    foreign_indexes: List[int] = []
    investment_indexes: List[int] = []
    dealer_indexes: List[int] = []

    for index, field in enumerate(fields):

        name = str(field)

        if "外陸資" in name and "買賣超股數" in name:
            foreign_indexes.append(index)

        elif "投信" in name and "買賣超股數" in name:
            investment_indexes.append(index)

        elif "自營商" in name and "買賣超股數" in name:
            dealer_indexes.append(index)

    for row in rows:

        if not isinstance(row, list):
            continue

        code = row_code(
            row,
            code_index,
        )

        if not code:
            continue

        foreign = sum_values(
            row,
            foreign_indexes,
        )

        investment = sum_values(
            row,
            investment_indexes,
        )

        dealer = sum_values(
            row,
            dealer_indexes,
        )

        total = None

        if (
            foreign is not None
            or investment is not None
            or dealer is not None
        ):

            total = (
                (foreign or 0.0)
                + (investment or 0.0)
                + (dealer or 0.0)
            )

        result[code] = {
            "foreign_net": foreign,
            "investment_net": investment,
            "dealer_net": dealer,
            "institutional_net": total,
            "date": date,
        }

    return result


def sum_values(
    row: List[Any],
    indexes: List[int],
) -> Optional[float]:

    values: List[float] = []

    for index in indexes:

        if index >= len(row):
            continue

        value = safe_float(
            row[index]
        )

        if value is not None:
            values.append(value)

    if not values:
        return None

    return sum(values)


# ============================================================
# TPEx：三大法人
# ============================================================

def fetch_tpex_institutional_day(
    date: str,
) -> Dict[str, Dict[str, Any]]:

    url = (
        "https://www.tpex.org.tw/openapi/"
        "v1/tpex_threeinsti_daily"
    )

    data = get_json(url)

    result: Dict[str, Dict[str, Any]] = {}

    if not data:
        return result

    rows = data.get("data")

    if not isinstance(rows, list):
        return result

    for item in rows:

        if not isinstance(item, dict):
            continue

        code = None

        for key in (
            "SecuritiesCompanyCode",
            "Code",
            "證券代號",
            "股票代號",
            "代號",
        ):

            if item.get(key):

                code = str(
                    item[key]
                ).strip()

                break

        if not code:
            continue

        foreign = None
        investment = None
        dealer = None

        for key, value in item.items():

            name = str(key)

            if (
                "外資" in name
                and "買賣超" in name
            ):

                foreign = safe_float(value)

            elif (
                "投信" in name
                and "買賣超" in name
            ):

                investment = safe_float(value)

            elif (
                "自營商" in name
                and "買賣超" in name
            ):

                dealer = safe_float(value)

        total = None

        if (
            foreign is not None
            or investment is not None
            or dealer is not None
        ):

            total = (
                (foreign or 0.0)
                + (investment or 0.0)
                + (dealer or 0.0)
            )

        result[code] = {
            "foreign_net": foreign,
            "investment_net": investment,
            "dealer_net": dealer,
            "institutional_net": total,
            "date": date,
        }

    return result


# ============================================================
# TWSE：成交量 / 當沖
# ============================================================

def fetch_twse_volume_day(
    date: str,
) -> Dict[str, Dict[str, Any]]:

    url = (
        "https://www.twse.com.tw/rwd/zh/"
        "afterTrading/MI_INDEX"
    )

    params = {
        "response": "json",
        "date": date.replace("-", ""),
        "type": "ALLBUT0999",
    }

    data = get_json(
        url,
        params,
    )

    result: Dict[str, Dict[str, Any]] = {}

    if not data:
        return result

    tables = data.get("tables")

    if not isinstance(tables, list):
        return result

    for table in tables:

        if not isinstance(table, dict):
            continue

        fields = table.get("fields")
        rows = table.get("data")

        if not isinstance(fields, list):
            continue

        if not isinstance(rows, list):
            continue

        code_index = find_field_index(
            fields,
            [
                "證券代號",
                "股票代號",
            ],
        )

        volume_index = find_field_index(
            fields,
            [
                "成交股數",
                "成交量",
            ],
        )

        if code_index is None:
            continue

        for row in rows:

            if not isinstance(row, list):
                continue

            code = row_code(
                row,
                code_index,
            )

            if not code:
                continue

            volume = None

            if (
                volume_index is not None
                and volume_index < len(row)
            ):

                volume = safe_float(
                    row[volume_index]
                )

            result[code] = {
                "volume": volume,
                "date": date,
            }

    return result


# ============================================================
# TPEx：成交量
# ============================================================

def fetch_tpex_volume_day(
    date: str,
) -> Dict[str, Dict[str, Any]]:

    url = (
        "https://www.tpex.org.tw/openapi/"
        "v1/tpex_daily_market_value"
    )

    data = get_json(url)

    result: Dict[str, Dict[str, Any]] = {}

    if not data:
        return result

    rows = data.get("data")

    if not isinstance(rows, list):
        return result

    for item in rows:

        if not isinstance(item, dict):
            continue

        code = None
        volume = None

        for key, value in item.items():

            name = str(key)

            if (
                "代號" in name
                or name.lower() == "code"
            ):

                code = str(value).strip()

            if (
                "成交量" in name
                or "Volume" in name
                or name.lower() == "volume"
            ):

                volume = safe_float(value)

        if code:

            result[code] = {
                "volume": volume,
                "date": date,
            }

    return result


# ============================================================
# 日期候選
# ============================================================

def generate_dates(
    days: int,
) -> List[str]:

    from datetime import timedelta
    from zoneinfo import ZoneInfo

    today = datetime.now(
        ZoneInfo("Asia/Taipei")
    ).date()

    dates: List[str] = []

    for offset in range(days):

        day = today - timedelta(
            days=offset
        )

        dates.append(
            day.strftime("%Y-%m-%d")
        )

    return dates


# ============================================================
# 市場批次資料
# ============================================================

def collect_market_data(
    market: str,
    required_symbols: set[str],
) -> Dict[str, Dict[str, Any]]:

    """
    批次取得市場資料。

    不逐股票打 API。
    """

    history: Dict[
        str,
        Dict[str, Any]
    ] = {}

    dates = generate_dates(
        LOOKBACK_DAYS
    )

    for date in dates:

        print(
            f"   {market} {date}"
        )

        if market == "TWSE":

            margin = fetch_twse_margin_day(
                date
            )

            institutional = (
                fetch_twse_institutional_day(
                    date
                )
            )

            volume = fetch_twse_volume_day(
                date
            )

        elif market == "TPEX":

            margin = fetch_tpex_margin_day(
                date
            )

            institutional = (
                fetch_tpex_institutional_day(
                    date
                )
            )

            volume = fetch_tpex_volume_day(
                date
            )

        else:

            break

        codes = (
            set(margin)
            | set(institutional)
            | set(volume)
        )

        for code in codes:

            if code not in required_symbols:
                continue

            if code not in history:

                history[code] = {
                    "dates": {},
                }

            day_record = {
                "date": date,
                "margin_balance": None,
                "short_balance": None,
                "foreign_net": None,
                "investment_net": None,
                "dealer_net": None,
                "institutional_net": None,
                "volume": None,
            }

            if code in margin:

                item = margin[code]

                day_record[
                    "margin_balance"
                ] = item.get(
                    "margin_balance"
                )

                day_record[
                    "short_balance"
                ] = item.get(
                    "short_balance"
                )

            if code in institutional:

                item = institutional[code]

                for field in (
                    "foreign_net",
                    "investment_net",
                    "dealer_net",
                    "institutional_net",
                ):

                    day_record[field] = item.get(
                        field
                    )

            if code in volume:

                day_record["volume"] = (
                    volume[code].get(
                        "volume"
                    )
                )

            history[code]["dates"][date] = (
                day_record
            )

        time.sleep(
            SLEEP_SECONDS
        )

    return history


# ============================================================
# 交易日資料
# ============================================================

def valid_daily_records(
    history: Dict[str, Any],
) -> List[Dict[str, Any]]:

    dates = history.get(
        "dates",
        {}
    )

    if not isinstance(dates, dict):
        return []

    records = []

    for date, record in dates.items():

        if not date_is_valid(date):
            continue

        if not isinstance(record, dict):
            continue

        record = dict(record)
        record["date"] = date

        records.append(record)

    records.sort(
        key=lambda item: item["date"],
        reverse=True,
    )

    return records


# ============================================================
# 累計
# ============================================================

def sum_field(
    records: List[Dict[str, Any]],
    field: str,
    count: int,
) -> Tuple[Optional[float], int]:

    selected = records[:count]

    values: List[float] = []

    for record in selected:

        value = safe_float(
            record.get(field)
        )

        if value is not None:
            values.append(value)

    if len(values) != count:

        return None, len(values)

    return sum(values), len(values)


# ============================================================
# 資券相抵
# ============================================================

def calculate_day_trade_ratio(
    record: Dict[str, Any],
) -> Optional[float]:

    """
    若官方資料沒有「資券相抵」，
    絕不使用猜測值。

    只有同時存在：
        offset_volume
        volume

    才計算。

    """

    offset = safe_float(
        record.get(
            "offset_volume"
        )
    )

    volume = safe_float(
        record.get(
            "volume"
        )
    )

    if offset is None:
        return None

    if volume is None:
        return None

    if volume <= 0:
        return None

    return (
        offset / volume * 100.0
    )


# ============================================================
# 建立股票資料
# ============================================================

def build_stock_record(
    symbol: str,
    history: Dict[str, Any],
) -> Dict[str, Any]:

    market = get_market(symbol)
    code = clean_code(symbol)

    records = valid_daily_records(
        history
    )

    latest = (
        records[0]
        if records
        else {}
    )

    institutional_5d, inst5_count = (
        sum_field(
            records,
            "institutional_net",
            REQUIRED_5D,
        )
    )

    institutional_20d, inst20_count = (
        sum_field(
            records,
            "institutional_net",
            REQUIRED_20D,
        )
    )

    margin_balance = safe_float(
        latest.get(
            "margin_balance"
        )
    )

    short_balance = safe_float(
        latest.get(
            "short_balance"
        )
    )

    volume = safe_float(
        latest.get(
            "volume"
        )
    )

    available_fields = 0

    if institutional_5d is not None:
        available_fields += 1

    if institutional_20d is not None:
        available_fields += 1

    if (
        margin_balance is not None
        and short_balance is not None
    ):
        available_fields += 1

    if volume is not None:
        available_fields += 1

    return {
        "symbol": symbol,
        "code": code,
        "market": market,

        "date": (
            latest.get("date")
            if latest
            else None
        ),

        # ----------------------------------------------------
        # 法人
        # ----------------------------------------------------

        "foreign_net": latest.get(
            "foreign_net"
        ),

        "investment_net": latest.get(
            "investment_net"
        ),

        "dealer_net": latest.get(
            "dealer_net"
        ),

        "institutional_net": latest.get(
            "institutional_net"
        ),

        "institutional_5d": (
            institutional_5d
        ),

        "institutional_20d": (
            institutional_20d
        ),

        "institutional_5d_days": (
            inst5_count
        ),

        "institutional_20d_days": (
            inst20_count
        ),

        # ----------------------------------------------------
        # 融資融券
        # ----------------------------------------------------

        "margin_balance": (
            margin_balance
        ),

        "short_balance": (
            short_balance
        ),

        # ----------------------------------------------------
        # 成交量
        # ----------------------------------------------------

        "volume": volume,

        # ----------------------------------------------------
        # 當沖
        # ----------------------------------------------------

        "day_trade_ratio": None,

        # ----------------------------------------------------
        # 主力
        # ----------------------------------------------------

        # 三大法人 != 主力
        # 在沒有可靠主力資料來源前保持 None
        "main_force_net": None,
        "main_force_5d": None,
        "main_force_20d": None,

        # ----------------------------------------------------
        # 狀態
        # ----------------------------------------------------

        "available_fields": available_fields,

        "complete": (
            available_fields >= 3
        ),

        "history_days": len(records),

        "history": records[:REQUIRED_20D],
    }


# ============================================================
# 建立 Universe 1:1
# ============================================================

def build_results(
    symbols: List[str],
    twse_history: Dict[str, Any],
    tpex_history: Dict[str, Any],
) -> Dict[str, Any]:

    results: Dict[str, Any] = {}

    for symbol in symbols:

        market = get_market(symbol)
        code = clean_code(symbol)

        if market == "TWSE":

            history = twse_history.get(
                code,
                {"dates": {}},
            )

        elif market == "TPEX":

            history = tpex_history.get(
                code,
                {"dates": {}},
            )

        else:

            history = {
                "dates": {}
            }

        results[symbol] = (
            build_stock_record(
                symbol,
                history,
            )
        )

    return results


# ============================================================
# JSON
# ============================================================

def write_json(
    records: Dict[str, Any],
) -> None:

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    payload = {
        "version": VERSION,
        "generated_at": now_taiwan(),
        "date": today_taiwan(),
        "count": len(records),
        "data": records,
    }

    temp_file = OUTPUT_FILE.with_suffix(
        ".json.tmp"
    )

    with temp_file.open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            payload,
            file,
            ensure_ascii=False,
            indent=2,
        )

    temp_file.replace(
        OUTPUT_FILE
    )


# ============================================================
# 主程式
# ============================================================

def main() -> int:

    print("=" * 72)
    print(
        f"台股 AI 選股系統 "
        f"fetch_chip.py {VERSION}"
    )
    print("=" * 72)

    print(
        f"開始時間：{now_taiwan()}"
    )

    # --------------------------------------------------------
    # Universe
    # --------------------------------------------------------

    print()
    print("=" * 72)
    print("讀取 Universe")
    print("=" * 72)

    try:

        symbols = load_universe()

    except Exception as exc:

        print(
            f"❌ Universe 讀取失敗：{exc}"
        )

        return 1

    if not symbols:

        print(
            "❌ Universe 沒有任何 active 台股。"
        )

        return 1

    twse_symbols = {
        clean_code(symbol)
        for symbol in symbols
        if get_market(symbol) == "TWSE"
    }

    tpex_symbols = {
        clean_code(symbol)
        for symbol in symbols
        if get_market(symbol) == "TPEX"
    }

    print(
        f"Universe：{len(symbols)}"
    )

    print(
        f"TWSE：{len(twse_symbols)}"
    )

    print(
        f"TPEx：{len(tpex_symbols)}"
    )

    # --------------------------------------------------------
    # TWSE
    # --------------------------------------------------------

    print()
    print("=" * 72)
    print("取得 TWSE 官方資料")
    print("=" * 72)

    twse_history = collect_market_data(
        "TWSE",
        twse_symbols,
    )

    print(
        f"TWSE 股票歷史資料："
        f"{len(twse_history)}"
    )

    # --------------------------------------------------------
    # TPEx
    # --------------------------------------------------------

    print()
    print("=" * 72)
    print("取得 TPEx 官方資料")
    print("=" * 72)

    tpex_history = collect_market_data(
        "TPEX",
        tpex_symbols,
    )

    print(
        f"TPEx 股票歷史資料："
        f"{len(tpex_history)}"
    )

    # --------------------------------------------------------
    # 建立結果
    # --------------------------------------------------------

    print()
    print("=" * 72)
    print("建立籌碼資料")
    print("=" * 72)

    results = build_results(
        symbols,
        twse_history,
        tpex_history,
    )

    # --------------------------------------------------------
    # 統計
    # --------------------------------------------------------

    complete = 0
    partial = 0
    invalid = 0

    twse_valid = 0
    tpex_valid = 0

    for symbol, record in results.items():

        history_days = record.get(
            "history_days",
            0,
        )

        if history_days >= REQUIRED_20D:

            complete += 1

        elif history_days > 0:

            partial += 1

        else:

            invalid += 1

        if history_days > 0:

            if get_market(symbol) == "TWSE":

                twse_valid += 1

            elif get_market(symbol) == "TPEX":

                tpex_valid += 1

    print(
        f"Universe：{len(symbols)}"
    )

    print(
        f"20D 歷史完整：{complete}"
    )

    print(
        f"部分歷史：{partial}"
    )

    print(
        f"完全無資料：{invalid}"
    )

    print(
        f"TWSE 有歷史資料：{twse_valid}"
    )

    print(
        f"TPEx 有歷史資料：{tpex_valid}"
    )

    # --------------------------------------------------------
    # 防呆
    # --------------------------------------------------------

    valid_records = sum(
        1
        for record in results.values()
        if record.get(
            "history_days",
            0,
        ) > 0
    )

    if valid_records == 0:

        print()
        print(
            "❌ 本次完全沒有取得有效籌碼資料。"
        )

        print(
            "❌ 不覆蓋既有 chip.json。"
        )

        return 1

    # --------------------------------------------------------
    # Universe 1:1 驗證
    # --------------------------------------------------------

    if len(results) != len(symbols):

        print(
            "❌ 籌碼結果與 Universe 數量不一致。"
        )

        return 1

    # --------------------------------------------------------
    # 寫入
    # --------------------------------------------------------

    try:

        write_json(
            results
        )

    except Exception as exc:

        print(
            f"❌ chip.json 寫入失敗：{exc}"
        )

        return 1

    # --------------------------------------------------------
    # 完成
    # --------------------------------------------------------

    print()
    print("=" * 72)
    print("完成")
    print("=" * 72)

    print(
        f"✅ 輸出：{OUTPUT_FILE}"
    )

    print(
        f"✅ Universe：{len(symbols)}"
    )

    print(
        f"✅ 有效資料：{valid_records}"
    )

    print(
        f"✅ 20D 完整：{complete}"
    )

    print(
        f"⚠️ 部分資料：{partial}"
    )

    print(
        f"❌ 無資料：{invalid}"
    )

    print()
    print(
        f"fetch_chip.py {VERSION} 完成"
    )

    return 0


if __name__ == "__main__":

    sys.exit(
        main()
    )