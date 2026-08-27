#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 - build_universe.py

UNIVERSE-REBUILD-V4

核心契約
------------------------------------------------------------
1. Data/universe.json 是後續資料流程唯一 Universe 來源
2. stocks 必須是 dict
3. 只建立普通股 STOCK
4. TWSE / TPEx 使用各自官方來源與解析器
5. 不依賴單一 API endpoint
6. 官方 endpoint 非 JSON / 空資料時自動嘗試下一來源
7. 不探測 CMoney
8. 不寫死 Universe 數量
9. 不使用既有 Universe 補造不存在的股票
10. 不把 ETF / ETN / 權證 / 債券 / TDR 等混入 STOCK
11. 每一檔必須具備完整核心 schema
12. status == active
13. instrument_type == "STOCK"
14. TWSE / TPEx 官方來源 Gate 必須通過
15. Universe 採 Atomic Write
16. 寫入後重新讀取並驗證
17. 不讓舊 Universe 污染官方核心資料
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
# OFFICIAL SOURCES
# ============================================================

TWSE_SOURCES = [
    (
        "STOCK_DAY_ALL",
        "https://openapi.twse.com.tw/v1/"
        "exchangeReport/STOCK_DAY_ALL",
        {},
    ),
    (
        "STOCK_DAY_AVG_ALL",
        "https://openapi.twse.com.tw/v1/"
        "exchangeReport/STOCK_DAY_AVG_ALL",
        {},
    ),
    (
        "BWIBBU_d",
        "https://www.twse.com.tw/rwd/zh/"
        "afterTrading/BWIBBU_d",
        {
            "response": "json",
            "selectType": "ALL",
        },
    ),
]


TPEX_SOURCES = [
    (
        "TPEX_DAILY_QUOTES",
        "https://www.tpex.org.tw/openapi/v1/"
        "tpex_mainboard_daily_close_quotes",
        {},
    ),
    (
        "TPEX_DAILY_QUOTES_ZH",
        "https://www.tpex.org.tw/openapi/v1/"
        "tpex_mainboard_daily_close_quotes",
        {
            "l": "zh-tw",
        },
    ),
]


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


# ============================================================
# TEXT
# ============================================================

def clean_text(value: Any) -> str:
    if value is None:
        return ""

    return str(value).strip()


def clean_code(value: Any) -> str:
    text = clean_text(value).upper()

    text = (
        text
        .replace(".TW", "")
        .replace(".TWO", "")
        .replace(" ", "")
        .replace("\u3000", "")
    )

    # 某些資料可能是：
    # 1101 台泥
    # 1101
    # 1101.0
    #
    # 先處理純數字字串。
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]

    return text


def normalize_key(value: Any) -> str:
    text = clean_text(value).lower()

    return re.sub(
        r"[\s_\-\/\(\)（）:：\[\]\{\}]+",
        "",
        text,
    )


# ============================================================
# JSON
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
        cleaned = text.lstrip("\ufeff")

        return json.loads(cleaned)

    except Exception:
        return None


# ============================================================
# HTTP
# ============================================================

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
                    f"HTTP {response.status_code}"
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

            last_error = str(exc)

        if attempt < RETRIES:
            time.sleep(
                RETRY_SLEEP * attempt
            )

    log(
        f"❌ {name}：{last_error}"
    )

    return None


# ============================================================
# GENERIC ROW EXTRACTION
# ============================================================

def rows_from_fields_data(
    fields: Any,
    data: Any,
) -> List[Dict[str, Any]]:

    if not isinstance(fields, list):
        return []

    if not isinstance(data, list):
        return []

    fields_clean = [
        clean_text(field)
        for field in fields
    ]

    result: List[Dict[str, Any]] = []

    for raw in data:

        if isinstance(raw, dict):
            result.append(raw)
            continue

        if not isinstance(raw, list):
            continue

        row: Dict[str, Any] = {}

        for index, value in enumerate(raw):

            if index >= len(fields_clean):
                break

            key = fields_clean[index]

            if key:
                row[key] = value

        if row:
            result.append(row)

    return result


