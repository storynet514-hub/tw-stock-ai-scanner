#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_universe.py
正式版 V10.3

============================================================
V10.3 核心定位
============================================================

本程式只負責建立：

    Data/universe.json

Universe = 系統可分析的「完整標的宇宙」

本程式不負責：

    ❌ 今日選股
    ❌ 六項核心選股
    ❌ RSI
    ❌ MACD
    ❌ KD
    ❌ 成交量條件
    ❌ 主力籌碼
    ❌ DCA
    ❌ Top 10
    ❌ 今日精選
    ❌ UI

資料流：

    原始標的資料
          ↓
    build_universe.py
          ↓
    Data/universe.json
          ↓
    analysis.py / 分析層
          ↓
    Data/analysis.json
          ↓
    build_ui_data.py
          ↓
    Data/ui_data.json
          ↓
    index.html


============================================================
V10.3 重要架構原則
============================================================

1. Universe 是完整標的清單。

2. Universe 不等於今日選股結果。

3. Universe 數量不得被短線條件縮減。

4. 不存在：

       六項核心
       RSI > 50
       MACD 黃金交叉
       KD 黃金交叉
       成交量 > 5 日均量
       站上 20MA

   等任何選股邏輯。

5. analysis.json 才負責分析。

6. build_ui_data.py 只負責：

       analysis
           ↓
       UI schema

7. 本程式不讀取：

       analysis.json
       ui_data.json
       prices.json
       chip.json

   避免形成反向依賴。

8. 不硬編碼 1985、2143 等固定股票數量。

9. universe_count 永遠由實際輸出的 stocks 數量產生。

10. symbol 必須是乾淨的台股代號：

       2330
       2337
       3081

   不輸出：

       2330.TW
       2337.TW
       3081.TWO

11. ETF 與一般股票均可存在於 Universe。

12. 債券 ETF / ETN 等商品若原始資料提供，
    依 instrument_type 正確分類。

13. 不因為商品不是普通股票就從 Universe
    任意刪除。

14. 不建立任何預設持倉。

15. 輸出的 JSON 必須可被後續分析層直接使用。


============================================================
輸入資料
============================================================

優先使用：

    Data/raw_universe.json

若不存在，依序嘗試：

    Data/universe_source.json
    Data/stock_universe.json

本程式不會自行探測 API。

============================================================
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


# ============================================================
# Version
# ============================================================

VERSION = "UNIVERSE-V10.3"


# ============================================================
# Path
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

OUTPUT_FILE = DATA_DIR / "universe.json"

SOURCE_CANDIDATES = [
    DATA_DIR / "raw_universe.json",
    DATA_DIR / "universe_source.json",
    DATA_DIR / "stock_universe.json",
]


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
# JSON
# ============================================================

def load_json(path: Path) -> Any:
    if not path.exists():
        raise RuntimeError(
            f"找不到輸入檔案：{path}"
        )

    try:
        with path.open(
            "r",
            encoding="utf-8-sig",
        ) as f:
            return json.load(f)

    except Exception as exc:
        raise RuntimeError(
            f"JSON 讀取失敗：{path}：{exc}"
        ) from exc


# ============================================================
# Number
# ============================================================

def number(value: Any) -> Optional[float]:

    if value is None:
        return None

    if isinstance(value, bool):
        return None

    try:
        value = float(
            str(value)
            .replace(",", "")
            .strip()
        )

        if not math.isfinite(value):
            return None

        return value

    except Exception:
        return None


# ============================================================
# Text
# ============================================================

def clean_text(value: Any) -> str:

    if value is None:
        return ""

    return str(value).strip()


# ============================================================
# First Value
# ============================================================

def first_value(
    record: Dict[str, Any],
    keys: Iterable[str],
) -> Any:

    for key in keys:

        if (
            key in record
            and record[key] is not None
        ):
            return record[key]

    return None


# ============================================================
# Symbol
# ============================================================

