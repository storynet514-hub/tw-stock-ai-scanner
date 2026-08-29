#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/fetch_prices.py

正式價格管線 V9.0
============================================================

核心原則
------------------------------------------------------------

1. Data/universe.json 是唯一 Universe 來源
2. STOCK / ETF 都進價格管線
3. 不修改 Universe
4. 不使用成交行情建立 Universe
5. 不使用 CMoney
6. 不逐檔抓官方歷史價格
7. 官方歷史資料採「整市場 / 每交易日」批次
8. TWSE 官方優先
9. TPEx 官方優先
10. Yahoo 僅作官方批次不足時的最後 fallback
11. fallback 永遠標記來源
12. 不把 fallback 假裝成官方資料
13. OHLC / 日期 / volume 做完整性驗證
14. 正常目標 90 個交易日
15. 絕對最低 20 筆
16. 少數商品缺資料只進 diagnostics
17. 不因少數商品缺失直接讓 Action FAIL
18. 成功率低於 80% 才 FAIL
19. temporary directory
20. shard 驗證
21. manifest 驗證
22. atomic replace
23. 舊版 prices schema 不符合 V9.0 時安全重建
24. 每日增量只更新最新交易日
25. 新增 Universe 商品無完整歷史時重新執行官方批次初始化


官方來源
------------------------------------------------------------

TWSE:
https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX

TPEx:
https://www.tpex.org.tw/web/stock/aftertrading/
otc_quotes_no1430/stk_wn1430_result.php

Yahoo fallback:
https://query1.finance.yahoo.com/v8/finance/chart/{symbol}


重要行為
------------------------------------------------------------

例如：

Universe = 1500
成功 = 1496
缺失 = 4

成功率：

1496 / 1500 = 99.73%

結果：

✓ Action PASS

而不是：

❌ 價格資料驗證失敗
❌ exit 1


缺失商品會寫入：

Data/price_diagnostics.json

以及：

Data/prices/diagnostics.json


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

from datetime import (
    date,
    datetime,
    timedelta,
    timezone,
)

from pathlib import Path
from typing import (
    Any,
    Dict,
    Iterable,
    List,
    Optional,
    Tuple,
)

import requests


# ============================================================
# VERSION
# ============================================================

VERSION = "V9.0"

SCHEMA_VERSION = "prices-v9.0"


# ============================================================
# PATH
# ============================================================

BASE_DIR = Path(
    __file__
).resolve().parent.parent

DATA_DIR = (
    BASE_DIR / "Data"
)

UNIVERSE_FILE = (
    DATA_DIR / "universe.json"
)

OUTPUT_DIR = (
    DATA_DIR / "prices"
)

DIAGNOSTIC_FILE = (
    DATA_DIR / "price_diagnostics.json"
)


# ============================================================
# PRICE SETTINGS
# ============================================================

INITIAL_HISTORY_DAYS = 90

INITIAL_LOOKBACK_CALENDAR_DAYS = 150

MAX_HISTORY_ROWS = 90

ABSOLUTE_MIN_HISTORY_ROWS = 20

STOCKS_PER_FILE = 100

MAX_FILE_SIZE_BYTES = (
    80 * 1024 * 1024
)


# ============================================================
# SAFETY
# ============================================================

MIN_SUCCESS_RATE = 0.80

MAX_RETRIES = 3

REQUEST_TIMEOUT = 30

REQUEST_DELAY = 0.15

RETRY_DELAY = 1.5


# ============================================================
# OFFICIAL ENDPOINTS
# ============================================================

TWSE_MI_INDEX_URL = (
    "https://www.twse.com.tw/"
    "rwd/zh/afterTrading/MI_INDEX"
)

TPEX_DAILY_URL = (
    "https://www.tpex.org.tw/"
    "web/stock/aftertrading/"
    "otc_quotes_no1430/"
    "stk_wn1430_result.php"
)


# ============================================================
# YAHOO FALLBACK
# ============================================================

YAHOO_URL = (
    "https://query1.finance.yahoo.com/"
    "v8/finance/chart/{symbol}"
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

def log(
    message: str = "",
) -> None:

    print(
        message,
        flush=True,
    )


def section(
    title: str,
) -> None:

    log(
        "\n"
        + "=" * 72
    )

    log(title)

    log(
        "=" * 72
    )


# ============================================================
# JSON
# ============================================================

def load_json(
    path: Path,
) -> Any:

    with path.open(
        "r",
        encoding="utf-8-sig",
    ) as f:

        return json.load(f)


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
    ) as f:

        json.dump(
            data,
            f,
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

    text = clean_text(
        value
    )

    text = (
        text
        .replace(",", "")
        .replace("--", "")
        .strip()
    )

    if not text:
        return None

    if text in {
        "-",
        "—",
        "－",
        "N/A",
        "NA",
    }:
        return None

    try:

        number = float(
            text.replace(
                "+",
                "",
            )
        )

        if not math.isfinite(
            number
        ):
            return None

        return number

    except Exception:

        return None


def safe_int(
    value: Any,
) -> int:

    number = safe_float(
        value
    )

    if number is None:
        return 0

    return int(number)


# ============================================================
# DATE
# ============================================================

def parse_date(
    value: Any,
) -> Optional[str]:

    text = clean_text(
        value
    )

    if not text:
        return None

    text = (
        text
        .replace("年", "/")
        .replace("月", "/")
        .replace("日", "")
    )

    compact = re.sub(
        r"[^0-9]",
        "",
        text,
    )

    # --------------------------------------------------------
    # YYYYMMDD
    # --------------------------------------------------------

    if len(compact) == 8:

        try:

            year = int(
                compact[:4]
            )

            month = int(
                compact[4:6]
            )

            day = int(
                compact[6:8]
            )

            if (
                1911
                <= year
                <= 2100
            ):

                return date(
                    year,
                    month,
                    day,
                ).isoformat()

        except Exception:
            pass

    # --------------------------------------------------------
    # ROC YYYYMMDD
    # --------------------------------------------------------

    if len(compact) == 7:

        try:

            year = (
                int(
                    compact[:3]
                )
                + 1911
            )

            month = int(
                compact[3:5]
            )

            day = int(
                compact[5:7]
            )

            return date(
                year,
                month,
                day,
            ).isoformat()

        except Exception:
            pass

    # --------------------------------------------------------
    # ISO / normal
    # --------------------------------------------------------

    for fmt in (
        "%Y/%m/%d",
        "%Y-%m-%d",
        "%Y%m%d",
    ):

        try:

            return datetime.strptime(
                text,
                fmt,
            ).date().isoformat()

        except Exception:
            pass

    # --------------------------------------------------------
    # ROC slash date
    # --------------------------------------------------------

    parts = text.split("/")

    if len(parts) == 3:

        try:

            year = int(
                parts[0]
            )

            month = int(
                parts[1]
            )

            day = int(
                parts[2]
            )

            if year < 1911:

                year += 1911

            return date(
                year,
                month,
                day,
            ).isoformat()

        except Exception:
            pass

    return None


# ============================================================
# CODE
# ============================================================

def extract_code(
    value: Any,
) -> Optional[str]:

    text = clean_text(
        value
    ).upper()

    if text.endswith(".TWO"):

        text = text[:-4]

    elif text.endswith(".TW"):

        text = text[:-3]

    if not (
        4
        <= len(text)
        <= 6
    ):

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

    if text in {
        "STOCK",
        "ETF",
    }:

        return text

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
        "上市股票",
    }:

        return "TW"

    if text in {
        "TWO",
        "TPEX",
        "OTC",
        "上櫃",
        "上柜",
        "上櫃股票",
    }:

        return "TWO"

    return None


# ============================================================
# DICT HELPER
# ============================================================

def first_value(
    data: Dict[str, Any],
    keys: Iterable[str],
) -> Any:

    lowered = {
        str(key)
        .strip()
        .lower(): value
        for key, value
        in data.items()
    }

    for key in keys:

        normalized = (
            str(key)
            .strip()
            .lower()
        )

        if normalized in lowered:

            return lowered[
                normalized
            ]

    return None


# ============================================================
# NORMALIZE UNIVERSE RECORD
# ============================================================

def normalize_record(
    item: Any,
    fallback_key: Optional[str] = None,
) -> Optional[
    Dict[str, str]
]:

    if not isinstance(
        item,
        dict,
    ):

        return None

    record_type = normalize_type(
        first_value(
            item,
            ["type"],
        )
    )

    if record_type is None:

        return None

    raw_symbol = first_value(
        item,
        [
            "full_symbol",
            "fullSymbol",
            "yahoo_symbol",
            "yahooSymbol",
            "symbol",
        ],
    )

    code = None

    candidates = [
        first_value(
            item,
            [
                "code",
                "stock_code",
                "stock_id",
                "ticker",
                "security_code",
                "證券代號",
                "有價證券代號",
                "代號",
            ],
        ),
        raw_symbol,
        fallback_key,
    ]

    for value in candidates:

        code = extract_code(
            value
        )

        if code:
            break

    if code is None:

        return None

    market = normalize_market(
        first_value(
            item,
            ["market"],
        )
    )

    symbol_text = clean_text(
        raw_symbol
    ).upper()

    if market is None:

        if symbol_text.endswith(
            ".TWO"
        ):

            market = "TWO"

        elif symbol_text.endswith(
            ".TW"
        ):

            market = "TW"

    if market is None:

        return None

    name = clean_text(
        first_value(
            item,
            [
                "name",
                "stock_name",
                "security_name",
                "company_name",
                "證券名稱",
                "名稱",
            ],
        )
    )

    suffix = (
        ".TWO"
        if market == "TWO"
        else ".TW"
    )

    return {
        "symbol": (
            code + suffix
        ),
        "code": code,
        "market": market,
        "type": record_type,
        "name": name,
    }


# ============================================================
# LOAD UNIVERSE
# ============================================================

def load_universe():

    section(
        "讀取 Data/universe.json"
    )

    if not UNIVERSE_FILE.exists():

        raise RuntimeError(
            "找不到 Data/universe.json"
        )

    universe_data = load_json(
        UNIVERSE_FILE
    )

    if not isinstance(
        universe_data,
        dict,
    ):

        raise RuntimeError(
            "universe.json 根節點必須是 object"
        )

    stock_count = universe_data.get(
        "stock_count"
    )

    etf_count = universe_data.get(
        "etf_count"
    )

    if not isinstance(
        stock_count,
        int,
    ):

        raise RuntimeError(
            "Universe stock_count "
            "不存在或不是整數"
        )

    if not isinstance(
        etf_count,
        int,
    ):

        raise RuntimeError(
            "Universe etf_count "
            "不存在或不是整數"
        )

    raw = universe_data.get(
        "stocks"
    )

    if isinstance(
        raw,
        list,
    ):

        pairs = [
            (None, item)
            for item in raw
        ]

    elif isinstance(
        raw,
        dict,
    ):

        pairs = [
            (str(key), value)
            for key, value
            in raw.items()
        ]

    else:

        raise RuntimeError(
            "Universe stocks "
            "必須是 list 或 dict"
        )

    stocks = {}
    etfs = {}
    invalid = []

    for fallback_key, item in pairs:

        normalized = normalize_record(
            item,
            fallback_key,
        )

        if normalized is None:

            invalid.append(
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

            stocks.setdefault(
                symbol,
                normalized,
            )

        else:

            etfs.setdefault(
                symbol,
                normalized,
            )

    if (
        len(stocks)
        != stock_count
        or len(etfs)
        != etf_count
    ):

        raise RuntimeError(
            "Universe 數量不一致："
            f"metadata STOCK={stock_count} "
            f"ETF={etf_count}；"
            f"parsed STOCK={len(stocks)} "
            f"ETF={len(etfs)}"
        )

    if invalid:

        raise RuntimeError(
            "Universe 存在無法解析商品："
            + ", ".join(
                invalid[:30]
            )
        )

    universe = (
        list(stocks.values())
        + list(etfs.values())
    )

    log(
        f"Universe STOCK："
        f"{len(stocks)}"
    )

    log(
        f"Universe ETF："
        f"{len(etfs)}"
    )

    log(
        f"Universe TOTAL："
        f"{len(universe)}"
    )

    return (
        stocks,
        etfs,
        universe,
    )


# ============================================================
# HTTP JSON
# ============================================================

def request_json(
    url: str,
    params: Optional[
        Dict[str, Any]
    ] = None,
) -> Any:

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )

            response.raise_for_status()

            if not response.text.strip():

                raise RuntimeError(
                    "empty response"
                )

            return response.json()

        except Exception as exc:

            last_error = exc

            if attempt < MAX_RETRIES:

                time.sleep(
                    RETRY_DELAY
                    * attempt
                )

    raise RuntimeError(
        f"HTTP JSON failed: "
        f"{last_error}"
    )


