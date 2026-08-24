#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_universe.py

正式版 UNIVERSE-V11.2

============================================================
核心目的
============================================================

建立「實際可分析的台股股票 + ETF Universe」。

資料流：

官方標的資料
    ↓
build_universe.py
    ↓
Data/universe.json
    ↓
analyze_stocks.py
    ↓
Data/analysis.json
    ↓
build_ui_data.py
    ↓
Data/ui_data.json
    ↓
index.html

============================================================
V11.2 核心修正
============================================================

1. TWSE 股票使用：
   t187ap03_L

2. TWSE ETF 使用：
   t187ap47_L

3. TPEX 股票使用：
   mopsfin_t187ap03_O

4. TPEX 全證券補充使用：
   tpex_mainboard_daily_close_quotes

5. 不再使用 ISIN 歷史資料建立 Universe。

6. ISIN 只能作為名稱補充，不能增加 Universe。

7. 不使用 tpex_mainboard_peratio 作為 Universe 主來源。

8. 舊 universe.json 只能補充官方已存在的 symbol，
   不得增加新的 Universe 標的。

9. 排除：
   - 權證
   - 歷史 ISIN
   - 舊有價證券
   - 明顯非股票 / ETF 商品
   - 純分類文字
   - 純國際代碼
   - 無法確認市場的標的

10. 保留：
    - 上市股票
    - 上櫃股票
    - 上市 ETF
    - 上櫃 ETF
    - 債券 ETF

11. 3081 聯亞必須存在。

12. Universe 上限 5000。

13. Universe 過少或核心股票缺失時直接停止。

14. 名稱優先順序：

    官方公司簡稱
        ↓
    官方公司名稱標準化
        ↓
    官方 ETF 名稱
        ↓
    舊 Universe 名稱

15. Yahoo 不參與 Universe 決策。

============================================================
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import requests


# ============================================================
# 基本設定
# ============================================================

VERSION = "UNIVERSE-V11.2"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

OUTPUT_FILE = DATA_DIR / "universe.json"

TIMEOUT = 30

MAX_UNIVERSE = 5000

MIN_REASONABLE_UNIVERSE = 1500


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}


# ============================================================
# 官方資料來源
# ============================================================

TWSE_STOCK_URL = (
    "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
)

TWSE_ETF_URL = (
    "https://openapi.twse.com.tw/v1/opendata/t187ap47_L"
)

TPEX_STOCK_URL = (
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
)

TPEX_QUOTES_URL = (
    "https://www.tpex.org.tw/openapi/v1/"
    "tpex_mainboard_daily_close_quotes"
)

# 備用 TPEX 行情來源
TPEX_QUOTES_FALLBACK_URL = (
    "https://www.tpex.org.tw/openapi/v1/"
    "tpex_mainboard_quotes"
)


# ============================================================
# 舊資料
# ============================================================

EXISTING_UNIVERSE_FILE = (
    DATA_DIR / "universe.json"
)


# ============================================================
# 必須存在的核心股票
# ============================================================

REQUIRED_SYMBOLS = {
    "2337": "旺宏",
    "2426": "鼎元",
    "2368": "金像電",
    "3081": "聯亞",
}


# ============================================================
# 名稱清理
# ============================================================

LEGAL_SUFFIXES = (
    "股份有限公司",
    "有限公司",
)

LEGAL_SHORT_SUFFIXES = (
    "(股)",
    "（股）",
)

BAD_NAMES = {
    "",
    "NAN",
    "NONE",
    "NULL",
    "UNDEFINED",
    "OTHERS",
    "OTHER",
    "FOOD",
    "SEMICONDUCTOR INDUSTRY",
    "STOCK",
    "ETF",
    "BOND",
    "BOND ETF",
    "FUND",
    "COMMON STOCK",
    "PREFERRED STOCK",
}


# ============================================================
# ETF 判斷
# ============================================================

ETF_KEYWORDS = (
    "ETF",
    "指數",
    "指數型",
    "基金",
    "被動式",
    "主動式",
    "收益型",
    "多元資產",
    "債券",
    "債",
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
    "bond",
    "bonds",
    "treasury",
    "government bond",
    "corporate bond",
    "investment grade",
    "high yield",
)


