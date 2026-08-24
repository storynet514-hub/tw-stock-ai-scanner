#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_universe.py
正式版 UNIVERSE-V10.5

============================================================
定位
============================================================

本程式只負責建立完整標的 Universe。

資料流：

    官方標的資料
          ↓
    build_universe.py
          ↓
    Data/universe.json
          ↓
    analyze_stocks.py
          ↓
    Data/analysis.json
          ↓
    build_ui_data.py
          ↓
    Data/ui_data.json
          ↓
    index.html

============================================================
本程式不負責
============================================================

❌ RSI
❌ MACD
❌ KD
❌ 成交量
❌ DCA
❌ 短線選股
❌ Entry Timing
❌ 籌碼
❌ 今日精選
❌ Top 10
❌ 前端 UI

============================================================
V10.5 修正
============================================================

1. 官方名稱優先
2. 禁止錯誤英文分類文字當股票名稱
3. 禁止 ISIN / 國際代碼當名稱
4. TWSE / TPEX 分開處理
5. 股票 / ETF / 債券 ETF 分類
6. 債券 ETF 額外分類
7. 不使用 Yahoo 名稱覆蓋官方名稱
8. 舊 universe.json 僅作最後 fallback
9. fallback 名稱也必須經過名稱驗證
10. Universe schema 強制驗證
11. symbol 不可重複
12. full_symbol 必須正確
13. 不允許空名稱
14. 不允許明顯錯誤分類名稱
15. ETF / Bond ETF 可供 UI 分頁使用
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests


# ============================================================
# 基本設定
# ============================================================

VERSION = "UNIVERSE-V10.5"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

OUTPUT_FILE = DATA_DIR / "universe.json"

TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
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

TWSE_ISIN_URL = (
    "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
)

TPEX_ISIN_URL = (
    "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4"
)


# ============================================================
# 明顯錯誤名稱
# ============================================================

BAD_NAMES = {
    "",
    "NAN",
    "NONE",
    "NULL",
    "UNDEFINED",

    "OTHERS",
    "OTHER",
    "FOOD",
    "SEMICONDUCTOR INDUSTRY",

    "CEOGEU",
    "CEOJEU",
    "CEOIEU",
    "CEOIRU",
    "CEOIEU",
    "CEOIEU",

    "STOCK",
    "ETF",
    "BOND",
    "BOND ETF",
}


# ============================================================
# 債券 ETF 關鍵字
# ============================================================

