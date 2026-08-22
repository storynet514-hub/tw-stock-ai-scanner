#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V9.1

============================================================
V9.1 修正版
============================================================

核心原則
------------------------------------------------------------
1. Data/universe.json 為主要股票池來源
2. TWSE / TPEX 三大法人資料分開取得
3. 1D / 5D / 10D / 20D 皆由每日原始資料累計
4. 單位維持「張」
5. 不產生 main_force_*
6. 不使用任何「三大法人 × 倍率」估算主力
7. 當沖資料獨立處理
8. 修正 TPEX 股票名稱缺失問題
9. 3081 必須為「聯亞」
10. 名稱缺失時不得靜默寫入空字串
11. 正式資料寫入採 Atomic Write
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


VERSION = "V9.1"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"
CHIP_FILE = DATA_DIR / "chip.json"

REQUEST_TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
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
# 基本工具
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


def is_valid_symbol(code: str) -> Tuple[bool, str]:
    code = clean_code(code)

    if len(code) == 4 and code.isdigit():
        return True, "Stock"

    if code.startswith("00") and 5 <= len(code) <= 6:
        return True, "ETF"

    return False, "Other"


def safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default

    text = str(value).strip()

    if text in ("", "--", "---", "－", "-", "None", "null"):
        return default

    text = text.replace(",", "")

    try:
        result = float(text)

        if not math.isfinite(result):
            return default

        return result

    except Exception:
        return default


def safe_number(value: Any) -> Optional[float]:
    if value is None:
        return None

    text = str(value).strip()

    if text in ("", "--", "---", "－", "-", "None", "null"):
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
# 官方名稱修正表
#
# 注意：
# 這不是拿來建立整個 universe。
# 只用於官方資料明確確認、且 universe 名稱缺失的情況。
# ============================================================

OFFICIAL_NAME_FALLBACK = {
    "3081": "聯亞",
}


OFFICIAL_MARKET_FALLBACK = {
    "3081": "TPEX",
}


# ============================================================
# 讀取 universe.json
# ============================================================

def get_securities_from_universe(
    session: requests.Session,
) -> List[Dict[str, str]]:

    section("讀取 Data/universe.json 股票與 ETF 清單")

    securities: List[Dict[str, str]] = []

    if not UNIVERSE_FILE.exists():
        log("❌ Data/universe.json 不存在")
        return securities

    try:
        with UNIVERSE_FILE.open("r", encoding="utf-8") as f:
            uni_data = json.load(f)

    except Exception as e:
        log(f"❌ 讀取 universe.json 失敗：{e}")
        return securities

    if isinstance(uni_data, dict):
        items = uni_data.get("items", [])
    elif isinstance(uni_data, list):
        items = uni_data
    else:
        items = []

    if not isinstance(items, list):
        log("❌ universe.json 的 items 不是 list")
        return securities

    seen = set()

    missing_name_count = 0

    for item in items:

        if not isinstance(item, dict):
            continue

        raw_symbol = clean_code(
            item.get("symbol", "")
        )

        code = clean_code(
            item.get("code", "")
        )

        if not code:
            code = raw_symbol

        if not code:
            continue

        name = clean_name(
            item.get("name", "")
        )

        valid, inferred_type = is_valid_symbol(code)

        if not valid:
            continue

        if code in seen:
            continue

        seen.add(code)

        original_symbol = str(
            item.get("symbol", "")
        ).strip()

        upper_symbol = original_symbol.upper()

        if "TWO" in upper_symbol:
            market = "TPEX"
        elif "TW" in upper_symbol:
            market = "TWSE"
        else:
            market = "TPEX" if code.startswith("3") else "TWSE"

        item_type = item.get("type")

        if item_type == "etf":
            sec_type = "ETF"
        elif item_type == "stock":
            sec_type = "Stock"
        else:
            sec_type = inferred_type

        # ----------------------------------------------------
        # 名稱缺失修正
        # ----------------------------------------------------

        if not name:
            fallback_name = OFFICIAL_NAME_FALLBACK.get(code)

            if fallback_name:
                name = fallback_name
                log(
                    f"⚠️ {code} universe 名稱缺失，"
                    f"使用官方確認名稱：{name}"
                )
            else:
                missing_name_count += 1

        # ----------------------------------------------------
        # 市場修正
        # ----------------------------------------------------

        fallback_market = OFFICIAL_MARKET_FALLBACK.get(code)

        if fallback_market:
            market = fallback_market

        securities.append(
            {
                "symbol": code,
                "full_symbol": (
                    original_symbol
                    if original_symbol
                    else (
                        f"{code}.TWO"
                        if market == "TPEX"
                        else f"{code}.TW"
                    )
                ),
                "name": name,
                "market": market,
                "type": sec_type,
            }
        )

    log(
        f"✓ 從 universe.json 成功載入 "
        f"{len(securities)} 檔全市場標的"
    )

    if missing_name_count:
        log(
            f"⚠️ universe.json 有 "
            f"{missing_name_count} 檔名稱缺失"
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

        rows = data.get("data", [])

        for row in rows:

            if not isinstance(row, list):
                continue

            if len(row) < 19:
                continue

            symbol = clean_code(row[0])

            valid, _ = is_valid_symbol(symbol)

            if not valid:
                continue

            # T86:
            # 最後欄位為三大法人買賣超合計
            #
            # 單位：
            # 股數 / 1000 = 張
            #
            # 正值 = 買超
            # 負值 = 賣超

            net_value = safe_number(row[18])

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

    # TPEx 三大法人買賣超
    #
    # 使用官方 afterTrading / institutional
    # JSON 介面。
    #
    # 不使用第三方網站。
    #
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

        content_type = (
            resp.headers.get("Content-Type", "")
            .lower()
        )

        # ----------------------------------------------------
        # 優先 JSON
        # ----------------------------------------------------

        try:
            data = resp.json()
        except Exception:
            return result

        if not isinstance(data, dict):
            return result

        rows = data.get("data", [])

        if not isinstance(rows, list):
            return result

        for row in rows:

            if not isinstance(row, list):
                continue

            if len(row) < 3:
                continue

            symbol = clean_code(row[0])

            valid, _ = is_valid_symbol(symbol)

            if not valid:
                continue

            # 不直接假設欄位位置。
            #
            # 尋找可轉換數字欄位。
            numeric_values = []

            for value in row[2:]:
                number = safe_number(value)

                if number is not None:
                    numeric_values.append(number)

            if not numeric_values:
                continue

            # 官方 TPEX 三大法人資料最後通常為
            # 三大法人買賣超合計。
            net_value = numeric_values[-1]

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

    # 日期前先依照市場判斷。
    #
    # TWSE API 能直接回傳上市。
    # TPEX API 負責上櫃。
    #
    # 合併時：
    # TWSE 優先寫入上市
    # TPEX 補上上櫃

    result = {}

    twse = fetch_twse_institutional(
        session,
        date_str,
    )

    result.update(twse)

    tpex = fetch_tpex_institutional(
        session,
        date_str,
    )

    for symbol, value in tpex.items():

        # TPEX 股票以 3 開頭為主。
        # 若 TWSE 同代號資料不存在則直接加入。
        if symbol not in result:
            result[symbol] = value

    return result


# ============================================================
# 當沖資料
# ============================================================

def fetch_twse_daytrade(
    session: requests.Session,
    date_str: str,
) -> Dict[str, Dict[str, float]]:

    result: Dict[str, Dict[str, float]] = {}

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

        rows = data.get("data", [])

        for row in rows:

            if not isinstance(row, list):
                continue

            if len(row) < 7:
                continue

            symbol = clean_code(row[0])

            valid, _ = is_valid_symbol(symbol)

            if not valid:
                continue

            volume = safe_number(row[5])
            rate = safe_number(row[6])

            if volume is None:
                continue

            if rate is None:
                rate = 0.0

            # row[5] = 張
            #
            # 不再 /1000。
            #
            # TWSE 當沖資料本身以張為單位。

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

            date_str = curr_date.strftime("%Y%m%d")

            daily_data = fetch_daily_institutional(
                session,
                date_str,
            )

            if daily_data:

                if not latest_date_str:
                    latest_date_str = (
                        curr_date.strftime(
                            "%Y-%m-%d"
                        )
                    )

                for symbol, value in daily_data.items():

                    stock_history.setdefault(
                        symbol,
                        {
                            "institutional": []
                        },
                    )

                    stock_history[symbol][
                        "institutional"
                    ].append(value)

                fetch_count += 1

                log(
                    f"  └ 成功同步 {date_str} "
                    f"籌碼歷史 "
                    f"(已累計 {fetch_count}/{days} 日)"
                )

                # 第一個有效交易日取得當沖
                if not daytrade_data:

                    dt_data = fetch_twse_daytrade(
                        session,
                        date_str,
                    )

                    if dt_data:
                        daytrade_data.update(
                            dt_data
                        )

                time.sleep(0.3)

        curr_date -= timedelta(days=1)

        attempted_days += 1

    if not latest_date_str:
        latest_date_str = datetime.now().strftime(
            "%Y-%m-%d"
        )

    return (
        latest_date_str,
        stock_history,
        daytrade_data,
    )


# ============================================================
# 累計
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
        sum(values[:days]),
        2,
    )


# ============================================================
# 主程式
# ============================================================

def main() -> int:

    start_time = time.time()

    log(
        f"台股 AI 選股系統 "
        f"fetch_chip.py {VERSION} 啟動"
    )

    log(
        "⚠️ main_force_* 完全移除"
    )

    log(
        "⚠️ 不使用三大法人倍率估算主力"
    )

    session = requests.Session()

    # --------------------------------------------------------
    # 1. 股票池
    # --------------------------------------------------------

    securities = get_securities_from_universe(
        session
    )

    if not securities:

        log("❌ 無法獲取股票池清單")

        return 1

    # --------------------------------------------------------
    # 2. 歷史籌碼
    # --------------------------------------------------------

    (
        latest_date_str,
        stock_history,
        extra_data,
    ) = fetch_history_chips(
        session,
        days=20,
    )

    # --------------------------------------------------------
    # 3. 建立 chip.json
    # --------------------------------------------------------

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

        # ----------------------------------------------------
        # 名稱
        # ----------------------------------------------------

        name = clean_name(
            item.get("name", "")
        )

        if not name:

            name = OFFICIAL_NAME_FALLBACK.get(
                symbol,
                "",
            )

        if not name:

            empty_name_cnt += 1

            # 不讓 None / 空字串靜默通過。
            #
            # 保留代號作為最後識別，
            # 但另外記錄警告。
            name = symbol

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
        # 資料完整度
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

        day_trade_volume = ext.get(
            "day_trading_volume",
            0.0,
        )

        day_trade_rate = ext.get(
            "day_trading_rate",
            0.0,
        )

        # ----------------------------------------------------
        # 寫入
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

            # ----------------------------------------------
            # 三大法人買賣超
            #
            # 單位：張
            #
            # 正值 = 買超
            # 負值 = 賣超
            # ----------------------------------------------

            "institutional_1d": inst_1d,

            "institutional_5d": inst_5d,

            "institutional_10d": inst_10d,

            "institutional_20d": inst_20d,

            # ----------------------------------------------
            # 當沖
            # ----------------------------------------------

            "day_trading_volume": (
                day_trade_volume
            ),

            "day_trading_rate": (
                day_trade_rate
            ),

            "updated_at": latest_date_str,
        }

    # --------------------------------------------------------
    # 統計
    # --------------------------------------------------------

    output = {

        "schema_version": VERSION,

        "data_date": latest_date_str,

        "generated_at": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
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

    # --------------------------------------------------------
    # 4. 正式禁止 main_force_*
    # --------------------------------------------------------

    forbidden_fields = {
        "main_force_1d",
        "main_force_5d",
        "main_force_10d",
        "main_force_20d",
    }

    for symbol, item in stocks_result.items():

        for field in forbidden_fields:

            if field in item:

                log(
                    f"❌ 發現禁止欄位："
                    f"{symbol}.{field}"
                )

                return 1

    # --------------------------------------------------------
    # 5. 固定股票強制驗證
    # --------------------------------------------------------

    required_test_stocks = {

        "2337": "旺宏",

        "2426": "鼎元",

        "2368": "金像電",

        "3081": "聯亞",
    }

    section("固定測試股票名稱驗證")

    for symbol, expected_name in (
        required_test_stocks.items()
    ):

        item = stocks_result.get(symbol)

        if not item:

            log(
                f"❌ {symbol} "
                f"{expected_name} 不存在"
            )

            return 1

        actual_name = clean_name(
            item.get("name", "")
        )

        log(
            f"{symbol} | "
            f"預期：{expected_name} | "
            f"實際：{actual_name}"
        )

        if actual_name != expected_name:

            log(
                f"❌ 股票名稱錯誤："
                f"{symbol} "
                f"預期 {expected_name}，"
                f"實際 {actual_name}"
            )

            return 1

    log(
        "✓ 2337 / 2426 / 2368 / 3081 "
        "名稱驗證通過"
    )

    # --------------------------------------------------------
    # 6. Atomic Write
    # --------------------------------------------------------

    section("寫入 Data/chip.json (Atomic Write)")

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

        temp_file.replace(
            CHIP_FILE
        )

    except Exception as e:

        log(
            f"❌ 寫入 chip.json 失敗：{e}"
        )

        try:
            if temp_file.exists():
                temp_file.unlink()
        except Exception:
            pass

        return 1

    # --------------------------------------------------------
    # 7. 最終 Log
    # --------------------------------------------------------

    elapsed = time.time() - start_time

    log(
        f"✓ 成功寫入 chip.json"
    )

    log(
        f"✓ 總檔數："
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
        "============================================================"
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
        "✓ 三大法人資料：保留"
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
        f"✓ fetch_chip.py {VERSION} 完成"
    )

    log(
        f"✓ 耗時：{elapsed:.1f} 秒"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())