#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 - fetch_prices.py
============================================================

V13.0

核心契約
------------------------------------------------------------
1. Data/universe.json 是唯一 Universe 來源
2. 只接受 status == "active"
3. 不自行建立 / 探測 / 刪除 Universe symbol
4. 官方 TWSE / TPEx 優先
5. TPEx 使用官方批次資料，不逐股票呼叫官方 API
6. Yahoo 僅用於官方資料缺口補洞
7. 已開始交易的商品必須至少有 1 筆有效 OHLCV
8. listed_date > end_date 的商品視為 not_started
9. not_started 商品允許 0 筆歷史價格
10. not_started 商品不得呼叫 Yahoo fallback
11. shard / manifest / final validation 必須接受同一 lifecycle 契約
12. 不因價格抓取問題修改 Universe
13. atomic write
14. read-back validation
15. 任一完整性驗證失敗，不污染既有輸出
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
# VERSION / SCHEMA
# ============================================================================

VERSION = "PRICE-FETCH-V13.0"
SCHEMA_VERSION = "prices-v13.0"


# ============================================================================
# PATHS
# ============================================================================

ROOT = Path(__file__).resolve().parents[1]

UNIVERSE_FILE = ROOT / "Data" / "universe.json"
OUTPUT_DIR = ROOT / "Data" / "prices"

TMP_PREFIX = ".prices_build_"


# ============================================================================
# OFFICIAL SOURCES
# ============================================================================

TWSE_DAILY_URL = (
    "https://www.twse.com.tw/exchangeReport/MI_INDEX"
)

TPEX_DAILY_URL = (
    "https://www.tpex.org.tw/web/stock/aftertrading/"
    "otc_quotes_no1430/stk_wn1430_result.php"
)


# ============================================================================
# CONFIG
# ============================================================================

MIN_UNIVERSE = 1

REQUEST_TIMEOUT = 30
REQUEST_RETRIES = 3
REQUEST_BACKOFF = 1.5
REQUEST_DELAY = 0.15

YAHOO_TIMEOUT = 20
YAHOO_RETRIES = 2
YAHOO_BACKOFF = 1.5

# 官方資料至少保留一筆才算 started 商品有效
MIN_HISTORY_ROWS = 1

# fallback 只在官方結果不足時使用
OFFICIAL_COMPLETENESS_THRESHOLD = 0.90

# 每個 shard 股票數
SHARD_SIZE = 250


# ============================================================================
# TAIWAN TIMEZONE
# ============================================================================

TAIWAN_TZ = timezone(timedelta(hours=8))


# ============================================================================
# HTTP SESSION
# ============================================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "(compatible; TW-Stock-AI-Scanner/13.0; +https://github.com/)"
        ),
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        "Accept": "*/*",
        "Connection": "keep-alive",
    }
)


# ============================================================================
# GENERIC HELPERS
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

    text = normalize_text(value)

    if not text:
        return None

    text = text.replace(",", "")
    text = text.replace(" ", "")

    if text in {"-", "--", "---", "N/A", "NA", "null"}:
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

    formats = (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y.%m.%d",
        "%Y%m%d",
    )

    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass

    # 民國日期，例如 115/09/03
    parts = text.replace(".", "/").replace("-", "/").split("/")

    if len(parts) == 3:
        try:
            y, m, d = [int(x) for x in parts]

            if y < 1911:
                y += 1911

            return date(y, m, d)

        except ValueError:
            pass

    return None


def today_taiwan() -> date:
    return datetime.now(TAIWAN_TZ).date()


def roc_date(d: date) -> str:
    return f"{d.year - 1911:03d}/{d.month:02d}/{d.day:02d}"


def date_range(start_date: date, end_date: date) -> Iterable[date]:
    current = start_date

    while current <= end_date:
        yield current
        current += timedelta(days=1)


def is_weekday(d: date) -> bool:
    return d.weekday() < 5


def valid_ohlcv_row(row: Dict[str, Any]) -> bool:
    required = (
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
    )

    for key in required:
        if key not in row:
            return False

    if not row["date"]:
        return False

    values = (
        row["open"],
        row["high"],
        row["low"],
        row["close"],
        row["volume"],
    )

    if any(value is None for value in values):
        return False

    if row["open"] <= 0:
        return False

    if row["high"] <= 0:
        return False

    if row["low"] <= 0:
        return False

    if row["close"] <= 0:
        return False

    if row["volume"] < 0:
        return False

    return True


# ============================================================================
# HTTP
# ============================================================================


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
                time.sleep(REQUEST_BACKOFF * attempt)

    raise RuntimeError(
        f"HTTP GET failed: {url}: {last_error}"
    )


def http_get_json(
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = REQUEST_TIMEOUT,
    retries: int = REQUEST_RETRIES,
) -> Any:

    response = http_get(
        url,
        params=params,
        timeout=timeout,
        retries=retries,
    )

    try:
        return response.json()

    except Exception as exc:
        raise RuntimeError(
            f"JSON decode failed: {exc}"
        ) from exc


