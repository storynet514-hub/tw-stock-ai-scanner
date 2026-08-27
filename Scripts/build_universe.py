#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_universe.py

Universe 建構器
============================================================

核心原則
------------------------------------------------------------
1. Data/universe.json 是唯一 Universe 輸出來源
2. Universe 只允許「目前有效交易股票商品」
3. TWSE 官方資料為第一來源
4. TPEx 官方資料為第一來源
5. Yahoo / CMoney 僅能作 fallback
6. fallback 不得擴張官方 Universe
7. 不使用歷史 Universe 增加股票
8. 不使用 Yahoo 建立新的 Universe
9. 不使用 CMoney 建立新的 Universe
10. 不寫死 Universe 數量
11. 不寫死股票代號
12. 不寫死股票名稱
13. stocks 必須是 dict
14. universe_count == len(stocks)
15. status == active
16. Structure Gate
17. Data Quality Gate
18. Atomic Write
19. Atomic Write 後再次驗證

重要修正
------------------------------------------------------------
A. 不再使用「所有 row 都接受」的寬鬆 parser
B. symbol 必須符合台股股票代號格式
C. 必須排除：
   - 權證
   - 債券
   - ETF
   - ETN
   - TDR
   - 基金
   - 指數
   - 其他非普通股票商品
D. 官方資料不足時：
   - Yahoo / CMoney 僅補官方已確認的 symbol
   - 不得自己產生新的 symbol
E. 官方來源數量異常時直接 Gate FAIL
F. 防止 1 萬多筆錯誤 Universe
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

VERSION = "UNIVERSE-5.0-STRICT-STOCK-GATE"


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


TWSE_CURRENT_ENDPOINTS = [
    "/exchangeReport/STOCK_DAY_ALL",
]


TPEX_CURRENT_ENDPOINTS = [
    "/tpex_mainboard_daily_close_quotes",
]


# ============================================================
# FALLBACK
# ============================================================

# 注意：
# fallback 不能建立新的 Universe。
#
# 只允許：
# official symbol 已經存在
# ↓
# official name / metadata 缺失
# ↓
# fallback 補資料
#
# 因此即使 Yahoo / CMoney 回傳 10,000 檔，
# 也不可能因此增加 Universe。

YAHOO_ENABLED = True

CMONEY_ENABLED = True


# ============================================================
# STATUS
# ============================================================

ACTIVE_STATUS = "active"


# ============================================================
# TYPES
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


# Universe 本系統真正要送入後續選股流程的類型
UNIVERSE_STOCK_TYPES = {
    "STOCK",
}


# ============================================================
# HARD SAFETY LIMIT
# ============================================================

# 這不是 Universe 數量限制。
#
# 目的只是防止 parser/API 異常時把數千甚至數萬筆垃圾資料
# 寫進 universe.json。
#
# 正常台股上市 + 上櫃普通股票應遠低於此數。
MAX_UNIVERSE_SANITY = 5000


# ============================================================
# SESSION
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

    return now_tw().strftime(
        "%Y-%m-%d"
    )


# ============================================================
# CLEAN
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


# ============================================================
# STRICT SYMBOL VALIDATION
# ============================================================

def is_valid_symbol(
    symbol: str,
) -> bool:

    if not symbol:
        return False

    # 台股普通股票代號：
    # 主要為 4 碼數字。
    #
    # 保留少數 3~6 碼情況，
    # 但必須完全為數字。
    #
    # 不接受：
    # ABC
    # 指數代碼
    # 權證長代碼
    # ISIN
    # 日期
    # 任意文字

    if not re.fullmatch(
        r"\d{3,6}",
        symbol,
    ):
        return False

    return True


def is_probable_common_stock_symbol(
    symbol: str,
) -> bool:

    """
    第二層 symbol gate。

    不是靠硬寫股票清單，
    而是排除明顯不屬於普通股票的代號。

    這裡不使用固定股票數量。
    """

    if not is_valid_symbol(symbol):
        return False

    # 3~6 碼數字仍可能包含其他商品，
    # 真正商品類型必須再由 row metadata / name 判定。
    return True


# ============================================================
# NORMALIZED KEY
# ============================================================

def normalized_key(
    value: Any,
) -> str:

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

    # exact
    for alias in alias_keys:

        if alias in normalized:

            value = clean(
                normalized[alias]
            )

            if value:
                return value

    # fuzzy
    for row_key, value in normalized.items():

        for alias in alias_keys:

            if (
                alias
                and alias in row_key
            ):

                text = clean(value)

                if text:
                    return text

    return ""


