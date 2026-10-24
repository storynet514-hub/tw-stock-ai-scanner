
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_ui_data.py

UI-DATA-2.6

資料流程：
    Universe + Analysis + Market
                ↓
       官方中文名稱驗證
                ↓
         Data/ui_data.json
                ↓
            index.html

修正：
    - 官方 API 自動重試
    - HTTP 傳輸完整性檢查
    - JSON 結構檢查
    - 必要官方資料來源失敗即停止
    - 一次列出全部缺少中文名稱代號
    - 不以英文名稱冒充中文名稱

保留：
    - Universe 是商品身份權威
    - Universe.type / official_type 決定 STOCK / ETF
    - instrument_type 不決定商品主分類
    - Today Picks 只能有普通個股
    - Top10 是 Today Picks 子集合
    - ETF / Bond 分頁獨立
    - 不重新計算技術指標
    - atomic write
    - read-back validation
"""

from __future__ import annotations

import http.client
import json
import math
import os
import re
import socket
import tempfile
import time
import urllib.error
import urllib.request

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


# ============================================================
# CONFIG
# ============================================================

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"
ANALYSIS_FILE = DATA_DIR / "analysis.json"
MARKET_FILE = DATA_DIR / "market.json"
OUTPUT_FILE = DATA_DIR / "ui_data.json"

VERSION = "UI-DATA-2.6"

TWSE_COMPANY_URL = (
    "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
)

TPEX_COMPANY_URL = (
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
)

TWSE_FUND_URL = (
    "https://openapi.twse.com.tw/v1/opendata/t187ap47_L"
)

HTTP_TIMEOUT = 45
HTTP_MAX_RETRIES = 5
HTTP_RETRY_DELAY = 3

OFFICIAL_SOURCES = (
    ("TWSE", TWSE_COMPANY_URL),
    ("TPEX", TPEX_COMPANY_URL),
    ("TWSE_FUND", TWSE_FUND_URL),
)

# 過去成功取得的官方名稱筆數：
# TWSE 1088 / TPEX 891 / TWSE_FUND 272
# 設定保守下限，避免異常空資料被當成成功。
MIN_SOURCE_COUNTS = {
    "TWSE": 900,
    "TPEX": 750,
    "TWSE_FUND": 200,
}

VERIFIED_OFFICIAL_NAMES = {
    "00411A": "主動統一前沿科技",
    "006201": "元大富櫃50",
}

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

SYMBOL_KEYS = (
    "公司代號",
    "證券代號",
    "股票代號",
    "基金代號",
    "ETF代號",
    "證券代碼",
    "有價證券代號",
    "代號",
    "SecuritiesCompanyCode",
    "CompanyCode",
    "SecurityCode",
    "SecuritiesCode",
    "Code",
)

NAME_KEYS = (
    "公司簡稱",
    "公司名稱",
    "股票名稱",
    "基金簡稱",
    "基金名稱",
    "證券簡稱",
    "證券名稱",
    "ETF名稱",
    "有價證券名稱",
    "名稱",
    "基金中文名稱",
    "基金中文簡稱",
    "中文簡稱",
    "中文名稱",
    "CompanyAbbreviation",
    "CompanyName",
    "SecurityName",
    "FundName",
    "Name",
)

UNIVERSE_NAME_KEYS = (
    "display_name_zh",
    "name_zh",
    "chinese_name",
    "security_name_zh",
    "stock_name",
    "基金簡稱",
    "基金名稱",
    "證券簡稱",
    "名稱",
    "股票名稱",
    "公司簡稱",
    "公司名稱",
    "display_name",
    "name",
    "security_name",
)


# ============================================================
# BASIC
# ============================================================

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
            return symbol[:-len(suffix)]

    return symbol


def contains_chinese(value: Any) -> bool:
    return bool(
        re.search(
            r"[\u3400-\u4DBF\u4E00-\u9FFF]",
            text(value),
        )
    )


def number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None

    try:
        result = float(
            str(value)
            .replace(",", "")
            .replace("%", "")
            .strip()
        )

        return result if math.isfinite(result) else None

    except (TypeError, ValueError):
        return None


def load_json(path: Path) -> Any:
    if not path.exists():
        raise RuntimeError(f"找不到檔案：{path}")

    try:
        with path.open("r", encoding="utf-8-sig") as f:
            return json.load(f)

    except Exception as exc:
        raise RuntimeError(
            f"JSON 讀取失敗：{path}：{exc}"
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


# ============================================================
# OFFICIAL API
# ============================================================

def fetch_json_url(url: str, source: str) -> Any:
    """
    官方 API 下載。

    處理：
        IncompleteRead
        HTTPError
        URLError
        Timeout
        JSONDecodeError
        Content-Length mismatch

    只接受完整且可解析的 JSON。
    """

    last_error = None

    for attempt in range(1, HTTP_MAX_RETRIES + 1):

        print(
            f"{source} API 下載 "
            f"{attempt}/{HTTP_MAX_RETRIES}",
            flush=True,
        )

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "TaiwanStockAI/2.6",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "Cache-Control": "no-cache",
                "Connection": "close",
            },
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=HTTP_TIMEOUT,
            ) as response:

                content_length = response.headers.get(
                    "Content-Length"
                )

                raw_bytes = response.read()

                if content_length:
                    expected = int(content_length)

                    if len(raw_bytes) != expected:
                        raise RuntimeError(
                            "HTTP 資料長度不一致："
                            f"預期 {expected} bytes，"
                            f"實際 {len(raw_bytes)} bytes"
                        )

            if not raw_bytes:
                raise RuntimeError("API 回傳空白資料")

            raw_text = raw_bytes.decode("utf-8-sig")

            data = json.loads(raw_text)

            if not isinstance(data, list):
                raise RuntimeError(
                    "官方 API JSON 根節點不是 array"
                )

            if not data:
                raise RuntimeError(
                    "官方 API 回傳空陣列"
                )

            print(
                f"{source} API 下載成功："
                f"{len(raw_bytes)} bytes，"
                f"{len(data)} rows",
                flush=True,
            )

            return data

        except (
            http.client.IncompleteRead,
            urllib.error.HTTPError,
            urllib.error.URLError,
            socket.timeout,
            TimeoutError,
            ConnectionError,
            OSError,
            UnicodeError,
            ValueError,
            RuntimeError,
        ) as exc:

            last_error = exc

            print(
                f"WARNING：{source} 第 {attempt} 次失敗："
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )

            if attempt < HTTP_MAX_RETRIES:
                delay = HTTP_RETRY_DELAY * attempt

                print(
                    f"{source} 等待 {delay} 秒後重試",
                    flush=True,
                )

                time.sleep(delay)

    raise RuntimeError(
        f"{source} 官方 API 連續 "
        f"{HTTP_MAX_RETRIES} 次下載失敗。"
        f"URL：{url}；"
        f"最後錯誤：{last_error}"
    )


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

    if not isinstance(rows, list):
        raise RuntimeError(
            f"{source} JSON 格式錯誤"
        )

    result: Dict[str, str] = {}

    for row in rows:
        if not isinstance(row, dict):
            continue

        symbol = normalize_symbol(
            first_nonempty(row, SYMBOL_KEYS)
        )

        name = first_nonempty(row, NAME_KEYS)

        if not symbol:
            continue

        if not contains_chinese(name):
            continue

        result[symbol] = name

    minimum = MIN_SOURCE_COUNTS[source]

    if len(result) < minimum:
        raise RuntimeError(
            f"{source} 中文名稱資料量異常："
            f"取得 {len(result)} 筆，"
            f"最低要求 {minimum} 筆。"
            "請檢查官方 API 與欄位格式。"
        )

    print(
        f"{source} 中文名稱筆數：{len(result)}",
        flush=True,
    )

    return result


def load_official_chinese_names() -> Dict[str, str]:
    """
    三個官方資料來源均為必要來源。

    任何來源失敗，不繼續建置 UI。
    避免因整批資料缺漏而逐檔報錯。
    """

    combined: Dict[str, str] = {}
    failures = []

    for source, url in OFFICIAL_SOURCES:

        try:
            rows = fetch_json_url(url, source)

            names = parse_official_name_rows(
                rows,
                source,
            )

            combined.update(names)

        except Exception as exc:
            failures.append(f"{source}: {exc}")

            print(
                f"ERROR：{source} 官方名稱資料失敗：{exc}",
                flush=True,
            )

    if failures:
        raise RuntimeError(
            "官方中文名稱資料來源未完整取得，"
            "停止 BUILD UI DATA。\n"
            + "\n".join(failures)
        )

    print(
        f"官方中文名稱合併筆數：{len(combined)}",
        flush=True,
    )

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
                f"Universe 重複 symbol：{symbol}"
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

    for key in (
        "type",
        "official_type",
        "security_type",
    ):
        value = text(universe.get(key)).upper()

        if value in {"STOCK", "STOCKS"}:
            return "STOCK"

        if value == "ETF":
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

    if get_category(universe) == "BOND":
        return "bond"

    return "etf"


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
# CHINESE NAME
# ============================================================

def get_official_name(
    symbol: str,
    universe: Dict[str, Any],
    official_names: Dict[str, str],
) -> str:

    candidates = [
        official_names.get(symbol),
        VERIFIED_OFFICIAL_NAMES.get(symbol),
    ]

    for key in UNIVERSE_NAME_KEYS:
        candidates.append(universe.get(key))

    for value in candidates:
        name = text(value)

        if name and contains_chinese(name):
            return name

    return ""


def validate_all_chinese_names(
    universe: Dict[str, Dict[str, Any]],
    analysis: Dict[str, Any],
    official_names: Dict[str, str],
) -> Dict[str, str]:
    """
    一次掃描所有需要建立 UI 的商品。

    不在第一檔缺名商品中斷。
    全部缺漏代號會在同一次 Actions log 顯示。
    """

    resolved = {}
    missing = []

    for symbol, record in universe.items():

        if symbol not in analysis:
            continue

        name = get_official_name(
            symbol,
            record,
            official_names,
        )

        if not name:
            missing.append(symbol)
        else:
            resolved[symbol] = name

    print(
        f"中文名稱驗證商品數："
        f"{len(resolved) + len(missing)}",
        flush=True,
    )

    print(
        f"中文名稱缺漏數：{len(missing)}",
        flush=True,
    )

    if missing:
        print(
            "========================================",
            flush=True,
        )
        print(
            "MISSING CHINESE NAMES",
            flush=True,
        )
        print(
            "========================================",
            flush=True,
        )

        for symbol in missing:
            record = universe[symbol]

            print(
                f"{symbol} | "
                f"market={record.get('market')} | "
                f"type={record.get('type')} | "
                f"name={record.get('name')}",
                flush=True,
            )

        raise RuntimeError(
            f"共有 {len(missing)} 檔商品缺少中文名稱。"
            "完整代號已列於上方日誌。"
            "停止建立 UI，避免英文名稱混入。"
        )

    print(
        "中文名稱完整性驗證 PASS",
        flush=True,
    )

    return resolved


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
                f"analysis.json stocks[{raw_symbol}] "
                "必須是 object"
            )

        symbol = normalize_symbol(raw_symbol)

        if not symbol:
            continue

        if symbol in normalized:
            raise RuntimeError(
                f"Analysis 重複 symbol：{symbol}"
            )

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
        change_pct = number(
            analysis.get("change_pct")
        )

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

def get_recommendation(
    analysis: Dict[str, Any],
) -> str:

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
# SCORE
# ============================================================

def get_core_score(
    analysis: Dict[str, Any],
) -> float:

    _, short_term = get_metrics(analysis)

    candidates = (
        short_term.get("core_score"),
        analysis.get("core_score"),
        analysis.get("score"),
    )

    for value in candidates:
        result = number(value)

        if result is not None:
            return result

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

    price, change, change_pct = get_price_fields(
        analysis
    )

    name = get_official_name(
        symbol,
        universe,
        official_names,
    )

    if not name:
        raise RuntimeError(
            f"{symbol} 缺少中文名稱，"
            "名稱預先驗證與 record 不一致"
        )

    return {
        "symbol": symbol,
        "name": name,
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

def extract_candidate_symbols(
    root: Dict[str, Any],
):
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


def is_explicit_candidate(
    analysis: Dict[str, Any],
) -> bool:

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

def validate_ui_data(
    data: Dict[str, Any],
) -> None:

    if data.get("schema_version") != VERSION:
        raise RuntimeError(
            "ui_data schema_version 錯誤"
        )

    stocks = data.get("stocks")

    if not isinstance(stocks, dict) or not stocks:
        raise RuntimeError(
            "ui_data.stocks 無效"
        )

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

        if not contains_chinese(name):
            raise RuntimeError(
                f"{symbol} 中文名稱無效：{name}"
            )

        if record.get("instrument_type") not in {
            "stock",
            "etf",
            "bond",
        }:
            raise RuntimeError(
                f"{symbol} instrument_type 無效"
            )

    tabs = data.get("tabs")

    if not isinstance(tabs, dict):
        raise RuntimeError(
            "ui_data.tabs 無效"
        )

    expected_tabs = {
        "today_picks",
        "top10",
        "etf",
        "bond",
        "watchlist",
    }

    if set(tabs) != expected_tabs:
        raise RuntimeError(
            "ui_data.tabs 欄位錯誤"
        )

    for key in expected_tabs:
        if not isinstance(tabs[key], list):
            raise RuntimeError(
                f"{key} 必須是 list"
            )

    today = tabs["today_picks"]
    top10 = tabs["top10"]
    etf = tabs["etf"]
    bond = tabs["bond"]

    for symbol in today:

        if symbol not in stocks:
            raise RuntimeError(
                f"Today Picks 缺少資料：{symbol}"
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
        raise RuntimeError(
            "Top10 超過 10 檔"
        )

    if len(etf) > 10:
        raise RuntimeError(
            "ETF 超過 10 檔"
        )

    if len(bond) > 10:
        raise RuntimeError(
            "Bond 超過 10 檔"
        )

    for symbol in etf:
        if (
            symbol not in stocks
            or stocks[symbol]["instrument_type"] != "etf"
        ):
            raise RuntimeError(
                f"ETF tab 分類錯誤：{symbol}"
            )

    for symbol in bond:
        if (
            symbol not in stocks
            or stocks[symbol]["instrument_type"] != "bond"
        ):
            raise RuntimeError(
                f"Bond tab 分類錯誤：{symbol}"
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

    print(
        "========================================",
        flush=True,
    )
    print(
        "BUILD UI DATA - UI-DATA-2.6",
        flush=True,
    )
    print(
        "========================================",
        flush=True,
    )

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

    # 1. 官方 API 必須完整成功
    official_names = load_official_chinese_names()

    # 2. 所有中文名稱一次驗證
    validate_all_chinese_names(
        universe,
        analysis,
        official_names,
    )

    # 3. 建立公開資料
    public = {}
    internal = {}
    missing_analysis = []

    for symbol, universe_record in universe.items():

        if symbol not in analysis:
            missing_analysis.append(symbol)
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

    if missing_analysis:
        print(
            "WARNING：Universe 有商品未出現在 Analysis："
            f"{len(missing_analysis)} 檔",
            flush=True,
        )
        print(
            "缺漏代號（最多 30 檔）："
            + ", ".join(missing_analysis[:30]),
            flush=True,
        )

    if not public:
        raise RuntimeError(
            "沒有任何 Universe / Analysis "
            "成功建立 UI record"
        )

    # --------------------------------------------------------
    # TODAY PICKS
    # --------------------------------------------------------

    candidates = extract_candidate_symbols(
        analysis_root
    )

    today_picks = []

    for symbol in candidates:

        if symbol not in public:
            continue

        if public[symbol]["instrument_type"] != "stock":
            continue

        today_picks.append(symbol)

    if not today_picks:

        for symbol in public:

            if public[symbol]["instrument_type"] != "stock":
                continue

            if is_explicit_candidate(
                internal[symbol]
            ):
                today_picks.append(symbol)

    def rank_key(symbol: str):
        record = public[symbol]

        return (
            STRENGTH_RANK.get(
                record["strength"],
                0,
            ),
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
            or record["recommendation"]
            in BUYABLE_RECOMMENDATIONS
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
            or record["recommendation"]
            in BUYABLE_RECOMMENDATIONS
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

    # 4. 寫入前驗證
    validate_ui_data(ui_data)

    # 5. Atomic write
    atomic_write_json(
        OUTPUT_FILE,
        ui_data,
    )

    # 6. 寫入後重新讀取驗證
    read_back = load_json(
        OUTPUT_FILE
    )

    validate_ui_data(read_back)

    print(
        "========================================",
        flush=True,
    )
    print(
        "UI DATA BUILD PASS",
        flush=True,
    )
    print(
        "========================================",
        flush=True,
    )
    print(f"Version：{VERSION}")
    print(f"Universe：{len(universe)}")
    print(f"Analysis：{len(analysis)}")
    print(f"未匹配 Analysis：{len(missing_analysis)}")
    print(f"官方中文名稱：{len(official_names)}")
    print(f"UI stocks：{len(public)}")
    print(f"Today Picks：{len(today_picks)}")
    print(f"Top10：{len(top10)}")
    print(f"ETF：{len(etf_symbols)}")
    print(f"Bond：{len(bond_symbols)}")
    print("中文名稱 validation PASS")
    print("商品分類 validation PASS")
    print("read-back validation PASS")
    print(
        "========================================",
        flush=True,
    )


if __name__ == "__main__":
    main()
