#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_universe.py

正式版 UNIVERSE-V11.0

============================================================
定位
============================================================

本程式只負責建立完整標的 Universe。

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
本程式不負責
============================================================

❌ RSI
❌ MACD
❌ KD
❌ 成交量
❌ DCA
❌ Entry Timing
❌ 籌碼
❌ 今日精選
❌ Top 10
❌ 前端 UI

============================================================
V11.0 修正
============================================================

1. TWSE / TPEX 官方資料分開處理
2. 官方「證券簡稱」優先於公司全名
3. 不再把公司名稱欄誤當股票簡稱
4. 不再使用粗暴的 1~999 / 8000~9999 ETF 判斷
5. ETF 依證券代號規則 + 官方類別 + 名稱判斷
6. 債券 ETF：
      B = 債券 ETF
      C = 債券 ETF 外幣加掛
      D = 債券主動式 ETF
7. 股票主動式 ETF：
      A
8. 平衡型 ETF：
      T
9. 一般股票型 ETF：
      00 開頭的數字型 ETF
10. 槓桿 / 反向 / 期貨 ETF 正確分類
11. ISIN 僅作名稱補充，不覆蓋官方名稱
12. 禁止 ISIN 當股票名稱
13. 禁止英文分類文字當股票名稱
14. 禁止 Food / Others / Semiconductor Industry 等錯誤名稱
15. 舊 universe.json 只能最後 fallback
16. 舊 fallback 不得覆蓋官方資料
17. fallback 名稱再次驗證
18. symbol 不可重複
19. full_symbol 強制驗證
20. Universe schema 強制驗證
21. 額外輸出 is_etf / is_bond_etf
22. 額外輸出 category，供 build_ui_data.py 使用
23. 不使用 Yahoo 名稱覆蓋官方名稱
24. 不探測未知 API
25. 官方來源失敗才使用既有資料補缺
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests


# ============================================================
# 基本設定
# ============================================================

VERSION = "UNIVERSE-V11.0"

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
    ),
    "Accept": "application/json,text/html,*/*",
}


# ============================================================
# 官方資料來源
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

    "OTHER",
    "OTHERS",

    "FOOD",
    "SEMICONDUCTOR INDUSTRY",

    "STOCK",
    "ETF",
    "BOND",
    "BOND ETF",

    "UNKNOWN",
    "UNNAMED",
    "NO NAME",

    "CEOGEU",
    "CEOJEU",
    "CEOIEU",
    "CEOIRU",
}


# ============================================================
# 明顯分類文字
# ============================================================

BAD_NAME_PATTERNS = (
    "SEMICONDUCTOR INDUSTRY",
    "SEMICONDUCTOR",
    "FOOD INDUSTRY",
    "FOOD",
    "OTHERS",
    "OTHER",
    "INDUSTRY",
    "STOCK",
    "ETF",
    "BOND ETF",
)


# ============================================================
# 債券關鍵字
# ============================================================

