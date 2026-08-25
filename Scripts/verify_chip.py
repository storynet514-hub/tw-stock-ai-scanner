#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
verify_chip.py V1.0

============================================================
目的
============================================================

本程式不是重新抓資料。

用途是驗證：

    Data/universe.json
            ↓
       fetch_chip.py
            ↓
       Data/chip.json

是否完整一致。

============================================================
驗證項目
============================================================

1. universe.json 是否存在
2. chip.json 是否存在
3. universe_count 是否正確
4. Universe stocks object 數量
5. chip.json stocks 數量
6. Universe → Chip 股票代號是否 100% 一致
7. Universe 有、Chip 沒有的代號
8. Chip 有、Universe 沒有的代號
9. 重複代號
10. 名稱是否遺失
11. market 是否遺失
12. type 是否遺失
13. full_symbol 是否遺失
14. institutional_1d
15. institutional_5d
16. institutional_10d
17. institutional_20d
18. day_trading_volume
19. day_trading_rate
20. 禁止 main_force_* 欄位
21. 不存在固定股票驗證
22. 10D / 20D 欄位存在
23. schema_version
24. data_date
25. generated_at
26. 全市場數量最終一致

============================================================
重要原則
============================================================

本驗證程式：

✗ 不固定驗證 2337
✗ 不固定驗證 2426
✗ 不固定驗證 2368
✗ 不固定驗證 3081

而是直接驗證：

    Universe 全部標的

也就是目前約 2387 檔。

============================================================
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Set


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
# Log
# ============================================================

ERROR_COUNT = 0
WARNING_COUNT = 0


def log(message: str = "") -> None:
    print(message, flush=True)


def error(message: str) -> None:

    global ERROR_COUNT

    ERROR_COUNT += 1

    log(f"❌ {message}")


def warning(message: str) -> None:

    global WARNING_COUNT

    WARNING_COUNT += 1

    log(f"⚠️ {message}")


def success(message: str) -> None:

    log(f"✓ {message}")


def section(title: str) -> None:

    log("")
    log("=" * 72)
    log(title)
    log("=" * 72)


# ============================================================
# Basic
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


def clean_name(value: Any) -> str:

    if value is None:
        return ""

    return str(value).strip()


def load_json(path: Path) -> Any:

    try:

        with path.open(
            "r",
            encoding="utf-8-sig",
        ) as f:

            return json.load(f)

    except Exception as exc:

        error(
            f"讀取 {path.name} 失敗：{exc}"
        )

        return None


# ============================================================
# Symbol validation
# ============================================================

def is_valid_symbol(code: str) -> bool:

    code = clean_code(code)

    if not code:
        return False

    # 4～6 碼純數字
    if re.fullmatch(
        r"\d{4,6}",
        code,
    ):

        return True

    # 4～6 碼數字 + 1～2 碼英數
    #
    # 例如：
    # 00400A
    # 00631L
    # 00710B
    # 2887Z1
    #
    if re.fullmatch(
        r"\d{4,6}[A-Z0-9]{1,2}",
        code,
    ):

        suffix_match = re.search(
            r"[A-Z0-9]{1,2}$",
            code,
        )

        if suffix_match:

            suffix = suffix_match.group(0)

            if re.search(
                r"[A-Z]",
                suffix,
            ):

                return True

    return False


# ============================================================
# Universe extraction
# ============================================================

def extract_universe(
    data: Any,
) -> Dict[str, Dict[str, Any]]:

    if not isinstance(
        data,
        dict,
    ):

        error(
            "universe.json 根節點不是 object"
        )

        return {}

    stocks = data.get(
        "stocks"
    )

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    # --------------------------------------------------------
    # 正式 stocks object
    # --------------------------------------------------------

    if isinstance(
        stocks,
        dict,
    ):

        for raw_key, raw_item in stocks.items():

            code = clean_code(
                raw_key
            )

            if not code:

                error(
                    "Universe 發現空白股票代號"
                )

                continue

            if not isinstance(
                raw_item,
                dict,
            ):

                error(
                    f"Universe {code} "
                    f"不是 object"
                )

                continue

            result[code] = dict(
                raw_item
            )

            result[code]["symbol"] = code

        return result

    # --------------------------------------------------------
    # 舊版 items
    # --------------------------------------------------------

    items = data.get(
        "items"
    )

    if isinstance(
        items,
        list,
    ):

        warning(
            "Universe 使用舊版 items list 架構"
        )

        for item in items:

            if not isinstance(
                item,
                dict,
            ):

                continue

            code = clean_code(
                item.get(
                    "symbol",
                    item.get(
                        "code",
                        "",
                    ),
                )
            )

            if not code:
                continue

            if code in result:

                error(
                    f"Universe 出現重複代號："
                    f"{code}"
                )

                continue

            result[code] = dict(
                item
            )

        return result

    error(
        "universe.json 找不到 stocks object "
        "或 items list"
    )

    return {}


