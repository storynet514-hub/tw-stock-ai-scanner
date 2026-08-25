#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
verify_chip_quality.py V1.0

============================================================
目的
============================================================

全市場 chip.json 資料品質驗證。

本驗證器：

1. 不依賴任何固定股票
2. 不指定 2337 / 2426 / 2368 / 3081
3. 全量掃描 Universe / Chip
4. 驗證 institutional 1D / 5D / 10D / 20D
5. 驗證 1D / 5D / 10D / 20D 數值合理性
6. 驗證 day-trading 欄位
7. 驗證數值型別
8. 驗證 NaN / Infinity
9. 驗證明顯異常值
10. 驗證 Stock / ETF 分布
11. 驗證 TWSE / TPEX 分布
12. 驗證資料日期
13. 驗證統計數字
14. 驗證禁止 main_force_* 欄位
15. 不修改任何正式資料

注意：

本 V1.0 不把「沒有籌碼資料」直接判定為錯誤。

因為：

「標的沒有資料」

與

「API 沒有抓到資料」

必須在下一層透過來源交叉驗證區分。

============================================================
"""

from __future__ import annotations

import json
import math
import re
import sys

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


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
# Output
# ============================================================

QUALITY_REPORT_FILE = (
    DATA_DIR / "chip_quality_report.json"
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
# Symbol
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


# ============================================================
# Number
# ============================================================

def is_real_number(value: Any) -> bool:

    if value is None:
        return False

    if isinstance(value, bool):
        return False

    if not isinstance(
        value,
        (int, float),
    ):
        return False

    return math.isfinite(
        float(value)
    )


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
# Main
# ============================================================

def main() -> int:

    section(
        f"台股 AI 選股系統 "
        f"chip 全市場資料品質驗證器 {VERSION}"
    )

    log(
        "驗證模式：全市場數值 / 結構 / 邏輯驗證"
    )

    log(
        "固定股票驗證：停用"
    )

    log(
        "2337 / 2426 / 2368 / 3081：不作為特殊驗證條件"
    )

    log(
        "正式資料：唯讀，不修改 chip.json"
    )

    errors = 0
    warnings = 0

    # ========================================================
    # 1. File
    # ========================================================

    section(
        "1. 檔案存在性"
    )

    if not UNIVERSE_FILE.exists():

        log(
            "❌ Data/universe.json 不存在"
        )

        return 1

    if not CHIP_FILE.exists():

        log(
            "❌ Data/chip.json 不存在"
        )

        return 1

    log(
        "✓ Data/universe.json 存在"
    )

    log(
        "✓ Data/chip.json 存在"
    )

    # ========================================================
    # 2. JSON
    # ========================================================

    section(
        "2. JSON 格式"
    )

    try:

        universe = load_json(
            UNIVERSE_FILE
        )

        log(
            "✓ universe.json JSON 正常"
        )

    except Exception as e:

        log(
            f"❌ universe.json 讀取失敗：{e}"
        )

        return 1

    try:

        chip = load_json(
            CHIP_FILE
        )

        log(
            "✓ chip.json JSON 正常"
        )

    except Exception as e:

        log(
            f"❌ chip.json 讀取失敗：{e}"
        )

        return 1

    # ========================================================
    # 3. Structure
    # ========================================================

    section(
        "3. Universe / Chip 股票池"
    )

    universe_stocks = (
        universe.get("stocks", {})
        if isinstance(universe, dict)
        else {}
    )

    chip_stocks = (
        chip.get("stocks", {})
        if isinstance(chip, dict)
        else {}
    )

    if not isinstance(
        universe_stocks,
        dict,
    ):

        log(
            "❌ Universe stocks 不是 object"
        )

        return 1

    if not isinstance(
        chip_stocks,
        dict,
    ):

        log(
            "❌ Chip stocks 不是 object"
        )

        return 1

    universe_symbols = {
        clean_code(x)
        for x in universe_stocks.keys()
    }

    chip_symbols = {
        clean_code(x)
        for x in chip_stocks.keys()
    }

    missing = sorted(
        universe_symbols
        - chip_symbols
    )

    extra = sorted(
        chip_symbols
        - universe_symbols
    )

    log(
        f"Universe：{len(universe_symbols)} 檔"
    )

    log(
        f"Chip：{len(chip_symbols)} 檔"
    )

    log(
        f"Universe → Chip 缺少：{len(missing)} 檔"
    )

    log(
        f"Chip → Universe 多出：{len(extra)} 檔"
    )

    if missing:

        log(
            "❌ Universe → Chip 不完整"
        )

        errors += 1

    if extra:

        log(
            "❌ Chip → Universe 出現多餘標的"
        )

        errors += 1

    if not missing and not extra:

        log(
            "✓ Universe / Chip 股票池完全一致"
        )

    # ========================================================
    # 4. Required fields
    # ========================================================

    section(
        "4. 全市場必要欄位"
    )

    required_fields = {

        "symbol",

        "full_symbol",

        "name",

        "market",

        "type",

        "institutional_1d",

        "institutional_5d",

        "institutional_10d",

        "institutional_20d",

        "day_trading_volume",

        "day_trading_rate",

        "updated_at",
    }

    missing_field_counter = Counter()

    malformed_objects = []

    for symbol in sorted(
        chip_symbols
    ):

        item = chip_stocks.get(
            symbol
        )

        if not isinstance(
            item,
            dict,
        ):

            malformed_objects.append(
                symbol
            )

            continue

        for field in required_fields:

            if field not in item:

                missing_field_counter[
                    field
                ] += 1

    if malformed_objects:

        log(
            f"❌ stocks object 異常："
            f"{len(malformed_objects)} 檔"
        )

        errors += 1

    else:

        log(
            "✓ 全市場 stocks object 均為 object"
        )

    if missing_field_counter:

        log(
            "❌ 發現缺少必要欄位："
        )

        for field, count in sorted(
            missing_field_counter.items()
        ):

            log(
                f"   {field}：{count} 檔"
            )

        errors += 1

    else:

        log(
            f"✓ {len(chip_symbols)} 檔全部具備必要欄位"
        )

    # ========================================================
    # 5. Forbidden fields
    # ========================================================

    section(
        "5. main_force_* 禁止欄位"
    )

    forbidden_hits = []

    for symbol in sorted(
        chip_symbols
    ):

        item = chip_stocks.get(
            symbol
        )

        if not isinstance(
            item,
            dict,
        ):

            continue

        for key in item.keys():

            if str(key).startswith(
                "main_force_"
            ):

                forbidden_hits.append(
                    f"{symbol}.{key}"
                )

    if forbidden_hits:

        log(
            f"❌ 發現 "
            f"{len(forbidden_hits)} 個禁止欄位"
        )

        for value in forbidden_hits[:100]:

            log(
                f"   {value}"
            )

        errors += 1

    else:

        log(
            "✓ 全市場沒有 main_force_*"
        )

    # ========================================================
    # 6. Institutional fields
    # ========================================================

    section(
        "6. 1D / 5D / 10D / 20D 數值驗證"
    )

    institutional_fields = [

        "institutional_1d",

        "institutional_5d",

        "institutional_10d",

        "institutional_20d",
    ]

    field_stats = {}

    for field in institutional_fields:

        none_count = 0
        valid_count = 0
        invalid_type_count = 0
        nan_count = 0
        extreme_count = 0

        min_value = None
        max_value = None

        for symbol in sorted(
            chip_symbols
        ):

            item = chip_stocks.get(
                symbol
            )

            if not isinstance(
                item,
                dict,
            ):

                continue

            value = item.get(
                field
            )

            if value is None:

                none_count += 1

                continue

            if not is_real_number(
                value
            ):

                invalid_type_count += 1

                continue

            number = float(value)

            if not math.isfinite(
                number
            ):

                nan_count += 1

                continue

            valid_count += 1

            if (
                min_value is None
                or number < min_value
            ):

                min_value = number

            if (
                max_value is None
                or number > max_value
            ):

                max_value = number

            # ------------------------------------------------
            # 明顯異常值
            #
            # 單位應為「張」。
            #
            # 這裡只標記極端值，
            # 不直接判定為錯誤。
            # ------------------------------------------------

            if abs(number) > 10_000_000:

                extreme_count += 1

        field_stats[field] = {

            "none": none_count,

            "valid": valid_count,

            "invalid_type": invalid_type_count,

            "nan": nan_count,

            "extreme": extreme_count,

            "min": min_value,

            "max": max_value,
        }

        log(
            f"{field}"
        )

        log(
            f"  ✓ 有效：{valid_count}"
        )

        log(
            f"  ○ None：{none_count}"
        )

        log(
            f"  ❌ 型別錯誤：{invalid_type_count}"
        )

        log(
            f"  ❌ NaN/Infinity：{nan_count}"
        )

        log(
            f"  ⚠ 極端值：{extreme_count}"
        )

        if min_value is not None:

            log(
                f"  範圍："
                f"{min_value:.2f}"
                f" ~ "
                f"{max_value:.2f}"
            )

        if invalid_type_count:

            errors += 1

        if nan_count:

            errors += 1

        if extreme_count:

            warnings += 1

    # ========================================================
    # 7. Period relationship
    # ========================================================

    section(
        "7. 1D / 5D / 10D / 20D 邏輯一致性"
    )

    relationship_errors = []

    relationship_warnings = []

    for symbol in sorted(
        chip_symbols
    ):

        item = chip_stocks.get(
            symbol
        )

        if not isinstance(
            item,
            dict,
        ):

            continue

        d1 = item.get(
            "institutional_1d"
        )

        d5 = item.get(
            "institutional_5d"
        )

        d10 = item.get(
            "institutional_10d"
        )

        d20 = item.get(
            "institutional_20d"
        )

        values = {
            "1d": d1,
            "5d": d5,
            "10d": d10,
            "20d": d20,
        }

        for name, value in values.items():

            if value is not None and not is_real_number(
                value
            ):

                relationship_errors.append(
                    (
                        symbol,
                        name,
                        "invalid_number",
                    )
                )

        # ----------------------------------------------------
        # 目前 chip.json 沒有每日原始 20 日序列，
        # 因此不能從 chip.json 反推出：
        #
        # 5D = day1 + day2 + ...
        #
        # 這裡不能做不存在資料的假驗證。
        #
        # 能做的是檢查：
        #
        # 1. 數值是否存在
        # 2. 型別是否正確
        # 3. 期間值是否不是明顯異常
        # ----------------------------------------------------

        numeric = {
            key: value
            for key, value in values.items()
            if is_real_number(value)
        }

        if (
            "5d" in numeric
            and "10d" in numeric
        ):

            if abs(
                numeric["5d"]
            ) > abs(
                numeric["10d"]
            ) * 20 + 1:

                relationship_warnings.append(
                    (
                        symbol,
                        "5d_vs_10d",
                        numeric["5d"],
                        numeric["10d"],
                    )
                )

        if (
            "10d" in numeric
            and "20d" in numeric
        ):

            if abs(
                numeric["10d"]
            ) > abs(
                numeric["20d"]
            ) * 20 + 1:

                relationship_warnings.append(
                    (
                        symbol,
                        "10d_vs_20d",
                        numeric["10d"],
                        numeric["20d"],
                    )
                )

    if relationship_errors:

        log(
            f"❌ 期間資料格式錯誤："
            f"{len(relationship_errors)}"
        )

        errors += 1

    else:

        log(
            "✓ 1D / 5D / 10D / 20D 數值格式一致"
        )

    if relationship_warnings:

        log(
            f"⚠ 發現 "
            f"{len(relationship_warnings)} "
            f"筆期間比例異常"
        )

        warnings += 1

    else:

        log(
            "✓ 未發現明顯期間比例異常"
        )

    # ========================================================
    # 8. Day trade
    # ========================================================

    section(
        "8. 當沖資料驗證"
    )

    daytrade_volume_none = 0
    daytrade_volume_valid = 0
    daytrade_rate_none = 0
    daytrade_rate_valid = 0

    daytrade_type_errors = []

    daytrade_rate_extreme = []

    for symbol in sorted(
        chip_symbols
    ):

        item = chip_stocks.get(
            symbol
        )

        if not isinstance(
            item,
            dict,
        ):

            continue

        volume = item.get(
            "day_trading_volume"
        )

        rate = item.get(
            "day_trading_rate"
        )

        if volume is None:

            daytrade_volume_none += 1

        elif is_real_number(volume):

            daytrade_volume_valid += 1

            if float(volume) < 0:

                daytrade_type_errors.append(
                    (
                        symbol,
                        "volume_negative",
                        volume,
                    )
                )

        else:

            daytrade_type_errors.append(
                (
                    symbol,
                    "volume_invalid",
                    volume,
                )
            )

        if rate is None:

            daytrade_rate_none += 1

        elif is_real_number(rate):

            daytrade_rate_valid += 1

            if (
                float(rate) < 0
                or float(rate) > 100
            ):

                daytrade_rate_extreme.append(
                    (
                        symbol,
                        rate,
                    )
                )

        else:

            daytrade_type_errors.append(
                (
                    symbol,
                    "rate_invalid",
                    rate,
                )
            )

    log(
        f"day_trading_volume 有效："
        f"{daytrade_volume_valid}"
    )

    log(
        f"day_trading_volume None："
        f"{daytrade_volume_none}"
    )

    log(
        f"day_trading_rate 有效："
        f"{daytrade_rate_valid}"
    )

    log(
        f"day_trading_rate None："
        f"{daytrade_rate_none}"
    )

    if daytrade_type_errors:

        log(
            f"❌ 當沖資料型別/數值錯誤："
            f"{len(daytrade_type_errors)}"
        )

        for item in daytrade_type_errors[:50]:

            log(
                f"   {item}"
            )

        errors += 1

    else:

        log(
            "✓ 當沖資料型別正常"
        )

    if daytrade_rate_extreme:

        log(
            f"⚠ 當沖率超出 0~100："
            f"{len(daytrade_rate_extreme)}"
        )

        for symbol, rate in (
            daytrade_rate_extreme[:50]
        ):

            log(
                f"   {symbol}：{rate}"
            )

        warnings += 1

    else:

        log(
            "✓ 當沖率範圍正常"
        )

    # ========================================================
    # 9. Metadata
    # ========================================================

    section(
        "9. Chip metadata"
    )

    schema_version = chip.get(
        "schema_version"
    )

    data_date = chip.get(
        "data_date"
    )

    generated_at = chip.get(
        "generated_at"
    )

    log(
        f"schema_version："
        f"{schema_version}"
    )

    log(
        f"data_date："
        f"{data_date}"
    )

    log(
        f"generated_at："
        f"{generated_at}"
    )

    if not data_date:

        log(
            "❌ data_date 缺失"
        )

        errors += 1

    # ========================================================
    # 10. Statistics
    # ========================================================

    section(
        "10. Statistics 交叉驗證"
    )

    statistics = chip.get(
        "statistics"
    )

    if not isinstance(
        statistics,
        dict,
    ):

        log(
            "❌ statistics 不是 object"
        )

        errors += 1

        statistics = {}

    complete_actual = 0
    partial_actual = 0
    insufficient_actual = 0
    empty_name_actual = 0

    for symbol in sorted(
        chip_symbols
    ):

        item = chip_stocks.get(
            symbol
        )

        if not isinstance(
            item,
            dict,
        ):

            continue

        values = [

            item.get(
                "institutional_1d"
            ),

            item.get(
                "institutional_5d"
            ),

            item.get(
                "institutional_10d"
            ),

            item.get(
                "institutional_20d"
            ),
        ]

        available = sum(
            1
            for value in values
            if value is not None
        )

        if available == 4:

            complete_actual += 1

        elif available > 0:

            partial_actual += 1

        else:

            insufficient_actual += 1

        name = str(
            item.get(
                "name",
                ""
            )
        ).strip()

        if not name:

            empty_name_actual += 1

    expected_statistics = {

        "complete": complete_actual,

        "partial": partial_actual,

        "insufficient": insufficient_actual,

        "empty_name": empty_name_actual,
    }

    for key, actual in (
        expected_statistics.items()
    ):

        reported = statistics.get(
            key
        )

        log(
            f"{key}："
            f"reported={reported} "
            f"actual={actual}"
        )

        if reported != actual:

            log(
                f"❌ statistics.{key} 不一致"
            )

            errors += 1

        else:

            log(
                f"✓ statistics.{key} 一致"
            )

    # ========================================================
    # 11. Stock / ETF
    # ========================================================

    section(
        "11. Stock / ETF 分布"
    )

    type_counter = Counter()

    market_counter = Counter()

    for symbol in sorted(
        chip_symbols
    ):

        item = chip_stocks.get(
            symbol
        )

        if not isinstance(
            item,
            dict,
        ):

            continue

        sec_type = str(
            item.get(
                "type",
                ""
            )
        ).strip()

        market = str(
            item.get(
                "market",
                ""
            )
        ).strip().upper()

        type_counter[sec_type] += 1

        market_counter[market] += 1

    stock_count = type_counter.get(
        "Stock",
        0
    )

    etf_count = type_counter.get(
        "ETF",
        0
    )

    unknown_type_count = (
        len(chip_symbols)
        - stock_count
        - etf_count
    )

    log(
        f"Stock：{stock_count}"
    )

    log(
        f"ETF：{etf_count}"
    )

    log(
        f"未知 type：{unknown_type_count}"
    )

    if unknown_type_count:

        errors += 1

    log(
        f"TWSE："
        f"{market_counter.get('TWSE', 0)}"
    )

    log(
        f"TPEX："
        f"{market_counter.get('TPEX', 0)}"
    )

    # ========================================================
    # 12. Name / market / type
    # ========================================================

    section(
        "12. 基本資料品質"
    )

    empty_name = []
    unknown_market = []
    unknown_type = []

    for symbol in sorted(
        chip_symbols
    ):

        item = chip_stocks.get(
            symbol
        )

        if not isinstance(
            item,
            dict,
        ):

            continue

        if not str(
            item.get(
                "name",
                ""
            )
        ).strip():

            empty_name.append(
                symbol
            )

        market = str(
            item.get(
                "market",
                ""
            )
        ).strip().upper()

        if market not in (
            "TWSE",
            "TPEX",
        ):

            unknown_market.append(
                symbol
            )

        sec_type = str(
            item.get(
                "type",
                ""
            )
        ).strip()

        if sec_type not in (
            "Stock",
            "ETF",
        ):

            unknown_type.append(
                symbol
            )

    log(
        f"名稱缺失："
        f"{len(empty_name)}"
    )

    log(
        f"市場未知："
        f"{len(unknown_market)}"
    )

    log(
        f"type 未知："
        f"{len(unknown_type)}"
    )

    if empty_name:

        warnings += 1

    if unknown_market:

        errors += 1

    if unknown_type:

        errors += 1

    # ========================================================
    # 13. Generate report
    # ========================================================

    section(
        "13. 建立資料品質報告"
    )

    report = {

        "schema_version": VERSION,

        "generated_at": datetime.now().isoformat(),

        "universe_count": len(
            universe_symbols
        ),

        "chip_count": len(
            chip_symbols
        ),

        "missing_count": len(
            missing
        ),

        "extra_count": len(
            extra
        ),

        "institutional": field_stats,

        "day_trade": {

            "volume_valid":
                daytrade_volume_valid,

            "volume_none":
                daytrade_volume_none,

            "rate_valid":
                daytrade_rate_valid,

            "rate_none":
                daytrade_rate_none,
        },

        "statistics_actual":
            expected_statistics,

        "stock_count":
            stock_count,

        "etf_count":
            etf_count,

        "market_count":
            dict(market_counter),

        "errors":
            errors,

        "warnings":
            warnings,
    }

    try:

        with QUALITY_REPORT_FILE.open(
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                report,
                f,
                ensure_ascii=False,
                indent=2,
            )

        log(
            f"✓ 已建立："
            f"Data/chip_quality_report.json"
        )

    except Exception as e:

        log(
            f"❌ 無法寫入品質報告：{e}"
        )

        errors += 1

    # ========================================================
    # Final
    # ========================================================

    section(
        "FINAL CHIP QUALITY VERIFICATION"
    )

    log(
        f"Universe："
        f"{len(universe_symbols)} 檔"
    )

    log(
        f"Chip："
        f"{len(chip_symbols)} 檔"
    )

    log(
        f"缺少："
        f"{len(missing)}"
    )

    log(
        f"多出："
        f"{len(extra)}"
    )

    log(
        f"20D完整："
        f"{complete_actual}"
    )

    log(
        f"部分資料："
        f"{partial_actual}"
    )

    log(
        f"無資料："
        f"{insufficient_actual}"
    )

    log(
        f"名稱缺失："
        f"{empty_name_actual}"
    )

    log(
        f"錯誤："
        f"{errors}"
    )

    log(
        f"警告："
        f"{warnings}"
    )

    log("")

    if errors:

        log(
            "============================================================"
        )

        log(
            "❌ CHIP QUALITY VERIFICATION FAILED"
        )

        log(
            "============================================================"
        )

        log(
            "❌ chip.json 不應直接進入下一階段正式資料流程"
        )

        return 1

    log(
        "============================================================"
    )

    log(
        "✓ CHIP QUALITY VERIFICATION PASSED"
    )

    log(
        "============================================================"
    )

    log(
        "✓ 全市場股票池一致"
    )

    log(
        "✓ 全市場必要欄位正常"
    )

    log(
        "✓ institutional 1D / 5D / 10D / 20D 格式正常"
    )

    log(
        "✓ day-trading 欄位格式正常"
    )

    log(
        "✓ main_force_* 完全不存在"
    )

    log(
        "✓ statistics 與實際資料一致"
    )

    log(
        "✓ 固定股票依賴完全停用"
    )

    if warnings:

        log(
            f"⚠ 存在 {warnings} 個警告，"
            f"但沒有阻擋正式流程"
        )

    log(
        "✓ CHIP DATA QUALITY 可以進入下一階段"
    )

    return 0


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )