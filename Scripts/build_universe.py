#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_universe.py

正式版 UNIVERSE-V11.3

============================================================
核心目的
============================================================

建立「實際可分析的台股股票 + ETF Universe」。

資料流：

官方 TWSE / TPEX 現行標的
        ↓
build_universe.py
        ↓
Data/universe.json
        ↓
fetch_chip.py / analyze_stocks.py
        ↓
後續分析資料

============================================================
V11.3 核心修正
============================================================

1. TWSE 股票：
   https://openapi.twse.com.tw/v1/opendata/t187ap03_L

   正式欄位：
   公司代號
   公司簡稱

2. TWSE ETF：
   https://openapi.twse.com.tw/v1/opendata/t187ap47_L

   正式欄位：
   基金代號
   基金簡稱
   基金類型

3. TPEX 股票：
   https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O

   正式欄位：
   SecuritiesCompanyCode
   CompanyAbbreviation

4. TPEX ETF：
   使用 TPEX 官方行情資料補充，
   僅接受符合 ETF 證券代號規則的標的。

5. 不使用 ISIN 建立 Universe。

6. 不使用 Yahoo 建立 Universe。

7. 舊 universe.json：
   只能補充「官方已存在 symbol」的名稱。
   不得增加 Universe。

8. 名稱來源：

   TWSE 股票：
       公司簡稱

   TPEX 股票：
       CompanyAbbreviation

   TWSE ETF：
       基金簡稱

   TPEX ETF：
       官方行情名稱

   舊 Universe：
       僅作最後 fallback

9. 法人完整名稱一律標準化：

   旺宏電子股份有限公司
       ↓
   旺宏

   鼎元光電科技股份有限公司
       ↓
   鼎元

   金像電子股份有限公司
       ↓
   金像電

10. 3081 聯亞必須存在。

11. Universe 必須包含：
    - TWSE 股票
    - TPEX 股票
    - TWSE ETF
    - TPEX ETF
    - 債券 ETF

12. 排除：
    - 權證
    - 興櫃
    - 舊有價證券
    - ISIN 歷史資料
    - 純分類文字
    - 無法確認市場標的
    - 非股票 / ETF 商品

13. Universe 上限 5000。

14. Universe 最少 1500。
    過少直接停止，避免 API 異常導致空 Universe。

15. universe_count 必須永遠等於 stocks object 實際數量。

16. symbol 必須唯一。

17. 2337 / 2426 / 2368 / 3081 固定驗證。

18. Yahoo 不參與任何 Universe 決策。

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

VERSION = "UNIVERSE-V11.3"

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
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
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

TPEX_QUOTES_FALLBACK_URL = (
    "https://www.tpex.org.tw/openapi/v1/"
    "tpex_mainboard_quotes"
)


# ============================================================
# 舊 Universe
# ============================================================

EXISTING_UNIVERSE_FILE = DATA_DIR / "universe.json"


# ============================================================
# 核心驗證標的
# ============================================================

REQUIRED_SYMBOLS = {
    "2337": "旺宏",
    "2426": "鼎元",
    "2368": "金像電",
    "3081": "聯亞",
}


# ============================================================
# 名稱
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
    "CEOIRU",
    "CEOIRU",
}


# ============================================================
# ETF / Bond
# ============================================================

ETF_NAME_KEYWORDS = (
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
    "bond",
    "bonds",
    "treasury",
    "government bond",
    "corporate bond",
    "investment grade",
    "high yield",
)


BOND_NAME_KEYWORDS = (
    "債券",
    "公司債",
    "金融債",
    "公債",
    "國債",
    "美國國債",
    "美元債",
    "投資級債",
    "投資級",
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

def request_json(
    url: str,
) -> Any:

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=TIMEOUT,
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# 文字
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
            text = text[:-len(suffix)]
            break

    text = text.strip()

    if not re.fullmatch(
        r"[A-Z0-9]{4,6}",
        text,
    ):
        return ""

    return text


def is_stock_symbol(
    symbol: str,
) -> bool:

    symbol = normalize_symbol(symbol)

    return bool(
        re.fullmatch(
            r"\d{4}",
            symbol,
        )
    )


def is_etf_symbol(
    symbol: str,
) -> bool:

    symbol = normalize_symbol(symbol)

    # 台灣 ETF 目前主要為：
    # 5~6 碼、00 開頭。
    #
    # 允許末碼為英文字母，
    # 例如部分債券 ETF：
    # 00679B
    # 00725B
    # 等。

    return bool(
        re.fullmatch(
            r"00\d{3}[A-Z0-9]?",
            symbol,
        )
    )


def is_valid_universe_symbol(
    symbol: str,
) -> bool:

    return (
        is_stock_symbol(symbol)
        or is_etf_symbol(symbol)
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
    # 法人短尾碼
    # --------------------------------------------------------

    for suffix in LEGAL_SHORT_SUFFIXES:

        if name.endswith(suffix):

            name = name[
                :-len(suffix)
            ].strip()

    # --------------------------------------------------------
    # 法人完整尾碼
    # --------------------------------------------------------

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
    # 括號法人
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
    # 前後符號
    # --------------------------------------------------------

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

    # 純英文分類 / 無意義代碼
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

    return True


# ============================================================
# Dictionary
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
# ETF / Bond
# ============================================================

def contains_keyword(
    text: str,
    keywords: Iterable[str],
) -> bool:

    text_lower = clean_text(text).lower()

    for keyword in keywords:

        if keyword.lower() in text_lower:
            return True

    return False


def looks_like_bond_etf(
    symbol: str,
    name: str,
    raw_type: Any = "",
) -> bool:

    symbol = normalize_symbol(symbol)

    name_text = clean_text(name)

    type_text = clean_text(raw_type)

    # 名稱必須明確具有債券屬性
    if not contains_keyword(
        name_text,
        BOND_NAME_KEYWORDS,
    ):
        return False

    # B / C / D 代號規則 + 名稱雙重判斷
    if re.fullmatch(
        r"00\d{3}[BCD]",
        symbol,
    ):
        return True

    # 官方基金類型明確包含債券時也接受
    if contains_keyword(
        type_text,
        BOND_NAME_KEYWORDS,
    ):
        return True

    return False


def looks_like_etf_name(
    name: str,
    raw_type: Any = "",
) -> bool:

    text = (
        clean_text(name)
        + " "
        + clean_text(raw_type)
    )

    return contains_keyword(
        text,
        ETF_NAME_KEYWORDS,
    )


# ============================================================
# Record
# ============================================================

def make_record(
    symbol: Any,
    name: Any,
    market: str,
    instrument_type: str,
    source: str,
) -> Optional[Dict[str, Any]]:

    symbol = normalize_symbol(symbol)

    name = normalize_company_name(name)

    market = clean_text(
        market
    ).upper()

    if not is_valid_universe_symbol(
        symbol
    ):
        return None

    if not is_valid_name(name):
        return None

    if market not in {
        "TWSE",
        "TPEX",
    }:
        return None

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

    result: Dict[str, Dict[str, Any]] = {}

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
            ),
        )

        # ★ 關鍵：
        # TWSE 官方簡稱優先。
        name = first_value(
            item,
            (
                "公司簡稱",
                "證券簡稱",
            ),
        )

        # 不允許公司名稱搶走公司簡稱。
        if not name:

            name = first_value(
                item,
                (
                    "公司名稱",
                    "名稱",
                ),
            )

        symbol = normalize_symbol(symbol)

        if not is_stock_symbol(symbol):
            continue

        record = make_record(
            symbol=symbol,
            name=name,
            market="TWSE",
            instrument_type="stock",
            source="TWSE_STOCK_OFFICIAL",
        )

        if record:

            result[symbol] = record

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
            f"❌ TWSE ETF API 失敗：{exc}"
        )

        return {}

    result: Dict[str, Dict[str, Any]] = {}

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
            ),
        )

        # ★ 官方 ETF 簡稱
        name = first_value(
            item,
            (
                "基金簡稱",
                "證券簡稱",
            ),
        )

        if not name:

            name = first_value(
                item,
                (
                    "基金中文名稱",
                    "基金名稱",
                ),
            )

        raw_type = first_value(
            item,
            (
                "基金類型",
                "證券類別",
            ),
        )

        symbol = normalize_symbol(symbol)

        if not is_etf_symbol(symbol):
            continue

        if not name:
            continue

        instrument_type = (
            "bond"
            if looks_like_bond_etf(
                symbol,
                name,
                raw_type,
            )
            else "etf"
        )

        record = make_record(
            symbol=symbol,
            name=name,
            market="TWSE",
            instrument_type=instrument_type,
            source="TWSE_ETF_OFFICIAL",
        )

        if record:

            result[symbol] = record

    log(
        f"✓ TWSE ETF：{len(result)} 檔"
    )

    return result


