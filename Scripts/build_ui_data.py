#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股 AI 選股系統
Scripts/build_ui_data.py
UI-DATA-2.2
資料來源
------------------------------------------------------------
Data/universe.json
Data/analysis.json
Data/market.json
        ↓
Data/ui_data.json
        ↓
index.html
核心責任
------------------------------------------------------------
1. universe.json 是商品身份與官方名稱的唯一權威來源。
2. analysis.json 只提供分析結果、行情與推薦資訊。
3. analysis.json 不得覆蓋 Universe 官方名稱。
4. Universe type=STOCK → UI stock。
5. Universe type=ETF → ETF identity。
6. ETF 的 category=BOND 才進債券頁。
7. Top 10 僅限普通個股。
8. 今日精選僅限普通個股。
9. ETF 頁最多 10 檔強勢/可持有 ETF。
10. 債券頁最多 10 檔債券 ETF。
11. Frontend 不直接讀取 backend technical fields。
12. 不計算 RSI/MACD/KD/MA/成交量/籌碼。
13. atomic write。
14. write 後 read-back validation。
15. 對 Universe / Analysis identity mismatch 直接 FAIL。
"""
from __future__ import annotations
import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
# ============================================================
# PATH
# ============================================================
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "Data"
ANALYSIS_FILE = DATA_DIR / "analysis.json"
UNIVERSE_FILE = DATA_DIR / "universe.json"
MARKET_FILE = DATA_DIR / "market.json"
OUTPUT_FILE = DATA_DIR / "ui_data.json"
VERSION = "UI-DATA-2.2"
# ============================================================
# CONSTANTS
# ============================================================
ALLOWED_UNIVERSE_TYPES = {
    "STOCK",
    "ETF",
}
ALLOWED_MARKETS = {
    "TWSE",
    "TPEX",
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
        text = text.replace(old, new)
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
def as_dict(value: Any) -> Dict[str, Any]:
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
    missing = required - set(data)
    if missing:
        raise RuntimeError(
            "market.json 缺少欄位："
            f"{sorted(missing)}"
        )
    conditions = data.get("conditions")
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
    """
    Universe 是名稱唯一權威。
    絕對不使用 analysis.name 覆蓋官方名稱。
    """
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
    value = clean_text(value).upper()
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
            "Universe market 無法辨識："
            f"{value}"
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
    商品身份只能來自 Universe。
    STOCK = 普通個股
    ETF   = ETF
    不允許 analysis.json 改變商品身份。
    """
    value = first_value(
        universe,
        [
            "type",
            "security_type",
        ],
    )
    text = normalize_text(value)
    if text == "STOCKS":
        return "STOCK"
    if text == "STOCK":
        return "STOCK"
    if text == "ETF":
        return "ETF"
    raise RuntimeError(
        f"{symbol} Universe type 無效：{value!r}"
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
            "instrument_type",
        ],
    )
    text = normalize_text(value)
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
    """
    UI category：
    STOCK
        → stock
    ETF + BOND category
        → bond
    ETF + other category
        → etf
    注意：
    bond 仍然保留其 Universe type=ETF 的身份。
    """
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
# ANALYSIS / UNIVERSE CONSISTENCY
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
    analysis_market = first_value(
        analysis,
        [
            "market",
            "exchange",
            "market_type",
        ],
    )
    if analysis_market is not None:
        normalized_analysis_market = clean_text(
            analysis_market
        ).upper()
        market_mapping = {
            "TSE": "TWSE",
            "TWSE": "TWSE",
            "OTC": "TPEX",
            "TPEX": "TPEX",
        }
        normalized_analysis_market = market_mapping.get(
            normalized_analysis_market,
            normalized_analysis_market,
        )
        if normalized_analysis_market not in {
            "",
            universe_market,
        }:
            raise RuntimeError(
                f"{symbol} market identity mismatch: "
                f"Universe={universe_market}, "
                f"Analysis={normalized_analysis_market}"
            )
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
    universe_type = get_universe_type(
        symbol,
        universe,
    )
    category = get_category(
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
        # 官方中文名稱。
        # analysis.name 不得覆蓋。
        "name": name,
        "market": market,
        # UI 分類。
        "instrument_type": instrument_type,
        # 保留 identity metadata，
        # 不暴露技術指標。
        "universe_type": universe_type,
        "category": category,
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
# STRENGTH RANK
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
# ============================================================
# CORE SCORE
#
# 僅供 backend 排序。
# 絕不輸出至 ui_data stock public object。
# ============================================================
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
# STOCK-ONLY FILTER
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
# ============================================================
# ETF FILTER
# ============================================================
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
# ============================================================
# BOND ETF FILTER
# ============================================================
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
# ============================================================
# ETF RANK
# ============================================================
def etf_rank_key(
    symbol: str,
    stocks: Dict[str, Dict[str, Any]],
    analysis_stocks: Dict[str, Dict[str, Any]],
) -> Tuple[int, int, float]:
    item = stocks[symbol]
    strength = strength_rank(
        item["strength"]
    )
    recommendation = item[
        "recommendation"
    ]
    # 優先順序：
    # 1. 強勢
    # 2. 偏多/可分批
    # 3. 積極關注
    # 4. 續抱觀察
    # 5. 其他
    recommendation_rank = 0
    if (
        "偏多" in recommendation
        or "可分批" in recommendation
    ):
        recommendation_rank = 3
    elif "積極關注" in recommendation:
        recommendation_rank = 2
    elif "續抱" in recommendation:
        recommendation_rank = 1
    score = get_core_score(
        analysis_stocks[symbol]
    )
    return (
        strength,
        recommendation_rank,
        score,
    )
# ============================================================
# MAIN
# ============================================================
def main() -> int:
    section(
        "BUILD UI DATA V2.2"
    )
    # --------------------------------------------------------
    # LOAD
    # --------------------------------------------------------
    universe = load_universe()
    analysis_root = load_analysis()
    market = load_market()
    analysis_stocks = analysis_root[
        "stocks"
    ]
    section(
        "VALIDATE UNIVERSE / ANALYSIS IDENTITY"
    )
    # --------------------------------------------------------
    # BUILD PUBLIC STOCK OBJECTS
    # --------------------------------------------------------
    stocks: Dict[
        str,
        Dict[str, Any]
    ] = {}
    missing_from_universe: List[str] = []
    for symbol, analysis in analysis_stocks.items():
        universe_record = universe.get(
            symbol
        )
        if universe_record is None:
            missing_from_universe.append(
                symbol
            )
            continue
        stocks[symbol] = build_public_stock(
            symbol,
            analysis,
            universe_record,
        )
    if missing_from_universe:
        sample = ", ".join(
            missing_from_universe[:20]
        )
        raise RuntimeError(
            "analysis.json 出現 Universe 不存在的 symbol："
            f"{sample}"
        )
    if not stocks:
        raise RuntimeError(
            "沒有可建立的 UI stocks"
        )
    # --------------------------------------------------------
    # TODAY PICKS
    #
    # 今日精選：
    # analysis.short_term_candidates
    # 但只允許普通 STOCK。
    # --------------------------------------------------------
    raw_today_picks = (
        extract_candidate_symbols(
            analysis_root.get(
                "short_term_candidates"
            )
        )
    )
    today_picks: List[str] = []
    for symbol in raw_today_picks:
        if symbol not in stocks:
            continue
        if not is_stock(
            symbol,
            universe,
        ):
            continue
        if symbol not in today_picks:
            today_picks.append(
                symbol
            )
    # --------------------------------------------------------
    # TOP 10
    #
    # 僅普通個股。
    # 絕對排除 ETF / 債券 ETF。
    # --------------------------------------------------------
    stock_symbols = [
        symbol
        for symbol in stocks
        if is_stock(
            symbol,
            universe,
        )
    ]
    top10 = sorted(
        stock_symbols,
        key=lambda symbol: (
            strength_rank(
                stocks[symbol][
                    "strength"
                ]
            ),
            get_core_score(
                analysis_stocks[symbol]
            ),
            symbol,
        ),
        reverse=True,
    )[:10]
    # --------------------------------------------------------
    # ETF TOP 10
    #
    # ETF 頁：
    # 強勢 / 可以買進持有的前 10 檔。
    # --------------------------------------------------------
    etf_symbols_all = [
        symbol
        for symbol in stocks
        if is_etf(
            symbol,
            universe,
        )
    ]
    etf_symbols = sorted(
        etf_symbols_all,
        key=lambda symbol: (
            etf_rank_key(
                symbol,
                stocks,
                analysis_stocks,
            ),
            symbol,
        ),
        reverse=True,
    )[:10]
    # --------------------------------------------------------
    # BOND TOP 10
    #
    # 債券頁：
    # Universe type=ETF
    # category=BOND
    # --------------------------------------------------------
    bond_symbols_all = [
        symbol
        for symbol in stocks
        if is_bond_etf(
            symbol,
            universe,
        )
    ]
    bond_symbols = sorted(
        bond_symbols_all,
        key=lambda symbol: (
            etf_rank_key(
                symbol,
                stocks,
                analysis_stocks,
            ),
            symbol,
        ),
        reverse=True,
    )[:10]
    # --------------------------------------------------------
    # MARKET
    # --------------------------------------------------------
    ui_market = {
        "name": "台股市場",
        "timezone": "Asia/Taipei",
        "status": market[
            "market_status"
        ],
        "latest_trading_date": market[
            "latest_trading_date"
        ],
        "index": market[
            "index"
        ],
        "sentiment": market[
            "sentiment"
        ],
    }
    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------
    summary = {
        "today_picks": len(
            today_picks
        ),
        "holdings": {
            "has_holdings": False,
            "profit": None,
        },
        "index": market[
            "index"
        ],
        "sentiment": market[
            "sentiment"
        ],
    }
    # --------------------------------------------------------
    # ROOT
    # --------------------------------------------------------
    ui_data = {
        "schema_version": VERSION,
        "generated_at": (
            datetime.now(
                timezone.utc
            )
            .astimezone()
            .isoformat(
                timespec="seconds"
            )
        ),
        "status": "ok",
        "market": ui_market,
        "summary": summary,
        "tabs": {
            "today_picks": today_picks,
            "top10": top10,
            "etf": etf_symbols,
            "bond": bond_symbols,
            # My List 永遠由使用者端建立。
            "watchlist": [],
        },
        "stocks": stocks,
    }
    # --------------------------------------------------------
    # PRE-WRITE CONTRACT VALIDATION
    # --------------------------------------------------------
    section(
        "VALIDATE UI DATA CONTRACT"
    )
    if not isinstance(
        ui_data["stocks"],
        dict,
    ):
        raise RuntimeError(
            "ui_data.stocks 必須是 object"
        )
    if not isinstance(
        ui_data["tabs"],
        dict,
    ):
        raise RuntimeError(
            "ui_data.tabs 必須是 object"
        )
    if ui_data[
        "tabs"
    ]["watchlist"] != []:
        raise RuntimeError(
            "watchlist 初始值必須為空"
        )
    # --------------------------------------------------------
    # TAB VALIDATION
    # --------------------------------------------------------
    for symbol in today_picks:
        if not is_stock(
            symbol,
            universe,
        ):
            raise RuntimeError(
                f"今日精選包含非 STOCK：{symbol}"
            )
    for symbol in top10:
        if not is_stock(
            symbol,
            universe,
        ):
            raise RuntimeError(
                f"Top 10 包含非 STOCK：{symbol}"
            )
    for symbol in etf_symbols:
        if not is_etf(
            symbol,
            universe,
        ):
            raise RuntimeError(
                f"ETF tab 包含非 ETF：{symbol}"
            )
    for symbol in bond_symbols:
        if not is_bond_etf(
            symbol,
            universe,
        ):
            raise RuntimeError(
                f"Bond tab 包含非債券 ETF：{symbol}"
            )
    if len(top10) > 10:
        raise RuntimeError(
            "Top 10 超過 10 檔"
        )
    if len(etf_symbols) > 10:
        raise RuntimeError(
            "ETF tab 超過 10 檔"
        )
    if len(bond_symbols) > 10:
        raise RuntimeError(
            "Bond tab 超過 10 檔"
        )
    # --------------------------------------------------------
    # PUBLIC STOCK CONTRACT
    # --------------------------------------------------------
    required_public_fields = {
        "symbol",
        "name",
        "market",
        "instrument_type",
        "universe_type",
        "category",
        "price",
        "change",
        "change_pct",
        "strength",
        "recommendation",
        "backend",
        "holding",
    }
    for symbol, item in stocks.items():
        missing = (
            required_public_fields
            - set(item)
        )
        if missing:
            raise RuntimeError(
                f"{symbol} UI 缺少欄位："
                f"{sorted(missing)}"
            )
        if not item[
            "name"
        ]:
            raise RuntimeError(
                f"{symbol} UI name 為空"
            )
        # ----------------------------------------------------
        # 名稱不能退回 symbol。
        # ----------------------------------------------------
        if item["name"] == symbol:
            raise RuntimeError(
                f"{symbol} UI name 錯誤："
                "不得以 symbol 代替官方名稱"
            )
        # ----------------------------------------------------
        # Universe identity validation
        # ----------------------------------------------------
        universe_type = get_universe_type(
            symbol,
            universe[symbol],
        )
        if item[
            "universe_type"
        ] != universe_type:
            raise RuntimeError(
                f"{symbol} UI Universe type mismatch"
            )
        expected_ui_type = (
            get_ui_instrument_type(
                symbol,
                universe[symbol],
            )
        )
        if item[
            "instrument_type"
        ] != expected_ui_type:
            raise RuntimeError(
                f"{symbol} UI instrument_type mismatch: "
                f"{item['instrument_type']} "
                f"!= "
                f"{expected_ui_type}"
            )
        # ----------------------------------------------------
        # Forbidden backend technical fields
        # ----------------------------------------------------
        for field in PUBLIC_FORBIDDEN_FIELDS:
            if field in item:
                raise RuntimeError(
                    f"{symbol} UI 不應暴露 technical field："
                    f"{field}"
                )
        # ----------------------------------------------------
        # Forbidden nested technical fields
        # ----------------------------------------------------
        for nested_name in (
            "backend",
            "holding",
        ):
            nested = item.get(
                nested_name
            )
            if not isinstance(
                nested,
                dict,
            ):
                raise RuntimeError(
                    f"{symbol}.{nested_name} 必須是 object"
                )
            for field in PUBLIC_FORBIDDEN_FIELDS:
                if field in nested:
                    raise RuntimeError(
                        f"{symbol}.{nested_name} "
                        f"不應暴露 technical field："
                        f"{field}"
                    )
    # --------------------------------------------------------
    # WRITE
    # --------------------------------------------------------
    section(
        "WRITE ui_data.json"
    )
    atomic_write_json(
        OUTPUT_FILE,
        ui_data,
    )
    # --------------------------------------------------------
    # READ BACK
    # --------------------------------------------------------
    section(
        "READ-BACK VALIDATION"
    )
    read_back = load_json(
        OUTPUT_FILE
    )
    if not isinstance(
        read_back,
        dict,
    ):
        raise RuntimeError(
            "ui_data.json read-back 不是 object"
        )
    if read_back.get(
        "schema_version"
    ) != VERSION:
        raise RuntimeError(
            "ui_data schema_version 錯誤"
        )
    if read_back.get(
        "status"
    ) != "ok":
        raise RuntimeError(
            "ui_data status 必須為 ok"
        )
    if not isinstance(
        read_back.get("market"),
        dict,
    ):
        raise RuntimeError(
            "ui_data.market 必須是 object"
        )
    if not isinstance(
        read_back.get("summary"),
        dict,
    ):
        raise RuntimeError(
            "ui_data.summary 必須是 object"
        )
    if not isinstance(
        read_back.get("tabs"),
        dict,
    ):
        raise RuntimeError(
            "ui_data.tabs 必須是 object"
        )
    if not isinstance(
        read_back.get("stocks"),
        dict,
    ):
        raise RuntimeError(
            "ui_data.stocks 必須是 object"
        )
    if len(
        read_back["stocks"]
    ) != len(stocks):
        raise RuntimeError(
            "UI stocks 數量與 analysis.stocks 不一致"
        )
    # --------------------------------------------------------
    # READ-BACK TAB VALIDATION
    # --------------------------------------------------------
    if read_back[
        "tabs"
    ]["watchlist"] != []:
        raise RuntimeError(
            "read-back watchlist 必須為空"
        )
    if read_back[
        "tabs"
    ]["today_picks"] != today_picks:
        raise RuntimeError(
            "read-back today_picks 不一致"
        )
    if read_back[
        "tabs"
    ]["top10"] != top10:
        raise RuntimeError(
            "read-back top10 不一致"
        )
    if read_back[
        "tabs"
    ]["etf"] != etf_symbols:
        raise RuntimeError(
            "read-back ETF tab 不一致"
        )
    if read_back[
        "tabs"
    ]["bond"] != bond_symbols:
        raise RuntimeError(
            "read-back Bond tab 不一致"
        )
    # --------------------------------------------------------
    # READ-BACK NAME VALIDATION
    # --------------------------------------------------------
    for symbol, item in read_back[
        "stocks"
    ].items():
        universe_name = get_official_name(
            symbol,
            universe[symbol],
        )
        if item.get(
            "name"
        ) != universe_name:
            raise RuntimeError(
                f"{symbol} UI name 與 Universe 官方名稱不一致："
                f"UI={item.get('name')!r}, "
                f"Universe={universe_name!r}"
            )
    # --------------------------------------------------------
    # FINAL LOG
    # --------------------------------------------------------
    stock_count = sum(
        1
        for symbol in stocks
        if is_stock(
            symbol,
            universe,
        )
    )
    etf_count = sum(
        1
        for symbol in stocks
        if is_etf(
            symbol,
            universe,
        )
    )
    bond_count = sum(
        1
        for symbol in stocks
        if is_bond_etf(
            symbol,
            universe,
        )
    )
    log("")
    log("=" * 76)
    log("FINAL UI DATA VALIDATION")
    log("=" * 76)
    log(
        f"Universe：{len(universe):,}"
    )
    log(
        f"Analysis stocks：{len(analysis_stocks):,}"
    )
    log(
        f"UI stocks：{len(stocks):,}"
    )
    log(
        f"Universe STOCK：{stock_count:,}"
    )
    log(
        f"Universe ETF：{etf_count:,}"
    )
    log(
        f"Bond ETF：{bond_count:,}"
    )
    log(
        f"今日精選：{len(today_picks):,}"
    )
    log(
        f"Top 10：{len(top10):,}"
    )
    log(
        f"ETF Top 10：{len(etf_symbols):,}"
    )
    log(
        f"Bond Top 10：{len(bond_symbols):,}"
    )
    log(
        f"市場最新交易日："
        f"{market['latest_trading_date']}"
    )
    log(
        f"市場風向："
        f"{market['sentiment']['level']}"
    )
    log("")
    log(
        "✓ Official Universe name validation PASS"
    )
    log(
        "✓ STOCK / ETF identity validation PASS"
    )
    log(
        "✓ Today Picks STOCK-only validation PASS"
    )
    log(
        "✓ Top 10 STOCK-only validation PASS"
    )
    log(
        "✓ ETF Top 10 validation PASS"
    )
    log(
        "✓ Bond ETF validation PASS"
    )
    log(
        "✓ Public technical-field isolation PASS"
    )
    log(
        "✓ ui_data.json read-back validation PASS"
    )
    log("")
    log(
        "✓ BUILD UI DATA V2.2 PASS"
    )
    return 0
# ============================================================
# ENTRY
# ============================================================
if __name__ == "__main__":
    raise SystemExit(
        main()
    )