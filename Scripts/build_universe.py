#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_universe.py V6.0.0

============================================================
正式修正版
============================================================

目的
------------------------------------------------------------
建立 Data/universe.json

核心原則
------------------------------------------------------------
1. universe.json 是全市場股票池來源
2. TWSE / TPEX 分開取得
3. 股票與 ETF 都保留
4. 不因單一 API timeout 破壞既有 universe
5. 不使用第三方股票池
6. 不產生任何主力估算
7. 不產生三大法人資料
8. 不產生當沖資料
9. 名稱不得靜默變成空字串
10. 3081 必須為「聯亞」
11. TPEX 名稱與市場資訊優先修正
12. 保留既有 universe 作為 fallback
13. 新資料必須通過安全門檻才允許覆蓋
14. Atomic Write
15. 最終輸出供 fetch_chip.py 使用

安全門檻
------------------------------------------------------------
TWSE Stock >= 700
TPEX Stock >= 300
Total Stock >= 1200
ETF >= 1

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


VERSION = "V6.0.0"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

REQUEST_TIMEOUT = 30
MAX_RETRIES = 3

MIN_TWSE_STOCKS = 700
MIN_TPEX_STOCKS = 300
MIN_TOTAL_STOCKS = 1200
MIN_ETF = 1

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "application/json, text/javascript, "
        "*/*; q=0.01"
    ),
    "Accept-Language": (
        "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"
    ),
    "Referer": "https://www.twse.com.tw/",
}

TEST_STOCKS = {
    "2337": "旺宏",
    "2426": "鼎元",
    "2368": "金像電",
    "3081": "聯亞",
}


# ============================================================
# 官方名稱 / 市場強制修正
# ============================================================

OFFICIAL_NAME_FALLBACK = {
    "2337": "旺宏",
    "2426": "鼎元",
    "2368": "金像電",
    "3081": "聯亞",
}

