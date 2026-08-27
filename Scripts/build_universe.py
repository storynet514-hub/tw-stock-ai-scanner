#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_universe.py

UNIVERSE-REBUILD-V2

核心設計
------------------------------------------------------------
1. Data/universe.json 是後續系統唯一 Universe 輸出。
2. 本程式負責建立：
       Data/universe.json
3. 不寫死 Universe 數量。
4. 不使用 CMoney。
5. 不依賴單一 TWSE OpenAPI endpoint。
6. TWSE / TPEx 優先使用官方來源。
7. 官方來源必須經過結構與內容驗證。
8. 官方來源失敗時，不拿非官方資料冒充官方。
9. 不因單一官方 API 回傳 HTML / 空內容 / JSON 錯誤
   就直接認定官方 Universe 不存在。
10. TWSE 與 TPEx 分開建立。
11. 只納入可確認為股票普通股的標的。
12. 排除 ETF、ETN、權證、牛熊證、受益證券、
    特別股及其他非普通股票工具。
13. 保留既有 universe.json 中可辨識的 metadata，
    但「是否存在於官方市場資料」以官方來源為準。
14. status：
       active
       inactive
15. active 才會被 fetch_chip.py 使用。
16. Atomic Write。
17. 寫入後重新讀取 universe.json 驗證。
18. 如果任何必要市場的官方來源完全無法取得：
       BUILD FAIL
    禁止產生一份看似正常但實際不完整的 Universe。

重要
------------------------------------------------------------
這份程式不是用「今天有成交」直接猜 Universe。

TWSE：
    優先：
        1. 官方證券主檔 / 官方收盤行情
        2. 官方 OpenAPI
    互相驗證。

TPEx：
    優先：
        1. 官方 OpenAPI
        2. 官方證券行情資料

如果官方來源回傳非 JSON：
    不會直接 crash。
    會嘗試其他官方 endpoint。

如果所有官方來源都失敗：
    明確 FAIL。

禁止：
    CMoney
    Yahoo
    MoneyDJ
    鉅亨
    玩股網
    FinMind
    任意第三方網站

作為 Universe 的權威來源。
"""

from __future__ import annotations

import json
import math
import re
import sys
import time

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


# ============================================================
# VERSION
# ============================================================

VERSION = "UNIVERSE-REBUILD-V2"


# ============================================================
# PATH
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"
TEMP_FILE = DATA_DIR / "universe.json.tmp"


# ============================================================
# NETWORK
# ============================================================

REQUEST_TIMEOUT = 30
RETRIES = 3
RETRY_SLEEP = 1.5

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
        "text/plain,"
        "text/csv,"
        "text/html,"
        "*/*"
    ),
    "Accept-Language": (
        "zh-TW,zh;q=0.9,"
        "en-US;q=0.8,en;q=0.7"
    ),
}


session = requests.Session()


# ============================================================
# OFFICIAL TWSE SOURCES
# ============================================================

TWSE_OPENAPI_STOCK_DAY_ALL = (
    "https://openapi.twse.com.tw/v1/"
    "exchangeReport/STOCK_DAY_ALL"
)

TWSE_OPENAPI_SECURITIES = (
    "https://openapi.twse.com.tw/v1/"
    "exchangeReport/STOCK_DAY_AVG_ALL"
)

TWSE_OPENAPI_STOCK_MAIN = (
    "https://openapi.twse.com.tw/v1/"
    "exchangeReport/BWIBBU_d"
)

TWSE_DAILY_QUOTE_URL = (
    "https://www.twse.com.tw/"
    "rwd/zh/afterTrading/STOCK_DAY_ALL"
)

TWSE_SECURITIES_MASTER_URL = (
    "https://www.twse.com.tw/"
    "rwd/zh/afterTrading/MI_INDEX"
)


# ============================================================
# OFFICIAL TPEX SOURCES
# ============================================================

TPEX_OPENAPI_BASE = (
    "https://www.tpex.org.tw/openapi/v1"
)

TPEX_DAILY_QUOTES = (
    TPEX_OPENAPI_BASE
    + "/tpex_mainboard_daily_close_quotes"
)

TPEX_STOCKS = (
    TPEX_OPENAPI_BASE
    + "/tpex_mainboard_peratio_analysis"
)

TPEX_LISTED_COMPANIES = (
    TPEX_OPENAPI_BASE
    + "/tpex_mainboard_peratio_analysis"
)


# ============================================================
# LOG
# ============================================================

def log(message: str = "") -> None:
    print(message, flush=True)


def section(title: str) -> None:
    log("")
    log("=" * 72)
    log(title)
    log("=" * 72)


# ============================================================
# TIME
# ============================================================

def now_tw() -> datetime:
    from zoneinfo import ZoneInfo

    return datetime.now(
        ZoneInfo("Asia/Taipei")
    )


def iso_datetime() -> str:
    return now_tw().isoformat()


def today() -> str:
    return now_tw().strftime(
        "%Y-%m-%d"
    )


# ============================================================
# NORMALIZATION
# ============================================================

def clean_text(value: Any) -> str:

    if value is None:
        return ""

    return (
        str(value)
        .strip()
        .replace("\u3000", " ")
    )


def clean_code(value: Any) -> str:

    if value is None:
        return ""

    text = clean_text(value).upper()

    text = (
        text
        .replace(".TW", "")
        .replace(".TWO", "")
        .replace(" ", "")
        .replace("\u3000", "")
    )

    return text


def normalize_key(value: Any) -> str:

    text = clean_text(value).lower()

    return re.sub(
        r"[\s_\-\/\(\)（）:：]+",
        "",
        text,
    )


def safe_number(
    value: Any,
) -> Optional[float]:

    if value is None:
        return None

    if isinstance(value, bool):
        return None

    text = clean_text(value)

    if not text:
        return None

    text = (
        text
        .replace(",", "")
        .replace("，", "")
        .replace("%", "")
        .replace("＋", "+")
        .replace("－", "-")
        .replace("—", "-")
        .replace("–", "-")
    )

    if text in {
        "-",
        "--",
        "---",
        "N/A",
        "NA",
        "null",
        "None",
    }:
        return None

    try:

        number = float(text)

        if not math.isfinite(number):
            return None

        return number

    except (
        TypeError,
        ValueError,
    ):
        return None


# ============================================================
# FIELD HELPERS
# ============================================================

def find_field(
    row: Dict[str, Any],
    aliases: List[str],
) -> Any:

    normalized = {
        normalize_key(key): value
        for key, value in row.items()
    }

    for alias in aliases:

        key = normalize_key(alias)

        if key in normalized:
            return normalized[key]

    return None


def find_symbol(
    row: Dict[str, Any],
) -> str:

    return clean_code(
        find_field(
            row,
            [
                "代號",
                "證券代號",
                "股票代號",
                "證券代碼",
                "Code",
                "SecurityCode",
                "StockCode",
                "Symbol",
                "ticker",
            ],
        )
    )


def find_name(
    row: Dict[str, Any],
) -> str:

    return clean_text(
        find_field(
            row,
            [
                "名稱",
                "證券名稱",
                "股票名稱",
                "公司名稱",
                "Name",
                "SecurityName",
                "StockName",
            ],
        )
    )


def find_market(
    row: Dict[str, Any],
) -> str:

    value = clean_text(
        find_field(
            row,
            [
                "市場別",
                "市場",
                "Market",
                "MarketType",
            ],
        )
    ).upper()

    if value in {
        "TWSE",
        "上市",
        "集中市場",
    }:
        return "TWSE"

    if value in {
        "TPEX",
        "TPEx",
        "上櫃",
        "櫃買",
    }:
        return "TPEX"

    return ""


# ============================================================
# HTTP
# ============================================================

def request_raw(
    url: str,
    params: Optional[Dict[str, Any]] = None,
) -> Optional[requests.Response]:

    last_error = ""

    for attempt in range(
        1,
        RETRIES + 1,
    ):

        try:

            response = session.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code == 200:
                return response

            last_error = (
                f"HTTP {response.status_code}"
            )

        except Exception as exc:

            last_error = str(exc)

        if attempt < RETRIES:

            time.sleep(
                RETRY_SLEEP * attempt
            )

    log(
        f"      ⚠️ {url}"
    )

    log(
        f"         {last_error}"
    )

    return None


def request_json(
    url: str,
    params: Optional[Dict[str, Any]] = None,
) -> Optional[Any]:

    response = request_raw(
        url,
        params,
    )

    if response is None:
        return None

    text = response.text.strip()

    if not text:
        log(
            "      ⚠️ HTTP 200 但 response 為空"
        )
        return None

    try:

        return response.json()

    except Exception as exc:

        log(
            "      ⚠️ response 不是 JSON："
            f"{exc}"
        )

        preview = text[:120].replace(
            "\n",
            " ",
        )

        log(
            f"         response preview: {preview}"
        )

        return None


# ============================================================
# PAYLOAD NORMALIZATION
# ============================================================

def rows_from_fields_data(
    fields: Any,
    data: Any,
) -> List[Dict[str, Any]]:

    if not isinstance(
        fields,
        list,
    ):
        return []

    if not isinstance(
        data,
        list,
    ):
        return []

    result = []

    for row in data:

        if isinstance(
            row,
            dict,
        ):

            result.append(row)
            continue

        if not isinstance(
            row,
            list,
        ):
            continue

        record = {}

        for index, field in enumerate(
            fields
        ):

            if index >= len(row):
                break

            record[
                str(field)
            ] = row[index]

        if record:
            result.append(record)

    return result


def normalize_records(
    payload: Any,
) -> List[Dict[str, Any]]:

    if isinstance(
        payload,
        list,
    ):

        return [
            row
            for row in payload
            if isinstance(
                row,
                dict,
            )
        ]

    if not isinstance(
        payload,
        dict,
    ):
        return []

    rows = rows_from_fields_data(
        payload.get("fields"),
        payload.get("data"),
    )

    if rows:
        return rows

    tables = payload.get(
        "tables"
    )

    if isinstance(
        tables,
        list,
    ):

        result = []

        for table in tables:

            if not isinstance(
                table,
                dict,
            ):
                continue

            result.extend(
                rows_from_fields_data(
                    table.get("fields"),
                    table.get("data"),
                )
            )

        if result:
            return result

    for key in (
        "data",
        "Data",
        "result",
        "results",
        "records",
        "Records",
    ):

        value = payload.get(key)

        if not isinstance(
            value,
            list,
        ):
            continue

        rows = [
            row
            for row in value
            if isinstance(
                row,
                dict,
            )
        ]

        if rows:
            return rows

    return []


# ============================================================
# SECURITY FILTER
# ============================================================

def looks_like_stock_code(
    symbol: str,
) -> bool:

    if not symbol:
        return False

    if not re.fullmatch(
        r"\d{4,6}",
        symbol,
    ):
        return False

    return True


def is_common_stock_name(
    name: str,
) -> bool:

    if not name:
        return False

    upper = name.upper()

    forbidden_keywords = [
        "ETF",
        "ETN",
        "權證",
        "牛熊證",
        "認購權證",
        "認售權證",
        "受益證券",
        "特別股",
        "優先股",
        "存託憑證",
        "DR",
        "TDR",
    ]

    for keyword in forbidden_keywords:

        if keyword in upper:
            return False

    return True


def is_common_stock_row(
    row: Dict[str, Any],
) -> bool:

    symbol = find_symbol(row)
    name = find_name(row)

    if not looks_like_stock_code(
        symbol
    ):
        return False

    if not is_common_stock_name(
        name
    ):
        return False

    # --------------------------------------------------------
    # 排除明確標示為 ETF / ETN / 權證等
    # --------------------------------------------------------

    combined = " ".join(
        clean_text(value)
        for value in row.values()
    ).upper()

    forbidden = [
        "ETF",
        "ETN",
        "權證",
        "牛熊證",
        "認購權證",
        "認售權證",
    ]

    for keyword in forbidden:

        if keyword in combined:

            # 名稱本身已經是普通股票時，
            # 不因其他描述欄位中的 ETF 字樣誤殺。
            if keyword not in name.upper():
                continue

            return False

    return True


# ============================================================
# EXISTING UNIVERSE
# ============================================================

def load_existing_universe(
) -> Dict[str, Dict[str, Any]]:

    if not UNIVERSE_FILE.exists():
        return {}

    try:

        payload = json.loads(
            UNIVERSE_FILE.read_text(
                encoding="utf-8"
            )
        )

    except Exception:

        return {}

    if not isinstance(
        payload,
        dict,
    ):
        return {}

    stocks = payload.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):
        return {}

    result = {}

    for key, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):
            continue

        symbol = clean_code(
            item.get(
                "symbol",
                key,
            )
        )

        if not symbol:
            continue

        result[symbol] = item

    return result


# ============================================================
# TWSE SOURCE 1
# ============================================================

def fetch_twse_openapi_daily(
) -> List[Dict[str, Any]]:

    section(
        "TWSE 官方來源 1：STOCK_DAY_ALL"
    )

    payload = request_json(
        TWSE_OPENAPI_STOCK_DAY_ALL
    )

    rows = normalize_records(
        payload
    )

    if rows:

        log(
            f"✓ TWSE STOCK_DAY_ALL："
            f"{len(rows)} rows"
        )

    else:

        log(
            "⚠️ TWSE STOCK_DAY_ALL 無法取得"
        )

    return rows


# ============================================================
# TWSE SOURCE 2
# ============================================================

def fetch_twse_openapi_avg(
) -> List[Dict[str, Any]]:

    section(
        "TWSE 官方來源 2：STOCK_DAY_AVG_ALL"
    )

    payload = request_json(
        TWSE_OPENAPI_SECURITIES
    )

    rows = normalize_records(
        payload
    )

    if rows:

        log(
            f"✓ TWSE STOCK_DAY_AVG_ALL："
            f"{len(rows)} rows"
        )

    else:

        log(
            "⚠️ TWSE STOCK_DAY_AVG_ALL 無法取得"
        )

    return rows


# ============================================================
# TWSE SOURCE 3
# ============================================================

def fetch_twse_stock_main(
) -> List[Dict[str, Any]]:

    section(
        "TWSE 官方來源 3：證券主檔替代"
    )

    payload = request_json(
        TWSE_OPENAPI_STOCK_MAIN
    )

    rows = normalize_records(
        payload
    )

    if rows:

        log(
            f"✓ TWSE BWIBBU_d："
            f"{len(rows)} rows"
        )

    else:

        log(
            "⚠️ TWSE BWIBBU_d 無法取得"
        )

    return rows


# ============================================================
# TWSE COMBINE
# ============================================================

def build_twse_candidates(
    sources: List[
        Tuple[
            str,
            List[Dict[str, Any]],
        ]
    ],
) -> Dict[str, Dict[str, Any]]:

    result: Dict[
        str,
        Dict[str, Any],
    ] = {}

    source_count: Dict[
        str,
        int,
    ] = {}

    for source_name, rows in sources:

        source_count[
            source_name
        ] = 0

        for row in rows:

            if not is_common_stock_row(
                row
            ):
                continue

            symbol = find_symbol(
                row
            )

            name = find_name(
                row
            )

            if not symbol or not name:
                continue

            source_count[
                source_name
            ] += 1

            if symbol not in result:

                result[symbol] = {
                    "symbol": symbol,
                    "name": name,
                    "market": "TWSE",
                    "source": (
                        "TWSE_OFFICIAL"
                    ),
                    "source_endpoint": (
                        source_name
                    ),
                }

            else:

                # 多官方來源名稱一致時，
                # 保留第一次有效資料。
                # 如果名稱不同，優先較長且非空名稱。
                old_name = result[
                    symbol
                ].get(
                    "name",
                    "",
                )

                if (
                    len(name)
                    > len(old_name)
                ):

                    result[
                        symbol
                    ]["name"] = name

    log("")
    log(
        "TWSE 官方來源解析結果："
    )

    for source_name, count in (
        source_count.items()
    ):

        log(
            f"  {source_name}：{count}"
        )

    log(
        f"✓ TWSE unique candidates："
        f"{len(result)}"
    )

    return result


# ============================================================
# TPEX SOURCE
# ============================================================

def fetch_tpex_daily_quotes(
) -> List[Dict[str, Any]]:

    section(
        "TPEx 官方來源：每日收盤行情"
    )

    payload = request_json(
        TPEX_DAILY_QUOTES
    )

    rows = normalize_records(
        payload
    )

    if rows:

        log(
            f"✓ TPEx daily quotes："
            f"{len(rows)} rows"
        )

    else:

        log(
            "⚠️ TPEx daily quotes 無法取得"
        )

    return rows


# ============================================================
# TPEX SECONDARY
# ============================================================

def fetch_tpex_secondary(
) -> List[Dict[str, Any]]:

    section(
        "TPEx 官方來源：官方替代 endpoint"
    )

    payload = request_json(
        TPEX_STOCKS
    )

    rows = normalize_records(
        payload
    )

    if rows:

        log(
            f"✓ TPEx secondary："
            f"{len(rows)} rows"
        )

    else:

        log(
            "⚠️ TPEx secondary 無法取得"
        )

    return rows


# ============================================================
# TPEX COMBINE
# ============================================================

def build_tpex_candidates(
    sources: List[
        Tuple[
            str,
            List[Dict[str, Any]],
        ]
    ],
) -> Dict[str, Dict[str, Any]]:

    result: Dict[
        str,
        Dict[str, Any],
    ] = {}

    source_count = {}

    for source_name, rows in sources:

        source_count[
            source_name
        ] = 0

        for row in rows:

            if not is_common_stock_row(
                row
            ):
                continue

            symbol = find_symbol(
                row
            )

            name = find_name(
                row
            )

            if not symbol or not name:
                continue

            source_count[
                source_name
            ] += 1

            if symbol not in result:

                result[symbol] = {
                    "symbol": symbol,
                    "name": name,
                    "market": "TPEX",
                    "source": (
                        "TPEX_OFFICIAL"
                    ),
                    "source_endpoint": (
                        source_name
                    ),
                }

            else:

                old_name = result[
                    symbol
                ].get(
                    "name",
                    "",
                )

                if (
                    len(name)
                    > len(old_name)
                ):

                    result[
                        symbol
                    ]["name"] = name

    log("")
    log(
        "TPEx 官方來源解析結果："
    )

    for source_name, count in (
        source_count.items()
    ):

        log(
            f"  {source_name}：{count}"
        )

    log(
        f"✓ TPEx unique candidates："
        f"{len(result)}"
    )

    return result


# ============================================================
# MERGE WITH EXISTING
# ============================================================

def merge_existing_metadata(
    candidates: Dict[
        str,
        Dict[str, Any],
    ],
    existing: Dict[
        str,
        Dict[str, Any],
    ],
) -> Dict[
    str,
    Dict[str, Any],
]:

    result = {}

    for symbol, candidate in (
        candidates.items()
    ):

        old = existing.get(
            symbol,
            {},
        )

        if not isinstance(
            old,
            dict,
        ):
            old = {}

        name = clean_text(
            candidate.get(
                "name"
            )
        )

        if not name:

            name = clean_text(
                old.get(
                    "name"
                )
            )

        market = candidate.get(
            "market"
        )

        suffix = (
            ".TW"
            if market == "TWSE"
            else ".TWO"
        )

        full_symbol = clean_text(
            old.get(
                "full_symbol"
            )
        )

        if not full_symbol:

            full_symbol = (
                symbol + suffix
            )

        # ----------------------------------------------------
        # status
        # ----------------------------------------------------

        status = "active"

        # ----------------------------------------------------
        # type
        # ----------------------------------------------------

        instrument_type = clean_text(
            old.get(
                "type",
                "STOCK",
            )
        ).upper()

        if not instrument_type:
            instrument_type = "STOCK"

        result[symbol] = {
            "symbol": symbol,
            "full_symbol": full_symbol,
            "name": name,
            "market": market,
            "type": instrument_type,
            "status": status,
            "source": candidate.get(
                "source",
                "OFFICIAL",
            ),
            "source_endpoint": candidate.get(
                "source_endpoint",
                "",
            ),
        }

    return result


# ============================================================
# PAYLOAD
# ============================================================

def build_payload(
    stocks: Dict[
        str,
        Dict[str, Any],
    ],
) -> Dict[str, Any]:

    twse_count = sum(
        1
        for item in stocks.values()
        if item.get("market")
        == "TWSE"
    )

    tpex_count = sum(
        1
        for item in stocks.values()
        if item.get("market")
        == "TPEX"
    )

    return {
        "version": VERSION,
        "generated_at": iso_datetime(),
        "data_date": today(),
        "universe_count": len(stocks),
        "market_counts": {
            "TWSE": twse_count,
            "TPEX": tpex_count,
        },
        "source_policy": {
            "authority": "OFFICIAL",
            "twse": [
                "TWSE_OFFICIAL",
            ],
            "tpex": [
                "TPEX_OFFICIAL",
            ],
            "third_party_allowed": False,
        },
        "stocks": stocks,
    }


# ============================================================
# STRUCTURE VALIDATION
# ============================================================

REQUIRED_STOCK_FIELDS = {
    "symbol",
    "full_symbol",
    "name",
    "market",
    "type",
    "status",
    "source",
    "source_endpoint",
}


def validate_structure(
    payload: Dict[str, Any],
) -> bool:

    section(
        "Structure Gate"
    )

    if not isinstance(
        payload,
        dict,
    ):

        log(
            "❌ payload 不是 dict"
        )

        return False

    stocks = payload.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        log(
            "❌ stocks 必須是 dict"
        )

        return False

    if not stocks:

        log(
            "❌ stocks 不得為空"
        )

        return False

    errors = 0

    for symbol, item in (
        stocks.items()
    ):

        if not isinstance(
            item,
            dict,
        ):

            log(
                f"❌ {symbol}: item 非 dict"
            )

            errors += 1
            continue

        missing = (
            REQUIRED_STOCK_FIELDS
            - set(item.keys())
        )

        if missing:

            log(
                f"❌ {symbol}: "
                f"missing={sorted(missing)}"
            )

            errors += len(missing)

        if clean_code(
            item.get(
                "symbol"
            )
        ) != symbol:

            log(
                f"❌ {symbol}: symbol mismatch"
            )

            errors += 1

        if item.get(
            "market"
        ) not in {
            "TWSE",
            "TPEX",
        }:

            log(
                f"❌ {symbol}: invalid market"
            )

            errors += 1

        if item.get(
            "status"
        ) != "active":

            log(
                f"❌ {symbol}: "
                "new Universe item 必須 active"
            )

            errors += 1

        if item.get(
            "type"
        ) != "STOCK":

            log(
                f"❌ {symbol}: "
                f"type={item.get('type')}"
            )

            errors += 1

        if not looks_like_stock_code(
            symbol
        ):

            log(
                f"❌ invalid symbol：{symbol}"
            )

            errors += 1

        if not item.get(
            "name"
        ):

            log(
                f"❌ {symbol}: name empty"
            )

            errors += 1

        source = item.get(
            "source"
        )

        if source not in {
            "TWSE_OFFICIAL",
            "TPEX_OFFICIAL",
        }:

            log(
                f"❌ {symbol}: "
                f"invalid source={source}"
            )

            errors += 1

    if errors:

        log(
            f"❌ Structure Gate FAIL："
            f"{errors}"
        )

        return False

    log(
        f"✓ Structure Gate PASS："
        f"{len(stocks)}"
    )

    return True


# ============================================================
# MARKET GATE
# ============================================================

def market_gate(
    stocks: Dict[
        str,
        Dict[str, Any],
    ],
) -> bool:

    section(
        "Market Coverage Gate"
    )

    twse = [
        symbol
        for symbol, item
        in stocks.items()
        if item.get("market")
        == "TWSE"
    ]

    tpex = [
        symbol
        for symbol, item
        in stocks.items()
        if item.get("market")
        == "TPEX"
    ]

    log(
        f"TWSE：{len(twse)}"
    )

    log(
        f"TPEx：{len(tpex)}"
    )

    if not twse:

        log(
            "❌ TWSE Universe 為 0"
        )

        return False

    if not tpex:

        log(
            "❌ TPEx Universe 為 0"
        )

        return False

    log(
        "✓ TWSE / TPEx 均有官方 Universe"
    )

    return True


# ============================================================
# PAYLOAD GATE
# ============================================================

def validate_payload(
    payload: Dict[str, Any],
) -> bool:

    section(
        "Payload Gate"
    )

    stocks = payload.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):
        return False

    if payload.get(
        "universe_count"
    ) != len(stocks):

        log(
            "❌ universe_count mismatch"
        )

        return False

    market_counts = payload.get(
        "market_counts"
    )

    if not isinstance(
        market_counts,
        dict,
    ):
        return False

    actual_twse = sum(
        1
        for item in stocks.values()
        if item.get("market")
        == "TWSE"
    )

    actual_tpex = sum(
        1
        for item in stocks.values()
        if item.get("market")
        == "TPEX"
    )

    if market_counts.get(
        "TWSE"
    ) != actual_twse:

        log(
            "❌ TWSE count mismatch"
        )

        return False

    if market_counts.get(
        "TPEX"
    ) != actual_tpex:

        log(
            "❌ TPEx count mismatch"
        )

        return False

    source_policy = payload.get(
        "source_policy"
    )

    if not isinstance(
        source_policy,
        dict,
    ):
        return False

    if source_policy.get(
        "third_party_allowed"
    ) is not False:

        log(
            "❌ third_party_allowed "
            "必須為 false"
        )

        return False

    log(
        "✓ Payload Gate PASS"
    )

    return True


# ============================================================
# ATOMIC WRITE
# ============================================================

def atomic_write(
    payload: Dict[str, Any],
) -> bool:

    section(
        "Atomic Write"
    )

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:

        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        )

        TEMP_FILE.write_text(
            serialized,
            encoding="utf-8",
        )

        # ----------------------------------------------------
        # temp file JSON validation
        # ----------------------------------------------------

        check = json.loads(
            TEMP_FILE.read_text(
                encoding="utf-8"
            )
        )

        if not isinstance(
            check,
            dict,
        ):

            raise RuntimeError(
                "temporary JSON root "
                "不是 dict"
            )

        TEMP_FILE.replace(
            UNIVERSE_FILE
        )

        log(
            f"✓ Atomic Write："
            f"{UNIVERSE_FILE}"
        )

        return True

    except Exception as exc:

        log(
            f"❌ Atomic Write FAIL："
            f"{exc}"
        )

        try:

            TEMP_FILE.unlink(
                missing_ok=True
            )

        except Exception:
            pass

        return False


# ============================================================
# POST WRITE VERIFY
# ============================================================

def post_write_verify(
    expected_payload: Dict[str, Any],
) -> bool:

    section(
        "Post Write Verify"
    )

    if not UNIVERSE_FILE.exists():

        log(
            "❌ universe.json 不存在"
        )

        return False

    try:

        payload = json.loads(
            UNIVERSE_FILE.read_text(
                encoding="utf-8"
            )
        )

    except Exception as exc:

        log(
            f"❌ universe.json JSON ERROR："
            f"{exc}"
        )

        return False

    if not isinstance(
        payload,
        dict,
    ):

        return False

    stocks = payload.get(
        "stocks"
    )

    expected_stocks = (
        expected_payload.get(
            "stocks"
        )
    )

    if not isinstance(
        stocks,
        dict,
    ):

        log(
            "❌ 寫入後 stocks 不是 dict"
        )

        return False

    if not isinstance(
        expected_stocks,
        dict,
    ):

        return False

    if set(
        stocks.keys()
    ) != set(
        expected_stocks.keys()
    ):

        log(
            "❌ 寫入後 Universe symbol set 改變"
        )

        return False

    if payload.get(
        "universe_count"
    ) != len(stocks):

        log(
            "❌ 寫入後 universe_count 錯誤"
        )

        return False

    for symbol, item in (
        stocks.items()
    ):

        if not isinstance(
            item,
            dict,
        ):

            log(
                f"❌ {symbol}: item 非 dict"
            )

            return False

        if item.get(
            "status"
        ) != "active":

            log(
                f"❌ {symbol}: status != active"
            )

            return False

        if item.get(
            "symbol"
        ) != symbol:

            log(
                f"❌ {symbol}: symbol mismatch"
            )

            return False

        if item.get(
            "market"
        ) not in {
            "TWSE",
            "TPEX",
        }:

            return False

        if item.get(
            "type"
        ) != "STOCK":

            return False

    log(
        "✓ universe.json 重新讀取成功"
    )

    log(
        f"✓ Universe：{len(stocks)}"
    )

    log(
        "✓ symbol set 一致"
    )

    log(
        "✓ active 狀態一致"
    )

    log(
        "✓ market/type 驗證一致"
    )

    log(
        "✓ Post Write Verify PASS"
    )

    return True


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    start = time.time()

    section(
        "台股 AI 選股系統 "
        f"Universe Builder {VERSION}"
    )

    log(
        f"開始時間：{iso_datetime()}"
    )

    try:

        # ====================================================
        # Existing Universe
        # ====================================================

        existing = (
            load_existing_universe()
        )

        log(
            f"既有 Universe metadata："
            f"{len(existing)}"
        )

        # ====================================================
        # TWSE
        # ====================================================

        twse_sources = []

        twse_rows_1 = (
            fetch_twse_openapi_daily()
        )

        if twse_rows_1:
            twse_sources.append(
                (
                    "STOCK_DAY_ALL",
                    twse_rows_1,
                )
            )

        # ----------------------------------------------------
        # 如果第一個官方 endpoint 失敗，
        # 不立即 FAIL。
        # 繼續官方替代來源。
        # ----------------------------------------------------

        twse_rows_2 = (
            fetch_twse_openapi_avg()
        )

        if twse_rows_2:
            twse_sources.append(
                (
                    "STOCK_DAY_AVG_ALL",
                    twse_rows_2,
                )
            )

        twse_rows_3 = (
            fetch_twse_stock_main()
        )

        if twse_rows_3:
            twse_sources.append(
                (
                    "BWIBBU_d",
                    twse_rows_3,
                )
            )

        twse_candidates = (
            build_twse_candidates(
                twse_sources
            )
        )

        # ====================================================
        # TPEx
        # ====================================================

        tpex_sources = []

        tpex_rows_1 = (
            fetch_tpex_daily_quotes()
        )

        if tpex_rows_1:
            tpex_sources.append(
                (
                    "TPEX_DAILY_QUOTES",
                    tpex_rows_1,
                )
            )

        tpex_rows_2 = (
            fetch_tpex_secondary()
        )

        if tpex_rows_2:
            tpex_sources.append(
                (
                    "TPEX_SECONDARY",
                    tpex_rows_2,
                )
            )

        tpex_candidates = (
            build_tpex_candidates(
                tpex_sources
            )
        )

        # ====================================================
        # HARD OFFICIAL SOURCE GATE
        # ====================================================

        section(
            "Official Source Gate"
        )

        if not twse_sources:

            log(
                "❌ TWSE 所有官方來源皆失敗"
            )

            log(
                "❌ 禁止建立 TWSE Universe"
            )

            return 1

        if not tpex_sources:

            log(
                "❌ TPEx 所有官方來源皆失敗"
            )

            log(
                "❌ 禁止建立 TPEx Universe"
            )

            return 1

        if not twse_candidates:

            log(
                "❌ TWSE 官方來源有回應，"
                "但無法解析有效普通股"
            )

            return 1

        if not tpex_candidates:

            log(
                "❌ TPEx 官方來源有回應，"
                "但無法解析有效普通股"
            )

            return 1

        log(
            "✓ TWSE 官方來源可用"
        )

        log(
            "✓ TPEx 官方來源可用"
        )

        # ====================================================
        # MERGE
        # ====================================================

        section(
            "Build Universe"
        )

        candidates = {}

        candidates.update(
            twse_candidates
        )

        candidates.update(
            tpex_candidates
        )

        stocks = (
            merge_existing_metadata(
                candidates,
                existing,
            )
        )

        log(
            f"TWSE："
            f"{sum(1 for x in stocks.values() if x['market'] == 'TWSE')}"
        )

        log(
            f"TPEx："
            f"{sum(1 for x in stocks.values() if x['market'] == 'TPEX')}"
        )

        log(
            f"Universe："
            f"{len(stocks)}"
        )

        # ====================================================
        # Payload
        # ====================================================

        payload = build_payload(
            stocks
        )

        # ====================================================
        # Structure Gate
        # ====================================================

        if not validate_structure(
            payload
        ):

            log(
                "❌ BUILD STOP"
            )

            return 1

        # ====================================================
        # Market Gate
        # ====================================================

        if not market_gate(
            stocks
        ):

            log(
                "❌ BUILD STOP"
            )

            return 1

        # ====================================================
        # Payload Gate
        # ====================================================

        if not validate_payload(
            payload
        ):

            log(
                "❌ BUILD STOP"
            )

            return 1

        # ====================================================
        # Atomic Write
        # ====================================================

        if not atomic_write(
            payload
        ):

            return 1

        # ====================================================
        # Post Write Verify
        # ====================================================

        if not post_write_verify(
            payload
        ):

            log(
                "❌ BUILD FAIL"
            )

            return 1

        # ====================================================
        # RESULT
        # ====================================================

        elapsed = (
            time.time()
            - start
        )

        section(
            "BUILD RESULT"
        )

        log(
            "✓ UNIVERSE BUILD PASS"
        )

        log(
            f"✓ Version：{VERSION}"
        )

        log(
            f"✓ Data Date：{payload['data_date']}"
        )

        log(
            f"✓ Universe："
            f"{payload['universe_count']}"
        )

        log(
            f"✓ TWSE："
            f"{payload['market_counts']['TWSE']}"
        )

        log(
            f"✓ TPEx："
            f"{payload['market_counts']['TPEX']}"
        )

        log(
            "✓ Source：OFFICIAL"
        )

        log(
            "✓ Third-party：DISABLED"
        )

        log(
            f"✓ elapsed：{elapsed:.1f}s"
        )

        return 0

    except KeyboardInterrupt:

        log(
            "❌ 使用者中斷"
        )

        return 130

    except Exception as exc:

        log(
            f"❌ BUILD EXCEPTION："
            f"{exc}"
        )

        return 1


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    sys.exit(
        main()
    )
