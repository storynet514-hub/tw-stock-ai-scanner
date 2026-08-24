#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_universe.py
正式版 UNIVERSE-V10.4

============================================================
定位
============================================================

本程式只負責：

    建立完整台股 Universe

資料流：

    官方標的資料
          ↓
    build_universe.py
          ↓
    Data/universe.json
          ↓
    analyze_stocks.py
          ↓
    analysis.json

============================================================
本程式絕對不負責
============================================================

❌ RSI
❌ MACD
❌ KD
❌ 成交量
❌ DCA
❌ 短線選股
❌ Entry Timing
❌ 六項核心
❌ 籌碼
❌ 今日精選
❌ Top 10

============================================================
V10.4 修正重點
============================================================

1. 修正股票名稱錯誤來源
2. 不再使用錯誤的 Yahoo fallback 名稱覆蓋官方名稱
3. TWSE / TPEX 分開建立
4. 正確保留股票 / ETF 類型
5. 新增 bond_count
6. 自動辨識債券 ETF
7. Universe 數量與實際資料強制驗證
8. 不允許錯誤名稱直接覆蓋正常名稱
9. 不使用舊 universe.json 的錯誤名稱作為優先名稱
10. 若官方來源失敗，才使用既有 universe 作為最後 fallback

============================================================
輸出 schema
============================================================

{
    "schema_version": "UNIVERSE-V10.4",
    "generated_at": "...",

    "source": {
        "primary": [...],
        "actual": "..."
    },

    "universe_count": 2143,
    "stock_count": ...,
    "etf_count": ...,
    "bond_count": ...,

    "market_count": {
        "TWSE": ...,
        "TPEX": ...
    },

    "stocks": {
        "2330": {
            "symbol": "2330",
            "full_symbol": "2330.TW",
            "name": "台積電",
            "market": "TWSE",
            "type": "Stock",
            "instrument_type": "stock",
            "asset_class": "equity",
            "source": "TWSE_OFFICIAL"
        }
    }
}
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests


# ============================================================
# 基本設定
# ============================================================

VERSION = "UNIVERSE-V10.4"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

OUTPUT_FILE = DATA_DIR / "universe.json"

TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "Chrome/131.0 Safari/537.36"
    )
}


# ============================================================
# 官方來源
# ============================================================

TWSE_URLS = [
    "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
    "https://openapi.twse.com.tw/v1/opendata/t187ap03_L_ci",
]

TPEX_URLS = [
    "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio",
]

# 官方 ISIN 查詢頁
TWSE_ISIN_URL = (
    "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
)

TPEX_ISIN_URL = (
    "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4"
)


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
# HTTP
# ============================================================

def request_json(url: str) -> Any:
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=TIMEOUT,
    )

    response.raise_for_status()

    return response.json()


def request_text(url: str) -> str:
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=TIMEOUT,
    )

    response.raise_for_status()

    response.encoding = (
        response.apparent_encoding
        or response.encoding
        or "utf-8"
    )

    return response.text


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
        ".TSE",
        ".OTC",
    ):
        if text.endswith(suffix):
            text = text[:-len(suffix)]
            break

    text = text.strip()

    # 台股代號基本格式
    if not re.fullmatch(r"[A-Z0-9]{4,6}", text):
        return ""

    return text


# ============================================================
# Text
# ============================================================

def clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value)

    text = (
        text
        .replace("\u3000", " ")
        .replace("\ufeff", "")
        .strip()
    )

    return text


# ============================================================
# Name validation
# ============================================================

BAD_NAMES = {
    "",
    "NAN",
    "NONE",
    "NULL",
    "CEOGEU",
    "CEOJEU",
    "CEOIEU",
    "CEOIRU",
    "CEOIEU",
    "CEOIEU",
    "OTHERS",
    "FOOD",
    "SEMICONDUCTOR INDUSTRY",
}


