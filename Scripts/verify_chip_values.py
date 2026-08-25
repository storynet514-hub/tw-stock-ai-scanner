#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
verify_chip_values.py V1.0

============================================================
用途
============================================================

驗證 Data/chip.json 中的籌碼數值結構與期間邏輯。

本驗證器：

1. 全市場驗證
2. 不固定任何股票
3. 不依賴特定股票名稱
4. 不修改 chip.json
5. 不修改 universe.json
6. 不產生 main_force_*
7. 不使用三大法人倍率估算
8. 驗證 institutional_1d / 5d / 10d / 20d
9. 驗證數值型態
10. 驗證期間欄位邏輯
11. 驗證不存在非法 NaN / Infinity
12. 驗證 metadata / statistics
13. 驗證 Chip stocks 與 Universe 全量一致

注意：

目前 chip.json 只保存期間累計值，
沒有保存每日 20 日原始序列。

因此：

institutional_5d / 10d / 20d
無法從 chip.json 單獨重新計算每日加總。

本驗證器不會假造每日資料，
只驗證目前輸出的數值邏輯與結構，
並確保不存在倍率估算欄位。

============================================================
"""

from __future__ import annotations

import json
import math
import re
import sys
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
# Constants
# ============================================================

PERIOD_FIELDS = (
    "institutional_1d",
    "institutional_5d",
    "institutional_10d",
    "institutional_20d",
)

FORBIDDEN_FIELDS = {
    "main_force_1d",
    "main_force_5d",
    "main_force_10d",
    "main_force_20d",
}

FORBIDDEN_PATTERN = re.compile(
    r"^main_force_"
)


# ============================================================
# Logging
# ============================================================

errors = 0
warnings = 0


def log(message: str = "") -> None:
    print(message, flush=True)


def section(title: str) -> None:
    log("")
    log("=" * 72)
    log(title)
    log("=" * 72)


def error(message: str) -> None:
    global errors

    errors += 1

    log(
        f"❌ {message}"
    )


def warning(message: str) -> None:
    global warnings

    warnings += 1

    log(
        f"⚠ {message}"
    )


# ============================================================
# Numeric
# ============================================================

def is_valid_number(
    value: Any,
) -> bool:

    if value is None:
        return True

    if isinstance(value, bool):
        return False

    if not isinstance(
        value,
        (int, float),
    ):
        return False

    try:

        return math.isfinite(
            float(value)
        )

    except Exception:

        return False


# ============================================================
# Load JSON
# ============================================================

def load_json(
    path: Path,
    label: str,
) -> Any:

    if not path.exists():

        error(
            f"{path} 不存在"
        )

        return None

    try:

        with path.open(
            "r",
            encoding="utf-8-sig",
        ) as f:

            data = json.load(f)

        log(
            f"✓ {label} JSON 格式正常"
        )

        return data

    except Exception as exc:

        error(
            f"{label} JSON 讀取失敗："
            f"{exc}"
        )

        return None


# ============================================================
# Universe symbols
# ============================================================

def get_universe_symbols(
    universe: Any,
) -> List[str]:

    if not isinstance(
        universe,
        dict,
    ):

        error(
            "Universe 根節點不是 object"
        )

        return []

    stocks = universe.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        error(
            "Universe stocks 不是 object"
        )

        return []

    return [
        str(symbol).strip().upper()
        for symbol in stocks.keys()
    ]


# ============================================================
# Chip stocks
# ============================================================

def get_chip_stocks(
    chip: Any,
) -> Dict[str, Any]:

    if not isinstance(
        chip,
        dict,
    ):

        error(
            "Chip 根節點不是 object"
        )

        return {}

    stocks = chip.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        error(
            "chip.json stocks 不是 object"
        )

        return {}

    return stocks


# ============================================================
# 1. File existence
# ============================================================

def verify_files() -> bool:

    section(
        "1. 檔案存在性"
    )

    ok = True

    if UNIVERSE_FILE.exists():

        log(
            "✓ Data/universe.json 存在"
        )

    else:

        error(
            "Data/universe.json 不存在"
        )

        ok = False

    if CHIP_FILE.exists():

        log(
            "✓ Data/chip.json 存在"
        )

    else:

        error(
            "Data/chip.json 不存在"
        )

        ok = False

    return ok


# ============================================================
# 2. Universe ↔ Chip
# ============================================================

def verify_symbol_set(
    universe_symbols: List[str],
    chip_stocks: Dict[str, Any],
) -> None:

    section(
        "2. Universe ↔ Chip 股票池全量對照"
    )

    universe_set = set(
        universe_symbols
    )

    chip_set = {
        str(symbol).strip().upper()
        for symbol in chip_stocks.keys()
    }

    missing = sorted(
        universe_set - chip_set
    )

    extra = sorted(
        chip_set - universe_set
    )

    if missing:

        error(
            f"Universe → Chip 缺少 "
            f"{len(missing)} 檔"
        )

        for symbol in missing[:20]:

            log(
                f"   缺少：{symbol}"
            )

    else:

        log(
            "✓ Universe → Chip：全部存在"
        )

    if extra:

        error(
            f"Chip → Universe 多出 "
            f"{len(extra)} 檔"
        )

        for symbol in extra[:20]:

            log(
                f"   多出：{symbol}"
            )

    else:

        log(
            "✓ Chip → Universe：沒有多餘標的"
        )

    if (
        not missing
        and not extra
    ):

        log(
            f"✓ Universe / Chip 100% 一致："
            f"{len(universe_set)} 檔"
        )


# ============================================================
# 3. Chip metadata
# ============================================================

def verify_metadata(
    chip: Dict[str, Any],
    chip_stocks: Dict[str, Any],
) -> None:

    section(
        "3. Chip metadata 驗證"
    )

    universe_count = chip.get(
        "universe_count"
    )

    actual_count = len(
        chip_stocks
    )

    if universe_count != actual_count:

        error(
            "chip.json universe_count "
            "與 stocks 數量不一致"
        )

        log(
            f"   universe_count："
            f"{universe_count}"
        )

        log(
            f"   stocks："
            f"{actual_count}"
        )

    else:

        log(
            f"✓ universe_count："
            f"{actual_count}"
        )

    schema_version = chip.get(
        "schema_version"
    )

    if schema_version:

        log(
            f"✓ schema_version："
            f"{schema_version}"
        )

    else:

        warning(
            "schema_version 不存在"
        )

    data_date = chip.get(
        "data_date"
    )

    if data_date:

        log(
            f"✓ data_date："
            f"{data_date}"
        )

    else:

        warning(
            "data_date 不存在"
        )

    generated_at = chip.get(
        "generated_at"
    )

    if generated_at:

        log(
            f"✓ generated_at："
            f"{generated_at}"
        )

    else:

        warning(
            "generated_at 不存在"
        )


# ============================================================
# 4. Forbidden fields
# ============================================================

def verify_forbidden_fields(
    chip_stocks: Dict[str, Any],
) -> None:

    section(
        "4. main_force_* 禁止欄位掃描"
    )

    found = []

    for symbol, item in (
        chip_stocks.items()
    ):

        if not isinstance(
            item,
            dict,
        ):

            continue

        for key in item.keys():

            key_text = str(
                key
            ).strip()

            if (
                key_text in FORBIDDEN_FIELDS
                or FORBIDDEN_PATTERN.match(
                    key_text
                )
            ):

                found.append(
                    f"{symbol}.{key_text}"
                )

    if found:

        error(
            f"發現 {len(found)} 個 "
            f"main_force_* 禁止欄位"
        )

        for item in found[:20]:

            log(
                f"   {item}"
            )

    else:

        log(
            "✓ 全市場 chip 均沒有 "
            "main_force_*"
        )


# ============================================================
# 5. Period fields
# ============================================================

def verify_period_fields(
    chip_stocks: Dict[str, Any],
) -> None:

    section(
        "5. institutional 1D / 5D / 10D / 20D "
        "全市場欄位驗證"
    )

    field_counts = {
        field: 0
        for field in PERIOD_FIELDS
    }

    invalid_values = []

    null_counts = {
        field: 0
        for field in PERIOD_FIELDS
    }

    for symbol, item in (
        chip_stocks.items()
    ):

        if not isinstance(
            item,
            dict,
        ):

            error(
                f"{symbol} 資料不是 object"
            )

            continue

        for field in PERIOD_FIELDS:

            if field not in item:

                error(
                    f"{symbol} 缺少欄位："
                    f"{field}"
                )

                continue

            field_counts[field] += 1

            value = item.get(
                field
            )

            if value is None:

                null_counts[field] += 1

                continue

            if not is_valid_number(
                value
            ):

                invalid_values.append(
                    (
                        symbol,
                        field,
                        value,
                    )
                )

    for field in PERIOD_FIELDS:

        log(
            f"✓ {field}："
            f"{field_counts[field]} "
            f"全市場欄位存在"
        )

        log(
            f"  └ NULL："
            f"{null_counts[field]}"
        )

    if invalid_values:

        error(
            f"發現 {len(invalid_values)} "
            f"個非法數值"
        )

        for symbol, field, value in (
            invalid_values[:20]
        ):

            log(
                f"   {symbol}.{field} = "
                f"{value!r}"
            )

    else:

        log(
            "✓ 所有 institutional 數值 "
            "均為有效數值或 null"
        )


# ============================================================
# 6. Period consistency
# ============================================================

def verify_period_consistency(
    chip_stocks: Dict[str, Any],
) -> None:

    section(
        "6. 期間資料邏輯驗證"
    )

    invalid = []

    for symbol, item in (
        chip_stocks.items()
    ):

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

        # ----------------------------------------------------
        # 重要：
        #
        # 這裡不要求 5D / 10D / 20D
        # 必須比前一期大。
        #
        # 因為法人每日買賣超可以正負互抵。
        #
        # 也不使用：
        #
        # 1D × 5
        # 1D × 10
        # 1D × 20
        #
        # 作為驗證公式。
        # ----------------------------------------------------

        # NULL 結構檢查
        #
        # 若 5D 有值而 1D 完全沒有，
        # 屬於可疑資料結構。
        #

        if d5 is not None and d1 is None:

            invalid.append(
                (
                    symbol,
                    "5D 有值但 1D 為 null",
                )
            )

        if d10 is not None and d5 is None:

            invalid.append(
                (
                    symbol,
                    "10D 有值但 5D 為 null",
                )
            )

        if d20 is not None and d10 is None:

            invalid.append(
                (
                    symbol,
                    "20D 有值但 10D 為 null",
                )
            )

    if invalid:

        error(
            f"發現 {len(invalid)} 個期間結構異常"
        )

        for symbol, reason in invalid[:30]:

            log(
                f"   {symbol} | {reason}"
            )

    else:

        log(
            "✓ 1D / 5D / 10D / 20D "
            "期間結構正常"
        )

    log(
        "✓ 不使用 1D × 倍率推估 5D / 10D / 20D"
    )


# ============================================================
# 7. Suspicious multiplier detection
# ============================================================

def verify_multiplier_suspicion(
    chip_stocks: Dict[str, Any],
) -> None:

    section(
        "7. 三大法人倍率估算異常檢查"
    )

    suspicious = []

    for symbol, item in (
        chip_stocks.items()
    ):

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

        if (
            not isinstance(d1, (int, float))
            or not isinstance(d5, (int, float))
        ):

            continue

        if d1 == 0:

            continue

        r5 = d5 / d1

        if abs(r5 - 5.0) < 1e-9:

            suspicious.append(
                (
                    symbol,
                    "5D",
                    d1,
                    d5,
                )
            )

        if (
            isinstance(d10, (int, float))
            and abs(d10 / d1 - 10.0) < 1e-9
        ):

            suspicious.append(
                (
                    symbol,
                    "10D",
                    d1,
                    d10,
                )
            )

        if (
            isinstance(d20, (int, float))
            and abs(d20 / d1 - 20.0) < 1e-9
        ):

            suspicious.append(
                (
                    symbol,
                    "20D",
                    d1,
                    d20,
                )
            )

    if suspicious:

        warning(
            "發現數值恰好符合 1D × 倍率的可疑項目"
        )

        for symbol, period, d1, value in (
            suspicious[:20]
        ):

            log(
                f"   {symbol} | "
                f"{period} | "
                f"1D={d1} | "
                f"{period}={value}"
            )

        warning(
            f"可疑項目共 {len(suspicious)} 筆；"
            f"僅列為警告，不直接判定為錯誤"
        )

    else:

        log(
            "✓ 未發現明顯的 1D × 5/10/20 "
            "倍率估算模式"
        )


# ============================================================
# 8. Stock / ETF
# ============================================================

def verify_stock_etf_counts(
    chip: Dict[str, Any],
    chip_stocks: Dict[str, Any],
) -> None:

    section(
        "8. Stock / ETF 數量驗證"
    )

    actual_stock = 0
    actual_etf = 0
    unknown_type = 0

    for item in chip_stocks.values():

        if not isinstance(
            item,
            dict,
        ):

            continue

        sec_type = str(
            item.get(
                "type",
                "",
            )
        ).strip()

        if sec_type == "Stock":

            actual_stock += 1

        elif sec_type == "ETF":

            actual_etf += 1

        else:

            unknown_type += 1

    declared_stock = chip.get(
        "stock_count"
    )

    declared_etf = chip.get(
        "etf_count"
    )

    if declared_stock != actual_stock:

        error(
            f"Stock 數量不一致："
            f"metadata={declared_stock}, "
            f"actual={actual_stock}"
        )

    else:

        log(
            f"✓ Stock 數量一致："
            f"{actual_stock}"
        )

    if declared_etf != actual_etf:

        error(
            f"ETF 數量不一致："
            f"metadata={declared_etf}, "
            f"actual={actual_etf}"
        )

    else:

        log(
            f"✓ ETF 數量一致："
            f"{actual_etf}"
        )

    if unknown_type:

        error(
            f"發現 {unknown_type} 個未知 type"
        )

    else:

        log(
            "✓ 所有標的 type 均為 Stock / ETF"
        )

    if (
        actual_stock
        + actual_etf
        == len(chip_stocks)
    ):

        log(
            "✓ Stock + ETF = Chip 全部標的"
        )


# ============================================================
# 9. Statistics
# ============================================================

def verify_statistics(
    chip: Dict[str, Any],
) -> None:

    section(
        "9. Statistics 驗證"
    )

    statistics = chip.get(
        "statistics"
    )

    if not isinstance(
        statistics,
        dict,
    ):

        error(
            "statistics 不是 object"
        )

        return

    required = (
        "complete",
        "partial",
        "insufficient",
        "empty_name",
    )

    for field in required:

        value = statistics.get(
            field
        )

        if not isinstance(
            value,
            int,
        ):

            error(
                f"statistics.{field} "
                f"不是整數"
            )

            continue

        if value < 0:

            error(
                f"statistics.{field} "
                f"不可小於 0"
            )

            continue

        log(
            f"✓ statistics.{field}："
            f"{value}"
        )


# ============================================================
# 10. Data integrity
# ============================================================

def verify_data_integrity(
    chip_stocks: Dict[str, Any],
) -> None:

    section(
        "10. 全市場資料完整性"
    )

    malformed = []

    for symbol, item in (
        chip_stocks.items()
    ):

        if not isinstance(
            item,
            dict,
        ):

            malformed.append(
                (
                    symbol,
                    "item 不是 object",
                )
            )

            continue

        item_symbol = str(
            item.get(
                "symbol",
                "",
            )
        ).strip().upper()

        if item_symbol != symbol:

            malformed.append(
                (
                    symbol,
                    "symbol 與 stocks key 不一致",
                )
            )

        if not item.get(
            "full_symbol"
        ):

            malformed.append(
                (
                    symbol,
                    "缺少 full_symbol",
                )
            )

        if not item.get(
            "market"
        ):

            malformed.append(
                (
                    symbol,
                    "缺少 market",
                )
            )

        if not item.get(
            "type"
        ):

            malformed.append(
                (
                    symbol,
                    "缺少 type",
                )
            )

        if not item.get(
            "updated_at"
        ):

            malformed.append(
                (
                    symbol,
                    "缺少 updated_at",
                )
            )

    if malformed:

        error(
            f"發現 {len(malformed)} 個 "
            f"資料完整性問題"
        )

        for symbol, reason in (
            malformed[:30]
        ):

            log(
                f"   {symbol} | {reason}"
            )

    else:

        log(
            f"✓ {len(chip_stocks)} 檔全市場 "
            f"資料完整性驗證通過"
        )


# ============================================================
# Main
# ============================================================

def main() -> int:

    log(
        "========================================"
    )

    log(
        "CHIP VALUE VERIFICATION"
    )

    log(
        "========================================"
    )

    log("")

    log(
        "=" * 72
    )

    log(
        f"台股 AI 選股系統 "
        f"chip 數值驗證器 {VERSION}"
    )

    log(
        "=" * 72
    )

    log(
        "驗證模式：全市場"
    )

    log(
        "固定股票驗證：停用"
    )

    log(
        "倍率估算：禁止"
    )

    log(
        "main_force_*：禁止"
    )

    # ========================================================
    # 1. Files
    # ========================================================

    if not verify_files():

        return 1

    # ========================================================
    # 2. Load
    # ========================================================

    section(
        "讀取 JSON"
    )

    universe = load_json(
        UNIVERSE_FILE,
        "universe.json",
    )

    chip = load_json(
        CHIP_FILE,
        "chip.json",
    )

    if universe is None or chip is None:

        return 1

    # ========================================================
    # 3. Structures
    # ========================================================

    universe_symbols = (
        get_universe_symbols(
            universe
        )
    )

    chip_stocks = get_chip_stocks(
        chip
    )

    if not universe_symbols:

        error(
            "Universe 股票池為空"
        )

    if not chip_stocks:

        error(
            "Chip 股票池為空"
        )

    # ========================================================
    # 4. Universe ↔ Chip
    # ========================================================

    verify_symbol_set(
        universe_symbols,
        chip_stocks,
    )

    # ========================================================
    # 5. Metadata
    # ========================================================

    if isinstance(
        chip,
        dict,
    ):

        verify_metadata(
            chip,
            chip_stocks,
        )

    # ========================================================
    # 6. Forbidden
    # ========================================================

    verify_forbidden_fields(
        chip_stocks
    )

    # ========================================================
    # 7. Period
    # ========================================================

    verify_period_fields(
        chip_stocks
    )

    # ========================================================
    # 8. Consistency
    # ========================================================

    verify_period_consistency(
        chip_stocks
    )

    # ========================================================
    # 9. Multiplier
    # ========================================================

    verify_multiplier_suspicion(
        chip_stocks
    )

    # ========================================================
    # 10. Stock / ETF
    # ========================================================

    if isinstance(
        chip,
        dict,
    ):

        verify_stock_etf_counts(
            chip,
            chip_stocks,
        )

        verify_statistics(
            chip
        )

    # ========================================================
    # 11. Integrity
    # ========================================================

    verify_data_integrity(
        chip_stocks
    )

    # ========================================================
    # Final
    # ========================================================

    section(
        "FINAL CHIP VALUE VERIFICATION"
    )

    log(
        f"Universe："
        f"{len(universe_symbols)} 檔"
    )

    log(
        f"Chip："
        f"{len(chip_stocks)} 檔"
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

    if errors > 0:

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
            "❌ chip.json 不符合目前數值驗證規則"
        )

        return 1

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
        "✓ Universe / Chip 全量一致"
    )

    log(
        "✓ 全市場 institutional 欄位完整"
    )

    log(
        "✓ 1D / 5D / 10D / 20D 數值格式正常"
    )

    log(
        "✓ 期間結構驗證通過"
    )

    log(
        "✓ main_force_* 完全不存在"
    )

    log(
        "✓ 不依賴任何固定個股"
    )

    log(
        "✓ 不使用三大法人倍率估算"
    )

    log(
        "✓ 不修改任何正式資料"
    )

    log(
        "✓ CHIP DATA VALUE 可以進入下一階段"
    )

    if warnings:

        log(
            f"⚠ 共 {warnings} 個警告，"
            f"但未發現阻斷性錯誤"
        )

    return 0


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )