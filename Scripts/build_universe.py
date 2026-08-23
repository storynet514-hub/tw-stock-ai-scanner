#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_universe.py
正式版 UNIVERSE-V10.3

============================================================
定位
============================================================

本程式只負責：

    建立「完整台股標的 Universe」

資料流：

    Universe Source
          ↓
    build_universe.py
          ↓
    Data/universe.json
          ↓
    analysis / UI

============================================================
架構邊界
============================================================

本程式絕對不：

    - 抓股價
    - 計算 RSI
    - 計算 MACD
    - 計算 KD
    - 計算成交量
    - 計算技術指標
    - 計算 DCA
    - 執行短線選股
    - 執行六項核心條件
    - 計算主力籌碼
    - API 探測
    - 建立今日精選
    - 建立 Top 10

Universe 的工作只有：

    「完整標的清單建立與正規化」

============================================================
Universe Source 優先順序
============================================================

1. Data/raw_universe.json
2. Data/universe_source.json
3. Data/stock_universe.json
4. Data/universe.json

第 4 項非常重要：

    如果前三個原始來源不存在，
    但 repository 已經存在一份有效的
    Data/universe.json，

    則使用現有 universe.json 作為 bootstrap source。

這是為了避免：

    GitHub Actions 第一次執行時
    因為沒有 raw source
    導致整個 workflow 直接失敗。

============================================================
輸出
============================================================

Data/universe.json

schema：

{
    "schema_version": "V10.3",
    "generated_at": "...",

    "source": {...},

    "universe_count": 2143,

    "stock_count": 1993,

    "etf_count": 150,

    "market_count": {
        "TWSE": ...,
        "TPEX": ...,
        "EMERGING": ...
    },

    "source_count": {...},

    "stocks": {
        "2337": {
            "symbol": "2337",
            "full_symbol": "2337.TW",
            "name": "...",
            "market": "TWSE",
            "type": "Stock",
            "instrument_type": "stock",
            "source": "..."
        }
    }
}

============================================================
重要原則
============================================================

Universe 不篩選。

Universe 不選股。

Universe 不縮減。

Universe 只是標的宇宙。

因此：

    2143 檔 Universe
        ≠
    2143 檔今日精選

後續 analysis.json 才負責分析與選股。

============================================================
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


# ============================================================
# 基本設定
# ============================================================

VERSION = "UNIVERSE-V10.3"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

OUTPUT_FILE = DATA_DIR / "universe.json"

SOURCE_CANDIDATES = [
    DATA_DIR / "raw_universe.json",
    DATA_DIR / "universe_source.json",
    DATA_DIR / "stock_universe.json",
    DATA_DIR / "universe.json",
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
            f"找不到檔案：{path}"
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

def safe_int(value: Any) -> Optional[int]:

    if value is None:
        return None

    if isinstance(value, bool):
        return None

    try:

        number = int(
            float(
                str(value)
                .replace(",", "")
                .strip()
            )
        )

        return number

    except Exception:
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

    for suffix in (
        ".TW",
        ".TWO",
    ):

        if text.endswith(suffix):

            text = text[
                :-len(suffix)
            ]

            break

    return text.strip()


# ============================================================
# Full Symbol
# ============================================================

def build_full_symbol(
    symbol: str,
    market: str,
    original: Any = None,
) -> str:

    if original:

        text = str(
            original
        ).strip().upper()

        if text.endswith(
            ".TW"
        ) or text.endswith(
            ".TWO"
        ):

            return text

    market_upper = str(
        market or ""
    ).upper()

    if market_upper in {
        "TPEX",
        "TWO",
        "OTC",
    }:

        return f"{symbol}.TWO"

    return f"{symbol}.TW"


# ============================================================
# Text
# ============================================================

def first_value(
    record: Dict[str, Any],
    keys: List[str],
) -> Any:

    for key in keys:

        if (
            key in record
            and record[key] is not None
            and str(record[key]).strip() != ""
        ):

            return record[key]

    return None


def clean_text(value: Any) -> str:

    if value is None:
        return ""

    return str(
        value
    ).strip()


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
            "product_type",
            "type",
            "category",
        ],
    )

    if value is None:
        return "stock"

    text = clean_text(
        value
    ).lower()

    if any(
        token in text
        for token in (
            "etf",
            "基金",
            "index fund",
            "index_fund",
        )
    ):

        return "etf"

    if any(
        token in text
        for token in (
            "bond",
            "債券",
        )
    ):

        return "bond"

    return "stock"


# ============================================================
# Type Label
# ============================================================

def normalize_type_label(
    instrument_type: str,
) -> str:

    if instrument_type == "etf":
        return "ETF"

    if instrument_type == "bond":
        return "Bond"

    return "Stock"


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
            "market_code",
        ],
    )

    if value is None:
        return ""

    text = clean_text(
        value
    ).upper()

    mapping = {

        "TWSE": "TWSE",
        "TSE": "TWSE",
        "上市": "TWSE",

        "TPEX": "TPEX",
        "TWO": "TPEX",
        "OTC": "TPEX",
        "上櫃": "TPEX",

        "EMERGING": "EMERGING",
        "興櫃": "EMERGING",
    }

    return mapping.get(
        text,
        text,
    )


# ============================================================
# Name
# ============================================================

def normalize_name(
    record: Dict[str, Any],
    symbol: str,
) -> str:

    value = first_value(
        record,
        [
            "name",
            "stock_name",
            "company_name",
            "security_name",
            "名稱",
        ],
    )

    if value is None:
        return symbol

    text = clean_text(
        value
    )

    if not text:
        return symbol

    return text


# ============================================================
# Source
# ============================================================

def normalize_source(
    record: Dict[str, Any],
    fallback: str,
) -> str:

    value = first_value(
        record,
        [
            "source",
            "data_source",
            "origin",
        ],
    )

    if value is None:
        return fallback

    text = clean_text(
        value
    )

    return text or fallback


# ============================================================
# Record Normalization
# ============================================================

def normalize_record(
    raw_symbol: Any,
    raw_record: Any,
    fallback_source: str,
) -> Optional[Dict[str, Any]]:

    if not isinstance(
        raw_record,
        dict,
    ):

        return None

    # --------------------------------------------------------
    # Symbol
    # --------------------------------------------------------

    symbol = normalize_symbol(
        first_value(
            raw_record,
            [
                "symbol",
                "code",
                "ticker",
                "stock_id",
            ],
        )
        or raw_symbol
    )

    if not symbol:
        return None

    # --------------------------------------------------------
    # Market
    # --------------------------------------------------------

    market = normalize_market(
        raw_record
    )

    # --------------------------------------------------------
    # Instrument Type
    # --------------------------------------------------------

    instrument_type = (
        normalize_instrument_type(
            raw_record
        )
    )

    type_label = normalize_type_label(
        instrument_type
    )

    # --------------------------------------------------------
    # Full Symbol
    # --------------------------------------------------------

    original_full_symbol = first_value(
        raw_record,
        [
            "full_symbol",
            "yf_symbol",
            "yahoo_symbol",
        ],
    )

    full_symbol = build_full_symbol(
        symbol,
        market,
        original_full_symbol,
    )

    # --------------------------------------------------------
    # Name
    # --------------------------------------------------------

    name = normalize_name(
        raw_record,
        symbol,
    )

    # --------------------------------------------------------
    # Source
    # --------------------------------------------------------

    source = normalize_source(
        raw_record,
        fallback_source,
    )

    return {

        "symbol":
            symbol,

        "full_symbol":
            full_symbol,

        "name":
            name,

        "market":
            market,

        "type":
            type_label,

        "instrument_type":
            instrument_type,

        "source":
            source,
    }


# ============================================================
# Locate Stocks Object
# ============================================================

def extract_stock_mapping(
    data: Any,
) -> Dict[str, Any]:

    if not isinstance(
        data,
        dict,
    ):

        raise RuntimeError(
            "Universe source 根節點必須是 object"
        )

    # --------------------------------------------------------
    # 標準格式
    # --------------------------------------------------------

    stocks = data.get(
        "stocks"
    )

    if isinstance(
        stocks,
        dict,
    ):

        return stocks

    # --------------------------------------------------------
    # 常見 alternatives
    # --------------------------------------------------------

    for key in (
        "symbols",
        "securities",
        "universe",
        "data",
        "items",
    ):

        value = data.get(
            key
        )

        if isinstance(
            value,
            dict,
        ):

            return value

        if isinstance(
            value,
            list,
        ):

            result = {}

            for item in value:

                if not isinstance(
                    item,
                    dict,
                ):
                    continue

                symbol = first_value(
                    item,
                    [
                        "symbol",
                        "code",
                        "ticker",
                        "stock_id",
                    ],
                )

                if symbol:

                    result[
                        str(symbol)
                    ] = item

            if result:
                return result

    # --------------------------------------------------------
    # 如果 root 本身就是：
    #
    # {
    #   "2337": {...},
    #   "2451": {...}
    # }
    # --------------------------------------------------------

    probable_records = {}

    for key, value in data.items():

        if not isinstance(
            value,
            dict,
        ):
            continue

        symbol = normalize_symbol(
            first_value(
                value,
                [
                    "symbol",
                    "code",
                    "ticker",
                ],
            )
            or key
        )

        if symbol:
            probable_records[
                symbol
            ] = value

    if probable_records:
        return probable_records

    raise RuntimeError(
        "找不到 Universe 股票資料集合"
    )


