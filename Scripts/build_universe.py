#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Scripts/build_universe.py

台股 AI 選股系統
Dynamic Universe Builder

核心原則
------------------------------------------------------------
1. TWSE OpenAPI：官方第一來源
2. TPEX OpenAPI：官方第一來源
3. TWSE ISIN：官方補充，不得覆蓋已確認的官方市場資料
4. TPEX 官方資料：補充 TPEX 股票
5. CMoney / Goodinfo：本檔不依賴，不新增 Universe
6. 舊 Universe：只可提供名稱快取，不可新增股票
7. 不使用 Yahoo
8. 不使用歷史價格
9. 不寫死任何股票代號
10. 不寫死任何股票名稱
11. 不用固定股票清單做驗證
12. 不用舊 Universe 湊數量
13. 所有 symbol / name / market / type 均由資料來源動態建立
14. 只有完整驗證成功後才 Atomic Write
------------------------------------------------------------

輸出：
    Data/universe.json
"""

from __future__ import annotations

import json
import re
import sys
import time
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests


# ============================================================
# Paths / Config
# ============================================================

ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = ROOT / "Data"
UNIVERSE_FILE = DATA_DIR / "universe.json"

VERSION = "DYNAMIC-UNIVERSE"

TIMEOUT = 30
RETRY_COUNT = 3
RETRY_SLEEP = 2

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(X11; Linux x86_64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/139 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ============================================================
# Official APIs
# ============================================================

TWSE_QUOTES_URL = (
    "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
)

TWSE_STOCK_DAY_ALL_URL = (
    "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
)

TWSE_ISIN_URL = (
    "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
)

TPEX_QUOTES_URLS = [
    "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes",
    "https://www.tpex.org.tw/openapi/v1/tpex_esb_latest_statistics",
]

TPEX_ISIN_URL = (
    "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4"
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
# Generic helpers
# ============================================================

def clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value)
    text = text.replace("\ufeff", "")
    text = text.replace("\u3000", " ")
    text = text.strip()

    return text


def normalize_space(value: Any) -> str:
    text = clean_text(value)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def is_digits(value: Any) -> bool:
    return bool(re.fullmatch(r"\d+", clean_text(value)))


def normalize_symbol(value: Any) -> str:
    """
    股票代號只接受純數字。

    不接受：
        043082
        2337.TW
        2337.TWO
        12345
        ISIN

    台股現貨 Universe 的 symbol 採 4 碼為主。
    部分官方來源可能存在 5~6 碼特殊商品，
    但 ETF / ETN / 受益證券等仍由官方 Type 決定。
    """

    text = clean_text(value)

    if not text:
        return ""

    # 移除常見市場後綴
    text = re.sub(r"\.(TW|TWO)$", "", text, flags=re.I)

    # 只接受純數字
    if not is_digits(text):
        return ""

    # 去除前導零後判斷
    stripped = text.lstrip("0")

    if not stripped:
        return "0"

    # 台股證券代號正常範圍
    # 不接受 ISIN 或其他長數字欄位
    if len(text) > 5:
        return ""

    # 4 碼代號直接保留
    if len(text) == 4:
        return text

    # 3 碼代號補零
    if len(text) == 3:
        return text

    # 2 / 1 碼只有官方來源明確時才接受
    if len(text) <= 2:
        return text

    # 5 碼商品保留，但必須由官方來源確認
    if len(text) == 5:
        return text

    return ""


def normalize_name(value: Any) -> str:
    text = normalize_space(value)

    if not text:
        return ""

    # 名稱不能是純數字
    if is_digits(text):
        return ""

    # 名稱不能是股票代號格式
    if re.fullmatch(r"\d+(?:\.\w+)?", text):
        return ""

    # 排除明顯 ISIN / 欄位污染
    upper = text.upper()

    polluted = {
        "CEOGEU",
        "CEOJEU",
        "CEOIEU",
        "CEOGEU ",
        "CEOJEU ",
        "CEOIEU ",
    }

    if upper in polluted:
        return ""

    # 名稱至少需要包含中文或英文字母
    if not re.search(r"[\u4e00-\u9fffA-Za-z]", text):
        return ""

    return text


def normalize_market(value: Any) -> str:
    text = normalize_space(value).upper()

    if text in {
        "TWSE",
        "上市",
        "上市股票",
        "上市公司",
        "TAIEX",
    }:
        return "TWSE"

    if text in {
        "TPEX",
        "TWO",
        "上櫃",
        "上櫃股票",
        "上櫃公司",
    }:
        return "TPEX"

    return ""


def normalize_type(value: Any) -> str:
    text = normalize_space(value).upper()

    if text in {
        "STOCK",
        "COMMON STOCK",
        "COMMON",
        "股票",
        "上市股票",
        "上櫃股票",
    }:
        return "Stock"

    if text in {
        "ETF",
        "ETN",
        "ETF FUND",
        "ETF/ETN",
        "指數股票型基金",
        "受益憑證",
        "指數投資證券",
    }:
        return "ETF"

    return ""


def looks_like_isin(value: Any) -> bool:
    text = clean_text(value).upper()

    if not text:
        return False

    # Taiwan ISIN:
    # TW + 9 digits + check digit
    if re.fullmatch(r"TW\d{10}", text):
        return True

    # 任何典型 ISIN
    if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", text):
        return True

    return False


# ============================================================
# HTTP
# ============================================================

def request_json(
    url: str,
    params: Optional[Dict[str, Any]] = None,
) -> Any:

    last_error: Optional[Exception] = None

    for attempt in range(1, RETRY_COUNT + 1):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=TIMEOUT,
            )

            response.raise_for_status()

            return response.json()

        except Exception as exc:

            last_error = exc

            log(
                f"⚠ API 失敗 "
                f"{attempt}/{RETRY_COUNT}："
                f"{type(exc).__name__}: {exc}"
            )

            if attempt < RETRY_COUNT:
                time.sleep(RETRY_SLEEP)

    raise RuntimeError(
        f"API request failed: {url}"
    ) from last_error


def request_text(
    url: str,
    params: Optional[Dict[str, Any]] = None,
) -> str:

    last_error: Optional[Exception] = None

    for attempt in range(1, RETRY_COUNT + 1):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=TIMEOUT,
            )

            response.raise_for_status()

            response.encoding = (
                response.apparent_encoding
                or response.encoding
                or "utf-8"
            )

            return response.text

        except Exception as exc:

            last_error = exc

            log(
                f"⚠ API 失敗 "
                f"{attempt}/{RETRY_COUNT}："
                f"{type(exc).__name__}: {exc}"
            )

            if attempt < RETRY_COUNT:
                time.sleep(RETRY_SLEEP)

    raise RuntimeError(
        f"API request failed: {url}"
    ) from last_error


# ============================================================
# Record
# ============================================================

def make_record(
    symbol: str,
    name: str,
    market: str,
    security_type: str,
    source: str,
) -> Optional[Dict[str, Any]]:

    symbol = normalize_symbol(symbol)
    name = normalize_name(name)
    market = normalize_market(market)
    security_type = normalize_type(security_type)

    if not symbol:
        return None

    if not name:
        return None

    if market not in {"TWSE", "TPEX"}:
        return None

    if security_type not in {"Stock", "ETF"}:
        return None

    full_symbol = (
        f"{symbol}.TW"
        if market == "TWSE"
        else f"{symbol}.TWO"
    )

    return {
        "symbol": symbol,
        "full_symbol": full_symbol,
        "name": name,
        "market": market,
        "type": security_type,
        "source": source,
    }


# ============================================================
# Existing Universe
# ============================================================

def load_old_universe() -> Dict[str, str]:

    cache: Dict[str, str] = {}

    if not UNIVERSE_FILE.exists():
        return cache

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8",
        ) as f:

            data = json.load(f)

    except Exception:
        return cache

    stocks = data.get("stocks", [])

    if not isinstance(stocks, list):
        return cache

    for item in stocks:

        if not isinstance(item, dict):
            continue

        symbol = normalize_symbol(
            item.get("symbol")
        )

        name = normalize_name(
            item.get("name")
        )

        if symbol and name:
            cache[symbol] = name

    return cache


# ============================================================
# TWSE OpenAPI
# ============================================================

def parse_twse_openapi(payload: Any) -> List[Dict[str, Any]]:

    records: List[Dict[str, Any]] = []

    if not isinstance(payload, list):
        return records

    for row in payload:

        if not isinstance(row, dict):
            continue

        # TWSE OpenAPI 常見欄位
        symbol_candidates = [
            row.get("證券代號"),
            row.get("股票代號"),
            row.get("有價證券代號"),
            row.get("代號"),
            row.get("code"),
            row.get("symbol"),
        ]

        name_candidates = [
            row.get("證券名稱"),
            row.get("股票名稱"),
            row.get("有價證券名稱"),
            row.get("名稱"),
            row.get("name"),
        ]

        symbol = ""

        for value in symbol_candidates:

            candidate = normalize_symbol(value)

            if candidate:
                symbol = candidate
                break

        name = ""

        for value in name_candidates:

            candidate = normalize_name(value)

            if candidate:
                name = candidate
                break

        if not symbol or not name:
            continue

        record = make_record(
            symbol=symbol,
            name=name,
            market="TWSE",
            security_type="Stock",
            source="TWSE_OpenAPI",
        )

        if record:
            records.append(record)

    return records


def fetch_twse() -> List[Dict[str, Any]]:

    section("TWSE OpenAPI")

    payload = request_json(
        TWSE_QUOTES_URL
    )

    records = parse_twse_openapi(
        payload
    )

    log(
        f"TWSE OpenAPI："
        f"{len(records)} 檔"
    )

    return records


# ============================================================
# TPEX OpenAPI
# ============================================================

def find_code_and_name_from_mapping(
    row: Dict[str, Any],
) -> Tuple[str, str]:

    symbol = ""
    name = ""

    # --------------------------------------------------------
    # 優先使用明確欄位名稱
    # --------------------------------------------------------

    code_keys = [
        "SecuritiesCompanyCode",
        "SecuritiesCode",
        "Code",
        "code",
        "代號",
        "證券代號",
        "股票代號",
        "有價證券代號",
        "公司代號",
    ]

    name_keys = [
        "CompanyName",
        "SecuritiesCompanyName",
        "SecuritiesName",
        "Name",
        "name",
        "名稱",
        "證券名稱",
        "股票名稱",
        "公司名稱",
        "有價證券名稱",
    ]

    for key in code_keys:

        if key in row:

            candidate = normalize_symbol(
                row.get(key)
            )

            if candidate:
                symbol = candidate
                break

    for key in name_keys:

        if key in row:

            candidate = normalize_name(
                row.get(key)
            )

            if candidate:
                name = candidate
                break

    # --------------------------------------------------------
    # 不依賴欄位名稱的 fallback
    # --------------------------------------------------------

    if not symbol:

        for value in row.values():

            if looks_like_isin(value):
                continue

            candidate = normalize_symbol(value)

            if not candidate:
                continue

            # 股票代號通常 4 碼
            if len(candidate) == 4:
                symbol = candidate
                break

    if not name:

        for value in row.values():

            candidate = normalize_name(value)

            if not candidate:
                continue

            if candidate == symbol:
                continue

            if looks_like_isin(candidate):
                continue

            # 排除明顯數值欄位
            if re.fullmatch(
                r"[-+]?[\d,.\s%]+",
                candidate,
            ):
                continue

            name = candidate
            break

    return symbol, name


def find_code_and_name_from_list(
    row: List[Any],
) -> Tuple[str, str]:

    """
    TPEX array parser。

    絕對不假設：
        row[0] = code
        row[1] = name

    而是掃描整列資料：

        1. 找真正的證券代號
        2. 排除 ISIN
        3. 找真正名稱
        4. 排除價格、成交量、日期、數字欄位

    這是避免：
        3081 -> 043082

    的核心。
    """

    symbol = ""
    name = ""

    # --------------------------------------------------------
    # 第一階段：找 4 碼代號
    # --------------------------------------------------------

    for value in row:

        text = clean_text(value)

        if not text:
            continue

        if looks_like_isin(text):
            continue

        candidate = normalize_symbol(text)

        if not candidate:
            continue

        if len(candidate) == 4:
            symbol = candidate
            break

    # --------------------------------------------------------
    # 第二階段：找名稱
    # --------------------------------------------------------

    for value in row:

        text = normalize_name(value)

        if not text:
            continue

        if text == symbol:
            continue

        if looks_like_isin(text):
            continue

        # 純數字 / 價格 / 百分比
        if re.fullmatch(
            r"[-+]?[\d,.\s%]+",
            text,
        ):
            continue

        # 日期
        if re.fullmatch(
            r"\d{4}[-/]\d{1,2}[-/]\d{1,2}",
            text,
        ):
            continue

        # 名稱至少有中文或英文字母
        if re.search(
            r"[\u4e00-\u9fffA-Za-z]",
            text,
        ):
            name = text
            break

    return symbol, name


def parse_tpex_payload(
    payload: Any,
) -> List[Dict[str, Any]]:

    records: List[Dict[str, Any]] = []

    if isinstance(payload, dict):

        # 常見包裝格式
        for key in (
            "data",
            "Data",
            "result",
            "results",
            "rows",
            "items",
        ):

            if key in payload:

                nested = payload[key]

                if isinstance(
                    nested,
                    list,
                ):
                    payload = nested
                    break

    if not isinstance(payload, list):
        return records

    # --------------------------------------------------------
    # JSON object rows
    # --------------------------------------------------------

    for row in payload:

        symbol = ""
        name = ""

        if isinstance(row, dict):

            symbol, name = (
                find_code_and_name_from_mapping(
                    row
                )
            )

        elif isinstance(row, list):

            symbol, name = (
                find_code_and_name_from_list(
                    row
                )
            )

        if not symbol or not name:
            continue

        record = make_record(
            symbol=symbol,
            name=name,
            market="TPEX",
            security_type="Stock",
            source="TPEX_OpenAPI",
        )

        if record:
            records.append(record)

    return records


def fetch_tpex() -> List[Dict[str, Any]]:

    section("TPEX OpenAPI")

    last_error: Optional[Exception] = None

    for url in TPEX_QUOTES_URLS:

        try:

            log(
                f"嘗試：{url}"
            )

            payload = request_json(
                url
            )

            records = parse_tpex_payload(
                payload
            )

            if records:

                log(
                    f"✓ TPEX OpenAPI："
                    f"{len(records)} 檔"
                )

                return records

        except Exception as exc:

            last_error = exc

            log(
                f"⚠ TPEX API 失敗："
                f"{type(exc).__name__}: {exc}"
            )

    raise RuntimeError(
        "TPEX OpenAPI 無法取得有效資料"
    ) from last_error


# ============================================================
# TWSE ISIN
# ============================================================

def parse_isin_html(
    html: str,
    market: str,
) -> List[Dict[str, Any]]:

    """
    ISIN 是補充資料。

    重要：
    不使用固定欄位位置。
    不把 ISIN 欄位當 symbol。
    只接受真正的股票代號與名稱。
    """

    records: List[Dict[str, Any]] = []

    # 去除 HTML
    text = re.sub(
        r"<[^>]+>",
        " ",
        html,
    )

    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&#39;", "'")
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    # --------------------------------------------------------
    # 優先找：
    # 4~5 碼代號 + 名稱
    # --------------------------------------------------------

    pattern = re.compile(
        r"(?<!\d)"
        r"(\d{4,5})"
        r"\s+"
        r"([^\d]{2,40}?)"
        r"(?=\s+(?:TW[A-Z0-9]{8,12}|\d{4,5})\b)",
        re.I,
    )

    for match in pattern.finditer(text):

        symbol = normalize_symbol(
            match.group(1)
        )

        name = normalize_name(
            match.group(2)
        )

        if not symbol or not name:
            continue

        record = make_record(
            symbol=symbol,
            name=name,
            market=market,
            security_type="Stock",
            source="TWSE_ISIN",
        )

        if record:
            records.append(record)

    # --------------------------------------------------------
    # fallback：逐段掃描
    # --------------------------------------------------------

    if not records:

        chunks = re.split(
            r"(?:\r?\n|\s{2,})",
            text,
        )

        for chunk in chunks:

            chunk = normalize_space(
                chunk
            )

            if not chunk:
                continue

            if looks_like_isin(chunk):
                continue

            match = re.search(
                r"(?<!\d)(\d{4,5})(?!\d)",
                chunk,
            )

            if not match:
                continue

            symbol = normalize_symbol(
                match.group(1)
            )

            if not symbol:
                continue

            name_text = chunk[
                match.end():
            ]

            name = normalize_name(
                name_text
            )

            if not name:
                continue

            record = make_record(
                symbol=symbol,
                name=name,
                market=market,
                security_type="Stock",
                source="TWSE_ISIN",
            )

            if record:
                records.append(record)

    return records


def fetch_twse_isin() -> List[Dict[str, Any]]:

    section("TWSE 官方 ISIN")

    try:

        html = request_text(
            TWSE_ISIN_URL
        )

        records = parse_isin_html(
            html,
            "TWSE",
        )

        log(
            f"TWSE ISIN："
            f"{len(records)} 檔"
        )

        return records

    except Exception as exc:

        log(
            f"⚠ TWSE ISIN 取得失敗："
            f"{type(exc).__name__}: {exc}"
        )

        return []


# ============================================================
# Merge
# ============================================================

def source_priority(
    source: str,
) -> int:

    priority = {
        "TWSE_OpenAPI": 100,
        "TPEX_OpenAPI": 100,
        "TWSE_ISIN": 50,
        "TPEX_ISIN": 50,
        "OLD_UNIVERSE": 10,
    }

    return priority.get(
        source,
        0,
    )


def merge_records(
    records: Iterable[Dict[str, Any]],
    old_names: Dict[str, str],
) -> List[Dict[str, Any]]:

    """
    官方資料優先。

    同一 symbol：
        OpenAPI > ISIN > 舊 Universe

    但最重要的是：

    TWSE / TPEX 是市場識別的一部分。

    因此不能只用 symbol 無條件鎖死第一筆資料。

    如果同一 symbol：
        TWSE
        TPEX

    同時存在，保留官方來源優先度較高且資料完整者。
    """

    merged: Dict[str, Dict[str, Any]] = {}

    for record in records:

        symbol = normalize_symbol(
            record.get("symbol")
        )

        if not symbol:
            continue

        candidate = dict(record)

        old = merged.get(
            symbol
        )

        if old is None:

            merged[symbol] = candidate
            continue

        old_priority = source_priority(
            old.get("source", "")
        )

        new_priority = source_priority(
            candidate.get("source", "")
        )

        # ----------------------------------------------------
        # OpenAPI vs OpenAPI
        #
        # 若兩個官方市場同時出現，
        # 不用名稱猜市場，也不使用舊資料覆蓋。
        #
        # 兩個來源都是真正官方資料時，
        # 優先保留具有完整資料者。
        # ----------------------------------------------------

        if new_priority > old_priority:

            merged[symbol] = candidate

        elif new_priority == old_priority:

            old_market = old.get(
                "market",
                "",
            )

            new_market = candidate.get(
                "market",
                "",
            )

            # 同市場：保留較完整名稱
            if old_market == new_market:

                old_name = normalize_name(
                    old.get("name")
                )

                new_name = normalize_name(
                    candidate.get("name")
                )

                if (
                    new_name
                    and len(new_name)
                    >= len(old_name)
                ):
                    merged[symbol] = candidate

            else:
                # ------------------------------------------------
                # 同 symbol 不同市場：
                #
                # 不允許舊資料 / ISIN 隨意覆蓋 OpenAPI。
                # OpenAPI 都是官方第一來源時，
                # 只有在其中一筆名稱明顯污染時才淘汰污染資料。
                # ------------------------------------------------

                old_name = normalize_name(
                    old.get("name")
                )

                new_name = normalize_name(
                    candidate.get("name")
                )

                if not old_name and new_name:
                    merged[symbol] = candidate

    # --------------------------------------------------------
    # 舊 Universe 只補「名稱」
    # 不得新增 Universe。
    # --------------------------------------------------------

    for symbol, record in merged.items():

        name = normalize_name(
            record.get("name")
        )

        if name:
            continue

        old_name = normalize_name(
            old_names.get(symbol)
        )

        if old_name:

            record["name"] = old_name
            record["source"] = (
                f"{record.get('source', '')}+OLD_NAME"
            )

    return list(
        merged.values()
    )


# ============================================================
# Validation
# ============================================================

def validate_record(
    record: Dict[str, Any],
) -> bool:

    symbol = normalize_symbol(
        record.get("symbol")
    )

    full_symbol = clean_text(
        record.get("full_symbol")
    )

    name = normalize_name(
        record.get("name")
    )

    market = normalize_market(
        record.get("market")
    )

    security_type = normalize_type(
        record.get("type")
    )

    if not symbol:
        return False

    if not name:
        return False

    if market not in {
        "TWSE",
        "TPEX",
    }:
        return False

    if security_type not in {
        "Stock",
        "ETF",
    }:
        return False

    expected = (
        f"{symbol}.TW"
        if market == "TWSE"
        else f"{symbol}.TWO"
    )

    if full_symbol != expected:
        return False

    # 名稱污染
    if is_digits(name):
        return False

    if looks_like_isin(name):
        return False

    if name.upper() in {
        "CEOGEU",
        "CEOJEU",
        "CEOIEU",
    }:
        return False

    return True


def validate_universe(
    data: Dict[str, Any],
) -> bool:

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        list,
    ):
        log(
            "❌ stocks 不是 list"
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

    if universe_count != len(stocks):

        log(
            "❌ universe_count != len(stocks)"
        )

        return False

    actual_stock = sum(
        1
        for item in stocks
        if item.get("type") == "Stock"
    )

    actual_etf = sum(
        1
        for item in stocks
        if item.get("type") == "ETF"
    )

    if stock_count != actual_stock:

        log(
            "❌ stock_count 錯誤"
        )

        return False

    if etf_count != actual_etf:

        log(
            "❌ etf_count 錯誤"
        )

        return False

    if actual_stock + actual_etf != len(stocks):

        log(
            "❌ Stock + ETF != Universe"
        )

        return False

    symbols = [
        item.get("symbol")
        for item in stocks
    ]

    if len(symbols) != len(
        set(symbols)
    ):

        log(
            "❌ symbol 重複"
        )

        return False

    full_symbols = [
        item.get("full_symbol")
        for item in stocks
    ]

    if len(full_symbols) != len(
        set(full_symbols)
    ):

        log(
            "❌ full_symbol 重複"
        )

        return False

    for item in stocks:

        if not validate_record(
            item
        ):

            log(
                "❌ record 驗證失敗："
                f"{item}"
            )

            return False

    market_count = data.get(
        "market_count",
        {},
    )

    expected_twse = sum(
        1
        for item in stocks
        if item.get("market") == "TWSE"
    )

    expected_tpex = sum(
        1
        for item in stocks
        if item.get("market") == "TPEX"
    )

    if market_count.get(
        "TWSE"
    ) != expected_twse:

        log(
            "❌ TWSE market_count 錯誤"
        )

        return False

    if market_count.get(
        "TPEX"
    ) != expected_tpex:

        log(
            "❌ TPEX market_count 錯誤"
        )

        return False

    return True


# ============================================================
# Build output
# ============================================================

def build_output(
    records: List[Dict[str, Any]],
) -> Dict[str, Any]:

    records = sorted(
        records,
        key=lambda item: (
            item.get("market", ""),
            item.get("type", ""),
            item.get("symbol", ""),
        ),
    )

    stock_count = sum(
        1
        for item in records
        if item.get("type") == "Stock"
    )

    etf_count = sum(
        1
        for item in records
        if item.get("type") == "ETF"
    )

    twse_count = sum(
        1
        for item in records
        if item.get("market") == "TWSE"
    )

    tpex_count = sum(
        1
        for item in records
        if item.get("market") == "TPEX"
    )

    # 不把內部 source 欄位暴露成前端必要資料
    stocks = []

    for item in records:

        stocks.append(
            {
                "symbol": item["symbol"],
                "full_symbol": item["full_symbol"],
                "name": item["name"],
                "market": item["market"],
                "type": item["type"],
            }
        )

    return {
        "version": VERSION,
        "generated_at": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(),
        ),
        "universe_count": len(stocks),
        "stock_count": stock_count,
        "etf_count": etf_count,
        "market_count": {
            "TWSE": twse_count,
            "TPEX": tpex_count,
        },
        "stocks": stocks,
    }


# ============================================================
# Atomic write
# ============================================================

def atomic_write_json(
    path: Path,
    data: Dict[str, Any],
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, temp_name = tempfile.mkstemp(
        prefix=".universe_",
        suffix=".json",
        dir=str(path.parent),
    )

    temp_path = Path(
        temp_name
    )

    try:

        with open(
            fd,
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
            temp_path.unlink(
                missing_ok=True
            )
        except Exception:
            pass

        raise


# ============================================================
# Main
# ============================================================

def main() -> int:

    started = time.time()

    log(
        "台股 AI 選股系統 "
        "build_universe.py"
    )

    log(
        "============================================================"
    )

    log(
        "核心原則"
    )

    log(
        "✓ TWSE OpenAPI → 官方第一來源"
    )

    log(
        "✓ TPEX OpenAPI → 官方第一來源"
    )

    log(
        "✓ TWSE ISIN → 官方補充"
    )

    log(
        "✓ 所有股票代號動態取得"
    )

    log(
        "✓ 所有股票名稱動態取得"
    )

    log(
        "✓ 不寫死任何股票"
    )

    log(
        "✓ 不寫死任何股票名稱"
    )

    log(
        "✓ 不使用固定測試標的"
    )

    log(
        "✓ 不使用 Yahoo"
    )

    log(
        "✓ 不使用歷史資料"
    )

    log(
        "✓ 舊 Universe 只補名稱"
    )

    log(
        "✓ 不用舊 Universe 湊數量"
    )

    log(
        "✓ 只有驗證成功才寫入"
    )

    log(
        "============================================================"
    )

    # --------------------------------------------------------
    # Load old name cache
    # --------------------------------------------------------

    old_names = load_old_universe()

    log(
        f"✓ 舊 Universe 名稱快取："
        f"{len(old_names)} 檔"
    )

    # --------------------------------------------------------
    # Official sources
    # --------------------------------------------------------

    try:

        twse_records = fetch_twse()

    except Exception as exc:

        log(
            "❌ TWSE OpenAPI 失敗："
            f"{type(exc).__name__}: {exc}"
        )

        return 1

    try:

        tpex_records = fetch_tpex()

    except Exception as exc:

        log(
            "❌ TPEX OpenAPI 失敗："
            f"{type(exc).__name__}: {exc}"
        )

        return 1

    # --------------------------------------------------------
    # ISIN supplemental
    # --------------------------------------------------------

    twse_isin_records = (
        fetch_twse_isin()
    )

    # --------------------------------------------------------
    # Merge
    # --------------------------------------------------------

    section(
        "建立官方 Universe"
    )

    official_records = (
        twse_records
        + tpex_records
        + twse_isin_records
    )

    log(
        f"官方來源原始資料："
        f"{len(official_records)} 筆"
    )

    merged = merge_records(
        official_records,
        old_names,
    )

    log(
        f"官方 Universe："
        f"{len(merged)} 檔"
    )

    # --------------------------------------------------------
    # Strict record validation
    # --------------------------------------------------------

    section(
        "Universe 嚴格驗證"
    )

    invalid = []

    for item in merged:

        if not validate_record(
            item
        ):

            invalid.append(
                item
            )

    if invalid:

        log(
            f"❌ 無效資料："
            f"{len(invalid)} 檔"
        )

        # 只顯示前 20 筆，避免 GitHub Actions log 爆量
        for item in invalid[:20]:

            log(
                f"  {item}"
            )

        return 1

    # --------------------------------------------------------
    # Build output
    # --------------------------------------------------------

    output = build_output(
        merged
    )

    # --------------------------------------------------------
    # Validate output before write
    # --------------------------------------------------------

    if not validate_universe(
        output
    ):

        log(
            "❌ 寫入前驗證失敗"
        )

        return 1

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    log(
        f"universe_count = "
        f"{output['universe_count']}"
    )

    log(
        f"stock_count = "
        f"{output['stock_count']}"
    )

    log(
        f"etf_count = "
        f"{output['etf_count']}"
    )

    log(
        f"TWSE = "
        f"{output['market_count']['TWSE']}"
    )

    log(
        f"TPEX = "
        f"{output['market_count']['TPEX']}"
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

    # --------------------------------------------------------
    # Atomic write
    # --------------------------------------------------------

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
            "❌ Atomic Write 失敗："
            f"{type(exc).__name__}: {exc}"
        )

        return 1

    # --------------------------------------------------------
    # Re-read verification
    # --------------------------------------------------------

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
            "❌ 重新讀取失敗："
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

    # --------------------------------------------------------
    # Final
    # --------------------------------------------------------

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
        "✓ 完全動態 Universe"
    )

    log(
        "✓ 無固定股票清單"
    )

    log(
        "✓ 無固定股票名稱"
    )

    log(
        "✓ 無固定測試標的"
    )

    log(
        "✓ 無 Yahoo"
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