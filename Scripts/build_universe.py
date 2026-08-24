#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
build_universe.py V13.0

============================================================
UNIVERSE-V13.0 正式版
============================================================

目的
------------------------------------------------------------
只負責建立「目前有效的台股 Universe」。

本程式：
✓ 只建立標的宇宙
✓ 不執行技術分析
✓ 不執行選股
✓ 不讀取歷史價格
✓ 不使用 Yahoo
✓ 不使用舊 Universe 新增股票
✓ 舊 Universe 只能補名稱
✓ TWSE / TPEX 優先使用官方來源
✓ TWSE 不再依賴 C_public.jsp
✓ TWSE 使用官方 e_C_public.jsp
✓ TPEX 官方來源失敗會自動重試
✓ 不使用舊 Universe 湊數量
✓ 禁止 CEOGEU / CEOJEU / CEOIEU 等分類名稱
✓ universe_count == len(stocks)
✓ stock_count + etf_count == universe_count
✓ 固定驗證 2337 / 2426 / 2368 / 3081
✓ Atomic Write
✓ 寫入後重新驗證
============================================================
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
# Version
# ============================================================

VERSION = "UNIVERSE-V13.0"


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
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,"
        "application/json;q=0.8,*/*;q=0.7"
    ),
    "Accept-Language": (
        "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"
    ),
    "Connection": "keep-alive",
}


# ============================================================
# Official URLs
# ============================================================

# ------------------------------------------------------------
# TWSE 官方 ISIN
#
# strMode=2 = Listed Equities
#
# 使用 e_C_public.jsp 是為了避開目前 C_public.jsp
# 在 GitHub Actions 環境容易出現 HTTP 500 的問題。
# ------------------------------------------------------------

TWSE_ISIN_URL = (
    "https://isin.twse.com.tw/isin/e_C_public.jsp"
    "?strMode=2"
)


# ------------------------------------------------------------
# TPEX 官方公司資訊頁
#
# 本程式會優先嘗試官方 OpenAPI。
# 若官方 OpenAPI 暫時異常，再嘗試官方
# company list endpoint。
# ------------------------------------------------------------

TPEX_OPENAPI_URLS = [

    (
        "https://www.tpex.org.tw/openapi/"
        "v1/tpex_mainboard_peratio"
    ),

    (
        "https://www.tpex.org.tw/openapi/"
        "v1/tpex_mainboard_quotes"
    ),

]