# ============================================================
# TPEX 股票
# ============================================================

def load_tpex_stocks() -> Dict[str, Dict[str, Any]]:

    section(
        "TPEX 官方股票資料"
    )

    try:

        payload = request_json(
            TPEX_STOCK_URL
        )

    except Exception as exc:

        log(
            f"❌ TPEX 股票 API 失敗：{exc}"
        )

        return {}

    result: Dict[str, Dict[str, Any]] = {}

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

        # ★ TPEX 正確欄位
        symbol = first_value(
            item,
            (
                "SecuritiesCompanyCode",
                "證券代號",
                "公司代號",
            ),
        )

        # ★ TPEX 正確簡稱欄位
        name = first_value(
            item,
            (
                "CompanyAbbreviation",
                "證券簡稱",
                "公司簡稱",
            ),
        )

        # fallback 僅在簡稱不存在時使用完整名稱
        if not name:

            name = first_value(
                item,
                (
                    "CompanyName",
                    "公司名稱",
                    "名稱",
                ),
            )

        symbol = normalize_symbol(symbol)

        # TPEX 股票只接受 4 碼股票代號。
        if not is_stock_symbol(symbol):
            continue

        record = make_record(
            symbol=symbol,
            name=name,
            market="TPEX",
            instrument_type="stock",
            source="TPEX_STOCK_OFFICIAL",
        )

        if record:

            result[symbol] = record

    log(
        f"✓ TPEX 股票：{len(result)} 檔"
    )

    return result


# ============================================================
# TPEX 行情
# ============================================================

def parse_tpex_quotes(
    payload: Any,
) -> Dict[str, Dict[str, Any]]:

    result: Dict[str, Dict[str, Any]] = {}

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
                "CompanyAbbreviation",
                "CompanyName",
                "證券簡稱",
                "證券名稱",
                "公司簡稱",
                "公司名稱",
                "名稱",
                "Name",
                "name",
            ),
        )

        raw_type = first_value(
            item,
            (
                "SecuritiesType",
                "SecurityType",
                "證券類別",
                "類別",
                "Type",
            ),
        )

        symbol = normalize_symbol(symbol)

        if not symbol:
            continue

        # TPEX 股票已由公司基本資料處理。
        if is_stock_symbol(symbol):
            continue

        # 只允許 ETF。
        if not is_etf_symbol(symbol):
            continue

        if not name:
            continue

        # 名稱沒有 ETF / 基金字樣時，
        # 仍允許 00 開頭的官方 ETF 編碼。
        is_etf = (
            is_etf_symbol(symbol)
            or looks_like_etf_name(
                name,
                raw_type,
            )
        )

        if not is_etf:
            continue

        instrument_type = (
            "bond"
            if looks_like_bond_etf(
                symbol,
                name,
                raw_type,
            )
            else "etf"
        )

        record = make_record(
            symbol=symbol,
            name=name,
            market="TPEX",
            instrument_type=instrument_type,
            source="TPEX_ETF_OFFICIAL_QUOTES",
        )

        if record:

            result[symbol] = record

    return result


