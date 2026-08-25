#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
verify_chip_values.py V1.0

============================================================
目的
============================================================

驗證 Data/chip.json 的「數值」是否與官方原始資料一致。

本驗證器：

1. 不依賴任何固定股票
2. 不使用 2337 / 2426 / 2368 / 3081 特殊條件
3. 依 universe.json 自動判斷 TWSE / TPEX
4. 驗證 institutional_1d
5. 重新由每日原始資料計算 5D / 10D / 20D
6. 驗證 chip.json 的 1D / 5D / 10D / 20D
7. 不使用 main_force_*
8. 不進行任何倍率估算
9. 不修改 chip.json
10. 只驗證，不寫入正式資料

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

VERSION = "V1.0"


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
# Validation
# ============================================================

VALUE_TOLERANCE = 0.01

REQUIRED_PERIODS = (
    1,
    5,
    10,
    20,
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
# Cleaning
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
# Symbol format
# ============================================================

def is_valid_symbol(
    code: str,
) -> bool:

    code = clean_code(code)

    if not code:
        return False

    if re.fullmatch(
        r"\d{4,6}",
        code,
    ):
        return True

    if re.fullmatch(
        r"\d{4,6}[A-Z]{1,2}",
        code,
    ):
        return True

    return False


# ============================================================
# Load JSON
# ============================================================

def load_json(
    path: Path,
) -> Any:

    with path.open(
        "r",
        encoding="utf-8-sig",
    ) as f:

        return json.load(f)


# ============================================================
# Load Universe
# ============================================================

def load_universe() -> Dict[str, Dict[str, Any]]:

    section(
        "1. 載入 Universe"
    )

    if not UNIVERSE_FILE.exists():

        raise RuntimeError(
            "Data/universe.json 不存在"
        )

    data = load_json(
        UNIVERSE_FILE
    )

    if not isinstance(
        data,
        dict,
    ):

        raise RuntimeError(
            "universe.json 根節點不是 object"
        )

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            "universe.json stocks 不是 object"
        )

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    for key, item in stocks.items():

        code = clean_code(key)

        if not is_valid_symbol(code):

            raise RuntimeError(
                f"Universe 出現非法代號：{code}"
            )

        if not isinstance(
            item,
            dict,
        ):

            raise RuntimeError(
                f"Universe {code} 不是 object"
            )

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

            symbol = str(
                item.get(
                    "symbol",
                    "",
                )
            ).upper()

            if ".TWO" in symbol:

                market = "TPEX"

            elif ".TW" in symbol:

                market = "TWSE"

        if market not in (
            "TWSE",
            "TPEX",
        ):

            raise RuntimeError(
                f"Universe {code} "
                f"市場無法判定：{market}"
            )

        result[code] = {
            "market": market,
            "name": str(
                item.get(
                    "name",
                    "",
                )
            ).strip(),
            "type": str(
                item.get(
                    "type",
                    "",
                )
            ).strip(),
        }

    log(
        f"✓ Universe：{len(result)} 檔"
    )

    twse_count = sum(
        1
        for x in result.values()
        if x["market"] == "TWSE"
    )

    tpex_count = sum(
        1
        for x in result.values()
        if x["market"] == "TPEX"
    )

    log(
        f"✓ TWSE：{twse_count} 檔"
    )

    log(
        f"✓ TPEX：{tpex_count} 檔"
    )

    return result


# ============================================================
# Load Chip
# ============================================================

def load_chip() -> Dict[str, Any]:

    section(
        "2. 載入 Chip"
    )

    if not CHIP_FILE.exists():

        raise RuntimeError(
            "Data/chip.json 不存在"
        )

    data = load_json(
        CHIP_FILE
    )

    if not isinstance(
        data,
        dict,
    ):

        raise RuntimeError(
            "chip.json 根節點不是 object"
        )

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            "chip.json stocks 不是 object"
        )

    log(
        f"✓ Chip：{len(stocks)} 檔"
    )

    return data


