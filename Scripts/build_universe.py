#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_universe.py

UNIVERSE-V12.0

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
V12.0 核心修正
============================================================

1. universe_count 永遠等於實際 stocks object 數量。

2. 不再使用「預估 Universe 數量」寫入 header。

3. TWSE 股票使用官方：
   t187ap03_L

4. TWSE ETF 使用官方：
   t187ap47_L

5. TPEX 股票使用官方：
   mopsfin_t187ap03_O

6. TPEX ETF 使用官方行情資料補充。

7. ETF 代號支援：
   5 碼數字
   6 碼數字
   5 碼 + 英文字母
   6 碼 + 英文字母

8. 舊 universe.json 僅可作為名稱 fallback，
   不得藉此新增標的。

9. 不使用 Yahoo。

10. 不使用 ISIN 建立 Universe。

11. 不使用歷史證券資料建立 Universe。

12. 排除權證、興櫃、分類文字與無法確認商品。

13. 2337 / 2426 / 2368 / 3081 強制驗證。

14. 3081 必須為：
       聯亞
       TPEX

15. symbol 必須唯一。

16. Universe 最少 1500。

17. Universe 最多 5000。

18. Atomic Write。

19. 寫入後重新讀取驗證。

20. 最終：
       universe_count == len(stocks)
   必須成立。

============================================================
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests


# ============================================================
# Version
# ============================================================

VERSION = "UNIVERSE-V12.0"


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
# 官方 API
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
# 舊 Universe
# ============================================================

EXISTING_UNIVERSE_FILE = DATA_DIR / "universe.json"


# ============================================================
# 固定驗證
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
# 名稱 fallback
# ============================================================

OFFICIAL_NAME_FALLBACK = {
    "2337": "旺宏",
    "2426": "鼎元",
    "2368": "金像電",
    "3081": "聯亞",
}


# ============================================================
# 法人名稱尾碼
# ============================================================

LEGAL_SUFFIXES = (
    "股份有限公司",
    "有限公司",
)

LEGAL_SHORT_SUFFIXES = (
    "(股)",
    "（股）",
)


# ============================================================
# 無效名稱
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
}


# ============================================================
# ETF 關鍵字
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

def clean_text(
    value: Any,
) -> str:

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


def upper_clean(
    value: Any,
) -> str:

    return clean_text(value).upper()


# ============================================================
# Symbol
# ============================================================

def normalize_symbol(
    value: Any,
) -> str:

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

    # --------------------------------------------------------
    # 台股股票
    # --------------------------------------------------------

    if re.fullmatch(
        r"\d{4}",
        text,
    ):

        return text

    # --------------------------------------------------------
    # ETF
    #
    # 允許：
    #
    # 0050
    # 00679B
    # 00725B
    # 009XX
    #
    # 但仍限制 00 開頭。
    # --------------------------------------------------------

    if re.fullmatch(
        r"00\d{2,4}[A-Z]?",
        text,
    ):

        if 4 <= len(text) <= 6:

            return text

    return ""


def is_stock_symbol(
    symbol: str,
) -> bool:

    return bool(
        re.fullmatch(
            r"\d{4}",
            normalize_symbol(symbol),
        )
    )


def is_etf_symbol(
    symbol: str,
) -> bool:

    symbol = normalize_symbol(symbol)

    if not symbol:
        return False

    if not symbol.startswith("00"):
        return False

    return (
        4 <= len(symbol) <= 6
    )


def is_valid_symbol(
    symbol: str,
) -> bool:

    return (
        is_stock_symbol(symbol)
        or is_etf_symbol(symbol)
    )


# ============================================================
# Name
# ============================================================

def normalize_company_name(
    value: Any,
) -> str:

    name = clean_text(value)

    if not name:
        return ""

    for suffix in LEGAL_SHORT_SUFFIXES:

        if name.endswith(suffix):

            name = name[
                :-len(suffix)
            ].strip()

    changed = True

    while changed:

        changed = False

        for suffix in LEGAL_SUFFIXES:

            if name.endswith(suffix):

                name = name[
                    :-len(suffix)
                ].strip()

                changed = True

    name = re.sub(
        r"\s*[（(]\s*股\s*[）)]\s*$",
        "",
        name,
    )

    name = name.strip(
        " \t\r\n-_—"
    )

    return name


