#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_universe.py

Universe 四層架構
============================================================

第一層：目前交易 Universe
------------------------------------------------------------
TWSE：
    官方 STOCK_DAY_ALL

TPEx：
    官方 tpex_mainboard_daily_close_quotes

第二層：商品類型判定
------------------------------------------------------------
STOCK
ETF
ETN
TDR
BOND
WARRANT
OTHER

第三層：status
------------------------------------------------------------
所有通過目前交易 Universe 的標的：

    status = "active"

第四層：名稱 / 市場 / 代號補充
------------------------------------------------------------
官方目前交易資料優先。

舊 universe.json：
    只能補名稱 / metadata
    不得用來增加 Universe 標的。

核心契約
============================================================

1. Data/universe.json 是唯一 Universe 輸出來源
2. stocks 必須是 dict/object
3. universe_count 必須等於 len(stocks)
4. status == "active" 才是有效 Universe
5. 不寫死股票數量
6. 不寫死股票代號
7. 不寫死股票名稱
8. 不寫死測試標的
9. 不使用 Yahoo
10. 不使用歷史資料建立 Universe
11. 不使用舊 Universe 湊數量
12. 舊 Universe 只能補名稱 / metadata
13. TWSE 使用官方目前交易資料
14. TPEx 使用官方目前交易資料
15. 不探測 CMoney
16. 不因 ESB API 失敗而讓 TPEx 主 Universe 變成 0
17. Structure Gate
18. Data Quality Gate
19. Gate 通過後才 Atomic Write
20. Atomic Write 後再次讀取驗證
21. Python AST / Syntax 必須合法
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests


# ============================================================
# PATH
# ============================================================

ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = ROOT / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"


# ============================================================
# VERSION
# ============================================================

VERSION = "UNIVERSE-4-LAYER-TPEx-FIX-COUNT"


# ============================================================
# NETWORK
# ============================================================

TIMEOUT = 40

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
        "*/*"
    ),
    "Accept-Language": (
        "zh-TW,zh;q=0.9,"
        "en-US;q=0.8,en;q=0.7"
    ),
}


# ============================================================
# OFFICIAL API
# ============================================================

TWSE_BASE = "https://openapi.twse.com.tw/v1"

TPEX_BASE = "https://www.tpex.org.tw/openapi/v1"


# ============================================================
# CURRENT UNIVERSE SOURCES
# ============================================================

TWSE_CURRENT_ENDPOINTS = [
    "/exchangeReport/STOCK_DAY_ALL",
]

TPEX_CURRENT_ENDPOINTS = [
    "/tpex_mainboard_daily_close_quotes",
]


# ============================================================
# STATUS CONTRACT
# ============================================================

ACTIVE_STATUS = "active"


# ============================================================
# INSTRUMENT TYPES
# ============================================================

ALLOWED_TYPES = {
    "STOCK",
    "ETF",
    "ETN",
    "TDR",
    "BOND",
    "WARRANT",
    "OTHER",
}


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(HEADERS)


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
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(
            ZoneInfo("Asia/Taipei")
        )

    except Exception:
        return datetime.now()


def today_string() -> str:
    return now_tw().strftime("%Y-%m-%d")


# ============================================================
# BASIC CLEAN
# ============================================================

def clean(value: Any) -> str:

    if value is None:
        return ""

    return (
        str(value)
        .replace("\ufeff", "")
        .replace("\u3000", " ")
        .strip()
    )


def normalize_symbol(value: Any) -> str:

    text = clean(value)

    if not text:
        return ""

    text = (
        text
        .replace(".TW", "")
        .replace(".TWO", "")
        .replace(".tw", "")
        .replace(".two", "")
        .strip()
    )

    text = re.sub(
        r"\s+",
        "",
        text,
    )

    return text


def normalize_name(value: Any) -> str:
    return clean(value)


def is_valid_symbol(symbol: str) -> bool:

    if not symbol:
        return False

    if len(symbol) > 12:
        return False

    return bool(
        re.fullmatch(
            r"[A-Za-z0-9]+",
            symbol,
        )
    )


# ============================================================
# NORMALIZED FIELD
# ============================================================

