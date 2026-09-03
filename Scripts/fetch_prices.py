#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 - fetch_prices.py

資料鏈：

Data/universe.json
        ↓
官方 TWSE / TPEx 批次資料
        ↓
Yahoo 僅補官方缺口
        ↓
Data/prices/
        ↓
manifest + shards
        ↓
FINAL VALIDATION

核心契約：
1. Universe 是唯一 symbol 來源。
2. 不因價格資料建立 / 修改 Universe。
3. TWSE 使用 MI_INDEX tables[].fields + tables[].data。
4. TPEx 使用官方批次資料，JSON 失敗再 HTML。
5. TPEx 全市場資料只允許 Universe symbol 通過。
6. listed_date > end_date => not_started。
7. not_started 絕對不呼叫 Yahoo。
8. 新上市商品從 listed_date 開始計算。
9. 官方來源「整個市場 / 整個期間完全失效」=> FAIL。
10. 官方資料正常但單一商品沒有資料 => Yahoo 補洞。
11. Yahoo 也沒有資料 => no_history，不讓整批失敗。
12. 官方資料優先，Yahoo 永遠不能覆蓋官方資料。
13. shard / manifest / final validation 必須一致。
14. atomic write。
15. read-back validation。
16. 每個 shard 寫入後必須實際 read-back。
17. FINAL VALIDATION 必須實際列出所有 shard 檔案與股票數。
"""

from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
import time

from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import requests


# ============================================================================
# PATH / CONTRACT
# ============================================================================

ROOT = Path(__file__).resolve().parents[1]

UNIVERSE_FILE = ROOT / "Data" / "universe.json"
OUTPUT_DIR = ROOT / "Data" / "prices"

SCHEMA_VERSION = "prices-v14.0"
VERSION = "PRICE-FETCH-V14.0"

START_DATE = date(2026, 1, 5)

MIN_UNIVERSE = 1
MIN_HISTORY_ROWS = 1

OFFICIAL_COMPLETENESS_THRESHOLD = 0.90

SHARD_SIZE = 250

REQUEST_TIMEOUT = 30
REQUEST_RETRIES = 3
REQUEST_BACKOFF = 1.5
REQUEST_DELAY = 0.10

YAHOO_TIMEOUT = 20
YAHOO_RETRIES = 2
YAHOO_BACKOFF = 1.5

TAIWAN_TZ = timezone(timedelta(hours=8))

TMP_PREFIX = ".prices_build_"

TWSE_DAILY_URL = (
    "https://www.twse.com.tw/exchangeReport/MI_INDEX"
)

TPEX_DAILY_URL = (
    "https://www.tpex.org.tw/web/stock/aftertrading/"
    "otc_quotes_no1430/stk_wn1430_result.php"
)


# ============================================================================
# HTTP
# ============================================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "(compatible; TW-Stock-AI-Scanner/price-fetch)"
        ),
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        "Accept": "*/*",
        "Connection": "keep-alive",
    }
)


def http_get(
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = REQUEST_TIMEOUT,
    retries: int = REQUEST_RETRIES,
) -> requests.Response:

    last_error: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        try:
            response = SESSION.get(
                url,
                params=params,
                timeout=timeout,
            )
            response.raise_for_status()
            return response

        except Exception as exc:
            last_error = exc

            if attempt < retries:
                time.sleep(
                    REQUEST_BACKOFF * attempt
                )

    raise RuntimeError(
        f"HTTP GET failed: {url}: {last_error}"
    )


def http_get_json(
    url: str,
    params: Dict[str, Any],
) -> Any:

    response = http_get(
        url,
        params=params,
    )

    try:
        return response.json()
    except Exception as exc:
        raise RuntimeError(
            f"JSON decode failed: {exc}"
        ) from exc


def http_get_text(
    url: str,
    params: Dict[str, Any],
) -> str:

    response = http_get(
        url,
        params=params,
    )

    encoding = (
        response.encoding
        or response.apparent_encoding
        or "utf-8"
    )

    try:
        return response.content.decode(
            encoding,
            errors="replace",
        )
    except Exception:
        return response.text


# ============================================================================
# NORMALIZATION
# ============================================================================

def normalize_text(value: Any) -> str:
    if value is None:
        return ""

    return (
        str(value)
        .replace("\u3000", " ")
        .replace("\r", " ")
        .replace("\n", " ")
        .strip()
    )


def normalize_header(value: Any) -> str:
    text = normalize_text(value)

    for token in (
        " ",
        "\t",
        "\r",
        "\n",
        "(",
        ")",
        "（",
        "）",
        "[",
        "]",
        "［",
        "］",
        ":",
        "：",
    ):
        text = text.replace(token, "")

    return text


def normalize_symbol(value: Any) -> str:
    return normalize_text(value).upper()


def parse_float(value: Any) -> Optional[float]:

    if value is None:
        return None

    text = (
        normalize_text(value)
        .replace(",", "")
        .replace(" ", "")
    )

    if not text:
        return None

    if text.upper() in {
        "-",
        "--",
        "---",
        "N/A",
        "NA",
        "NULL",
        "X",
    }:
        return None

    try:
        result = float(text)

        if not math.isfinite(result):
            return None

        return result

    except (TypeError, ValueError):
        return None


def parse_int(value: Any) -> Optional[int]:
    number = parse_float(value)

    if number is None:
        return None

    return int(number)


def parse_date(value: Any) -> Optional[date]:

    if value is None:
        return None

    text = normalize_text(value)

    if not text:
        return None

    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y.%m.%d",
        "%Y%m%d",
    ):
        try:
            return datetime.strptime(
                text,
                fmt,
            ).date()
        except ValueError:
            pass

    parts = (
        text
        .replace(".", "/")
        .replace("-", "/")
        .split("/")
    )

    if len(parts) == 3:
        try:
            y, m, d = (
                int(x)
                for x in parts
            )

            if y < 1911:
                y += 1911

            return date(y, m, d)

        except ValueError:
            pass

    return None


def today_taiwan() -> date:
    return datetime.now(TAIWAN_TZ).date()


def roc_date(d: date) -> str:
    return (
        f"{d.year - 1911:03d}/"
        f"{d.month:02d}/"
        f"{d.day:02d}"
    )


def is_weekday(d: date) -> bool:
    return d.weekday() < 5


def date_range(
    start_date: date,
    end_date: date,
) -> Iterable[date]:

    current = start_date

    while current <= end_date:
        yield current
        current += timedelta(days=1)


# ============================================================================
# OHLCV
# ============================================================================

def valid_ohlcv_row(
    row: Dict[str, Any],
) -> bool:

    for key in (
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ):
        if key not in row:
            return False

    if not row["date"]:
        return False

    for key in (
        "open",
        "high",
        "low",
        "close",
        "volume",
    ):
        if row[key] is None:
            return False

    for key in (
        "open",
        "high",
        "low",
        "close",
    ):
        if row[key] <= 0:
            return False

    if row["volume"] < 0:
        return False

    return True


def normalize_price_row(
    target_date: date,
    symbol: Any,
    open_value: Any,
    high_value: Any,
    low_value: Any,
    close_value: Any,
    volume_value: Any,
) -> Optional[Dict[str, Any]]:

    normalized_symbol = normalize_symbol(symbol)

    if not normalized_symbol:
        return None

    row = {
        "date": target_date.isoformat(),
        "open": parse_float(open_value),
        "high": parse_float(high_value),
        "low": parse_float(low_value),
        "close": parse_float(close_value),
        "volume": parse_int(volume_value),
    }

    if not valid_ohlcv_row(row):
        return None

    return row


# ============================================================================
# UNIVERSE
# ============================================================================

def load_universe() -> List[Dict[str, Any]]:

    if not UNIVERSE_FILE.exists():
        raise RuntimeError(
            f"Universe file not found: {UNIVERSE_FILE}"
        )

    try:
        payload = json.loads(
            UNIVERSE_FILE.read_text(
                encoding="utf-8"
            )
        )
    except Exception as exc:
        raise RuntimeError(
            f"Unable to read universe: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise RuntimeError(
            "universe.json root must be dict"
        )

    stocks = payload.get("stocks")

    if not isinstance(stocks, dict):
        raise RuntimeError(
            "universe.json stocks must be dict"
        )

    result: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    for key, raw in stocks.items():

        if not isinstance(raw, dict):
            continue

        if raw.get("status") != "active":
            continue

        symbol = normalize_symbol(
            raw.get("symbol") or key
        )

        market = normalize_text(
            raw.get("market")
        ).upper()

        instrument_type = normalize_text(
            raw.get("type")
            or raw.get("instrument_type")
        ).upper()

        if not symbol:
            continue

        if symbol in seen:
            raise RuntimeError(
                f"Duplicate Universe symbol: {symbol}"
            )

        if market not in {
            "TWSE",
            "TPEX",
        }:
            continue

        if instrument_type not in {
            "STOCK",
            "ETF",
        }:
            continue

        listed = parse_date(
            raw.get("listed_date")
        )

        result.append(
            {
                "symbol": symbol,
                "code": symbol,
                "name": normalize_text(
                    raw.get("name")
                ),
                "market": market,
                "type": instrument_type,
                "listed_date": (
                    listed.isoformat()
                    if listed
                    else None
                ),
            }
        )

        seen.add(symbol)

    if len(result) < MIN_UNIVERSE:
        raise RuntimeError(
            "Universe contains no usable "
            "active instruments"
        )

    result.sort(
        key=lambda item: (
            item["market"],
            item["symbol"],
        )
    )

    return result


def is_not_started(
    item: Dict[str, Any],
    end_date: date,
) -> bool:

    listed = parse_date(
        item.get("listed_date")
    )

    return (
        listed is not None
        and listed > end_date
    )


# ============================================================================
# FIELD DISCOVERY
# ============================================================================

def find_field_index(
    fields: List[Any],
    aliases: Iterable[str],
) -> Optional[int]:

    normalized_fields = [
        normalize_header(field)
        for field in fields
    ]

    normalized_aliases = [
        normalize_header(alias)
        for alias in aliases
    ]

    for alias in normalized_aliases:
        for index, field in enumerate(
            normalized_fields
        ):
            if field == alias:
                return index

    for alias in normalized_aliases:
        for index, field in enumerate(
            normalized_fields
        ):
            if alias and alias in field:
                return index

    return None


# ============================================================================
# TWSE
# ============================================================================

def parse_twse_json(
    data: Any,
    target_date: date,
    allowed_symbols: Set[str],
) -> Dict[str, Dict[str, Any]]:

    if not isinstance(data, dict):
        raise RuntimeError("invalid_json_root")

    tables = data.get("tables")

    if not isinstance(tables, list):
        raise RuntimeError("missing_tables")

    result: Dict[str, Dict[str, Any]] = {}

    for table in tables:

        if not isinstance(table, dict):
            continue

        fields = table.get("fields")
        rows = table.get("data")

        if not isinstance(fields, list):
            continue

        if not isinstance(rows, list):
            continue

        symbol_index = find_field_index(
            fields,
            (
                "證券代號",
                "股票代號",
                "代號",
            ),
        )

        open_index = find_field_index(
            fields,
            (
                "開盤價",
                "開盤",
            ),
        )

        high_index = find_field_index(
            fields,
            (
                "最高價",
                "最高",
            ),
        )

        low_index = find_field_index(
            fields,
            (
                "最低價",
                "最低",
            ),
        )

        close_index = find_field_index(
            fields,
            (
                "收盤價",
                "收盤",
            ),
        )

        volume_index = find_field_index(
            fields,
            (
                "成交股數",
                "成交量",
                "成交量(股)",
            ),
        )

        indices = (
            symbol_index,
            open_index,
            high_index,
            low_index,
            close_index,
            volume_index,
        )

        if any(index is None for index in indices):
            continue

        max_index = max(
            int(index)
            for index in indices
            if index is not None
        )

        for raw_row in rows:

            if not isinstance(raw_row, list):
                continue

            if len(raw_row) <= max_index:
                continue

            symbol = normalize_symbol(
                raw_row[symbol_index]
            )

            if symbol not in allowed_symbols:
                continue

            row = normalize_price_row(
                target_date,
                symbol,
                raw_row[open_index],
                raw_row[high_index],
                raw_row[low_index],
                raw_row[close_index],
                raw_row[volume_index],
            )

            if row is not None:
                result[symbol] = row

    return result


def fetch_twse_daily_batch(
    target_date: date,
    allowed_symbols: Set[str],
) -> Tuple[
    Dict[str, Dict[str, Any]],
    bool,
    Optional[str],
]:

    try:

        data = http_get_json(
            TWSE_DAILY_URL,
            {
                "response": "json",
                "date": target_date.strftime(
                    "%Y%m%d"
                ),
                "type": "ALLBUT0999",
            },
        )

        rows = parse_twse_json(
            data,
            target_date,
            allowed_symbols,
        )

        return rows, True, (
            None
            if rows
            else "twse_official_no_universe_rows"
        )

    except Exception as exc:

        return (
            {},
            False,
            f"twse_official_failed:{exc}",
        )


# ============================================================================
# TPEX HTML
# ============================================================================

class TpexTableParser(HTMLParser):

    def __init__(self) -> None:

        super().__init__(
            convert_charrefs=True
        )

        self.rows: List[List[str]] = []

        self.in_tr = False
        self.in_cell = False

        self.row: List[str] = []
        self.cell: List[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: List[
            Tuple[str, Optional[str]]
        ],
    ) -> None:

        tag = tag.lower()

        if tag == "tr":
            self.in_tr = True
            self.row = []

        elif (
            tag in {"td", "th"}
            and self.in_tr
        ):
            self.in_cell = True
            self.cell = []

        elif (
            tag == "br"
            and self.in_cell
        ):
            self.cell.append(" ")

    def handle_data(
        self,
        data: str,
    ) -> None:

        if self.in_cell:
            self.cell.append(data)

    def handle_endtag(
        self,
        tag: str,
    ) -> None:

        tag = tag.lower()

        if (
            tag in {"td", "th"}
            and self.in_cell
        ):

            self.row.append(
                normalize_text(
                    "".join(self.cell)
                )
            )

            self.cell = []
            self.in_cell = False

        elif tag == "tr":

            if self.row:
                self.rows.append(self.row)

            self.row = []
            self.in_tr = False
            self.in_cell = False


def parse_tpex_html(
    html: str,
    target_date: date,
    allowed_symbols: Set[str],
) -> Dict[str, Dict[str, Any]]:

    parser = TpexTableParser()

    parser.feed(html)

    result: Dict[str, Dict[str, Any]] = {}

    for header_index, header in enumerate(
        parser.rows
    ):

        symbol_index = find_field_index(
            header,
            (
                "證券代號",
                "股票代號",
                "代號",
                "代碼",
            ),
        )

        open_index = find_field_index(
            header,
            (
                "開盤價",
                "開盤",
            ),
        )

        high_index = find_field_index(
            header,
            (
                "最高價",
                "最高",
            ),
        )

        low_index = find_field_index(
            header,
            (
                "最低價",
                "最低",
            ),
        )

        close_index = find_field_index(
            header,
            (
                "收盤價",
                "收盤",
            ),
        )

        volume_index = find_field_index(
            header,
            (
                "成交股數",
                "成交量",
                "成交量(股)",
            ),
        )

        indices = (
            symbol_index,
            open_index,
            high_index,
            low_index,
            close_index,
            volume_index,
        )

        if any(index is None for index in indices):
            continue

        max_index = max(
            int(index)
            for index in indices
            if index is not None
        )

        for raw_row in parser.rows[
            header_index + 1:
        ]:

            if len(raw_row) <= max_index:
                continue

            symbol = normalize_symbol(
                raw_row[symbol_index]
            )

            if symbol not in allowed_symbols:
                continue

            row = normalize_price_row(
                target_date,
                symbol,
                raw_row[open_index],
                raw_row[high_index],
                raw_row[low_index],
                raw_row[close_index],
                raw_row[volume_index],
            )

            if row is not None:
                result[symbol] = row

        if result:
            return result

    # TPEx historical fixed layout:
    # 0 代號
    # 1 名稱
    # 2 收盤
    # 3 漲跌
    # 4 開盤
    # 5 最高
    # 6 最低
    # 7 成交股數

    for raw_row in parser.rows:

        if len(raw_row) < 8:
            continue

        symbol = normalize_symbol(
            raw_row[0]
        )

        if symbol not in allowed_symbols:
            continue

        row = normalize_price_row(
            target_date,
            symbol,
            raw_row[4],
            raw_row[5],
            raw_row[6],
            raw_row[2],
            raw_row[7],
        )

        if row is not None:
            result[symbol] = row

    return result


# ============================================================================
# TPEX JSON
# ============================================================================

def parse_tpex_json(
    data: Any,
    target_date: date,
    allowed_symbols: Set[str],
) -> Dict[str, Dict[str, Any]]:

    if not isinstance(data, dict):
        raise RuntimeError("invalid_json_root")

    rows = data.get("aaData")

    if not isinstance(rows, list):
        rows = data.get("data")

    if not isinstance(rows, list):
        raise RuntimeError("missing_aaData")

    result: Dict[str, Dict[str, Any]] = {}

    for raw_row in rows:

        if not isinstance(raw_row, list):
            continue

        if len(raw_row) < 8:
            continue

        symbol = normalize_symbol(
            raw_row[0]
        )

        if symbol not in allowed_symbols:
            continue

        row = normalize_price_row(
            target_date,
            symbol,
            raw_row[4],
            raw_row[5],
            raw_row[6],
            raw_row[2],
            raw_row[7],
        )

        if row is not None:
            result[symbol] = row

    return result


# ============================================================================
# TPEX OFFICIAL
# ============================================================================

def fetch_tpex_daily_batch(
    target_date: date,
    allowed_symbols: Set[str],
) -> Tuple[
    Dict[str, Dict[str, Any]],
    bool,
    Optional[str],
]:

    roc = roc_date(target_date)

    json_error: Optional[str] = None

    # --------------------------------------------------------
    # Official JSON
    # --------------------------------------------------------

    try:

        data = http_get_json(
            TPEX_DAILY_URL,
            {
                "l": "zh-tw",
                "o": "json",
                "d": roc,
                "se": "EW",
            },
        )

        rows = parse_tpex_json(
            data,
            target_date,
            allowed_symbols,
        )

        if rows:
            return rows, True, None

        json_error = "json_empty_valid_rows"

    except Exception as exc:
        json_error = str(exc)

    # --------------------------------------------------------
    # Official HTML fallback
    # --------------------------------------------------------

    try:

        html = http_get_text(
            TPEX_DAILY_URL,
            {
                "l": "zh-tw",
                "o": "htm",
                "d": roc,
                "se": "EW",
            },
        )

        rows = parse_tpex_html(
            html,
            target_date,
            allowed_symbols,
        )

        if rows:
            return rows, True, None

        return (
            {},
            True,
            (
                "tpex_official_no_universe_rows:"
                f"json={json_error};"
                "html=empty_valid_rows"
            ),
        )

    except Exception as exc:

        return (
            {},
            False,
            (
                "tpex_official_failed:"
                f"json={json_error};"
                f"html={exc}"
            ),
        )


# ============================================================================
# OFFICIAL COLLECTION
# ============================================================================

def collect_official_market_data(
    market: str,
    start_date: date,
    end_date: date,
    allowed_symbols: Set[str],
) -> Tuple[
    Dict[
        str,
        Dict[str, Dict[str, Any]]
    ],
    Dict[str, int],
    List[str],
]:

    all_data: Dict[
        str,
        Dict[str, Dict[str, Any]]
    ] = {}

    stats = {
        "attempted": 0,
        "source_ok": 0,
        "rows": 0,
        "failed": 0,
    }

    diagnostics: List[str] = []

    if not allowed_symbols:
        return all_data, stats, diagnostics

    current = start_date

    while current <= end_date:

        if not is_weekday(current):
            current += timedelta(days=1)
            continue

        stats["attempted"] += 1

        if market == "TWSE":

            rows, source_ok, error = (
                fetch_twse_daily_batch(
                    current,
                    allowed_symbols,
                )
            )

        elif market == "TPEX":

            rows, source_ok, error = (
                fetch_tpex_daily_batch(
                    current,
                    allowed_symbols,
                )
            )

        else:
            raise RuntimeError(
                f"Unsupported market: {market}"
            )

        if source_ok:
            stats["source_ok"] += 1
        else:
            stats["failed"] += 1

        if rows:

            stats["rows"] += len(rows)

            for symbol, row in rows.items():

                all_data.setdefault(
                    symbol,
                    {}
                )[row["date"]] = row

        if error and (
            not rows
            or not source_ok
        ):

            diagnostics.append(
                f"{current.isoformat()}:{error}"
            )

        if REQUEST_DELAY > 0:
            time.sleep(REQUEST_DELAY)

        current += timedelta(days=1)

    # --------------------------------------------------------
    # System-level official source health.
    # --------------------------------------------------------

    if (
        stats["attempted"] > 0
        and stats["source_ok"] == 0
    ):

        raise RuntimeError(
            f"{market} official source "
            "failed on every attempted weekday: "
            + "; ".join(diagnostics[:5])
        )

    if not all_data:

        raise RuntimeError(
            f"{market} official source "
            "returned zero valid Universe rows "
            "across the requested period"
        )

    return (
        all_data,
        stats,
        diagnostics,
    )


# ============================================================================
# EXISTING PRICE DATA
# ============================================================================

def load_existing_prices(
    symbol: str,
) -> Dict[str, Dict[str, Any]]:

    manifest_path = OUTPUT_DIR / "manifest.json"

    if not manifest_path.exists():
        return {}

    try:
        manifest = json.loads(
            manifest_path.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        return {}

    files = manifest.get("files", [])

    if not isinstance(files, list):
        return {}

    for file_name in files:

        path = OUTPUT_DIR / str(file_name)

        if not path.exists():
            continue

        try:

            payload = json.loads(
                path.read_text(
                    encoding="utf-8"
                )
            )

            stocks = payload.get(
                "stocks",
                {}
            )

            prices = stocks.get(symbol)

            if not isinstance(prices, list):
                continue

            result: Dict[
                str,
                Dict[str, Any]
            ] = {}

            for row in prices:

                if not isinstance(row, dict):
                    continue

                row_date = normalize_text(
                    row.get("date")
                )

                if not row_date:
                    continue

                if valid_ohlcv_row(row):
                    result[row_date] = row

            if result:
                return result

        except Exception:
            continue

    return {}


# ============================================================================
# YAHOO FALLBACK
# ============================================================================

def yahoo_symbol(
    item: Dict[str, Any],
) -> str:

    suffix = (
        ".TW"
        if item["market"] == "TWSE"
        else ".TWO"
    )

    return f"{item['symbol']}{suffix}"


def fetch_yahoo_history(
    item: Dict[str, Any],
    start_date: date,
    end_date: date,
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Optional[str],
]:

    if start_date > end_date:
        return {}, None

    symbol = yahoo_symbol(item)

    period1 = int(
        datetime.combine(
            start_date,
            datetime.min.time(),
            tzinfo=timezone.utc,
        ).timestamp()
    )

    period2 = int(
        datetime.combine(
            end_date + timedelta(days=1),
            datetime.min.time(),
            tzinfo=timezone.utc,
        ).timestamp()
    )

    url = (
        "https://query1.finance.yahoo.com/"
        "v8/finance/chart/"
        f"{symbol}"
    )

    params = {
        "period1": period1,
        "period2": period2,
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "true",
    }

    last_error: Optional[str] = None

    for attempt in range(
        1,
        YAHOO_RETRIES + 1,
    ):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=YAHOO_TIMEOUT,
            )

            response.raise_for_status()

            payload = response.json()

            chart = payload.get(
                "chart",
                {}
            )

            results = (
                chart.get("result")
                or []
            )

            if not results:
                raise RuntimeError(
                    "Yahoo result empty"
                )

            data = results[0]

            timestamps = (
                data.get("timestamp")
                or []
            )

            quote_list = (
                data
                .get("indicators", {})
                .get("quote", [])
            )

            if not quote_list:
                raise RuntimeError(
                    "Yahoo quote empty"
                )

            quote = quote_list[0]

            opens = quote.get("open", [])
            highs = quote.get("high", [])
            lows = quote.get("low", [])
            closes = quote.get("close", [])
            volumes = quote.get("volume", [])

            output: Dict[
                str,
                Dict[str, Any]
            ] = {}

            for index, timestamp in enumerate(
                timestamps
            ):

                try:
                    row_date = (
                        datetime.fromtimestamp(
                            timestamp,
                            timezone.utc,
                        ).date()
                    )
                except Exception:
                    continue

                if (
                    row_date < start_date
                    or row_date > end_date
                ):
                    continue

                arrays = (
                    opens,
                    highs,
                    lows,
                    closes,
                    volumes,
                )

                if any(
                    index >= len(array)
                    for array in arrays
                ):
                    continue

                row = normalize_price_row(
                    row_date,
                    item["symbol"],
                    opens[index],
                    highs[index],
                    lows[index],
                    closes[index],
                    volumes[index],
                )

                if row is not None:
                    output[row["date"]] = row

            if not output:
                raise RuntimeError(
                    "Yahoo returned no valid OHLCV rows"
                )

            return output, None

        except Exception as exc:

            last_error = str(exc)

            if attempt < YAHOO_RETRIES:
                time.sleep(
                    YAHOO_BACKOFF * attempt
                )

    return (
        {},
        f"yahoo_failed:{last_error}",
    )


# ============================================================================
# MERGE
# ============================================================================

def merge_rows(
    existing: Dict[str, Dict[str, Any]],
    official: Dict[str, Dict[str, Any]],
    yahoo: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:

    merged: Dict[
        str,
        Dict[str, Any]
    ] = {}

    # Existing.
    for row_date, row in existing.items():
        if valid_ohlcv_row(row):
            merged[row_date] = row

    # Official always wins.
    for row_date, row in official.items():
        if valid_ohlcv_row(row):
            merged[row_date] = row

    # Yahoo only fills missing dates.
    for row_date, row in yahoo.items():
        if (
            row_date not in merged
            and valid_ohlcv_row(row)
        ):
            merged[row_date] = row

    return [
        merged[key]
        for key in sorted(merged)
    ]


# ============================================================================
# BUILD RESULTS
# ============================================================================

def build_results(
    universe: List[Dict[str, Any]],
    start_date: date,
    end_date: date,
    official_data: Dict[
        str,
        Dict[str, Dict[str, Any]]
    ],
) -> Tuple[
    List[Dict[str, Any]],
    Dict[str, int],
    List[str],
]:

    results: List[Dict[str, Any]] = []

    stats = {
        "yahoo_attempted": 0,
        "yahoo_success": 0,
        "yahoo_failed": 0,
        "not_started": 0,
        "no_history": 0,
    }

    diagnostics: List[str] = []

    for item in universe:

        symbol = item["symbol"]

        listed = parse_date(
            item.get("listed_date")
        )

        # --------------------------------------------------------
        # NOT STARTED
        # --------------------------------------------------------

        if is_not_started(
            item,
            end_date,
        ):

            stats["not_started"] += 1

            results.append(
                {
                    "symbol": symbol,
                    "code": symbol,
                    "market": item["market"],
                    "type": item["type"],
                    "name": item["name"],
                    "listed_date": item.get(
                        "listed_date"
                    ),
                    "source": "not_started",
                    "history_rows": 0,
                    "history_status": "not_started",
                    "latest_date": None,
                    "prices": [],
                }
            )

            continue

        # --------------------------------------------------------
        # HISTORY START
        # --------------------------------------------------------

        history_start = (
            max(start_date, listed)
            if listed is not None
            else start_date
        )

        expected_dates = {
            d.isoformat()
            for d in date_range(
                history_start,
                end_date,
            )
            if is_weekday(d)
        }

        # --------------------------------------------------------
        # Existing
        # --------------------------------------------------------

        existing_rows = load_existing_prices(
            symbol
        )

        # --------------------------------------------------------
        # Official
        # --------------------------------------------------------

        official_rows = official_data.get(
            symbol,
            {}
        )

        # --------------------------------------------------------
        # Merge before Yahoo
        # --------------------------------------------------------

        merged_before_yahoo = merge_rows(
            existing_rows,
            official_rows,
            {},
        )

        merged_dates = {
            row["date"]
            for row in merged_before_yahoo
        }

        covered = (
            merged_dates
            & expected_dates
        )

        coverage = (
            len(covered)
            / len(expected_dates)
            if expected_dates
            else 1.0
        )

        missing_dates = (
            expected_dates
            - covered
        )

        # --------------------------------------------------------
        # Yahoo fallback
        # --------------------------------------------------------

        yahoo_rows: Dict[
            str,
            Dict[str, Any]
        ] = {}

        if (
            missing_dates
            and (
                coverage
                < OFFICIAL_COMPLETENESS_THRESHOLD
                or not merged_before_yahoo
            )
        ):

            stats["yahoo_attempted"] += 1

            yahoo_rows, yahoo_error = (
                fetch_yahoo_history(
                    item,
                    history_start,
                    end_date,
                )
            )

            if yahoo_rows:
                stats["yahoo_success"] += 1

            else:

                stats["yahoo_failed"] += 1

                if yahoo_error:
                    diagnostics.append(
                        f"{symbol}:{yahoo_error}"
                    )

        # --------------------------------------------------------
        # Final merge
        # --------------------------------------------------------

        prices = merge_rows(
            existing_rows,
            official_rows,
            yahoo_rows,
        )

        filtered_prices: List[
            Dict[str, Any]
        ] = []

        for row in prices:

            row_date = parse_date(
                row.get("date")
            )

            if row_date is None:
                continue

            if (
                row_date < history_start
                or row_date > end_date
            ):
                continue

            if valid_ohlcv_row(row):
                filtered_prices.append(row)

        prices = filtered_prices

        # --------------------------------------------------------
        # Started + zero history
        # --------------------------------------------------------

        if not prices:

            stats["no_history"] += 1

            diagnostics.append(
                f"{symbol}:no_history:"
                f"listed_date={item.get('listed_date')};"
                f"official_rows={len(official_rows)};"
                f"yahoo_rows={len(yahoo_rows)}"
            )

            results.append(
                {
                    "symbol": symbol,
                    "code": symbol,
                    "market": item["market"],
                    "type": item["type"],
                    "name": item["name"],
                    "listed_date": item.get(
                        "listed_date"
                    ),
                    "source": "none",
                    "history_rows": 0,
                    "history_status": "no_history",
                    "latest_date": None,
                    "prices": [],
                }
            )

            continue

        # --------------------------------------------------------
        # Status
        # --------------------------------------------------------

        history_rows = len(prices)

        if history_rows >= 60:
            history_status = "complete"

        elif history_rows >= 20:
            history_status = "partial"

        else:
            history_status = "short"

        latest_date = prices[-1]["date"]

        source_parts: List[str] = []

        if official_rows:
            source_parts.append("official")

        if yahoo_rows:
            source_parts.append("yahoo")

        if (
            not source_parts
            and existing_rows
        ):
            source_parts.append("existing")

        source = (
            "+".join(source_parts)
            if source_parts
            else "none"
        )

        results.append(
            {
                "symbol": symbol,
                "code": symbol,
                "market": item["market"],
                "type": item["type"],
                "name": item["name"],
                "listed_date": item.get(
                    "listed_date"
                ),
                "source": source,
                "history_rows": history_rows,
                "history_status": history_status,
                "latest_date": latest_date,
                "prices": prices,
            }
        )

    return (
        results,
        stats,
        diagnostics,
    )


# ============================================================================
# RESULT VALIDATION
# ============================================================================

def validate_result_record(
    result: Dict[str, Any],
    end_date: date,
) -> None:

    symbol = normalize_symbol(
        result.get("symbol")
    )

    if not symbol:
        raise RuntimeError(
            "Result contains empty symbol"
        )

    prices = result.get("prices")

    if not isinstance(prices, list):
        raise RuntimeError(
            f"{symbol}: prices must be list"
        )

    listed = parse_date(
        result.get("listed_date")
    )

    not_started = (
        listed is not None
        and listed > end_date
    )

    # --------------------------------------------------------
    # NOT STARTED
    # --------------------------------------------------------

    if not_started:

        if prices:
            raise RuntimeError(
                f"{symbol}: not_started "
                "must have zero prices"
            )

        if result.get("history_rows") != 0:
            raise RuntimeError(
                f"{symbol}: not_started "
                "history_rows != 0"
            )

        if result.get(
            "history_status"
        ) != "not_started":
            raise RuntimeError(
                f"{symbol}: invalid "
                "not_started status"
            )

        if result.get(
            "source"
        ) != "not_started":
            raise RuntimeError(
                f"{symbol}: invalid "
                "not_started source"
            )

        if result.get(
            "latest_date"
        ) is not None:
            raise RuntimeError(
                f"{symbol}: not_started "
                "latest_date must be null"
            )

        return

    # --------------------------------------------------------
    # NO HISTORY
    # --------------------------------------------------------

    if (
        result.get("history_status")
        == "no_history"
    ):

        if prices != []:
            raise RuntimeError(
                f"{symbol}: no_history "
                "must have zero prices"
            )

        if result.get(
            "history_rows"
        ) != 0:
            raise RuntimeError(
                f"{symbol}: no_history "
                "history_rows != 0"
            )

        if result.get(
            "latest_date"
        ) is not None:
            raise RuntimeError(
                f"{symbol}: no_history "
                "latest_date must be null"
            )

        if result.get("source") != "none":
            raise RuntimeError(
                f"{symbol}: no_history "
                "source must be none"
            )

        return

    # --------------------------------------------------------
    # STARTED WITH HISTORY
    # --------------------------------------------------------

    if len(prices) < MIN_HISTORY_ROWS:
        raise RuntimeError(
            f"{symbol}: started instrument "
            "has no valid price history"
        )

    previous_date: Optional[str] = None
    seen_dates: Set[str] = set()

    for row in prices:

        if not isinstance(row, dict):
            raise RuntimeError(
                f"{symbol}: malformed price row"
            )

        if not valid_ohlcv_row(row):
            raise RuntimeError(
                f"{symbol}: invalid OHLCV row"
            )

        row_date = normalize_text(
            row.get("date")
        )

        if row_date in seen_dates:
            raise RuntimeError(
                f"{symbol}: duplicate date "
                f"{row_date}"
            )

        seen_dates.add(row_date)

        if (
            previous_date is not None
            and row_date <= previous_date
        ):
            raise RuntimeError(
                f"{symbol}: price dates "
                "not strictly ascending"
            )

        previous_date = row_date

    if result.get(
        "history_rows"
    ) != len(prices):
        raise RuntimeError(
            f"{symbol}: history_rows mismatch"
        )

    if result.get(
        "latest_date"
    ) != prices[-1]["date"]:
        raise RuntimeError(
            f"{symbol}: latest_date mismatch"
        )

    if result.get(
        "history_status"
    ) not in {
        "short",
        "partial",
        "complete",
    }:
        raise RuntimeError(
            f"{symbol}: invalid history_status"
        )


def validate_results(
    universe: List[Dict[str, Any]],
    results: List[Dict[str, Any]],
    end_date: date,
) -> None:

    expected = {
        item["symbol"]
        for item in universe
    }

    actual = {
        normalize_symbol(
            result.get("symbol")
        )
        for result in results
    }

    missing = expected - actual
    extra = actual - expected

    if missing:
        raise RuntimeError(
            "Universe -> Result missing symbols: "
            + ", ".join(sorted(missing))
        )

    if extra:
        raise RuntimeError(
            "Result contains extra symbols: "
            + ", ".join(sorted(extra))
        )

    if len(results) != len(universe):
        raise RuntimeError(
            "Universe -> Result count mismatch: "
            f"{len(universe)} != {len(results)}"
        )

    for result in results:
        validate_result_record(
            result,
            end_date,
        )


# ============================================================================
# SHARDS
# ============================================================================

def build_shards(
    results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    ordered = sorted(
        results,
        key=lambda item: item["symbol"],
    )

    shards: List[Dict[str, Any]] = []

    for start in range(
        0,
        len(ordered),
        SHARD_SIZE,
    ):

        chunk = ordered[
            start:start + SHARD_SIZE
        ]

        shard_number = (
            start // SHARD_SIZE
        ) + 1

        stocks = {
            item["symbol"]: item["prices"]
            for item in chunk
        }

        shards.append(
            {
                "schema_version": SCHEMA_VERSION,
                "version": VERSION,
                "shard": shard_number,
                "stocks": stocks,
            }
        )

    return shards


# ============================================================================
# MANIFEST
# ============================================================================

def build_manifest(
    results: List[Dict[str, Any]],
    files: List[str],
) -> Dict[str, Any]:

    counts = {
        status: sum(
            result.get("history_status")
            == status
            for result in results
        )
        for status in (
            "complete",
            "partial",
            "short",
            "not_started",
            "no_history",
        )
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "version": VERSION,
        "generated_at": datetime.now(
            TAIWAN_TZ
        ).isoformat(),
        "total_symbols": len(results),
        "complete_count": counts["complete"],
        "partial_count": counts["partial"],
        "short_count": counts["short"],
        "not_started_count": counts[
            "not_started"
        ],
        "no_history_count": counts[
            "no_history"
        ],
        "files": files,
    }


# ============================================================================
# SHARD VALIDATION
# ============================================================================

def validate_shard(
    path: Path,
    expected_symbols: Set[str],
    not_started_symbols: Set[str],
    no_history_symbols: Set[str],
) -> None:

    if not path.is_file():
        raise RuntimeError(
            f"Shard file missing: {path}"
        )

    try:
        payload = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except Exception as exc:
        raise RuntimeError(
            f"Invalid shard JSON: "
            f"{path}: {exc}"
        ) from exc

    if payload.get(
        "schema_version"
    ) != SCHEMA_VERSION:
        raise RuntimeError(
            f"Shard schema mismatch: {path}"
        )

    stocks = payload.get("stocks")

    if not isinstance(stocks, dict):
        raise RuntimeError(
            f"Shard stocks invalid: {path}"
        )

    actual_symbols = {
        normalize_symbol(symbol)
        for symbol in stocks.keys()
    }

    if actual_symbols != expected_symbols:

        missing = (
            expected_symbols
            - actual_symbols
        )

        extra = (
            actual_symbols
            - expected_symbols
        )

        raise RuntimeError(
            f"Shard symbol mismatch: {path}; "
            f"missing={sorted(missing)}; "
            f"extra={sorted(extra)}"
        )

    for symbol, prices in stocks.items():

        symbol = normalize_symbol(symbol)

        # --------------------------------------------------------
        # not_started
        # --------------------------------------------------------

        if symbol in not_started_symbols:

            if prices != []:
                raise RuntimeError(
                    f"{symbol}: not_started "
                    "shard must be empty"
                )

            continue

        # --------------------------------------------------------
        # no_history
        # --------------------------------------------------------

        if symbol in no_history_symbols:

            if prices != []:
                raise RuntimeError(
                    f"{symbol}: no_history "
                    "shard must be empty"
                )

            continue

        # --------------------------------------------------------
        # Started with history
        # --------------------------------------------------------

        if not isinstance(prices, list):
            raise RuntimeError(
                f"{symbol}: shard prices "
                "must be list"
            )

        if len(prices) < MIN_HISTORY_ROWS:
            raise RuntimeError(
                f"{symbol}: shard has no history"
            )

        previous_date: Optional[str] = None
        seen_dates: Set[str] = set()

        for row in prices:

            if not isinstance(row, dict):
                raise RuntimeError(
                    f"{symbol}: malformed "
                    "shard row"
                )

            if not valid_ohlcv_row(row):
                raise RuntimeError(
                    f"{symbol}: invalid "
                    "shard OHLCV"
                )

            row_date = normalize_text(
                row.get("date")
            )

            if row_date in seen_dates:
                raise RuntimeError(
                    f"{symbol}: duplicate "
                    "shard date"
                )

            seen_dates.add(row_date)

            if (
                previous_date is not None
                and row_date <= previous_date
            ):
                raise RuntimeError(
                    f"{symbol}: shard dates "
                    "not ascending"
                )

            previous_date = row_date


# ============================================================================
# SHARD READ-BACK
# ============================================================================

def verify_shard_file(
    path: Path,
    expected_symbols: Set[str],
) -> int:
    """
    實際重新從磁碟讀取 shard。

    目的：
    atomic_write_json() 成功不代表後續檔案一定存在且內容正確。

    這裡強制確認：
    1. 檔案真的存在。
    2. JSON 真的能重新讀取。
    3. stocks 真的是 object。
    4. symbol 集合完全一致。
    5. 回傳實際股票數。
    """

    if not path.is_file():
        raise RuntimeError(
            f"Shard file missing after write: "
            f"{path.name}"
        )

    try:
        payload = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except Exception as exc:
        raise RuntimeError(
            f"Shard read-back JSON failed: "
            f"{path.name}: {exc}"
        ) from exc

    if payload.get(
        "schema_version"
    ) != SCHEMA_VERSION:
        raise RuntimeError(
            f"Shard read-back schema mismatch: "
            f"{path.name}"
        )

    stocks = payload.get("stocks")

    if not isinstance(stocks, dict):
        raise RuntimeError(
            f"Shard read-back stocks invalid: "
            f"{path.name}"
        )

    actual_symbols = {
        normalize_symbol(symbol)
        for symbol in stocks.keys()
    }

    if actual_symbols != expected_symbols:

        missing = (
            expected_symbols
            - actual_symbols
        )

        extra = (
            actual_symbols
            - expected_symbols
        )

        raise RuntimeError(
            f"Shard read-back symbol mismatch: "
            f"{path.name}; "
            f"missing={sorted(missing)}; "
            f"extra={sorted(extra)}"
        )

    return len(stocks)


# ============================================================================
# ATOMIC JSON
# ============================================================================

def atomic_write_json(
    path: Path,
    payload: Dict[str, Any],
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, temp_name = tempfile.mkstemp(
        prefix=".tmp_",
        suffix=".json",
        dir=str(path.parent),
        text=True,
    )

    try:

        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
        ) as handle:

            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
            )

            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(
            temp_name,
            path,
        )

    finally:

        if os.path.exists(temp_name):
            os.unlink(temp_name)


# ============================================================================
# WRITE PRICE DIRECTORY
# ============================================================================

def write_price_directory(
    results: List[Dict[str, Any]],
    universe: List[Dict[str, Any]],
    end_date: date,
) -> None:

    parent = OUTPUT_DIR.parent

    parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_dir = Path(
        tempfile.mkdtemp(
            prefix=TMP_PREFIX,
            dir=str(parent),
        )
    )

    try:

        shards = build_shards(results)

        shard_files: List[str] = []

        expected_symbols = {
            item["symbol"]
            for item in universe
        }

        not_started_symbols = {
            item["symbol"]
            for item in universe
            if is_not_started(
                item,
                end_date,
            )
        }

        no_history_symbols = {
            result["symbol"]
            for result in results
            if result.get(
                "history_status"
            ) == "no_history"
        }

        seen_symbols: Set[str] = set()

        print(
            f"Shard size：{SHARD_SIZE}"
        )

        print(
            f"Expected shards：{len(shards)}"
        )

        print("")

        print("WRITE SHARDS")

        for shard in shards:

            shard_number = shard["shard"]

            file_name = (
                f"prices_{shard_number:03d}.json"
            )

            shard_files.append(file_name)

            shard_symbols = {
                normalize_symbol(symbol)
                for symbol in shard["stocks"]
            }

            overlap = (
                seen_symbols
                & shard_symbols
            )

            if overlap:
                raise RuntimeError(
                    "Duplicate symbols across "
                    "shards: "
                    + ", ".join(
                        sorted(overlap)
                    )
                )

            seen_symbols.update(
                shard_symbols
            )

            # ----------------------------------------------------
            # Write.
            # ----------------------------------------------------

            atomic_write_json(
                temp_dir / file_name,
                shard,
            )

            # ----------------------------------------------------
            # Immediate validation.
            # ----------------------------------------------------

            validate_shard(
                temp_dir / file_name,
                shard_symbols,
                (
                    not_started_symbols
                    & shard_symbols
                ),
                (
                    no_history_symbols
                    & shard_symbols
                ),
            )

            # ----------------------------------------------------
            # Immediate physical read-back.
            # ----------------------------------------------------

            shard_stock_count = (
                verify_shard_file(
                    temp_dir / file_name,
                    shard_symbols,
                )
            )

            print(
                f"✓ {file_name} → "
                f"{shard_stock_count} stocks"
            )

        # --------------------------------------------------------
        # Complete shard coverage.
        # --------------------------------------------------------

        if seen_symbols != expected_symbols:

            missing = (
                expected_symbols
                - seen_symbols
            )

            extra = (
                seen_symbols
                - expected_symbols
            )

            raise RuntimeError(
                "Shard Universe mismatch: "
                f"missing={sorted(missing)}; "
                f"extra={sorted(extra)}"
            )

        print("")

        print(
            f"Total shards：{len(shard_files)}"
        )

        print(
            f"Total stocks：{len(seen_symbols)}"
        )

        if len(seen_symbols) != len(expected_symbols):
            raise RuntimeError(
                "Shard total stock count mismatch"
            )

        # --------------------------------------------------------
        # Manifest.
        # --------------------------------------------------------

        manifest = build_manifest(
            results,
            shard_files,
        )

        atomic_write_json(
            temp_dir / "manifest.json",
            manifest,
        )

        # --------------------------------------------------------
        # Manifest validation before replacement.
        # --------------------------------------------------------

        expected_counts = {
            status: sum(
                result.get("history_status")
                == status
                for result in results
            )
            for status in (
                "complete",
                "partial",
                "short",
                "not_started",
                "no_history",
            )
        }

        if manifest.get(
            "schema_version"
        ) != SCHEMA_VERSION:
            raise RuntimeError(
                "Manifest schema mismatch"
            )

        if manifest.get(
            "total_symbols"
        ) != len(expected_symbols):
            raise RuntimeError(
                "Manifest total_symbols mismatch"
            )

        for status, count in (
            expected_counts.items()
        ):

            if manifest.get(
                f"{status}_count"
            ) != count:
                raise RuntimeError(
                    f"Manifest {status}_count "
                    "mismatch"
                )

        if manifest.get(
            "files"
        ) != shard_files:
            raise RuntimeError(
                "Manifest files mismatch"
            )

        # --------------------------------------------------------
        # Manifest physical read-back.
        # --------------------------------------------------------

        manifest_path = (
            temp_dir / "manifest.json"
        )

        if not manifest_path.is_file():
            raise RuntimeError(
                "Manifest file missing after write"
            )

        try:
            manifest_readback = json.loads(
                manifest_path.read_text(
                    encoding="utf-8"
                )
            )
        except Exception as exc:
            raise RuntimeError(
                f"Manifest read-back failed: {exc}"
            ) from exc

        if manifest_readback != manifest:
            raise RuntimeError(
                "Manifest read-back mismatch"
            )

        print("")
        print(
            "✓ manifest.json → "
            f"{len(shard_files)} shard files"
        )

        # --------------------------------------------------------
        # Directory-level atomic replacement.
        # --------------------------------------------------------

        backup_dir: Optional[Path] = None

        if OUTPUT_DIR.exists():

            backup_dir = OUTPUT_DIR.with_name(
                OUTPUT_DIR.name + ".backup"
            )

            if backup_dir.exists():
                shutil.rmtree(backup_dir)

            os.replace(
                OUTPUT_DIR,
                backup_dir,
            )

        try:

            os.replace(
                temp_dir,
                OUTPUT_DIR,
            )

        except Exception:

            if (
                backup_dir is not None
                and backup_dir.exists()
                and not OUTPUT_DIR.exists()
            ):
                os.replace(
                    backup_dir,
                    OUTPUT_DIR,
                )

            raise

        if (
            backup_dir is not None
            and backup_dir.exists()
        ):
            shutil.rmtree(backup_dir)

    except Exception:

        if temp_dir.exists():
            shutil.rmtree(
                temp_dir,
                ignore_errors=True,
            )

        raise


# ============================================================================
# FINAL OUTPUT VALIDATION
# ============================================================================

def validate_complete_output(
    universe: List[Dict[str, Any]],
    results: List[Dict[str, Any]],
    end_date: date,
) -> None:

    validate_results(
        universe,
        results,
        end_date,
    )

    if not OUTPUT_DIR.exists():
        raise RuntimeError(
            "Price output directory missing"
        )

    if not OUTPUT_DIR.is_dir():
        raise RuntimeError(
            "Price output path is not directory"
        )

    manifest_path = (
        OUTPUT_DIR / "manifest.json"
    )

    if not manifest_path.is_file():
        raise RuntimeError(
            "Price manifest missing"
        )

    try:
        manifest = json.loads(
            manifest_path.read_text(
                encoding="utf-8"
            )
        )
    except Exception as exc:
        raise RuntimeError(
            f"Invalid manifest JSON: {exc}"
        ) from exc

    if manifest.get(
        "schema_version"
    ) != SCHEMA_VERSION:
        raise RuntimeError(
            "Manifest schema mismatch"
        )

    shard_files = manifest.get(
        "files"
    )

    if not isinstance(
        shard_files,
        list,
    ):
        raise RuntimeError(
            "Manifest files missing"
        )

    if not shard_files:
        raise RuntimeError(
            "Manifest contains zero shard files"
        )

    expected_symbols = {
        item["symbol"]
        for item in universe
    }

    not_started_symbols = {
        item["symbol"]
        for item in universe
        if is_not_started(
            item,
            end_date,
        )
    }

    no_history_symbols = {
        result["symbol"]
        for result in results
        if result.get(
            "history_status"
        ) == "no_history"
    }

    seen_symbols: Set[str] = set()

    print("")
    print("FINAL SHARD FILES")
    print("-" * 72)

    for file_name in shard_files:

        path = (
            OUTPUT_DIR
            / str(file_name)
        )

        # --------------------------------------------------------
        # 實體檔案存在檢查。
        # --------------------------------------------------------

        if not path.is_file():
            raise RuntimeError(
                f"Manifest shard missing: "
                f"{file_name}"
            )

        # --------------------------------------------------------
        # 實際 read-back。
        # --------------------------------------------------------

        try:
            payload = json.loads(
                path.read_text(
                    encoding="utf-8"
                )
            )
        except Exception as exc:
            raise RuntimeError(
                f"Invalid shard JSON: "
                f"{file_name}: {exc}"
            ) from exc

        if payload.get(
            "schema_version"
        ) != SCHEMA_VERSION:
            raise RuntimeError(
                f"Final shard schema mismatch: "
                f"{file_name}"
            )

        stocks = payload.get("stocks")

        if not isinstance(stocks, dict):
            raise RuntimeError(
                f"Shard stocks invalid: "
                f"{file_name}"
            )

        shard_symbols = {
            normalize_symbol(symbol)
            for symbol in stocks
        }

        if not shard_symbols:
            raise RuntimeError(
                f"Shard stocks empty: "
                f"{file_name}"
            )

        overlap = (
            seen_symbols
            & shard_symbols
        )

        if overlap:
            raise RuntimeError(
                "Duplicate symbols across "
                "final shards: "
                + ", ".join(
                    sorted(overlap)
                )
            )

        # --------------------------------------------------------
        # 完整 shard validation。
        # --------------------------------------------------------

        validate_shard(
            path,
            shard_symbols,
            (
                not_started_symbols
                & shard_symbols
            ),
            (
                no_history_symbols
                & shard_symbols
            ),
        )

        # --------------------------------------------------------
        # 再次 read-back helper validation。
        # --------------------------------------------------------

        readback_count = (
            verify_shard_file(
                path,
                shard_symbols,
            )
        )

        if readback_count != len(
            shard_symbols
        ):
            raise RuntimeError(
                f"Shard read-back count mismatch: "
                f"{file_name}"
            )

        seen_symbols.update(
            shard_symbols
        )

        print(
            f"✓ {file_name} → "
            f"{readback_count} stocks"
        )

    print("-" * 72)

    # --------------------------------------------------------
    # Final Universe coverage.
    # --------------------------------------------------------

    if seen_symbols != expected_symbols:

        missing = (
            expected_symbols
            - seen_symbols
        )

        extra = (
            seen_symbols
            - expected_symbols
        )

        raise RuntimeError(
            "Final shard Universe mismatch: "
            f"missing={sorted(missing)}; "
            f"extra={sorted(extra)}"
        )

    if len(seen_symbols) != len(
        expected_symbols
    ):
        raise RuntimeError(
            "Final shard total stock count mismatch"
        )

    # --------------------------------------------------------
    # Manifest count validation.
    # --------------------------------------------------------

    expected_counts = {
        status: sum(
            result.get("history_status")
            == status
            for result in results
        )
        for status in (
            "complete",
            "partial",
            "short",
            "not_started",
            "no_history",
        )
    }

    for status, count in (
        expected_counts.items()
    ):

        if manifest.get(
            f"{status}_count"
        ) != count:
            raise RuntimeError(
                f"Manifest {status}_count mismatch"
            )

    if manifest.get(
        "total_symbols"
    ) != len(expected_symbols):
        raise RuntimeError(
            "Manifest total_symbols mismatch"
        )

    if manifest.get(
        "files"
    ) != shard_files:
        raise RuntimeError(
            "Manifest files mismatch"
        )

    # --------------------------------------------------------
    # Manifest itself read-back.
    # --------------------------------------------------------

    try:
        manifest_readback = json.loads(
            manifest_path.read_text(
                encoding="utf-8"
            )
        )
    except Exception as exc:
        raise RuntimeError(
            f"Final manifest read-back failed: "
            f"{exc}"
        ) from exc

    if not isinstance(
        manifest_readback,
        dict,
    ):
        raise RuntimeError(
            "Final manifest read-back is not object"
        )

    if manifest_readback.get(
        "schema_version"
    ) != SCHEMA_VERSION:
        raise RuntimeError(
            "Final manifest schema mismatch"
        )

    if manifest_readback.get(
        "files"
    ) != shard_files:
        raise RuntimeError(
            "Final manifest shard list mismatch"
        )

    if manifest_readback.get(
        "total_symbols"
    ) != len(expected_symbols):
        raise RuntimeError(
            "Final manifest total_symbols mismatch"
        )

    # --------------------------------------------------------
    # 明確輸出最終結果。
    # --------------------------------------------------------

    print("")
    print(
        f"Total shards：{len(shard_files)}"
    )

    print(
        f"Total stocks：{len(seen_symbols)}"
    )

    print(
        "✓ shard files existence PASS"
    )

    print(
        "✓ shard read-back PASS"
    )

    print(
        "✓ manifest read-back PASS"
    )


# ============================================================================
# REPORT
# ============================================================================

def print_report(
    universe: List[Dict[str, Any]],
    results: List[Dict[str, Any]],
    twse: Dict[str, int],
    tpex: Dict[str, int],
    yahoo: Dict[str, int],
    diagnostics: List[str],
) -> None:

    counts = {
        status: sum(
            result.get("history_status")
            == status
            for result in results
        )
        for status in (
            "complete",
            "partial",
            "short",
            "not_started",
            "no_history",
        )
    }

    print("")
    print("=" * 72)
    print("PRICE DATA VALIDATION")
    print("=" * 72)

    print(
        f"Universe：{len(universe)}"
    )

    print(
        f"Price：{len(results)}"
    )

    print(
        f"Complete：{counts['complete']}"
    )

    print(
        f"Partial：{counts['partial']}"
    )

    print(
        f"Short：{counts['short']}"
    )

    print(
        f"Not started：{counts['not_started']}"
    )

    print(
        f"No history：{counts['no_history']}"
    )

    print("")

    print(
        "TWSE："
        f"attempted={twse['attempted']} "
        f"source_ok={twse['source_ok']} "
        f"rows={twse['rows']} "
        f"failed={twse['failed']}"
    )

    print(
        "TPEx："
        f"attempted={tpex['attempted']} "
        f"source_ok={tpex['source_ok']} "
        f"rows={tpex['rows']} "
        f"failed={tpex['failed']}"
    )

    print(
        "Yahoo fallback："
        f"attempted={yahoo['yahoo_attempted']} "
        f"success={yahoo['yahoo_success']} "
        f"failed={yahoo['yahoo_failed']} "
        f"no_history={yahoo['no_history']}"
    )

    if diagnostics:

        print("")
        print(
            f"Diagnostics：{len(diagnostics)}"
        )

        for diagnostic in diagnostics[:50]:
            print(
                f"  {diagnostic}"
            )


# ============================================================================
# MAIN
# ============================================================================

def main() -> int:

    print("=" * 72)
    print("TW STOCK AI SCANNER")
    print("=" * 72)

    print(
        f"PRICE FETCH {VERSION}"
    )

    print(
        f"Schema：{SCHEMA_VERSION}"
    )

    end_date = today_taiwan()

    if START_DATE > end_date:
        raise RuntimeError(
            "Invalid date range"
        )

    print(
        f"資料日期："
        f"{START_DATE.isoformat()} ~ "
        f"{end_date.isoformat()}"
    )

    print("")

    # ------------------------------------------------------------------------
    # Universe
    # ------------------------------------------------------------------------

    universe = load_universe()

    not_started_symbols = {
        item["symbol"]
        for item in universe
        if is_not_started(
            item,
            end_date,
        )
    }

    twse_symbols = {
        item["symbol"]
        for item in universe
        if (
            item["market"] == "TWSE"
            and not is_not_started(
                item,
                end_date,
            )
        )
    }

    tpex_symbols = {
        item["symbol"]
        for item in universe
        if (
            item["market"] == "TPEX"
            and not is_not_started(
                item,
                end_date,
            )
        )
    }

    print(
        f"Universe：{len(universe)}"
    )

    print(
        f"TWSE started：{len(twse_symbols)}"
    )

    print(
        f"TPEx started：{len(tpex_symbols)}"
    )

    print(
        f"Not started："
        f"{len(not_started_symbols)}"
    )

    # ------------------------------------------------------------------------
    # Official TWSE
    # ------------------------------------------------------------------------

    print("")
    print("=" * 72)
    print("FETCH OFFICIAL TWSE")
    print("=" * 72)

    twse_data, twse_stats, twse_diag = (
        collect_official_market_data(
            "TWSE",
            START_DATE,
            end_date,
            twse_symbols,
        )
    )

    print(
        f"TWSE Universe symbols with data："
        f"{len(twse_data)}"
    )

    # ------------------------------------------------------------------------
    # Official TPEx
    # ------------------------------------------------------------------------

    print("")
    print("=" * 72)
    print("FETCH OFFICIAL TPEX")
    print("=" * 72)

    tpex_data, tpex_stats, tpex_diag = (
        collect_official_market_data(
            "TPEX",
            START_DATE,
            end_date,
            tpex_symbols,
        )
    )

    print(
        f"TPEx Universe symbols with data："
        f"{len(tpex_data)}"
    )

    # ------------------------------------------------------------------------
    # Combine official
    # ------------------------------------------------------------------------

    official_data: Dict[
        str,
        Dict[str, Dict[str, Any]]
    ] = {}

    for source_data in (
        twse_data,
        tpex_data,
    ):

        for symbol, rows in (
            source_data.items()
        ):

            official_data.setdefault(
                symbol,
                {}
            ).update(rows)

    # ------------------------------------------------------------------------
    # Build results
    # ------------------------------------------------------------------------

    print("")
    print("=" * 72)
    print("BUILD PRICE RESULTS")
    print("=" * 72)

    (
        results,
        yahoo_stats,
        build_diag,
    ) = build_results(
        universe,
        START_DATE,
        end_date,
        official_data,
    )

    diagnostics = (
        twse_diag
        + tpex_diag
        + build_diag
    )

    # ------------------------------------------------------------------------
    # Validate before write
    # ------------------------------------------------------------------------

    print("")
    print("=" * 72)
    print("VALIDATE RESULTS BEFORE WRITE")
    print("=" * 72)

    validate_results(
        universe,
        results,
        end_date,
    )

    print(
        "Universe -> Result validation passed"
    )

    # ------------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------------

    print("")
    print("=" * 72)
    print("WRITE PRICE OUTPUT")
    print("=" * 72)

    write_price_directory(
        results,
        universe,
        end_date,
    )

    print("")
    print(
        "Price output written atomically"
    )

    # ------------------------------------------------------------------------
    # FINAL VALIDATION
    # ------------------------------------------------------------------------

    print("")
    print("=" * 72)
    print("FINAL VALIDATION")
    print("=" * 72)

    validate_complete_output(
        universe,
        results,
        end_date,
    )

    print("")
    print(
        "Manifest validation passed"
    )

    print(
        "Shard validation passed"
    )

    print(
        "Universe -> Price validation passed"
    )

    print(
        "Read-back validation passed"
    )

    # ------------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------------

    print_report(
        universe,
        results,
        twse_stats,
        tpex_stats,
        yahoo_stats,
        diagnostics,
    )

    print("")
    print("=" * 72)
    print("PRICE FETCH COMPLETED")
    print("=" * 72)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
