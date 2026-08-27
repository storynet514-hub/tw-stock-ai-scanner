#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_universe.py

============================================================
Universe Builder - 全新重寫版
============================================================

核心契約
------------------------------------------------------------
1. Data/universe.json 是唯一 Universe 輸出檔。
2. TWSE / TPEx 官方 OpenAPI 是 Universe 的唯一建立來源。
3. 不使用歷史 universe.json 擴張 Universe。
4. 不使用 Yahoo 擴張 Universe。
5. 不使用 CMoney 擴張 Universe。
6. 不寫死 Universe 數量。
7. 不寫死股票代號。
8. 不以「預期股票數量」判定成功。
9. stocks 必須是 dict。
10. 每個 symbol 必須唯一。
11. 只納入普通股票 STOCK。
12. 排除 ETF / ETN / TDR / Warrant / Bond / Fund 等非普通股票商品。
13. 官方來源失敗 => BUILD FAIL。
14. 官方來源資料結構異常 => BUILD FAIL。
15. Universe 1:1 結構驗證。
16. Structure Gate。
17. Data Quality Gate。
18. Atomic Write。
19. Atomic Write 後重新讀取 universe.json。
20. Post-Write Verify。

重要設計
------------------------------------------------------------
Universe 的任務只是建立「後續資料抓取允許處理的股票集合」。

因此本程式不負責：
- 三大法人
- 資券當沖率
- 融資融券
- 歷史價格
- 選股條件

上述資料由後續 fetch_chip.py / fetch_data.py 等流程處理。

市場來源
------------------------------------------------------------
TWSE：
    https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL

TPEx：
    https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes

注意：
------------------------------------------------------------
官方行情 API 的欄位名稱可能有小幅變動，
因此 parser 採「候選欄位 + 嚴格驗證」。

但如果：
- API 完全失敗
- 回傳非 JSON
- 沒有股票代號
- 無法建立有效普通股票集合

則直接 FAIL。