def normalized_key(value: Any) -> str:

    text = clean(value)

    return (
        text
        .replace("_", "")
        .replace("-", "")
        .replace("/", "")
        .replace(" ", "")
        .replace("\t", "")
        .replace("（", "")
        .replace("）", "")
        .replace("(", "")
        .replace(")", "")
        .lower()
    )


def find_value(
    row: Dict[str, Any],
    aliases: Iterable[str],
) -> str:

    if not isinstance(row, dict):
        return ""

    normalized = {}

    for key, value in row.items():
        normalized[
            normalized_key(key)
        ] = value

    alias_keys = [
        normalized_key(alias)
        for alias in aliases
    ]

    # Exact
    for alias in alias_keys:

        if alias in normalized:

            value = clean(
                normalized[alias]
            )

            if value:
                return value

    # Fuzzy
    for row_key, value in normalized.items():

        for alias in alias_keys:

            if alias and alias in row_key:

                text = clean(value)

                if text:
                    return text

    return ""


# ============================================================
# SYMBOL / NAME
# ============================================================

def parse_symbol(
    row: Dict[str, Any],
) -> str:

    return normalize_symbol(
        find_value(
            row,
            [
                "證券代號",
                "股票代號",
                "代號",
                "代碼",
                "證券代碼",
                "Code",
                "code",
                "Symbol",
                "symbol",
                "SecurityCode",
                "securityCode",
                "SecuritiesCompanyCode",
            ],
        )
    )


def parse_name(
    row: Dict[str, Any],
) -> str:

    return normalize_name(
        find_value(
            row,
            [
                "證券名稱",
                "股票名稱",
                "名稱",
                "公司名稱",
                "CompanyName",
                "Company Name",
                "companyName",
                "Name",
                "name",
                "SecurityName",
                "securityName",
            ],
        )
    )


# ============================================================
# MARKET
# ============================================================

def normalize_market(
    value: Any,
    default: str,
) -> str:

    text = clean(value).upper()

    if (
        "TWSE" in text
        or "上市" in text
    ):
        return "TWSE"

    if (
        "TPEX" in text
        or "OTC" in text
        or "上櫃" in text
    ):
        return "TPEX"

    return default


def parse_market(
    row: Dict[str, Any],
    default: str,
) -> str:

    value = find_value(
        row,
        [
            "市場別",
            "市場",
            "Market",
            "market",
            "MarketType",
            "marketType",
            "MarketCode",
            "marketCode",
        ],
    )

    return normalize_market(
        value,
        default,
    )


# ============================================================
# INSTRUMENT CLASSIFICATION
# ============================================================

ETF_KEYWORDS = (
    "ETF",
    "指數股票型基金",
    "指數型基金",
)

ETN_KEYWORDS = (
    "ETN",
    "指數投資證券",
)

TDR_KEYWORDS = (
    "TDR",
    "存託憑證",
)

WARRANT_KEYWORDS = (
    "權證",
    "認購權證",
    "認售權證",
    "牛證",
    "熊證",
)

BOND_KEYWORDS = (
    "債券",
    "公司債",
    "政府債",
)

OTHER_KEYWORDS = (
    "受益憑證",
    "基金",
)


def contains_any(
    text: str,
    keywords: Iterable[str],
) -> bool:

    upper = text.upper()

    for keyword in keywords:

        if keyword.upper() in upper:
            return True

    return False


def classify_instrument(
    row: Dict[str, Any],
    name: str,
) -> Tuple[str, str]:

    explicit = find_value(
        row,
        [
            "證券種類",
            "商品類別",
            "商品種類",
            "證券類型",
            "類別",
            "Type",
            "type",
            "InstrumentType",
            "instrument_type",
            "SecurityType",
            "securityType",
        ],
    )

    combined_parts = [
        name,
        explicit,
    ]

    for value in row.values():

        text = clean(value)

        if text:
            combined_parts.append(text)

    combined = " ".join(combined_parts)

    explicit_upper = explicit.upper()

    if (
        "ETF" in explicit_upper
        or contains_any(
            combined,
            ETF_KEYWORDS,
        )
    ):
        return "ETF", "ETF"

    if (
        "ETN" in explicit_upper
        or contains_any(
            combined,
            ETN_KEYWORDS,
        )
    ):
        return "ETN", "ETN"

    if contains_any(
        combined,
        TDR_KEYWORDS,
    ):
        return "TDR", "TDR"

    if contains_any(
        combined,
        WARRANT_KEYWORDS,
    ):
        return "WARRANT", "WARRANT"

    if contains_any(
        combined,
        BOND_KEYWORDS,
    ):
        return "BOND", "BOND"

    if contains_any(
        combined,
        OTHER_KEYWORDS,
    ):
        return "OTHER", "OTHER"

    return "STOCK", "COMMON_STOCK"


