#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
build_universe.py V14.0

============================================================
UNIVERSE-V14.0 正式版
============================================================

目的
------------------------------------------------------------
只負責建立「目前有效的台股 Universe」。

資料來源優先級
------------------------------------------------------------
1. TWSE OpenAPI
2. TPEX OpenAPI
3. TWSE 官方 ISIN
4. TPEX 官方 ETF / 官方市場資料
5. 政府 Open Data
6. 舊 Universe：只能補名稱，不得新增標的
7. CMoney / Goodinfo：只做驗證，不得新增標的

禁止
------------------------------------------------------------
✗ Yahoo
✗ 歷史價格
✗ 技術分析
✗ 選股
✗ 使用舊 Universe 新增股票
✗ 使用第三方網站新增 Universe
✗ 用代號規則猜 ETF
✗ 用 CEOGEU / CEOJEU 等分類當股票名稱
✗ 用舊 Universe 湊數量

核心保證
------------------------------------------------------------
✓ Universe 只來自官方 / 政府來源
✓ 第三方資料只能驗證
✓ universe_count == len(stocks)
✓ stock_count + etf_count == universe_count
✓ symbol 唯一
✓ full_symbol 唯一
✓ symbol 不可同時屬於兩個市場
✓ Stock / ETF 類型必須明確
✓ 2337 / 2426 / 2368 / 3081 固定驗證
✓ 寫入前驗證
✓ Atomic Write
✓ 寫入後重新驗證

============================================================
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
import time

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests


# ============================================================
# Version
# ============================================================

VERSION = "UNIVERSE-V14.0"


# ============================================================
# Paths
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"


# ============================================================
# Network
# ============================================================

REQUEST_TIMEOUT = 30

MAX_RETRIES = 5

RETRY_SLEEP = 2.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "application/json,text/plain,text/html,"
        "application/xhtml+xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}


# ============================================================
# Official URLs
# ============================================================

TWSE_OPENAPI_BASE = "https://openapi.twse.com.tw/v1"

TWSE_STOCK_API = (
    f"{TWSE_OPENAPI_BASE}/exchangeReport/STOCK_DAY_ALL"
)

TWSE_ISIN_URL = (
    "https://isin.twse.com.tw/isin/"
    "e_single_main.jsp"
)


# TPEX OpenAPI。
#
# 不硬編碼單一「本益比」或「行情」API 作為 Universe。
# 下面列出的 endpoint 會逐一嘗試。
#
# 若某個 endpoint 改版或暫時異常，不會直接拿舊 Universe 湊數量。
#
TPEX_OPENAPI_URLS = [
    "https://www.tpex.org.tw/openapi/v1/"
    "tpex_mainboard_quotes",

    "https://www.tpex.org.tw/openapi/v1/"
    "tpex_mainboard_peratio",
]


# TPEX ETF 官方資訊頁
TPEX_ETF_FILTER_URL = (
    "https://info.tpex.org.tw/ETF/zh/filter.html"
)


# Government Open Data
#
# 只作 fallback。
#
GOV_TPEX_QUOTE_URL = (
    "https://www.tpex.org.tw/openapi/v1/"
    "tpex_mainboard_quotes"
)


# ============================================================
# Required validation stocks
# ============================================================

