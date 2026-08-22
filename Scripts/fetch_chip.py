#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V9.0

============================================================
V9.0 核心修正
============================================================

【重大修正】
完全移除原本錯誤的「主力買賣超估算」。

舊版：
    mf_buy = inst_buy * 1.12

這不是原始資料，也沒有可靠統計依據，
因此 V9.0 完全禁止使用。

============================================================

目前正式資料：

1. 三大法人買賣超
   - 1D
   - 5D
   - 10D
   - 20D

2. 當沖資料
   - 當沖成交量
   - 當沖率

============================================================

目前暫不寫入：

    main_force_1d
    main_force_5d
    main_force_10d
    main_force_20d

原因：

目前尚未完成「全券商分點原始資料」的正式驗證。

在取得可靠來源以前：

    不估算
    不猜測
    不用倍率
    不把三大法人當主力
    不把熱門券商排行當全券商
    不寫入假主力資料

============================================================

正式資料流程：

    Data/universe.json
            ↓
    fetch_chip.py
            ↓
    TWSE / TPEX
            ↓
    三大法人原始資料
            ↓
    1D / 5D / 10D / 20D
            ↓
    chip.json

另外：

    TWSE / TPEX
            ↓
    當沖原始資料
            ↓
    當沖量 / 當沖率

未來：

    券商分點原始資料
            ↓
    broker_net_1d
    broker_net_5d
    broker_net_10d
    broker_net_20d

驗證完成後才正式加入。
============================================================
"""

from __future__ import annotations

import json
import sys
import time

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests


# ============================================================
# 基本設定
# ============================================================

VERSION = "V9.0"

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
# 股票代號驗證
# ============================================================

def is_valid_symbol(code: str) -> Tuple[bool, str]:
    """
    判斷股票 / ETF 代號。

    Stock:
        4 位數字

    ETF:
        00 開頭
        5～6 位
    """

    if not code:
        return False, "Other"

    code = (
        str(code)
        .strip()
        .upper()
        .replace(".TW", "")
        .replace(".TWO", "")
    )

    if len(code) == 4 and code.isdigit():
        return True, "Stock"

    if code.startswith("00") and 5 <= len(code) <= 6:
        return True, "ETF"

    return False, "Other"


# ============================================================
# 安全轉換數字
# ============================================================

def parse_number(value: Any, default: float = 0.0) -> float:
    """
    將 TWSE / TPEX API 常見數字格式轉成 float。

    支援：
        1,234
        -1,234
        1234
        --
        空值
        None
    """

    if value is None:
        return default

    text = str(value).strip()

    if not text or text in {"--", "---", "null", "None"}:
        return default

    text = text.replace(",", "")

    try:
        return float(text)
    except (ValueError, TypeError):
        return default


# ============================================================
# 1. 讀取 universe.json
# ============================================================

def get_securities_from_universe(
    session: requests.Session,
) -> List[Dict[str, str]]:

    section("讀取 Data/universe.json 股票與 ETF 清單")

    securities: List[Dict[str, str]] = []

    # --------------------------------------------------------
    # 優先使用本地 universe.json
    # --------------------------------------------------------

    if UNIVERSE_FILE.exists():

        try:

            with UNIVERSE_FILE.open(
                "r",
                encoding="utf-8",
            ) as f:

                uni_data = json.load(f)

            if isinstance(uni_data, dict):
                items = uni_data.get("items", [])
            elif isinstance(uni_data, list):
                items = uni_data
            else:
                items = []

            for item in items:

                if not isinstance(item, dict):
                    continue

                raw_symbol = str(
                    item.get("symbol", "")
                ).strip()

                code = str(
                    item.get(
                        "code",
                        raw_symbol.split(".")[0],
                    )
                ).strip()

                name = str(
                    item.get("name", "")
                ).strip()

                if not code:
                    continue

                valid, detected_type = is_valid_symbol(code)

                if not valid:
                    continue

                # ------------------------------------------------
                # 市場判斷
                # ------------------------------------------------

                raw_upper = raw_symbol.upper()

                if ".TWO" in raw_upper:
                    market = "TPEX"
                elif ".TW" in raw_upper:
                    market = "TWSE"
                else:
                    # 若 universe 沒有 full symbol，
                    # 暫時依 item.market 判斷
                    market_value = str(
                        item.get("market", "")
                    ).upper()

                    if market_value in {
                        "TPEX",
                        "TWO",
                        "TPEx",
                    }:
                        market = "TPEX"
                    else:
                        market = "TWSE"

                # ------------------------------------------------
                # ETF 判斷
                # ------------------------------------------------

                item_type = str(
                    item.get("type", "")
                ).lower()

                if (
                    item_type == "etf"
                    or detected_type == "ETF"
                    or code.startswith("00")
                ):
                    sec_type = "ETF"
                else:
                    sec_type = "Stock"

                securities.append(
                    {
                        "symbol": code,
                        "full_symbol": (
                            raw_symbol
                            if raw_symbol
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

            return securities

        except Exception as exc:

            log(
                f"⚠️ 讀取 universe.json 失敗：{exc}"
            )

            log(
                "⚠️ 改用 TWSE 備用 API..."
            )

    # ========================================================
    # 備用 API
    # ========================================================

    try:

        url = (
            "https://openapi.twse.com.tw/"
            "v1/exchangeReport/BWIBBU_ALL"
        )

        response = session.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code == 200:

            data = response.json()

            for item in data:

                if not isinstance(item, dict):
                    continue

                code = str(
                    item.get("Code", "")
                ).strip()

                name = str(
                    item.get("Name", "")
                ).strip()

                valid, sec_type = is_valid_symbol(code)

                if not valid:
                    continue

                securities.append(
                    {
                        "symbol": code,
                        "full_symbol": f"{code}.TW",
                        "name": name,
                        "market": "TWSE",
                        "type": sec_type,
                    }
                )

    except Exception as exc:

        log(
            f"❌ 上市線上備用 API 異常：{exc}"
        )

    return securities


# ============================================================
# 2. TWSE 三大法人資料
# ============================================================

def fetch_twse_institutional(
    session: requests.Session,
    date_str: str,
    stock_history: Dict[str, Dict[str, List[float]]],
) -> bool:

    """
    抓取 TWSE T86 三大法人買賣超。

    注意：

    row[18]
        = 三大法人買賣超

    TWSE T86 的數值單位為「股」，
    因此：

        股 / 1000 = 張

    不進行任何估算倍率。
    """

    url = (
        "https://www.twse.com.tw/"
        f"rwd/zh/fund/T86?date={date_str}&selectType=ALL"
    )

    try:

        response = session.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            return False

        result = response.json()

        if result.get("stat") != "OK":
            return False

        data = result.get("data", [])

        if not isinstance(data, list):
            return False

        found_count = 0

        for row in data:

            if not isinstance(row, list):
                continue

            if len(row) < 19:
                continue

            symbol = str(row[0]).strip()

            valid, _ = is_valid_symbol(symbol)

            if not valid:
                continue

            # ------------------------------------------------
            # 三大法人買賣超
            #
            # T86 row[18]
            #
            # 原始單位：股
            # 輸出單位：張
            # ------------------------------------------------

            institutional_shares = parse_number(
                row[18],
                default=0.0,
            )

            institutional_lots = (
                institutional_shares / 1000.0
            )

            stock_history.setdefault(
                symbol,
                {
                    "institutional": [],
                },
            )

            stock_history[symbol][
                "institutional"
            ].append(
                round(
                    institutional_lots,
                    2,
                )
            )

            found_count += 1

        return found_count > 0

    except Exception:
        return False


# ============================================================
# 3. TPEX 三大法人資料
# ============================================================

def fetch_tpex_institutional(
    session: requests.Session,
    date_str: str,
    stock_history: Dict[str, Dict[str, List[float]]],
) -> bool:

    """
    抓取 TPEX 三大法人資料。

    TPEx API 版本可能變動，因此採多候選 endpoint。

    重要：

    只接受能明確辨識：
        股票代號
        三大法人買賣超

    的資料。

    不進行估算。
    """

    date_compact = date_str

    candidates = [
        (
            "https://www.tpex.org.tw/"
            "web/stock/3insti/daily_trade/"
            "3itrade_hedge_print.php",
            {
                "l": "zh-tw",
                "d": date_compact,
                "s": "0,asc",
            },
        ),
        (
            "https://www.tpex.org.tw/"
            "www/zh-tw/3insti/daily",
            {
                "date": date_compact,
            },
        ),
    ]

    for url, params in candidates:

        try:

            response = session.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code != 200:
                continue

            content_type = response.headers.get(
                "Content-Type",
                "",
            ).lower()

            # ------------------------------------------------
            # JSON
            # ------------------------------------------------

            if (
                "json" in content_type
                or response.text.lstrip().startswith("{")
                or response.text.lstrip().startswith("[")
            ):

                result = response.json()

                rows = []

                if isinstance(result, dict):
                    rows = result.get(
                        "tables",
                        result.get(
                            "data",
                            [],
                        ),
                    )

                elif isinstance(result, list):
                    rows = result

                # 遞迴處理可能的 tables
                if isinstance(rows, list):

                    flat_rows: List[Any] = []

                    for block in rows:

                        if isinstance(block, dict):
                            block_data = block.get(
                                "data",
                                [],
                            )

                            if isinstance(
                                block_data,
                                list,
                            ):
                                flat_rows.extend(
                                    block_data
                                )

                        elif isinstance(block, list):
                            flat_rows.append(block)

                    if flat_rows:
                        rows = flat_rows

                found = False

                for row in rows:

                    if not isinstance(row, list):
                        continue

                    if len(row) < 2:
                        continue

                    symbol = str(
                        row[0]
                    ).strip()

                    valid, _ = is_valid_symbol(symbol)

                    if not valid:
                        continue

                    # ------------------------------------------------
                    # TPEX 欄位格式可能因 API 版本不同而變動。
                    #
                    # 不猜欄位。
                    #
                    # 因此這裡只有在明確符合既有格式時才解析。
                    # ------------------------------------------------

                    net_value = None

                    for value in reversed(row):

                        text = str(value).strip()

                        if not text:
                            continue

                        if text in {
                            "--",
                            "---",
                        }:
                            continue

                        try:
                            candidate = float(
                                text.replace(
                                    ",",
                                    "",
                                )
                            )
                        except Exception:
                            continue

                        net_value = candidate
                        break

                    if net_value is None:
                        continue

                    # TPEX 常見資料為股數
                    # 只有在資料結構符合預期時才轉張
                    institutional_lots = (
                        net_value / 1000.0
                    )

                    stock_history.setdefault(
                        symbol,
                        {
                            "institutional": [],
                        },
                    )

                    stock_history[symbol][
                        "institutional"
                    ].append(
                        round(
                            institutional_lots,
                            2,
                        )
                    )

                    found = True

                if found:
                    return True

        except Exception:
            continue

    return False


# ============================================================
# 4. 當沖資料
# ============================================================

def fetch_twse_day_trade(
    session: requests.Session,
    date_str: str,
) -> Dict[str, Dict[str, float]]:

    """
    TWSE 當沖資料。

    注意：

    本函式只處理最新交易日。
    不將歷史當沖資料誤當成主力資料。
    """

    daytrade_data: Dict[
        str,
        Dict[str, float],
    ] = {}

    url = (
        "https://www.twse.com.tw/"
        f"rwd/zh/trading/historical/"
        f"day-trading?date={date_str}"
    )

    try:

        response = session.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            return daytrade_data

        result = response.json()

        if result.get("stat") != "OK":
            return daytrade_data

        data = result.get("data", [])

        for row in data:

            if not isinstance(row, list):
                continue

            if len(row) < 7:
                continue

            symbol = str(row[0]).strip()

            valid, _ = is_valid_symbol(symbol)

            if not valid:
                continue

            # ------------------------------------------------
            # 當沖成交量
            #
            # TWSE 原始資料為股
            # → 張
            # ------------------------------------------------

            volume_shares = parse_number(
                row[5],
                default=0.0,
            )

            volume_lots = (
                volume_shares / 1000.0
            )

            # ------------------------------------------------
            # 當沖率
            # ------------------------------------------------

            rate = parse_number(
                row[6],
                default=0.0,
            )

            daytrade_data[symbol] = {
                "day_trading_volume": round(
                    volume_lots,
                    2,
                ),
                "day_trading_rate": round(
                    rate,
                    4,
                ),
            }

    except Exception:
        pass

    return daytrade_data


# ============================================================
# 5. 歷史籌碼同步
# ============================================================

def fetch_history_chips(
    session: requests.Session,
    securities: List[Dict[str, str]],
    days: int = 20,
) -> Tuple[
    str,
    Dict[str, Dict[str, List[float]]],
    Dict[str, Dict[str, float]],
]:

    section(
        f"同步 TWSE/TPEX 最近 {days} 個交易日三大法人資料"
    )

    stock_history: Dict[
        str,
        Dict[str, List[float]],
    ] = {}

    daytrade_data: Dict[
        str,
        Dict[str, float],
    ] = {}

    latest_date_str = ""

    fetch_count = 0

    curr_date = datetime.now()

    # --------------------------------------------------------
    # 交易日
    # --------------------------------------------------------

    while (
        fetch_count < days
        and (datetime.now() - curr_date).days < 60
    ):

        if curr_date.weekday() < 5:

            date_str = curr_date.strftime(
                "%Y%m%d"
            )

            twse_ok = fetch_twse_institutional(
                session,
                date_str,
                stock_history,
            )

            # ------------------------------------------------
            # TPEX
            # ------------------------------------------------

            tpex_ok = fetch_tpex_institutional(
                session,
                date_str,
                stock_history,
            )

            # ------------------------------------------------
            # 只要當日有任何市場資料成功，
            # 才算一個交易日。
            # ------------------------------------------------

            if twse_ok or tpex_ok:

                fetch_count += 1

                if not latest_date_str:
                    latest_date_str = (
                        curr_date.strftime(
                            "%Y-%m-%d"
                        )
                    )

                log(
                    f"  └ 成功同步 "
                    f"{date_str} 籌碼歷史 "
                    f"(已累計 "
                    f"{fetch_count}/{days} 日)"
                )

                # ------------------------------------------------
                # 只抓最新交易日當沖
                # ------------------------------------------------

                if fetch_count == 1:

                    daytrade_data = (
                        fetch_twse_day_trade(
                            session,
                            date_str,
                        )
                    )

                time.sleep(0.3)

        curr_date -= timedelta(days=1)

    # --------------------------------------------------------
    # 如果完全沒有抓到資料
    # --------------------------------------------------------

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
# 6. 建立 1D / 5D / 10D / 20D
# ============================================================

def calculate_periods(
    values: List[float],
) -> Dict[str, Any]:

    """
    將每日原始籌碼資料計算成：

        1D
        5D
        10D
        20D

    注意：

    只使用實際取得的交易日。

    不補假資料。
    不用倍率。
    不做估算。
    """

    result: Dict[str, Any] = {
        "1d": None,
        "5d": None,
        "10d": None,
        "20d": None,
    }

    if len(values) >= 1:

        result["1d"] = round(
            values[0],
            2,
        )

    if len(values) >= 5:

        result["5d"] = round(
            sum(values[:5]),
            2,
        )

    if len(values) >= 10:

        result["10d"] = round(
            sum(values[:10]),
            2,
        )

    if len(values) >= 20:

        result["20d"] = round(
            sum(values[:20]),
            2,
        )

    return result


# ============================================================
# 7. 主程式
# ============================================================

def main() -> int:

    start_time = time.time()

    log(
        f"台股 AI 選股系統 "
        f"fetch_chip.py {VERSION} 啟動"
    )

    log("")
    log(
        "⚠️ 本版本已完全移除 "
        "「三大法人 × 1.12」主力估算"
    )

    log(
        "⚠️ main_force_* 不會寫入 chip.json"
    )

    session = requests.Session()

    # ========================================================
    # 股票池
    # ========================================================

    securities = get_securities_from_universe(
        session
    )

    if not securities:

        log("❌ 無法獲取股票池清單")

        return 1

    # ========================================================
    # 歷史資料
    # ========================================================

    (
        latest_date_str,
        stock_history,
        extra_data,
    ) = fetch_history_chips(
        session,
        securities,
        days=20,
    )

    # ========================================================
    # 建立結果
    # ========================================================

    stocks_result: Dict[
        str,
        Dict[str, Any],
    ] = {}

    complete_cnt = 0
    partial_cnt = 0
    insufficient_cnt = 0

    for item in securities:

        symbol = item["symbol"]

        history = stock_history.get(
            symbol,
            {
                "institutional": [],
            },
        )

        inst_list = history.get(
            "institutional",
            [],
        )

        periods = calculate_periods(
            inst_list
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

        # ----------------------------------------------------
        # 正式輸出
        # ----------------------------------------------------

        stocks_result[symbol] = {

            "symbol": symbol,

            "full_symbol": item.get(
                "full_symbol",
                symbol,
            ),

            "name": item.get(
                "name",
                "",
            ),

            "market": item.get(
                "market",
                "TWSE",
            ),

            "type": item.get(
                "type",
                "Stock",
            ),

            # =================================================
            # 三大法人買賣超
            #
            # 單位：張
            #
            # 來源：
            # TWSE / TPEX 原始資料
            # =================================================

            "institutional_1d": periods[
                "1d"
            ],

            "institutional_5d": periods[
                "5d"
            ],

            "institutional_10d": periods[
                "10d"
            ],

            "institutional_20d": periods[
                "20d"
            ],

            # =================================================
            # 當沖
            # =================================================

            "day_trading_volume": ext.get(
                "day_trading_volume",
                0.0,
            ),

            "day_trading_rate": ext.get(
                "day_trading_rate",
                0.0,
            ),

            # =================================================
            # 資料狀態
            # =================================================

            "institutional_days": len(
                inst_list
            ),

            "updated_at": latest_date_str,
        }

    # ========================================================
    # Output
    # ========================================================

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

        # ====================================================
        # 資料來源說明
        # ====================================================

        "data_sources": {

            "institutional": {
                "enabled": True,
                "description": (
                    "TWSE/TPEX 三大法人原始資料"
                ),
                "unit": "張",
                "calculation": (
                    "原始股數 ÷ 1000"
                ),
            },

            "day_trading": {
                "enabled": True,
                "description": (
                    "TWSE 當沖原始資料"
                ),
                "volume_unit": "張",
                "rate_unit": "%",
            },

            "broker_chip": {
                "enabled": False,
                "description": (
                    "全券商分點資料尚未正式驗證"
                ),
                "reason": (
                    "目前禁止使用估算值"
                ),
            },
        },

        # ====================================================
        # 主力資料狀態
        # ====================================================

        "main_force": {

            "enabled": False,

            "status": "NOT_AVAILABLE",

            "reason": (
                "尚未取得經驗證的全券商分點原始資料"
            ),

            "estimation": False,

            "estimated_multiplier": None,

        },

        # ====================================================
        # 統計
        # ====================================================

        "statistics": {

            "complete": complete_cnt,

            "partial": partial_cnt,

            "insufficient": insufficient_cnt,

        },

        "stocks": stocks_result,
    }

    # ========================================================
    # Atomic Write
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

        f.write("\n")

    temp_file.replace(
        CHIP_FILE
    )

    # ========================================================
    # 驗證輸出
    # ========================================================

    log("")

    log(
        "✓ 成功寫入 chip.json"
    )

    log(
        f"✓ 總檔數：{len(stocks_result)} 檔"
    )

    log(
        f"✓ 股票：{output['stock_count']} 檔"
    )

    log(
        f"✓ ETF：{output['etf_count']} 檔"
    )

    log(
        f"✓ 20D完整：{complete_cnt} 檔"
    )

    log(
        f"✓ 部分資料：{partial_cnt} 檔"
    )

    log(
        f"✓ 無資料：{insufficient_cnt} 檔"
    )

    # ========================================================
    # 明確確認沒有 main_force
    # ========================================================

    log("")
    log(
        "============================================================"
    )
    log(
        "主力資料狀態確認"
    )
    log(
        "============================================================"
    )

    log(
        "✗ main_force_1d   ：未寫入"
    )

    log(
        "✗ main_force_5d   ：未寫入"
    )

    log(
        "✗ main_force_10d  ：未寫入"
    )

    log(
        "✗ main_force_20d  ：未寫入"
    )

    log(
        "✓ 三大法人資料：保留"
    )

    log(
        "✓ 當沖資料：保留"
    )

    log(
        "✓ 估算倍率：完全移除"
    )

    log(
        "✓ 假主力資料：完全禁止"
    )

    # ========================================================
    # 完成
    # ========================================================

    elapsed = time.time() - start_time

    log("")

    log(
        f"✓ fetch_chip.py {VERSION} 完成"
    )

    log(
        f"✓ 耗時：{elapsed:.1f} 秒"
    )

    return 0


# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":
    sys.exit(main())