def normalize_records(
    payload: Any,
) -> List[Dict[str, Any]]:
    """
    統一處理官方 API 常見格式。

    支援：

    1. list[dict]

    2. {
         "fields": [...],
         "data": [...]
       }

    3. {
         "tables": [...]
       }

    4. {
         "data": [...]
       }

    5. {
         "records": [...]
       }

    6. 某些 API 回傳：
       {
         "0": {...},
         "1": {...}
       }

    """

    # --------------------------------------------------------
    # 直接 list
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

    rows = rows_from_fields_data(
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

            if not isinstance(table, dict):
                continue

            table_rows = rows_from_fields_data(
                table.get("fields"),
                table.get("data"),
            )

            result.extend(table_rows)

            for key in (
                "data",
                "records",
                "rows",
            ):

                value = table.get(key)

                if isinstance(value, list):

                    result.extend(
                        row
                        for row in value
                        if isinstance(row, dict)
                    )

        if result:
            return result

    # --------------------------------------------------------
    # common array keys
    # --------------------------------------------------------

    for key in (
        "data",
        "Data",
        "result",
        "results",
        "records",
        "Records",
        "rows",
        "Rows",
        "items",
        "Items",
    ):

        value = payload.get(key)

        if not isinstance(value, list):
            continue

        rows = [
            row
            for row in value
            if isinstance(row, dict)
        ]

        if rows:
            return rows

        # list[list] + fields
        if value and all(
            isinstance(item, list)
            for item in value
        ):

            fields = (
                payload.get("fields")
                or payload.get("columns")
                or payload.get("Columns")
            )

            rows = rows_from_fields_data(
                fields,
                value,
            )

            if rows:
                return rows

    # --------------------------------------------------------
    # dict-of-records
    # --------------------------------------------------------

    dict_rows: List[Dict[str, Any]] = []

    for value in payload.values():

        if isinstance(value, dict):

            # 只接受看起來像資料列的 dict
            if any(
                normalize_key(k) in {
                    "證券代號",
                    "代號",
                    "code",
                    "symbol",
                    "securitiescompanycode",
                    "stockcode",
                }
                for k in value.keys()
            ):

                dict_rows.append(value)

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

    normalized = {
        normalize_key(key): value
        for key, value in row.items()
    }

    for alias in aliases:

        key = normalize_key(alias)

        if key in normalized:
            return normalized[key]

    return None


# ============================================================
# CODE EXTRACTION
# ============================================================

CODE_ALIASES = [
    "證券代號",
    "證券代碼",
    "股票代號",
    "股票代碼",
    "有價證券代號",
    "有價證券代碼",
    "SecuritiesCompanyCode",
    "SecurityCode",
    "StockCode",
    "LocalCode",
    "Code",
    "code",
    "symbol",
    "Symbol",
    "ticker",
    "Ticker",
]


NAME_ALIASES = [
    "證券名稱",
    "名稱",
    "股票名稱",
    "股票簡稱",
    "有價證券名稱",
    "SecuritiesCompanyName",
    "SecurityName",
    "StockName",
    "CompanyName",
    "Name",
    "name",
]


def extract_code(
    row: Dict[str, Any],
) -> str:

    value = field_value(
        row,
        CODE_ALIASES,
    )

    code = clean_code(value)

    if re.fullmatch(
        r"\d{4}",
        code,
    ):
        return code

    # --------------------------------------------------------
    # 第二層：掃描所有欄位名稱
    # --------------------------------------------------------

    for key, value in row.items():

        key_normalized = normalize_key(key)

        if not any(
            token in key_normalized
            for token in (
                "代號",
                "代碼",
                "code",
                "symbol",
                "ticker",
                "localcode",
                "securitiescompanycode",
            )
        ):
            continue

        text = clean_code(value)

        match = re.search(
            r"(?<!\d)(\d{4})(?!\d)",
            text,
        )

        if match:
            return match.group(1)

    # --------------------------------------------------------
    # 第三層：掃描資料列本身
    #
    # TPEx 某些格式欄位名稱變化較大。
    # 只接受明確 4 碼數字，不接受 5/6 碼。
    # --------------------------------------------------------

    for value in row.values():

        text = clean_text(value)

        match = re.fullmatch(
            r"\s*(\d{4})\s*",
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

    return clean_text(value)


# ============================================================
# ORDINARY STOCK FILTER
# ============================================================

NON_STOCK_KEYWORDS = (
    "ETF",
    "ETN",
    "權證",
    "認購",
    "認售",
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
    "CB",
)


def looks_like_ordinary_stock(
    code: str,
    name: str,
    row: Dict[str, Any],
) -> bool:

    if not re.fullmatch(
        r"\d{4}",
        code,
    ):
        return False

    combined = (
        f"{name} "
        f"{' '.join(
            clean_text(v)
            for v in row.values()
        )}"
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

    rows = normalize_records(payload)

    result: Dict[str, Dict[str, str]] = {}

    for row in rows:

        code = extract_code(row)
        name = extract_name(row)

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
            "instrument_type": "STOCK",
            "source": source_name,
        }

    return result