# ============================================================
# JSON PAYLOAD NORMALIZATION
# ============================================================

def rows_from_payload(
    payload: Any,
) -> List[Dict[str, Any]]:

    if isinstance(payload, list):

        return [
            item
            for item in payload
            if isinstance(item, dict)
        ]

    if not isinstance(payload, dict):
        return []

    # fields + data
    fields = payload.get("fields")
    data = payload.get("data")

    if (
        isinstance(fields, list)
        and isinstance(data, list)
    ):

        result = []

        for values in data:

            if isinstance(values, dict):

                result.append(values)
                continue

            if not isinstance(values, list):
                continue

            row = {}

            for index, field in enumerate(fields):

                if index >= len(values):
                    break

                row[str(field)] = values[index]

            if row:
                result.append(row)

        if result:
            return result

    # tables
    tables = payload.get("tables")

    if isinstance(tables, list):

        result = []

        for table in tables:

            if not isinstance(table, dict):
                continue

            table_fields = table.get("fields")
            table_data = table.get("data")

            if (
                not isinstance(table_fields, list)
                or not isinstance(table_data, list)
            ):
                continue

            for values in table_data:

                if isinstance(values, dict):

                    result.append(values)
                    continue

                if not isinstance(values, list):
                    continue

                row = {}

                for index, field in enumerate(table_fields):

                    if index >= len(values):
                        break

                    row[str(field)] = values[index]

                if row:
                    result.append(row)

        if result:
            return result

    # Generic arrays
    for key in (
        "data",
        "Data",
        "result",
        "results",
        "records",
        "Records",
        "aaData",
    ):

        value = payload.get(key)

        if not isinstance(value, list):
            continue

        result = [
            item
            for item in value
            if isinstance(item, dict)
        ]

        if result:
            return result

    return []


# ============================================================
# HTTP
# ============================================================

def request_json(
    url: str,
) -> Optional[Any]:

    last_error = ""

    for attempt in range(
        1,
        RETRIES + 1,
    ):

        try:

            response = SESSION.get(
                url,
                timeout=TIMEOUT,
            )

            response.raise_for_status()

            text = response.text.strip()

            if not text:

                last_error = "EMPTY RESPONSE"

            else:

                return response.json()

        except Exception as exc:

            last_error = (
                f"{type(exc).__name__}: {exc}"
            )

        if attempt < RETRIES:

            time.sleep(
                RETRY_SLEEP * attempt
            )

    log(f"⚠ API 讀取失敗：{url}")
    log(f"  {last_error}")

    return None


# ============================================================
# OLD UNIVERSE
# ============================================================

def load_old_universe_names() -> Dict[str, Dict[str, Any]]:

    """
    舊 Universe 僅作名稱 / metadata cache。

    舊 Universe 不可以增加新的 symbol。
    """

    if not UNIVERSE_FILE.exists():
        return {}

    try:

        payload = json.loads(
            UNIVERSE_FILE.read_text(
                encoding="utf-8-sig"
            )
        )

    except Exception:

        return {}

    if not isinstance(payload, dict):
        return {}

    raw = payload.get("stocks")

    if not isinstance(raw, dict):
        return {}

    cache = {}

    for key, item in raw.items():

        if not isinstance(item, dict):
            continue

        symbol = normalize_symbol(
            item.get(
                "symbol",
                key,
            )
        )

        if not is_valid_symbol(symbol):
            continue

        cache[symbol] = {
            "name": clean(
                item.get("name", "")
            ),
            "full_symbol": clean(
                item.get("full_symbol", "")
            ),
            "type": clean(
                item.get("type", "")
            ),
            "instrument_type": clean(
                item.get("instrument_type", "")
            ),
        }

    return cache


