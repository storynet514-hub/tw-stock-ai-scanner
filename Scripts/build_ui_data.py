#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_ui_data.py

UI-DATA-2.4

資料責任：
    Universe + Analysis + Market
                ↓
         Data/ui_data.json
                ↓
            index.html

名稱處理：
    1. 官方上市公司基本資料
    2. 官方上櫃公司基本資料
    3. Universe 已存在的中文名稱
    4. 不允許純英文名稱冒充中文名稱

保留契約：
    - Universe 是商品身份唯一權威來源
    - Universe.type / official_type 決定 STOCK / ETF
    - instrument_type 是子類型，不是 STOCK / ETF 主分類
    - 今日精選只能包含普通個股
    - Top10 只能是今日精選的子集合
    - ETF / 債券分頁獨立
    - 不在此處計算技術指標
    - atomic write
    - write 後 read-back validation
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import urllib.request
import urllib.error

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"
ANALYSIS_FILE = DATA_DIR / "analysis.json"
MARKET_FILE = DATA_DIR / "market.json"
OUTPUT_FILE = DATA_DIR / "ui_data.json"

VERSION = "UI-DATA-2.4"

# 官方公司基本資料
TWSE_COMPANY_URL = (
    "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
)
TPEX_COMPANY_URL = (
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
)

HTTP_TIMEOUT = 20

BUYABLE_RECOMMENDATIONS = {
    "偏多，可分批",
    "積極關注",
    "續抱觀察",
}

STRENGTH_RANK = {
    "強勢": 3,
    "中性": 2,
    "弱勢": 1,
}


# ============================================================
# BASIC
# ============================================================

def load_json(path: Path) -> Any:
    if not path.exists():
        raise RuntimeError(f"找不到檔案：{path}")

    try:
        with path.open("r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception as exc:
        raise RuntimeError(
            f"JSON 讀取失敗：{path}: {exc}"
        ) from exc


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_path, path)

    finally:
        tmp_path.unlink(missing_ok=True)


def text(value: Any) -> str:
    if value is None:
        return ""

    return (
        str(value)
        .replace("\ufeff", "")
        .replace("\xa0", " ")
        .replace("\u3000", " ")
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("\t", " ")
        .strip()
    )


def normalize_symbol(value: Any) -> str:
    symbol = text(value).upper()

    for suffix in (
        ".TW",
        ".TWO",
        ".TSE",
        ".OTC",
        ".TWSE",
        ".TPEX",
    ):
        if symbol.endswith(suffix):
            symbol = symbol[:-len(suffix)]
            break

    return symbol


def number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None

    try:
        value = str(value)
        value = value.replace(",", "").replace("%", "").strip()
        result = float(value)

        if not math.isfinite(result):
            return None

        return result

    except (TypeError, ValueError):
        return None


def contains_chinese(value: Any) -> bool:
    return bool(
        re.search(
            r"[\u3400-\u4DBF\u4E00-\u9FFF]",
            text(value),
        )
    )


def fetch_json_url(url: str) -> Any:
    """
    從官方 API 取得 JSON。
    不使用第三方行情網站作為公司名稱來源。
    """
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "TaiwanStockAI/1.0",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=HTTP_TIMEOUT,
        ) as response:
            raw = response.read().decode("utf-8-sig")

        return json.loads(raw)

    except Exception as exc:
        raise RuntimeError(
            f"官方名稱 API 讀取失敗：{url}；原因：{exc}"
        ) from exc


# ============================================================
# OFFICIAL CHINESE NAME MAP
# ============================================================

def first_nonempty(
    row: Dict[str, Any],
    keys: tuple[str, ...],
) -> str:
    for key in keys:
        value = text(row.get(key))
        if value:
            return value
    return ""


