#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_universe.py

Universe 四層架構
------------------------------------------------------------
第一層：目前交易標的
第二層：股票 / ETF / ETN / TDR / 其他類型判定
第三層：Status 正確產生與驗證
第四層：名稱 / 市場 / 代號補充

重要原則
------------------------------------------------------------
1. TWSE 官方目前交易資料
2. TPEx 官方目前交易資料
3. TWSE 官方公司 / 證券資料只作補充
4. TPEx 官方公司 / 證券資料只作補充
5. 不把 TWSE ISIN 全量資料直接當 Universe
6. 不使用歷史資料建立目前 Universe
7. 不使用 Yahoo
8. 不寫死股票代號
9. 不寫死股票名稱
10. 不寫死測試股票
11. 不用舊 Universe 湊數量
12. 舊 Universe 最多只能補名稱
13. status 必須由本次 Universe 建立流程產生
14. 不透過移除 status=verify 繞過驗證
15. 只有通過全部驗證才 Atomic Write
16. 寫入後重新讀取驗證
17. stocks 必須是 object/dict
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests


# ============================================================
# Paths
# ============================================================

ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = ROOT / "Data"
UNIVERSE_FILE = DATA_DIR / "universe.json"


# ============================================================
# Network
# ============================================================

TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/120 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}


# ============================================================
# Official endpoints
# ============================================================

TWSE_BASE = "https://openapi.twse.com.tw/v1"
TPEX_BASE = "https://www.tpex.org.tw/openapi/v1"


# ============================================================
# Current market endpoints
# ============================================================

TWSE_CURRENT_ENDPOINTS = [
    "/exchangeReport/STOCK_DAY_ALL",
    "/exchangeReport/FMTQIK",
]

TPEX_CURRENT_ENDPOINTS = [
    "/tpex_mainboard_quotes",
]


# ============================================================
# Supplement endpoints
# ============================================================

TWSE_COMPANY_ENDPOINTS = [
    "/opendata/t187ap03_L",
]

TPEX_COMPANY_ENDPOINTS = [
    "/tpex_securities",
]


# ============================================================
# Constants
# ============================================================

VALID_MARKETS = {
    "TWSE",
    "TPEX",
}

VALID_TYPES = {
    "STOCK",
    "ETF",
    "ETN",
    "TDR",
    "WARRANT",
    "BOND",
}

# Status 是「Universe eligibility」，
# 不是 classification 的 verify flag。
#
# 正確 Universe：
#   ACTIVE
#
# 只有官方目前交易資料進入第一層，
# 才能產生 ACTIVE。
#
# 非目前交易標的不能靠舊 Universe / supplement
# 被重新放回 Universe。
ACTIVE_STATUS = "ACTIVE"


# ============================================================
# Utility
# ============================================================

def clean(value: Any) -> str:
    if value is None:
        return ""

    text = str(value)

    text = (
        text.replace("\ufeff", "")
        .replace("\u3000", " ")
        .strip()
    )

    return text


def normalize_symbol(value: Any) -> str:
    text = clean(value)

    if not text:
        return ""

    text = re.sub(
        r"\.(TW|TWO)$",
        "",
        text,
        flags=re.I,
    )

    text = text.replace(" ", "")

    return text


def normalize_name(value: Any) -> str:
    return clean(value)


def is_valid_symbol(symbol: str) -> bool:
    if not symbol:
        return False

    if len(symbol) > 10:
        return False

    return bool(
        re.fullmatch(
            r"[A-Za-z0-9]+",
            symbol,
        )
    )


def request_json(
    session: requests.Session,
    url: str,
) -> Optional[Any]:

    try:
        response = session.get(
            url,
            headers=HEADERS,
            timeout=TIMEOUT,
        )

        response.raise_for_status()

        if not response.text.strip():
            return None

        return response.json()

    except Exception as exc:
        print(
            f"⚠ API 讀取失敗：{url}"
        )
        print(
            f"  {type(exc).__name__}: {exc}"
        )

        return None


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

    for key in (
        "data",
        "result",
        "aaData",
        "results",
        "records",
    ):

        value = payload.get(key)

        if isinstance(value, list):

            return [
                item
                for item in value
                if isinstance(item, dict)
            ]

    return []


