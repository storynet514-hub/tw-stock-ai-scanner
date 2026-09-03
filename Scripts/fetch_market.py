#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""台股 AI 選股系統 - 市場環境資料建構。"""
from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "Data"
OUTPUT = DATA / "market.json"
UNIVERSE_FILE = DATA / "universe.json"
PRICES_DIR = DATA / "prices"
MANIFEST_FILE = PRICES_DIR / "manifest.json"

SCHEMA_VERSION = "market-v2.0"
TAIWAN_TZ = timezone(timedelta(hours=8))
TIMEOUT = 30

INDEX_URL = "https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX"
INDEX_HISTORY_URL = "https://openapi.twse.com.tw/v1/indicesReport/MI_5MINS_HIST"
MARKET_VOLUME_URL = "https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK"
INSTITUTIONAL_URL = "https://www.twse.com.tw/rwd/zh/fund/BFI82U"

CONFIG = {
    "ma_period": 20,
    "rsi_period": 14,
    "atr_period": 14,
    "breadth_ma_period": 20,
    "volume_ma_period": 20,
    "new_high_low_period": 20,
    "atr_pct_max": 0.03,
    "breadth_min_pct": 0.50,
    "advance_decline_min_ratio": 1.00,
    "volume_ratio_min": 1.00,
    "new_high_low_min_ratio": 1.00,
    "score_bullish": 8,
    "score_sideways": 5,
    "minimum_valid_conditions": 6,
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TW-Stock-AI-Scanner/2.0)",
    "Accept": "application/json, text/plain, */*",
}


def log(s: str) -> None:
    print(s, flush=True)


def num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(str(v).replace(",", "").replace("%", "").strip())
        return x if math.isfinite(x) else None
    except Exception:
        return None


def symbol(v: Any) -> str:
    s = str(v or "").strip().upper()
    for suffix in (".TW", ".TWO", ".TSE", ".OTC"):
        if s.endswith(suffix):
            s = s[:-len(suffix)]
            break
    return s


def parse_date(v: Any) -> Optional[date]:
    s = str(v or "").strip()
    if not s:
        return None
    if s.isdigit() and len(s) == 7:
        try:
            return date(int(s[:3]) + 1911, int(s[3:5]), int(s[5:7]))
        except ValueError:
            return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def request_json(url: str, params: Optional[Dict[str, Any]] = None) -> Any:
    r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    try:
        return r.json()
    except Exception as exc:
        preview = r.text[:300].replace("\n", " ")
        raise RuntimeError(f"非 JSON 回應：{url}; {preview}") from exc