def parse_official_name_rows(
    rows: Any,
    source: str,
) -> Dict[str, str]:
    """
    將官方公司基本資料正規化為：
        股票代號 -> 中文簡稱

    TWSE 使用中文欄位；
    TPEx API 使用英文欄位。
    """
    if not isinstance(rows, list):
        raise RuntimeError(
            f"{source} 回傳格式錯誤：預期 JSON array"
        )

    result: Dict[str, str] = {}

    for row in rows:
        if not isinstance(row, dict):
            continue

        symbol = normalize_symbol(
            first_nonempty(
                row,
                (
                    "公司代號",
                    "證券代號",
                    "股票代號",
                    "SecuritiesCompanyCode",
                    "CompanyCode",
                    "Code",
                ),
            )
        )

        name = first_nonempty(
            row,
            (
                "公司簡稱",
                "公司名稱",
                "股票名稱",
                "名稱",
                "CompanyAbbreviation",
                "CompanyName",
                "SecurityName",
                "Name",
            ),
        )

        if not symbol or not name:
            continue

        if not contains_chinese(name):
            continue

        result[symbol] = name

    if not result:
        raise RuntimeError(
            f"{source} 沒有取得有效中文名稱資料"
        )

    print(f"{source} 中文名稱筆數：{len(result)}")
    return result


def load_official_chinese_names() -> Dict[str, str]:
    """
    同時讀取上市、上櫃官方公司基本資料。

    兩個來源都失敗時，不會靜默當成成功。
    若只有一個來源成功，保留成功來源並輸出警告。
    """
    combined: Dict[str, str] = {}
    errors = []

    sources = (
        ("TWSE", TWSE_COMPANY_URL),
        ("TPEX", TPEX_COMPANY_URL),
    )

    for source, url in sources:
        try:
            rows = fetch_json_url(url)
            names = parse_official_name_rows(rows, source)
            combined.update(names)

        except Exception as exc:
            errors.append(f"{source}: {exc}")
            print(f"WARNING：{source} 名稱資料取得失敗：{exc}")

    if not combined:
        raise RuntimeError(
            "上市、上櫃官方中文名稱資料均無法取得。"
            "停止建立 UI 資料，避免英文名稱混入輸出。"
            + "；".join(errors)
        )

    print(f"官方中文名稱合併筆數：{len(combined)}")
    return combined


# ============================================================
# UNIVERSE
# ============================================================

def load_universe() -> Dict[str, Dict[str, Any]]:
    data = load_json(UNIVERSE_FILE)

    if not isinstance(data, dict):
        raise RuntimeError(
            "universe.json 根節點必須是 object"
        )

    stocks = data.get("stocks")

    if not isinstance(stocks, dict):
        raise RuntimeError(
            "universe.json stocks 必須是 object"
        )

    output = {}

    for raw_symbol, raw_item in stocks.items():
        if not isinstance(raw_item, dict):
            continue

        symbol = normalize_symbol(raw_symbol)
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
        raise RuntimeError("Universe 沒有有效商品")

    return output


# ============================================================
# UNIVERSE IDENTITY
# ============================================================

def get_universe_type(
    symbol: str,
    universe: Dict[str, Any],
) -> str:
    value = text(universe.get("type")).upper()

    if value in {"STOCK", "STOCKS"}:
        return "STOCK"

    if value == "ETF":
        return "ETF"

    fallback = text(universe.get("official_type")).upper()

    if fallback in {"STOCK", "STOCKS"}:
        return "STOCK"

    if fallback == "ETF":
        return "ETF"

    fallback = text(universe.get("security_type")).upper()

    if fallback in {"STOCK", "STOCKS"}:
        return "STOCK"

    if fallback == "ETF":
        return "ETF"

    raise RuntimeError(
        f"{symbol} Universe type 無效："
        f"{universe.get('type')!r}"
    )


def get_category(universe: Dict[str, Any]) -> str:
    value = (
        universe.get("category")
        or universe.get("asset_category")
        or universe.get("instrument_category")
        or ""
    )

    normalized = text(value).upper()

    if "BOND" in normalized or "債" in normalized:
        return "BOND"

    return "EQUITY"