def http_get_text(
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = REQUEST_TIMEOUT,
    retries: int = REQUEST_RETRIES,
) -> str:

    response = http_get(
        url,
        params=params,
        timeout=timeout,
        retries=retries,
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
            f"Unable to read universe.json: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise RuntimeError(
            "universe.json root must be object"
        )

    stocks = payload.get("stocks")

    if not isinstance(stocks, dict):
        raise RuntimeError(
            "universe.json stocks must be dict"
        )

    result: List[Dict[str, Any]] = []

    for symbol, raw in stocks.items():

        if not isinstance(raw, dict):
            continue

        if raw.get("status") != "active":
            continue

        normalized_symbol = normalize_symbol(
            raw.get("symbol") or symbol
        )

        if not normalized_symbol:
            continue

        market = normalize_text(
            raw.get("market")
        ).upper()

        if market not in {"TWSE", "TPEX"}:
            continue

        instrument_type = normalize_text(
            raw.get("type")
            or raw.get("instrument_type")
        ).upper()

        if instrument_type not in {"STOCK", "ETF"}:
            continue

        listed_date = parse_date(
            raw.get("listed_date")
        )

        result.append(
            {
                "symbol": normalized_symbol,
                "code": normalized_symbol,
                "name": normalize_text(
                    raw.get("name")
                ),
                "market": market,
                "type": instrument_type,
                "listed_date": listed_date.isoformat()
                if listed_date
                else None,
            }
        )

    if len(result) < MIN_UNIVERSE:
        raise RuntimeError(
            "Universe contains no usable active instruments."
        )

    result.sort(
        key=lambda item: (
            item["market"],
            item["symbol"],
        )
    )

    return result


# ============================================================================
# LIFECYCLE
# ============================================================================


def is_not_started(
    item: Dict[str, Any],
    end_date: date,
) -> bool:

    listed_date = parse_date(
        item.get("listed_date")
    )

    if listed_date is None:
        return False

    return listed_date > end_date


def get_not_started_symbols(
    universe: List[Dict[str, Any]],
    end_date: date,
) -> Set[str]:

    return {
        item["symbol"]
        for item in universe
        if is_not_started(item, end_date)
    }


# ============================================================================
# PRICE NORMALIZATION
# ============================================================================


