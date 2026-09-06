#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_ui_data.py
UI-DATA-2.2

資料流
------------------------------------------------------------
Data/universe.json
Data/analysis.json
Data/market.json
        ↓
Data/ui_data.json
        ↓
index.html

核心契約
------------------------------------------------------------
1. Universe 是商品身份與官方中文名稱唯一權威來源。
2. Universe instrument_type 決定 STOCK / ETF。
3. analysis.json 只提供行情、分析、推薦結果。
4. analysis.json 不得覆蓋 Universe 官方名稱。
5. 普通個股才進今日精選。
6. Top 10 僅從今日精選普通個股中排序。
7. ETF 頁最多 10 檔強勢／可持有 ETF。
8. 債券頁最多 10 檔債券 ETF。
9. Frontend 不直接讀 backend technical fields。
10. 不在 UI builder 計算 RSI/MACD/KD/MA/成交量。
11. atomic write。
12. write 後 read-back validation。
13. Universe / Analysis identity mismatch 直接 FAIL。
14. 強弱標準：
       強勢 = 🔴
       中性 = ⚪
       弱勢 = 🟢
   燈號由 frontend 顯示，backend 只輸出文字。
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# PATH
# ============================================================

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"
ANALYSIS_FILE = DATA_DIR / "analysis.json"
MARKET_FILE = DATA_DIR / "market.json"
OUTPUT_FILE = DATA_DIR / "ui_data.json"

VERSION = "UI-DATA-2.2"


# ============================================================
# CONSTANTS
# ============================================================

ALLOWED_UNIVERSE_TYPES = {"STOCK", "ETF"}
ALLOWED_MARKETS = {"TWSE", "TPEX"}

BUYABLE_RECOMMENDATIONS = {
    "偏多，可分批",
    "積極關注",
    "續抱觀察",
}

PUBLIC_FORBIDDEN_FIELDS = {
    "rsi",
    "macd",
    "kd",
    "ma5",
    "ma20",
    "volume",
    "average_volume",
    "avg_volume",
    "technical_score",
    "strength_score",
    "core_score",
    "core_total",
}


# ============================================================
# LOG
# ============================================================

def log(message: str = "") -> None:
    print(message, flush=True)


def section(title: str) -> None:
    log("")
    log("=" * 76)
    log(title)
    log("=" * 76)


# ============================================================
# JSON
# ============================================================

def load_json(path: Path) -> Any:
    if not path.exists():
        raise RuntimeError(f"找不到檔案：{path}")

    try:
        return json.loads(
            path.read_text(
                encoding="utf-8-sig"
            )
        )
    except Exception as exc:
        raise RuntimeError(
            f"JSON 讀取失敗：{path}: {exc}"
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

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )

    temp_path = Path(temp_name)

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
            os.fsync(file.fileno())

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

def number(value: Any) -> Optional[float]:
    if value is None:
        return None

    if isinstance(value, bool):
        return None

    try:
        result = float(
            str(value)
            .replace(",", "")
            .replace("%", "")
            .strip()
        )

        if not math.isfinite(result):
            return None

        return result

    except Exception:
        return None


def rounded(
    value: Any,
    digits: int = 2,
) -> Optional[float]:

    value = number(value)

    if value is None:
        return None

    return round(
        value,
        digits,
    )


# ============================================================
# TEXT
# ============================================================

def clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value)

    replacements = {
        "\ufeff": " ",
        "\xa0": " ",
        "\u3000": " ",
        "\r": " ",
        "\n": " ",
        "\t": " ",
    }

    for old, new in replacements.items():
        text = text.replace(
            old,
            new,
        )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def normalize_text(value: Any) -> str:
    return (
        clean_text(value)
        .upper()
        .replace(" ", "")
        .replace("\u3000", "")
    )


# ============================================================
# SYMBOL
# ============================================================

def normalize_symbol(value: Any) -> str:
    text = clean_text(value).upper()

    for suffix in (
        ".TW",
        ".TWO",
        ".TSE",
        ".OTC",
        ".TWSE",
        ".TPEX",
    ):
        if text.endswith(suffix):
            text = text[:-len(suffix)]
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
        value = record.get(key)

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

