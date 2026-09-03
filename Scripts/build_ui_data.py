#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_ui_data.py

UI-DATA-2.1

資料來源
------------------------------------------------------------

Data/universe.json
        +
Data/analysis.json
        +
Data/market.json
        ↓
Data/ui_data.json
        ↓
index.html


責任邊界
------------------------------------------------------------

本程式：

    ✓ 只做資料轉換
    ✓ 不呼叫 API
    ✓ 不抓股價
    ✓ 不計算 RSI
    ✓ 不計算 MACD
    ✓ 不計算 KD
    ✓ 不計算成交量
    ✓ 不計算籌碼
    ✓ 不建立新的選股條件
    ✓ 不修改 analysis.json
    ✓ 不修改 universe.json
    ✓ 不修改 market.json


市場資料唯一來源
------------------------------------------------------------

Data/market.json

Frontend 不直接讀取：

    analysis.json
    prices/
    chip.json
    universe.json

Frontend 最終只讀：

    Data/ui_data.json
"""

from __future__ import annotations

import json
import math
import os
import tempfile

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# PATH
# ============================================================

ROOT = Path(
    __file__
).resolve().parents[1]

DATA_DIR = ROOT / "Data"

ANALYSIS_FILE = (
    DATA_DIR / "analysis.json"
)

UNIVERSE_FILE = (
    DATA_DIR / "universe.json"
)

MARKET_FILE = (
    DATA_DIR / "market.json"
)

OUTPUT_FILE = (
    DATA_DIR / "ui_data.json"
)


VERSION = "UI-DATA-2.1"


# ============================================================
# LOG
# ============================================================

def log(
    message: str = "",
) -> None:

    print(
        message,
        flush=True,
    )


# ============================================================
# JSON
# ============================================================

def load_json(
    path: Path,
) -> Any:

    if not path.exists():

        raise RuntimeError(
            f"找不到檔案：{path}"
        )

    try:

        return json.loads(
            path.read_text(
                encoding="utf-8-sig"
            )
        )

    except Exception as exc:

        raise RuntimeError(
            f"JSON 讀取失敗："
            f"{path}: {exc}"
        ) from exc


# ============================================================
# ATOMIC WRITE
# ============================================================

def atomic_write_json(
    path: Path,
    data: Any,
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, temp_name = (
        tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
    )

    temp_path = Path(
        temp_name
    )

    try:

        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )

            file.write("\n")

            file.flush()

            os.fsync(
                file.fileno()
            )

        os.replace(
            temp_path,
            path,
        )

    finally:

        temp_path.unlink(
            missing_ok=True
        )


# ============================================================
# NUMBER
# ============================================================

def number(
    value: Any,
) -> Optional[float]:

    if value is None:
        return None

    if isinstance(
        value,
        bool,
    ):

        return None

    try:

        result = float(
            str(value)
            .replace(",", "")
            .replace("%", "")
            .strip()
        )

        if not math.isfinite(
            result
        ):

            return None

        return result

    except Exception:

        return None


def rounded(
    value: Any,
    digits: int = 2,
) -> Optional[float]:

    value = number(
        value
    )

    if value is None:
        return None

    return round(
        value,
        digits,
    )


# ============================================================
# SYMBOL
# ============================================================

def normalize_symbol(
    value: Any,
) -> str:

    text = str(
        value or ""
    ).strip().upper()

    for suffix in (
        ".TW",
        ".TWO",
        ".TSE",
        ".OTC",
    ):

        if text.endswith(
            suffix
        ):

            text = text[
                :-len(suffix)
            ]

            break

    return text


# ============================================================
# GENERIC
# ============================================================

def first_value(
    record: Any,
    keys: List[str],
) -> Any:

    if not isinstance(
        record,
        dict,
    ):

        return None

    for key in keys:

        value = record.get(
            key
        )

        if value is not None:

            return value

    return None


def as_dict(
    value: Any,
) -> Dict[str, Any]:

    if isinstance(
        value,
        dict,
    ):

        return value

    return {}


# ============================================================
# UNIVERSE
# ============================================================

def load_universe() -> Dict[
    str,
    Dict[str, Any],
]:

    data = load_json(
        UNIVERSE_FILE
    )

    if not isinstance(
        data,
        dict,
    ):

        raise RuntimeError(
            "universe.json 根節點 "
            "必須是 object"
        )

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            "universe.json stocks "
            "必須是 object"
        )

    output = {}

    for raw_symbol, item in (
        stocks.items()
    ):

        if not isinstance(
            item,
            dict,
        ):

            continue

        symbol = normalize_symbol(
            raw_symbol
        )

        if not symbol:

            continue

        record = dict(
            item
        )

        record[
            "symbol"
        ] = symbol

        output[
            symbol
        ] = record

    if not output:

        raise RuntimeError(
            "universe.json 沒有有效標的"
        )

    return output


# ============================================================
# ANALYSIS
# ============================================================

def load_analysis() -> Dict[
    str,
    Any,
]:

    data = load_json(
        ANALYSIS_FILE
    )

    if not isinstance(
        data,
        dict,
    ):

        raise RuntimeError(
            "analysis.json 根節點 "
            "必須是 object"
        )

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            "analysis.json stocks "
            "必須是 object"
        )

    output = {}

    for raw_symbol, item in (
        stocks.items()
    ):

        if not isinstance(
            item,
            dict,
        ):

            raise RuntimeError(
                f"analysis.json "
                f"stocks[{raw_symbol}] "
                "必須是 object"
            )

        symbol = normalize_symbol(
            raw_symbol
        )

        if not symbol:

            raise RuntimeError(
                "analysis.json 出現 "
                "無效 symbol"
            )

        if symbol in output:

            raise RuntimeError(
                "analysis.json "
                "normalize 後 "
                f"重複 symbol：{symbol}"
            )

        record = dict(
            item
        )

        record[
            "symbol"
        ] = symbol

        output[
            symbol
        ] = record

    if not output:

        raise RuntimeError(
            "analysis.json "
            "沒有有效 stocks"
        )

    return {
        "root": data,
        "stocks": output,
    }


# ============================================================
# MARKET
# ============================================================

def load_market() -> Dict[
    str,
    Any,
]:

    data = load_json(
        MARKET_FILE
    )

    if not isinstance(
        data,
        dict,
    ):

        raise RuntimeError(
            "market.json 根節點 "
            "必須是 object"
        )

    if data.get(
        "schema_version"
    ) not in {
        "market-v2.1",
    }:

        raise RuntimeError(
            "market.json schema_version "
            "不是 market-v2.1"
        )

    required = {
        "market_status",
        "latest_trading_date",
        "index",
        "sentiment",
        "conditions",
    }

    missing = (
        required
        - set(data)
    )

    if missing:

        raise RuntimeError(
            "market.json 缺少欄位："
            f"{sorted(missing)}"
        )

    conditions = data[
        "conditions"
    ]

    if not isinstance(
        conditions,
        list,
    ):

        raise RuntimeError(
            "market.json conditions "
            "不是 list"
        )

    if len(
        conditions
    ) != 10:

        raise RuntimeError(
            "market.json 必須有 "
            "10 項市場條件"
        )

    return data


# ============================================================
# NAME
# ============================================================

def get_name(
    symbol: str,
    analysis: Dict[str, Any],
    universe: Dict[str, Any],
) -> str:

    value = first_value(
        analysis,
        [
            "name",
            "stock_name",
            "security_name",
            "company_name",
            "名稱",
            "股票名稱",
        ],
    )

    if value:

        return str(
            value
        ).strip()

    value = first_value(
        universe,
        [
            "name",
            "stock_name",
            "security_name",
            "company_name",
            "名稱",
            "股票名稱",
        ],
    )

    if value:

        return str(
            value
        ).strip()

    return symbol


# ============================================================
# MARKET NAME
# ============================================================

def get_market(
    analysis: Dict[str, Any],
    universe: Dict[str, Any],
) -> str:

    value = first_value(
        analysis,
        [
            "market",
            "exchange",
            "market_type",
        ],
    )

    if value is None:

        value = first_value(
            universe,
            [
                "market",
                "exchange",
                "market_type",
            ],
        )

    if value is None:

        return ""

    value = str(
        value
    ).strip().upper()

    mapping = {
        "TSE": "TWSE",
        "TWSE": "TWSE",
        "OTC": "TPEX",
        "TPEX": "TPEX",
    }

    return mapping.get(
        value,
        value,
    )


# ============================================================
# INSTRUMENT TYPE
# ============================================================

def get_instrument_type(
    analysis: Dict[str, Any],
    universe: Dict[str, Any],
) -> str:

    value = first_value(
        universe,
        [
            "instrument_type",
            "security_type",
            "type",
            "category",
            "product_type",
        ],
    )

    if value is None:

        value = first_value(
            analysis,
            [
                "instrument_type",
                "security_type",
                "type",
                "category",
                "product_type",
            ],
        )

    text = str(
        value or "stock"
    ).strip().lower()

    if (
        "bond" in text
        or "債券" in text
    ):

        return "bond"

    if (
        "etf" in text
        or "基金" in text
    ):

        return "etf"

    if (
        "etn" in text
        or "指數投資證券"
        in text
    ):

        return "etn"

    return "stock"


# ============================================================
# PRICE
# ============================================================

def get_price(
    record: Dict[str, Any],
) -> Optional[float]:

    metrics = as_dict(
        record.get(
            "metrics"
        )
    )

    value = first_value(
        metrics,
        [
            "price",
            "close",
            "latest_price",
            "last_price",
        ],
    )

    if value is None:

        value = first_value(
            record,
            [
                "price",
                "close",
                "latest_price",
                "last_price",
            ],
        )

    return rounded(
        value
    )


# ============================================================
# CHANGE
# ============================================================

def get_change(
    record: Dict[str, Any],
) -> Tuple[
    Optional[float],
    Optional[float],
]:

    metrics = as_dict(
        record.get(
            "metrics"
        )
    )

    change = first_value(
        metrics,
        [
            "change",
            "price_change",
        ],
    )

    if change is None:

        change = first_value(
            record,
            [
                "change",
                "price_change",
            ],
        )

    change_pct = first_value(
        metrics,
        [
            "change_pct",
            "change_percent",
            "change1_pct",
        ],
    )

    if change_pct is None:

        change_pct = first_value(
            record,
            [
                "change_pct",
                "change_percent",
                "change1_pct",
            ],
        )

    return (
        rounded(
            change
        ),
        rounded(
            change_pct
        ),
    )


# ============================================================
# STRENGTH
# ============================================================

def get_strength(
    record: Dict[str, Any],
) -> str:

    short_term = as_dict(
        record.get(
            "short_term"
        )
    )

    value = first_value(
        short_term,
        [
            "strength",
            "technical_strength",
        ],
    )

    if isinstance(
        value,
        str,
    ):

        if "強" in value:

            return "強勢"

        if "弱" in value:

            return "弱勢"

    qualified = (
        short_term.get(
            "qualified"
        )
    )

    if qualified is True:

        return "強勢"

    score = number(
        short_term.get(
            "core_score"
        )
    )

    if score is not None:

        if score >= 4:

            return "強勢"

        if score <= 1:

            return "弱勢"

    return "中性"


# ============================================================
# RECOMMENDATION
# ============================================================

def get_recommendation(
    record: Dict[str, Any],
    strength: str,
) -> str:

    short_term = as_dict(
        record.get(
            "short_term"
        )
    )

    value = first_value(
        short_term,
        [
            "recommendation",
            "operation",
        ],
    )

    if isinstance(
        value,
        str,
    ):

        text = value.strip()

        mapping = {
            "買進":
                "偏多，可分批",

            "立即進場":
                "積極關注",

            "等回測":
                "等待拉回",

            "回測":
                "等待拉回",

            "續抱":
                "續抱觀察",

            "觀察":
                "續抱觀察",

            "暫停":
                "暫停操作",

            "減碼":
                "考慮減碼",
        }

        for key, result in (
            mapping.items()
        ):

            if key in text:

                return result

    if strength == "強勢":

        return "積極關注"

    if strength == "弱勢":

        return "暫停操作"

    return "續抱觀察"


# ============================================================
# PUBLIC STOCK
#
# 注意：
#
# 不輸出 RSI
# 不輸出 MACD
# 不輸出 KD
# 不輸出 MA5
# 不輸出 MA20
# 不輸出成交量
# 不輸出平均成交量
# 不輸出 5/5
# 不輸出 numeric strength score
#
# ============================================================

def build_public_stock(
    symbol: str,
    analysis: Dict[str, Any],
    universe: Dict[str, Any],
) -> Dict[str, Any]:

    price = get_price(
        analysis
    )

    change, change_pct = (
        get_change(
            analysis
        )
    )

    strength = get_strength(
        analysis
    )

    recommendation = (
        get_recommendation(
            analysis,
            strength,
        )
    )

    return {

        "symbol":
            symbol,

        "name":
            get_name(
                symbol,
                analysis,
                universe,
            ),

        "market":
            get_market(
                analysis,
                universe,
            ),

        "instrument_type":
            get_instrument_type(
                analysis,
                universe,
            ),

        "price":
            price,

        "change":
            change,

        "change_pct":
            change_pct,

        "strength":
            strength,

        "recommendation":
            recommendation,

        # 僅保留非技術性 backend metadata。
        # 不把 RSI/MACD/KD/MA/volume 放進 UI。
        "backend": {
            "status":
                analysis.get(
                    "status"
                ),

            "latest_date":
                analysis.get(
                    "latest_date"
                )
                or analysis.get(
                    "data_date"
                ),
        },

        "holding": {
            "shares": None,
            "average_cost": None,
            "market_value": None,
            "profit": None,
            "return_pct": None,
        },
    }


# ============================================================
# CANDIDATE SYMBOLS
# ============================================================

def extract_candidate_symbols(
    value: Any,
) -> List[str]:

    if not isinstance(
        value,
        list,
    ):

        return []

    result = []

    for item in value:

        if isinstance(
            item,
            str,
        ):

            symbol = (
                normalize_symbol(
                    item
                )
            )

        elif isinstance(
            item,
            dict,
        ):

            symbol = normalize_symbol(
                item.get(
                    "symbol"
                )
                or item.get(
                    "code"
                )
                or item.get(
                    "ticker"
                )
            )

        else:

            symbol = ""

        if (
            symbol
            and symbol not in result
        ):

            result.append(
                symbol
            )

    return result


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    log("=" * 72)
    log("BUILD UI DATA V2.1")
    log("=" * 72)

    universe = (
        load_universe()
    )

    analysis_root = load_analysis()

    market = load_market()

    analysis_stocks = (
        analysis_root[
            "stocks"
        ]
    )

    # --------------------------------------------------------
    # STOCKS
    # --------------------------------------------------------

    stocks = {}

    for symbol, analysis in (
        analysis_stocks.items()
    ):

        universe_record = (
            universe.get(
                symbol,
                {},
            )
        )

        stocks[
            symbol
        ] = build_public_stock(
            symbol,
            analysis,
            universe_record,
        )

    if not stocks:

        raise RuntimeError(
            "沒有可建立的 UI stocks"
        )

    # --------------------------------------------------------
    # TODAY PICKS
    # --------------------------------------------------------

    today_picks = (
        extract_candidate_symbols(
            analysis_root.get(
                "short_term_candidates"
            )
        )
    )

    today_picks = [
        symbol
        for symbol in today_picks
        if symbol in stocks
    ]

    # --------------------------------------------------------
    # TOP 10
    # --------------------------------------------------------

    def sort_key(
        symbol: str,
    ):

        analysis = (
            analysis_stocks[
                symbol
            ]
        )

        short_term = as_dict(
            analysis.get(
                "short_term"
            )
        )

        score = number(
            short_term.get(
                "core_score"
            )
        )

        strength = (
            stocks[
                symbol
            ][
                "strength"
            ]
        )

        strength_rank = {
            "強勢": 2,
            "中性": 1,
            "弱勢": 0,
        }.get(
            strength,
            0,
        )

        return (
            strength_rank,
            score
            if score is not None
            else -1,
        )

    top10 = sorted(
        stocks.keys(),
        key=sort_key,
        reverse=True,
    )[:10]

    # --------------------------------------------------------
    # ETF / BOND
    # --------------------------------------------------------

    etf_symbols = [
        symbol
        for symbol, item
        in stocks.items()
        if item[
            "instrument_type"
        ] == "etf"
    ]

    bond_symbols = [
        symbol
        for symbol, item
        in stocks.items()
        if item[
            "instrument_type"
        ] == "bond"
    ]

    # --------------------------------------------------------
    # MARKET
    #
    # 唯一來源：market.json
    # --------------------------------------------------------

    ui_market = {

        "name":
            "台股市場",

        "timezone":
            "Asia/Taipei",

        "status":
            market[
                "market_status"
            ],

        "latest_trading_date":
            market[
                "latest_trading_date"
            ],

        "index":
            market[
                "index"
            ],

        "sentiment":
            market[
                "sentiment"
            ],
    }

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

    summary = {

        "today_picks":
            len(today_picks),

        "holdings": {

            "has_holdings":
                False,

            "profit":
                None,
        },

        "index":
            market[
                "index"
            ],

        "sentiment":
            market[
                "sentiment"
            ],
    }

    # --------------------------------------------------------
    # ROOT
    # --------------------------------------------------------

    ui_data = {

        "schema_version":
            VERSION,

        "generated_at":
            datetime.now(
                timezone.utc
            ).astimezone().isoformat(
                timespec="seconds"
            ),

        "status":
            "ok",

        "market":
            ui_market,

        "summary":
            summary,

        "tabs": {

            "today_picks":
                today_picks,

            "top10":
                top10,

            "etf":
                etf_symbols,

            "bond":
                bond_symbols,

            "watchlist":
                [],
        },

        "stocks":
            stocks,
    }

    # --------------------------------------------------------
    # WRITE
    # --------------------------------------------------------

    atomic_write_json(
        OUTPUT_FILE,
        ui_data,
    )

    # --------------------------------------------------------
    # READ BACK
    # --------------------------------------------------------

    read_back = load_json(
        OUTPUT_FILE
    )

    if not isinstance(
        read_back,
        dict,
    ):

        raise RuntimeError(
            "ui_data.json "
            "read-back 不是 object"
        )

    if (
        read_back.get(
            "schema_version"
        )
        != VERSION
    ):

        raise RuntimeError(
            "ui_data schema_version "
            "錯誤"
        )

    if not isinstance(
        read_back.get(
            "market"
        ),
        dict,
    ):

        raise RuntimeError(
            "ui_data.market "
            "不是 object"
        )

    if not isinstance(
        read_back.get(
            "stocks"
        ),
        dict,
    ):

        raise RuntimeError(
            "ui_data.stocks "
            "不是 object"
        )

    if (
        len(
            read_back[
                "stocks"
            ]
        )
        != len(stocks)
    ):

        raise RuntimeError(
            "UI stocks 數量與 "
            "analysis.stocks 不一致"
        )

    if (
        read_back[
            "tabs"
        ][
            "watchlist"
        ]
        != []
    ):

        raise RuntimeError(
            "watchlist 初始值必須為空"
        )

    # --------------------------------------------------------
    # PUBLIC UI CONTRACT CHECK
    # --------------------------------------------------------

    forbidden_fields = {
        "rsi",
        "macd",
        "kd",
        "ma5",
        "ma20",
        "volume",
        "average_volume",
        "technical_score",
        "strength_score",
        "core_score",
        "core_total",
    }

    for symbol, item in (
        read_back[
            "stocks"
        ].items()
    ):

        for field in forbidden_fields:

            if field in item:

                raise RuntimeError(
                    f"{symbol} UI "
                    f"不應暴露 backend "
                    f"technical field："
                    f"{field}"
                )

    log("")
    log(
        f"Universe："
        f"{len(universe)}"
    )

    log(
        f"Analysis stocks："
        f"{len(analysis_stocks)}"
    )

    log(
        f"UI stocks："
        f"{len(stocks)}"
    )

    log(
        f"今日精選："
        f"{len(today_picks)}"
    )

    log(
        f"Top 10："
        f"{len(top10)}"
    )

    log(
        "市場最新交易日："
        f"{market['latest_trading_date']}"
    )

    log(
        "市場風向："
        f"{market['sentiment']['level']}"
    )

    log(
        "✓ ui_data.json "
        "validation PASS"
    )

    return 0


if __name__ == "__main__":

    raise SystemExit(
        main()
    )