#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 - fetch_market.py
============================================================

V3.0

核心契約
------------------------------------------------------------
1. Data/universe.json 是唯一 Universe 來源
2. 只處理 status == "active"
3. common-stock Universe 用於價格 coverage 驗證
4. Data/prices/manifest.json 是價格 shard 的主要索引
5. 不假設 shard 必須是 data / rows / prices 單一固定結構
6. 價格 shard 採遞迴 schema detection
7. 只接受可驗證的：
      code + date + close
8. 不使用任何非官方市場資料 fallback
9. TAIEX 使用 TWSE 官方資料
10. TWSE 三大法人使用官方 T86
11. TPEx 三大法人使用官方 OpenAPI
12. 官方資料不足時標記 unavailable / partial
13. 不偽造、不補造法人資料
14. Data/market.json 原子寫入
15. 最終 validation 嚴格執行
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


# ============================================================
# PATHS
# ============================================================

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"
PRICES_DIR = DATA_DIR / "prices"
MARKET_FILE = DATA_DIR / "market.json"


# ============================================================
# OFFICIAL ENDPOINTS
# ============================================================

TWSE_MI_INDEX_URL = (
    "https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX"
)

TWSE_MI_5MINS_HIST_URL = (
    "https://openapi.twse.com.tw/v1/indicesReport/MI_5MINS_HIST"
)

TWSE_MI_5MINS_HIST_RWD_URL = (
    "https://www.twse.com.tw/indicesReport/MI_5MINS_HIST"
)

TWSE_T86_URL = (
    "https://www.twse.com.tw/rwd/zh/fund/T86"
)

TPEX_3INSTI_URL = (
    "https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading"
)


# ============================================================
# CONSTANTS
# ============================================================

TIMEOUT = 30
RETRIES = 3

MIN_PRICE = 0.000001
MAX_PRICE = 1_000_000_000

COMMON_STOCK_MIN_CODE_LEN = 4
COMMON_STOCK_MAX_CODE_LEN = 6

DATE_KEYS = (
    "date",
    "Date",
    "日期",
    "交易日期",
    "資料日期",
    "交易日",
)

CODE_KEYS = (
    "code",
    "Code",
    "stock_code",
    "stockCode",
    "symbol",
    "Symbol",
    "證券代號",
    "證券碼",
)

CLOSE_KEYS = (
    "close",
    "Close",
    "CLOSE",
    "收盤價",
    "收盤",
    "close_price",
    "closePrice",
)

PRICE_RECORD_KEYS = (
    "data",
    "rows",
    "prices",
    "records",
    "items",
    "result",
    "results",
    "list",
)


# ============================================================
# TPEX OFFICIAL FIELD ALIASES
# ============================================================

TPEX_CODE_KEYS = [
    "SecuritiesCompanyCode",
    "SecuritiesCode",
    "Code",
    "代號",
    "證券代號",
]

TPEX_DATE_KEYS = [
    "Date",
    "date",
    "資料日期",
    "交易日期",
    "日期",
]

TPEX_FOREIGN_NET_KEYS = [
    (
        "Foreign Investors include Mainland Area Investors "
        "(Foreign Dealers excluded)-Difference"
    ),
    (
        "Foreign Investors include Mainland Area Investors "
        "(Foreign Dealers excluded) - Difference"
    ),
    "ForeignInvestorNet",
    "Foreign_Investor_Net",
]

TPEX_TRUST_NET_KEYS = [
    "SecuritiesInvestmentTrustCompanies-Difference",
    "Securities Investment Trust Companies-Difference",
    "InvestmentTrustNet",
    "Investment_Trust_Net",
]

TPEX_DEALER_NET_KEYS = [
    "Dealers-Difference",
    "Dealers - Difference",
    "DealerNet",
    "Dealers_Net",
]

TPEX_TOTAL_NET_KEYS = [
    "TotalDifference",
    "Total Difference",
    "TotalNet",
    "Total_Net",
]


# ============================================================
# BASIC HELPERS
# ============================================================

def log(message: str = "") -> None:
    print(message, flush=True)


def fail(message: str) -> None:
    raise RuntimeError(message)


def is_finite_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False

    try:
        number = float(value)
    except (TypeError, ValueError):
        return False

    return math.isfinite(number)


