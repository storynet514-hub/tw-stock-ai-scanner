#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V5.0
固定股票基準測試版

============================================================
測試目的
============================================================

本測試不是重新實作 fetch_chip.py。

而是：

直接 import 正式版 fetch_chip.py V5.0

使用 V5.0 原本的：

1. request_page()
2. parse_main_force_table()
3. clean_history()
4. discover_more_urls()
5. build_pagination_urls()
6. fetch_20d_history()
7. calculate_periods()

因此：

測試版與正式版使用完全相同的抓取與計算邏輯。

============================================================
重要
============================================================

本程式：

不修改 fetch_chip.py
不修改正式 chip.json
不修改 Universe
不修改正式資料

只輸出：

Data/chip_test_v5.json

============================================================
固定測試股票
============================================================

2337 旺宏
2426 鼎元
2368 金像電
3081 艾訊

============================================================
驗證項目
============================================================

1. CMoney 是否成功取得
2. 是否解析「買賣超」
3. 是否取得至少20個交易日
4. 日期是否正確排序
5. 1D
6. 5D
7. 10D
8. 20D
9. 每個期間是否與 history 重新計算一致

============================================================
"""

from __future__ import annotations

import json
import sys
import time

from datetime import datetime
from pathlib import Path


# ============================================================
# 路徑
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent

BASE_DIR = SCRIPT_DIR.parent

DATA_DIR = BASE_DIR / "Data"

TEST_OUTPUT = DATA_DIR / "chip_test_v5.json"


# ============================================================
# 固定測試股票
# ============================================================

TEST_STOCKS = [
    {
        "symbol": "2337",
        "name": "旺宏",
        "market": "TW",
    },
    {
        "symbol": "2426",
        "name": "鼎元",
        "market": "TW",
    },
    {
        "symbol": "2368",
        "name": "金像電",
        "market": "TW",
    },
    {
        "symbol": "3081",
        "name": "艾訊",
        "market": "TWO",
    },
]


# ============================================================
# 匯入正式 V5.0
# ============================================================

try:

    import fetch_chip

except Exception as exc:

    print("")
    print("=" * 72)
    print("❌ 無法載入正式版 fetch_chip.py")
    print("=" * 72)
    print(f"原因：{exc}")
    print("")
    sys.exit(1)


# ============================================================
# 基準版本確認
# ============================================================

EXPECTED_VERSION = "V5.0"


def section(title):

    print("")
    print("=" * 72)
    print(title)
    print("=" * 72)


def log(message=""):

    print(message, flush=True)


# ============================================================
# 驗證正式版
# ============================================================

def verify_baseline():

    section("確認正式基準版本")

    version = getattr(
        fetch_chip,
        "VERSION",
        None
    )

    log(
        f"載入版本：{version}"
    )

    if version != EXPECTED_VERSION:

        raise RuntimeError(
            "目前載入的 fetch_chip.py "
            f"不是 {EXPECTED_VERSION}，"
            f"而是 {version}"
        )

    required_functions = [
        "request_page",
        "parse_main_force_table",
        "clean_history",
        "discover_more_urls",
        "build_pagination_urls",
        "fetch_20d_history",
        "calculate_periods",
    ]

    missing = []

    for name in required_functions:

        if not hasattr(
            fetch_chip,
            name
        ):

            missing.append(name)

    if missing:

        raise RuntimeError(
            "正式 V5.0 缺少必要函式："
            + ", ".join(missing)
        )

    log(
        "✓ 正式 fetch_chip.py V5.0 已確認"
    )

    log(
        "✓ 測試將直接使用 V5.0 原始函式"
    )


# ============================================================
# 重新計算驗證
# ============================================================

def calculate_again(history):

    values = [
        row["main_force"]
        for row in history
        if row.get(
            "main_force"
        ) is not None
    ]

    result = {
        "main_force_1d": None,
        "main_force_5d": None,
        "main_force_10d": None,
        "main_force_20d": None,
    }

    if len(values) >= 1:

        result[
            "main_force_1d"
        ] = round(
            sum(values[:1]),
            2
        )

    if len(values) >= 5:

        result[
            "main_force_5d"
        ] = round(
            sum(values[:5]),
            2
        )

    if len(values) >= 10:

        result[
            "main_force_10d"
        ] = round(
            sum(values[:10]),
            2
        )

    if len(values) >= 20:

        result[
            "main_force_20d"
        ] = round(
            sum(values[:20]),
            2
        )

    return result


# ============================================================
# 驗證 history
# ============================================================

def validate_history(
    symbol,
    history
):

    if not history:

        raise RuntimeError(
            f"{symbol} 沒有 history"
        )

    if len(history) < 20:

        raise RuntimeError(
            f"{symbol} history 只有 "
            f"{len(history)} 筆，"
            "不足20筆"
        )

    dates = [
        row.get("date")
        for row in history
    ]

    if any(
        not date
        for date in dates
    ):

        raise RuntimeError(
            f"{symbol} 存在空白日期"
        )

    if len(dates) != len(set(dates)):

        raise RuntimeError(
            f"{symbol} history 存在重複日期"
        )

    parsed_dates = []

    for date in dates:

        parsed_dates.append(
            datetime.strptime(
                date,
                "%Y/%m/%d"
            )
        )

    for i in range(
        len(parsed_dates) - 1
    ):

        if (
            parsed_dates[i]
            < parsed_dates[i + 1]
        ):

            raise RuntimeError(
                f"{symbol} 日期排序錯誤："
                f"{dates[i]} → {dates[i + 1]}"
            )

    for row in history:

        if "main_force" not in row:

            raise RuntimeError(
                f"{symbol} history 缺少 "
                "main_force"
            )

        if row["main_force"] is None:

            raise RuntimeError(
                f"{symbol} history 存在 "
                "main_force=None"
            )


# ============================================================
# 執行固定股票測試
# ============================================================

def run_test():

    section(
        "開始 V5.0 固定股票測試"
    )

    log(
        "注意："
        "以下所有抓取與計算均直接呼叫正式 V5.0。"
    )

    log("")

    session = fetch_chip.requests.Session()

    session.headers.update(
        fetch_chip.HEADERS
    )

    results = {}

    complete = 0

    failed = 0

    for index, stock in enumerate(
        TEST_STOCKS,
        start=1
    ):

        symbol = stock["symbol"]

        name = stock["name"]

        log(
            f"[{index}/{len(TEST_STOCKS)}] "
            f"{symbol} {name}"
        )

        record = {
            "symbol": symbol,
            "name": name,
            "market": stock["market"],
            "source": "CMoney",
            "baseline_version": EXPECTED_VERSION,

            "main_force_1d": None,
            "main_force_5d": None,
            "main_force_10d": None,
            "main_force_20d": None,

            "history_count": 0,

            "history": [],

            "status": "failed",

            "calculation_check": False,

            "error": None,
        }

        try:

            # ------------------------------------------------
            # 這裡直接使用正式 V5.0
            # ------------------------------------------------

            history = fetch_chip.fetch_20d_history(
                session,
                symbol
            )

            # ------------------------------------------------
            # 驗證歷史
            # ------------------------------------------------

            validate_history(
                symbol,
                history
            )

            # ------------------------------------------------
            # 這裡直接使用正式 V5.0
            # ------------------------------------------------

            periods = fetch_chip.calculate_periods(
                history
            )

            record.update(
                periods
            )

            record["history"] = history[:20]

            record["history_count"] = len(
                history
            )

            # ------------------------------------------------
            # 再獨立計算一次
            # ------------------------------------------------

            recalculated = calculate_again(
                history
            )

            calculation_ok = (
                periods["main_force_1d"]
                == recalculated["main_force_1d"]
                and
                periods["main_force_5d"]
                == recalculated["main_force_5d"]
                and
                periods["main_force_10d"]
                == recalculated["main_force_10d"]
                and
                periods["main_force_20d"]
                == recalculated["main_force_20d"]
            )

            record[
                "calculation_check"
            ] = calculation_ok

            if not calculation_ok:

                raise RuntimeError(
                    "V5.0 calculate_periods "
                    "與重新計算結果不一致"
                )

            record["status"] = "complete"

            complete += 1

            # ------------------------------------------------
            # 顯示結果
            # ------------------------------------------------

            log(
                f"   ✓ 歷史："
                f"{len(history)} 筆"
            )

            log(
                f"   1D  = "
                f"{record['main_force_1d']}"
            )

            log(
                f"   5D  = "
                f"{record['main_force_5d']}"
            )

            log(
                f"   10D = "
                f"{record['main_force_10d']}"
            )

            log(
                f"   20D = "
                f"{record['main_force_20d']}"
            )

            log(
                "   ✓ 計算一致"
            )

            log(
                "   最近20日："
            )

            for row in history:

                log(
                    f"      "
                    f"{row['date']}  "
                    f"{row['main_force']}"
                )

        except Exception as exc:

            failed += 1

            record["error"] = str(
                exc
            )

            log(
                f"   ❌ 測試失敗："
                f"{exc}"
            )

        results[
            symbol
        ] = record

        time.sleep(
            fetch_chip.REQUEST_DELAY
        )

    return (
        results,
        complete,
        failed
    )


# ============================================================
# 輸出測試結果
# ============================================================

def save_test_result(
    results,
    complete,
    failed
):

    section(
        "寫入測試結果"
    )

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    output = {

        "test_version":
            "V5.0_BASELINE_TEST",

        "baseline_version":
            EXPECTED_VERSION,

        "generated_at":
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "source":
            "CMoney",

        "purpose":
            "驗證正式 fetch_chip.py V5.0 "
            "固定股票結果",

        "test_stocks": [
            stock["symbol"]
            for stock in TEST_STOCKS
        ],

        "statistics": {

            "total":
                len(TEST_STOCKS),

            "complete":
                complete,

            "failed":
                failed,
        },

        "stocks":
            results,
    }

    temp_file = TEST_OUTPUT.with_suffix(
        ".json.tmp"
    )

    with temp_file.open(
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2
        )

    # --------------------------------------------------------
    # 寫入後重新讀取
    # --------------------------------------------------------

    with temp_file.open(
        "r",
        encoding="utf-8"
    ) as f:

        verify = json.load(f)

    if not isinstance(
        verify.get("stocks"),
        dict
    ):

        raise RuntimeError(
            "測試 JSON stocks 格式錯誤"
        )

    if len(
        verify["stocks"]
    ) != len(
        TEST_STOCKS
    ):

        raise RuntimeError(
            "測試 JSON 股票數量錯誤"
        )

    temp_file.replace(
        TEST_OUTPUT
    )

    log(
        f"✓ 測試結果："
        f"{TEST_OUTPUT}"
    )


# ============================================================
# 最終結果
# ============================================================

def final_report(
    results,
    complete,
    failed
):

    section(
        "V5.0 基準測試結果"
    )

    log(
        f"固定測試股票："
        f"{len(TEST_STOCKS)}"
    )

    log(
        f"完整：{complete}"
    )

    log(
        f"失敗：{failed}"
    )

    log("")

    for stock in TEST_STOCKS:

        symbol = stock["symbol"]

        record = results.get(
            symbol
        )

        if not record:

            log(
                f"{symbol}：❌ 無結果"
            )

            continue

        if (
            record["status"]
            == "complete"
        ):

            log(
                f"{symbol} "
                f"{record['name']}："
                f"✓"
            )

            log(
                f"   1D  = "
                f"{record['main_force_1d']}"
            )

            log(
                f"   5D  = "
                f"{record['main_force_5d']}"
            )

            log(
                f"   10D = "
                f"{record['main_force_10d']}"
            )

            log(
                f"   20D = "
                f"{record['main_force_20d']}"
            )

        else:

            log(
                f"{symbol} "
                f"{record['name']}：❌"
            )

            log(
                f"   原因："
                f"{record.get('error')}"
            )

    log("")

    if complete == len(
        TEST_STOCKS
    ):

        log(
            "================================================"
        )

        log(
            "✓ V5.0 固定股票基準測試全部通過"
        )

        log(
            "================================================"
        )

        return 0

    log(
        "================================================"
    )

    log(
        "❌ V5.0 固定股票測試未全部通過"
    )

    log(
        "================================================"
    )

    return 1


# ============================================================
# Main
# ============================================================

def main():

    start = time.time()

    log("")
    log("=" * 72)

    log(
        "台股 AI 選股系統"
    )

    log(
        "fetch_chip.py V5.0 "
        "固定股票基準測試"
    )

    log("=" * 72)

    log(
        "開始時間："
        + datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    try:

        verify_baseline()

        (
            results,
            complete,
            failed
        ) = run_test()

        save_test_result(
            results,
            complete,
            failed
        )

        elapsed = (
            time.time()
            - start
        )

        result_code = final_report(
            results,
            complete,
            failed
        )

        log("")
        log(
            f"總耗時："
            f"{elapsed:.1f} 秒"
        )

        log(
            f"測試檔案："
            f"{TEST_OUTPUT}"
        )

        return result_code

    except Exception as exc:

        log("")
        log("=" * 72)

        log(
            "❌ 測試執行失敗"
        )

        log("=" * 72)

        log(
            f"原因：{exc}"
        )

        return 1


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