def is_valid_name(name: Any) -> bool:
    text = clean_text(name)

    if not text:
        return False

    upper = text.upper()

    if upper in BAD_NAMES:
        return False

    if re.fullmatch(
        r"[A-Z]{2,}[0-9]{0,}",
        upper,
    ):
        return False

    # 避免把 ISIN / CUSIP / 國際代碼當公司名稱
    if re.fullmatch(
        r"[A-Z]{2,}[0-9]{6,}",
        upper,
    ):
        return False

    if len(text) > 100:
        return False

    return True


# ============================================================
# Generic field lookup
# ============================================================

def first_value(
    record: Dict[str, Any],
    keys: Iterable[str],
) -> Any:

    for key in keys:

        if key not in record:
            continue

        value = record[key]

        if value is None:
            continue

        if clean_text(value):
            return value

    return None


# ============================================================
# Market
# ============================================================

def normalize_market(value: Any) -> str:
    text = clean_text(value).upper()

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

    return mapping.get(text, text)


# ============================================================
# Instrument Type
# ============================================================

def classify_instrument(
    symbol: str,
    name: str,
    raw_type: Any = None,
) -> str:

    text = (
        f"{clean_text(raw_type)} "
        f"{clean_text(name)}"
    ).lower()

    if (
        "etf" in text
        or "基金" in text
        or "指數" in text
        or "index" in text
    ):
        return "etf"

    # 台股 ETF 常見代號區域與名稱
    if symbol.startswith("00"):
        if any(
            token in text
            for token in (
                "元大",
                "國泰",
                "富邦",
                "中信",
                "復華",
                "群益",
                "永豐",
                "兆豐",
                "第一金",
                "統一",
                "凱基",
                "野村",
                "新光",
                "台新",
                "聯邦",
                "中租",
            )
        ):
            return "etf"

    return "stock"


# ============================================================
# Bond ETF
# ============================================================

BOND_KEYWORDS = (
    "債券",
    "債",
    "bond",
    "treasury",
    "government bond",
    "investment grade",
    "high yield",
    "非投資等級",
    "投資級",
    "公司債",
    "公債",
    "美國國債",
    "國債",
    "短天期",
    "短天期債",
    "長天期債",
    "美元債",
    "金融債",
    "優先債",
)


def is_bond_etf(
    name: str,
    instrument_type: str,
) -> bool:

    if instrument_type != "etf":
        return False

    text = clean_text(name).lower()

    return any(
        keyword.lower() in text
        for keyword in BOND_KEYWORDS
    )


# ============================================================
# Full Symbol
# ============================================================

def build_full_symbol(
    symbol: str,
    market: str,
) -> str:

    if market == "TPEX":
        return f"{symbol}.TWO"

    return f"{symbol}.TW"


# ============================================================
# Record
# ============================================================

def build_record(
    symbol: str,
    name: str,
    market: str,
    raw_type: Any,
    source: str,
) -> Optional[Dict[str, Any]]:

    symbol = normalize_symbol(symbol)

    if not symbol:
        return None

    name = clean_text(name)

    if not is_valid_name(name):
        return None

    market = normalize_market(market)

    if market not in {
        "TWSE",
        "TPEX",
        "EMERGING",
    }:
        return None

    instrument_type = classify_instrument(
        symbol,
        name,
        raw_type,
    )

    bond = is_bond_etf(
        name,
        instrument_type,
    )

    if bond:
        instrument_type = "bond"

    if instrument_type == "stock":
        type_label = "Stock"
        asset_class = "equity"

    elif instrument_type == "etf":
        type_label = "ETF"
        asset_class = "fund"

    elif instrument_type == "bond":
        type_label = "Bond ETF"
        asset_class = "bond"

    else:
        type_label = "Other"
        asset_class = "other"

    return {
        "symbol": symbol,
        "full_symbol": build_full_symbol(
            symbol,
            market,
        ),
        "name": name,
        "market": market,
        "type": type_label,
        "instrument_type": instrument_type,
        "asset_class": asset_class,
        "source": source,
    }