def to_number(value: Any) -> float | None:
    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None

    text = str(value).strip()

    if not text:
        return None

    text = text.replace(",", "")
    text = text.replace(" ", "")
    text = text.replace("\u3000", "")

    if text in {"-", "--", "N/A", "NA", "null", "None"}:
        return None

    try:
        number = float(text)
    except ValueError:
        return None

    return number if math.isfinite(number) else None


def normalize_key(value: Any) -> str:
    if value is None:
        return ""

    text = str(value)

    text = text.replace("\ufeff", "")
    text = text.replace("\r", "")
    text = text.replace("\n", "")
    text = text.replace("\t", "")
    text = text.replace("\u3000", " ")

    return text.strip()


def normalize_code(value: Any) -> str | None:
    if value is None:
        return None

    text = normalize_key(value)

    if not text:
        return None

    if text.endswith(".TW"):
        text = text[:-3]

    if text.endswith(".TWO"):
        text = text[:-4]

    text = text.strip()

    if not re.fullmatch(
        rf"[A-Za-z0-9]{{{COMMON_STOCK_MIN_CODE_LEN},{COMMON_STOCK_MAX_CODE_LEN}}}",
        text,
    ):
        return None

    return text


# ============================================================
# DATE PARSER
# ============================================================

def parse_date(value: Any) -> str | None:
    if value is None:
        return None

    text = normalize_key(value)

    if not text:
        return None

    # YYYY-MM-DD
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)

    if m:
        try:
            dt = datetime(
                int(m.group(1)),
                int(m.group(2)),
                int(m.group(3)),
            )
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return None

    # YYYY/MM/DD
    m = re.fullmatch(r"(\d{4})/(\d{1,2})/(\d{1,2})", text)

    if m:
        try:
            dt = datetime(
                int(m.group(1)),
                int(m.group(2)),
                int(m.group(3)),
            )
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return None

    # YYYYMMDD
    m = re.fullmatch(r"(\d{8})", text)

    if m:
        try:
            dt = datetime.strptime(text, "%Y%m%d")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return None

    # ROC YYYYMMDD，例如 1150903
    m = re.fullmatch(r"(\d{3})(\d{2})(\d{2})", text)

    if m:
        try:
            year = int(m.group(1)) + 1911
            month = int(m.group(2))
            day = int(m.group(3))

            dt = datetime(year, month, day)

            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return None

    # datetime / ISO datetime
    if "T" in text:
        candidate = text.split("T", 1)[0]

        parsed = parse_date(candidate)

        if parsed:
            return parsed

    # Timestamp-like text
    m = re.search(
        r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})",
        text,
    )

    if m:
        try:
            dt = datetime(
                int(m.group(1)),
                int(m.group(2)),
                int(m.group(3)),
            )
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return None

    return None


# ============================================================
# JSON HTTP
# ============================================================

def request_json(url: str, params: dict[str, Any] | None = None) -> Any:
    import requests

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(compatible; TW-Stock-AI-Scanner/3.0)"
        ),
        "Accept": "application/json,text/plain,*/*",
    }

    last_error: Exception | None = None

    for attempt in range(1, RETRIES + 1):
        try:
            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=TIMEOUT,
            )

            response.raise_for_status()

            return response.json()

        except Exception as exc:
            last_error = exc

            if attempt < RETRIES:
                time.sleep(attempt)

    raise RuntimeError(
        f"Official API request failed: {url}: {last_error}"
    )


# ============================================================
# GENERIC DICT VALUE LOOKUP
# ============================================================

def first_value(
    obj: dict[str, Any],
    keys: list[str] | tuple[str, ...],
) -> Any:
    if not isinstance(obj, dict):
        return None

    # Exact key first
    for key in keys:
        if key in obj:
            return obj[key]

    # Normalized key matching
    normalized = {
        normalize_key(k): v
        for k, v in obj.items()
    }

    for key in keys:
        normalized_key = normalize_key(key)

        if normalized_key in normalized:
            return normalized[normalized_key]

    return None


# ============================================================
# UNIVERSE
# ============================================================