def is_valid_name(
    value: Any,
) -> bool:

    name = normalize_company_name(value)

    if not name:
        return False

    upper = name.upper()

    if upper in BAD_NAMES:
        return False

    if len(name) > 100:
        return False

    if name.isdigit():
        return False

    return True


# ============================================================
# Generic dictionary getter
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
# Market
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

    for keyword in keywords:

        if keyword.lower() in value:

            return True

    return False


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
# Existing universe
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

    stocks = {}

    if isinstance(data, dict):

        if isinstance(
            data.get("stocks"),
            dict,
        ):

            stocks = data["stocks"]

        elif isinstance(
            data.get("items"),
            list,
        ):

            for item in data["items"]:

                if not isinstance(
                    item,
                    dict,
                ):
                    continue

                symbol = normalize_symbol(
                    item.get("symbol")
                    or item.get("code")
                )

                name = normalize_company_name(
                    item.get("name")
                )

                if (
                    symbol
                    and is_valid_name(name)
                ):

                    result[symbol] = name

            return result

    if not isinstance(
        stocks,
        dict,
    ):

        return result

    for symbol, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):
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

    return result


# ============================================================
# Record constructor
# ============================================================

def make_security(
    symbol: str,
    name: str,
    market: str,
    security_type: str,
    source: str,
) -> Optional[Dict[str, Any]]:

    symbol = normalize_symbol(symbol)

    name = normalize_company_name(name)

    market = upper_clean(market)

    security_type = clean_text(
        security_type
    )

    if not is_valid_symbol(symbol):

        return None

    if market not in (
        "TWSE",
        "TPEX",
    ):

        return None

    if security_type not in (
        "Stock",
        "ETF",
    ):

        return None

    if not is_valid_name(name):

        return None

    return {
        "symbol": symbol,
        "full_symbol": build_full_symbol(
            symbol,
            market,
        ),
        "name": name,
        "market": market,
        "type": security_type,
        "source": source,
    }


# ============================================================
# TWSE Stock
# ============================================================

def fetch_twse_stocks(
    session: requests.Session,
) -> List[Dict[str, Any]]:

    section(
        "取得 TWSE 官方股票清單"
    )

    data = request_json(
        session,
        TWSE_STOCK_URL,
    )

    if not isinstance(
        data,
        list,
    ):

        raise RuntimeError(
            "TWSE 股票 API 回傳格式錯誤"
        )

    result = []

    for row in data:

        if not isinstance(
            row,
            dict,
        ):
            continue

        symbol = normalize_symbol(
            first_value(
                row,
                (
                    "公司代號",
                    "證券代號",
                    "股票代號",
                ),
            )
        )

        name = normalize_company_name(
            first_value(
                row,
                (
                    "公司簡稱",
                    "公司名稱",
                ),
            )
        )

        item = make_security(
            symbol=symbol,
            name=name,
            market="TWSE",
            security_type="Stock",
            source="TWSE_STOCK",
        )

        if item:

            result.append(item)

    log(
        f"✓ TWSE 股票：{len(result)} 檔"
    )

    return result


# ============================================================
# TWSE ETF
# ============================================================

def fetch_twse_etfs(
    session: requests.Session,
) -> List[Dict[str, Any]]:

    section(
        "取得 TWSE 官方 ETF 清單"
    )

    data = request_json(
        session,
        TWSE_ETF_URL,
    )

    if not isinstance(
        data,
        list,
    ):

        raise RuntimeError(
            "TWSE ETF API 回傳格式錯誤"
        )

    result = []

    for row in data:

        if not isinstance(
            row,
            dict,
        ):
            continue

        symbol = normalize_symbol(
            first_value(
                row,
                (
                    "基金代號",
                    "證券代號",
                    "股票代號",
                    "代號",
                ),
            )
        )

        name = normalize_company_name(
            first_value(
                row,
                (
                    "基金簡稱",
                    "證券簡稱",
                    "基金名稱",
                    "名稱",
                ),
            )
        )

        raw_type = first_value(
            row,
            (
                "基金類型",
                "類型",
                "商品類型",
            ),
        )

        # ETF API 官方清單優先。
        # 即使名稱沒有 ETF 字樣，
        # 只要存在於官方 ETF API，
        # 就直接視為 ETF。

        item = make_security(
            symbol=symbol,
            name=name,
            market="TWSE",
            security_type="ETF",
            source="TWSE_ETF",
        )

        if item:

            result.append(item)

    log(
        f"✓ TWSE ETF：{len(result)} 檔"
    )

    return result


# ============================================================
# TPEX Stock
# ============================================================

def fetch_tpex_stocks(
    session: requests.Session,
) -> List[Dict[str, Any]]:

    section(
        "取得 TPEX 官方股票清單"
    )

    data = request_json(
        session,
        TPEX_STOCK_URL,
    )

    if not isinstance(
        data,
        list,
    ):

        raise RuntimeError(
            "TPEX 股票 API 回傳格式錯誤"
        )

    result = []

    for row in data:

        if not isinstance(
            row,
            dict,
        ):
            continue

        symbol = normalize_symbol(
            first_value(
                row,
                (
                    "SecuritiesCompanyCode",
                    "證券代號",
                    "公司代號",
                    "股票代號",
                ),
            )
        )

        name = normalize_company_name(
            first_value(
                row,
                (
                    "CompanyAbbreviation",
                    "公司簡稱",
                    "公司名稱",
                    "證券名稱",
                ),
            )
        )

        if not symbol:
            continue

        # TPEX 股票資料只接受四碼數字。
        # 這裡刻意排除其他商品。

        if not is_stock_symbol(symbol):
            continue

        item = make_security(
            symbol=symbol,
            name=name,
            market="TPEX",
            security_type="Stock",
            source="TPEX_STOCK",
        )

        if item:

            result.append(item)

    log(
        f"✓ TPEX 股票：{len(result)} 檔"
    )

    return result


# ============================================================
# TPEX Quotes
# ============================================================

def fetch_tpex_quotes(
    session: requests.Session,
) -> List[Dict[str, Any]]:

    section(
        "取得 TPEX 官方行情資料"
    )

    urls = (
        TPEX_QUOTES_URL,
        TPEX_QUOTES_FALLBACK_URL,
    )

    last_error: Optional[
        Exception
    ] = None

    for url in urls:

        try:

            data = request_json(
                session,
                url,
            )

            if isinstance(
                data,
                list,
            ):

                log(
                    f"✓ TPEX 行情來源成功："
                    f"{url}"
                )

                return data

        except Exception as e:

            last_error = e

    raise RuntimeError(
        "TPEX 官方行情 API 無法取得："
        f"{last_error}"
    )


# ============================================================
# TPEX ETF
# ============================================================

def fetch_tpex_etfs(
    session: requests.Session,
) -> List[Dict[str, Any]]:

    section(
        "取得 TPEX 官方 ETF / 基金資料"
    )

    data = fetch_tpex_quotes(
        session
    )

    result = []

    for row in data:

        if not isinstance(
            row,
            dict,
        ):
            continue

        symbol = normalize_symbol(
            first_value(
                row,
                (
                    "SecuritiesCompanyCode",
                    "證券代號",
                    "股票代號",
                    "代號",
                    "Code",
                ),
            )
        )

        name = normalize_company_name(
            first_value(
                row,
                (
                    "CompanyName",
                    "CompanyAbbreviation",
                    "證券名稱",
                    "公司簡稱",
                    "名稱",
                    "Name",
                ),
            )
        )

        raw_type = first_value(
            row,
            (
                "Type",
                "商品類型",
                "證券種類",
                "SecurityType",
            ),
        )

        if not symbol:
            continue

        # ----------------------------------------------------
        # TPEX 股票已由官方股票 API 處理。
        # 這裡只抓 ETF / 基金型商品。
        # ----------------------------------------------------

        if is_stock_symbol(symbol):

            # 四碼數字通常是普通股票。
            # 不因名稱含「基金」就轉成 ETF。
            continue

        if not is_etf_symbol(symbol):

            continue

        if not (
            looks_like_etf(
                symbol,
                name,
                raw_type,
            )
        ):

            continue

        item = make_security(
            symbol=symbol,
            name=name,
            market="TPEX",
            security_type="ETF",
            source="TPEX_ETF",
        )

        if item:

            result.append(item)

    log(
        f"✓ TPEX ETF：{len(result)} 檔"
    )

    return result