def first_dict_rows(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("data", "rows", "records", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def table_rows(payload: Any) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    tables = payload.get("tables")
    if not isinstance(tables, list):
        return []
    out: List[Dict[str, Any]] = []
    for table in tables:
        if not isinstance(table, dict):
            continue
        fields = table.get("fields")
        rows = table.get("data")
        if not isinstance(fields, list) or not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, list):
                out.append({str(k).strip(): row[i] if i < len(row) else None for i, k in enumerate(fields)})
    return out


def fetch_index() -> Tuple[date, Dict[str, Any]]:
    rows = first_dict_rows(request_json(INDEX_URL))
    for row in rows:
        if str(row.get("指數", "")).strip() != "發行量加權股價指數":
            continue
        d = parse_date(row.get("日期"))
        close = num(row.get("收盤指數"))
        change = num(row.get("漲跌點數"))
        pct = num(row.get("漲跌百分比"))
        sign = str(row.get("漲跌", "")).strip()
        if sign == "-" and change is not None:
            change = -abs(change)
        if d and close is not None:
            return d, {"value": round(close, 2), "change": round(change, 2) if change is not None else None,
                       "change_pct": round(pct, 2) if pct is not None else None}
    raise RuntimeError("TWSE MI_INDEX 找不到發行量加權股價指數")


def fetch_index_history() -> List[Dict[str, Any]]:
    rows = first_dict_rows(request_json(INDEX_HISTORY_URL))
    out = []
    for row in rows:
        d = parse_date(row.get("Date") or row.get("日期"))
        close = num(row.get("ClosingIndex") or row.get("收盤指數"))
        high = num(row.get("HighestIndex") or row.get("最高指數"))
        low = num(row.get("LowestIndex") or row.get("最低指數"))
        if d and close is not None:
            out.append({"date": d.isoformat(), "close": close, "high": high, "low": low})
    out.sort(key=lambda x: x["date"])
    return out


def load_universe() -> Dict[str, Dict[str, Any]]:
    with UNIVERSE_FILE.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    stocks = data.get("stocks") if isinstance(data, dict) else None
    if not isinstance(stocks, dict):
        raise RuntimeError("universe.json stocks 必須是 object")
    return {symbol(k): v for k, v in stocks.items() if symbol(k) and isinstance(v, dict)}


def is_stock(item: Dict[str, Any]) -> bool:
    text = " ".join(str(item.get(k, "")) for k in ("type", "instrument_type", "security_type", "category", "product_type")).lower()
    if any(x in text for x in ("etf", "基金", "bond", "債券", "etn", "權證", "warrant", "reit")):
        return False
    return True


def extract_history(value: Any) -> Optional[List[Any]]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for k in ("prices", "history", "data", "rows", "records", "daily"):
            if isinstance(value.get(k), list):
                return value[k]
    return None


def extract_symbol_histories(data: Any) -> Dict[str, List[Any]]:
    out: Dict[str, List[Any]] = {}
    if isinstance(data, dict):
        containers = [data.get(k) for k in ("stocks", "prices", "data", "history")]
        for container in containers:
            if isinstance(container, dict):
                for k, v in container.items():
                    h = extract_history(v)
                    if symbol(k) and h is not None:
                        out[symbol(k)] = h
                if out:
                    return out
        for k, v in data.items():
            h = extract_history(v)
            if symbol(k) and h is not None:
                out[symbol(k)] = h
    elif isinstance(data, list):
        for row in data:
            if not isinstance(row, dict):
                continue
            s = symbol(row.get("symbol") or row.get("code") or row.get("ticker"))
            h = extract_history(row)
            if s and h is not None:
                out[s] = h
    return out


def parse_rows(history: List[Any]) -> List[Dict[str, Any]]:
    out = []
    for row in history:
        if not isinstance(row, dict):
            continue
        d = parse_date(row.get("date") or row.get("Date") or row.get("trade_date") or row.get("TradeDate"))
        close = num(row.get("close") or row.get("Close") or row.get("closing_price") or row.get("收盤價"))
        volume = num(row.get("volume") or row.get("Volume") or row.get("成交量") or row.get("成交股數"))
        if d and close is not None:
            out.append({"date": d, "close": close, "volume": volume})
    out.sort(key=lambda x: x["date"])
    return out


def load_price_histories(universe: Dict[str, Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    manifest = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    files = manifest.get("files", []) if isinstance(manifest, dict) else []
    paths = []
    for item in files:
        name = item if isinstance(item, str) else item.get("file") or item.get("path") or item.get("filename")
        if name and (PRICES_DIR / name).exists():
            paths.append(PRICES_DIR / name)
    if not paths:
        paths = sorted(PRICES_DIR.glob("prices_*.json"))
    out: Dict[str, List[Dict[str, Any]]] = {}
    allowed = {s for s, item in universe.items() if is_stock(item)}
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            raise RuntimeError(f"價格 shard JSON 讀取失敗：{path}: {exc}") from exc
        for s, history in extract_symbol_histories(payload).items():
            if s in allowed:
                rows = parse_rows(history)
                if rows:
                    out[s] = rows
    return out


def rsi(closes: List[float], period: int) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for a, b in zip(closes[-period-1:-1], closes[-period:]):
        delta = b - a
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    ag, al = sum(gains) / period, sum(losses) / period
    if al == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + ag / al))


def market_breadth(histories: Dict[str, List[Dict[str, Any]]], latest: date) -> Dict[str, Any]:
    latest_rows = []
    for rows in histories.values():
        current = [r for r in rows if r["date"] == latest]
        if current:
            latest_rows.append(rows)
    up = down = unchanged = 0
    above = 0
    high20 = low20 = 0
    valid_ma = valid_volume = valid_hl = 0
    total_volume = 0.0
    volumes: Dict[date, float] = {}
    for rows in latest_rows:
        closes = [r["close"] for r in rows]
        cur = rows[-1]
        prev = rows[-2] if len(rows) >= 2 else None
        if prev:
            if cur["close"] > prev["close"]: up += 1
            elif cur["close"] < prev["close"]: down += 1
            else: unchanged += 1
        last20 = [r["close"] for r in rows if r["date"] <= latest][-CONFIG["breadth_ma_period"]:]
        if len(last20) >= CONFIG["breadth_ma_period"]:
            valid_ma += 1
            if cur["close"] > sum(last20) / len(last20): above += 1
            valid_hl += 1
            window = last20
            if cur["close"] >= max(window): high20 += 1
            if cur["close"] <= min(window): low20 += 1
        vols = [r for r in rows if r.get("volume") is not None and r["date"] <= latest]
        if len(vols) >= CONFIG["volume_ma_period"] + 1:
            valid_volume += 1
            for r in vols[-CONFIG["volume_ma_period"]:]:
                volumes[r["date"]] = volumes.get(r["date"], 0.0) + float(r["volume"])
            total_volume += float(cur.get("volume") or 0.0)
    breadth_pct = above / valid_ma if valid_ma else None
    ad_ratio = up / down if down else (float("inf") if up else None)
    nhl_ratio = high20 / low20 if low20 else (float("inf") if high20 else None)
    avg20 = sum(volumes.values()) / len(volumes) if len(volumes) >= 20 else None
    volume_ratio = total_volume / avg20 if avg20 else None
    return {
        "advancing": up, "declining": down, "unchanged": unchanged,
        "advance_decline_ratio": ad_ratio if math.isfinite(ad_ratio or 0) else None,
        "above_ma20": above, "ma20_valid": valid_ma, "above_ma20_pct": breadth_pct,
        "new_high_20d": high20, "new_low_20d": low20,
        "new_high_low_ratio": nhl_ratio if math.isfinite(nhl_ratio or 0) else None,
        "volume": round(total_volume, 0) if total_volume else None,
        "volume_20d_average": round(avg20, 0) if avg20 else None,
        "volume_ratio": volume_ratio,
        "coverage": len(latest_rows),
    }


def index_metrics(history: List[Dict[str, Any]]) -> Dict[str, Any]:
    closes = [x["close"] for x in history]
    if len(closes) < 20:
        return {"ma20": None, "ma20_previous": None, "rsi14": None, "atr14_pct": None}
    ma20 = sum(closes[-20:]) / 20
    prev_ma20 = sum(closes[-21:-1]) / 20 if len(closes) >= 21 else None
    rr = rsi(closes, 14)
    trs = []
    for row, prev in zip(history[1:], history[:-1]):
        if row["high"] is None or row["low"] is None:
            continue
        trs.append(max(row["high"] - row["low"], abs(row["high"] - prev["close"]), abs(row["low"] - prev["close"])))
    atr = sum(trs[-14:]) / 14 if len(trs) >= 14 else None
    return {"ma20": ma20, "ma20_previous": prev_ma20, "rsi14": rr,
            "atr14_pct": (atr / closes[-1]) if atr is not None and closes[-1] else None}


def fetch_institutional(trading_date: date) -> Dict[str, Any]:
    params = {"response": "json", "dayDate": trading_date.strftime("%Y%m%d"), "type": "day"}
    try:
        payload = request_json(INSTITUTIONAL_URL, params)
    except Exception as exc:
        log(f"⚠️ 三大法人資料取得失敗：{exc}")
        return {"status": "unavailable", "reason": str(exc)}
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    result: Dict[str, Any] = {"status": "unavailable"}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, list) or len(row) < 4:
            continue
        label = str(row[0]).strip()
        if label == "外資及陸資":
            result["foreign_net"] = num(row[3])
        elif label == "投信":
            result["trust_net"] = num(row[3])
    if "foreign_net" in result and "trust_net" in result:
        result["status"] = "ok"
    return result