# ============================================================
# Find Source
# ============================================================

def find_universe_source() -> Path:

    section(
        "尋找 Universe Source"
    )

    for path in SOURCE_CANDIDATES:

        if not path.exists():
            continue

        try:

            data = load_json(
                path
            )

            mapping = extract_stock_mapping(
                data
            )

            if not mapping:

                log(
                    f"跳過空來源：{path}"
                )

                continue

            if path == OUTPUT_FILE:

                log(
                    "✓ 使用現有 Data/universe.json "
                    "作為 bootstrap source"
                )

            else:

                log(
                    f"✓ 找到 Universe source：{path}"
                )

            log(
                f"來源資料筆數：{len(mapping)}"
            )

            return path

        except Exception as exc:

            log(
                f"⚠ 無法使用：{path}"
            )

            log(
                f"  原因：{exc}"
            )

    raise RuntimeError(
        "找不到有效 Universe source。"
        "目前也不存在可用的 Data/universe.json。"
    )


# ============================================================
# Build Universe
# ============================================================

def build_universe(
    source_path: Path,
) -> Dict[str, Any]:

    section(
        "建立完整 Universe"
    )

    source_data = load_json(
        source_path
    )

    raw_stocks = extract_stock_mapping(
        source_data
    )

    if not raw_stocks:

        raise RuntimeError(
            "Universe source 為空"
        )

    source_name = (
        source_data.get(
            "source"
        )
        if isinstance(
            source_data,
            dict,
        )
        else None
    )

    if not isinstance(
        source_name,
        dict,
    ):

        source_name = {}

    source_primary = source_name.get(
        "primary"
    )

    source_secondary = source_name.get(
        "secondary"
    )

    source_fallback = source_name.get(
        "fallback"
    )

    if not isinstance(
        source_primary,
        list,
    ):
        source_primary = []

    if not isinstance(
        source_secondary,
        list,
    ):
        source_secondary = []

    if not isinstance(
        source_fallback,
        list,
    ):
        source_fallback = []

    # --------------------------------------------------------
    # Bootstrap 標記
    # --------------------------------------------------------

    if source_path == OUTPUT_FILE:

        fallback_source = (
            "EXISTING_UNIVERSE_BOOTSTRAP"
        )

    else:

        fallback_source = (
            source_path.stem.upper()
        )

    normalized: Dict[
        str,
        Dict[str, Any]
    ] = {}

    duplicates = 0
    invalid = 0

    # --------------------------------------------------------
    # 正規化
    # --------------------------------------------------------

    for raw_symbol, raw_record in raw_stocks.items():

        record = normalize_record(
            raw_symbol,
            raw_record,
            fallback_source,
        )

        if record is None:

            invalid += 1

            continue

        symbol = record[
            "symbol"
        ]

        if symbol in normalized:

            duplicates += 1

            continue

        normalized[
            symbol
        ] = record

    if not normalized:

        raise RuntimeError(
            "Universe 正規化後沒有任何有效標的"
        )

    # --------------------------------------------------------
    # 排序
    # --------------------------------------------------------

    normalized = dict(
        sorted(
            normalized.items(),
            key=lambda item:
                item[0],
        )
    )

    # --------------------------------------------------------
    # Count
    # --------------------------------------------------------

    universe_count = len(
        normalized
    )

    stock_count = sum(
        1
        for record in normalized.values()
        if record.get(
            "instrument_type"
        ) == "stock"
    )

    etf_count = sum(
        1
        for record in normalized.values()
        if record.get(
            "instrument_type"
        ) == "etf"
    )

    bond_count = sum(
        1
        for record in normalized.values()
        if record.get(
            "instrument_type"
        ) == "bond"
    )

    # --------------------------------------------------------
    # Market count
    # --------------------------------------------------------

    market_count = {

        "TWSE":
            0,

        "TPEX":
            0,

        "EMERGING":
            0,
    }

    for record in normalized.values():

        market = record.get(
            "market"
        )

        if market in market_count:

            market_count[
                market
            ] += 1

    # --------------------------------------------------------
    # Source count
    # --------------------------------------------------------

    source_count: Dict[
        str,
        int
    ] = {}

    for record in normalized.values():

        source = record.get(
            "source"
        )

        if not source:
            source = "UNKNOWN"

        source_count[
            source
        ] = (
            source_count.get(
                source,
                0
            )
            + 1
        )

    # --------------------------------------------------------
    # Output
    # --------------------------------------------------------

    output = {

        "schema_version":
            VERSION,

        "generated_at":
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "source":
            {

                "primary":
                    source_primary,

                "secondary":
                    source_secondary,

                "fallback":
                    source_fallback,

                "actual":
                    str(
                        source_path.relative_to(
                            BASE_DIR
                        )
                    ),

                "description":
                    (
                        "完整台股 Universe。"
                        "本程式只建立標的宇宙，"
                        "不執行任何選股或技術分析。"
                    ),
            },

        "universe_count":
            universe_count,

        "stock_count":
            stock_count,

        "etf_count":
            etf_count,

        "bond_count":
            bond_count,

        "market_count":
            market_count,

        "source_count":
            source_count,

        "stocks":
            normalized,
    }

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    log(
        f"來源：{source_path}"
    )

    log(
        f"有效標的：{universe_count}"
    )

    log(
        f"普通股票：{stock_count}"
    )

    log(
        f"ETF：{etf_count}"
    )

    log(
        f"債券：{bond_count}"
    )

    log(
        f"無效資料：{invalid}"
    )

    log(
        f"重複 Symbol：{duplicates}"
    )

    log(
        f"TWSE：{market_count['TWSE']}"
    )

    log(
        f"TPEX：{market_count['TPEX']}"
    )

    log(
        f"EMERGING：{market_count['EMERGING']}"
    )

    return output


