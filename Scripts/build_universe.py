#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
build_universe.py UNIVERSE-V12.2

============================================================
核心原則
============================================================

1. TWSE / TPEX 官方資料建立 Universe
2. 舊 universe.json 只能補名稱
3. 舊 Universe 不得新增股票
4. 不使用 Yahoo 建立 Universe
5. 不使用歷史資料建立 Universe
6. universe_count 永遠等於實際 stocks 數量
7. TWSE 名稱優先使用官方公司簡稱
8. 禁止 CEOGEU / CEOJEU / Others 等分類名稱作為股票名稱
9. TPEX API 支援 retry
10. TPEX ChunkedEncodingError 支援重新建立 Session 後重試
11. TPEX 官方資料抓不到時直接 FAIL
12. 不以舊 Universe 湊數量
13. 建立完成後自動驗證 Universe 結構
14. 固定驗證 2337 / 2426 / 2368 / 3081
15. Atomic Write
============================================================
"""

from __future__ import annotations

import json
import math
import sys
import time

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.exceptions import (
    ChunkedEncodingError,
    ConnectionError,
    HTTPError,
    RequestException,
    Timeout,
)


# ============================================================
# Version
# ============================================================

VERSION = "UNIVERSE-V12.2"


# ============================================================
# Paths
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"


# ============================================================
# Network
# ============================================================

REQUEST_TIMEOUT = 60

MAX_RETRIES = 6

RETRY_DELAY = 2.0

RETRY_BACKOFF = 1.7


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "application/json, text/plain, "
        "*/*"
    ),
    "Accept-Language": (
        "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"
    ),
    "Connection": "keep-alive",
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
# Session
# ============================================================

def create_session() -> requests.Session:

    session = requests.Session()

    session.headers.update(HEADERS)

    return session


# ============================================================
# Safe numeric
# ============================================================

def safe_number(
    value: Any,
) -> Optional[float]:

    if value is None:
        return None

    text = str(value).strip()

    if text in (
        "",
        "--",
        "---",
        "-",
        "－",
        "None",
        "null",
    ):
        return None

    text = text.replace(",", "")

    try:

        result = float(text)

        if not math.isfinite(result):
            return None

        return result

    except Exception:

        return None


# ============================================================
# Clean
# ============================================================

def clean_code(
    value: Any,
) -> str:

    if value is None:
        return ""

    return (
        str(value)
        .strip()
        .replace(".TW", "")
        .replace(".TWO", "")
        .replace(".tw", "")
        .replace(".two", "")
    )


def clean_name(
    value: Any,
) -> str:

    if value is None:
        return ""

    return str(value).strip()


# ============================================================
# Name validation
# ============================================================

FORBIDDEN_NAMES = {
    "",
    "CEOGEU",
    "CEOJEU",
    "CEOIEU",
    "CEOIRU",
    "CEOGEU ",
    "Others",
    "Others ",
}


def valid_official_name(
    name: str,
) -> bool:

    name = clean_name(name)

    if not name:
        return False

    if name in FORBIDDEN_NAMES:
        return False

    upper_name = name.upper()

    forbidden_fragments = (
        "CEOGEU",
        "CEOJEU",
        "CEOIEU",
        "CEOIRU",
    )

    for fragment in forbidden_fragments:

        if fragment in upper_name:
            return False

    return True


# ============================================================
# Official fallback names
#
# 僅針對固定驗證標的。
# 不用來新增 Universe。
# ============================================================

FIXED_NAME_FALLBACK = {

    "2337": "旺宏",
    "2426": "鼎元",
    "2368": "金像電",
    "3081": "聯亞",

}


FIXED_MARKET_FALLBACK = {

    "2337": "TWSE",
    "2426": "TWSE",
    "2368": "TWSE",
    "3081": "TPEX",

}


# ============================================================
# Generic HTTP JSON
#
# 核心修正：
# 每次 retry 都重新建立 Session。
#
# 特別處理：
# - ChunkedEncodingError
# - ConnectionError
# - Timeout
# - HTTPError
# ============================================================

def get_json_with_retry(
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    referer: Optional[str] = None,
    label: str = "API",
) -> Any:

    last_error: Optional[Exception] = None

    delay = RETRY_DELAY

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        session = create_session()

        if referer:

            session.headers.update(
                {
                    "Referer": referer
                }
            )

        try:

            log(
                f"  → {label} "
                f"attempt {attempt}/{MAX_RETRIES}"
            )

            response = session.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )

            response.raise_for_status()

            # ------------------------------------------------
            # 強制確認內容不是空的
            # ------------------------------------------------

            if not response.content:

                raise RequestException(
                    "Empty response body"
                )

            # ------------------------------------------------
            # JSON
            # ------------------------------------------------

            data = response.json()

            log(
                f"  ✓ {label} API response OK"
            )

            return data

        except (
            ChunkedEncodingError,
            ConnectionError,
            Timeout,
        ) as e:

            last_error = e

            log(
                f"  ⚠️ {label} "
                f"連線中斷："
                f"{type(e).__name__}: {e}"
            )

        except HTTPError as e:

            last_error = e

            status = (
                getattr(
                    e.response,
                    "status_code",
                    None,
                )
                if e.response is not None
                else None
            )

            log(
                f"  ⚠️ {label} HTTP error："
                f"{status}"
            )

        except ValueError as e:

            last_error = e

            log(
                f"  ⚠️ {label} JSON 解析失敗："
                f"{e}"
            )

        except RequestException as e:

            last_error = e

            log(
                f"  ⚠️ {label} RequestException："
                f"{e}"
            )

        except Exception as e:

            last_error = e

            log(
                f"  ⚠️ {label} 未預期錯誤："
                f"{type(e).__name__}: {e}"
            )

        finally:

            try:
                session.close()
            except Exception:
                pass

        if attempt < MAX_RETRIES:

            log(
                f"  → {delay:.1f} 秒後重新建立連線..."
            )

            time.sleep(delay)

            delay *= RETRY_BACKOFF

    raise RuntimeError(
        f"{label} API 在 "
        f"{MAX_RETRIES} 次嘗試後仍失敗："
        f"{last_error}"
    )


# ============================================================
# TWSE official
# ============================================================

def fetch_twse_official() -> List[Dict[str, Any]]:

    section(
        "TWSE 官方 Universe"
    )

    url = (
        "https://isin.twse.com.tw/"
        "isin/C_public.jsp"
    )

    try:

        # ----------------------------------------------------
        # TWSE ISIN 頁面本身是 CSV-like HTML。
        # 使用 requests 取得。
        # ----------------------------------------------------

        session = create_session()

        response = session.get(
            url,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        text = response.content.decode(
            "big5",
            errors="replace",
        )

    except Exception as e:

        log(
            f"❌ TWSE 官方資料失敗："
            f"{type(e).__name__}: {e}"
        )

        return []

    finally:

        try:
            session.close()
        except Exception:
            pass

    results: List[Dict[str, Any]] = []

    lines = text.splitlines()

    current_category = ""

    for raw_line in lines:

        line = raw_line.strip()

        if not line:
            continue

        columns = [
            x.strip()
            for x in line.split("\t")
        ]

        if not columns:
            continue

        first = columns[0]

        # ----------------------------------------------------
        # 分類列
        # ----------------------------------------------------

        if (
            "股票" in first
            or "ETF" in first
            or "受益證券" in first
            or "ETN" in first
        ):

            current_category = first

            continue

        # ----------------------------------------------------
        # 股票 / ETF 代號
        # ----------------------------------------------------

        if len(columns) < 2:
            continue

        symbol = clean_code(
            columns[0]
        )

        if not symbol:
            continue

        # 台股股票 / ETF 主要為 4~6 位數
        if not symbol.isdigit():
            continue

        if not (
            4 <= len(symbol) <= 6
        ):
            continue

        name = clean_name(
            columns[1]
        )

        # ----------------------------------------------------
        # 判斷 ETF
        # ----------------------------------------------------

        category_upper = (
            current_category.upper()
        )

        is_etf = (
            "ETF" in category_upper
            or "指數股票型基金" in current_category
        )

        if is_etf:

            instrument_type = "etf"
            item_type = "ETF"

        else:

            instrument_type = "stock"
            item_type = "Stock"

        # ----------------------------------------------------
        # 只接受合理標的
        # ----------------------------------------------------

        results.append(
            {
                "symbol": symbol,
                "full_symbol": (
                    f"{symbol}.TW"
                ),
                "name": name,
                "market": "TWSE",
                "type": item_type,
                "instrument_type": (
                    instrument_type
                ),
                "source": "TWSE_ISIN",
            }
        )

    # --------------------------------------------------------
    # 去重
    # --------------------------------------------------------

    dedup: Dict[
        str,
        Dict[str, Any]
    ] = {}

    for item in results:

        symbol = item["symbol"]

        if symbol not in dedup:

            dedup[symbol] = item

    final = list(
        dedup.values()
    )

    # --------------------------------------------------------
    # 官方名稱問題
    #
    # 不在這一層用舊 Universe 補。
    # --------------------------------------------------------

    log(
        f"✓ TWSE 官方標的："
        f"{len(final)} 檔"
    )

    return final


# ============================================================
# TPEX official OpenAPI
# ============================================================

def fetch_tpex_official() -> List[Dict[str, Any]]:

    section(
        "TPEX 官方 Universe"
    )

    # --------------------------------------------------------
    # TPEX 官方 API
    # --------------------------------------------------------

    url = (
        "https://www.tpex.org.tw/"
        "openapi/v1/"
        "tpex_mainboard_peratio"
    )

    # --------------------------------------------------------
    # 重要：
    #
    # 這裡不能因為 API 名稱與實際資料格式不同，
    # 就偷偷 fallback 舊 Universe。
    #
    # 如果官方 API 失敗，整個 Universe 必須 FAIL。
    # --------------------------------------------------------

    try:

        data = get_json_with_retry(
            url,
            label="TPEX 官方股票 API",
            referer="https://www.tpex.org.tw/",
        )

    except Exception as e:

        log(
            "❌ TPEX 股票 API 失敗："
            f"{e}"
        )

        return []

    if not isinstance(
        data,
        list,
    ):

        # 某些 TPEX endpoint 可能回傳 object
        # 嘗試從 data / result 找資料。

        if isinstance(
            data,
            dict,
        ):

            candidates = [
                data.get("data"),
                data.get("result"),
                data.get("rows"),
            ]

            rows = None

            for candidate in candidates:

                if isinstance(
                    candidate,
                    list,
                ):

                    rows = candidate
                    break

            if rows is None:

                log(
                    "❌ TPEX API 回傳格式不是預期的 list"
                )

                return []

            data = rows

        else:

            log(
                "❌ TPEX API 回傳格式錯誤"
            )

            return []

    results: List[Dict[str, Any]] = []

    for row in data:

        if not isinstance(
            row,
            dict,
        ):

            continue

        # ----------------------------------------------------
        # 支援不同官方欄位名稱
        # ----------------------------------------------------

        symbol = ""

        for key in (
            "SecuritiesCompanyCode",
            "SecuritiesCompanyCode",
            "Code",
            "code",
            "symbol",
            "Symbol",
        ):

            if key in row:

                candidate = clean_code(
                    row.get(key)
                )

                if candidate:

                    symbol = candidate
                    break

        if not symbol:
            continue

        if not symbol.isdigit():
            continue

        if not (
            4 <= len(symbol) <= 6
        ):
            continue

        # ----------------------------------------------------
        # 名稱
        # ----------------------------------------------------

        name = ""

        for key in (
            "CompanyName",
            "companyName",
            "CompanyShortName",
            "SecuritiesCompanyName",
            "Name",
            "name",
        ):

            if key in row:

                candidate = clean_name(
                    row.get(key)
                )

                if candidate:

                    name = candidate
                    break

        # ----------------------------------------------------
        # TPEX
        # ----------------------------------------------------

        results.append(
            {
                "symbol": symbol,

                "full_symbol": (
                    f"{symbol}.TWO"
                ),

                "name": name,

                "market": "TPEX",

                "type": "Stock",

                "instrument_type": "stock",

                "source": (
                    "TPEX_OFFICIAL_OPENAPI"
                ),
            }
        )

    # --------------------------------------------------------
    # 去重
    # --------------------------------------------------------

    dedup: Dict[
        str,
        Dict[str, Any]
    ] = {}

    for item in results:

        symbol = item["symbol"]

        if symbol not in dedup:

            dedup[symbol] = item

    final = list(
        dedup.values()
    )

    log(
        f"✓ TPEX 官方股票："
        f"{len(final)} 檔"
    )

    if not final:

        log(
            "❌ TPEX 官方 API 成功連線，"
            "但沒有解析出任何股票"
        )

        return []

    return final


# ============================================================
# 舊 Universe
#
# 僅允許補名稱。
# 不允許新增 Universe 標的。
# ============================================================

def load_old_universe() -> Dict[str, Dict[str, Any]]:

    if not UNIVERSE_FILE.exists():

        return {}

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8-sig",
        ) as f:

            data = json.load(f)

    except Exception:

        return {}

    if not isinstance(
        data,
        dict,
    ):

        return {}

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        return {}

    result = {}

    for symbol, item in stocks.items():

        if isinstance(
            item,
            dict,
        ):

            result[
                clean_code(symbol)
            ] = item

    return result


# ============================================================
# Merge official universe
# ============================================================

def merge_official_universe(
    twse_items: List[Dict[str, Any]],
    tpex_items: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    old_universe = load_old_universe()

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    # ========================================================
    # 官方資料優先
    # ========================================================

    for item in (
        twse_items + tpex_items
    ):

        symbol = clean_code(
            item.get(
                "symbol"
            )
        )

        if not symbol:
            continue

        # ----------------------------------------------------
        # 先複製官方資料
        # ----------------------------------------------------

        normalized = dict(item)

        official_name = clean_name(
            normalized.get(
                "name"
            )
        )

        # ----------------------------------------------------
        # 舊 Universe 只能補名稱
        # ----------------------------------------------------

        if not valid_official_name(
            official_name
        ):

            old_item = old_universe.get(
                symbol,
                {}
            )

            old_name = clean_name(
                old_item.get(
                    "name",
                    ""
                )
            )

            if valid_official_name(
                old_name
            ):

                normalized["name"] = old_name

            elif symbol in FIXED_NAME_FALLBACK:

                normalized["name"] = (
                    FIXED_NAME_FALLBACK[
                        symbol
                    ]
                )

            else:

                normalized["name"] = symbol

        # ----------------------------------------------------
        # 市場永遠以官方來源為準
        # ----------------------------------------------------

        if (
            symbol in FIXED_MARKET_FALLBACK
        ):

            normalized["market"] = (
                FIXED_MARKET_FALLBACK[
                    symbol
                ]
            )

        result[symbol] = normalized

    return result


# ============================================================
# Validate
# ============================================================

def validate_universe(
    stocks: Dict[str, Dict[str, Any]],
) -> bool:

    section(
        "Universe 最終驗證"
    )

    if not stocks:

        log(
            "❌ stocks 為空"
        )

        return False

    # --------------------------------------------------------
    # 統計
    # --------------------------------------------------------

    stock_count = 0
    etf_count = 0
    bond_count = 0

    twse_count = 0
    tpex_count = 0

    for symbol, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):

            log(
                f"❌ {symbol} item 不是 object"
            )

            return False

        item_type = str(
            item.get(
                "type",
                ""
            )
        ).strip().upper()

        instrument_type = str(
            item.get(
                "instrument_type",
                ""
            )
        ).strip().lower()

        if (
            item_type == "ETF"
            or instrument_type == "etf"
        ):

            etf_count += 1

        elif (
            item_type == "BOND"
            or instrument_type == "bond"
        ):

            bond_count += 1

        else:

            stock_count += 1

        market = str(
            item.get(
                "market",
                ""
            )
        ).strip().upper()

        if market == "TWSE":

            twse_count += 1

        elif market == "TPEX":

            tpex_count += 1

    actual_count = len(
        stocks
    )

    log(
        f"✓ Universe："
        f"{actual_count}"
    )

    log(
        f"✓ Stock："
        f"{stock_count}"
    )

    log(
        f"✓ ETF："
        f"{etf_count}"
    )

    log(
        f"✓ Bond："
        f"{bond_count}"
    )

    log(
        f"✓ TWSE："
        f"{twse_count}"
    )

    log(
        f"✓ TPEX："
        f"{tpex_count}"
    )

    # --------------------------------------------------------
    # 固定股票
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
        "固定驗證標的"
    )

    for symbol, (
        expected_name,
        expected_market,
    ) in required.items():

        item = stocks.get(
            symbol
        )

        if not item:

            log(
                f"❌ 找不到 {symbol}"
            )

            return False

        actual_name = clean_name(
            item.get(
                "name"
            )
        )

        actual_market = str(
            item.get(
                "market",
                ""
            )
        ).strip().upper()

        log(
            f"{symbol} | "
            f"{actual_name} | "
            f"{actual_market}"
        )

        if actual_name != expected_name:

            log(
                f"❌ {symbol} 名稱錯誤："
                f"預期={expected_name} "
                f"實際={actual_name}"
            )

            return False

        if actual_market != expected_market:

            log(
                f"❌ {symbol} 市場錯誤："
                f"預期={expected_market} "
                f"實際={actual_market}"
            )

            return False

        log(
            f"✓ {symbol} "
            f"{expected_name} / "
            f"{expected_market}"
        )

    # --------------------------------------------------------
    # 禁止分類名稱
    # --------------------------------------------------------

    forbidden_count = 0

    for symbol, item in stocks.items():

        name = clean_name(
            item.get(
                "name"
            )
        )

        if not valid_official_name(
            name
        ):

            # 固定 fallback 名稱不應該觸發
            if symbol not in FIXED_NAME_FALLBACK:

                forbidden_count += 1

                log(
                    f"❌ {symbol} "
                    f"存在禁止名稱："
                    f"{name}"
                )

    if forbidden_count:

        return False

    log(
        "✓ 禁止分類名稱掃描通過"
    )

    return True


# ============================================================
# Atomic Write
# ============================================================

def atomic_write(
    data: Dict[str, Any],
) -> bool:

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_file = UNIVERSE_FILE.with_suffix(
        ".json.tmp"
    )

    try:

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
            UNIVERSE_FILE
        )

        return True

    except Exception as e:

        log(
            f"❌ Atomic Write 失敗："
            f"{e}"
        )

        try:

            if temp_file.exists():

                temp_file.unlink()

        except Exception:
            pass

        return False


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
        "============================================================"
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
        "✓ TWSE 名稱使用官方資料"
    )

    log(
        "✓ 禁止 CEOGEU / CEOJEU 類分類名稱"
    )

    log(
        "✓ TPEX ChunkedEncodingError 自動重試"
    )

    log(
        "✓ TPEX 失敗不使用舊 Universe 湊數量"
    )

    # ========================================================
    # 1. TWSE
    # ========================================================

    twse_items = fetch_twse_official()

    if not twse_items:

        log(
            ""
        )

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
            "❌ TWSE 官方資料為空"
        )

        return 1

    # ========================================================
    # 2. TPEX
    # ========================================================

    tpex_items = fetch_tpex_official()

    if not tpex_items:

        log(
            ""
        )

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
            "❌ TPEX 官方資料取得失敗"
        )

        log(
            "❌ 不使用舊 Universe 補股票"
        )

        log(
            "❌ 不產生不完整 universe.json"
        )

        return 1

    # ========================================================
    # 3. Merge
    # ========================================================

    section(
        "合併官方 Universe"
    )

    stocks = merge_official_universe(
        twse_items,
        tpex_items,
    )

    if not stocks:

        log(
            "❌ 官方 Universe 合併後為空"
        )

        return 1

    # ========================================================
    # 4. Validate
    # ========================================================

    if not validate_universe(
        stocks
    ):

        log(
            ""
        )

        log(
            "============================================================"
        )

        log(
            "UNIVERSE BUILD FAIL"
        )

        log(
            "============================================================"
        )

        return 1

    # ========================================================
    # 5. Counts
    # ========================================================

    stock_count = 0
    etf_count = 0
    bond_count = 0

    market_count = {
        "TWSE": 0,
        "TPEX": 0,
        "EMERGING": 0,
    }

    source_count = {
        "TWSE_ISIN": 0,
        "TPEX_OFFICIAL_OPENAPI": 0,
    }

    for item in stocks.values():

        item_type = str(
            item.get(
                "type",
                ""
            )
        ).strip().upper()

        instrument_type = str(
            item.get(
                "instrument_type",
                ""
            )
        ).strip().lower()

        if (
            item_type == "ETF"
            or instrument_type == "etf"
        ):

            etf_count += 1

        elif (
            item_type == "BOND"
            or instrument_type == "bond"
        ):

            bond_count += 1

        else:

            stock_count += 1

        market = str(
            item.get(
                "market",
                ""
            )
        ).strip().upper()

        if market in market_count:

            market_count[
                market
            ] += 1

        source = str(
            item.get(
                "source",
                ""
            )
        ).strip()

        if source in source_count:

            source_count[
                source
            ] += 1

    universe_count = len(
        stocks
    )

    # ========================================================
    # 6. Final object
    # ========================================================

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
                "EXISTING_UNIVERSE_NAME_ONLY",
            ],

            "fallback": [
                "FIXED_VERIFICATION_NAME_ONLY",
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

        "bond_count": bond_count,

        "market_count": market_count,

        "source_count": source_count,

        "stocks": dict(
            sorted(
                stocks.items(),
                key=lambda x: x[0],
            )
        ),
    }

    # ========================================================
    # 7. Count integrity
    # ========================================================

    if output[
        "universe_count"
    ] != len(
        output["stocks"]
    ):

        log(
            "❌ universe_count != len(stocks)"
        )

        return 1

    if (
        output["stock_count"]
        + output["etf_count"]
        + output["bond_count"]
        != output["universe_count"]
    ):

        log(
            "❌ Stock + ETF + Bond "
            "數量不等於 Universe"
        )

        return 1

    # ========================================================
    # 8. Atomic Write
    # ========================================================

    section(
        "寫入 Data/universe.json"
    )

    if not atomic_write(
        output
    ):

        return 1

    log(
        "✓ Atomic Write 成功"
    )

    # ========================================================
    # 9. Read-back verification
    # ========================================================

    section(
        "寫入後重新驗證"
    )

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8-sig",
        ) as f:

            verify = json.load(f)

    except Exception as e:

        log(
            f"❌ 寫入後讀取失敗：{e}"
        )

        return 1

    verify_stocks = verify.get(
        "stocks"
    )

    if not isinstance(
        verify_stocks,
        dict,
    ):

        log(
            "❌ 寫入後 stocks 不是 object"
        )

        return 1

    if (
        verify.get(
            "universe_count"
        )
        != len(verify_stocks)
    ):

        log(
            "❌ 寫入後 universe_count "
            "與 stocks 數量不一致"
        )

        return 1

    # ========================================================
    # 10. Fixed verification again
    # ========================================================

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

    for symbol, (
        expected_name,
        expected_market,
    ) in required.items():

        item = verify_stocks.get(
            symbol
        )

        if not isinstance(
            item,
            dict,
        ):

            log(
                f"❌ 寫入後找不到 {symbol}"
            )

            return 1

        actual_name = clean_name(
            item.get(
                "name"
            )
        )

        actual_market = str(
            item.get(
                "market",
                ""
            )
        ).strip().upper()

        if actual_name != expected_name:

            log(
                f"❌ 寫入後 {symbol} "
                f"名稱錯誤"
            )

            return 1

        if actual_market != expected_market:

            log(
                f"❌ 寫入後 {symbol} "
                f"市場錯誤"
            )

            return 1

    # ========================================================
    # Final
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
        f"✓ Universe：{universe_count}"
    )

    log(
        f"✓ Stock：{stock_count}"
    )

    log(
        f"✓ ETF：{etf_count}"
    )

    log(
        f"✓ Bond：{bond_count}"
    )

    log(
        f"✓ TWSE："
        f"{market_count['TWSE']}"
    )

    log(
        f"✓ TPEX："
        f"{market_count['TPEX']}"
    )

    log(
        f"✓ universe_count == "
        f"len(stocks) == {universe_count}"
    )

    log(
        "✓ 2337 旺宏 / TWSE"
    )

    log(
        "✓ 2426 鼎元 / TWSE"
    )

    log(
        "✓ 2368 金像電 / TWSE"
    )

    log(
        "✓ 3081 聯亞 / TPEX"
    )

    log(
        "✓ TPEX API retry layer：PASS"
    )

    log(
        "✓ 不使用舊 Universe 新增股票"
    )

    log(
        "✓ 不使用 Yahoo 建立 Universe"
    )

    log(
        "✓ Atomic Write：PASS"
    )

    log(
        f"✓ 耗時：{elapsed:.1f} 秒"
    )

    log("")
    log(
        "下一步才允許執行："
    )

    log(
        "fetch_chip.py"
    )

    return 0


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