def condition(name: str, value: Any, passed: Optional[bool], threshold: Any, unit: str = "") -> Dict[str, Any]:
    return {"name": name, "value": value, "pass": passed, "threshold": threshold, "unit": unit,
            "status": "pass" if passed is True else "fail" if passed is False else "unavailable"}


def build_sentiment(conditions: List[Dict[str, Any]]) -> Dict[str, Any]:
    valid = [c for c in conditions if c["pass"] is not None]
    score = sum(1 for c in valid if c["pass"] is True)
    if len(valid) < CONFIG["minimum_valid_conditions"]:
        level, desc = "資料不足", "有效市場條件不足，停止放大風險"
    elif score >= CONFIG["score_bullish"]:
        level, desc = "偏多", "市場氣氛偏強"
    elif score >= CONFIG["score_sideways"]:
        level, desc = "震盪", "多空力量接近"
    else:
        level, desc = "偏弱", "市場氣氛偏弱"
    return {"level": level, "description": desc, "score": score, "valid_conditions": len(valid), "total_conditions": len(conditions)}


def atomic_write(data: Dict[str, Any]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".market.", suffix=".tmp", dir=DATA)
    p = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write("\n"); f.flush(); os.fsync(f.fileno())
        os.replace(p, OUTPUT)
    finally:
        p.unlink(missing_ok=True)


def validate(data: Dict[str, Any]) -> None:
    required = {"schema_version", "generated_at", "market_status", "latest_trading_date", "index", "trend", "breadth", "volume", "institutional", "sentiment", "conditions", "source", "config"}
    missing = required - set(data)
    if missing:
        raise RuntimeError(f"market.json 缺少欄位：{sorted(missing)}")
    if data["schema_version"] != SCHEMA_VERSION:
        raise RuntimeError("schema_version 錯誤")
    if data["market_status"] not in {"open", "closed"}:
        raise RuntimeError("market_status 無效")
    if not isinstance(data["conditions"], list) or len(data["conditions"]) != 10:
        raise RuntimeError("市場核心條件必須正好 10 項")
    if data["sentiment"].get("level") not in {"偏多", "震盪", "偏弱", "資料不足"}:
        raise RuntimeError("市場風向 level 無效")
    if num(data["index"].get("value")) is None:
        raise RuntimeError("TAIEX value 無效")