# ============================================================
# PRICE VALIDATION
# ============================================================

def normalize_price_row(
    date_value: Any,
    open_value: Any,
    high_value: Any,
    low_value: Any,
    close_value: Any,
    volume_value: Any,
) -> Optional[
    Dict[str, Any]
]:

    parsed_date = parse_date(
        date_value
    )

    open_price = safe_float(
        open_value
    )

    high_price = safe_float(
        high_value
    )

    low_price = safe_float(
        low_value
    )

    close_price = safe_float(
        close_value
    )

    volume = safe_int(
        volume_value
    )

    if not parsed_date:

        return None

    if None in {
        open_price,
        high_price,
        low_price,
        close_price,
    }:

        return None

    if min(
        open_price,
        high_price,
        low_price,
        close_price,
    ) <= 0:

        return None

    if volume < 0:

        return None

    if high_price < max(
        open_price,
        close_price,
    ):

        return None

    if low_price > min(
        open_price,
        close_price,
    ):

        return None

    if high_price < low_price:

        return None

    return {
        "date": parsed_date,
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": close_price,
        "volume": volume,
    }


# ============================================================
# TWSE TABLE RECURSION
# ============================================================

def find_twse_tables(
    payload: Any,
) -> List[Dict[str, Any]]:

    result = []

    def walk(
        value: Any,
    ):

        if isinstance(
            value,
            dict,
        ):

            if (
                isinstance(
                    value.get("fields"),
                    list,
                )
                and isinstance(
                    value.get("data"),
                    list,
                )
            ):

                result.append(
                    value
                )

            for child in value.values():

                walk(child)

        elif isinstance(
            value,
            list,
        ):

            for child in value:

                walk(child)

    walk(payload)

    return result


# ============================================================
# TWSE DAILY
# ============================================================

def fetch_twse_day(
    target_date: str,
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Optional[str],
]:

    params = {
        "response": "json",
        "date": target_date.replace(
            "-",
            "",
        ),
        "type": "ALLBUT0999",
    }

    payload = request_json(
        TWSE_MI_INDEX_URL,
        params,
    )

    if isinstance(
        payload,
        dict,
    ):

        status = payload.get(
            "stat"
        )

        if status not in (
            None,
            "OK",
        ):

            return (
                {},
                f"stat={status}",
            )

    tables = find_twse_tables(
        payload
    )

    for table in tables:

        fields = table.get(
            "fields",
            []
        )

        data = table.get(
            "data",
            []
        )

        if (
            "證券代號"
            not in fields
            or "收盤價"
            not in fields
        ):

            continue

        index = {
            name: fields.index(name)
            for name in fields
        }

        required = [
            "證券代號",
            "開盤價",
            "最高價",
            "最低價",
            "收盤價",
        ]

        if not all(
            key in index
            for key in required
        ):

            continue

        result = {}

        for row in data:

            if not isinstance(
                row,
                list,
            ):

                continue

            code_index = index[
                "證券代號"
            ]

            if (
                code_index
                >= len(row)
            ):

                continue

            code = extract_code(
                row[code_index]
            )

            if not code:

                continue

            row_date = (
                row[index["日期"]]
                if (
                    "日期" in index
                    and index["日期"]
                    < len(row)
                )
                else target_date
            )

            volume = (
                row[index["成交股數"]]
                if (
                    "成交股數" in index
                    and index["成交股數"]
                    < len(row)
                )
                else 0
            )

            normalized = (
                normalize_price_row(
                    row_date,
                    row[index["開盤價"]],
                    row[index["最高價"]],
                    row[index["最低價"]],
                    row[index["收盤價"]],
                    volume,
                )
            )

            if (
                normalized
                and normalized["date"]
                == target_date
            ):

                result[code] = normalized

        if result:

            return (
                result,
                None,
            )

    return (
        {},
        "找不到含證券代號/完整 OHLC 的官方表格",
    )


# ============================================================
# TPEX TABLE EXTRACTION
# ============================================================

def extract_tpex_table(
    payload: Any,
) -> Tuple[
    List[Any],
    List[Any],
]:

    if not isinstance(
        payload,
        dict,
    ):

        return [], []

    tables = payload.get(
        "tables"
    )

    if (
        isinstance(
            tables,
            list,
        )
        and tables
    ):

        for table in tables:

            if (
                isinstance(
                    table,
                    dict,
                )
                and isinstance(
                    table.get("data"),
                    list,
                )
            ):

                return (
                    table.get(
                        "fields"
                    )
                    or [],
                    table.get(
                        "data"
                    )
                    or [],
                )

    fields = payload.get(
        "fields"
    )

    data = payload.get(
        "aaData"
    )

    if data is None:

        data = payload.get(
            "data"
        )

    return (
        fields or [],
        data or [],
    )


# ============================================================
# TPEX FIELD INDEX
# ============================================================

def tpex_field_index(
    fields: List[Any],
    names: List[str],
) -> Optional[int]:

    if not isinstance(
        fields,
        list,
    ):

        return None

    for name in names:

        if name in fields:

            return fields.index(
                name
            )

    return None


# ============================================================
# TPEX DAILY
# ============================================================

