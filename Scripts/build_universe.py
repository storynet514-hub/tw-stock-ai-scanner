#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 - Scripts/build_universe.py
UNIVERSE BUILDER V4

契約：
1. Universe identity 不依賴價格、成交量或 Yahoo/CMoney。
2. 官方商品主檔是 active universe 的硬性身份來源。
3. FinMind TaiwanStockInfo / ActiveETFInfo 用於身份補充與分類。
4. terminated 是 hard gate；terminated 不得進 active。
5. 允許 STOCK / ETF；排除權證、ETN、REIT、TDR、特別股、
   一般債券/可轉債及結構型商品。
6. 合法 6 碼 ETF、主動式 ETF、債券 ETF 必須保留。
7. 舊 universe.json 只保存同一合法 candidate 的 metadata，不得復活商品。
8. 所有 validation 完成後，先驗證暫存 JSON，再 atomic replace。
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"
UNIVERSE_FILE = DATA_DIR / "universe.json"

FINMIND_API = "https://api.finmindtrade.com/api/v4/data"
FINMIND_INFO = "TaiwanStockInfo"
FINMIND_ACTIVE_ETF = "TaiwanStockActiveETFInfo"
FINMIND_DELISTING = "TaiwanStockDelisting"

TWSE_PUBLIC_MASTER_URLS = (
    "https://isin.twse.com.tw/isin/e_C_public.jsp?strMode=1",
    "https://isin.twse.com.tw/isin/C_public.jsp?strMode=1",
)

TWSE_DELISTED_URL = (
    "https://www.twse.com.tw/company/suspendListingCsvAndHtml"
    "?lang=zh&startYear=&type=html"
)
TPEX_DELISTED_URL = (
    "https://www.tpex.org.tw/zh-tw/mainboard/listed/delisted.html"
)

REQUEST_TIMEOUT = 60
RETRIES = 4
FINMIND_RETRIES = 3
RETRY_SLEEP = 2.0
MASTER_MIN_BYTES = 1_000
MASTER_MIN_SYMBOLS = 100

ALLOWED_MARKETS = {"TWSE", "TPEX"}
ALLOWED_TYPES = {"STOCK", "ETF"}
ACTIVE_STATUS = "active"

WARRANT_WORDS = (
    "權證", "認購權證", "認售權證", "牛證", "熊證", "認購", "認售",
    "WARRANT", "CALL WARRANT", "PUT WARRANT",
)
ETN_WORDS = (
    "ETN", "指數投資證券", "INDEX INVESTMENT SECURITIES",
)
REIT_WORDS = (
    "REIT", "REITS", "不動產投資信託",
    "不動產投資信託受益證券", "不動產投資信託基金",
    "REAL ESTATE INVESTMENT TRUST",
)
TDR_WORDS = (
    "TDR", "海外存託憑證", "存託憑證",
    "GLOBAL DEPOSITARY", "DEPOSITARY RECEIPT",
)
PREFERRED_WORDS = (
    "特別股", "甲特", "乙特", "丙特", "丁特", "戊特",
    "優先股", "優先特別股",
    "PREFERRED STOCK", "PREFERRED SHARE", "PREFERENCE SHARE",
)
BOND_WORDS = (
    "公司債", "一般債券", "政府債券", "金融債",
    "可轉換公司債", "可轉債", "債券",
    "CORPORATE BOND", "GOVERNMENT BOND",
    "FINANCIAL BOND", "CONVERTIBLE BOND",
)
STRUCTURED_WORDS = (
    "受益證券", "資產基礎證券", "結構型商品", "結構型證券",
)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
})


def log(message: str = "") -> None:
    print(message, flush=True)


def section(title: str) -> None:
    log("")
    log("=" * 76)
    log(title)
    log("=" * 76)


def now_tw() -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Taipei"))
    except Exception:
        return datetime.now()


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    text = (
        text.replace("\ufeff", "")
        .replace("\xa0", " ")
        .replace("\u3000", " ")
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("\t", " ")
    )
    return re.sub(r"\s+", " ", text).strip()