def find_value(
    row: Dict[str, Any],
    keys: Iterable[str],
) -> str:

    normalized: Dict[str, Any] = {}

    for key, value in row.items():

        normalized[
            clean(key)
        ] = value

    # Exact match
    for key in keys:

        if key in normalized:

            value = clean(
                normalized[key]
            )

            if value:
                return value

    # Fuzzy match
    for row_key, value in normalized.items():

        for wanted in keys:

            if wanted in row_key:

                text = clean(value)

                if text:
                    return text

    return ""


def parse_market(
    row: Dict[str, Any],
    default_market: str,
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
        ],
    )

    value_upper = value.upper()

    if "TWSE" in value_upper:
        return "TWSE"

    if "TPEX" in value_upper:
        return "TPEX"

    if "OTC" in value_upper:
        return "TPEX"

    if "上市" in value:
        return "TWSE"

    if "上櫃" in value:
        return "TPEX"

    if "興櫃" in value:
        return "TPEX"

    return default_market


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
                "Code",
                "code",
                "Symbol",
                "symbol",
                "securityCode",
                "SecurityCode",
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
                "Company Name",
                "CompanyName",
                "name",
                "Name",
                "SecurityName",
                "securityName",
            ],
        )
    )


# ============================================================
# Instrument classification
# ============================================================

ETF_KEYWORDS = (
    "ETF",
    "指數股票型",
    "指數型基金",
    "債券型ETF",
    "股票型ETF",
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
)

BOND_KEYWORDS = (
    "債券",
    "公司債",
    "政府債",
    "地方政府債",
)

PREFERRED_KEYWORDS = (
    "特別股",
    "甲特",
    "乙特",
    "丙特",
    "特",
)


def contains_any(
    text: str,
    keywords: Iterable[str],
) -> bool:

    upper = text.upper()

    return any(
        keyword.upper() in upper
        for keyword in keywords
    )


def classify_instrument(
    row: Dict[str, Any],
    name: str,
) -> Tuple[str, str]:

    """
    回傳：
        type
        instrument_type
    """

    raw_text = " ".join(
        clean(value)
        for value in row.values()
    )

    combined = (
        f"{name} {raw_text}"
    )

    explicit_type = find_value(
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
        ],
    ).upper()

    if (
        "ETF" in explicit_type
        or contains_any(
            combined,
            ETF_KEYWORDS,
        )
    ):
        return "ETF", "ETF"

    if (
        "ETN" in explicit_type
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
        PREFERRED_KEYWORDS,
    ):
        return "STOCK", "PREFERRED_STOCK"

    return "STOCK", "COMMON_STOCK"


# ============================================================
# Layer 1
# Current trading universe
# ============================================================

def collect_twse_current(
    session: requests.Session,
) -> Dict[str, Dict[str, Any]]:

    print("")
    print("=" * 72)
    print("TWSE CURRENT TRADING UNIVERSE")
    print("=" * 72)

    candidates: Dict[str, Dict[str, Any]] = {}

    for endpoint in TWSE_CURRENT_ENDPOINTS:

        url = TWSE_BASE + endpoint

        payload = request_json(
            session,
            url,
        )

        rows = rows_from_payload(
            payload
        )

        if not rows:
            continue

        accepted = 0

        for row in rows:

            symbol = parse_symbol(row)
            name = parse_name(row)

            if not is_valid_symbol(symbol):
                continue

            if not name:
                continue

            market = parse_market(
                row,
                "TWSE",
            )

            candidates[symbol] = {
                "symbol": symbol,
                "name": name,
                "market": market,
                "raw": row,
                "source": "TWSE_CURRENT",
            }

            accepted += 1

        if accepted:

            print(
                f"✓ {endpoint}："
                f"{accepted} 檔"
            )

            break

    print(
        f"TWSE current candidates："
        f"{len(candidates)}"
    )

    return candidates


def collect_tpex_current(
    session: requests.Session,
) -> Dict[str, Dict[str, Any]]:

    print("")
    print("=" * 72)
    print("TPEx CURRENT TRADING UNIVERSE")
    print("=" * 72)

    candidates: Dict[str, Dict[str, Any]] = {}

    for endpoint in TPEX_CURRENT_ENDPOINTS:

        url = TPEX_BASE + endpoint

        print(
            f"嘗試：{url}"
        )

        payload = request_json(
            session,
            url,
        )

        rows = rows_from_payload(
            payload
        )

        if not rows:
            continue

        accepted = 0

        for row in rows:

            symbol = parse_symbol(row)
            name = parse_name(row)

            if not is_valid_symbol(symbol):
                continue

            if not name:
                continue

            market = parse_market(
                row,
                "TPEX",
            )

            candidates[symbol] = {
                "symbol": symbol,
                "name": name,
                "market": market,
                "raw": row,
                "source": "TPEX_CURRENT",
            }

            accepted += 1

        if accepted:

            print(
                f"✓ {endpoint}："
                f"{accepted} 檔"
            )

            break

    print(
        f"TPEx current candidates："
        f"{len(candidates)}"
    )

    return candidates


# ============================================================
# TPEx Emerging
# ============================================================

def collect_tpex_emerging(
    session: requests.Session,
) -> Dict[str, Dict[str, Any]]:

    print("")
    print("=" * 72)
    print("TPEx EMERGING STOCK")
    print("=" * 72)

    candidates: Dict[str, Dict[str, Any]] = {}

    possible_endpoints = [
        "/tpex_esb_quotes",
        "/tpex_esb_today",
        "/tpex_emerging_quotes",
    ]

    for endpoint in possible_endpoints:

        url = TPEX_BASE + endpoint

        payload = request_json(
            session,
            url,
        )

        rows = rows_from_payload(
            payload
        )

        if not rows:
            continue

        accepted = 0

        for row in rows:

            symbol = parse_symbol(row)
            name = parse_name(row)

            if not is_valid_symbol(symbol):
                continue

            if not name:
                continue

            candidates[symbol] = {
                "symbol": symbol,
                "name": name,
                "market": "TPEX",
                "raw": row,
                "source": "TPEX_EMERGING",
            }

            accepted += 1

        if accepted:

            print(
                f"✓ {endpoint}："
                f"{accepted} 檔"
            )

            break

    print(
        f"TPEx emerging candidates："
        f"{len(candidates)}"
    )

    return candidates


# ============================================================
# Official supplement
# ============================================================

def collect_twse_company_data(
    session: requests.Session,
) -> Dict[str, Dict[str, Any]]:

    print("")
    print("=" * 72)
    print("TWSE OFFICIAL SUPPLEMENT")
    print("=" * 72)

    result: Dict[str, Dict[str, Any]] = {}

    for endpoint in TWSE_COMPANY_ENDPOINTS:

        payload = request_json(
            session,
            TWSE_BASE + endpoint,
        )

        rows = rows_from_payload(
            payload
        )

        if not rows:
            continue

        for row in rows:

            symbol = parse_symbol(row)

            if not is_valid_symbol(symbol):
                continue

            name = parse_name(row)

            result[symbol] = {
                "symbol": symbol,
                "name": name,
                "market": parse_market(
                    row,
                    "TWSE",
                ),
                "raw": row,
            }

        if result:
            break

    print(
        f"TWSE official supplement："
        f"{len(result)}"
    )

    return result


def collect_tpex_company_data(
    session: requests.Session,
) -> Dict[str, Dict[str, Any]]:

    print("")
    print("=" * 72)
    print("TPEx OFFICIAL SUPPLEMENT")
    print("=" * 72)

    result: Dict[str, Dict[str, Any]] = {}

    for endpoint in TPEX_COMPANY_ENDPOINTS:

        payload = request_json(
            session,
            TPEX_BASE + endpoint,
        )

        rows = rows_from_payload(
            payload
        )

        if not rows:
            continue

        for row in rows:

            symbol = parse_symbol(row)

            if not is_valid_symbol(symbol):
                continue

            name = parse_name(row)

            result[symbol] = {
                "symbol": symbol,
                "name": name,
                "market": parse_market(
                    row,
                    "TPEX",
                ),
                "raw": row,
            }

        if result:
            break

    print(
        f"TPEx official supplement："
        f"{len(result)}"
    )

    return result


# ============================================================
# Existing Universe
# ============================================================

def load_old_universe() -> Dict[str, Dict[str, Any]]:

    if not UNIVERSE_FILE.exists():

        print(
            "✓ 舊 Universe：不存在"
        )

        return {}

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8-sig",
        ) as f:

            data = json.load(f)

    except Exception as exc:

        print(
            f"⚠ 舊 Universe 讀取失敗："
            f"{exc}"
        )

        return {}

    if not isinstance(data, dict):
        return {}

    stocks = data.get(
        "stocks"
    )

    if not isinstance(stocks, dict):
        return {}

    result: Dict[str, Dict[str, Any]] = {}

    for symbol, item in stocks.items():

        if not isinstance(item, dict):
            continue

        normalized = normalize_symbol(
            symbol
        )

        if not is_valid_symbol(
            normalized
        ):
            continue

        result[normalized] = item

    print(
        f"✓ 舊 Universe 名稱快取："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# Layer 3
# Status
# ============================================================

def generate_status(
    candidate: Dict[str, Any],
    item_type: str,
    instrument_type: str,
) -> str:
    """
    第三層 Status。

    只有「第一層目前交易資料」進來的標的，
    才有資格產生 ACTIVE。

    舊 Universe：
        不得產生 status
        不得恢復已不存在的標的

    Official supplement：
        只能補名稱 / 市場 / 代號資訊
        不得單獨建立 Universe

    因此 status 不再依賴：
        status == verify
        或舊 Universe status
        或 classification status

    00400A 是否進入 Universe，
    由第一層目前交易資料決定。
    """

    symbol = normalize_symbol(
        candidate.get("symbol")
    )

    if not is_valid_symbol(symbol):
        return ""

    market = clean(
        candidate.get("market")
    ).upper()

    if market not in VALID_MARKETS:
        return ""

    if item_type not in VALID_TYPES:
        return ""

    if not instrument_type:
        return ""

    source = clean(
        candidate.get("source")
    )

    if not source:
        return ""

    # 只有目前交易來源才可產生 ACTIVE
    if source not in {
        "TWSE_CURRENT",
        "TPEX_CURRENT",
        "TPEX_EMERGING",
    }:
        return ""

    return ACTIVE_STATUS


# ============================================================
# Build instrument
# ============================================================

def build_instrument(
    candidate: Dict[str, Any],
    supplement: Optional[Dict[str, Any]],
    old_item: Optional[Dict[str, Any]],
) -> Dict[str, Any]:

    symbol = normalize_symbol(
        candidate.get("symbol")
    )

    # --------------------------------------------------------
    # Name
    #
    # 優先：
    # 1. 目前交易資料
    # 2. 官方 supplement
    # 3. 舊 Universe 名稱快取
    #
    # 舊 Universe 只能補名稱。
    # --------------------------------------------------------

    name = normalize_name(
        candidate.get("name")
    )

    if not name and supplement:

        name = normalize_name(
            supplement.get("name")
        )

    if not name and old_item:

        name = normalize_name(
            old_item.get("name")
        )

    if not name:
        name = symbol

    raw = candidate.get(
        "raw",
        {},
    )

    if not isinstance(raw, dict):
        raw = {}

    # --------------------------------------------------------
    # Layer 2
    # --------------------------------------------------------

    item_type, instrument_type = (
        classify_instrument(
            raw,
            name,
        )
    )

    # --------------------------------------------------------
    # Market
    #
    # 市場來源優先目前交易資料。
    # supplement 只能補充。
    # --------------------------------------------------------

    market = clean(
        candidate.get("market")
    ).upper()

    if (
        market not in VALID_MARKETS
        and supplement
    ):

        market = clean(
            supplement.get("market")
        ).upper()

    if market not in VALID_MARKETS:
        market = ""

    if market == "TWSE":
        full_symbol = (
            f"{symbol}.TW"
        )

    elif market == "TPEX":
        full_symbol = (
            f"{symbol}.TWO"
        )

    else:
        full_symbol = ""

    # --------------------------------------------------------
    # Layer 3
    # --------------------------------------------------------

    status = generate_status(
        candidate=candidate,
        item_type=item_type,
        instrument_type=instrument_type,
    )

    return {
        "symbol": symbol,
        "full_symbol": full_symbol,
        "name": name,
        "market": market,
        "type": item_type,
        "instrument_type": instrument_type,
        "status": status,
        "source": candidate.get(
            "source",
            "",
        ),
    }


# ============================================================
# Status validation
# ============================================================

def validate_status(
    item: Dict[str, Any],
) -> bool:

    symbol = clean(
        item.get("symbol")
    )

    name = clean(
        item.get("name")
    )

    market = clean(
        item.get("market")
    ).upper()

    item_type = clean(
        item.get("type")
    ).upper()

    instrument_type = clean(
        item.get("instrument_type")
    ).upper()

    status = clean(
        item.get("status")
    ).upper()

    source = clean(
        item.get("source")
    )

    if not is_valid_symbol(symbol):
        return False

    if not name:
        return False

    if market not in VALID_MARKETS:
        return False

    if item_type not in VALID_TYPES:
        return False

    if not instrument_type:
        return False

    if status != ACTIVE_STATUS:
        return False

    if source not in {
        "TWSE_CURRENT",
        "TPEX_CURRENT",
        "TPEX_EMERGING",
    }:
        return False

    return True


# ============================================================
# Layer 4 / Build Universe
# ============================================================

def build_universe(
    twse_current: Dict[str, Dict[str, Any]],
    tpex_current: Dict[str, Dict[str, Any]],
    twse_company: Dict[str, Dict[str, Any]],
    tpex_company: Dict[str, Dict[str, Any]],
    tpex_emerging: Dict[str, Dict[str, Any]],
    old_universe: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    print("")
    print("=" * 72)
    print("BUILD OFFICIAL UNIVERSE")
    print("=" * 72)

    candidates: Dict[str, Dict[str, Any]] = {}

    # --------------------------------------------------------
    # Layer 1
    #
    # Universe 的唯一建立來源：
    # 目前交易資料
    #
    # 舊 Universe 不得加入候選
    # supplement 不得加入候選
    # --------------------------------------------------------

    for source in (
        twse_current,
        tpex_current,
        tpex_emerging,
    ):

        for symbol, item in source.items():

            if symbol not in candidates:

                candidates[symbol] = item

    print(
        f"第一層目前交易候選："
        f"{len(candidates)}"
    )

    # --------------------------------------------------------
    # Layer 4 supplement
    #
    # supplement 只能補資料，
    # 不可以新增 Universe。
    # --------------------------------------------------------

    result: Dict[str, Dict[str, Any]] = {}

    supplemented = 0
    rejected = 0

    for symbol, candidate in candidates.items():

        if candidate.get("market") == "TWSE":

            supplement = (
                twse_company.get(symbol)
            )

        else:

            supplement = (
                tpex_company.get(symbol)
            )

        # 舊 Universe 只作名稱快取
        old_item = old_universe.get(
            symbol
        )

        item = build_instrument(
            candidate=candidate,
            supplement=supplement,
            old_item=old_item,
        )

        if not validate_status(item):

            rejected += 1

            continue

        result[symbol] = item

        if supplement:
            supplemented += 1

    print(
        f"第四層官方資料補充："
        f"{supplemented}"
    )

    print(
        f"第三層 ACTIVE："
        f"{len(result)}"
    )

    print(
        f"第三層 rejected："
        f"{rejected}"
    )

    print(
        f"官方 Universe："
        f"{len(result)}"
    )

    return result


# ============================================================
# Strict validation
# ============================================================

def validate_universe(
    stocks: Dict[str, Dict[str, Any]],
) -> Dict[str, int]:

    print("")
    print("=" * 72)
    print("UNIVERSE STRICT VALIDATION")
    print("=" * 72)

    if not isinstance(stocks, dict):

        raise RuntimeError(
            "stocks 必須是 object/dict"
        )

    universe_count = len(stocks)

    actual_stock_count = 0
    actual_etf_count = 0
    actual_etn_count = 0
    actual_tdr_count = 0
    actual_warrant_count = 0
    actual_bond_count = 0

    symbols = set()
    full_symbols = set()

    twse_count = 0
    tpex_count = 0

    status_counter: Dict[str, int] = {}

    for key, item in stocks.items():

        if not isinstance(item, dict):

            raise RuntimeError(
                f"{key} 不是 object"
            )

        symbol = clean(
            item.get("symbol")
        )

        full_symbol = clean(
            item.get("full_symbol")
        )

        name = clean(
            item.get("name")
        )

        market = clean(
            item.get("market")
        ).upper()

        item_type = clean(
            item.get("type")
        ).upper()

        instrument_type = clean(
            item.get(
                "instrument_type"
            )
        ).upper()

        status = clean(
            item.get("status")
        ).upper()

        source = clean(
            item.get("source")
        )

        if key != symbol:

            raise RuntimeError(
                f"key != symbol："
                f"{key} != {symbol}"
            )

        if not is_valid_symbol(symbol):

            raise RuntimeError(
                f"無效 symbol："
                f"{symbol}"
            )

        if not full_symbol:

            raise RuntimeError(
                f"{symbol} 缺少 full_symbol"
            )

        if not name:

            raise RuntimeError(
                f"{symbol} 缺少 name"
            )

        if market not in VALID_MARKETS:

            raise RuntimeError(
                f"{symbol} market 無效："
                f"{market}"
            )

        if item_type not in VALID_TYPES:

            raise RuntimeError(
                f"{symbol} type 無效："
                f"{item_type}"
            )

        if not instrument_type:

            raise RuntimeError(
                f"{symbol} 缺少 instrument_type"
            )

        if status != ACTIVE_STATUS:

            raise RuntimeError(
                f"{symbol} status 無效："
                f"{status}"
            )

        if source not in {
            "TWSE_CURRENT",
            "TPEX_CURRENT",
            "TPEX_EMERGING",
        }:

            raise RuntimeError(
                f"{symbol} source 非目前交易來源："
                f"{source}"
            )

        if symbol in symbols:

            raise RuntimeError(
                f"symbol duplicate："
                f"{symbol}"
            )

        if full_symbol in full_symbols:

            raise RuntimeError(
                f"full_symbol duplicate："
                f"{full_symbol}"
            )

        symbols.add(symbol)
        full_symbols.add(full_symbol)

        status_counter[status] = (
            status_counter.get(
                status,
                0,
            )
            + 1
        )

        if market == "TWSE":
            twse_count += 1
        else:
            tpex_count += 1

        if item_type == "ETF":

            actual_etf_count += 1

        elif item_type == "ETN":

            actual_etn_count += 1

        elif item_type == "TDR":

            actual_tdr_count += 1

        elif item_type == "WARRANT":

            actual_warrant_count += 1

        elif item_type == "BOND":

            actual_bond_count += 1

        else:

            actual_stock_count += 1

    classification_total = (
        actual_stock_count
        + actual_etf_count
        + actual_etn_count
        + actual_tdr_count
        + actual_warrant_count
        + actual_bond_count
    )

    print(
        f"universe_count = "
        f"{universe_count}"
    )

    print(
        f"stock_count = "
        f"{actual_stock_count}"
    )

    print(
        f"etf_count = "
        f"{actual_etf_count}"
    )

    print(
        f"etn_count = "
        f"{actual_etn_count}"
    )

    print(
        f"tdr_count = "
        f"{actual_tdr_count}"
    )

    print(
        f"warrant_count = "
        f"{actual_warrant_count}"
    )

    print(
        f"bond_count = "
        f"{actual_bond_count}"
    )

    print(
        f"TWSE = {twse_count}"
    )

    print(
        f"TPEX = {tpex_count}"
    )

    print(
        "Status："
        f"{status_counter}"
    )

    if classification_total != universe_count:

        raise RuntimeError(
            "分類數量 != Universe"
        )

    if status_counter.get(
        ACTIVE_STATUS,
        0,
    ) != universe_count:

        raise RuntimeError(
            "所有 Universe 標的必須為 "
            f"{ACTIVE_STATUS}"
        )

    print(
        "✓ universe_count == "
        "分類總數"
    )

    print(
        "✓ 所有 Universe status = ACTIVE"
    )

    print(
        "✓ symbol uniqueness"
    )

    print(
        "✓ full_symbol uniqueness"
    )

    print(
        "✓ Market / Type / Name validation"
    )

    return {
        "universe_count": universe_count,
        "stock_count": actual_stock_count,
        "etf_count": actual_etf_count,
        "etn_count": actual_etn_count,
        "tdr_count": actual_tdr_count,
        "warrant_count": actual_warrant_count,
        "bond_count": actual_bond_count,
        "twse_count": twse_count,
        "tpex_count": tpex_count,
        "active_count": status_counter.get(
            ACTIVE_STATUS,
            0,
        ),
    }


# ============================================================
# Output
# ============================================================

def create_output(
    stocks: Dict[str, Dict[str, Any]],
    statistics: Dict[str, int],
) -> Dict[str, Any]:

    return {
        "generated_at": time.strftime(
            "%Y-%m-%dT%H:%M:%S+08:00",
            time.localtime(),
        ),

        "source": [
            "TWSE_CURRENT",
            "TPEX_CURRENT",
            "TPEX_EMERGING",
            "TWSE_OFFICIAL_SUPPLEMENT",
            "TPEX_OFFICIAL_SUPPLEMENT",
        ],

        "rules": {
            "dynamic_symbols": True,
            "dynamic_names": True,
            "dynamic_market": True,
            "dynamic_type": True,
            "dynamic_status": True,
            "current_trading_only": True,
            "historical_data_as_universe": False,
            "yahoo": False,
            "hardcoded_stocks": False,
            "hardcoded_names": False,
            "hardcoded_test_symbols": False,
            "old_universe_for_count": False,
            "old_universe_for_status": False,
            "status_from_current_source": True,
        },

        "universe_count": statistics[
            "universe_count"
        ],

        "stock_count": statistics[
            "stock_count"
        ],

        "etf_count": statistics[
            "etf_count"
        ],

        "etn_count": statistics[
            "etn_count"
        ],

        "tdr_count": statistics[
            "tdr_count"
        ],

        "warrant_count": statistics[
            "warrant_count"
        ],

        "bond_count": statistics[
            "bond_count"
        ],

        "twse_count": statistics[
            "twse_count"
        ],

        "tpex_count": statistics[
            "tpex_count"
        ],

        "active_count": statistics[
            "active_count"
        ],

        "stocks": stocks,
    }


# ============================================================
# Atomic Write
# ============================================================

def atomic_write(
    data: Dict[str, Any],
) -> None:

    print("")
    print("=" * 72)
    print("ATOMIC WRITE")
    print("=" * 72)

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, temp_path = tempfile.mkstemp(
        prefix=".universe.",
        suffix=".json",
        dir=str(DATA_DIR),
    )

    try:

        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2,
            )

            f.write("\n")

            f.flush()

            os.fsync(
                f.fileno()
            )

        os.replace(
            temp_path,
            UNIVERSE_FILE,
        )

    except Exception:

        try:
            os.unlink(
                temp_path
            )
        except OSError:
            pass

        raise


# ============================================================
# Post-write validation
# ============================================================

def reload_and_validate() -> Dict[str, Any]:

    print("")
    print("=" * 72)
    print("POST-WRITE VALIDATION")
    print("=" * 72)

    with UNIVERSE_FILE.open(
        "r",
        encoding="utf-8-sig",
    ) as f:

        data = json.load(f)

    if not isinstance(data, dict):

        raise RuntimeError(
            "universe.json 根節點不是 object"
        )

    stocks = data.get(
        "stocks"
    )

    if not isinstance(stocks, dict):

        raise RuntimeError(
            "universe.json 的 stocks "
            "不是 object"
        )

    universe_count = data.get(
        "universe_count"
    )

    if universe_count != len(stocks):

        raise RuntimeError(
            "寫入後 Universe 數量不一致："
            f"{universe_count} != "
            f"{len(stocks)}"
        )

    for symbol, item in stocks.items():

        if not isinstance(item, dict):

            raise RuntimeError(
                f"{symbol} item 不是 object"
            )

        if clean(
            item.get("status")
        ).upper() != ACTIVE_STATUS:

            raise RuntimeError(
                f"{symbol} 寫入後 status 不正確："
                f"{item.get('status')}"
            )

    print(
        "✓ universe.json 可正常解析"
    )

    print(
        f"✓ stocks object："
        f"{len(stocks)}"
    )

    print(
        f"✓ universe_count："
        f"{universe_count}"
    )

    print(
        "✓ 所有 status = ACTIVE"
    )

    return data


# ============================================================
# Main
# ============================================================

def main() -> int:

    start_time = time.time()

    print(
        "台股 AI 選股系統 "
        "build_universe.py"
    )

    print(
        "=" * 60
    )

    print(
        "Universe 四層架構"
    )

    print(
        "✓ 第一層：目前交易標的"
    )

    print(
        "✓ 第二層：股票 / ETF / ETN / TDR / 其他類型判定"
    )

    print(
        "✓ 第三層：Status 正確產生"
    )

    print(
        "✓ 第四層：名稱 / 市場 / 代號補充"
    )

    print(
        "✓ Status 不靠移除 verify 繞過"
    )

    print(
        "✓ 不寫死股票"
    )

    print(
        "✓ 不寫死股票名稱"
    )

    print(
        "✓ 不寫死測試標的"
    )

    print(
        "✓ 不用歷史資料湊 Universe"
    )

    print(
        "✓ 不使用 Yahoo"
    )

    print(
        "✓ 舊 Universe 只能補名稱"
    )

    print(
        "✓ 舊 Universe 不得建立 status"
    )

    print(
        "✓ Atomic Write"
    )

    print(
        "✓ 寫入後重新驗證"
    )

    print(
        "=" * 60
    )

    session = requests.Session()

    # --------------------------------------------------------
    # 舊 Universe
    # --------------------------------------------------------

    old_universe = (
        load_old_universe()
    )

    # --------------------------------------------------------
    # Layer 1
    # --------------------------------------------------------

    twse_current = (
        collect_twse_current(
            session
        )
    )

    tpex_current = (
        collect_tpex_current(
            session
        )
    )

    tpex_emerging = (
        collect_tpex_emerging(
            session
        )
    )

    # --------------------------------------------------------
    # 官方目前交易資料失敗：
    # 不允許用舊 Universe 湊數量。
    # --------------------------------------------------------

    if not twse_current:

        print("")
        print(
            "❌ TWSE 目前交易 Universe "
            "為 0"
        )

        print(
            "❌ 不使用舊 Universe 湊數量"
        )

        print(
            "❌ 停止寫入"
        )

        return 1

    if not tpex_current:

        print("")
        print(
            "❌ TPEx 目前交易 Universe "
            "為 0"
        )

        print(
            "❌ 不使用舊 Universe 湊數量"
        )

        print(
            "❌ 停止寫入"
        )

        return 1

    # --------------------------------------------------------
    # Official supplement
    # --------------------------------------------------------

    twse_company = (
        collect_twse_company_data(
            session
        )
    )

    tpex_company = (
        collect_tpex_company_data(
            session
        )
    )

    # --------------------------------------------------------
    # Build
    # --------------------------------------------------------

    stocks = build_universe(
        twse_current=twse_current,
        tpex_current=tpex_current,
        twse_company=twse_company,
        tpex_company=tpex_company,
        tpex_emerging=tpex_emerging,
        old_universe=old_universe,
    )

    # --------------------------------------------------------
    # Must contain data
    # --------------------------------------------------------

    if not stocks:

        print("")
        print(
            "❌ Universe 為 0"
        )

        print(
            "❌ 停止寫入"
        )

        return 1

    # --------------------------------------------------------
    # Strict validation
    # --------------------------------------------------------

    try:

        statistics = validate_universe(
            stocks
        )

    except Exception as exc:

        print("")
        print(
            "=" * 60
        )

        print(
            "UNIVERSE BUILD FAIL"
        )

        print(
            "=" * 60
        )

        print(
            f"❌ 驗證失敗：{exc}"
        )

        return 1

    # --------------------------------------------------------
    # Output
    # --------------------------------------------------------

    output = create_output(
        stocks,
        statistics,
    )

    # --------------------------------------------------------
    # Atomic Write
    # --------------------------------------------------------

    try:

        atomic_write(
            output
        )

    except Exception as exc:

        print("")
        print(
            "❌ Atomic Write 失敗："
            f"{exc}"
        )

        return 1

    # --------------------------------------------------------
    # Reload
    # --------------------------------------------------------

    try:

        reload_and_validate()

    except Exception as exc:

        print("")
        print(
            "❌ 寫入後驗證失敗："
            f"{exc}"
        )

        return 1

    elapsed = (
        time.time()
        - start_time
    )

    # --------------------------------------------------------
    # PASS
    # --------------------------------------------------------

    print("")
    print("=" * 60)
    print("UNIVERSE BUILD PASS")
    print("=" * 60)

    print(
        f"✓ Universe："
        f"{statistics['universe_count']}"
    )

    print(
        f"✓ Stock："
        f"{statistics['stock_count']}"
    )

    print(
        f"✓ ETF："
        f"{statistics['etf_count']}"
    )

    print(
        f"✓ ETN："
        f"{statistics['etn_count']}"
    )

    print(
        f"✓ TDR："
        f"{statistics['tdr_count']}"
    )

    print(
        f"✓ Warrant："
        f"{statistics['warrant_count']}"
    )

    print(
        f"✓ Bond："
        f"{statistics['bond_count']}"
    )

    print(
        f"✓ TWSE："
        f"{statistics['twse_count']}"
    )

    print(
        f"✓ TPEX："
        f"{statistics['tpex_count']}"
    )

    print(
        f"✓ ACTIVE："
        f"{statistics['active_count']}"
    )

    print(
        "✓ 完全動態 Universe"
    )

    print(
        "✓ Status 由目前交易來源產生"
    )

    print(
        "✓ 無固定股票清單"
    )

    print(
        "✓ 無固定股票名稱"
    )

    print(
        "✓ 無固定測試標的"
    )

    print(
        "✓ 不使用歷史資料湊數"
    )

    print(
        "✓ 無 Yahoo"
    )

    print(
        "✓ Atomic Write"
    )

    print(
        "✓ 寫入後重新驗證"
    )

    print(
        f"✓ 完成，耗時："
        f"{elapsed:.1f} 秒"
    )

    return 0


if __name__ == "__main__":
    sys.exit(
        main()
    )
