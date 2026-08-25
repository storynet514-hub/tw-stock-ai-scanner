#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
CHIP 全市場數值邏輯驗證器 V1.0

用途：
1. 驗證 Data/chip.json
2. 驗證 Universe ↔ Chip 股票池
3. 驗證 institutional 1D / 5D / 10D / 20D
4. 找出期間比例異常資料
5. 列出異常標的與實際數值
6. 不修改任何正式資料
7. 不依賴固定個股

注意：
institutional_* 的實際定義取決於資料來源。
本驗證器不假設它們一定是簡單的：
5D = 5 × 1D
10D = 10 × 1D
20D = 20 × 1D

因此：
「比例異常」只代表值得檢查，
不直接代表資料錯誤。
"""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime
from typing import Any


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

UNIVERSE_PATH = os.path.join(BASE_DIR, "Data", "universe.json")
CHIP_PATH = os.path.join(BASE_DIR, "Data", "chip.json")
REPORT_PATH = os.path.join(BASE_DIR, "Data", "chip_logic_report.json")

PERIODS = [
    "institutional_1d",
    "institutional_5d",
    "institutional_10d",
    "institutional_20d",
]


def line():
    print("=" * 72)


def section(title: str):
    print()
    line()
    print(title)
    line()


def load_json(path: str, label: str):
    if not os.path.exists(path):
        print(f"❌ {label} 不存在：{path}")
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"✓ {label} JSON 正常")
        return data
    except Exception as e:
        print(f"❌ {label} JSON 讀取失敗：{e}")
        return None


def is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def safe_float(value: Any):
    if not is_number(value):
        return None
    return float(value)


def ratio(a: float, b: float):
    """
    回傳 abs(a / b)。
    分母接近 0 時不計算。
    """
    if b is None:
        return None

    if abs(b) < 1e-12:
        return None

    return abs(a / b)


def sign(value: float):
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def classify_relationship(values: dict[str, float | None]):
    """
    對期間資料進行邏輯分類。

    這裡刻意不把「期間值沒有線性倍數關係」
    視為錯誤。

    主要檢查：
    - 型別
    - 有效期間數
    - 正負號突變
    - 明顯不合理的比例
    """

    available = {
        k: v for k, v in values.items()
        if v is not None
    }

    if not available:
        return {
            "status": "NO_DATA",
            "reason": "沒有任何有效 institutional 數值",
        }

    if len(available) == 1:
        return {
            "status": "INSUFFICIENT",
            "reason": "只有一個期間有資料",
        }

    # ------------------------------------------------------------
    # 正負號檢查
    # ------------------------------------------------------------

    signs = {
        k: sign(v)
        for k, v in available.items()
        if v != 0
    }

    positive = sum(1 for x in signs.values() if x > 0)
    negative = sum(1 for x in signs.values() if x < 0)

    sign_flip = positive > 0 and negative > 0

    # ------------------------------------------------------------
    # 期間比例
    # ------------------------------------------------------------

    r_5_1 = None
    r_10_5 = None
    r_20_10 = None
    r_10_1 = None
    r_20_1 = None

    if values["institutional_1d"] is not None:
        if values["institutional_5d"] is not None:
            r_5_1 = ratio(
                values["institutional_5d"],
                values["institutional_1d"],
            )

        if values["institutional_10d"] is not None:
            r_10_1 = ratio(
                values["institutional_10d"],
                values["institutional_1d"],
            )

        if values["institutional_20d"] is not None:
            r_20_1 = ratio(
                values["institutional_20d"],
                values["institutional_1d"],
            )

    if values["institutional_5d"] is not None:
        if values["institutional_10d"] is not None:
            r_10_5 = ratio(
                values["institutional_10d"],
                values["institutional_5d"],
            )

    if values["institutional_10d"] is not None:
        if values["institutional_20d"] is not None:
            r_20_10 = ratio(
                values["institutional_20d"],
                values["institutional_10d"],
            )

    ratios = {
        "5D/1D": r_5_1,
        "10D/5D": r_10_5,
        "20D/10D": r_20_10,
        "10D/1D": r_10_1,
        "20D/1D": r_20_1,
    }

    # ------------------------------------------------------------
    # 異常比例偵測
    # ------------------------------------------------------------

    suspicious = []

    # 非零分母下，極端倍數只標記為 suspicious。
    #
    # 注意：
    # 這不是財務邏輯上的絕對錯誤。
    # 因為期間買賣超可能因單日數值接近 0
    # 而自然產生巨大倍數。
    for name, r in ratios.items():
        if r is None:
            continue

        if r > 100:
            suspicious.append(
                f"{name}={r:.2f}x"
            )

    # ------------------------------------------------------------
    # 期間值大小關係
    # ------------------------------------------------------------

    abs_values = [
        abs(v)
        for v in available.values()
        if v is not None
    ]

    max_abs = max(abs_values) if abs_values else None
    min_nonzero_abs = min(
        [x for x in abs_values if x > 1e-12],
        default=None,
    )

    magnitude_ratio = None

    if max_abs is not None and min_nonzero_abs is not None:
        magnitude_ratio = max_abs / min_nonzero_abs

    if magnitude_ratio is not None and magnitude_ratio > 1000:
        suspicious.append(
            f"期間絕對值最大/最小={magnitude_ratio:.2f}x"
        )

    # ------------------------------------------------------------
    # 判定
    # ------------------------------------------------------------

    if suspicious:
        status = "SUSPICIOUS"

        reasons = list(suspicious)

        if sign_flip:
            reasons.append("不同期間正負號發生變化")

        return {
            "status": status,
            "reason": "; ".join(reasons),
            "sign_flip": sign_flip,
            "ratios": ratios,
        }

    if sign_flip:
        return {
            "status": "REVIEW",
            "reason": "不同期間正負號發生變化，但數值比例未達極端門檻",
            "sign_flip": True,
            "ratios": ratios,
        }

    return {
        "status": "NORMAL",
        "reason": "未發現明顯期間比例異常",
        "sign_flip": False,
        "ratios": ratios,
    }


def main():

    print()
    line()
    print("台股 AI 選股系統 CHIP 全市場數值邏輯驗證器 V1.0")
    line()
    print("驗證模式：全市場 institutional 期間邏輯")
    print("固定股票驗證：停用")
    print("正式資料：唯讀，不修改 chip.json")

    errors = []
    warnings = []

    # ============================================================
    # 1. 載入資料
    # ============================================================

    section("1. 載入 Universe / Chip")

    universe = load_json(
        UNIVERSE_PATH,
        "universe.json",
    )

    chip = load_json(
        CHIP_PATH,
        "chip.json",
    )

    if universe is None or chip is None:
        print("❌ 無法進行邏輯驗證")
        sys.exit(1)

    universe_stocks = universe.get("stocks", {})
    chip_stocks = chip.get("stocks", {})

    if not isinstance(universe_stocks, dict):
        errors.append("Universe stocks 不是 object")
        universe_stocks = {}

    if not isinstance(chip_stocks, dict):
        errors.append("Chip stocks 不是 object")
        chip_stocks = {}

    universe_symbols = set(universe_stocks.keys())
    chip_symbols = set(chip_stocks.keys())

    print(f"Universe：{len(universe_symbols)} 檔")
    print(f"Chip：{len(chip_symbols)} 檔")

    missing = sorted(universe_symbols - chip_symbols)
    extra = sorted(chip_symbols - universe_symbols)

    print(f"Universe → Chip 缺少：{len(missing)}")
    print(f"Chip → Universe 多出：{len(extra)}")

    if missing:
        errors.append(
            f"Universe → Chip 缺少 {len(missing)} 檔"
        )

    if extra:
        errors.append(
            f"Chip → Universe 多出 {len(extra)} 檔"
        )

    if not missing and not extra:
        print("✓ Universe / Chip 股票池完全一致")

    # ============================================================
    # 2. 全市場期間資料
    # ============================================================

    section("2. institutional 1D / 5D / 10D / 20D")

    counts = {
        p: {
            "valid": 0,
            "none": 0,
            "invalid": 0,
        }
        for p in PERIODS
    }

    all_results = {}

    for symbol in sorted(chip_symbols):

        stock = chip_stocks.get(symbol)

        if not isinstance(stock, dict):
            errors.append(
                f"{symbol}: stocks item 不是 object"
            )
            continue

        values = {}

        for period in PERIODS:

            value = safe_float(stock.get(period))

            values[period] = value

            if value is None:

                if stock.get(period) is None:
                    counts[period]["none"] += 1
                else:
                    counts[period]["invalid"] += 1

            else:
                counts[period]["valid"] += 1

        result = classify_relationship(values)

        all_results[symbol] = {
            "symbol": symbol,
            "name": stock.get("name"),
            "type": stock.get("type"),
            "market": stock.get("market"),
            "values": values,
            "analysis": result,
        }

    for period in PERIODS:
        print()
        print(period)
        print(
            f"  ✓ 有效：{counts[period]['valid']}"
        )
        print(
            f"  ○ None：{counts[period]['none']}"
        )
        print(
            f"  ❌ 無效：{counts[period]['invalid']}"
        )

        if counts[period]["invalid"] > 0:
            errors.append(
                f"{period} 有 {counts[period]['invalid']} 筆無效資料"
            )

    # ============================================================
    # 3. 邏輯結果統計
    # ============================================================

    section("3. 期間邏輯分類")

    status_counts = {
        "NORMAL": 0,
        "REVIEW": 0,
        "SUSPICIOUS": 0,
        "INSUFFICIENT": 0,
        "NO_DATA": 0,
    }

    for result in all_results.values():
        status = result["analysis"]["status"]

        if status not in status_counts:
            status_counts[status] = 0

        status_counts[status] += 1

    for status, count in status_counts.items():
        print(f"{status:12s}：{count}")

    # ============================================================
    # 4. 列出 SUSPICIOUS
    # ============================================================

    suspicious = [
        result
        for result in all_results.values()
        if result["analysis"]["status"] == "SUSPICIOUS"
    ]

    review = [
        result
        for result in all_results.values()
        if result["analysis"]["status"] == "REVIEW"
    ]

    section("4. SUSPICIOUS 標的")

    print(f"發現：{len(suspicious)} 檔")

    if suspicious:

        for index, result in enumerate(
            suspicious,
            start=1,
        ):

            values = result["values"]
            analysis = result["analysis"]

            print()
            print(
                f"[{index}] "
                f"{result['symbol']} "
                f"{result.get('name') or ''}"
            )

            print(
                "  1D  = "
                f"{values['institutional_1d']}"
            )

            print(
                "  5D  = "
                f"{values['institutional_5d']}"
            )

            print(
                "  10D = "
                f"{values['institutional_10d']}"
            )

            print(
                "  20D = "
                f"{values['institutional_20d']}"
            )

            print(
                "  原因："
                f"{analysis['reason']}"
            )

            ratios = analysis.get("ratios", {})

            for ratio_name, ratio_value in ratios.items():

                if ratio_value is not None:
                    print(
                        f"  {ratio_name} = "
                        f"{ratio_value:.4f}x"
                    )

    else:
        print("✓ 沒有發現極端期間比例")

    # ============================================================
    # 5. REVIEW
    # ============================================================

    section("5. REVIEW 標的")

    print(f"發現：{len(review)} 檔")

    if review:

        for index, result in enumerate(
            review,
            start=1,
        ):

            values = result["values"]

            print(
                f"[{index}] "
                f"{result['symbol']} "
                f"{result.get('name') or ''}"
            )

            print(
                f"  1D={values['institutional_1d']} "
                f"5D={values['institutional_5d']} "
                f"10D={values['institutional_10d']} "
                f"20D={values['institutional_20d']}"
            )

            print(
                f"  原因：{result['analysis']['reason']}"
            )

    # ============================================================
    # 6. 重要說明
    # ============================================================

    section("6. 邏輯判定原則")

    print("✓ 不要求 5D = 5 × 1D")
    print("✓ 不要求 10D = 10 × 1D")
    print("✓ 不要求 20D = 20 × 1D")
    print("✓ 不因期間數值不同而直接判錯")
    print("✓ 單純比例極端 → SUSPICIOUS")
    print("✓ 正負號跨期間變化 → REVIEW")
    print("✓ 資料不足 → INSUFFICIENT")
    print("✓ 不依賴固定股票")
    print("✓ 不修改 chip.json")

    # ============================================================
    # 7. 建立報告
    # ============================================================

    section("7. 建立 Logic Report")

    report = {
        "schema_version": "CHIP_LOGIC_REPORT_V1.0",
        "generated_at": datetime.now().astimezone().isoformat(),
        "source": {
            "universe": "Data/universe.json",
            "chip": "Data/chip.json",
        },
        "universe_count": len(universe_symbols),
        "chip_count": len(chip_symbols),
        "missing": missing,
        "extra": extra,
        "period_counts": counts,
        "status_counts": status_counts,
        "suspicious_count": len(suspicious),
        "review_count": len(review),
        "suspicious": suspicious,
        "review": review,
        "errors": errors,
        "warnings": warnings,
    }

    try:

        with open(
            REPORT_PATH,
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                report,
                f,
                ensure_ascii=False,
                indent=2,
            )

        print(
            f"✓ 已建立：{REPORT_PATH}"
        )

    except Exception as e:

        print(
            f"❌ Logic Report 建立失敗：{e}"
        )

        errors.append(
            f"Logic Report 建立失敗：{e}"
        )

    # ============================================================
    # FINAL
    # ============================================================

    section("FINAL CHIP LOGIC VERIFICATION")

    print(
        f"Universe：{len(universe_symbols)} 檔"
    )

    print(
        f"Chip：{len(chip_symbols)} 檔"
    )

    print(
        f"SUSPICIOUS：{len(suspicious)}"
    )

    print(
        f"REVIEW：{len(review)}"
    )

    print(
        f"錯誤：{len(errors)}"
    )

    print(
        f"警告：{len(warnings)}"
    )

    print()

    # 目前 V1.0 的目的，是「找出問題」，
    # 而不是因為比例異常就直接阻擋正式流程。
    #
    # 真正結構性錯誤才 exit 1。

    if errors:

        print("=" * 60)
        print("❌ CHIP LOGIC VERIFICATION FAILED")
        print("=" * 60)

        for error in errors:
            print(f"❌ {error}")

        sys.exit(1)

    print("=" * 60)
    print("✓ CHIP LOGIC VERIFICATION PASSED")
    print("=" * 60)

    print("✓ Universe / Chip 股票池一致")
    print("✓ institutional 數值型別正常")
    print("✓ 已完成 1D / 5D / 10D / 20D 邏輯掃描")
    print(
        f"✓ SUSPICIOUS：{len(suspicious)} "
        "（列出但不直接判定為錯誤）"
    )
    print(
        f"✓ REVIEW：{len(review)} "
        "（列出供下一階段分析）"
    )
    print("✓ 正式 chip.json 未被修改")
    print("✓ 不依賴固定個股")


if __name__ == "__main__":
    main()