# ============================================================
# TPEx PARSER
# ============================================================

def parse_tpex_candidates(
    source_name: str,
    payload: Any,
) -> Dict[str, Dict[str, str]]:

    """
    TPEx 專用解析器。

    不假設資料一定使用：
        證券代號

    同時支援：
        SecuritiesCompanyCode
        證券代碼
        代號
        Code
        code
        LocalCode
        symbol
        ticker

    並在最後才使用 row-level fallback。
    """

    rows = normalize_records(payload)

    result: Dict[str, Dict[str, str]] = {}

    for row in rows:

        code = extract_code(row)
        name = extract_name(row)

        if not code:
            continue

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
            "instrument_type": "STOCK",
            "source": source_name,
        }

    return result


# ============================================================
# DEBUG SAMPLE
# ============================================================

def print_sample_rows(
    payload: Any,
    market: str,
) -> None:

    rows = normalize_records(payload)

    if not rows:
        return

    log("")
    log(
        f"{market} parser sample："
    )

    for index, row in enumerate(
        rows[:3]
    ):

        log(
            f"  row[{index}] keys="
            f"{list(row.keys())[:12]}"
        )

        log(
            f"  row[{index}] code="
            f"{extract_code(row)!r} "
            f"name="
            f"{extract_name(row)!r}"
        )


# ============================================================
# EXISTING METADATA
# ============================================================

def load_existing_metadata() -> Dict[
    str,
    Dict[str, Any],
]:

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

        if code:
            result[code] = value

    return result


# ============================================================
# TWSE COLLECTION
# ============================================================

def collect_twse() -> Dict[
    str,
    Dict[str, str],
]:

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

    for (
        source_name,
        url,
        params,
    ) in TWSE_SOURCES:

        log("")
        log(
            f"TWSE 官方來源："
            f"{source_name}"
        )

        payload = request_official_json(
            source_name,
            url,
            params,
        )

        if payload is None:
            continue

        rows = normalize_records(
            payload
        )

        log(
            f"✓ {source_name}："
            f"{len(rows)} rows"
        )

        parsed = parse_twse_candidates(
            source_name,
            payload,
        )

        source_counts[
            source_name
        ] = len(parsed)

        log(
            f"  可解析普通股："
            f"{len(parsed)}"
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
            f"  {name}：{count}"
        )

    log(
        f"✓ TWSE unique ordinary stocks："
        f"{len(combined)}"
    )

    return combined


# ============================================================
# TPEx COLLECTION
# ============================================================

def collect_tpex() -> Dict[
    str,
    Dict[str, str],
]:

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

    for (
        source_name,
        url,
        params,
    ) in TPEX_SOURCES:

        log("")
        log(
            f"TPEx 官方來源："
            f"{source_name}"
        )

        payload = request_official_json(
            source_name,
            url,
            params,
        )

        if payload is None:
            continue

        rows = normalize_records(
            payload
        )

        log(
            f"✓ {source_name}："
            f"{len(rows)} rows"
        )

        # ----------------------------------------------------
        # 第一個來源失敗解析時輸出樣本。
        # 不影響正式流程。
        # ----------------------------------------------------

        if not rows:

            log(
                "⚠ TPEx 無法標準化資料列"
            )

        parsed = parse_tpex_candidates(
            source_name,
            payload,
        )

        source_counts[
            source_name
        ] = len(parsed)

        log(
            f"  可解析普通股："
            f"{len(parsed)}"
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
            f"  {name}：{count}"
        )

    log(
        f"✓ TPEx unique ordinary stocks："
        f"{len(combined)}"
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
            "❌ TWSE 官方來源無法解析普通股"
        )

        return False

    if not tpex:

        log(
            "❌ TPEx 官方來源無法解析普通股"
        )

        return False

    log(
        f"✓ TWSE 官方普通股："
        f"{len(twse)}"
    )

    log(
        f"✓ TPEx 官方普通股："
        f"{len(tpex)}"
    )

    log(
        "✓ Official Source Gate PASS"
    )

    return True


# ============================================================
# BUILD UNIVERSE
# ============================================================

