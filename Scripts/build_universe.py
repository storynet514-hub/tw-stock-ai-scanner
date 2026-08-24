#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_universe.py
正式版 UNIVERSE-V11.1

============================================================
定位
============================================================

本程式只負責建立「可分析的完整台股標的 Universe」。

資料流：

    TWSE / TPEX 官方標的
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
V11.1 核心修正
============================================================

【重大修正】

V11.0 發生：

    TWSE ISIN：33702
    TPEX ISIN：10815
    Universe：44517

原因：

    ISIN HTML parser 過度寬鬆，
    將 HTML 中大量非證券資料誤判為股票。

V11.1 改為：

    ISIN 絕對不能自行建立 Universe。

ISIN 的角色只有：

    1. 補充官方名稱
    2. 補充 ETF / Bond ETF 資訊
    3. OpenAPI 缺少名稱時作名稱 fallback

禁止：

    ❌ ISIN parser 大量建立新 symbol
    ❌ HTML 任意 4~6 位英數字串變成股票
    ❌ 將網頁索引 / 日期 / 欄位資料當股票
    ❌ 將 33702 / 10815 類型資料灌入 Universe

============================================================
資料來源優先級
============================================================

第一順位：

    TWSE OpenAPI
    TPEX OpenAPI

第二順位：

    官方 ISIN

但：

    ISIN 不可無限制建立 Universe。

第三順位：

    既有 universe.json

只允許補官方資料缺失。

============================================================
分類
============================================================

stock
etf
bond

============================================================
本程式不負責
============================================================

❌ RSI
❌ MACD
❌ KD
❌ 成交量
❌ DCA
❌ 短線選股
❌ Entry Timing
❌ 籌碼
❌ 今日精選
❌ Top 10
❌ 前端 UI
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
from datetime import datetime, timezone
from html import unescape
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

# 防止 Universe 再次因 parser 錯誤暴增
MIN_EXPECTED_UNIVERSE = 1000
MAX_EXPECTED_UNIVERSE = 6000

# 官方資料正常情況下：
# TWSE + TPEX + ETF 應落在數千級距。
#
# 如果超過此值，直接停止寫入，
# 防止錯誤 Universe 污染後續分析。

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": (
        "application/json,text/plain,text/html,"
        "*/*"
    ),
}


# ============================================================
# 官方來源
# ============================================================

TWSE_URLS = [
    "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
    "https://openapi.twse.com.tw/v1/opendata/t187ap03_L_ci",
]

TPEX_URLS = [
    "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio",
]

TWSE_ISIN_URL = (
    "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
)

TPEX_ISIN_URL = (
    "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4"
)


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

    "LISTED",
    "OTC",
    "TPEX",
    "TWSE",

    "ISIN",
    "CODE",
    "NAME",
    "COMPANY",
}


# ============================================================
# 明顯不是股票名稱的文字
# ============================================================

BAD_NAME_KEYWORDS = (
    "上市日期",
    "上櫃日期",
    "發行日期",
    "到期日期",
    "ISIN CODE",
    "ISIN",
    "證券代號",
    "證券名稱",
    "有價證券",
    "市場別",
    "資料日期",
    "公司代號",
    "公司名稱",
)


# ============================================================
# 債券 ETF 關鍵字
# ============================================================