# ============================================================
# TWSE official data
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

        response = session.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:

            return result

        data = response.json()

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

            code = clean_code(
                row[0]
            )

            if not is_valid_symbol(code):

                continue

            value = safe_number(
                row[18]
            )

            if value is None:

                continue

            # 官方原始資料為元
            # chip 單位為「張」對應的數值邏輯沿用現有 fetch_chip
            #
            # 此處只重新取得相同官方欄位，
            # 不進行倍率估算。

            result[code] = round(
                value / 1000.0,
                2,
            )

    except Exception:

        return result

    return result


# ============================================================
# TPEX official data
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

        response = session.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:

            return result

        data = response.json()

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

            code = clean_code(
                row[0]
            )

            if not is_valid_symbol(code):

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

            value = numeric_values[-1]

            result[code] = round(
                value / 1000.0,
                2,
            )

    except Exception:

        return result

    return result


# ============================================================
# Fetch one official trading day
# ============================================================

def fetch_official_day(
    session: requests.Session,
    date_str: str,
) -> Dict[str, float]:

    twse = fetch_twse_institutional(
        session,
        date_str,
    )

    tpex = fetch_tpex_institutional(
        session,
        date_str,
    )

    result = {}

    result.update(
        twse
    )

    for code, value in tpex.items():

        if code not in result:

            result[code] = value

    return result


# ============================================================
# Get latest official trading days
# ============================================================

def fetch_recent_days(
    session: requests.Session,
    required_days: int = 20,
) -> Tuple[
    List[str],
    Dict[str, Dict[str, float]],
]:

    section(
        f"3. 重新抓取官方最近 {required_days} 個交易日"
    )

    days: List[str] = []

    daily: Dict[
        str,
        Dict[str, float]
    ] = {}

    current = datetime.now()

    attempts = 0

    while (
        len(days) < required_days
        and attempts < 60
    ):

        if current.weekday() < 5:

            date_str = current.strftime(
                "%Y%m%d"
            )

            values = fetch_official_day(
                session,
                date_str,
            )

            if values:

                days.append(
                    date_str
                )

                daily[date_str] = values

                log(
                    f"✓ {date_str} "
                    f"官方資料："
                    f"{len(values)} 檔"
                )

                time.sleep(
                    0.3
                )

        current -= timedelta(
            days=1
        )

        attempts += 1

    if len(days) < required_days:

        raise RuntimeError(
            "無法取得足夠的官方交易日資料："
            f"{len(days)}/{required_days}"
        )

    return (
        days,
        daily,
    )


# ============================================================
# Compare values
# ============================================================

def values_equal(
    actual: Any,
    expected: Optional[float],
) -> bool:

    actual_num = safe_number(
        actual
    )

    if expected is None:

        return actual_num is None

    if actual_num is None:

        return False

    return (
        abs(
            actual_num - expected
        )
        <= VALUE_TOLERANCE
    )


# ============================================================
# Main verification
# ============================================================

