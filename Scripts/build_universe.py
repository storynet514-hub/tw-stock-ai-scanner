#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
build_universe.py V10.0

============================================================
V10.0 正式版
============================================================

用途
------------------------------------------------------------
建立 Data/universe.json

資料來源架構
------------------------------------------------------------
1. TWSE 官方 OpenAPI
   /opendata/t187ap03_L
   → 上市公司基本資料

2. TPEX 官方 OpenAPI
   /mopsfin_t187ap03_O
   → 上櫃股票基本資料

3. 不使用民間第三方作為主要股票池來源

核心原則
------------------------------------------------------------
1. 官方來源優先
2. TWSE / TPEX 分開抓取
3. 市場別直接由官方來源決定
4. 不依靠股票代號首碼猜測市場
5. 名稱不得為空字串
6. 不允許來源資料缺名稱後靜默寫入
7. 不使用 fetch_chip.py 補名稱
8. 3081 必須由 universe 層正確建立：
      symbol      = 3081
      full_symbol = 3081.TWO
      name        = 聯亞
      market      = TPEX
      type        = Stock
9. 股票與 ETF 分類
10. 代號去重
11. Atomic Write
12. 寫入前完整驗證
13. 驗證失敗不覆蓋舊 universe.json
14. 保留既有 universe.json 作為安全備援
15. 輸出完整來源統計

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


# ============================================================
# Version
# ============================================================

VERSION = "V10.0"


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

MAX_RETRIES = 3

RETRY_DELAY = 1.5


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
# 官方 API
# ============================================================

TWSE_API_URL = (
    "https://openapi.twse.com.tw/v1/"
    "opendata/t187ap03_L"
)


TPEX_API_URL = (
    "https://www.tpex.org.tw/openapi/v1.0/"
    "mopsfin_t187ap03_O"
)


# ============================================================
# 固定驗證標的
# ============================================================

REQUIRED_TEST_STOCKS = {
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
# 基本清理
# ============================================================

def clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value)

    text = (
        text
        .replace("\ufeff", "")
        .replace("\u3000", " ")
        .strip()
    )

    return text


def clean_symbol(value: Any) -> str:
    text = clean_text(value)

    if not text:
        return ""

    upper = text.upper()

    for suffix in (
        ".TW",
        ".TWO",
        ".TWSE",
        ".TPEX",
    ):
        if upper.endswith(suffix):
            text = text[: -len(suffix)]
            break

    text = text.strip()

    # 避免 2337.0
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]

    return text


def normalize_market(
    market: Any,
    default: str = "",
) -> str:

    text = clean_text(market).upper()

    if text in {
        "TWSE",
        "TSE",
        "上市",
        "集中市場",
    }:
        return "TWSE"

    if text in {
        "TPEX",
        "TPEx",
        "OTC",
        "上櫃",
        "櫃買",
    }:
        return "TPEX"

    return default


# ============================================================
# 數字 / 代號判斷
# ============================================================

def is_stock_code(symbol: str) -> bool:
    symbol = clean_symbol(symbol)

    return (
        len(symbol) == 4
        and symbol.isdigit()
    )


def is_etf_code(symbol: str) -> bool:
    symbol = clean_symbol(symbol)

    if not symbol.isdigit():
        return False

    # 台股 ETF / ETN / 基金類代號通常不是一般四位股票。
    #
    # 這裡不把它當成「股票」。
    #
    # 允許 5~6 碼數字。
    return (
        5 <= len(symbol) <= 6
    )


def infer_type(
    symbol: str,
    raw_type: Any = None,
) -> str:

    text = clean_text(raw_type).lower()

    if text in {
        "etf",
        "基金",
        "指數股票型基金",
        "etn",
    }:
        return "ETF"

    if text in {
        "stock",
        "股票",
        "普通股",
    }:
        return "Stock"

    if is_stock_code(symbol):
        return "Stock"

    if is_etf_code(symbol):
        return "ETF"

    return "Other"


# ============================================================
# 官方欄位抽取
# ============================================================

def get_first_value(
    item: Dict[str, Any],
    keys: List[str],
) -> Any:

    for key in keys:

        if key in item:

            value = item.get(key)

            if value is not None:
                text = clean_text(value)

                if text:
                    return value

    return None


