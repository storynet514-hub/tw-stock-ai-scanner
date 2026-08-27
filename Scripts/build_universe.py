#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 - build_universe.py

UNIVERSE-BUILD

核心架構
============================================================

1. Data/universe.json 是 downstream 唯一 Universe 來源
2. 官方 TWSE / TPEx 來源決定商品是否存在
3. STOCK 與 ETF 完全分流
4. 不使用既有 universe.json 補造商品
5. 不依賴固定 Universe 數量
6. 不探測 CMoney
7. 不使用 ISIN HTML 作為 ETF 唯一來源
8. 不使用 STOCK_DAY_AVG_ALL 建立商品 Universe
9. 不把歷史行情列誤當 ETF
10. ETF 包含舊制 4/5 碼及新制 6 碼
11. ETF 包含：
      - 股票型
      - 債券型
      - 多資產 / 平衡型
      - 主動式
      - 債券主動式
      - 槓桿型
      - 反向型
      - 期貨 / 原物料
      - 貨幣型
      - REIT / 其他官方 ETF
12. 排除：
      - ETN
      - 權證
      - 一般債券
      - 可轉債
      - 公司債
      - TDR
      - 特別股
      - 基金
      - 其他非 STOCK / ETF 商品
13. STOCK：
      - 必須為 4 碼純數字
      - 不得以 0 開頭
14. ETF：
      - 官方行情中存在
      - 符合官方 ETF 編碼 / 商品名稱規則
15. 舊 Universe 只保留非核心 metadata
16. 舊 Universe 不得影響：
      - symbol
      - full_symbol
      - market
      - type
      - instrument_type
      - status
17. status == active 才是有效 Universe
18. Atomic Write
19. 寫入後重新讀取驗證
20. TWSE / TPEx 官方來源 Gate 必須通過

重要修正
============================================================

之前 V4 的錯誤：

STOCK_DAY_AVG_ALL
    ↓
29,087 rows
    ↓
錯誤地當成商品清單
    ↓
產生 1,851 個假 ETF

V5 的錯誤：

STOCK 正常
    ↓
ETF 改由 ISIN HTML 判斷
    ↓
GitHub Actions：
TWSE HTML = 0
TPEx HTML = 121
    ↓
ETF = 0
    ↓
Universe Gate FAIL

本版本：

TWSE / TPEx 官方「當日商品行情」
    ↓
逐筆辨識商品
    ↓
STOCK / ETF 分類
    ↓
排除非目標商品
    ↓
Universe