def normalize_symbol(value: Any) -> str:

    if value is None:
        return ""

    text = str(value).strip().upper()

    if not text:
        return ""

    # --------------------------------------------------------
    # 移除常見市場 suffix
    # --------------------------------------------------------

    for suffix in (
        ".TW",
        ".TWO",
        ".TSE",
        ".OTC",
    ):

        if text.endswith(suffix):

            text = text[
                :-len(suffix)
            ]

            break

    # --------------------------------------------------------
    # 移除前後空白
    # --------------------------------------------------------

    text = text.strip()

    # --------------------------------------------------------
    # 台股代號基本驗證
    #
    # 一般股票：
    #   4 碼
    #
    # 特殊商品：
    #   4~6 碼亦允許
    #
    # 不允許空白。
    # --------------------------------------------------------

    if not text:
        return ""

    if any(
        char.isspace()
        for char in text
    ):
        return ""

    return text


# ============================================================
# Market
# ============================================================

def normalize_market(
    record: Dict[str, Any],
) -> str:

    value = first_value(
        record,
        [
            "market",
            "exchange",
            "market_type",
            "市場",
            "市場別",
            "交易所",
        ],
    )

    text = clean_text(
        value
    ).lower()

    if not text:
        return ""

    mapping = {

        "twse": "TWSE",
        "tse": "TWSE",
        "上市": "TWSE",

        "otc": "TPEx",
        "tpex": "TPEx",
        "上櫃": "TPEx",

        "emerging": "ESB",
        "esb": "ESB",
        "興櫃": "ESB",

        "rotc": "ROTC",

        "taiwan": "TWSE",
        "tw": "TWSE",
    }

    return mapping.get(
        text,
        clean_text(value),
    )


# ============================================================
# Instrument Type
# ============================================================

def normalize_instrument_type(
    record: Dict[str, Any],
) -> str:

    value = first_value(
        record,
        [
            "instrument_type",
            "security_type",
            "type",
            "product_type",
            "category",
            "商品類型",
            "證券類型",
            "類型",
        ],
    )

    text = clean_text(
        value
    ).lower()

    # --------------------------------------------------------
    # ETF
    # --------------------------------------------------------

    if any(
        token in text
        for token in (
            "etf",
            "exchange traded fund",
            "指數型基金",
            "指數基金",
            "基金",
        )
    ):
        return "etf"

    # --------------------------------------------------------
    # Bond
    # --------------------------------------------------------

    if any(
        token in text
        for token in (
            "bond",
            "債券",
            "公司債",
            "政府債",
        )
    ):
        return "bond"

    # --------------------------------------------------------
    # ETN
    # --------------------------------------------------------

    if any(
        token in text
        for token in (
            "etn",
            "指數投資證券",
        )
    ):
        return "etn"

    # --------------------------------------------------------
    # Default
    # --------------------------------------------------------

    return "stock"


# ============================================================
# Name
# ============================================================

def get_name(
    record: Dict[str, Any],
    symbol: str,
) -> str:

    value = first_value(
        record,
        [
            "name",
            "stock_name",
            "security_name",
            "company_name",
            "名稱",
            "股票名稱",
            "證券名稱",
        ],
    )

    name = clean_text(
        value
    )

    return name or symbol


# ============================================================
# Source Record Extraction
# ============================================================

def extract_records(
    data: Any,
) -> List[Dict[str, Any]]:

    # --------------------------------------------------------
    # Case 1
    #
    # {
    #     "stocks": {
    #         "2330": {...}
    #     }
    # }
    # --------------------------------------------------------

    if isinstance(
        data,
        dict,
    ):

        stocks = data.get(
            "stocks"
        )

        if isinstance(
            stocks,
            dict,
        ):

            result = []

            for raw_symbol, value in stocks.items():

                if not isinstance(
                    value,
                    dict,
                ):
                    continue

                record = dict(
                    value
                )

                if not record.get(
                    "symbol"
                ):
                    record[
                        "symbol"
                    ] = raw_symbol

                result.append(
                    record
                )

            return result

        # ----------------------------------------------------
        # Case 2
        #
        # {
        #     "2330": {...},
        #     "2337": {...}
        # }
        # ----------------------------------------------------

        result = []

        for raw_symbol, value in data.items():

            if raw_symbol in {
                "schema_version",
                "generated_at",
                "updated_at",
                "universe_count",
                "metadata",
            }:
                continue

            if not isinstance(
                value,
                dict,
            ):
                continue

            record = dict(
                value
            )

            if not record.get(
                "symbol"
            ):
                record[
                    "symbol"
                ] = raw_symbol

            result.append(
                record
            )

        if result:
            return result

    # --------------------------------------------------------
    # Case 3
    #
    # [
    #   {...},
    #   {...}
    # ]
    # --------------------------------------------------------

    if isinstance(
        data,
        list,
    ):

        return [
            dict(item)
            for item in data
            if isinstance(
                item,
                dict,
            )
        ]

    raise RuntimeError(
        "Universe source 格式無法辨識"
    )