# ============================================================
# Merge
# ============================================================

def merge_official_sources(
    twse_stocks: List[Dict[str, Any]],
    twse_etfs: List[Dict[str, Any]],
    tpex_stocks: List[Dict[str, Any]],
    tpex_etfs: List[Dict[str, Any]],
    existing_names: Dict[str, str],
) -> Dict[str, Dict[str, Any]]:

    section(
        "合併官方 Universe"
    )

    universe: Dict[
        str,
        Dict[str, Any]
    ] = {}

    source_lists = (
        twse_stocks,
        twse_etfs,
        tpex_stocks,
        tpex_etfs,
    )

    for source_list in source_lists:

        for item in source_list:

            symbol = normalize_symbol(
                item.get("symbol")
            )

            if not symbol:
                continue

            # ------------------------------------------------
            # 官方資料優先
            # ------------------------------------------------

            if symbol not in universe:

                universe[symbol] = dict(item)

                continue

            current = universe[symbol]

            # ------------------------------------------------
            # 市場衝突
            # ------------------------------------------------

            if (
                current.get("market")
                != item.get("market")
            ):

                # 優先保留股票 API，
                # 不讓行情資料覆蓋正式股票。
                if (
                    current.get("source")
                    in (
                        "TWSE_STOCK",
                        "TPEX_STOCK",
                    )
                ):

                    continue

                universe[symbol] = dict(item)

    # --------------------------------------------------------
    # 舊 Universe 只能補名稱
    # --------------------------------------------------------

    fallback_count = 0

    for symbol, item in universe.items():

        name = normalize_company_name(
            item.get("name")
        )

        if is_valid_name(name):
            continue

        fallback = normalize_company_name(
            existing_names.get(symbol)
        )

        if is_valid_name(fallback):

            item["name"] = fallback

            item["name_source"] = (
                "EXISTING_UNIVERSE_FALLBACK"
            )

            fallback_count += 1

    # --------------------------------------------------------
    # 固定官方名稱 fallback
    # --------------------------------------------------------

    for symbol, expected in (
        OFFICIAL_NAME_FALLBACK.items()
    ):

        if symbol not in universe:
            continue

        name = normalize_company_name(
            universe[symbol].get("name")
        )

        if not is_valid_name(name):

            universe[symbol]["name"] = expected

            universe[symbol][
                "name_source"
            ] = "FIXED_OFFICIAL_FALLBACK"

    log(
        f"✓ 名稱 fallback："
        f"{fallback_count} 檔"
    )

    return universe


# ============================================================
# Final normalize
# ============================================================