絕不使用猜測資料補洞。
"""


from __future__ import annotations


# ============================================================
# IMPORT
# ============================================================

import json
import math
import os
import re
import sys
import tempfile
import time

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import requests


# ============================================================
# VERSION
# ============================================================

VERSION = "UNIVERSE-REBUILD-V1"


# ============================================================
# PATH
# ============================================================

ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = ROOT / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"


# ============================================================
# OFFICIAL SOURCES
# ============================================================

TWSE_API = (
    "https://openapi.twse.com.tw/v1/"
    "exchangeReport/STOCK_DAY_ALL"
)

TPEX_API = (
    "https://www.tpex.org.tw/openapi/v1/"
    "tpex_mainboard_daily_close_quotes"
)


# ============================================================
# NETWORK
# ============================================================

REQUEST_TIMEOUT = 40

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
        "*/*"
    ),
    "Accept-Language": (
        "zh-TW,zh;q=0.9,"
        "en-US;q=0.8,en;q=0.7"
    ),
}


SESSION = requests.Session()

SESSION.headers.update(
    HEADERS
)


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


# ============================================================
# BASIC NORMALIZATION
# ============================================================

def clean_text(value: Any) -> str:

    if value is None:
        return ""

    return (
        str(value)
        .replace("\ufeff", "")
        .replace("\u3000", " ")
        .strip()
    )


def normalize_key(value: Any) -> str:

    text = clean_text(value)

    text = (
        text
        .replace("_", "")
        .replace("-", "")
        .replace("/", "")
        .replace(" ", "")
        .replace("\t", "")
        .replace("\n", "")
        .replace("\r", "")
        .replace("（", "")
        .replace("）", "")
        .replace("(", "")
        .replace(")", "")
        .lower()
    )

    return text


def normalize_symbol(value: Any) -> str:

    text = clean_text(value)

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
# NUMBER
# ============================================================

def safe_number(
    value: Any,
) -> Optional[float]:

    if value is None:
        return None

    if isinstance(value, bool):
        return None

    text = clean_text(value)

    if not text:
        return None

    if text.lower() in {
        "-",
        "--",
        "---",
        "none",
        "null",
        "n/a",
        "na",
    }:
        return None

    text = (
        text
        .replace(",", "")
        .replace("，", "")
        .replace("%", "")
        .replace(" ", "")
        .replace("\u3000", "")
    )

    try:
        number = float(text)

    except (TypeError, ValueError):
        return None

    if not math.isfinite(number):
        return None

    return number


# ============================================================
# FIELD FINDER
# ============================================================

def find_field(
    row: Dict[str, Any],
    aliases: Iterable[str],
) -> Any:

    if not isinstance(row, dict):
        return None

    normalized: Dict[str, Any] = {}

    for key, value in row.items():

        normalized[
            normalize_key(key)
        ] = value

    aliases_normalized = [
        normalize_key(alias)
        for alias in aliases
    ]

    # --------------------------------------------------------
    # Exact
    # --------------------------------------------------------

    for alias in aliases_normalized:

        if alias in normalized:
            return normalized[alias]

    # --------------------------------------------------------
    # Controlled fuzzy matching
    # --------------------------------------------------------

    for alias in aliases_normalized:

        if not alias:
            continue

        for key, value in normalized.items():

            if key == alias:
                return value

    return None


# ============================================================
# SYMBOL
# ============================================================

def extract_symbol(
    row: Dict[str, Any],
) -> str:

    value = find_field(
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
        ],
    )

    return normalize_symbol(value)


# ============================================================
# NAME
# ============================================================

def extract_name(
    row: Dict[str, Any],
) -> str:

    value = find_field(
        row,
        [
            "證券名稱",
            "股票名稱",
            "名稱",
            "公司名稱",
            "SecurityName",
            "securityName",
            "CompanyName",
            "companyName",
            "Name",
            "name",
        ],
    )

    return clean_text(value)


# ============================================================
# SYMBOL GATE
# ============================================================

def valid_symbol(
    symbol: str,
) -> bool:

    if not symbol:
        return False

    # 普通台股股票代號主要為數字。
    #
    # 這裡不使用固定股票清單。
    #
    # 3~6 碼數字允許進入下一階段，
    # 最終商品資格仍由官方 row 判定。

    return bool(
        re.fullmatch(
            r"\d{3,6}",
            symbol,
        )
    )


# ============================================================
# PRODUCT CLASSIFICATION
# ============================================================

def explicit_type(
    row: Dict[str, Any],
) -> str:

    value = find_field(
        row,
        [
            "證券種類",
            "商品類別",
            "商品種類",
            "證券類型",
            "證券類別",
            "類別",
            "Type",
            "type",
            "InstrumentType",
            "instrument_type",
            "SecurityType",
            "securityType",
        ],
    )

    return clean_text(value)


def classify_product(
    row: Dict[str, Any],
    name: str,
) -> Tuple[str, str]:

    type_text = explicit_type(row)

    combined = (
        f"{type_text} {name}"
    ).upper()

    # --------------------------------------------------------
    # ETF
    # --------------------------------------------------------

    if (
        "ETF" in combined
        or "指數股票型基金" in combined
        or "指數型基金" in combined
    ):
        return (
            "ETF",
            "NON_STOCK",
        )

    # --------------------------------------------------------
    # ETN
    # --------------------------------------------------------

    if (
        "ETN" in combined
        or "指數投資證券" in combined
    ):
        return (
            "ETN",
            "NON_STOCK",
        )

    # --------------------------------------------------------
    # Warrant
    # --------------------------------------------------------

    if any(
        keyword in combined
        for keyword in (
            "權證",
            "認購權證",
            "認售權證",
            "牛證",
            "熊證",
        )
    ):
        return (
            "WARRANT",
            "NON_STOCK",
        )

    # --------------------------------------------------------
    # TDR
    # --------------------------------------------------------

    if (
        "TDR" in combined
        or "存託憑證" in combined
    ):
        return (
            "TDR",
            "NON_STOCK",
        )

    # --------------------------------------------------------
    # Bond
    # --------------------------------------------------------

    if any(
        keyword in combined
        for keyword in (
            "債券",
            "公司債",
            "政府債",
            "金融債",
        )
    ):
        return (
            "BOND",
            "NON_STOCK",
        )

    # --------------------------------------------------------
    # Fund
    # --------------------------------------------------------

    if any(
        keyword in combined
        for keyword in (
            "基金",
            "受益憑證",
        )
    ):
        return (
            "FUND",
            "NON_STOCK",
        )

    # --------------------------------------------------------
    # Index
    # --------------------------------------------------------

    if (
        "指數" in combined
        and "股票" not in combined
    ):
        return (
            "INDEX",
            "NON_STOCK",
        )

    # --------------------------------------------------------
    # Default
    # --------------------------------------------------------

    return (
        "STOCK",
        "COMMON_STOCK",
    )


# ============================================================
# OFFICIAL ROW VALIDATION
# ============================================================

def parse_official_stock(
    row: Dict[str, Any],
    market: str,
) -> Optional[Dict[str, str]]:

    if not isinstance(row, dict):
        return None

    symbol = extract_symbol(row)

    if not valid_symbol(symbol):
        return None

    name = extract_name(row)

    if not name:
        return None

    product_type, product_class = (
        classify_product(
            row,
            name,
        )
    )

    # --------------------------------------------------------
    # 嚴格只收普通股票
    # --------------------------------------------------------

    if product_class != "COMMON_STOCK":
        return None

    if product_type != "STOCK":
        return None

    # --------------------------------------------------------
    # 排除名稱明顯屬於非股票商品
    # --------------------------------------------------------

    name_upper = name.upper()

    forbidden_name_keywords = (
        "ETF",
        "ETN",
        "權證",
        "認購",
        "認售",
        "牛證",
        "熊證",
        "存託憑證",
        "TDR",
        "債券",
        "基金",
        "受益憑證",
    )

    if any(
        keyword in name_upper
        for keyword in forbidden_name_keywords
    ):
        return None

    # --------------------------------------------------------
    # 建立標準 Universe record
    # --------------------------------------------------------

    suffix = (
        ".TW"
        if market == "TWSE"
        else ".TWO"
    )

    return {
        "symbol": symbol,
        "full_symbol": symbol + suffix,
        "name": name,
        "market": market,
        "type": "STOCK",
        "status": "active",
    }


# ============================================================
# HTTP JSON
# ============================================================

def fetch_json(
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
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code != 200:

                last_error = (
                    f"HTTP "
                    f"{response.status_code}"
                )

            else:

                text = (
                    response.text
                    .strip()
                )

                if not text:

                    last_error = (
                        "EMPTY RESPONSE"
                    )

                else:

                    try:

                        return response.json()

                    except Exception as exc:

                        last_error = (
                            f"JSON ERROR: {exc}"
                        )

        except Exception as exc:

            last_error = (
                f"REQUEST ERROR: {exc}"
            )

        if attempt < RETRIES:

            time.sleep(
                RETRY_SLEEP
                * attempt
            )

    log(
        f"❌ 官方 API 失敗："
        f"{url}"
    )

    log(
        f"   {last_error}"
    )

    return None


# ============================================================
# NORMALIZE API PAYLOAD
# ============================================================

def normalize_payload(
    payload: Any,
) -> List[Dict[str, Any]]:

    if isinstance(payload, list):

        return [
            row
            for row in payload
            if isinstance(row, dict)
        ]

    if isinstance(payload, dict):

        # ----------------------------------------------------
        # data
        # ----------------------------------------------------

        for key in (
            "data",
            "Data",
            "result",
            "results",
            "records",
            "Records",
        ):

            value = payload.get(key)

            if isinstance(value, list):

                rows = [
                    row
                    for row in value
                    if isinstance(row, dict)
                ]

                if rows:
                    return rows

        # ----------------------------------------------------
        # fields + data
        # ----------------------------------------------------

        fields = payload.get(
            "fields"
        )

        data = payload.get(
            "data"
        )

        if (
            isinstance(fields, list)
            and isinstance(data, list)
        ):

            result = []

            for raw_row in data:

                if not isinstance(
                    raw_row,
                    list,
                ):
                    continue

                row: Dict[str, Any] = {}

                for index, field in enumerate(
                    fields
                ):

                    if index >= len(
                        raw_row
                    ):
                        break

                    row[
                        clean_text(field)
                    ] = raw_row[index]

                if row:
                    result.append(row)

            if result:
                return result

        # ----------------------------------------------------
        # tables
        # ----------------------------------------------------

        tables = payload.get(
            "tables"
        )

        if isinstance(
            tables,
            list,
        ):

            result = []

            for table in tables:

                if not isinstance(
                    table,
                    dict,
                ):
                    continue

                table_fields = (
                    table.get("fields")
                )

                table_data = (
                    table.get("data")
                )

                if not (
                    isinstance(
                        table_fields,
                        list,
                    )
                    and isinstance(
                        table_data,
                        list,
                    )
                ):
                    continue

                for raw_row in table_data:

                    if not isinstance(
                        raw_row,
                        list,
                    ):
                        continue

                    row = {}

                    for index, field in enumerate(
                        table_fields
                    ):

                        if index >= len(
                            raw_row
                        ):
                            break

                        row[
                            clean_text(field)
                        ] = raw_row[index]

                    if row:
                        result.append(row)

            if result:
                return result

    return []


# ============================================================
# FETCH OFFICIAL MARKET
# ============================================================

def fetch_market_universe(
    market: str,
    url: str,
) -> List[Dict[str, str]]:

    section(
        f"{market} 官方 Universe"
    )

    log(
        f"來源：{url}"
    )

    payload = fetch_json(url)

    if payload is None:

        raise RuntimeError(
            f"{market} 官方 API 無法取得資料"
        )

    rows = normalize_payload(
        payload
    )

    if not rows:

        raise RuntimeError(
            f"{market} 官方 API 回傳資料"
            "無法解析成 records"
        )

    log(
        f"官方 records：{len(rows)}"
    )

    stocks: Dict[
        str,
        Dict[str, str],
    ] = {}

    invalid_symbol = 0
    missing_name = 0
    non_stock = 0
    accepted = 0

    for row in rows:

        symbol = extract_symbol(
            row
        )

        if not valid_symbol(
            symbol
        ):

            invalid_symbol += 1
            continue

        name = extract_name(
            row
        )

        if not name:

            missing_name += 1
            continue

        product_type, product_class = (
            classify_product(
                row,
                name,
            )
        )

        if (
            product_class
            != "COMMON_STOCK"
            or product_type
            != "STOCK"
        ):

            non_stock += 1
            continue

        record = parse_official_stock(
            row,
            market,
        )

        if record is None:
            non_stock += 1
            continue

        if symbol in stocks:

            # 官方資料出現重複代號：
            # 如果 metadata 相同可以忽略重複；
            # 若 metadata 不一致則直接 FAIL。
            existing = stocks[symbol]

            if (
                existing["name"]
                != record["name"]
            ):

                raise RuntimeError(
                    f"{market} 官方資料出現"
                    f"同代號不同名稱："
                    f"{symbol}"
                )

            continue

        stocks[
            symbol
        ] = record

        accepted += 1

    log(
        f"✓ 普通股票：{accepted}"
    )

    log(
        f"  無效代號：{invalid_symbol}"
    )

    log(
        f"  缺名稱：{missing_name}"
    )

    log(
        f"  排除非普通股票：{non_stock}"
    )

    # --------------------------------------------------------
    # 官方資料有 records，
    # 但一檔普通股票都解析不到 => FAIL
    # --------------------------------------------------------

    if not stocks:

        raise RuntimeError(
            f"{market} 官方資料存在，"
            "但無法建立任何普通股票"
        )

    return list(
        stocks.values()
    )


# ============================================================
# MERGE
# ============================================================

def merge_markets(
    twse: List[Dict[str, str]],
    tpex: List[Dict[str, str]],
) -> List[Dict[str, str]]:

    section(
        "Universe 合併"
    )

    stocks: Dict[
        str,
        Dict[str, str],
    ] = {}

    # --------------------------------------------------------
    # TWSE
    # --------------------------------------------------------

    for item in twse:

        symbol = item["symbol"]

        if symbol in stocks:

            raise RuntimeError(
                f"TWSE 重複 symbol：{symbol}"
            )

        stocks[
            symbol
        ] = item

    # --------------------------------------------------------
    # TPEx
    # --------------------------------------------------------

    for item in tpex:

        symbol = item["symbol"]

        if symbol in stocks:

            raise RuntimeError(
                "TWSE / TPEx 出現相同 symbol："
                f"{symbol}"
            )

        stocks[
            symbol
        ] = item

    result = list(
        stocks.values()
    )

    result.sort(
        key=lambda item: item["symbol"]
    )

    log(
        f"TWSE：{len(twse)}"
    )

    log(
        f"TPEx：{len(tpex)}"
    )

    log(
        f"Total：{len(result)}"
    )

    if not result:

        raise RuntimeError(
            "合併後 Universe 為空"
        )

    return result


# ============================================================
# STRUCTURE GATE
# ============================================================

REQUIRED_STOCK_FIELDS = {
    "symbol",
    "full_symbol",
    "name",
    "market",
    "type",
    "status",
}


def structure_gate(
    stocks: Dict[str, Dict[str, str]],
) -> bool:

    section(
        "Structure Gate"
    )

    errors = 0

    if not isinstance(
        stocks,
        dict,
    ):

        log(
            "❌ stocks 必須是 dict"
        )

        return False

    for symbol, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):

            log(
                f"❌ {symbol}: "
                "item 不是 dict"
            )

            errors += 1
            continue

        missing = (
            REQUIRED_STOCK_FIELDS
            - set(item.keys())
        )

        if missing:

            log(
                f"❌ {symbol}: "
                f"缺欄位 {sorted(missing)}"
            )

            errors += len(
                missing
            )

        if (
            item.get("symbol")
            != symbol
        ):

            log(
                f"❌ {symbol}: "
                "symbol mismatch"
            )

            errors += 1

        if item.get(
            "status"
        ) != "active":

            log(
                f"❌ {symbol}: "
                "status != active"
            )

            errors += 1

        if item.get(
            "type"
        ) != "STOCK":

            log(
                f"❌ {symbol}: "
                "type != STOCK"
            )

            errors += 1

        if item.get(
            "market"
        ) not in {
            "TWSE",
            "TPEX",
        }:

            log(
                f"❌ {symbol}: "
                "market 無效"
            )

            errors += 1

        if not valid_symbol(
            symbol
        ):

            log(
                f"❌ {symbol}: "
                "symbol 格式無效"
            )

            errors += 1

        expected_suffix = (
            ".TW"
            if item.get("market")
            == "TWSE"
            else ".TWO"
        )

        if item.get(
            "full_symbol"
        ) != (
            symbol
            + expected_suffix
        ):

            log(
                f"❌ {symbol}: "
                "full_symbol mismatch"
            )

            errors += 1

        if not clean_text(
            item.get("name")
        ):

            log(
                f"❌ {symbol}: "
                "name 空白"
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
        f"{len(stocks)} 檔"
    )

    return True


# ============================================================
# DATA QUALITY GATE
# ============================================================

def data_quality_gate(
    stocks: Dict[str, Dict[str, str]],
) -> bool:

    section(
        "Data Quality Gate"
    )

    errors = 0

    symbols: Set[str] = set()

    for key, item in stocks.items():

        # ----------------------------------------------------
        # unique
        # ----------------------------------------------------

        if key in symbols:

            log(
                f"❌ 重複 symbol：{key}"
            )

            errors += 1

        symbols.add(key)

        # ----------------------------------------------------
        # active
        # ----------------------------------------------------

        if item.get(
            "status"
        ) != "active":

            log(
                f"❌ {key}: inactive"
            )

            errors += 1

        # ----------------------------------------------------
        # market
        # ----------------------------------------------------

        market = item.get(
            "market"
        )

        if market not in {
            "TWSE",
            "TPEX",
        }:

            errors += 1

        # ----------------------------------------------------
        # product
        # ----------------------------------------------------

        if item.get(
            "type"
        ) != "STOCK":

            log(
                f"❌ {key}: "
                "非 STOCK"
            )

            errors += 1

        # ----------------------------------------------------
        # name
        # ----------------------------------------------------

        name = clean_text(
            item.get("name")
        )

        if not name:

            log(
                f"❌ {key}: "
                "name 空白"
            )

            errors += 1

        # ----------------------------------------------------
        # symbol
        # ----------------------------------------------------

        if not valid_symbol(
            key
        ):

            log(
                f"❌ {key}: "
                "symbol 無效"
            )

            errors += 1

    # --------------------------------------------------------
    # Market counts
    # --------------------------------------------------------

    twse_count = sum(
        1
        for item in stocks.values()
        if item.get("market")
        == "TWSE"
    )

    tpex_count = sum(
        1
        for item in stocks.values()
        if item.get("market")
        == "TPEX"
    )

    log(
        f"TWSE：{twse_count}"
    )

    log(
        f"TPEx：{tpex_count}"
    )

    log(
        f"Total：{len(stocks)}"
    )

    # --------------------------------------------------------
    # 基本合理性
    #
    # 不設定「應該是多少檔」。
    # 只防止明顯 API/parser 災難。
    # --------------------------------------------------------

    if len(stocks) <= 0:

        log(
            "❌ Universe 為 0"
        )

        errors += 1

    if len(stocks) > 5000:

        log(
            "❌ Universe 超過安全上限 5000"
        )

        log(
            "   這通常代表 API/parser 異常"
        )

        errors += 1

    if twse_count <= 0:

        log(
            "❌ TWSE Universe 為 0"
        )

        errors += 1

    if tpex_count <= 0:

        log(
            "❌ TPEx Universe 為 0"
        )

        errors += 1

    # --------------------------------------------------------
    # PASS / FAIL
    # --------------------------------------------------------

    if errors:

        log(
            f"❌ Data Quality Gate FAIL："
            f"{errors}"
        )

        return False

    log(
        "✓ 所有 symbol 唯一"
    )

    log(
        "✓ 所有商品為 STOCK"
    )

    log(
        "✓ 所有 status == active"
    )

    log(
        "✓ TWSE / TPEx 均有資料"
    )

    log(
        "✓ Universe 數量未出現"
        "明顯 parser 災難"
    )

    log(
        "✓ Data Quality Gate PASS"
    )

    return True


# ============================================================
# PAYLOAD
# ============================================================

def build_payload(
    stocks: List[Dict[str, str]],
) -> Dict[str, Any]:

    stock_dict: Dict[
        str,
        Dict[str, str],
    ] = {}

    for item in stocks:

        symbol = item[
            "symbol"
        ]

        if symbol in stock_dict:

            raise RuntimeError(
                f"Payload duplicate："
                f"{symbol}"
            )

        stock_dict[
            symbol
        ] = item

    twse_count = sum(
        1
        for item in stock_dict.values()
        if item.get("market")
        == "TWSE"
    )

    tpex_count = sum(
        1
        for item in stock_dict.values()
        if item.get("market")
        == "TPEX"
    )

    return {
        "version": VERSION,

        "generated_at":
            now_tw().isoformat(),

        "data_date":
            now_tw().strftime(
                "%Y-%m-%d"
            ),

        "universe_count":
            len(stock_dict),

        "market_counts": {
            "TWSE":
                twse_count,
            "TPEX":
                tpex_count,
        },

        "instrument_filter": {
            "included": [
                "STOCK",
            ],

            "excluded": [
                "ETF",
                "ETN",
                "TDR",
                "WARRANT",
                "BOND",
                "FUND",
                "INDEX",
                "OTHER",
            ],
        },

        "source": {
            "TWSE":
                TWSE_API,

            "TPEX":
                TPEX_API,

            "fallback_allowed":
                False,
        },

        "stocks":
            stock_dict,
    }


# ============================================================
# PAYLOAD CONTRACT
# ============================================================

def payload_contract_gate(
    payload: Dict[str, Any],
) -> bool:

    section(
        "Payload Contract Gate"
    )

    if not isinstance(
        payload,
        dict,
    ):

        log(
            "❌ payload 不是 dict"
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
            "❌ stocks 不是 dict"
        )

        return False

    universe_count = payload.get(
        "universe_count"
    )

    if universe_count != len(
        stocks
    ):

        log(
            "❌ universe_count != "
            "len(stocks)"
        )

        return False

    market_counts = payload.get(
        "market_counts"
    )

    if not isinstance(
        market_counts,
        dict,
    ):

        log(
            "❌ market_counts 無效"
        )

        return False

    twse_count = sum(
        1
        for item in stocks.values()
        if item.get("market")
        == "TWSE"
    )

    tpex_count = sum(
        1
        for item in stocks.values()
        if item.get("market")
        == "TPEX"
    )

    if market_counts.get(
        "TWSE"
    ) != twse_count:

        log(
            "❌ TWSE count mismatch"
        )

        return False

    if market_counts.get(
        "TPEX"
    ) != tpex_count:

        log(
            "❌ TPEx count mismatch"
        )

        return False

    if payload.get(
        "source",
        {},
    ).get(
        "fallback_allowed"
    ) is not False:

        log(
            "❌ Universe fallback "
            "不得擴張"
        )

        return False

    log(
        "✓ universe_count == len(stocks)"
    )

    log(
        "✓ market_counts 正確"
    )

    log(
        "✓ 官方來源契約正確"
    )

    log(
        "✓ Payload Contract PASS"
    )

    return True


# ============================================================
# ATOMIC WRITE
# ============================================================

def atomic_write(
    payload: Dict[str, Any],
) -> bool:

    section(
        "Atomic Write"
    )

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_path: Optional[
        Path
    ] = None

    try:

        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        )

        # ----------------------------------------------------
        # 先確認 serialization 本身有效
        # ----------------------------------------------------

        json.loads(
            serialized
        )

        # ----------------------------------------------------
        # temp file
        # ----------------------------------------------------

        fd, temp_name = (
            tempfile.mkstemp(
                prefix="universe.",
                suffix=".tmp",
                dir=str(DATA_DIR),
                text=True,
            )
        )

        temp_path = Path(
            temp_name
        )

        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
        ) as handle:

            handle.write(
                serialized
            )

            handle.flush()

            os.fsync(
                handle.fileno()
            )

        # ----------------------------------------------------
        # temp read verify
        # ----------------------------------------------------

        temp_payload = json.loads(
            temp_path.read_text(
                encoding="utf-8"
            )
        )

        if not isinstance(
            temp_payload,
            dict,
        ):

            raise RuntimeError(
                "temp payload 不是 dict"
            )

        # ----------------------------------------------------
        # Atomic replace
        # ----------------------------------------------------

        temp_path.replace(
            UNIVERSE_FILE
        )

        temp_path = None

        log(
            f"✓ Atomic Write："
            f"{UNIVERSE_FILE}"
        )

        return True

    except Exception as exc:

        log(
            f"❌ Atomic Write FAIL："
            f"{exc}"
        )

        if (
            temp_path is not None
            and temp_path.exists()
        ):

            try:
                temp_path.unlink()

            except Exception:
                pass

        return False


# ============================================================
# POST WRITE VERIFY
# ============================================================

def post_write_verify(
    expected_payload: Dict[str, Any],
) -> bool:

    section(
        "Post-Write Verify"
    )

    if not UNIVERSE_FILE.exists():

        log(
            "❌ universe.json 不存在"
        )

        return False

    try:

        actual = json.loads(
            UNIVERSE_FILE.read_text(
                encoding="utf-8"
            )
        )

    except Exception as exc:

        log(
            f"❌ universe.json "
            f"JSON 解析失敗：{exc}"
        )

        return False

    if not isinstance(
        actual,
        dict,
    ):

        log(
            "❌ universe.json "
            "根節點不是 dict"
        )

        return False

    # --------------------------------------------------------
    # universe count
    # --------------------------------------------------------

    expected_stocks = (
        expected_payload.get(
            "stocks",
            {}
        )
    )

    actual_stocks = (
        actual.get(
            "stocks",
            {}
        )
    )

    if not isinstance(
        actual_stocks,
        dict,
    ):

        log(
            "❌ 寫入後 stocks 不是 dict"
        )

        return False

    if len(
        actual_stocks
    ) != len(
        expected_stocks
    ):

        log(
            "❌ 寫入後數量不一致"
        )

        return False

    # --------------------------------------------------------
    # exact symbol set
    # --------------------------------------------------------

    expected_symbols = set(
        expected_stocks.keys()
    )

    actual_symbols = set(
        actual_stocks.keys()
    )

    if (
        expected_symbols
        != actual_symbols
    ):

        missing = sorted(
            expected_symbols
            - actual_symbols
        )

        extra = sorted(
            actual_symbols
            - expected_symbols
        )

        log(
            f"❌ symbol set mismatch"
        )

        log(
            f"   missing：{missing[:20]}"
        )

        log(
            f"   extra：{extra[:20]}"
        )

        return False

    # --------------------------------------------------------
    # record-level verify
    # --------------------------------------------------------

    for symbol in sorted(
        expected_symbols
    ):

        expected = (
            expected_stocks[
                symbol
            ]
        )

        actual_item = (
            actual_stocks[
                symbol
            ]
        )

        if expected != actual_item:

            log(
                f"❌ {symbol}: "
                "寫入前後資料不一致"
            )

            return False

    # --------------------------------------------------------
    # count
    # --------------------------------------------------------

    if actual.get(
        "universe_count"
    ) != len(
        actual_stocks
    ):

        log(
            "❌ universe_count "
            "寫入後錯誤"
        )

        return False

    # --------------------------------------------------------
    # active
    # --------------------------------------------------------

    active_count = sum(
        1
        for item in actual_stocks.values()
        if item.get(
            "status"
        ) == "active"
    )

    if active_count != len(
        actual_stocks
    ):

        log(
            "❌ 寫入後存在 "
            "非 active 股票"
        )

        return False

    # --------------------------------------------------------
    # final structure
    # --------------------------------------------------------

    if not structure_gate(
        actual_stocks
    ):

        log(
            "❌ Post-Write Structure Gate FAIL"
        )

        return False

    log(
        "✓ universe.json 重新讀取成功"
    )

    log(
        f"✓ Total："
        f"{len(actual_stocks)}"
    )

    log(
        f"✓ Active："
        f"{active_count}"
    )

    log(
        "✓ Symbol set 完全一致"
    )

    log(
        "✓ Record 完全一致"
    )

    log(
        "✓ Post-Write Verify PASS"
    )

    return True


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    start_time = time.time()

    section(
        f"台股 AI 選股系統 "
        f"Universe Builder "
        f"{VERSION}"
    )

    log(
        f"開始時間："
        f"{now_tw().isoformat()}"
    )

    try:

        # ====================================================
        # 1. TWSE
        # ====================================================

        twse = fetch_market_universe(
            "TWSE",
            TWSE_API,
        )

        time.sleep(
            0.8
        )

        # ====================================================
        # 2. TPEx
        # ====================================================

        tpex = fetch_market_universe(
            "TPEX",
            TPEX_API,
        )

        # ====================================================
        # 3. Merge
        # ====================================================

        stocks_list = merge_markets(
            twse,
            tpex,
        )

        # ====================================================
        # 4. Convert
        # ====================================================

        stocks: Dict[
            str,
            Dict[str, str],
        ] = {}

        for item in stocks_list:

            symbol = item[
                "symbol"
            ]

            if symbol in stocks:

                raise RuntimeError(
                    f"Duplicate symbol："
                    f"{symbol}"
                )

            stocks[
                symbol
            ] = item

        # ====================================================
        # 5. Structure Gate
        # ====================================================

        if not structure_gate(
            stocks
        ):

            log(
                "❌ BUILD STOP"
            )

            return 1

        # ====================================================
        # 6. Data Quality Gate
        # ====================================================

        if not data_quality_gate(
            stocks
        ):

            log(
                "❌ BUILD STOP"
            )

            return 1

        # ====================================================
        # 7. Payload
        # ====================================================

        payload = build_payload(
            stocks_list
        )

        # ====================================================
        # 8. Payload Contract
        # ====================================================

        if not payload_contract_gate(
            payload
        ):

            log(
                "❌ BUILD STOP"
            )

            return 1

        # ====================================================
        # 9. Atomic Write
        # ====================================================

        if not atomic_write(
            payload
        ):

            log(
                "❌ BUILD STOP"
            )

            return 1

        # ====================================================
        # 10. Post Write Verify
        # ====================================================

        if not post_write_verify(
            payload
        ):

            log(
                "❌ BUILD STOP"
            )

            return 1

        # ====================================================
        # 11. RESULT
        # ====================================================

        elapsed = (
            time.time()
            - start_time
        )

        section(
            "BUILD RESULT"
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

        active_count = sum(
            1
            for item in stocks.values()
            if item.get(
                "status"
            ) == "active"
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
            f"✓ Active："
            f"{active_count}"
        )

        log(
            f"✓ universe_count："
            f"{payload['universe_count']}"
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

        log(
            f"❌ BUILD EXCEPTION："
            f"{exc}"
        )

        return 1


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    sys.exit(main())
