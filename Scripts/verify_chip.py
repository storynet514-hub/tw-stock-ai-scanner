#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
verify_chip.py V1.2

============================================================
V1.2 全市場正式驗證版
============================================================

驗證原則
------------------------------------------------------------
Universe 是唯一股票池來源。

驗證器只確認：

1. Universe / Chip 是否存在
2. JSON 是否正常
3. Universe stocks object 是否正常
4. Universe 股票代號格式是否可接受
5. Universe ↔ Chip 是否 100% 一致
6. Chip universe_count 是否正確
7. 全市場資料欄位是否完整
8. 禁止欄位是否存在
9. 1D / 5D / 10D / 20D 是否存在
10. Chip metadata 是否正常
11. statistics 是否正常
12. Stock / ETF 數量是否一致

============================================================
重要設計
------------------------------------------------------------

不對任何單一股票做特殊驗證。

不依賴任何固定股票。

不掃描驗證器自身原始碼。

Universe 已接受的正式代號，只要符合台股
Universe 可接受的代號格式，就不得被 verify_chip
自行排除。

============================================================
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Set


# ============================================================
# Version
# ============================================================

VERSION = "V1.2"


# ============================================================
# Paths
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

CHIP_FILE = DATA_DIR / "chip.json"


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
# Basic helpers
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


def clean_text(value: Any) -> str:

    if value is None:
        return ""

    return str(value).strip()


def is_finite_number(value: Any) -> bool:

    if value is None:
        return True

    if isinstance(value, bool):
        return False

    if isinstance(value, (int, float)):

        try:
            return math.isfinite(
                float(value)
            )
        except Exception:
            return False

    return False


# ============================================================
# Symbol validation
# ============================================================

def is_valid_symbol(code: str) -> bool:
    """
    驗證 Universe 股票代號格式。

    接受：

    4～6 碼純數字

    4～6 碼數字 + 1～2 碼英數字

    例如：

    2337
    0050
    006203
    00636
    00713
    00400A
    00631L
    00632R
    00710B
    2887Z1

    注意：
    Universe 本身才是正式股票池來源。
    verify_chip 不負責重新定義哪些商品是 ETF、
    ETN 或其他證券。

    因此，只要代號符合 Universe 可接受的
    4～6 位數字基礎 + 最多 2 位英數尾碼格式，
    即視為合法。
    """

    code = clean_code(code)

    if not code:
        return False

    # --------------------------------------------------------
    # 4～6 碼純數字
    # --------------------------------------------------------

    if re.fullmatch(
        r"\d{4,6}",
        code,
    ):
        return True

    # --------------------------------------------------------
    # 4～6 碼數字 + 1～2 碼英數字
    #
    # 包含：
    #
    # 00400A
    # 00631L
    # 00632R
    # 00710B
    # 2887Z1
    #
    # 不在 verify_chip 重新判斷商品類型。
    # --------------------------------------------------------

    if re.fullmatch(
        r"\d{4,6}[A-Z0-9]{1,2}",
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

    try:

        with path.open(
            "r",
            encoding="utf-8-sig",
        ) as f:

            return json.load(f)

    except Exception as e:

        raise RuntimeError(
            f"{path}：{e}"
        )


# ============================================================
# Forbidden fields
# ============================================================

FORBIDDEN_FIELDS = {
    "main_force_1d",
    "main_force_5d",
    "main_force_10d",
    "main_force_20d",
}


def scan_forbidden_fields(
    stocks: Dict[str, Any],
) -> List[str]:

    found: List[str] = []

    for symbol, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):
            continue

        for field in FORBIDDEN_FIELDS:

            if field in item:

                found.append(
                    f"{symbol}.{field}"
                )

    return found


# ============================================================
# Main
# ============================================================