def get_public_instrument_type(
    universe: Dict[str, Any],
) -> str:
    base_type = get_universe_type(
        universe["symbol"],
        universe,
    )

    if base_type == "STOCK":
        return "stock"

    if base_type == "ETF" and get_category(universe) == "BOND":
        return "bond"

    return "etf"


# ============================================================
# NAME
# ============================================================

def get_official_name(
    symbol: str,
    universe: Dict[str, Any],
    official_names: Dict[str, str],
) -> str:
    """
    名稱優先順序：

    1. 證交所／櫃買中心官方中文簡稱
    2. Universe 明確中文名稱欄位
    3. Universe 其他欄位中確實含中文的名稱

    不允許純英文名稱直接進入 UI。
    """

    mapped_name = text(official_names.get(symbol))

    if mapped_name and contains_chinese(mapped_name):
        return mapped_name

    chinese_candidates = (
        "display_name_zh",
        "name_zh",
        "chinese_name",
        "security_name_zh",
        "stock_name",
        "名稱",
        "股票名稱",
        "公司簡稱",
        "公司名稱",
    )

    for key in chinese_candidates:
        value = text(universe.get(key))

        if value and contains_chinese(value):
            return value

    fallback_candidates = (
        "display_name",
        "name",
        "security_name",
    )

    for key in fallback_candidates:
        value = text(universe.get(key))

        if value and contains_chinese(value):
            return value

    raise RuntimeError(
        f"{symbol} 找不到中文名稱。"
        "官方公司主檔與 Universe 均未提供可用中文名稱。"
    )


# ============================================================
# MARKET
# ============================================================

def get_market(universe: Dict[str, Any]) -> str:
    value = (
        universe.get("market")
        or universe.get("exchange")
        or universe.get("market_type")
        or ""
    )

    value = text(value).upper()

    if value in {"TWSE", "TSE"}:
        return "TWSE"

    if value in {"TPEX", "OTC"}:
        return "TPEX"

    raise RuntimeError(
        f"{universe.get('symbol')} market 無效：{value!r}"
    )


# ============================================================
# ANALYSIS
# ============================================================

def load_analysis() -> Dict[str, Any]:
    data = load_json(ANALYSIS_FILE)

    if not isinstance(data, dict):
        raise RuntimeError(
            "analysis.json 根節點必須是 object"
        )

    stocks = data.get("stocks")

    if not isinstance(stocks, dict):
        raise RuntimeError(
            "analysis.json stocks 必須是 object"
        )

    normalized = {}

    for raw_symbol, raw_item in stocks.items():
        if not isinstance(raw_item, dict):
            raise RuntimeError(
                f"analysis.json stocks[{raw_symbol}] 必須是 object"
            )

        symbol = normalize_symbol(raw_symbol)

        if symbol:
            normalized[symbol] = dict(raw_item)

    if not normalized:
        raise RuntimeError(
            "analysis.json 沒有有效 stocks"
        )

    return {
        "root": data,
        "stocks": normalized,
    }


def get_metrics(analysis: Dict[str, Any]):
    metrics = analysis.get("metrics")
    short_term = analysis.get("short_term")

    if not isinstance(metrics, dict):
        metrics = {}

    if not isinstance(short_term, dict):
        short_term = {}

    return metrics, short_term


# ============================================================
# PRICE
# ============================================================

def get_price_fields(analysis: Dict[str, Any]):
    metrics, _ = get_metrics(analysis)

    price = None
    change = None
    change_pct = None

    for key in (
        "price",
        "close",
        "latest_price",
        "last_price",
    ):
        value = number(metrics.get(key))

        if value is not None:
            price = value
            break

    if price is None:
        for key in ("price", "close"):
            value = number(analysis.get(key))

            if value is not None:
                price = value
                break

    for key in ("change", "price_change"):
        value = number(metrics.get(key))

        if value is not None:
            change = value
            break

    if change is None:
        change = number(analysis.get("change"))

    for key in (
        "change_pct",
        "change_percent",
        "change1_pct",
    ):
        value = number(metrics.get(key))

        if value is not None:
            change_pct = value
            break

    if change_pct is None:
        change_pct = number(analysis.get("change_pct"))

    return price, change, change_pct


# ============================================================
# STRENGTH
# ============================================================

def get_strength(analysis: Dict[str, Any]) -> str:
    metrics, short_term = get_metrics(analysis)

    candidates = (
        short_term.get("strength"),
        short_term.get("technical_strength"),
        analysis.get("strength"),
        analysis.get("strength_label"),
        metrics.get("strength"),
    )

    for value in candidates:
        value = text(value)

        if not value:
            continue

        lowered = value.lower()

        if (
            "強" in value
            or "多" in value
            or lowered in {"strong", "bullish"}
        ):
            return "強勢"

        if (
            "弱" in value
            or "空" in value
            or lowered in {"weak", "bearish"}
        ):
            return "弱勢"

        if (
            "中" in value
            or lowered in {"neutral", "flat"}
        ):
            return "中性"

    return "中性"


# ============================================================
# RECOMMENDATION
# ============================================================

def get_recommendation(analysis: Dict[str, Any]) -> str:
    metrics, short_term = get_metrics(analysis)

    candidates = (
        analysis.get("recommendation"),
        analysis.get("signal"),
        short_term.get("recommendation"),
        metrics.get("recommendation"),
    )

    for value in candidates:
        value = text(value)

        if not value:
            continue

        if "立即進場" in value:
            return "積極關注"

        if "買進" in value or "偏多" in value:
            return "偏多，可分批"

        if "回測" in value:
            return "等回測"

        if "減碼" in value:
            return "考慮減碼"

        if "暫停" in value:
            return "暫停操作"

        if "續抱" in value or "觀察" in value:
            return "續抱觀察"

    return "續抱觀察"


# ============================================================
# INTERNAL SCORE
# ============================================================

def get_core_score(analysis: Dict[str, Any]) -> float:
    _, short_term = get_metrics(analysis)

    candidates = (
        short_term.get("core_score"),
        analysis.get("core_score"),
        analysis.get("score"),
    )

    for value in candidates:
        value = number(value)

        if value is not None:
            return value

    return 0.0


# ============================================================
# PUBLIC RECORD
# ============================================================

def build_public_record(
    symbol: str,
    universe: Dict[str, Any],
    analysis: Dict[str, Any],
    latest_date: str,
    official_names: Dict[str, str],
) -> Dict[str, Any]:
    price, change, change_pct = get_price_fields(analysis)

    return {
        "symbol": symbol,
        "name": get_official_name(
            symbol,
            universe,
            official_names,
        ),
        "market": get_market(universe),
        "instrument_type": get_public_instrument_type(
            universe
        ),
        "price": price,
        "change": change,
        "change_pct": change_pct,
        "strength": get_strength(analysis),
        "recommendation": get_recommendation(analysis),
        "backend": {
            "status": "ok",
            "latest_date": latest_date,
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

def extract_candidate_symbols(root: Dict[str, Any]):
    raw = root.get("short_term_candidates")

    if not raw:
        return []

    if isinstance(raw, dict):
        raw = list(raw.values())

    if not isinstance(raw, list):
        return []

    result = []

    for item in raw:
        if isinstance(item, dict):
            symbol = normalize_symbol(
                item.get("symbol")
                or item.get("code")
                or item.get("ticker")
            )
        else:
            symbol = normalize_symbol(item)

        if symbol and symbol not in result:
            result.append(symbol)

    return result


def is_explicit_candidate(analysis: Dict[str, Any]) -> bool:
    short_term = analysis.get("short_term")

    if not isinstance(short_term, dict):
        short_term = {}

    flags = (
        "qualified",
        "is_candidate",
        "candidate",
        "short_term_qualified",
    )

    for key in flags:
        if (
            analysis.get(key) is True
            or short_term.get(key) is True
        ):
            return True

    return False


# ============================================================
# VALIDATION
# ============================================================

def validate_ui_data(data: Dict[str, Any]) -> None:
    if data.get("schema_version") != VERSION:
        raise RuntimeError("ui_data schema_version 錯誤")

    stocks = data.get("stocks")

    if not isinstance(stocks, dict) or not stocks:
        raise RuntimeError("ui_data.stocks 無效")

    for symbol, record in stocks.items():
        if not isinstance(record, dict):
            raise RuntimeError(
                f"{symbol} UI record 不是 object"
            )

        if record.get("symbol") != symbol:
            raise RuntimeError(
                f"{symbol} symbol mismatch"
            )

        name = text(record.get("name"))

        if not name:
            raise RuntimeError(
                f"{symbol} 缺少 name"
            )

        if not contains_chinese(name):
            raise RuntimeError(
                f"{symbol} 名稱不是中文：{name}"
            )

        if record.get("instrument_type") not in {
            "stock",
            "etf",
            "bond",
        }:
            raise RuntimeError(
                f"{symbol} instrument_type 無效："
                f"{record.get('instrument_type')}"
            )

    tabs = data.get("tabs")

    if not isinstance(tabs, dict):
        raise RuntimeError("ui_data.tabs 無效")

    expected_tabs = {
        "today_picks",
        "top10",
        "etf",
        "bond",
        "watchlist",
    }

    if set(tabs) != expected_tabs:
        raise RuntimeError("ui_data.tabs 欄位錯誤")

    today = tabs["today_picks"]
    top10 = tabs["top10"]
    etf = tabs["etf"]
    bond = tabs["bond"]

    for symbol in today:
        if symbol not in stocks:
            raise RuntimeError(
                f"Today Picks 缺少股票資料：{symbol}"
            )

        if stocks[symbol]["instrument_type"] != "stock":
            raise RuntimeError(
                f"Today Picks 出現非個股：{symbol}"
            )

    if not set(top10).issubset(set(today)):
        raise RuntimeError(
            "Top10 不是 Today Picks 子集合"
        )

    if len(top10) > 10:
        raise RuntimeError("Top10 超過 10 檔")

    if len(etf) > 10:
        raise RuntimeError("ETF 超過 10 檔")

    if len(bond) > 10:
        raise RuntimeError("Bond 超過 10 檔")

    for symbol in etf:
        if (
            symbol not in stocks
            or stocks[symbol]["instrument_type"] != "etf"
        ):
            raise RuntimeError(
                f"ETF tab 出現非 ETF：{symbol}"
            )

    for symbol in bond:
        if (
            symbol not in stocks
            or stocks[symbol]["instrument_type"] != "bond"
        ):
            raise RuntimeError(
                f"Bond tab 出現非債券 ETF：{symbol}"
            )

    if "00400A" in stocks:
        if stocks["00400A"]["instrument_type"] != "etf":
            raise RuntimeError(
                "00400A 必須判定為 ETF"
            )


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    universe = load_universe()
    analysis_bundle = load_analysis()

    analysis_root = analysis_bundle["root"]
    analysis = analysis_bundle["stocks"]

    market = load_json(MARKET_FILE)

    if not isinstance(market, dict):
        raise RuntimeError(
            "market.json 根節點必須是 object"
        )

    latest_date = text(
        market.get("latest_trading_date")
    )

    # 官方公司中文名稱資料
    official_names = load_official_chinese_names()

    public = {}
    internal = {}

    for symbol, universe_record in universe.items():
        if symbol not in analysis:
            continue

        analysis_record = analysis[symbol]
        internal[symbol] = analysis_record

        public[symbol] = build_public_record(
            symbol,
            universe_record,
            analysis_record,
            latest_date,
            official_names,
        )

    if not public:
        raise RuntimeError(
            "沒有任何 Universe / Analysis "
            "成功建立 UI record"
        )

    # --------------------------------------------------------
    # TODAY PICKS
    # --------------------------------------------------------

    candidates = extract_candidate_symbols(analysis_root)
    today_picks = []

    for symbol in candidates:
        if symbol not in public:
            continue

        if public[symbol]["instrument_type"] != "stock":
            continue

        today_picks.append(symbol)

    # fallback：只接受 analysis 明確標示 candidate 的個股
    if not today_picks:
        for symbol in public:
            if public[symbol]["instrument_type"] != "stock":
                continue

            if is_explicit_candidate(internal[symbol]):
                today_picks.append(symbol)

    def rank_key(symbol: str):
        record = public[symbol]

        return (
            STRENGTH_RANK.get(record["strength"], 0),
            get_core_score(internal[symbol]),
            symbol,
        )

    today_picks = sorted(
        list(dict.fromkeys(today_picks)),
        key=rank_key,
        reverse=True,
    )

    top10 = today_picks[:10]

    # --------------------------------------------------------
    # ETF
    # --------------------------------------------------------

    etf_symbols = []

    for symbol, record in public.items():
        if record["instrument_type"] != "etf":
            continue

        if (
            record["strength"] == "強勢"
            or record["recommendation"] in BUYABLE_RECOMMENDATIONS
        ):
            etf_symbols.append(symbol)

    etf_symbols = sorted(
        etf_symbols,
        key=rank_key,
        reverse=True,
    )[:10]

    # --------------------------------------------------------
    # BOND
    # --------------------------------------------------------

    bond_symbols = []

    for symbol, record in public.items():
        if record["instrument_type"] != "bond":
            continue

        if (
            record["strength"] == "強勢"
            or record["recommendation"] in BUYABLE_RECOMMENDATIONS
        ):
            bond_symbols.append(symbol)

    bond_symbols = sorted(
        bond_symbols,
        key=rank_key,
        reverse=True,
    )[:10]

    # --------------------------------------------------------
    # MARKET
    # --------------------------------------------------------

    index = market.get("index")

    if not isinstance(index, dict):
        index = {}

    sentiment = market.get("sentiment")

    # --------------------------------------------------------
    # OUTPUT
    # --------------------------------------------------------

    ui_data = {
        "schema_version": VERSION,
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "status": "ok",
        "market": {
            "name": "台股市場",
            "timezone": "Asia/Taipei",
            "status": market.get("market_status"),
            "latest_trading_date": latest_date,
            "index": index,
            "sentiment": sentiment,
        },
        "summary": {
            "today_picks_count": len(today_picks),
            "holdings": False,
            "index": index,
            "sentiment": sentiment,
        },
        "tabs": {
            "today_picks": today_picks,
            "top10": top10,
            "etf": etf_symbols,
            "bond": bond_symbols,
            "watchlist": [],
        },
        "stocks": public,
    }

    validate_ui_data(ui_data)

    atomic_write_json(OUTPUT_FILE, ui_data)

    read_back = load_json(OUTPUT_FILE)
    validate_ui_data(read_back)

    print("========================================")
    print("UI DATA BUILD PASS")
    print("========================================")
    print(f"Universe：{len(universe)}")
    print(f"官方中文名稱：{len(official_names)}")
    print(f"UI stocks：{len(public)}")
    print(f"Today Picks：{len(today_picks)}")
    print(f"Top10：{len(top10)}")
    print(f"ETF：{len(etf_symbols)}")
    print(f"Bond：{len(bond_symbols)}")
    print("中文名稱 validation PASS")
    print("read-back validation PASS")
    print("========================================")


if __name__ == "__main__":
    main()