def load_universe() -> Dict[str, Dict[str, Any]]:

    data = load_json(
        UNIVERSE_FILE
    )

    if not isinstance(
        data,
        dict,
    ):
        raise RuntimeError(
            "universe.json 根節點必須是 object"
        )

    stocks = data.get("stocks")

    if not isinstance(
        stocks,
        dict,
    ):
        raise RuntimeError(
            "universe.json stocks 必須是 object"
        )

    output: Dict[str, Dict[str, Any]] = {}

    for raw_symbol, raw_item in stocks.items():

        if not isinstance(
            raw_item,
            dict,
        ):
            continue

        symbol = normalize_symbol(
            raw_symbol
        )

        if not symbol:
            continue

        if symbol in output:
            raise RuntimeError(
                f"Universe normalize 後重複 symbol：{symbol}"
            )

        item = dict(raw_item)
        item["symbol"] = symbol

        output[symbol] = item

    if not output:
        raise RuntimeError(
            "universe.json 沒有有效標的"
        )

    return output


# ============================================================
# ANALYSIS
# ============================================================

def load_analysis() -> Dict[str, Any]:

    data = load_json(
        ANALYSIS_FILE
    )

    if not isinstance(
        data,
        dict,
    ):
        raise RuntimeError(
            "analysis.json 根節點必須是 object"
        )

    stocks = data.get("stocks")

    if not isinstance(
        stocks,
        dict,
    ):
        raise RuntimeError(
            "analysis.json stocks 必須是 object"
        )

    output: Dict[str, Dict[str, Any]] = {}

    for raw_symbol, raw_item in stocks.items():

        if not isinstance(
            raw_item,
            dict,
        ):
            raise RuntimeError(
                f"analysis.json stocks[{raw_symbol}] 必須是 object"
            )

        symbol = normalize_symbol(
            raw_symbol
        )

        if not symbol:
            raise RuntimeError(
                f"analysis.json 出現無效 symbol：{raw_symbol}"
            )

        if symbol in output:
            raise RuntimeError(
                f"analysis.json normalize 後重複 symbol：{symbol}"
            )

        item = dict(raw_item)
        item["symbol"] = symbol

        output[symbol] = item

    if not output:
        raise RuntimeError(
            "analysis.json 沒有有效 stocks"
        )

    return {
        "root": data,
        "stocks": output,
    }


# ============================================================
# MARKET
# ============================================================