def main() -> int:

    errors: List[str] = []

    warnings: List[str] = []

    universe_count = None
    chip_count = None

    log(
        "========================================"
    )

    log(
        "CHIP DATA VERIFICATION"
    )

    log(
        "========================================"
    )

    section(
        f"台股 AI 選股系統 "
        f"chip 全市場驗證器 {VERSION}"
    )

    log(
        "驗證模式：Universe ↔ Chip 全量對照"
    )

    log(
        "驗證範圍：全市場"
    )

    log(
        "固定個股特殊驗證：停用"
    )

    # ========================================================
    # 1. File existence
    # ========================================================

    section(
        "1. 檔案存在性"
    )

    if not UNIVERSE_FILE.exists():

        log(
            "❌ Data/universe.json 不存在"
        )

        errors.append(
            "universe.json 不存在"
        )

    else:

        log(
            "✓ Data/universe.json 存在"
        )

    if not CHIP_FILE.exists():

        log(
            "❌ Data/chip.json 不存在"
        )

        errors.append(
            "chip.json 不存在"
        )

    else:

        log(
            "✓ Data/chip.json 存在"
        )

    if errors:

        return finalize(
            errors,
            warnings,
            universe_count,
            chip_count,
        )

    # ========================================================
    # 2. JSON
    # ========================================================

    section(
        "2. 讀取 JSON"
    )

    try:

        universe = load_json(
            UNIVERSE_FILE
        )

        log(
            "✓ universe.json JSON 格式正常"
        )

    except Exception as e:

        log(
            f"❌ universe.json 讀取失敗：{e}"
        )

        errors.append(
            "universe.json JSON 格式錯誤"
        )

        return finalize(
            errors,
            warnings,
            universe_count,
            chip_count,
        )

    try:

        chip = load_json(
            CHIP_FILE
        )

        log(
            "✓ chip.json JSON 格式正常"
        )

    except Exception as e:

        log(
            f"❌ chip.json 讀取失敗：{e}"
        )

        errors.append(
            "chip.json JSON 格式錯誤"
        )

        return finalize(
            errors,
            warnings,
            universe_count,
            chip_count,
        )

    # ========================================================
    # 3. Universe structure
    # ========================================================

    section(
        "3. Universe 結構驗證"
    )

    if not isinstance(
        universe,
        dict,
    ):

        log(
            "❌ Universe 根節點不是 object"
        )

        errors.append(
            "Universe 根節點錯誤"
        )

        return finalize(
            errors,
            warnings,
            universe_count,
            chip_count,
        )

    universe_stocks = universe.get(
        "stocks"
    )

    if not isinstance(
        universe_stocks,
        dict,
    ):

        log(
            "❌ Universe stocks 不是 object"
        )

        errors.append(
            "Universe stocks 結構錯誤"
        )

        return finalize(
            errors,
            warnings,
            universe_count,
            chip_count,
        )

    universe_codes: Set[str] = set()

    empty_universe_keys: List[str] = []

    for key in universe_stocks.keys():

        code = clean_code(key)

        if not code:

            empty_universe_keys.append(
                str(key)
            )

            continue

        universe_codes.add(
            code
        )

    universe_count = len(
        universe_codes
    )

    log(
        f"✓ Universe stocks："
        f"{universe_count} 檔"
    )

    if empty_universe_keys:

        log(
            f"❌ Universe 發現 "
            f"{len(empty_universe_keys)} "
            f"個空白代號"
        )

        errors.append(
            "Universe 存在空白代號"
        )

    declared_universe_count = (
        universe.get(
            "universe_count"
        )
    )

    try:

        declared_universe_count = int(
            declared_universe_count
        )

    except Exception:

        declared_universe_count = None

    if (
        declared_universe_count
        is None
    ):

        log(
            "❌ Universe universe_count 無效"
        )

        errors.append(
            "Universe universe_count 無效"
        )

    elif (
        declared_universe_count
        != universe_count
    ):

        log(
            "❌ Universe universe_count "
            "與 stocks object 數量不一致"
        )

        log(
            f"   universe_count："
            f"{declared_universe_count}"
        )

        log(
            f"   stocks："
            f"{universe_count}"
        )

        errors.append(
            "Universe 數量不一致"
        )

    else:

        log(
            "✓ Universe universe_count "
            "與 stocks object 數量一致"
        )

    # ========================================================
    # 4. Universe symbol format
    # ========================================================

    section(
        "4. Universe 股票代號格式"
    )

    invalid_universe_symbols: List[str] = []

    for code in sorted(
        universe_codes
    ):

        if not is_valid_symbol(
            code
        ):

            invalid_universe_symbols.append(
                code
            )

    if invalid_universe_symbols:

        log(
            f"❌ 發現 "
            f"{len(invalid_universe_symbols)} "
            f"個無法識別的 Universe 股票代號"
        )

        for code in (
            invalid_universe_symbols[:100]
        ):

            log(
                f"   {code}"
            )

        errors.append(
            "Universe 存在無法識別的股票代號"
        )

    else:

        log(
            "✓ Universe 所有股票代號格式正常"
        )

    # ========================================================
    # 5. Chip structure
    # ========================================================

    section(
        "5. Chip 結構驗證"
    )

    if not isinstance(
        chip,
        dict,
    ):

        log(
            "❌ chip.json 根節點不是 object"
        )

        errors.append(
            "chip.json 根節點錯誤"
        )

        return finalize(
            errors,
            warnings,
            universe_count,
            chip_count,
        )

    chip_stocks = chip.get(
        "stocks"
    )

    if not isinstance(
        chip_stocks,
        dict,
    ):

        log(
            "❌ Chip stocks 不是 object"
        )

        errors.append(
            "Chip stocks 結構錯誤"
        )

        return finalize(
            errors,
            warnings,
            universe_count,
            chip_count,
        )

    chip_codes: Set[str] = set()

    for key in chip_stocks.keys():

        code = clean_code(key)

        if code:

            chip_codes.add(
                code
            )

    chip_count = len(
        chip_codes
    )

    log(
        f"✓ Chip stocks："
        f"{chip_count} 檔"
    )

    # ========================================================
    # 6. Universe → Chip full comparison
    # ========================================================

    section(
        "6. Universe → Chip 全量對照"
    )

    missing_from_chip = sorted(
        universe_codes
        - chip_codes
    )

    extra_in_chip = sorted(
        chip_codes
        - universe_codes
    )

    if missing_from_chip:

        log(
            f"❌ Universe → Chip "
            f"缺少："
            f"{len(missing_from_chip)} 檔"
        )

        for code in (
            missing_from_chip[:100]
        ):

            log(
                f"   {code}"
            )

        errors.append(
            "Universe → Chip 存在缺少標的"
        )

    else:

        log(
            "✓ Universe → Chip："
            "全部存在"
        )

    if extra_in_chip:

        log(
            f"❌ Chip → Universe "
            f"多出："
            f"{len(extra_in_chip)} 檔"
        )

        for code in (
            extra_in_chip[:100]
        ):

            log(
                f"   {code}"
            )

        errors.append(
            "Chip → Universe 存在多餘標的"
        )

    else:

        log(
            "✓ Chip → Universe："
            "沒有多餘標的"
        )

    if (
        not missing_from_chip
        and not extra_in_chip
    ):

        log(
            f"✓ Universe / Chip "
            f"股票池 100% 一致："
            f"{universe_count} 檔"
        )

    # ========================================================
    # 7. Count validation
    # ========================================================

    section(
        "7. 數量驗證"
    )

    chip_declared_count = chip.get(
        "universe_count"
    )

    try:

        chip_declared_count = int(
            chip_declared_count
        )

    except Exception:

        chip_declared_count = None

    if chip_declared_count is None:

        log(
            "❌ chip.json universe_count 無效"
        )

        errors.append(
            "Chip universe_count 無效"
        )

    elif chip_declared_count != chip_count:

        log(
            "❌ chip.json universe_count "
            "與 stocks 數量不一致"
        )

        log(
            f"   header："
            f"{chip_declared_count}"
        )

        log(
            f"   stocks："
            f"{chip_count}"
        )

        errors.append(
            "Chip universe_count 不一致"
        )

    else:

        log(
            "✓ chip.json universe_count "
            "與 stocks 數量一致"
        )

    if (
        universe_count == chip_count
        and not missing_from_chip
        and not extra_in_chip
    ):

        log(
            f"✓ Universe / Chip "
            f"所有數量完全一致："
            f"{chip_count} 檔"
        )

    # ========================================================
    # 8. Full market field validation
    # ========================================================

    section(
        "8. 全市場個股資料欄位驗證"
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

    field_errors: List[str] = []

    for symbol in sorted(
        chip_codes
    ):

        item = chip_stocks.get(
            symbol
        )

        if not isinstance(
            item,
            dict,
        ):

            field_errors.append(
                f"{symbol}: item 不是 object"
            )

            continue

        missing_fields = []

        for field in required_fields:

            if field not in item:

                missing_fields.append(
                    field
                )

        if missing_fields:

            field_errors.append(
                f"{symbol}: 缺少 "
                + ", ".join(
                    missing_fields
                )
            )

        # ----------------------------------------------------
        # symbol 必須與 key 一致
        # ----------------------------------------------------

        item_symbol = clean_code(
            item.get(
                "symbol"
            )
        )

        if item_symbol != symbol:

            field_errors.append(
                f"{symbol}: "
                f"item.symbol={item_symbol}"
            )

        # ----------------------------------------------------
        # name 不得為空
        # ----------------------------------------------------

        name = clean_text(
            item.get(
                "name"
            )
        )

        if not name:

            field_errors.append(
                f"{symbol}: name 為空"
            )

        # ----------------------------------------------------
        # market
        # ----------------------------------------------------

        market = clean_text(
            item.get(
                "market"
            )
        ).upper()

        if market not in (
            "TWSE",
            "TPEX",
        ):

            field_errors.append(
                f"{symbol}: market={market}"
            )

        # ----------------------------------------------------
        # type
        # ----------------------------------------------------

        sec_type = clean_text(
            item.get(
                "type"
            )
        )

        if sec_type not in (
            "Stock",
            "ETF",
        ):

            field_errors.append(
                f"{symbol}: type={sec_type}"
            )

        # ----------------------------------------------------
        # numeric fields
        # ----------------------------------------------------

        numeric_fields = {

            "institutional_1d",
            "institutional_5d",
            "institutional_10d",
            "institutional_20d",
            "day_trading_volume",
            "day_trading_rate",

        }

        for field in numeric_fields:

            if field not in item:
                continue

            value = item.get(
                field
            )

            if not is_finite_number(
                value
            ):

                field_errors.append(
                    f"{symbol}: "
                    f"{field} 非有效數值"
                )

    if field_errors:

        log(
            f"❌ 發現 "
            f"{len(field_errors)} "
            f"筆欄位錯誤"
        )

        for error in (
            field_errors[:100]
        ):

            log(
                f"   {error}"
            )

        errors.append(
            "全市場資料欄位驗證失敗"
        )

    else:

        log(
            f"✓ 已完成 "
            f"{chip_count} 檔全市場資料欄位驗證"
        )

    # ========================================================
    # 9. Forbidden fields
    # ========================================================

    section(
        "9. main_force_* 禁止欄位掃描"
    )

    forbidden_found = (
        scan_forbidden_fields(
            chip_stocks
        )
    )

    if forbidden_found:

        log(
            f"❌ 發現 "
            f"{len(forbidden_found)} "
            f"個禁止欄位"
        )

        for item in (
            forbidden_found[:100]
        ):

            log(
                f"   {item}"
            )

        errors.append(
            "發現禁止欄位"
        )

    else:

        log(
            "✓ 全市場 chip 均沒有 "
            "main_force_*"
        )

    # ========================================================
    # 10. Period fields
    # ========================================================

    section(
        "10. 1D / 5D / 10D / 20D 欄位驗證"
    )

    period_fields = [

        "institutional_1d",
        "institutional_5d",
        "institutional_10d",
        "institutional_20d",

    ]

    period_errors: List[str] = []

    for field in period_fields:

        missing_count = 0

        for symbol in sorted(
            chip_codes
        ):

            item = chip_stocks.get(
                symbol
            )

            if not isinstance(
                item,
                dict,
            ):

                missing_count += 1
                continue

            if field not in item:

                missing_count += 1

        if missing_count:

            log(
                f"❌ {field}："
                f"缺少 {missing_count} 檔"
            )

            period_errors.append(
                field
            )

        else:

            log(
                f"✓ {field}："
                f"{chip_count} "
                f"全市場欄位存在"
            )

    if period_errors:

        errors.append(
            "1D / 5D / 10D / 20D "
            "欄位不完整"
        )

    # ========================================================
    # 11. Metadata
    # ========================================================

    section(
        "11. Chip metadata 驗證"
    )

    schema_version = clean_text(
        chip.get(
            "schema_version"
        )
    )

    data_date = clean_text(
        chip.get(
            "data_date"
        )
    )

    generated_at = clean_text(
        chip.get(
            "generated_at"
        )
    )

    if schema_version:

        log(
            f"✓ schema_version："
            f"{schema_version}"
        )

    else:

        log(
            "❌ schema_version 缺失"
        )

        errors.append(
            "schema_version 缺失"
        )

    if data_date:

        log(
            f"✓ data_date："
            f"{data_date}"
        )

    else:

        log(
            "❌ data_date 缺失"
        )

        errors.append(
            "data_date 缺失"
        )

    if generated_at:

        log(
            f"✓ generated_at："
            f"{generated_at}"
        )

    else:

        log(
            "❌ generated_at 缺失"
        )

        errors.append(
            "generated_at 缺失"
        )

    # ========================================================
    # 12. Statistics
    # ========================================================

    section(
        "12. Statistics 驗證"
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

        errors.append(
            "statistics 結構錯誤"
        )

    else:

        statistic_fields = [

            "complete",
            "partial",
            "insufficient",
            "empty_name",

        ]

        statistic_errors = []

        for field in statistic_fields:

            value = statistics.get(
                field
            )

            try:

                value = int(
                    value
                )

            except Exception:

                statistic_errors.append(
                    field
                )

                continue

            if value < 0:

                statistic_errors.append(
                    field
                )

                continue

            log(
                f"✓ statistics.{field}："
                f"{value}"
            )

        if statistic_errors:

            errors.append(
                "statistics 欄位錯誤"
            )

    # ========================================================
    # 13. Stock / ETF
    # ========================================================

    section(
        "13. Stock / ETF 數量驗證"
    )

    chip_stock_count = sum(

        1

        for item in chip_stocks.values()

        if isinstance(
            item,
            dict,
        )
        and item.get(
            "type"
        ) == "Stock"

    )

    chip_etf_count = sum(

        1

        for item in chip_stocks.values()

        if isinstance(
            item,
            dict,
        )
        and item.get(
            "type"
        ) == "ETF"

    )

    declared_stock_count = chip.get(
        "stock_count"
    )

    declared_etf_count = chip.get(
        "etf_count"
    )

    try:

        declared_stock_count = int(
            declared_stock_count
        )

    except Exception:

        declared_stock_count = None

    try:

        declared_etf_count = int(
            declared_etf_count
        )

    except Exception:

        declared_etf_count = None

    if (
        declared_stock_count
        == chip_stock_count
    ):

        log(
            f"✓ Stock 數量一致："
            f"{chip_stock_count}"
        )

    else:

        log(
            "❌ Stock 數量不一致"
        )

        log(
            f"   metadata："
            f"{declared_stock_count}"
        )

        log(
            f"   實際："
            f"{chip_stock_count}"
        )

        errors.append(
            "Stock 數量不一致"
        )

    if (
        declared_etf_count
        == chip_etf_count
    ):

        log(
            f"✓ ETF 數量一致："
            f"{chip_etf_count}"
        )

    else:

        log(
            "❌ ETF 數量不一致"
        )

        log(
            f"   metadata："
            f"{declared_etf_count}"
        )

        log(
            f"   實際："
            f"{chip_etf_count}"
        )

        errors.append(
            "ETF 數量不一致"
        )

    if (
        chip_stock_count
        + chip_etf_count
        == chip_count
    ):

        log(
            "✓ Stock + ETF = "
            "Chip 全部標的"
        )

    else:

        log(
            "❌ Stock + ETF "
            "不等於 Chip 全部標的"
        )

        errors.append(
            "Stock + ETF 數量不一致"
        )

    # ========================================================
    # Final
    # ========================================================

    return finalize(
        errors,
        warnings,
        universe_count,
        chip_count,
        missing_from_chip,
        extra_in_chip,
    )


# ============================================================
# Finalizer
# ============================================================

def finalize(
    errors: List[str],
    warnings: List[str],
    universe_count: Any,
    chip_count: Any,
    missing_from_chip: List[str] | None = None,
    extra_in_chip: List[str] | None = None,
) -> int:

    section(
        "FINAL VERIFICATION"
    )

    if universe_count is None:

        log(
            "Universe：無法取得"
        )

    else:

        log(
            f"Universe："
            f"{universe_count} 檔"
        )

    if chip_count is None:

        log(
            "Chip：無法取得"
        )

    else:

        log(
            f"Chip："
            f"{chip_count} 檔"
        )

    if missing_from_chip is None:

        log(
            "Universe → Chip 缺少："
            "未完成計算"
        )

    else:

        log(
            f"Universe → Chip 缺少："
            f"{len(missing_from_chip)} 檔"
        )

    if extra_in_chip is None:

        log(
            "Chip → Universe 多出："
            "未完成計算"
        )

    else:

        log(
            f"Chip → Universe 多出："
            f"{len(extra_in_chip)} 檔"
        )

    log(
        f"錯誤："
        f"{len(errors)}"
    )

    log(
        f"警告："
        f"{len(warnings)}"
    )

    if errors:

        log("")

        log(
            "============================================================"
        )

        log(
            "❌ CHIP VERIFICATION FAILED"
        )

        log(
            "============================================================"
        )

        for error in errors:

            log(
                f"❌ {error}"
            )

        return 1

    log("")

    log(
        "============================================================"
    )

    log(
        "✓ CHIP VERIFICATION PASSED"
    )

    log(
        "============================================================"
    )

    log(
        "✓ Universe / Chip 全量一致"
    )

    log(
        "✓ 全市場資料欄位完整"
    )

    log(
        "✓ 1D / 5D / 10D / 20D 欄位存在"
    )

    log(
        "✓ main_force_* 完全不存在"
    )

    log(
        "✓ 驗證不依賴任何固定個股"
    )

    log(
        "✓ CHIP DATA 可以進入正式流程"
    )

    return 0


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )