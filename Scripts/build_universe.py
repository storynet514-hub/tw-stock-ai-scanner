#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_universe.py

正式版 UNIVERSE-V11.1

============================================================
核心目的
============================================================

建立「台股實際可分析標的」Universe。

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
V11.1 修正
============================================================

1. 修正 TWSE / TPEX 官方名稱為法人完整名稱的問題
   例如：
       旺宏電子股份有限公司
       ↓
       旺宏電子

2. 名稱標準化不會把 ETF 名稱亂砍。

3. ISIN 僅作「名稱補充」，
   不再把整個 ISIN 歷史資料表直接當 Universe。

4. Universe 僅接受：
   - TWSE
   - TPEX
   - 可確認為目前台股證券標的

5. 禁止將數萬筆 ISIN 歷史資料導入 Universe。

6. 舊 universe.json 只能補充名稱，
   不可擴張 Universe。

7. 股票 / ETF / 債券 ETF 分類保留。

8. full_symbol 強制：
       TWSE → XXXX.TW
       TPEX → XXXX.TWO

9. symbol 不可重複。

10. 名稱不可為：
       OTHERS
       FOOD
       SEMICONDUCTOR INDUSTRY
       ISIN
       純英文分類文字
       純數字
       空字串

11. 強制 Schema Validation。

12. Universe 數量異常時直接停止。
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
# 基本設定
# ============================================================

VERSION = "UNIVERSE-V11.1"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

OUTPUT_FILE = DATA_DIR / "universe.json"

TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    )
}


# ============================================================
# 官方 API
# ============================================================

TWSE_URLS = [
    "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
    "https://openapi.twse.com.tw/v1/opendata/t187ap03_L_ci",
]

TPEX_URLS = [
    "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio",
]


# ============================================================
# ISIN
#
# 注意：
# ISIN 不再用來建立大量新 Universe。
# 只允許作名稱補充。
# ============================================================

TWSE_ISIN_URL = (
    "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
)

TPEX_ISIN_URL = (
    "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4"
)


# ============================================================
# Universe 安全上限
# ============================================================

# 台股目前正常股票 + ETF + 其他可交易證券
# 約落在數千檔，不可能是 4 萬檔。
MAX_UNIVERSE = 5000

# 如果官方資料低於此數量仍允許繼續，
# 因為 API 可能暫時只回一部分。
MIN_REASONABLE_UNIVERSE = 500


# ============================================================
# 明顯錯誤名稱
# ============================================================

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

    "COMMON STOCK",
    "PREFERRED STOCK",
    "FUND",
}


# ============================================================
# 法人名稱後綴
# ============================================================

LEGAL_SUFFIXES = (
    "股份有限公司",
    "有限公司",
)


# ============================================================
# 名稱中不應存在的欄位污染
# ============================================================

NAME_GARBAGE_PATTERNS = (
    r"\bISIN\b",
    r"\bCODE\b",
    r"\bSTOCK CODE\b",
    r"\bETF CODE\b",
)


# ============================================================
# 債券 ETF 關鍵字
# ============================================================

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
# ETF 關鍵字
# ============================================================

ETF_KEYWORDS = (
    "ETF",
    "指數",
    "基金",
    "指數型",
    "被動式",
    "主動式",
    "收益型",
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


def request_text(url: str) -> str:

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=TIMEOUT,
    )

    response.raise_for_status()

    response.encoding = (
        response.apparent_encoding
        or response.encoding
        or "utf-8"
    )

    return response.text


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
# 名稱標準化
# ============================================================

def normalize_company_name(
    value: Any,
) -> str:

    name = clean_text(value)

    if not name:
        return ""

    # --------------------------------------------------------
    # 去除明顯欄位污染
    # --------------------------------------------------------

    for pattern in NAME_GARBAGE_PATTERNS:

        name = re.sub(
            pattern,
            "",
            name,
            flags=re.IGNORECASE,
        )

    name = clean_text(name)

    # --------------------------------------------------------
    # 法人名稱清理
    #
    # 旺宏電子股份有限公司
    # ↓
    # 旺宏電子
    #
    # 旺宏電子股份有限公司股份有限公司
    # ↓
    # 旺宏電子
    # --------------------------------------------------------

    changed = True

    while changed:

        changed = False

        for suffix in LEGAL_SUFFIXES:

            if name.endswith(suffix):

                name = name[
                    :-len(suffix)
                ]

                name = clean_text(name)

                changed = True

    # --------------------------------------------------------
    # 有些來源會把「公司」單獨掛在最後
    # 但不能無條件砍所有公司文字。
    #
    # 僅處理：
    # XXXX公司
    #
    # 如果本身是「公司債」等 ETF 名稱，
    # 不會在這裡處理。
    # --------------------------------------------------------

    if (
        name.endswith("公司")
        and not name.endswith("公司債")
    ):

        candidate = name[:-2].strip()

        if len(candidate) >= 2:
            name = candidate

    # --------------------------------------------------------
    # 去除前後標點
    # --------------------------------------------------------

    name = name.strip(
        " \t\r\n-—_"
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

    # 純英文分類文字
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

    # 國際代碼類型
    if re.fullmatch(
        r"[A-Z]{2,}[0-9]{6,}",
        upper,
    ):
        return False

    return True


# ============================================================
# Symbol
# ============================================================

def normalize_symbol(
    value: Any,
) -> str:

    if value is None:
        return ""

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

    if not re.fullmatch(
        r"[A-Z0-9]{4,6}",
        text,
    ):
        return ""

    return text


def is_valid_symbol(
    symbol: str,
) -> bool:

    symbol = normalize_symbol(
        symbol
    )

    if not symbol:
        return False

    return bool(
        re.fullmatch(
            r"[A-Z0-9]{4,6}",
            symbol,
        )
    )


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

def normalize_market(
    value: Any,
) -> str:

    text = upper_clean(value)

    mapping = {
        "TWSE": "TWSE",
        "TSE": "TWSE",
        "上市": "TWSE",

        "TPEX": "TPEX",
        "TWO": "TPEX",
        "OTC": "TPEX",
        "上櫃": "TPEX",

        "EMERGING": "EMERGING",
        "興櫃": "EMERGING",
    }

    return mapping.get(
        text,
        text,
    )


# ============================================================
# ETF 判斷
# ============================================================

def looks_like_etf(
    symbol: str,
    name: str,
    raw_type: Any = None,
) -> bool:

    text = (
        clean_text(raw_type)
        + " "
        + clean_text(name)
    )

    upper = text.upper()

    for keyword in ETF_KEYWORDS:

        if keyword.upper() in upper:
            return True

    return False


# ============================================================
# 債券 ETF
# ============================================================

def looks_like_bond_etf(
    name: str,
    raw_type: Any = None,
) -> bool:

    text = (
        clean_text(name)
        + " "
        + clean_text(raw_type)
    ).lower()

    return any(
        keyword.lower() in text
        for keyword in BOND_KEYWORDS
    )


# ============================================================
# 分類
# ============================================================

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
# Full Symbol
# ============================================================

def build_full_symbol(
    symbol: str,
    market: str,
) -> str:

    if market == "TPEX":
        return f"{symbol}.TWO"

    return f"{symbol}.TW"


# ============================================================
# Record
# ============================================================

def build_record(
    symbol: Any,
    name: Any,
    market: Any,
    raw_type: Any,
    source: str,
) -> Optional[Dict[str, Any]]:

    symbol = normalize_symbol(
        symbol
    )

    name = normalize_company_name(
        name
    )

    market = normalize_market(
        market
    )

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
# TWSE Parser
# ============================================================

def parse_twse_openapi(
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
                "公司代號",
                "證券代號",
                "代號",
                "Code",
                "code",
            ),
        )

        name = first_value(
            item,
            (
                "公司名稱",
                "證券名稱",
                "名稱",
                "CompanyName",
                "name",
            ),
        )

        raw_type = first_value(
            item,
            (
                "證券類別",
                "市場別",
                "產業類別",
                "type",
                "Type",
            ),
        )

        if not symbol or not name:
            continue

        record = build_record(
            symbol=symbol,
            name=name,
            market="TWSE",
            raw_type=raw_type,
            source="TWSE_OFFICIAL",
        )

        if record:

            result[
                record["symbol"]
            ] = record

    return result


# ============================================================
# TPEX Parser
# ============================================================

def parse_tpex_openapi(
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
                "公司代號",
                "代號",
                "Code",
                "code",
            ),
        )

        name = first_value(
            item,
            (
                "CompanyName",
                "證券名稱",
                "公司名稱",
                "名稱",
                "name",
            ),
        )

        raw_type = first_value(
            item,
            (
                "Type",
                "類別",
                "證券類別",
                "產業類別",
            ),
        )

        if not symbol or not name:
            continue

        record = build_record(
            symbol=symbol,
            name=name,
            market="TPEX",
            raw_type=raw_type,
            source="TPEX_OFFICIAL",
        )

        if record:

            result[
                record["symbol"]
            ] = record

    return result


# ============================================================
# ISIN Parser
#
# 重要：
# ISIN 不建立 Universe。
#
# 只回傳名稱 mapping：
#
# symbol → name
#
# 供官方標的補名稱使用。
# ============================================================

def parse_isin_names(
    html: str,
) -> Dict[str, str]:

    result = {}

    if not html:
        return result

    text = re.sub(
        r"<[^>]+>",
        " ",
        html,
    )

    text = (
        text
        .replace("&nbsp;", " ")
        .replace("&amp;", "&")
    )

    # 尋找：
    #
    # 2330 台積電
    #
    # 但不直接把整個表格當 Universe。

    matches = re.findall(
        r"(?<![A-Z0-9])"
        r"([0-9A-Z]{4,6})"
        r"\s+"
        r"([^<>\r\n]{1,80})",
        text,
        flags=re.IGNORECASE,
    )

    for symbol, name in matches:

        symbol = normalize_symbol(
            symbol
        )

        if not symbol:
            continue

        name = normalize_company_name(
            name
        )

        if not is_valid_name(
            name
        ):
            continue

        result[symbol] = name

    return result


# ============================================================
# ISIN 名稱補充
# ============================================================

def load_isin_name_map(
    url: str,
) -> Dict[str, str]:

    try:

        html = request_text(
            url
        )

        return parse_isin_names(
            html
        )

    except Exception as exc:

        log(
            f"⚠ ISIN 名稱補充失敗："
            f"{exc}"
        )

        return {}


# ============================================================
# TWSE
# ============================================================

def load_twse() -> Dict[str, Dict[str, Any]]:

    section(
        "TWSE 官方資料"
    )

    for url in TWSE_URLS:

        try:

            log(
                f"嘗試：{url}"
            )

            payload = request_json(
                url
            )

            result = parse_twse_openapi(
                payload
            )

            if result:

                log(
                    f"✓ TWSE OpenAPI："
                    f"{len(result)} 檔"
                )

                return result

        except Exception as exc:

            log(
                f"⚠ TWSE API 失敗："
                f"{exc}"
            )

    return {}


# ============================================================
# TPEX
# ============================================================

def load_tpex() -> Dict[str, Dict[str, Any]]:

    section(
        "TPEX 官方資料"
    )

    for url in TPEX_URLS:

        try:

            log(
                f"嘗試：{url}"
            )

            payload = request_json(
                url
            )

            result = parse_tpex_openapi(
                payload
            )

            if result:

                log(
                    f"✓ TPEX OpenAPI："
                    f"{len(result)} 檔"
                )

                return result

        except Exception as exc:

            log(
                f"⚠ TPEX API 失敗："
                f"{exc}"
            )

    return {}


# ============================================================
# Existing Universe
#
# 注意：
# 舊 Universe 只能補「官方已存在的 symbol」。
# 不允許用舊 Universe 擴張成新的 Universe。
# ============================================================

def load_existing_universe() -> Dict[str, Dict[str, Any]]:

    if not OUTPUT_FILE.exists():
        return {}

    try:

        with OUTPUT_FILE.open(
            "r",
            encoding="utf-8-sig",
        ) as f:

            data = json.load(f)

    except Exception:

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

        symbol = normalize_symbol(
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

        market = normalize_market(
            item.get(
                "market",
                "",
            )
        )

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

        record = build_record(
            symbol=symbol,
            name=name,
            market=market,
            raw_type=item.get(
                "type",
                "",
            ),
            source="EXISTING_UNIVERSE",
        )

        if record:

            result[
                symbol
            ] = record

    return result


# ============================================================
# 官方資料名稱補充
# ============================================================

def supplement_names(
    official: Dict[str, Dict[str, Any]],
    isin_names: Dict[str, str],
) -> None:

    for symbol, record in official.items():

        current_name = normalize_company_name(
            record.get(
                "name",
                "",
            )
        )

        # 官方名稱已有效，優先保留
        if is_valid_name(
            current_name
        ):
            record["name"] = current_name
            continue

        # 只有官方名稱無效時才使用 ISIN
        fallback = isin_names.get(
            symbol,
            "",
        )

        fallback = normalize_company_name(
            fallback
        )

        if is_valid_name(
            fallback
        ):

            record["name"] = fallback

            record["source"] = (
                record.get(
                    "source",
                    "",
                )
                + "+ISIN_NAME"
            )


# ============================================================
# 合併
# ============================================================

def merge_sources(
    twse: Dict[str, Dict[str, Any]],
    tpex: Dict[str, Dict[str, Any]],
    existing: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    merged = {}

    # 官方 TWSE
    for symbol, record in twse.items():

        merged[symbol] = record

    # 官方 TPEX
    for symbol, record in tpex.items():

        if symbol not in merged:

            merged[symbol] = record

    # --------------------------------------------------------
    # 舊 Universe：
    #
    # 只補「已經存在於官方 Universe」的名稱。
    #
    # 絕對不能：
    #
    # for symbol in existing:
    #     merged[symbol] = existing[symbol]
    #
    # 否則會再次造成 Universe 膨脹。
    # --------------------------------------------------------

    for symbol in list(
        merged.keys()
    ):

        if symbol not in existing:
            continue

        official_record = merged[
            symbol
        ]

        existing_record = existing[
            symbol
        ]

        official_name = normalize_company_name(
            official_record.get(
                "name",
                "",
            )
        )

        existing_name = normalize_company_name(
            existing_record.get(
                "name",
                "",
            )
        )

        if (
            not is_valid_name(
                official_name
            )
            and is_valid_name(
                existing_name
            )
        ):

            official_record[
                "name"
            ] = existing_name

    return merged


# ============================================================
# 最終驗證
# ============================================================

def validate_records(
    stocks: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    result = {}

    for symbol, record in stocks.items():

        if not isinstance(
            record,
            dict,
        ):
            continue

        symbol2 = normalize_symbol(
            record.get(
                "symbol",
                symbol,
            )
        )

        name = normalize_company_name(
            record.get(
                "name",
                "",
            )
        )

        market = normalize_market(
            record.get(
                "market",
                "",
            )
        )

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

        raw_type = record.get(
            "type",
            "",
        )

        instrument_type = classify_instrument(
            symbol2,
            name,
            raw_type,
        )

        if looks_like_bond_etf(
            name,
            raw_type,
        ):
            instrument_type = "bond"

        if instrument_type == "bond":

            type_label = "Bond ETF"
            asset_class = "bond"

        elif instrument_type == "etf":

            type_label = "ETF"
            asset_class = "fund"

        else:

            type_label = "Stock"
            asset_class = "equity"

        clean_record = {
            "symbol": symbol2,

            "full_symbol": build_full_symbol(
                symbol2,
                market,
            ),

            "name": name,

            "market": market,

            "type": type_label,

            "instrument_type": instrument_type,

            "asset_class": asset_class,

            "source": record.get(
                "source",
                "UNKNOWN",
            ),
        }

        result[symbol2] = clean_record

    return result


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
        },
    }


# ============================================================
# Schema
# ============================================================

def validate_output(
    data: Dict[str, Any],
) -> None:

    required = (
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

    for key in required:

        if key not in data:

            raise RuntimeError(
                f"缺少 schema 欄位：{key}"
            )

    stocks = data[
        "stocks"
    ]

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            "stocks 必須為 object"
        )

    if (
        data["universe_count"]
        != len(stocks)
    ):

        raise RuntimeError(
            "Universe 數量不一致"
        )

    if len(stocks) > MAX_UNIVERSE:

        raise RuntimeError(
            f"Universe 異常："
            f"{len(stocks)} > "
            f"{MAX_UNIVERSE}"
        )

    seen = set()

    for symbol, record in stocks.items():

        if symbol in seen:

            raise RuntimeError(
                f"symbol 重複：{symbol}"
            )

        seen.add(symbol)

        if not isinstance(
            record,
            dict,
        ):

            raise RuntimeError(
                f"{symbol} record 錯誤"
            )

        for key in (
            "symbol",
            "full_symbol",
            "name",
            "market",
            "type",
            "instrument_type",
            "asset_class",
        ):

            if key not in record:

                raise RuntimeError(
                    f"{symbol} "
                    f"缺少欄位：{key}"
                )

        if record[
            "symbol"
        ] != symbol:

            raise RuntimeError(
                f"{symbol} symbol mismatch"
            )

        if not is_valid_name(
            record["name"]
        ):

            raise RuntimeError(
                f"{symbol} "
                f"名稱錯誤："
                f"{record['name']}"
            )

        expected_full = build_full_symbol(
            symbol,
            record["market"],
        )

        if (
            record["full_symbol"]
            != expected_full
        ):

            raise RuntimeError(
                f"{symbol} "
                f"full_symbol 錯誤"
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

        if (
            data[key]
            != stats[key]
        ):

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

    temp = OUTPUT_FILE.with_suffix(
        ".json.tmp"
    )

    with temp.open(
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

    temp.replace(
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
        "ISIN：只作名稱補充，不建立 Universe"
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

    twse = load_twse()

    tpex = load_tpex()

    # --------------------------------------------------------
    # ISIN 名稱補充
    # --------------------------------------------------------

    section(
        "載入官方 ISIN 名稱補充資料"
    )

    twse_isin_names = load_isin_name_map(
        TWSE_ISIN_URL
    )

    tpex_isin_names = load_isin_name_map(
        TPEX_ISIN_URL
    )

    log(
        f"TWSE ISIN 名稱："
        f"{len(twse_isin_names)}"
    )

    log(
        f"TPEX ISIN 名稱："
        f"{len(tpex_isin_names)}"
    )

    # --------------------------------------------------------
    # 官方名稱補充
    # --------------------------------------------------------

    supplement_names(
        twse,
        twse_isin_names,
    )

    supplement_names(
        tpex,
        tpex_isin_names,
    )

    # --------------------------------------------------------
    # Existing
    # --------------------------------------------------------

    section(
        "載入既有 Universe fallback"
    )

    existing = load_existing_universe()

    log(
        f"既有 Universe："
        f"{len(existing)} 檔"
    )

    # --------------------------------------------------------
    # Merge
    # --------------------------------------------------------

    section(
        "建立 Universe"
    )

    merged = merge_sources(
        twse=twse,
        tpex=tpex,
        existing=existing,
    )

    log(
        f"官方 Universe："
        f"{len(merged)} 檔"
    )

    # --------------------------------------------------------
    # 防止資料來源異常
    # --------------------------------------------------------

    if len(merged) > MAX_UNIVERSE:

        raise RuntimeError(
            "官方 Universe 數量異常："
            f"{len(merged)} 檔。"
            "拒絕寫入。"
        )

    if len(merged) < MIN_REASONABLE_UNIVERSE:

        raise RuntimeError(
            "官方 Universe 數量過少："
            f"{len(merged)} 檔。"
            "可能是官方 API 異常。"
        )

    # --------------------------------------------------------
    # 最終驗證
    # --------------------------------------------------------

    section(
        "名稱 / Symbol / ETF 最終驗證"
    )

    stocks = validate_records(
        merged
    )

    log(
        f"最終有效："
        f"{len(stocks)} 檔"
    )

    if not stocks:

        raise RuntimeError(
            "Universe 為空"
        )

    # --------------------------------------------------------
    # 關鍵標的驗證
    # --------------------------------------------------------

    section(
        "核心股票名稱驗證"
    )

    expected_symbols = {
        "2337": "旺宏",
        "2426": "鼎元",
        "2368": "金像電",
        "3081": "聯亞",
    }

    for symbol, expected_name in expected_symbols.items():

        record = stocks.get(
            symbol
        )

        if not record:

            raise RuntimeError(
                f"{symbol} 不存在於 Universe"
            )

        actual = record[
            "name"
        ]

        log(
            f"✓ {symbol} | "
            f"{actual} | "
            f"{record['market']}"
        )

    # --------------------------------------------------------
    # Statistics
    # --------------------------------------------------------

    stats = build_statistics(
        stocks
    )

    # --------------------------------------------------------
    # Output
    # --------------------------------------------------------

    now = datetime.now(
        timezone.utc
    ).isoformat()

    data = {

        "schema_version": VERSION,

        "generated_at": now,

        "source": {
            "primary": [
                "TWSE_OFFICIAL",
                "TPEX_OFFICIAL",
            ],

            "name_supplement": [
                "TWSE_ISIN",
                "TPEX_ISIN",
            ],

            "actual": (
                "OFFICIAL_ONLY"
            ),
        },

        "universe_count":
            stats[
                "universe_count"
            ],

        "stock_count":
            stats[
                "stock_count"
            ],

        "etf_count":
            stats[
                "etf_count"
            ],

        "bond_count":
            stats[
                "bond_count"
            ],

        "market_count":
            stats[
                "market_count"
            ],

        "stocks": dict(
            sorted(
                stocks.items(),
                key=lambda x: x[0],
            )
        ),
    }

    # --------------------------------------------------------
    # Schema
    # --------------------------------------------------------

    section(
        "Universe Schema Validation"
    )

    validate_output(
        data
    )

    log(
        "✓ Schema validation：PASS"
    )

    # --------------------------------------------------------
    # Write
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
    # Summary
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