BOND_KEYWORDS = (
    "債券",
    "債",
    "公司債",
    "金融債",
    "公債",
    "國債",
    "美國國債",
    "美元債",
    "投資級",
    "投資級債",
    "非投資等級",
    "高收益債",
    "高收益",
    "短天期債",
    "長天期債",
    "短債",
    "長債",
    "優先債",
    "bond",
    "bonds",
    "treasury",
    "government bond",
    "corporate bond",
    "investment grade",
    "high yield",
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

def request_json(url: str) -> Any:

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=TIMEOUT,
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# 基本文字
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
            text = text[:-len(suffix)]
            break

    text = text.strip()

    if not re.fullmatch(
        r"[A-Z0-9]{4,6}",
        text,
    ):
        return ""

    return text


def is_valid_symbol(symbol: str) -> bool:

    symbol = normalize_symbol(symbol)

    if not symbol:
        return False

    # 本系統主要分析股票與 ETF。
    #
    # 權證通常為 5~6 碼，
    # 但不以單純長度判斷。
    #
    # 明確排除部分權證常見格式：
    if symbol.startswith("7") and len(symbol) == 6:
        return False

    return bool(
        re.fullmatch(
            r"[A-Z0-9]{4,6}",
            symbol,
        )
    )


# ============================================================
# 名稱標準化
# ============================================================

def normalize_company_name(
    value: Any,
) -> str:

    name = clean_text(value)

    if not name:
        return ""

    # --------------------------------------------------------
    # 移除法人後綴
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # 常見括號法人尾碼
    # --------------------------------------------------------

    name = re.sub(
        r"\s*[\(（]\s*股\s*[\)）]\s*$",
        "",
        name,
    )

    name = re.sub(
        r"\s*[\(（]\s*有\s*[\)）]\s*$",
        "",
        name,
    )

    # --------------------------------------------------------
    # 移除前後標點
    # --------------------------------------------------------

    name = name.strip(
        " \t\r\n-_—"
    )

    return name


# ============================================================
# 名稱驗證
# ============================================================

def is_valid_name(
    value: Any,
) -> bool:

    name = normalize_company_name(
        value
    )

    if not name:
        return False

    upper = name.upper()

    if upper in BAD_NAMES:
        return False

    if len(name) > 100:
        return False

    if name.isdigit():
        return False

    # 純英文分類名稱
    if re.fullmatch(
        r"[A-Z][A-Z0-9 _\-/\.]{1,}",
        upper,
    ):
        return False

    # ISIN
    if re.fullmatch(
        r"[A-Z]{2}[A-Z0-9]{9}[0-9]",
        upper,
    ):
        return False

    # 國際代碼
    if re.fullmatch(
        r"[A-Z]{2,}[0-9]{6,}",
        upper,
    ):
        return False

    return True


# ============================================================
# Dictionary 欄位
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

    if market == "TPEX":
        return f"{symbol}.TWO"

    return f"{symbol}.TW"


# ============================================================
# ETF / Bond 判斷
# ============================================================

def looks_like_etf(
    symbol: str,
    name: str,
    raw_type: Any = None,
) -> bool:

    text = (
        clean_text(name)
        + " "
        + clean_text(raw_type)
    ).upper()

    for keyword in ETF_KEYWORDS:

        if keyword.upper() in text:
            return True

    # 00xx 是台股 ETF 常見代號區域
    if (
        symbol.isdigit()
        and symbol.startswith("00")
    ):
        return True

    return False


def looks_like_bond_etf(
    name: str,
    raw_type: Any = None,
) -> bool:

    text = (
        clean_text(name)
        + " "
        + clean_text(raw_type)
    ).lower()

    for keyword in BOND_KEYWORDS:

        if keyword.lower() in text:
            return True

    # 台股常見海外 / 債券 ETF 代號
    #
    # 例如：
    # 00679B
    # 00725B
    # 00687B
    #
    # 只作輔助。
    if re.fullmatch(
        r"\d{5}[A-Z]",
        clean_text(name),
    ):
        return False

    return False


def classify_instrument(
    symbol: str,
    name: str,
    raw_type: Any = None,
) -> str:

    if looks_like_bond_etf(
        name,
        raw_type,
    ):
        return "bond"

    if looks_like_etf(
        symbol,
        name,
        raw_type,
    ):
        return "etf"

    return "stock"


# ============================================================
# Record
# ============================================================

def build_record(
    symbol: Any,
    name: Any,
    market: str,
    raw_type: Any = "",
    source: str = "",
) -> Optional[Dict[str, Any]]:

    symbol = normalize_symbol(
        symbol
    )

    name = normalize_company_name(
        name
    )

    market = clean_text(
        market
    ).upper()

    if not is_valid_symbol(
        symbol
    ):
        return None

    if not is_valid_name(
        name
    ):
        return None

    if market not in {
        "TWSE",
        "TPEX",
    }:
        return None

    instrument_type = classify_instrument(
        symbol,
        name,
        raw_type,
    )

    if instrument_type == "bond":

        type_label = "Bond ETF"
        asset_class = "bond"

    elif instrument_type == "etf":

        type_label = "ETF"
        asset_class = "fund"

    else:

        type_label = "Stock"
        asset_class = "equity"

    return {
        "symbol": symbol,
        "full_symbol": build_full_symbol(
            symbol,
            market,
        ),
        "name": name,
        "market": market,
        "type": type_label,
        "instrument_type": instrument_type,
        "asset_class": asset_class,
        "source": source,
    }


# ============================================================
# TWSE 股票
# ============================================================

def load_twse_stocks() -> Dict[str, Dict[str, Any]]:

    section(
        "TWSE 官方股票資料"
    )

    try:

        payload = request_json(
            TWSE_STOCK_URL
        )

    except Exception as exc:

        log(
            f"❌ TWSE 股票 API 失敗：{exc}"
        )

        return {}

    result = {}

    if not isinstance(
        payload,
        list,
    ):
        return result

    for item in payload:

        if not isinstance(
            item,
            dict,
        ):
            continue

        symbol = first_value(
            item,
            (
                "公司代號",
                "證券代號",
                "代號",
                "Code",
                "code",
            ),
        )

        # 公司簡稱優先
        name = first_value(
            item,
            (
                "公司簡稱",
                "證券名稱",
                "名稱",
                "Name",
                "name",
                "公司名稱",
                "CompanyName",
            ),
        )

        raw_type = first_value(
            item,
            (
                "證券類別",
                "類別",
                "產業類別",
            ),
        )

        record = build_record(
            symbol=symbol,
            name=name,
            market="TWSE",
            raw_type=raw_type,
            source="TWSE_STOCK_OFFICIAL",
        )

        if record:

            result[
                record["symbol"]
            ] = record

    log(
        f"✓ TWSE 股票：{len(result)} 檔"
    )

    return result


# ============================================================
# TWSE ETF
# ============================================================

def load_twse_etf() -> Dict[str, Dict[str, Any]]:

    section(
        "TWSE 官方 ETF 資料"
    )

    try:

        payload = request_json(
            TWSE_ETF_URL
        )

    except Exception as exc:

        log(
            f"⚠ TWSE ETF API 失敗：{exc}"
        )

        return {}

    result = {}

    if not isinstance(
        payload,
        list,
    ):
        return result

    for item in payload:

        if not isinstance(
            item,
            dict,
        ):
            continue

        symbol = first_value(
            item,
            (
                "基金代號",
                "證券代號",
                "代號",
                "Code",
                "code",
            ),
        )

        name = first_value(
            item,
            (
                "基金名稱",
                "證券名稱",
                "名稱",
                "Name",
                "name",
            ),
        )

        if not symbol or not name:
            continue

        record = build_record(
            symbol=symbol,
            name=name,
            market="TWSE",
            raw_type="ETF",
            source="TWSE_ETF_OFFICIAL",
        )

        if record:

            # 強制 ETF
            record["type"] = (
                "Bond ETF"
                if looks_like_bond_etf(
                    record["name"]
                )
                else "ETF"
            )

            record["instrument_type"] = (
                "bond"
                if record["type"] == "Bond ETF"
                else "etf"
            )

            record["asset_class"] = (
                "bond"
                if record["type"] == "Bond ETF"
                else "fund"
            )

            result[
                record["symbol"]
            ] = record

    log(
        f"✓ TWSE ETF：{len(result)} 檔"
    )

    return result


# ============================================================
# TPEX 股票
# ============================================================

def load_tpex_stocks() -> Dict[str, Dict[str, Any]]:

    section(
        "TPEX 官方公司基本資料"
    )

    try:

        payload = request_json(
            TPEX_STOCK_URL
        )

    except Exception as exc:

        log(
            f"⚠ TPEX 公司基本資料失敗：{exc}"
        )

        return {}

    result = {}

    if not isinstance(
        payload,
        list,
    ):
        return result

    for item in payload:

        if not isinstance(
            item,
            dict,
        ):
            continue

        symbol = first_value(
            item,
            (
                "SecuritiesCompanyCode",
                "公司代號",
                "證券代號",
                "Code",
                "code",
            ),
        )

        # TPEX 官方簡稱優先
        name = first_value(
            item,
            (
                "CompanyAbbreviation",
                "證券名稱",
                "公司簡稱",
                "名稱",
                "CompanyName",
                "name",
            ),
        )

        raw_type = first_value(
            item,
            (
                "SecuritiesIndustryCode",
                "證券類別",
                "類別",
                "Type",
            ),
        )

        record = build_record(
            symbol=symbol,
            name=name,
            market="TPEX",
            raw_type=raw_type,
            source="TPEX_STOCK_OFFICIAL",
        )

        if record:

            result[
                record["symbol"]
            ] = record

    log(
        f"✓ TPEX 公司資料：{len(result)} 檔"
    )

    return result


# ============================================================
# TPEX 全證券行情
# ============================================================

def parse_tpex_quotes(
    payload: Any,
) -> Dict[str, Dict[str, Any]]:

    result = {}

    if not isinstance(
        payload,
        list,
    ):
        return result

    for item in payload:

        if not isinstance(
            item,
            dict,
        ):
            continue

        symbol = first_value(
            item,
            (
                "SecuritiesCompanyCode",
                "證券代號",
                "代號",
                "Code",
                "code",
            ),
        )

        name = first_value(
            item,
            (
                "CompanyName",
                "公司名稱",
                "證券名稱",
                "名稱",
                "Name",
                "name",
            ),
        )

        if not symbol or not name:
            continue

        record = build_record(
            symbol=symbol,
            name=name,
            market="TPEX",
            raw_type="",
            source="TPEX_QUOTES_OFFICIAL",
        )

        if record:

            result[
                record["symbol"]
            ] = record

    return result


def load_tpex_quotes() -> Dict[str, Dict[str, Any]]:

    section(
        "TPEX 官方全證券行情"
    )

    urls = (
        TPEX_QUOTES_URL,
        TPEX_QUOTES_FALLBACK_URL,
    )

    for url in urls:

        try:

            log(
                f"嘗試：{url}"
            )

            payload = request_json(
                url
            )

            result = parse_tpex_quotes(
                payload
            )

            if result:

                log(
                    f"✓ TPEX 全證券行情："
                    f"{len(result)} 檔"
                )

                return result

        except Exception as exc:

            log(
                f"⚠ TPEX 行情來源失敗：{exc}"
            )

    return {}


# ============================================================
# 舊 Universe
# ============================================================

def load_existing_universe() -> Dict[str, Dict[str, Any]]:

    section(
        "載入既有 Universe fallback"
    )

    if not EXISTING_UNIVERSE_FILE.exists():

        log(
            "既有 Universe：0 檔"
        )

        return {}

    try:

        with EXISTING_UNIVERSE_FILE.open(
            "r",
            encoding="utf-8-sig",
        ) as f:

            data = json.load(f)

    except Exception as exc:

        log(
            f"⚠ 舊 Universe 讀取失敗：{exc}"
        )

        return {}

    stocks = data.get(
        "stocks",
        {},
    )

    if not isinstance(
        stocks,
        dict,
    ):
        return {}

    result = {}

    for symbol, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):
            continue

        symbol2 = normalize_symbol(
            item.get(
                "symbol",
                symbol,
            )
        )

        name = normalize_company_name(
            item.get(
                "name",
                "",
            )
        )

        market = clean_text(
            item.get(
                "market",
                "",
            )
        ).upper()

        if not is_valid_symbol(
            symbol2
        ):
            continue

        if not is_valid_name(
            name
        ):
            continue

        if market not in {
            "TWSE",
            "TPEX",
        }:
            continue

        record = build_record(
            symbol=symbol2,
            name=name,
            market=market,
            raw_type=item.get(
                "type",
                "",
            ),
            source="EXISTING_UNIVERSE_FALLBACK",
        )

        if record:

            result[symbol2] = record

    log(
        f"既有 Universe：{len(result)} 檔"
    )

    return result