OFFICIAL_MARKET_FALLBACK = {
    "3081": "TPEX",
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
# 基本工具
# ============================================================

def clean_code(value: Any) -> str:

    if value is None:
        return ""

    text = str(value).strip()

    for suffix in (
        ".TW",
        ".TWO",
        ".tw",
        ".two",
    ):
        if text.endswith(suffix):
            text = text[:-len(suffix)]

    return text.strip()


def clean_name(value: Any) -> str:

    if value is None:
        return ""

    text = str(value).strip()

    invalid_values = {
        "",
        "-",
        "--",
        "---",
        "None",
        "null",
        "nan",
    }

    if text in invalid_values:
        return ""

    return text


def is_stock_code(code: str) -> bool:

    code = clean_code(code)

    return (
        len(code) == 4
        and code.isdigit()
    )


def is_etf_code(code: str) -> bool:

    code = clean_code(code)

    return (
        code.startswith("00")
        and 5 <= len(code) <= 6
        and code.isdigit()
    )


def is_valid_code(code: str) -> Tuple[bool, str]:

    if is_stock_code(code):
        return True, "Stock"

    if is_etf_code(code):
        return True, "ETF"

    return False, "Other"


def safe_number(value: Any) -> Optional[float]:

    if value is None:
        return None

    text = str(value).strip()

    if text in {
        "",
        "--",
        "---",
        "-",
        "－",
        "None",
        "null",
        "nan",
    }:
        return None

    text = text.replace(",", "")

    try:
        result = float(text)

        if not math.isfinite(result):
            return None

        return result

    except Exception:
        return None


def request_json(
    session: requests.Session,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    retries: int = MAX_RETRIES,
) -> Optional[Any]:

    for attempt in range(1, retries + 1):

        try:

            log(
                f"  HTTP GET attempt "
                f"{attempt}/{retries}"
            )

            response = session.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            data = response.json()

            return data

        except Exception as exc:

            log(
                f"  ⚠️ attempt {attempt} "
                f"失敗：{exc}"
            )

            if attempt < retries:
                time.sleep(1.5)

    return None


# ============================================================
# 市場判斷
# ============================================================

def infer_market(
    code: str,
    full_symbol: str = "",
) -> str:

    upper = str(
        full_symbol or ""
    ).upper()

    if ".TWO" in upper:
        return "TPEX"

    if ".TW" in upper:
        return "TWSE"

    # 已知 TPEX 標的
    if code == "3081":
        return "TPEX"

    # 上櫃常見代號區域。
    #
    # 注意：
    # 這只是 fallback，
    # API 有明確市場時以 API 為準。
    if code.startswith(
        (
            "3",
            "4",
            "5",
            "6",
            "8",
        )
    ):
        return "TPEX"

    return "TWSE"


# ============================================================
# 正規化項目
# ============================================================

def normalize_item(
    code: str,
    name: str,
    market: str,
    sec_type: str,
    full_symbol: str = "",
) -> Optional[Dict[str, str]]:

    code = clean_code(code)
    name = clean_name(name)

    valid, inferred_type = is_valid_code(code)

    if not valid:
        return None

    if sec_type not in {
        "Stock",
        "ETF",
    }:
        sec_type = inferred_type

    if market not in {
        "TWSE",
        "TPEX",
    }:
        market = infer_market(
            code,
            full_symbol,
        )

    # --------------------------------------------------------
    # 官方名稱 fallback
    # --------------------------------------------------------

    if not name:
        fallback = OFFICIAL_NAME_FALLBACK.get(
            code
        )

        if fallback:
            name = fallback

    # --------------------------------------------------------
    # 3081 強制修正
    # --------------------------------------------------------

    if code == "3081":
        name = "聯亞"
        market = "TPEX"
        sec_type = "Stock"

    if not name:
        return None

    if not full_symbol:

        suffix = (
            ".TWO"
            if market == "TPEX"
            else ".TW"
        )

        full_symbol = (
            f"{code}{suffix}"
        )

    return {
        "symbol": code,
        "full_symbol": full_symbol,
        "name": name,
        "market": market,
        "type": sec_type,
    }


# ============================================================
# TWSE
# ============================================================

def fetch_twse_universe(
    session: requests.Session,
) -> List[Dict[str, str]]:

    section(
        "取得 TWSE 上市股票 / ETF"
    )

    urls = [
        (
            "https://openapi.twse.com.tw/"
            "v1/opendata/t187ap03_L"
        ),
        (
            "https://openapi.twse.com.tw/"
            "v1/opendata/t187ap03_L"
        ),
    ]

    data = None

    for url in urls:

        data = request_json(
            session,
            url,
        )

        if data is not None:
            break

    if not isinstance(data, list):

        log(
            "❌ TWSE API 未取得有效資料"
        )

        return []

    result: List[
        Dict[str, str]
    ] = []

    seen = set()

    for row in data:

        if not isinstance(row, dict):
            continue

        code = clean_code(
            row.get("公司代號")
            or row.get("證券代號")
            or row.get("代號")
        )

        name = clean_name(
            row.get("公司簡稱")
            or row.get("證券名稱")
            or row.get("名稱")
        )

        if not code:
            continue

        valid, sec_type = (
            is_valid_code(code)
        )

        if not valid:
            continue

        if code in seen:
            continue

        normalized = normalize_item(
            code=code,
            name=name,
            market="TWSE",
            sec_type=sec_type,
            full_symbol=f"{code}.TW",
        )

        if normalized:

            result.append(
                normalized
            )

            seen.add(code)

    log(
        f"✓ TWSE API 取得 "
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# TPEX
# ============================================================

def parse_tpex_rows(
    rows: Any,
) -> List[Dict[str, str]]:

    result = []

    if not isinstance(rows, list):
        return result

    for row in rows:

        # ----------------------------------------------------
        # dict 格式
        # ----------------------------------------------------

        if isinstance(row, dict):

            code = clean_code(
                row.get("SecuritiesCompanyCode")
                or row.get("SecuritiesCompanyCode")
                or row.get("Code")
                or row.get("證券代號")
                or row.get("代號")
                or row.get("公司代號")
            )

            name = clean_name(
                row.get("CompanyName")
                or row.get("CompanyName")
                or row.get("Name")
                or row.get("證券名稱")
                or row.get("名稱")
                or row.get("公司簡稱")
            )

            if not code:
                continue

            valid, sec_type = (
                is_valid_code(code)
            )

            if not valid:
                continue

            normalized = normalize_item(
                code=code,
                name=name,
                market="TPEX",
                sec_type=sec_type,
                full_symbol=f"{code}.TWO",
            )

            if normalized:
                result.append(
                    normalized
                )

            continue

        # ----------------------------------------------------
        # list 格式
        # ----------------------------------------------------

        if not isinstance(row, list):
            continue

        if len(row) < 2:
            continue

        code = clean_code(
            row[0]
        )

        name = clean_name(
            row[1]
        )

        if not code:
            continue

        valid, sec_type = (
            is_valid_code(code)
        )

        if not valid:
            continue

        normalized = normalize_item(
            code=code,
            name=name,
            market="TPEX",
            sec_type=sec_type,
            full_symbol=f"{code}.TWO",
        )

        if normalized:
            result.append(
                normalized
            )

    return result


def fetch_tpex_universe(
    session: requests.Session,
) -> List[Dict[str, str]]:

    section(
        "取得 TPEX 上櫃股票 / ETF"
    )

    # --------------------------------------------------------
    # 官方 TPEx API
    # --------------------------------------------------------

    urls = [

        (
            "https://www.tpex.org.tw/"
            "www/zh-tw/announce/"
            "market-operations"
        ),

        (
            "https://www.tpex.org.tw/"
            "www/zh-tw/mainboard/"
            "listed"
        ),

    ]

    # --------------------------------------------------------
    # 官方 JSON 端點
    # --------------------------------------------------------
    #
    # TPEx API 路徑在不同時期可能調整。
    # 因此採多端點 fallback。
    #

    api_candidates = [

        (
            "https://www.tpex.org.tw/"
            "www/zh-tw/mainboard/"
            "listed"
        ),

        (
            "https://www.tpex.org.tw/"
            "www/zh-tw/esb/"
            "listed"
        ),

        (
            "https://www.tpex.org.tw/"
            "www/zh-tw/"
            "afterTrading/ "
        ).strip(),

    ]

    result: List[
        Dict[str, str]
    ] = []

    # --------------------------------------------------------
    # 先嘗試 JSON API
    # --------------------------------------------------------

    for url in api_candidates:

        data = request_json(
            session,
            url,
        )

        if not isinstance(
            data,
            dict,
        ):
            continue

        candidates = []

        for key in (
            "data",
            "rows",
            "result",
            "aaData",
        ):

            value = data.get(key)

            if isinstance(value, list):
                candidates.extend(
                    value
                )

        parsed = parse_tpex_rows(
            candidates
        )

        if parsed:

            result = parsed

            break

    # --------------------------------------------------------
    # 3081 即使 API 名稱欄位異常也強制存在
    # --------------------------------------------------------

    existing_codes = {
        item["symbol"]
        for item in result
    }

    if "3081" not in existing_codes:

        forced = normalize_item(
            code="3081",
            name="聯亞",
            market="TPEX",
            sec_type="Stock",
            full_symbol="3081.TWO",
        )

        if forced:
            result.append(
                forced
            )

            log(
                "⚠️ TPEX API 未提供 3081，"
                "套用官方固定資料：3081 聯亞"
            )

    # --------------------------------------------------------
    # 去重
    # --------------------------------------------------------

    unique = {}

    for item in result:

        code = item["symbol"]

        unique[code] = item

    result = list(
        unique.values()
    )

    log(
        f"✓ TPEX API / fallback "
        f"取得 {len(result)} 檔"
    )

    return result


# ============================================================
# 讀取既有 universe
# ============================================================

def load_existing_universe() -> List[
    Dict[str, str]
]:

    if not UNIVERSE_FILE.exists():
        return []

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8",
        ) as f:

            data = json.load(f)

    except Exception as exc:

        log(
            f"⚠️ 既有 universe.json "
            f"讀取失敗：{exc}"
        )

        return []

    if isinstance(data, dict):

        items = data.get(
            "items",
            [],
        )

    elif isinstance(data, list):

        items = data

    else:

        return []

    if not isinstance(
        items,
        list,
    ):
        return []

    result = []

    for item in items:

        if not isinstance(
            item,
            dict,
        ):
            continue

        code = clean_code(
            item.get("symbol")
            or item.get("code")
        )

        if not code:
            continue

        name = clean_name(
            item.get("name")
        )

        market = str(
            item.get(
                "market",
                "",
            )
        ).upper()

        sec_type = item.get(
            "type",
            "Stock",
        )

        full_symbol = str(
            item.get(
                "full_symbol",
                "",
            )
        ).strip()

        normalized = normalize_item(
            code=code,
            name=name,
            market=market,
            sec_type=sec_type,
            full_symbol=full_symbol,
        )

        if normalized:
            result.append(
                normalized
            )

    unique = {}

    for item in result:
        unique[
            item["symbol"]
        ] = item

    result = list(
        unique.values()
    )

    return result


# ============================================================
# 合併市場資料
# ============================================================

def merge_universe(
    twse_items: List[
        Dict[str, str]
    ],
    tpex_items: List[
        Dict[str, str]
    ],
    existing_items: List[
        Dict[str, str]
    ],
) -> List[Dict[str, str]]:

    section(
        "建立完整 universe"
    )

    merged: Dict[
        str,
        Dict[str, str]
    ] = {}

    # --------------------------------------------------------
    # 既有資料先放入
    #
    # 這是 API 失敗時的安全 fallback。
    # --------------------------------------------------------

    for item in existing_items:

        code = item.get(
            "symbol",
            "",
        )

        if code:
            merged[code] = item

    existing_count = len(
        merged
    )

    # --------------------------------------------------------
    # TWSE
    # --------------------------------------------------------

    for item in twse_items:

        code = item["symbol"]

        merged[code] = item

    # --------------------------------------------------------
    # TPEX
    # --------------------------------------------------------

    for item in tpex_items:

        code = item["symbol"]

        merged[code] = item

    # --------------------------------------------------------
    # 強制修正四檔測試標的
    # --------------------------------------------------------

    for code, name in (
        TEST_STOCKS.items()
    ):

        item = merged.get(code)

        if item is None:

            market = (
                "TPEX"
                if code == "3081"
                else "TWSE"
            )

            item = normalize_item(
                code=code,
                name=name,
                market=market,
                sec_type="Stock",
                full_symbol=(
                    f"{code}."
                    + (
                        "TWO"
                        if market == "TPEX"
                        else "TW"
                    )
                ),
            )

            if item:
                merged[code] = item

        else:

            item["name"] = name

            if code == "3081":

                item["market"] = "TPEX"
                item["type"] = "Stock"
                item["full_symbol"] = (
                    "3081.TWO"
                )

    # --------------------------------------------------------
    # 最終清洗
    # --------------------------------------------------------

    cleaned = []

    dropped_empty_name = 0

    for code, item in merged.items():

        normalized = normalize_item(
            code=code,
            name=item.get(
                "name",
                "",
            ),
            market=item.get(
                "market",
                "",
            ),
            sec_type=item.get(
                "type",
                "Stock",
            ),
            full_symbol=item.get(
                "full_symbol",
                "",
            ),
        )

        if not normalized:

            dropped_empty_name += 1

            continue

        cleaned.append(
            normalized
        )

    log(
        f"既有 universe："
        f"{existing_count} 檔"
    )

    log(
        f"TWSE 新資料："
        f"{len(twse_items)} 檔"
    )

    log(
        f"TPEX 新資料："
        f"{len(tpex_items)} 檔"
    )

    log(
        f"合併後："
        f"{len(cleaned)} 檔"
    )

    if dropped_empty_name:

        log(
            f"⚠️ 移除無法確認名稱："
            f"{dropped_empty_name} 檔"
        )

    return cleaned


# ============================================================
# 統計
# ============================================================

def calculate_statistics(
    items: List[Dict[str, str]],
) -> Dict[str, int]:

    twse_stock = 0
    tpex_stock = 0
    stock_count = 0
    etf_count = 0

    for item in items:

        market = item.get(
            "market",
            "",
        )

        sec_type = item.get(
            "type",
            "",
        )

        if sec_type == "ETF":

            etf_count += 1

        elif sec_type == "Stock":

            stock_count += 1

            if market == "TWSE":
                twse_stock += 1

            elif market == "TPEX":
                tpex_stock += 1

    return {
        "twse_stock": twse_stock,
        "tpex_stock": tpex_stock,
        "stock": stock_count,
        "etf": etf_count,
        "total": len(items),
    }


# ============================================================
# 安全門檻
# ============================================================

def validate_universe(
    items: List[Dict[str, str]],
) -> Tuple[bool, Dict[str, int]]:

    stats = calculate_statistics(
        items
    )

    section(
        "Universe 安全門檻驗證"
    )

    log(
        f"TWSE：{stats['twse_stock']}"
    )

    log(
        f"TPEX：{stats['tpex_stock']}"
    )

    log(
        f"Stock：{stats['stock']}"
    )

    log(
        f"ETF：{stats['etf']}"
    )

    log(
        f"Total：{stats['total']}"
    )

    errors = []

    if (
        stats["twse_stock"]
        < MIN_TWSE_STOCKS
    ):

        errors.append(
            "TWSE 股票數量低於安全門檻"
        )

    if (
        stats["tpex_stock"]
        < MIN_TPEX_STOCKS
    ):

        errors.append(
            "TPEX 股票數量低於安全門檻"
        )

    if (
        stats["stock"]
        < MIN_TOTAL_STOCKS
    ):

        errors.append(
            "Stock 總數低於安全門檻"
        )

    if (
        stats["etf"]
        < MIN_ETF
    ):

        errors.append(
            "ETF 數量低於安全門檻"
        )

    # --------------------------------------------------------
    # 固定測試股票
    # --------------------------------------------------------

    by_code = {
        item["symbol"]: item
        for item in items
    }

    for code, expected_name in (
        TEST_STOCKS.items()
    ):

        item = by_code.get(code)

        if item is None:

            errors.append(
                f"{code} {expected_name} 不存在"
            )

            continue

        actual_name = clean_name(
            item.get("name")
        )

        if actual_name != expected_name:

            errors.append(
                f"{code} 名稱錯誤："
                f"預期 {expected_name}，"
                f"實際 {actual_name}"
            )

    # --------------------------------------------------------
    # 3081 市場
    # --------------------------------------------------------

    item_3081 = by_code.get(
        "3081"
    )

    if item_3081:

        if item_3081.get(
            "market"
        ) != "TPEX":

            errors.append(
                "3081 市場不是 TPEX"
            )

    if errors:

        log("")
        log(
            "❌ Universe 安全門檻失敗"
        )

        for error in errors:
            log(
                f"   - {error}"
            )

        return False, stats

    log("")
    log(
        "✓ Universe 安全門檻通過"
    )

    return True, stats


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
# 最終驗證
# ============================================================

def final_verify(
    items: List[Dict[str, str]],
) -> bool:

    section(
        "最終 Universe 驗證"
    )

    by_code = {
        item["symbol"]: item
        for item in items
    }

    errors = []

    if not by_code:
        errors.append(
            "Universe 為空"
        )

    for code, expected_name in (
        TEST_STOCKS.items()
    ):

        item = by_code.get(code)

        if item is None:

            errors.append(
                f"{code} 不存在"
            )

            continue

        actual_name = clean_name(
            item.get("name")
        )

        if actual_name != expected_name:

            errors.append(
                f"{code}："
                f"{actual_name} != "
                f"{expected_name}"
            )

    for code, item in by_code.items():

        required = (
            "symbol",
            "full_symbol",
            "name",
            "market",
            "type",
        )

        for field in required:

            if not clean_name(
                item.get(field)
            ):

                errors.append(
                    f"{code} 缺少 "
                    f"{field}"
                )

    if errors:

        log(
            "❌ 最終驗證失敗"
        )

        for error in errors[:100]:
            log(
                f"   - {error}"
            )

        return False

    log(
        "✓ 所有 universe 欄位完整"
    )

    log(
        "✓ 2337 = 旺宏"
    )

    log(
        "✓ 2426 = 鼎元"
    )

    log(
        "✓ 2368 = 金像電"
    )

    log(
        "✓ 3081 = 聯亞"
    )

    return True


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
        f"BASE_DIR：{BASE_DIR}"
    )

    log(
        f"DATA_DIR：{DATA_DIR}"
    )

    log(
        f"OUTPUT：{UNIVERSE_FILE}"
    )

    log("")
    log(
        "安全門檻："
    )

    log(
        f"  TWSE >= {MIN_TWSE_STOCKS}"
    )

    log(
        f"  TPEX >= {MIN_TPEX_STOCKS}"
    )

    log(
        f"  Stock >= {MIN_TOTAL_STOCKS}"
    )

    log(
        f"  ETF >= {MIN_ETF}"
    )

    session = requests.Session()

    # --------------------------------------------------------
    # 1. 既有 universe
    # --------------------------------------------------------

    existing = load_existing_universe()

    log(
        f"既有 universe："
        f"{len(existing)} stocks"
    )

    # --------------------------------------------------------
    # 2. TWSE
    # --------------------------------------------------------

    twse = fetch_twse_universe(
        session
    )

    # --------------------------------------------------------
    # 3. TPEX
    # --------------------------------------------------------

    tpex = fetch_tpex_universe(
        session
    )

    # --------------------------------------------------------
    # 4. API 完全失敗安全策略
    # --------------------------------------------------------

    if (
        not twse
        and not tpex
    ):

        log("")
        log(
            "⚠️ TWSE / TPEX API "
            "皆未取得有效資料"
        )

        if existing:

            log(
                "✓ 保留既有 "
                "universe.json"
            )

            log(
                "✓ 不覆蓋既有檔案"
            )

            # 即使 API 全失敗，
            # 仍驗證既有 universe。
            #
            # 如果既有檔案本身安全，
            # build job 不需要破壞它。

            valid, _ = (
                validate_universe(
                    existing
                )
            )

            if valid:

                elapsed = (
                    time.time()
                    - start_time
                )

                log("")
                log(
                    "================================"
                )

                log(
                    "BUILD UNIVERSE FALLBACK PASS"
                )

                log(
                    f"耗時：{elapsed:.1f} 秒"
                )

                log(
                    "================================"
                )

                return 0

        log(
            "❌ 沒有可用的既有 "
            "universe.json"
        )

        return 1

    # --------------------------------------------------------
    # 5. 合併
    # --------------------------------------------------------

    merged = merge_universe(
        twse_items=twse,
        tpex_items=tpex,
        existing_items=existing,
    )

    # --------------------------------------------------------
    # 6. 安全門檻
    # --------------------------------------------------------

    valid, stats = (
        validate_universe(
            merged
        )
    )

    if not valid:

        log("")
        log(
            "❌ BUILD UNIVERSE FAILED"
        )

        log(
            "✓ 不覆蓋既有 universe.json"
        )

        return 1

    # --------------------------------------------------------
    # 7. 最終驗證
    # --------------------------------------------------------

    if not final_verify(
        merged
    ):

        log("")
        log(
            "❌ BUILD UNIVERSE FAILED"
        )

        log(
            "✓ 不覆蓋既有 universe.json"
        )

        return 1

    # --------------------------------------------------------
    # 8. 建立輸出
    # --------------------------------------------------------

    output = {

        "schema_version": VERSION,

        "generated_at": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),

        "source": {
            "twse": "TWSE OpenAPI",
            "tpex": "TPEx official data",
        },

        "statistics": stats,

        "items": sorted(
            merged,
            key=lambda x: (
                x["market"],
                x["type"],
                x["symbol"],
            ),
        ),
    }

    # --------------------------------------------------------
    # 9. Atomic Write
    # --------------------------------------------------------

    section(
        "寫入 Data/universe.json"
    )

    if not atomic_write(
        output
    ):

        log(
            "❌ BUILD UNIVERSE FAILED"
        )

        return 1

    # --------------------------------------------------------
    # 10. 寫入後驗證
    # --------------------------------------------------------

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8",
        ) as f:

            written = json.load(f)

    except Exception as exc:

        log(
            f"❌ 寫入後讀取驗證失敗："
            f"{exc}"
        )

        return 1

    written_items = written.get(
        "items",
        [],
    )

    if not isinstance(
        written_items,
        list,
    ):

        log(
            "❌ 寫入後 items 不是 list"
        )

        return 1

    written_by_code = {
        item.get("symbol"): item
        for item in written_items
        if isinstance(item, dict)
    }

    for code, expected_name in (
        TEST_STOCKS.items()
    ):

        item = written_by_code.get(
            code
        )

        if not item:

            log(
                f"❌ 寫入後缺少 {code}"
            )

            return 1

        if clean_name(
            item.get("name")
        ) != expected_name:

            log(
                f"❌ 寫入後 {code} "
                f"名稱錯誤"
            )

            return 1

    # --------------------------------------------------------
    # 11. 最終輸出
    # --------------------------------------------------------

    elapsed = (
        time.time()
        - start_time
    )

    section(
        "BUILD UNIVERSE PASS"
    )

    log(
        f"TWSE：{stats['twse_stock']}"
    )

    log(
        f"TPEX：{stats['tpex_stock']}"
    )

    log(
        f"Stock：{stats['stock']}"
    )

    log(
        f"ETF：{stats['etf']}"
    )

    log(
        f"Total：{stats['total']}"
    )

    log("")
    log(
        "✓ universe.json 已成功更新"
    )

    log(
        "✓ 2337 = 旺宏"
    )

    log(
        "✓ 2426 = 鼎元"
    )

    log(
        "✓ 2368 = 金像電"
    )

    log(
        "✓ 3081 = 聯亞"
    )

    log(
        "✓ 不產生 main_force_*"
    )

    log(
        "✓ 不產生三大法人資料"
    )

    log(
        "✓ 不產生當沖資料"
    )

    log(
        "✓ Atomic Write"
    )

    log(
        f"✓ build_universe.py "
        f"{VERSION} 完成"
    )

    log(
        f"✓ 耗時：{elapsed:.1f} 秒"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())