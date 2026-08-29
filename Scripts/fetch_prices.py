#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/fetch_prices.py

正式版 V8.0
============================================================

核心原則
------------------------------------------------------------

1. Data/universe.json 是唯一 Universe 來源
2. 只接受 Universe 中 type == STOCK 的標的
3. ETF 完全不進價格資料管線
4. 不修改 Universe
5. 不使用成交行情建立 Universe
6. 不使用 CMoney
7. 不使用 Yahoo
8. 不使用任何第三方價格 fallback
9. TWSE / TPEx 官方資料是唯一價格來源
10. 官方資料不足時直接 FAIL，不補資料
11. Universe STOCK 與 Price STOCK 必須 1:1
12. 20 筆是絕對最低歷史資料
13. 60 筆以上為 complete
14. 20~59 筆為 short_history
15. temporary directory 建置
16. shard 驗證
17. manifest 驗證
18. atomic replace
19. 任一 Universe STOCK 無法取得最低 20 筆官方資料
    整個價格建置 FAIL
20. 不允許部分成功後覆蓋正式 Data/prices

============================================================
V8.0 與 V7.1 的關鍵差異
============================================================

V7.1：

    官方資料不足
        ↓
    Yahoo fallback
        ↓
    只要成功率 >= 80%
        ↓
    寫入 Data/prices

這會造成：

    1. 第三方資料混入
    2. Universe 與價格資料不是嚴格 1:1
    3. 某些股票來源不同
    4. Dashboard 無法確認資料可信來源

V8.0：

    官方資料
        ↓
    >= 20 筆
        ↓
    OK

    < 20 筆
        ↓
    FAIL

完全移除：

    Yahoo
    fallback
    80% safety gate
    部分成功覆蓋正式資料

============================================================
歷史資料策略
============================================================

V7.1 從：

    2023-01-01

一路抓到現在。

對約 1944 檔 STOCK：

    1944 × 約 44 個月份

會產生非常大量 HTTP request。

V8.0 改成：

    最近約 100 個日曆日

並自動取得涵蓋期間的官方月份。

目的：

    最少取得 60 個交易日
    同時保留足夠 buffer

通常只需要：

    3~4 個月份 / 股票

而不是從 2023 年開始。

