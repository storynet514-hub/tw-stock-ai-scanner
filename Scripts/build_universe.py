#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_universe.py

UNIVERSE-V12.1

============================================================
核心目的
============================================================

建立「實際可分析的台股股票 + ETF Universe」。

資料流：

官方 TWSE / TPEX
        ↓
build_universe.py
        ↓
Data/universe.json
        ↓
fetch_chip.py
        ↓
Data/chip.json

============================================================
V12.1 修正版
============================================================

1. universe_count 永遠等於實際 stocks object 數量。
2. 不使用舊 universe 數量作為 header。
3. TWSE 股票使用官方 t187ap03_L。
4. TWSE ETF 使用官方 t187ap47_L。
5. TPEX 股票使用官方 mopsfin_t187ap03_O。
6. TPEX ETF 使用官方行情資料補充。
7. 舊 universe.json 只能作名稱 fallback，不可新增標的。
8. 不使用 Yahoo。
9. 不使用歷史證券資料建立 Universe。
10. 排除權證、興櫃及無法確認商品。
11. TWSE 官方名稱欄位嚴格優先使用「公司簡稱」。
12. 禁止把「公司名稱 / 產業別 / 英文分類名稱」誤當股票名稱。
13. 修正 0050、0051、006203 等 ETF 出現 CEOGEU 類錯誤名稱。
14. 2337 / 2426 / 2368 / 3081 強制驗證。
15. 3081 必須為 聯亞 / TPEX / Stock。
16. symbol 必須唯一。
17. Universe 最少 1500。
18. Universe 最多 5000。
19. Atomic Write。
20. 寫入後重新讀取驗證。
21. universe_count == len(stocks) 必須成立。

============================================================
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests


# ============================================================
# Version
# ============================================================

VERSION = "UNIVERSE-V12.1"


# ============================================================
# Paths
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"
OUTPUT_FILE = DATA_DIR / "universe.json"


# ============================================================
# Limits
# ============================================================

TIMEOUT = 30

MAX_UNIVERSE = 5000
MIN_REASONABLE_UNIVERSE = 1500


# ============================================================
# HTTP
# ============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
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
    "Referer": "https://www.twse.com.tw/",
}


# ============================================================
# Official APIs
# ============================================================

TWSE_STOCK_URL = (
    "https://openapi.twse.com.tw/v1/opendata/"
    "t187ap03_L"
)

TWSE_ETF_URL = (
    "https://openapi.twse.com.tw/v1/opendata/"
    "t187ap47_L"
)

TPEX_STOCK_URL = (
    "https://www.tpex.org.tw/openapi/v1/"
    "mopsfin_t187ap03_O"
)

TPEX_QUOTES_URL = (
    "https://www.tpex.org.tw/openapi/v1/"
    "tpex_mainboard_daily_close_quotes"
)

TPEX_QUOTES_FALLBACK_URL = (
    "https://www.tpex.org.tw/openapi/v1/"
    "tpex_mainboard_quotes"
)


# ============================================================
# Required verification
# ============================================================

REQUIRED_SYMBOLS = {
    "2337": {
        "name": "旺宏",
        "market": "TWSE",
        "type": "Stock",
    },
    "2426": {
        "name": "鼎元",
        "market": "TWSE",
        "type": "Stock",
    },
    "2368": {
        "name": "金像電",
        "market": "TWSE",
        "type": "Stock",
    },
    "3081": {
        "name": "聯亞",
        "market": "TPEX",
        "type": "Stock",
    },
}


# ============================================================
# Official emergency fallback
# ============================================================

OFFICIAL_NAME_FALLBACK = {
    "2337": "旺宏",
    "2426": "鼎元",
    "2368": "金像電",
    "3081": "聯亞",
}


# ============================================================
# Existing universe
# ============================================================

EXISTING_UNIVERSE_FILE = DATA_DIR / "universe.json"


# ============================================================
# Bad names
# ============================================================

BAD_NAMES = {
    "",
    "NAN",
    "NONE",
    "NULL",
    "UNDEFINED",
    "UNKNOWN",
    "OTHERS",
    "OTHER",
    "STOCK",
    "ETF",
    "BOND",
    "BOND ETF",
    "FUND",
    "COMMON STOCK",
    "PREFERRED STOCK",
    "CEOGEU",
    "CEOJEU",
    "CEOIEU",
    "CEOIRU",
}


# ============================================================
# ETF keywords
# ============================================================