BOND_KEYWORDS = (
    "債券",
    "債",
    "公司債",
    "金融債",
    "公債",
    "國債",
    "美國國債",
    "美元債",
    "投資級",
    "投資級債",
    "非投資等級",
    "高收益債",
    "高收益",
    "短天期債",
    "長天期債",
    "短債",
    "長債",
    "優先債",
    "bond",
    "bonds",
    "treasury",
    "government bond",
    "corporate bond",
    "investment grade",
    "high yield",
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
# 基本文字處理
# ============================================================

def clean_text(value: Any) -> str:

    if value is None:
        return ""

    text = str(value)

    text = (
        text
        .replace("\ufeff", "")
        .replace("\u3000", " ")
        .replace("\xa0", " ")
        .strip()
    )

    return text


def upper_clean(value: Any) -> str:
    return clean_text(value).upper()


# ============================================================
# Symbol
# ============================================================

def normalize_symbol(value: Any) -> str:

    if value is None:
        return ""

    text = upper_clean(value)

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

    # 台股代號主要為數字，也保留部分特殊英數代號
    if not re.fullmatch(
        r"[A-Z0-9]{4,6}",
        text,
    ):
        return ""

    return text


# ============================================================
# Symbol 是否合理
# ============================================================

def is_valid_symbol(symbol: str) -> bool:

    symbol = normalize_symbol(symbol)

    if not symbol:
        return False

    # 純數字代號
    if symbol.isdigit():
        return 4 <= len(symbol) <= 6

    # 英數混合特殊標的
    return bool(
        re.fullmatch(
            r"[A-Z0-9]{4,6}",
            symbol,
        )
    )


# ============================================================
# 名稱驗證
# ============================================================

def is_valid_name(name: Any) -> bool:

    text = clean_text(name)

    if not text:
        return False

    upper = text.upper()

    if upper in BAD_NAMES:
        return False

    if len(text) > 100:
        return False

    # 純數字不能作為名稱
    if text.isdigit():
        return False

    # 純英文字母 / 英文分類名稱通常不是正式公司名稱
    if re.fullmatch(
        r"[A-Z][A-Z0-9 _\-./]{1,}",
        upper,
    ):
        return False

    # 國際代碼 / ISIN 類型
    if re.fullmatch(
        r"[A-Z]{2,}[0-9]{6,}",
        upper,
    ):
        return False

    # 明顯 ISIN
    if re.fullmatch(
        r"[A-Z]{2}[A-Z0-9]{9}[0-9]",
        upper,
    ):
        return False

    return True


# ============================================================
# Dictionary 欄位搜尋
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

    text = upper_clean(value)

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
# ETF 判斷
# ============================================================

def looks_like_etf(
    symbol: str,
    name: str,
    raw_type: Any = None,
) -> bool:

    text = (
        clean_text(raw_type)
        + " "
        + clean_text(name)
    ).lower()

    keywords = (
        "etf",
        "基金",
        "指數",
        "指數型",
        "被動式",
        "主動式",
        "收益型",
    )

    if any(
        keyword in text
        for keyword in keywords
    ):
        return True

    # 台股常見 ETF 代號區域
    #
    # 注意：
    # 這裡只作輔助，不單獨決定 ETF。
    #
    if symbol.isdigit():

        number = int(symbol)

        if (
            1 <= number <= 999
            or 8000 <= number <= 9999
        ):
            return True

    return False


# ============================================================
# 債券 ETF 判斷
# ============================================================

def looks_like_bond_etf(
    name: str,
    raw_type: Any = None,
) -> bool:

    text = (
        clean_text(name)
        + " "
        + clean_text(raw_type)
    ).lower()

    for keyword in BOND_KEYWORDS:

        if keyword.lower() in text:
            return True

    return False


# ============================================================
# Instrument Type
# ============================================================

def classify_instrument(
    symbol: str,
    name: str,
    raw_type: Any = None,
) -> str:

    if looks_like_bond_etf(
        name,
        raw_type,
    ):
        return "bond"

    if looks_like_etf(
        symbol,
        name,
        raw_type,
    ):
        return "etf"

    return "stock"


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
# 建立 Record
# ============================================================

def build_record(
    symbol: Any,
    name: Any,
    market: Any,
    raw_type: Any,
    source: str,
) -> Optional[Dict[str, Any]]:

    symbol = normalize_symbol(symbol)
    name = clean_text(name)
    market = normalize_market(market)

    if not is_valid_symbol(symbol):
        return None

    if not is_valid_name(name):
        return None

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
# TWSE OpenAPI Parser
# ============================================================

def parse_twse_openapi(
    payload: Any,
) -> Dict[str, Dict[str, Any]]:

    result: Dict[str, Dict[str, Any]] = {}

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

        raw_type = first_value(
            item,
            (
                "證券類別",
                "市場別",
                "產業類別",
                "type",
                "Type",
            ),
        )

        if not symbol or not name:
            continue

        record = build_record(
            symbol=symbol,
            name=name,
            market="TWSE",
            raw_type=raw_type,
            source="TWSE_OFFICIAL",
        )

        if record:

            result[
                record["symbol"]
            ] = record

    return result


# ============================================================
# TPEX OpenAPI Parser
# ============================================================

def parse_tpex_openapi(
    payload: Any,
) -> Dict[str, Dict[str, Any]]:

    result: Dict[str, Dict[str, Any]] = {}

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
                "證券代號",
                "公司代號",
                "代號",
                "Code",
                "code",
            ),
        )

        name = first_value(
            item,
            (
                "CompanyName",
                "證券名稱",
                "公司名稱",
                "名稱",
                "name",
            ),
        )

        raw_type = first_value(
            item,
            (
                "Type",
                "類別",
                "證券類別",
                "產業類別",
            ),
        )

        if not symbol or not name:
            continue

        record = build_record(
            symbol=symbol,
            name=name,
            market="TPEX",
            raw_type=raw_type,
            source="TPEX_OFFICIAL",
        )

        if record:

            result[
                record["symbol"]
            ] = record

    return result


# ============================================================
# HTML / ISIN Parser
# ============================================================

def parse_isin_html(
    html: str,
    market: str,
) -> Dict[str, Dict[str, Any]]:

    result: Dict[str, Dict[str, Any]] = {}

    if not html:
        return result

    # 去除 HTML tag
    text = re.sub(
        r"<[^>]+>",
        " ",
        html,
    )

    text = (
        text
        .replace("&nbsp;", " ")
        .replace("&amp;", "&")
    )

    lines = [
        clean_text(x)
        for x in text.splitlines()
    ]

    lines = [
        x
        for x in lines
        if x
    ]

    for line in lines:

        # 常見 ISIN 表格：
        #
        # 有價證券代號及名稱
        # ISIN Code
        # 上市日
        #
        # 例如：
        #
        # 2330 台積電
        #
        match = re.search(
            r"\b([0-9A-Z]{4,6})\s+(.+)",
            line,
        )

        if not match:
            continue

        symbol = match.group(1)
        name = match.group(2)

        symbol = normalize_symbol(
            symbol
        )

        if not symbol:
            continue

        # 清除後面的日期 / 類別欄位
        name = re.split(
            r"\s{2,}",
            name,
        )[0]

        name = clean_text(name)

        if not is_valid_name(name):
            continue

        record = build_record(
            symbol=symbol,
            name=name,
            market=market,
            raw_type="",
            source=(
                "TWSE_ISIN"
                if market == "TWSE"
                else "TPEX_ISIN"
            ),
        )

        if record:
            result[symbol] = record

    return result


# ============================================================
# 官方來源載入
# ============================================================

def load_twse() -> Dict[str, Dict[str, Any]]:

    section(
        "TWSE 官方資料"
    )

    for url in TWSE_URLS:

        try:

            log(
                f"嘗試：{url}"
            )

            payload = request_json(
                url
            )

            result = parse_twse_openapi(
                payload
            )

            if result:

                log(
                    f"✓ TWSE OpenAPI："
                    f"{len(result)} 檔"
                )

                return result

        except Exception as exc:

            log(
                f"⚠ TWSE來源失敗："
                f"{exc}"
            )

    # fallback：ISIN
    try:

        log(
            "嘗試 TWSE ISIN"
        )

        html = request_text(
            TWSE_ISIN_URL
        )

        result = parse_isin_html(
            html,
            "TWSE",
        )

        if result:

            log(
                f"✓ TWSE ISIN："
                f"{len(result)} 檔"
            )

            return result

    except Exception as exc:

        log(
            f"⚠ TWSE ISIN 失敗："
            f"{exc}"
        )

    return {}


# ============================================================
# TPEX 官方資料
# ============================================================

def load_tpex() -> Dict[str, Dict[str, Any]]:

    section(
        "TPEX 官方資料"
    )

    for url in TPEX_URLS:

        try:

            log(
                f"嘗試：{url}"
            )

            payload = request_json(
                url
            )

            result = parse_tpex_openapi(
                payload
            )

            if result:

                log(
                    f"✓ TPEX OpenAPI："
                    f"{len(result)} 檔"
                )

                return result

        except Exception as exc:

            log(
                f"⚠ TPEX來源失敗："
                f"{exc}"
            )

    try:

        log(
            "嘗試 TPEX ISIN"
        )

        html = request_text(
            TPEX_ISIN_URL
        )

        result = parse_isin_html(
            html,
            "TPEX",
        )

        if result:

            log(
                f"✓ TPEX ISIN："
                f"{len(result)} 檔"
            )

            return result

    except Exception as exc:

        log(
            f"⚠ TPEX ISIN 失敗："
            f"{exc}"
        )

    return {}


# ============================================================
# 舊 Universe fallback
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

    except Exception as exc:

        log(
            f"⚠ 舊 universe.json 無法讀取："
            f"{exc}"
        )

        return {}

    stocks = data.get(
        "stocks",
        {},
    )

    if not isinstance(
        stocks,
        dict,
    ):
        return {}

    result = {}

    for symbol, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):
            continue

        normalized_symbol = normalize_symbol(
            item.get(
                "symbol",
                symbol,
            )
        )

        name = clean_text(
            item.get(
                "name",
                "",
            )
        )

        market = normalize_market(
            item.get(
                "market",
                "",
            )
        )

        if not is_valid_symbol(
            normalized_symbol
        ):
            continue

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

        # 舊資料只能作最後 fallback
        record = build_record(
            symbol=normalized_symbol,
            name=name,
            market=market,
            raw_type=item.get(
                "type",
                "",
            ),
            source="EXISTING_UNIVERSE_FALLBACK",
        )

        if record:
            result[
                normalized_symbol
            ] = record

    return result


# ============================================================
# 合併
# ============================================================

def merge_sources(
    twse: Dict[str, Dict[str, Any]],
    tpex: Dict[str, Dict[str, Any]],
    existing: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    merged: Dict[str, Dict[str, Any]] = {}

    # 官方 TWSE 優先
    for symbol, record in twse.items():
        merged[symbol] = record

    # 官方 TPEX 優先
    for symbol, record in tpex.items():

        if symbol not in merged:
            merged[symbol] = record

    # 舊資料只補官方缺失
    for symbol, record in existing.items():

        if symbol not in merged:
            merged[symbol] = record

    return merged


# ============================================================
# 重新驗證所有資料
# ============================================================

def validate_records(
    stocks: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    result = {}

    for symbol, record in stocks.items():

        if not isinstance(
            record,
            dict,
        ):
            continue

        symbol2 = normalize_symbol(
            record.get(
                "symbol",
                symbol,
            )
        )

        name = clean_text(
            record.get(
                "name",
                "",
            )
        )

        market = normalize_market(
            record.get(
                "market",
                "",
            )
        )

        if not is_valid_symbol(
            symbol2
        ):
            continue

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

        raw_type = record.get(
            "type",
            "",
        )

        instrument_type = classify_instrument(
            symbol2,
            name,
            raw_type,
        )

        # 若名稱明確是債券 ETF，重新分類
        if looks_like_bond_etf(
            name,
            raw_type,
        ):
            instrument_type = "bond"

        if instrument_type == "stock":

            type_label = "Stock"
            asset_class = "equity"

        elif instrument_type == "etf":

            type_label = "ETF"
            asset_class = "fund"

        else:

            type_label = "Bond ETF"
            asset_class = "bond"

        clean_record = {
            "symbol": symbol2,
            "full_symbol": build_full_symbol(
                symbol2,
                market,
            ),
            "name": name,
            "market": market,
            "type": type_label,
            "instrument_type": instrument_type,
            "asset_class": asset_class,
            "source": record.get(
                "source",
                "UNKNOWN",
            ),
        }

        result[symbol2] = clean_record

    return result


# ============================================================
# 統計
# ============================================================

def build_statistics(
    stocks: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:

    stock_count = 0
    etf_count = 0
    bond_count = 0

    twse_count = 0
    tpex_count = 0
    emerging_count = 0

    for record in stocks.values():

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

        if market == "TWSE":
            twse_count += 1

        elif market == "TPEX":
            tpex_count += 1

        elif market == "EMERGING":
            emerging_count += 1

    return {
        "universe_count": len(stocks),
        "stock_count": stock_count,
        "etf_count": etf_count,
        "bond_count": bond_count,
        "market_count": {
            "TWSE": twse_count,
            "TPEX": tpex_count,
            "EMERGING": emerging_count,
        },
    }


# ============================================================
# Schema 驗證
# ============================================================

def validate_output(
    data: Dict[str, Any],
) -> None:

    required_top_level = (
        "schema_version",
        "generated_at",
        "source",
        "universe_count",
        "stock_count",
        "etf_count",
        "bond_count",
        "market_count",
        "stocks",
    )

    for key in required_top_level:

        if key not in data:

            raise RuntimeError(
                f"缺少 schema 欄位：{key}"
            )

    stocks = data["stocks"]

    if not isinstance(
        stocks,
        dict,
    ):
        raise RuntimeError(
            "stocks 必須為 object"
        )

    if data["universe_count"] != len(
        stocks
    ):
        raise RuntimeError(
            "universe_count 與 stocks 數量不一致"
        )

    seen = set()

    for symbol, record in stocks.items():

        if symbol in seen:
            raise RuntimeError(
                f"symbol 重複：{symbol}"
            )

        seen.add(symbol)

        if not isinstance(
            record,
            dict,
        ):
            raise RuntimeError(
                f"{symbol} record 格式錯誤"
            )

        required = (
            "symbol",
            "full_symbol",
            "name",
            "market",
            "type",
            "instrument_type",
            "asset_class",
        )

        for key in required:

            if key not in record:

                raise RuntimeError(
                    f"{symbol} 缺少欄位：{key}"
                )

        if (
            record["symbol"]
            != symbol
        ):
            raise RuntimeError(
                f"{symbol} symbol mismatch"
            )

        if not is_valid_name(
            record["name"]
        ):
            raise RuntimeError(
                f"{symbol} 名稱驗證失敗："
                f"{record['name']}"
            )

        expected_full = build_full_symbol(
            symbol,
            record["market"],
        )

        if record["full_symbol"] != expected_full:

            raise RuntimeError(
                f"{symbol} full_symbol 錯誤："
                f"{record['full_symbol']} "
                f"!= {expected_full}"
            )

    stats = build_statistics(
        stocks
    )

    for key in (
        "universe_count",
        "stock_count",
        "etf_count",
        "bond_count",
    ):

        if data[key] != stats[key]:

            raise RuntimeError(
                f"{key} 統計錯誤："
                f"{data[key]} "
                f"!= {stats[key]}"
            )


# ============================================================
# 寫入 JSON
# ============================================================

def write_output(
    data: Dict[str, Any],
) -> None:

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = OUTPUT_FILE.with_suffix(
        ".json.tmp"
    )

    with temporary.open(
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )

        f.write("\n")

    temporary.replace(
        OUTPUT_FILE
    )


# ============================================================
# 主程式
# ============================================================

def main() -> int:

    start = datetime.now()

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

    # --------------------------------------------------------
    # 官方資料
    # --------------------------------------------------------

    twse = load_twse()

    tpex = load_tpex()

    # --------------------------------------------------------
    # 舊資料
    # --------------------------------------------------------

    section(
        "載入既有 Universe fallback"
    )

    existing = load_existing_universe()

    log(
        f"既有 Universe："
        f"{len(existing)} 檔"
    )

    # --------------------------------------------------------
    # 合併
    # --------------------------------------------------------

    section(
        "建立 Universe"
    )

    merged = merge_sources(
        twse=twse,
        tpex=tpex,
        existing=existing,
    )

    log(
        f"合併後："
        f"{len(merged)} 檔"
    )

    # --------------------------------------------------------
    # 最終驗證
    # --------------------------------------------------------

    stocks = validate_records(
        merged
    )

    log(
        f"最終有效："
        f"{len(stocks)} 檔"
    )

    if not stocks:

        raise RuntimeError(
            "Universe 為空，停止寫入。"
        )

    # --------------------------------------------------------
    # 統計
    # --------------------------------------------------------

    stats = build_statistics(
        stocks
    )

    # --------------------------------------------------------
    # 建立輸出
    # --------------------------------------------------------

    now = datetime.now(
        timezone.utc
    ).isoformat()

    data = {
        "schema_version": VERSION,

        "generated_at": now,

        "source": {
            "primary": [
                "TWSE_OFFICIAL",
                "TPEX_OFFICIAL",
            ],
            "actual": (
                "OFFICIAL_WITH_EXISTING_FALLBACK"
            ),
        },

        "universe_count": stats[
            "universe_count"
        ],

        "stock_count": stats[
            "stock_count"
        ],

        "etf_count": stats[
            "etf_count"
        ],

        "bond_count": stats[
            "bond_count"
        ],

        "market_count": stats[
            "market_count"
        ],

        "stocks": dict(
            sorted(
                stocks.items(),
                key=lambda item: item[0],
            )
        ),
    }

    # --------------------------------------------------------
    # 強制 schema validation
    # --------------------------------------------------------

    section(
        "Universe Schema Validation"
    )

    validate_output(
        data
    )

    log(
        "✓ Schema validation：PASS"
    )

    # --------------------------------------------------------
    # 寫入
    # --------------------------------------------------------

    section(
        "寫入 Data/universe.json"
    )

    write_output(
        data
    )

    log(
        f"✓ 已寫入："
        f"{OUTPUT_FILE}"
    )

    # --------------------------------------------------------
    # 結果
    # --------------------------------------------------------

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
        f"TWSE："
        f"{stats['market_count']['TWSE']}"
    )

    log(
        f"TPEX："
        f"{stats['market_count']['TPEX']}"
    )

    log(
        f"興櫃："
        f"{stats['market_count']['EMERGING']}"
    )

    log(
        f"耗時："
        f"{elapsed:.1f} 秒"
    )

    log(
        f"輸出："
        f"{OUTPUT_FILE}"
    )

    return 0


# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":

    try:

        sys.exit(
            main()
        )

    except KeyboardInterrupt:

        log(
            "❌ 使用者中止"
        )

        sys.exit(130)

    except Exception as exc:

        section(
            f"❌ build_universe.py "
            f"{VERSION} 執行失敗"
        )

        log(
            f"原因：{exc}"
        )

        sys.exit(1)