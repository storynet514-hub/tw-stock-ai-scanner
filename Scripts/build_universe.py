#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_universe.py

UNIVERSE-REBUILD-V4

============================================================
核心契約
============================================================

1. Data/universe.json 是唯一 Universe 來源
2. stocks 必須是 dict
3. Universe 只允許普通股
4. TWSE / TPEX 分開解析
5. 只使用官方來源
6. 不使用 CMoney
7. 不依賴既有 Universe 製造股票
8. 不固定 Universe 數量
9. 每一檔必須具有：
       symbol
       full_symbol
       name
       market
       type
       instrument_type
       status
10. status 必須為 "active"
11. type 必須為 "STOCK"
12. instrument_type 必須為 "COMMON_STOCK"
13. symbol 必須為四碼數字
14. full_symbol：
       TWSE -> XXXX.TW
       TPEX -> XXXX.TWO
15. TWSE / TPEX 官方來源都必須成功取得有效普通股
16. Atomic Write
17. 寫入後重新讀取驗證
18. Build 失敗時不得破壞原 Universe
19. 不使用巢狀 f-string
20. 所有核心資料重新建立，不讓舊 Universe 覆蓋官方核心欄位
"""

from __future__ import annotations

import json
import re
import sys
import time

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


# ============================================================
# VERSION
# ============================================================

VERSION = "UNIVERSE-REBUILD-V4"


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

TIMEOUT = 40
RETRIES = 4
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
        "text/html,"
        "*/*"
    ),
    "Accept-Language": (
        "zh-TW,zh;q=0.9,"
        "en-US;q=0.8,en;q=0.7"
    ),
}


session = requests.Session()
session.headers.update(HEADERS)


# ============================================================
# OFFICIAL TWSE SOURCES
# ============================================================

TWSE_SOURCES = [
    {
        "name": "STOCK_DAY_ALL",
        "url": (
            "https://openapi.twse.com.tw/v1/"
            "exchangeReport/STOCK_DAY_ALL"
        ),
        "params": {},
    },
    {
        "name": "STOCK_DAY_AVG_ALL",
        "url": (
            "https://openapi.twse.com.tw/v1/"
            "exchangeReport/STOCK_DAY_AVG_ALL"
        ),
        "params": {},
    },
    {
        "name": "BWIBBU_d",
        "url": (
            "https://www.twse.com.tw/rwd/zh/"
            "afterTrading/BWIBBU_d"
        ),
        "params": {
            "response": "json",
            "selectType": "ALL",
        },
    },
]


# ============================================================
# OFFICIAL TPEX SOURCES
# ============================================================

TPEX_SOURCES = [
    {
        "name": "TPEX_DAILY_QUOTES",
        "url": (
            "https://www.tpex.org.tw/openapi/v1/"
            "tpex_mainboard_daily_close_quotes"
        ),
        "params": {},
    },
    {
        "name": "TPEX_DAILY_QUOTES_ZH",
        "url": (
            "https://www.tpex.org.tw/openapi/v1/"
            "tpex_mainboard_daily_close_quotes"
        ),
        "params": {
            "l": "zh-tw",
        },
    },
]


# ============================================================
# LOGGING
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


# ============================================================
# TEXT
# ============================================================

def clean_text(value: Any) -> str:
    if value is None:
        return ""

    return str(value).strip()


def clean_code(value: Any) -> str:
    text = clean_text(value).upper()

    text = text.replace(
        ".TW",
        "",
    )

    text = text.replace(
        ".TWO",
        "",
    )

    text = text.replace(
        " ",
        "",
    )

    text = text.replace(
        "\u3000",
        "",
    )

    return text


def normalize_key(value: Any) -> str:
    text = clean_text(value).lower()

    return re.sub(
        r"[\s_\-\/\(\)（）:：]+",
        "",
        text,
    )


# ============================================================
# HTTP / JSON
# ============================================================

def parse_json_response(
    response: requests.Response,
) -> Optional[Any]:

    text = response.text.strip()

    if not text:
        return None

    try:
        return response.json()

    except Exception:
        pass

    try:
        text = text.lstrip("\ufeff")

        return json.loads(
            text
        )

    except Exception:
        return None


def request_official_json(
    name: str,
    url: str,
    params: Optional[Dict[str, Any]] = None,
) -> Optional[Any]:

    last_error = ""

    for attempt in range(
        1,
        RETRIES + 1,
    ):

        try:
            response = session.get(
                url,
                params=params or {},
                timeout=TIMEOUT,
            )

            if response.status_code != 200:

                last_error = (
                    "HTTP "
                    + str(response.status_code)
                )

            else:

                payload = parse_json_response(
                    response
                )

                if payload is not None:
                    return payload

                preview = (
                    response.text[:200]
                    .replace("\n", " ")
                    .replace("\r", " ")
                )

                last_error = (
                    "非 JSON 回應："
                    + preview
                )

        except Exception as exc:

            last_error = (
                type(exc).__name__
                + ": "
                + str(exc)
            )

        if attempt < RETRIES:
            time.sleep(
                RETRY_SLEEP * attempt
            )

    log(
        "❌ "
        + name
        + "："
        + last_error
    )

    return None


# ============================================================
# GENERIC JSON NORMALIZER
# ============================================================

def fields_data_to_rows(
    fields: Any,
    data: Any,
) -> List[Dict[str, Any]]:

    if not isinstance(fields, list):
        return []

    if not isinstance(data, list):
        return []

    result: List[Dict[str, Any]] = []

    for raw in data:

        if isinstance(raw, dict):
            result.append(raw)
            continue

        if not isinstance(raw, list):
            continue

        row: Dict[str, Any] = {}

        for index, field in enumerate(fields):

            if index >= len(raw):
                break

            key = clean_text(field)

            if not key:
                continue

            row[key] = raw[index]

        if row:
            result.append(row)

    return result


def normalize_records(
    payload: Any,
) -> List[Dict[str, Any]]:

    # --------------------------------------------------------
    # JSON array
    # --------------------------------------------------------

    if isinstance(payload, list):

        return [
            row
            for row in payload
            if isinstance(row, dict)
        ]

    if not isinstance(payload, dict):
        return []

    # --------------------------------------------------------
    # fields + data
    # --------------------------------------------------------

    rows = fields_data_to_rows(
        payload.get("fields"),
        payload.get("data"),
    )

    if rows:
        return rows

    # --------------------------------------------------------
    # tables
    # --------------------------------------------------------

    tables = payload.get("tables")

    if isinstance(tables, list):

        result: List[Dict[str, Any]] = []

        for table in tables:

            if not isinstance(
                table,
                dict,
            ):
                continue

            table_rows = fields_data_to_rows(
                table.get("fields"),
                table.get("data"),
            )

            result.extend(
                table_rows
            )

        if result:
            return result

    # --------------------------------------------------------
    # common result containers
    # --------------------------------------------------------

    for key in (
        "data",
        "Data",
        "result",
        "results",
        "records",
        "Records",
        "items",
        "Items",
    ):

        value = payload.get(key)

        if not isinstance(
            value,
            list,
        ):
            continue

        dict_rows = [
            row
            for row in value
            if isinstance(row, dict)
        ]

        if dict_rows:
            return dict_rows

    return []


# ============================================================
# FIELD LOOKUP
# ============================================================

def field_value(
    row: Dict[str, Any],
    aliases: List[str],
) -> Any:

    normalized = {}

    for key, value in row.items():

        normalized[
            normalize_key(key)
        ] = value

    for alias in aliases:

        alias_key = normalize_key(
            alias
        )

        if alias_key in normalized:

            return normalized[
                alias_key
            ]

    return None


# ============================================================
# CODE / NAME ALIASES
# ============================================================

CODE_ALIASES = [
    "證券代號",
    "證券代碼",
    "股票代號",
    "有價證券代號",
    "代號",
    "SecuritiesCompanyCode",
    "SecurityCode",
    "StockCode",
    "Code",
    "code",
    "symbol",
    "Symbol",
    "ticker",
]


NAME_ALIASES = [
    "證券名稱",
    "證券簡稱",
    "名稱",
    "股票名稱",
    "有價證券名稱",
    "SecuritiesCompanyName",
    "SecurityName",
    "StockName",
    "CompanyName",
    "Name",
    "name",
]


# ============================================================
# EXTRACTION
# ============================================================

def extract_code(
    row: Dict[str, Any],
) -> str:

    value = field_value(
        row,
        CODE_ALIASES,
    )

    code = clean_code(
        value
    )

    if code:
        return code

    for key, value in row.items():

        normalized = normalize_key(
            key
        )

        if not any(
            token in normalized
            for token in (
                "代號",
                "代碼",
                "code",
                "symbol",
                "ticker",
            )
        ):
            continue

        text = clean_text(
            value
        )

        match = re.search(
            r"(?<!\d)(\d{4})(?!\d)",
            text,
        )

        if match:
            return match.group(1)

    return ""


def extract_name(
    row: Dict[str, Any],
) -> str:

    value = field_value(
        row,
        NAME_ALIASES,
    )

    return clean_text(
        value
    )


# ============================================================
# ORDINARY STOCK FILTER
# ============================================================

NON_STOCK_KEYWORDS = (
    "ETF",
    "ETN",
    "權證",
    "認購權證",
    "認售權證",
    "債券",
    "公司債",
    "政府債",
    "受益證券",
    "特別股",
    "存託憑證",
    "TDR",
    "基金",
    "指數",
    "牛熊證",
    "可轉換公司債",
    "轉換公司債",
)


def looks_like_ordinary_stock(
    code: str,
    name: str,
    row: Dict[str, Any],
) -> bool:

    # --------------------------------------------------------
    # 必須四碼
    # --------------------------------------------------------

    if not re.fullmatch(
        r"\d{4}",
        code,
    ):
        return False

    # --------------------------------------------------------
    # 名稱 / 欄位內容排除商品
    # --------------------------------------------------------

    values = []

    values.append(
        name
    )

    for value in row.values():

        values.append(
            clean_text(value)
        )

    combined = " ".join(
        values
    ).upper()

    for keyword in NON_STOCK_KEYWORDS:

        if keyword.upper() in combined:
            return False

    return True


# ============================================================
# TWSE PARSER
# ============================================================

def parse_twse_candidates(
    source_name: str,
    payload: Any,
) -> Dict[str, Dict[str, str]]:

    rows = normalize_records(
        payload
    )

    result: Dict[
        str,
        Dict[str, str],
    ] = {}

    for row in rows:

        code = extract_code(
            row
        )

        if not code:
            continue

        name = extract_name(
            row
        )

        if not looks_like_ordinary_stock(
            code,
            name,
            row,
        ):
            continue

        result[code] = {
            "symbol": code,
            "name": name or code,
            "market": "TWSE",
            "type": "STOCK",
            "instrument_type": "COMMON_STOCK",
            "source": source_name,
        }

    return result


# ============================================================
# TPEX PARSER
# ============================================================

def parse_tpex_candidates(
    source_name: str,
    payload: Any,
) -> Dict[str, Dict[str, str]]:

    rows = normalize_records(
        payload
    )

    result: Dict[
        str,
        Dict[str, str],
    ] = {}

    for row in rows:

        code = extract_code(
            row
        )

        if not code:
            continue

        name = extract_name(
            row
        )

        if not looks_like_ordinary_stock(
            code,
            name,
            row,
        ):
            continue

        result[code] = {
            "symbol": code,
            "name": name or code,
            "market": "TPEX",
            "type": "STOCK",
            "instrument_type": "COMMON_STOCK",
            "source": source_name,
        }

    return result


# ============================================================
# EXISTING METADATA
# ============================================================

def load_existing_metadata(
) -> Dict[str, Dict[str, Any]]:

    if not UNIVERSE_FILE.exists():
        return {}

    try:

        text = UNIVERSE_FILE.read_text(
            encoding="utf-8-sig"
        )

        payload = json.loads(
            text
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

    result: Dict[
        str,
        Dict[str, Any],
    ] = {}

    for key, value in stocks.items():

        if not isinstance(
            value,
            dict,
        ):
            continue

        code = clean_code(
            value.get(
                "symbol",
                key,
            )
        )

        if not code:
            continue

        result[code] = value

    return result


# ============================================================
# COLLECT TWSE
# ============================================================

def collect_twse(
) -> Dict[str, Dict[str, str]]:

    section(
        "TWSE 官方來源"
    )

    combined: Dict[
        str,
        Dict[str, str],
    ] = {}

    source_counts: Dict[
        str,
        int,
    ] = {}

    for source in TWSE_SOURCES:

        name = source["name"]
        url = source["url"]
        params = source["params"]

        log("")
        log(
            "TWSE 官方來源："
            + name
        )

        payload = request_official_json(
            name,
            url,
            params,
        )

        if payload is None:
            continue

        rows = normalize_records(
            payload
        )

        log(
            "✓ "
            + name
            + " rows："
            + str(len(rows))
        )

        parsed = parse_twse_candidates(
            name,
            payload,
        )

        source_counts[name] = len(
            parsed
        )

        log(
            "  可解析普通股："
            + str(len(parsed))
        )

        for code, item in parsed.items():

            if code not in combined:

                combined[code] = item

    log("")
    log(
        "TWSE 官方來源解析結果："
    )

    for name, count in source_counts.items():

        log(
            "  "
            + name
            + "："
            + str(count)
        )

    log(
        "✓ TWSE unique ordinary stocks："
        + str(len(combined))
    )

    return combined


# ============================================================
# COLLECT TPEX
# ============================================================

def collect_tpex(
) -> Dict[str, Dict[str, str]]:

    section(
        "TPEx 官方來源"
    )

    combined: Dict[
        str,
        Dict[str, str],
    ] = {}

    source_counts: Dict[
        str,
        int,
    ] = {}

    for source in TPEX_SOURCES:

        name = source["name"]
        url = source["url"]
        params = source["params"]

        log("")
        log(
            "TPEx 官方來源："
            + name
        )

        payload = request_official_json(
            name,
            url,
            params,
        )

        if payload is None:
            continue

        rows = normalize_records(
            payload
        )

        log(
            "✓ "
            + name
            + " rows："
            + str(len(rows))
        )

        parsed = parse_tpex_candidates(
            name,
            payload,
        )

        source_counts[name] = len(
            parsed
        )

        log(
            "  可解析普通股："
            + str(len(parsed))
        )

        for code, item in parsed.items():

            if code not in combined:

                combined[code] = item

    log("")
    log(
        "TPEx 官方來源解析結果："
    )

    for name, count in source_counts.items():

        log(
            "  "
            + name
            + "："
            + str(count)
        )

    log(
        "✓ TPEx unique ordinary stocks："
        + str(len(combined))
    )

    return combined


# ============================================================
# OFFICIAL SOURCE GATE
# ============================================================

def official_source_gate(
    twse: Dict[str, Dict[str, str]],
    tpex: Dict[str, Dict[str, str]],
) -> bool:

    section(
        "Official Source Gate"
    )

    if not twse:

        log(
            "❌ TWSE 官方來源沒有解析出普通股"
        )

        return False

    if not tpex:

        log(
            "❌ TPEx 官方來源沒有解析出普通股"
        )

        return False

    log(
        "✓ TWSE 官方普通股："
        + str(len(twse))
    )

    log(
        "✓ TPEx 官方普通股："
        + str(len(tpex))
    )

    log(
        "✓ Official Source Gate PASS"
    )

    return True


# ============================================================
# BUILD
# ============================================================

def build_universe(
    twse: Dict[str, Dict[str, str]],
    tpex: Dict[str, Dict[str, str]],
    existing: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    section(
        "建立 Universe"
    )

    combined: Dict[
        str,
        Dict[str, str],
    ] = {}

    combined.update(
        twse
    )

    combined.update(
        tpex
    )

    stocks: Dict[
        str,
        Dict[str, Any],
    ] = {}

    for code in sorted(
        combined.keys()
    ):

        item = combined[code]

        market = clean_text(
            item.get(
                "market"
            )
        ).upper()

        if market not in {
            "TWSE",
            "TPEX",
        }:
            continue

        name = clean_text(
            item.get(
                "name"
            )
        )

        old = existing.get(
            code,
            {},
        )

        if not isinstance(
            old,
            dict,
        ):
            old = {}

        if not name:

            name = clean_text(
                old.get(
                    "name"
                )
            )

        if not name:
            name = code

        if market == "TWSE":

            full_symbol = (
                code
                + ".TW"
            )

        else:

            full_symbol = (
                code
                + ".TWO"
            )

        # ----------------------------------------------------
        # 核心欄位完全重新建立
        # ----------------------------------------------------

        stock: Dict[str, Any] = {
            "symbol": code,
            "full_symbol": full_symbol,
            "name": name,
            "market": market,
            "type": "STOCK",
            "instrument_type": "COMMON_STOCK",
            "status": "active",
        }

        # ----------------------------------------------------
        # 只保留非核心 metadata
        # ----------------------------------------------------

        for key in (
            "industry",
            "sector",
            "category",
        ):

            value = old.get(
                key
            )

            if value is None:
                continue

            if clean_text(value) == "":
                continue

            stock[key] = value

        stocks[code] = stock

    return stocks


# ============================================================
# STRUCTURE VALIDATION
# ============================================================

def validate_universe_structure(
    stocks: Dict[str, Dict[str, Any]],
) -> bool:

    section(
        "Universe Structure Gate"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        log(
            "❌ stocks 必須為 dict"
        )

        return False

    if not stocks:

        log(
            "❌ stocks 不得為空"
        )

        return False

    errors = 0

    symbols = set()
    full_symbols = set()

    required_fields = {
        "symbol",
        "full_symbol",
        "name",
        "market",
        "type",
        "instrument_type",
        "status",
    }

    for code, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):

            log(
                "❌ "
                + code
                + " item 不是 dict"
            )

            errors += 1
            continue

        missing = (
            required_fields
            - set(item.keys())
        )

        if missing:

            log(
                "❌ "
                + code
                + " 缺少欄位："
                + str(
                    sorted(missing)
                )
            )

            errors += 1

        symbol = clean_code(
            item.get(
                "symbol"
            )
        )

        if symbol != code:

            log(
                "❌ "
                + code
                + " symbol mismatch："
                + symbol
            )

            errors += 1

        if symbol in symbols:

            log(
                "❌ symbol duplicate："
                + symbol
            )

            errors += 1

        symbols.add(
            symbol
        )

        if not re.fullmatch(
            r"\d{4}",
            symbol,
        ):

            log(
                "❌ "
                + code
                + " 不是四碼普通股"
            )

            errors += 1

        full_symbol = clean_text(
            item.get(
                "full_symbol"
            )
        )

        if full_symbol in full_symbols:

            log(
                "❌ full_symbol duplicate："
                + full_symbol
            )

            errors += 1

        full_symbols.add(
            full_symbol
        )

        market = clean_text(
            item.get(
                "market"
            )
        ).upper()

        if market not in {
            "TWSE",
            "TPEX",
        }:

            log(
                "❌ "
                + code
                + " market 錯誤："
                + market
            )

            errors += 1

        expected_full_symbol = ""

        if market == "TWSE":

            expected_full_symbol = (
                code
                + ".TW"
            )

        elif market == "TPEX":

            expected_full_symbol = (
                code
                + ".TWO"
            )

        if full_symbol != expected_full_symbol:

            log(
                "❌ "
                + code
                + " full_symbol 錯誤："
                + full_symbol
            )

            errors += 1

        item_type = clean_text(
            item.get(
                "type"
            )
        ).upper()

        if item_type != "STOCK":

            log(
                "❌ "
                + code
                + " type != STOCK"
            )

            errors += 1

        instrument_type = clean_text(
            item.get(
                "instrument_type"
            )
        )

        if instrument_type != "COMMON_STOCK":

            log(
                "❌ "
                + code
                + " instrument_type != COMMON_STOCK"
            )

            errors += 1

        status = clean_text(
            item.get(
                "status"
            )
        ).lower()

        if status != "active":

            log(
                "❌ "
                + code
                + " status != active"
            )

            errors += 1

        name = clean_text(
            item.get(
                "name"
            )
        )

        if not name:

            log(
                "❌ "
                + code
                + " name 空白"
            )

            errors += 1

    if errors:

        log(
            "❌ Universe Structure Gate FAIL："
            + str(errors)
            + " errors"
        )

        return False

    log(
        "✓ Required fields"
    )

    log(
        "✓ symbol uniqueness"
    )

    log(
        "✓ full_symbol uniqueness"
    )

    log(
        "✓ market validation"
    )

    log(
        "✓ type=STOCK"
    )

    log(
        "✓ instrument_type=COMMON_STOCK"
    )

    log(
        "✓ status=active"
    )

    log(
        "✓ Universe Structure Gate PASS："
        + str(len(stocks))
    )

    return True


# ============================================================
# MARKET BALANCE
# ============================================================

def validate_market_balance(
    stocks: Dict[str, Dict[str, Any]],
    twse_source_count: int,
    tpex_source_count: int,
) -> bool:

    section(
        "Market Balance Gate"
    )

    twse_count = 0
    tpex_count = 0

    for item in stocks.values():

        if item.get(
            "market"
        ) == "TWSE":

            twse_count += 1

        elif item.get(
            "market"
        ) == "TPEX":

            tpex_count += 1

    log(
        "TWSE Universe："
        + str(twse_count)
    )

    log(
        "TPEx Universe："
        + str(tpex_count)
    )

    log(
        "TWSE official parsed："
        + str(twse_source_count)
    )

    log(
        "TPEx official parsed："
        + str(tpex_source_count)
    )

    if twse_source_count <= 0:

        log(
            "❌ TWSE 官方來源解析數為 0"
        )

        return False

    if tpex_source_count <= 0:

        log(
            "❌ TPEx 官方來源解析數為 0"
        )

        return False

    if twse_count <= 0:

        log(
            "❌ TWSE Universe 為 0"
        )

        return False

    if tpex_count <= 0:

        log(
            "❌ TPEx Universe 為 0"
        )

        return False

    log(
        "✓ Market Balance Gate PASS"
    )

    return True


# ============================================================
# PAYLOAD
# ============================================================

def make_payload(
    stocks: Dict[str, Dict[str, Any]],
    twse_source_count: int,
    tpex_source_count: int,
) -> Dict[str, Any]:

    generated_at = now_tw().isoformat()

    return {
        "version": VERSION,
        "generated_at": generated_at,
        "universe_count": len(stocks),

        "source": {
            "policy": (
                "TWSE / TPEX official sources only"
            ),
            "twse_official_candidates": (
                twse_source_count
            ),
            "tpex_official_candidates": (
                tpex_source_count
            ),
        },

        "contract": {
            "root": "dict",
            "stocks": "dict",
            "active_status": (
                "status == active"
            ),
            "ordinary_stock_only": True,
            "allowed_markets": [
                "TWSE",
                "TPEX",
            ],
            "allowed_type": "STOCK",
            "instrument_type": "COMMON_STOCK",
        },

        "stocks": stocks,
    }


# ============================================================
# PAYLOAD VALIDATION
# ============================================================

def validate_payload(
    payload: Dict[str, Any],
) -> bool:

    if not isinstance(
        payload,
        dict,
    ):
        return False

    stocks = payload.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):
        return False

    universe_count = payload.get(
        "universe_count"
    )

    if universe_count != len(
        stocks
    ):
        return False

    if universe_count <= 0:
        return False

    contract = payload.get(
        "contract"
    )

    if not isinstance(
        contract,
        dict,
    ):
        return False

    if contract.get(
        "active_status"
    ) != "status == active":

        return False

    if contract.get(
        "ordinary_stock_only"
    ) is not True:

        return False

    if contract.get(
        "allowed_markets"
    ) != [
        "TWSE",
        "TPEX",
    ]:

        return False

    if contract.get(
        "allowed_type"
    ) != "STOCK":

        return False

    if contract.get(
        "instrument_type"
    ) != "COMMON_STOCK":

        return False

    return validate_universe_structure(
        stocks
    )


# ============================================================
# ATOMIC WRITE
# ============================================================

def atomic_write(
    payload: Dict[str, Any],
) -> bool:

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:

        text = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        )

        # ----------------------------------------------------
        # 先寫 temporary file
        # ----------------------------------------------------

        TEMP_FILE.write_text(
            text,
            encoding="utf-8",
        )

        # ----------------------------------------------------
        # temporary file 必須能重新解析
        # ----------------------------------------------------

        check_text = TEMP_FILE.read_text(
            encoding="utf-8"
        )

        json.loads(
            check_text
        )

        # ----------------------------------------------------
        # replace 是 atomic rename
        # ----------------------------------------------------

        TEMP_FILE.replace(
            UNIVERSE_FILE
        )

        return True

    except Exception as exc:

        log(
            "❌ Atomic Write FAIL："
            + type(exc).__name__
            + ": "
            + str(exc)
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

def post_write_verify() -> bool:

    section(
        "Post Write Verify"
    )

    if not UNIVERSE_FILE.exists():

        log(
            "❌ universe.json 不存在"
        )

        return False

    try:

        text = UNIVERSE_FILE.read_text(
            encoding="utf-8-sig"
        )

        payload = json.loads(
            text
        )

    except Exception as exc:

        log(
            "❌ JSON 重新讀取失敗："
            + type(exc).__name__
            + ": "
            + str(exc)
        )

        return False

    if not validate_payload(
        payload
    ):

        log(
            "❌ universe.json Contract FAIL"
        )

        return False

    stocks = payload.get(
        "stocks"
    )

    log(
        "✓ universe.json 重新讀取："
        + str(len(stocks))
        + " 檔"
    )

    log(
        "✓ universe_count："
        + str(
            payload.get(
                "universe_count"
            )
        )
    )

    log(
        "✓ stocks object"
    )

    log(
        "✓ required fields"
    )

    log(
        "✓ instrument_type=COMMON_STOCK"
    )

    log(
        "✓ status=active"
    )

    log(
        "✓ Universe Contract PASS"
    )

    log(
        "✓ Post Write Verify PASS"
    )

    return True


# ============================================================
# SUMMARY
# ============================================================

def print_summary(
    stocks: Dict[str, Dict[str, Any]],
) -> None:

    twse = 0
    tpex = 0
    active = 0
    common_stock = 0

    for item in stocks.values():

        if item.get(
            "market"
        ) == "TWSE":

            twse += 1

        if item.get(
            "market"
        ) == "TPEX":

            tpex += 1

        if item.get(
            "status"
        ) == "active":

            active += 1

        if item.get(
            "instrument_type"
        ) == "COMMON_STOCK":

            common_stock += 1

    section(
        "UNIVERSE BUILD RESULT"
    )

    log(
        "Version："
        + VERSION
    )

    log(
        "Total："
        + str(len(stocks))
    )

    log(
        "TWSE："
        + str(twse)
    )

    log(
        "TPEX："
        + str(tpex)
    )

    log(
        "active："
        + str(active)
    )

    log(
        "COMMON_STOCK："
        + str(common_stock)
    )

    log(
        "ordinary STOCK only"
    )

    log(
        "official sources only"
    )

    log(
        "Output："
        + str(UNIVERSE_FILE)
    )


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    started = time.time()

    section(
        "台股 AI 選股系統 "
        + "Universe Builder "
        + VERSION
    )

    log(
        "開始時間："
        + now_tw().isoformat()
    )

    existing = load_existing_metadata()

    log(
        "既有 Universe metadata："
        + str(len(existing))
    )

    # ========================================================
    # 1. TWSE
    # ========================================================

    twse = collect_twse()

    # ========================================================
    # 2. TPEX
    # ========================================================

    tpex = collect_tpex()

    # ========================================================
    # 3. OFFICIAL SOURCE GATE
    # ========================================================

    if not official_source_gate(
        twse,
        tpex,
    ):

        log(
            "❌ 官方來源 Gate FAIL"
        )

        return 1

    # ========================================================
    # 4. BUILD
    # ========================================================

    stocks = build_universe(
        twse,
        tpex,
        existing,
    )

    # ========================================================
    # 5. STRUCTURE
    # ========================================================

    if not validate_universe_structure(
        stocks
    ):

        return 1

    # ========================================================
    # 6. MARKET BALANCE
    # ========================================================

    if not validate_market_balance(
        stocks,
        len(twse),
        len(tpex),
    ):

        return 1

    # ========================================================
    # 7. PAYLOAD
    # ========================================================

    payload = make_payload(
        stocks,
        len(twse),
        len(tpex),
    )

    if not validate_payload(
        payload
    ):

        log(
            "❌ Payload Contract FAIL"
        )

        return 1

    log(
        "✓ Payload Contract PASS"
    )

    # ========================================================
    # 8. ATOMIC WRITE
    # ========================================================

    if not atomic_write(
        payload
    ):

        return 1

    log(
        "✓ Atomic Write PASS"
    )

    # ========================================================
    # 9. POST WRITE VERIFY
    # ========================================================

    if not post_write_verify():

        log(
            "❌ Post Write Verify FAIL"
        )

        return 1

    # ========================================================
    # 10. SUMMARY
    # ========================================================

    print_summary(
        stocks
    )

    elapsed = time.time() - started

    log(
        "elapsed："
        + str(
            round(
                elapsed,
                1,
            )
        )
        + "s"
    )

    log("")
    log(
        "============================================================"
    )
    log(
        "UNIVERSE BUILD SUCCESS"
    )
    log(
        "============================================================"
    )

    return 0


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    sys.exit(
        main()
    )