def load_universe() -> list[str]:
    if not UNIVERSE_FILE.exists():
        fail(f"Universe file not found: {UNIVERSE_FILE}")

    with UNIVERSE_FILE.open(
        "r",
        encoding="utf-8",
    ) as fh:
        payload = json.load(fh)

    if not isinstance(payload, dict):
        fail("universe.json must be an object")

    stocks = payload.get("stocks")

    if not isinstance(stocks, dict):
        fail("universe.json stocks must be a dict")

    result: list[str] = []

    for code, info in stocks.items():
        if not isinstance(info, dict):
            continue

        status = str(
            info.get("status", "")
        ).strip().lower()

        if status != "active":
            continue

        normalized = normalize_code(code)

        if normalized:
            result.append(normalized)

    result = sorted(set(result))

    if not result:
        fail("No active stocks found in universe.json")

    return result


# ============================================================
# MANIFEST
# ============================================================

def load_price_manifest() -> list[str]:
    manifest_file = PRICES_DIR / "manifest.json"

    if not manifest_file.exists():
        fail(
            f"Price manifest not found: {manifest_file}"
        )

    with manifest_file.open(
        "r",
        encoding="utf-8",
    ) as fh:
        payload = json.load(fh)

    candidates: list[Any] = []

    if isinstance(payload, list):
        candidates = payload

    elif isinstance(payload, dict):
        for key in (
            "shards",
            "files",
            "parts",
            "prices",
            "data",
        ):
            value = payload.get(key)

            if isinstance(value, list):
                candidates = value
                break

    result: list[str] = []

    for item in candidates:
        if isinstance(item, str):
            name = item.strip()

        elif isinstance(item, dict):
            name = ""

            for key in (
                "file",
                "filename",
                "path",
                "name",
                "shard",
            ):
                if item.get(key):
                    name = str(item[key]).strip()
                    break
        else:
            continue

        if not name:
            continue

        path = Path(name)

        if path.name == "manifest.json":
            continue

        # Manifest normally stores filenames.
        # Prevent escaping Data/prices.
        if path.is_absolute():
            continue

        if ".." in path.parts:
            continue

        result.append(path.as_posix())

    result = list(dict.fromkeys(result))

    if not result:
        # Do not guess silently.
        # As a controlled compatibility path, inspect actual directory
        # only when manifest has no shard list.
        discovered = sorted(
            p.name
            for p in PRICES_DIR.glob("*.json")
            if p.name != "manifest.json"
        )

        if discovered:
            return discovered

        fail("manifest.json contains no usable price shards")

    return result


# ============================================================
# PRICE SHARD RECURSIVE EXTRACTION
# ============================================================

def dict_has_any_key(
    obj: dict[str, Any],
    keys: tuple[str, ...],
) -> bool:
    if not isinstance(obj, dict):
        return False

    actual = {
        normalize_key(k)
        for k in obj.keys()
    }

    expected = {
        normalize_key(k)
        for k in keys
    }

    return bool(actual & expected)


def looks_like_price_record(
    obj: dict[str, Any],
) -> bool:
    if not isinstance(obj, dict):
        return False

    has_code = dict_has_any_key(
        obj,
        CODE_KEYS,
    )

    has_date = dict_has_any_key(
        obj,
        DATE_KEYS,
    )

    has_close = dict_has_any_key(
        obj,
        CLOSE_KEYS,
    )

    return has_code and has_date and has_close


def iter_candidate_records(
    value: Any,
    depth: int = 0,
):
    """
    遞迴搜尋價格 row。

    不限制 payload 必須是：
        data
        rows
        prices

    只要最終能找到：
        code + date + close
    就視為 candidate record。
    """

    if depth > 30:
        return

    if isinstance(value, dict):

        if looks_like_price_record(value):
            yield value

        for key, child in value.items():

            # 優先走常見資料容器
            if normalize_key(key) in {
                normalize_key(k)
                for k in PRICE_RECORD_KEYS
            }:
                yield from iter_candidate_records(
                    child,
                    depth + 1,
                )
            else:
                # 其他 nested object 也繼續搜尋
                if isinstance(child, (dict, list)):
                    yield from iter_candidate_records(
                        child,
                        depth + 1,
                    )

    elif isinstance(value, list):

        for child in value:
            yield from iter_candidate_records(
                child,
                depth + 1,
            )


# ============================================================
# PRICE RECORD NORMALIZATION
# ============================================================

def normalize_price_record(
    record: dict[str, Any],
) -> dict[str, Any] | None:

    code = normalize_code(
        first_value(
            record,
            CODE_KEYS,
        )
    )

    if not code:
        return None

    date = parse_date(
        first_value(
            record,
            DATE_KEYS,
        )
    )

    if not date:
        return None

    close = to_number(
        first_value(
            record,
            CLOSE_KEYS,
        )
    )

    if close is None:
        return None

    if not (
        MIN_PRICE
        <= close
        <= MAX_PRICE
    ):
        return None

    return {
        "code": code,
        "date": date,
        "close": close,
    }


# ============================================================
# PRICE SHARDS
# ============================================================

def load_price_shards(
    universe: list[str],
) -> dict[str, Any]:

    manifest_shards = load_price_manifest()

    log(
        f"Price shards: {len(manifest_shards)} files"
    )

    all_rows: list[dict[str, Any]] = []

    shard_stats: list[dict[str, Any]] = []

    valid_shards = 0
    malformed_shards = 0
    raw_rows = 0

    for shard_name in manifest_shards:

        shard_path = PRICES_DIR / shard_name

        stat = {
            "file": shard_name,
            "exists": shard_path.exists(),
            "raw_rows": 0,
            "valid_rows": 0,
            "status": "malformed",
        }

        if not shard_path.exists():
            malformed_shards += 1
            stat["error"] = "file_not_found"
            shard_stats.append(stat)
            continue

        try:
            with shard_path.open(
                "r",
                encoding="utf-8",
            ) as fh:
                payload = json.load(fh)

        except Exception as exc:
            malformed_shards += 1
            stat["error"] = (
                f"json_error: {exc}"
            )
            shard_stats.append(stat)
            continue

        try:
            candidates = list(
                iter_candidate_records(payload)
            )

        except Exception as exc:
            malformed_shards += 1
            stat["error"] = (
                f"schema_error: {exc}"
            )
            shard_stats.append(stat)
            continue

        stat["raw_rows"] = len(candidates)
        raw_rows += len(candidates)

        seen_in_shard: set[
            tuple[str, str]
        ] = set()

        for candidate in candidates:

            normalized = normalize_price_record(
                candidate
            )

            if normalized is None:
                continue

            identity = (
                normalized["code"],
                normalized["date"],
            )

            # 同 shard 重複資料只保留一次
            if identity in seen_in_shard:
                continue

            seen_in_shard.add(identity)

            all_rows.append(normalized)
            stat["valid_rows"] += 1

        if stat["valid_rows"] > 0:
            valid_shards += 1
            stat["status"] = "valid"
        else:
            malformed_shards += 1

        shard_stats.append(stat)

    log(f"valid shards: {valid_shards}")
    log(f"malformed shards: {malformed_shards}")
    log(f"raw price rows: {raw_rows}")
    log(f"valid rows: {len(all_rows)}")

    if not all_rows:
        raise RuntimeError(
            "No valid price rows found"
        )

    # 全域去重
    dedup: dict[
        tuple[str, str],
        dict[str, Any],
    ] = {}

    for row in all_rows:
        key = (
            row["code"],
            row["date"],
        )

        dedup[key] = row

    all_rows = list(dedup.values())

    # Coverage
    covered = {
        row["code"]
        for row in all_rows
        if row["code"] in set(universe)
    }

    log(
        f"stock coverage: "
        f"{len(covered)}/{len(universe)}"
    )

    return {
        "rows": all_rows,
        "stats": shard_stats,
        "valid_shards": valid_shards,
        "malformed_shards": malformed_shards,
        "raw_rows": raw_rows,
    }


# ============================================================
# TAIEX VALUE EXTRACTION
# ============================================================

TAIEX_KEYS = (
    "TAIEX",
    "tai_ex",
    "taiex",
    "index",
    "Index",
    "value",
    "Value",
    "收盤指數",
    "指數",
)


def extract_taiex_values(
    payload: Any,
    result: list[float],
    depth: int = 0,
) -> None:

    if depth > 30:
        return

    if isinstance(payload, dict):

        for key, value in payload.items():

            normalized_key = normalize_key(key)

            if normalized_key in {
                normalize_key(k)
                for k in TAIEX_KEYS
            }:
                number = to_number(value)

                if (
                    number is not None
                    and 1_000 <= number <= 100_000
                ):
                    result.append(number)

            if isinstance(value, (dict, list)):
                extract_taiex_values(
                    value,
                    result,
                    depth + 1,
                )

    elif isinstance(payload, list):

        for item in payload:
            extract_taiex_values(
                item,
                result,
                depth + 1,
            )