def load_tpex_etf() -> Dict[str, Dict[str, Any]]:

    section(
        "TPEX 官方 ETF 補充"
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
                    f"✓ TPEX ETF："
                    f"{len(result)} 檔"
                )

                return result

        except Exception as exc:

            log(
                f"⚠ TPEX ETF 來源失敗："
                f"{exc}"
            )

    log(
        "⚠ TPEX ETF 官方補充資料為 0 檔"
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
            f"⚠ 舊 Universe 讀取失敗："
            f"{exc}"
        )

        return {}

    stocks = {}

    if isinstance(data, dict):

        raw_stocks = data.get(
            "stocks"
        )

        if isinstance(
            raw_stocks,
            dict,
        ):
            stocks = raw_stocks

    result: Dict[str, Dict[str, Any]] = {}

    for symbol, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):
            continue

        normalized_symbol = normalize_symbol(
            item.get(
                "symbol",
                symbol,
            )
        )

        if not is_valid_universe_symbol(
            normalized_symbol
        ):
            continue

        name = normalize_company_name(
            item.get(
                "name",
                "",
            )
        )

        if not is_valid_name(name):
            continue

        market = clean_text(
            item.get(
                "market",
                "",
            )
        ).upper()

        if market not in {
            "TWSE",
            "TPEX",
        }:
            continue

        result[
            normalized_symbol
        ] = {
            "symbol": normalized_symbol,
            "name": name,
            "market": market,
            "type": clean_text(
                item.get(
                    "type",
                    "",
                )
            ),
        }

    log(
        f"既有 Universe："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# 舊資料只作名稱補充
# ============================================================

def supplement_existing_names(
    official: Dict[str, Dict[str, Any]],
    existing: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    result = {
        symbol: dict(record)
        for symbol, record in official.items()
    }

    for symbol, old_record in existing.items():

        # ★ 絕對禁止舊 Universe 增加新標的。
        if symbol not in result:
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

        # 官方名稱有效時永遠保留官方名稱。
        if is_valid_name(
            current_name
        ):
            continue

        if is_valid_name(
            old_name
        ):

            current["name"] = old_name

            current["source"] = (
                current.get(
                    "source",
                    "OFFICIAL",
                )
                + "_NAME_FALLBACK"
            )

    return result


# ============================================================
# 官方 Universe 合併
# ============================================================

def merge_official_sources(
    twse_stocks: Dict[str, Dict[str, Any]],
    twse_etf: Dict[str, Dict[str, Any]],
    tpex_stocks: Dict[str, Dict[str, Any]],
    tpex_etf: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    result: Dict[str, Dict[str, Any]] = {}

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
    # TPEX 股票
    # --------------------------------------------------------

    for symbol, record in tpex_stocks.items():

        result[symbol] = record

    # --------------------------------------------------------
    # TPEX ETF
    # --------------------------------------------------------

    for symbol, record in tpex_etf.items():

        # ETF 不應覆蓋股票。
        if symbol in result:
            continue

        result[symbol] = record

    return result


# ============================================================
# 最終過濾
# ============================================================

def final_filter(
    stocks: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    result: Dict[str, Dict[str, Any]] = {}

    for symbol, record in stocks.items():

        symbol = normalize_symbol(symbol)

        if not is_valid_universe_symbol(
            symbol
        ):
            continue

        name = normalize_company_name(
            record.get(
                "name",
                "",
            )
        )

        if not is_valid_name(name):
            continue

        market = clean_text(
            record.get(
                "market",
                "",
            )
        ).upper()

        if market not in {
            "TWSE",
            "TPEX",
        }:
            continue

        instrument_type = record.get(
            "instrument_type",
            "",
        )

        raw_type = record.get(
            "type",
            "",
        )

        # ----------------------------------------------------
        # 股票
        # ----------------------------------------------------

        if is_stock_symbol(symbol):

            instrument_type = "stock"

        # ----------------------------------------------------
        # ETF
        # ----------------------------------------------------

        elif is_etf_symbol(symbol):

            if instrument_type not in {
                "etf",
                "bond",
            }:

                if looks_like_bond_etf(
                    symbol,
                    name,
                    raw_type,
                ):
                    instrument_type = "bond"

                else:
                    instrument_type = "etf"

        else:
            continue

        # ----------------------------------------------------
        # 建立標準 record
        # ----------------------------------------------------

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
# 核心標的驗證
# ============================================================

def validate_required_symbols(
    stocks: Dict[str, Dict[str, Any]],
) -> None:

    section(
        "核心股票名稱驗證"
    )

    missing = []

    wrong_name = []

    for symbol, expected_name in (
        REQUIRED_SYMBOLS.items()
    ):

        record = stocks.get(symbol)

        if record is None:

            log(
                f"❌ {symbol} 不存在於 Universe"
            )

            missing.append(symbol)

            continue

        actual_name = normalize_company_name(
            record.get(
                "name",
                "",
            )
        )

        market = record.get(
            "market",
            "",
        )

        log(
            f"✓ {symbol} | "
            f"{actual_name} | "
            f"{market}"
        )

        if actual_name != expected_name:

            log(
                f"❌ {symbol} 名稱錯誤："
                f"實際={actual_name} "
                f"預期={expected_name}"
            )

            wrong_name.append(symbol)

    if missing:

        raise RuntimeError(
            "核心股票缺失："
            + ", ".join(missing)
        )

    if wrong_name:

        raise RuntimeError(
            "核心股票名稱錯誤："
            + ", ".join(wrong_name)
        )


# ============================================================
# 統計
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

        instrument_type = record.get(
            "instrument_type"
        )

        market = record.get(
            "market"
        )

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
            "EMERGING": 0,
        },
    }


# ============================================================
# Schema Validation
# ============================================================

def validate_schema(
    data: Dict[str, Any],
) -> None:

    required_keys = (
        "schema_version",
        "generated_at",
        "source",
        "universe_count",
        "stock_count",
        "etf_count",
        "bond_count",
        "market_count",
        "stocks",
    )

    for key in required_keys:

        if key not in data:

            raise RuntimeError(
                f"缺少 Schema 欄位：{key}"
            )

    stocks = data["stocks"]

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            "stocks 必須是 object"
        )

    # --------------------------------------------------------
    # Universe count
    # --------------------------------------------------------

    if data[
        "universe_count"
    ] != len(stocks):

        raise RuntimeError(
            "Universe 數量不一致："
            f"header={data['universe_count']} "
            f"實際={len(stocks)}"
        )

    # --------------------------------------------------------
    # Unique symbols
    # --------------------------------------------------------

    seen = set()

    for symbol, record in stocks.items():

        if symbol in seen:

            raise RuntimeError(
                f"股票代號重複：{symbol}"
            )

        seen.add(symbol)

        if record.get(
            "symbol"
        ) != symbol:

            raise RuntimeError(
                f"{symbol} symbol mismatch"
            )

        if not is_valid_universe_symbol(
            symbol
        ):

            raise RuntimeError(
                f"無效 symbol：{symbol}"
            )

        if not is_valid_name(
            record.get(
                "name",
                "",
            )
        ):

            raise RuntimeError(
                f"{symbol} 名稱錯誤："
                f"{record.get('name')}"
            )

        market = record.get(
            "market"
        )

        if market not in {
            "TWSE",
            "TPEX",
        }:

            raise RuntimeError(
                f"{symbol} market 錯誤："
                f"{market}"
            )

        expected_full = build_full_symbol(
            symbol,
            market,
        )

        if record.get(
            "full_symbol"
        ) != expected_full:

            raise RuntimeError(
                f"{symbol} full_symbol 錯誤："
                f"{record.get('full_symbol')} "
                f"!= {expected_full}"
            )

    # --------------------------------------------------------
    # Statistics
    # --------------------------------------------------------

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
                f"{key} 統計錯誤："
                f"header={data[key]} "
                f"actual={stats[key]}"
            )

    if (
        data["market_count"]
        != stats["market_count"]
    ):

        raise RuntimeError(
            "market_count 統計錯誤"
        )


# ============================================================
# Atomic Write
# ============================================================

def write_output(
    data: Dict[str, Any],
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
        "Universe：實際可分析台股股票 + ETF"
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
        "TPEX ETF：官方行情補充"
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

    twse_stocks = load_twse_stocks()

    if not twse_stocks:
        raise RuntimeError(
            "TWSE 股票官方資料為 0，"
            "禁止建立 Universe"
        )

    twse_etf = load_twse_etf()

    tpex_stocks = load_tpex_stocks()

    if not tpex_stocks:
        raise RuntimeError(
            "TPEX 股票官方資料為 0，"
            "禁止建立 Universe"
        )

    tpex_etf = load_tpex_etf()

    # --------------------------------------------------------
    # 舊 Universe
    # --------------------------------------------------------

    existing = load_existing_universe()

    # --------------------------------------------------------
    # 官方 Universe
    # --------------------------------------------------------

    section(
        "建立官方 Universe"
    )

    official = merge_official_sources(
        twse_stocks=twse_stocks,
        twse_etf=twse_etf,
        tpex_stocks=tpex_stocks,
        tpex_etf=tpex_etf,
    )

    log(
        f"官方 Universe："
        f"{len(official)} 檔"
    )

    # --------------------------------------------------------
    # 舊資料只能補名稱
    # --------------------------------------------------------

    merged = supplement_existing_names(
        official,
        existing,
    )

    # --------------------------------------------------------
    # 最終過濾
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
    # 數量安全檢查
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
            "primary": [
                "TWSE_STOCK_OFFICIAL",
                "TWSE_ETF_OFFICIAL",
                "TPEX_STOCK_OFFICIAL",
                "TPEX_ETF_OFFICIAL_QUOTES",
            ],
            "fallback": [
                "EXISTING_UNIVERSE_NAME_ONLY",
            ],
            "yahoo": False,
            "isin_creates_universe": False,
            "description": (
                "現行可分析台股股票與ETF Universe"
            ),
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
                key=lambda item: item[0],
            )
        ),
    }

    # --------------------------------------------------------
    # Schema
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
        f"EMERGING：0"
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