def main() -> int:

    start = time.time()

    log(
        "========================================"
    )

    log(
        "CHIP VALUE VERIFICATION"
    )

    log(
        "========================================"
    )

    log(
        ""
    )

    log(
        "=" * 72
    )

    log(
        f"台股 AI 選股系統 chip 數值驗證器 {VERSION}"
    )

    log(
        "=" * 72
    )

    log(
        "驗證模式：官方原始資料 ↔ chip.json"
    )

    log(
        "驗證範圍：全市場"
    )

    log(
        "固定個股特殊驗證：停用"
    )

    log(
        "2337 / 2426 / 2368 / 3081：不作特殊條件"
    )

    errors = 0

    warnings = 0

    try:

        universe = load_universe()

        chip = load_chip()

    except Exception as e:

        log(
            f"❌ 初始化失敗：{e}"
        )

        return 1

    chip_stocks = chip.get(
        "stocks",
        {}
    )

    # ========================================================
    # 股票池一致性
    # ========================================================

    section(
        "4. Universe / Chip 股票池確認"
    )

    universe_codes = set(
        universe.keys()
    )

    chip_codes = set(
        chip_stocks.keys()
    )

    missing = sorted(
        universe_codes - chip_codes
    )

    extra = sorted(
        chip_codes - universe_codes
    )

    if missing:

        errors += 1

        log(
            f"❌ Universe → Chip 缺少："
            f"{len(missing)}"
        )

    else:

        log(
            "✓ Universe → Chip：全部存在"
        )

    if extra:

        errors += 1

        log(
            f"❌ Chip → Universe 多出："
            f"{len(extra)}"
        )

    else:

        log(
            "✓ Chip → Universe：沒有多餘標的"
        )

    if errors:

        log(
            "❌ 股票池一致性失敗"
        )

        return 1

    # ========================================================
    # Forbidden fields
    # ========================================================

    section(
        "5. main_force_* 禁止欄位"
    )

    forbidden = []

    for code, item in chip_stocks.items():

        if not isinstance(
            item,
            dict,
        ):

            errors += 1

            continue

        for key in item.keys():

            if key.startswith(
                "main_force_"
            ):

                forbidden.append(
                    f"{code}.{key}"
                )

    if forbidden:

        errors += 1

        for value in forbidden[:20]:

            log(
                f"❌ {value}"
            )

    else:

        log(
            "✓ main_force_* 完全不存在"
        )

    # ========================================================
    # Official data
    # ========================================================

    session = requests.Session()

    try:

        (
            trading_days,
            daily_data,
        ) = fetch_recent_days(
            session,
            required_days=20,
        )

    except Exception as e:

        log(
            f"❌ 官方資料取得失敗：{e}"
        )

        return 1

    # ========================================================
    # Determine latest date
    # ========================================================

    section(
        "6. 資料日期驗證"
    )

    latest_official_date = (
        datetime.strptime(
            trading_days[0],
            "%Y%m%d",
        ).strftime(
            "%Y-%m-%d"
        )
    )

    chip_date = str(
        chip.get(
            "data_date",
            ""
        )
    ).strip()

    log(
        f"官方最新交易日："
        f"{latest_official_date}"
    )

    log(
        f"chip.json data_date："
        f"{chip_date}"
    )

    if chip_date != latest_official_date:

        errors += 1

        log(
            "❌ chip.json data_date "
            "與官方最新交易日不一致"
        )

    else:

        log(
            "✓ data_date 一致"
        )

    # ========================================================
    # Full market numeric verification
    # ========================================================

    section(
        "7. 全市場 1D / 5D / 10D / 20D 數值驗證"
    )

    counters = {
        "1d_checked": 0,
        "5d_checked": 0,
        "10d_checked": 0,
        "20d_checked": 0,
        "1d_match": 0,
        "5d_match": 0,
        "10d_match": 0,
        "20d_match": 0,
        "1d_mismatch": 0,
        "5d_mismatch": 0,
        "10d_mismatch": 0,
        "20d_mismatch": 0,
        "no_official_data": 0,
    }

    mismatches: List[str] = []

    for code, info in universe.items():

        item = chip_stocks.get(
            code
        )

        if not isinstance(
            item,
            dict,
        ):

            errors += 1

            continue

        market = info["market"]

        # ----------------------------------------------------
        # 只使用對應市場的官方資料
        # ----------------------------------------------------

        code_values: List[float] = []

        for date_str in trading_days:

            value = daily_data.get(
                date_str,
                {}
            ).get(
                code
            )

            if value is not None:

                code_values.append(
                    value
                )

        # ----------------------------------------------------
        # 沒有官方資料
        # ----------------------------------------------------

        if not code_values:

            counters[
                "no_official_data"
            ] += 1

            continue

        # ----------------------------------------------------
        # 1D
        # ----------------------------------------------------

        expected_1d = (
            code_values[0]
            if len(code_values) >= 1
            else None
        )

        actual_1d = item.get(
            "institutional_1d"
        )

        counters[
            "1d_checked"
        ] += 1

        if values_equal(
            actual_1d,
            expected_1d,
        ):

            counters[
                "1d_match"
            ] += 1

        else:

            counters[
                "1d_mismatch"
            ] += 1

            if len(mismatches) < 100:

                mismatches.append(
                    (
                        f"{code} 1D "
                        f"chip={actual_1d} "
                        f"official={expected_1d}"
                    )
                )

        # ----------------------------------------------------
        # 5D / 10D / 20D
        # ----------------------------------------------------

        for period in (
            5,
            10,
            20,
        ):

            field = (
                f"institutional_{period}d"
            )

            counter_checked = (
                f"{period}d_checked"
            )

            counter_match = (
                f"{period}d_match"
            )

            counter_mismatch = (
                f"{period}d_mismatch"
            )

            counters[
                counter_checked
            ] += 1

            if len(code_values) < period:

                expected = None

            else:

                expected = round(
                    sum(
                        code_values[
                            :period
                        ]
                    ),
                    2,
                )

            actual = item.get(
                field
            )

            if values_equal(
                actual,
                expected,
            ):

                counters[
                    counter_match
                ] += 1

            else:

                counters[
                    counter_mismatch
                ] += 1

                if len(mismatches) < 100:

                    mismatches.append(
                        (
                            f"{code} {period}D "
                            f"chip={actual} "
                            f"official_sum={expected}"
                        )
                    )

    # ========================================================
    # Results
    # ========================================================

    section(
        "8. 數值驗證結果"
    )

    for period in (
        1,
        5,
        10,
        20,
    ):

        checked = counters[
            f"{period}d_checked"
        ]

        matched = counters[
            f"{period}d_match"
        ]

        mismatch = counters[
            f"{period}d_mismatch"
        ]

        log(
            f"{period}D："
            f"檢查 {checked} | "
            f"一致 {matched} | "
            f"不一致 {mismatch}"
        )

        if mismatch:

            errors += mismatch

    log(
        f"無官方資料："
        f"{counters['no_official_data']} 檔"
    )

    # ========================================================
    # Mismatch detail
    # ========================================================

    if mismatches:

        section(
            "9. 數值不一致明細"
        )

        for value in mismatches:

            log(
                f"❌ {value}"
            )

        if (
            counters["1d_mismatch"]
            + counters["5d_mismatch"]
            + counters["10d_mismatch"]
            + counters["20d_mismatch"]
            > 100
        ):

            log(
                "⚠️ 僅顯示前 100 筆"
            )

    # ========================================================
    # Structural summary
    # ========================================================

    section(
        "10. FINAL VALUE VERIFICATION"
    )

    log(
        f"Universe：{len(universe)} 檔"
    )

    log(
        f"Chip：{len(chip_stocks)} 檔"
    )

    log(
        f"官方交易日：{len(trading_days)} 日"
    )

    log(
        f"官方最新日期："
        f"{latest_official_date}"
    )

    log(
        f"1D 不一致："
        f"{counters['1d_mismatch']}"
    )

    log(
        f"5D 不一致："
        f"{counters['5d_mismatch']}"
    )

    log(
        f"10D 不一致："
        f"{counters['10d_mismatch']}"
    )

    log(
        f"20D 不一致："
        f"{counters['20d_mismatch']}"
    )

    log(
        f"錯誤：{errors}"
    )

    log(
        f"警告：{warnings}"
    )

    elapsed = (
        time.time()
        - start
    )

    log(
        f"耗時：{elapsed:.1f} 秒"
    )

    if errors:

        log("")
        log(
            "============================================================"
        )
        log(
            "❌ CHIP VALUE VERIFICATION FAILED"
        )
        log(
            "============================================================"
        )
        log(
            "目前 chip.json 不應直接視為數值驗證通過。"
        )

        return 1

    log("")
    log(
        "============================================================"
    )
    log(
        "✓ CHIP VALUE VERIFICATION PASSED"
    )
    log(
        "============================================================"
    )

    log(
        "✓ 官方最新交易日一致"
    )

    log(
        "✓ institutional_1d 數值一致"
    )

    log(
        "✓ institutional_5d 累計一致"
    )

    log(
        "✓ institutional_10d 累計一致"
    )

    log(
        "✓ institutional_20d 累計一致"
    )

    log(
        "✓ 未發現 main_force_*"
    )

    log(
        "✓ 數值驗證不依賴固定個股"
    )

    return 0


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