def fetch_tpex_day(
    target_date: str,
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Optional[str],
]:

    dt = datetime.strptime(
        target_date,
        "%Y-%m-%d",
    )

    roc_year = (
        dt.year - 1911
    )

    params = {
        "l": "zh-tw",
        "d": (
            f"{roc_year:03d}/"
            f"{dt.month:02d}/"
            f"{dt.day:02d}"
        ),
        "se": "EW",
        "o": "json",
    }

    payload = request_json(
        TPEX_DAILY_URL,
        params,
    )

    fields, data = (
        extract_tpex_table(
            payload
        )
    )

    if not isinstance(
        data,
        list,
    ) or not data:

        return (
            {},
            "aaData/data 為空",
        )

    # --------------------------------------------------------
    # 優先使用官方欄位名稱。
    # --------------------------------------------------------

    code_index = tpex_field_index(
        fields,
        [
            "公司代號",
            "證券代號",
        ],
    )

    close_index = tpex_field_index(
        fields,
        ["收盤價"],
    )

    open_index = tpex_field_index(
        fields,
        ["開盤價"],
    )

    high_index = tpex_field_index(
        fields,
        ["最高價"],
    )

    low_index = tpex_field_index(
        fields,
        ["最低價"],
    )

    volume_index = tpex_field_index(
        fields,
        [
            "成交股數",
            "成交量",
        ],
    )

    result = {}

    for row in data:

        if not isinstance(
            row,
            list,
        ):

            continue

        try:

            # ------------------------------------------------
            # 官方 fields 模式
            # ------------------------------------------------

            if (
                fields
                and None not in {
                    code_index,
                    close_index,
                    open_index,
                    high_index,
                    low_index,
                }
            ):

                code_value = row[
                    code_index
                ]

                close_value = row[
                    close_index
                ]

                open_value = row[
                    open_index
                ]

                high_value = row[
                    high_index
                ]

                low_value = row[
                    low_index
                ]

                if (
                    volume_index
                    is not None
                    and volume_index
                    < len(row)
                ):

                    volume_value = row[
                        volume_index
                    ]

                else:

                    volume_value = 0

            # ------------------------------------------------
            # TPEx 官方 aaData 固定報表結構
            #
            # 0 公司代號
            # 1 公司名稱
            # 2 收盤價
            # 3 漲跌
            # 4 開盤價
            # 5 最高價
            # 6 最低價
            # 7 成交股數
            # ------------------------------------------------

            else:

                if len(row) < 8:

                    continue

                code_value = row[0]

                close_value = row[2]

                open_value = row[4]

                high_value = row[5]

                low_value = row[6]

                volume_value = row[7]

            stock_code = extract_code(
                code_value
            )

            if not stock_code:

                continue

            normalized = (
                normalize_price_row(
                    target_date,
                    open_value,
                    high_value,
                    low_value,
                    close_value,
                    volume_value,
                )
            )

            if normalized:

                result[
                    stock_code
                ] = normalized

        except Exception:

            continue

    if result:

        return (
            result,
            None,
        )

    return (
        {},
        "無有效官方 TPEx OHLCV",
    )


# ============================================================
# YAHOO
# ============================================================

def fetch_yahoo(
    symbol: str,
) -> Tuple[
    List[Dict[str, Any]],
    Optional[str],
]:

    period1 = int(
        (
            datetime.now(
                timezone.utc
            )
            - timedelta(
                days=INITIAL_HISTORY_DAYS
                + 30
            )
        ).timestamp()
    )

    period2 = int(
        datetime.now(
            timezone.utc
        ).timestamp()
    )

    params = {
        "period1": period1,
        "period2": period2,
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "false",
    }

    try:

        payload = request_json(
            YAHOO_URL.format(
                symbol=symbol
            ),
            params,
        )

        chart = payload.get(
            "chart",
            {},
        )

        if chart.get(
            "error"
        ):

            raise RuntimeError(
                str(
                    chart[
                        "error"
                    ]
                )
            )

        results = chart.get(
            "result"
        ) or []

        if not results:

            raise RuntimeError(
                "Yahoo result 為空"
            )

        result = results[0]

        timestamps = (
            result.get(
                "timestamp"
            )
            or []
        )

        quotes = (
            result.get(
                "indicators",
                {},
            )
            .get(
                "quote",
                [],
            )
        )

        if not quotes:

            raise RuntimeError(
                "Yahoo quote 為空"
            )

        quote = quotes[0]

        opens = quote.get(
            "open",
            [],
        )

        highs = quote.get(
            "high",
            [],
        )

        lows = quote.get(
            "low",
            [],
        )

        closes = quote.get(
            "close",
            [],
        )

        volumes = quote.get(
            "volume",
            [],
        )

        rows = []

        for i, timestamp in enumerate(
            timestamps
        ):

            try:

                row_date = (
                    datetime.fromtimestamp(
                        int(timestamp),
                        timezone.utc,
                    )
                    .date()
                    .isoformat()
                )

            except Exception:

                continue

            normalized = (
                normalize_price_row(
                    row_date,
                    opens[i]
                    if i < len(opens)
                    else None,
                    highs[i]
                    if i < len(highs)
                    else None,
                    lows[i]
                    if i < len(lows)
                    else None,
                    closes[i]
                    if i < len(closes)
                    else None,
                    volumes[i]
                    if i < len(volumes)
                    else 0,
                )
            )

            if normalized:

                rows.append(
                    normalized
                )

        rows.sort(
            key=lambda x: x["date"]
        )

        return (
            rows,
            None,
        )

    except Exception as exc:

        return (
            [],
            str(exc),
        )


# ============================================================
# DATE RANGE
# ============================================================

def candidate_dates(
    days: int,
) -> List[str]:

    end = date.today()

    start = (
        end
        - timedelta(
            days=days
        )
    )

    result = []

    current = start

    while current <= end:

        if current.weekday() < 5:

            result.append(
                current.isoformat()
            )

        current += timedelta(
            days=1
        )

    return result


# ============================================================
# EMPTY RECORDS
# ============================================================

def build_empty_records(
    universe: List[Dict[str, str]],
) -> Dict[
    str,
    Dict[str, Any],
]:

    return {
        item["symbol"]: {
            **item,
            "prices": {},
            "source": None,
        }
        for item in universe
    }


# ============================================================
# APPEND MARKET DATA
# ============================================================

def append_market_data(
    records: Dict[
        str,
        Dict[str, Any]
    ],
    rows: Dict[
        str,
        Dict[str, Any]
    ],
    universe_map: Dict[
        Tuple[str, str],
        Dict[str, str]
    ],
    market: str,
    source: str,
) -> int:

    matched = 0

    for stock_code, row in rows.items():

        item = universe_map.get(
            (
                market,
                stock_code,
            )
        )

        if item is None:

            continue

        symbol = item[
            "symbol"
        ]

        records[
            symbol
        ][
            "prices"
        ][
            row["date"]
        ] = row

        records[
            symbol
        ][
            "source"
        ] = source

        matched += 1

    return matched


# ============================================================
# FINALIZE OFFICIAL RECORDS
# ============================================================

def finalize_records(
    records: Dict[
        str,
        Dict[str, Any]
    ],
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Dict[str, Dict[str, Any]],
]:

    result = {}

    failures = {}

    for symbol, record in records.items():

        rows = sorted(
            record[
                "prices"
            ].values(),
            key=lambda x: x[
                "date"
            ],
        )

        rows = rows[
            -MAX_HISTORY_ROWS:
        ]

        if len(rows) >= (
            ABSOLUTE_MIN_HISTORY_ROWS
        ):

            result[symbol] = {
                key: record[key]
                for key in (
                    "symbol",
                    "code",
                    "market",
                    "type",
                    "name",
                )
            }

            result[symbol].update(
                {
                    "source":
                        record[
                            "source"
                        ]
                        or "unknown",

                    "history_rows":
                        len(rows),

                    "history_status":
                        (
                            "complete"
                            if len(rows)
                            >= INITIAL_HISTORY_DAYS
                            else
                            "short_history"
                        ),

                    "latest_date":
                        rows[-1][
                            "date"
                        ],

                    "prices":
                        rows,
                }
            )

        else:

            failures[symbol] = {
                "reason":
                    "official_history_insufficient",

                "rows":
                    len(rows),
            }

    return (
        result,
        failures,
    )


# ============================================================
# INITIAL OFFICIAL HISTORY
# ============================================================

def fetch_initial_history(
    universe: List[Dict[str, str]],
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Dict[str, Dict[str, Any]],
]:

    section(
        "V9.0 官方全市場批次歷史初始化"
    )

    log(
        f"目標歷史："
        f"{INITIAL_HISTORY_DAYS} 個交易日"
    )

    log(
        "TWSE / TPEx："
        "每交易日整市場批次"
    )

    records = build_empty_records(
        universe
    )

    universe_map = {
        (
            item["market"],
            item["code"],
        ): item
        for item in universe
    }

    dates = candidate_dates(
        INITIAL_LOOKBACK_CALENDAR_DAYS
    )

    for index, target_date in enumerate(
        dates,
        start=1,
    ):

        complete = all(
            len(
                record[
                    "prices"
                ]
            )
            >= INITIAL_HISTORY_DAYS
            for record
            in records.values()
        )

        if complete:

            break

        log(
            f"[BATCH "
            f"{index}/"
            f"{len(dates)}] "
            f"{target_date}"
        )

        # ====================================================
        # TWSE
        # ====================================================

        try:

            twse_rows, twse_error = (
                fetch_twse_day(
                    target_date
                )
            )

            if twse_rows:

                matched = (
                    append_market_data(
                        records,
                        twse_rows,
                        universe_map,
                        "TW",
                        "official_twse",
                    )
                )

                log(
                    f"  ✓ TWSE："
                    f"{len(twse_rows)} 檔，"
                    f"匹配 {matched} 檔"
                )

            else:

                log(
                    f"  ↳ TWSE："
                    f"{twse_error or '無資料'}"
                )

        except Exception as exc:

            log(
                f"  ⚠️ TWSE："
                f"{exc}"
            )

        time.sleep(
            REQUEST_DELAY
        )

        # ====================================================
        # TPEx
        # ====================================================

        try:

            tpex_rows, tpex_error = (
                fetch_tpex_day(
                    target_date
                )
            )

            if tpex_rows:

                matched = (
                    append_market_data(
                        records,
                        tpex_rows,
                        universe_map,
                        "TWO",
                        "official_tpex",
                    )
                )

                log(
                    f"  ✓ TPEx："
                    f"{len(tpex_rows)} 檔，"
                    f"匹配 {matched} 檔"
                )

            else:

                log(
                    f"  ↳ TPEx："
                    f"{tpex_error or '無資料'}"
                )

        except Exception as exc:

            log(
                f"  ⚠️ TPEx："
                f"{exc}"
            )

        time.sleep(
            REQUEST_DELAY
        )

    # ========================================================
    # 官方資料初步結果
    # ========================================================

    result, failures = (
        finalize_records(
            records
        )
    )

    # ========================================================
    # FINAL FALLBACK
    # ========================================================

    if failures:

        section(
            "官方批次不足 → Yahoo 最後 fallback"
        )

        log(
            f"需要 fallback："
            f"{len(failures)} 檔"
        )

        for index, symbol in enumerate(
            list(failures.keys()),
            start=1,
        ):

            log(
                f"[FALLBACK "
                f"{index}/"
                f"{len(failures)}] "
                f"{symbol}"
            )

            rows, error = fetch_yahoo(
                symbol
            )

            rows = rows[
                -MAX_HISTORY_ROWS:
            ]

            if len(rows) >= (
                ABSOLUTE_MIN_HISTORY_ROWS
            ):

                item = next(
                    item
                    for item in universe
                    if item["symbol"]
                    == symbol
                )

                result[symbol] = {
                    **item,

                    "source":
                        "Yahoo fallback",

                    "history_rows":
                        len(rows),

                    "history_status":
                        (
                            "complete"
                            if len(rows)
                            >= INITIAL_HISTORY_DAYS
                            else
                            "short_history"
                        ),

                    "latest_date":
                        rows[-1][
                            "date"
                        ],

                    "prices":
                        rows,
                }

                failures.pop(
                    symbol,
                    None,
                )

                log(
                    f"  ✓ Yahoo："
                    f"{len(rows)} 筆"
                )

            else:

                failures[
                    symbol
                ] = {
                    "reason":
                        "official_history_insufficient_and_yahoo_failed",

                    "rows":
                        len(rows),

                    "error":
                        error,
                }

            time.sleep(
                REQUEST_DELAY
            )

    return (
        result,
        failures,
    )