BOND_KEYWORDS = (
    "債券",
    "公司債",
    "金融債",
    "政府債",
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
# ETF 代號尾碼
# ============================================================

ETF_SUFFIX_TYPES = {
    "A": "active_stock",
    "B": "bond",
    "C": "bond",
    "D": "bond",
    "K": "etf",
    "L": "leveraged",
    "M": "leveraged",
    "R": "inverse",
    "S": "inverse",
    "T": "balanced",
    "U": "futures",
    "V": "futures",
}


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
# 基本文字處理
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
        .strip()
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text


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

    # 台股正常標的：
    # 4~6 位數字
    # 或特殊 ETF 英數代號，例如 00980B
    if not re.fullmatch(
        r"[0-9A-Z]{4,6}",
        text,
    ):
        return ""

    return text


def is_valid_symbol(symbol: str) -> bool:

    symbol = normalize_symbol(symbol)

    if not symbol:
        return False

    return bool(
        re.fullmatch(
            r"[0-9A-Z]{4,6}",
            symbol,
        )
    )


# ============================================================
# 名稱判斷
# ============================================================

def contains_chinese(text: str) -> bool:

    return bool(
        re.search(
            r"[\u3400-\u9fff]",
            text,
        )
    )


def looks_like_isin(text: str) -> bool:

    text = upper_clean(text)

    # 標準 ISIN：
    # 2 國家碼 + 9 字元 + check digit
    if re.fullmatch(
        r"[A-Z]{2}[A-Z0-9]{9}[0-9]",
        text,
    ):
        return True

    # 其他明顯國際代碼形式
    if re.fullmatch(
        r"[A-Z]{2,}[0-9]{6,}",
        text,
    ):
        return True

    return False


def looks_like_classification_name(
    text: str,
) -> bool:

    upper = upper_clean(text)

    if upper in BAD_NAMES:
        return True

    for pattern in BAD_NAME_PATTERNS:

        if upper == pattern:
            return True

    # 純英文分類字串
    if re.fullmatch(
        r"[A-Z][A-Z0-9 _./&+\-]{2,}",
        upper,
    ):
        return True

    return False


def is_valid_name(name: Any) -> bool:

    text = clean_text(name)

    if not text:
        return False

    if len(text) > 120:
        return False

    if text.isdigit():
        return False

    if looks_like_isin(text):
        return False

    if looks_like_classification_name(text):
        return False

    # 名稱如果完全是英數代碼，不接受
    if re.fullmatch(
        r"[A-Z0-9._/\-]+",
        upper_clean(text),
    ):
        return False

    return True


# ============================================================
# Dictionary 欄位搜尋
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

        value = clean_text(value)

        if value:
            return value

    return None


# ============================================================
# 市場
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
# HTML Table Parser
# ============================================================

class TableParser(HTMLParser):

    def __init__(self) -> None:

        super().__init__(
            convert_charrefs=True
        )

        self.rows: List[List[str]] = []

        self.current_row: Optional[
            List[str]
        ] = None

        self.current_cell: Optional[
            List[str]
        ] = None

    def handle_starttag(
        self,
        tag: str,
        attrs: List[Any],
    ) -> None:

        tag = tag.lower()

        if tag == "tr":

            self.current_row = []

        elif tag in {
            "td",
            "th",
        }:

            self.current_cell = []

    def handle_data(
        self,
        data: str,
    ) -> None:

        if self.current_cell is not None:

            self.current_cell.append(
                data
            )

    def handle_endtag(
        self,
        tag: str,
    ) -> None:

        tag = tag.lower()

        if tag in {
            "td",
            "th",
        }:

            if (
                self.current_row is not None
                and self.current_cell is not None
            ):

                self.current_row.append(
                    clean_text(
                        "".join(
                            self.current_cell
                        )
                    )
                )

            self.current_cell = None

        elif tag == "tr":

            if (
                self.current_row
                and any(
                    self.current_row
                )
            ):

                self.rows.append(
                    self.current_row
                )

            self.current_row = None


# ============================================================
# ISIN 表格解析
# ============================================================

def parse_isin_html(
    html: str,
    market: str,
) -> Dict[str, Dict[str, Any]]:

    result: Dict[
        str,
        Dict[str, Any],
    ] = {}

    parser = TableParser()

    parser.feed(html)

    for row in parser.rows:

        if not row:
            continue

        symbol = ""
        name = ""

        # ----------------------------------------------------
        # 找「證券代號 + 名稱」
        # ----------------------------------------------------

        for cell in row:

            cell = clean_text(cell)

            if not cell:
                continue

            # 常見格式：
            # 2330 台積電
            # 00980B ...
            match = re.match(
                r"^([0-9A-Z]{4,6})\s+(.+)$",
                cell,
                re.I,
            )

            if match:

                candidate_symbol = (
                    normalize_symbol(
                        match.group(1)
                    )
                )

                candidate_name = clean_text(
                    match.group(2)
                )

                if (
                    is_valid_symbol(
                        candidate_symbol
                    )
                    and is_valid_name(
                        candidate_name
                    )
                ):

                    symbol = candidate_symbol
                    name = candidate_name
                    break

        if not symbol:

            # 有些表格會把代號、名稱拆開
            for index in range(
                len(row) - 1
            ):

                candidate_symbol = (
                    normalize_symbol(
                        row[index]
                    )
                )

                candidate_name = clean_text(
                    row[index + 1]
                )

                if (
                    is_valid_symbol(
                        candidate_symbol
                    )
                    and is_valid_name(
                        candidate_name
                    )
                ):

                    symbol = candidate_symbol
                    name = candidate_name
                    break

        if not symbol or not name:
            continue

        record = make_record(
            symbol=symbol,
            name=name,
            market=market,
            raw_type="",
            source=(
                "TWSE_ISIN_FALLBACK"
                if market == "TWSE"
                else "TPEX_ISIN_FALLBACK"
            ),
        )

        if record:

            result[
                record["symbol"]
            ] = record

    return result


# ============================================================
# ETF 代號判斷
# ============================================================

def symbol_suffix(
    symbol: str,
) -> str:

    symbol = normalize_symbol(
        symbol
    )

    if not symbol:
        return ""

    if symbol[-1].isalpha():
        return symbol[-1]

    return ""


def looks_like_etf_symbol(
    symbol: str,
) -> bool:

    symbol = normalize_symbol(
        symbol
    )

    if not symbol:
        return False

    # --------------------------------------------------------
    # ETF 特殊尾碼
    # --------------------------------------------------------

    suffix = symbol_suffix(
        symbol
    )

    if suffix in ETF_SUFFIX_TYPES:
        return True

    # --------------------------------------------------------
    # 一般型 ETF
    #
    # 新制：
    # 009801
    # 004001
    # 005001
    #
    # 舊制：
    # 0050
    # 00679
    # 00878
    # --------------------------------------------------------

    if symbol.isdigit():

        if (
            len(symbol) in {4, 5, 6}
            and symbol.startswith("00")
        ):
            return True

    return False


# ============================================================
# 名稱 ETF 判斷
# ============================================================

def looks_like_etf_name(
    name: str,
    raw_type: Any = None,
) -> bool:

    text = (
        clean_text(name)
        + " "
        + clean_text(raw_type)
    ).lower()

    keywords = (
        "etf",
        "指數股票型",
        "交易所交易基金",
        "指數型基金",
        "指數基金",
        "基金",
        "主動式",
        "平衡",
    )

    return any(
        keyword in text
        for keyword in keywords
    )


# ============================================================
# 債券 ETF
# ============================================================

def looks_like_bond_symbol(
    symbol: str,
) -> bool:

    suffix = symbol_suffix(
        symbol
    )

    return suffix in {
        "B",
        "C",
        "D",
    }


def looks_like_bond_name(
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
# Instrument classification
# ============================================================

def classify_instrument(
    symbol: str,
    name: str,
    raw_type: Any = None,
) -> str:

    # --------------------------------------------------------
    # 第一優先：代號規則
    # --------------------------------------------------------

    if looks_like_bond_symbol(
        symbol
    ):

        return "bond"

    if looks_like_etf_symbol(
        symbol
    ):

        # A = 主動式股票 ETF
        # T = 平衡型 ETF
        # L/M = 槓桿
        # R/S = 反向
        # U/V = 期貨
        # K = 外幣 ETF

        return "etf"

    # --------------------------------------------------------
    # 第二優先：名稱
    # --------------------------------------------------------

    if looks_like_bond_name(
        name,
        raw_type,
    ):

        return "bond"

    if looks_like_etf_name(
        name,
        raw_type,
    ):

        return "etf"

    return "stock"


# ============================================================
# ETF 子類型
# ============================================================

def classify_etf_type(
    symbol: str,
    name: str,
) -> str:

    suffix = symbol_suffix(
        symbol
    )

    if suffix == "B":
        return "bond"

    if suffix == "C":
        return "bond"

    if suffix == "D":
        return "bond_active"

    if suffix == "A":
        return "active_stock"

    if suffix == "T":
        return "balanced"

    if suffix in {
        "L",
        "M",
    }:
        return "leveraged"

    if suffix in {
        "R",
        "S",
    }:
        return "inverse"

    if suffix in {
        "U",
        "V",
    }:
        return "futures"

    if suffix == "K":
        return "etf"

    if (
        symbol.isdigit()
        and symbol.startswith("00")
    ):
        return "equity"

    if looks_like_bond_name(
        name
    ):
        return "bond"

    return "etf"


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

def make_record(
    symbol: Any,
    name: Any,
    market: Any,
    raw_type: Any,
    source: str,
) -> Optional[
    Dict[str, Any]
]:

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

    if instrument_type == "bond":

        type_label = "Bond ETF"
        asset_class = "bond"
        category = "bond_etf"
        is_etf = True
        is_bond_etf = True
        etf_type = classify_etf_type(
            symbol,
            name,
        )

    elif instrument_type == "etf":

        type_label = "ETF"
        asset_class = "fund"
        category = "etf"
        is_etf = True
        is_bond_etf = False
        etf_type = classify_etf_type(
            symbol,
            name,
        )

    else:

        type_label = "Stock"
        asset_class = "equity"
        category = "stock"
        is_etf = False
        is_bond_etf = False
        etf_type = None

    return {
        "symbol": symbol,

        "full_symbol": build_full_symbol(
            symbol,
            market,
        ),

        "name": name,

        "display_name": name,

        "market": market,

        "type": type_label,

        "instrument_type": instrument_type,

        "asset_class": asset_class,

        "category": category,

        "is_etf": is_etf,

        "is_bond_etf": is_bond_etf,

        "etf_type": etf_type,

        "source": source,
    }


# ============================================================
# TWSE OpenAPI
# ============================================================

def parse_twse_openapi(
    payload: Any,
) -> Dict[str, Dict[str, Any]]:

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

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

        # ----------------------------------------------------
        # 代號
        # ----------------------------------------------------

        symbol = first_value(
            item,
            (
                "證券代號",
                "公司代號",
                "代號",
                "Code",
                "code",
            ),
        )

        # ----------------------------------------------------
        # 名稱：
        #
        # 「證券簡稱」優先
        # 避免把公司全名當 UI 股票名稱
        # ----------------------------------------------------

        name = first_value(
            item,
            (
                "證券簡稱",
                "公司簡稱",
                "證券名稱",
                "公司名稱",
                "名稱",
                "CompanyShortName",
                "CompanyName",
                "name",
            ),
        )

        raw_type = first_value(
            item,
            (
                "證券類別",
                "證券種類",
                "市場別",
                "產業類別",
                "type",
                "Type",
            ),
        )

        if not symbol or not name:
            continue

        record = make_record(
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

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

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

        # TPEX 同樣優先簡稱
        name = first_value(
            item,
            (
                "SecuritiesCompanyName",
                "證券簡稱",
                "公司簡稱",
                "證券名稱",
                "CompanyShortName",
                "CompanyName",
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
                "證券種類",
                "產業類別",
            ),
        )

        if not symbol or not name:
            continue

        record = make_record(
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
# TWSE 載入
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

            records = parse_twse_openapi(
                payload
            )

            if records:

                log(
                    f"✓ TWSE OpenAPI："
                    f"{len(records)} 檔"
                )

                return records

            log(
                "⚠ TWSE API 回傳資料無有效標的"
            )

        except Exception as exc:

            log(
                f"⚠ TWSE API 失敗："
                f"{exc}"
            )

    # --------------------------------------------------------
    # ISIN fallback
    # --------------------------------------------------------

    try:

        log(
            "嘗試 TWSE ISIN fallback"
        )

        html = request_text(
            TWSE_ISIN_URL
        )

        records = parse_isin_html(
            html,
            "TWSE",
        )

        if records:

            log(
                f"✓ TWSE ISIN："
                f"{len(records)} 檔"
            )

            return records

    except Exception as exc:

        log(
            f"⚠ TWSE ISIN 失敗："
            f"{exc}"
        )

    return {}


# ============================================================
# TPEX 載入
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

            records = parse_tpex_openapi(
                payload
            )

            if records:

                log(
                    f"✓ TPEX OpenAPI："
                    f"{len(records)} 檔"
                )

                return records

            log(
                "⚠ TPEX API 回傳資料無有效標的"
            )

        except Exception as exc:

            log(
                f"⚠ TPEX API 失敗："
                f"{exc}"
            )

    try:

        log(
            "嘗試 TPEX ISIN fallback"
        )

        html = request_text(
            TPEX_ISIN_URL
        )

        records = parse_isin_html(
            html,
            "TPEX",
        )

        if records:

            log(
                f"✓ TPEX ISIN："
                f"{len(records)} 檔"
            )

            return records

    except Exception as exc:

        log(
            f"⚠ TPEX ISIN 失敗："
            f"{exc}"
        )

    return {}


# ============================================================
# 官方 ISIN 補充名稱
# ============================================================

def load_isin_supplement(
    market: str,
) -> Dict[str, Dict[str, Any]]:

    url = (
        TWSE_ISIN_URL
        if market == "TWSE"
        else TPEX_ISIN_URL
    )

    try:

        html = request_text(
            url
        )

        return parse_isin_html(
            html,
            market,
        )

    except Exception as exc:

        log(
            f"⚠ {market} ISIN 補充失敗："
            f"{exc}"
        )

        return {}


# ============================================================
# 舊 Universe fallback
# ============================================================

def load_existing_universe() -> Dict[
    str,
    Dict[str, Any]
]:

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
            f"⚠ 舊 universe.json 無法讀取："
            f"{exc}"
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

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

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
                item.get(
                    "display_name",
                    "",
                ),
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

        record = make_record(
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
# 名稱品質修正
# ============================================================

def repair_name(
    record: Dict[str, Any],
    supplement: Dict[
        str,
        Dict[str, Any]
    ],
) -> Dict[str, Any]:

    symbol = record[
        "symbol"
    ]

    current_name = clean_text(
        record.get(
            "name",
            "",
        )
    )

    # 官方名稱有效 → 絕不覆蓋
    if is_valid_name(
        current_name
    ):

        record[
            "display_name"
        ] = current_name

        return record

    # 官方名稱無效 → 才允許 ISIN 補充
    fallback = supplement.get(
        symbol
    )

    if fallback:

        fallback_name = clean_text(
            fallback.get(
                "name",
                "",
            )
        )

        if is_valid_name(
            fallback_name
        ):

            record[
                "name"
            ] = fallback_name

            record[
                "display_name"
            ] = fallback_name

            record[
                "source"
            ] = (
                record.get(
                    "source",
                    "",
                )
                + "+ISIN_NAME"
            )

    return record


# ============================================================
# 合併來源
# ============================================================

def merge_sources(
    twse: Dict[str, Dict[str, Any]],
    tpex: Dict[str, Dict[str, Any]],
    twse_supplement: Dict[
        str,
        Dict[str, Any]
    ],
    tpex_supplement: Dict[
        str,
        Dict[str, Any]
    ],
    existing: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    merged: Dict[
        str,
        Dict[str, Any]
    ] = {}

    # --------------------------------------------------------
    # 1. TWSE 官方
    # --------------------------------------------------------

    for symbol, record in twse.items():

        merged[symbol] = repair_name(
            dict(record),
            twse_supplement,
        )

    # --------------------------------------------------------
    # 2. TPEX 官方
    # --------------------------------------------------------

    for symbol, record in tpex.items():

        if symbol not in merged:

            merged[symbol] = repair_name(
                dict(record),
                tpex_supplement,
            )

    # --------------------------------------------------------
    # 3. 官方 ISIN 補充
    #
    # 只補不存在的標的
    # --------------------------------------------------------

    for symbol, record in (
        twse_supplement.items()
    ):

        if symbol not in merged:

            merged[symbol] = record

    for symbol, record in (
        tpex_supplement.items()
    ):

        if symbol not in merged:

            merged[symbol] = record

    # --------------------------------------------------------
    # 4. 舊 Universe
    #
    # 最後才使用
    # --------------------------------------------------------

    for symbol, record in existing.items():

        if symbol not in merged:

            merged[symbol] = record

    return merged


# ============================================================
# 最終分類重新計算
# ============================================================

def recalculate_record(
    record: Dict[str, Any],
) -> Dict[str, Any]:

    symbol = normalize_symbol(
        record.get(
            "symbol",
            "",
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

    raw_type = record.get(
        "type",
        "",
    )

    instrument_type = classify_instrument(
        symbol,
        name,
        raw_type,
    )

    if instrument_type == "bond":

        type_label = "Bond ETF"
        asset_class = "bond"
        category = "bond_etf"
        is_etf = True
        is_bond_etf = True

    elif instrument_type == "etf":

        type_label = "ETF"
        asset_class = "fund"
        category = "etf"
        is_etf = True
        is_bond_etf = False

    else:

        type_label = "Stock"
        asset_class = "equity"
        category = "stock"
        is_etf = False
        is_bond_etf = False

    return {
        "symbol": symbol,

        "full_symbol": build_full_symbol(
            symbol,
            market,
        ),

        "name": name,

        "display_name": name,

        "market": market,

        "type": type_label,

        "instrument_type": instrument_type,

        "asset_class": asset_class,

        "category": category,

        "is_etf": is_etf,

        "is_bond_etf": is_bond_etf,

        "etf_type": (
            classify_etf_type(
                symbol,
                name,
            )
            if is_etf
            else None
        ),

        "source": record.get(
            "source",
            "UNKNOWN",
        ),
    }


# ============================================================
# 最終驗證
# ============================================================

def validate_records(
    stocks: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    for symbol, record in stocks.items():

        if not isinstance(
            record,
            dict,
        ):
            continue

        normalized_symbol = normalize_symbol(
            record.get(
                "symbol",
                symbol,
            )
        )

        if not is_valid_symbol(
            normalized_symbol
        ):
            continue

        name = clean_text(
            record.get(
                "name",
                "",
            )
        )

        if not is_valid_name(
            name
        ):
            continue

        market = normalize_market(
            record.get(
                "market",
                "",
            )
        )

        if market not in {
            "TWSE",
            "TPEX",
            "EMERGING",
        }:
            continue

        cleaned = recalculate_record(
            {
                **record,
                "symbol": normalized_symbol,
                "name": name,
                "market": market,
            }
        )

        result[
            normalized_symbol
        ] = cleaned

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
    emerging_count = 0

    active_stock_count = 0
    balanced_count = 0
    leveraged_count = 0
    inverse_count = 0
    futures_count = 0

    for record in stocks.values():

        instrument_type = record.get(
            "instrument_type"
        )

        market = record.get(
            "market"
        )

        etf_type = record.get(
            "etf_type"
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

        if etf_type == "active_stock":
            active_stock_count += 1

        elif etf_type == "balanced":
            balanced_count += 1

        elif etf_type == "leveraged":
            leveraged_count += 1

        elif etf_type == "inverse":
            inverse_count += 1

        elif etf_type == "futures":
            futures_count += 1

    return {
        "universe_count": len(stocks),

        "stock_count": stock_count,

        "etf_count": etf_count,

        "bond_count": bond_count,

        "etf_total": (
            etf_count
            + bond_count
        ),

        "etf_subtypes": {
            "active_stock": active_stock_count,
            "balanced": balanced_count,
            "leveraged": leveraged_count,
            "inverse": inverse_count,
            "futures": futures_count,
        },

        "market_count": {
            "TWSE": twse_count,
            "TPEX": tpex_count,
            "EMERGING": emerging_count,
        },
    }


# ============================================================
# Schema 驗證
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
            "universe_count 與 stocks 數量不一致"
        )

    for symbol, record in stocks.items():

        if not isinstance(
            record,
            dict,
        ):

            raise RuntimeError(
                f"{symbol} record 格式錯誤"
            )

        required_fields = (
            "symbol",
            "full_symbol",
            "name",
            "display_name",
            "market",
            "type",
            "instrument_type",
            "asset_class",
            "category",
            "is_etf",
            "is_bond_etf",
        )

        for field in required_fields:

            if field not in record:

                raise RuntimeError(
                    f"{symbol} 缺少欄位："
                    f"{field}"
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
                f"{symbol} 名稱驗證失敗："
                f"{record['name']}"
            )

        if (
            record["display_name"]
            != record["name"]
        ):

            raise RuntimeError(
                f"{symbol} display_name mismatch"
            )

        expected_full_symbol = (
            build_full_symbol(
                symbol,
                record["market"],
            )
        )

        if (
            record["full_symbol"]
            != expected_full_symbol
        ):

            raise RuntimeError(
                f"{symbol} full_symbol 錯誤："
                f"{record['full_symbol']} "
                f"!= "
                f"{expected_full_symbol}"
            )

        # ----------------------------------------------------
        # ETF schema
        # ----------------------------------------------------

        if record["is_etf"]:

            if record[
                "instrument_type"
            ] not in {
                "etf",
                "bond",
            }:

                raise RuntimeError(
                    f"{symbol} ETF instrument_type 錯誤"
                )

        # ----------------------------------------------------
        # Bond ETF schema
        # ----------------------------------------------------

        if record[
            "is_bond_etf"
        ]:

            if record[
                "instrument_type"
            ] != "bond":

                raise RuntimeError(
                    f"{symbol} 債券 ETF instrument_type 錯誤"
                )

            if record[
                "category"
            ] != "bond_etf":

                raise RuntimeError(
                    f"{symbol} bond category 錯誤"
                )

            if not record[
                "is_etf"
            ]:

                raise RuntimeError(
                    f"{symbol} 債券 ETF 必須 is_etf=true"
                )

    stats = build_statistics(
        stocks
    )

    for field in (
        "universe_count",
        "stock_count",
        "etf_count",
        "bond_count",
    ):

        if data[field] != stats[field]:

            raise RuntimeError(
                f"{field} 統計錯誤："
                f"{data[field]} "
                f"!= "
                f"{stats[field]}"
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
        "ETF：依官方證券代號規則分類"
    )

    log(
        "債券 ETF：B / C / D + 名稱雙重判斷"
    )

    log(
        "Yahoo：不參與名稱決策"
    )

    # ========================================================
    # 1. 官方資料
    # ========================================================

    twse = load_twse()

    tpex = load_tpex()

    # ========================================================
    # 2. ISIN 補充
    # ========================================================

    section(
        "載入官方 ISIN 名稱補充資料"
    )

    twse_supplement = (
        load_isin_supplement(
            "TWSE"
        )
    )

    log(
        f"TWSE ISIN 補充："
        f"{len(twse_supplement)} 檔"
    )

    tpex_supplement = (
        load_isin_supplement(
            "TPEX"
        )
    )

    log(
        f"TPEX ISIN 補充："
        f"{len(tpex_supplement)} 檔"
    )

    # ========================================================
    # 3. 舊 Universe
    # ========================================================

    section(
        "載入既有 Universe fallback"
    )

    existing = (
        load_existing_universe()
    )

    log(
        f"既有 Universe："
        f"{len(existing)} 檔"
    )

    # ========================================================
    # 4. 合併
    # ========================================================

    section(
        "建立 Universe"
    )

    merged = merge_sources(
        twse=twse,
        tpex=tpex,
        twse_supplement=(
            twse_supplement
        ),
        tpex_supplement=(
            tpex_supplement
        ),
        existing=existing,
    )

    log(
        f"合併後："
        f"{len(merged)} 檔"
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

    if not stocks:

        raise RuntimeError(
            "Universe 為空，停止寫入。"
        )

    # ========================================================
    # 6. 統計
    # ========================================================

    stats = build_statistics(
        stocks
    )

    # ========================================================
    # 7. 建立輸出
    # ========================================================

    generated_at = (
        datetime.now(
            timezone.utc
        ).isoformat()
    )

    data = {
        "schema_version": VERSION,

        "generated_at": generated_at,

        "source": {
            "primary": [
                "TWSE_OFFICIAL",
                "TPEX_OFFICIAL",
            ],

            "name_priority": [
                "TWSE_TPEX_OFFICIAL",
                "OFFICIAL_ISIN",
                "EXISTING_UNIVERSE_FALLBACK",
            ],

            "yahoo_used": False,
        },

        "universe_count": (
            stats[
                "universe_count"
            ]
        ),

        "stock_count": (
            stats[
                "stock_count"
            ]
        ),

        "etf_count": (
            stats[
                "etf_count"
            ]
        ),

        "bond_count": (
            stats[
                "bond_count"
            ]
        ),

        "etf_total": (
            stats[
                "etf_total"
            ]
        ),

        "etf_subtypes": (
            stats[
                "etf_subtypes"
            ]
        ),

        "market_count": (
            stats[
                "market_count"
            ]
        ),

        "stocks": dict(
            sorted(
                stocks.items(),
                key=lambda item: (
                    item[0]
                ),
            )
        ),
    }

    # ========================================================
    # 8. Schema validation
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
    # 9. 寫入
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
    # 10. 完成
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
        f"ETF 總數："
        f"{stats['etf_total']}"
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
        "ETF 子類型："
    )

    for key, value in (
        stats[
            "etf_subtypes"
        ].items()
    ):

        log(
            f"  {key}：{value}"
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