# ============================================================
# TWSE OpenAPI
# ============================================================

def parse_twse_openapi(
    payload: Any,
) -> Dict[str, Dict[str, Any]]:

    result = {}

    if not isinstance(
        payload,
        list,
    ):
        return result

    for item in payload:

        if not isinstance(
            item,
            dict,
        ):
            continue

        symbol = first_value(
            item,
            (
                "公司代號",
                "證券代號",
                "代號",
                "Code",
                "code",
            ),
        )

        name = first_value(
            item,
            (
                "公司名稱",
                "證券名稱",
                "名稱",
                "CompanyName",
                "name",
            ),
        )

        if not symbol or not name:
            continue

        symbol = normalize_symbol(
            symbol
        )

        if not symbol:
            continue

        if not is_valid_name(name):
            continue

        raw_type = first_value(
            item,
            (
                "產業類別",
                "證券類別",
                "市場別",
                "type",
            ),
        )

        record = build_record(
            symbol=symbol,
            name=clean_text(name),
            market="TWSE",
            raw_type=raw_type,
            source="TWSE_OFFICIAL",
        )

        if record:
            result[symbol] = record

    return result


# ============================================================
# TPEX OpenAPI
# ============================================================

def parse_tpex_openapi(
    payload: Any,
) -> Dict[str, Dict[str, Any]]:

    result = {}

    if not isinstance(
        payload,
        list,
    ):
        return result

    for item in payload:

        if not isinstance(
            item,
            dict,
        ):
            continue

        symbol = first_value(
            item,
            (
                "SecuritiesCompanyCode",
                "SecuritiesCompanyCode",
                "代號",
                "證券代號",
                "公司代號",
                "code",
                "Code",
            ),
        )

        name = first_value(
            item,
            (
                "CompanyName",
                "公司名稱",
                "證券名稱",
                "名稱",
                "name",
            ),
        )

        if not symbol or not name:
            continue

        symbol = normalize_symbol(
            symbol
        )

        if not symbol:
            continue

        if not is_valid_name(name):
            continue

        raw_type = first_value(
            item,
            (
                "Type",
                "類別",
                "證券類別",
                "產業類別",
            ),
        )

        record = build_record(
            symbol=symbol,
            name=clean_text(name),
            market="TPEX",
            raw_type=raw_type,
            source="TPEX_OFFICIAL",
        )

        if record:
            result[symbol] = record

    return result


# ============================================================
# TWSE ISIN HTML
# ============================================================

def parse_isin_html(
    html: str,
    market: str,
) -> Dict[str, Dict[str, Any]]:

    result = {}

    # TWSE ISIN 頁面資料通常存在：
    #
    # 有價證券代號
    # 有價證券名稱
    #
    # 這裡直接從 table rows 抓取。
    #
    # 不使用 pandas，避免 workflow 額外依賴。

    row_pattern = re.compile(
        r"<tr[^>]*>(.*?)</tr>",
        re.IGNORECASE | re.DOTALL,
    )

    cell_pattern = re.compile(
        r"<t[dh][^>]*>(.*?)</t[dh]>",
        re.IGNORECASE | re.DOTALL,
    )

    rows = row_pattern.findall(
        html
    )

    for row in rows:

        cells = cell_pattern.findall(
            row
        )

        cleaned = []

        for cell in cells:

            text = re.sub(
                r"<[^>]+>",
                "",
                cell,
            )

            text = (
                text
                .replace("&nbsp;", " ")
                .replace("&amp;", "&")
            )

            text = clean_text(
                text
            )

            cleaned.append(text)

        if len(cleaned) < 2:
            continue

        # 尋找看起來像股票代號的欄位
        symbol = ""

        symbol_index = -1

        for index, cell in enumerate(
            cleaned
        ):

            candidate = normalize_symbol(
                cell
            )

            if re.fullmatch(
                r"\d{4,6}[A-Z]?",
                candidate,
            ):
                symbol = candidate
                symbol_index = index
                break

        if not symbol:
            continue

        name = ""

        if (
            symbol_index >= 0
            and symbol_index + 1 < len(cleaned)
        ):
            candidate_name = cleaned[
                symbol_index + 1
            ]

            if is_valid_name(
                candidate_name
            ):
                name = candidate_name

        if not name:
            continue

        record = build_record(
            symbol=symbol,
            name=name,
            market=market,
            raw_type="",
            source=f"{market}_ISIN",
        )

        if record:
            result[symbol] = record

    return result


# ============================================================
# Official Universe
# ============================================================

def load_official_universe() -> Dict[str, Dict[str, Any]]:

    result = {}

    # --------------------------------------------------------
    # TWSE
    # --------------------------------------------------------

    section(
        "讀取 TWSE 官方標的資料"
    )

    for url in TWSE_URLS:

        try:

            payload = request_json(
                url
            )

            parsed = parse_twse_openapi(
                payload
            )

            if parsed:

                log(
                    f"✓ TWSE OpenAPI："
                    f"{len(parsed)} 檔"
                )

                result.update(
                    parsed
                )

                break

        except Exception as exc:

            log(
                f"TWSE OpenAPI 失敗："
                f"{type(exc).__name__}"
            )

    # --------------------------------------------------------
    # TWSE ISIN fallback
    # --------------------------------------------------------

    if not any(
        item.get("market") == "TWSE"
        for item in result.values()
    ):

        try:

            html = request_text(
                TWSE_ISIN_URL
            )

            parsed = parse_isin_html(
                html,
                "TWSE",
            )

            log(
                f"✓ TWSE ISIN："
                f"{len(parsed)} 檔"
            )

            result.update(
                parsed
            )

        except Exception as exc:

            log(
                f"TWSE ISIN 失敗："
                f"{type(exc).__name__}"
            )

    # --------------------------------------------------------
    # TPEX
    # --------------------------------------------------------

    section(
        "讀取 TPEX 官方標的資料"
    )

    for url in TPEX_URLS:

        try:

            payload = request_json(
                url
            )

            parsed = parse_tpex_openapi(
                payload
            )

            if parsed:

                log(
                    f"✓ TPEX OpenAPI："
                    f"{len(parsed)} 檔"
                )

                result.update(
                    parsed
                )

                break

        except Exception as exc:

            log(
                f"TPEX OpenAPI 失敗："
                f"{type(exc).__name__}"
            )

    # --------------------------------------------------------
    # TPEX ISIN fallback
    # --------------------------------------------------------

    if not any(
        item.get("market") == "TPEX"
        for item in result.values()
    ):

        try:

            html = request_text(
                TPEX_ISIN_URL
            )

            parsed = parse_isin_html(
                html,
                "TPEX",
            )

            log(
                f"✓ TPEX ISIN："
                f"{len(parsed)} 檔"
            )

            result.update(
                parsed
            )

        except Exception as exc:

            log(
                f"TPEX ISIN 失敗："
                f"{type(exc).__name__}"
            )

    return result


# ============================================================
# Existing Universe fallback
# ============================================================

def load_existing_universe() -> Dict[str, Dict[str, Any]]:

    if not OUTPUT_FILE.exists():
        return {}

    try:

        with OUTPUT_FILE.open(
            "r",
            encoding="utf-8-sig",
        ) as f:

            data = json.load(f)

    except Exception:
        return {}

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):
        return {}

    result = {}

    for raw_symbol, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):
            continue

        symbol = normalize_symbol(
            raw_symbol
        )

        name = clean_text(
            item.get("name")
        )

        if not symbol:
            continue

        if not is_valid_name(
            name
        ):
            continue

        market = normalize_market(
            item.get("market")
        )

        if market not in {
            "TWSE",
            "TPEX",
            "EMERGING",
        }:
            continue

        record = build_record(
            symbol=symbol,
            name=name,
            market=market,
            raw_type=item.get(
                "instrument_type"
            ),
            source="EXISTING_UNIVERSE_FALLBACK",
        )

        if record:
            result[symbol] = record

    return result