# ============================================================
# LOAD EXISTING V9
# ============================================================

def load_existing_prices() -> Optional[
    Dict[str, Dict[str, Any]]
]:

    if not OUTPUT_DIR.exists():

        return None

    manifest_path = (
        OUTPUT_DIR
        / "manifest.json"
    )

    if not manifest_path.exists():

        return None

    try:

        manifest = load_json(
            manifest_path
        )

        if manifest.get(
            "schema_version"
        ) != SCHEMA_VERSION:

            return None

        result = {}

        for filename in (
            manifest.get(
                "files"
            )
            or []
        ):

            path = (
                OUTPUT_DIR
                / filename
            )

            if not path.exists():

                return None

            shard = load_json(
                path
            )

            stocks = shard.get(
                "stocks"
            )

            if not isinstance(
                stocks,
                dict,
            ):

                return None

            for symbol, rows in (
                stocks.items()
            ):

                if not isinstance(
                    rows,
                    list,
                ):

                    return None

                normalized_rows = []

                for row in rows:

                    if not isinstance(
                        row,
                        dict,
                    ):

                        continue

                    normalized = (
                        normalize_price_row(
                            row.get(
                                "date"
                            ),
                            row.get(
                                "open"
                            ),
                            row.get(
                                "high"
                            ),
                            row.get(
                                "low"
                            ),
                            row.get(
                                "close"
                            ),
                            row.get(
                                "volume"
                            ),
                        )
                    )

                    if normalized:

                        normalized_rows.append(
                            normalized
                        )

                if normalized_rows:

                    result[symbol] = {
                        "symbol":
                            symbol,

                        "prices":
                            sorted(
                                normalized_rows,
                                key=lambda x:
                                x["date"],
                            )[
                                -MAX_HISTORY_ROWS:
                            ],
                    }

        if not result:

            return None

        return result

    except Exception:

        return None


# ============================================================
# DAILY INCREMENTAL
# ============================================================

def update_existing_with_latest(
    existing: Dict[
        str,
        Dict[str, Any]
    ],
    universe: List[
        Dict[str, str]
    ],
) -> Tuple[
    Optional[
        Dict[str, Dict[str, Any]]
    ],
    Dict[str, Dict[str, Any]],
]:

    # --------------------------------------------------------
    # 任何 Universe 新增商品，
    # 或既有歷史不足，
    # 直接回到官方全市場初始化。
    # --------------------------------------------------------

    for item in universe:

        symbol = item[
            "symbol"
        ]

        if symbol not in existing:

            return (
                None,
                {},
            )

        if len(
            existing[symbol].get(
                "prices",
                [],
            )
        ) < ABSOLUTE_MIN_HISTORY_ROWS:

            return (
                None,
                {},
            )

    # ========================================================
    # TWSE 最新交易日
    # ========================================================

    twse_rows = {}

    try:

        twse_rows, _ = (
            fetch_twse_day(
                date.today().isoformat()
            )
        )

    except Exception:

        twse_rows = {}

    time.sleep(
        REQUEST_DELAY
    )

    # ========================================================
    # TPEx 最新交易日
    # ========================================================

    tpex_rows = {}

    for offset in range(
        0,
        7,
    ):

        target = (
            date.today()
            - timedelta(
                days=offset
            )
        )

        if target.weekday() >= 5:

            continue

        try:

            tpex_rows, _ = (
                fetch_tpex_day(
                    target.isoformat()
                )
            )

            if tpex_rows:

                break

        except Exception:

            continue

    latest_dates = [
        row["date"]
        for row
        in list(
            twse_rows.values()
        )
        + list(
            tpex_rows.values()
        )
    ]

    if not latest_dates:

        return (
            None,
            {},
        )

    latest_date = max(
        latest_dates
    )

    result = {}

    failures = {}

    for item in universe:

        symbol = item[
            "symbol"
        ]

        previous_rows = existing[
            symbol
        ].get(
            "prices",
            [],
        )

        row_map = {
            row["date"]: row
            for row
            in previous_rows
            if isinstance(
                row,
                dict,
            )
        }

        market_rows = (
            twse_rows
            if item["market"] == "TW"
            else tpex_rows
        )

        official_row = (
            market_rows.get(
                item["code"]
            )
        )

        source = "existing"

        if (
            official_row
            and official_row["date"]
            == latest_date
        ):

            row_map[
                latest_date
            ] = official_row

            source = (
                "official_twse"
                if item["market"]
                == "TW"
                else "official_tpex"
            )

        else:

            yahoo_rows, _ = (
                fetch_yahoo(
                    symbol
                )
            )

            yahoo_row = next(
                (
                    row
                    for row
                    in yahoo_rows
                    if row["date"]
                    == latest_date
                ),
                None,
            )

            if yahoo_row:

                row_map[
                    latest_date
                ] = yahoo_row

                source = (
                    "Yahoo fallback"
                )

        rows = sorted(
            row_map.values(),
            key=lambda x:
            x["date"],
        )[
            -MAX_HISTORY_ROWS:
        ]

        if len(rows) < (
            ABSOLUTE_MIN_HISTORY_ROWS
        ):

            failures[
                symbol
            ] = {
                "reason":
                    "incremental_history_insufficient",

                "rows":
                    len(rows),
            }

            continue

        result[symbol] = {
            **item,

            "source":
                source,

            "history_rows":
                len(rows),

            "history_status":
                (
                    "complete"
                    if len(rows)
                    >= INITIAL_HISTORY_DAYS
                    else
                    "short_history"
                ),

            "latest_date":
                rows[-1]["date"],

            "prices":
                rows,
        }

    if failures:

        return (
            None,
            failures,
        )

    return (
        result,
        {},
    )


# ============================================================
# RESULT VALIDATION
# ============================================================

def validate_results(
    results: Dict[
        str,
        Dict[str, Any]
    ],
    universe: List[
        Dict[str, str]
    ],
) -> Tuple[
    List[str],
    List[
        Tuple[str, str]
    ],
]:

    expected = {
        item["symbol"]
        for item in universe
    }

    actual = set(
        results.keys()
    )

    missing = sorted(
        expected - actual
    )

    issues = []

    for symbol, record in (
        results.items()
    ):

        rows = record.get(
            "prices"
        )

        if (
            not isinstance(
                rows,
                list,
            )
            or len(rows)
            < ABSOLUTE_MIN_HISTORY_ROWS
        ):

            issues.append(
                (
                    symbol,
                    "history_insufficient",
                )
            )

            continue

        previous = ""

        for row in rows:

            normalized = (
                normalize_price_row(
                    row.get("date"),
                    row.get("open"),
                    row.get("high"),
                    row.get("low"),
                    row.get("close"),
                    row.get("volume"),
                )
            )

            if normalized is None:

                issues.append(
                    (
                        symbol,
                        "invalid_ohlcv",
                    )
                )

                break

            if (
                previous
                and normalized["date"]
                <= previous
            ):

                issues.append(
                    (
                        symbol,
                        "date_not_increasing",
                    )
                )

                break

            previous = normalized[
                "date"
            ]

        if not record.get(
            "source"
        ):

            issues.append(
                (
                    symbol,
                    "missing_source",
                )
            )

    return (
        missing,
        issues,
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

            stocks[
                symbol
            ] = results[
                symbol
            ][
                "prices"
            ]

        shards.append(
            {
                "stocks":
                    stocks
            }
        )

    return shards


# ============================================================
# MANIFEST
# ============================================================

def build_manifest(
    results: Dict[
        str,
        Dict[str, Any]
    ],
    files: List[str],
    stock_count: int,
    etf_count: int,
    diagnostics: Dict[str, Any],
) -> Dict[str, Any]:

    source_counts = {}
    type_counts = {}

    complete_count = 0
    short_count = 0

    for record in results.values():

        source = record[
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

        record_type = record[
            "type"
        ]

        type_counts[
            record_type
        ] = (
            type_counts.get(
                record_type,
                0,
            )
            + 1
        )

        if (
            record[
                "history_status"
            ]
            == "complete"
        ):

            complete_count += 1

        else:

            short_count += 1

    total = (
        stock_count
        + etf_count
    )

    success_rate = (
        len(results)
        / total
        if total
        else 0
    )

    return {
        "schema_version":
            SCHEMA_VERSION,

        "generator_version":
            VERSION,

        "generated_at":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "universe_stock_count":
            stock_count,

        "universe_etf_count":
            etf_count,

        "price_stock_count":
            type_counts.get(
                "STOCK",
                0,
            ),

        "price_etf_count":
            type_counts.get(
                "ETF",
                0,
            ),

        "price_total_count":
            len(results),

        "complete_history_count":
            complete_count,

        "short_history_count":
            short_count,

        "target_history_rows":
            INITIAL_HISTORY_DAYS,

        "absolute_min_history_rows":
            ABSOLUTE_MIN_HISTORY_ROWS,

        "max_history_rows":
            MAX_HISTORY_ROWS,

        "success_rate":
            round(
                success_rate,
                6,
            ),

        "diagnostic_missing_count":
            len(
                diagnostics.get(
                    "missing",
                    {},
                )
            ),

        "diagnostic_issue_count":
            len(
                diagnostics.get(
                    "issues",
                    [],
                )
            ),

        "sources":
            source_counts,

        "types":
            type_counts,

        "latest_date":
            max(
                (
                    record[
                        "latest_date"
                    ]
                    for record
                    in results.values()
                ),
                default=None,
            ),

        "files":
            files,
    }


# ============================================================
# WRITE TEMP DIRECTORY
# ============================================================

def write_price_directory(
    temp_dir: Path,
    results: Dict[
        str,
        Dict[str, Any]
    ],
    stock_count: int,
    etf_count: int,
    diagnostics: Dict[str, Any],
) -> None:

    temp_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    shards = build_shards(
        results
    )

    symbols = sorted(
        results.keys()
    )

    files = []

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

        if (
            path.stat().st_size
            > MAX_FILE_SIZE_BYTES
        ):

            raise RuntimeError(
                f"{filename} 超過 80 MB"
            )

        expected = symbols[
            (
                index - 1
            )
            * STOCKS_PER_FILE:
            index
            * STOCKS_PER_FILE
        ]

        if set(
            shard["stocks"].keys()
        ) != set(expected):

            raise RuntimeError(
                f"{filename} 股票集合錯誤"
            )

        for symbol, rows in (
            shard[
                "stocks"
            ].items()
        ):

            if len(rows) < (
                ABSOLUTE_MIN_HISTORY_ROWS
            ):

                raise RuntimeError(
                    f"{symbol} 歷史不足"
                )

        files.append(
            filename
        )

    manifest = build_manifest(
        results,
        files,
        stock_count,
        etf_count,
        diagnostics,
    )

    save_json(
        temp_dir
        / "manifest.json",
        manifest,
    )

    save_json(
        temp_dir
        / "diagnostics.json",
        diagnostics,
    )

    # --------------------------------------------------------
    # 二次讀回驗證
    # --------------------------------------------------------

    verified_manifest = load_json(
        temp_dir
        / "manifest.json"
    )

    if (
        verified_manifest[
            "files"
        ]
        != files
    ):

        raise RuntimeError(
            "manifest files 驗證失敗"
        )

    if (
        verified_manifest[
            "price_total_count"
        ]
        != len(results)
    ):

        raise RuntimeError(
            "manifest price_total_count "
            "驗證失敗"
        )

    for filename in files:

        data = load_json(
            temp_dir
            / filename
        )

        if not isinstance(
            data.get("stocks"),
            dict,
        ):

            raise RuntimeError(
                f"{filename} schema 錯誤"
            )

    # --------------------------------------------------------
    # diagnostics 同步到 Data 根目錄
    # --------------------------------------------------------

    save_json(
        DIAGNOSTIC_FILE,
        diagnostics,
    )


# ============================================================
# ATOMIC REPLACE
# ============================================================

def atomic_replace(
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

    if OUTPUT_DIR.exists():

        OUTPUT_DIR.rename(
            backup_dir
        )

    try:

        temp_dir.rename(
            OUTPUT_DIR
        )

    except Exception:

        if OUTPUT_DIR.exists():

            shutil.rmtree(
                OUTPUT_DIR
            )

        if backup_dir.exists():

            backup_dir.rename(
                OUTPUT_DIR
            )

        raise

    if backup_dir.exists():

        shutil.rmtree(
            backup_dir
        )


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    started = time.time()

    section(
        f"fetch_prices.py {VERSION}"
    )

    # ========================================================
    # UNIVERSE
    # ========================================================

    stocks, etfs, universe = (
        load_universe()
    )

    universe_total = len(
        universe
    )

    # ========================================================
    # EXISTING DATA
    # ========================================================

    existing = (
        load_existing_prices()
    )

    # ========================================================
    # INITIAL / INCREMENTAL
    # ========================================================

    if existing:

        results, incremental_failures = (
            update_existing_with_latest(
                existing,
                universe,
            )
        )

        if results is None:

            log("")
            log(
                "⚠️ 既有資料需要重新建立"
            )

            results, failures = (
                fetch_initial_history(
                    universe
                )
            )

        else:

            failures = (
                incremental_failures
            )

    else:

        log(
            "既有 V9.0 價格不存在，"
            "執行官方歷史初始化"
        )

        results, failures = (
            fetch_initial_history(
                universe
            )
        )

    # ========================================================
    # VALIDATION
    # ========================================================

    missing, issues = (
        validate_results(
            results,
            universe,
        )
    )

    # ========================================================
    # DIAGNOSTICS
    # ========================================================

    diagnostics = {
        "generated_at":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "version":
            VERSION,

        "missing": {
            symbol:
                failures.get(
                    symbol,
                    {
                        "reason":
                            "not_in_result"
                    },
                )
            for symbol
            in missing
        },

        "issues": [
            {
                "symbol":
                    symbol,

                "reason":
                    reason,
            }

            for symbol, reason
            in issues
        ],

        "source_summary":
            {},
    }

    for record in (
        results.values()
    ):

        source = record[
            "source"
        ]

        diagnostics[
            "source_summary"
        ][source] = (
            diagnostics[
                "source_summary"
            ].get(
                source,
                0,
            )
            + 1
        )

    # ========================================================
    # SUCCESS RATE
    # ========================================================

    success_count = len(
        results
    )

    missing_count = (
        universe_total
        - success_count
    )

    success_rate = (
        success_count
        / universe_total
        if universe_total
        else 0
    )

    # ========================================================
    # RESULT
    # ========================================================

    section(
        "價格資料結果"
    )

    log(
        f"Universe TOTAL："
        f"{universe_total}"
    )

    log(
        f"成功："
        f"{success_count}"
    )

    log(
        f"缺失："
        f"{missing_count}"
    )

    log(
        f"驗證異常："
        f"{len(issues)}"
    )

    log(
        f"成功率："
        f"{success_rate:.2%}"
    )

    for source, count in sorted(
        diagnostics[
            "source_summary"
        ].items()
    ):

        log(
            f"來源 {source}："
            f"{count}"
        )

    # ========================================================
    # MISSING DIAGNOSTICS
    # ========================================================

    if missing:

        log("")

        log(
            "⚠️ 缺失商品："
        )

        log(
            "這些商品只進入 diagnostics，"
            "不直接讓 Action FAIL。"
        )

        for symbol in missing:

            log(
                f"  - {symbol}："
                f"{diagnostics['missing'][symbol]}"
            )

    # ========================================================
    # ISSUE DIAGNOSTICS
    # ========================================================

    if issues:

        log("")

        log(
            "⚠️ 價格資料異常："
        )

        for symbol, reason in issues:

            log(
                f"  - {symbol}："
                f"{reason}"
            )

    # ========================================================
    # SUCCESS RATE GATE
    # ========================================================

    if success_rate < (
        MIN_SUCCESS_RATE
    ):

        log("")

        log(
            f"❌ 價格資料成功率 "
            f"{success_rate:.2%} "
            f"< "
            f"{MIN_SUCCESS_RATE:.0%}"
        )

        log(
            "Action FAIL"
        )

        save_json(
            DIAGNOSTIC_FILE,
            diagnostics,
        )

        return 1

    log("")

    log(
        f"✓ 價格資料成功率 "
        f"{success_rate:.2%} "
        f">= "
        f"{MIN_SUCCESS_RATE:.0%}"
    )

    log(
        "✓ Action PASS"
    )

    # ========================================================
    # EMPTY RESULT SAFETY
    # ========================================================

    if not results:

        save_json(
            DIAGNOSTIC_FILE,
            diagnostics,
        )

        return 1

    # ========================================================
    # TEMPORARY BUILD
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
            len(stocks),
            len(etfs),
            diagnostics,
        )

        # ====================================================
        # ATOMIC REPLACE
        # ====================================================

        section(
            "Atomic replace Data/prices"
        )

        atomic_replace(
            temp_dir
        )

        log(
            "✓ Data/prices/ "
            "已成功更新"
        )

    except Exception as exc:

        log("")

        log(
            f"❌ 價格資料建置失敗："
            f"{exc}"
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
        f"{len(stocks)}"
    )

    log(
        f"Universe ETF："
        f"{len(etfs)}"
    )

    log(
        f"Universe TOTAL："
        f"{universe_total}"
    )

    log(
        f"Price 成功："
        f"{success_count}"
    )

    log(
        f"Price 缺失："
        f"{missing_count}"
    )

    log(
        f"成功率："
        f"{success_rate:.2%}"
    )

    log(
        f"執行時間："
        f"{elapsed:.1f} 秒"
    )

    log(
        f"✓ fetch_prices.py "
        f"{VERSION} 完成"
    )

    return 0


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