============================================================
"""

from __future__ import annotations

import json
import math
import re
import shutil
import sys
import tempfile
import time

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


# ============================================================
# VERSION
# ============================================================

VERSION = "V8.0"

SCHEMA_VERSION = "prices-v8.0"


# ============================================================
# PATH
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

OUTPUT_DIR = DATA_DIR / "prices"


# ============================================================
# HISTORY
# ============================================================

# 不再從 2023 年開始抓。
#
# 100 個日曆日通常可以涵蓋：
#
#   60+ 個交易日
#
# 並且能容忍：
#
#   週末
#   國定假日
#   颱風假
#   臨時休市
#
HISTORY_LOOKBACK_DAYS = 100


# 絕對最低資料筆數
ABSOLUTE_MIN_HISTORY_ROWS = 20


# 達到這個數量視為完整歷史
MIN_HISTORY_ROWS = 60


# 每個 shard 最多幾檔
STOCKS_PER_FILE = 100


# 單一 shard 最大容量
MAX_FILE_SIZE_MB = 80.0

MAX_FILE_SIZE_BYTES = int(
    MAX_FILE_SIZE_MB * 1024 * 1024
)


# ============================================================
# NETWORK
# ============================================================

MAX_RETRIES = 4

REQUEST_TIMEOUT = 30

REQUEST_DELAY = 0.10

RETRY_DELAY = 1.5


# ============================================================
# OFFICIAL TWSE
# ============================================================

TWSE_STOCK_DAY_URL = (
    "https://www.twse.com.tw/"
    "exchangeReport/STOCK_DAY"
)


# ============================================================
# OFFICIAL TPEx
# ============================================================

TPEX_ST43_URL = (
    "https://www.tpex.org.tw/"
    "web/stock/aftertrading/"
    "daily_trading_info/st43_result.php"
)


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/151.0 Safari/537.36"
        ),
        "Accept": (
            "application/json,"
            "text/plain,"
            "*/*"
        ),
        "Accept-Language": (
            "zh-TW,zh;q=0.9,"
            "en-US;q=0.8,en;q=0.7"
        ),
        "Connection": "keep-alive",
    }
)


# ============================================================
# LOG
# ============================================================

def log(message: str = "") -> None:

    print(
        message,
        flush=True,
    )


def section(title: str) -> None:

    log("")

    log("=" * 76)

    log(title)

    log("=" * 76)


# ============================================================
# JSON
# ============================================================

def load_json(
    path: Path,
) -> Any:

    with path.open(
        "r",
        encoding="utf-8-sig",
    ) as file:

        return json.load(file)


def save_json(
    path: Path,
    data: Any,
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            data,
            file,
            ensure_ascii=False,
            separators=(",", ":"),
        )


# ============================================================
# TEXT
# ============================================================

def clean_text(
    value: Any,
) -> str:

    if value is None:

        return ""

    return (
        str(value)
        .replace("\ufeff", "")
        .replace("\u3000", " ")
        .strip()
    )


# ============================================================
# NUMBER
# ============================================================

def safe_float(
    value: Any,
) -> Optional[float]:

    if value is None:

        return None

    text = clean_text(value)

    if not text:

        return None

    text = (
        text
        .replace(",", "")
        .replace("，", "")
        .replace("--", "")
        .replace("－", "-")
        .replace("—", "-")
        .replace("–", "-")
        .replace("X", "")
        .replace("x", "")
    )

    if not text:

        return None

    try:

        number = float(text)

        if not math.isfinite(number):

            return None

        return number

    except Exception:

        return None


def safe_int(
    value: Any,
) -> int:

    number = safe_float(value)

    if number is None:

        return 0

    return int(number)


# ============================================================
# DATE
# ============================================================

def parse_date(
    value: Any,
) -> Optional[str]:

    text = clean_text(value)

    if not text:

        return None

    # --------------------------------------------------------
    # ROC date
    #
    # 115/08/28
    # --------------------------------------------------------

    match = re.fullmatch(
        r"(\d{2,3})/(\d{1,2})/(\d{1,2})",
        text,
    )

    if match:

        try:

            year = int(match.group(1))

            month = int(match.group(2))

            day = int(match.group(3))

            if year < 1911:

                year += 1911

            dt = datetime(
                year,
                month,
                day,
            )

            return dt.strftime(
                "%Y-%m-%d"
            )

        except Exception:

            return None

    # --------------------------------------------------------
    # Gregorian slash
    # --------------------------------------------------------

    for fmt in (
        "%Y/%m/%d",
        "%Y-%m-%d",
        "%Y%m%d",
    ):

        try:

            dt = datetime.strptime(
                text,
                fmt,
            )

            return dt.strftime(
                "%Y-%m-%d"
            )

        except Exception:

            continue

    return None


# ============================================================
# CODE
# ============================================================

def extract_code(
    value: Any,
) -> Optional[str]:
    """
    Universe 商品代號規則：

        4~6 碼
        第一碼必須為數字
        後續允許英數字

    合法：

        2330
        3081
        7794
        0050
        00400A
        00980A

    不合法：

        ABCD
        A123
        123
        1234567
    """

    text = clean_text(
        value
    ).upper()

    if not text:

        return None

    if text.endswith(".TWO"):

        text = text[:-4]

    elif text.endswith(".TW"):

        text = text[:-3]

    if not 4 <= len(text) <= 6:

        return None

    if not text[0].isdigit():

        return None

    if not all(
        char.isalnum()
        for char in text
    ):

        return None

    return text


# ============================================================
# TYPE
# ============================================================

def normalize_type(
    value: Any,
) -> Optional[str]:

    text = clean_text(
        value
    ).upper()

    if text == "STOCK":

        return "STOCK"

    if text == "ETF":

        return "ETF"

    return None


# ============================================================
# MARKET
# ============================================================

def normalize_market(
    value: Any,
) -> Optional[str]:

    text = clean_text(
        value
    ).upper()

    if text in {
        "TW",
        "TWSE",
        "TSE",
        "上市",
    }:

        return "TW"

    if text in {
        "TWO",
        "TPEX",
        "OTC",
        "上櫃",
        "上柜",
    }:

        return "TWO"

    return None


# ============================================================
# NAME
# ============================================================

def extract_name(
    item: Dict[str, Any],
) -> str:

    for key in (
        "name",
        "stock_name",
        "security_name",
        "company_name",
        "證券名稱",
        "名稱",
    ):

        value = clean_text(
            item.get(key)
        )

        if value:

            return value

    return ""


# ============================================================
# SYMBOL
# ============================================================

def extract_symbol(
    item: Dict[str, Any],
    fallback_key: Optional[str] = None,
) -> Optional[str]:

    for key in (
        "full_symbol",
        "fullSymbol",
        "yahoo_symbol",
        "yahooSymbol",
        "symbol",
    ):

        value = clean_text(
            item.get(key)
        ).upper()

        if not value:

            continue

        code = extract_code(
            value
        )

        if not code:

            continue

        if value.endswith(".TWO"):

            return (
                code
                + ".TWO"
            )

        if value.endswith(".TW"):

            return (
                code
                + ".TW"
            )

        # 沒有 suffix 時暫時只回傳 code。
        #
        # market 會在 normalize_record()
        # 補上正確 suffix。
        return code

    if fallback_key:

        value = clean_text(
            fallback_key
        ).upper()

        code = extract_code(
            value
        )

        if code:

            if value.endswith(".TWO"):

                return (
                    code
                    + ".TWO"
                )

            if value.endswith(".TW"):

                return (
                    code
                    + ".TW"
                )

            return code

    return None


# ============================================================
# ITEM CODE
# ============================================================

def extract_item_code(
    item: Dict[str, Any],
    symbol: Optional[str],
    fallback_key: Optional[str] = None,
) -> Optional[str]:

    for key in (
        "code",
        "stock_code",
        "stock_id",
        "ticker",
        "security_code",
        "證券代號",
        "有價證券代號",
        "代號",
    ):

        code = extract_code(
            item.get(key)
        )

        if code:

            return code

    if symbol:

        code = extract_code(
            symbol
        )

        if code:

            return code

    if fallback_key:

        return extract_code(
            fallback_key
        )

    return None


# ============================================================
# NORMALIZE RECORD
# ============================================================

def normalize_record(
    item: Any,
    fallback_key: Optional[str] = None,
) -> Optional[Dict[str, str]]:

    if not isinstance(
        item,
        dict,
    ):

        return None

    # --------------------------------------------------------
    # TYPE
    # --------------------------------------------------------

    record_type = normalize_type(
        item.get("type")
    )

    if record_type is None:

        return None

    # --------------------------------------------------------
    # SYMBOL
    # --------------------------------------------------------

    symbol = extract_symbol(
        item,
        fallback_key,
    )

    code = extract_item_code(
        item,
        symbol,
        fallback_key,
    )

    if code is None:

        return None

    # --------------------------------------------------------
    # MARKET
    # --------------------------------------------------------

    market = normalize_market(
        item.get("market")
    )

    if market is None and symbol:

        if symbol.endswith(".TWO"):

            market = "TWO"

        elif symbol.endswith(".TW"):

            market = "TW"

    if market is None:

        return None

    suffix = (
        ".TWO"
        if market == "TWO"
        else ".TW"
    )

    symbol = (
        code
        + suffix
    )

    name = extract_name(
        item
    )

    if not name:

        return None

    return {
        "symbol": symbol,
        "code": code,
        "market": market,
        "type": record_type,
        "name": name,
    }


# ============================================================
# CONTAINER
# ============================================================

def extract_container(
    universe: Dict[str, Any],
    key: str,
) -> List[
    Tuple[Optional[str], Any]
]:

    value = universe.get(
        key
    )

    result = []

    # --------------------------------------------------------
    # list
    # --------------------------------------------------------

    if isinstance(
        value,
        list,
    ):

        for item in value:

            result.append(
                (
                    None,
                    item,
                )
            )

        return result

    # --------------------------------------------------------
    # dict
    # --------------------------------------------------------

    if isinstance(
        value,
        dict,
    ):

        for symbol, item in value.items():

            result.append(
                (
                    str(symbol),
                    item,
                )
            )

        return result

    return result


# ============================================================
# LOAD UNIVERSE
# ============================================================

def load_universe() -> List[
    Dict[str, str]
]:

    section(
        "V8.0 Universe 驗證"
    )

    if not UNIVERSE_FILE.exists():

        raise RuntimeError(
            "找不到 Data/universe.json"
        )

    universe = load_json(
        UNIVERSE_FILE
    )

    if not isinstance(
        universe,
        dict,
    ):

        raise RuntimeError(
            "universe.json 根節點必須是 object"
        )

    # --------------------------------------------------------
    # metadata
    # --------------------------------------------------------

    declared_stock_count = (
        universe.get(
            "stock_count"
        )
    )

    declared_etf_count = (
        universe.get(
            "etf_count"
        )
    )

    if not isinstance(
        declared_stock_count,
        int,
    ):

        raise RuntimeError(
            "Universe stock_count "
            "不存在或不是整數"
        )

    if not isinstance(
        declared_etf_count,
        int,
    ):

        raise RuntimeError(
            "Universe etf_count "
            "不存在或不是整數"
        )

    # --------------------------------------------------------
    # raw
    # --------------------------------------------------------

    raw_items = extract_container(
        universe,
        "stocks",
    )

    if not raw_items:

        raise RuntimeError(
            "Universe stocks 為空"
        )

    parsed_stocks = {}

    parsed_etfs = {}

    unparsed = []

    # --------------------------------------------------------
    # parse
    # --------------------------------------------------------

    for fallback_key, item in raw_items:

        normalized = normalize_record(
            item,
            fallback_key,
        )

        if normalized is None:

            unparsed.append(
                fallback_key
                or "<unknown>"
            )

            continue

        symbol = normalized[
            "symbol"
        ]

        if normalized[
            "type"
        ] == "STOCK":

            if symbol in parsed_stocks:

                raise RuntimeError(
                    "Universe 發現重複 STOCK："
                    + symbol
                )

            parsed_stocks[
                symbol
            ] = normalized

        elif normalized[
            "type"
        ] == "ETF":

            if symbol in parsed_etfs:

                raise RuntimeError(
                    "Universe 發現重複 ETF："
                    + symbol
                )

            parsed_etfs[
                symbol
            ] = normalized

    actual_stock_count = len(
        parsed_stocks
    )

    actual_etf_count = len(
        parsed_etfs
    )

    log(
        f"metadata stock_count : "
        f"{declared_stock_count}"
    )

    log(
        f"metadata etf_count   : "
        f"{declared_etf_count}"
    )

    log(
        f"解析 STOCK             : "
        f"{actual_stock_count}"
    )

    log(
        f"解析 ETF               : "
        f"{actual_etf_count}"
    )

    log(
        f"無法解析               : "
        f"{len(unparsed)}"
    )

    # --------------------------------------------------------
    # HARD GATE
    # --------------------------------------------------------

    if actual_stock_count != (
        declared_stock_count
    ):

        log("")
        log(
            "❌ STOCK Universe 數量不一致"
        )

        log(
            f"metadata = "
            f"{declared_stock_count}"
        )

        log(
            f"actual   = "
            f"{actual_stock_count}"
        )

        if unparsed:

            log(
                "前 100 個無法解析項目："
            )

            for value in unparsed[:100]:

                log(
                    f"  {value}"
                )

        raise RuntimeError(
            "Universe STOCK 數量不一致"
        )

    if actual_etf_count != (
        declared_etf_count
    ):

        log("")
        log(
            "❌ ETF Universe 數量不一致"
        )

        log(
            f"metadata = "
            f"{declared_etf_count}"
        )

        log(
            f"actual   = "
            f"{actual_etf_count}"
        )

        if unparsed:

            log(
                "前 100 個無法解析項目："
            )

            for value in unparsed[:100]:

                log(
                    f"  {value}"
                )

        raise RuntimeError(
            "Universe ETF 數量不一致"
        )

    if unparsed:

        log("")
        log(
            "❌ Universe 存在無法解析項目"
        )

        for value in unparsed[:100]:

            log(
                f"  {value}"
            )

        raise RuntimeError(
            "Universe 存在未解析商品"
        )

    # --------------------------------------------------------
    # 7794
    # --------------------------------------------------------

    target = parsed_stocks.get(
        "7794.TWO"
    )

    if target:

        log("")
        log(
            "✓ 7794.TWO Universe 驗證"
        )

        log(
            f"  code   = "
            f"{target['code']}"
        )

        log(
            f"  market = "
            f"{target['market']}"
        )

        log(
            f"  symbol = "
            f"{target['symbol']}"
        )

        log(
            f"  type   = "
            f"{target['type']}"
        )

    # --------------------------------------------------------
    # 英數 ETF / STOCK 驗證
    # --------------------------------------------------------

    alphanumeric = []

    for record in list(
        parsed_stocks.values()
    ) + list(
        parsed_etfs.values()
    ):

        code = record["code"]

        if not code.isdigit():

            alphanumeric.append(
                (
                    record["symbol"],
                    record["type"],
                )
            )

    log("")

    log(
        "英數商品代號解析："
        f"{len(alphanumeric)}"
    )

    if alphanumeric:

        for symbol, record_type in (
            alphanumeric[:20]
        ):

            log(
                f"  ✓ {symbol} "
                f"[{record_type}]"
            )

    return list(
        parsed_stocks.values()
    )


# ============================================================
# DATE WINDOW
# ============================================================

def required_months() -> List[
    Tuple[int, int]
]:
    """
    產生需要抓取的月份。

    以今天往前 HISTORY_LOOKBACK_DAYS
    到今天為範圍。

    多抓前一個月份作為 buffer。
    """

    now = datetime.now(
        timezone.utc
    )

    start_date = (
        now
        - timedelta(
            days=HISTORY_LOOKBACK_DAYS
        )
    )

    # 前一個月 buffer
    start_date = (
        start_date
        - timedelta(days=35)
    )

    year = start_date.year

    month = start_date.month

    end_year = now.year

    end_month = now.month

    result = []

    while (
        year < end_year
        or (
            year == end_year
            and month <= end_month
        )
    ):

        result.append(
            (
                year,
                month,
            )
        )

        month += 1

        if month > 12:

            month = 1
            year += 1

    return result


# ============================================================
# DATE FILTER
# ============================================================

def history_start_date() -> str:

    now = datetime.now(
        timezone.utc
    )

    start = (
        now
        - timedelta(
            days=HISTORY_LOOKBACK_DAYS
        )
    )

    return start.strftime(
        "%Y-%m-%d"
    )


# ============================================================
# TWSE MONTH
# ============================================================

def fetch_twse_month(
    code: str,
    year: int,
    month: int,
) -> List[
    Dict[str, Any]
]:

    roc_year = year - 1911

    date_value = (
        f"{roc_year:03d}"
        f"{month:02d}01"
    )

    params = {
        "response": "json",
        "date": date_value,
        "stockNo": code,
    }

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            response = SESSION.get(
                TWSE_STOCK_DAY_URL,
                params=params,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )

            response.raise_for_status()

            payload = response.json()

            if not isinstance(
                payload,
                dict,
            ):

                return []

            data = payload.get(
                "data",
                []
            )

            if not isinstance(
                data,
                list,
            ):

                return []

            rows = []

            for row in data:

                if not isinstance(
                    row,
                    list,
                ):

                    continue

                if len(row) < 7:

                    continue

                date_value = parse_date(
                    row[0]
                )

                if not date_value:

                    continue

                volume = safe_int(
                    row[1]
                )

                open_value = safe_float(
                    row[3]
                )

                high = safe_float(
                    row[4]
                )

                low = safe_float(
                    row[5]
                )

                close = safe_float(
                    row[6]
                )

                if (
                    close is None
                    or high is None
                    or low is None
                ):

                    continue

                if close <= 0:

                    continue

                if open_value is None:

                    open_value = close

                rows.append(
                    {
                        "date": date_value,
                        "open": open_value,
                        "high": high,
                        "low": low,
                        "close": close,
                        "volume": volume,
                    }
                )

            return rows

        except Exception as exc:

            if attempt >= MAX_RETRIES:

                log(
                    f"      ❌ TWSE "
                    f"{code} "
                    f"{year}-{month:02d}: "
                    f"{exc}"
                )

            else:

                time.sleep(
                    RETRY_DELAY
                    * attempt
                )

    return []


# ============================================================
# TPEX MONTH
# ============================================================

def fetch_tpex_month(
    code: str,
    year: int,
    month: int,
) -> List[
    Dict[str, Any]
]:

    roc_year = year - 1911

    params = {
        "l": "zh-tw",
        "d": (
            f"{roc_year:03d}/"
            f"{month:02d}"
        ),
        "stkno": code,
    }

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            response = SESSION.get(
                TPEX_ST43_URL,
                params=params,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )

            response.raise_for_status()

            payload = response.json()

            if not isinstance(
                payload,
                dict,
            ):

                return []

            data = payload.get(
                "aaData",
                []
            )

            if not isinstance(
                data,
                list,
            ):

                return []

            rows = []

            for row in data:

                if not isinstance(
                    row,
                    list,
                ):

                    continue

                if len(row) < 7:

                    continue

                date_value = parse_date(
                    row[0]
                )

                if not date_value:

                    continue

                volume = safe_int(
                    row[1]
                )

                open_value = safe_float(
                    row[3]
                )

                high = safe_float(
                    row[4]
                )

                low = safe_float(
                    row[5]
                )

                close = safe_float(
                    row[6]
                )

                if (
                    close is None
                    or high is None
                    or low is None
                ):

                    continue

                if close <= 0:

                    continue

                if open_value is None:

                    open_value = close

                rows.append(
                    {
                        "date": date_value,
                        "open": open_value,
                        "high": high,
                        "low": low,
                        "close": close,
                        "volume": volume,
                    }
                )

            return rows

        except Exception as exc:

            if attempt >= MAX_RETRIES:

                log(
                    f"      ❌ TPEx "
                    f"{code} "
                    f"{year}-{month:02d}: "
                    f"{exc}"
                )

            else:

                time.sleep(
                    RETRY_DELAY
                    * attempt
                )

    return []


# ============================================================
# OFFICIAL HISTORY
# ============================================================

def fetch_official_history(
    item: Dict[str, str],
    months: List[
        Tuple[int, int]
    ],
    start_date: str,
) -> List[
    Dict[str, Any]
]:

    code = item["code"]

    market = item["market"]

    all_rows = {}

    for year, month in months:

        if market == "TW":

            rows = fetch_twse_month(
                code,
                year,
                month,
            )

        else:

            rows = fetch_tpex_month(
                code,
                year,
                month,
            )

        for row in rows:

            date_value = row.get(
                "date"
            )

            if not date_value:

                continue

            # ------------------------------------------------
            # 只保留 lookback window
            # ------------------------------------------------

            if date_value < start_date:

                continue

            all_rows[
                date_value
            ] = row

        time.sleep(
            REQUEST_DELAY
        )

    return sorted(
        all_rows.values(),
        key=lambda row: row["date"],
    )


# ============================================================
# ONE STOCK
# ============================================================

def fetch_one(
    item: Dict[str, str],
    months: List[
        Tuple[int, int]
    ],
    start_date: str,
) -> Tuple[
    Optional[Dict[str, Any]],
    str,
]:

    symbol = item["symbol"]

    name = item["name"]

    market = item["market"]

    log(
        f"→ 官方："
        f"{symbol} "
        f"{name}"
    )

    rows = fetch_official_history(
        item,
        months,
        start_date,
    )

    row_count = len(rows)

    # --------------------------------------------------------
    # HARD MINIMUM
    # --------------------------------------------------------

    if row_count < (
        ABSOLUTE_MIN_HISTORY_ROWS
    ):

        reason = (
            "official_history_insufficient:"
            f"{row_count}"
        )

        log(
            f"❌ {symbol} "
            f"→ 官方資料只有 "
            f"{row_count} 筆 "
            f"< "
            f"{ABSOLUTE_MIN_HISTORY_ROWS}"
        )

        return (
            None,
            reason,
        )

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    if row_count >= (
        MIN_HISTORY_ROWS
    ):

        history_status = (
            "complete"
        )

    else:

        history_status = (
            "short_history"
        )

    source = (
        "TWSE official"
        if market == "TW"
        else "TPEx official"
    )

    log(
        f"✓ {symbol} "
        f"→ {row_count} 筆 "
        f"→ {source} "
        f"→ {history_status}"
    )

    return (
        {
            "symbol": symbol,
            "code": item["code"],
            "market": market,
            "name": name,
            "source": source,
            "history_rows": row_count,
            "history_status": history_status,
            "latest_date": rows[-1][
                "date"
            ],
            "prices": rows,
        },
        "",
    )


# ============================================================
# SHARDS
# ============================================================

def build_shards(
    results: Dict[
        str,
        Dict[str, Any]
    ],
) -> List[
    Dict[str, Any]
]:

    symbols = sorted(
        results.keys()
    )

    shards = []

    for start in range(
        0,
        len(symbols),
        STOCKS_PER_FILE,
    ):

        chunk = symbols[
            start:
            start + STOCKS_PER_FILE
        ]

        stocks = {}

        for symbol in chunk:

            stocks[symbol] = (
                results[
                    symbol
                ]["prices"]
            )

        shards.append(
            {
                "stocks": stocks
            }
        )

    return shards


# ============================================================
# SHARD VALIDATION
# ============================================================

def validate_shard(
    path: Path,
    expected_symbols: List[str],
) -> None:

    if not path.exists():

        raise RuntimeError(
            f"找不到 shard："
            f"{path.name}"
        )

    file_size = path.stat().st_size

    if file_size > (
        MAX_FILE_SIZE_BYTES
    ):

        raise RuntimeError(
            f"shard 超過 "
            f"{MAX_FILE_SIZE_MB} MB："
            f"{path.name}"
        )

    data = load_json(
        path
    )

    if not isinstance(
        data,
        dict,
    ):

        raise RuntimeError(
            f"{path.name} root 錯誤"
        )

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            f"{path.name} stocks 錯誤"
        )

    actual_symbols = set(
        stocks.keys()
    )

    expected_set = set(
        expected_symbols
    )

    if actual_symbols != expected_set:

        missing = (
            expected_set
            - actual_symbols
        )

        extra = (
            actual_symbols
            - expected_set
        )

        raise RuntimeError(
            f"{path.name} 股票集合不一致 "
            f"missing={sorted(missing)[:10]} "
            f"extra={sorted(extra)[:10]}"
        )

    for symbol, rows in stocks.items():

        if not isinstance(
            rows,
            list,
        ):

            raise RuntimeError(
                f"{symbol} prices "
                f"不是 list"
            )

        if len(rows) < (
            ABSOLUTE_MIN_HISTORY_ROWS
        ):

            raise RuntimeError(
                f"{symbol} 歷史資料不足："
                f"{len(rows)}"
            )

        previous_date = ""

        seen_dates = set()

        for row in rows:

            if not isinstance(
                row,
                dict,
            ):

                raise RuntimeError(
                    f"{symbol} "
                    f"price row 錯誤"
                )

            required = {
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
            }

            missing = (
                required
                - set(row.keys())
            )

            if missing:

                raise RuntimeError(
                    f"{symbol} "
                    f"缺少欄位："
                    f"{sorted(missing)}"
                )

            date_value = clean_text(
                row["date"]
            )

            if not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}",
                date_value,
            ):

                raise RuntimeError(
                    f"{symbol} "
                    f"日期格式錯誤："
                    f"{date_value}"
                )

            if (
                previous_date
                and date_value
                < previous_date
            ):

                raise RuntimeError(
                    f"{symbol} "
                    f"日期未排序"
                )

            if date_value in seen_dates:

                raise RuntimeError(
                    f"{symbol} "
                    f"存在重複日期："
                    f"{date_value}"
                )

            seen_dates.add(
                date_value
            )

            previous_date = date_value

            # ------------------------------------------------
            # Numeric validation
            # ------------------------------------------------

            for field in (
                "open",
                "high",
                "low",
                "close",
            ):

                number = safe_float(
                    row[field]
                )

                if number is None:

                    raise RuntimeError(
                        f"{symbol} "
                        f"{date_value} "
                        f"{field} 無效"
                    )

                if number <= 0:

                    raise RuntimeError(
                        f"{symbol} "
                        f"{date_value} "
                        f"{field} <= 0"
                    )

            volume = safe_int(
                row["volume"]
            )

            if volume < 0:

                raise RuntimeError(
                    f"{symbol} "
                    f"{date_value} "
                    f"volume < 0"
                )


# ============================================================
# MANIFEST
# ============================================================

def build_manifest(
    shard_files: List[str],
    results: Dict[
        str,
        Dict[str, Any]
    ],
    universe_count: int,
) -> Dict[str, Any]:

    source_counts = {}

    complete_count = 0

    short_count = 0

    latest_dates = []

    for result in results.values():

        source = result.get(
            "source",
            ""
        )

        source_counts[
            source
        ] = (
            source_counts.get(
                source,
                0,
            )
            + 1
        )

        status = result.get(
            "history_status"
        )

        if status == "complete":

            complete_count += 1

        elif status == "short_history":

            short_count += 1

        else:

            raise RuntimeError(
                "未知 history_status："
                + str(status)
            )

        latest = result.get(
            "latest_date"
        )

        if latest:

            latest_dates.append(
                latest
            )

    price_stock_count = len(
        results
    )

    failed_count = (
        universe_count
        - price_stock_count
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "generator_version": VERSION,
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "history_lookback_days": (
            HISTORY_LOOKBACK_DAYS
        ),
        "universe_stock_count": (
            universe_count
        ),
        "price_stock_count": (
            price_stock_count
        ),
        "complete_history_count": (
            complete_count
        ),
        "short_history_count": (
            short_count
        ),
        "failed_count": (
            failed_count
        ),
        "min_history_rows": (
            MIN_HISTORY_ROWS
        ),
        "absolute_min_history_rows": (
            ABSOLUTE_MIN_HISTORY_ROWS
        ),
        "sources": source_counts,
        "latest_date": (
            max(latest_dates)
            if latest_dates
            else None
        ),
        "files": shard_files,
    }


# ============================================================
# MANIFEST VALIDATION
# ============================================================

def validate_manifest(
    path: Path,
    expected_symbols: List[str],
    expected_shards: List[str],
) -> None:

    if not path.exists():

        raise RuntimeError(
            "manifest.json 不存在"
        )

    manifest = load_json(
        path
    )

    if not isinstance(
        manifest,
        dict,
    ):

        raise RuntimeError(
            "manifest root 錯誤"
        )

    if manifest.get(
        "schema_version"
    ) != SCHEMA_VERSION:

        raise RuntimeError(
            "manifest schema_version 錯誤"
        )

    if manifest.get(
        "generator_version"
    ) != VERSION:

        raise RuntimeError(
            "manifest generator_version 錯誤"
        )

    if manifest.get(
        "universe_stock_count"
    ) != len(expected_symbols):

        raise RuntimeError(
            "manifest "
            "universe_stock_count 錯誤"
        )

    if manifest.get(
        "price_stock_count"
    ) != len(expected_symbols):

        raise RuntimeError(
            "manifest "
            "price_stock_count 錯誤："
            f"{manifest.get('price_stock_count')} "
            f"!= "
            f"{len(expected_symbols)}"
        )

    if manifest.get(
        "failed_count"
    ) != 0:

        raise RuntimeError(
            "manifest failed_count "
            "必須為 0"
        )

    files = manifest.get(
        "files"
    )

    if files != expected_shards:

        raise RuntimeError(
            "manifest.files 不一致"
        )

    sources = manifest.get(
        "sources"
    )

    if not isinstance(
        sources,
        dict,
    ):

        raise RuntimeError(
            "manifest.sources 錯誤"
        )

    # V8.0 價格來源只能是官方
    for source in sources:

        if source not in {
            "TWSE official",
            "TPEx official",
        }:

            raise RuntimeError(
                "偵測到非官方價格來源："
                + source
            )


# ============================================================
# WRITE TEMP
# ============================================================

def write_price_directory(
    temp_dir: Path,
    results: Dict[
        str,
        Dict[str, Any]
    ],
    universe_count: int,
) -> None:

    temp_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # --------------------------------------------------------
    # 1. Universe / Result 1:1
    # --------------------------------------------------------

    if len(results) != universe_count:

        raise RuntimeError(
            "Price 結果數量與 Universe "
            "不一致："
            f"{len(results)} "
            f"!= "
            f"{universe_count}"
        )

    # --------------------------------------------------------
    # 2. Build shards
    # --------------------------------------------------------

    shards = build_shards(
        results
    )

    symbols = sorted(
        results.keys()
    )

    shard_files = []

    for index, shard in enumerate(
        shards,
        start=1,
    ):

        filename = (
            f"prices_{index:03d}.json"
        )

        path = (
            temp_dir
            / filename
        )

        save_json(
            path,
            shard,
        )

        start = (
            (index - 1)
            * STOCKS_PER_FILE
        )

        expected = symbols[
            start:
            start + STOCKS_PER_FILE
        ]

        validate_shard(
            path,
            expected,
        )

        shard_files.append(
            filename
        )

    # --------------------------------------------------------
    # 3. Manifest
    # --------------------------------------------------------

    manifest = build_manifest(
        shard_files,
        results,
        universe_count,
    )

    manifest_path = (
        temp_dir
        / "manifest.json"
    )

    save_json(
        manifest_path,
        manifest,
    )

    validate_manifest(
        manifest_path,
        symbols,
        shard_files,
    )

    log(
        f"✓ shard 驗證完成："
        f"{len(shard_files)} 個"
    )

    log(
        "✓ manifest 驗證完成"
    )


# ============================================================
# ATOMIC REPLACE
# ============================================================

def replace_output(
    temp_dir: Path,
) -> None:

    backup_dir = (
        DATA_DIR
        / ".prices_backup"
    )

    if backup_dir.exists():

        shutil.rmtree(
            backup_dir
        )

    # --------------------------------------------------------
    # 舊資料先改名
    # --------------------------------------------------------

    if OUTPUT_DIR.exists():

        OUTPUT_DIR.rename(
            backup_dir
        )

    try:

        temp_dir.rename(
            OUTPUT_DIR
        )

    except Exception:

        # ----------------------------------------------------
        # 新資料替換失敗
        # 恢復舊資料
        # ----------------------------------------------------

        if OUTPUT_DIR.exists():

            shutil.rmtree(
                OUTPUT_DIR
            )

        if backup_dir.exists():

            backup_dir.rename(
                OUTPUT_DIR
            )

        raise

    # --------------------------------------------------------
    # 成功後刪除 backup
    # --------------------------------------------------------

    if backup_dir.exists():

        shutil.rmtree(
            backup_dir
        )


# ============================================================
# RESULT REPORT
# ============================================================

def print_failures(
    failures: Dict[str, str],
    limit: int = 100,
) -> None:

    if not failures:

        return

    log("")

    log(
        f"失敗標的："
        f"{len(failures)}"
    )

    for symbol, reason in list(
        failures.items()
    )[:limit]:

        log(
            f"  {symbol}: "
            f"{reason}"
        )


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    started = time.time()

    section(
        f"fetch_prices.py {VERSION}"
    )

    log(
        "價格來源：TWSE / TPEx 官方"
    )

    log(
        "Yahoo fallback：DISABLED"
    )

    log(
        "CMoney：DISABLED"
    )

    log(
        f"最低歷史資料："
        f"{ABSOLUTE_MIN_HISTORY_ROWS}"
        f" 筆"
    )

    log(
        f"完整歷史："
        f"{MIN_HISTORY_ROWS}"
        f" 筆"
    )

    log(
        f"歷史窗口："
        f"最近 {HISTORY_LOOKBACK_DAYS} "
        f"個日曆日"
    )

    # ========================================================
    # Universe
    # ========================================================

    universe = load_universe()

    universe_count = len(
        universe
    )

    expected_symbols = {
        item["symbol"]
        for item in universe
    }

    if len(expected_symbols) != (
        universe_count
    ):

        raise RuntimeError(
            "Universe STOCK symbol "
            "存在重複"
        )

    # ========================================================
    # Date window
    # ========================================================

    months = required_months()

    start_date = history_start_date()

    section(
        "官方價格資料抓取窗口"
    )

    log(
        f"開始日期："
        f"{start_date}"
    )

    log(
        f"月份數量："
        f"{len(months)}"
    )

    log(
        "月份："
        + ", ".join(
            f"{year}-{month:02d}"
            for year, month in months
        )
    )

    # ========================================================
    # Fetch
    # ========================================================

    section(
        "開始官方 STOCK 價格抓取"
    )

    log(
        f"Universe STOCK："
        f"{universe_count} 檔"
    )

    results = {}

    failures = {}

    source_counts = {}

    for index, item in enumerate(
        universe,
        start=1,
    ):

        symbol = item["symbol"]

        log("")

        log(
            f"[{index}/{universe_count}] "
            f"{symbol} "
            f"{item['name']}"
        )

        try:

            result, reason = fetch_one(
                item,
                months,
                start_date,
            )

            if result is None:

                failures[
                    symbol
                ] = reason

            else:

                results[
                    symbol
                ] = result

                source = result[
                    "source"
                ]

                source_counts[
                    source
                ] = (
                    source_counts.get(
                        source,
                        0,
                    )
                    + 1
                )

        except Exception as exc:

            failures[
                symbol
            ] = str(exc)

            log(
                f"❌ {symbol} "
                f"未預期錯誤："
                f"{exc}"
            )

        time.sleep(
            REQUEST_DELAY
        )

    # ========================================================
    # Result
    # ========================================================

    success_count = len(
        results
    )

    failed_count = len(
        failures
    )

    success_rate = (
        success_count
        / universe_count
        if universe_count
        else 0
    )

    section(
        "V8.0 PRICE RESULT"
    )

    log(
        f"Universe STOCK："
        f"{universe_count}"
    )

    log(
        f"官方成功："
        f"{success_count}"
    )

    log(
        f"官方失敗："
        f"{failed_count}"
    )

    log(
        f"成功率："
        f"{success_rate:.2%}"
    )

    for source, count in sorted(
        source_counts.items()
    ):

        log(
            f"來源 {source}："
            f"{count}"
        )

    # ========================================================
    # HARD 1:1 GATE
    # ========================================================

    missing_symbols = (
        expected_symbols
        - set(results.keys())
    )

    extra_symbols = (
        set(results.keys())
        - expected_symbols
    )

    if extra_symbols:

        log("")

        log(
            "❌ Price 出現 Universe "
            "以外的標的"
        )

        for symbol in sorted(
            extra_symbols
        )[:100]:

            log(
                f"  {symbol}"
            )

        return 1

    if missing_symbols:

        log("")

        log(
            "❌ 官方價格資料未達成 "
            "Universe 1:1"
        )

        log(
            f"缺少："
            f"{len(missing_symbols)} 檔"
        )

        for symbol in sorted(
            missing_symbols
        )[:100]:

            log(
                f"  {symbol}: "
                f"{failures.get(symbol, '')}"
            )

        log("")

        log(
            "V8.0 不允許部分成功覆蓋 "
            "Data/prices"
        )

        return 1

    if success_count != (
        universe_count
    ):

        log("")

        log(
            "❌ Price 數量不等於 "
            "Universe STOCK"
        )

        return 1

    # ========================================================
    # HARD SOURCE GATE
    # ========================================================

    allowed_sources = {
        "TWSE official",
        "TPEx official",
    }

    for result in results.values():

        source = result.get(
            "source"
        )

        if source not in (
            allowed_sources
        ):

            log("")

            log(
                "❌ 偵測到非官方來源："
                + str(source)
            )

            return 1

    # ========================================================
    # TEMP DIRECTORY
    # ========================================================

    temp_root = Path(
        tempfile.mkdtemp(
            prefix="prices_build_",
            dir=str(DATA_DIR),
        )
    )

    temp_dir = (
        temp_root
        / "prices"
    )

    try:

        section(
            "建立 temporary Data/prices"
        )

        write_price_directory(
            temp_dir,
            results,
            universe_count,
        )

        # ====================================================
        # 7794
        # ====================================================

        if "7794.TWO" in (
            expected_symbols
        ):

            record = results.get(
                "7794.TWO"
            )

            log("")

            log(
                "=" * 60
            )

            if record:

                log(
                    "✓ 7794.TWO 最終驗證"
                )

                log(
                    f"資料筆數："
                    f"{record['history_rows']}"
                )

                log(
                    f"資料來源："
                    f"{record['source']}"
                )

                log(
                    f"最新日期："
                    f"{record['latest_date']}"
                )

                log(
                    f"狀態："
                    f"{record['history_status']}"
                )

            else:

                log(
                    "❌ 7794.TWO "
                    "不存在價格資料"
                )

                raise RuntimeError(
                    "7794.TWO price missing"
                )

            log(
                "=" * 60
            )

        # ====================================================
        # FINAL MANIFEST CHECK
        # ====================================================

        manifest_path = (
            temp_dir
            / "manifest.json"
        )

        validate_manifest(
            manifest_path,
            sorted(expected_symbols),
            sorted(
                p.name
                for p in temp_dir.glob(
                    "prices_*.json"
                )
            ),
        )

        # ====================================================
        # ATOMIC
        # ====================================================

        section(
            "Atomic Replace Data/prices"
        )

        replace_output(
            temp_dir
        )

        log(
            "✓ Data/prices/ 已成功更新"
        )

    except Exception as exc:

        log("")

        log(
            "❌ 價格資料建置失敗："
            f"{exc}"
        )

        log(
            "正式 Data/prices 未被更新"
        )

        if temp_root.exists():

            shutil.rmtree(
                temp_root,
                ignore_errors=True,
            )

        return 1

    finally:

        if temp_root.exists():

            shutil.rmtree(
                temp_root,
                ignore_errors=True,
            )

    # ========================================================
    # FINAL
    # ========================================================

    elapsed = (
        time.time()
        - started
    )

    section(
        "FINAL PRICE RESULT"
    )

    log(
        f"Universe STOCK："
        f"{universe_count}"
    )

    log(
        f"官方成功："
        f"{success_count}"
    )

    log(
        f"官方失敗："
        f"{failed_count}"
    )

    log(
        f"成功率："
        f"{success_rate:.2%}"
    )

    log(
        f"TWSE official："
        f"{source_counts.get('TWSE official', 0)}"
    )

    log(
        f"TPEx official："
        f"{source_counts.get('TPEx official', 0)}"
    )

    complete_count = sum(
        1
        for result in results.values()
        if result.get(
            "history_status"
        ) == "complete"
    )

    short_count = sum(
        1
        for result in results.values()
        if result.get(
            "history_status"
        ) == "short_history"
    )

    log(
        f"complete："
        f"{complete_count}"
    )

    log(
        f"short_history："
        f"{short_count}"
    )

    # --------------------------------------------------------
    # 7794
    # --------------------------------------------------------

    if "7794.TWO" in expected_symbols:

        record = results.get(
            "7794.TWO"
        )

        if record:

            log(
                "✓ 7794.TWO："
                "已進入官方價格資料鏈"
            )

        else:

            log(
                "❌ 7794.TWO："
                "缺少官方價格資料"
            )

            return 1

    # --------------------------------------------------------
    # FINAL HARD GATE
    # --------------------------------------------------------

    if success_count != (
        universe_count
    ):

        log(
            "❌ FINAL FAIL："
            "Price / Universe != 1:1"
        )

        return 1

    if failed_count != 0:

        log(
            "❌ FINAL FAIL："
            "存在價格抓取失敗"
        )

        return 1

    log(
        f"執行時間："
        f"{elapsed:.1f} 秒"
    )

    log(
        "✓ V8.0 fetch_prices.py 完成"
    )

    log(
        "✓ 官方來源 ONLY"
    )

    log(
        "✓ Universe / Price 1:1"
    )

    log(
        "✓ Yahoo fallback DISABLED"
    )

    log(
        "✓ Atomic replace"
    )

    return 0


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    try:

        sys.exit(
            main()
        )

    except KeyboardInterrupt:

        log("")
        log(
            "❌ 使用者中斷執行"
        )

        sys.exit(130)

    except Exception as exc:

        log("")
        log(
            "❌ FATAL ERROR："
            f"{exc}"
        )

        sys.exit(1)