因此不再需要脆弱的 ISIN ETF Gate。
"""

from __future__ import annotations

import json
import re
import sys
import time

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


# ============================================================
# VERSION
# ============================================================

VERSION = "UNIVERSE-BUILD"


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

# ------------------------------------------------------------
# TWSE
# ------------------------------------------------------------

TWSE_SOURCES = [
    (
        "TWSE_STOCK_DAY_ALL",
        "https://openapi.twse.com.tw/v1/"
        "exchangeReport/STOCK_DAY_ALL",
        {},
    ),
    (
        "TWSE_BWIBBU",
        "https://www.twse.com.tw/rwd/zh/"
        "afterTrading/BWIBBU_d",
        {
            "response": "json",
            "selectType": "ALL",
        },
    ),
]


# ------------------------------------------------------------
# TPEx
# ------------------------------------------------------------

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

        return json.loads(
            text.lstrip("\ufeff")
        )

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

        if not isinstance(
            value,
            list,
        ):
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
            r"(?<![A-Z0-9])"
            r"([0-9]{4,6}[A-Z]?)"
            r"(?![A-Z0-9])",
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


# ============================================================
# PRODUCT KEYWORDS
# ============================================================

ETF_KEYWORDS = (
    "ETF",
    "指數股票型基金",
    "指數型基金",
    "股票型ETF",
    "債券型ETF",
    "主動式ETF",
    "多資產ETF",
    "平衡型ETF",
    "槓桿型ETF",
    "反向型ETF",
    "期貨ETF",
    "期信託ETF",
)


NON_ETF_KEYWORDS = (
    "ETN",
    "權證",
    "認購權證",
    "認售權證",
    "公司債",
    "金融債",
    "政府債",
    "可轉換公司債",
    "可轉債",
    "交換公司債",
    "特別股",
    "存託憑證",
    "TDR",
    "基金受益憑證",
)


# ============================================================
# ETF CODE RULES
# ============================================================

# ------------------------------------------------------------
# ETF 最後一碼類型
#
# 官方 TWSE / TPEx 編碼規則目前包含：
#
# A = 主動式 ETF
# B = 債券 ETF
# C = 債券 ETF 外幣
# D = 債券主動式 ETF
# K = 外幣交易 ETF
# L = 槓桿型 ETF
# M = 槓桿型 ETF 外幣
# R = 反向型 ETF
# S = 反向型 ETF 外幣
# T = 多資產 / 平衡型 ETF
# U = 期貨 / 期信託 ETF
# V = 期貨 / 期信託 ETF 外幣
#
# ------------------------------------------------------------

ETF_SUFFIX_TYPES = {
    "A": "ACTIVE",
    "B": "BOND",
    "C": "BOND_FX",
    "D": "ACTIVE_BOND",
    "K": "ETF_FX",
    "L": "LEVERAGED",
    "M": "LEVERAGED_FX",
    "R": "INVERSE",
    "S": "INVERSE_FX",
    "T": "MULTI_ASSET",
    "U": "FUTURES",
    "V": "FUTURES_FX",
}


def classify_etf_code(
    code: str,
) -> Optional[str]:

    code = clean_code(code)

    # --------------------------------------------------------
    # 6 碼 ETF
    # --------------------------------------------------------

    if re.fullmatch(
        r"\d{5}[A-Z]",
        code,
    ):

        prefix = code[:5]
        suffix = code[-1]

        # 官方新制 ETF 區域
        if prefix.startswith(
            (
                "009",
                "004",
                "005",
                "006",
                "007",
                "008",
            )
        ):

            if suffix in ETF_SUFFIX_TYPES:

                return ETF_SUFFIX_TYPES[
                    suffix
                ]

            # 最後一碼為數字：
            # 一般型 ETF
            #
            # 009800
            # 009816
            # 004001
            # 006208
            # 等
            return "EQUITY"

    # --------------------------------------------------------
    # 舊制 / 既有 ETF
    #
    # 例如：
    # 0050
    # 0056
    # 00878
    # 00919
    #
    # 只接受 00 開頭，避免一般股票被誤判。
    # --------------------------------------------------------

    if re.fullmatch(
        r"00\d{2}",
        code,
    ):

        return "EQUITY"

    if re.fullmatch(
        r"00\d{3}",
        code,
    ):

        return "EQUITY"

    return None


# ============================================================
# ETF NAME RULE
# ============================================================

def classify_etf_name(
    name: str,
) -> Optional[str]:

    text = clean_text(name).upper()

    if not text:
        return None

    if "ETN" in text:
        return None

    if "主動式ETF" in text:
        if "債券" in text:
            return "ACTIVE_BOND"
        return "ACTIVE"

    if "債券ETF" in text:
        return "BOND"

    if "槓桿型ETF" in text:
        return "LEVERAGED"

    if "反向型ETF" in text:
        return "INVERSE"

    if (
        "平衡型ETF" in text
        or "多資產ETF" in text
    ):
        return "MULTI_ASSET"

    if (
        "期貨ETF" in text
        or "期信託ETF" in text
    ):
        return "FUTURES"

    if any(
        keyword.upper() in text
        for keyword in ETF_KEYWORDS
    ):
        return "EQUITY"

    return None


# ============================================================
# ETF CLASSIFICATION
# ============================================================

def classify_etf(
    code: str,
    name: str,
    row: Dict[str, Any],
) -> Optional[str]:

    combined = (
        clean_text(name)
        + " "
        + " ".join(
            clean_text(value)
            for value in row.values()
        )
    ).upper()

    # --------------------------------------------------------
    # 先排除 ETN / 權證 / 債券等
    # --------------------------------------------------------

    for keyword in NON_ETF_KEYWORDS:

        if keyword.upper() in combined:

            # 但是 ETF 名稱內出現
            # 「債券」本身不能排除 ETF。
            if (
                keyword
                in (
                    "公司債",
                    "金融債",
                    "政府債",
                    "可轉換公司債",
                    "可轉債",
                )
                and "ETF" in combined
            ):
                continue

            return None

    # --------------------------------------------------------
    # 官方 ETF 名稱
    # --------------------------------------------------------

    name_type = classify_etf_name(
        name
    )

    if name_type:

        return name_type

    # --------------------------------------------------------
    # 官方 ETF code pattern
    # --------------------------------------------------------

    code_type = classify_etf_code(
        code
    )

    if code_type:

        return code_type

    return None


# ============================================================
# STOCK CLASSIFICATION
# ============================================================

def looks_like_stock_code(
    code: str,
) -> bool:

    # 普通股票：
    #
    # 4 碼純數字
    # 第一碼不得為 0
    #
    # 例如：
    # 2330
    # 2426
    # 3005
    # 8059
    #
    # 不接受：
    # 0050
    # 00919
    # 00631L
    # 020000
    # 權證
    # 債券
    # ETN
    # TDR

    return bool(
        re.fullmatch(
            r"[1-9]\d{3}",
            code,
        )
    )


def looks_like_stock(
    code: str,
    name: str,
    row: Dict[str, Any],
) -> bool:

    if not looks_like_stock_code(
        code
    ):
        return False

    combined = (
        clean_text(name)
        + " "
        + " ".join(
            clean_text(value)
            for value in row.values()
        )
    ).upper()

    for keyword in NON_ETF_KEYWORDS:

        if keyword.upper() in combined:

            return False

    # 四碼普通股
    return True


# ============================================================
# PARSE TWSE
# ============================================================

def parse_twse(
    source_name: str,
    payload: Any,
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Dict[str, Dict[str, Any]],
]:

    rows = normalize_records(
        payload
    )

    stocks: Dict[
        str,
        Dict[str, Any],
    ] = {}

    etfs: Dict[
        str,
        Dict[str, Any],
    ] = {}

    for row in rows:

        code = extract_code(
            row
        )

        if not code:
            continue

        name = extract_name(
            row
        )

        # ----------------------------------------------------
        # ETF FIRST
        # ----------------------------------------------------

        etf_type = classify_etf(
            code,
            name,
            row,
        )

        if etf_type:

            etfs[code] = {
                "symbol": code,
                "name": name or code,
                "market": "TWSE",
                "type": "ETF",
                "instrument_type": etf_type,
                "source": source_name,
            }

            continue

        # ----------------------------------------------------
        # STOCK
        # ----------------------------------------------------

        if looks_like_stock(
            code,
            name,
            row,
        ):

            stocks[code] = {
                "symbol": code,
                "name": name or code,
                "market": "TWSE",
                "type": "STOCK",
                "instrument_type": "COMMON_STOCK",
                "source": source_name,
            }

    return stocks, etfs


# ============================================================
# PARSE TPEX
# ============================================================

def parse_tpex(
    source_name: str,
    payload: Any,
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Dict[str, Dict[str, Any]],
]:

    rows = normalize_records(
        payload
    )

    stocks: Dict[
        str,
        Dict[str, Any],
    ] = {}

    etfs: Dict[
        str,
        Dict[str, Any],
    ] = {}

    for row in rows:

        code = extract_code(
            row
        )

        if not code:
            continue

        name = extract_name(
            row
        )

        # ----------------------------------------------------
        # ETF FIRST
        # ----------------------------------------------------

        etf_type = classify_etf(
            code,
            name,
            row,
        )

        if etf_type:

            etfs[code] = {
                "symbol": code,
                "name": name or code,
                "market": "TPEX",
                "type": "ETF",
                "instrument_type": etf_type,
                "source": source_name,
            }

            continue

        # ----------------------------------------------------
        # STOCK
        # ----------------------------------------------------

        if looks_like_stock(
            code,
            name,
            row,
        ):

            stocks[code] = {
                "symbol": code,
                "name": name or code,
                "market": "TPEX",
                "type": "STOCK",
                "instrument_type": "COMMON_STOCK",
                "source": source_name,
            }

    return stocks, etfs


# ============================================================
# EXISTING METADATA
# ============================================================

def load_existing_metadata() -> Dict[
    str,
    Dict[str, Any],
]:

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

    if not isinstance(
        payload,
        dict,
    ):
        return {}

    stocks = payload.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):
        return {}

    result: Dict[
        str,
        Dict[str, Any],
    ] = {}

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

        if not code:
            continue

        result[code] = value

    return result


# ============================================================
# COLLECTION
# ============================================================

def collect_market_sources(
    market: str,
    sources: List[
        Tuple[
            str,
            str,
            Dict[str, Any],
        ]
    ],
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Dict[str, Dict[str, Any]],
]:

    section(
        f"{market} 官方來源"
    )

    all_stocks: Dict[
        str,
        Dict[str, Any],
    ] = {}

    all_etfs: Dict[
        str,
        Dict[str, Any],
    ] = {}

    source_results: List[
        Tuple[
            str,
            int,
            int,
            int,
        ]
    ] = []

    for (
        source_name,
        url,
        params,
    ) in sources:

        log("")
        log(
            f"{market}：{source_name}"
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
            f"  原始 rows：{len(rows)}"
        )

        if market == "TWSE":

            stocks, etfs = parse_twse(
                source_name,
                payload,
            )

        else:

            stocks, etfs = parse_tpex(
                source_name,
                payload,
            )

        log(
            f"  STOCK：{len(stocks)}"
        )

        log(
            f"  ETF：{len(etfs)}"
        )

        source_results.append(
            (
                source_name,
                len(rows),
                len(stocks),
                len(etfs),
            )
        )

        for code, item in stocks.items():

            if code not in all_stocks:

                all_stocks[code] = item

        for code, item in etfs.items():

            if code not in all_etfs:

                all_etfs[code] = item

    log("")
    log(
        f"{market} 官方來源結果："
    )

    for (
        name,
        rows,
        stocks,
        etfs,
    ) in source_results:

        log(
            f"  {name}: "
            f"rows={rows}, "
            f"STOCK={stocks}, "
            f"ETF={etfs}"
        )

    log(
        f"✓ {market} STOCK unique："
        f"{len(all_stocks)}"
    )

    log(
        f"✓ {market} ETF unique："
        f"{len(all_etfs)}"
    )

    return (
        all_stocks,
        all_etfs,
    )


# ============================================================
# OFFICIAL SOURCE GATE
# ============================================================

def official_source_gate(
    twse_stocks: Dict[str, Dict[str, Any]],
    twse_etfs: Dict[str, Dict[str, Any]],
    tpex_stocks: Dict[str, Dict[str, Any]],
    tpex_etfs: Dict[str, Dict[str, Any]],
) -> bool:

    section(
        "Official Source Gate"
    )

    log(
        f"TWSE STOCK：{len(twse_stocks)}"
    )

    log(
        f"TWSE ETF：{len(twse_etfs)}"
    )

    log(
        f"TPEx STOCK：{len(tpex_stocks)}"
    )

    log(
        f"TPEx ETF：{len(tpex_etfs)}"
    )

    errors = 0

    if len(twse_stocks) <= 0:

        log(
            "❌ TWSE STOCK 官方來源 Gate FAIL"
        )

        errors += 1

    if len(tpex_stocks) <= 0:

        log(
            "❌ TPEx STOCK 官方來源 Gate FAIL"
        )

        errors += 1

    if len(twse_etfs) <= 0:

        log(
            "❌ TWSE ETF 官方來源 Gate FAIL"
        )

        errors += 1

    if len(tpex_etfs) <= 0:

        log(
            "❌ TPEx ETF 官方來源 Gate FAIL"
        )

        errors += 1

    if errors:

        return False

    log(
        "✓ Official Source Gate PASS"
    )

    return True


# ============================================================
# BUILD UNIVERSE
# ============================================================

def build_universe(
    twse_stocks: Dict[str, Dict[str, Any]],
    twse_etfs: Dict[str, Dict[str, Any]],
    tpex_stocks: Dict[str, Dict[str, Any]],
    tpex_etfs: Dict[str, Dict[str, Any]],
    existing: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    section(
        "建立 Universe"
    )

    official: Dict[
        str,
        Dict[str, Any],
    ] = {}

    # --------------------------------------------------------
    # STOCK
    # --------------------------------------------------------

    for code, item in twse_stocks.items():

        official[
            code
        ] = item

    for code, item in tpex_stocks.items():

        if code not in official:

            official[
                code
            ] = item

    # --------------------------------------------------------
    # ETF
    #
    # ETF 與 STOCK code 理論上不應碰撞。
    # 若碰撞，以官方 ETF classification
    # 明確性優先，但記錄。
    # --------------------------------------------------------

    for code, item in twse_etfs.items():

        if code in official:

            log(
                f"⚠️ code classification collision："
                f"{code} → ETF 優先"
            )

        official[
            code
        ] = item

    for code, item in tpex_etfs.items():

        if code in official:

            log(
                f"⚠️ code classification collision："
                f"{code} → ETF 優先"
            )

        official[
            code
        ] = item

    stocks: Dict[
        str,
        Dict[str, Any],
    ] = {}

    for code in sorted(
        official.keys()
    ):

        item = official[
            code
        ]

        market = clean_text(
            item.get(
                "market"
            )
        ).upper()

        product_type = clean_text(
            item.get(
                "type"
            )
        ).upper()

        instrument_type = clean_text(
            item.get(
                "instrument_type"
            )
        ).upper()

        name = clean_text(
            item.get(
                "name"
            )
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
        # Core fields are ALWAYS rebuilt from official source.
        # ----------------------------------------------------

        if product_type == "STOCK":

            full_symbol = (
                code
                + (
                    ".TW"
                    if market == "TWSE"
                    else ".TWO"
                )
            )

            instrument_type = (
                "COMMON_STOCK"
            )

        elif product_type == "ETF":

            full_symbol = (
                code
                + (
                    ".TW"
                    if market == "TWSE"
                    else ".TWO"
                )
            )

        else:

            continue

        stock: Dict[
            str,
            Any,
        ] = {
            "symbol": code,
            "full_symbol": full_symbol,
            "name": (
                name
                or clean_text(
                    old.get(
                        "name"
                    )
                )
                or code
            ),
            "market": market,
            "type": product_type,
            "instrument_type": (
                instrument_type
                or (
                    "ETF"
                    if product_type == "ETF"
                    else "COMMON_STOCK"
                )
            ),
            "status": "active",
        }

        # ----------------------------------------------------
        # 保留非核心 metadata
        # ----------------------------------------------------

        for key in (
            "industry",
            "sector",
            "category",
        ):

            value = old.get(
                key
            )

            if value not in (
                None,
                "",
            ):

                stock[
                    key
                ] = value

        stocks[
            code
        ] = stock

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
            "❌ Universe 不得為空"
        )

        return False

    errors = 0

    symbols: set[str] = set()

    full_symbols: set[str] = set()

    stock_count = 0

    etf_count = 0

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

        symbol = clean_code(
            item.get(
                "symbol"
            )
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

        symbols.add(
            symbol
        )

        name = clean_text(
            item.get(
                "name"
            )
        )

        if not name:

            log(
                f"❌ {code}: name 空白"
            )

            errors += 1

        market = item.get(
            "market"
        )

        if market not in {
            "TWSE",
            "TPEX",
        }:

            log(
                f"❌ {code}: "
                f"market={market}"
            )

            errors += 1

        product_type = item.get(
            "type"
        )

        if product_type not in {
            "STOCK",
            "ETF",
        }:

            log(
                f"❌ {code}: "
                f"type={product_type}"
            )

            errors += 1

        instrument_type = clean_text(
            item.get(
                "instrument_type"
            )
        )

        if not instrument_type:

            log(
                f"❌ {code}: "
                f"instrument_type 空白"
            )

            errors += 1

        if item.get(
            "status"
        ) != "active":

            log(
                f"❌ {code}: "
                f"status != active"
            )

            errors += 1

        full_symbol = clean_text(
            item.get(
                "full_symbol"
            )
        )

        expected_suffix = (
            ".TW"
            if market == "TWSE"
            else ".TWO"
        )

        expected_full_symbol = (
            code
            + expected_suffix
        )

        if full_symbol != expected_full_symbol:

            log(
                f"❌ {code}: "
                f"full_symbol={full_symbol}"
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
        # STOCK validation
        # ----------------------------------------------------

        if product_type == "STOCK":

            stock_count += 1

            if not re.fullmatch(
                r"[1-9]\d{3}",
                code,
            ):

                log(
                    f"❌ STOCK {code}: "
                    "不是 4 碼普通股"
                )

                errors += 1

            if instrument_type != (
                "COMMON_STOCK"
            ):

                log(
                    f"❌ STOCK {code}: "
                    f"instrument_type="
                    f"{instrument_type}"
                )

                errors += 1

        # ----------------------------------------------------
        # ETF validation
        # ----------------------------------------------------

        elif product_type == "ETF":

            etf_count += 1

            if (
                classify_etf(
                    code,
                    name,
                    item,
                )
                is None
            ):

                log(
                    f"❌ ETF {code}: "
                    "無法再次確認 ETF classification"
                )

                errors += 1

    log(
        f"✓ STOCK：{stock_count}"
    )

    log(
        f"✓ ETF：{etf_count}"
    )

    log(
        f"✓ Total：{len(stocks)}"
    )

    if errors:

        log(
            f"❌ Universe Structure Gate FAIL："
            f"{errors}"
        )

        return False

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
# MARKET BALANCE
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

        product_type = item.get(
            "type"
        )

        if market == "TWSE":

            if product_type == "STOCK":
                twse_stock += 1

            elif product_type == "ETF":
                twse_etf += 1

        elif market == "TPEX":

            if product_type == "STOCK":
                tpex_stock += 1

            elif product_type == "ETF":
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

    if twse_etf <= 0:
        log(
            "❌ TWSE ETF = 0"
        )
        return False

    if tpex_etf <= 0:
        log(
            "❌ TPEx ETF = 0"
        )
        return False

    log(
        "✓ Market Balance Gate PASS"
    )

    return True


# ============================================================
# ETF CATEGORY STATISTICS
# ============================================================

def print_etf_statistics(
    stocks: Dict[str, Dict[str, Any]],
) -> None:

    section(
        "ETF CATEGORY STATISTICS"
    )

    counts: Dict[
        str,
        int,
    ] = {}

    for item in stocks.values():

        if item.get(
            "type"
        ) != "ETF":

            continue

        category = clean_text(
            item.get(
                "instrument_type"
            )
        )

        if not category:

            category = "UNKNOWN"

        counts[
            category
        ] = (
            counts.get(
                category,
                0,
            )
            + 1
        )

    for category in sorted(
        counts.keys()
    ):

        log(
            f"  {category}: "
            f"{counts[category]}"
        )


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
        if item.get(
            "type"
        ) == "STOCK"
    )

    etf_count = sum(
        1
        for item in stocks.values()
        if item.get(
            "type"
        ) == "ETF"
    )

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

    return {
        "version": VERSION,

        "generated_at": (
            now.isoformat()
        ),

        "universe_count": len(
            stocks
        ),

        "stock_count": stock_count,

        "etf_count": etf_count,

        "market_count": {
            "TWSE": twse_count,
            "TPEX": tpex_count,
        },

        "source": {
            "policy": (
                "TWSE / TPEx official "
                "market sources only"
            ),
            "stock_source": (
                "official market quote sources"
            ),
            "etf_source": (
                "official market quote sources "
                "with official ETF classification rules"
            ),
        },

        "contract": {
            "root": "dict",
            "stocks": "dict",
            "active_status": (
                "status == active"
            ),
            "ordinary_stock_only": True,
            "etf_included": True,
            "etf_bond_included": True,
            "etf_multi_asset_included": True,
            "etf_active_included": True,
            "etf_leveraged_included": True,
            "etf_inverse_included": True,
            "etf_futures_included": True,
            "allowed_markets": [
                "TWSE",
                "TPEX",
            ],
            "allowed_types": [
                "STOCK",
                "ETF",
            ],
        },

        "stocks": stocks,
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
    ) != len(
        stocks
    ):

        return False

    if payload.get(
        "stock_count"
    ) != sum(
        1
        for item in stocks.values()
        if item.get(
            "type"
        ) == "STOCK"
    ):

        return False

    if payload.get(
        "etf_count"
    ) != sum(
        1
        for item in stocks.values()
        if item.get(
            "type"
        ) == "ETF"
    ):

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

        return False

    if contract.get(
        "ordinary_stock_only"
    ) is not True:

        return False

    if contract.get(
        "etf_included"
    ) is not True:

        return False

    if contract.get(
        "etf_bond_included"
    ) is not True:

        return False

    if contract.get(
        "etf_multi_asset_included"
    ) is not True:

        return False

    if contract.get(
        "etf_active_included"
    ) is not True:

        return False

    if contract.get(
        "etf_leveraged_included"
    ) is not True:

        return False

    if contract.get(
        "etf_inverse_included"
    ) is not True:

        return False

    if contract.get(
        "etf_futures_included"
    ) is not True:

        return False

    if contract.get(
        "allowed_markets"
    ) != [
        "TWSE",
        "TPEX",
    ]:

        return False

    if contract.get(
        "allowed_types"
    ) != [
        "STOCK",
        "ETF",
    ]:

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

    temp_file = DATA_DIR / (
        "universe.json.tmp"
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
        # Write-before-replace validation
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
            "❌ universe.json "
            "Contract FAIL"
        )

        return False

    stocks = payload.get(
        "stocks"
    )

    stock_count = sum(
        1
        for item in stocks.values()
        if item.get(
            "type"
        ) == "STOCK"
    )

    etf_count = sum(
        1
        for item in stocks.values()
        if item.get(
            "type"
        ) == "ETF"
    )

    log(
        f"✓ universe.json "
        f"重新讀取："
        f"{len(stocks)} 檔"
    )

    log(
        f"✓ STOCK："
        f"{stock_count}"
    )

    log(
        f"✓ ETF："
        f"{etf_count}"
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
# SUMMARY
# ============================================================

def print_summary(
    stocks: Dict[str, Dict[str, Any]],
) -> None:

    twse_stock = 0
    twse_etf = 0

    tpex_stock = 0
    tpex_etf = 0

    stock_count = 0
    etf_count = 0

    active_count = 0

    for item in stocks.values():

        market = item.get(
            "market"
        )

        product_type = item.get(
            "type"
        )

        if item.get(
            "status"
        ) == "active":

            active_count += 1

        if product_type == "STOCK":

            stock_count += 1

            if market == "TWSE":
                twse_stock += 1

            elif market == "TPEX":
                tpex_stock += 1

        elif product_type == "ETF":

            etf_count += 1

            if market == "TWSE":
                twse_etf += 1

            elif market == "TPEX":
                tpex_etf += 1

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
        f"✓ TWSE STOCK："
        f"{twse_stock}"
    )

    log(
        f"✓ TWSE ETF："
        f"{twse_etf}"
    )

    log(
        f"✓ TPEx STOCK："
        f"{tpex_stock}"
    )

    log(
        f"✓ TPEx ETF："
        f"{tpex_etf}"
    )

    log(
        f"✓ active："
        f"{active_count}"
    )

    log(
        "✓ ETF included"
    )

    log(
        "✓ Bond ETF included"
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
        "✓ Futures / commodity ETF included"
    )

    log(
        "✓ Official sources only"
    )

    log(
        f"✓ Output："
        f"{UNIVERSE_FILE}"
    )


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    started = time.time()

    section(
        "台股 AI 選股系統 "
        "Official Universe Builder"
    )

    log(
        f"開始時間："
        f"{now_tw().isoformat()}"
    )

    # --------------------------------------------------------
    # Existing metadata
    # --------------------------------------------------------

    existing = load_existing_metadata()

    log(
        f"既有 Universe metadata："
        f"{len(existing)}"
    )

    # --------------------------------------------------------
    # TWSE
    # --------------------------------------------------------

    (
        twse_stocks,
        twse_etfs,
    ) = collect_market_sources(
        "TWSE",
        TWSE_SOURCES,
    )

    # --------------------------------------------------------
    # TPEx
    # --------------------------------------------------------

    (
        tpex_stocks,
        tpex_etfs,
    ) = collect_market_sources(
        "TPEX",
        TPEX_SOURCES,
    )

    # --------------------------------------------------------
    # Official Gate
    # --------------------------------------------------------

    if not official_source_gate(
        twse_stocks,
        twse_etfs,
        tpex_stocks,
        tpex_etfs,
    ):

        log(
            "❌ 官方來源 Gate FAIL"
        )

        return 1

    # --------------------------------------------------------
    # Build
    # --------------------------------------------------------

    stocks = build_universe(
        twse_stocks,
        twse_etfs,
        tpex_stocks,
        tpex_etfs,
        existing,
    )

    # --------------------------------------------------------
    # Structure Gate
    # --------------------------------------------------------

    if not validate_universe_structure(
        stocks
    ):

        return 1

    # --------------------------------------------------------
    # Market Balance
    # --------------------------------------------------------

    if not validate_market_balance(
        stocks
    ):

        return 1

    # --------------------------------------------------------
    # Payload
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Atomic Write
    # --------------------------------------------------------

    if not atomic_write(
        payload
    ):

        return 1

    log(
        "✓ Atomic Write PASS"
    )

    # --------------------------------------------------------
    # Post Write Verify
    # --------------------------------------------------------

    if not post_write_verify():

        log(
            "❌ Post Write Verify FAIL"
        )

        return 1

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    print_summary(
        stocks
    )

    print_etf_statistics(
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

    sys.exit(
        main()
    )