# ============================================================
# Build Record
# ============================================================

def build_record(
    source: Dict[str, Any],
) -> Optional[Dict[str, Any]]:

    symbol = normalize_symbol(
        first_value(
            source,
            [
                "symbol",
                "code",
                "stock_id",
                "ticker",
                "證券代號",
                "股票代號",
                "代號",
            ],
        )
    )

    if not symbol:
        return None

    name = get_name(
        source,
        symbol,
    )

    market = normalize_market(
        source
    )

    instrument_type = normalize_instrument_type(
        source
    )

    # --------------------------------------------------------
    # 建立乾淨 schema
    #
    # 不把來源中的分析欄位全部複製進 Universe。
    # --------------------------------------------------------

    record: Dict[str, Any] = {

        "symbol":
            symbol,

        "name":
            name,

        "market":
            market,

        "instrument_type":
            instrument_type,
    }

    # --------------------------------------------------------
    # 保留必要的來源識別資訊
    # --------------------------------------------------------

    industry = first_value(
        source,
        [
            "industry",
            "industry_name",
            "產業",
            "產業名稱",
        ],
    )

    if industry is not None:

        industry = clean_text(
            industry
        )

        if industry:
            record[
                "industry"
            ] = industry

    # --------------------------------------------------------
    # ISIN
    # --------------------------------------------------------

    isin = first_value(
        source,
        [
            "isin",
            "ISIN",
        ],
    )

    if isin is not None:

        isin = clean_text(
            isin
        )

        if isin:
            record[
                "isin"
            ] = isin

    # --------------------------------------------------------
    # Active
    # --------------------------------------------------------

    active = first_value(
        source,
        [
            "active",
            "is_active",
            "enabled",
            "status",
        ],
    )

    if isinstance(
        active,
        bool,
    ):

        record[
            "active"
        ] = active

    elif active is not None:

        text = clean_text(
            active
        ).lower()

        if text in {
            "true",
            "1",
            "yes",
            "active",
            "上市",
            "上櫃",
            "興櫃",
        }:

            record[
                "active"
            ] = True

        elif text in {
            "false",
            "0",
            "no",
            "inactive",
        }:

            record[
                "active"
            ] = False

    # --------------------------------------------------------
    # Default active
    # --------------------------------------------------------

    if "active" not in record:

        record[
            "active"
        ] = True

    return record


# ============================================================
# Build Universe
# ============================================================

def build_universe(
    records: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    universe: Dict[
        str,
        Dict[str, Any]
    ] = {}

    duplicate_count = 0
    invalid_count = 0

    for source in records:

        record = build_record(
            source
        )

        if record is None:

            invalid_count += 1

            continue

        symbol = record[
            "symbol"
        ]

        if symbol in universe:

            duplicate_count += 1

            # ------------------------------------------------
            # 優先保留資訊較完整的 record
            # ------------------------------------------------

            current = universe[
                symbol
            ]

            current_score = len(
                [
                    key
                    for key, value
                    in current.items()
                    if value not in (
                        None,
                        "",
                    )
                ]
            )

            new_score = len(
                [
                    key
                    for key, value
                    in record.items()
                    if value not in (
                        None,
                        "",
                    )
                ]
            )

            if new_score > current_score:

                universe[
                    symbol
                ] = record

            continue

        universe[
            symbol
        ] = record

    log(
        f"來源 records：{len(records)}"
    )

    log(
        f"有效標的：{len(universe)}"
    )

    log(
        f"無效 records：{invalid_count}"
    )

    log(
        f"重複 symbol：{duplicate_count}"
    )

    return universe


# ============================================================
# Validate Universe
# ============================================================

def validate_universe(
    universe: Dict[str, Dict[str, Any]],
) -> None:

    section(
        "Universe V10.3 Validation"
    )

    if not universe:

        raise RuntimeError(
            "❌ Universe 為空，拒絕輸出"
        )

    # --------------------------------------------------------
    # Symbol
    # --------------------------------------------------------

    for symbol, record in universe.items():

        if not symbol:

            raise RuntimeError(
                "❌ 發現空白 symbol"
            )

        if not isinstance(
            record,
            dict,
        ):

            raise RuntimeError(
                f"❌ {symbol} record 不是 object"
            )

        if record.get(
            "symbol"
        ) != symbol:

            raise RuntimeError(
                f"❌ {symbol} symbol 欄位不一致"
            )

        if not record.get(
            "name"
        ):

            raise RuntimeError(
                f"❌ {symbol} 缺少 name"
            )

        if not record.get(
            "instrument_type"
        ):

            raise RuntimeError(
                f"❌ {symbol} 缺少 instrument_type"
            )

    # --------------------------------------------------------
    # duplicate
    # --------------------------------------------------------

    symbols = list(
        universe.keys()
    )

    if len(symbols) != len(
        set(symbols)
    ):

        raise RuntimeError(
            "❌ Universe 存在重複 symbol"
        )

    # --------------------------------------------------------
    # 不允許 suffix
    # --------------------------------------------------------

    for symbol in symbols:

        if symbol.endswith(
            ".TW"
        ) or symbol.endswith(
            ".TWO"
        ):

            raise RuntimeError(
                f"❌ Universe symbol 不應包含市場 suffix：{symbol}"
            )

    # --------------------------------------------------------
    # 不允許選股邏輯欄位
    #
    # Universe 不應出現今日選股結果。
    # --------------------------------------------------------

    forbidden_selection_keys = {

        "qualified",
        "score",
        "short_term",
        "short_term_candidates",
        "today_picks",
        "recommendation",
        "strength",
        "rsi",
        "macd",
        "kd",
        "volume_ratio",
        "main_force",
        "dca",
    }

    for symbol, record in universe.items():

        lower_keys = {
            str(key).lower()
            for key in record.keys()
        }

        overlap = (
            forbidden_selection_keys
            & lower_keys
        )

        if overlap:

            raise RuntimeError(
                f"❌ {symbol} Universe 混入分析/選股欄位："
                + ", ".join(
                    sorted(overlap)
                )
            )

    # --------------------------------------------------------
    # 分類統計
    # --------------------------------------------------------

    type_counts: Dict[
        str,
        int
    ] = {}

    market_counts: Dict[
        str,
        int
    ] = {}

    for record in universe.values():

        instrument_type = record.get(
            "instrument_type",
            "unknown",
        )

        market = record.get(
            "market",
            "",
        ) or "unknown"

        type_counts[
            instrument_type
        ] = (
            type_counts.get(
                instrument_type,
                0,
            )
            + 1
        )

        market_counts[
            market
        ] = (
            market_counts.get(
                market,
                0,
            )
            + 1
        )

    log(
        f"Universe 總數：{len(universe)}"
    )

    log("")

    log("商品分類：")

    for key in sorted(
        type_counts
    ):

        log(
            f"  {key}: "
            f"{type_counts[key]}"
        )

    log("")

    log("市場分類：")

    for key in sorted(
        market_counts
    ):

        log(
            f"  {key}: "
            f"{market_counts[key]}"
        )

    log("")

    log(
        "✓ Universe schema：PASS"
    )

    log(
        "✓ Symbol uniqueness：PASS"
    )

    log(
        "✓ No selection logic：PASS"
    )

    log(
        "✓ No legacy six-core fields：PASS"
    )


# ============================================================
# Output
# ============================================================

def build_output(
    universe: Dict[str, Dict[str, Any]],
    source_file: Path,
) -> Dict[str, Any]:

    ordered_stocks: Dict[
        str,
        Dict[str, Any]
    ] = {}

    for symbol in sorted(
        universe.keys()
    ):

        ordered_stocks[
            symbol
        ] = universe[
            symbol
        ]

    return {

        "schema_version":
            VERSION,

        "generated_at":
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "source":
            source_file.name,

        "universe_count":
            len(ordered_stocks),

        "stocks":
            ordered_stocks,
    }


# ============================================================
# Save
# ============================================================

def save_output(
    output: Dict[str, Any],
) -> None:

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_file = OUTPUT_FILE.with_suffix(
        ".json.tmp"
    )

    try:

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

        # ----------------------------------------------------
        # 寫入後重新讀取
        # ----------------------------------------------------

        with temp_file.open(
            "r",
            encoding="utf-8",
        ) as f:

            verify = json.load(
                f
            )

        if not isinstance(
            verify,
            dict,
        ):

            raise RuntimeError(
                "寫入驗證失敗：root 不是 object"
            )

        stocks = verify.get(
            "stocks"
        )

        if not isinstance(
            stocks,
            dict,
        ):

            raise RuntimeError(
                "寫入驗證失敗：stocks 不是 object"
            )

        count = verify.get(
            "universe_count"
        )

        if count != len(stocks):

            raise RuntimeError(
                "寫入驗證失敗："
                f"universe_count={count}, "
                f"actual={len(stocks)}"
            )

        if not stocks:

            raise RuntimeError(
                "寫入驗證失敗：stocks 為空"
            )

        temp_file.replace(
            OUTPUT_FILE
        )

    finally:

        if temp_file.exists():

            try:
                temp_file.unlink()
            except Exception:
                pass

    log(
        f"✓ 已寫入：{OUTPUT_FILE}"
    )


# ============================================================
# Main
# ============================================================

def main() -> int:

    section(
        "台股 AI 選股系統 "
        f"build_universe.py {VERSION}"
    )

    log(
        "Universe 定位：完整標的宇宙"
    )

    log(
        "選股邏輯：無"
    )

    log(
        "六項核心：無"
    )

    log(
        "RSI / MACD / KD：不計算"
    )

    log(
        "DCA：不計算"
    )

    log(
        "API 探測：無"
    )

    try:

        # ----------------------------------------------------
        # 1. 找來源
        # ----------------------------------------------------

        section(
            "尋找 Universe Source"
        )

        source_file: Optional[
            Path
        ] = None

        for candidate in SOURCE_CANDIDATES:

            if candidate.exists():

                source_file = candidate

                break

        if source_file is None:

            raise RuntimeError(
                "找不到 Universe source。\n"
                "請提供：\n"
                + "\n".join(
                    str(path)
                    for path
                    in SOURCE_CANDIDATES
                )
            )

        log(
            f"來源：{source_file}"
        )

        # ----------------------------------------------------
        # 2. Load
        # ----------------------------------------------------

        data = load_json(
            source_file
        )

        # ----------------------------------------------------
        # 3. Extract
        # ----------------------------------------------------

        records = extract_records(
            data
        )

        if not records:

            raise RuntimeError(
                "Universe source 沒有任何 records"
            )

        # ----------------------------------------------------
        # 4. Build
        # ----------------------------------------------------

        universe = build_universe(
            records
        )

        # ----------------------------------------------------
        # 5. Validate
        # ----------------------------------------------------

        validate_universe(
            universe
        )

        # ----------------------------------------------------
        # 6. Output
        # ----------------------------------------------------

        output = build_output(
            universe,
            source_file,
        )

        # ----------------------------------------------------
        # 7. Final validation
        # ----------------------------------------------------

        if output[
            "universe_count"
        ] != len(
            output["stocks"]
        ):

            raise RuntimeError(
                "最終 universe_count 不一致"
            )

        if output[
            "universe_count"
        ] <= 0:

            raise RuntimeError(
                "最終 Universe 為空"
            )

        # ----------------------------------------------------
        # 8. Save
        # ----------------------------------------------------

        save_output(
            output
        )

        # ----------------------------------------------------
        # 9. Summary
        # ----------------------------------------------------

        section(
            "Universe V10.3 建置完成"
        )

        log(
            f"版本：{VERSION}"
        )

        log(
            f"來源：{source_file.name}"
        )

        log(
            f"Universe："
            f"{output['universe_count']} 檔"
        )

        log(
            f"輸出：{OUTPUT_FILE}"
        )

        log("")

        log(
            "資料鏈："
        )

        log(
            "Universe Source"
            " → "
            "build_universe.py"
            " → "
            "universe.json"
        )

        log("")

        log(
            "✓ Universe 建置成功"
        )

        return 0

    except Exception as exc:

        log("")
        log("=" * 72)
        log(
            f"❌ build_universe.py "
            f"{VERSION} 執行失敗"
        )
        log("=" * 72)
        log(
            f"原因：{exc}"
        )

        return 1


if __name__ == "__main__":
    sys.exit(
        main()
    )