def extract_symbol(
    item: Dict[str, Any],
) -> str:

    value = get_first_value(
        item,
        [
            "有價證券代號",
            "公司代號",
            "證券代號",
            "股票代號",
            "代號",
            "code",
            "Code",
            "stock_code",
            "StockCode",
        ],
    )

    return clean_symbol(value)


def extract_name(
    item: Dict[str, Any],
) -> str:

    value = get_first_value(
        item,
        [
            "有價證券名稱",
            "公司名稱",
            "證券名稱",
            "股票名稱",
            "名稱",
            "name",
            "Name",
            "company_name",
            "CompanyName",
        ],
    )

    return clean_text(value)


# ============================================================
# HTTP
# ============================================================

def fetch_json(
    session: requests.Session,
    url: str,
    source_name: str,
) -> Optional[Any]:

    last_error = ""

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            log(
                f"  → {source_name} "
                f"第 {attempt}/{MAX_RETRIES} 次請求"
            )

            response = session.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code != 200:

                last_error = (
                    f"HTTP {response.status_code}"
                )

                log(
                    f"  ⚠️ {source_name} "
                    f"{last_error}"
                )

                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)

                continue

            try:

                data = response.json()

            except Exception as exc:

                last_error = (
                    f"JSON 解析失敗：{exc}"
                )

                log(
                    f"  ⚠️ {source_name} "
                    f"{last_error}"
                )

                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)

                continue

            return data

        except requests.RequestException as exc:

            last_error = str(exc)

            log(
                f"  ⚠️ {source_name} "
                f"網路錯誤：{exc}"
            )

            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

        except Exception as exc:

            last_error = str(exc)

            log(
                f"  ⚠️ {source_name} "
                f"未知錯誤：{exc}"
            )

            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

    log(
        f"❌ {source_name} 取得失敗："
        f"{last_error}"
    )

    return None


# ============================================================
# 官方資料標準化
# ============================================================

def normalize_record(
    item: Dict[str, Any],
    market: str,
) -> Optional[Dict[str, str]]:

    symbol = extract_symbol(item)

    if not symbol:
        return None

    # 股票池目前只收：
    #
    # 4 碼普通股票
    # 5~6 碼 ETF 類
    #
    if not (
        is_stock_code(symbol)
        or is_etf_code(symbol)
    ):
        return None

    name = extract_name(item)

    # 名稱缺失直接不產生 record。
    #
    # 不允許空字串進 universe。
    if not name:
        return {
            "_invalid": "missing_name",
            "symbol": symbol,
            "market": market,
        }

    raw_type = get_first_value(
        item,
        [
            "有價證券種類",
            "證券種類",
            "股票種類",
            "類別",
            "type",
            "Type",
        ],
    )

    sec_type = infer_type(
        symbol,
        raw_type,
    )

    if sec_type == "Other":
        return None

    if market == "TWSE":
        full_symbol = (
            f"{symbol}.TW"
        )

    elif market == "TPEX":
        full_symbol = (
            f"{symbol}.TWO"
        )

    else:
        return None

    return {
        "symbol": symbol,
        "full_symbol": full_symbol,
        "name": name,
        "market": market,
        "type": sec_type,
    }


# ============================================================
# TWSE
# ============================================================

def fetch_twse(
    session: requests.Session,
) -> Tuple[
    List[Dict[str, str]],
    List[Dict[str, str]],
]:

    section(
        "1. TWSE 官方上市股票資料"
    )

    data = fetch_json(
        session,
        TWSE_API_URL,
        "TWSE 官方 t187ap03_L",
    )

    if data is None:
        return [], []

    if not isinstance(data, list):
        log(
            "❌ TWSE API 回傳格式不是 list"
        )
        return [], []

    valid_records: List[
        Dict[str, str]
    ] = []

    invalid_name_records: List[
        Dict[str, str]
    ] = []

    seen = set()

    for item in data:

        if not isinstance(item, dict):
            continue

        record = normalize_record(
            item,
            "TWSE",
        )

        if record is None:
            continue

        if record.get("_invalid"):
            invalid_name_records.append(
                record
            )
            continue

        symbol = record["symbol"]

        if symbol in seen:
            continue

        seen.add(symbol)

        valid_records.append(record)

    log(
        f"✓ TWSE 官方原始資料："
        f"{len(data)} 筆"
    )

    log(
        f"✓ TWSE 有效標的："
        f"{len(valid_records)} 檔"
    )

    log(
        f"⚠️ TWSE 名稱缺失："
        f"{len(invalid_name_records)} 檔"
    )

    return (
        valid_records,
        invalid_name_records,
    )


# ============================================================
# TPEX
# ============================================================

def fetch_tpex(
    session: requests.Session,
) -> Tuple[
    List[Dict[str, str]],
    List[Dict[str, str]],
]:

    section(
        "2. TPEX 官方上櫃股票資料"
    )

    data = fetch_json(
        session,
        TPEX_API_URL,
        "TPEX 官方 mopsfin_t187ap03_O",
    )

    if data is None:
        return [], []

    # TPEX OpenAPI 正常情況：
    #
    # [
    #   {...},
    #   {...}
    # ]
    #
    # 某些 API / 版本可能包在 data。
    if isinstance(data, dict):

        if isinstance(
            data.get("data"),
            list,
        ):
            data = data["data"]

        elif isinstance(
            data.get("result"),
            list,
        ):
            data = data["result"]

        else:

            log(
                "❌ TPEX API 找不到資料陣列"
            )

            return [], []

    if not isinstance(data, list):

        log(
            "❌ TPEX API 回傳格式無法解析"
        )

        return [], []

    valid_records: List[
        Dict[str, str]
    ] = []

    invalid_name_records: List[
        Dict[str, str]
    ] = []

    seen = set()

    for item in data:

        if not isinstance(item, dict):
            continue

        record = normalize_record(
            item,
            "TPEX",
        )

        if record is None:
            continue

        if record.get("_invalid"):
            invalid_name_records.append(
                record
            )
            continue

        symbol = record["symbol"]

        if symbol in seen:
            continue

        seen.add(symbol)

        valid_records.append(record)

    log(
        f"✓ TPEX 官方原始資料："
        f"{len(data)} 筆"
    )

    log(
        f"✓ TPEX 有效標的："
        f"{len(valid_records)} 檔"
    )

    log(
        f"⚠️ TPEX 名稱缺失："
        f"{len(invalid_name_records)} 檔"
    )

    return (
        valid_records,
        invalid_name_records,
    )


# ============================================================
# 合併官方來源
# ============================================================

def merge_records(
    twse_records: List[Dict[str, str]],
    tpex_records: List[Dict[str, str]],
) -> Tuple[
    Dict[str, Dict[str, str]],
    List[str],
]:

    section(
        "3. 合併 TWSE / TPEX 官方股票池"
    )

    merged: Dict[
        str,
        Dict[str, str]
    ] = {}

    conflicts: List[str] = []

    # --------------------------------------------------------
    # TWSE
    # --------------------------------------------------------

    for record in twse_records:

        symbol = record["symbol"]

        if symbol in merged:

            conflicts.append(
                f"{symbol}: "
                f"TWSE duplicate"
            )

            continue

        merged[symbol] = record

    # --------------------------------------------------------
    # TPEX
    # --------------------------------------------------------

    for record in tpex_records:

        symbol = record["symbol"]

        if symbol in merged:

            existing = merged[symbol]

            # 同代號不同市場屬來源衝突。
            if (
                existing["market"]
                != record["market"]
            ):

                conflicts.append(
                    f"{symbol}: "
                    f"{existing['market']} "
                    f"vs "
                    f"{record['market']}"
                )

                continue

            # 同市場重複。
            conflicts.append(
                f"{symbol}: "
                f"TPEX duplicate"
            )

            continue

        merged[symbol] = record

    log(
        f"✓ 合併後唯一標的："
        f"{len(merged)} 檔"
    )

    log(
        f"✓ 來源衝突 / 重複："
        f"{len(conflicts)} 筆"
    )

    return (
        merged,
        conflicts,
    )


# ============================================================
# 來源完整性檢查
# ============================================================

def validate_source_counts(
    twse_records: List[Dict[str, str]],
    tpex_records: List[Dict[str, str]],
) -> bool:

    section(
        "4. 官方來源完整性檢查"
    )

    twse_count = len(twse_records)

    tpex_count = len(tpex_records)

    total = (
        twse_count
        + tpex_count
    )

    log(
        f"TWSE：{twse_count} 檔"
    )

    log(
        f"TPEX：{tpex_count} 檔"
    )

    log(
        f"合計：{total} 檔"
    )

    # --------------------------------------------------------
    # 不硬編固定數字。
    #
    # 市場標的會隨上市、下市、ETF、公司異動。
    #
    # 但如果其中一個市場完全為 0，
    # 幾乎一定是 API 失敗或解析錯誤。
    # --------------------------------------------------------

    if twse_count == 0:

        log(
            "❌ TWSE 有效資料為 0"
        )

        return False

    if tpex_count == 0:

        log(
            "❌ TPEX 有效資料為 0"
        )

        return False

    if total < 1000:

        log(
            "❌ 官方股票池數量異常偏低："
            f"{total}"
        )

        return False

    log(
        "✓ TWSE / TPEX 官方來源均有有效資料"
    )

    return True


# ============================================================
# 固定測試股票
# ============================================================

def validate_required_stocks(
    stocks: Dict[str, Dict[str, str]],
) -> bool:

    section(
        "5. 固定測試股票來源驗證"
    )

    passed = True

    for symbol, expected in (
        REQUIRED_TEST_STOCKS.items()
    ):

        item = stocks.get(symbol)

        if item is None:

            log(
                f"❌ {symbol} "
                f"{expected['name']} "
                f"不存在 universe"
            )

            passed = False

            continue

        actual_name = clean_text(
            item.get("name")
        )

        actual_market = clean_text(
            item.get("market")
        )

        actual_type = clean_text(
            item.get("type")
        )

        actual_full_symbol = clean_text(
            item.get("full_symbol")
        )

        log(
            f"{symbol} | "
            f"名稱：{actual_name} | "
            f"市場：{actual_market} | "
            f"類型：{actual_type} | "
            f"full_symbol："
            f"{actual_full_symbol}"
        )

        if actual_name != expected["name"]:

            log(
                f"  ❌ 名稱錯誤："
                f"預期 {expected['name']} / "
                f"實際 {actual_name}"
            )

            passed = False

        if actual_market != expected["market"]:

            log(
                f"  ❌ 市場錯誤："
                f"預期 {expected['market']} / "
                f"實際 {actual_market}"
            )

            passed = False

        if actual_type != expected["type"]:

            log(
                f"  ❌ 類型錯誤："
                f"預期 {expected['type']} / "
                f"實際 {actual_type}"
            )

            passed = False

        expected_suffix = (
            ".TW"
            if expected["market"] == "TWSE"
            else ".TWO"
        )

        expected_full_symbol = (
            f"{symbol}{expected_suffix}"
        )

        if (
            actual_full_symbol
            != expected_full_symbol
        ):

            log(
                f"  ❌ full_symbol 錯誤："
                f"預期 {expected_full_symbol} / "
                f"實際 {actual_full_symbol}"
            )

            passed = False

    if passed:

        log(
            "✓ 2337 / 2426 / 2368 / 3081 "
            "全部通過"
        )

    return passed


# ============================================================
# 全部資料 schema 驗證
# ============================================================

