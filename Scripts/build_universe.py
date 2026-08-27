#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 - build_universe.py

UNIVERSE-REBUILD-V4

核心設計
============================================================

1. Data/universe.json 是後續資料流程唯一 Universe 來源
2. stocks 必須是 dict
3. Universe 同時包含：
   - 普通股票 STOCK
   - ETF
4. ETF 不限制 4 碼
5. 支援：
   - 股票型 ETF
   - 債券型 ETF
   - 多資產 ETF
   - 期貨型 / 原物料 ETF
   - 貨幣型 ETF
   - REITs
   - 主動式 ETF
   - 槓桿型 ETF
   - 反向型 ETF
6. 排除：
   - ETN
   - 權證
   - 一般債券
   - 公司債
   - 特別股
   - TDR
   - 基金（非 ETF）
   - 其他非 STOCK / ETF 商品
7. TWSE / TPEX 分別使用自己的官方來源
8. 不依賴單一 endpoint
9. 官方 endpoint 非 JSON / 空資料時自動嘗試下一個官方來源
10. 不探測 CMoney
11. 不使用固定 Universe 數量
12. 不使用舊 Universe 補造不存在商品
13. 每筆 Universe 必須具備：
    - symbol
    - full_symbol
    - name
    - market
    - type
    - instrument_type
    - status
14. status == active 是後續流程有效 Universe
15. Atomic Write
16. 寫入後重新讀取驗證
17. Universe Builder 不要求所有商品都是 4 碼
18. STOCK 必須是 4 碼
19. ETF 可為 4 / 5 / 6 碼及英文字尾碼
20. ETF 分類由官方資料欄位、代號與名稱共同判斷
============================================================
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
        "text/csv,"
        "text/html,"
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

    return text