def main() -> int:
    log("=" * 72); log("FETCH MARKET V2.0"); log("=" * 72)
    now = datetime.now(TAIWAN_TZ)
    latest_date, idx = fetch_index()
    history = fetch_index_history()
    history = [x for x in history if parse_date(x["date"]) <= latest_date]
    if history and parse_date(history[-1]["date"]) != latest_date:
        history.append({"date": latest_date.isoformat(), "close": idx["value"], "high": idx["value"], "low": idx["value"]})
    im = index_metrics(history)
    universe = load_universe()
    histories = load_price_histories(universe)
    breadth = market_breadth(histories, latest_date)
    inst = fetch_institutional(latest_date)

    close = idx["value"]
    c = []
    c.append(condition("TAIEX > MA20", close > im["ma20"] if im["ma20"] is not None else None, close > im["ma20"] if im["ma20"] is not None else None, "close > MA20"))
    c.append(condition("MA20 上升", im["ma20"] > im["ma20_previous"] if im["ma20"] is not None and im["ma20_previous"] is not None else None, im["ma20"] > im["ma20_previous"] if im["ma20"] is not None and im["ma20_previous"] is not None else None, "MA20 > 前一日 MA20"))
    c.append(condition("TAIEX RSI14 > 50", im["rsi14"], im["rsi14"] > 50 if im["rsi14"] is not None else None, 50.0))
    c.append(condition("上漲家數 / 下跌家數 >= 1", breadth["advance_decline_ratio"], breadth["advance_decline_ratio"] >= 1 if breadth["advance_decline_ratio"] is not None else None, 1.0))
    c.append(condition("站上 MA20 比例 >= 50%", breadth["above_ma20_pct"], breadth["above_ma20_pct"] >= CONFIG["breadth_min_pct"] if breadth["above_ma20_pct"] is not None else None, 0.50))
    c.append(condition("市場成交量 / 20日均量 >= 1", breadth["volume_ratio"], breadth["volume_ratio"] >= CONFIG["volume_ratio_min"] if breadth["volume_ratio"] is not None else None, 1.0))
    c.append(condition("外資買賣超 > 0", inst.get("foreign_net"), inst.get("foreign_net") > 0 if inst.get("foreign_net") is not None else None, 0))
    c.append(condition("投信買賣超 > 0", inst.get("trust_net"), inst.get("trust_net") > 0 if inst.get("trust_net") is not None else None, 0))
    c.append(condition("20日新高 / 新低 >= 1", breadth["new_high_low_ratio"], breadth["new_high_low_ratio"] >= 1 if breadth["new_high_low_ratio"] is not None else None, 1.0))
    c.append(condition("TAIEX ATR14% <= 3%", im["atr14_pct"], im["atr14_pct"] <= CONFIG["atr_pct_max"] if im["atr14_pct"] is not None else None, 0.03))
    sentiment = build_sentiment(c)
    market_status = "open" if now.weekday() < 5 and now.date() == latest_date and time(9, 0) <= now.time() <= time(13, 30) else "closed"
    data = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(timespec="seconds"),
        "market_status": market_status,
        "latest_trading_date": latest_date.isoformat(),
        "index": {"name": "加權指數", **idx},
        "trend": {"ma20": im["ma20"], "ma20_previous": im["ma20_previous"], "rsi14": im["rsi14"], "atr14_pct": im["atr14_pct"]},
        "breadth": breadth,
        "volume": {"market_volume": breadth["volume"], "average_20d": breadth["volume_20d_average"], "ratio": breadth["volume_ratio"]},
        "institutional": inst,
        "sentiment": sentiment,
        "conditions": c,
        "source": {"index": INDEX_URL, "index_history": INDEX_HISTORY_URL, "market_volume": "Data/prices/ official-priority histories", "institutional": INSTITUTIONAL_URL},
        "config": CONFIG,
        "coverage": {"universe": len(universe), "stock_price_histories": len(histories), "latest_date": latest_date.isoformat()},
    }
    validate(data)
    atomic_write(data)
    readback = json.loads(OUTPUT.read_text(encoding="utf-8"))
    validate(readback)
    log(f"✓ 最新交易日：{latest_date}")
    log(f"✓ 加權指數：{idx['value']}")
    log(f"✓ 市場條件：{sentiment['score']}/{sentiment['valid_conditions']} valid → {sentiment['level']}")
    log(f"✓ 股票價格歷史覆蓋：{len(histories)}")
    log(f"✓ Data/market.json：PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