def validate_all_records(
    stocks: Dict[str, Dict[str, str]],
) -> bool:

    section(
        "6. universe 全資料 schema 驗證"
    )

    errors: List[str] = []

    required_fields = {
        "symbol",
        "full_symbol",
        "name",
        "market",
        "type",
    }

    seen_symbols = set()

    for symbol, item in stocks.items():

        if not isinstance(item, dict):

            errors.append(
                f"{symbol}: item 不是 object"
            )

            continue

        # ----------------------------------------------------
        # key / symbol
        # ----------------------------------------------------

        actual_symbol = clean_symbol(
            item.get("symbol")
        )

        if not actual_symbol:

            errors.append(
                f"{symbol}: symbol 空白"
            )

        if actual_symbol != symbol:

            errors.append(
                f"{symbol}: key 與 symbol 不一致"
            )

        if symbol in seen_symbols:

            errors.append(
                f"{symbol}: duplicate"
            )

        seen_symbols.add(symbol)

        # ----------------------------------------------------
        # 欄位
        # ----------------------------------------------------

        for field in required_fields:

            value = clean_text(
                item.get(field)
            )

            if not value:

                errors.append(
                    f"{symbol}: "
                    f"{field} 空白"
                )

        # ----------------------------------------------------
        # 市場
        # ----------------------------------------------------

        market = item.get("market")

        if market not in {
            "TWSE",
            "TPEX",
        }:

            errors.append(
                f"{symbol}: "
                f"非法 market={market!r}"
            )

        # ----------------------------------------------------
        # 類型
        # ----------------------------------------------------

        sec_type = item.get("type")

        if sec_type not in {
            "Stock",
            "ETF",
        }:

            errors.append(
                f"{symbol}: "
                f"非法 type={sec_type!r}"
            )

        # ----------------------------------------------------
        # full_symbol
        # ----------------------------------------------------

        full_symbol = (
            clean_text(
                item.get("full_symbol")
            )
            .upper()
        )

        if market == "TWSE":

            if not full_symbol.endswith(
                ".TW"
            ):

                errors.append(
                    f"{symbol}: "
                    f"TWSE full_symbol 錯誤"
                )

        elif market == "TPEX":

            if not full_symbol.endswith(
                ".TWO"
            ):

                errors.append(
                    f"{symbol}: "
                    f"TPEX full_symbol 錯誤"
                )

        # ----------------------------------------------------
        # 禁止空名稱
        # ----------------------------------------------------

        if not clean_text(
            item.get("name")
        ):

            errors.append(
                f"{symbol}: "
                f"name 不得為空"
            )

    if errors:

        log(
            f"❌ 發現 {len(errors)} 個 schema 錯誤"
        )

        for error in errors[:100]:
            log(
                f"  - {error}"
            )

        if len(errors) > 100:

            log(
                f"  ...另外 "
                f"{len(errors) - 100} 個錯誤"
            )

        return False

    log(
        "✓ 所有 universe 標的 schema 正確"
    )

    return True


# ============================================================
# 統計
# ============================================================

def build_statistics(
    stocks: Dict[str, Dict[str, str]],
) -> Dict[str, int]:

    twse_stock = 0
    twse_etf = 0

    tpex_stock = 0
    tpex_etf = 0

    for item in stocks.values():

        market = item["market"]

        sec_type = item["type"]

        if market == "TWSE":

            if sec_type == "Stock":
                twse_stock += 1

            elif sec_type == "ETF":
                twse_etf += 1

        elif market == "TPEX":

            if sec_type == "Stock":
                tpex_stock += 1

            elif sec_type == "ETF":
                tpex_etf += 1

    return {
        "twse_stock": twse_stock,
        "twse_etf": twse_etf,
        "tpex_stock": tpex_stock,
        "tpex_etf": tpex_etf,
        "twse_total": (
            twse_stock
            + twse_etf
        ),
        "tpex_total": (
            tpex_stock
            + tpex_etf
        ),
        "stock_total": (
            twse_stock
            + tpex_stock
        ),
        "etf_total": (
            twse_etf
            + tpex_etf
        ),
        "total": len(stocks),
    }


# ============================================================
# Atomic Write
# ============================================================

def atomic_write_json(
    path: Path,
    data: Dict[str, Any],
) -> bool:

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_file = path.with_suffix(
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

        temp_file.replace(path)

        return True

    except Exception as exc:

        log(
            f"❌ Atomic Write 失敗："
            f"{exc}"
        )

        try:

            if temp_file.exists():
                temp_file.unlink()

        except Exception:
            pass

        return False


# ============================================================
# 舊檔安全檢查
# ============================================================

def existing_universe_info() -> str:

    if not UNIVERSE_FILE.exists():

        return "不存在"

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8",
        ) as f:

            data = json.load(f)

        if not isinstance(data, dict):
            return "存在但格式錯誤"

        stocks = data.get(
            "items",
            [],
        )

        if isinstance(stocks, list):

            return (
                f"存在，items={len(stocks)}"
            )

        return "存在"

    except Exception as exc:

        return (
            f"存在但無法解析：{exc}"
        )


# ============================================================
# Main
# ============================================================