def build_universe(
    twse: Dict[str, Dict[str, str]],
    tpex: Dict[str, Dict[str, str]],
    existing: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    section(
        "建立 Universe"
    )

    stocks: Dict[
        str,
        Dict[str, Any],
    ] = {}

    combined: Dict[
        str,
        Dict[str, str],
    ] = {}

    combined.update(twse)
    combined.update(tpex)

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

        official_name = clean_text(
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

        # ====================================================
        # 核心欄位全部重新建立
        # 舊資料不得覆蓋
        # ====================================================

        stock: Dict[str, Any] = {
            "symbol": code,

            "full_symbol": (
                f"{code}.TW"
                if market == "TWSE"
                else f"{code}.TWO"
            ),

            "name": (
                official_name
                or clean_text(
                    old.get("name")
                )
                or code
            ),

            "market": market,

            "type": "STOCK",

            "instrument_type": "STOCK",

            "status": "active",
        }

        # ====================================================
        # 非核心 metadata 可以保留
        # ====================================================

        for key in (
            "industry",
            "sector",
            "category",
        ):

            value = old.get(key)

            if value not in (
                None,
                "",
            ):

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
    full_symbols = set()

    required = {
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
                f"❌ {code}: item 非 dict"
            )

            errors += 1
            continue

        # ----------------------------------------------------
        # Required fields
        # ----------------------------------------------------

        missing = (
            required
            - set(item.keys())
        )

        if missing:

            log(
                f"❌ {code}: 缺少欄位 "
                f"{sorted(missing)}"
            )

            errors += 1
            continue

        # ----------------------------------------------------
        # Symbol
        # ----------------------------------------------------

        symbol = clean_code(
            item.get(
                "symbol"
            )
        )

        if symbol != code:

            log(
                f"❌ {code}: symbol mismatch"
            )

            errors += 1

        if not re.fullmatch(
            r"\d{4}",
            symbol,
        ):

            log(
                f"❌ {code}: 非四碼普通股"
            )

            errors += 1

        # ----------------------------------------------------
        # Name
        # ----------------------------------------------------

        if not clean_text(
            item.get("name")
        ):

            log(
                f"❌ {code}: name 空白"
            )

            errors += 1

        # ----------------------------------------------------
        # Market
        # ----------------------------------------------------

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
                f"❌ {code}: market={market}"
            )

            errors += 1

        # ----------------------------------------------------
        # Type
        # ----------------------------------------------------

        if item.get(
            "type"
        ) != "STOCK":

            log(
                f"❌ {code}: type != STOCK"
            )

            errors += 1

        # ----------------------------------------------------
        # Instrument type
        # ----------------------------------------------------

        if item.get(
            "instrument_type"
        ) != "STOCK":

            log(
                f"❌ {code}: "
                f"instrument_type != STOCK"
            )

            errors += 1

        # ----------------------------------------------------
        # Status
        # ----------------------------------------------------

        if item.get(
            "status"
        ) != "active":

            log(
                f"❌ {code}: "
                f"status != active"
            )

            errors += 1

        # ----------------------------------------------------
        # Full symbol
        # ----------------------------------------------------

        expected_full = (
            f"{code}.TW"
            if market == "TWSE"
            else f"{code}.TWO"
        )

        if item.get(
            "full_symbol"
        ) != expected_full:

            log(
                f"❌ {code}: "
                f"full_symbol 錯誤："
                f"{item.get('full_symbol')} "
                f"!= {expected_full}"
            )

            errors += 1

        full_symbol = clean_text(
            item.get(
                "full_symbol"
            )
        )

        if full_symbol in full_symbols:

            log(
                f"❌ full_symbol duplicate："
                f"{full_symbol}"
            )

            errors += 1

        full_symbols.add(
            full_symbol
        )

    if errors:

        log(
            f"❌ Universe Structure Gate FAIL："
            f"{errors}"
        )

        return False

    log(
        f"✓ Universe Structure Gate PASS："
        f"{len(stocks)}"
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

    twse_count = sum(
        1
        for item in stocks.values()
        if item.get(
            "market"
        ) == "TWSE"
    )

    tpex_count = sum(
        1
        for item in stocks.values()
        if item.get(
            "market"
        ) == "TPEX"
    )

    log(
        f"TWSE Universe："
        f"{twse_count}"
    )

    log(
        f"TPEx Universe："
        f"{tpex_count}"
    )

    if twse_source_count <= 0:

        log(
            "❌ TWSE 官方解析數為 0"
        )

        return False

    if tpex_source_count <= 0:

        log(
            "❌ TPEx 官方解析數為 0"
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

    return {
        "version": VERSION,

        "generated_at":
            now_tw().isoformat(),

        "universe_count":
            len(stocks),

        "source": {
            "policy":
                "TWSE / TPEx official sources only",

            "twse_official_candidates":
                twse_source_count,

            "tpex_official_candidates":
                tpex_source_count,
        },

        "contract": {
            "root":
                "dict",

            "stocks":
                "dict",

            "active_status":
                "status == active",

            "ordinary_stock_only":
                True,

            "allowed_markets": [
                "TWSE",
                "TPEX",
            ],

            "allowed_type":
                "STOCK",

            "instrument_type":
                "STOCK",
        },

        "stocks":
            stocks,
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

    if payload.get(
        "universe_count"
    ) != len(stocks):

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
    ) != "STOCK":

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

    temp_file = DATA_DIR / (
        "universe.json.tmp"
    )

    try:

        text = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        )

        temp_file.write_text(
            text,
            encoding="utf-8",
        )

        # ----------------------------------------------------
        # 寫入前 parse
        # ----------------------------------------------------

        json.loads(
            temp_file.read_text(
                encoding="utf-8"
            )
        )

        # ----------------------------------------------------
        # Atomic replace
        # ----------------------------------------------------

        temp_file.replace(
            UNIVERSE_FILE
        )

        return True

    except Exception as exc:

        log(
            f"❌ Atomic Write FAIL："
            f"{exc}"
        )

        try:

            temp_file.unlink(
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

        payload = json.loads(
            UNIVERSE_FILE.read_text(
                encoding="utf-8-sig"
            )
        )

    except Exception as exc:

        log(
            f"❌ JSON 重新讀取失敗："
            f"{exc}"
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
        f"✓ universe.json 重新讀取："
        f"{len(stocks)} 檔"
    )

    log(
        "✓ instrument_type=STOCK"
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

    twse = sum(
        1
        for item in stocks.values()
        if item.get(
            "market"
        ) == "TWSE"
    )

    tpex = sum(
        1
        for item in stocks.values()
        if item.get(
            "market"
        ) == "TPEX"
    )

    active = sum(
        1
        for item in stocks.values()
        if item.get(
            "status"
        ) == "active"
    )

    instrument_stock = sum(
        1
        for item in stocks.values()
        if item.get(
            "instrument_type"
        ) == "STOCK"
    )

    section(
        "UNIVERSE BUILD RESULT"
    )

    log(
        f"✓ Version：{VERSION}"
    )

    log(
        f"✓ Total：{len(stocks)}"
    )

    log(
        f"✓ TWSE：{twse}"
    )

    log(
        f"✓ TPEx：{tpex}"
    )

    log(
        f"✓ active：{active}"
    )

    log(
        f"✓ instrument_type=STOCK："
        f"{instrument_stock}"
    )

    log(
        "✓ ordinary STOCK only"
    )

    log(
        "✓ official sources only"
    )

    log(
        f"✓ Output："
        f"{UNIVERSE_FILE}"
    )


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    started = time.time()

    section(
        f"台股 AI 選股系統 "
        f"Universe Builder {VERSION}"
    )

    log(
        f"開始時間："
        f"{now_tw().isoformat()}"
    )

    existing = load_existing_metadata()

    log(
        f"既有 Universe metadata："
        f"{len(existing)}"
    )

    # ========================================================
    # 1. TWSE
    # ========================================================

    twse = collect_twse()

    # ========================================================
    # 2. TPEx
    # ========================================================

    tpex = collect_tpex()

    # ========================================================
    # 3. Official Source Gate
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
    # 4. Build
    # ========================================================

    stocks = build_universe(
        twse,
        tpex,
        existing,
    )

    # ========================================================
    # 5. Structure
    # ========================================================

    if not validate_universe_structure(
        stocks
    ):

        return 1

    # ========================================================
    # 6. Market Balance
    # ========================================================

    if not validate_market_balance(
        stocks,
        len(twse),
        len(tpex),
    ):

        return 1

    # ========================================================
    # 7. Payload
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
    # 8. Atomic Write
    # ========================================================

    if not atomic_write(
        payload
    ):

        return 1

    log(
        "✓ Atomic Write PASS"
    )

    # ========================================================
    # 9. Post Write Verify
    # ========================================================

    if not post_write_verify():

        log(
            "❌ Post Write Verify FAIL"
        )

        return 1

    # ========================================================
    # 10. Summary
    # ========================================================

    print_summary(
        stocks
    )

    elapsed = (
        time.time()
        - started
    )

    log(
        f"✓ elapsed："
        f"{elapsed:.1f}s"
    )

    log("")
    log(
        "========================================"
    )
    log(
        "UNIVERSE BUILD SUCCESS"
    )
    log(
        "========================================"
    )

    return 0


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    sys.exit(main())
