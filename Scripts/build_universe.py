#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
build_universe.py V5.4.0

============================================================
目的
============================================================
建立 Data/universe.json

核心原則：

1. TWSE / TPEX 資料來源分開處理
2. 單一 API timeout 不得破壞既有 universe.json
3. 優先使用官方 API
4. 官方 API 失敗時啟動備援
5. 所有新資料完成驗證後才允許覆蓋
6. 若新資料不完整，保留既有 universe.json
7. 3081 聯亞必須可以正常進入 TPEX 股票池
8. 不把三大法人、行情資料等誤當股票基本資料
9. Atomic Write
10. GitHub Actions CI/CD 可安全執行

============================================================
V5.4.0 修正
============================================================
- 修正 TWSE OpenAPI timeout 導致整個 workflow 失敗
- 增加多層 TWSE fallback
- 增加既有 universe.json 安全備援
- 增加 TPEX 多來源 fallback
- 強化 4 碼股票代號驗證
- 強化名稱清洗
- 強制保留 3081 聯亞
- 新 universe 必須通過安全門檻才覆蓋
- 失敗時保留既有 universe
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests


# ============================================================
# 基本設定
# ============================================================

VERSION = "V5.4.0"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"
UNIVERSE_FILE = DATA_DIR / "universe.json"

REQUEST_TIMEOUT = 20
RETRY_COUNT = 3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
}


# ============================================================
# 安全門檻
# ============================================================

MIN_TWSE = 700
MIN_TPEX = 300
MIN_TOTAL = 1200

REQUIRED_TEST_STOCKS = {
    "2337": "旺宏",
    "2426": "鼎元",
    "2368": "金像電",
    "3081": "聯亞",
}


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

def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def get_json(
    session: requests.Session,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = REQUEST_TIMEOUT,
    retries: int = RETRY_COUNT,
) -> Any:

    last_error: Optional[Exception] = None

    for attempt in range(1, retries + 1):

        log(f"  HTTP GET attempt {attempt}/{retries}")

        try:
            response = session.get(
                url,
                params=params,
                timeout=timeout,
            )

            log(
                f"  HTTP Status: {response.status_code}"
            )

            response.raise_for_status()

            content_type = (
                response.headers.get(
                    "Content-Type",
                    ""
                ).lower()
            )

            text = response.text.strip()

            if not text:
                raise RuntimeError(
                    "HTTP 回應內容為空"
                )

            # 官方 API 通常為 JSON。
            # 即使 Content-Type 不正確，也嘗試 JSON。
            try:
                return response.json()
            except Exception as json_error:

                # 某些官方頁面可能回傳 BOM
                cleaned = text.lstrip("\ufeff")

                try:
                    return json.loads(cleaned)
                except Exception:
                    raise RuntimeError(
                        f"JSON 解析失敗 "
                        f"(Content-Type={content_type})"
                    ) from json_error

        except Exception as exc:

            last_error = exc

            log(
                f"  ⚠️ attempt {attempt} 失敗：{exc}"
            )

            if attempt < retries:
                time.sleep(1.5 * attempt)

    raise RuntimeError(
        f"取得資料失敗：{last_error}"
    )


# ============================================================
# 字串處理
# ============================================================