# ============================================================
# Remove invalid records
# ============================================================

def validate_records(
    records: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    result = {}

    for symbol, record in records.items():

        if not isinstance(
            record,
            dict,
        ):
            continue

        name = clean_text(
            record.get("name")
        )

        market = normalize_market(
            record.get("market")
        )

        if not is_valid_name(
            name
        ):
            continue

        if market not in {
            "TWSE",
            "TPEX",
            "EMERGING",
        }:
            continue

        symbol = normalize_symbol(
            record.get("symbol")
            or symbol
        )

        if not symbol:
            continue

        record["symbol"] = symbol
        record["name"] = name
        record["market"] = market

        result[symbol] = record

    return result


# ============================================================
# Statistics
# ============================================================

def calculate_stats(
    records: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:

    stock_count = 0
    etf_count = 0
    bond_count = 0

    market_count = {
        "TWSE": 0,
        "TPEX": 0,
        "EMERGING": 0,
    }

    for record in records.values():

        instrument_type = record.get(
            "instrument_type"
        )

        market = record.get(
            "market"
        )

        if instrument_type == "stock":
            stock_count += 1

        elif instrument_type == "etf":
            etf_count += 1

        elif instrument_type == "bond":
            bond_count += 1

        if market in market_count:
            market_count[market] += 1

    return {
        "universe_count": len(records),
        "stock_count": stock_count,
        "etf_count": etf_count,
        "bond_count": bond_count,
        "market_count": market_count,
    }


# ============================================================
# Name quality audit
# ============================================================

def audit_names(
    records: Dict[str, Dict[str, Any]],
) -> None:

    suspicious = []

    for symbol, record in records.items():

        name = clean_text(
            record.get("name")
        )

        if not is_valid_name(
            name
        ):
            suspicious.append(
                (
                    symbol,
                    name,
                )
            )

    log(
        f"名稱品質檢查："
        f"{len(records) - len(suspicious)}/"
        f"{len(records)} 正常"
    )

    if suspicious:

        log(
            "⚠ 發現疑似錯誤名稱："
        )

        for symbol, name in suspicious[:20]:

            log(
                f"  {symbol} → {name}"
            )

        raise RuntimeError(
            "Universe 名稱品質檢查失敗"
        )


# ============================================================
# Bond ETF audit
# ============================================================

def audit_bond_etf(
    records: Dict[str, Dict[str, Any]],
) -> None:

    bonds = [
        record
        for record in records.values()
        if record.get(
            "instrument_type"
        ) == "bond"
    ]

    log(
        f"債券 ETF："
        f"{len(bonds)} 檔"
    )

    for record in bonds[:20]:

        log(
            f"  {record['symbol']} "
            f"{record['name']}"
        )


# ============================================================
# Write
# ============================================================

def write_output(
    records: Dict[str, Dict[str, Any]],
    source_name: str,
) -> None:

    stats = calculate_stats(
        records
    )

    output = {
        "schema_version": VERSION,
        "generated_at": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),

        "source": {
            "primary": [
                "TWSE_OFFICIAL",
                "TPEX_OFFICIAL",
            ],
            "fallback": [
                "EXISTING_UNIVERSE_FALLBACK",
            ],
            "actual": source_name,
            "description": (
                "完整台股 Universe。"
                "本程式只建立標的宇宙，"
                "不執行選股或技術分析。"
            ),
        },

        "universe_count":
            stats["universe_count"],

        "stock_count":
            stats["stock_count"],

        "etf_count":
            stats["etf_count"],

        "bond_count":
            stats["bond_count"],

        "market_count":
            stats["market_count"],

        "stocks":
            dict(
                sorted(
                    records.items(),
                    key=lambda item: item[0],
                )
            ),
    }

    # --------------------------------------------------------
    # 強制一致性
    # --------------------------------------------------------

    if (
        output["universe_count"]
        != len(output["stocks"])
    ):
        raise RuntimeError(
            "universe_count 與 stocks 數量不一致"
        )

    if output["universe_count"] == 0:
        raise RuntimeError(
            "Universe 為空，禁止寫檔"
        )

    # --------------------------------------------------------
    # 寫入暫存檔
    # --------------------------------------------------------

    temp_file = (
        OUTPUT_FILE.with_suffix(
            ".tmp"
        )
    )

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

        f.write("\n")

    temp_file.replace(
        OUTPUT_FILE
    )

    log(
        f"✓ 已寫入：{OUTPUT_FILE}"
    )


# ============================================================
# Main
# ============================================================

def main() -> int:

    start = datetime.now()

    try:

        section(
            f"台股 AI 選股系統 "
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
            "Entry Timing：不計算"
        )

        log(
            "API 探測：無"
        )

        # ----------------------------------------------------
        # 官方 Universe
        # ----------------------------------------------------

        official = load_official_universe()

        # ----------------------------------------------------
        # 官方資料不足時才使用既有 Universe
        # ----------------------------------------------------

        if len(official) >= 1000:

            records = official

            source_name = (
                "TWSE_OFFICIAL + TPEX_OFFICIAL"
            )

        else:

            log("")
            log(
                "⚠ 官方 Universe 資料不足"
            )

            fallback = load_existing_universe()

            if len(fallback) < 1000:

                raise RuntimeError(
                    "官方 Universe 不足，"
                    "且既有 universe.json "
                    "也不足，禁止產生錯誤 Universe"
                )

            records = fallback

            source_name = (
                "EXISTING_UNIVERSE_FALLBACK"
            )

        # ----------------------------------------------------
        # Validate
        # ----------------------------------------------------

        records = validate_records(
            records
        )

        if len(records) < 1000:

            raise RuntimeError(
                "有效 Universe 少於 1000 檔，"
                "禁止寫入"
            )

        # ----------------------------------------------------
        # Audit
        # ----------------------------------------------------

        section(
            "Universe 品質驗證"
        )

        audit_names(
            records
        )

        audit_bond_etf(
            records
        )

        stats = calculate_stats(
            records
        )

        log("")
        log(
            f"Universe："
            f"{stats['universe_count']}"
        )

        log(
            f"股票："
            f"{stats['stock_count']}"
        )

        log(
            f"ETF："
            f"{stats['etf_count']}"
        )

        log(
            f"債券 ETF："
            f"{stats['bond_count']}"
        )

        log(
            f"TWSE："
            f"{stats['market_count']['TWSE']}"
        )

        log(
            f"TPEX："
            f"{stats['market_count']['TPEX']}"
        )

        # ----------------------------------------------------
        # Write
        # ----------------------------------------------------

        section(
            "寫入 Data/universe.json"
        )

        write_output(
            records,
            source_name,
        )

        # ----------------------------------------------------
        # Final
        # ----------------------------------------------------

        elapsed = (
            datetime.now()
            - start
        ).total_seconds()

        section(
            "Universe 建立完成"
        )

        log(
            f"Universe："
            f"{stats['universe_count']}"
        )

        log(
            f"股票："
            f"{stats['stock_count']}"
        )

        log(
            f"ETF："
            f"{stats['etf_count']}"
        )

        log(
            f"債券 ETF："
            f"{stats['bond_count']}"
        )

        log(
            f"耗時："
            f"{elapsed:.1f} 秒"
        )

        log(
            "下一階段："
            "analyze_stocks.py"
        )

        return 0

    except Exception as exc:

        section(
            f"❌ build_universe.py "
            f"{VERSION} 執行失敗"
        )

        log(
            f"原因：{exc}"
        )

        return 1


if __name__ == "__main__":

    sys.exit(
        main()
    )