# ============================================================
# TWSE CURRENT
# ============================================================

def collect_twse_current() -> Dict[str, Dict[str, Any]]:

    section("TWSE CURRENT TRADING UNIVERSE")

    candidates = {}

    for endpoint in TWSE_CURRENT_ENDPOINTS:

        url = TWSE_BASE + endpoint

        payload = request_json(url)

        rows = rows_from_payload(payload)

        if not rows:
            continue

        accepted = 0

        for row in rows:

            symbol = parse_symbol(row)

            if not is_valid_symbol(symbol):
                continue

            name = parse_name(row)

            if not name:
                continue

            market = parse_market(
                row,
                "TWSE",
            )

            if market != "TWSE":
                continue

            candidates[symbol] = {
                "symbol": symbol,
                "name": name,
                "market": "TWSE",
                "raw": row,
                "source": "TWSE_STOCK_DAY_ALL",
            }

            accepted += 1

        if accepted:

            log(
                f"✓ {endpoint}：{accepted} 檔"
            )

            break

    log(
        f"TWSE current candidates："
        f"{len(candidates)}"
    )

    return candidates


# ============================================================
# TPEX CURRENT
# ============================================================

def collect_tpex_current() -> Dict[str, Dict[str, Any]]:

    section("TPEX CURRENT TRADING UNIVERSE")

    candidates = {}

    for endpoint in TPEX_CURRENT_ENDPOINTS:

        url = TPEX_BASE + endpoint

        log(f"嘗試：{url}")

        payload = request_json(url)

        rows = rows_from_payload(payload)

        if not rows:
            continue

        accepted = 0

        for row in rows:

            symbol = parse_symbol(row)

            if not is_valid_symbol(symbol):
                continue

            name = parse_name(row)

            if not name:
                continue

            market = parse_market(
                row,
                "TPEX",
            )

            if market not in {
                "TPEX",
                "",
            }:
                continue

            candidates[symbol] = {
                "symbol": symbol,
                "name": name,
                "market": "TPEX",
                "raw": row,
                "source": (
                    "TPEX_MAINBOARD_DAILY_CLOSE_QUOTES"
                ),
            }

            accepted += 1

        if accepted:

            log(
                f"✓ {endpoint}：{accepted} 檔"
            )

            break

    log(
        f"TPEx current candidates："
        f"{len(candidates)}"
    )

    return candidates


# ============================================================
# MERGE CURRENT UNIVERSE
# ============================================================