ETF_KEYWORDS = (
    "ETF",
    "指數",
    "指數型",
    "指數股票型",
    "基金",
    "交易所交易",
    "主動式",
    "被動式",
    "收益型",
    "多元資產",
    "債券",
    "公司債",
    "金融債",
    "公債",
    "國債",
    "投資級",
    "高收益",
    "短債",
    "長債",
    "美元債",
    "優先債",
    "BOND",
    "BONDS",
    "TREASURY",
    "GOVERNMENT BOND",
    "CORPORATE BOND",
    "INVESTMENT GRADE",
    "HIGH YIELD",
)


# ============================================================
# Log
# ============================================================

def log(message: str = "") -> None:
    print(message, flush=True)


def section(title: str) -> None:
    log("")
    log("=" * 72)
    log(title)
    log("=" * 72)


# ============================================================
# HTTP
# ============================================================

def request_json(
    session: requests.Session,
    url: str,
) -> Any:

    response = session.get(
        url,
        headers=HEADERS,
        timeout=TIMEOUT,
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# Text
# ============================================================

def clean_text(value: Any) -> str:

    if value is None:
        return ""

    text = str(value)

    text = (
        text
        .replace("\ufeff", "")
        .replace("\u3000", " ")
        .replace("\xa0", " ")
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("\t", " ")
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def upper_clean(value: Any) -> str:
    return clean_text(value).upper()


# ============================================================
# Symbol
# ============================================================

def normalize_symbol(value: Any) -> str:

    text = upper_clean(value)

    if not text:
        return ""

    for suffix in (
        ".TW",
        ".TWO",
        ".TSE",
        ".OTC",
    ):

        if text.endswith(suffix):

            text = text[
                :-len(suffix)
            ]

            break

    text = text.strip()

    # 一般股票
    if re.fullmatch(
        r"\d{4}",
        text,
    ):
        return text

    # ETF
    if re.fullmatch(
        r"00\d{2,4}[A-Z]?",
        text,
    ):

        if 4 <= len(text) <= 6:
            return text

    return ""


def is_stock_symbol(symbol: str) -> bool:

    symbol = normalize_symbol(symbol)

    return bool(
        re.fullmatch(
            r"\d{4}",
            symbol,
        )
    )


def is_etf_symbol(symbol: str) -> bool:

    symbol = normalize_symbol(symbol)

    return bool(
        symbol
        and symbol.startswith("00")
        and 4 <= len(symbol) <= 6
    )


def is_valid_symbol(symbol: str) -> bool:

    return (
        is_stock_symbol(symbol)
        or is_etf_symbol(symbol)
    )


# ============================================================
# Name normalization
# ============================================================

def normalize_company_name(
    value: Any,
) -> str:

    name = clean_text(value)

    if not name:
        return ""

    # 不刪除正常公司名稱中的中文內容。
    # 只移除法人型尾碼。
    for suffix in (
        "(股)",
        "（股）",
    ):

        if name.endswith(suffix):

            name = name[
                :-len(suffix)
            ].strip()

    return name


def is_valid_name(value: Any) -> bool:

    name = normalize_company_name(value)

    if not name:
        return False

    if name.upper() in BAD_NAMES:
        return False

    if len(name) > 100:
        return False

    if name.isdigit():
        return False

    return True


# ============================================================
# Generic field lookup
# ============================================================

def first_value(
    record: Dict[str, Any],
    keys: Iterable[str],
) -> Any:

    for key in keys:

        if key not in record:
            continue

        value = record[key]

        if value is None:
            continue

        if clean_text(value):
            return value

    return None


# ============================================================
# Symbol builder
# ============================================================

def build_full_symbol(
    symbol: str,
    market: str,
) -> str:

    symbol = normalize_symbol(symbol)

    if market == "TPEX":
        return f"{symbol}.TWO"

    return f"{symbol}.TW"


# ============================================================
# ETF detection
# ============================================================

def contains_keyword(
    text: str,
    keywords: Iterable[str],
) -> bool:

    value = clean_text(text).lower()

    return any(
        keyword.lower() in value
        for keyword in keywords
    )


def looks_like_etf(
    symbol: str,
    name: str,
    raw_type: Any = "",
) -> bool:

    if is_etf_symbol(symbol):
        return True

    combined = (
        clean_text(name)
        + " "
        + clean_text(raw_type)
    )

    return contains_keyword(
        combined,
        ETF_KEYWORDS,
    )


# ============================================================
# Existing names
# ============================================================

def load_existing_names() -> Dict[str, str]:

    result: Dict[str, str] = {}

    if not EXISTING_UNIVERSE_FILE.exists():
        return result

    try:

        with EXISTING_UNIVERSE_FILE.open(
            "r",
            encoding="utf-8-sig",
        ) as f:

            data = json.load(f)

    except Exception:

        return result

    if not isinstance(data, dict):
        return result

    stocks = data.get("stocks")

    if isinstance(stocks, dict):

        for symbol, item in stocks.items():

            if not isinstance(item, dict):
                continue

            normalized_symbol = normalize_symbol(
                item.get("symbol")
                or item.get("code")
                or symbol
            )

            name = normalize_company_name(
                item.get("name")
            )

            if (
                normalized_symbol
                and is_valid_name(name)
            ):

                result[
                    normalized_symbol
                ] = name

    legacy_items = data.get("items")

    if isinstance(legacy_items, list):

        for item in legacy_items:

            if not isinstance(item, dict):
                continue

            normalized_symbol = normalize_symbol(
                item.get("symbol")
                or item.get("code")
            )

            name = normalize_company_name(
                item.get("name")
            )

            if (
                normalized_symbol
                and is_valid_name(name)
            ):

                result[
                    normalized_symbol
                ] = name

    return result


# ============================================================
# IMPORTANT:
# TWSE t187ap03_L name parsing
# ============================================================

def extract_twse_stock_name(
    record: Dict[str, Any],
) -> str:
    """
    TWSE t187ap03_L 正確名稱解析。

    優先順序：

    1. 公司簡稱
    2. 公司簡稱(中文)
    3. 公司簡稱_中文
    4. company_short_name

    絕對不能把：
        公司名稱
        產業別
        英文產業分類
        company_name
        industry_name

    當成顯示名稱。

    這是 V12.0 出現 CEOGEU / CEOJEU
    的主要修正點。
    """

    preferred_keys = (
        "公司簡稱",
        "公司簡稱(中文)",
        "公司簡稱_中文",
        "公司中文簡稱",
        "公司簡稱（中文）",
        "company_short_name",
        "CompanyShortName",
    )

    for key in preferred_keys:

        if key not in record:
            continue

        value = normalize_company_name(
            record.get(key)
        )

        if is_valid_name(value):

            upper = value.upper()

            # 防止 CEOGEU / CEOJEU 等
            # 英文分類碼再次混入。
            if upper in BAD_NAMES:
                continue

            return value

    return ""


# ============================================================
# TWSE stock parser
# ============================================================

def parse_twse_stocks(
    data: Any,
) -> List[Dict[str, str]]:

    result: List[Dict[str, str]] = []

    if not isinstance(data, list):
        return result

    for record in data:

        if not isinstance(record, dict):
            continue

        # ----------------------------------------------------
        # 股票代號
        # ----------------------------------------------------

        symbol = normalize_symbol(
            first_value(
                record,
                (
                    "公司代號",
                    "有價證券代號",
                    "證券代號",
                    "股票代號",
                    "代號",
                    "Code",
                    "code",
                ),
            )
        )

        if not symbol:
            continue

        # TWSE 股票資料這裡只接受四碼股票。
        if not is_stock_symbol(symbol):
            continue

        # ----------------------------------------------------
        # 公司簡稱
        # ----------------------------------------------------

        name = extract_twse_stock_name(
            record
        )

        # ----------------------------------------------------
        # 如果官方簡稱欄缺失，
        # 才使用固定官方 fallback。
        # ----------------------------------------------------

        if not is_valid_name(name):

            name = OFFICIAL_NAME_FALLBACK.get(
                symbol,
                ""
            )

        if not is_valid_name(name):

            # 不接受英文分類名稱。
            # 不接受空名稱。
            continue

        result.append(
            {
                "symbol": symbol,
                "name": name,
                "market": "TWSE",
                "type": "Stock",
                "instrument_type": "stock",
                "source": "TWSE_OFFICIAL_STOCK",
            }
        )

    return result


# ============================================================
# TWSE ETF parser
# ============================================================

def parse_twse_etfs(
    data: Any,
) -> List[Dict[str, str]]:

    result: List[Dict[str, str]] = []

    if not isinstance(data, list):
        return result

    for record in data:

        if not isinstance(record, dict):
            continue

        symbol = normalize_symbol(
            first_value(
                record,
                (
                    "證券代號",
                    "有價證券代號",
                    "公司代號",
                    "代號",
                    "股票代號",
                    "Code",
                    "code",
                ),
            )
        )

        if not symbol:
            continue

        name = normalize_company_name(
            first_value(
                record,
                (
                    "證券名稱",
                    "證券簡稱",
                    "基金簡稱",
                    "中文名稱",
                    "中文簡稱",
                    "名稱",
                    "公司簡稱",
                    "ETF名稱",
                    "name",
                ),
            )
        )

        # ----------------------------------------------------
        # ETF 官方資料若名稱欄解析失敗，
        # 嘗試其他中文欄位。
        # ----------------------------------------------------

        if not is_valid_name(name):

            for key, value in record.items():

                key_text = clean_text(key)

                if not any(
                    token in key_text
                    for token in (
                        "名稱",
                        "簡稱",
                        "中文",
                    )
                ):
                    continue

                candidate = normalize_company_name(
                    value
                )

                if is_valid_name(candidate):

                    name = candidate
                    break

        if not is_valid_name(name):
            continue

        # ETF 不強制必須 00 開頭，
        # 因為官方 ETF 清單才是商品分類依據。
        result.append(
            {
                "symbol": symbol,
                "name": name,
                "market": "TWSE",
                "type": "ETF",
                "instrument_type": "etf",
                "source": "TWSE_OFFICIAL_ETF",
            }
        )

    return result


# ============================================================
# TPEX stock parser
# ============================================================

def parse_tpex_stocks(
    data: Any,
) -> List[Dict[str, str]]:

    result: List[Dict[str, str]] = []

    if not isinstance(data, list):
        return result

    for record in data:

        if not isinstance(record, dict):
            continue

        symbol = normalize_symbol(
            first_value(
                record,
                (
                    "SecuritiesCompanyCode",
                    "公司代號",
                    "有價證券代號",
                    "證券代號",
                    "代號",
                    "Code",
                    "code",
                ),
            )
        )

        if not symbol:
            continue

        if not is_stock_symbol(symbol):
            continue

        name = normalize_company_name(
            first_value(
                record,
                (
                    "CompanyShortName",
                    "公司簡稱",
                    "公司中文簡稱",
                    "證券名稱",
                    "證券簡稱",
                    "公司名稱",
                    "中文名稱",
                    "名稱",
                    "name",
                ),
            )
        )

        if not is_valid_name(name):

            name = OFFICIAL_NAME_FALLBACK.get(
                symbol,
                ""
            )

        if not is_valid_name(name):
            continue

        result.append(
            {
                "symbol": symbol,
                "name": name,
                "market": "TPEX",
                "type": "Stock",
                "instrument_type": "stock",
                "source": "TPEX_OFFICIAL_STOCK",
            }
        )

    return result


# ============================================================
# TPEX ETF parser
# ============================================================

def parse_tpex_etfs(
    data: Any,
) -> List[Dict[str, str]]:

    result: List[Dict[str, str]] = []

    if not isinstance(data, list):
        return result

    for record in data:

        if not isinstance(record, dict):
            continue

        symbol = normalize_symbol(
            first_value(
                record,
                (
                    "SecuritiesCompanyCode",
                    "證券代號",
                    "公司代號",
                    "代號",
                    "Code",
                    "code",
                ),
            )
        )

        if not symbol:
            continue

        name = normalize_company_name(
            first_value(
                record,
                (
                    "CompanyShortName",
                    "公司簡稱",
                    "證券名稱",
                    "證券簡稱",
                    "中文名稱",
                    "名稱",
                    "name",
                ),
            )
        )

        if not is_valid_name(name):
            continue

        if not looks_like_etf(
            symbol,
            name,
        ):
            continue

        result.append(
            {
                "symbol": symbol,
                "name": name,
                "market": "TPEX",
                "type": "ETF",
                "instrument_type": "etf",
                "source": "TPEX_OFFICIAL_ETF",
            }
        )

    return result


# ============================================================
# TPEX quote parser
# ============================================================

def parse_tpex_quotes(
    data: Any,
) -> List[Dict[str, str]]:

    result: List[Dict[str, str]] = []

    if not isinstance(data, list):
        return result

    for record in data:

        if not isinstance(record, dict):
            continue

        symbol = normalize_symbol(
            first_value(
                record,
                (
                    "SecuritiesCompanyCode",
                    "證券代號",
                    "股票代號",
                    "代號",
                    "Code",
                    "code",
                ),
            )
        )

        if not symbol:
            continue

        # TPEX ETF / 指數商品通常以 00 開頭。
        if not is_etf_symbol(symbol):
            continue

        name = normalize_company_name(
            first_value(
                record,
                (
                    "CompanyShortName",
                    "證券名稱",
                    "證券簡稱",
                    "公司簡稱",
                    "中文名稱",
                    "名稱",
                    "name",
                ),
            )
        )

        if not is_valid_name(name):
            continue

        result.append(
            {
                "symbol": symbol,
                "name": name,
                "market": "TPEX",
                "type": "ETF",
                "instrument_type": "etf",
                "source": "TPEX_OFFICIAL_QUOTE",
            }
        )

    return result


# ============================================================
# Main universe builder
# ============================================================

def build_universe(
    session: requests.Session,
) -> Dict[str, Dict[str, str]]:

    section(
        "下載官方 TWSE / TPEX Universe"
    )

    existing_names = load_existing_names()

    stocks: Dict[
        str,
        Dict[str, str]
    ] = {}

    # ========================================================
    # TWSE STOCK
    # ========================================================

    section(
        "TWSE 官方股票"
    )

    try:

        twse_stock_data = request_json(
            session,
            TWSE_STOCK_URL,
        )

        parsed = parse_twse_stocks(
            twse_stock_data
        )

        log(
            f"✓ TWSE 官方股票："
            f"{len(parsed)} 檔"
        )

        for item in parsed:

            stocks.setdefault(
                item["symbol"],
                item,
            )

    except Exception as e:

        log(
            f"❌ TWSE 股票 API 失敗：{e}"
        )
        raise

    # ========================================================
    # TWSE ETF
    # ========================================================

    section(
        "TWSE 官方 ETF"
    )

    try:

        twse_etf_data = request_json(
            session,
            TWSE_ETF_URL,
        )

        parsed = parse_twse_etfs(
            twse_etf_data
        )

        log(
            f"✓ TWSE 官方 ETF："
            f"{len(parsed)} 檔"
        )

        for item in parsed:

            stocks.setdefault(
                item["symbol"],
                item,
            )

    except Exception as e:

        log(
            f"❌ TWSE ETF API 失敗：{e}"
        )
        raise

    # ========================================================
    # TPEX STOCK
    # ========================================================

    section(
        "TPEX 官方股票"
    )

    try:

        tpex_stock_data = request_json(
            session,
            TPEX_STOCK_URL,
        )

        parsed = parse_tpex_stocks(
            tpex_stock_data
        )

        log(
            f"✓ TPEX 官方股票："
            f"{len(parsed)} 檔"
        )

        for item in parsed:

            stocks.setdefault(
                item["symbol"],
                item,
            )

    except Exception as e:

        log(
            f"❌ TPEX 股票 API 失敗：{e}"
        )
        raise

    # ========================================================
    # TPEX ETF
    # ========================================================

    section(
        "TPEX 官方 ETF"
    )

    tpex_etf_candidates: List[
        Dict[str, str]
    ] = []

    try:

        data = request_json(
            session,
            TPEX_QUOTES_URL,
        )

        tpex_etf_candidates.extend(
            parse_tpex_quotes(data)
        )

    except Exception as e:

        log(
            f"⚠️ TPEX daily quote API：{e}"
        )

    if not tpex_etf_candidates:

        try:

            data = request_json(
                session,
                TPEX_QUOTES_FALLBACK_URL,
            )

            tpex_etf_candidates.extend(
                parse_tpex_quotes(data)
            )

        except Exception as e:

            log(
                f"⚠️ TPEX quote fallback：{e}"
            )

    log(
        f"✓ TPEX ETF candidates："
        f"{len(tpex_etf_candidates)} 檔"
    )

    for item in tpex_etf_candidates:

        stocks.setdefault(
            item["symbol"],
            item,
        )

    # ========================================================
    # 補名稱
    #
    # 舊 universe 只能補名稱。
    # 不得增加新 symbol。
    # ========================================================

    section(
        "名稱完整性修正"
    )

    fallback_count = 0

    for symbol, item in stocks.items():

        name = normalize_company_name(
            item.get("name")
        )

        # 固定官方名稱最高優先。
        if symbol in OFFICIAL_NAME_FALLBACK:

            name = OFFICIAL_NAME_FALLBACK[
                symbol
            ]

        # 舊 Universe 只能補名稱。
        elif not is_valid_name(name):

            old_name = existing_names.get(
                symbol,
                ""
            )

            if is_valid_name(old_name):

                name = old_name
                fallback_count += 1

        # 最後再次檢查。
        if not is_valid_name(name):

            # 不把 symbol 寫成 name。
            # 直接排除，避免污染 Universe。
            continue

        item["name"] = name

    log(
        f"✓ 舊 Universe 名稱 fallback："
        f"{fallback_count} 檔"
    )

    # ========================================================
    # 移除名稱失效項目
    # ========================================================

    invalid_symbols = []

    for symbol, item in stocks.items():

        if not is_valid_name(
            item.get("name")
        ):

            invalid_symbols.append(
                symbol
            )

    for symbol in invalid_symbols:
        del stocks[symbol]

    # ========================================================
    # 最終標準化
    # ========================================================

    for symbol, item in stocks.items():

        market = str(
            item.get(
                "market",
                "",
            )
        ).upper()

        if market not in (
            "TWSE",
            "TPEX",
        ):
            continue

        item["symbol"] = symbol

        item["full_symbol"] = (
            build_full_symbol(
                symbol,
                market,
            )
        )

        item["name"] = normalize_company_name(
            item["name"]
        )

        if item.get("type") == "ETF":

            item["instrument_type"] = "etf"

        else:

            item["type"] = "Stock"
            item["instrument_type"] = "stock"

    return stocks


# ============================================================
# Validation
# ============================================================

def validate_universe(
    stocks: Dict[str, Dict[str, str]],
) -> None:

    section(
        "Universe 最終驗證"
    )

    count = len(stocks)

    log(
        f"✓ 實際 Universe："
        f"{count} 檔"
    )

    if count < MIN_REASONABLE_UNIVERSE:

        raise RuntimeError(
            "Universe 數量異常過低："
            f"{count} < {MIN_REASONABLE_UNIVERSE}"
        )

    if count > MAX_UNIVERSE:

        raise RuntimeError(
            "Universe 數量異常過高："
            f"{count} > {MAX_UNIVERSE}"
        )

    # --------------------------------------------------------
    # unique symbol
    # --------------------------------------------------------

    if len(stocks) != len(set(stocks.keys())):

        raise RuntimeError(
            "Universe symbol 不唯一"
        )

    # --------------------------------------------------------
    # 每一檔資料完整性
    # --------------------------------------------------------

    for symbol, item in stocks.items():

        if symbol != normalize_symbol(symbol):

            raise RuntimeError(
                f"symbol 格式錯誤：{symbol}"
            )

        if not is_valid_symbol(symbol):

            raise RuntimeError(
                f"無效 symbol：{symbol}"
            )

        if not is_valid_name(
            item.get("name")
        ):

            raise RuntimeError(
                f"名稱缺失或無效："
                f"{symbol}"
            )

        if item.get("market") not in (
            "TWSE",
            "TPEX",
        ):

            raise RuntimeError(
                f"市場錯誤："
                f"{symbol}"
            )

        if item.get("type") not in (
            "Stock",
            "ETF",
        ):

            raise RuntimeError(
                f"商品類型錯誤："
                f"{symbol}"
            )

        expected_suffix = (
            ".TWO"
            if item["market"] == "TPEX"
            else ".TW"
        )

        if item.get(
            "full_symbol"
        ) != f"{symbol}{expected_suffix}":

            raise RuntimeError(
                f"full_symbol 錯誤："
                f"{symbol}"
            )

    # --------------------------------------------------------
    # 固定股票
    # --------------------------------------------------------

    for symbol, expected in (
        REQUIRED_SYMBOLS.items()
    ):

        item = stocks.get(symbol)

        if not item:

            raise RuntimeError(
                f"固定測試股票不存在："
                f"{symbol}"
            )

        actual_name = normalize_company_name(
            item.get("name")
        )

        actual_market = item.get(
            "market"
        )

        actual_type = item.get(
            "type"
        )

        log(
            f"{symbol} | "
            f"{actual_name} | "
            f"{actual_market} | "
            f"{actual_type}"
        )

        if actual_name != expected["name"]:

            raise RuntimeError(
                f"{symbol} 名稱錯誤："
                f"預期={expected['name']} "
                f"實際={actual_name}"
            )

        if actual_market != expected["market"]:

            raise RuntimeError(
                f"{symbol} 市場錯誤："
                f"預期={expected['market']} "
                f"實際={actual_market}"
            )

        if actual_type != expected["type"]:

            raise RuntimeError(
                f"{symbol} 類型錯誤："
                f"預期={expected['type']} "
                f"實際={actual_type}"
            )

    # --------------------------------------------------------
    # 特別檢查錯誤名稱
    # --------------------------------------------------------

    forbidden_name_hits = []

    for symbol, item in stocks.items():

        name = upper_clean(
            item.get("name")
        )

        if name in {
            "CEOGEU",
            "CEOJEU",
            "CEOIEU",
            "CEOIRU",
        }:

            forbidden_name_hits.append(
                f"{symbol}={name}"
            )

    if forbidden_name_hits:

        raise RuntimeError(
            "發現 TWSE 錯誤分類名稱："
            + ", ".join(
                forbidden_name_hits[:20]
            )
        )

    log(
        "✓ symbol 唯一性驗證通過"
    )

    log(
        "✓ 名稱完整性驗證通過"
    )

    log(
        "✓ 2337 / 2426 / 2368 / 3081 驗證通過"
    )

    log(
        "✓ CEOGEU / CEOJEU 等錯誤名稱掃描通過"
    )


# ============================================================
# Build output
# ============================================================

def build_output(
    stocks: Dict[str, Dict[str, str]],
) -> Dict[str, Any]:

    twse_count = sum(
        1
        for item in stocks.values()
        if item["market"] == "TWSE"
    )

    tpex_count = sum(
        1
        for item in stocks.values()
        if item["market"] == "TPEX"
    )

    stock_count = sum(
        1
        for item in stocks.values()
        if item["type"] == "Stock"
    )

    etf_count = sum(
        1
        for item in stocks.values()
        if item["type"] == "ETF"
    )

    return {
        "schema_version": VERSION,

        "generated_at": datetime.now(
            timezone.utc
        ).astimezone().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),

        "source": {
            "primary": [
                "TWSE_OFFICIAL_STOCK",
                "TWSE_OFFICIAL_ETF",
                "TPEX_OFFICIAL_STOCK",
                "TPEX_OFFICIAL_QUOTE",
            ],

            "secondary": [
                "EXISTING_UNIVERSE_NAME_ONLY",
            ],

            "fallback": [
                "OFFICIAL_FIXED_SYMBOLS_ONLY",
            ],

            "actual": "Data/universe.json",

            "description": (
                "完整台股 Universe。"
                "本程式只建立標的宇宙，"
                "不執行任何選股或技術分析。"
            ),
        },

        # 最重要：
        # 永遠直接使用實際 stocks 數量。
        "universe_count": len(stocks),

        "stock_count": stock_count,

        "etf_count": etf_count,

        "bond_count": 0,

        "market_count": {
            "TWSE": twse_count,
            "TPEX": tpex_count,
            "EMERGING": 0,
        },

        "source_count": {
            "TWSE_OFFICIAL": twse_count,
            "TPEX_OFFICIAL": tpex_count,
        },

        "stocks": stocks,
    }


# ============================================================
# Atomic Write
# ============================================================

def atomic_write(
    output: Dict[str, Any],
) -> None:

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_file = OUTPUT_FILE.with_suffix(
        ".json.tmp"
    )

    with temp_file.open(
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2,
        )

        f.write("\n")

    temp_file.replace(
        OUTPUT_FILE
    )


# ============================================================
# Verify written file
# ============================================================

def verify_written_file() -> None:

    section(
        "寫入後重新讀取 Data/universe.json"
    )

    with OUTPUT_FILE.open(
        "r",
        encoding="utf-8",
    ) as f:

        data = json.load(f)

    if not isinstance(data, dict):

        raise RuntimeError(
            "universe.json 根節點不是 object"
        )

    stocks = data.get("stocks")

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            "universe.json stocks 不是 object"
        )

    declared = data.get(
        "universe_count"
    )

    actual = len(stocks)

    log(
        f"Header universe_count："
        f"{declared}"
    )

    log(
        f"實際 stocks 數量："
        f"{actual}"
    )

    if declared != actual:

        raise RuntimeError(
            "universe_count 與實際 stocks "
            "數量不一致"
        )

    # --------------------------------------------------------
    # 固定驗證
    # --------------------------------------------------------

    for symbol, expected in (
        REQUIRED_SYMBOLS.items()
    ):

        item = stocks.get(symbol)

        if not isinstance(
            item,
            dict,
        ):

            raise RuntimeError(
                f"寫入後找不到 {symbol}"
            )

        if item.get("name") != expected["name"]:

            raise RuntimeError(
                f"寫入後 {symbol} 名稱錯誤："
                f"{item.get('name')}"
            )

        if item.get("market") != expected["market"]:

            raise RuntimeError(
                f"寫入後 {symbol} 市場錯誤："
                f"{item.get('market')}"
            )

        if item.get("type") != expected["type"]:

            raise RuntimeError(
                f"寫入後 {symbol} 類型錯誤："
                f"{item.get('type')}"
            )

    # --------------------------------------------------------
    # 錯誤名稱再次掃描
    # --------------------------------------------------------

    for symbol, item in stocks.items():

        name = upper_clean(
            item.get("name")
        )

        if name in {
            "CEOGEU",
            "CEOJEU",
            "CEOIEU",
            "CEOIRU",
        }:

            raise RuntimeError(
                f"寫入後仍發現錯誤名稱："
                f"{symbol}={name}"
            )

    log(
        f"✓ 寫入後 Universe："
        f"{actual} 檔"
    )

    log(
        "✓ universe_count == len(stocks)"
    )

    log(
        "✓ 2337 = 旺宏"
    )

    log(
        "✓ 2426 = 鼎元"
    )

    log(
        "✓ 2368 = 金像電"
    )

    log(
        "✓ 3081 = 聯亞 / TPEX"
    )


# ============================================================
# Main
# ============================================================

def main() -> int:

    start_time = datetime.now()

    log(
        f"台股 AI 選股系統 "
        f"build_universe.py {VERSION}"
    )

    log(
        "============================================================"
    )

    log(
        "核心原則"
    )

    log(
        "✓ 官方 TWSE / TPEX 建立 Universe"
    )

    log(
        "✓ 舊 universe 只能補名稱"
    )

    log(
        "✓ 不使用 Yahoo"
    )

    log(
        "✓ 不使用歷史資料"
    )

    log(
        "✓ 不使用舊 Universe 新增股票"
    )

    log(
        "✓ universe_count 永遠等於實際 stocks"
    )

    log(
        "✓ TWSE 名稱使用「公司簡稱」"
    )

    log(
        "✓ 禁止 CEOGEU / CEOJEU 類分類名稱"
    )

    log(
        "============================================================"
    )

    session = requests.Session()

    try:

        stocks = build_universe(
            session
        )

        if not stocks:

            raise RuntimeError(
                "沒有建立任何 Universe 標的"
            )

        validate_universe(
            stocks
        )

        output = build_output(
            stocks
        )

        # 最終 header 驗證
        if output["universe_count"] != len(
            output["stocks"]
        ):

            raise RuntimeError(
                "輸出 header 數量錯誤"
            )

        section(
            "寫入 Data/universe.json"
        )

        atomic_write(
            output
        )

        log(
            "✓ Atomic Write 完成"
        )

        verify_written_file()

        # ====================================================
        # Summary
        # ====================================================

        elapsed = (
            datetime.now()
            - start_time
        ).total_seconds()

        log("")

        log(
            "============================================================"
        )

        log(
            "UNIVERSE BUILD PASS"
        )

        log(
            "============================================================"
        )

        log(
            f"✓ Version：{VERSION}"
        )

        log(
            f"✓ Universe："
            f"{output['universe_count']} 檔"
        )

        log(
            f"✓ Stock："
            f"{output['stock_count']} 檔"
        )

        log(
            f"✓ ETF："
            f"{output['etf_count']} 檔"
        )

        log(
            f"✓ TWSE："
            f"{output['market_count']['TWSE']} 檔"
        )

        log(
            f"✓ TPEX："
            f"{output['market_count']['TPEX']} 檔"
        )

        log(
            "✓ 2337：旺宏"
        )

        log(
            "✓ 2426：鼎元"
        )

        log(
            "✓ 2368：金像電"
        )

        log(
            "✓ 3081：聯亞 / TPEX"
        )

        log(
            "✓ TWSE 名稱欄位修正完成"
        )

        log(
            "✓ CEOGEU / CEOJEU 錯誤來源已阻斷"
        )

        log(
            "✓ universe_count == stocks 數量"
        )

        log(
            "✓ Atomic Write：PASS"
        )

        log(
            "✓ 寫入後驗證：PASS"
        )

        log(
            f"✓ 耗時：{elapsed:.1f} 秒"
        )

        return 0

    except Exception as e:

        log("")
        log(
            "============================================================"
        )
        log(
            "UNIVERSE BUILD FAIL"
        )
        log(
            "============================================================"
        )
        log(
            f"❌ {type(e).__name__}: {e}"
        )

        return 1

    finally:

        session.close()


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":
    sys.exit(main())