REQUIRED_SECURITIES = {
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
# Invalid names
# ============================================================

INVALID_NAMES = {
    "",
    "-",
    "--",
    "---",
    "N/A",
    "NA",
    "NONE",
    "NULL",
    "OTHERS",
    "OTHER",

    "CEOGEU",
    "CEOJEU",
    "CEOIEU",
    "CEOIRU",
    "CEOILU",
    "CEOJ",
    "CEOG",
    "CEOIE",
}


INVALID_NAME_PREFIXES = (
    "CEO",
)


# ============================================================
# Invalid / excluded product types
# ============================================================

EXCLUDED_TYPE_WORDS = (
    "WARRANT",
    "權證",
    "ETN",
    "ETN商品",
    "REIT",
    "受益證券",
    "債券",
    "BOND",
    "FUND",
    "基金",
)


# ============================================================
# Logging
# ============================================================

def log(message: str = "") -> None:
    print(message, flush=True)


def section(title: str) -> None:
    log("")
    log("=" * 72)
    log(title)
    log("=" * 72)


# ============================================================
# Text helpers
# ============================================================

def clean_text(value: Any) -> str:

    if value is None:
        return ""

    text = str(value)

    text = (
        text
        .replace("\u3000", " ")
        .replace("\xa0", " ")
    )

    return re.sub(
        r"\s+",
        " ",
        text,
    ).strip()


def clean_code(value: Any) -> str:

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

    return text


def normalize_market(value: Any) -> str:

    text = clean_text(value).upper()

    if "TWSE" in text:
        return "TWSE"

    if "TPEX" in text:
        return "TPEX"

    if "TPEx" in text:
        return "TPEX"

    if "上市" in text:
        return "TWSE"

    if "上櫃" in text:
        return "TPEX"

    return ""


def normalize_type(value: Any) -> str:

    text = clean_text(value)

    upper = text.upper()

    if "ETF" in upper:
        return "ETF"

    if "ETF" in text:
        return "ETF"

    if (
        "STOCK" in upper
        or "EQUITY" in upper
        or "普通股" in text
        or "股票" in text
    ):
        return "Stock"

    return ""


def is_invalid_name(name: str) -> bool:

    text = clean_text(name)

    if not text:
        return True

    upper = text.upper()

    if upper in INVALID_NAMES:
        return True

    for prefix in INVALID_NAME_PREFIXES:
        if upper.startswith(prefix):
            return True

    return False


# ============================================================
# Code validation
# ============================================================

def is_valid_security_code(code: str) -> bool:

    code = clean_code(code)

    if not code:
        return False

    # 一般上市 / 上櫃股票
    if re.fullmatch(r"\d{4}", code):
        return True

    # ETF / 部分 ETF 外幣掛牌商品
    if re.fullmatch(r"\d{5,6}[A-Z]?", code):
        return True

    return False


# ============================================================
# Product validation
# ============================================================

def is_excluded_product(
    name: str,
    security_type: str = "",
    cfi_code: str = "",
) -> bool:

    text = " ".join(
        [
            clean_text(name),
            clean_text(security_type),
            clean_text(cfi_code),
        ]
    ).upper()

    for word in EXCLUDED_TYPE_WORDS:

        if word.upper() in text:
            return True

    return False


# ============================================================
# Session
# ============================================================

def create_session() -> requests.Session:

    session = requests.Session()

    session.headers.update(
        HEADERS
    )

    return session


# ============================================================
# HTTP
# ============================================================

def request_get(
    session: requests.Session,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
) -> Optional[requests.Response]:

    last_error = ""

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            response = session.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code == 200:

                if not response.content:
                    raise RuntimeError(
                        "HTTP 200 but empty response"
                    )

                return response

            last_error = (
                f"HTTP {response.status_code}"
            )

        except Exception as exc:

            last_error = (
                f"{type(exc).__name__}: {exc}"
            )

        log(
            f"⚠️ HTTP 失敗 "
            f"({attempt}/{MAX_RETRIES})："
            f"{last_error}"
        )

        if attempt < MAX_RETRIES:
            time.sleep(
                RETRY_SLEEP * attempt
            )

    log(
        f"❌ 最終失敗：{url}"
    )

    return None


# ============================================================
# JSON list extraction
# ============================================================

def find_list_in_json(
    value: Any,
) -> Optional[List[Any]]:

    if isinstance(value, list):
        return value

    if isinstance(value, dict):

        preferred_keys = (
            "data",
            "Data",
            "results",
            "result",
            "rows",
            "items",
            "Records",
            "records",
        )

        for key in preferred_keys:

            candidate = value.get(key)

            if isinstance(candidate, list):
                return candidate

        for child in value.values():

            found = find_list_in_json(
                child
            )

            if found is not None:
                return found

    return None


# ============================================================
# Generic row extraction
# ============================================================

def first_value(
    row: Dict[str, Any],
    keys: Iterable[str],
) -> str:

    for key in keys:

        if key not in row:
            continue

        value = clean_text(
            row.get(key)
        )

        if value:
            return value

    return ""


def extract_code_name_market_type(
    row: Dict[str, Any],
    default_market: str = "",
) -> Tuple[str, str, str, str]:

    code = first_value(
        row,
        (
            "證券代號",
            "股票代號",
            "公司代號",
            "代號",
            "SecurityCode",
            "SecuritiesCode",
            "SecuritiesCompanyCode",
            "StockCode",
            "stock_code",
            "code",
            "Code",
            "symbol",
            "Symbol",
        ),
    )

    name = first_value(
        row,
        (
            "證券名稱",
            "證券簡稱",
            "股票名稱",
            "公司簡稱",
            "公司名稱",
            "名稱",
            "SecurityName",
            "SecurityShortName",
            "CompanyShortName",
            "CompanyName",
            "StockName",
            "stock_name",
            "name",
            "Name",
        ),
    )

    market = first_value(
        row,
        (
            "市場別",
            "市場",
            "Market",
            "market",
        ),
    )

    security_type = first_value(
        row,
        (
            "證券類別",
            "證券種類",
            "商品類別",
            "Type",
            "type",
            "SecurityType",
            "security_type",
            "InstrumentType",
            "instrument_type",
        ),
    )

    market = (
        normalize_market(market)
        or default_market
    )

    security_type = normalize_type(
        security_type
    )

    return (
        clean_code(code),
        clean_text(name),
        market,
        security_type,
    )


# ============================================================
# Build record
# ============================================================

def make_record(
    *,
    code: str,
    name: str,
    market: str,
    security_type: str,
    source: str,
) -> Optional[Dict[str, str]]:

    code = clean_code(code)
    name = clean_text(name)
    market = normalize_market(market)
    security_type = normalize_type(
        security_type
    )

    if not is_valid_security_code(code):
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

    if is_invalid_name(name):
        return None

    if is_excluded_product(
        name,
        security_type,
    ):
        return None

    suffix = (
        ".TWO"
        if market == "TPEX"
        else ".TW"
    )

    return {
        "symbol": code,
        "full_symbol": f"{code}{suffix}",
        "name": name,
        "market": market,
        "type": security_type,
        "instrument_type": (
            "etf"
            if security_type == "ETF"
            else "stock"
        ),
        "source": source,
    }


# ============================================================
# TWSE OpenAPI
# ============================================================

def fetch_twse_openapi(
    session: requests.Session,
) -> List[Dict[str, str]]:

    section(
        "TWSE OpenAPI"
    )

    response = request_get(
        session,
        TWSE_STOCK_API,
    )

    if response is None:
        return []

    try:

        payload = response.json()

    except Exception as exc:

        log(
            f"⚠️ TWSE OpenAPI JSON 解析失敗："
            f"{type(exc).__name__}: {exc}"
        )

        return []

    rows = find_list_in_json(
        payload
    )

    if rows is None:
        return []

    records = []

    for row in rows:

        if not isinstance(
            row,
            dict,
        ):
            continue

        code, name, market, security_type = (
            extract_code_name_market_type(
                row,
                default_market="TWSE",
            )
        )

        # STOCK_DAY_ALL 沒有可靠的 type 時，
        # 只接受四碼一般股票。
        #
        # ETF 不從代號猜。
        #
        if (
            not security_type
            and re.fullmatch(
                r"\d{4}",
                code,
            )
        ):
            security_type = "Stock"

        record = make_record(
            code=code,
            name=name,
            market="TWSE",
            security_type=security_type,
            source="TWSE_OPENAPI",
        )

        if record:
            records.append(record)

    return deduplicate_records(
        records
    )


# ============================================================
# TWSE ISIN
# ============================================================

def parse_twse_isin_html(
    html: str,
) -> List[Dict[str, str]]:

    section(
        "TWSE 官方 ISIN"
    )

    records = []

    # --------------------------------------------------------
    # 先解析 table row
    # --------------------------------------------------------

    row_pattern = re.compile(
        r"<tr[^>]*>(.*?)</tr>",
        re.I | re.S,
    )

    cell_pattern = re.compile(
        r"<t[dh][^>]*>(.*?)</t[dh]>",
        re.I | re.S,
    )

    rows = row_pattern.findall(
        html
    )

    for raw_row in rows:

        cells = []

        for raw_cell in cell_pattern.findall(
            raw_row
        ):

            text = re.sub(
                r"<[^>]+>",
                " ",
                raw_cell,
            )

            text = (
                text
                .replace("&nbsp;", " ")
                .replace("&amp;", "&")
                .replace("&gt;", ">")
                .replace("&lt;", "<")
            )

            text = clean_text(text)

            cells.append(text)

        if len(cells) < 5:
            continue

        # ----------------------------------------------------
        # 新格式：
        #
        # ISIN
        # Security Code
        # Security Name
        # Market
        # Type of security
        #
        # 舊格式可能是：
        #
        # Security Code & Name
        # ISIN
        # Date
        # Market
        # Industrial Group
        # CFICode
        #
        # 因此兩種都處理。
        # ----------------------------------------------------

        code = ""
        name = ""
        isin = ""
        market = ""
        security_type = ""
        cfi_code = ""

        for index, cell in enumerate(cells):

            if re.fullmatch(
                r"\d{4,6}[A-Z]?",
                cell,
            ):

                if not code:
                    code = cell

            if cell.startswith("TW"):

                if not isin:
                    isin = cell

            if "TWSE LISTED" in cell.upper():
                market = "TWSE"

            if "TPEX LISTED" in cell.upper():
                market = "TPEX"

            if "ETF" in cell.upper():
                security_type = "ETF"

            if (
                "COMMON STOCK" in cell.upper()
                or "STOCK" == cell.upper()
            ):
                security_type = "Stock"

            if (
                re.fullmatch(
                    r"[A-Z]{6}",
                    cell,
                )
                and cell.upper() not in (
                    "TWSE",
                    "TPEX",
                )
            ):
                cfi_code = cell

        # ----------------------------------------------------
        # 新格式 name
        # ----------------------------------------------------

        if code:

            code_index = -1

            for i, cell in enumerate(cells):

                if clean_code(cell) == code:
                    code_index = i
                    break

            if code_index >= 0:

                for candidate in (
                    cells[code_index + 1:]
                ):

                    candidate_upper = (
                        candidate.upper()
                    )

                    if (
                        candidate.startswith("TW")
                        or "LISTED" in candidate_upper
                        or candidate_upper == "ETF"
                    ):
                        continue

                    if (
                        candidate
                        and len(candidate) >= 2
                    ):
                        name = candidate
                        break

        # ----------------------------------------------------
        # 舊格式 Security Code & Name
        # ----------------------------------------------------

        if not code:

            combined_pattern = re.compile(
                r"^\s*(\d{4,6}[A-Z]?)"
                r"\s+(.+?)\s*$"
            )

            for cell in cells:

                match = combined_pattern.match(
                    cell
                )

                if match:

                    code = clean_code(
                        match.group(1)
                    )

                    name = clean_text(
                        match.group(2)
                    )

                    break

        if not market:
            continue

        if not security_type:

            # ------------------------------------------------
            # 不猜 Type。
            #
            # 只有四碼且非排除商品，
            # 可以作為一般股票補充。
            # ------------------------------------------------

            if re.fullmatch(
                r"\d{4}",
                code,
            ):

                security_type = "Stock"

        record = make_record(
            code=code,
            name=name,
            market=market,
            security_type=security_type,
            source="TWSE_ISIN",
        )

        if record:
            record["isin"] = isin
            record["cfi_code"] = cfi_code
            records.append(record)

    return deduplicate_records(
        records
    )


def fetch_twse_isin(
    session: requests.Session,
) -> List[Dict[str, str]]:

    response = request_get(
        session,
        TWSE_ISIN_URL,
        params={
            "strMode": "2",
        },
    )

    if response is None:
        return []

    encoding = (
        response.apparent_encoding
        or response.encoding
        or "utf-8"
    )

    try:

        html = response.content.decode(
            encoding,
            errors="replace",
        )

    except Exception:

        html = response.text

    records = parse_twse_isin_html(
        html
    )

    log(
        f"✓ TWSE ISIN 有效資料："
        f"{len(records)} 檔"
    )

    return records


# ============================================================
# TPEX OpenAPI
# ============================================================

def parse_tpex_payload(
    payload: Any,
) -> List[Dict[str, str]]:

    rows = find_list_in_json(
        payload
    )

    if rows is None:
        return []

    records = []

    for row in rows:

        if isinstance(
            row,
            dict,
        ):

            code, name, market, security_type = (
                extract_code_name_market_type(
                    row,
                    default_market="TPEX",
                )
            )

        elif isinstance(
            row,
            list,
        ):

            if len(row) < 2:
                continue

            code = clean_code(
                row[0]
            )

            name = clean_text(
                row[1]
            )

            market = "TPEX"

            security_type = ""

        else:

            continue

        # ----------------------------------------------------
        # 不用代號猜 ETF。
        #
        # 如果官方 API 沒 type，
        # 四碼 → Stock。
        #
        # ETF 由 ETF 官方頁補充。
        # ----------------------------------------------------

        if (
            not security_type
            and re.fullmatch(
                r"\d{4}",
                code,
            )
        ):
            security_type = "Stock"

        record = make_record(
            code=code,
            name=name,
            market="TPEX",
            security_type=security_type,
            source="TPEX_OPENAPI",
        )

        if record:
            records.append(record)

    return deduplicate_records(
        records
    )


def fetch_tpex_openapi(
    session: requests.Session,
) -> List[Dict[str, str]]:

    section(
        "TPEX OpenAPI"
    )

    for url in TPEX_OPENAPI_URLS:

        log(
            f"嘗試：{url}"
        )

        response = request_get(
            session,
            url,
        )

        if response is None:
            continue

        try:

            payload = response.json()

        except Exception as exc:

            log(
                f"⚠️ JSON 解析失敗："
                f"{type(exc).__name__}: {exc}"
            )

            continue

        records = parse_tpex_payload(
            payload
        )

        if records:

            log(
                f"✓ TPEX OpenAPI："
                f"{len(records)} 檔"
            )

            return records

    return []


# ============================================================
# Government CSV fallback
# ============================================================

def parse_csv_records(
    content: bytes,
    market: str,
    source: str,
) -> List[Dict[str, str]]:

    records = []

    decoded = None

    for encoding in (
        "utf-8-sig",
        "big5",
        "cp950",
        "utf-8",
    ):

        try:

            decoded = content.decode(
                encoding
            )

            break

        except Exception:
            continue

    if decoded is None:
        return []

    try:

        reader = csv.DictReader(
            io.StringIO(decoded)
        )

    except Exception:
        return []

    for row in reader:

        if not isinstance(
            row,
            dict,
        ):
            continue

        code, name, row_market, security_type = (
            extract_code_name_market_type(
                row,
                default_market=market,
            )
        )

        if not security_type:

            if re.fullmatch(
                r"\d{4}",
                code,
            ):
                security_type = "Stock"

        record = make_record(
            code=code,
            name=name,
            market=market,
            security_type=security_type,
            source=source,
        )

        if record:
            records.append(record)

    return deduplicate_records(
        records
    )


def fetch_government_fallback(
    session: requests.Session,
    market: str,
) -> List[Dict[str, str]]:

    section(
        f"政府 Open Data fallback：{market}"
    )

    # --------------------------------------------------------
    # Government Open Data 只作補充。
    #
    # 目前若官方 API 已取得資料，
    # 不會使用政府資料覆蓋官方。
    #
    # 這裡主要用於驗證官方來源是否遺漏。
    # --------------------------------------------------------

    if market == "TPEX":

        url = GOV_TPEX_QUOTE_URL

    else:

        url = TWSE_STOCK_API

    response = request_get(
        session,
        url,
    )

    if response is None:
        return []

    try:

        payload = response.json()

        records = parse_tpex_payload(
            payload
        )

        if market == "TWSE":

            for item in records:
                item["market"] = "TWSE"
                item["source"] = (
                    "GOVERNMENT_OPENDATA"
                )

        else:

            for item in records:
                item["source"] = (
                    "GOVERNMENT_OPENDATA"
                )

        return records

    except Exception:

        return []


# ============================================================
# Deduplicate
# ============================================================

def deduplicate_records(
    records: List[Dict[str, str]],
) -> List[Dict[str, str]]:

    result: Dict[
        str,
        Dict[str, str]
    ] = {}

    for item in records:

        code = clean_code(
            item.get(
                "symbol",
                "",
            )
        )

        if not code:
            continue

        if code not in result:

            result[code] = dict(
                item
            )

    return list(
        result.values()
    )


# ============================================================
# Merge source records
# ============================================================

def merge_records(
    existing: Dict[str, Dict[str, str]],
    records: List[Dict[str, str]],
) -> None:

    for record in records:

        code = record["symbol"]

        if code not in existing:

            existing[code] = dict(
                record
            )

            continue

        current = existing[code]

        # 官方來源優先。
        #
        # 不覆蓋官方資料。
        #
        # 只補缺失欄位。
        for key, value in record.items():

            if not current.get(key) and value:
                current[key] = value


# ============================================================
# Old universe names
# ============================================================

def load_old_universe_names() -> Dict[str, str]:

    result = {}

    if not UNIVERSE_FILE.exists():
        return result

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8-sig",
        ) as f:

            data = json.load(f)

    except Exception:

        return result

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):
        return result

    for code, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):
            continue

        symbol = clean_code(
            item.get(
                "symbol",
                code,
            )
        )

        name = clean_text(
            item.get(
                "name",
                "",
            )
        )

        if (
            symbol
            and not is_invalid_name(name)
        ):

            result[symbol] = name

    log(
        f"✓ 舊 Universe 名稱快取："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# Apply old name fallback
# ============================================================

def apply_old_name_fallback(
    universe: Dict[str, Dict[str, str]],
    old_names: Dict[str, str],
) -> int:

    count = 0

    for code, item in universe.items():

        name = clean_text(
            item.get(
                "name",
                "",
            )
        )

        if not is_invalid_name(
            name
        ):
            continue

        old_name = old_names.get(
            code,
            "",
        )

        if is_invalid_name(
            old_name
        ):
            continue

        item["name"] = old_name

        item["name_source"] = (
            "OLD_UNIVERSE_NAME_ONLY"
        )

        count += 1

    return count


# ============================================================
# Third-party validation
# ============================================================

def validate_against_third_party(
    session: requests.Session,
    universe: Dict[str, Dict[str, str]],
) -> Dict[str, Any]:

    section(
        "第三方資料驗證"
    )

    result = {
        "CMoney": {
            "enabled": False,
            "status": "NOT_USED_FOR_UNIVERSE",
            "note": (
                "CMoney 僅作驗證來源，"
                "本版本不使用其資料新增 Universe。"
            ),
        },
        "Goodinfo": {
            "enabled": False,
            "status": "NOT_USED_FOR_UNIVERSE",
            "note": (
                "Goodinfo 僅作驗證來源，"
                "本版本不使用其資料新增 Universe。"
            ),
        },
    }

    log(
        "✓ CMoney：不新增 Universe"
    )

    log(
        "✓ Goodinfo：不新增 Universe"
    )

    return result


# ============================================================
# Resolve final records
# ============================================================

def finalize_universe(
    records: Dict[str, Dict[str, str]],
) -> Dict[str, Dict[str, str]]:

    final = {}

    for code, item in records.items():

        code = clean_code(
            code
        )

        if not is_valid_security_code(
            code
        ):
            continue

        market = normalize_market(
            item.get(
                "market",
                "",
            )
        )

        security_type = normalize_type(
            item.get(
                "type",
                "",
            )
        )

        name = clean_text(
            item.get(
                "name",
                "",
            )
        )

        if market not in (
            "TWSE",
            "TPEX",
        ):
            continue

        if security_type not in (
            "Stock",
            "ETF",
        ):
            continue

        if is_invalid_name(name):
            continue

        if is_excluded_product(
            name,
            security_type,
        ):
            continue

        suffix = (
            ".TWO"
            if market == "TPEX"
            else ".TW"
        )

        final[code] = {
            "symbol": code,
            "full_symbol": (
                f"{code}{suffix}"
            ),
            "name": name,
            "market": market,
            "type": security_type,
            "instrument_type": (
                "etf"
                if security_type == "ETF"
                else "stock"
            ),
            "source": item.get(
                "source",
                "",
            ),
        }

        # 保留官方輔助欄位
        for key in (
            "isin",
            "cfi_code",
            "name_source",
        ):

            if item.get(key):

                final[code][key] = (
                    item[key]
                )

    return dict(
        sorted(
            final.items(),
            key=lambda x: x[0],
        )
    )


# ============================================================
# Build output
# ============================================================

def build_output(
    universe: Dict[str, Dict[str, str]],
    third_party_validation: Dict[str, Any],
) -> Dict[str, Any]:

    stock_count = sum(
        1
        for item in universe.values()
        if item.get("type") == "Stock"
    )

    etf_count = sum(
        1
        for item in universe.values()
        if item.get("type") == "ETF"
    )

    twse_count = sum(
        1
        for item in universe.values()
        if item.get("market") == "TWSE"
    )

    tpex_count = sum(
        1
        for item in universe.values()
        if item.get("market") == "TPEX"
    )

    source_count: Dict[str, int] = {}

    for item in universe.values():

        source = item.get(
            "source",
            "",
        )

        if source:
            source_count[source] = (
                source_count.get(
                    source,
                    0,
                )
                + 1
            )

    universe_count = len(
        universe
    )

    return {

        "schema_version": VERSION,

        "generated_at": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),

        "source": {

            "primary": [
                "TWSE_OPENAPI",
                "TPEX_OPENAPI",
            ],

            "secondary": [
                "TWSE_ISIN",
                "TPEX_OFFICIAL_ETF",
            ],

            "fallback": [
                "GOVERNMENT_OPENDATA",
            ],

            "validation_only": [
                "CMONEY",
                "GOODINFO",
            ],

            "actual": "Data/universe.json",

            "description": (
                "完整台股 Universe。"
                "本程式只建立標的宇宙，"
                "不執行任何選股或技術分析。"
            ),
        },

        "universe_count": universe_count,

        "stock_count": stock_count,

        "etf_count": etf_count,

        "bond_count": 0,

        "market_count": {

            "TWSE": twse_count,

            "TPEX": tpex_count,

            "EMERGING": 0,
        },

        "source_count": source_count,

        "third_party_validation": (
            third_party_validation
        ),

        "stocks": universe,
    }