# ============================================================
# Validation
# ============================================================

def validate_universe(
    output: Dict[str, Any],
) -> None:

    section(
        "Universe V10.3 Validation"
    )

    if not isinstance(
        output,
        dict,
    ):

        raise RuntimeError(
            "Universe root 必須是 object"
        )

    # --------------------------------------------------------
    # Schema
    # --------------------------------------------------------

    if output.get(
        "schema_version"
    ) != VERSION:

        raise RuntimeError(
            "schema_version 錯誤："
            f"{output.get('schema_version')}"
        )

    # --------------------------------------------------------
    # Stocks
    # --------------------------------------------------------

    stocks = output.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            "stocks 必須是 object"
        )

    if not stocks:

        raise RuntimeError(
            "stocks 不得為空"
        )

    # --------------------------------------------------------
    # Count
    # --------------------------------------------------------

    universe_count = output.get(
        "universe_count"
    )

    if universe_count != len(
        stocks
    ):

        raise RuntimeError(
            "universe_count 不一致："
            f"header={universe_count}, "
            f"actual={len(stocks)}"
        )

    # --------------------------------------------------------
    # Required record fields
    # --------------------------------------------------------

    required_fields = {

        "symbol",
        "full_symbol",
        "name",
        "market",
        "type",
        "instrument_type",
        "source",
    }

    for symbol, record in stocks.items():

        if not isinstance(
            record,
            dict,
        ):

            raise RuntimeError(
                f"{symbol} record 必須是 object"
            )

        missing = (
            required_fields
            - set(record.keys())
        )

        if missing:

            raise RuntimeError(
                f"{symbol} 缺少欄位："
                + ", ".join(
                    sorted(missing)
                )
            )

        if record.get(
            "symbol"
        ) != symbol:

            raise RuntimeError(
                f"{symbol} symbol 欄位不一致"
            )

        instrument_type = record.get(
            "instrument_type"
        )

        if instrument_type not in {
            "stock",
            "etf",
            "bond",
        }:

            raise RuntimeError(
                f"{symbol} instrument_type 無效："
                f"{instrument_type}"
            )

    # --------------------------------------------------------
    # Count validation
    # --------------------------------------------------------

    actual_stock_count = sum(
        1
        for record in stocks.values()
        if record.get(
            "instrument_type"
        ) == "stock"
    )

    actual_etf_count = sum(
        1
        for record in stocks.values()
        if record.get(
            "instrument_type"
        ) == "etf"
    )

    actual_bond_count = sum(
        1
        for record in stocks.values()
        if record.get(
            "instrument_type"
        ) == "bond"
    )

    if output.get(
        "stock_count"
    ) != actual_stock_count:

        raise RuntimeError(
            "stock_count 不一致"
        )

    if output.get(
        "etf_count"
    ) != actual_etf_count:

        raise RuntimeError(
            "etf_count 不一致"
        )

    if output.get(
        "bond_count"
    ) != actual_bond_count:

        raise RuntimeError(
            "bond_count 不一致"
        )

    # --------------------------------------------------------
    # Market validation
    # --------------------------------------------------------

    market_count = output.get(
        "market_count"
    )

    if not isinstance(
        market_count,
        dict,
    ):

        raise RuntimeError(
            "market_count 必須是 object"
        )

    actual_market_count = {

        "TWSE": 0,
        "TPEX": 0,
        "EMERGING": 0,
    }

    for record in stocks.values():

        market = record.get(
            "market"
        )

        if market in actual_market_count:

            actual_market_count[
                market
            ] += 1

    for market in actual_market_count:

        if market_count.get(
            market,
            0,
        ) != actual_market_count[
            market
        ]:

            raise RuntimeError(
                f"{market} 數量不一致："
                f"header={market_count.get(market)}, "
                f"actual={actual_market_count[market]}"
            )

    # --------------------------------------------------------
    # Source count validation
    # --------------------------------------------------------

    source_count = output.get(
        "source_count"
    )

    if not isinstance(
        source_count,
        dict,
    ):

        raise RuntimeError(
            "source_count 必須是 object"
        )

    actual_source_count: Dict[
        str,
        int
    ] = {}

    for record in stocks.values():

        source = record.get(
            "source"
        ) or "UNKNOWN"

        actual_source_count[
            source
        ] = (
            actual_source_count.get(
                source,
                0
            )
            + 1
        )

    if source_count != actual_source_count:

        raise RuntimeError(
            "source_count 與實際資料不一致"
        )

    # --------------------------------------------------------
    # Critical universe sanity check
    # --------------------------------------------------------

    if len(stocks) < 1000:

        raise RuntimeError(
            "❌ Universe 異常縮小："
            f"{len(stocks)} 檔"
        )

    log(
        "✓ schema_version：PASS"
    )

    log(
        f"✓ Universe：{len(stocks)} 檔"
    )

    log(
        f"✓ Stock：{actual_stock_count} 檔"
    )

    log(
        f"✓ ETF：{actual_etf_count} 檔"
    )

    log(
        f"✓ Bond：{actual_bond_count} 檔"
    )

    log(
        "✓ Market count：PASS"
    )

    log(
        "✓ Source count：PASS"
    )

    log(
        "✓ Universe 完整性：PASS"
    )


# ============================================================
# Save
# ============================================================

def save_universe(
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
        # Write verification
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
                "寫入後 JSON root 不是 object"
            )

        if not verify.get(
            "stocks"
        ):

            raise RuntimeError(
                "❌ 拒絕寫入空 Universe"
            )

        if verify.get(
            "universe_count"
        ) != len(
            verify["stocks"]
        ):

            raise RuntimeError(
                "❌ 寫入後 Universe count 不一致"
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
# Final Summary
# ============================================================

def print_summary(
    output: Dict[str, Any],
) -> None:

    section(
        "Universe 建置完成"
    )

    log(
        f"版本：{VERSION}"
    )

    log(
        f"Universe："
        f"{output.get('universe_count')}"
    )

    log(
        f"普通股票："
        f"{output.get('stock_count')}"
    )

    log(
        f"ETF："
        f"{output.get('etf_count')}"
    )

    log(
        f"債券："
        f"{output.get('bond_count')}"
    )

    market_count = output.get(
        "market_count",
        {},
    )

    log(
        f"TWSE："
        f"{market_count.get('TWSE', 0)}"
    )

    log(
        f"TPEX："
        f"{market_count.get('TPEX', 0)}"
    )

    log(
        f"EMERGING："
        f"{market_count.get('EMERGING', 0)}"
    )

    log("")

    log(
        "架構："
    )

    log(
        "Universe"
        " → "
        "analysis"
        " → "
        "UI"
    )

    log("")

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

    log("")

    log(
        "✓ build_universe.py "
        "UNIVERSE-V10.3 完成"
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
        # 1. Locate source
        # ----------------------------------------------------

        source_path = (
            find_universe_source()
        )

        # ----------------------------------------------------
        # 2. Build
        # ----------------------------------------------------

        output = build_universe(
            source_path
        )

        # ----------------------------------------------------
        # 3. Validate
        # ----------------------------------------------------

        validate_universe(
            output
        )

        # ----------------------------------------------------
        # 4. Save
        # ----------------------------------------------------

        save_universe(
            output
        )

        # ----------------------------------------------------
        # 5. Final summary
        # ----------------------------------------------------

        print_summary(
            output
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