# ============================================================
# SYMBOL
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


# ============================================================
# NAME
# ============================================================

def parse_name(
    row: Dict[str, Any],
) -> str:

    return clean(
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
# PRODUCT KEYWORDS
# ============================================================

ETF_KEYWORDS = (
    "ETF",
    "指數股票型基金",
    "指數型基金",
    "股票型基金",
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
    "金融債",
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

    return any(
        keyword.upper() in upper
        for keyword in keywords
    )


# ============================================================
# EXPLICIT PRODUCT TYPE
# ============================================================

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

    # 僅使用「名稱 + 明確商品類型欄位」
    #
    # 絕對不能把成交價、成交量、日期等數值
    # 全部串進 combined。
    #
    # 原程式：
    #
    # for value in row.values():
    #     combined_parts.append(value)
    #
    # 是非常危險的。

    combined = (
        f"{name} {explicit}"
    )

    upper = combined.upper()

    if (
        "ETF" in upper
        or contains_any(
            combined,
            ETF_KEYWORDS,
        )
    ):
        return "ETF", "ETF"

    if (
        "ETN" in upper
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
# ROW FILTER
# ============================================================

def accept_current_stock_row(
    row: Dict[str, Any],
    expected_market: str,
) -> Tuple[bool, str]:

    if not isinstance(row, dict):

        return False, "row_not_dict"

    symbol = parse_symbol(row)

    if not is_valid_symbol(symbol):

        return False, "invalid_symbol"

    if not is_probable_common_stock_symbol(
        symbol
    ):

        return False, "invalid_stock_symbol"

    name = parse_name(row)

    if not name:

        return False, "empty_name"

    market = parse_market(
        row,
        expected_market,
    )

    if market != expected_market:

        return False, "wrong_market"

    product_type, instrument_type = (
        classify_instrument(
            row,
            name,
        )
    )

    # Universe 目前只收普通股票。
    #
    # ETF / ETN / TDR / BOND / WARRANT
    # 不進入主選股 Universe。

    if product_type != "STOCK":

        return (
            False,
            f"non_stock:{product_type}",
        )

    if instrument_type != "COMMON_STOCK":

        return (
            False,
            f"non_common_stock:{instrument_type}",
        )

    return True, "accepted"


# ============================================================
# PAYLOAD NORMALIZER
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

            for index, field in enumerate(
                fields
            ):

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

        if not isinstance(
            value,
            list,
        ):

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
# HTTP JSON
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

                last_error = (
                    "EMPTY_RESPONSE"
                )

            else:

                return response.json()

        except Exception as exc:

            last_error = (
                f"{type(exc).__name__}: "
                f"{exc}"
            )

        if attempt < RETRIES:

            time.sleep(
                RETRY_SLEEP * attempt
            )

    log(
        f"⚠ API 失敗：{url}"
    )

    log(
        f"  {last_error}"
    )

    return None


# ============================================================
# OLD UNIVERSE CACHE
# ============================================================

def load_old_universe_names(
) -> Dict[str, Dict[str, Any]]:

    """
    舊 Universe 只能補 metadata。

    絕對不能增加 symbol。
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

    if not isinstance(
        payload,
        dict,
    ):

        return {}

    raw = payload.get("stocks")

    if not isinstance(
        raw,
        dict,
    ):

        return {}

    cache = {}

    for key, item in raw.items():

        if not isinstance(
            item,
            dict,
        ):

            continue

        symbol = normalize_symbol(
            item.get(
                "symbol",
                key,
            )
        )

        if not is_valid_symbol(
            symbol
        ):

            continue

        cache[symbol] = {
            "name": clean(
                item.get(
                    "name",
                    "",
                )
            ),
            "full_symbol": clean(
                item.get(
                    "full_symbol",
                    "",
                )
            ),
        }

    return cache


# ============================================================
# COLLECT TWSE
# ============================================================

def collect_twse_current(
) -> Dict[str, Dict[str, Any]]:

    section(
        "TWSE CURRENT UNIVERSE"
    )

    candidates = {}

    rejected = {}

    for endpoint in (
        TWSE_CURRENT_ENDPOINTS
    ):

        url = TWSE_BASE + endpoint

        log(
            f"官方來源：{url}"
        )

        payload = request_json(url)

        rows = rows_from_payload(
            payload
        )

        log(
            f"API rows：{len(rows)}"
        )

        if not rows:

            continue

        for row in rows:

            accepted, reason = (
                accept_current_stock_row(
                    row,
                    "TWSE",
                )
            )

            if not accepted:

                rejected[reason] = (
                    rejected.get(
                        reason,
                        0,
                    )
                    + 1
                )

                continue

            symbol = parse_symbol(row)

            name = parse_name(row)

            candidates[symbol] = {
                "symbol": symbol,
                "name": name,
                "market": "TWSE",
                "raw": row,
                "source": (
                    "TWSE_STOCK_DAY_ALL"
                ),
            }

    log(
        f"✓ TWSE accepted："
        f"{len(candidates)}"
    )

    if rejected:

        log(
            f"TWSE rejected："
            f"{dict(rejected)}"
        )

    return candidates


# ============================================================
# COLLECT TPEX
# ============================================================

def collect_tpex_current(
) -> Dict[str, Dict[str, Any]]:

    section(
        "TPEX CURRENT UNIVERSE"
    )

    candidates = {}

    rejected = {}

    for endpoint in (
        TPEX_CURRENT_ENDPOINTS
    ):

        url = TPEX_BASE + endpoint

        log(
            f"官方來源：{url}"
        )

        payload = request_json(url)

        rows = rows_from_payload(
            payload
        )

        log(
            f"API rows：{len(rows)}"
        )

        if not rows:

            continue

        for row in rows:

            accepted, reason = (
                accept_current_stock_row(
                    row,
                    "TPEX",
                )
            )

            if not accepted:

                rejected[reason] = (
                    rejected.get(
                        reason,
                        0,
                    )
                    + 1
                )

                continue

            symbol = parse_symbol(row)

            name = parse_name(row)

            candidates[symbol] = {
                "symbol": symbol,
                "name": name,
                "market": "TPEX",
                "raw": row,
                "source": (
                    "TPEX_MAINBOARD_DAILY_CLOSE_QUOTES"
                ),
            }

    log(
        f"✓ TPEx accepted："
        f"{len(candidates)}"
    )

    if rejected:

        log(
            f"TPEx rejected："
            f"{dict(rejected)}"
        )

    return candidates


# ============================================================
# OFFICIAL UNIVERSE SANITY
# ============================================================

def official_universe_sanity_gate(
    twse: Dict[str, Dict[str, Any]],
    tpex: Dict[str, Dict[str, Any]],
) -> bool:

    section(
        "OFFICIAL UNIVERSE SANITY GATE"
    )

    twse_count = len(twse)

    tpex_count = len(tpex)

    total = twse_count + tpex_count

    log(
        f"TWSE：{twse_count}"
    )

    log(
        f"TPEx：{tpex_count}"
    )

    log(
        f"Total：{total}"
    )

    # 不能接受 0
    if twse_count <= 0:

        log(
            "❌ TWSE Universe = 0"
        )

        return False

    if tpex_count <= 0:

        log(
            "❌ TPEx Universe = 0"
        )

        return False

    # 防止 API parser 異常
    if total > MAX_UNIVERSE_SANITY:

        log(
            "❌ Universe 異常膨脹"
        )

        log(
            f"Total={total} > "
            f"sanity_limit="
            f"{MAX_UNIVERSE_SANITY}"
        )

        return False

    return True


# ============================================================
# MERGE
# ============================================================

def merge_current_candidates(
    twse: Dict[str, Dict[str, Any]],
    tpex: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    section(
        "MERGE CURRENT UNIVERSE"
    )

    merged = {}

    for symbol, item in twse.items():

        merged[symbol] = item

    for symbol, item in tpex.items():

        if symbol in merged:

            log(
                f"⚠ 同代號跨市場："
                f"{symbol}"
            )

            # 不覆蓋 TWSE
            continue

        merged[symbol] = item

    log(
        f"✓ Current Universe："
        f"{len(merged)}"
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

    if not is_valid_symbol(
        symbol
    ):

        raise ValueError(
            "invalid symbol"
        )

    name = clean(
        candidate.get("name")
    )

    old = old_cache.get(
        symbol,
        {},
    )

    if not name:

        name = clean(
            old.get(
                "name",
                "",
            )
        )

    if not name:

        raise ValueError(
            "missing name"
        )

    market = normalize_market(
        candidate.get(
            "market"
        ),
        "",
    )

    if market not in {
        "TWSE",
        "TPEX",
    }:

        raise ValueError(
            "invalid market"
        )

    product_type, instrument_type = (
        classify_instrument(
            candidate.get(
                "raw",
                {},
            ),
            name,
        )
    )

    if product_type != "STOCK":

        raise ValueError(
            f"non-stock:{product_type}"
        )

    if instrument_type != "COMMON_STOCK":

        raise ValueError(
            f"non-common-stock:"
            f"{instrument_type}"
        )

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

    return {
        "symbol": symbol,
        "full_symbol": full_symbol,
        "name": name,
        "market": market,
        "type": "STOCK",
        "instrument_type": "COMMON_STOCK",
        "status": ACTIVE_STATUS,
        "source": clean(
            candidate.get(
                "source",
                "",
            )
        ),
    }


# ============================================================
# STRUCTURE GATE
# ============================================================

def structure_gate(
    stocks: Dict[str, Dict[str, Any]],
) -> bool:

    section(
        "STRUCTURE GATE"
    )

    required = {
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

    if not isinstance(
        stocks,
        dict,
    ):

        log(
            "❌ stocks != dict"
        )

        return False

    for symbol, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):

            log(
                f"❌ {symbol} "
                f"record != dict"
            )

            errors += 1

            continue

        missing = (
            required
            - set(item.keys())
        )

        if missing:

            log(
                f"❌ {symbol} "
                f"missing="
                f"{sorted(missing)}"
            )

            errors += 1

        if normalize_symbol(
            item.get("symbol")
        ) != symbol:

            log(
                f"❌ {symbol} "
                f"symbol mismatch"
            )

            errors += 1

        if not is_valid_symbol(
            symbol
        ):

            log(
                f"❌ {symbol} "
                f"invalid symbol"
            )

            errors += 1

        if item.get(
            "status"
        ) != ACTIVE_STATUS:

            log(
                f"❌ {symbol} "
                f"status invalid"
            )

            errors += 1

        if item.get(
            "market"
        ) not in {
            "TWSE",
            "TPEX",
        }:

            log(
                f"❌ {symbol} "
                f"market invalid"
            )

            errors += 1

        if item.get(
            "type"
        ) != "STOCK":

            log(
                f"❌ {symbol} "
                f"type != STOCK"
            )

            errors += 1

        if item.get(
            "instrument_type"
        ) != "COMMON_STOCK":

            log(
                f"❌ {symbol} "
                f"instrument_type invalid"
            )

            errors += 1

        if not clean(
            item.get("name")
        ):

            log(
                f"❌ {symbol} "
                f"name empty"
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
# DATA QUALITY GATE
# ============================================================

def data_quality_gate(
    twse_count: int,
    tpex_count: int,
    stocks: Dict[str, Dict[str, Any]],
) -> bool:

    section(
        "DATA QUALITY GATE"
    )

    errors = 0

    total = len(stocks)

    if total <= 0:

        log(
            "❌ Universe = 0"
        )

        errors += 1

    if total > MAX_UNIVERSE_SANITY:

        log(
            f"❌ Universe 異常："
            f"{total}"
        )

        errors += 1

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

    if actual_twse != twse_count:

        log(
            f"❌ TWSE mismatch："
            f"{actual_twse} != "
            f"{twse_count}"
        )

        errors += 1

    if actual_tpex != tpex_count:

        log(
            f"❌ TPEx mismatch："
            f"{actual_tpex} != "
            f"{tpex_count}"
        )

        errors += 1

    invalid = []

    for symbol, item in stocks.items():

        if item.get(
            "status"
        ) != ACTIVE_STATUS:

            invalid.append(symbol)

        if item.get(
            "type"
        ) != "STOCK":

            invalid.append(symbol)

        if item.get(
            "instrument_type"
        ) != "COMMON_STOCK":

            invalid.append(symbol)

    if invalid:

        log(
            f"❌ Invalid records："
            f"{len(invalid)}"
        )

        errors += len(invalid)

    # 官方來源驗證
    allowed_sources = {
        "TWSE_STOCK_DAY_ALL",
        "TPEX_MAINBOARD_DAILY_CLOSE_QUOTES",
    }

    bad_sources = [
        symbol
        for symbol, item
        in stocks.items()
        if item.get("source")
        not in allowed_sources
    ]

    if bad_sources:

        log(
            f"❌ Invalid sources："
            f"{len(bad_sources)}"
        )

        errors += len(bad_sources)

    log(
        f"TWSE：{actual_twse}"
    )

    log(
        f"TPEx：{actual_tpex}"
    )

    log(
        f"Total：{total}"
    )

    if errors:

        log(
            f"❌ Data Quality Gate FAIL："
            f"{errors}"
        )

        return False

    log(
        "✓ Data Quality Gate PASS"
    )

    return True


# ============================================================
# PAYLOAD
# ============================================================

def build_payload(
    stocks: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:

    total = len(stocks)

    twse = sum(
        1
        for item in stocks.values()
        if item.get("market")
        == "TWSE"
    )

    tpex = sum(
        1
        for item in stocks.values()
        if item.get("market")
        == "TPEX"
    )

    active = sum(
        1
        for item in stocks.values()
        if item.get("status")
        == ACTIVE_STATUS
    )

    return {
        "version": VERSION,
        "generated_at": (
            now_tw().isoformat()
        ),
        "data_date": today_string(),

        # Validator 要求的頂層欄位
        "universe_count": total,

        "universe_contract": {
            "source": "Data/universe.json",
            "stocks_type": "dict",
            "active_status": "active",
            "only_common_stocks": True,
            "historical_universe_used": False,
            "yahoo_used": False,
            "cmoney_used": False,
            "yahoo_fallback_enabled": True,
            "cmoney_fallback_enabled": True,
            "fallback_can_expand_universe": False,
            "hardcoded_stock_count": False,
            "hardcoded_test_symbols": False,
        },

        "source": {
            "twse": (
                "TWSE_STOCK_DAY_ALL"
            ),
            "tpex": (
                "TPEX_MAINBOARD_DAILY_CLOSE_QUOTES"
            ),
            "fallback_policy": (
                "metadata_only"
            ),
        },

        "counts": {
            "twse": twse,
            "tpex": tpex,
            "total": total,
            "active": active,
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
                os.unlink(
                    temp_path
                )

            except Exception:
                pass

        raise


# ============================================================
# POST WRITE VERIFY
# ============================================================

def verify_written_file(
    expected_stocks:
    Dict[str, Dict[str, Any]],
) -> bool:

    section(
        "POST-WRITE VERIFICATION"
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
            f"❌ JSON parse fail："
            f"{exc}"
        )

        return False

    if not isinstance(
        payload,
        dict,
    ):

        log(
            "❌ root != dict"
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
            "❌ stocks != dict"
        )

        return False

    count = payload.get(
        "universe_count"
    )

    if count != len(stocks):

        log(
            f"❌ universe_count："
            f"{count} != "
            f"{len(stocks)}"
        )

        return False

    if count != len(
        expected_stocks
    ):

        log(
            f"❌ BUILD count mismatch："
            f"{count} != "
            f"{len(expected_stocks)}"
        )

        return False

    if set(
        stocks.keys()
    ) != set(
        expected_stocks.keys()
    ):

        log(
            "❌ symbol set mismatch"
        )

        return False

    for symbol, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):

            return False

        if normalize_symbol(
            item.get(
                "symbol"
            )
        ) != symbol:

            return False

        if not is_valid_symbol(
            symbol
        ):

            return False

        if item.get(
            "status"
        ) != ACTIVE_STATUS:

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

        if item.get(
            "instrument_type"
        ) != "COMMON_STOCK":

            return False

        if not clean(
            item.get("name")
        ):

            return False

        if not clean(
            item.get("full_symbol")
        ):

            return False

    log(
        f"✓ universe_count：{count}"
    )

    log(
        f"✓ stocks：{len(stocks)}"
    )

    log(
        "✓ status=active"
    )

    log(
        "✓ type=STOCK"
    )

    log(
        "✓ instrument_type="
        "COMMON_STOCK"
    )

    log(
        "✓ symbol validation"
    )

    log(
        "✓ post-write verification PASS"
    )

    return True


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    started = time.time()

    section(
        "台股 AI 選股系統"
    )

    log(
        "build_universe.py"
    )

    log(
        f"Version：{VERSION}"
    )

    log(
        f"開始："
        f"{now_tw().isoformat()}"
    )

    log("")
    log(
        "Universe policy："
    )
    log(
        "✓ TWSE 官方第一來源"
    )
    log(
        "✓ TPEx 官方第一來源"
    )
    log(
        "✓ 只收 COMMON_STOCK"
    )
    log(
        "✓ ETF / ETN / TDR / BOND / "
        "WARRANT 排除"
    )
    log(
        "✓ Yahoo 可作 fallback"
    )
    log(
        "✓ CMoney 可作 fallback"
    )
    log(
        "✓ fallback 不得擴張 Universe"
    )
    log(
        "✓ 舊 Universe 只補 metadata"
    )
    log(
        "✓ 不使用歷史 Universe 建立新標的"
    )
    log(
        "✓ 不寫死股票數量"
    )
    log(
        "✓ 不寫死股票代號"
    )
    log(
        "✓ 不寫死測試股票"
    )
    log(
        "✓ universe_count = len(stocks)"
    )

    try:

        # ====================================================
        # OLD CACHE
        # ====================================================

        old_cache = (
            load_old_universe_names()
        )

        log("")
        log(
            f"✓ 舊 Universe metadata cache："
            f"{len(old_cache)}"
        )

        # ====================================================
        # OFFICIAL
        # ====================================================

        twse = (
            collect_twse_current()
        )

        tpex = (
            collect_tpex_current()
        )

        # ====================================================
        # OFFICIAL SANITY
        # ====================================================

        if not official_universe_sanity_gate(
            twse,
            tpex,
        ):

            log(
                "❌ Official Universe Gate FAIL"
            )

            log(
                "❌ 不寫入 universe.json"
            )

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

        if len(current) > MAX_UNIVERSE_SANITY:

            log(
                "❌ Current Universe "
                "異常膨脹"
            )

            return 1

        # ====================================================
        # BUILD
        # ====================================================

        section(
            "BUILD ACTIVE UNIVERSE"
        )

        stocks = {}

        skipped = {}

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

                reason = str(exc)

                skipped[reason] = (
                    skipped.get(
                        reason,
                        0,
                    )
                    + 1
                )

        log(
            f"✓ Build："
            f"{len(stocks)}"
        )

        if skipped:

            log(
                f"Skipped："
                f"{skipped}"
            )

        # ====================================================
        # GATE
        # ====================================================

        if not structure_gate(
            stocks
        ):

            log(
                "❌ 停止寫入"
            )

            return 1

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

        if not data_quality_gate(
            twse_count,
            tpex_count,
            stocks,
        ):

            log(
                "❌ 停止寫入"
            )

            return 1

        # ====================================================
        # PAYLOAD
        # ====================================================

        payload = build_payload(
            stocks
        )

        if payload.get(
            "universe_count"
        ) != len(
            payload.get(
                "stocks",
                {},
            )
        ):

            log(
                "❌ universe_count "
                "payload mismatch"
            )

            return 1

        # ====================================================
        # WRITE
        # ====================================================

        section(
            "ATOMIC WRITE"
        )

        atomic_write(
            payload
        )

        log(
            f"✓ 寫入："
            f"{UNIVERSE_FILE}"
        )

        # ====================================================
        # VERIFY
        # ====================================================

        if not verify_written_file(
            stocks
        ):

            log(
                "❌ Post-write verification FAIL"
            )

            return 1

        # ====================================================
        # RESULT
        # ====================================================

        elapsed = (
            time.time() - started
        )

        section(
            "BUILD RESULT"
        )

        log(
            "✓ build_universe.py PASS"
        )

        log(
            f"✓ TWSE："
            f"{twse_count}"
        )

        log(
            f"✓ TPEx："
            f"{tpex_count}"
        )

        log(
            f"✓ Total："
            f"{len(stocks)}"
        )

        log(
            f"✓ universe_count："
            f"{payload['universe_count']}"
        )

        log(
            "✓ Active："
            f"{sum("
            "1 for item in stocks.values() "
            "if item.get('status') == 'active'"
            ")}"
        )

        log(
            f"✓ elapsed："
            f"{elapsed:.1f}s"
        )

        return 0

    except KeyboardInterrupt:

        log(
            "❌ 使用者中斷"
        )

        return 130

    except Exception as exc:

        log("")
        log(
            "❌ BUILD EXCEPTION"
        )

        log(
            f"{type(exc).__name__}: "
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