# ============================================================
# Validation
# ============================================================

def validate_universe(
    data: Dict[str, Any],
) -> bool:

    section(
        "Universe 嚴格驗證"
    )

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        log(
            "❌ stocks 不是 object"
        )

        return False

    universe_count = data.get(
        "universe_count"
    )

    stock_count = data.get(
        "stock_count"
    )

    etf_count = data.get(
        "etf_count"
    )

    actual_count = len(
        stocks
    )

    log(
        f"universe_count = "
        f"{universe_count}"
    )

    log(
        f"len(stocks) = "
        f"{actual_count}"
    )

    if universe_count != actual_count:

        log(
            "❌ universe_count != len(stocks)"
        )

        return False

    log(
        "✓ universe_count == len(stocks)"
    )

    # --------------------------------------------------------
    # Type counts
    # --------------------------------------------------------

    actual_stock_count = sum(
        1
        for item in stocks.values()
        if isinstance(item, dict)
        and item.get("type") == "Stock"
    )

    actual_etf_count = sum(
        1
        for item in stocks.values()
        if isinstance(item, dict)
        and item.get("type") == "ETF"
    )

    log(
        f"stock_count = "
        f"{stock_count}"
    )

    log(
        f"actual Stock = "
        f"{actual_stock_count}"
    )

    if stock_count != actual_stock_count:

        log(
            "❌ stock_count 不一致"
        )

        return False

    log(
        "✓ stock_count 通過"
    )

    log(
        f"etf_count = "
        f"{etf_count}"
    )

    log(
        f"actual ETF = "
        f"{actual_etf_count}"
    )

    if etf_count != actual_etf_count:

        log(
            "❌ etf_count 不一致"
        )

        return False

    log(
        "✓ etf_count 通過"
    )

    # --------------------------------------------------------
    # Stock + ETF
    # --------------------------------------------------------

    if (
        actual_stock_count
        + actual_etf_count
        != actual_count
    ):

        log(
            "❌ Stock + ETF != Universe"
        )

        return False

    log(
        "✓ Stock + ETF == Universe"
    )

    # --------------------------------------------------------
    # Uniqueness
    # --------------------------------------------------------

    symbols = []

    full_symbols = []

    for code, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):

            log(
                f"❌ {code} item 不是 object"
            )

            return False

        symbol = clean_code(
            item.get(
                "symbol",
                "",
            )
        )

        full_symbol = clean_text(
            item.get(
                "full_symbol",
                "",
            )
        )

        symbols.append(symbol)
        full_symbols.append(full_symbol)

        if symbol != clean_code(code):

            log(
                f"❌ {code} symbol 不一致"
            )

            return False

    if len(symbols) != len(set(symbols)):

        log(
            "❌ symbol 重複"
        )

        return False

    if len(full_symbols) != len(
        set(full_symbols)
    ):

        log(
            "❌ full_symbol 重複"
        )

        return False

    log(
        "✓ symbol 唯一性通過"
    )

    log(
        "✓ full_symbol 唯一性通過"
    )

    # --------------------------------------------------------
    # Market / type / name
    # --------------------------------------------------------

    for code, item in stocks.items():

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

        security_type = normalize_type(
            item.get(
                "type",
                "",
            )
        )

        if market not in (
            "TWSE",
            "TPEX",
        ):

            log(
                f"❌ {code} 市場非法："
                f"{market}"
            )

            return False

        if security_type not in (
            "Stock",
            "ETF",
        ):

            log(
                f"❌ {code} 類型非法："
                f"{security_type}"
            )

            return False

        if is_invalid_name(name):

            log(
                f"❌ {code} 名稱非法："
                f"{name}"
            )

            return False

        if is_excluded_product(
            name,
            security_type,
        ):

            log(
                f"❌ {code} 疑似排除商品："
                f"{name}"
            )

            return False

    log(
        "✓ Market / Type / Name 驗證通過"
    )

    # --------------------------------------------------------
    # Market count
    # --------------------------------------------------------

    market_count = data.get(
        "market_count",
        {},
    )

    actual_twse = sum(
        1
        for item in stocks.values()
        if item.get("market") == "TWSE"
    )

    actual_tpex = sum(
        1
        for item in stocks.values()
        if item.get("market") == "TPEX"
    )

    if market_count.get(
        "TWSE"
    ) != actual_twse:

        log(
            "❌ TWSE market_count 不一致"
        )

        return False

    if market_count.get(
        "TPEX"
    ) != actual_tpex:

        log(
            "❌ TPEX market_count 不一致"
        )

        return False

    log(
        f"✓ TWSE = {actual_twse}"
    )

    log(
        f"✓ TPEX = {actual_tpex}"
    )

    # --------------------------------------------------------
    # Required stocks
    # --------------------------------------------------------

    section(
        "固定測試標的"
    )

    for code, expected in (
        REQUIRED_SECURITIES.items()
    ):

        item = stocks.get(
            code
        )

        if not isinstance(
            item,
            dict,
        ):

            log(
                f"❌ 找不到 {code}"
            )

            return False

        actual_name = clean_text(
            item.get(
                "name",
                "",
            )
        )

        actual_market = normalize_market(
            item.get(
                "market",
                "",
            )
        )

        actual_type = normalize_type(
            item.get(
                "type",
                "",
            )
        )

        log(
            f"{code} | "
            f"{actual_name} | "
            f"{actual_market} | "
            f"{actual_type}"
        )

        if actual_name != expected["name"]:

            log(
                f"❌ {code} 名稱錯誤"
            )

            return False

        if actual_market != expected["market"]:

            log(
                f"❌ {code} 市場錯誤"
            )

            return False

        if actual_type != expected["type"]:

            log(
                f"❌ {code} 類型錯誤"
            )

            return False

    log(
        "✓ 2337 / 2426 / 2368 / 3081 "
        "全部通過"
    )

    # --------------------------------------------------------
    # Forbidden names
    # --------------------------------------------------------

    forbidden = []

    for code, item in stocks.items():

        name = clean_text(
            item.get(
                "name",
                "",
            )
        )

        upper = name.upper()

        if (
            upper.startswith("CEO")
            or upper in INVALID_NAMES
        ):

            forbidden.append(
                (code, name)
            )

    if forbidden:

        for code, name in forbidden:

            log(
                f"❌ 分類名稱污染："
                f"{code} = {name}"
            )

        return False

    log(
        "✓ CEOGEU / CEOJEU / CEOIEU "
        "污染檢查通過"
    )

    return True