def normalize_price_row(
    *,
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

    open_price = parse_float(open_value)
    high_price = parse_float(high_value)
    low_price = parse_float(low_value)
    close_price = parse_float(close_value)
    volume = parse_int(volume_value)

    row = {
        "date": target_date.isoformat(),
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": close_price,
        "volume": volume,
    }

    if not valid_ohlcv_row(row):
        return None

    return row


# ============================================================================
# TWSE
# ============================================================================


def fetch_twse_daily_batch(
    target_date: date,
) -> Tuple[Dict[str, Dict[str, Any]], Optional[str]]:

    params = {
        "response": "json",
        "date": target_date.strftime("%Y%m%d"),
        "type": "ALLBUT0999",
    }

    try:
        data = http_get_json(
            TWSE_DAILY_URL,
            params=params,
        )
    except Exception as exc:
        return {}, f"twse_official_failed:{exc}"

    if not isinstance(data, dict):
        return {}, "twse_official_failed:invalid_json_root"

    tables = data.get("data")

    if not isinstance(tables, list):
        return {}, "twse_official_failed:no_data"

    result: Dict[str, Dict[str, Any]] = {}

    for raw_row in tables:

        if not isinstance(raw_row, list):
            continue

        if len(raw_row) < 9:
            continue

        symbol = normalize_symbol(raw_row[0])

        if not symbol:
            continue

        # TWSE MI_INDEX 常見欄位：
        # 0 代號
        # 1 名稱
        # 2 成交股數
        # 3 成交筆數
        # 4 成交金額
        # 5 開盤
        # 6 最高
        # 7 最低
        # 8 收盤

        row = normalize_price_row(
            target_date=target_date,
            symbol=symbol,
            open_value=raw_row[5],
            high_value=raw_row[6],
            low_value=raw_row[7],
            close_value=raw_row[8],
            volume_value=raw_row[2],
        )

        if row is not None:
            result[symbol] = row

    if not result:
        return {}, "twse_official_failed:empty_valid_rows"

    return result, None


# ============================================================================
# TPEX HTML TABLE PARSER
# ============================================================================


class TpexTableParser(HTMLParser):

    def __init__(self) -> None:
        super().__init__(
            convert_charrefs=True
        )

        self.rows: List[List[str]] = []

        self._inside_tr = False
        self._inside_cell = False
        self._cell_tag: Optional[str] = None

        self._current_row: List[str] = []
        self._current_cell: List[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: List[Tuple[str, Optional[str]]],
    ) -> None:

        tag = tag.lower()

        if tag == "tr":
            self._inside_tr = True
            self._current_row = []

        elif (
            tag in {"td", "th"}
            and self._inside_tr
        ):
            self._inside_cell = True
            self._cell_tag = tag
            self._current_cell = []

        elif (
            tag == "br"
            and self._inside_cell
        ):
            self._current_cell.append(" ")

    def handle_endtag(
        self,
        tag: str,
    ) -> None:

        tag = tag.lower()

        if (
            tag in {"td", "th"}
            and self._inside_cell
        ):
            text = normalize_text(
                "".join(self._current_cell)
            )

            self._current_row.append(text)

            self._current_cell = []
            self._inside_cell = False
            self._cell_tag = None

        elif tag == "tr":
            if self._inside_tr:

                if self._current_row:
                    self.rows.append(
                        self._current_row
                    )

            self._inside_tr = False
            self._current_row = []
            self._inside_cell = False
            self._cell_tag = None

    def handle_data(
        self,
        data: str,
    ) -> None:

        if self._inside_cell:
            self._current_cell.append(data)


# ============================================================================
# TPEX HEADER DETECTION
# ============================================================================


def find_column_index(
    headers: List[str],
    aliases: Iterable[str],
) -> Optional[int]:

    normalized_headers = [
        normalize_header(header)
        for header in headers
    ]

    normalized_aliases = [
        normalize_header(alias)
        for alias in aliases
    ]

    # exact first
    for alias in normalized_aliases:
        for index, header in enumerate(
            normalized_headers
        ):
            if header == alias:
                return index

    # substring second
    for alias in normalized_aliases:
        for index, header in enumerate(
            normalized_headers
        ):
            if alias and alias in header:
                return index

    return None


def find_tpex_columns(
    headers: List[str],
) -> Optional[Dict[str, int]]:

    symbol_index = find_column_index(
        headers,
        (
            "證券代號",
            "股票代號",
            "代號",
            "代碼",
        ),
    )

    close_index = find_column_index(
        headers,
        (
            "收盤價",
            "收盤",
        ),
    )

    open_index = find_column_index(
        headers,
        (
            "開盤價",
            "開盤",
        ),
    )

    high_index = find_column_index(
        headers,
        (
            "最高價",
            "最高",
        ),
    )

    low_index = find_column_index(
        headers,
        (
            "最低價",
            "最低",
        ),
    )

    volume_index = find_column_index(
        headers,
        (
            "成交股數",
            "成交量",
            "成交量(股)",
            "成交股數(股)",
        ),
    )

    indices = {
        "symbol": symbol_index,
        "open": open_index,
        "high": high_index,
        "low": low_index,
        "close": close_index,
        "volume": volume_index,
    }

    if any(
        value is None
        for value in indices.values()
    ):
        return None

    return {
        key: int(value)
        for key, value in indices.items()
        if value is not None
    }


# ============================================================================
# TPEX HTML
# ============================================================================


def fetch_tpex_html_batch(
    target_date: date,
    roc: str,
) -> Tuple[Dict[str, Dict[str, Any]], Optional[str]]:

    params = {
        "l": "zh-tw",
        "o": "htm",
        "d": roc,
        "se": "EW",
    }

    try:
        html = http_get_text(
            TPEX_DAILY_URL,
            params=params,
        )
    except Exception as exc:
        return {}, f"html_http:{exc}"

    if not html or len(html) < 100:
        return {}, "html_empty_response"

    parser = TpexTableParser()

    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:
        return {}, f"html_parse:{exc}"

    rows = parser.rows

    if not rows:
        return {}, "html_no_rows"

    result: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------------
    # Header-based parser
    # ------------------------------------------------------------------------

    for header_index, raw_header in enumerate(rows):

        if not raw_header:
            continue

        columns = find_tpex_columns(
            raw_header
        )

        if columns is None:
            continue

        for raw_row in rows[
            header_index + 1 :
        ]:

            if not raw_row:
                continue

            max_index = max(
                columns.values()
            )

            if len(raw_row) <= max_index:
                continue

            row = normalize_price_row(
                target_date=target_date,
                symbol=raw_row[
                    columns["symbol"]
                ],
                open_value=raw_row[
                    columns["open"]
                ],
                high_value=raw_row[
                    columns["high"]
                ],
                low_value=raw_row[
                    columns["low"]
                ],
                close_value=raw_row[
                    columns["close"]
                ],
                volume_value=raw_row[
                    columns["volume"]
                ],
            )

            if row is not None:
                result[
                    normalize_symbol(
                        raw_row[
                            columns["symbol"]
                        ]
                    )
                ] = row

        if result:
            return result, None

    # ------------------------------------------------------------------------
    # Official fixed-position fallback
    #
    # TPEx official table historically uses:
    # 0 代號
    # 1 名稱
    # 2 收盤
    # 3 漲跌
    # 4 開盤
    # 5 最高
    # 6 最低
    # 7 成交股數
    # ------------------------------------------------------------------------

    for raw_row in rows:

        if len(raw_row) < 8:
            continue

        symbol = normalize_symbol(
            raw_row[0]
        )

        if not symbol:
            continue

        # 避免將 header 當資料
        if symbol in {
            "證券代號",
            "股票代號",
            "代號",
            "代碼",
        }:
            continue

        row = normalize_price_row(
            target_date=target_date,
            symbol=symbol,
            open_value=raw_row[4],
            high_value=raw_row[5],
            low_value=raw_row[6],
            close_value=raw_row[2],
            volume_value=raw_row[7],
        )

        if row is not None:
            result[symbol] = row

    if not result:
        return {}, "html_no_valid_ohlcv_rows"

    return result, None


# ============================================================================
# TPEX JSON
# ============================================================================


def parse_tpex_json_rows(
    data: Any,
    target_date: date,
) -> Dict[str, Dict[str, Any]]:

    if not isinstance(data, dict):
        return {}

    aa_data = data.get("aaData")

    if not isinstance(aa_data, list):
        return {}

    result: Dict[str, Dict[str, Any]] = {}

    for raw_row in aa_data:

        if not isinstance(raw_row, list):
            continue

        if len(raw_row) < 8:
            continue

        row = normalize_price_row(
            target_date=target_date,
            symbol=raw_row[0],
            open_value=raw_row[4],
            high_value=raw_row[5],
            low_value=raw_row[6],
            close_value=raw_row[2],
            volume_value=raw_row[7],
        )

        if row is not None:
            result[
                normalize_symbol(raw_row[0])
            ] = row

    return result


# ============================================================================
# TPEX OFFICIAL BATCH
# ============================================================================


def fetch_tpex_daily_batch(
    target_date: date,
) -> Tuple[Dict[str, Dict[str, Any]], Optional[str]]:

    roc = roc_date(target_date)

    json_error: Optional[str] = None
    html_error: Optional[str] = None

    # ------------------------------------------------------------------------
    # 1. Official JSON
    # ------------------------------------------------------------------------

    json_params = {
        "l": "zh-tw",
        "o": "json",
        "d": roc,
        "se": "EW",
    }

    try:
        data = http_get_json(
            TPEX_DAILY_URL,
            params=json_params,
        )

        result = parse_tpex_json_rows(
            data,
            target_date,
        )

        if result:
            return result, None

        json_error = "json_empty_valid_rows"

    except Exception as exc:
        json_error = str(exc)

    # ------------------------------------------------------------------------
    # 2. Official HTML fallback
    # ------------------------------------------------------------------------

    result, html_error = fetch_tpex_html_batch(
        target_date,
        roc,
    )

    if result:
        return result, None

    return {}, (
        "tpex_official_failed:"
        f"json={json_error};"
        f"html={html_error}"
    )


# ============================================================================
# OFFICIAL MARKET COLLECTION
# ============================================================================


def collect_official_market_data(
    market: str,
    start_date: date,
    end_date: date,
) -> Tuple[
    Dict[str, Dict[str, Dict[str, Any]]],
    Dict[str, int],
    List[str],
]:

    all_data: Dict[
        str,
        Dict[str, Dict[str, Any]]
    ] = {}

    stats = {
        "attempted": 0,
        "success": 0,
        "failed": 0,
    }

    diagnostics: List[str] = []

    current = start_date

    while current <= end_date:

        if not is_weekday(current):
            current += timedelta(days=1)
            continue

        stats["attempted"] += 1

        if market == "TWSE":
            rows, error = fetch_twse_daily_batch(
                current
            )
        elif market == "TPEX":
            rows, error = fetch_tpex_daily_batch(
                current
            )
        else:
            raise RuntimeError(
                f"Unsupported market: {market}"
            )

        if rows:

            stats["success"] += 1

            for symbol, row in rows.items():

                all_data.setdefault(
                    symbol,
                    {}
                )[row["date"]] = row

        else:

            stats["failed"] += 1

            if error:
                diagnostics.append(
                    f"{current.isoformat()}:{error}"
                )

        if REQUEST_DELAY > 0:
            time.sleep(REQUEST_DELAY)

        current += timedelta(days=1)

    return (
        all_data,
        stats,
        diagnostics,
    )


# ============================================================================
# EXISTING DATA
# ============================================================================


def load_existing_prices(
    symbol: str,
) -> Dict[str, Dict[str, Any]]:

    if not OUTPUT_DIR.exists():
        return {}

    manifest_path = (
        OUTPUT_DIR / "manifest.json"
    )

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
        except Exception:
            continue

        stocks = payload.get("stocks")

        if not isinstance(stocks, dict):
            continue

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

    return {}


# ============================================================================
# YAHOO FALLBACK
# ============================================================================


def yahoo_symbol(
    item: Dict[str, Any],
) -> str:

    market = item["market"]

    if market == "TWSE":
        suffix = ".TW"
    elif market == "TPEX":
        suffix = ".TWO"
    else:
        suffix = ".TW"

    return f"{item['symbol']}{suffix}"


def fetch_yahoo_history(
    item: Dict[str, Any],
    start_date: date,
    end_date: date,
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Optional[str],
]:

    symbol = yahoo_symbol(item)

    start_timestamp = int(
        datetime.combine(
            start_date,
            datetime.min.time(),
            tzinfo=timezone.utc,
        ).timestamp()
    )

    end_timestamp = int(
        datetime.combine(
            end_date + timedelta(days=1),
            datetime.min.time(),
            tzinfo=timezone.utc,
        ).timestamp()
    )

    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{symbol}"
    )

    params = {
        "period1": start_timestamp,
        "period2": end_timestamp,
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

            chart = payload.get("chart", {})

            result = chart.get("result")

            if not isinstance(result, list):
                raise RuntimeError(
                    "Yahoo result is not list"
                )

            if not result:
                raise RuntimeError(
                    "Yahoo result is empty"
                )

            data = result[0]

            timestamps = data.get(
                "timestamp"
            )

            indicators = data.get(
                "indicators",
                {},
            )

            quote = indicators.get(
                "quote",
                []
            )

            if not timestamps:
                raise RuntimeError(
                    "Yahoo timestamps empty"
                )

            if not quote:
                raise RuntimeError(
                    "Yahoo quote empty"
                )

            quote_data = quote[0]

            opens = quote_data.get(
                "open",
                []
            )
            highs = quote_data.get(
                "high",
                []
            )
            lows = quote_data.get(
                "low",
                []
            )
            closes = quote_data.get(
                "close",
                []
            )
            volumes = quote_data.get(
                "volume",
                []
            )

            output: Dict[
                str,
                Dict[str, Any]
            ] = {}

            for index, timestamp in enumerate(
                timestamps
            ):

                if index >= len(opens):
                    continue

                try:
                    row_date = datetime.fromtimestamp(
                        timestamp,
                        timezone.utc,
                    ).date()
                except Exception:
                    continue

                if (
                    row_date < start_date
                    or row_date > end_date
                ):
                    continue

                if index >= len(highs):
                    continue

                if index >= len(lows):
                    continue

                if index >= len(closes):
                    continue

                if index >= len(volumes):
                    continue

                row = normalize_price_row(
                    target_date=row_date,
                    symbol=item["symbol"],
                    open_value=opens[index],
                    high_value=highs[index],
                    low_value=lows[index],
                    close_value=closes[index],
                    volume_value=volumes[index],
                )

                if row is not None:
                    output[
                        row["date"]
                    ] = row

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

    return {}, (
        f"yahoo_failed:{last_error}"
    )


# ============================================================================
# MERGE
# ============================================================================


def merge_price_rows(
    official_rows: Dict[
        str,
        Dict[str, Any]
    ],
    existing_rows: Dict[
        str,
        Dict[str, Any]
    ],
    yahoo_rows: Dict[
        str,
        Dict[str, Any]
    ],
) -> List[Dict[str, Any]]:

    merged: Dict[
        str,
        Dict[str, Any]
    ] = {}

    # 1. Existing historical data
    for row_date, row in existing_rows.items():
        if valid_ohlcv_row(row):
            merged[row_date] = row

    # 2. Official data always overrides same-date fallback data
    for row_date, row in official_rows.items():
        if valid_ohlcv_row(row):
            merged[row_date] = row

    # 3. Yahoo only fills unresolved dates
    for row_date, row in yahoo_rows.items():
        if (
            row_date not in merged
            and valid_ohlcv_row(row)
        ):
            merged[row_date] = row

    return [
        merged[key]
        for key in sorted(merged.keys())
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
    }

    diagnostics: List[str] = []

    for item in universe:

        symbol = item["symbol"]

        # --------------------------------------------------------------------
        # Lifecycle gate
        # --------------------------------------------------------------------

        if is_not_started(
            item,
            end_date,
        ):

            stats["not_started"] += 1

            listed_date = item.get(
                "listed_date"
            )

            diagnostics.append(
                f"{symbol}:not_started:{listed_date}"
            )

            results.append(
                {
                    "symbol": symbol,
                    "code": item["code"],
                    "market": item["market"],
                    "type": item["type"],
                    "name": item["name"],
                    "listed_date": listed_date,
                    "source": "not_started",
                    "history_rows": 0,
                    "history_status": "not_started",
                    "latest_date": None,
                    "prices": [],
                }
            )

            continue

        # --------------------------------------------------------------------
        # Existing data
        # --------------------------------------------------------------------

        existing_rows = load_existing_prices(
            symbol
        )

        # --------------------------------------------------------------------
        # Official data
        # --------------------------------------------------------------------

        official_rows = official_data.get(
            symbol,
            {}
        )

        # --------------------------------------------------------------------
        # Determine missing official history
        # --------------------------------------------------------------------

        merged_before_yahoo = merge_price_rows(
            official_rows=official_rows,
            existing_rows=existing_rows,
            yahoo_rows={},
        )

        merged_dates = {
            row["date"]
            for row in merged_before_yahoo
        }

        expected_weekdays = {
            d.isoformat()
            for d in date_range(
                start_date,
                end_date,
            )
            if is_weekday(d)
        }

        missing_count = len(
            expected_weekdays - merged_dates
        )

        expected_count = len(
            expected_weekdays
        )

        coverage = (
            len(
                merged_dates
                & expected_weekdays
            )
            / expected_count
            if expected_count
            else 1.0
        )

        yahoo_rows: Dict[
            str,
            Dict[str, Any]
        ] = {}

        # --------------------------------------------------------------------
        # Yahoo fallback
        #
        # 官方 coverage < threshold 或完全沒有資料才補
        # --------------------------------------------------------------------

        if (
            missing_count > 0
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
                    start_date,
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

        # --------------------------------------------------------------------
        # Final merge
        # --------------------------------------------------------------------

        prices = merge_price_rows(
            official_rows=official_rows,
            existing_rows=existing_rows,
            yahoo_rows=yahoo_rows,
        )

        # --------------------------------------------------------------------
        # Trim strictly to requested range
        # --------------------------------------------------------------------

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
                row_date < start_date
                or row_date > end_date
            ):
                continue

            if valid_ohlcv_row(row):
                filtered_prices.append(row)

        prices = filtered_prices

        # --------------------------------------------------------------------
        # History status
        # --------------------------------------------------------------------

        history_rows = len(prices)

        if history_rows <= 0:
            history_status = "short"
        elif history_rows < 20:
            history_status = "short"
        elif history_rows < 60:
            history_status = "partial"
        else:
            history_status = "complete"

        latest_date = (
            prices[-1]["date"]
            if prices
            else None
        )

        source_parts: List[str] = []

        if official_rows:
            source_parts.append(
                "official"
            )

        if yahoo_rows:
            source_parts.append(
                "yahoo"
            )

        source = (
            "+".join(source_parts)
            if source_parts
            else "none"
        )

        results.append(
            {
                "symbol": symbol,
                "code": item["code"],
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
# VALIDATE RESULT
# ============================================================================


def validate_result_record(
    result: Dict[str, Any],
    *,
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

    listed_date = parse_date(
        result.get("listed_date")
    )

    not_started = (
        listed_date is not None
        and listed_date > end_date
    )

    if not_started:

        if prices:
            raise RuntimeError(
                f"{symbol}: not_started "
                "must not contain price rows"
            )

        if result.get(
            "history_status"
        ) != "not_started":

            raise RuntimeError(
                f"{symbol}: invalid "
                "not_started history_status"
            )

        if result.get("source") != "not_started":
            raise RuntimeError(
                f"{symbol}: invalid "
                "not_started source"
            )

        if result.get(
            "history_rows"
        ) != 0:

            raise RuntimeError(
                f"{symbol}: not_started "
                "history_rows must be 0"
            )

        return

    # ------------------------------------------------------------------------
    # Started instrument
    # ------------------------------------------------------------------------

    if len(prices) < MIN_HISTORY_ROWS:
        raise RuntimeError(
            f"{symbol}: started instrument "
            "has no valid price history"
        )

    dates: Set[str] = set()

    previous_date: Optional[str] = None

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

        if row_date in dates:
            raise RuntimeError(
                f"{symbol}: duplicate date "
                f"{row_date}"
            )

        dates.add(row_date)

        if (
            previous_date is not None
            and row_date <= previous_date
        ):
            raise RuntimeError(
                f"{symbol}: price dates "
                "are not strictly ascending"
            )

        previous_date = row_date

    history_rows = result.get(
        "history_rows"
    )

    if history_rows != len(prices):
        raise RuntimeError(
            f"{symbol}: history_rows mismatch"
        )

    latest_date = (
        prices[-1]["date"]
        if prices
        else None
    )

    if result.get(
        "latest_date"
    ) != latest_date:

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


# ============================================================================
# VALIDATE ALL RESULTS
# ============================================================================


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
            "Universe → Result missing symbols: "
            + ", ".join(sorted(missing))
        )

    if extra:
        raise RuntimeError(
            "Result contains extra symbols: "
            + ", ".join(sorted(extra))
        )

    if len(results) != len(universe):
        raise RuntimeError(
            "Universe → Result count mismatch: "
            f"{len(universe)} != {len(results)}"
        )

    for result in results:
        validate_result_record(
            result,
            end_date=end_date,
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

    for index in range(
        0,
        len(ordered),
        SHARD_SIZE,
    ):

        chunk = ordered[
            index : index + SHARD_SIZE
        ]

        stocks: Dict[
            str,
            List[Dict[str, Any]]
        ] = {}

        for result in chunk:
            stocks[
                result["symbol"]
            ] = result["prices"]

        shard_number = (
            index // SHARD_SIZE
        ) + 1

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
# VALIDATE SHARD
# ============================================================================


def validate_shard(
    path: Path,
    expected_symbols: Set[str],
    not_started_symbols: Optional[Set[str]] = None,
) -> None:

    if not_started_symbols is None:
        not_started_symbols = set()

    try:
        payload = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except Exception as exc:
        raise RuntimeError(
            f"Shard invalid JSON: {path}: {exc}"
        ) from exc

    stocks = payload.get("stocks")

    if not isinstance(stocks, dict):
        raise RuntimeError(
            f"Shard stocks must be dict: {path}"
        )

    actual_symbols = {
        normalize_symbol(symbol)
        for symbol in stocks.keys()
    }

    if actual_symbols != expected_symbols:
        missing = expected_symbols - actual_symbols
        extra = actual_symbols - expected_symbols

        raise RuntimeError(
            f"Shard symbol mismatch: {path}; "
            f"missing={sorted(missing)}; "
            f"extra={sorted(extra)}"
        )

    for symbol in expected_symbols:

        prices = stocks.get(symbol)

        if not isinstance(prices, list):
            raise RuntimeError(
                f"Shard {path}: "
                f"{symbol} prices must be list"
            )

        # ------------------------------------------------------------
        # Lifecycle exception
        # ------------------------------------------------------------

        if symbol in not_started_symbols:

            if len(prices) != 0:
                raise RuntimeError(
                    f"Shard {path}: "
                    f"{symbol} not_started "
                    "must have zero rows"
                )

            continue

        # ------------------------------------------------------------
        # Started instruments
        # ------------------------------------------------------------

        if len(prices) < MIN_HISTORY_ROWS:
            raise RuntimeError(
                f"Shard {path}: "
                f"{symbol} has no valid history"
            )

        dates: Set[str] = set()

        previous_date: Optional[str] = None

        for row in prices:

            if not valid_ohlcv_row(row):
                raise RuntimeError(
                    f"Shard {path}: "
                    f"{symbol} malformed OHLCV"
                )

            row_date = normalize_text(
                row.get("date")
            )

            if row_date in dates:
                raise RuntimeError(
                    f"Shard {path}: "
                    f"{symbol} duplicate date "
                    f"{row_date}"
                )

            dates.add(row_date)

            if (
                previous_date is not None
                and row_date <= previous_date
            ):
                raise RuntimeError(
                    f"Shard {path}: "
                    f"{symbol} dates not ascending"
                )

            previous_date = row_date


# ============================================================================
# MANIFEST
# ============================================================================


def build_manifest(
    results: List[Dict[str, Any]],
    shard_files: List[str],
) -> Dict[str, Any]:

    total = len(results)

    complete_count = sum(
        1
        for result in results
        if result.get("history_status")
        == "complete"
    )

    partial_count = sum(
        1
        for result in results
        if result.get("history_status")
        == "partial"
    )

    short_count = sum(
        1
        for result in results
        if result.get("history_status")
        == "short"
    )

    not_started_count = sum(
        1
        for result in results
        if result.get("history_status")
        == "not_started"
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "version": VERSION,
        "generated_at": datetime.now(
            TAIWAN_TZ
        ).isoformat(),
        "total_symbols": total,
        "complete_count": complete_count,
        "partial_count": partial_count,
        "short_count": short_count,
        "not_started_count": not_started_count,
        "files": shard_files,
    }


# ============================================================================
# MANIFEST VALIDATION
# ============================================================================


def validate_manifest(
    manifest: Dict[str, Any],
    *,
    expected_total: int,
    expected_complete: int,
    expected_partial: int,
    expected_short: int,
    expected_not_started: int,
    expected_files: List[str],
) -> None:

    if manifest.get(
        "schema_version"
    ) != SCHEMA_VERSION:

        raise RuntimeError(
            "Manifest schema_version mismatch"
        )

    if manifest.get(
        "version"
    ) != VERSION:

        raise RuntimeError(
            "Manifest version mismatch"
        )

    if manifest.get(
        "total_symbols"
    ) != expected_total:

        raise RuntimeError(
            "Manifest total_symbols mismatch"
        )

    if manifest.get(
        "complete_count"
    ) != expected_complete:

        raise RuntimeError(
            "Manifest complete_count mismatch"
        )

    if manifest.get(
        "partial_count"
    ) != expected_partial:

        raise RuntimeError(
            "Manifest partial_count mismatch"
        )

    if manifest.get(
        "short_count"
    ) != expected_short:

        raise RuntimeError(
            "Manifest short_count mismatch"
        )

    if manifest.get(
        "not_started_count"
    ) != expected_not_started:

        raise RuntimeError(
            "Manifest not_started_count mismatch"
        )

    files = manifest.get("files")

    if not isinstance(files, list):
        raise RuntimeError(
            "Manifest files must be list"
        )

    if files != expected_files:
        raise RuntimeError(
            "Manifest files mismatch"
        )


# ============================================================================
# ATOMIC WRITE
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
    )

    temp_path = Path(temp_name)

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
            temp_path,
            path,
        )

    finally:

        if temp_path.exists():
            temp_path.unlink(
                missing_ok=True
            )


# ============================================================================
# WRITE PRICE DIRECTORY
# ============================================================================


def write_price_directory(
    results: List[Dict[str, Any]],
    universe: List[Dict[str, Any]],
    end_date: date,
) -> Path:

    OUTPUT_DIR.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_dir = Path(
        tempfile.mkdtemp(
            prefix=TMP_PREFIX,
            dir=str(
                OUTPUT_DIR.parent
            ),
        )
    )

    try:

        shards = build_shards(
            results
        )

        shard_files: List[str] = []

        for shard in shards:

            shard_number = shard[
                "shard"
            ]

            file_name = (
                f"prices_{shard_number:03d}.json"
            )

            shard_files.append(
                file_name
            )

            atomic_write_json(
                temp_dir / file_name,
                shard,
            )

        manifest = build_manifest(
            results,
            shard_files,
        )

        atomic_write_json(
            temp_dir / "manifest.json",
            manifest,
        )

        # ------------------------------------------------------------
        # Validate temp output before replace
        # ------------------------------------------------------------

        universe_symbols = {
            item["symbol"]
            for item in universe
        }

        not_started_symbols = (
            get_not_started_symbols(
                universe,
                end_date,
            )
        )

        for shard, file_name in zip(
            shards,
            shard_files,
        ):

            shard_symbols = {
                normalize_symbol(symbol)
                for symbol in shard[
                    "stocks"
                ].keys()
            }

            validate_shard(
                temp_dir / file_name,
                shard_symbols,
                not_started_symbols
                & shard_symbols,
            )

        complete_count = sum(
            1
            for result in results
            if result.get(
                "history_status"
            ) == "complete"
        )

        partial_count = sum(
            1
            for result in results
            if result.get(
                "history_status"
            ) == "partial"
        )

        short_count = sum(
            1
            for result in results
            if result.get(
                "history_status"
            ) == "short"
        )

        not_started_count = sum(
            1
            for result in results
            if result.get(
                "history_status"
            ) == "not_started"
        )

        validate_manifest(
            manifest,
            expected_total=len(
                universe_symbols
            ),
            expected_complete=complete_count,
            expected_partial=partial_count,
            expected_short=short_count,
            expected_not_started=(
                not_started_count
            ),
            expected_files=shard_files,
        )

        # ------------------------------------------------------------
        # Replace output atomically at directory level
        # ------------------------------------------------------------

        backup_dir: Optional[Path] = None

        if OUTPUT_DIR.exists():

            backup_dir = OUTPUT_DIR.with_name(
                OUTPUT_DIR.name
                + ".backup"
            )

            if backup_dir.exists():
                shutil.rmtree(
                    backup_dir
                )

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
            shutil.rmtree(
                backup_dir
            )

        return OUTPUT_DIR

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

    manifest_path = (
        OUTPUT_DIR / "manifest.json"
    )

    if not manifest_path.exists():
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
            f"Unable to read manifest: {exc}"
        ) from exc

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

    expected_symbols = {
        item["symbol"]
        for item in universe
    }

    not_started_symbols = (
        get_not_started_symbols(
            universe,
            end_date,
        )
    )

    seen_symbols: Set[str] = set()

    for file_name in shard_files:

        path = (
            OUTPUT_DIR
            / str(file_name)
        )

        if not path.exists():
            raise RuntimeError(
                f"Manifest shard missing: "
                f"{file_name}"
            )

        payload = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

        stocks = payload.get(
            "stocks"
        )

        if not isinstance(
            stocks,
            dict,
        ):
            raise RuntimeError(
                f"Shard stocks invalid: "
                f"{file_name}"
            )

        shard_symbols = {
            normalize_symbol(symbol)
            for symbol in stocks.keys()
        }

        validate_shard(
            path,
            shard_symbols,
            not_started_symbols
            & shard_symbols,
        )

        overlap = (
            seen_symbols
            & shard_symbols
        )

        if overlap:
            raise RuntimeError(
                "Duplicate symbols across shards: "
                + ", ".join(
                    sorted(overlap)
                )
            )

        seen_symbols.update(
            shard_symbols
        )

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

    complete_count = sum(
        1
        for result in results
        if result.get(
            "history_status"
        ) == "complete"
    )

    partial_count = sum(
        1
        for result in results
        if result.get(
            "history_status"
        ) == "partial"
    )

    short_count = sum(
        1
        for result in results
        if result.get(
            "history_status"
        ) == "short"
    )

    not_started_count = sum(
        1
        for result in results
        if result.get(
            "history_status"
        ) == "not_started"
    )

    validate_manifest(
        manifest,
        expected_total=len(
            expected_symbols
        ),
        expected_complete=complete_count,
        expected_partial=partial_count,
        expected_short=short_count,
        expected_not_started=(
            not_started_count
        ),
        expected_files=[
            str(file_name)
            for file_name in shard_files
        ],
    )


# ============================================================================
# REPORT
# ============================================================================


def print_report(
    universe: List[Dict[str, Any]],
    results: List[Dict[str, Any]],
    twse_stats: Dict[str, int],
    tpex_stats: Dict[str, int],
    yahoo_stats: Dict[str, int],
    diagnostics: List[str],
) -> None:

    total = len(universe)

    complete = sum(
        1
        for result in results
        if result.get(
            "history_status"
        ) == "complete"
    )

    partial = sum(
        1
        for result in results
        if result.get(
            "history_status"
        ) == "partial"
    )

    short = sum(
        1
        for result in results
        if result.get(
            "history_status"
        ) == "short"
    )

    not_started = sum(
        1
        for result in results
        if result.get(
            "history_status"
        ) == "not_started"
    )

    valid_started = (
        complete
        + partial
        + short
    )

    success_rate = (
        (
            valid_started
            + not_started
        )
        / total
        * 100
        if total
        else 0.0
    )

    print()
    print("=" * 72)
    print("PRICE DATA VALIDATION")
    print("=" * 72)

    print(
        f"Universe：{total}"
    )

    print(
        f"Price：{len(results)}"
    )

    print(
        f"Complete：{complete}"
    )

    print(
        f"Partial：{partial}"
    )

    print(
        f"Short：{short}"
    )

    print(
        f"Not started：{not_started}"
    )

    print(
        f"成功率：{success_rate:.2f}%"
    )

    print()

    print(
        "TWSE 官方交易日嘗試："
        f"{twse_stats['attempted']}"
    )

    print(
        "TWSE 成功："
        f"{twse_stats['success']}"
    )

    print(
        "TWSE 失敗："
        f"{twse_stats['failed']}"
    )

    print()

    print(
        "TPEx 官方交易日嘗試："
        f"{tpex_stats['attempted']}"
    )

    print(
        "TPEx 成功："
        f"{tpex_stats['success']}"
    )

    print(
        "TPEx 失敗："
        f"{tpex_stats['failed']}"
    )

    print()

    print(
        "Yahoo fallback 嘗試："
        f"{yahoo_stats['yahoo_attempted']}"
    )

    print(
        "Yahoo fallback 成功："
        f"{yahoo_stats['yahoo_success']}"
    )

    print(
        "Yahoo fallback 失敗："
        f"{yahoo_stats['yahoo_failed']}"
    )

    if diagnostics:

        print()
        print(
            f"Diagnostics：{len(diagnostics)}"
        )

        for diagnostic in diagnostics[:50]:
            print(
                f"  {diagnostic}"
            )

        if len(diagnostics) > 50:
            print(
                f"  ... "
                f"{len(diagnostics) - 50} more"
            )

    print("=" * 72)


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

    print(
        f"Universe：{UNIVERSE_FILE}"
    )

    print(
        f"Output：{OUTPUT_DIR}"
    )

    print()

    # ------------------------------------------------------------------------
    # Date range
    # ------------------------------------------------------------------------

    start_date = date(
        2026,
        1,
        5,
    )

    end_date = today_taiwan()

    if start_date > end_date:
        raise RuntimeError(
            "Invalid date range"
        )

    print(
        f"資料日期："
        f"{start_date.isoformat()} "
        f"~ "
        f"{end_date.isoformat()}"
    )

    print()

    # ------------------------------------------------------------------------
    # Load Universe
    # ------------------------------------------------------------------------

    universe = load_universe()

    print(
        f"Universe total："
        f"{len(universe)}"
    )

    twse_universe = [
        item
        for item in universe
        if item["market"] == "TWSE"
    ]

    tpex_universe = [
        item
        for item in universe
        if item["market"] == "TPEX"
    ]

    print(
        f"TWSE："
        f"{len(twse_universe)}"
    )

    print(
        f"TPEx："
        f"{len(tpex_universe)}"
    )

    not_started_symbols = (
        get_not_started_symbols(
            universe,
            end_date,
        )
    )

    print(
        f"Not started："
        f"{len(not_started_symbols)}"
    )

    if not_started_symbols:
        print(
            "Lifecycle："
            + ", ".join(
                sorted(
                    not_started_symbols
                )
            )
        )

    print()

    # ------------------------------------------------------------------------
    # Official TWSE
    # ------------------------------------------------------------------------

    print("=" * 72)
    print("FETCH OFFICIAL TWSE")
    print("=" * 72)

    twse_data, twse_stats, twse_diag = (
        collect_official_market_data(
            "TWSE",
            start_date,
            end_date,
        )
    )

    print(
        f"TWSE symbols with data："
        f"{len(twse_data)}"
    )

    # ------------------------------------------------------------------------
    # Official TPEx
    # ------------------------------------------------------------------------

    print()
    print("=" * 72)
    print("FETCH OFFICIAL TPEX")
    print("=" * 72)

    tpex_data, tpex_stats, tpex_diag = (
        collect_official_market_data(
            "TPEX",
            start_date,
            end_date,
        )
    )

    print(
        f"TPEx symbols with data："
        f"{len(tpex_data)}"
    )

    # ------------------------------------------------------------------------
    # Combine official
    # ------------------------------------------------------------------------

    official_data: Dict[
        str,
        Dict[str, Dict[str, Any]]
    ] = {}

    for symbol, rows in twse_data.items():
        official_data[
            symbol
        ] = rows

    for symbol, rows in tpex_data.items():

        if symbol not in official_data:
            official_data[
                symbol
            ] = rows

        else:

            official_data[
                symbol
            ].update(rows)

    # ------------------------------------------------------------------------
    # Build results
    # ------------------------------------------------------------------------

    print()
    print("=" * 72)
    print("BUILD PRICE RESULTS")
    print("=" * 72)

    results, yahoo_stats, build_diag = (
        build_results(
            universe,
            start_date,
            end_date,
            official_data,
        )
    )

    diagnostics = (
        twse_diag
        + tpex_diag
        + build_diag
    )

    # ------------------------------------------------------------------------
    # Validate before writing
    # ------------------------------------------------------------------------

    print()
    print("=" * 72)
    print("VALIDATE RESULTS BEFORE WRITE")
    print("=" * 72)

    validate_results(
        universe,
        results,
        end_date,
    )

    print(
        "✓ Universe → Result validation passed"
    )

    # ------------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------------

    print()
    print("=" * 72)
    print("WRITE PRICE OUTPUT")
    print("=" * 72)

    output_path = write_price_directory(
        results,
        universe,
        end_date,
    )

    print(
        f"✓ Output：{output_path}"
    )

    # ------------------------------------------------------------------------
    # Final validation
    # ------------------------------------------------------------------------

    print()
    print("=" * 72)
    print("FINAL VALIDATION")
    print("=" * 72)

    validate_complete_output(
        universe,
        results,
        end_date,
    )

    print(
        "✓ Manifest validation passed"
    )

    print(
        "✓ Shard validation passed"
    )

    print(
        "✓ Universe → Price validation passed"
    )

    print(
        "✓ Read-back validation passed"
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

    print()
    print("=" * 72)
    print("PRICE FETCH COMPLETED")
    print("=" * 72)

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )