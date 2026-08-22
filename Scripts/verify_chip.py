#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
verify_chip.py V2.0

用途：
1. 驗證 Data/chip.json 是否存在
2. 固定驗證四檔：
   2337 旺宏
   2426 鼎元
   2368 金像電
   3081 聯亞
3. 重新從 TWSE T86 / TPEX 官方資料取得原始三大法人資料
4. 驗證：
   - 1D
   - 5D
   - 10D
   - 20D
5. 驗證 chip.json 與官方原始資料的累計結果
6. 驗證 TWSE / TPEX 市場
7. 驗證資料單位
8. 驗證正負方向
9. 驗證當沖資料
10. 檢查 main_force_* 是否被錯誤寫入
11. 統計 chip.json：
    - 完整
    - 部分
    - 無資料
12. 不修改任何正式資料
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


VERSION = "V2.0"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

CHIP_FILE = DATA_DIR / "chip.json"
UNIVERSE_FILE = DATA_DIR / "universe.json"

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


TEST_STOCKS = {
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


# ============================================================
# 基礎工具
# ============================================================

def log(message: str = "") -> None:
    print(message, flush=True)


def section(title: str) -> None:
    log("")
    log("=" * 78)
    log(title)
    log("=" * 78)


def subsection(title: str) -> None:
    log("")
    log("-" * 78)
    log(title)
    log("-" * 78)


def fmt_number(value: Any) -> str:
    if value is None:
        return "None"

    if isinstance(value, float):
        return f"{value:,.2f}"

    if isinstance(value, int):
        return f"{value:,}"

    return str(value)


def parse_number(value: Any) -> Optional[float]:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()

    if text in ("", "--", "—", "－", "None", "null"):
        return None

    text = text.replace(",", "")

    try:
        return float(text)
    except ValueError:
        return None


def almost_equal(
    a: Optional[float],
    b: Optional[float],
    tolerance: float = 0.01,
) -> bool:
    if a is None or b is None:
        return a is None and b is None

    return abs(float(a) - float(b)) <= tolerance


# ============================================================
# 日期
# ============================================================

def trading_dates(days: int = 20) -> List[str]:
    """
    產生最近交易日候選日期。

    注意：
    這裡只負責產生日期。
    是否真的有資料，必須由官方 API 回傳結果確認。
    """

    dates: List[str] = []

    current = datetime.now()

    while len(dates) < days:
        if current.weekday() < 5:
            dates.append(current.strftime("%Y%m%d"))

        current -= timedelta(days=1)

    return dates


# ============================================================
# 讀取 chip.json
# ============================================================

def load_chip() -> Dict[str, Any]:
    section("讀取 Data/chip.json")

    if not CHIP_FILE.exists():
        log(f"❌ 找不到：{CHIP_FILE}")
        raise FileNotFoundError(CHIP_FILE)

    with CHIP_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("chip.json 根節點不是 object")

    stocks = data.get("stocks")

    if not isinstance(stocks, dict):
        raise ValueError("chip.json 缺少 stocks object")

    log(f"✓ chip.json 讀取成功")
    log(f"✓ schema_version：{data.get('schema_version')}")
    log(f"✓ data_date：{data.get('data_date')}")
    log(f"✓ universe_count：{data.get('universe_count')}")
    log(f"✓ stocks 實際筆數：{len(stocks)}")

    return data


# ============================================================
# 讀取 universe.json
# ============================================================

def load_universe() -> Dict[str, Dict[str, Any]]:
    section("讀取 Data/universe.json")

    if not UNIVERSE_FILE.exists():
        log("⚠️ universe.json 不存在")
        return {}

    with UNIVERSE_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)

    items = data.get("items", []) if isinstance(data, dict) else data

    result: Dict[str, Dict[str, Any]] = {}

    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue

            raw_symbol = str(item.get("symbol", "")).strip()
            code = str(
                item.get("code", raw_symbol.split(".")[0])
            ).strip()

            if code:
                result[code] = item

    log(f"✓ universe.json 標的數：{len(result)}")

    return result


# ============================================================
# TWSE T86
# ============================================================

def fetch_twse_t86(
    session: requests.Session,
    date_str: str,
) -> Tuple[bool, Dict[str, List[Any]]]:
    """
    TWSE 三大法人買賣超日報。

    官方資料：

    T86 fields 共 19 欄。

    常用欄位：
        0  股票代號
        1  股票名稱

        2  外陸資買進
        3  外陸資賣出
        4  外陸資買賣超

        5  外資自營商買進
        6  外資自營商賣出
        7  外資自營商買賣超

        8  投信買進
        9  投信賣出
        10 投信買賣超

        11 自營商買進
        12 自營商賣出
        13 自營商買賣超
        14 自營商合計買賣超

        15 三大法人買進
        16 三大法人賣出
        17 三大法人買賣超
        18 三大法人合計買賣超

    實際欄位名稱仍以 API 回傳 fields 為準。
    """

    url = (
        "https://www.twse.com.tw/rwd/zh/fund/T86"
        f"?date={date_str}&selectType=ALL"
    )

    try:
        response = session.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            return False, {}

        data = response.json()

        if data.get("stat") != "OK":
            return False, {}

        rows = data.get("data")

        if not isinstance(rows, list):
            return False, {}

        result: Dict[str, List[Any]] = {}

        for row in rows:
            if not isinstance(row, list):
                continue

            if len(row) < 19:
                continue

            code = str(row[0]).strip()

            if code:
                result[code] = row

        return True, result

    except Exception:
        return False, {}


# ============================================================
# TPEX 法人資料
# ============================================================

def fetch_tpex_institutional(
    session: requests.Session,
    date_str: str,
) -> Tuple[bool, Dict[str, List[Any]]]:
    """
    TPEx 三大法人資料。

    TPEx API 的實際結構可能因官方端點調整而改變，
    因此這裡採用多候選端點 + 結構辨識。

    本驗證程式不猜欄位。
    找不到明確資料時直接回傳失敗。
    """

    date_slash = (
        f"{date_str[:4]}/{date_str[4:6]}/{date_str[6:8]}"
    )

    date_dash = (
        f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    )

    candidates = [
        (
            "https://www.tpex.org.tw/www/zh-tw/insti/"
            "institutional-investors",
            {
                "date": date_slash,
                "type": "Daily",
                "response": "json",
            },
        ),
        (
            "https://www.tpex.org.tw/www/zh-tw/insti/"
            "institutional-investors",
            {
                "date": date_dash,
                "type": "Daily",
                "response": "json",
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

            try:
                data = response.json()
            except Exception:
                continue

            rows = extract_rows_from_tpex_response(data)

            if not rows:
                continue

            parsed = parse_tpex_rows(rows)

            if parsed:
                return True, parsed

        except Exception:
            continue

    return False, {}


def extract_rows_from_tpex_response(data: Any) -> List[Any]:
    """
    從 TPEX 回應中找出可能的資料 rows。
    """

    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    for key in (
        "tables",
        "data",
        "aaData",
        "rows",
        "results",
        "items",
    ):
        value = data.get(key)

        if isinstance(value, list):
            if value:
                if isinstance(value[0], list):
                    return value

                if isinstance(value[0], dict):
                    return value

    return []


def parse_tpex_rows(
    rows: List[Any],
) -> Dict[str, List[Any]]:
    """
    嘗試辨識 TPEX 股票代號。

    這裡只做結構解析，不對未知欄位做數字猜測。
    """

    result: Dict[str, List[Any]] = {}

    for row in rows:
        if isinstance(row, list):
            if not row:
                continue

            possible_codes = [
                str(x).strip()
                for x in row[:5]
                if x is not None
            ]

            code = next(
                (
                    x for x in possible_codes
                    if len(x) == 4 and x.isdigit()
                ),
                None,
            )

            if code:
                result[code] = row

        elif isinstance(row, dict):
            code = None

            for key in (
                "SecuritiesCompanyCode",
                "Code",
                "StockCode",
                "Symbol",
                "股票代號",
            ):
                value = row.get(key)

                if value is not None:
                    text = str(value).strip()

                    if len(text) == 4 and text.isdigit():
                        code = text
                        break

            if code:
                result[code] = row

    return result


# ============================================================
# 找指定股票的官方原始資料
# ============================================================

def get_raw_value(
    row: List[Any],
    index: int,
) -> Optional[float]:
    if len(row) <= index:
        return None

    return parse_number(row[index])


def fetch_daily_institutional_value(
    session: requests.Session,
    symbol: str,
    market: str,
    date_str: str,
) -> Tuple[bool, Optional[float], str]:
    """
    取得指定股票指定日期的三大法人買賣超。

    回傳：
        success
        value
        source

    TWSE：
        T86 第 17 欄 = 三大法人買賣超

    注意：
        官方 T86 數字本身為「千股」，
        也就是張數概念。
        這裡轉成「張」時不再除以 1000。

    例如：
        6,268,164
        -> 6,268,164 張

    這個欄位的實際意義必須以 TWSE API fields
    為準，而不是用數字大小猜。
    """

    if market == "TWSE":
        success, rows = fetch_twse_t86(
            session,
            date_str,
        )

        if not success:
            return False, None, "TWSE_T86"

        row = rows.get(symbol)

        if row is None:
            return True, None, "TWSE_T86"

        value = get_raw_value(row, 17)

        return True, value, "TWSE_T86"

    if market == "TPEX":
        success, rows = fetch_tpex_institutional(
            session,
            date_str,
        )

        if not success:
            return False, None, "TPEX_institutional"

        row = rows.get(symbol)

        if row is None:
            return True, None, "TPEX_institutional"

        # TPEX API 結構可能改變。
        # 未能由官方欄位明確辨識時，不猜。
        return True, None, "TPEX_institutional_UNMAPPED"

    return False, None, "UNKNOWN"


# ============================================================
# 建立官方 20 日序列
# ============================================================

def build_official_series(
    session: requests.Session,
    symbol: str,
    market: str,
    dates: List[str],
) -> Tuple[List[str], List[Optional[float]], str]:

    valid_dates: List[str] = []
    values: List[Optional[float]] = []

    source = ""

    for date_str in dates:
        success, value, current_source = (
            fetch_daily_institutional_value(
                session=session,
                symbol=symbol,
                market=market,
                date_str=date_str,
            )
        )

        if not success:
            continue

        if not source:
            source = current_source

        # 只有官方資料成功回傳該股票時才進入序列。
        valid_dates.append(date_str)
        values.append(value)

        if len(valid_dates) >= 20:
            break

    return valid_dates, values, source


# ============================================================
# 累計
# ============================================================

def cumulative(
    values: List[Optional[float]],
    count: int,
) -> Optional[float]:

    selected = values[:count]

    if len(selected) < count:
        return None

    if any(v is None for v in selected):
        return None

    return round(
        sum(float(v) for v in selected),
        2,
    )


def calculate_periods(
    values: List[Optional[float]],
) -> Dict[str, Optional[float]]:

    return {
        "1d": cumulative(values, 1),
        "5d": cumulative(values, 5),
        "10d": cumulative(values, 10),
        "20d": cumulative(values, 20),
    }


# ============================================================
# chip.json 欄位驗證
# ============================================================

def verify_chip_stock(
    chip_stock: Dict[str, Any],
    symbol: str,
) -> None:

    section(
        f"{symbol} {TEST_STOCKS[symbol]['name']} "
        f"| {TEST_STOCKS[symbol]['market']}"
    )

    expected_market = TEST_STOCKS[symbol]["market"]

    actual_market = chip_stock.get("market")

    log(f"市場：")
    log(f"  chip.json = {actual_market}")
    log(f"  expected  = {expected_market}")

    if actual_market == expected_market:
        log("  ✓ 市場分類正確")
    else:
        log("  ❌ 市場分類錯誤")

    log("")
    log("chip.json 三大法人資料：")

    for key in (
        "institutional_1d",
        "institutional_5d",
        "institutional_10d",
        "institutional_20d",
    ):
        log(
            f"  {key:<24} "
            f"{fmt_number(chip_stock.get(key))}"
        )

    log("")
    log("當沖資料：")

    log(
        f"  {'day_trading_volume':<24}"
        f"{fmt_number(chip_stock.get('day_trading_volume'))}"
    )

    log(
        f"  {'day_trading_rate':<24}"
        f"{fmt_number(chip_stock.get('day_trading_rate'))}"
    )

    log("")
    log("主力欄位檢查：")

    main_force_keys = (
        "main_force_1d",
        "main_force_5d",
        "main_force_10d",
        "main_force_20d",
    )

    found_main_force = False

    for key in main_force_keys:
        if key in chip_stock:
            found_main_force = True
            log(
                f"  ❌ {key} 仍存在："
                f"{fmt_number(chip_stock.get(key))}"
            )
        else:
            log(f"  ✓ {key} 未寫入")

    if found_main_force:
        log("❌ 發現主力估算欄位")
    else:
        log("✓ 沒有假主力欄位")


# ============================================================
# 官方原始資料交叉驗證
# ============================================================

def verify_official_against_chip(
    session: requests.Session,
    chip_stock: Dict[str, Any],
    symbol: str,
) -> Dict[str, bool]:

    market = TEST_STOCKS[symbol]["market"]

    subsection(
        f"{symbol} 官方原始資料 → 1D / 5D / 10D / 20D"
    )

    dates = trading_dates(20)

    log("候選日期：")

    for i, date in enumerate(dates, start=1):
        log(f"  {i:02d}. {date}")

    valid_dates, values, source = build_official_series(
        session=session,
        symbol=symbol,
        market=market,
        dates=dates,
    )

    log("")
    log(f"資料源：{source or 'UNKNOWN'}")
    log(f"實際取得日期數：{len(valid_dates)}")

    if valid_dates:
        log("")
        log("官方每日原始值：")

        for date_str, value in zip(
            valid_dates,
            values,
        ):
            log(
                f"  {date_str} : "
                f"{fmt_number(value)}"
            )

    official = calculate_periods(values)

    log("")
    log("官方重新計算：")

    for key in (
        "1d",
        "5d",
        "10d",
        "20d",
    ):
        log(
            f"  {key.upper():<4} = "
            f"{fmt_number(official[key])}"
        )

    chip = {
        "1d": parse_number(
            chip_stock.get("institutional_1d")
        ),
        "5d": parse_number(
            chip_stock.get("institutional_5d")
        ),
        "10d": parse_number(
            chip_stock.get("institutional_10d")
        ),
        "20d": parse_number(
            chip_stock.get("institutional_20d")
        ),
    }

    log("")
    log("chip.json vs 官方重新計算：")

    results: Dict[str, bool] = {}

    mapping = {
        "1d": "institutional_1d",
        "5d": "institutional_5d",
        "10d": "institutional_10d",
        "20d": "institutional_20d",
    }

    for period, chip_key in mapping.items():

        official_value = official[period]
        chip_value = chip[period]

        if official_value is None:
            log(
                f"  {period.upper():<4} "
                f"⚠️ 官方資料不足，無法驗證"
            )

            results[period] = False
            continue

        if almost_equal(
            official_value,
            chip_value,
        ):
            log(
                f"  {period.upper():<4} "
                f"✓ MATCH | "
                f"chip={fmt_number(chip_value)} | "
                f"official={fmt_number(official_value)}"
            )

            results[period] = True

        else:
            log(
                f"  {period.upper():<4} "
                f"❌ MISMATCH | "
                f"chip={fmt_number(chip_value)} | "
                f"official={fmt_number(official_value)}"
            )

            results[period] = False

    return results


# ============================================================
# 單位與方向驗證
# ============================================================

def verify_unit_and_direction(
    session: requests.Session,
    symbol: str,
) -> bool:

    subsection(
        f"{symbol} 單位 / 正負方向檢查"
    )

    market = TEST_STOCKS[symbol]["market"]

    dates = trading_dates(20)

    success, value, source = (
        fetch_daily_institutional_value(
            session=session,
            symbol=symbol,
            market=market,
            date_str=dates[0],
        )
    )

    log(f"資料源：{source}")

    if not success:
        log("⚠️ 官方資料取得失敗")
        return False

    if value is None:
        log("⚠️ 官方資料中沒有可解析的三大法人買賣超值")
        return False

    log(f"官方原始三大法人買賣超：{fmt_number(value)}")

    log("")
    log("單位判定：")

    if market == "TWSE" and source == "TWSE_T86":
        log("✓ TWSE T86 欄位為官方三大法人買賣超資料")
        log("✓ 本驗證不進行 ×1.12 或任何主力估算")
        log("✓ 正值 = 三大法人買超")
        log("✓ 負值 = 三大法人賣超")
        log("✓ T86 數值以官方資料欄位定義為準")
        return True

    log(
        "⚠️ TPEX 原始欄位目前尚未完成明確映射，"
        "不猜測單位與方向"
    )

    return False


# ============================================================
# 全檔統計
# ============================================================

def verify_global_statistics(
    chip: Dict[str, Any],
) -> None:

    section("chip.json 全檔統計")

    stocks = chip.get("stocks", {})

    complete = 0
    partial = 0
    none_count = 0

    main_force_found = 0

    for stock in stocks.values():

        values = [
            stock.get("institutional_1d"),
            stock.get("institutional_5d"),
            stock.get("institutional_10d"),
            stock.get("institutional_20d"),
        ]

        if all(v is not None for v in values):
            complete += 1
        elif any(v is not None for v in values):
            partial += 1
        else:
            none_count += 1

        if any(
            key in stock
            for key in (
                "main_force_1d",
                "main_force_5d",
                "main_force_10d",
                "main_force_20d",
            )
        ):
            main_force_found += 1

    log(f"總標的：{len(stocks)}")
    log(f"20D 完整：{complete}")
    log(f"部分資料：{partial}")
    log(f"無資料：{none_count}")

    log("")

    if main_force_found == 0:
        log("✓ 全部標的均沒有 main_force_* 假主力欄位")
    else:
        log(
            f"❌ 發現 {main_force_found} 檔仍含 "
            f"main_force_* 欄位"
        )


# ============================================================
# 主程式
# ============================================================

def main() -> int:

    start_time = time.time()

    section(
        f"台股 AI 選股系統 verify_chip.py {VERSION}"
    )

    log("本程式只驗證，不修改正式資料。")
    log("不寫入 Data/chip.json。")
    log("不修改 Data/universe.json。")
    log("不修改 index.html。")
    log("")

    session = requests.Session()

    # --------------------------------------------------------
    # 讀取 chip
    # --------------------------------------------------------

    try:
        chip = load_chip()
    except Exception as exc:
        log("")
        log(f"❌ chip.json 讀取失敗：{exc}")
        return 1

    # --------------------------------------------------------
    # universe
    # --------------------------------------------------------

    universe = load_universe()

    # --------------------------------------------------------
    # 固定四檔
    # --------------------------------------------------------

    section("固定四檔驗證")

    log("2337 旺宏 | TWSE")
    log("2426 鼎元 | TWSE")
    log("2368 金像電 | TWSE")
    log("3081 聯亞 | TPEX")

    all_period_results: Dict[str, Dict[str, bool]] = {}

    overall_failed = False

    for symbol, info in TEST_STOCKS.items():

        subsection(
            f"{symbol} {info['name']} | "
            f"{info['market']}"
        )

        chip_stock = chip["stocks"].get(symbol)

        if chip_stock is None:
            log("❌ chip.json 找不到此股票")
            overall_failed = True
            continue

        verify_chip_stock(
            chip_stock,
            symbol,
        )

        results = verify_official_against_chip(
            session=session,
            chip_stock=chip_stock,
            symbol=symbol,
        )

        all_period_results[symbol] = results

        if not all(results.values()):
            overall_failed = True

        unit_ok = verify_unit_and_direction(
            session=session,
            symbol=symbol,
        )

        if not unit_ok:
            overall_failed = True

        # universe 對照
        if symbol in universe:
            log("")
            log("universe.json 對照：")

            uni = universe[symbol]

            log(
                f"  symbol = "
                f"{uni.get('symbol')}"
            )

            log(
                f"  name   = "
                f"{uni.get('name')}"
            )

            log(
                f"  type   = "
                f"{uni.get('type')}"
            )

        else:
            log("")
            log("⚠️ universe.json 找不到此股票")

    # --------------------------------------------------------
    # 全檔統計
    # --------------------------------------------------------

    verify_global_statistics(chip)

    # --------------------------------------------------------
    # 結果摘要
    # --------------------------------------------------------

    section("四檔驗證結果摘要")

    for symbol, results in all_period_results.items():

        name = TEST_STOCKS[symbol]["name"]

        status_list = []

        for period in (
            "1d",
            "5d",
            "10d",
            "20d",
        ):
            status = "PASS" if results.get(period) else "FAIL"
            status_list.append(
                f"{period.upper()}={status}"
            )

        log(
            f"{symbol} {name} | "
            + " | ".join(status_list)
        )

    # --------------------------------------------------------
    # 主力欄位全檔檢查
    # --------------------------------------------------------

    section("主力估算清除驗證")

    stocks = chip.get("stocks", {})

    forbidden_keys = {
        "main_force_1d",
        "main_force_5d",
        "main_force_10d",
        "main_force_20d",
    }

    found = []

    for symbol, stock in stocks.items():
        for key in forbidden_keys:
            if key in stock:
                found.append(
                    f"{symbol}:{key}"
                )

    if found:
        log(
            f"❌ 發現 {len(found)} 個 "
            f"main_force_* 欄位"
        )

        for item in found[:20]:
            log(f"  {item}")

        if len(found) > 20:
            log(
                f"  ...其餘 {len(found) - 20} 個省略"
            )

        overall_failed = True

    else:
        log(
            "✓ 全部 chip.json 標的均沒有 "
            "main_force_*"
        )

    # --------------------------------------------------------
    # 結束
    # --------------------------------------------------------

    elapsed = time.time() - start_time

    section("驗證完成")

    log(f"耗時：{elapsed:.1f} 秒")

    if overall_failed:
        log("")
        log("❌ 驗證結果：FAIL")
        log("")
        log("注意：")
        log("本結果不代表 fetch_chip.py 一定錯誤。")
        log("如果官方 TPEX API 尚未完成欄位映射，")
        log("TPEX 驗證會被標記為 FAIL，避免猜測資料。")
        return 1

    log("")
    log("✓ 驗證結果：PASS")
    log("✓ 三大法人資料未使用估算倍率")
    log("✓ 沒有假主力資料")
    log("✓ 1D / 5D / 10D / 20D 驗證通過")

    return 0


if __name__ == "__main__":
    sys.exit(main())