def normalize_key(value: Any) -> str:
    text = clean_text(value).lower()

    return re.sub(
        r"[\s_\-\/\(\)（）:：]+",
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
                    response.text[:180]
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
# RECORD NORMALIZATION
# ============================================================

def fields_data_to_rows(
    fields: Any,
    data: Any,
) -> List[Dict[str, Any]]:

    if not isinstance(fields, list):
        return []

    if not isinstance(data, list):
        return []

    result: List[Dict[str, Any]] = []

    for raw in data:

        if isinstance(raw, dict):

            result.append(raw)
            continue

        if not isinstance(raw, list):
            continue

        row: Dict[str, Any] = {}

        for index, field in enumerate(fields):

            if index >= len(raw):
                break

            row[
                clean_text(field)
            ] = raw[index]

        if row:
            result.append(row)

    return result


def normalize_records(
    payload: Any,
) -> List[Dict[str, Any]]:

    if isinstance(payload, list):

        return [
            row
            for row in payload
            if isinstance(row, dict)
        ]

    if not isinstance(payload, dict):
        return []

    rows = fields_data_to_rows(
        payload.get("fields"),
        payload.get("data"),
    )

    if rows:
        return rows

    tables = payload.get("tables")

    if isinstance(tables, list):

        result: List[Dict[str, Any]] = []

        for table in tables:

            if not isinstance(
                table,
                dict,
            ):
                continue

            result.extend(
                fields_data_to_rows(
                    table.get("fields"),
                    table.get("data"),
                )
            )

        if result:
            return result

    for key in (
        "data",
        "Data",
        "result",
        "results",
        "records",
        "Records",
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
# CODE / NAME
# ============================================================

CODE_ALIASES = [
    "證券代號",
    "證券代碼",
    "代號",
    "股票代號",
    "有價證券代號",
    "SecuritiesCompanyCode",
    "SecurityCode",
    "StockCode",
    "Code",
    "code",
    "symbol",
    "Symbol",
    "ticker",
]


NAME_ALIASES = [
    "證券名稱",
    "證券簡稱",
    "名稱",
    "股票名稱",
    "有價證券名稱",
    "ETF名稱",
    "ETF簡稱",
    "SecuritiesCompanyName",
    "SecurityName",
    "StockName",
    "Name",
    "name",
]


TYPE_ALIASES = [
    "商品類型",
    "證券類別",
    "證券種類",
    "有價證券種類",
    "類別",
    "類型",
    "產品類型",
    "Type",
    "type",
    "InstrumentType",
]


def extract_code(
    row: Dict[str, Any],
) -> str:

    value = field_value(
        row,
        CODE_ALIASES,
    )

    code = clean_code(value)

    if code:
        return code

    for key, value in row.items():

        normalized = normalize_key(key)

        if not any(
            token in normalized
            for token in (
                "代號",
                "代碼",
                "code",
                "symbol",
                "ticker",
            )
        ):
            continue

        text = clean_text(value)

        match = re.search(
            r"(?<![A-Z0-9])([0-9]{4,6}[A-Z]?)(?![A-Z0-9])",
            text.upper(),
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


def extract_declared_type(
    row: Dict[str, Any],
) -> str:

    value = field_value(
        row,
        TYPE_ALIASES,
    )

    return clean_text(value).upper()


# ============================================================
# TEXT CLASSIFICATION
# ============================================================

def row_text(
    row: Dict[str, Any],
) -> str:

    values = [
        clean_text(value)
        for value in row.values()
        if value is not None
    ]

    return " ".join(values).upper()


NON_TARGET_KEYWORDS = (
    "ETN",
    "權證",
    "認購權證",
    "認售權證",
    "公司債",
    "政府債",
    "金融債",
    "一般債券",
    "特別股",
    "存託憑證",
    "TDR",
    "基金",
    "牛熊證",
)


ETF_KEYWORDS = (
    "ETF",
    "指數股票型基金",
    "交易所交易基金",
    "交易所交易基金受益憑證",
    "指數型基金",
    "主動式交易所交易基金",
)


def has_non_target_keyword(
    text: str,
) -> bool:

    for keyword in NON_TARGET_KEYWORDS:

        if keyword.upper() in text:
            return True

    return False


def looks_like_etf(
    code: str,
    name: str,
    row: Dict[str, Any],
) -> bool:

    text = (
        f"{code} "
        f"{name} "
        f"{row_text(row)}"
    ).upper()

    declared = extract_declared_type(
        row
    )

    if "ETF" in declared:
        return True

    if "ETF" in text:
        return True

    for keyword in ETF_KEYWORDS:

        if keyword.upper() in text:
            return True

    # --------------------------------------------------------
    # TWSE ETF 代號規則
    #
    # 新制 ETF 可為：
    # 00980A
    # 00980B
    # 00980D
    # 00980L
    # 00980R
    # 00980T
    # 等
    #
    # 舊 ETF 則可能維持 4 / 5 碼數字。
    # --------------------------------------------------------

    if re.fullmatch(
        r"\d{5}[ABCDKLMRTU]?",
        code,
    ):

        # 這裡不能單靠代號直接把所有
        # 5 碼商品判定為 ETF。
        #
        # 只有明確 ETF 名稱 / 欄位或
        # 官方 ETF 來源資料才進一步確認。
        #
        # 因此此條件只作為輔助。
        if any(
            token in text
            for token in (
                "ETF",
                "指數",
                "基金",
                "主動",
                "槓桿",
                "反向",
            )
        ):
            return True

    # 六碼且第六碼為 ETF 類型碼。
    if re.fullmatch(
        r"\d{5}[ABCDKLMRTU]",
        code,
    ):

        return True

    return False


# ============================================================
# ETF CATEGORY
# ============================================================

def classify_etf_category(
    code: str,
    name: str,
    row: Dict[str, Any],
) -> str:

    text = (
        f"{code} "
        f"{name} "
        f"{row_text(row)}"
    ).upper()

    # --------------------------------------------------------
    # 代號尾碼
    # --------------------------------------------------------

    suffix = ""

    if re.fullmatch(
        r"\d{5}[A-Z]",
        code,
    ):
        suffix = code[-1]

    # --------------------------------------------------------
    # 官方代號分類
    # --------------------------------------------------------

    if suffix == "B":
        return "BOND"

    if suffix == "C":
        return "BOND_FX"

    if suffix == "D":
        return "ACTIVE_BOND"

    if suffix == "A":
        return "ACTIVE_EQUITY"

    if suffix == "T":
        return "MULTI_ASSET"

    if suffix == "L":
        return "LEVERAGED"

    if suffix == "R":
        return "INVERSE"

    if suffix == "U":
        return "FUTURES"

    if suffix == "K":
        return "FX"

    # --------------------------------------------------------
    # 名稱 / 欄位分類
    # --------------------------------------------------------

    if any(
        keyword in text
        for keyword in (
            "債券",
            "固定收益",
            "BOND",
        )
    ):
        return "BOND"

    if any(
        keyword in text
        for keyword in (
            "多資產",
            "MULTI ASSET",
            "MULTI-ASSET",
        )
    ):
        return "MULTI_ASSET"

    if any(
        keyword in text
        for keyword in (
            "REIT",
            "不動產投資信託",
        )
    ):
        return "REIT"

    if any(
        keyword in text
        for keyword in (
            "貨幣",
            "美元",
            "外幣",
            "FX",
        )
    ):
        return "FX"

    if any(
        keyword in text
        for keyword in (
            "期貨",
            "原油",
            "黃金",
            "貴金屬",
            "大宗商品",
        )
    ):
        return "FUTURES"

    if any(
        keyword in text
        for keyword in (
            "槓桿",
            "正2",
            "正向",
            "LEVERAGED",
        )
    ):
        return "LEVERAGED"

    if any(
        keyword in text
        for keyword in (
            "反向",
            "反1",
            "反2",
            "INVERSE",
        )
    ):
        return "INVERSE"

    if any(
        keyword in text
        for keyword in (
            "主動",
            "ACTIVE",
        )
    ):
        return "ACTIVE_EQUITY"

    return "EQUITY"


# ============================================================
# INSTRUMENT CLASSIFICATION
# ============================================================

def classify_instrument(
    code: str,
    name: str,
    row: Dict[str, Any],
) -> Optional[Dict[str, str]]:

    if not code:
        return None

    text = (
        f"{code} "
        f"{name} "
        f"{row_text(row)}"
    ).upper()

    # --------------------------------------------------------
    # 排除非目標商品
    # --------------------------------------------------------

    if has_non_target_keyword(
        text
    ):

        # ETF 名稱同時含有債券等字樣時，
        # 必須優先判斷 ETF。
        #
        # 例如：
        # 債券 ETF
        # 主動式債券 ETF
        if not looks_like_etf(
            code,
            name,
            row,
        ):
            return None

    # --------------------------------------------------------
    # ETF
    # --------------------------------------------------------

    if looks_like_etf(
        code,
        name,
        row,
    ):

        category = classify_etf_category(
            code,
            name,
            row,
        )

        return {
            "type": "ETF",
            "instrument_type": "ETF",
            "category": category,
        }

    # --------------------------------------------------------
    # STOCK
    # --------------------------------------------------------

    # 普通股票維持 4 碼規則。
    if re.fullmatch(
        r"\d{4}",
        code,
    ):

        return {
            "type": "STOCK",
            "instrument_type": "STOCK",
            "category": "EQUITY",
        }

    # --------------------------------------------------------
    # 其他商品
    # --------------------------------------------------------

    return None


# ============================================================
# GENERIC PARSER
# ============================================================

def parse_candidates(
    source_name: str,
    market: str,
    payload: Any,
) -> Dict[str, Dict[str, Any]]:

    rows = normalize_records(
        payload
    )

    result: Dict[str, Dict[str, Any]] = {}

    for row in rows:

        code = extract_code(row)

        if not code:
            continue

        name = extract_name(row)

        classified = classify_instrument(
            code,
            name,
            row,
        )

        if classified is None:
            continue

        result[code] = {
            "symbol": code,
            "name": name or code,
            "market": market,
            "type": classified["type"],
            "instrument_type":
                classified["instrument_type"],
            "category":
                classified["category"],
            "source": source_name,
        }

    return result


# ============================================================
# TWSE COLLECTION
# ============================================================

def collect_twse() -> Dict[str, Dict[str, Any]]:

    section(
        "TWSE 官方來源"
    )

    combined: Dict[str, Dict[str, Any]] = {}

    source_counts: Dict[str, int] = {}

    for source_name, url, params in TWSE_SOURCES:

        log("")
        log(
            f"TWSE 官方來源：{source_name}"
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

        parsed = parse_candidates(
            source_name,
            "TWSE",
            payload,
        )

        source_counts[
            source_name
        ] = len(parsed)

        stock_count = sum(
            1
            for item in parsed.values()
            if item.get("type") == "STOCK"
        )

        etf_count = sum(
            1
            for item in parsed.values()
            if item.get("type") == "ETF"
        )

        log(
            f"  STOCK：{stock_count}"
        )

        log(
            f"  ETF：{etf_count}"
        )

        for code, item in parsed.items():

            # 同一商品以第一次成功官方來源資料為主。
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

    twse_stock = sum(
        1
        for item in combined.values()
        if item.get("type") == "STOCK"
    )

    twse_etf = sum(
        1
        for item in combined.values()
        if item.get("type") == "ETF"
    )

    log(
        f"✓ TWSE STOCK：{twse_stock}"
    )

    log(
        f"✓ TWSE ETF：{twse_etf}"
    )

    log(
        f"✓ TWSE unique：{len(combined)}"
    )

    return combined


# ============================================================
# TPEX COLLECTION
# ============================================================

def collect_tpex() -> Dict[str, Dict[str, Any]]:

    section(
        "TPEx 官方來源"
    )

    combined: Dict[str, Dict[str, Any]] = {}

    source_counts: Dict[str, int] = {}

    for source_name, url, params in TPEX_SOURCES:

        log("")
        log(
            f"TPEx 官方來源：{source_name}"
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

        parsed = parse_candidates(
            source_name,
            "TPEX",
            payload,
        )

        source_counts[
            source_name
        ] = len(parsed)

        stock_count = sum(
            1
            for item in parsed.values()
            if item.get("type") == "STOCK"
        )

        etf_count = sum(
            1
            for item in parsed.values()
            if item.get("type") == "ETF"
        )

        log(
            f"  STOCK：{stock_count}"
        )

        log(
            f"  ETF：{etf_count}"
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

    tpex_stock = sum(
        1
        for item in combined.values()
        if item.get("type") == "STOCK"
    )

    tpex_etf = sum(
        1
        for item in combined.values()
        if item.get("type") == "ETF"
    )

    log(
        f"✓ TPEx STOCK：{tpex_stock}"
    )

    log(
        f"✓ TPEx ETF：{tpex_etf}"
    )

    log(
        f"✓ TPEx unique：{len(combined)}"
    )

    return combined


# ============================================================
# EXISTING METADATA
# ============================================================

def load_existing_metadata() -> Dict[str, Dict[str, Any]]:

    if not UNIVERSE_FILE.exists():
        return {}

    try:

        payload = json.loads(
            UNIVERSE_FILE.read_text(
                encoding="utf-8"
            )
        )

    except Exception:
        return {}

    if not isinstance(payload, dict):
        return {}

    stocks = payload.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):
        return {}

    result: Dict[str, Dict[str, Any]] = {}

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
# OFFICIAL SOURCE GATE
# ============================================================

def official_source_gate(
    twse: Dict[str, Dict[str, Any]],
    tpex: Dict[str, Dict[str, Any]],
) -> bool:

    section(
        "Official Source Gate"
    )

    if not twse:

        log(
            "❌ TWSE 官方來源沒有有效商品"
        )

        return False

    if not tpex:

        log(
            "❌ TPEx 官方來源沒有有效商品"
        )

        return False

    twse_stock = sum(
        1
        for item in twse.values()
        if item.get("type") == "STOCK"
    )

    twse_etf = sum(
        1
        for item in twse.values()
        if item.get("type") == "ETF"
    )

    tpex_stock = sum(
        1
        for item in tpex.values()
        if item.get("type") == "STOCK"
    )

    tpex_etf = sum(
        1
        for item in tpex.values()
        if item.get("type") == "ETF"
    )

    log(
        f"TWSE STOCK：{twse_stock}"
    )

    log(
        f"TWSE ETF：{twse_etf}"
    )

    log(
        f"TPEx STOCK：{tpex_stock}"
    )

    log(
        f"TPEx ETF：{tpex_etf}"
    )

    if twse_stock <= 0:
        log(
            "❌ TWSE STOCK 為 0"
        )
        return False

    if tpex_stock <= 0:
        log(
            "❌ TPEx STOCK 為 0"
        )
        return False

    log(
        "✓ Official Source Gate PASS"
    )

    return True


# ============================================================
# BUILD UNIVERSE
# ============================================================

def build_universe(
    twse: Dict[str, Dict[str, Any]],
    tpex: Dict[str, Dict[str, Any]],
    existing: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    section(
        "建立 Universe"
    )

    combined: Dict[str, Dict[str, Any]] = {}

    combined.update(twse)
    combined.update(tpex)

    stocks: Dict[str, Dict[str, Any]] = {}

    for code in sorted(
        combined.keys()
    ):

        item = combined[code]

        market = clean_text(
            item.get("market")
        ).upper()

        item_type = clean_text(
            item.get("type")
        ).upper()

        instrument_type = clean_text(
            item.get("instrument_type")
        ).upper()

        category = clean_text(
            item.get("category")
        ).upper()

        name = clean_text(
            item.get("name")
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

        # ----------------------------------------------------
        # 核心欄位完全由本次官方來源建立
        # ----------------------------------------------------

        stock: Dict[str, Any] = {
            "symbol": code,
            "full_symbol": (
                code
                + (
                    ".TW"
                    if market == "TWSE"
                    else ".TWO"
                )
            ),
            "name": name or code,
            "market": market,
            "type": item_type,
            "instrument_type":
                instrument_type,
            "category": category,
            "status": "active",
        }

        # ----------------------------------------------------
        # 非核心 metadata 可以沿用
        # ----------------------------------------------------

        for key in (
            "industry",
            "sector",
            "issuer",
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

    symbols = set()
    full_symbols = set()

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

        required = {
            "symbol",
            "full_symbol",
            "name",
            "market",
            "type",
            "instrument_type",
            "status",
        }

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
        # symbol
        # ----------------------------------------------------

        symbol = clean_code(
            item.get("symbol")
        )

        if symbol != code:

            log(
                f"❌ {code}: symbol mismatch"
            )

            errors += 1

        if symbol in symbols:

            log(
                f"❌ duplicate symbol："
                f"{symbol}"
            )

            errors += 1

        symbols.add(symbol)

        # ----------------------------------------------------
        # full symbol
        # ----------------------------------------------------

        full_symbol = clean_text(
            item.get("full_symbol")
        )

        if not full_symbol:

            log(
                f"❌ {code}: full_symbol 空白"
            )

            errors += 1

        if full_symbol in full_symbols:

            log(
                f"❌ duplicate full_symbol："
                f"{full_symbol}"
            )

            errors += 1

        full_symbols.add(
            full_symbol
        )

        # ----------------------------------------------------
        # name
        # ----------------------------------------------------

        if not clean_text(
            item.get("name")
        ):

            log(
                f"❌ {code}: name 空白"
            )

            errors += 1

        # ----------------------------------------------------
        # market
        # ----------------------------------------------------

        market = clean_text(
            item.get("market")
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
        # type
        # ----------------------------------------------------

        item_type = clean_text(
            item.get("type")
        ).upper()

        if item_type not in {
            "STOCK",
            "ETF",
        }:

            log(
                f"❌ {code}: type={item_type}"
            )

            errors += 1

        # ----------------------------------------------------
        # instrument_type
        # ----------------------------------------------------

        instrument_type = clean_text(
            item.get(
                "instrument_type"
            )
        ).upper()

        if instrument_type not in {
            "STOCK",
            "ETF",
        }:

            log(
                f"❌ {code}: "
                f"instrument_type="
                f"{instrument_type}"
            )

            errors += 1

        if instrument_type != item_type:

            log(
                f"❌ {code}: "
                f"type != instrument_type"
            )

            errors += 1

        # ----------------------------------------------------
        # STOCK code
        # ----------------------------------------------------

        if item_type == "STOCK":

            if not re.fullmatch(
                r"\d{4}",
                code,
            ):

                log(
                    f"❌ {code}: "
                    f"STOCK 必須為四碼"
                )

                errors += 1

        # ----------------------------------------------------
        # ETF code
        # ----------------------------------------------------

        if item_type == "ETF":

            if not re.fullmatch(
                r"[0-9]{4,6}[A-Z]?",
                code,
            ):

                log(
                    f"❌ {code}: "
                    f"ETF 代號格式錯誤"
                )

                errors += 1

        # ----------------------------------------------------
        # status
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
        # full symbol
        # ----------------------------------------------------

        expected_suffix = (
            ".TW"
            if market == "TWSE"
            else ".TWO"
        )

        if full_symbol != (
            code + expected_suffix
        ):

            log(
                f"❌ {code}: "
                f"full_symbol 錯誤"
            )

            errors += 1

    if errors:

        log(
            f"❌ Universe Structure "
            f"Gate FAIL：{errors}"
        )

        return False

    stock_count = sum(
        1
        for item in stocks.values()
        if item.get("type") == "STOCK"
    )

    etf_count = sum(
        1
        for item in stocks.values()
        if item.get("type") == "ETF"
    )

    log(
        f"✓ STOCK：{stock_count}"
    )

    log(
        f"✓ ETF：{etf_count}"
    )

    log(
        f"✓ Total：{len(stocks)}"
    )

    log(
        "✓ Required fields"
    )

    log(
        "✓ symbol uniqueness"
    )

    log(
        "✓ full_symbol uniqueness"
    )

    log(
        "✓ STOCK / ETF classification"
    )

    log(
        "✓ instrument_type"
    )

    log(
        "✓ status=active"
    )

    log(
        "✓ Universe Structure Gate PASS"
    )

    return True


# ============================================================
# MARKET VALIDATION
# ============================================================

def validate_market_balance(
    stocks: Dict[str, Dict[str, Any]],
) -> bool:

    section(
        "Market Balance Gate"
    )

    twse_stock = 0
    twse_etf = 0
    tpex_stock = 0
    tpex_etf = 0

    for item in stocks.values():

        market = item.get(
            "market"
        )

        item_type = item.get(
            "type"
        )

        if market == "TWSE":

            if item_type == "STOCK":
                twse_stock += 1

            elif item_type == "ETF":
                twse_etf += 1

        elif market == "TPEX":

            if item_type == "STOCK":
                tpex_stock += 1

            elif item_type == "ETF":
                tpex_etf += 1

    log(
        f"TWSE STOCK：{twse_stock}"
    )

    log(
        f"TWSE ETF：{twse_etf}"
    )

    log(
        f"TPEx STOCK：{tpex_stock}"
    )

    log(
        f"TPEx ETF：{tpex_etf}"
    )

    if twse_stock <= 0:

        log(
            "❌ TWSE STOCK = 0"
        )

        return False

    if tpex_stock <= 0:

        log(
            "❌ TPEx STOCK = 0"
        )

        return False

    # ETF 可以暫時為 0。
    #
    # 不能因某一官方 endpoint 暫時沒有 ETF
    # 就讓整個 Universe BUILD 失敗。
    #
    # 但最後會把實際解析結果列印出來。

    log(
        "✓ Market Balance Gate PASS"
    )

    return True


# ============================================================
# PAYLOAD
# ============================================================

def make_payload(
    stocks: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:

    now = now_tw()

    stock_count = sum(
        1
        for item in stocks.values()
        if item.get("type") == "STOCK"
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
        "version": VERSION,
        "generated_at": now.isoformat(),

        "universe_count":
            len(stocks),

        "statistics": {
            "total":
                len(stocks),
            "stock":
                stock_count,
            "etf":
                etf_count,
            "twse":
                twse_count,
            "tpex":
                tpex_count,
        },

        "source": {
            "policy":
                "TWSE / TPEx official sources only",
            "cmoney":
                False,
            "official_only":
                True,
        },

        "contract": {
            "root":
                "dict",

            "stocks":
                "dict",

            "active_status":
                "status == active",

            "allowed_types": [
                "STOCK",
                "ETF",
            ],

            "allowed_instrument_types": [
                "STOCK",
                "ETF",
            ],

            "ordinary_stock_only":
                False,

            "etf_included":
                True,

            "etf_categories": [
                "EQUITY",
                "ACTIVE_EQUITY",
                "ACTIVE_BOND",
                "BOND",
                "BOND_FX",
                "MULTI_ASSET",
                "FUTURES",
                "FX",
                "REIT",
                "LEVERAGED",
                "INVERSE",
            ],

            "allowed_markets": [
                "TWSE",
                "TPEX",
            ],
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

        log(
            "❌ universe_count mismatch"
        )

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

        log(
            "❌ active_status contract"
        )

        return False

    if contract.get(
        "etf_included"
    ) is not True:

        log(
            "❌ ETF inclusion contract"
        )

        return False

    allowed_types = contract.get(
        "allowed_types"
    )

    if allowed_types != [
        "STOCK",
        "ETF",
    ]:

        log(
            "❌ allowed_types contract"
        )

        return False

    allowed_instrument_types = (
        contract.get(
            "allowed_instrument_types"
        )
    )

    if allowed_instrument_types != [
        "STOCK",
        "ETF",
    ]:

        log(
            "❌ allowed_instrument_types"
        )

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

    temp_file = (
        DATA_DIR
        / "universe.json.tmp"
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
                encoding="utf-8"
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

    stock_count = sum(
        1
        for item in stocks.values()
        if item.get("type") == "STOCK"
    )

    etf_count = sum(
        1
        for item in stocks.values()
        if item.get("type") == "ETF"
    )

    log(
        f"✓ universe.json 重新讀取："
        f"{len(stocks)} 檔"
    )

    log(
        f"✓ STOCK：{stock_count}"
    )

    log(
        f"✓ ETF：{etf_count}"
    )

    log(
        "✓ instrument_type 全部存在"
    )

    log(
        "✓ Universe Contract PASS"
    )

    log(
        "✓ Post Write Verify PASS"
    )

    return True


# ============================================================
# ETF STATISTICS
# ============================================================

def print_etf_statistics(
    stocks: Dict[str, Dict[str, Any]],
) -> None:

    section(
        "ETF CATEGORY STATISTICS"
    )

    categories: Dict[str, int] = {}

    for item in stocks.values():

        if item.get(
            "type"
        ) != "ETF":

            continue

        category = clean_text(
            item.get(
                "category"
            )
        ).upper()

        if not category:
            category = "UNKNOWN"

        categories[
            category
        ] = (
            categories.get(
                category,
                0
            ) + 1
        )

    if not categories:

        log(
            "⚠ ETF = 0"
        )

        return

    for category in sorted(
        categories.keys()
    ):

        log(
            f"  {category}: "
            f"{categories[category]}"
        )


# ============================================================
# SUMMARY
# ============================================================

def print_summary(
    stocks: Dict[str, Dict[str, Any]],
) -> None:

    twse = sum(
        1
        for item in stocks.values()
        if item.get("market") == "TWSE"
    )

    tpex = sum(
        1
        for item in stocks.values()
        if item.get("market") == "TPEX"
    )

    stock_count = sum(
        1
        for item in stocks.values()
        if item.get("type") == "STOCK"
    )

    etf_count = sum(
        1
        for item in stocks.values()
        if item.get("type") == "ETF"
    )

    active = sum(
        1
        for item in stocks.values()
        if item.get("status") == "active"
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
        f"✓ STOCK：{stock_count}"
    )

    log(
        f"✓ ETF：{etf_count}"
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
        "✓ ETF included"
    )

    log(
        "✓ Bond ETF included when officially identified"
    )

    log(
        "✓ Multi-asset ETF included"
    )

    log(
        "✓ Active ETF included"
    )

    log(
        "✓ Leveraged / inverse ETF included"
    )

    log(
        "✓ Official sources only"
    )

    log(
        f"✓ Output：{UNIVERSE_FILE}"
    )

    print_etf_statistics(
        stocks
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
    # 2. TPEX
    # ========================================================

    tpex = collect_tpex()

    # ========================================================
    # 3. Official Gate
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
    # 6. Market
    # ========================================================

    if not validate_market_balance(
        stocks
    ):

        return 1

    # ========================================================
    # 7. Payload
    # ========================================================

    payload = make_payload(
        stocks
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
    # 9. Post Verify
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
        "============================================================"
    )
    log(
        "UNIVERSE BUILD SUCCESS"
    )
    log(
        "============================================================"
    )

    return 0


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    sys.exit(main())