def normalize_text(value: Any) -> str:
    return clean_text(value).upper().replace(" ", "").replace("\u3000", "")


def clean_symbol(value: Any) -> str:
    text = clean_text(value).upper()
    for suffix in (".TW", ".TWO", ".TPEX", ".TWSE"):
        if text.endswith(suffix):
            text = text[:-len(suffix)]
            break
    return text.replace(" ", "").replace("\u3000", "")


def is_valid_symbol(value: Any) -> bool:
    return bool(re.fullmatch(r"[0-9]{4,6}[A-Z]?", clean_symbol(value)))


def is_six_digit_symbol(symbol: str) -> bool:
    return bool(re.fullmatch(r"[0-9]{6}", symbol))


def normalize_market(value: Any) -> Optional[str]:
    text = normalize_text(value)
    if not text:
        return None
    if any(x in text for x in ("TPEX", "TPEx", "上櫃", "櫃買", "OTC")):
        return "TPEX"
    if any(x in text for x in ("TWSE", "上市")):
        return "TWSE"
    return None


def contains_any(text: str, words: Tuple[str, ...]) -> bool:
    normalized = normalize_text(text)
    return any(normalize_text(word) in normalized for word in words)


def is_etf_record(industry: Any, name: Any, active_etf: bool = False) -> bool:
    if active_etf:
        return True
    industry_text = normalize_text(industry)
    name_text = normalize_text(name)
    return (
        industry_text == "ETF"
        or "ETF" in industry_text
        or "ETF" in name_text
        or "指數股票型基金" in name_text
        or "主動式ETF" in name_text
    )


def is_excluded_instrument(
    symbol: str,
    name: str,
    industry: str,
    cfi: str = "",
    *,
    is_etf: bool = False,
) -> Tuple[bool, str]:
    combined = normalize_text(name) + normalize_text(industry)
    cfi_text = normalize_text(cfi)

    # ETF 是合法 instrument type；債券 ETF 不可被 BOND_WORDS 誤殺。
    # 但明確的 ETN / REIT / warrant / TDR 不因名稱看似 ETF 而放行。
    if contains_any(combined, ETN_WORDS):
        return True, "etn"
    if contains_any(combined, REIT_WORDS):
        return True, "reit"
    if contains_any(combined, WARRANT_WORDS):
        return True, "warrant"
    if contains_any(combined, TDR_WORDS):
        return True, "tdr"

    if not is_etf:
        if cfi_text.startswith("EPN"):
            return True, "preferred_share_cfi"
        if contains_any(combined, PREFERRED_WORDS):
            return True, "preferred_share"
        if contains_any(combined, BOND_WORDS):
            return True, "bond"
        if contains_any(combined, STRUCTURED_WORDS):
            return True, "structured_security"
        if re.fullmatch(r"[0-9]{5}T", symbol):
            return True, "structured_T_security"
        if re.fullmatch(r"[0-9]{5}P", symbol):
            return True, "structured_P_security"
        if is_six_digit_symbol(symbol):
            return True, "six_digit_non_etf"

    return False, ""


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: List[List[str]] = []
        self.current_row: Optional[List[str]] = None
        self.current_cell: Optional[List[str]] = None

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if tag == "tr":
            self.current_row = []
        elif tag in {"td", "th"} and self.current_row is not None:
            self.current_cell = []
        elif tag == "br" and self.current_cell is not None:
            self.current_cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"}:
            if self.current_row is not None and self.current_cell is not None:
                self.current_row.append(clean_text("".join(self.current_cell)))
            self.current_cell = None
        elif tag == "tr":
            if self.current_row:
                self.rows.append(self.current_row)
            self.current_row = None
            self.current_cell = None

    def handle_data(self, data: str) -> None:
        if self.current_cell is not None:
            self.current_cell.append(data)


