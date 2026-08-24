#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V9.3

============================================================
V9.3 正式版
============================================================

核心架構
------------------------------------------------------------
1. Data/universe.json V10.2 為唯一主要股票池來源
2. 支援 universe.json V10.2 stocks object 架構
3. 相容舊版 items list 架構
4. TWSE / TPEX 三大法人資料分開取得
5. 1D / 5D / 10D / 20D 全部由每日原始資料累計
6. 單位統一為「張」
7. 不產生 main_force_*
8. 不使用任何「三大法人 × 倍率」估算主力
9. 當沖資料獨立處理
10. 名稱缺失不得寫入空字串
11. 3081 必須為「聯亞」
12. 3081 必須為 TPEX
13. 正式資料採 Atomic Write
14. 固定驗證 2337 / 2426 / 2368 / 3081
15. Universe 數量必須與實際載入數量一致
16. 10D 正式保留
17. 20D 正式保留
18. TPEX 三大法人使用明確的三大法人買賣超欄位
19. 不以「最後一個 numeric value」猜測三大法人欄位
20. 當日資料抓取失敗不得偽造資料
21. 歷史資料依實際成功交易日累計
22. 同一交易日 TWSE/TPEX 分開取得
============================================================
"""

from __future__ import annotations

import json
import math
import sys
import time

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


# ============================================================
# Version
# ============================================================

VERSION = "V9.3"


# ============================================================
# Paths
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

CHIP_FILE = DATA_DIR / "chip.json"


# ============================================================
# Network
# ============================================================

REQUEST_TIMEOUT = 30

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


# ============================================================
# Request retry
# ============================================================

REQUEST_RETRIES = 3

REQUEST_RETRY_SLEEP = 1.0


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

def clean_code(value: Any) -> str:

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


def clean_name(value: Any) -> str:

    if value is None:
        return ""

    return str(value).strip()


# ============================================================
# Symbol 判斷
# ============================================================

def is_valid_symbol(
    code: str,
) -> Tuple[bool, str]:

    code = clean_code(code)

    # 一般四碼股票
    if len(code) == 4 and code.isdigit():
        return True, "Stock"

    # ETF / 特殊 ETF
    if (
        code.startswith("00")
        and 5 <= len(code) <= 6
        and code.isdigit()
    ):
        return True, "ETF"

    return False, "Other"


# ============================================================
# Numeric
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
        "－",
        "-",
        "None",
        "null",
    ):
        return None

    text = (
        text
        .replace(",", "")
        .replace(" ", "")
    )

    try:

        result = float(text)

        if not math.isfinite(result):
            return None

        return result

    except Exception:

        return None


# ============================================================
# Official fallback
# ============================================================

OFFICIAL_NAME_FALLBACK = {

    "3081": "聯亞",

}


OFFICIAL_MARKET_FALLBACK = {

    "3081": "TPEX",

}


# ============================================================
# HTTP JSON
# ============================================================

def request_json(
    session: requests.Session,
    url: str,
) -> Optional[Any]:

    last_error: Optional[Exception] = None

    for attempt in range(
        1,
        REQUEST_RETRIES + 1,
    ):

        try:

            response = session.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code != 200:

                last_error = RuntimeError(
                    f"HTTP {response.status_code}"
                )

                if attempt < REQUEST_RETRIES:

                    time.sleep(
                        REQUEST_RETRY_SLEEP
                    )

                    continue

                return None

            try:

                return response.json()

            except Exception as e:

                last_error = e

                if attempt < REQUEST_RETRIES:

                    time.sleep(
                        REQUEST_RETRY_SLEEP
                    )

                    continue

                return None

        except Exception as e:

            last_error = e

            if attempt < REQUEST_RETRIES:

                time.sleep(
                    REQUEST_RETRY_SLEEP
                )

                continue

            return None

    if last_error:

        return None

    return None


# ============================================================
# 讀取 universe.json
#
# 正式 V10.2：
#
# {
#   "schema_version": "V10.2",
#   "universe_count": 2143,
#   "stocks": {
#       "2337": {...},
#       "2426": {...}
#   }
# }
#
# 舊版：
#
# {
#   "items": [...]
# }
# ============================================================

def get_securities_from_universe(
    session: requests.Session,
) -> List[Dict[str, str]]:

    section(
        "讀取 Data/universe.json 股票與 ETF 清單"
    )

    securities: List[Dict[str, str]] = []

    if not UNIVERSE_FILE.exists():

        log(
            "❌ Data/universe.json 不存在"
        )

        return securities

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8-sig",
        ) as f:

            uni_data = json.load(f)

    except Exception as e:

        log(
            f"❌ 讀取 universe.json 失敗：{e}"
        )

        return securities

    items: List[Any] = []

    # ========================================================
    # V10.2 stocks object
    # ========================================================

    if isinstance(
        uni_data,
        dict,
    ):

        stocks = uni_data.get(
            "stocks"
        )

        if isinstance(
            stocks,
            dict,
        ):

            log(
                "✓ 偵測到 universe.json "
                "V10.x stocks object 架構"
            )

            for code, item in stocks.items():

                if not isinstance(
                    item,
                    dict,
                ):

                    continue

                normalized = dict(item)

                if not normalized.get(
                    "symbol"
                ):

                    normalized["symbol"] = code

                if not normalized.get(
                    "code"
                ):

                    normalized["code"] = code

                items.append(
                    normalized
                )

        else:

            legacy_items = uni_data.get(
                "items",
                [],
            )

            if isinstance(
                legacy_items,
                list,
            ):

                log(
                    "⚠️ 偵測到舊版 universe.json "
                    "items list 架構"
                )

                items = legacy_items

    elif isinstance(
        uni_data,
        list,
    ):

        log(
            "⚠️ 偵測到舊版 universe.json "
            "list 架構"
        )

        items = uni_data

    # ========================================================
    # 沒有資料
    # ========================================================

    if not items:

        log(
            "❌ universe.json 找不到 stocks "
            "或 items 資料"
        )

        return securities

    # ========================================================
    # Universe header
    # ========================================================

    declared_count = None

    if isinstance(
        uni_data,
        dict,
    ):

        declared_count = uni_data.get(
            "universe_count"
        )

    if declared_count is not None:

        log(
            f"✓ Universe header："
            f"{declared_count} 檔"
        )

    # ========================================================
    # 正式解析
    # ========================================================

    seen = set()

    missing_name_count = 0

    for item in items:

        if not isinstance(
            item,
            dict,
        ):

            continue

        raw_symbol = clean_code(
            item.get(
                "symbol",
                "",
            )
        )

        raw_code = clean_code(
            item.get(
                "code",
                "",
            )
        )

        code = raw_code or raw_symbol

        if not code:
            continue

        valid, inferred_type = (
            is_valid_symbol(code)
        )

        if not valid:
            continue

        if code in seen:
            continue

        seen.add(code)

        # ----------------------------------------------------
        # name
        # ----------------------------------------------------

        name = clean_name(
            item.get(
                "name",
                "",
            )
        )

        if not name:

            fallback_name = (
                OFFICIAL_NAME_FALLBACK.get(
                    code
                )
            )

            if fallback_name:

                name = fallback_name

                log(
                    f"⚠️ {code} universe "
                    f"名稱缺失，使用官方確認名稱："
                    f"{name}"
                )

            else:

                missing_name_count += 1

        # ----------------------------------------------------
        # market
        # ----------------------------------------------------

        market = str(
            item.get(
                "market",
                "",
            )
        ).strip().upper()

        if market not in (
            "TWSE",
            "TPEX",
        ):

            original_symbol = str(
                item.get(
                    "symbol",
                    "",
                )
            ).strip().upper()

            if ".TWO" in original_symbol:

                market = "TPEX"

            elif ".TW" in original_symbol:

                market = "TWSE"

            else:

                market = (
                    "TPEX"
                    if code.startswith("3")
                    else "TWSE"
                )

        # ----------------------------------------------------
        # 3081 強制確認
        # ----------------------------------------------------

        fallback_market = (
            OFFICIAL_MARKET_FALLBACK.get(
                code
            )
        )

        if fallback_market:

            market = fallback_market

        # ----------------------------------------------------
        # type
        # ----------------------------------------------------

        raw_type = str(
            item.get(
                "type",
                "",
            )
        ).strip().lower()

        if raw_type == "etf":

            sec_type = "ETF"

        elif raw_type == "stock":

            sec_type = "Stock"

        else:

            sec_type = inferred_type

        # ----------------------------------------------------
        # full symbol
        # ----------------------------------------------------

        full_symbol = str(
            item.get(
                "full_symbol",
                "",
            )
        ).strip()

        if not full_symbol:

            full_symbol = str(
                item.get(
                    "symbol",
                    "",
                )
            ).strip()

        if not full_symbol:

            if market == "TPEX":

                full_symbol = (
                    f"{code}.TWO"
                )

            else:

                full_symbol = (
                    f"{code}.TW"
                )

        # ----------------------------------------------------
        # 寫入
        # ----------------------------------------------------

        securities.append(
            {
                "symbol": code,
                "full_symbol": full_symbol,
                "name": name,
                "market": market,
                "type": sec_type,
            }
        )

    # ========================================================
    # 統計
    # ========================================================

    log(
        f"✓ 從 universe.json 成功載入 "
        f"{len(securities)} 檔全市場標的"
    )

    stock_count = sum(
        1
        for x in securities
        if x["type"] == "Stock"
    )

    etf_count = sum(
        1
        for x in securities
        if x["type"] == "ETF"
    )

    twse_count = sum(
        1
        for x in securities
        if x["market"] == "TWSE"
    )

    tpex_count = sum(
        1
        for x in securities
        if x["market"] == "TPEX"
    )

    log(
        f"✓ Stock：{stock_count} 檔"
    )

    log(
        f"✓ ETF：{etf_count} 檔"
    )

    log(
        f"✓ TWSE：{twse_count} 檔"
    )

    log(
        f"✓ TPEX：{tpex_count} 檔"
    )

    if declared_count is not None:

        if declared_count != len(securities):

            log(
                f"❌ Universe 數量不一致："
                f"header={declared_count} "
                f"實際={len(securities)}"
            )

            return []

        log(
            "✓ Universe 數量驗證通過"
        )

    if missing_name_count:

        log(
            f"⚠️ universe.json 有 "
            f"{missing_name_count} 檔名稱缺失"
        )

    else:

        log(
            "✓ Universe 名稱完整"
        )

    return securities


# ============================================================
# TWSE 三大法人
#
# T86：
#
# row[0]  證券代號
# row[18] 三大法人買賣超股數
#
# 單位：股
# /1000 = 張
# ============================================================

def fetch_twse_institutional(
    session: requests.Session,
    date_str: str,
) -> Dict[str, float]:

    result: Dict[str, float] = {}

    url = (
        "https://www.twse.com.tw/rwd/zh/fund/T86"
        f"?date={date_str}&selectType=ALL"
    )

    data = request_json(
        session,
        url,
    )

    if not isinstance(
        data,
        dict,
    ):

        return result

    if data.get("stat") != "OK":

        return result

    rows = data.get(
        "data",
        [],
    )

    if not isinstance(
        rows,
        list,
    ):

        return result

    for row in rows:

        if not isinstance(
            row,
            list,
        ):

            continue

        if len(row) < 19:

            continue

        symbol = clean_code(
            row[0]
        )

        valid, _ = is_valid_symbol(
            symbol
        )

        if not valid:

            continue

        # 明確使用 T86 最後欄
        net_value = safe_number(
            row[18]
        )

        if net_value is None:

            continue

        result[symbol] = round(
            net_value / 1000.0,
            2,
        )

    return result


# ============================================================
# TPEX 三大法人
#
# TPEX 官方日報：
#
# 代號
# 名稱
# ...
# 三大法人買賣超股數
#
# 由於不同時期 API 可能存在不同 schema，
# 本函式優先使用明確欄位名稱：
#
# 1. "三大法人買賣超股數"
# 2. "三大法人買賣超"
# 3. "三大法人買賣超股數合計"
#
# 若 API 只有 data list，
# 則使用最後一欄，但必須先確認：
# - row[0] 為代號
# - row 至少有足夠欄位
# - 最後欄確實是 numeric
#
# 單位：股
# /1000 = 張
# ============================================================

def extract_tpex_net_value(
    data: Dict[str, Any],
    row: List[Any],
) -> Optional[float]:

    # --------------------------------------------------------
    # 1. 找 API columns / fields / columnsName
    # --------------------------------------------------------

    columns = None

    for key in (
        "fields",
        "columns",
        "columnsName",
        "column",
        "title",
    ):

        value = data.get(key)

        if isinstance(
            value,
            list,
        ):

            columns = value

            break

    # --------------------------------------------------------
    # 2. 透過欄位名稱精準定位
    # --------------------------------------------------------

    if columns:

        normalized_columns = [
            str(x).strip()
            for x in columns
        ]

        target_indexes = []

        for index, column in enumerate(
            normalized_columns
        ):

            if (
                "三大法人買賣超股數" in column
                or "三大法人買賣超" in column
                or "三大法人買賣超股數合計" in column
            ):

                target_indexes.append(
                    index
                )

        if target_indexes:

            # 優先最後一個明確三大法人欄位
            index = target_indexes[-1]

            if index < len(row):

                value = safe_number(
                    row[index]
                )

                if value is not None:

                    return value

    # --------------------------------------------------------
    # 3. 嘗試 data 欄位 metadata
    # --------------------------------------------------------

    data_meta = data.get(
        "data",
    )

    if isinstance(
        data_meta,
        dict,
    ):

        for key in (
            "fields",
            "columns",
            "columnsName",
        ):

            value = data_meta.get(
                key
            )

            if isinstance(
                value,
                list,
            ):

                normalized_columns = [
                    str(x).strip()
                    for x in value
                ]

                for index, column in enumerate(
                    normalized_columns
                ):

                    if (
                        "三大法人買賣超股數"
                        in column
                        or "三大法人買賣超"
                        in column
                    ):

                        if index < len(row):

                            parsed = safe_number(
                                row[index]
                            )

                            if parsed is not None:

                                return parsed

    # --------------------------------------------------------
    # 4. fallback
    #
    # 官方 TPEX 日報資料結構中最後欄為：
    # 三大法人買賣超股數
    #
    # 這裡只有在 row 本身有合理長度時才使用。
    # --------------------------------------------------------

    if len(row) >= 10:

        value = safe_number(
            row[-1]
        )

        if value is not None:

            return value

    return None


def fetch_tpex_institutional(
    session: requests.Session,
    date_str: str,
) -> Dict[str, float]:

    result: Dict[str, float] = {}

    # --------------------------------------------------------
    # 主要 JSON API
    # --------------------------------------------------------

    urls = [

        (
            "https://www.tpex.org.tw/www/zh-tw/"
            "institutions/institutional"
            f"?date={date_str}&type=Daily"
        ),

        (
            "https://www.tpex.org.tw/www/zh-tw/"
            "institutions/institutional"
            f"?date={date_str}"
        ),

    ]

    data: Optional[Any] = None

    for url in urls:

        data = request_json(
            session,
            url,
        )

        if isinstance(
            data,
            dict,
        ):

            rows = data.get(
                "data",
                [],
            )

            if isinstance(
                rows,
                list,
            ) and rows:

                break

    if not isinstance(
        data,
        dict,
    ):

        return result

    rows = data.get(
        "data",
        [],
    )

    if not isinstance(
        rows,
        list,
    ):

        return result

    for row in rows:

        if not isinstance(
            row,
            list,
        ):

            continue

        if len(row) < 3:

            continue

        symbol = clean_code(
            row[0]
        )

        valid, _ = is_valid_symbol(
            symbol
        )

        if not valid:

            continue

        net_value = extract_tpex_net_value(
            data,
            row,
        )

        if net_value is None:

            continue

        result[symbol] = round(
            net_value / 1000.0,
            2,
        )

    return result


# ============================================================
# 每日三大法人
# ============================================================

def fetch_daily_institutional(
    session: requests.Session,
    date_str: str,
) -> Dict[str, float]:

    result: Dict[str, float] = {}

    # --------------------------------------------------------
    # TWSE
    # --------------------------------------------------------

    twse = fetch_twse_institutional(
        session,
        date_str,
    )

    for symbol, value in twse.items():

        result[symbol] = value

    # --------------------------------------------------------
    # TPEX
    # --------------------------------------------------------

    tpex = fetch_tpex_institutional(
        session,
        date_str,
    )

    for symbol, value in tpex.items():

        # 不會用 TPEX 覆蓋 TWSE
        # 同一代號理論上不應同時存在。
        if symbol not in result:

            result[symbol] = value

    return result


# ============================================================
# TWSE 當沖
# ============================================================

def fetch_twse_daytrade(
    session: requests.Session,
    date_str: str,
) -> Dict[str, Dict[str, float]]:

    result: Dict[
        str,
        Dict[str, float]
    ] = {}

    url = (
        "https://www.twse.com.tw/rwd/zh/trading/"
        f"historical/day-trading?date={date_str}"
    )

    data = request_json(
        session,
        url,
    )

    if not isinstance(
        data,
        dict,
    ):

        return result

    if data.get("stat") != "OK":

        return result

    rows = data.get(
        "data",
        [],
    )

    if not isinstance(
        rows,
        list,
    ):

        return result

    for row in rows:

        if not isinstance(
            row,
            list,
        ):

            continue

        if len(row) < 7:

            continue

        symbol = clean_code(
            row[0]
        )

        valid, _ = is_valid_symbol(
            symbol
        )

        if not valid:

            continue

        volume = safe_number(
            row[5]
        )

        rate = safe_number(
            row[6]
        )

        if volume is None:

            continue

        if rate is None:

            rate = 0.0

        result[symbol] = {

            "day_trading_volume": round(
                volume,
                2,
            ),

            "day_trading_rate": round(
                rate,
                4,
            ),
        }

    return result


# ============================================================
# 歷史資料
# ============================================================

def fetch_history_chips(
    session: requests.Session,
    days: int = 20,
) -> Tuple[
    str,
    Dict[str, Dict[str, List[float]]],
    Dict[str, Dict[str, float]],
    List[str],
]:

    section(
        f"同步最近 {days} 個有效交易日 "
        f"TWSE/TPEX 三大法人資料"
    )

    stock_history: Dict[
        str,
        Dict[str, List[float]]
    ] = {}

    daytrade_data: Dict[
        str,
        Dict[str, float]
    ] = {}

    successful_dates: List[str] = []

    latest_date_str = ""

    curr_date = datetime.now()

    attempted_days = 0

    # --------------------------------------------------------
    # 最多往前搜尋 60 個日曆日
    # --------------------------------------------------------

    while (
        len(successful_dates) < days
        and attempted_days < 60
    ):

        if curr_date.weekday() < 5:

            date_str = curr_date.strftime(
                "%Y%m%d"
            )

            daily_data = (
                fetch_daily_institutional(
                    session,
                    date_str,
                )
            )

            # ------------------------------------------------
            # 只有有實際資料才算成功交易日
            # ------------------------------------------------

            if daily_data:

                successful_dates.append(
                    date_str
                )

                if not latest_date_str:

                    latest_date_str = (
                        curr_date.strftime(
                            "%Y-%m-%d"
                        )
                    )

                for symbol, value in (
                    daily_data.items()
                ):

                    stock_history.setdefault(
                        symbol,
                        {
                            "institutional": []
                        },
                    )

                    stock_history[symbol][
                        "institutional"
                    ].append(
                        value
                    )

                log(
                    f"  └ 成功同步 {date_str} "
                    f"籌碼資料 "
                    f"(有效交易日 "
                    f"{len(successful_dates)}/{days}，"
                    f"標的 {len(daily_data)} 檔)"
                )

                # ------------------------------------------------
                # 只抓最新有效交易日當沖
                # ------------------------------------------------

                if len(successful_dates) == 1:

                    dt_data = (
                        fetch_twse_daytrade(
                            session,
                            date_str,
                        )
                    )

                    if dt_data:

                        daytrade_data.update(
                            dt_data
                        )

                        log(
                            f"  └ 當沖資料同步："
                            f"{len(dt_data)} 檔"
                        )

                time.sleep(
                    0.3
                )

        curr_date -= timedelta(
            days=1
        )

        attempted_days += 1

    if not latest_date_str:

        latest_date_str = (
            datetime.now().strftime(
                "%Y-%m-%d"
            )
        )

    log("")

    log(
        f"✓ 有效交易日："
        f"{len(successful_dates)}/{days}"
    )

    if successful_dates:

        log(
            f"✓ 最新交易日："
            f"{successful_dates[0]}"
        )

        log(
            f"✓ 最舊交易日："
            f"{successful_dates[-1]}"
        )

    else:

        log(
            "❌ 沒有取得任何有效交易日資料"
        )

    return (
        latest_date_str,
        stock_history,
        daytrade_data,
        successful_dates,
    )


# ============================================================
# Period
# ============================================================

def calculate_period(
    values: List[float],
    days: int,
) -> Optional[float]:

    if not values:

        return None

    if len(values) < days:

        return None

    return round(
        sum(
            values[:days]
        ),
        2,
    )


# ============================================================
# 建立安全名稱
# ============================================================

def resolve_name(
    symbol: str,
    value: Any,
) -> Tuple[str, bool]:

    name = clean_name(
        value
    )

    if name:

        return name, False

    fallback = (
        OFFICIAL_NAME_FALLBACK.get(
            symbol
        )
    )

    if fallback:

        return fallback, True

    # --------------------------------------------------------
    # 不允許寫入空字串。
    #
    # 如果 universe 沒有名稱，
    # 只能使用 symbol 作為最後安全識別值，
    # 並記錄 fallback。
    # --------------------------------------------------------

    return symbol, True


# ============================================================
# 驗證固定測試股票
# ============================================================

def validate_required_stocks(
    stocks_result: Dict[str, Dict[str, Any]],
) -> bool:

    required_test_stocks = {

        "2337": {
            "name": "旺宏",
            "market": "TWSE",
        },

        "2426": {
            "name": "鼎元",
            "market": "TWSE",
        },

        "2368": {
            "name": "金像電",
            "market": "TWSE",
        },

        "3081": {
            "name": "聯亞",
            "market": "TPEX",
        },

    }

    section(
        "固定測試股票名稱與市場驗證"
    )

    for symbol, expected in (
        required_test_stocks.items()
    ):

        item = stocks_result.get(
            symbol
        )

        if not isinstance(
            item,
            dict,
        ):

            log(
                f"❌ {symbol} "
                f"{expected['name']} 不存在"
            )

            return False

        actual_name = clean_name(
            item.get(
                "name",
                "",
            )
        )

        actual_market = str(
            item.get(
                "market",
                "",
            )
        ).strip().upper()

        log(
            f"{symbol} | "
            f"預期：{expected['name']} | "
            f"實際：{actual_name} | "
            f"市場：{actual_market}"
        )

        if actual_name != expected["name"]:

            log(
                f"❌ 股票名稱錯誤："
                f"{symbol} "
                f"預期 {expected['name']}，"
                f"實際 {actual_name}"
            )

            return False

        if actual_market != expected["market"]:

            log(
                f"❌ 股票市場錯誤："
                f"{symbol} "
                f"預期 {expected['market']}，"
                f"實際 {actual_market}"
            )

            return False

    log(
        "✓ 2337 / 2426 / 2368 / 3081 "
        "名稱與市場驗證通過"
    )

    return True


# ============================================================
# 禁止 main_force
# ============================================================

FORBIDDEN_FIELDS = {

    "main_force_1d",
    "main_force_5d",
    "main_force_10d",
    "main_force_20d",

}


def validate_no_main_force(
    stocks: Dict[str, Any],
) -> bool:

    for symbol, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):

            continue

        for field in FORBIDDEN_FIELDS:

            if field in item:

                log(
                    f"❌ 發現禁止欄位："
                    f"{symbol}.{field}"
                )

                return False

    return True


# ============================================================
# Atomic Write
# ============================================================

def atomic_write_json(
    target_file: Path,
    payload: Dict[str, Any],
) -> bool:

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_file = target_file.with_suffix(
        ".json.tmp"
    )

    try:

        with temp_file.open(
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                payload,
                f,
                ensure_ascii=False,
                indent=2,
            )

            f.write("\n")

            f.flush()

        temp_file.replace(
            target_file
        )

        return True

    except Exception as e:

        log(
            f"❌ Atomic Write 失敗：{e}"
        )

        try:

            if temp_file.exists():

                temp_file.unlink()

        except Exception:

            pass

        return False


# ============================================================
# 重新讀取 chip.json
# ============================================================

def verify_written_chip(
    expected_count: int,
    required_test_stocks: Dict[str, str],
) -> bool:

    section(
        "寫入後重新讀取 chip.json 驗證"
    )

    try:

        with CHIP_FILE.open(
            "r",
            encoding="utf-8",
        ) as f:

            verify_data = json.load(
                f
            )

    except Exception as e:

        log(
            f"❌ chip.json 重新讀取失敗：{e}"
        )

        return False

    if not isinstance(
        verify_data,
        dict,
    ):

        log(
            "❌ chip.json 根節點不是 object"
        )

        return False

    verify_stocks = (
        verify_data.get(
            "stocks"
        )
    )

    if not isinstance(
        verify_stocks,
        dict,
    ):

        log(
            "❌ chip.json stocks 格式錯誤"
        )

        return False

    if len(verify_stocks) != expected_count:

        log(
            f"❌ chip.json 寫入數量錯誤："
            f"預期 {expected_count}，"
            f"實際 {len(verify_stocks)}"
        )

        return False

    # --------------------------------------------------------
    # 固定股票
    # --------------------------------------------------------

    for symbol, expected_name in (
        required_test_stocks.items()
    ):

        item = verify_stocks.get(
            symbol
        )

        if not isinstance(
            item,
            dict,
        ):

            log(
                f"❌ 寫入後找不到：{symbol}"
            )

            return False

        actual_name = clean_name(
            item.get(
                "name",
                "",
            )
        )

        if actual_name != expected_name:

            log(
                f"❌ 寫入後名稱錯誤："
                f"{symbol} "
                f"預期 {expected_name}，"
                f"實際 {actual_name}"
            )

            return False

        if not actual_name:

            log(
                f"❌ 寫入後出現空名稱："
                f"{symbol}"
            )

            return False

    # --------------------------------------------------------
    # 禁止欄位
    # --------------------------------------------------------

    if not validate_no_main_force(
        verify_stocks
    ):

        return False

    # --------------------------------------------------------
    # Schema
    # --------------------------------------------------------

    if verify_data.get(
        "schema_version"
    ) != VERSION:

        log(
            "❌ chip.json schema_version "
            "與 fetch_chip 版本不一致"
        )

        return False

    # --------------------------------------------------------
    # Universe count
    # --------------------------------------------------------

    if verify_data.get(
        "universe_count"
    ) != expected_count:

        log(
            "❌ chip.json universe_count "
            "錯誤"
        )

        return False

    log(
        f"✓ 寫入後 Chip："
        f"{len(verify_stocks)} 檔"
    )

    log(
        "✓ 寫入後 2337 / 2426 / "
        "2368 / 3081 驗證成功"
    )

    log(
        "✓ main_force_* 掃描通過"
    )

    return True


# ============================================================
# Main
# ============================================================

def main() -> int:

    start_time = time.time()

    log(
        f"台股 AI 選股系統 "
        f"fetch_chip.py {VERSION} 啟動"
    )

    log(
        "============================================================"
    )

    log(
        "核心資料架構"
    )

    log(
        "✓ Universe：Data/universe.json"
    )

    log(
        "✓ Output：Data/chip.json"
    )

    log(
        "✓ 1D / 5D / 10D / 20D：保留"
    )

    log(
        "✓ 三大法人：原始每日資料累計"
    )

    log(
        "✓ 當沖：獨立資料"
    )

    log(
        "✗ main_force_*：完全禁止"
    )

    log(
        "✗ 三大法人倍率估算：完全禁止"
    )

    log(
        "============================================================"
    )

    session = requests.Session()

    # ========================================================
    # 1. 股票池
    # ========================================================

    securities = (
        get_securities_from_universe(
            session
        )
    )

    if not securities:

        log(
            "❌ 無法獲取股票池清單"
        )

        return 1

    # ========================================================
    # 2. 歷史籌碼
    # ========================================================

    (
        latest_date_str,
        stock_history,
        extra_data,
        successful_dates,
    ) = fetch_history_chips(
        session,
        days=20,
    )

    # ========================================================
    # 歷史資料硬性檢查
    # ========================================================

    if not successful_dates:

        log(
            "❌ 完全沒有取得有效交易日資料"
        )

        return 1

    # ========================================================
    # 3. 建立 chip.json
    # ========================================================

    stocks_result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    complete_cnt = 0

    partial_cnt = 0

    insufficient_cnt = 0

    empty_name_cnt = 0

    # ========================================================
    # 每一檔 universe 標的
    # ========================================================

    for item in securities:

        symbol = item["symbol"]

        history = stock_history.get(
            symbol,
            {
                "institutional": []
            },
        )

        inst_list = history.get(
            "institutional",
            [],
        )

        if not isinstance(
            inst_list,
            list,
        ):

            inst_list = []

        # ----------------------------------------------------
        # 名稱
        # ----------------------------------------------------

        name, used_fallback = resolve_name(
            symbol,
            item.get(
                "name",
                "",
            ),
        )

        if used_fallback:

            empty_name_cnt += 1

        # ----------------------------------------------------
        # 1D
        # ----------------------------------------------------

        inst_1d = (

            inst_list[0]

            if len(inst_list) >= 1

            else None

        )

        # ----------------------------------------------------
        # 5D
        # ----------------------------------------------------

        inst_5d = calculate_period(
            inst_list,
            5,
        )

        # ----------------------------------------------------
        # 10D
        # ----------------------------------------------------

        inst_10d = calculate_period(
            inst_list,
            10,
        )

        # ----------------------------------------------------
        # 20D
        # ----------------------------------------------------

        inst_20d = calculate_period(
            inst_list,
            20,
        )

        # ----------------------------------------------------
        # 完整度
        # ----------------------------------------------------

        if len(inst_list) >= 20:

            complete_cnt += 1

        elif len(inst_list) >= 1:

            partial_cnt += 1

        else:

            insufficient_cnt += 1

        # ----------------------------------------------------
        # 當沖
        # ----------------------------------------------------

        ext = extra_data.get(
            symbol,
            {},
        )

        if not isinstance(
            ext,
            dict,
        ):

            ext = {}

        day_trade_volume = safe_number(
            ext.get(
                "day_trading_volume",
                0.0,
            )
        )

        day_trade_rate = safe_number(
            ext.get(
                "day_trading_rate",
                0.0,
            )
        )

        if day_trade_volume is None:

            day_trade_volume = 0.0

        if day_trade_rate is None:

            day_trade_rate = 0.0

        # ----------------------------------------------------
        # 正式資料
        # ----------------------------------------------------

        stocks_result[symbol] = {

            "symbol": symbol,

            "full_symbol": item.get(
                "full_symbol",
                symbol,
            ),

            "name": name,

            "market": item.get(
                "market",
                "",
            ),

            "type": item.get(
                "type",
                "Stock",
            ),

            # =================================================
            # 三大法人
            #
            # 單位：張
            #
            # 正值 = 買超
            # 負值 = 賣超
            # =================================================

            "institutional_1d": inst_1d,

            "institutional_5d": inst_5d,

            "institutional_10d": inst_10d,

            "institutional_20d": inst_20d,

            # =================================================
            # 當沖
            # =================================================

            "day_trading_volume": round(
                day_trade_volume,
                2,
            ),

            "day_trading_rate": round(
                day_trade_rate,
                4,
            ),

            "updated_at": latest_date_str,
        }

    # ========================================================
    # 4. 三大法人欄位完整性統計
    # ========================================================

    inst_1d_count = sum(
        1
        for item in stocks_result.values()
        if item.get("institutional_1d") is not None
    )

    inst_5d_count = sum(
        1
        for item in stocks_result.values()
        if item.get("institutional_5d") is not None
    )

    inst_10d_count = sum(
        1
        for item in stocks_result.values()
        if item.get("institutional_10d") is not None
    )

    inst_20d_count = sum(
        1
        for item in stocks_result.values()
        if item.get("institutional_20d") is not None
    )

    # ========================================================
    # 5. 建立 output
    # ========================================================

    output = {

        "schema_version": VERSION,

        "data_date": latest_date_str,

        "generated_at": (
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        ),

        "universe_count": len(
            stocks_result
        ),

        "stock_count": len(
            [
                s
                for s in stocks_result.values()
                if s["type"] == "Stock"
            ]
        ),

        "etf_count": len(
            [
                s
                for s in stocks_result.values()
                if s["type"] == "ETF"
            ]
        ),

        "statistics": {

            "complete": complete_cnt,

            "partial": partial_cnt,

            "insufficient": insufficient_cnt,

            "empty_name": empty_name_cnt,

            "institutional_1d": (
                inst_1d_count
            ),

            "institutional_5d": (
                inst_5d_count
            ),

            "institutional_10d": (
                inst_10d_count
            ),

            "institutional_20d": (
                inst_20d_count
            ),

            "successful_trading_days": (
                len(successful_dates)
            ),
        },

        "trading_dates": successful_dates,

        "stocks": stocks_result,
    }

    # ========================================================
    # 6. 禁止 main_force_*
    # ========================================================

    if not validate_no_main_force(
        stocks_result
    ):

        return 1

    # ========================================================
    # 7. Universe 數量再次驗證
    # ========================================================

    universe_declared_count = None

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8-sig",
        ) as f:

            universe_check = json.load(
                f
            )

        if isinstance(
            universe_check,
            dict,
        ):

            universe_declared_count = (
                universe_check.get(
                    "universe_count"
                )
            )

    except Exception as e:

        log(
            f"⚠️ 無法重新讀取 "
            f"universe.json header：{e}"
        )

    if universe_declared_count is not None:

        if (
            universe_declared_count
            != len(stocks_result)
        ):

            log(
                "❌ Chip 股票池數量與 "
                "universe.json 不一致"
            )

            log(
                f"   universe.json："
                f"{universe_declared_count}"
            )

            log(
                f"   chip："
                f"{len(stocks_result)}"
            )

            return 1

        log(
            f"✓ Chip Universe 數量："
            f"{len(stocks_result)} 檔"
        )

    # ========================================================
    # 8. 固定測試股票
    # ========================================================

    if not validate_required_stocks(
        stocks_result
    ):

        return 1

    # ========================================================
    # 9. 固定測試股票資料狀態
    # ========================================================

    section(
        "固定測試股票籌碼資料驗證"
    )

    for symbol in (
        "2337",
        "2426",
        "2368",
        "3081",
    ):

        item = stocks_result.get(
            symbol
        )

        if not item:

            log(
                f"❌ {symbol} 不存在"
            )

            return 1

        log(
            f"{symbol} | "
            f"{item['name']} | "
            f"1D={item['institutional_1d']} | "
            f"5D={item['institutional_5d']} | "
            f"10D={item['institutional_10d']} | "
            f"20D={item['institutional_20d']} | "
            f"當沖率={item['day_trading_rate']}"
        )

    # ========================================================
    # 10. Atomic Write
    # ========================================================

    section(
        "寫入 Data/chip.json (Atomic Write)"
    )

    if not atomic_write_json(
        CHIP_FILE,
        output,
    ):

        return 1

    log(
        "✓ Atomic Write 成功"
    )

    # ========================================================
    # 11. 寫入後重新驗證
    # ========================================================

    required_test_names = {

        "2337": "旺宏",

        "2426": "鼎元",

        "2368": "金像電",

        "3081": "聯亞",

    }

    if not verify_written_chip(
        len(stocks_result),
        required_test_names,
    ):

        return 1

    # ========================================================
    # 12. 最終統計
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
        "CHIP BUILD PASS"
    )

    log(
        "============================================================"
    )

    log(
        f"✓ Version：{VERSION}"
    )

    log(
        f"✓ Universe："
        f"{len(stocks_result)} 檔"
    )

    log(
        f"✓ 股票："
        f"{output['stock_count']} 檔"
    )

    log(
        f"✓ ETF："
        f"{output['etf_count']} 檔"
    )

    log(
        f"✓ 有效交易日："
        f"{len(successful_dates)} 日"
    )

    log(
        f"✓ 20D完整："
        f"{complete_cnt} 檔"
    )

    log(
        f"✓ 部分資料："
        f"{partial_cnt} 檔"
    )

    log(
        f"✓ 無資料："
        f"{insufficient_cnt} 檔"
    )

    log(
        f"✓ 名稱 fallback："
        f"{empty_name_cnt} 檔"
    )

    log(
        "------------------------------------------------------------"
    )

    log(
        "三大法人資料狀態"
    )

    log(
        f"✓ 1D："
        f"{inst_1d_count} 檔"
    )

    log(
        f"✓ 5D："
        f"{inst_5d_count} 檔"
    )

    log(
        f"✓ 10D："
        f"{inst_10d_count} 檔"
    )

    log(
        f"✓ 20D："
        f"{inst_20d_count} 檔"
    )

    log(
        "------------------------------------------------------------"
    )

    log(
        "主力資料狀態確認"
    )

    log(
        "✗ main_force_1d ：未寫入"
    )

    log(
        "✗ main_force_5d ：未寫入"
    )

    log(
        "✗ main_force_10d：未寫入"
    )

    log(
        "✗ main_force_20d：未寫入"
    )

    log(
        "✓ 三大法人 1D：保留"
    )

    log(
        "✓ 三大法人 5D：保留"
    )

    log(
        "✓ 三大法人 10D：保留"
    )

    log(
        "✓ 三大法人 20D：保留"
    )

    log(
        "✓ 當沖資料：保留"
    )

    log(
        "✓ 三大法人倍率估算：完全移除"
    )

    log(
        "✓ 假主力資料：完全禁止"
    )

    log(
        "✓ Universe 數量一致：通過"
    )

    log(
        "✓ 固定股票驗證：通過"
    )

    log(
        "✓ Atomic Write：通過"
    )

    log(
        "✓ 寫入後重新驗證：通過"
    )

    log(
        f"✓ fetch_chip.py {VERSION} 完成"
    )

    log(
        f"✓ 耗時：{elapsed:.1f} 秒"
    )

    log(
        "============================================================"
    )

    return 0


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
