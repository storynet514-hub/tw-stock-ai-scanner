#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
verify_fetch_chip_v5_output.py

============================================================
目的
============================================================

驗證正式 fetch_chip.py V5.0.1 已產生的：

Data/chip.json

本程式：

1. 不呼叫 CMoney
2. 不執行 fetch_chip.py
3. 不執行 fetch_all()
4. 不修改 fetch_chip.py
5. 不修改 chip.json
6. 不探測任何 API
7. 不讀取完整 Universe
8. 固定只驗證 4 檔股票

固定測試：

2337 旺宏
2426 鼎元
2368 金像電
3081 聯亞

============================================================
驗證內容
============================================================

A. 股票是否存在

B. history 是否存在

C. history 是否至少 20 筆

D. 日期是否：
   - 合法
   - 無重複
   - 由新到舊排序

E. 是否存在明顯異常舊日期

F. main_force 數值是否為數字

G. 重新計算：

   1D
   5D
   10D
   20D

H. 與 chip.json：

   main_force_1d
   main_force_5d
   main_force_10d
   main_force_20d

逐項比對。

I. 確認：

main_force_20d

確實等於：

最近 20 筆 history 的
main_force 加總

============================================================
輸出
============================================================

Data/chip_v5_output_verification.json

注意：

本程式只是驗證工具。
不修改正式資料。
"""


from __future__ import annotations

import json
import sys

from datetime import datetime
from pathlib import Path


# ============================================================
# 路徑
# ============================================================

BASE_DIR = (
    Path(__file__)
    .resolve()
    .parent
    .parent
)

DATA_DIR = (
    BASE_DIR
    / "Data"
)

CHIP_FILE = (
    DATA_DIR
    / "chip.json"
)

OUTPUT_FILE = (
    DATA_DIR
    / "chip_v5_output_verification.json"
)


# ============================================================
# 固定測試股票
# ============================================================

TEST_STOCKS = {
    "2337": "旺宏",
    "2426": "鼎元",
    "2368": "金像電",
    "3081": "聯亞",
}


# ============================================================
# 允許誤差
# ============================================================

EPSILON = 0.01


# ============================================================
# Log
# ============================================================

def log(message=""):
    print(
        message,
        flush=True
    )


def section(title):

    log("")
    log("=" * 72)
    log(title)
    log("=" * 72)


# ============================================================
# 日期
# ============================================================

def parse_date(value):

    if value is None:
        return None

    try:

        return datetime.strptime(
            str(value),
            "%Y/%m/%d"
        )

    except Exception:

        return None


# ============================================================
# 數值
# ============================================================

def parse_float(value):

    if value is None:
        return None

    if isinstance(
        value,
        bool
    ):
        return None

    try:

        return float(value)

    except Exception:

        return None


# ============================================================
# 比較數值
# ============================================================

def values_equal(
    actual,
    expected
):

    if actual is None:
        return False

    if expected is None:
        return False

    return (
        abs(
            float(actual)
            - float(expected)
        )
        <= EPSILON
    )


# ============================================================
# 重新計算
# ============================================================

def calculate_periods(history):

    values = [
        float(row["main_force"])
        for row in history
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
# 驗證單一股票
# ============================================================

def verify_stock(
    symbol,
    expected_name,
    record
):

    result = {

        "symbol": symbol,

        "expected_name":
            expected_name,

        "actual_name":
            record.get(
                "name"
            ),

        "passed": True,

        "errors": [],

        "warnings": [],

        "history_count":
            0,

        "unique_date_count":
            0,

        "latest_date":
            None,

        "oldest_date":
            None,

        "actual": {},

        "calculated": {},

        "checks": {},
    }

    # ========================================================
    # 1. 股票名稱
    # ========================================================

    actual_name = str(
        record.get(
            "name",
            ""
        )
    ).strip()

    if actual_name != expected_name:

        result["warnings"].append(
            "股票名稱不一致："
            f"預期={expected_name} "
            f"實際={actual_name}"
        )


    # ========================================================
    # 2. history
    # ========================================================

    history = record.get(
        "history"
    )

    if not isinstance(
        history,
        list
    ):

        result["passed"] = False

        result["errors"].append(
            "history 不是 list"
        )

        return result


    result[
        "history_count"
    ] = len(history)


    # ========================================================
    # 3. history 筆數
    # ========================================================

    if len(history) < 20:

        result["passed"] = False

        result["errors"].append(
            "history 少於 20 筆："
            f"{len(history)}"
        )


    # ========================================================
    # 4. 解析 history
    # ========================================================

    parsed_rows = []

    invalid_rows = []

    for index, row in enumerate(
        history,
        start=1
    ):

        if not isinstance(
            row,
            dict
        ):

            invalid_rows.append(
                {
                    "index": index,
                    "reason":
                        "不是 object",
                }
            )

            continue

        date_text = row.get(
            "date"
        )

        force_value = row.get(
            "main_force"
        )

        dt = parse_date(
            date_text
        )

        number = parse_float(
            force_value
        )

        if dt is None:

            invalid_rows.append(
                {
                    "index": index,
                    "date": date_text,
                    "reason":
                        "日期格式錯誤",
                }
            )

            continue

        if number is None:

            invalid_rows.append(
                {
                    "index": index,
                    "date": date_text,
                    "main_force":
                        force_value,
                    "reason":
                        "main_force 不是數字",
                }
            )

            continue

        parsed_rows.append(
            {
                "date": date_text,
                "datetime": dt,
                "main_force": number,
            }
        )


    # ========================================================
    # 5. 無效資料
    # ========================================================

    if invalid_rows:

        result["passed"] = False

        result["errors"].append(
            f"存在 {len(invalid_rows)} 筆"
            "無效 history 資料"
        )


    # ========================================================
    # 6. 日期重複
    # ========================================================

    dates = [
        row["date"]
        for row in parsed_rows
    ]

    unique_dates = set(
        dates
    )

    result[
        "unique_date_count"
    ] = len(unique_dates)

    if len(unique_dates) != len(dates):

        result["passed"] = False

        duplicate_dates = []

        seen = set()

        for date in dates:

            if date in seen:
                if date not in duplicate_dates:
                    duplicate_dates.append(
                        date
                    )
            else:
                seen.add(date)

        result["errors"].append(
            "存在重複日期："
            + ", ".join(
                duplicate_dates
            )
        )


    # ========================================================
    # 7. 日期排序
    # ========================================================

    for i in range(
        len(parsed_rows) - 1
    ):

        current = (
            parsed_rows[i]["datetime"]
        )

        next_date = (
            parsed_rows[i + 1]["datetime"]
        )

        if current < next_date:

            result["passed"] = False

            result["errors"].append(
                "history 日期不是由新到舊排序"
            )

            break


    # ========================================================
    # 8. 最新 / 最舊日期
    # ========================================================

    if parsed_rows:

        result[
            "latest_date"
        ] = parsed_rows[0][
            "date"
        ]

        result[
            "oldest_date"
        ] = parsed_rows[-1][
            "date"
        ]


    # ========================================================
    # 9. 明顯舊日期
    # ========================================================

    old_rows = []

    for row in parsed_rows:

        if row["datetime"].year < 2025:

            old_rows.append(
                {
                    "date":
                        row["date"],
                    "main_force":
                        row["main_force"],
                }
            )

    if old_rows:

        result["passed"] = False

        result["errors"].append(
            "發現 2025 年以前的異常舊資料："
            f"{len(old_rows)} 筆"
        )

        result["old_date_rows"] = (
            old_rows
        )

    else:

        result["old_date_rows"] = []


    # ========================================================
    # 10. 實際 chip.json 數值
    # ========================================================

    actual_fields = [
        "main_force_1d",
        "main_force_5d",
        "main_force_10d",
        "main_force_20d",
    ]

    for field in actual_fields:

        result[
            "actual"
        ][field] = record.get(
            field
        )


    # ========================================================
    # 11. 重新計算
    # ========================================================

    if len(parsed_rows) >= 20:

        calculation_history = [
            {
                "date":
                    row["date"],
                "main_force":
                    row["main_force"],
            }
            for row in parsed_rows[:20]
        ]

        calculated = calculate_periods(
            calculation_history
        )

    else:

        calculated = calculate_periods(
            [
                {
                    "date":
                        row["date"],
                    "main_force":
                        row["main_force"],
                }
                for row in parsed_rows
            ]
        )

    result[
        "calculated"
    ] = calculated


    # ========================================================
    # 12. 逐項比對
    # ========================================================

    for field in actual_fields:

        actual = result[
            "actual"
        ].get(
            field
        )

        expected = result[
            "calculated"
        ].get(
            field
        )

        passed = values_equal(
            actual,
            expected
        )

        result[
            "checks"
        ][field] = {

            "actual":
                actual,

            "calculated":
                expected,

            "passed":
                passed,
        }

        if not passed:

            result["passed"] = False

            result["errors"].append(
                f"{field} 計算不一致："
                f"chip.json={actual}, "
                f"重新計算={expected}"
            )


    # ========================================================
    # 13. status
    # ========================================================

    status = record.get(
        "status"
    )

    if (
        len(parsed_rows) >= 20
        and status != "complete"
    ):

        result["warnings"].append(
            "history 已有至少 20 筆，"
            f"但 status={status}"
        )


    # ========================================================
    # 14. 最終摘要
    # ========================================================

    return result


# ============================================================
# 載入 chip.json
# ============================================================

def load_chip():

    section(
        "載入正式 Data/chip.json"
    )

    if not CHIP_FILE.exists():

        raise RuntimeError(
            f"找不到：{CHIP_FILE}"
        )

    with CHIP_FILE.open(
        "r",
        encoding="utf-8"
    ) as f:

        data = json.load(f)

    if not isinstance(
        data,
        dict
    ):

        raise RuntimeError(
            "chip.json 不是 object"
        )

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict
    ):

        raise RuntimeError(
            "chip.json stocks 不是 dict"
        )

    log(
        f"schema_version："
        f"{data.get('schema_version')}"
    )

    log(
        f"generated_at："
        f"{data.get('generated_at')}"
    )

    log(
        f"universe_count："
        f"{data.get('universe_count')}"
    )

    log(
        f"chip.json 實際股票數："
        f"{len(stocks)}"
    )

    return data


# ============================================================
# 儲存驗證結果
# ============================================================

def save_result(
    output
):

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    temp_file = (
        OUTPUT_FILE.with_suffix(
            ".json.tmp"
        )
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

    # 驗證 JSON 可以重新讀取

    with temp_file.open(
        "r",
        encoding="utf-8"
    ) as f:

        json.load(f)

    temp_file.replace(
        OUTPUT_FILE
    )

    log(
        f"✓ 驗證結果已寫入："
        f"{OUTPUT_FILE}"
    )


# ============================================================
# Main
# ============================================================

def main():

    section(
        "台股 AI 選股系統"
    )

    log(
        "fetch_chip.py V5.0.1"
    )

    log(
        "正式輸出資料驗證"
    )

    log("")
    log(
        "固定驗證股票："
    )

    for symbol, name in TEST_STOCKS.items():

        log(
            f"{symbol} {name}"
        )

    log("")
    log(
        "測試限制："
    )

    log(
        "✓ 不呼叫 CMoney"
    )

    log(
        "✓ 不執行 fetch_chip.py"
    )

    log(
        "✓ 不執行 fetch_all()"
    )

    log(
        "✓ 不測試 Universe"
    )

    log(
        "✓ 不修改 chip.json"
    )

    log(
        "✓ 只驗證正式輸出"
    )


    try:

        data = load_chip()

        stocks = data[
            "stocks"
        ]

        results = {}

        total_passed = 0
        total_failed = 0


        # ====================================================
        # 固定四檔
        # ====================================================

        for symbol, name in TEST_STOCKS.items():

            section(
                f"{symbol} {name}"
            )

            if symbol not in stocks:

                log(
                    "❌ chip.json 找不到此股票"
                )

                results[symbol] = {

                    "symbol":
                        symbol,

                    "expected_name":
                        name,

                    "passed":
                        False,

                    "errors": [
                        "chip.json 找不到此股票"
                    ],

                    "warnings": [],
                }

                total_failed += 1

                continue


            record = stocks[
                symbol
            ]

            verification = verify_stock(
                symbol,
                name,
                record
            )

            results[symbol] = (
                verification
            )


            # ------------------------------------------------
            # 顯示結果
            # ------------------------------------------------

            log(
                f"history："
                f"{verification['history_count']} 筆"
            )

            log(
                f"日期："
                f"{verification['latest_date']} "
                f"→ "
                f"{verification['oldest_date']}"
            )

            log("")

            log(
                "chip.json 實際值："
            )

            for field in [
                "main_force_1d",
                "main_force_5d",
                "main_force_10d",
                "main_force_20d",
            ]:

                log(
                    f"   {field} = "
                    f"{verification['actual'].get(field)}"
                )

            log("")

            log(
                "重新計算值："
            )

            for field in [
                "main_force_1d",
                "main_force_5d",
                "main_force_10d",
                "main_force_20d",
            ]:

                log(
                    f"   {field} = "
                    f"{verification['calculated'].get(field)}"
                )

            log("")

            log(
                "計算驗證："
            )

            for field, check in (
                verification[
                    "checks"
                ].items()
            ):

                if check["passed"]:

                    log(
                        f"   ✓ {field}"
                    )

                else:

                    log(
                        f"   ❌ {field}"
                    )


            if verification[
                "warnings"
            ]:

                log("")

                for warning in (
                    verification[
                        "warnings"
                    ]
                ):

                    log(
                        f"⚠️ {warning}"
                    )


            if verification[
                "errors"
            ]:

                log("")

                for error in (
                    verification[
                        "errors"
                    ]
                ):

                    log(
                        f"❌ {error}"
                    )


            log("")

            if verification[
                "passed"
            ]:

                log(
                    "✓ 此股票驗證通過"
                )

                total_passed += 1

            else:

                log(
                    "❌ 此股票驗證失敗"
                )

                total_failed += 1


        # ====================================================
        # 建立輸出
        # ====================================================

        output = {

            "schema_version":
                "V5.0_OUTPUT_VERIFICATION_1.0",

            "generated_at":
                datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),

            "source":
                "Data/chip.json",

            "fetch_chip_version":
                data.get(
                    "schema_version"
                ),

            "scope":
                {
                    "stocks_tested":
                        list(
                            TEST_STOCKS.keys()
                        ),

                    "stock_count":
                        len(TEST_STOCKS),

                    "cmoney_requests":
                        0,

                    "fetch_all_executed":
                        False,
                },

            "summary":
                {
                    "passed":
                        total_passed,

                    "failed":
                        total_failed,

                    "all_passed":
                        total_failed == 0,
                },

            "results":
                results,
        }


        save_result(
            output
        )


        # ====================================================
        # 最終結果
        # ====================================================

        section(
            "V5.0.1 正式輸出驗證完成"
        )

        log(
            f"固定測試股票："
            f"{len(TEST_STOCKS)} 檔"
        )

        log(
            f"通過："
            f"{total_passed}"
        )

        log(
            f"失敗："
            f"{total_failed}"
        )

        log("")

        if total_failed == 0:

            log(
                "========================================"
            )

            log(
                "✓ 四檔正式輸出全部驗證通過"
            )

            log(
                "✓ 1D 計算正確"
            )

            log(
                "✓ 5D 計算正確"
            )

            log(
                "✓ 10D 計算正確"
            )

            log(
                "✓ 20D 計算正確"
            )

            log(
                "✓ history 日期結構正常"
            )

            log(
                "✓ 沒有發現 2025 年以前舊資料"
            )

            log(
                "========================================"
            )

            return 0

        else:

            log(
                "========================================"
            )

            log(
                "❌ 正式輸出驗證未全部通過"
            )

            log(
                "請依照上面的錯誤項目處理"
            )

            log(
                "========================================"
            )

            return 1


    except Exception as exc:

        log("")

        log(
            "=" * 72
        )

        log(
            "❌ 正式輸出驗證失敗"
        )

        log(
            "=" * 72
        )

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