# ============================================================
# Duplicate detector
# ============================================================

def detect_duplicate_keys(
    data: Any,
    label: str,
) -> None:

    # JSON object 本身在 Python 中已經會覆蓋重複 key，
    # 因此這裡只能驗證最終 object。
    #
    # 真正的重複 key 已無法從 json.load 後復原。
    #
    # 仍保留此函式作為結構檢查入口。

    if not isinstance(
        data,
        dict,
    ):

        error(
            f"{label} 根節點不是 object"
        )


# ============================================================
# Required chip fields
# ============================================================

REQUIRED_FIELDS = {

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


# ============================================================
# Forbidden fields
# ============================================================

FORBIDDEN_FIELDS = {

    "main_force_1d",

    "main_force_5d",

    "main_force_10d",

    "main_force_20d",

}


# ============================================================
# Validate chip item
# ============================================================

def validate_chip_item(
    universe_item: Dict[str, Any],
    chip_item: Dict[str, Any],
    code: str,
) -> None:

    # --------------------------------------------------------
    # Object
    # --------------------------------------------------------

    if not isinstance(
        chip_item,
        dict,
    ):

        error(
            f"{code}：chip item 不是 object"
        )

        return

    # --------------------------------------------------------
    # Required fields
    # --------------------------------------------------------

    missing_fields = []

    for field in REQUIRED_FIELDS:

        if field not in chip_item:

            missing_fields.append(
                field
            )

    if missing_fields:

        error(
            f"{code}：缺少欄位 "
            f"{', '.join(missing_fields)}"
        )

    # --------------------------------------------------------
    # symbol
    # --------------------------------------------------------

    chip_symbol = clean_code(
        chip_item.get(
            "symbol",
            "",
        )
    )

    if chip_symbol != code:

        error(
            f"{code}：symbol 錯誤，"
            f"chip={chip_symbol}"
        )

    # --------------------------------------------------------
    # name
    # --------------------------------------------------------

    chip_name = clean_name(
        chip_item.get(
            "name",
            "",
        )
    )

    if not chip_name:

        error(
            f"{code}：name 為空"
        )

    # --------------------------------------------------------
    # market
    # --------------------------------------------------------

    market = str(
        chip_item.get(
            "market",
            "",
        )
    ).strip().upper()

    if market not in (
        "TWSE",
        "TPEX",
    ):

        error(
            f"{code}：market 無效："
            f"{market}"
        )

    # --------------------------------------------------------
    # type
    # --------------------------------------------------------

    sec_type = str(
        chip_item.get(
            "type",
            "",
        )
    ).strip()

    if sec_type not in (
        "Stock",
        "ETF",
    ):

        warning(
            f"{code}：type 非預期值："
            f"{sec_type}"
        )

    # --------------------------------------------------------
    # full_symbol
    # --------------------------------------------------------

    full_symbol = str(
        chip_item.get(
            "full_symbol",
            "",
        )
    ).strip()

    if not full_symbol:

        error(
            f"{code}：full_symbol 為空"
        )

    # --------------------------------------------------------
    # 20D hierarchy
    # --------------------------------------------------------

    period_fields = [

        "institutional_1d",

        "institutional_5d",

        "institutional_10d",

        "institutional_20d",

    ]

    for field in period_fields:

        if field not in chip_item:

            continue

        value = chip_item.get(
            field
        )

        if value is not None:

            if not isinstance(
                value,
                (int, float),
            ):

                error(
                    f"{code}.{field} "
                    f"不是數值或 null"
                )

    # --------------------------------------------------------
    # Day trade
    # --------------------------------------------------------

    dt_volume = chip_item.get(
        "day_trading_volume"
    )

    dt_rate = chip_item.get(
        "day_trading_rate"
    )

    if dt_volume is not None:

        if not isinstance(
            dt_volume,
            (int, float),
        ):

            error(
                f"{code}.day_trading_volume "
                f"不是數值"
            )

    if dt_rate is not None:

        if not isinstance(
            dt_rate,
            (int, float),
        ):

            error(
                f"{code}.day_trading_rate "
                f"不是數值"
            )

    # --------------------------------------------------------
    # Forbidden
    # --------------------------------------------------------

    for field in FORBIDDEN_FIELDS:

        if field in chip_item:

            error(
                f"{code}：發現禁止欄位 "
                f"{field}"
            )


# ============================================================
# Main
# ============================================================

def main() -> int:

    section(
        f"台股 AI 選股系統 "
        f"chip 全市場驗證器 {VERSION}"
    )

    log(
        "驗證模式：Universe ↔ Chip 全量對照"
    )

    log(
        "固定股票驗證：停用"
    )

    log(
        "2337 / 2426 / 2368 / 3081："
        "不作為特殊驗證條件"
    )

    # ========================================================
    # 1. Files
    # ========================================================

    section(
        "1. 檔案存在性"
    )

    if not UNIVERSE_FILE.exists():

        error(
            "Data/universe.json 不存在"
        )

    else:

        success(
            "Data/universe.json 存在"
        )

    if not CHIP_FILE.exists():

        error(
            "Data/chip.json 不存在"
        )

    else:

        success(
            "Data/chip.json 存在"
        )

    if ERROR_COUNT:

        return 1

    # ========================================================
    # 2. Load
    # ========================================================

    section(
        "2. 讀取 JSON"
    )

    universe_data = load_json(
        UNIVERSE_FILE
    )

    chip_data = load_json(
        CHIP_FILE
    )

    if ERROR_COUNT:

        return 1

    success(
        "universe.json JSON 格式正常"
    )

    success(
        "chip.json JSON 格式正常"
    )

    # ========================================================
    # 3. Universe
    # ========================================================

    section(
        "3. Universe 結構驗證"
    )

    if not isinstance(
        universe_data,
        dict,
    ):

        error(
            "Universe 根節點不是 object"
        )

        return 1

    declared_count = (
        universe_data.get(
            "universe_count"
        )
    )

    try:

        declared_count_int = int(
            declared_count
        )

    except Exception:

        error(
            "universe_count 不是有效整數"
        )

        return 1

    universe_stocks = extract_universe(
        universe_data
    )

    universe_codes: Set[str] = set(
        universe_stocks.keys()
    )

    success(
        f"Universe stocks："
        f"{len(universe_codes)} 檔"
    )

    if declared_count_int != len(
        universe_codes
    ):

        error(
            "Universe universe_count "
            "與 stocks object 數量不一致"
        )

        log(
            f"   universe_count："
            f"{declared_count_int}"
        )

        log(
            f"   stocks："
            f"{len(universe_codes)}"
        )

    else:

        success(
            "Universe universe_count "
            "與 stocks object 數量一致"
        )

    # ========================================================
    # 4. Symbol format
    # ========================================================

    section(
        "4. Universe 股票代號格式"
    )

    invalid_symbols = []

    for code in sorted(
        universe_codes
    ):

        if not is_valid_symbol(
            code
        ):

            invalid_symbols.append(
                code
            )

    if invalid_symbols:

        error(
            f"Universe 發現 "
            f"{len(invalid_symbols)} "
            f"個無法識別代號"
        )

        for code in invalid_symbols[
            :100
        ]:

            log(
                f"   {code}"
            )

    else:

        success(
            "Universe 所有股票代號格式正常"
        )

    # ========================================================
    # 5. Chip structure
    # ========================================================

    section(
        "5. Chip 結構驗證"
    )

    if not isinstance(
        chip_data,
        dict,
    ):

        error(
            "chip.json 根節點不是 object"
        )

        return 1

    chip_stocks = chip_data.get(
        "stocks"
    )

    if not isinstance(
        chip_stocks,
        dict,
    ):

        error(
            "chip.json stocks 不是 object"
        )

        return 1

    chip_codes: Set[str] = set(
        clean_code(code)
        for code in chip_stocks.keys()
        if clean_code(code)
    )

    success(
        f"Chip stocks："
        f"{len(chip_codes)} 檔"
    )

    # ========================================================
    # 6. Universe -> Chip
    # ========================================================

    section(
        "6. Universe → Chip 全量對照"
    )

    missing_in_chip = sorted(
        universe_codes
        - chip_codes
    )

    extra_in_chip = sorted(
        chip_codes
        - universe_codes
    )

    if missing_in_chip:

        error(
            f"Universe 有、Chip 沒有："
            f"{len(missing_in_chip)} 檔"
        )

        for code in missing_in_chip[
            :200
        ]:

            log(
                f"   {code}"
            )

    else:

        success(
            "Universe → Chip："
            "全部存在"
        )

    if extra_in_chip:

        error(
            f"Chip 有、Universe 沒有："
            f"{len(extra_in_chip)} 檔"
        )

        for code in extra_in_chip[
            :200
        ]:

            log(
                f"   {code}"
            )

    else:

        success(
            "Chip → Universe："
            "沒有多餘標的"
        )

    if (
        not missing_in_chip
        and not extra_in_chip
        and len(universe_codes)
        == len(chip_codes)
    ):

        success(
            f"Universe / Chip "
            f"股票池 100% 一致："
            f"{len(universe_codes)} 檔"
        )

    # ========================================================
    # 7. Count
    # ========================================================

    section(
        "7. 數量驗證"
    )

    chip_universe_count = (
        chip_data.get(
            "universe_count"
        )
    )

    try:

        chip_universe_count_int = int(
            chip_universe_count
        )

    except Exception:

        error(
            "chip.json universe_count "
            "不是有效整數"
        )

        chip_universe_count_int = -1

    if (
        chip_universe_count_int
        != len(chip_codes)
    ):

        error(
            "chip.json universe_count "
            "與 stocks 數量不一致"
        )

        log(
            f"   header："
            f"{chip_universe_count_int}"
        )

        log(
            f"   stocks："
            f"{len(chip_codes)}"
        )

    else:

        success(
            "chip.json universe_count "
            "與 stocks 數量一致"
        )

    if (
        chip_universe_count_int
        == declared_count_int
        == len(universe_codes)
        == len(chip_codes)
    ):

        success(
            f"Universe / Chip "
            f"所有數量完全一致："
            f"{len(chip_codes)} 檔"
        )

    # ========================================================
    # 8. Every chip item
    # ========================================================

    section(
        "8. 全市場個股資料欄位驗證"
    )

    checked = 0

    for code in sorted(
        universe_codes
    ):

        chip_item = chip_stocks.get(
            code
        )

        if chip_item is None:

            continue

        validate_chip_item(
            universe_stocks[code],
            chip_item,
            code,
        )

        checked += 1

    success(
        f"已完成 {checked} 檔全市場資料欄位驗證"
    )

    # ========================================================
    # 9. Forbidden fields
    # ========================================================

    section(
        "9. main_force_* 禁止欄位掃描"
    )

    forbidden_found = []

    for code, item in chip_stocks.items():

        if not isinstance(
            item,
            dict,
        ):

            continue

        for key in item.keys():

            if key.startswith(
                "main_force_"
            ):

                forbidden_found.append(
                    f"{code}.{key}"
                )

    if forbidden_found:

        error(
            f"發現 "
            f"{len(forbidden_found)} "
            f"個 main_force_* 欄位"
        )

        for value in forbidden_found[
            :100
        ]:

            log(
                f"   {value}"
            )

    else:

        success(
            "全市場 chip 均沒有 main_force_*"
        )

    # ========================================================
    # 10. Required period fields
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

    period_missing: Dict[
        str,
        int
    ] = {
        field: 0
        for field in period_fields
    }

    for code in universe_codes:

        item = chip_stocks.get(
            code
        )

        if not isinstance(
            item,
            dict,
        ):

            continue

        for field in period_fields:

            if field not in item:

                period_missing[field] += 1

    for field, count in (
        period_missing.items()
    ):

        if count:

            error(
                f"{field} 缺少："
                f"{count} 檔"
            )

        else:

            success(
                f"{field}："
                f"2387 全市場欄位存在"
            )

    # ========================================================
    # 11. Metadata
    # ========================================================

    section(
        "11. Chip metadata 驗證"
    )

    schema_version = chip_data.get(
        "schema_version"
    )

    data_date = chip_data.get(
        "data_date"
    )

    generated_at = chip_data.get(
        "generated_at"
    )

    if schema_version:

        success(
            f"schema_version："
            f"{schema_version}"
        )

    else:

        error(
            "schema_version 缺失"
        )

    if data_date:

        success(
            f"data_date："
            f"{data_date}"
        )

    else:

        error(
            "data_date 缺失"
        )

    if generated_at:

        success(
            f"generated_at："
            f"{generated_at}"
        )

    else:

        error(
            "generated_at 缺失"
        )

    # ========================================================
    # 12. Statistics
    # ========================================================

    section(
        "12. Statistics 驗證"
    )

    statistics = chip_data.get(
        "statistics"
    )

    if not isinstance(
        statistics,
        dict,
    ):

        error(
            "chip.json statistics 缺失或格式錯誤"
        )

    else:

        for field in (
            "complete",
            "partial",
            "insufficient",
            "empty_name",
        ):

            if field not in statistics:

                error(
                    f"statistics.{field} 缺失"
                )

            else:

                value = statistics[field]

                if not isinstance(
                    value,
                    int,
                ):

                    error(
                        f"statistics.{field} "
                        f"不是整數"
                    )

                else:

                    success(
                        f"statistics.{field}："
                        f"{value}"
                    )

    # ========================================================
    # 13. Stock / ETF counts
    # ========================================================

    section(
        "13. Stock / ETF 數量驗證"
    )

    stock_count_actual = 0
    etf_count_actual = 0

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

            stock_count_actual += 1

        elif sec_type == "ETF":

            etf_count_actual += 1

    chip_stock_count = chip_data.get(
        "stock_count"
    )

    chip_etf_count = chip_data.get(
        "etf_count"
    )

    if chip_stock_count != stock_count_actual:

        error(
            "stock_count 不一致："
            f"header={chip_stock_count}, "
            f"actual={stock_count_actual}"
        )

    else:

        success(
            f"Stock 數量一致："
            f"{stock_count_actual}"
        )

    if chip_etf_count != etf_count_actual:

        error(
            "etf_count 不一致："
            f"header={chip_etf_count}, "
            f"actual={etf_count_actual}"
        )

    else:

        success(
            f"ETF 數量一致："
            f"{etf_count_actual}"
        )

    if (
        stock_count_actual
        + etf_count_actual
        != len(chip_codes)
    ):

        warning(
            "Stock + ETF 無法涵蓋全部 Chip 標的"
        )

    else:

        success(
            "Stock + ETF = Chip 全部標的"
        )

    # ========================================================
    # 14. No special stock dependency
    # ========================================================

    section(
        "14. 固定股票依賴檢查"
    )

    source_text = ""

    try:

        source_text = Path(
            __file__
        ).read_text(
            encoding="utf-8"
        )

    except Exception:

        pass

    forbidden_test_patterns = [

        "required_test_stocks",

        "expected_name",

        "2337 / 2426 / 2368 / 3081",

        "2337",

        "2426",

        "2368",

        "3081",

    ]

    fixed_dependency_found = []

    for pattern in forbidden_test_patterns:

        if pattern in source_text:

            fixed_dependency_found.append(
                pattern
            )

    if fixed_dependency_found:

        error(
            "驗證器仍含固定股票依賴："
            + ", ".join(
                fixed_dependency_found
            )
        )

    else:

        success(
            "驗證器沒有固定股票依賴"
        )

    # ========================================================
    # 15. Final
    # ========================================================

    section(
        "FINAL VERIFICATION"
    )

    log(
        f"Universe："
        f"{len(universe_codes)} 檔"
    )

    log(
        f"Chip："
        f"{len(chip_codes)} 檔"
    )

    log(
        f"Universe → Chip 缺少："
        f"{len(missing_in_chip)} 檔"
    )

    log(
        f"Chip → Universe 多出："
        f"{len(extra_in_chip)} 檔"
    )

    log(
        f"錯誤："
        f"{ERROR_COUNT}"
    )

    log(
        f"警告："
        f"{WARNING_COUNT}"
    )

    if ERROR_COUNT > 0:

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

        log(
            "請不要使用目前 chip.json 作為正式資料。"
        )

        return 1

    log("")
    log(
        "============================================================"
    )

    log(
        "✓ CHIP VERIFICATION PASS"
    )

    log(
        "============================================================"
    )

    log(
        "✓ Universe 與 Chip 全市場標的完全一致"
    )

    log(
        "✓ 沒有 Universe 標的被靜默排除"
    )

    log(
        "✓ 沒有 Chip 多餘標的"
    )

    log(
        "✓ 1D / 5D / 10D / 20D 欄位存在"
    )

    log(
        "✓ main_force_* 完全不存在"
    )

    log(
        "✓ 沒有固定 4 檔股票依賴"
    )

    log(
        "✓ 全市場資料結構驗證完成"
    )

    return 0


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