BOND_KEYWORDS = (
    "債券",
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
    "債",
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
    "指數型",
    "基金",
    "被動式",
    "主動式",
    "收益型",
    "槓桿",
    "反向",
    "期貨",
    "多元資產",
    "平衡",
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

    encoding = (
        response.apparent_encoding
        or response.encoding
        or "utf-8"
    )

    response.encoding = encoding

    return response.text


# ============================================================
# 基本文字處理
# ============================================================

def clean_text(value: Any) -> str:

    if value is None:
        return ""

    text = str(value)

    text = unescape(text)

    text = (
        text
        .replace("\ufeff", "")
        .replace("\u3000", " ")
        .replace("\xa0", " ")
        .replace("\r", " ")
        .replace("\n", " ")
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

    if value is None:
        return ""

    text = upper_clean(value)

    if not text:
        return ""

    # 去除 Yahoo suffix
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

    # 去除可能的前後括號
    text = text.strip("()[]{}")

    # 台股證券代號
    #
    # 主要為：
    # 4 位數
    # 5 位數
    # 6 位特殊代號
    #
    # 保留英數字，避免破壞特殊 ETF 代碼。
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

    # 正常台股代號主要為 4~6 位
    if not re.fullmatch(
        r"[A-Z0-9]{4,6}",
        symbol,
    ):
        return False

    # 避免把日期 / 純長數字誤認為代號
    if symbol.isdigit():

        if len(symbol) not in {
            4,
            5,
            6,
        }:
            return False

        # 明顯日期格式
        if (
            len(symbol) == 6
            and (
                symbol.startswith("19")
                or symbol.startswith("20")
            )
        ):
            return False

    return True


# ============================================================
# 名稱驗證
# ============================================================

def is_valid_name(name: Any) -> bool:

    text = clean_text(name)

    if not text:
        return False

    upper = text.upper()

    if upper in BAD_NAMES:
        return False

    if len(text) > 100:
        return False

    if len(text) < 1:
        return False

    # 純數字不能當名稱
    if text.isdigit():
        return False

    # 明顯欄位標題
    for keyword in BAD_NAME_KEYWORDS:

        if keyword.upper() == upper:
            return False

    # 純英文分類文字
    #
    # 注意：
    # 公司正式英文名稱可能存在，
    # 所以不完全禁止英文。
    #
    # 但明顯分類名稱直接排除。
    if upper in {
        "FOOD",
        "OTHERS",
        "OTHER",
        "SEMICONDUCTOR INDUSTRY",
        "STOCK",
        "ETF",
        "BOND",
    }:
        return False

    # ISIN
    if re.fullmatch(
        r"[A-Z]{2}[A-Z0-9]{9}[0-9]",
        upper,
    ):
        return False

    # 純 ISIN 類型字串
    if re.fullmatch(
        r"[A-Z]{2}[A-Z0-9]{8,12}",
        upper,
    ):
        return False

    return True


# ============================================================
# 欄位搜尋
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

def normalize_market(value: Any) -> str:

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
        clean_text(name)
        + " "
        + clean_text(raw_type)
    ).lower()

    for keyword in ETF_KEYWORDS:

        if keyword.lower() in text:
            return True

    # 常見 ETF 代號區域僅作輔助。
    #
    # 不再使用過度寬鬆的：
    # 1~999 / 8000~9999
    #
    # 因為這會將大量普通證券誤判成 ETF。
    #
    if symbol.isdigit():

        number = int(symbol)

        # 台灣 ETF 常見區域
        if (
            9000 <= number <= 9999
        ):
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

    for keyword in BOND_KEYWORDS:

        if keyword.lower() in text:
            return True

    return False


# ============================================================
# Instrument Type
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

    symbol = normalize_symbol(
        symbol
    )

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

    name = clean_text(
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
        "EMERGING",
    }:
        return None

    instrument_type = classify_instrument(
        symbol,
        name,
        raw_type,
    )

    if instrument_type == "stock":

        type_label = "Stock"
        asset_class = "equity"

    elif instrument_type == "etf":

        type_label = "ETF"
        asset_class = "fund"

    else:

        type_label = "Bond ETF"
        asset_class = "bond"

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
# TWSE OpenAPI
# ============================================================

def parse_twse_openapi(
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
# TPEX OpenAPI
# ============================================================

def parse_tpex_openapi(
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
# HTML table parser
#
# 重要：
#
# V11.1 不再用：
#
#     re.search("4~6位英數 + 空白 + 任意文字")
#
# 直接掃整份 HTML。
#
# 必須先找 table / tr / td 結構。
# ============================================================

def strip_html_tags(
    value: str,
) -> str:

    value = re.sub(
        r"<br\s*/?>",
        " ",
        value,
        flags=re.IGNORECASE,
    )

    value = re.sub(
        r"<[^>]+>",
        " ",
        value,
    )

    return clean_text(
        unescape(value)
    )


def extract_html_rows(
    html: str,
) -> List[List[str]]:

    rows: List[List[str]] = []

    if not html:
        return rows

    table_rows = re.findall(
        r"<tr\b[^>]*>(.*?)</tr>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    for row_html in table_rows:

        cells = re.findall(
            r"<t[dh]\b[^>]*>(.*?)</t[dh]>",
            row_html,
            flags=re.IGNORECASE | re.DOTALL,
        )

        cleaned_cells = [
            strip_html_tags(cell)
            for cell in cells
        ]

        cleaned_cells = [
            cell
            for cell in cleaned_cells
            if cell
        ]

        if cleaned_cells:
            rows.append(
                cleaned_cells
            )

    return rows


# ============================================================
# 判斷是否為真正的 ISIN
# ============================================================

def is_valid_isin(
    value: Any,
) -> bool:

    text = upper_clean(
        value
    )

    if not text:
        return False

    # ISO 6166 ISIN：
    #
    # 12 字元
    # 2 國家碼
    # 9 識別碼
    # 1 check digit
    #
    if not re.fullmatch(
        r"[A-Z]{2}[A-Z0-9]{9}[0-9]",
        text,
    ):
        return False

    return True


# ============================================================
# 找 symbol / name / ISIN 欄位
# ============================================================

def detect_isin_row(
    cells: List[str],
    market: str,
) -> Optional[Tuple[str, str, str]]:

    if not cells:
        return None

    # 先找 ISIN
    isin_index = -1

    for i, cell in enumerate(cells):

        if is_valid_isin(
            cell
        ):
            isin_index = i
            break

    if isin_index < 0:
        return None

    # 找代號
    symbol = ""

    # 通常 ISIN 前方會有：
    #
    # 證券代號及名稱
    #
    # 例如：
    #
    # 2330
    # 台積電
    #
    for i, cell in enumerate(cells):

        normalized = normalize_symbol(
            cell
        )

        if not normalized:
            continue

        if not is_valid_symbol(
            normalized
        ):
            continue

        # 排除明顯日期 / ISIN 欄位
        if is_valid_isin(
            normalized
        ):
            continue

        # 台股代號主要為數字
        if normalized.isdigit():

            if 4 <= len(normalized) <= 6:
                symbol = normalized
                break

    if not symbol:
        return None

    # 找名稱
    #
    # 名稱通常：
    # 1. 在 symbol 後方
    # 2. 或同一個 cell 裡面
    #
    name = ""

    for i, cell in enumerate(cells):

        if i == isin_index:
            continue

        candidate = clean_text(
            cell
        )

        if not candidate:
            continue

        if candidate == symbol:
            continue

        if is_valid_isin(
            candidate
        ):
            continue

        # 避免把日期當名稱
        if re.fullmatch(
            r"\d{4}/\d{1,2}/\d{1,2}",
            candidate,
        ):
            continue

        if re.fullmatch(
            r"\d{4}-\d{1,2}-\d{1,2}",
            candidate,
        ):
            continue

        if not is_valid_name(
            candidate
        ):
            continue

        # 不接受明顯表頭
        if candidate in {
            "有價證券代號及名稱",
            "有價證券代號",
            "證券代號",
            "證券名稱",
            "ISIN Code",
        }:
            continue

        name = candidate

        # 優先選中文名稱
        if re.search(
            r"[\u4e00-\u9fff]",
            candidate,
        ):
            break

    if not name:
        return None

    return (
        symbol,
        name,
        market,
    )


# ============================================================
# 嚴格 ISIN Parser
#
# 重要：
#
# 此函式只能產生「名稱補充索引」。
#
# 不允許它直接建立 Universe。
# ============================================================

def parse_isin_for_name_map(
    html: str,
    market: str,
) -> Dict[str, Dict[str, str]]:

    result: Dict[str, Dict[str, str]] = {}

    rows = extract_html_rows(
        html
    )

    for cells in rows:

        parsed = detect_isin_row(
            cells,
            market,
        )

        if not parsed:
            continue

        symbol, name, parsed_market = parsed

        if not is_valid_symbol(
            symbol
        ):
            continue

        if not is_valid_name(
            name
        ):
            continue

        result[symbol] = {
            "symbol": symbol,
            "name": name,
            "market": parsed_market,
        }

    return result


# ============================================================
# ISIN fallback 載入
#
# 注意：
#
# 回傳的是 name_map。
#
# 不是 Universe。
# ============================================================

def load_isin_name_map(
    url: str,
    market: str,
) -> Dict[str, Dict[str, str]]:

    try:

        html = request_text(
            url
        )

        name_map = parse_isin_for_name_map(
            html,
            market,
        )

        log(
            f"{market} ISIN 名稱補充："
            f"{len(name_map)} 檔"
        )

        # 安全檢查
        #
        # 正常不應該出現幾萬筆。
        #
        if len(name_map) > MAX_EXPECTED_UNIVERSE:

            log(
                f"⚠ {market} ISIN parser "
                f"結果異常："
                f"{len(name_map)}"
            )

            log(
                "⚠ 本次 ISIN 名稱補充停用"
            )

            return {}

        return name_map

    except Exception as exc:

        log(
            f"⚠ {market} ISIN "
            f"補充失敗：{exc}"
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
# 舊 Universe
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

    except Exception as exc:

        log(
            f"⚠ 舊 universe.json "
            f"無法讀取：{exc}"
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

        name = clean_text(
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
            normalized_symbol
        ):
            continue

        if not is_valid_name(
            name
        ):
            continue

        if market not in {
            "TWSE",
            "TPEX",
            "EMERGING",
        }:
            continue

        record = build_record(
            symbol=normalized_symbol,
            name=name,
            market=market,
            raw_type=item.get(
                "type",
                "",
            ),
            source="EXISTING_UNIVERSE_FALLBACK",
        )

        if record:

            result[
                normalized_symbol
            ] = record

    return result


# ============================================================
# 官方名稱補充
#
# 只對「已存在於 Universe」的 symbol 補名稱。
#
# 絕不新增 symbol。
# ============================================================

def apply_name_map(
    stocks: Dict[str, Dict[str, Any]],
    name_map: Dict[str, Dict[str, str]],
) -> int:

    updated = 0

    if not name_map:
        return 0

    for symbol, info in name_map.items():

        # 最重要的安全限制：
        #
        # ISIN 不可以新增 Universe。
        #
        if symbol not in stocks:
            continue

        record = stocks[symbol]

        old_name = clean_text(
            record.get(
                "name",
                "",
            )
        )

        new_name = clean_text(
            info.get(
                "name",
                "",
            )
        )

        if not is_valid_name(
            new_name
        ):
            continue

        # 官方 ISIN 名稱只在：
        #
        # 1. 舊名稱不存在
        # 2. 舊名稱明顯錯誤
        #
        # 時才補。
        #
        # 不覆蓋正常官方名稱。
        if not is_valid_name(
            old_name
        ):

            record["name"] = new_name

            updated += 1

    return updated


# ============================================================
# 合併
# ============================================================

def merge_sources(
    twse: Dict[str, Dict[str, Any]],
    tpex: Dict[str, Dict[str, Any]],
    existing: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    merged: Dict[str, Dict[str, Any]] = {}

    # --------------------------------------------------------
    # TWSE 官方
    # --------------------------------------------------------

    for symbol, record in twse.items():

        merged[symbol] = record

    # --------------------------------------------------------
    # TPEX 官方
    # --------------------------------------------------------

    for symbol, record in tpex.items():

        if symbol not in merged:

            merged[symbol] = record

    # --------------------------------------------------------
    # 舊 Universe
    #
    # 只補不存在的 symbol。
    # --------------------------------------------------------

    for symbol, record in existing.items():

        if symbol not in merged:

            merged[symbol] = record

    return merged


# ============================================================
# 最終驗證
# ============================================================

def validate_records(
    stocks: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    result: Dict[str, Dict[str, Any]] = {}

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

        name = clean_text(
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
            "EMERGING",
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

        if instrument_type == "stock":

            type_label = "Stock"
            asset_class = "equity"

        elif instrument_type == "etf":

            type_label = "ETF"
            asset_class = "fund"

        else:

            type_label = "Bond ETF"
            asset_class = "bond"

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
# Universe 數量安全檢查
# ============================================================

def validate_universe_size(
    stocks: Dict[str, Dict[str, Any]],
) -> None:

    count = len(
        stocks
    )

    log(
        f"Universe 數量安全檢查："
        f"{count}"
    )

    if count < MIN_EXPECTED_UNIVERSE:

        raise RuntimeError(
            "Universe 數量過少："
            f"{count}。"
            "可能官方資料來源異常，"
            "停止寫入，避免覆蓋正常資料。"
        )

    if count > MAX_EXPECTED_UNIVERSE:

        raise RuntimeError(
            "Universe 數量異常過大："
            f"{count}。"
            "疑似資料 parser 錯誤，"
            "停止寫入。"
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
    emerging_count = 0

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

        elif market == "EMERGING":
            emerging_count += 1

    return {
        "universe_count": len(stocks),
        "stock_count": stock_count,
        "etf_count": etf_count,
        "bond_count": bond_count,
        "market_count": {
            "TWSE": twse_count,
            "TPEX": tpex_count,
            "EMERGING": emerging_count,
        },
    }


# ============================================================
# Schema Validation
# ============================================================

def validate_output(
    data: Dict[str, Any],
) -> None:

    required_top_level = (
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

    for key in required_top_level:

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

    if data[
        "universe_count"
    ] != len(stocks):

        raise RuntimeError(
            "universe_count "
            "與 stocks 數量不一致"
        )

    seen = set()

    for symbol, record in stocks.items():

        if symbol in seen:

            raise RuntimeError(
                f"symbol 重複：{symbol}"
            )

        seen.add(
            symbol
        )

        if not isinstance(
            record,
            dict,
        ):

            raise RuntimeError(
                f"{symbol} record 格式錯誤"
            )

        required = (
            "symbol",
            "full_symbol",
            "name",
            "market",
            "type",
            "instrument_type",
            "asset_class",
        )

        for key in required:

            if key not in record:

                raise RuntimeError(
                    f"{symbol} 缺少欄位：{key}"
                )

        if record[
            "symbol"
        ] != symbol:

            raise RuntimeError(
                f"{symbol} symbol mismatch"
            )

        if not is_valid_symbol(
            symbol
        ):

            raise RuntimeError(
                f"{symbol} symbol 驗證失敗"
            )

        if not is_valid_name(
            record["name"]
        ):

            raise RuntimeError(
                f"{symbol} 名稱驗證失敗："
                f"{record['name']}"
            )

        market = normalize_market(
            record["market"]
        )

        if market not in {
            "TWSE",
            "TPEX",
            "EMERGING",
        }:

            raise RuntimeError(
                f"{symbol} market 錯誤："
                f"{record['market']}"
            )

        expected_full = build_full_symbol(
            symbol,
            market,
        )

        if record[
            "full_symbol"
        ] != expected_full:

            raise RuntimeError(
                f"{symbol} full_symbol 錯誤："
                f"{record['full_symbol']} "
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
                f"{key} 統計錯誤："
                f"{data[key]} "
                f"!= {stats[key]}"
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

    temporary = OUTPUT_FILE.with_suffix(
        ".json.tmp"
    )

    with temporary.open(
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

    temporary.replace(
        OUTPUT_FILE
    )


# ============================================================
# 主程式
# ============================================================

def main() -> int:

    start = datetime.now()

    section(
        f"台股 AI 選股系統 "
        f"build_universe.py {VERSION}"
    )

    log(
        "Universe：完整標的宇宙"
    )

    log(
        "名稱來源：TWSE / TPEX 官方優先"
    )

    log(
        "ISIN：只能補名稱，不能建立 Universe"
    )

    log(
        "Yahoo：不參與名稱決策"
    )

    # ========================================================
    # 1. 官方 OpenAPI
    # ========================================================

    twse = load_twse()

    tpex = load_tpex()

    # ========================================================
    # 2. 舊 Universe
    # ========================================================

    section(
        "載入既有 Universe fallback"
    )

    existing = load_existing_universe()

    log(
        f"既有 Universe："
        f"{len(existing)} 檔"
    )

    # ========================================================
    # 3. 合併
    # ========================================================

    section(
        "建立 Universe"
    )

    merged = merge_sources(
        twse=twse,
        tpex=tpex,
        existing=existing,
    )

    log(
        f"OpenAPI TWSE："
        f"{len(twse)}"
    )

    log(
        f"OpenAPI TPEX："
        f"{len(tpex)}"
    )

    log(
        f"Existing fallback："
        f"{len(existing)}"
    )

    log(
        f"合併後："
        f"{len(merged)} 檔"
    )

    # ========================================================
    # 4. ISIN 名稱補充
    #
    # 重大規則：
    #
    # ISIN 不能新增 Universe。
    #
    # ========================================================

    section(
        "載入官方 ISIN 名稱補充資料"
    )

    twse_isin = load_isin_name_map(
        TWSE_ISIN_URL,
        "TWSE",
    )

    tpex_isin = load_isin_name_map(
        TPEX_ISIN_URL,
        "TPEX",
    )

    updated_twse = apply_name_map(
        merged,
        twse_isin,
    )

    updated_tpex = apply_name_map(
        merged,
        tpex_isin,
    )

    log(
        f"TWSE ISIN 實際補名："
        f"{updated_twse}"
    )

    log(
        f"TPEX ISIN 實際補名："
        f"{updated_tpex}"
    )

    # ========================================================
    # 5. 最終驗證
    # ========================================================

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

    # ========================================================
    # 6. Universe 數量防呆
    # ========================================================

    section(
        "Universe 數量安全檢查"
    )

    validate_universe_size(
        stocks
    )

    log(
        "✓ Universe 數量正常"
    )

    # ========================================================
    # 7. 統計
    # ========================================================

    stats = build_statistics(
        stocks
    )

    # ========================================================
    # 8. 建立輸出
    # ========================================================

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
            "fallback": (
                "EXISTING_UNIVERSE"
            ),
            "isin_can_create_symbol": False,
        },

        "universe_count": stats[
            "universe_count"
        ],

        "stock_count": stats[
            "stock_count"
        ],

        "etf_count": stats[
            "etf_count"
        ],

        "bond_count": stats[
            "bond_count"
        ],

        "market_count": stats[
            "market_count"
        ],

        "stocks": dict(
            sorted(
                stocks.items(),
                key=lambda item: item[0],
            )
        ),
    }

    # ========================================================
    # 9. Schema Validation
    # ========================================================

    section(
        "Universe Schema Validation"
    )

    validate_output(
        data
    )

    log(
        "✓ Schema validation：PASS"
    )

    # ========================================================
    # 10. 寫入
    # ========================================================

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

    # ========================================================
    # 11. 最終結果
    # ========================================================

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
        f"興櫃："
        f"{stats['market_count']['EMERGING']}"
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