# ============================================================
# 官方合併
# ============================================================

def merge_official_sources(
    twse_stocks: Dict[str, Dict[str, Any]],
    twse_etf: Dict[str, Dict[str, Any]],
    tpex_stocks: Dict[str, Dict[str, Any]],
    tpex_quotes: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    result = {}

    # --------------------------------------------------------
    # TWSE 股票
    # --------------------------------------------------------

    for symbol, record in twse_stocks.items():

        result[symbol] = record

    # --------------------------------------------------------
    # TWSE ETF
    # --------------------------------------------------------

    for symbol, record in twse_etf.items():

        result[symbol] = record

    # --------------------------------------------------------
    # TPEX 公司資料
    #
    # 公司資料優先於行情資料。
    # --------------------------------------------------------

    for symbol, record in tpex_stocks.items():

        result[symbol] = record

    # --------------------------------------------------------
    # TPEX 全證券行情
    #
    # 只補官方公司資料沒有的標的。
    #
    # 這可以補：
    # ETF
    # 特殊可交易證券
    #
    # 但不允許權證等非 Universe 商品。
    # --------------------------------------------------------

    for symbol, record in tpex_quotes.items():

        if symbol in result:
            continue

        name = record["name"]

        # TPEX 額外行情資料只允許：
        #
        # 00 開頭 ETF
        #
        # 或名稱明確判斷為 ETF
        #

        is_etf = (
            symbol.startswith("00")
            or looks_like_etf(
                symbol,
                name,
            )
        )

        if not is_etf:
            continue

        result[symbol] = record

    return result


# ============================================================
# 舊資料只補名稱
# ============================================================

def supplement_names(
    official: Dict[str, Dict[str, Any]],
    existing: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    result = {}

    for symbol, record in official.items():

        result[symbol] = dict(
            record
        )

    for symbol, old_record in existing.items():

        if symbol not in result:
            # 絕對禁止舊資料擴張 Universe
            continue

        current = result[symbol]

        current_name = normalize_company_name(
            current.get(
                "name",
                "",
            )
        )

        old_name = normalize_company_name(
            old_record.get(
                "name",
                "",
            )
        )

        # 只有官方名稱不存在時才補舊名稱
        if (
            not is_valid_name(
                current_name
            )
            and is_valid_name(
                old_name
            )
        ):

            current["name"] = old_name
            current["source"] = (
                "OFFICIAL_SYMBOL_EXISTING_NAME"
            )

    return result


# ============================================================
# 最終過濾
# ============================================================

def final_filter(
    stocks: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    result = {}

    for symbol, record in stocks.items():

        symbol = normalize_symbol(
            symbol
        )

        name = normalize_company_name(
            record.get(
                "name",
                "",
            )
        )

        market = clean_text(
            record.get(
                "market",
                "",
            )
        ).upper()

        if not is_valid_symbol(
            symbol
        ):
            continue

        if not is_valid_name(
            name
        ):
            continue

        if market not in {
            "TWSE",
            "TPEX",
        }:
            continue

        instrument_type = classify_instrument(
            symbol,
            name,
            record.get(
                "type",
                "",
            ),
        )

        if instrument_type == "bond":

            type_label = "Bond ETF"
            asset_class = "bond"

        elif instrument_type == "etf":

            type_label = "ETF"
            asset_class = "fund"

        else:

            type_label = "Stock"
            asset_class = "equity"

        result[symbol] = {
            "symbol": symbol,
            "full_symbol": build_full_symbol(
                symbol,
                market,
            ),
            "name": name,
            "market": market,
            "type": type_label,
            "instrument_type": instrument_type,
            "asset_class": asset_class,
            "source": record.get(
                "source",
                "OFFICIAL",
            ),
        }

    return result


# ============================================================
# 核心股票驗證
# ============================================================

def validate_required_symbols(
    stocks: Dict[str, Dict[str, Any]],
) -> None:

    section(
        "核心股票名稱驗證"
    )

    missing = []

    for symbol, expected_name in (
        REQUIRED_SYMBOLS.items()
    ):

        if symbol not in stocks:

            log(
                f"❌ {symbol} 不存在於 Universe"
            )

            missing.append(
                symbol
            )

            continue

        actual_name = stocks[
            symbol
        ]["name"]

        market = stocks[
            symbol
        ]["market"]

        log(
            f"✓ {symbol} | "
            f"{actual_name} | "
            f"{market}"
        )

    if missing:

        raise RuntimeError(
            "核心股票缺失："
            + ", ".join(
                missing
            )
        )


# ============================================================
# Schema
# ============================================================

def build_statistics(
    stocks: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:

    stock_count = 0
    etf_count = 0
    bond_count = 0

    twse_count = 0
    tpex_count = 0

    for record in stocks.values():

        instrument_type = record[
            "instrument_type"
        ]

        market = record[
            "market"
        ]

        if instrument_type == "stock":
            stock_count += 1

        elif instrument_type == "etf":
            etf_count += 1

        elif instrument_type == "bond":
            bond_count += 1

        if market == "TWSE":
            twse_count += 1

        elif market == "TPEX":
            tpex_count += 1

    return {
        "universe_count": len(stocks),
        "stock_count": stock_count,
        "etf_count": etf_count,
        "bond_count": bond_count,
        "market_count": {
            "TWSE": twse_count,
            "TPEX": tpex_count,
        },
    }


def validate_schema(
    data: Dict[str, Any],
) -> None:

    required = (
        "schema_version",
        "generated_at",
        "universe_count",
        "stock_count",
        "etf_count",
        "bond_count",
        "market_count",
        "stocks",
    )

    for key in required:

        if key not in data:

            raise RuntimeError(
                f"缺少 Schema 欄位：{key}"
            )

    stocks = data[
        "stocks"
    ]

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            "stocks 必須是 object"
        )

    if len(stocks) != data[
        "universe_count"
    ]:

        raise RuntimeError(
            "Universe count 不一致"
        )

    seen = set()

    for symbol, record in stocks.items():

        if symbol in seen:

            raise RuntimeError(
                f"symbol 重複：{symbol}"
            )

        seen.add(symbol)

        if record.get(
            "symbol"
        ) != symbol:

            raise RuntimeError(
                f"{symbol} symbol mismatch"
            )

        if not is_valid_symbol(
            symbol
        ):

            raise RuntimeError(
                f"無效 symbol：{symbol}"
            )

        if not is_valid_name(
            record.get(
                "name"
            )
        ):

            raise RuntimeError(
                f"{symbol} 名稱錯誤："
                f"{record.get('name')}"
            )

        expected_full = build_full_symbol(
            symbol,
            record.get(
                "market"
            ),
        )

        if record.get(
            "full_symbol"
        ) != expected_full:

            raise RuntimeError(
                f"{symbol} full_symbol 錯誤："
                f"{record.get('full_symbol')} "
                f"!= {expected_full}"
            )

    stats = build_statistics(
        stocks
    )

    for key in (
        "universe_count",
        "stock_count",
        "etf_count",
        "bond_count",
    ):

        if data[key] != stats[key]:

            raise RuntimeError(
                f"{key} 統計錯誤"
            )


# ============================================================
# 寫入
# ============================================================

def write_output(
    data: Dict[str, Any],
) -> None:

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_file = (
        OUTPUT_FILE.with_suffix(
            ".json.tmp"
        )
    )

    with temp_file.open(
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

    temp_file.replace(
        OUTPUT_FILE
    )


# ============================================================
# Main
# ============================================================

def main() -> int:

    start = datetime.now()

    section(
        f"台股 AI 選股系統 "
        f"build_universe.py {VERSION}"
    )

    log(
        "Universe：實際可分析台股標的"
    )

    log(
        "名稱來源：TWSE / TPEX 官方優先"
    )

    log(
        "TWSE 股票：t187ap03_L"
    )

    log(
        "TWSE ETF：t187ap47_L"
    )

    log(
        "TPEX 股票：mopsfin_t187ap03_O"
    )

    log(
        "TPEX ETF：mainboard daily quotes 補充"
    )

    log(
        "ISIN：不建立 Universe"
    )

    log(
        "Yahoo：不參與 Universe 決策"
    )

    log(
        f"Universe 上限：{MAX_UNIVERSE}"
    )

    # --------------------------------------------------------
    # 官方資料
    # --------------------------------------------------------

    twse_stocks = (
        load_twse_stocks()
    )

    twse_etf = (
        load_twse_etf()
    )

    tpex_stocks = (
        load_tpex_stocks()
    )

    tpex_quotes = (
        load_tpex_quotes()
    )

    # --------------------------------------------------------
    # 舊資料
    # --------------------------------------------------------

    existing = (
        load_existing_universe()
    )

    # --------------------------------------------------------
    # 合併官方資料
    # --------------------------------------------------------

    section(
        "建立官方 Universe"
    )

    official = merge_official_sources(
        twse_stocks=twse_stocks,
        twse_etf=twse_etf,
        tpex_stocks=tpex_stocks,
        tpex_quotes=tpex_quotes,
    )

    log(
        f"官方 Universe："
        f"{len(official)} 檔"
    )

    # --------------------------------------------------------
    # 舊資料只能補名稱
    # --------------------------------------------------------

    merged = supplement_names(
        official,
        existing,
    )

    # --------------------------------------------------------
    # 最終驗證
    # --------------------------------------------------------

    section(
        "名稱 / Symbol / ETF 最終驗證"
    )

    stocks = final_filter(
        merged
    )

    log(
        f"最終有效："
        f"{len(stocks)} 檔"
    )

    # --------------------------------------------------------
    # Universe 數量安全檢查
    # --------------------------------------------------------

    if len(stocks) > MAX_UNIVERSE:

        raise RuntimeError(
            f"Universe 異常過大："
            f"{len(stocks)} > "
            f"{MAX_UNIVERSE}"
        )

    if len(stocks) < MIN_REASONABLE_UNIVERSE:

        raise RuntimeError(
            f"Universe 異常過少："
            f"{len(stocks)} < "
            f"{MIN_REASONABLE_UNIVERSE}"
        )

    # --------------------------------------------------------
    # 核心股票
    # --------------------------------------------------------

    validate_required_symbols(
        stocks
    )

    # --------------------------------------------------------
    # 統計
    # --------------------------------------------------------

    stats = build_statistics(
        stocks
    )

    # --------------------------------------------------------
    # 建立 JSON
    # --------------------------------------------------------

    data = {
        "schema_version": VERSION,

        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),

        "source": {
            "TWSE_stock": (
                "TWSE_OFFICIAL"
            ),
            "TWSE_etf": (
                "TWSE_ETF_OFFICIAL"
            ),
            "TPEX_stock": (
                "TPEX_OFFICIAL"
            ),
            "TPEX_quotes": (
                "TPEX_OFFICIAL"
            ),
            "existing_universe": (
                "NAME_ONLY_FALLBACK"
            ),
            "yahoo": False,
            "isin_creates_universe": False,
        },

        "universe_count": (
            stats["universe_count"]
        ),

        "stock_count": (
            stats["stock_count"]
        ),

        "etf_count": (
            stats["etf_count"]
        ),

        "bond_count": (
            stats["bond_count"]
        ),

        "market_count": (
            stats["market_count"]
        ),

        "stocks": dict(
            sorted(
                stocks.items(),
                key=lambda x: x[0],
            )
        ),
    }

    # --------------------------------------------------------
    # Schema validation
    # --------------------------------------------------------

    section(
        "Universe Schema Validation"
    )

    validate_schema(
        data
    )

    log(
        "✓ Schema validation：PASS"
    )

    # --------------------------------------------------------
    # 寫入
    # --------------------------------------------------------

    section(
        "寫入 Data/universe.json"
    )

    write_output(
        data
    )

    log(
        f"✓ 已寫入："
        f"{OUTPUT_FILE}"
    )

    # --------------------------------------------------------
    # 完成
    # --------------------------------------------------------

    elapsed = (
        datetime.now()
        - start
    ).total_seconds()

    section(
        "Universe 建立完成"
    )

    log(
        f"Universe："
        f"{stats['universe_count']}"
    )

    log(
        f"股票："
        f"{stats['stock_count']}"
    )

    log(
        f"ETF："
        f"{stats['etf_count']}"
    )

    log(
        f"債券 ETF："
        f"{stats['bond_count']}"
    )

    log(
        f"ETF 總數："
        f"{stats['etf_count'] + stats['bond_count']}"
    )

    log(
        f"TWSE："
        f"{stats['market_count']['TWSE']}"
    )

    log(
        f"TPEX："
        f"{stats['market_count']['TPEX']}"
    )

    log(
        f"耗時："
        f"{elapsed:.1f} 秒"
    )

    log(
        f"輸出："
        f"{OUTPUT_FILE}"
    )

    return 0


# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":

    try:

        sys.exit(
            main()
        )

    except KeyboardInterrupt:

        log(
            "❌ 使用者中止"
        )

        sys.exit(130)

    except Exception as exc:

        section(
            f"❌ build_universe.py "
            f"{VERSION} 執行失敗"
        )

        log(
            f"原因：{exc}"
        )

        sys.exit(1)