# ============================================================
# TAIEX CURRENT
# ============================================================

def fetch_taiex() -> float | None:

    urls = [
        TWSE_MI_5MINS_HIST_URL,
        TWSE_MI_INDEX_URL,
    ]

    for url in urls:

        try:
            payload = request_json(url)

            values: list[float] = []

            extract_taiex_values(
                payload,
                values,
            )

            if values:
                return values[-1]

        except Exception:
            continue

    return None


# ============================================================
# TAIEX HISTORY
# ============================================================

def build_taiex_history(
    price_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:

    dates = sorted(
        {
            row["date"]
            for row in price_rows
        }
    )

    # 保留日期 skeleton。
    # 不將股票價格冒充 TAIEX。
    return [
        {
            "date": date,
            "close": None,
        }
        for date in dates
    ]


# ============================================================
# INDICATORS
# ============================================================

def calculate_ma(
    values: list[float],
    period: int,
) -> float | None:

    if len(values) < period:
        return None

    return statistics.mean(
        values[-period:]
    )


def calculate_rsi(
    values: list[float],
    period: int = 14,
) -> float | None:

    if len(values) < period + 1:
        return None

    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    recent = changes[-period:]

    gains = [
        max(change, 0)
        for change in recent
    ]

    losses = [
        max(-change, 0)
        for change in recent
    ]

    avg_gain = statistics.mean(gains)
    avg_loss = statistics.mean(losses)

    if avg_loss == 0:
        if avg_gain == 0:
            return 50.0
        return 100.0

    rs = avg_gain / avg_loss

    return 100 - (
        100 / (1 + rs)
    )


def calculate_atr_proxy_pct(
    values: list[float],
    period: int = 14,
) -> float | None:

    if len(values) < period + 1:
        return None

    changes = []

    for i in range(1, len(values)):
        previous = values[i - 1]

        if previous == 0:
            continue

        changes.append(
            abs(values[i] - previous)
            / previous
            * 100
        )

    if len(changes) < period:
        return None

    return statistics.mean(
        changes[-period:]
    )


# ============================================================
# TWSE T86
# ============================================================

def parse_twse_t86_payload(
    payload: Any,
) -> tuple[float, float] | None:

    rows: list[Any] = []

    if isinstance(payload, dict):

        for key in (
            "data",
            "tables",
            "aaData",
            "rows",
        ):
            value = payload.get(key)

            if isinstance(value, list):
                rows.extend(value)

    elif isinstance(payload, list):
        rows = payload

    foreign_total = 0.0
    trust_total = 0.0

    found = False

    for row in rows:

        if isinstance(row, dict):

            foreign = None
            trust = None

            for key in (
                "外陸資買賣超股數(不含外資自營商)",
                "外資及陸資(不含外資自營商)-買賣超股數",
                "外資及陸資買賣超股數",
                "foreign_net",
            ):
                if key in row:
                    foreign = to_number(
                        row[key]
                    )
                    break

            for key in (
                "投信買賣超股數",
                "投信-買賣超股數",
                "investment_trust_net",
            ):
                if key in row:
                    trust = to_number(
                        row[key]
                    )
                    break

            if foreign is not None:
                foreign_total += foreign
                found = True

            if trust is not None:
                trust_total += trust
                found = True

        elif isinstance(row, (list, tuple)):

            # TWSE T86 常見欄位位置
            if len(row) > 4:
                foreign = to_number(row[4])

                if foreign is not None:
                    foreign_total += foreign
                    found = True

            if len(row) > 7:
                trust = to_number(row[7])

                if trust is not None:
                    trust_total += trust
                    found = True

    if not found:
        return None

    return (
        foreign_total,
        trust_total,
    )


def fetch_twse_institutional(
    trading_date: str,
) -> dict[str, Any]:

    roc_date = datetime.strptime(
        trading_date,
        "%Y-%m-%d",
    )

    roc_date_text = (
        f"{roc_date.year - 1911:03d}"
        f"{roc_date.month:02d}"
        f"{roc_date.day:02d}"
    )

    params_list = [
        {
            "date": trading_date.replace("-", ""),
            "selectType": "ALL",
        },
        {
            "date": roc_date_text,
            "selectType": "ALL",
        },
    ]

    last_error: Exception | None = None

    for params in params_list:

        try:
            payload = request_json(
                TWSE_T86_URL,
                params=params,
            )

            parsed = parse_twse_t86_payload(
                payload
            )

            if parsed is None:
                continue

            foreign, trust = parsed

            return {
                "status": "available",
                "source": "TWSE_T86",
                "foreign": foreign,
                "investment_trust": trust,
            }

        except Exception as exc:
            last_error = exc

    return {
        "status": "unavailable",
        "source": "TWSE_T86",
        "foreign": None,
        "investment_trust": None,
        "error": str(last_error)
        if last_error
        else "No valid TWSE T86 data",
    }


# ============================================================
# TPEX INSTITUTIONAL
# ============================================================

def fetch_tpex_institutional(
    trading_date: str,
) -> dict[str, Any]:

    payload = request_json(
        TPEX_3INSTI_URL
    )

    if not isinstance(payload, list):
        raise RuntimeError(
            "TPEx institutional payload is not a list"
        )

    if not payload:
        raise RuntimeError(
            "TPEx institutional payload is empty"
        )

    foreign_total = 0.0
    trust_total = 0.0

    valid_rows = 0
    date_mismatch = 0

    for item in payload:

        if not isinstance(item, dict):
            continue

        code = normalize_code(
            first_value(
                item,
                TPEX_CODE_KEYS,
            )
        )

        if not code:
            continue

        raw_date = first_value(
            item,
            TPEX_DATE_KEYS,
        )

        if raw_date is not None:

            parsed_date = parse_date(
                raw_date
            )

            if (
                parsed_date is not None
                and parsed_date != trading_date
            ):
                date_mismatch += 1
                continue

        foreign = to_number(
            first_value(
                item,
                TPEX_FOREIGN_NET_KEYS,
            )
        )

        trust = to_number(
            first_value(
                item,
                TPEX_TRUST_NET_KEYS,
            )
        )

        dealer = to_number(
            first_value(
                item,
                TPEX_DEALER_NET_KEYS,
            )
        )

        total = to_number(
            first_value(
                item,
                TPEX_TOTAL_NET_KEYS,
            )
        )

        # foreign + trust 是本系統需要的核心值
        # dealer / total 同時要求可解析，
        # 用於確認 schema 沒有錯位。
        if foreign is None:
            continue

        if trust is None:
            continue

        if dealer is None:
            continue

        if total is None:
            continue

        foreign_total += foreign
        trust_total += trust

        valid_rows += 1

    if valid_rows == 0:

        if date_mismatch > 0:
            raise RuntimeError(
                "TPEx institutional data date mismatch"
            )

        raise RuntimeError(
            "TPEx institutional parsed zero valid rows"
        )

    return {
        "status": "available",
        "source": "TPEx_3INSTI",
        "foreign": foreign_total,
        "investment_trust": trust_total,
        "valid_rows": valid_rows,
    }


# ============================================================
# INSTITUTIONAL COMBINATION
# ============================================================

def build_institutional(
    trading_date: str,
) -> dict[str, Any]:

    twse = fetch_twse_institutional(
        trading_date
    )

    try:
        tpex = fetch_tpex_institutional(
            trading_date
        )

    except Exception as exc:
        tpex = {
            "status": "unavailable",
            "source": "TPEx_3INSTI",
            "foreign": None,
            "investment_trust": None,
            "error": str(exc),
        }

    twse_available = (
        twse.get("status") == "available"
    )

    tpex_available = (
        tpex.get("status") == "available"
    )

    if twse_available and tpex_available:
        status = "complete"

    elif twse_available or tpex_available:
        status = "partial"

    else:
        status = "unavailable"

    return {
        "status": status,
        "trading_date": trading_date,
        "twse": twse,
        "tpex": tpex,
    }


# ============================================================
# STOCK SERIES
# ============================================================

def build_stock_series(
    rows: list[dict[str, Any]],
    universe: list[str],
) -> dict[str, dict[str, Any]]:

    universe_set = set(universe)

    grouped: dict[
        str,
        list[dict[str, Any]]
    ] = {}

    for row in rows:

        code = row["code"]

        if code not in universe_set:
            continue

        grouped.setdefault(
            code,
            []
        ).append(row)

    result: dict[
        str,
        dict[str, Any]
    ] = {}

    for code, stock_rows in grouped.items():

        stock_rows.sort(
            key=lambda x: x["date"]
        )

        closes = [
            float(row["close"])
            for row in stock_rows
        ]

        latest = (
            stock_rows[-1]
            if stock_rows
            else None
        )

        result[code] = {
            "latest": latest,
            "history_count": len(stock_rows),
            "ma5": calculate_ma(
                closes,
                5,
            ),
            "ma20": calculate_ma(
                closes,
                20,
            ),
            "rsi14": calculate_rsi(
                closes,
                14,
            ),
            "atr14_pct": calculate_atr_proxy_pct(
                closes,
                14,
            ),
        }

    return result


# ============================================================
# MARKET STATUS
# ============================================================

def determine_trading_date(
    rows: list[dict[str, Any]],
) -> str:

    dates = sorted(
        {
            row["date"]
            for row in rows
        }
    )

    if not dates:
        fail(
            "Unable to determine trading date"
        )

    return dates[-1]


# ============================================================
# MARKET JSON
# ============================================================

def build_market(
    universe: list[str],
    price_data: dict[str, Any],
    trading_date: str,
) -> dict[str, Any]:

    price_rows = price_data["rows"]

    stock_series = build_stock_series(
        price_rows,
        universe,
    )

    covered = sorted(
        set(stock_series)
        & set(universe)
    )

    coverage = (
        len(covered)
        / len(universe)
        if universe
        else 0
    )

    taiex = fetch_taiex()

    taiex_history = build_taiex_history(
        price_rows
    )

    institutional = build_institutional(
        trading_date
    )

    return {
        "version": "3.0",
        "generated_at": datetime.utcnow().isoformat()
        + "Z",
        "trading_date": trading_date,

        "market_status": {
            "price": (
                "available"
                if price_rows
                else "unavailable"
            ),
            "taiex": (
                "available"
                if taiex is not None
                else "unavailable"
            ),
            "institutional": institutional["status"],
        },

        "universe": {
            "active_common_stocks": len(universe),
            "price_coverage": len(covered),
            "coverage_ratio": coverage,
        },

        "prices": {
            "rows": price_rows,
            "shards": price_data["stats"],
            "valid_shards": price_data[
                "valid_shards"
            ],
            "malformed_shards": price_data[
                "malformed_shards"
            ],
            "raw_rows": price_data[
                "raw_rows"
            ],
            "valid_rows": len(price_rows),
        },

        "taiex": {
            "current": taiex,
            "history": taiex_history,
        },

        "institutional": institutional,

        "stocks": stock_series,
    }


# ============================================================
# VALIDATION
# ============================================================

def validate_market(
    market: dict[str, Any],
    universe: list[str],
) -> None:

    if not isinstance(
        market,
        dict,
    ):
        fail(
            "market.json must be an object"
        )

    if market.get("version") != "3.0":
        fail(
            "market.json version mismatch"
        )

    trading_date = market.get(
        "trading_date"
    )

    if not parse_date(trading_date):
        fail(
            "Invalid trading_date"
        )

    prices = market.get(
        "prices"
    )

    if not isinstance(
        prices,
        dict,
    ):
        fail(
            "prices must be an object"
        )

    rows = prices.get(
        "rows"
    )

    if not isinstance(
        rows,
        list,
    ):
        fail(
            "prices.rows must be a list"
        )

    if not rows:
        fail(
            "prices.rows is empty"
        )

    malformed_shards = prices.get(
        "malformed_shards",
        0,
    )

    valid_shards = prices.get(
        "valid_shards",
        0,
    )

    if valid_shards <= 0:
        fail(
            "No valid price shards"
        )

    if len(rows) <= 0:
        fail(
            "No valid price rows"
        )

    covered = {
        row.get("code")
        for row in rows
        if isinstance(row, dict)
        and row.get("code") in set(universe)
    }

    if not covered:
        fail(
            "No Universe stock has valid price data"
        )

    # 至少要求主要 common-stock coverage。
    # 不硬編碼 1943，避免 Universe 未來合法變動。
    coverage_ratio = (
        len(covered)
        / len(universe)
        if universe
        else 0
    )

    if coverage_ratio < 0.90:
        fail(
            "Price coverage below 90%: "
            f"{len(covered)}/{len(universe)}"
        )

    taiex = market.get(
        "taiex"
    )

    if not isinstance(
        taiex,
        dict,
    ):
        fail(
            "taiex must be an object"
        )

    institutional = market.get(
        "institutional"
    )

    if not isinstance(
        institutional,
        dict,
    ):
        fail(
            "institutional must be an object"
        )

    institutional_status = institutional.get(
        "status"
    )

    if institutional_status not in {
        "complete",
        "partial",
        "unavailable",
    }:
        fail(
            "Invalid institutional status"
        )

    twse_status = institutional.get(
        "twse",
        {}
    ).get(
        "status"
    )

    tpex_status = institutional.get(
        "tpex",
        {}
    ).get(
        "status"
    )

    if institutional_status == "complete":

        if twse_status != "available":
            fail(
                "Institutional complete but TWSE unavailable"
            )

        if tpex_status != "available":
            fail(
                "Institutional complete but TPEx unavailable"
            )

    # 驗證所有 price rows
    for index, row in enumerate(rows):

        if not isinstance(
            row,
            dict,
        ):
            fail(
                f"Invalid price row at {index}"
            )

        code = normalize_code(
            row.get("code")
        )

        if not code:
            fail(
                f"Invalid price code at row {index}"
            )

        date = parse_date(
            row.get("date")
        )

        if not date:
            fail(
                f"Invalid price date at row {index}"
            )

        close = to_number(
            row.get("close")
        )

        if close is None:
            fail(
                f"Invalid close at row {index}"
            )

    log(
        "VALIDATE MARKET.JSON V3.0 PASS"
    )


# ============================================================
# ATOMIC WRITE
# ============================================================

def atomic_write_json(
    path: Path,
    payload: dict[str, Any],
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = path.with_suffix(
        path.suffix + ".tmp"
    )

    with temporary.open(
        "w",
        encoding="utf-8",
    ) as fh:

        json.dump(
            payload,
            fh,
            ensure_ascii=False,
            indent=2,
        )

        fh.write("\n")

        fh.flush()

        os.fsync(
            fh.fileno()
        )

    os.replace(
        temporary,
        path,
    )


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    log("========================================")
    log("FETCH MARKET V3.0")
    log("========================================")

    try:

        universe = load_universe()

        log(
            "Active common-stock universe: "
            f"{len(universe)}"
        )

        price_data = load_price_shards(
            universe
        )

        price_rows = price_data[
            "rows"
        ]

        trading_date = determine_trading_date(
            price_rows
        )

        log(
            "latest trading date: "
            f"{trading_date}"
        )

        market = build_market(
            universe,
            price_data,
            trading_date,
        )

        # ----------------------------------------------------
        # Diagnostic output
        # ----------------------------------------------------

        taiex = market["taiex"]

        if taiex.get("current") is not None:
            log(
                "TAIEX: "
                f"{taiex['current']}"
            )
        else:
            log(
                "TAIEX: unavailable"
            )

        institutional = market[
            "institutional"
        ]

        log(
            "TWSE T86: "
            f"{institutional['twse']['status']}"
        )

        log(
            "TPEx institutional: "
            f"{institutional['tpex']['status']}"
        )

        if (
            institutional["tpex"]["status"]
            == "available"
        ):
            log(
                "TPEx foreign(ex-dealer): "
                f"{institutional['tpex']['foreign']}"
            )

            log(
                "TPEx investment trust: "
                f"{institutional['tpex']['investment_trust']}"
            )
        else:
            log(
                "TPEx institutional unavailable: "
                f"{institutional['tpex'].get('error', '')}"
            )

        # ----------------------------------------------------
        # Final validation
        # ----------------------------------------------------

        validate_market(
            market,
            universe,
        )

        atomic_write_json(
            MARKET_FILE,
            market,
        )

        if not MARKET_FILE.exists():
            fail(
                "Data/market.json was not created"
            )

        log(
            "✓ Data/market.json"
        )

        log("========================================")
        log("FETCH MARKET V3.0 TEST PASSED")
        log("========================================")

        return 0

    except Exception as exc:

        log("")
        log("========================================")
        log("FETCH MARKET V3.0 FAILED")
        log("========================================")
        log(str(exc))

        return 1


if __name__ == "__main__":
    sys.exit(main())