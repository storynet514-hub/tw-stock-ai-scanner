#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V9.4.2

============================================================
V9.4.2 正式修正版
============================================================

V9.4.2 核心修正
------------------------------------------------------------
1. Data/universe.json 為唯一主要股票池來源
2. 支援 universe.json V10.x / V11.x stocks object 架構
3. 相容舊版 items list 架構
4. stocks object 的 key 視為正式股票代號
5. 不再用「00 開頭」判斷 ETF
6. 支援台灣 ETF / ETN 的英文字母尾碼
7. 支援 Universe 中 4～6 碼純數字代號
8. 支援 Universe 中 4～6 碼數字 + 1～2 碼英文字母代號
9. Universe 中合法標的不得被 fetch_chip 靜默排除
10. universe_count 必須與 stocks object 實際數量一致
11. fetch_chip 載入數量必須與 Universe 實際數量一致
12. 被排除的代號必須明確列出
13. TWSE / TPEX 三大法人資料分開取得
14. 1D / 5D / 10D / 20D 皆由每日原始資料累計
15. 單位維持「張」
16. 不產生 main_force_*
17. 不使用任何「三大法人 × 倍率」估算主力
18. 當沖資料獨立處理
19. 名稱缺失不得靜默寫入空字串
20. 3081 必須為「聯亞」
21. 3081 必須為 TPEX
22. 正式資料寫入採 Atomic Write
23. 固定驗證 2337 / 2426 / 2368 / 3081
24. 10D 正式保留
25. ETF / ETN 代號允許英文字母尾碼
26. 不再因代號長度錯誤排除合法 Universe 標的

============================================================
"""

from __future__ import annotations

import json
import math
import re
import sys
import time

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


# ============================================================
# Version
# ============================================================

VERSION = "V9.4.2"


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
        .upper()
        .replace(".TW", "")
        .replace(".TWO", "")
    )


def clean_name(value: Any) -> str:

    if value is None:
        return ""

    return str(value).strip()


# ============================================================
# Symbol 判斷
#
# V9.4.2 重要修正
#
# 原 V9.4.1 錯誤：
#
#     \d{4}
#     \d{4}[A-Z]{1,2}
#
# 導致合法 Universe 代號被錯誤排除，例如：
#
#     006203
#     006204
#     006205
#     00636
#     00690
#     00713
#     00400A
#     00631L
#     00632R
#     00710B
#
# 實際 Universe 已存在這些合法標的，
# fetch_chip 不應自行用過窄格式把它們排除。
#
# V9.4.2：
#
#     4～6碼純數字
#         -> 合法
#
#     4～6碼數字 + 1～2碼英文字母
#         -> 合法
#
# 例如：
#
#     2337
#     0050
#     006203
#     00636
#     00713
#     00400A
#     00631L
#     00632R
#     00710B
#     00981T
#     01001T
#
# 注意：
# 不在這裡自行判斷「是不是 ETF」。
# type 應優先使用 Universe 提供的 type。
#
# ============================================================

def is_valid_symbol(
    code: str,
) -> Tuple[bool, str]:

    code = clean_code(code)

    if not code:
        return False, "Other"

    # --------------------------------------------------------
    # 4～6 碼純數字
    #
    # 股票、ETF、ETN 等合法代號均允許。
    # --------------------------------------------------------

    if re.fullmatch(
        r"\d{4,6}",
        code,
    ):

        return True, "Stock"

    # --------------------------------------------------------
    # 4～6 碼數字 + 1～2 碼英文字母
    #
    # 例如：
    #
    # 00400A
    # 00625K
    # 00631L
    # 00632R
    # 00710B
    # 00981T
    # 01001T
    # --------------------------------------------------------

    if re.fullmatch(
        r"\d{4,6}[A-Z]{1,2}",
        code,
    ):

        return True, "ETF"

    # --------------------------------------------------------
    # 其他未知格式
    # --------------------------------------------------------

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

    text = text.replace(",", "")

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
# Universe loader
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

    # --------------------------------------------------------
    # 讀取 JSON
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # 宣告數量
    # --------------------------------------------------------

    declared_count: Optional[int] = None

    if isinstance(
        uni_data,
        dict,
    ):

        raw_count = uni_data.get(
            "universe_count"
        )

        if raw_count is not None:

            try:

                declared_count = int(
                    raw_count
                )

            except Exception:

                log(
                    "❌ universe_count 不是有效整數"
                )

                return securities

    # --------------------------------------------------------
    # 正式 V10.x / V11.x stocks object
    # --------------------------------------------------------

    items: List[Dict[str, Any]] = []

    source_stock_keys: List[str] = []

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
                "stocks object 架構"
            )

            source_stock_keys = [
                clean_code(key)
                for key in stocks.keys()
                if clean_code(key)
            ]

            raw_stock_count = len(
                source_stock_keys
            )

            log(
                f"✓ Universe stocks object："
                f"{raw_stock_count} 檔"
            )

            # ------------------------------------------------
            # 第一層驗證：
            # universe_count vs stocks object
            # ------------------------------------------------

            if declared_count is not None:

                if (
                    declared_count
                    != raw_stock_count
                ):

                    log(
                        "❌ Universe 原始數量不一致"
                    )

                    log(
                        f"   universe_count："
                        f"{declared_count}"
                    )

                    log(
                        f"   stocks object："
                        f"{raw_stock_count}"
                    )

                    log(
                        "❌ 停止 fetch_chip.py"
                    )

                    return securities

                log(
                    "✓ universe_count 與 "
                    "stocks object 數量一致"
                )

            for key, item in stocks.items():

                if not isinstance(
                    item,
                    dict,
                ):

                    log(
                        f"❌ stocks[{key}] "
                        f"不是 object"
                    )

                    return securities

                normalized = dict(item)

                # stocks key 是最高優先級
                normalized["symbol"] = (
                    clean_code(key)
                )

                if not normalized.get(
                    "code"
                ):

                    normalized["code"] = (
                        clean_code(key)
                    )

                items.append(
                    normalized
                )

        else:

            # ------------------------------------------------
            # 舊版 items list
            # ------------------------------------------------

            legacy_items = uni_data.get(
                "items",
                [],
            )

            if isinstance(
                legacy_items,
                list,
            ):

                log(
                    "⚠️ 偵測到舊版 "
                    "universe.json items list 架構"
                )

                for item in legacy_items:

                    if isinstance(
                        item,
                        dict,
                    ):

                        items.append(
                            dict(item)
                        )

    elif isinstance(
        uni_data,
        list,
    ):

        log(
            "⚠️ 偵測到舊版 "
            "universe.json list 架構"
        )

        for item in uni_data:

            if isinstance(
                item,
                dict,
            ):

                items.append(
                    dict(item)
                )

    # --------------------------------------------------------
    # 沒有資料
    # --------------------------------------------------------

    if not items:

        log(
            "❌ universe.json 找不到 "
            "stocks 或 items 資料"
        )

        return securities

    # --------------------------------------------------------
    # 正式解析
    # --------------------------------------------------------

    seen = set()

    rejected: List[Dict[str, str]] = []

    missing_name_count = 0

    for item in items:

        # ----------------------------------------------------
        # symbol
        # ----------------------------------------------------

        raw_symbol = clean_code(
            item.get(
                "symbol",
                "",
            )
        )

        # ----------------------------------------------------
        # code
        # ----------------------------------------------------

        raw_code = clean_code(
            item.get(
                "code",
                "",
            )
        )

        # stocks object 中 symbol 已經是 key，
        # 優先使用 symbol。
        code = raw_symbol or raw_code

        if not code:

            rejected.append(
                {
                    "code": "",
                    "reason": "missing_symbol",
                }
            )

            continue

        # ----------------------------------------------------
        # 重複
        # ----------------------------------------------------

        if code in seen:

            rejected.append(
                {
                    "code": code,
                    "reason": "duplicate",
                }
            )

            continue

        # ----------------------------------------------------
        # 型別判斷
        # ----------------------------------------------------

        valid, inferred_type = (
            is_valid_symbol(code)
        )

        if not valid:

            rejected.append(
                {
                    "code": code,
                    "reason": "invalid_symbol",
                }
            )

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
                    f"⚠️ {code} 名稱缺失，"
                    f"使用官方確認名稱："
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

            if (
                ".TWO" in original_symbol
                or original_symbol.endswith(
                    "TWO"
                )
            ):

                market = "TPEX"

            elif (
                ".TW" in original_symbol
                or original_symbol.endswith(
                    "TW"
                )
            ):

                market = "TWSE"

            else:

                # 安全 fallback
                #
                # 市場真正來源仍以 Universe
                # 的 market 欄位為優先。
                market = (
                    "TPEX"
                    if code.startswith("3")
                    else "TWSE"
                )

        # ----------------------------------------------------
        # 官方市場 override
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
        # full_symbol
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
        # 正式寫入
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
    # 解析結果驗證
    # ========================================================

    log("")

    log(
        "Universe 解析結果"
    )

    log(
        f"  原始 stocks object："
        f"{len(source_stock_keys)} 檔"
    )

    log(
        f"  成功解析："
        f"{len(securities)} 檔"
    )

    log(
        f"  被排除："
        f"{len(rejected)} 檔"
    )

    # --------------------------------------------------------
    # 被排除標的明確列出
    # --------------------------------------------------------

    if rejected:

        log("")

        log(
            "❌ 發現 Universe 標的被排除："
        )

        for item in rejected[:100]:

            log(
                f"   {item.get('code', '')}"
                f" | "
                f"{item.get('reason', '')}"
            )

        if len(rejected) > 100:

            log(
                f"   ...其餘 "
                f"{len(rejected) - 100} 檔省略"
            )

    # --------------------------------------------------------
    # stocks object：
    # 實際解析數量必須完全一致
    # --------------------------------------------------------

    if source_stock_keys:

        expected_count = len(
            source_stock_keys
        )

        actual_count = len(
            securities
        )

        if actual_count != expected_count:

            log("")

            log(
                "❌ Universe 解析後數量不一致"
            )

            log(
                f"   stocks object："
                f"{expected_count}"
            )

            log(
                f"   fetch_chip 載入："
                f"{actual_count}"
            )

            log(
                "❌ 不允許繼續寫入 chip.json"
            )

            return []

        log(
            "✓ Universe 原始標的"
            "全部成功解析"
        )

    # --------------------------------------------------------
    # 最終 declared count
    # --------------------------------------------------------

    if declared_count is not None:

        if declared_count != len(
            securities
        ):

            log(
                "❌ Universe 最終數量驗證失敗"
            )

            log(
                f"   header："
                f"{declared_count}"
            )

            log(
                f"   實際："
                f"{len(securities)}"
            )

            return []

        log(
            "✓ Universe header / "
            "實際載入數量驗證通過"
        )

    # ========================================================
    # 統計
    # ========================================================

    log("")

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

    if missing_name_count:

        log(
            f"⚠️ Universe 有 "
            f"{missing_name_count} 檔名稱缺失"
        )

    else:

        log(
            "✓ Universe 名稱完整"
        )

    return securities


# ============================================================
# TWSE 三大法人
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

    try:

        resp = session.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        if resp.status_code != 200:
            return result

        data = resp.json()

        if data.get("stat") != "OK":
            return result

        rows = data.get(
            "data",
            [],
        )

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

            net_value = safe_number(
                row[18]
            )

            if net_value is None:
                continue

            result[symbol] = round(
                net_value / 1000.0,
                2,
            )

    except Exception:

        return result

    return result


# ============================================================
# TPEX 三大法人
# ============================================================

def fetch_tpex_institutional(
    session: requests.Session,
    date_str: str,
) -> Dict[str, float]:

    result: Dict[str, float] = {}

    url = (
        "https://www.tpex.org.tw/www/zh-tw/"
        "institutions/institutional"
        f"?date={date_str}&type=Daily"
    )

    try:

        resp = session.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        if resp.status_code != 200:
            return result

        try:

            data = resp.json()

        except Exception:

            return result

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

            numeric_values = []

            for value in row[2:]:

                number = safe_number(
                    value
                )

                if number is not None:

                    numeric_values.append(
                        number
                    )

            if not numeric_values:

                continue

            net_value = (
                numeric_values[-1]
            )

            result[symbol] = round(
                net_value / 1000.0,
                2,
            )

    except Exception:

        return result

    return result


# ============================================================
# 每日三大法人
# ============================================================

def fetch_daily_institutional(
    session: requests.Session,
    date_str: str,
) -> Dict[str, float]:

    result: Dict[str, float] = {}

    twse = fetch_twse_institutional(
        session,
        date_str,
    )

    result.update(
        twse
    )

    tpex = fetch_tpex_institutional(
        session,
        date_str,
    )

    for symbol, value in (
        tpex.items()
    ):

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

    try:

        resp = session.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        if resp.status_code != 200:
            return result

        data = resp.json()

        if data.get("stat") != "OK":
            return result

        rows = data.get(
            "data",
            [],
        )

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

    except Exception:

        return result

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
]:

    section(
        f"同步 TWSE/TPEX 最近 {days} "
        f"個交易日三大法人資料"
    )

    stock_history: Dict[
        str,
        Dict[str, List[float]]
    ] = {}

    daytrade_data: Dict[
        str,
        Dict[str, float]
    ] = {}

    latest_date_str = ""

    fetch_count = 0

    curr_date = datetime.now()

    attempted_days = 0

    while (
        fetch_count < days
        and attempted_days < 45
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

            if daily_data:

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

                fetch_count += 1

                log(
                    f"  └ 成功同步 {date_str} "
                    f"籌碼歷史 "
                    f"(已累計 "
                    f"{fetch_count}/{days} 日)"
                )

                if not daytrade_data:

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

    return (
        latest_date_str,
        stock_history,
        daytrade_data,
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
# Forbidden field scanner
# ============================================================

def scan_forbidden_fields(
    stocks: Dict[str, Dict[str, Any]],
) -> bool:

    forbidden_fields = {
        "main_force_1d",
        "main_force_5d",
        "main_force_10d",
        "main_force_20d",
    }

    for symbol, item in (
        stocks.items()
    ):

        if not isinstance(
            item,
            dict,
        ):

            continue

        for field in forbidden_fields:

            if field in item:

                log(
                    f"❌ 發現禁止欄位："
                    f"{symbol}.{field}"
                )

                return False

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

    section(
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

    session = requests.Session()

    # ========================================================
    # 1. Universe
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
    ) = fetch_history_chips(
        session,
        days=20,
    )

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

        name = clean_name(
            item.get(
                "name",
                "",
            )
        )

        if not name:

            name = (
                OFFICIAL_NAME_FALLBACK.get(
                    symbol,
                    "",
                )
            )

        if not name:

            empty_name_cnt += 1

            name = symbol

        inst_1d = (
            inst_list[0]
            if len(inst_list) >= 1
            else None
        )

        inst_5d = calculate_period(
            inst_list,
            5,
        )

        inst_10d = calculate_period(
            inst_list,
            10,
        )

        inst_20d = calculate_period(
            inst_list,
            20,
        )

        if len(inst_list) >= 20:

            complete_cnt += 1

        elif len(inst_list) >= 1:

            partial_cnt += 1

        else:

            insufficient_cnt += 1

        ext = extra_data.get(
            symbol,
            {},
        )

        day_trade_volume = ext.get(
            "day_trading_volume",
            0.0,
        )

        day_trade_rate = ext.get(
            "day_trading_rate",
            0.0,
        )

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

            "institutional_1d": inst_1d,

            "institutional_5d": inst_5d,

            "institutional_10d": inst_10d,

            "institutional_20d": inst_20d,

            "day_trading_volume": (
                day_trade_volume
            ),

            "day_trading_rate": (
                day_trade_rate
            ),

            "updated_at": latest_date_str,
        }

    # ========================================================
    # 4. 建立 Output
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
        },

        "stocks": stocks_result,
    }

    # ========================================================
    # 5. 禁止欄位驗證
    # ========================================================

    if not scan_forbidden_fields(
        stocks_result
    ):

        return 1

    # ========================================================
    # 6. Universe 最終數量驗證
    # ========================================================

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8-sig",
        ) as f:

            universe_check = json.load(
                f
            )

    except Exception as e:

        log(
            f"❌ 無法重新讀取 universe.json："
            f"{e}"
        )

        return 1

    universe_declared_count = None

    universe_actual_count = None

    if isinstance(
        universe_check,
        dict,
    ):

        raw_count = universe_check.get(
            "universe_count"
        )

        if raw_count is not None:

            try:

                universe_declared_count = int(
                    raw_count
                )

            except Exception:

                log(
                    "❌ universe_count 無效"
                )

                return 1

        stocks_obj = universe_check.get(
            "stocks"
        )

        if isinstance(
            stocks_obj,
            dict,
        ):

            universe_actual_count = len(
                stocks_obj
            )

    if universe_actual_count is not None:

        if (
            universe_declared_count
            != universe_actual_count
        ):

            log(
                "❌ Universe 原始資料本身數量不一致"
            )

            log(
                f"   header："
                f"{universe_declared_count}"
            )

            log(
                f"   stocks："
                f"{universe_actual_count}"
            )

            return 1

        if (
            len(stocks_result)
            != universe_actual_count
        ):

            log(
                "❌ fetch_chip 股票池數量不一致"
            )

            log(
                f"   Universe："
                f"{universe_actual_count}"
            )

            log(
                f"   Chip："
                f"{len(stocks_result)}"
            )

            return 1

        log(
            f"✓ Universe / Chip 數量一致："
            f"{universe_actual_count} 檔"
        )

    # ========================================================
    # 7. 固定測試股票
    # ========================================================

    required_test_stocks = {

        "2337": "旺宏",

        "2426": "鼎元",

        "2368": "金像電",

        "3081": "聯亞",
    }

    section(
        "固定測試股票名稱與市場驗證"
    )

    for symbol, expected_name in (
        required_test_stocks.items()
    ):

        item = stocks_result.get(
            symbol
        )

        if not item:

            log(
                f"❌ {symbol} "
                f"{expected_name} 不存在"
            )

            return 1

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

        expected_market = (
            "TPEX"
            if symbol == "3081"
            else "TWSE"
        )

        log(
            f"{symbol} | "
            f"預期：{expected_name} | "
            f"實際：{actual_name} | "
            f"市場：{actual_market}"
        )

        if actual_name != expected_name:

            log(
                f"❌ 股票名稱錯誤："
                f"{symbol}"
            )

            return 1

        if actual_market != expected_market:

            log(
                f"❌ 股票市場錯誤："
                f"{symbol}"
            )

            return 1

    log(
        "✓ 2337 / 2426 / 2368 / 3081 "
        "名稱與市場驗證通過"
    )

    # ========================================================
    # 8. Atomic Write
    # ========================================================

    section(
        "寫入 Data/chip.json (Atomic Write)"
    )

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_file = CHIP_FILE.with_suffix(
        ".json.tmp"
    )

    try:

        with temp_file.open(
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                output,
                f,
                ensure_ascii=False,
                indent=2,
            )

            f.flush()

        temp_file.replace(
            CHIP_FILE
        )

    except Exception as e:

        log(
            f"❌ 寫入 chip.json 失敗："
            f"{e}"
        )

        try:

            if temp_file.exists():
                temp_file.unlink()

        except Exception:
            pass

        return 1

    # ========================================================
    # 9. 寫入後重新驗證
    # ========================================================

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
            f"❌ chip.json 重新讀取失敗："
            f"{e}"
        )

        return 1

    if not isinstance(
        verify_data,
        dict,
    ):

        log(
            "❌ chip.json 根節點不是 object"
        )

        return 1

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

        return 1

    if len(verify_stocks) != len(
        stocks_result
    ):

        log(
            "❌ chip.json 寫入數量錯誤"
        )

        return 1

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
                f"❌ 寫入後找不到："
                f"{symbol}"
            )

            return 1

        if clean_name(
            item.get(
                "name",
                "",
            )
        ) != expected_name:

            log(
                f"❌ 寫入後名稱錯誤："
                f"{symbol}"
            )

            return 1

    if not scan_forbidden_fields(
        verify_stocks
    ):

        return 1

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

    # ========================================================
    # 10. Final
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
        "主力資料狀態確認"
    )

    log(
        "✗ main_force_1d：未寫入"
    )

    log(
        "✗ main_force_5d：未寫入"
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

    return 0


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