def main() -> int:

    start_time = time.time()

    section(
        f"台股 AI 選股系統 "
        f"build_universe.py {VERSION}"
    )

    log(
        "股票池來源："
    )

    log(
        "  ✓ TWSE 官方 OpenAPI"
    )

    log(
        "  ✓ TPEX 官方 OpenAPI"
    )

    log(
        "  ✗ 不使用民間第三方作為主要來源"
    )

    log(
        "  ✓ 名稱由官方來源直接建立"
    )

    log(
        "  ✓ 不依賴 fetch_chip.py 補名稱"
    )

    log(
        f"目前 universe.json："
        f"{existing_universe_info()}"
    )

    session = requests.Session()

    session.headers.update(
        HEADERS
    )

    # ========================================================
    # 1. TWSE
    # ========================================================

    (
        twse_records,
        twse_missing_names,
    ) = fetch_twse(
        session
    )

    # ========================================================
    # 2. TPEX
    # ========================================================

    (
        tpex_records,
        tpex_missing_names,
    ) = fetch_tpex(
        session
    )

    # ========================================================
    # 3. 官方來源完整性
    # ========================================================

    if not validate_source_counts(
        twse_records,
        tpex_records,
    ):

        log("")
        log(
            "❌ 官方來源完整性驗證失敗"
        )

        log(
            "❌ 不覆蓋既有 universe.json"
        )

        return 1

    # ========================================================
    # 4. 合併
    # ========================================================

    (
        merged,
        conflicts,
    ) = merge_records(
        twse_records,
        tpex_records,
    )

    # ========================================================
    # 5. 名稱缺失
    # ========================================================

    missing_name_records = (
        twse_missing_names
        + tpex_missing_names
    )

    if missing_name_records:

        section(
            "名稱缺失來源資料"
        )

        for item in (
            missing_name_records[:100]
        ):

            log(
                f"⚠️ "
                f"{item.get('market')} "
                f"{item.get('symbol')} "
                f"官方來源沒有名稱"
            )

        if len(
            missing_name_records
        ) > 100:

            log(
                f"...另外 "
                f"{len(missing_name_records) - 100} 檔"
            )

        # 注意：
        #
        # 這些資料不進 universe。
        #
        # 不做任何第三方猜測。
        #
        # 不寫空字串。
        #
        # 這是刻意的安全策略。

    # ========================================================
    # 6. 固定股票驗證
    # ========================================================

    if not validate_required_stocks(
        merged
    ):

        log("")
        log(
            "❌ 固定測試股票驗證失敗"
        )

        log(
            "❌ 不覆蓋既有 universe.json"
        )

        return 1

    # ========================================================
    # 7. 全資料 schema
    # ========================================================

    if not validate_all_records(
        merged
    ):

        log("")
        log(
            "❌ universe schema 驗證失敗"
        )

        log(
            "❌ 不覆蓋既有 universe.json"
        )

        return 1

    # ========================================================
    # 8. 統計
    # ========================================================

    statistics = build_statistics(
        merged
    )

    section(
        "7. 最終股票池統計"
    )

    log(
        f"TWSE 股票："
        f"{statistics['twse_stock']}"
    )

    log(
        f"TWSE ETF："
        f"{statistics['twse_etf']}"
    )

    log(
        f"TWSE 合計："
        f"{statistics['twse_total']}"
    )

    log("")

    log(
        f"TPEX 股票："
        f"{statistics['tpex_stock']}"
    )

    log(
        f"TPEX ETF："
        f"{statistics['tpex_etf']}"
    )

    log(
        f"TPEX 合計："
        f"{statistics['tpex_total']}"
    )

    log("")

    log(
        f"股票總數："
        f"{statistics['stock_total']}"
    )

    log(
        f"ETF 總數："
        f"{statistics['etf_total']}"
    )

    log(
        f"全市場總數："
        f"{statistics['total']}"
    )

    log(
        f"來源衝突 / 重複："
        f"{len(conflicts)}"
    )

    log(
        f"官方名稱缺失："
        f"{len(missing_name_records)}"
    )

    # ========================================================
    # 9. 建立正式 universe
    # ========================================================

    generated_at = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    # --------------------------------------------------------
    # 排序
    # --------------------------------------------------------

    sorted_items = sorted(
        merged.values(),
        key=lambda item: (
            0
            if item["market"] == "TWSE"
            else 1,
            item["symbol"],
        ),
    )

    output = {

        "schema_version": VERSION,

        "generated_at": generated_at,

        "source": {

            "primary": [
                "TWSE",
                "TPEX",
            ],

            "twse_api": TWSE_API_URL,

            "tpex_api": TPEX_API_URL,

            "third_party_primary": False,
        },

        "statistics": statistics,

        "items": sorted_items,
    }

    # ========================================================
    # 10. 再一次檢查輸出
    # ========================================================

    output_stocks = {
        item["symbol"]: item
        for item in sorted_items
    }

    if not validate_required_stocks(
        output_stocks
    ):

        log(
            "❌ 最終輸出驗證失敗"
        )

        return 1

    if not validate_all_records(
        output_stocks
    ):

        log(
            "❌ 最終輸出 schema 驗證失敗"
        )

        return 1

    # ========================================================
    # 11. Atomic Write
    # ========================================================

    section(
        "8. 寫入 Data/universe.json"
    )

    if not atomic_write_json(
        UNIVERSE_FILE,
        output,
    ):

        log(
            "❌ universe.json 寫入失敗"
        )

        return 1

    log(
        "✓ Data/universe.json Atomic Write 成功"
    )

    # ========================================================
    # 12. 寫入後重新讀取驗證
    # ========================================================

    section(
        "9. 寫入後重新讀取驗證"
    )

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8",
        ) as f:

            verify_data = json.load(f)

    except Exception as exc:

        log(
            f"❌ 寫入後 JSON 重新讀取失敗："
            f"{exc}"
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

    verify_items = verify_data.get(
        "items"
    )

    if not isinstance(
        verify_items,
        list,
    ):

        log(
            "❌ universe.json items 不是 list"
        )

        return 1

    verify_map = {
        item["symbol"]: item
        for item in verify_items
        if isinstance(item, dict)
        and item.get("symbol")
    }

    if not validate_required_stocks(
        verify_map
    ):

        log(
            "❌ 寫入後固定股票驗證失敗"
        )

        return 1

    # ========================================================
    # 13. 特別列印 3081
    # ========================================================

    section(
        "10. 3081 聯亞最終來源確認"
    )

    stock_3081 = verify_map.get(
        "3081"
    )

    if not stock_3081:

        log(
            "❌ 3081 不存在"
        )

        return 1

    log(
        f"symbol      = "
        f"{stock_3081.get('symbol')}"
    )

    log(
        f"full_symbol = "
        f"{stock_3081.get('full_symbol')}"
    )

    log(
        f"name        = "
        f"{stock_3081.get('name')}"
    )

    log(
        f"market      = "
        f"{stock_3081.get('market')}"
    )

    log(
        f"type        = "
        f"{stock_3081.get('type')}"
    )

    if (
        stock_3081.get("symbol")
        != "3081"
        or stock_3081.get("full_symbol")
        != "3081.TWO"
        or stock_3081.get("name")
        != "聯亞"
        or stock_3081.get("market")
        != "TPEX"
        or stock_3081.get("type")
        != "Stock"
    ):

        log(
            "❌ 3081 聯亞最終來源驗證失敗"
        )

        return 1

    log(
        "✓ 3081 聯亞來源驗證通過"
    )

    # ========================================================
    # 14. 最終結果
    # ========================================================

    elapsed = (
        time.time()
        - start_time
    )

    section(
        "BUILD UNIVERSE PASS"
    )

    log(
        f"✓ build_universe.py {VERSION}"
    )

    log(
        f"✓ TWSE："
        f"{statistics['twse_total']} 檔"
    )

    log(
        f"✓ TPEX："
        f"{statistics['tpex_total']} 檔"
    )

    log(
        f"✓ 股票："
        f"{statistics['stock_total']} 檔"
    )

    log(
        f"✓ ETF："
        f"{statistics['etf_total']} 檔"
    )

    log(
        f"✓ 全市場："
        f"{statistics['total']} 檔"
    )

    log(
        "✓ 名稱空白未寫入"
    )

    log(
        "✓ 不使用第三方作為主要股票池"
    )

    log(
        "✓ TWSE / TPEX 官方來源保留"
    )

    log(
        "✓ 市場別不再靠代號猜測"
    )

    log(
        "✓ 3081 = 聯亞 / TPEX / 3081.TWO"
    )

    log(
        "✓ Atomic Write"
    )

    log(
        f"✓ 耗時：{elapsed:.1f} 秒"
    )

    return 0


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":
    sys.exit(main())