def clean_text(value: Any) -> str:

    if value is None:
        return ""

    text = str(value)

    text = (
        text.replace("\ufeff", "")
        .replace("\u3000", " ")
        .strip()
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text


def normalize_code(value: Any) -> str:

    text = clean_text(value)

    if "." in text:
        text = text.split(".")[0]

    return text


def is_stock_code(code: str) -> bool:

    return (
        len(code) == 4
        and code.isdigit()
    )


def is_etf_code(code: str) -> bool:

    return (
        len(code) in (4, 5, 6)
        and code.isdigit()
        and code.startswith("00")
    )


# ============================================================
# 股票資料標準化
# ============================================================

def make_security(
    code: Any,
    name: Any,
    market: str,
    security_type: str = "Stock",
    full_symbol: Optional[str] = None,
) -> Optional[Dict[str, str]]:

    code = normalize_code(code)
    name = clean_text(name)

    if not is_stock_code(code):
        return None

    if not name:
        return None

    if security_type == "ETF":
        sec_type = "ETF"
    else:
        sec_type = "Stock"

    if full_symbol:
        full = clean_text(full_symbol)
    else:
        suffix = (
            ".TW"
            if market == "TWSE"
            else ".TWO"
        )
        full = f"{code}{suffix}"

    return {
        "symbol": code,
        "full_symbol": full,
        "name": name,
        "market": market,
        "type": sec_type,
    }


# ============================================================
# 既有 universe
# ============================================================

def load_existing_universe() -> List[Dict[str, str]]:

    if not UNIVERSE_FILE.exists():
        log("⚠️ 既有 universe.json 不存在")
        return []

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8",
        ) as f:

            data = json.load(f)

        if isinstance(data, dict):
            items = data.get(
                "items",
                []
            )
        elif isinstance(data, list):
            items = data
        else:
            items = []

        result: List[Dict[str, str]] = []

        for item in items:

            if not isinstance(item, dict):
                continue

            code = item.get(
                "symbol",
                item.get("code", "")
            )

            name = item.get(
                "name",
                ""
            )

            market = item.get(
                "market",
                ""
            )

            sec_type = item.get(
                "type",
                "Stock"
            )

            normalized = make_security(
                code=code,
                name=name,
                market=market,
                security_type=sec_type,
                full_symbol=item.get(
                    "full_symbol"
                ),
            )

            if normalized:
                result.append(
                    normalized
                )

        return deduplicate(result)

    except Exception as exc:

        log(
            f"⚠️ 讀取既有 universe.json 失敗：{exc}"
        )

        return []


# ============================================================
# 去重
# ============================================================

def deduplicate(
    securities: Iterable[Dict[str, str]]
) -> List[Dict[str, str]]:

    result: Dict[
        Tuple[str, str],
        Dict[str, str]
    ] = {}

    for item in securities:

        code = item.get(
            "symbol",
            ""
        )

        market = item.get(
            "market",
            ""
        )

        if not code or not market:
            continue

        key = (
            code,
            market,
        )

        result[key] = item

    return sorted(
        result.values(),
        key=lambda x: (
            x.get("market", ""),
            x.get("symbol", ""),
        ),
    )


# ============================================================
# TWSE API
# ============================================================

def fetch_twse_primary(
    session: requests.Session
) -> List[Dict[str, str]]:

    section(
        "取得 TWSE 上市股票"
    )

    url = (
        "https://openapi.twse.com.tw/"
        "v1/opendata/t187ap03_L"
    )

    log(
        f"主 API：{url}"
    )

    data = get_json(
        session,
        url,
    )

    if not isinstance(data, list):
        raise RuntimeError(
            "TWSE API 回傳格式不是 list"
        )

    result: List[Dict[str, str]] = []

    for row in data:

        if not isinstance(row, dict):
            continue

        code = (
            row.get("公司代號")
            or row.get("Code")
            or row.get("公司代碼")
        )

        name = (
            row.get("公司名稱")
            or row.get("Name")
            or row.get("公司簡稱")
        )

        security = make_security(
            code,
            name,
            "TWSE",
            "Stock",
            f"{normalize_code(code)}.TW"
            if code
            else None,
        )

        if security:
            result.append(
                security
            )

    result = deduplicate(result)

    if len(result) < MIN_TWSE:

        raise RuntimeError(
            f"TWSE 主 API 資料不足："
            f"{len(result)} < {MIN_TWSE}"
        )

    log(
        f"✓ TWSE 主 API 成功："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# TWSE fallback 1
# ============================================================

def fetch_twse_fallback_bwibbu(
    session: requests.Session
) -> List[Dict[str, str]]:

    section(
        "TWSE 備援來源 1：BWIBBU_ALL"
    )

    url = (
        "https://openapi.twse.com.tw/"
        "v1/exchangeReport/BWIBBU_ALL"
    )

    data = get_json(
        session,
        url,
    )

    if not isinstance(data, list):
        raise RuntimeError(
            "BWIBBU_ALL 回傳格式不是 list"
        )

    result: List[Dict[str, str]] = []

    for row in data:

        if not isinstance(row, dict):
            continue

        code = (
            row.get("Code")
            or row.get("公司代號")
            or row.get("證券代號")
        )

        name = (
            row.get("Name")
            or row.get("公司名稱")
            or row.get("證券名稱")
        )

        security = make_security(
            code,
            name,
            "TWSE",
            "Stock",
        )

        if security:
            result.append(
                security
            )

    result = deduplicate(result)

    if len(result) < MIN_TWSE:

        raise RuntimeError(
            f"TWSE BWIBBU_ALL 資料不足："
            f"{len(result)} < {MIN_TWSE}"
        )

    log(
        f"✓ TWSE 備援 1 成功："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# TWSE fallback 2
# 使用既有 universe 的 TWSE 部分
# ============================================================

def fetch_twse_from_existing(
    existing: List[Dict[str, str]]
) -> List[Dict[str, str]]:

    section(
        "TWSE 備援 2：既有 universe.json"
    )

    result = [
        item
        for item in existing
        if item.get("market") == "TWSE"
    ]

    result = deduplicate(result)

    log(
        f"✓ 從既有 universe 保留 "
        f"{len(result)} 檔 TWSE"
    )

    if len(result) < MIN_TWSE:

        raise RuntimeError(
            f"既有 TWSE 資料不足："
            f"{len(result)} < {MIN_TWSE}"
        )

    return result


# ============================================================
# TPEX API 1
# ============================================================

def fetch_tpex_primary(
    session: requests.Session
) -> List[Dict[str, str]]:

    section(
        "取得 TPEX 上櫃股票"
    )

    # TPEx 公開資料常見 API
    url = (
        "https://www.tpex.org.tw/"
        "openapi/v1/tpex_mainboard_peratio"
    )

    log(
        f"主 API：{url}"
    )

    data = get_json(
        session,
        url,
    )

    if not isinstance(data, list):
        raise RuntimeError(
            "TPEX 主 API 回傳格式不是 list"
        )

    result: List[Dict[str, str]] = []

    for row in data:

        if not isinstance(row, dict):
            continue

        code = (
            row.get("SecuritiesCompanyCode")
            or row.get("Code")
            or row.get("證券代號")
            or row.get("股票代號")
        )

        name = (
            row.get("CompanyName")
            or row.get("Name")
            or row.get("證券名稱")
            or row.get("公司名稱")
        )

        security = make_security(
            code,
            name,
            "TPEX",
            "Stock",
        )

        if security:
            result.append(
                security
            )

    result = deduplicate(result)

    if len(result) < MIN_TPEX:

        raise RuntimeError(
            f"TPEX 主 API 資料不足："
            f"{len(result)} < {MIN_TPEX}"
        )

    log(
        f"✓ TPEX 主 API 成功："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# TPEX fallback
# ============================================================

def fetch_tpex_fallback(
    session: requests.Session
) -> List[Dict[str, str]]:

    section(
        "TPEX 備援來源"
    )

    candidates = [
        (
            "https://www.tpex.org.tw/"
            "openapi/v1/tpex_mainboard_daily_close_quotes"
        ),
        (
            "https://www.tpex.org.tw/"
            "openapi/v1/tpex_esb_latest_statistics"
        ),
    ]

    errors = []

    for url in candidates:

        log(
            f"嘗試 TPEX API：{url}"
        )

        try:

            data = get_json(
                session,
                url,
                retries=2,
            )

            if not isinstance(data, list):
                raise RuntimeError(
                    "回傳不是 list"
                )

            result: List[
                Dict[str, str]
            ] = []

            for row in data:

                if not isinstance(row, dict):
                    continue

                code = (
                    row.get("SecuritiesCompanyCode")
                    or row.get("Code")
                    or row.get("證券代號")
                    or row.get("股票代號")
                )

                name = (
                    row.get("CompanyName")
                    or row.get("Name")
                    or row.get("證券名稱")
                    or row.get("公司名稱")
                )

                security = make_security(
                    code,
                    name,
                    "TPEX",
                    "Stock",
                )

                if security:
                    result.append(
                        security
                    )

            result = deduplicate(
                result
            )

            if len(result) >= MIN_TPEX:

                log(
                    f"✓ TPEX 備援成功："
                    f"{len(result)} 檔"
                )

                return result

            errors.append(
                f"{url}: "
                f"僅取得 {len(result)} 檔"
            )

        except Exception as exc:

            errors.append(
                f"{url}: {exc}"
            )

    raise RuntimeError(
        "TPEX 所有 API 備援均失敗：\n"
        + "\n".join(errors)
    )


# ============================================================
# TPEX 既有 universe fallback
# ============================================================

def fetch_tpex_from_existing(
    existing: List[Dict[str, str]]
) -> List[Dict[str, str]]:

    section(
        "TPEX 最終備援：既有 universe.json"
    )

    result = [
        item
        for item in existing
        if item.get("market") == "TPEX"
    ]

    result = deduplicate(result)

    log(
        f"✓ 從既有 universe 保留 "
        f"{len(result)} 檔 TPEX"
    )

    if len(result) < MIN_TPEX:

        raise RuntimeError(
            f"既有 TPEX 資料不足："
            f"{len(result)} < {MIN_TPEX}"
        )

    return result


# ============================================================
# 強制測試股票
# ============================================================

def verify_required_stocks(
    securities: List[Dict[str, str]]
) -> Tuple[bool, List[str]]:

    by_code = {
        item["symbol"]: item
        for item in securities
    }

    errors: List[str] = []

    for code, expected_name in (
        REQUIRED_TEST_STOCKS.items()
    ):

        item = by_code.get(code)

        if item is None:

            errors.append(
                f"{code} {expected_name} 不存在"
            )
            continue

        actual_name = clean_text(
            item.get("name")
        )

        if actual_name != expected_name:

            errors.append(
                f"{code} 名稱錯誤："
                f"預期={expected_name}, "
                f"實際={actual_name}"
            )

    return (
        len(errors) == 0,
        errors,
    )


# ============================================================
# Universe 驗證
# ============================================================

def validate_universe(
    securities: List[Dict[str, str]]
) -> Tuple[bool, Dict[str, Any], List[str]]:

    securities = deduplicate(
        securities
    )

    twse = [
        x
        for x in securities
        if x.get("market") == "TWSE"
    ]

    tpex = [
        x
        for x in securities
        if x.get("market") == "TPEX"
    ]

    stocks = [
        x
        for x in securities
        if x.get("type") == "Stock"
    ]

    etfs = [
        x
        for x in securities
        if x.get("type") == "ETF"
    ]

    errors: List[str] = []

    if len(twse) < MIN_TWSE:

        errors.append(
            f"TWSE {len(twse)} < {MIN_TWSE}"
        )

    if len(tpex) < MIN_TPEX:

        errors.append(
            f"TPEX {len(tpex)} < {MIN_TPEX}"
        )

    if len(securities) < MIN_TOTAL:

        errors.append(
            f"Total {len(securities)} "
            f"< {MIN_TOTAL}"
        )

    required_ok, required_errors = (
        verify_required_stocks(
            securities
        )
    )

    if not required_ok:
        errors.extend(
            required_errors
        )

    statistics = {
        "twse_count": len(twse),
        "tpex_count": len(tpex),
        "stock_count": len(stocks),
        "etf_count": len(etfs),
        "total_count": len(securities),
    }

    return (
        len(errors) == 0,
        statistics,
        errors,
    )


# ============================================================
# 寫入 universe.json
# ============================================================

def write_universe(
    securities: List[Dict[str, str]],
    source_status: Dict[str, str],
) -> None:

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    securities = deduplicate(
        securities
    )

    output = {
        "schema_version": VERSION,
        "generated_at": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "universe_count": len(securities),
        "source_status": source_status,
        "items": securities,
    }

    temp_file = (
        UNIVERSE_FILE.with_suffix(
            ".json.tmp"
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
        UNIVERSE_FILE
    )


# ============================================================
# Main
# ============================================================

def main() -> int:

    start_time = time.time()

    section(
        f"台股 AI 選股系統 "
        f"build_universe.py {VERSION}"
    )

    log(
        f"BASE_DIR：{BASE_DIR}"
    )

    log(
        f"DATA_DIR：{DATA_DIR}"
    )

    log(
        f"OUTPUT：{UNIVERSE_FILE}"
    )

    log("")
    log("安全門檻：")
    log(
        f"  TWSE >= {MIN_TWSE}"
    )
    log(
        f"  TPEX >= {MIN_TPEX}"
    )
    log(
        f"  Total >= {MIN_TOTAL}"
    )

    # --------------------------------------------------------
    # 讀取既有 universe
    # --------------------------------------------------------

    existing = load_existing_universe()

    log("")

    if existing:

        log(
            f"既有 universe："
            f"{len(existing)} stocks"
        )

    else:

        log(
            "既有 universe：不存在或無法讀取"
        )

    session = create_session()

    # --------------------------------------------------------
    # TWSE
    # --------------------------------------------------------

    twse: List[
        Dict[str, str]
    ] = []

    twse_source = ""

    try:

        twse = fetch_twse_primary(
            session
        )

        twse_source = (
            "TWSE OpenAPI "
            "t187ap03_L"
        )

    except Exception as exc:

        log("")
        log(
            "⚠️ TWSE 主 API 失敗"
        )
        log(
            f"原因：{exc}"
        )

        try:

            twse = (
                fetch_twse_fallback_bwibbu(
                    session
                )
            )

            twse_source = (
                "TWSE OpenAPI "
                "BWIBBU_ALL fallback"
            )

        except Exception as fallback_exc:

            log("")
            log(
                "⚠️ TWSE 備援 1 失敗"
            )
            log(
                f"原因：{fallback_exc}"
            )

            try:

                twse = (
                    fetch_twse_from_existing(
                        existing
                    )
                )

                twse_source = (
                    "existing universe "
                    "TWSE fallback"
                )

            except Exception as existing_exc:

                log("")
                log(
                    "❌ TWSE 所有來源均失敗"
                )
                log(
                    f"原因：{existing_exc}"
                )

    # --------------------------------------------------------
    # TPEX
    # --------------------------------------------------------

    tpex: List[
        Dict[str, str]
    ] = []

    tpex_source = ""

    try:

        tpex = fetch_tpex_primary(
            session
        )

        tpex_source = (
            "TPEX OpenAPI primary"
        )

    except Exception as exc:

        log("")
        log(
            "⚠️ TPEX 主 API 失敗"
        )
        log(
            f"原因：{exc}"
        )

        try:

            tpex = fetch_tpex_fallback(
                session
            )

            tpex_source = (
                "TPEX OpenAPI fallback"
            )

        except Exception as fallback_exc:

            log("")
            log(
                "⚠️ TPEX API 備援失敗"
            )
            log(
                f"原因：{fallback_exc}"
            )

            try:

                tpex = (
                    fetch_tpex_from_existing(
                        existing
                    )
                )

                tpex_source = (
                    "existing universe "
                    "TPEX fallback"
                )

            except Exception as existing_exc:

                log("")
                log(
                    "❌ TPEX 所有來源均失敗"
                )
                log(
                    f"原因：{existing_exc}"
                )

    # --------------------------------------------------------
    # 判斷是否允許建新 universe
    # --------------------------------------------------------

    new_universe = deduplicate(
        twse + tpex
    )

    section(
        "新 Universe 驗證"
    )

    valid, stats, errors = (
        validate_universe(
            new_universe
        )
    )

    log(
        f"TWSE：{stats['twse_count']}"
    )

    log(
        f"TPEX：{stats['tpex_count']}"
    )

    log(
        f"Stock：{stats['stock_count']}"
    )

    log(
        f"ETF：{stats['etf_count']}"
    )

    log(
        f"Total：{stats['total_count']}"
    )

    if errors:

        log("")
        log(
            "❌ 新 Universe 驗證失敗："
        )

        for error in errors:
            log(
                f"  - {error}"
            )

    # --------------------------------------------------------
    # 新資料有效 → 覆蓋
    # --------------------------------------------------------

    if valid:

        section(
            "BUILD UNIVERSE SUCCESS"
        )

        source_status = {
            "twse": twse_source,
            "tpex": tpex_source,
        }

        write_universe(
            new_universe,
            source_status,
        )

        log(
            "✓ 新 universe.json "
            "已通過所有安全門檻"
        )

        log(
            f"✓ 寫入：{UNIVERSE_FILE}"
        )

        log(
            f"✓ 總檔數："
            f"{len(new_universe)}"
        )

        log(
            f"✓ TWSE："
            f"{stats['twse_count']}"
        )

        log(
            f"✓ TPEX："
            f"{stats['tpex_count']}"
        )

        # 最後再次確認 3081
        by_code = {
            x["symbol"]: x
            for x in new_universe
        }

        if "3081" in by_code:

            item = by_code["3081"]

            log(
                "✓ 3081："
                f"{item['name']} | "
                f"{item['market']} | "
                f"{item['full_symbol']}"
            )

        elapsed = (
            time.time() - start_time
        )

        log(
            f"✓ 耗時：{elapsed:.1f} 秒"
        )

        return 0

    # --------------------------------------------------------
    # 新資料失敗 → 保留既有 universe
    # --------------------------------------------------------

    section(
        "BUILD UNIVERSE FALLBACK"
    )

    existing_valid = False
    existing_stats: Dict[str, Any] = {}

    if existing:

        (
            existing_valid,
            existing_stats,
            existing_errors,
        ) = validate_universe(
            existing
        )

        log(
            "既有 universe 驗證："
        )

        log(
            f"  TWSE："
            f"{existing_stats.get('twse_count', 0)}"
        )

        log(
            f"  TPEX："
            f"{existing_stats.get('tpex_count', 0)}"
        )

        log(
            f"  Total："
            f"{existing_stats.get('total_count', 0)}"
        )

    if existing_valid:

        log(
            "⚠️ 新 API 資料不足或異常"
        )

        log(
            "✓ 不覆蓋既有 universe.json"
        )

        log(
            f"✓ 保留既有："
            f"{len(existing)} 檔"
        )

        log(
            "✓ 這次 API failure "
            "不會破壞既有股票池"
        )

        elapsed = (
            time.time() - start_time
        )

        log(
            f"✓ 耗時：{elapsed:.1f} 秒"
        )

        # 非致命 fallback。
        # 對 CI/CD 而言，只要既有 universe
        # 本身有效，就允許 workflow 繼續。
        return 0

    # --------------------------------------------------------
    # 連既有 universe 都無法使用
    # --------------------------------------------------------

    log("")
    log(
        "❌ BUILD UNIVERSE FAILED"
    )

    log(
        "❌ 新資料未通過安全門檻"
    )

    log(
        "❌ 既有 universe 也無法作為安全備援"
    )

    log(
        "❌ 為避免產生錯誤股票池，"
        "本次不建立 universe.json"
    )

    elapsed = (
        time.time() - start_time
    )

    log(
        f"耗時：{elapsed:.1f} 秒"
    )

    return 1


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":
    sys.exit(main())