def load_market() -> Dict[str, Any]:

    data = load_json(
        MARKET_FILE
    )

    if not isinstance(
        data,
        dict,
    ):
        raise RuntimeError(
            "market.json 根節點必須是 object"
        )

    if data.get(
        "schema_version"
    ) != "market-v2.1":

        raise RuntimeError(
            "market.json schema_version 必須為 market-v2.1"
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

    conditions = data.get(
        "conditions"
    )

    if not isinstance(
        conditions,
        list,
    ):
        raise RuntimeError(
            "market.json conditions 必須是 list"
        )

    if len(conditions) != 10:
        raise RuntimeError(
            "market.json conditions 必須正好 10 項"
        )

    return data


# ============================================================
# OFFICIAL NAME
# ============================================================

def get_official_name(
    symbol: str,
    universe: Dict[str, Any],
) -> str:

    name = first_value(
        universe,
        [
            "name",
            "security_name",
            "stock_name",
            "company_name",
            "名稱",
            "股票名稱",
        ],
    )

    name = clean_text(name)

    if not name:
        raise RuntimeError(
            f"{symbol} Universe 缺少官方名稱"
        )

    return name


# ============================================================
# MARKET
# ============================================================

def get_market(
    universe: Dict[str, Any],
) -> str:

    value = first_value(
        universe,
        [
            "market",
            "exchange",
            "market_type",
        ],
    )

    value = clean_text(
        value
    ).upper()

    mapping = {
        "TWSE": "TWSE",
        "TSE": "TWSE",
        "TPEX": "TPEX",
        "OTC": "TPEX",
    }

    result = mapping.get(
        value
    )

    if result is None:
        raise RuntimeError(
            f"Universe market 無法辨識：{value!r}"
        )

    return result


# ============================================================
# UNIVERSE TYPE
# ============================================================

def get_universe_type(
    symbol: str,
    universe: Dict[str, Any],
) -> str:
    """
    Universe 商品身份唯一來源。

    重要：
    實際 Universe 的官方分類欄位為 instrument_type。

    type / security_type 僅作相容性 fallback，
    不再把 type 當成主要欄位。

    因此：
        00400A instrument_type=ETF
        type=None

    仍然必須正確判定為 ETF。
    """

    value = universe.get(
        "instrument_type"
    )

    text = normalize_text(
        value
    )

    if text in {
        "STOCK",
        "STOCKS",
    }:
        return "STOCK"

    if text == "ETF":
        return "ETF"

    # 相容舊 Universe 結構
    fallback = first_value(
        universe,
        [
            "type",
            "security_type",
        ],
    )

    fallback_text = normalize_text(
        fallback
    )

    if fallback_text in {
        "STOCK",
        "STOCKS",
    }:
        return "STOCK"

    if fallback_text == "ETF":
        return "ETF"

    raise RuntimeError(
        f"{symbol} Universe instrument_type 無效："
        f"{value!r}; type={fallback!r}"
    )


# ============================================================
# ETF CATEGORY
# ============================================================

def get_category(
    universe: Dict[str, Any],
) -> str:

    value = first_value(
        universe,
        [
            "category",
            "instrument_category",
            "asset_category",
            "instrument_type",
        ],
    )

    text = normalize_text(
        value
    )

    if not text:
        return "EQUITY"

    if (
        "BOND" in text
        or "債" in text
        or "公司債" in text
        or "公債" in text
    ):
        return "BOND"

    if (
        "LEVERAGED" in text
        or "槓桿" in text
    ):
        return "LEVERAGED"

    if (
        "INVERSE" in text
        or "反向" in text
    ):
        return "INVERSE"

    if (
        "ACTIVE" in text
        or "主動" in text
    ):
        return "ACTIVE"

    if (
        "FX" in text
        or "匯率" in text
    ):
        return "FX"

    if (
        "MULTI" in text
        or "多資產" in text
    ):
        return "MULTI_ASSET"

    return "EQUITY"


# ============================================================
# UI INSTRUMENT TYPE
# ============================================================

def get_ui_instrument_type(
    symbol: str,
    universe: Dict[str, Any],
) -> str:

    universe_type = get_universe_type(
        symbol,
        universe,
    )

    if universe_type == "STOCK":
        return "stock"

    category = get_category(
        universe
    )

    if category == "BOND":
        return "bond"

    return "etf"


# ============================================================
# IDENTITY VALIDATION
# ============================================================

def validate_identity(
    symbol: str,
    analysis: Dict[str, Any],
    universe: Dict[str, Any],
) -> None:

    universe_type = get_universe_type(
        symbol,
        universe,
    )

    universe_market = get_market(
        universe
    )

    # --------------------------------------------------------
    # Market
    # --------------------------------------------------------

    analysis_market = first_value(
        analysis,
        [
            "market",
            "exchange",
            "market_type",
        ],
    )

    if analysis_market is not None:

        normalized = clean_text(
            analysis_market
        ).upper()

        market_mapping = {
            "TSE": "TWSE",
            "TWSE": "TWSE",
            "OTC": "TPEX",
            "TPEX": "TPEX",
        }

        normalized = market_mapping.get(
            normalized,
            normalized,
        )

        if normalized and normalized != universe_market:
            raise RuntimeError(
                f"{symbol} market identity mismatch: "
                f"Universe={universe_market}, "
                f"Analysis={normalized}"
            )

    # --------------------------------------------------------
    # Type
    # --------------------------------------------------------

    analysis_type = first_value(
        analysis,
        [
            "type",
            "security_type",
            "universe_type",
        ],
    )

    if analysis_type is not None:

        text = normalize_text(
            analysis_type
        )

        if text == "STOCKS":
            text = "STOCK"

        if text in {
            "STOCK",
            "ETF",
        } and text != universe_type:

            raise RuntimeError(
                f"{symbol} type identity mismatch: "
                f"Universe={universe_type}, "
                f"Analysis={text}"
            )


# ============================================================
# PRICE
# ============================================================

def get_price(
    record: Dict[str, Any],
) -> Optional[float]:

    metrics = as_dict(
        record.get("metrics")
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

    return rounded(value)


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
        record.get("metrics")
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
        rounded(change),
        rounded(change_pct),
    )


# ============================================================
# STRENGTH
# ============================================================

def get_strength(
    record: Dict[str, Any],
) -> str:

    short_term = as_dict(
        record.get("short_term")
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

        text = value.strip()

        if "強" in text:
            return "強勢"

        if "弱" in text:
            return "弱勢"

        if "中" in text:
            return "中性"

    qualified = short_term.get(
        "qualified"
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
        record.get("short_term")
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
            "買進": "偏多，可分批",
            "立即進場": "積極關注",
            "等回測": "等待拉回",
            "回測": "等待拉回",
            "續抱": "續抱觀察",
            "觀察": "續抱觀察",
            "暫停": "暫停操作",
            "減碼": "考慮減碼",
        }

        for key, result in mapping.items():

            if key in text:
                return result

    if strength == "強勢":
        return "積極關注"

    if strength == "弱勢":
        return "暫停操作"

    return "續抱觀察"


# ============================================================
# PUBLIC STOCK
# ============================================================

def build_public_stock(
    symbol: str,
    analysis: Dict[str, Any],
    universe: Dict[str, Any],
) -> Dict[str, Any]:

    validate_identity(
        symbol,
        analysis,
        universe,
    )

    name = get_official_name(
        symbol,
        universe,
    )

    market = get_market(
        universe
    )

    instrument_type = get_ui_instrument_type(
        symbol,
        universe,
    )

    price = get_price(
        analysis
    )

    change, change_pct = get_change(
        analysis
    )

    strength = get_strength(
        analysis
    )

    recommendation = get_recommendation(
        analysis,
        strength,
    )

    return {
        "symbol": symbol,
        "name": name,
        "market": market,
        "instrument_type": instrument_type,
        "price": price,
        "change": change,
        "change_pct": change_pct,
        "strength": strength,
        "recommendation": recommendation,
        "backend": {
            "status": analysis.get("status"),
            "latest_date": (
                analysis.get("latest_date")
                or analysis.get("data_date")
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
# CANDIDATES
# ============================================================

def extract_candidate_symbols(
    value: Any,
) -> List[str]:

    if not isinstance(
        value,
        list,
    ):
        return []

    result: List[str] = []

    for item in value:

        symbol = ""

        if isinstance(
            item,
            str,
        ):
            symbol = normalize_symbol(
                item
            )

        elif isinstance(
            item,
            dict,
        ):
            symbol = normalize_symbol(
                item.get("symbol")
                or item.get("code")
                or item.get("ticker")
            )

        if (
            symbol
            and symbol not in result
        ):
            result.append(symbol)

    return result


# ============================================================
# RANK
# ============================================================

def strength_rank(
    value: str,
) -> int:

    return {
        "強勢": 2,
        "中性": 1,
        "弱勢": 0,
    }.get(
        value,
        0,
    )


def get_core_score(
    analysis: Dict[str, Any],
) -> float:

    short_term = as_dict(
        analysis.get("short_term")
    )

    value = number(
        short_term.get(
            "core_score"
        )
    )

    if value is None:
        return -1.0

    return value


# ============================================================
# FILTERS
# ============================================================

def is_stock(
    symbol: str,
    universe: Dict[str, Any],
) -> bool:

    return (
        get_universe_type(
            symbol,
            universe,
        )
        == "STOCK"
    )


def is_etf(
    symbol: str,
    universe: Dict[str, Any],
) -> bool:

    return (
        get_universe_type(
            symbol,
            universe,
        )
        == "ETF"
    )


def is_bond_etf(
    symbol: str,
    universe: Dict[str, Any],
) -> bool:

    return (
        is_etf(
            symbol,
            universe,
        )
        and
        get_category(
            universe[symbol]
        )
        == "BOND"
    )


def is_eligible_etf(
    item: Dict[str, Any],
) -> bool:

    return (
        item.get("strength") == "強勢"
        or
        item.get("recommendation")
        in BUYABLE_RECOMMENDATIONS
    )


# ============================================================
# SORT
# ============================================================

def stock_rank_key(
    symbol: str,
    stocks: Dict[str, Dict[str, Any]],
    analysis_stocks: Dict[str, Dict[str, Any]],
) -> Tuple[int, float, str]:

    item = stocks[symbol]

    return (
        strength_rank(
            item["strength"]
        ),
        get_core_score(
            analysis_stocks[symbol]
        ),
        symbol,
    )


def etf_rank_key(
    symbol: str,
    stocks: Dict[str, Dict[str, Any]],
    analysis_stocks: Dict[str, Dict[str, Any]],
) -> Tuple[int, int, float, str]:

    item = stocks[symbol]

    recommendation_rank = {
        "偏多，可分批": 3,
        "積極關注": 2,
        "續抱觀察": 1,
    }.get(
        item["recommendation"],
        0,
    )

    return (
        strength_rank(
            item["strength"]
        ),
        recommendation_rank,
        get_core_score(
            analysis_stocks[symbol]
        ),
        symbol,
    )


# ============================================================
# PUBLIC FIELD VALIDATION
# ============================================================

def validate_public_fields(
    item: Dict[str, Any],
) -> None:

    def walk(
        value: Any,
    ) -> None:

        if isinstance(
            value,
            dict,
        ):

            for key, child in value.items():

                if key.lower() in PUBLIC_FORBIDDEN_FIELDS:
                    raise RuntimeError(
                        f"UI public data 禁止暴露 backend technical field：{key}"
                    )

                walk(child)

        elif isinstance(
            value,
            list,
        ):

            for child in value:
                walk(child)

    walk(item)


# ============================================================
# BUILD MARKET UI
# ============================================================

def build_market_ui(
    market: Dict[str, Any],
) -> Dict[str, Any]:

    sentiment = market.get(
        "sentiment"
    )

    if not isinstance(
        sentiment,
        dict,
    ):
        sentiment = {}

    index = market.get(
        "index"
    )

    if not isinstance(
        index,
        dict,
    ):
        index = {}

    return {
        "name": "台股市場",
        "timezone": "Asia/Taipei",
        "status": market.get(
            "market_status"
        ),
        "latest_trading_date": market.get(
            "latest_trading_date"
        ),
        "index": index,
        "sentiment": sentiment,
    }


# ============================================================
# VALIDATE OUTPUT
# ============================================================

def validate_output(
    data: Dict[str, Any],
) -> None:

    required = {
        "schema_version",
        "generated_at",
        "status",
        "market",
        "summary",
        "tabs",
        "stocks",
    }

    missing = (
        required
        - set(data)
    )

    if missing:
        raise RuntimeError(
            f"ui_data.json 缺少欄位：{sorted(missing)}"
        )

    if data.get(
        "schema_version"
    ) != VERSION:

        raise RuntimeError(
            f"ui_data schema_version 錯誤："
            f"{data.get('schema_version')}"
        )

    if data.get(
        "status"
    ) != "ok":

        raise RuntimeError(
            "ui_data status 必須為 ok"
        )

    if not isinstance(
        data.get("stocks"),
        dict,
    ):
        raise RuntimeError(
            "ui_data stocks 必須為 object"
        )

    tabs = data.get(
        "tabs"
    )

    if not isinstance(
        tabs,
        dict,
    ):
        raise RuntimeError(
            "ui_data tabs 必須為 object"
        )

    required_tabs = {
        "today",
        "top10",
        "etf",
        "bond",
        "mylist",
    }

    if set(tabs) != required_tabs:
        raise RuntimeError(
            "ui_data tabs 必須正好包含："
            f"{sorted(required_tabs)}"
        )

    for item in data["stocks"].values():
        validate_public_fields(
            item
        )

        if not item.get(
            "name"
        ):
            raise RuntimeError(
                f"{item.get('symbol')} 缺少中文名稱"
            )


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    section(
        "BUILD UI DATA"
    )

    universe = load_universe()
    analysis_bundle = load_analysis()
    analysis_root = analysis_bundle["root"]
    analysis_stocks = analysis_bundle["stocks"]
    market = load_market()

    log(
        f"Universe：{len(universe)}"
    )

    log(
        f"Analysis：{len(analysis_stocks)}"
    )

    # --------------------------------------------------------
    # Build intersection
    # --------------------------------------------------------

    stocks: Dict[str, Dict[str, Any]] = {}

    missing_analysis = []

    for symbol, universe_item in universe.items():

        analysis_item = analysis_stocks.get(
            symbol
        )

        if analysis_item is None:
            missing_analysis.append(
                symbol
            )
            continue

        stocks[symbol] = build_public_stock(
            symbol,
            analysis_item,
            universe_item,
        )

    if not stocks:
        raise RuntimeError(
            "Universe / Analysis 沒有任何可建立 UI data 的共同標的"
        )

    # --------------------------------------------------------
    # Today Picks
    # --------------------------------------------------------

    candidate_symbols = extract_candidate_symbols(
        analysis_root.get(
            "short_term_candidates"
        )
    )

    # 若 analysis 沒有建立候選清單，
    # 退回使用 qualified=True 的普通個股。
    if not candidate_symbols:

        for symbol, analysis_item in analysis_stocks.items():

            if symbol not in universe:
                continue

            if not is_stock(
                symbol,
                universe[symbol],
            ):
                continue

            short_term = as_dict(
                analysis_item.get(
                    "short_term"
                )
            )

            if short_term.get(
                "qualified"
            ) is True:
                candidate_symbols.append(
                    symbol
                )

    today_picks = []

    for symbol in candidate_symbols:

        if symbol not in stocks:
            continue

        if not is_stock(
            symbol,
            universe[symbol],
        ):
            continue

        today_picks.append(
            symbol
        )

    today_picks = list(
        dict.fromkeys(
            today_picks
        )
    )

    # --------------------------------------------------------
    # Top 10
    # --------------------------------------------------------

    top10 = sorted(
        today_picks,
        key=lambda symbol: stock_rank_key(
            symbol,
            stocks,
            analysis_stocks,
        ),
        reverse=True,
    )[:10]

    # --------------------------------------------------------
    # ETF
    # --------------------------------------------------------

    etf_symbols = []

    for symbol, item in stocks.items():

        if not is_etf(
            symbol,
            universe[symbol],
        ):
            continue

        if is_bond_etf(
            symbol,
            universe,
        ):
            continue

        if not is_eligible_etf(
            item
        ):
            continue

        etf_symbols.append(
            symbol
        )

    etf_symbols = sorted(
        etf_symbols,
        key=lambda symbol: etf_rank_key(
            symbol,
            stocks,
            analysis_stocks,
        ),
        reverse=True,
    )[:10]

    # --------------------------------------------------------
    # Bond ETF
    # --------------------------------------------------------

    bond_symbols = []

    for symbol, item in stocks.items():

        if not is_bond_etf(
            symbol,
            universe,
        ):
            continue

        if not is_eligible_etf(
            item
        ):
            continue

        bond_symbols.append(
            symbol
        )

    bond_symbols = sorted(
        bond_symbols,
        key=lambda symbol: etf_rank_key(
            symbol,
            stocks,
            analysis_stocks,
        ),
        reverse=True,
    )[:10]

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    market_ui = build_market_ui(
        market
    )

    sentiment = market_ui.get(
        "sentiment"
    ) or {}

    sentiment_level = (
        sentiment.get("level")
        or sentiment.get("status")
        or sentiment.get("label")
        or sentiment.get("result")
        or "資料不足"
    )

    ui_data = {
        "schema_version": VERSION,
        "generated_at": (
            __import__("datetime")
            .datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat()
        ),
        "status": "ok",

        "market": market_ui,

        "summary": {
            "today_picks": len(
                today_picks
            ),
            "holdings": False,
            "index": market_ui.get(
                "index"
            ),
            "sentiment": sentiment_level,
        },

        "tabs": {
            "today": today_picks,
            "top10": top10,
            "etf": etf_symbols,
            "bond": bond_symbols,
            "mylist": [],
        },

        "stocks": stocks,
    }

    # --------------------------------------------------------
    # Validate before write
    # --------------------------------------------------------

    validate_output(
        ui_data
    )

    # --------------------------------------------------------
    # Atomic write
    # --------------------------------------------------------

    atomic_write_json(
        OUTPUT_FILE,
        ui_data,
    )

    # --------------------------------------------------------
    # Read-back
    # --------------------------------------------------------

    written = load_json(
        OUTPUT_FILE
    )

    if not isinstance(
        written,
        dict,
    ):
        raise RuntimeError(
            "ui_data.json write 後 read-back 不是 object"
        )

    validate_output(
        written
    )

    # --------------------------------------------------------
    # Identity statistics
    # --------------------------------------------------------

    stock_count = sum(
        1
        for item in stocks.values()
        if item["instrument_type"] == "stock"
    )

    etf_count = sum(
        1
        for item in stocks.values()
        if item["instrument_type"] == "etf"
    )

    bond_count = sum(
        1
        for item in stocks.values()
        if item["instrument_type"] == "bond"
    )

    section(
        "UI DATA RESULT"
    )

    log(
        f"UI stocks：{len(stocks)}"
    )

    log(
        f"普通個股：{stock_count}"
    )

    log(
        f"ETF：{etf_count}"
    )

    log(
        f"債券 ETF：{bond_count}"
    )

    log(
        f"今日精選：{len(today_picks)}"
    )

    log(
        f"Top 10：{len(top10)}"
    )

    log(
        f"ETF Top 10：{len(etf_symbols)}"
    )

    log(
        f"債券 Top 10：{len(bond_symbols)}"
    )

    log(
        f"最新交易日："
        f"{market.get('latest_trading_date')}"
    )

    log(
        f"市場風向：{sentiment_level}"
    )

    if missing_analysis:
        log(
            f"⚠️ Universe 中沒有 Analysis："
            f"{len(missing_analysis)}"
        )

    log("")
    log(
        "✓ BUILD UI DATA PASS"
    )


if __name__ == "__main__":
    main()