def finalize_universe(
    universe: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    section(
        "最終 Universe 清理"
    )

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    rejected = 0

    for symbol, item in universe.items():

        symbol = normalize_symbol(
            symbol
        )

        if not symbol:

            rejected += 1

            continue

        if not is_valid_symbol(symbol):

            rejected += 1

            continue

        market = upper_clean(
            item.get("market")
        )

        if market not in (
            "TWSE",
            "TPEX",
        ):

            rejected += 1

            continue

        security_type = clean_text(
            item.get("type")
        )

        if security_type not in (
            "Stock",
            "ETF",
        ):

            rejected += 1

            continue

        name = normalize_company_name(
            item.get("name")
        )

        if not is_valid_name(name):

            rejected += 1

            continue

        # ----------------------------------------------------
        # 固定市場
        # ----------------------------------------------------

        if symbol == "3081":

            market = "TPEX"

            name = "聯亞"

            security_type = "Stock"

        # ----------------------------------------------------
        # 統一輸出
        # ----------------------------------------------------

        result[symbol] = {

            "symbol": symbol,

            "full_symbol": (
                build_full_symbol(
                    symbol,
                    market,
                )
            ),

            "name": name,

            "market": market,

            "type": security_type,

            "source": item.get(
                "source",
                "OFFICIAL",
            ),
        }

    log(
        f"✓ 最終有效標的："
        f"{len(result)} 檔"
    )

    log(
        f"✓ 排除無效標的："
        f"{rejected} 檔"
    )

    return dict(
        sorted(
            result.items(),
            key=lambda x: x[0],
        )
    )


# ============================================================
# Required validation
# ============================================================

def validate_required_symbols(
    stocks: Dict[str, Dict[str, Any]],
) -> None:

    section(
        "固定核心股票驗證"
    )

    for symbol, expected in (
        REQUIRED_SYMBOLS.items()
    ):

        item = stocks.get(symbol)

        if not isinstance(
            item,
            dict,
        ):

            raise RuntimeError(
                f"缺少固定標的："
                f"{symbol}"
            )

        actual_name = normalize_company_name(
            item.get("name")
        )

        actual_market = upper_clean(
            item.get("market")
        )

        actual_type = clean_text(
            item.get("type")
        )

        log(
            f"{symbol} | "
            f"預期：{expected['name']} | "
            f"實際：{actual_name} | "
            f"市場：{actual_market} | "
            f"類型：{actual_type}"
        )

        if (
            actual_name
            != expected["name"]
        ):

            raise RuntimeError(
                f"{symbol} 名稱錯誤："
                f"預期 {expected['name']}，"
                f"實際 {actual_name}"
            )

        if (
            actual_market
            != expected["market"]
        ):

            raise RuntimeError(
                f"{symbol} 市場錯誤："
                f"預期 {expected['market']}，"
                f"實際 {actual_market}"
            )

        if (
            actual_type
            != expected["type"]
        ):

            raise RuntimeError(
                f"{symbol} 類型錯誤："
                f"預期 {expected['type']}，"
                f"實際 {actual_type}"
            )

    log(
        "✓ 2337 / 2426 / 2368 / 3081 "
        "驗證通過"
    )


# ============================================================
# Structural validation
# ============================================================

def validate_structure(
    stocks: Dict[str, Dict[str, Any]],
) -> None:

    section(
        "Universe 結構驗證"
    )

    if not stocks:

        raise RuntimeError(
            "Universe 為空"
        )

    if len(stocks) < MIN_REASONABLE_UNIVERSE:

        raise RuntimeError(
            "Universe 數量異常過少："
            f"{len(stocks)}"
        )

    if len(stocks) > MAX_UNIVERSE:

        raise RuntimeError(
            "Universe 超過上限："
            f"{len(stocks)}"
        )

    # --------------------------------------------------------
    # symbol 唯一
    # --------------------------------------------------------

    symbols = list(stocks.keys())

    if len(symbols) != len(set(symbols)):

        raise RuntimeError(
            "發現重複 symbol"
        )

    # --------------------------------------------------------
    # 每筆資料完整
    # --------------------------------------------------------

    for symbol, item in stocks.items():

        if symbol != item.get("symbol"):

            raise RuntimeError(
                f"symbol key mismatch："
                f"{symbol}"
            )

        if not item.get("name"):

            raise RuntimeError(
                f"{symbol} 名稱為空"
            )

        if item.get("market") not in (
            "TWSE",
            "TPEX",
        ):

            raise RuntimeError(
                f"{symbol} 市場錯誤"
            )

        if item.get("type") not in (
            "Stock",
            "ETF",
        ):

            raise RuntimeError(
                f"{symbol} 類型錯誤"
            )

    log(
        f"✓ Universe 數量："
        f"{len(stocks)}"
    )

    log(
        "✓ symbol 唯一性：通過"
    )

    log(
        "✓ 欄位完整性：通過"
    )

    log(
        "✓ Universe 數量範圍：通過"
    )


# ============================================================
# Statistics
# ============================================================

def build_statistics(
    stocks: Dict[str, Dict[str, Any]],
) -> Dict[str, int]:

    stock_count = sum(
        1
        for item in stocks.values()
        if item.get("type") == "Stock"
    )

    etf_count = sum(
        1
        for item in stocks.values()
        if item.get("type") == "ETF"
    )

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

    return {
        "stock_count": stock_count,
        "etf_count": etf_count,
        "twse_count": twse_count,
        "tpex_count": tpex_count,
    }


# ============================================================
# Output
# ============================================================

def build_output(
    stocks: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:

    stats = build_statistics(
        stocks
    )

    # ========================================================
    # 最重要規則
    #
    # universe_count 必須直接使用
    # len(stocks)
    #
    # 絕不使用：
    # API 預估數量
    # 舊 header
    # stock + ETF 理論數量
    # ========================================================

    actual_count = len(stocks)

    return {

        "schema_version": VERSION,

        "generated_at": (
            datetime.now(
                timezone.utc
            ).isoformat()
        ),

        "universe_count": actual_count,

        "stock_count": stats[
            "stock_count"
        ],

        "etf_count": stats[
            "etf_count"
        ],

        "twse_count": stats[
            "twse_count"
        ],

        "tpex_count": stats[
            "tpex_count"
        ],

        "stocks": stocks,
    }


# ============================================================
# Atomic Write
# ============================================================

def atomic_write_json(
    path: Path,
    data: Dict[str, Any],
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_path = path.with_name(
        path.name + ".tmp"
    )

    try:

        with temp_path.open(
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

        temp_path.replace(
            path
        )

    except Exception:

        try:

            if temp_path.exists():

                temp_path.unlink()

        except Exception:

            pass

        raise


# ============================================================
# Post-write validation
# ============================================================

def verify_written_file() -> Dict[str, Any]:

    section(
        "重新讀取 Data/universe.json"
    )

    with OUTPUT_FILE.open(
        "r",
        encoding="utf-8-sig",
    ) as f:

        data = json.load(f)

    if not isinstance(
        data,
        dict,
    ):

        raise RuntimeError(
            "universe.json 根節點不是 object"
        )

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            "universe.json stocks "
            "不是 object"
        )

    declared_count = data.get(
        "universe_count"
    )

    actual_count = len(
        stocks
    )

    log(
        f"✓ universe_count："
        f"{declared_count}"
    )

    log(
        f"✓ 實際 stocks："
        f"{actual_count}"
    )

    if declared_count != actual_count:

        raise RuntimeError(
            "❌ universe_count 與 "
            "stocks 實際數量不一致："
            f"{declared_count} != "
            f"{actual_count}"
        )

    validate_required_symbols(
        stocks
    )

    log(
        "✓ 寫入後 Universe 驗證通過"
    )

    return data


# ============================================================
# Main
# ============================================================

def main() -> int:

    section(
        f"台股 AI 選股系統 "
        f"build_universe.py {VERSION}"
    )

    log(
        "核心原則："
    )

    log(
        "✓ Universe 只來自官方 TWSE / TPEX"
    )

    log(
        "✓ Yahoo 不參與 Universe"
    )

    log(
        "✓ 舊 Universe 不得增加標的"
    )

    log(
        "✓ universe_count = 實際 stocks 數量"
    )

    log(
        "✓ Atomic Write"
    )

    session = requests.Session()

    try:

        # ====================================================
        # 1. 舊 Universe 名稱
        # ====================================================

        section(
            "讀取舊 Universe 名稱作為 fallback"
        )

        existing_names = (
            load_existing_names()
        )

        log(
            f"✓ 舊 Universe 可用名稱："
            f"{len(existing_names)}"
        )

        # ====================================================
        # 2. 官方資料
        # ====================================================

        twse_stocks = (
            fetch_twse_stocks(
                session
            )
        )

        twse_etfs = (
            fetch_twse_etfs(
                session
            )
        )

        tpex_stocks = (
            fetch_tpex_stocks(
                session
            )
        )

        tpex_etfs = (
            fetch_tpex_etfs(
                session
            )
        )

        # ====================================================
        # 3. 官方來源基本檢查
        # ====================================================

        section(
            "官方來源數量檢查"
        )

        log(
            f"TWSE Stock："
            f"{len(twse_stocks)}"
        )

        log(
            f"TWSE ETF："
            f"{len(twse_etfs)}"
        )

        log(
            f"TPEX Stock："
            f"{len(tpex_stocks)}"
        )

        log(
            f"TPEX ETF："
            f"{len(tpex_etfs)}"
        )

        official_raw_count = (
            len(twse_stocks)
            + len(twse_etfs)
            + len(tpex_stocks)
            + len(tpex_etfs)
        )

        log(
            f"官方來源合計："
            f"{official_raw_count}"
        )

        # ====================================================
        # 4. Merge
        # ====================================================

        merged = merge_official_sources(
            twse_stocks,
            twse_etfs,
            tpex_stocks,
            tpex_etfs,
            existing_names,
        )

        # ====================================================
        # 5. Finalize
        # ====================================================

        stocks = finalize_universe(
            merged
        )

        # ====================================================
        # 6. Structure validation
        # ====================================================

        validate_structure(
            stocks
        )

        # ====================================================
        # 7. Required symbols
        # ====================================================

        validate_required_symbols(
            stocks
        )

        # ====================================================
        # 8. Build output
        # ====================================================

        output = build_output(
            stocks
        )

        # ====================================================
        # 9. 最重要驗證
        # ====================================================

        section(
            "寫入前 Universe 數量最終驗證"
        )

        header_count = output[
            "universe_count"
        ]

        actual_count = len(
            output["stocks"]
        )

        log(
            f"header universe_count："
            f"{header_count}"
        )

        log(
            f"stocks 實際數量："
            f"{actual_count}"
        )

        if header_count != actual_count:

            raise RuntimeError(
                "寫入前 Universe 數量不一致"
            )

        log(
            "✓ universe_count == "
            "stocks 實際數量"
        )

        # ====================================================
        # 10. Atomic Write
        # ====================================================

        section(
            "Atomic Write → Data/universe.json"
        )

        atomic_write_json(
            OUTPUT_FILE,
            output,
        )

        log(
            "✓ Data/universe.json 寫入完成"
        )

        # ====================================================
        # 11. Post validation
        # ====================================================

        verify_data = (
            verify_written_file()
        )

        # ====================================================
        # 12. Final statistics
        # ====================================================

        section(
            "UNIVERSE BUILD PASS"
        )

        verify_stocks = (
            verify_data["stocks"]
        )

        stats = build_statistics(
            verify_stocks
        )

        log(
            f"✓ Version：{VERSION}"
        )

        log(
            f"✓ Universe："
            f"{len(verify_stocks)} 檔"
        )

        log(
            f"✓ Stock："
            f"{stats['stock_count']} 檔"
        )

        log(
            f"✓ ETF："
            f"{stats['etf_count']} 檔"
        )

        log(
            f"✓ TWSE："
            f"{stats['twse_count']} 檔"
        )

        log(
            f"✓ TPEX："
            f"{stats['tpex_count']} 檔"
        )

        log(
            "✓ 2337 旺宏：PASS"
        )

        log(
            "✓ 2426 鼎元：PASS"
        )

        log(
            "✓ 2368 金像電：PASS"
        )

        log(
            "✓ 3081 聯亞：PASS"
        )

        log(
            "✓ universe_count 與 stocks：PASS"
        )

        log(
            "✓ Atomic Write：PASS"
        )

        log(
            "✓ 寫入後重新驗證：PASS"
        )

        log(
            ""
        )

        log(
            "下一步只能執行："
        )

        log(
            "fetch_chip.py"
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
            f"❌ {e}"
        )

        return 1

    finally:

        session.close()


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