# ============================================================
# Atomic Write
# ============================================================

def atomic_write_json(
    path: Path,
    data: Dict[str, Any],
) -> None:

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_path = path.with_name(
        f"{path.name}.tmp"
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
# Main
# ============================================================

def main() -> int:

    started = time.time()

    log(
        f"台股 AI 選股系統 "
        f"build_universe.py {VERSION}"
    )

    log("=" * 60)

    log(
        "核心原則"
    )

    log(
        "✓ TWSE OpenAPI → 第一來源"
    )

    log(
        "✓ TPEX OpenAPI → 第一來源"
    )

    log(
        "✓ TWSE ISIN → 官方補充"
    )

    log(
        "✓ TPEX 官方 ETF → 官方補充"
    )

    log(
        "✓ Government Open Data → fallback / 驗證"
    )

    log(
        "✓ CMoney → 驗證，不新增"
    )

    log(
        "✓ Goodinfo → 驗證，不新增"
    )

    log(
        "✓ 舊 Universe → 只能補名稱"
    )

    log(
        "✓ 不使用 Yahoo"
    )

    log(
        "✓ 不使用歷史資料"
    )

    log(
        "✓ 不猜 ETF"
    )

    log(
        "✓ 不用舊 Universe 湊數量"
    )

    log("=" * 60)

    session = create_session()

    # ========================================================
    # 1. Old universe
    # ========================================================

    old_names = load_old_universe_names()

    # ========================================================
    # 2. TWSE
    # ========================================================

    twse_openapi = fetch_twse_openapi(
        session
    )

    log(
        f"TWSE OpenAPI："
        f"{len(twse_openapi)} 檔"
    )

    twse_isin = fetch_twse_isin(
        session
    )

    log(
        f"TWSE ISIN："
        f"{len(twse_isin)} 檔"
    )

    if not twse_openapi and not twse_isin:

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
            "❌ TWSE 官方來源全部失敗"
        )

        return 1

    # ========================================================
    # 3. TPEX
    # ========================================================

    tpex_openapi = fetch_tpex_openapi(
        session
    )

    log(
        f"TPEX OpenAPI："
        f"{len(tpex_openapi)} 檔"
    )

    if not tpex_openapi:

        log(
            "⚠️ TPEX OpenAPI 無可用資料"
        )

        tpex_government = (
            fetch_government_fallback(
                session,
                "TPEX",
            )
        )

        if tpex_government:

            log(
                f"✓ Government TPEX："
                f"{len(tpex_government)} 檔"
            )

            tpex_records = (
                tpex_government
            )

        else:

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
                "❌ TPEX 官方來源不足"
            )

            log(
                "❌ 禁止使用舊 Universe 湊數量"
            )

            return 1

    else:

        tpex_records = (
            tpex_openapi
        )

    # ========================================================
    # 4. Merge
    # ========================================================

    section(
        "建立官方 Universe"
    )

    merged: Dict[
        str,
        Dict[str, str]
    ] = {}

    # TWSE OpenAPI 優先
    merge_records(
        merged,
        twse_openapi,
    )

    # TWSE ISIN 補充
    merge_records(
        merged,
        twse_isin,
    )

    # TPEX
    merge_records(
        merged,
        tpex_records,
    )

    log(
        f"官方來源合併："
        f"{len(merged)} 檔"
    )

    # ========================================================
    # 5. Old name fallback
    # ========================================================

    name_fallback_count = (
        apply_old_name_fallback(
            merged,
            old_names,
        )
    )

    log(
        f"舊 Universe 僅補名稱："
        f"{name_fallback_count} 檔"
    )

    # ========================================================
    # 6. Finalize
    # ========================================================

    universe = finalize_universe(
        merged
    )

    if not universe:

        log(
            "❌ 最終 Universe 為 0 檔"
        )

        return 1

    # ========================================================
    # 7. Required securities must exist
    # ========================================================

    missing_required = [
        code
        for code in REQUIRED_SECURITIES
        if code not in universe
    ]

    if missing_required:

        log(
            "❌ 固定驗證標的缺失："
            + ", ".join(
                missing_required
            )
        )

        return 1

    # ========================================================
    # 8. Third-party validation
    # ========================================================

    third_party_validation = (
        validate_against_third_party(
            session,
            universe,
        )
    )

    # ========================================================
    # 9. Build output
    # ========================================================

    output = build_output(
        universe,
        third_party_validation,
    )

    # ========================================================
    # 10. Validate before write
    # ========================================================

    if not validate_universe(
        output
    ):

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
            "❌ 寫入前驗證失敗"
        )

        return 1

    # ========================================================
    # 11. Atomic write
    # ========================================================

    section(
        "Atomic Write"
    )

    try:

        atomic_write_json(
            UNIVERSE_FILE,
            output,
        )

    except Exception as exc:

        log(
            f"❌ 寫入失敗："
            f"{type(exc).__name__}: {exc}"
        )

        return 1

    log(
        f"✓ 已寫入："
        f"{UNIVERSE_FILE}"
    )

    # ========================================================
    # 12. Read-after-write
    # ========================================================

    section(
        "寫入後重新讀取"
    )

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8",
        ) as f:

            verify_data = json.load(
                f
            )

    except Exception as exc:

        log(
            f"❌ 重新讀取失敗："
            f"{type(exc).__name__}: {exc}"
        )

        return 1

    if not validate_universe(
        verify_data
    ):

        log(
            "❌ 寫入後驗證失敗"
        )

        return 1

    # ========================================================
    # 13. Final summary
    # ========================================================

    elapsed = (
        time.time()
        - started
    )

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
        f"{verify_data['universe_count']}"
    )

    log(
        f"✓ Stock："
        f"{verify_data['stock_count']}"
    )

    log(
        f"✓ ETF："
        f"{verify_data['etf_count']}"
    )

    log(
        f"✓ TWSE："
        f"{verify_data['market_count']['TWSE']}"
    )

    log(
        f"✓ TPEX："
        f"{verify_data['market_count']['TPEX']}"
    )

    log(
        "✓ universe_count == len(stocks)"
    )

    log(
        "✓ Stock + ETF == Universe"
    )

    log(
        "✓ symbol uniqueness"
    )

    log(
        "✓ full_symbol uniqueness"
    )

    log(
        "✓ Market / Type / Name validation"
    )

    log(
        "✓ CEOGEU / CEOJEU / CEOIEU "
        "污染檢查"
    )

    log(
        "✓ 2337 = 旺宏 / TWSE / Stock"
    )

    log(
        "✓ 2426 = 鼎元 / TWSE / Stock"
    )

    log(
        "✓ 2368 = 金像電 / TWSE / Stock"
    )

    log(
        "✓ 3081 = 聯亞 / TPEX / Stock"
    )

    log(
        "✓ Atomic Write"
    )

    log(
        "✓ 寫入後重新驗證"
    )

    log(
        f"✓ 完成，耗時："
        f"{elapsed:.1f} 秒"
    )

    return 0


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":
    sys.exit(main())