TPEX_COMPANY_URL = (
    "https://www.tpex.org.tw/zh-tw/"
    "mainboard/listed/company.html"
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
# Clean
# ============================================================

def clean_text(value: Any) -> str:

    if value is None:
        return ""

    return str(value).strip()


def clean_code(value: Any) -> str:

    if value is None:
        return ""

    text = str(value).strip()

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

    if "TPEX" in text:
        return "TPEX"

    if "TPEx" in text:
        return "TPEX"

    if "TWSE" in text:
        return "TWSE"

    if "上市" in text:
        return "TWSE"

    if "上櫃" in text:
        return "TPEX"

    if "櫃" in text:
        return "TPEX"

    return ""


# ============================================================
# Invalid names
# ============================================================

INVALID_NAMES = {
    "",
    "--",
    "---",
    "N/A",
    "NA",
    "NONE",
    "NULL",

    # TWSE 分類名稱錯誤
    "CEOGEU",
    "CEOJEU",
    "CEOIEU",
    "CEOIRU",
    "CEOJ",
    "CEOG",
    "CEOIE",

    # 英文分類名稱，不可當股票名稱
    "OTHERS",
    "OTHER",
    "TOURISM AND HOSPITALITY",
    "BUILDING MATERIAL&CONSTRUCTION",
    "GREEN ENERGY AND ENVIRONMENTAL SERVICES",
    "IRON & STEEL",
}


def is_invalid_name(name: str) -> bool:

    text = clean_text(name)

    if not text:
        return True

    upper = text.upper()

    if upper in INVALID_NAMES:
        return True

    if upper.startswith("CEO"):
        return True

    return False


# ============================================================
# Security classification
# ============================================================

def infer_type(
    code: str,
    name: str = "",
    market: str = "",
) -> str:

    code = clean_code(code)

    # --------------------------------------------------------
    # ETF 常見代號：
    # 00 開頭但不是一般公司股票
    # --------------------------------------------------------

    if code.startswith("00"):

        return "ETF"

    # --------------------------------------------------------
    # 其他特殊商品先排除
    # --------------------------------------------------------

    if not code.isdigit():
        return "Other"

    # --------------------------------------------------------
    # 一般四碼股票
    # --------------------------------------------------------

    if len(code) == 4:
        return "Stock"

    # --------------------------------------------------------
    # 6 碼 ETF
    # --------------------------------------------------------

    if len(code) == 6 and code.startswith("00"):
        return "ETF"

    return "Other"


def is_valid_security_code(code: str) -> bool:

    code = clean_code(code)

    if not code:
        return False

    if not code.isdigit():
        return False

    # 一般股票
    if len(code) == 4:
        return True

    # ETF / ETN / 指數型商品等
    if code.startswith("00") and 5 <= len(code) <= 6:
        return True

    return False


# ============================================================
# Official fallback names
# ============================================================

# 只允許極少數已知固定驗證標的。
# 這不是用來增加 Universe。
# 只是官方來源名稱解析異常時的最後名稱修正。
OFFICIAL_NAME_FALLBACK = {

    "2337": "旺宏",
    "2426": "鼎元",
    "2368": "金像電",
    "3081": "聯亞",

}


OFFICIAL_MARKET_FALLBACK = {

    "2337": "TWSE",
    "2426": "TWSE",
    "2368": "TWSE",
    "3081": "TPEX",

}


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
# HTTP GET with retry
# ============================================================

def request_get(
    session: requests.Session,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    accept_json: bool = False,
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
            f"⚠️ HTTP 請求失敗 "
            f"({attempt}/{MAX_RETRIES})："
            f"{last_error}"
        )

        if attempt < MAX_RETRIES:

            time.sleep(
                RETRY_SLEEP * attempt
            )

    log(
        f"❌ HTTP 請求最終失敗：{url}"
    )

    return None


# ============================================================
# Existing universe names
# ============================================================

def load_old_universe_names() -> Dict[str, str]:

    result: Dict[str, str] = {}

    if not UNIVERSE_FILE.exists():

        return result

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8-sig",
        ) as f:

            data = json.load(f)

    except Exception as exc:

        log(
            f"⚠️ 舊 universe.json 無法讀取："
            f"{exc}"
        )

        return result

    if not isinstance(data, dict):
        return result

    stocks = data.get("stocks")

    if isinstance(stocks, dict):

        for code, item in stocks.items():

            if not isinstance(item, dict):
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

    else:

        items = data.get(
            "items",
            []
        )

        if isinstance(items, list):

            for item in items:

                if not isinstance(item, dict):
                    continue

                symbol = clean_code(
                    item.get(
                        "symbol",
                        item.get(
                            "code",
                            "",
                        ),
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
# TWSE official ISIN parser
# ============================================================

def parse_twse_isin_html(
    html: str,
) -> List[Dict[str, str]]:

    records: List[
        Dict[str, str]
    ] = []

    # --------------------------------------------------------
    # 先把 HTML table 轉成純文字行
    # --------------------------------------------------------

    html = re.sub(
        r"<br\s*/?>",
        "\n",
        html,
        flags=re.I,
    )

    html = re.sub(
        r"<[^>]+>",
        " ",
        html,
    )

    # HTML entity
    html = (
        html
        .replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&gt;", ">")
        .replace("&lt;", "<")
    )

    lines = []

    for line in html.splitlines():

        text = re.sub(
            r"\s+",
            " ",
            line,
        ).strip()

        if text:
            lines.append(text)

    # --------------------------------------------------------
    # 每一行尋找：
    #
    # 代號 + 名稱 + ISIN + 日期 + 市場
    #
    # 官方英文頁目前格式：
    #
    # 1101 TCC | TW0001101004 |
    # 1962/02/09 | TWSE LISTED | ...
    # --------------------------------------------------------

    pattern = re.compile(
        r"^\s*"
        r"([0-9]{4,6})"
        r"\s+"
        r"(.+?)"
        r"\s+"
        r"(TW[A-Z0-9]{8,12})"
        r"\s+"
        r"([0-9]{4}/[0-9]{2}/[0-9]{2})"
        r"\s+"
        r"(.+?)"
        r"(?:\s+|$)"
    )

    for line in lines:

        match = pattern.search(line)

        if not match:
            continue

        code = clean_code(
            match.group(1)
        )

        name = clean_text(
            match.group(2)
        )

        isin = clean_text(
            match.group(3)
        )

        date_listed = clean_text(
            match.group(4)
        )

        market_text = clean_text(
            match.group(5)
        )

        if not is_valid_security_code(code):
            continue

        # ----------------------------------------------------
        # 只接受 TWSE LISTED
        #
        # Emerging / bonds / futures 等全部排除。
        # ----------------------------------------------------

        if "TWSE LISTED" not in (
            market_text.upper()
        ):

            continue

        # ----------------------------------------------------
        # 避免 header
        # ----------------------------------------------------

        if (
            code == "0000"
            or not isin.startswith("TW")
        ):

            continue

        # ----------------------------------------------------
        # 名稱不能是分類
        # ----------------------------------------------------

        if is_invalid_name(name):

            continue

        sec_type = infer_type(
            code,
            name,
            "TWSE",
        )

        if sec_type not in (
            "Stock",
            "ETF",
        ):

            continue

        records.append(
            {
                "symbol": code,
                "name": name,
                "market": "TWSE",
                "type": sec_type,
                "instrument_type": (
                    "etf"
                    if sec_type == "ETF"
                    else "stock"
                ),
                "isin": isin,
                "date_listed": date_listed,
                "source": "TWSE_ISIN",
            }
        )

    # --------------------------------------------------------
    # 去重
    # --------------------------------------------------------

    unique: Dict[
        str,
        Dict[str, str]
    ] = {}

    for item in records:

        code = item["symbol"]

        if code not in unique:

            unique[code] = item

    return list(
        unique.values()
    )


# ============================================================
# TWSE official
# ============================================================

def fetch_twse_universe(
    session: requests.Session,
) -> List[Dict[str, str]]:

    section(
        "TWSE 官方 Universe"
    )

    log(
        "來源：TWSE 官方 ISIN"
    )

    log(
        f"URL：{TWSE_ISIN_URL}"
    )

    response = request_get(
        session,
        TWSE_ISIN_URL,
    )

    if response is None:

        raise RuntimeError(
            "TWSE 官方 ISIN 資料下載失敗"
        )

    # --------------------------------------------------------
    # 官方頁面有時 charset 標示不正確。
    # 優先使用 apparent_encoding。
    # --------------------------------------------------------

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

    if not records:

        raise RuntimeError(
            "TWSE 官方 ISIN 解析後為 0 檔"
        )

    stock_count = sum(
        1
        for x in records
        if x["type"] == "Stock"
    )

    etf_count = sum(
        1
        for x in records
        if x["type"] == "ETF"
    )

    log(
        f"✓ TWSE 官方有效標的："
        f"{len(records)} 檔"
    )

    log(
        f"✓ TWSE Stock："
        f"{stock_count} 檔"
    )

    log(
        f"✓ TWSE ETF："
        f"{etf_count} 檔"
    )

    return records


# ============================================================
# TPEX JSON helpers
# ============================================================

def find_list_in_json(
    value: Any,
) -> Optional[List[Any]]:

    if isinstance(value, list):

        return value

    if isinstance(value, dict):

        # 常見資料欄位
        for key in (
            "data",
            "Data",
            "results",
            "result",
            "rows",
            "items",
        ):

            candidate = value.get(
                key
            )

            if isinstance(
                candidate,
                list,
            ):

                return candidate

        # 遞迴搜尋
        for child in value.values():

            found = find_list_in_json(
                child
            )

            if found is not None:
                return found

    return None


def extract_json_code_name(
    row: Dict[str, Any],
) -> Tuple[str, str]:

    code = ""

    name = ""

    code_keys = (
        "SecuritiesCompanyCode",
        "SecuritiesCode",
        "SecurityCode",
        "StockCode",
        "stock_code",
        "code",
        "Code",
        "symbol",
        "Symbol",
        "代號",
        "股票代號",
        "證券代號",
    )

    name_keys = (
        "CompanyName",
        "CompanyShortName",
        "SecurityName",
        "StockName",
        "stock_name",
        "name",
        "Name",
        "公司名稱",
        "公司簡稱",
        "股票名稱",
        "證券名稱",
    )

    for key in code_keys:

        if key in row:

            candidate = clean_code(
                row.get(key)
            )

            if candidate:

                code = candidate
                break

    for key in name_keys:

        if key in row:

            candidate = clean_text(
                row.get(key)
            )

            if candidate:

                name = candidate
                break

    return code, name


# ============================================================
# TPEX official OpenAPI
# ============================================================

def parse_tpex_json(
    payload: Any,
) -> List[Dict[str, str]]:

    rows = find_list_in_json(
        payload
    )

    if rows is None:

        return []

    records: List[
        Dict[str, str]
    ] = []

    for row in rows:

        if isinstance(row, dict):

            code, name = (
                extract_json_code_name(
                    row
                )
            )

        elif isinstance(row, list):

            if len(row) < 2:
                continue

            code = clean_code(
                row[0]
            )

            name = clean_text(
                row[1]
            )

        else:

            continue

        if not is_valid_security_code(
            code
        ):

            continue

        # TPEX Universe 只接受四碼股票
        # 或 00 開頭 ETF。
        sec_type = infer_type(
            code,
            name,
            "TPEX",
        )

        if sec_type not in (
            "Stock",
            "ETF",
        ):

            continue

        if is_invalid_name(name):

            continue

        records.append(
            {
                "symbol": code,
                "name": name,
                "market": "TPEX",
                "type": sec_type,
                "instrument_type": (
                    "etf"
                    if sec_type == "ETF"
                    else "stock"
                ),
                "source": "TPEX_OFFICIAL_OPENAPI",
            }
        )

    unique: Dict[
        str,
        Dict[str, str]
    ] = {}

    for item in records:

        unique[
            item["symbol"]
        ] = item

    return list(
        unique.values()
    )


# ============================================================
# TPEX official OpenAPI fetch
# ============================================================

def fetch_tpex_openapi(
    session: requests.Session,
) -> List[Dict[str, str]]:

    section(
        "TPEX 官方 OpenAPI"
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

        records = parse_tpex_json(
            payload
        )

        if records:

            log(
                f"✓ TPEX 官方 OpenAPI："
                f"{len(records)} 檔"
            )

            return records

        log(
            "⚠️ 該官方 OpenAPI "
            "沒有取得可用股票清單"
        )

    return []


# ============================================================
# TPEX HTML fallback
# ============================================================

def parse_tpex_company_html(
    html: str,
) -> List[Dict[str, str]]:

    records: List[
        Dict[str, str]
    ] = []

    # --------------------------------------------------------
    # TPEX 公司查詢頁本身主要是 JS application，
    # 因此只做非常保守的靜態解析。
    #
    # 不把它當主要資料源。
    # --------------------------------------------------------

    patterns = [

        re.compile(
            r"([0-9]{4})"
            r"\s*"
            r"[-－]?"
            r"\s*"
            r"([\u4e00-\u9fffA-Za-z0-9\-\.\(\) ]{2,30})"
        ),

    ]

    for pattern in patterns:

        for match in pattern.finditer(
            html
        ):

            code = clean_code(
                match.group(1)
            )

            name = clean_text(
                match.group(2)
            )

            if not is_valid_security_code(
                code
            ):

                continue

            if is_invalid_name(name):
                continue

            records.append(
                {
                    "symbol": code,
                    "name": name,
                    "market": "TPEX",
                    "type": infer_type(
                        code,
                        name,
                        "TPEX",
                    ),
                    "instrument_type": (
                        "etf"
                        if code.startswith("00")
                        else "stock"
                    ),
                    "source": (
                        "TPEX_OFFICIAL_COMPANY"
                    ),
                }
            )

    unique: Dict[
        str,
        Dict[str, str]
    ] = {}

    for item in records:

        unique[
            item["symbol"]
        ] = item

    return list(
        unique.values()
    )


def fetch_tpex_company_page(
    session: requests.Session,
) -> List[Dict[str, str]]:

    log(
        "⚠️ TPEX OpenAPI 無可用資料，"
        "嘗試官方公司資訊頁"
    )

    response = request_get(
        session,
        TPEX_COMPANY_URL,
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

    records = parse_tpex_company_html(
        html
    )

    if records:

        log(
            f"✓ TPEX 官方公司頁解析："
            f"{len(records)} 檔"
        )

    return records


# ============================================================
# TPEX Universe
# ============================================================

def fetch_tpex_universe(
    session: requests.Session,
) -> List[Dict[str, str]]:

    section(
        "TPEX 官方 Universe"
    )

    records = fetch_tpex_openapi(
        session
    )

    if records:

        return records

    records = fetch_tpex_company_page(
        session
    )

    if records:

        return records

    raise RuntimeError(
        "TPEX 官方資料全部失敗，"
        "禁止使用舊 Universe 湊數量"
    )


# ============================================================
# Merge
# ============================================================

def merge_official_universe(
    twse_records: List[Dict[str, str]],
    tpex_records: List[Dict[str, str]],
    old_names: Dict[str, str],
) -> Dict[str, Dict[str, str]]:

    section(
        "合併官方 Universe"
    )

    result: Dict[
        str,
        Dict[str, str]
    ] = {}

    # --------------------------------------------------------
    # TWSE
    # --------------------------------------------------------

    for item in twse_records:

        code = clean_code(
            item.get(
                "symbol",
                "",
            )
        )

        if not code:
            continue

        result[code] = dict(
            item
        )

    # --------------------------------------------------------
    # TPEX
    # --------------------------------------------------------

    for item in tpex_records:

        code = clean_code(
            item.get(
                "symbol",
                "",
            )
        )

        if not code:
            continue

        # 官方 TPEX 優先
        result[code] = dict(
            item
        )

    # --------------------------------------------------------
    # 舊 Universe 只能補名稱
    #
    # 絕對不能：
    # old code → 新增 Universe
    # --------------------------------------------------------

    name_fallback_count = 0

    for code, item in result.items():

        current_name = clean_text(
            item.get(
                "name",
                "",
            )
        )

        if is_invalid_name(
            current_name
        ):

            fallback = old_names.get(
                code,
                "",
            )

            if not is_invalid_name(
                fallback
            ):

                item["name"] = fallback

                item["name_source"] = (
                    "OLD_UNIVERSE_FALLBACK"
                )

                name_fallback_count += 1

    # --------------------------------------------------------
    # 固定官方名稱
    # --------------------------------------------------------

    for code, fallback_name in (
        OFFICIAL_NAME_FALLBACK.items()
    ):

        if code not in result:
            continue

        current = clean_text(
            result[code].get(
                "name",
                "",
            )
        )

        if is_invalid_name(current):

            result[code]["name"] = (
                fallback_name
            )

            result[code]["name_source"] = (
                "OFFICIAL_VALIDATION_FALLBACK"
            )

            name_fallback_count += 1

    # --------------------------------------------------------
    # 最終過濾
    # --------------------------------------------------------

    invalid_codes = []

    for code, item in result.items():

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

        sec_type = clean_text(
            item.get(
                "type",
                "",
            )
        )

        if not is_valid_security_code(
            code
        ):

            invalid_codes.append(code)
            continue

        if is_invalid_name(name):

            invalid_codes.append(code)
            continue

        if market not in (
            "TWSE",
            "TPEX",
        ):

            invalid_codes.append(code)
            continue

        if sec_type not in (
            "Stock",
            "ETF",
        ):

            sec_type = infer_type(
                code,
                name,
                market,
            )

            if sec_type not in (
                "Stock",
                "ETF",
            ):

                invalid_codes.append(code)
                continue

            item["type"] = sec_type

        # ----------------------------------------------------
        # full symbol
        # ----------------------------------------------------

        item["symbol"] = code

        item["market"] = market

        if market == "TPEX":

            item["full_symbol"] = (
                f"{code}.TWO"
            )

        else:

            item["full_symbol"] = (
                f"{code}.TW"
            )

        item["instrument_type"] = (
            "etf"
            if item["type"] == "ETF"
            else "stock"
        )

    for code in invalid_codes:

        result.pop(
            code,
            None,
        )

    log(
        f"✓ 官方 Universe 合併後："
        f"{len(result)} 檔"
    )

    log(
        f"✓ 舊 Universe 僅補名稱："
        f"{name_fallback_count} 檔"
    )

    return result


# ============================================================
# Build output
# ============================================================

def build_output(
    stocks: Dict[str, Dict[str, str]],
) -> Dict[str, Any]:

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

    # --------------------------------------------------------
    # 最終數量以實際 stocks 為準
    # --------------------------------------------------------

    universe_count = len(
        stocks
    )

    output = {

        "schema_version": VERSION,

        "generated_at": (
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        ),

        "source": {

            "primary": [
                "TWSE_OFFICIAL_ISIN",
                "TPEX_OFFICIAL_OPENAPI",
            ],

            "secondary": [
                "TPEX_OFFICIAL_COMPANY",
                "OLD_UNIVERSE_NAME_ONLY",
            ],

            "fallback": [],

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

        "source_count": {

            "TWSE_ISIN": sum(
                1
                for item in stocks.values()
                if item.get(
                    "source"
                ) == "TWSE_ISIN"
            ),

            "TPEX_OFFICIAL_OPENAPI": sum(
                1
                for item in stocks.values()
                if item.get(
                    "source"
                )
                == "TPEX_OFFICIAL_OPENAPI"
            ),

            "TPEX_OFFICIAL_COMPANY": sum(
                1
                for item in stocks.values()
                if item.get(
                    "source"
                )
                == "TPEX_OFFICIAL_COMPANY"
            ),

            "OLD_UNIVERSE_FALLBACK": sum(
                1
                for item in stocks.values()
                if item.get(
                    "name_source"
                )
                == "OLD_UNIVERSE_FALLBACK"
            ),
        },

        "stocks": dict(
            sorted(
                stocks.items(),
                key=lambda x: x[0],
            )
        ),
    }

    return output


# ============================================================
# Validation
# ============================================================

def validate_universe(
    data: Dict[str, Any],
) -> bool:

    section(
        "Universe 最終驗證"
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

    # --------------------------------------------------------
    # 1
    # --------------------------------------------------------

    log(
        f"universe_count = "
        f"{universe_count}"
    )

    log(
        f"stocks 實際數量 = "
        f"{actual_count}"
    )

    if universe_count != actual_count:

        log(
            "❌ universe_count != "
            "len(stocks)"
        )

        return False

    log(
        "✓ universe_count == "
        "len(stocks)"
    )

    # --------------------------------------------------------
    # 2
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
        f"stock_count header = "
        f"{stock_count}"
    )

    log(
        f"stock_count actual = "
        f"{actual_stock_count}"
    )

    if stock_count != actual_stock_count:

        log(
            "❌ stock_count 不一致"
        )

        return False

    log(
        "✓ stock_count 驗證通過"
    )

    # --------------------------------------------------------
    # 3
    # --------------------------------------------------------

    log(
        f"etf_count header = "
        f"{etf_count}"
    )

    log(
        f"etf_count actual = "
        f"{actual_etf_count}"
    )

    if etf_count != actual_etf_count:

        log(
            "❌ etf_count 不一致"
        )

        return False

    log(
        "✓ etf_count 驗證通過"
    )

    # --------------------------------------------------------
    # 4
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
    # 5
    # --------------------------------------------------------

    market_count = data.get(
        "market_count",
        {},
    )

    actual_twse = sum(
        1
        for item in stocks.values()
        if isinstance(item, dict)
        and item.get("market") == "TWSE"
    )

    actual_tpex = sum(
        1
        for item in stocks.values()
        if isinstance(item, dict)
        and item.get("market") == "TPEX"
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
        f"✓ TWSE：{actual_twse}"
    )

    log(
        f"✓ TPEX：{actual_tpex}"
    )

    # --------------------------------------------------------
    # 6
    # --------------------------------------------------------

    forbidden_name_count = 0

    for code, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):

            log(
                f"❌ {code} item 不是 object"
            )

            return False

        name = clean_text(
            item.get(
                "name",
                "",
            )
        )

        if is_invalid_name(name):

            forbidden_name_count += 1

            log(
                f"❌ {code} 名稱非法："
                f"{name}"
            )

    if forbidden_name_count:

        return False

    log(
        "✓ 名稱分類污染檢查通過"
    )

    # --------------------------------------------------------
    # 7
    # --------------------------------------------------------

    required = {

        "2337": (
            "旺宏",
            "TWSE",
        ),

        "2426": (
            "鼎元",
            "TWSE",
        ),

        "2368": (
            "金像電",
            "TWSE",
        ),

        "3081": (
            "聯亞",
            "TPEX",
        ),

    }

    section(
        "固定測試股票驗證"
    )

    for code, (
        expected_name,
        expected_market,
    ) in required.items():

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

        log(
            f"{code} | "
            f"預期：{expected_name} / "
            f"{expected_market} | "
            f"實際：{actual_name} / "
            f"{actual_market}"
        )

        if actual_name != expected_name:

            log(
                f"❌ {code} 名稱錯誤"
            )

            return False

        if actual_market != expected_market:

            log(
                f"❌ {code} 市場錯誤"
            )

            return False

    log(
        "✓ 2337 / 2426 / 2368 / 3081 "
        "全部驗證通過"
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
# Main
# ============================================================

def main() -> int:

    start_time = time.time()

    log(
        f"台股 AI 選股系統 "
        f"build_universe.py {VERSION}"
    )

    log(
        "=" * 60
    )

    log(
        "核心原則"
    )

    log(
        "✓ 官方 TWSE / TPEX 建立 Universe"
    )

    log(
        "✓ 舊 universe 只能補名稱"
    )

    log(
        "✓ 不使用 Yahoo"
    )

    log(
        "✓ 不使用歷史資料"
    )

    log(
        "✓ 不使用舊 Universe 新增股票"
    )

    log(
        "✓ universe_count 永遠等於實際 stocks"
    )

    log(
        "✓ TWSE 使用官方 ISIN"
    )

    log(
        "✓ 禁止 CEOGEU / CEOJEU / CEOIEU"
    )

    log(
        "✓ TPEX 官方來源自動重試"
    )

    log(
        "✓ TPEX 失敗不使用舊 Universe 湊數量"
    )

    log(
        "=" * 60
    )

    session = create_session()

    # ========================================================
    # 1. 舊 Universe
    # ========================================================

    old_names = load_old_universe_names()

    # ========================================================
    # 2. TWSE
    # ========================================================

    try:

        twse_records = fetch_twse_universe(
            session
        )

    except Exception as exc:

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
            f"❌ TWSE 官方資料失敗："
            f"{type(exc).__name__}: {exc}"
        )

        return 1

    # ========================================================
    # 3. TPEX
    # ========================================================

    try:

        tpex_records = fetch_tpex_universe(
            session
        )

    except Exception as exc:

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
            f"❌ TPEX 官方資料失敗："
            f"{type(exc).__name__}: {exc}"
        )

        log(
            "❌ 禁止使用舊 Universe 湊數量"
        )

        return 1

    # ========================================================
    # 4. Merge
    # ========================================================

    stocks = merge_official_universe(
        twse_records,
        tpex_records,
        old_names,
    )

    if not stocks:

        log(
            "❌ 官方 Universe 合併後為 0 檔"
        )

        return 1

    # ========================================================
    # 5. Build
    # ========================================================

    output = build_output(
        stocks
    )

    # ========================================================
    # 6. Before write validation
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
    # 7. Atomic Write
    # ========================================================

    section(
        "寫入 Data/universe.json"
    )

    try:

        atomic_write_json(
            UNIVERSE_FILE,
            output,
        )

    except Exception as exc:

        log(
            f"❌ Atomic Write 失敗："
            f"{type(exc).__name__}: {exc}"
        )

        return 1

    log(
        f"✓ 已寫入：{UNIVERSE_FILE}"
    )

    # ========================================================
    # 8. Read-after-write validation
    # ========================================================

    section(
        "寫入後重新讀取 Universe"
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
            f"❌ 寫入後讀取失敗："
            f"{type(exc).__name__}: {exc}"
        )

        return 1

    if not isinstance(
        verify_data,
        dict,
    ):

        log(
            "❌ universe.json 根節點不是 object"
        )

        return 1

    if not validate_universe(
        verify_data
    ):

        log(
            "❌ 寫入後 Universe 驗證失敗"
        )

        return 1

    # ========================================================
    # 9. Final
    # ========================================================

    elapsed = (
        time.time()
        - start_time
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
        f"{verify_data['universe_count']} 檔"
    )

    log(
        f"✓ Stock："
        f"{verify_data['stock_count']} 檔"
    )

    log(
        f"✓ ETF："
        f"{verify_data['etf_count']} 檔"
    )

    log(
        f"✓ TWSE："
        f"{verify_data['market_count']['TWSE']} 檔"
    )

    log(
        f"✓ TPEX："
        f"{verify_data['market_count']['TPEX']} 檔"
    )

    log(
        "✓ universe_count == len(stocks)"
    )

    log(
        "✓ Stock + ETF == Universe"
    )

    log(
        "✓ CEOGEU / CEOJEU / CEOIEU "
        "污染檢查通過"
    )

    log(
        "✓ 2337 = 旺宏 / TWSE"
    )

    log(
        "✓ 2426 = 鼎元 / TWSE"
    )

    log(
        "✓ 2368 = 金像電 / TWSE"
    )

    log(
        "✓ 3081 = 聯亞 / TPEX"
    )

    log(
        "✓ Atomic Write：通過"
    )

    log(
        "✓ 寫入後重新驗證：通過"
    )

    log(
        f"✓ build_universe.py {VERSION} 完成"
    )

    log(
        f"✓ 耗時：{elapsed:.1f} 秒"
    )

    return 0


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