def merge_current_candidates(
    twse: Dict[str, Dict[str, Any]],
    tpex: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    section("CURRENT UNIVERSE MERGE")

    merged = {}

    for symbol, item in twse.items():
        merged[symbol] = item

    for symbol, item in tpex.items():

        if symbol in merged:

            existing_market = merged[
                symbol
            ].get("market")

            incoming_market = item.get(
                "market"
            )

            if existing_market != incoming_market:

                log(
                    f"⚠ 同代號不同市場："
                    f"{symbol} "
                    f"{existing_market}/"
                    f"{incoming_market}"
                )

                continue

        merged[symbol] = item

    log(
        f"✓ Current Universe："
        f"{len(merged)} 檔"
    )

    return merged


# ============================================================
# BUILD RECORD
# ============================================================

def build_record(
    candidate: Dict[str, Any],
    old_cache: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:

    symbol = normalize_symbol(
        candidate.get("symbol")
    )

    name = normalize_name(
        candidate.get("name")
    )

    market = normalize_market(
        candidate.get("market"),
        "",
    )

    old = old_cache.get(
        symbol,
        {},
    )

    # Name
    if not name:

        name = clean(
            old.get("name", "")
        )

    if not name:

        raise ValueError(
            f"{symbol} 缺少名稱"
        )

    # Classification
    type_name, instrument_type = (
        classify_instrument(
            candidate.get("raw", {}),
            name,
        )
    )

    if (
        type_name == "STOCK"
        and instrument_type == "COMMON_STOCK"
    ):

        old_type = clean(
            old.get("type", "")
        ).upper()

        old_instrument = clean(
            old.get("instrument_type", "")
        ).upper()

        if old_type in ALLOWED_TYPES:

            if old_type != "STOCK":
                type_name = old_type

        if old_instrument:
            instrument_type = old_instrument

    if type_name not in ALLOWED_TYPES:
        type_name = "OTHER"

    # Full symbol
    full_symbol = clean(
        candidate.get(
            "full_symbol",
            "",
        )
    )

    if not full_symbol:

        full_symbol = clean(
            old.get(
                "full_symbol",
                "",
            )
        )

    if not full_symbol:

        suffix = (
            ".TW"
            if market == "TWSE"
            else ".TWO"
        )

        full_symbol = (
            symbol + suffix
        )

    # Current status
    status = ACTIVE_STATUS

    # Source
    source = clean(
        candidate.get(
            "source",
            "",
        )
    )

    return {
        "symbol": symbol,
        "full_symbol": full_symbol,
        "name": name,
        "market": market,
        "type": type_name,
        "instrument_type": instrument_type,
        "status": status,
        "source": source,
    }


# ============================================================
# STRUCTURE GATE
# ============================================================

def structure_gate(
    stocks: Dict[str, Dict[str, Any]],
) -> bool:

    section("STRUCTURE GATE")

    required_fields = {
        "symbol",
        "full_symbol",
        "name",
        "market",
        "type",
        "instrument_type",
        "status",
        "source",
    }

    errors = 0

    if not isinstance(stocks, dict):

        log("❌ stocks 不是 dict")

        return False

    for symbol, item in stocks.items():

        if not isinstance(item, dict):

            log(
                f"❌ {symbol} record "
                f"不是 dict"
            )

            errors += 1

            continue

        missing = (
            required_fields
            - set(item.keys())
        )

        if missing:

            log(
                f"❌ {symbol} "
                f"缺少欄位："
                f"{sorted(missing)}"
            )

            errors += 1

        actual_symbol = normalize_symbol(
            item.get("symbol")
        )

        if actual_symbol != symbol:

            log(
                f"❌ key / symbol 不一致："
                f"{symbol}/"
                f"{actual_symbol}"
            )

            errors += 1

        if item.get("status") != ACTIVE_STATUS:

            log(
                f"❌ {symbol} "
                f"status 不是 active"
            )

            errors += 1

        if item.get("market") not in {
            "TWSE",
            "TPEX",
        }:

            log(
                f"❌ {symbol} "
                f"market 無效："
                f"{item.get('market')}"
            )

            errors += 1

        if item.get("type") not in ALLOWED_TYPES:

            log(
                f"❌ {symbol} "
                f"type 無效："
                f"{item.get('type')}"
            )

            errors += 1

        if not clean(item.get("name")):

            log(
                f"❌ {symbol} "
                f"name 為空"
            )

            errors += 1

        if not clean(
            item.get("full_symbol")
        ):

            log(
                f"❌ {symbol} "
                f"full_symbol 為空"
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
        f"{len(stocks)} 檔"
    )

    return True


# ============================================================
# DATA QUALITY GATE
# ============================================================

def data_quality_gate(
    twse_count: int,
    tpex_count: int,
    stocks: Dict[str, Dict[str, Any]],
) -> bool:

    section("DATA QUALITY GATE")

    errors = 0

    total = len(stocks)

    if twse_count <= 0:

        log(
            "❌ TWSE current Universe = 0"
        )

        errors += 1

    if tpex_count <= 0:

        log(
            "❌ TPEx current Universe = 0"
        )

        errors += 1

    if total <= 0:

        log(
            "❌ Current Universe = 0"
        )

        errors += 1

    symbols = list(stocks.keys())

    if len(symbols) != len(set(symbols)):

        log(
            "❌ Universe 存在重複 symbol"
        )

        errors += 1

    actual_twse = sum(
        1
        for item in stocks.values()
        if item.get("market") == "TWSE"
    )

    actual_tpex = sum(
        1
        for item in stocks.values()
        if item.get("market") == "TPEX"
    )

    if actual_twse != twse_count:

        log(
            f"❌ TWSE count 不一致："
            f"{actual_twse}/"
            f"{twse_count}"
        )

        errors += 1

    if actual_tpex != tpex_count:

        log(
            f"❌ TPEx count 不一致："
            f"{actual_tpex}/"
            f"{tpex_count}"
        )

        errors += 1

    inactive = [
        symbol
        for symbol, item in stocks.items()
        if item.get("status") != ACTIVE_STATUS
    ]

    if inactive:

        log(
            f"❌ 發現非 active 標的："
            f"{len(inactive)}"
        )

        errors += len(inactive)

    empty_names = [
        symbol
        for symbol, item in stocks.items()
        if not clean(item.get("name"))
    ]

    if empty_names:

        log(
            f"❌ 空白名稱："
            f"{len(empty_names)}"
        )

        errors += len(empty_names)

    invalid_sources = [
        symbol
        for symbol, item in stocks.items()
        if item.get("source")
        not in {
            "TWSE_STOCK_DAY_ALL",
            "TPEX_MAINBOARD_DAILY_CLOSE_QUOTES",
        }
    ]

    if invalid_sources:

        log(
            f"❌ 非官方目前交易來源："
            f"{len(invalid_sources)}"
        )

        errors += len(invalid_sources)

    log(f"TWSE：{actual_twse}")
    log(f"TPEx：{actual_tpex}")
    log(f"Total：{total}")
    log(
        f"Active："
        f"{total - len(inactive)}"
    )

    if errors:

        log(
            f"❌ Data Quality Gate FAIL："
            f"{errors}"
        )

        return False

    log("✓ Data Quality Gate PASS")

    return True


# ============================================================
# PAYLOAD
# ============================================================

def build_payload(
    stocks: Dict[str, Dict[str, Any]],
    twse_count: int,
    tpex_count: int,
) -> Dict[str, Any]:

    timestamp = now_tw().isoformat()

    # ========================================================
    # 關鍵修正
    #
    # validator 要求：
    #
    # universe_count == len(stocks)
    #
    # 因此這個欄位必須是頂層欄位。
    # ========================================================

    universe_count = len(stocks)

    active_count = sum(
        1
        for item in stocks.values()
        if item.get("status") == ACTIVE_STATUS
    )

    return {
        "version": VERSION,

        "generated_at": timestamp,

        "data_date": today_string(),

        # ★ Validator 明確要求的頂層欄位
        "universe_count": universe_count,

        "universe_contract": {
            "source": "Data/universe.json",
            "stocks_type": "dict",
            "active_status": "active",
            "historical_universe_used": False,
            "yahoo_used": False,
            "cmoney_used": False,
            "hardcoded_stock_count": False,
            "hardcoded_test_symbols": False,
        },

        "source": {
            "twse": "TWSE_STOCK_DAY_ALL",
            "tpex": (
                "TPEX_MAINBOARD_DAILY_CLOSE_QUOTES"
            ),
        },

        "counts": {
            "twse": twse_count,
            "tpex": tpex_count,
            "total": universe_count,
            "active": active_count,
        },

        "stocks": stocks,
    }


# ============================================================
# ATOMIC WRITE
# ============================================================

def atomic_write(
    payload: Dict[str, Any],
) -> None:

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd = None
    temp_path = None

    try:

        fd, temp_path = tempfile.mkstemp(
            prefix="universe_",
            suffix=".json.tmp",
            dir=str(DATA_DIR),
        )

        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
        ) as file:

            fd = None

            json.dump(
                payload,
                file,
                ensure_ascii=False,
                indent=2,
            )

            file.write("\n")

            file.flush()

            os.fsync(
                file.fileno()
            )

        os.replace(
            temp_path,
            UNIVERSE_FILE,
        )

        temp_path = None

    except Exception:

        if fd is not None:

            try:
                os.close(fd)
            except Exception:
                pass

        if temp_path:

            try:
                os.unlink(temp_path)
            except Exception:
                pass

        raise


# ============================================================
# POST WRITE VERIFY
# ============================================================

def verify_written_file(
    expected_stocks: Dict[str, Dict[str, Any]],
) -> bool:

    section("POST-WRITE VERIFICATION")

    if not UNIVERSE_FILE.exists():

        log(
            "❌ universe.json 不存在"
        )

        return False

    try:

        payload = json.loads(
            UNIVERSE_FILE.read_text(
                encoding="utf-8-sig"
            )
        )

    except Exception as exc:

        log(
            f"❌ universe.json JSON "
            f"解析失敗：{exc}"
        )

        return False

    if not isinstance(payload, dict):

        log(
            "❌ universe.json root "
            "不是 dict"
        )

        return False

    stocks = payload.get("stocks")

    if not isinstance(stocks, dict):

        log(
            "❌ universe.json stocks "
            "不是 dict"
        )

        return False

    # ========================================================
    # 關鍵修正驗證
    # ========================================================

    universe_count = payload.get(
        "universe_count"
    )

    if universe_count != len(stocks):

        log(
            f"❌ universe_count 錯誤："
            f"{universe_count} != "
            f"{len(stocks)}"
        )

        return False

    if universe_count != len(expected_stocks):

        log(
            f"❌ universe_count 與 "
            f"BUILD 結果不一致："
            f"{universe_count} != "
            f"{len(expected_stocks)}"
        )

        return False

    # Symbol set
    if set(stocks.keys()) != set(
        expected_stocks.keys()
    ):

        log(
            "❌ 寫入後 symbol 集合 "
            "與 BUILD 結果不一致"
        )

        return False

    # Record validation
    for symbol, item in stocks.items():

        if not isinstance(item, dict):

            log(
                f"❌ {symbol} "
                f"寫入後不是 dict"
            )

            return False

        if item.get("status") != ACTIVE_STATUS:

            log(
                f"❌ {symbol} "
                f"寫入後 status "
                f"不是 active"
            )

            return False

        if normalize_symbol(
            item.get("symbol")
        ) != symbol:

            log(
                f"❌ {symbol} "
                f"寫入後 symbol 不一致"
            )

            return False

        if item.get("market") not in {
            "TWSE",
            "TPEX",
        }:

            log(
                f"❌ {symbol} "
                f"寫入後 market 無效"
            )

            return False

        if item.get("type") not in ALLOWED_TYPES:

            log(
                f"❌ {symbol} "
                f"寫入後 type 無效"
            )

            return False

        if not clean(
            item.get("name")
        ):

            log(
                f"❌ {symbol} "
                f"寫入後 name 為空"
            )

            return False

        if not clean(
            item.get("full_symbol")
        ):

            log(
                f"❌ {symbol} "
                f"寫入後 full_symbol 為空"
            )

            return False

    log(
        f"✓ universe_count："
        f"{universe_count}"
    )

    log(
        f"✓ stocks："
        f"{len(stocks)}"
    )

    log(
        f"✓ 寫入後驗證 PASS："
        f"{len(stocks)} 檔"
    )

    return True


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    start = time.time()

    section(
        "台股 AI 選股系統 "
        "build_universe.py"
    )

    log(f"Version：{VERSION}")

    log(
        f"開始時間："
        f"{now_tw().isoformat()}"
    )

    log("")
    log("Universe 四層架構")
    log("✓ 第一層：目前交易標的")
    log(
        "✓ 第二層：股票 / ETF / ETN / "
        "TDR / 其他類型判定"
    )
    log("✓ 第三層：status 狀態驗證")
    log("✓ 第四層：名稱 / 市場 / 代號補充")
    log("✓ status 統一為 active")
    log("✓ universe_count = len(stocks)")
    log("✓ 不寫死股票")
    log("✓ 不寫死股票名稱")
    log("✓ 不寫死測試標的")
    log("✓ 不使用歷史資料建立 Universe")
    log("✓ 不使用 Yahoo")
    log("✓ 不探測 CMoney")
    log("✓ 舊 Universe 只能補名稱 / metadata")
    log(
        "✓ TPEx 使用官方 "
        "tpex_mainboard_daily_close_quotes"
    )
    log("✓ 不讓 ESB API 失敗阻斷主 Universe")
    log("✓ Structure Gate")
    log("✓ Data Quality Gate")
    log("✓ Atomic Write")
    log("✓ 寫入後重新驗證")

    try:

        # ====================================================
        # OLD CACHE
        # ====================================================

        old_cache = (
            load_old_universe_names()
        )

        log("")
        log(
            f"✓ 舊 Universe 名稱快取："
            f"{len(old_cache)} 檔"
        )

        # ====================================================
        # LAYER 1
        # ====================================================

        twse = collect_twse_current()

        tpex = collect_tpex_current()

        # ====================================================
        # HARD GATE
        # ====================================================

        if not twse:

            log("")
            log(
                "❌ TWSE 目前交易 "
                "Universe 為 0"
            )
            log("❌ 停止寫入")

            return 1

        if not tpex:

            log("")
            log(
                "❌ TPEx 目前交易 "
                "Universe 為 0"
            )
            log("❌ 停止寫入")

            return 1

        # ====================================================
        # MERGE
        # ====================================================

        current = (
            merge_current_candidates(
                twse,
                tpex,
            )
        )

        if not current:

            log(
                "❌ Current Universe = 0"
            )

            return 1

        # ====================================================
        # LAYER 2 + 3 + 4
        # ====================================================

        section(
            "BUILD ACTIVE UNIVERSE"
        )

        stocks = {}

        for symbol, candidate in (
            current.items()
        ):

            try:

                record = build_record(
                    candidate,
                    old_cache,
                )

                stocks[symbol] = record

            except Exception as exc:

                log(
                    f"⚠ 跳過 {symbol}："
                    f"{exc}"
                )

        log(
            f"✓ Build candidates："
            f"{len(stocks)}"
        )

        # ====================================================
        # STRUCTURE GATE
        # ====================================================

        if not structure_gate(stocks):

            log("❌ 停止寫入")

            return 1

        # ====================================================
        # DATA QUALITY GATE
        # ====================================================

        twse_count = sum(
            1
            for item in stocks.values()
            if item.get("market") == "TWSE"
        )

        tpex_count = sum(
            1
            for item in stocks.values()
            if item.get("market") == "TPEX"
        )

        if not data_quality_gate(
            twse_count,
            tpex_count,
            stocks,
        ):

            log("❌ 停止寫入")

            return 1

        # ====================================================
        # PAYLOAD
        # ====================================================

        payload = build_payload(
            stocks,
            twse_count,
            tpex_count,
        )

        # ====================================================
        # INTERNAL PAYLOAD CHECK
        # ====================================================

        if payload.get(
            "universe_count"
        ) != len(
            payload.get(
                "stocks",
                {},
            )
        ):

            log(
                "❌ BUILD PAYLOAD "
                "universe_count 不一致"
            )

            return 1

        log(
            f"✓ Payload universe_count："
            f"{payload['universe_count']}"
        )

        # ====================================================
        # ATOMIC WRITE
        # ====================================================

        section("ATOMIC WRITE")

        atomic_write(payload)

        log(
            f"✓ 已寫入："
            f"{UNIVERSE_FILE}"
        )

        # ====================================================
        # POST WRITE VERIFY
        # ====================================================

        if not verify_written_file(
            stocks
        ):

            log(
                "❌ 寫入後驗證失敗"
            )

            return 1

        # ====================================================
        # RESULT
        # ====================================================

        elapsed = (
            time.time() - start
        )

        active_count = sum(
            1
            for item in stocks.values()
            if item.get(
                "status"
            ) == ACTIVE_STATUS
        )

        section("BUILD RESULT")

        log(
            "✓ build_universe.py PASS"
        )

        log(
            f"✓ TWSE：{twse_count}"
        )

        log(
            f"✓ TPEx：{tpex_count}"
        )

        log(
            f"✓ Total：{len(stocks)}"
        )

        log(
            f"✓ universe_count："
            f"{len(stocks)}"
        )

        log(
            f"✓ Active："
            f"{active_count}"
        )

        log(
            f"✓ elapsed："
            f"{elapsed:.1f}s"
        )

        return 0

    except KeyboardInterrupt:

        log("❌ 使用者中斷")

        return 130

    except Exception as exc:

        log("")
        log("❌ BUILD EXCEPTION")

        log(
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        return 1


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    sys.exit(main())