def http_get(
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    retries: int = RETRIES,
) -> requests.Response:
    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            response = SESSION.get(
                url, params=params, headers=headers, timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
            return response
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(RETRY_SLEEP * attempt)
    raise RuntimeError(f"HTTP request failed: {url}: {last_error}")


def finmind_headers() -> Dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": "tw-stock-ai-scanner/universe-v4"}
    token = os.environ.get("FINMIND_TOKEN") or os.environ.get("FINMIND_API_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_finmind_dataset(dataset: str) -> List[Dict[str, Any]]:
    section(f"FINMIND — {dataset}")
    last_error: Optional[Exception] = None
    for attempt in range(1, FINMIND_RETRIES + 1):
        try:
            response = SESSION.get(
                FINMIND_API,
                params={"dataset": dataset},
                headers=finmind_headers(),
                timeout=REQUEST_TIMEOUT,
            )
            log(f"→ request {attempt}/{FINMIND_RETRIES} HTTP {response.status_code}")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("FinMind response 不是 object")
            status = payload.get("status")
            if status not in (None, 200, "200"):
                raise RuntimeError(f"FinMind status={status}: {payload.get('msg', '')}")
            data = payload.get("data")
            if not isinstance(data, list):
                raise RuntimeError("FinMind data 不是 list")
            records = [x for x in data if isinstance(x, dict)]
            log(f"✓ records：{len(records):,}")
            return records
        except Exception as exc:
            last_error = exc
            log(f"⚠️ 第 {attempt} 次失敗：{exc}")
            if attempt < FINMIND_RETRIES:
                time.sleep(RETRY_SLEEP * attempt)
    raise RuntimeError(f"FinMind {dataset} failed: {last_error}")


def fetch_active_etfs() -> Dict[str, Dict[str, Any]]:
    records = fetch_finmind_dataset(FINMIND_ACTIVE_ETF)
    result: Dict[str, Dict[str, Any]] = {}
    for record in records:
        symbol = clean_symbol(record.get("stock_id"))
        market = normalize_market(record.get("type"))
        if not is_valid_symbol(symbol) or market not in ALLOWED_MARKETS:
            continue
        result[symbol] = {
            "symbol": symbol,
            "name": clean_text(record.get("stock_name")),
            "market": market,
            "category": clean_text(record.get("category")),
            "date": clean_text(record.get("date")),
        }
    log(f"✓ Active ETF：{len(result):,}")
    return result


def fetch_finmind_identity(active_etfs: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    records = fetch_finmind_dataset(FINMIND_INFO)
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for record in records:
        symbol = clean_symbol(record.get("stock_id"))
        if is_valid_symbol(symbol):
            grouped.setdefault(symbol, []).append(record)

    result: Dict[str, Dict[str, Any]] = {}
    for symbol, rows in grouped.items():
        valid_rows = []
        for row in rows:
            market = normalize_market(row.get("type"))
            if market in ALLOWED_MARKETS:
                valid_rows.append((clean_text(row.get("date")), row, market))
        if not valid_rows:
            continue
        valid_rows.sort(key=lambda x: (x[0], x[2]), reverse=True)
        _, row, market = valid_rows[0]
        name = clean_text(row.get("stock_name"))
        industry = clean_text(row.get("industry_category"))
        is_etf = is_etf_record(industry, name, symbol in active_etfs)
        excluded, reason = is_excluded_instrument(
            symbol, name, industry, "", is_etf=is_etf
        )
        if excluded:
            log(f"→ FinMind 排除 {symbol}: {reason}")
            continue
        result[symbol] = {
            "symbol": symbol,
            "name": name,
            "market": market,
            "type": "ETF" if is_etf else "STOCK",
            "industry_category": industry,
            "finmind_date": clean_text(row.get("date")),
            "source": "FINMIND",
        }

    for symbol, etf in active_etfs.items():
        if symbol in result:
            result[symbol]["type"] = "ETF"
            result[symbol]["market"] = etf["market"]
            if not result[symbol].get("name"):
                result[symbol]["name"] = etf.get("name", "")
        else:
            result[symbol] = {
                "symbol": symbol,
                "name": etf.get("name", ""),
                "market": etf["market"],
                "type": "ETF",
                "industry_category": "ETF",
                "finmind_date": etf.get("date", ""),
                "source": "FINMIND_ACTIVE_ETF",
            }
    log(f"✓ FinMind identity candidates：{len(result):,}")
    return result


def decode_html(response: requests.Response) -> str:
    content = response.content
    candidates = []
    content_type = response.headers.get("Content-Type", "")
    match = re.search(r"charset\s*=\s*['\"]?([^;'\"\s]+)", content_type, re.I)
    if match:
        candidates.append(match.group(1))
    candidates.extend(["utf-8", "big5", "cp950"])
    best_text, best_score = "", -10**9
    for encoding in candidates:
        try:
            text = content.decode(encoding, errors="replace")
            upper = text.upper()
            score = 0
            score += 10 if "<HTML" in upper else 0
            score += 20 if "<TABLE" in upper else 0
            score += 20 if "<TR" in upper else 0
            score += min(len(re.findall(r"(?<![0-9A-Z])[0-9]{4,6}[A-Z]?(?![0-9A-Z])", upper)), 500)
            if score > best_score:
                best_score, best_text = score, text
        except Exception:
            pass
    return best_text


def parse_official_master(text: str) -> Dict[str, Dict[str, Any]]:
    parser = TableParser()
    parser.feed(text)
    result: Dict[str, Dict[str, Any]] = {}

    for row in parser.rows:
        if not row:
            continue

        symbols: List[str] = []
        for cell in row:
            # 官方主檔常把代號與名稱放同一格；先抓獨立代號。
            for raw in re.findall(
                r"(?<![0-9A-Z])([0-9]{4,6}[A-Z]?)(?![0-9A-Z])",
                cell.upper(),
            ):
                symbol = clean_symbol(raw)
                if is_valid_symbol(symbol) and symbol not in symbols:
                    symbols.append(symbol)
        if not symbols:
            continue

        market = None
        for cell in row:
            market = normalize_market(cell)
            if market:
                break

        if market not in ALLOWED_MARKETS:
            continue

        cfi = ""
        for cell in row:
            value = clean_text(cell).upper()
            if re.fullmatch(r"[A-Z]{6}", value):
                cfi = value
                break

        name = ""
        for cell in row:
            value = clean_text(cell)
            if not value:
                continue
            if re.fullmatch(r"[0-9A-Z\-\s\.]+", value.upper()):
                continue
            if "TW000" in value.upper():
                continue
            if normalize_market(value):
                continue
            name = value
            break

        for symbol in symbols:
            result[symbol] = {
                "symbol": symbol,
                "name": name,
                "market": market,
                "cfi": cfi,
                "source": "TWSE_OFFICIAL",
            }
    return result


def fetch_official_master() -> Dict[str, Dict[str, Any]]:
    section("OFFICIAL PRODUCT MASTER")
    best: Dict[str, Dict[str, Any]] = {}
    for url in TWSE_PUBLIC_MASTER_URLS:
        try:
            response = http_get(url, retries=3)
            log(f"→ HTTP {response.status_code} bytes={len(response.content):,}")
            if len(response.content) < MASTER_MIN_BYTES:
                continue
            parsed = parse_official_master(decode_html(response))
            log(f"→ official symbols：{len(parsed):,}")
            if len(parsed) > len(best):
                best = parsed
            if len(parsed) >= MASTER_MIN_SYMBOLS:
                log("✓ 官方商品主檔可用")
                return parsed
        except Exception as exc:
            log(f"⚠️ 官方主檔失敗：{exc}")
    if len(best) < MASTER_MIN_SYMBOLS:
        raise RuntimeError(
            f"官方商品主檔不可用或解析不足：{len(best):,} < {MASTER_MIN_SYMBOLS}"
        )
    return best


def extract_termination_symbols(text: str) -> Set[str]:
    """
    只接受「終止/下市/下櫃」語境附近的代號。
    禁止對整個 HTML 直接 regex 全部 4~6 碼，避免把頁面導覽/其他資料誤判成 terminated。
    """
    result: Set[str] = set()
    parser = TableParser()
    parser.feed(text)
    keywords = ("終止上市", "終止上櫃", "終止櫃買", "下市", "下櫃", "終止掛牌")
    for row in parser.rows:
        joined = " ".join(row)
        if not any(k in joined for k in keywords):
            continue
        for raw in re.findall(
            r"(?<![0-9A-Z])([0-9]{4,6}[A-Z]?)(?![0-9A-Z])",
            joined.upper(),
        ):
            symbol = clean_symbol(raw)
            if is_valid_symbol(symbol):
                result.add(symbol)
    return result


def fetch_finmind_delisted() -> Set[str]:
    records = fetch_finmind_dataset(FINMIND_DELISTING)
    result: Set[str] = set()
    for record in records:
        symbol = clean_symbol(record.get("stock_id"))
        if is_valid_symbol(symbol):
            result.add(symbol)
    log(f"✓ FinMind terminated：{len(result):,}")
    return result


def fetch_official_delisted() -> Set[str]:
    section("OFFICIAL TERMINATION DATA")
    result: Set[str] = set()
    for url, label in (
        (TWSE_DELISTED_URL, "TWSE"),
        (TPEX_DELISTED_URL, "TPEX"),
    ):
        try:
            response = http_get(url, retries=3)
            text = decode_html(response)
            found = extract_termination_symbols(text)
            result |= found
            log(f"✓ {label} terminated：{len(found):,}")
        except Exception as exc:
            log(f"⚠️ {label} terminated 失敗：{exc}")
    log(f"✓ Official terminated：{len(result):,}")
    return result


def load_existing() -> Dict[str, Dict[str, Any]]:
    if not UNIVERSE_FILE.exists():
        return {}
    try:
        with UNIVERSE_FILE.open("r", encoding="utf-8-sig") as f:
            payload = json.load(f)
        stocks = payload.get("stocks")
        if not isinstance(stocks, dict):
            return {}
        result: Dict[str, Dict[str, Any]] = {}
        for key, item in stocks.items():
            if not isinstance(item, dict):
                continue
            symbol = clean_symbol(item.get("symbol") or key)
            if is_valid_symbol(symbol):
                result[symbol] = item
        return result
    except Exception as exc:
        log(f"⚠️ 舊 Universe 讀取失敗：{exc}")
        return {}


def infer_instrument_type(
    symbol: str,
    name: str,
    record_type: str,
    old: Optional[Dict[str, Any]],
) -> str:
    if record_type == "STOCK":
        return "STOCK"
    text = normalize_text(name)
    if "主動" in text or "ACTIVE" in text:
        return "ACTIVE"
    if symbol.endswith("L") or "槓桿" in text or "LEVERAGE" in text or "BULL" in text:
        return "LEVERAGED"
    if symbol.endswith("R") or "反向" in text or "INVERSE" in text or "BEAR" in text:
        return "INVERSE"
    if "債" in text or "BOND" in text:
        return "BOND_ETF"
    if old:
        old_type = clean_text(old.get("instrument_type"))
        if old_type:
            return old_type
    return "EQUITY"


def build_record(
    symbol: str,
    source: Dict[str, Any],
    official: Dict[str, Any],
    old: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    record_type = source["type"]
    name = clean_text(source.get("name")) or clean_text(official.get("name"))
    market = source.get("market")
    official_market = official.get("market")
    if official_market not in ALLOWED_MARKETS:
        raise ValueError(f"{symbol}: official market invalid")
    if market != official_market:
        raise ValueError(
            f"{symbol}: market mismatch FinMind={market} Official={official_market}"
        )

    instrument_type = infer_instrument_type(symbol, name, record_type, old)
    record: Dict[str, Any] = {
        "symbol": symbol,
        "full_symbol": f"{symbol}.TW" if market == "TWSE" else f"{symbol}.TWO",
        "name": name,
        "market": market,
        "type": record_type,
        "instrument_type": instrument_type,
        "status": ACTIVE_STATUS,
    }

    if old:
        for field in ("listed_date", "cfi_code", "category"):
            value = old.get(field)
            if value not in (None, ""):
                record[field] = value

    cfi = clean_text(official.get("cfi"))
    if cfi:
        record["cfi_code"] = cfi

    if "category" not in record:
        if record_type == "STOCK":
            record["category"] = "STOCK"
        elif instrument_type == "BOND_ETF":
            record["category"] = "BOND"
        elif instrument_type == "ACTIVE":
            record["category"] = "ACTIVE_EQUITY"
        elif instrument_type == "LEVERAGED":
            record["category"] = "LEVERAGED"
        elif instrument_type == "INVERSE":
            record["category"] = "INVERSE"
        else:
            record["category"] = "EQUITY"
    return record


def validate_record(symbol: str, item: Dict[str, Any]) -> None:
    required = {
        "symbol", "full_symbol", "name", "market",
        "type", "instrument_type", "status",
    }
    missing = required - set(item)
    if missing:
        raise ValueError(f"{symbol}: missing {sorted(missing)}")
    if item["symbol"] != symbol or not is_valid_symbol(symbol):
        raise ValueError(f"{symbol}: invalid symbol")
    if item["market"] not in ALLOWED_MARKETS:
        raise ValueError(f"{symbol}: invalid market")
    if item["type"] not in ALLOWED_TYPES:
        raise ValueError(f"{symbol}: invalid type")
    if item["status"] != ACTIVE_STATUS:
        raise ValueError(f"{symbol}: status invalid")
    if not clean_text(item["name"]):
        raise ValueError(f"{symbol}: empty name")

    excluded, reason = is_excluded_instrument(
        symbol,
        item["name"],
        item.get("category", ""),
        item.get("cfi_code", ""),
        is_etf=item["type"] == "ETF",
    )
    if excluded:
        raise ValueError(f"{symbol}: excluded instrument survived: {reason}")


def validate_universe(
    stocks: Dict[str, Dict[str, Any]],
    terminated: Set[str],
) -> None:
    if not stocks:
        raise RuntimeError("Universe 為 0")
    active = set(stocks)
    overlap = active & terminated
    if overlap:
        raise RuntimeError(
            f"active ∩ terminated != 0: {sorted(overlap)[:50]}"
        )
    for symbol, item in stocks.items():
        validate_record(symbol, item)

    if "00838B" in active:
        raise RuntimeError("00838B 仍存在 active Universe")

    stock_count = sum(x["type"] == "STOCK" for x in stocks.values())
    etf_count = sum(x["type"] == "ETF" for x in stocks.values())
    twse_count = sum(x["market"] == "TWSE" for x in stocks.values())
    tpex_count = sum(x["market"] == "TPEX" for x in stocks.values())

    log("UNIVERSE VALIDATION")
    log(f"  Universe：{len(stocks):,}")
    log(f"  STOCK：{stock_count:,}")
    log(f"  ETF：{etf_count:,}")
    log(f"  TWSE：{twse_count:,}")
    log(f"  TPEX：{tpex_count:,}")
    log(f"  Terminated blocked：{len(terminated):,}")
    log("✓ active ∩ terminated = 0")
    log("✓ schema validation PASS")


def atomic_write_json(
    path: Path,
    payload: Dict[str, Any],
    terminated: Set[str],
) -> None:
    """
    關鍵修正：
    先寫 temp → reload temp → 完整 validation → fsync → replace。
    因此 post-write validation 失敗不會破壞舊 universe.json。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())

        with temp_path.open("r", encoding="utf-8") as f:
            check = json.load(f)

        if not isinstance(check, dict):
            raise RuntimeError("temporary JSON root invalid")
        stocks = check.get("stocks")
        if not isinstance(stocks, dict):
            raise RuntimeError("temporary stocks invalid")
        if check.get("universe_count") != len(stocks):
            raise RuntimeError("temporary universe_count mismatch")
        validate_universe(stocks, terminated)

        os.replace(temp_path, path)
        log(f"✓ atomic replace：{path}")
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass


def build_payload(
    stocks: Dict[str, Dict[str, Any]],
    official_count: int,
    terminated_count: int,
) -> Dict[str, Any]:
    stock_count = sum(x["type"] == "STOCK" for x in stocks.values())
    etf_count = sum(x["type"] == "ETF" for x in stocks.values())
    twse_count = sum(x["market"] == "TWSE" for x in stocks.values())
    tpex_count = sum(x["market"] == "TPEX" for x in stocks.values())

    return {
        "version": "UNIVERSE-BUILD-V4",
        "generated_at": now_tw().isoformat(),
        "universe_count": len(stocks),
        "stock_count": stock_count,
        "etf_count": etf_count,
        "market_count": {"TWSE": twse_count, "TPEX": tpex_count},
        "source": {
            "identity_primary": "FinMind TaiwanStockInfo",
            "active_etf_source": "FinMind TaiwanStockActiveETFInfo",
            "official_master": "TWSE ISIN C_public",
            "official_master_required": True,
            "official_candidates": official_count,
            "termination_primary": "FinMind TaiwanStockDelisting",
            "official_termination_secondary": True,
            "price_is_not_identity_source": True,
            "volume_is_not_identity_source": True,
            "yahoo_is_not_identity_source": True,
            "cmoney_is_not_identity_source": True,
        },
        "contract": {
            "root": "dict",
            "stocks": "dict",
            "active_status": "status == active",
            "allowed_types": sorted(ALLOWED_TYPES),
            "allowed_markets": sorted(ALLOWED_MARKETS),
            "official_master_required": True,
            "active_etf_supported": True,
            "six_digit_etf_supported": True,
            "bond_etf_supported": True,
            "preferred_share_excluded": True,
            "warrant_excluded": True,
            "etn_excluded": True,
            "reit_excluded": True,
            "tdr_excluded": True,
            "bond_excluded": True,
            "terminated_blocked": True,
            "metadata_preserved": True,
            "fixed_universe_count": False,
            "price_is_validation_only": True,
            "volume_is_validation_only": True,
            "old_universe_cannot_revive": True,
        },
        "stocks": dict(sorted(stocks.items())),
    }


def main() -> int:
    section("台股 AI 選股系統")
    log("UNIVERSE BUILDER V4")
    log(f"開始時間：{now_tw().isoformat()}")
    log(f"Universe：{UNIVERSE_FILE}")

    existing = load_existing()
    log(f"既有 Universe metadata：{len(existing):,} 檔")

    section("STEP 1 — IDENTITY")
    active_etfs = fetch_active_etfs()
    finmind = fetch_finmind_identity(active_etfs)
    if not finmind:
        raise RuntimeError("FinMind identity 沒有建立任何 candidate")

    official = fetch_official_master()

    section("STEP 2 — LIFECYCLE")
    finmind_terminated = fetch_finmind_delisted()
    official_terminated = fetch_official_delisted()
    terminated = finmind_terminated | official_terminated
    log(f"✓ Terminated union：{len(terminated):,}")

    section("STEP 3 — RESOLVE ACTIVE UNIVERSE")
    stocks: Dict[str, Dict[str, Any]] = {}
    stats = {
        "finmind_candidates": len(finmind),
        "official_candidates": len(official),
        "official_overlap": 0,
        "terminated": len(terminated),
        "terminated_removed": 0,
        "not_in_official": 0,
        "excluded": 0,
        "active": 0,
    }
    exclusion_reasons: Dict[str, int] = {}

    for symbol in sorted(finmind):
        if symbol in terminated:
            stats["terminated_removed"] += 1
            continue

        official_item = official.get(symbol)
        if not official_item:
            stats["not_in_official"] += 1
            continue
        stats["official_overlap"] += 1

        source = dict(finmind[symbol])
        if not source.get("name") and official_item.get("name"):
            source["name"] = official_item["name"]

        is_etf = source.get("type") == "ETF"
        name = clean_text(source.get("name"))
        industry = clean_text(source.get("industry_category"))
        cfi = clean_text(official_item.get("cfi"))

        excluded, reason = is_excluded_instrument(
            symbol, name, industry, cfi, is_etf=is_etf
        )
        if excluded:
            stats["excluded"] += 1
            exclusion_reasons[reason] = exclusion_reasons.get(reason, 0) + 1
            continue

        try:
            record = build_record(
                symbol, source, official_item, existing.get(symbol)
            )
            validate_record(symbol, record)
            stocks[symbol] = record
        except Exception as exc:
            log(f"⚠️ 排除 {symbol}: {exc}")
            stats["excluded"] += 1
            exclusion_reasons["validation"] = (
                exclusion_reasons.get("validation", 0) + 1
            )

    stats["active"] = len(stocks)

    section("UNIVERSE BUILD STATISTICS")
    for key in (
        "finmind_candidates", "official_candidates", "official_overlap",
        "terminated", "terminated_removed", "not_in_official",
        "excluded", "active",
    ):
        log(f"{key}: {stats[key]:,}")

    log("")
    log("EXCLUSION BREAKDOWN")
    for reason, count in sorted(exclusion_reasons.items()):
        log(f"  {reason}: {count:,}")

    suspicious_symbols = {
        "01003T", "01005T", "01008T", "2833A", "2883A", "2887C",
        "2888A", "2888B", "2891A", "2897A", "3036A", "3702A",
        "4129A", "708785", "709966", "710516", "710533", "710560",
        "710561", "710566", "710569", "710575", "711126", "711127",
        "711133", "711134", "711135", "711140", "711145",
        "73107P", "73193P", "8916A",
    }
    survivors = suspicious_symbols & set(stocks)
    if survivors:
        raise RuntimeError(f"特殊商品仍進入 Universe：{sorted(survivors)}")

    section("STEP 4 — PRE-WRITE VALIDATION")
    validate_universe(stocks, terminated)

    payload = build_payload(
        stocks, len(official), len(terminated)
    )

    section("STEP 5 — ATOMIC WRITE")
    atomic_write_json(UNIVERSE_FILE, payload, terminated)

    section("STEP 6 — FINAL READ-BACK")
    with UNIVERSE_FILE.open("r", encoding="utf-8") as f:
        written = json.load(f)
    if written.get("universe_count") != len(written.get("stocks", {})):
        raise RuntimeError("final universe_count mismatch")
    validate_universe(written["stocks"], terminated)

    log(f"✓ Universe：{written['universe_count']:,}")
    log(f"✓ STOCK：{written['stock_count']:,}")
    log(f"✓ ETF：{written['etf_count']:,}")
    log(f"✓ TWSE：{written['market_count']['TWSE']:,}")
    log(f"✓ TPEX：{written['market_count']['TPEX']:,}")
    log("✓ 00838B 不存在" if "00838B" not in written["stocks"]
        else "❌ 00838B 存在")
    section("UNIVERSE BUILD COMPLETED")
    log(f"完成時間：{now_tw().isoformat()}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("❌ 使用者中止")
        sys.exit(130)
    except Exception as exc:
        section("UNIVERSE BUILD FAILED")
        log(f"❌ {exc}")
        log("❌ 不覆蓋既有 universe.json")
        sys.exit(1)
