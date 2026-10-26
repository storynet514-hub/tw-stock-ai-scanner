
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Taiwan Stock AI UI data builder, UI-DATA-2.7.

Universe determines identity; Analysis determines signals; official sources
supply Chinese names. Missing names are reported together and never guessed.
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
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'Data'
VERSION = 'UI-DATA-2.7'
TWSE = 'https://openapi.twse.com.tw/v1/opendata/t187ap03_L'
TPEX = 'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O'
FUND = 'https://openapi.twse.com.tw/v1/opendata/t187ap47_L'
ISIN_URLS = {
    'ISIN_LISTED': 'https://isin.twse.com.tw/isin/C_public.jsp?strMode=2',
    'ISIN_OTC': 'https://isin.twse.com.tw/isin/C_public.jsp?strMode=4',
}
SOURCE_MINIMUM = {'TWSE': 900, 'TPEX': 750, 'TWSE_FUND': 200}
VERIFIED_NAMES = {'00411A': '主動統一前沿科技', '006201': '元大富櫃50'}
SYMBOL_KEYS = ('公司代號', '證券代號', '股票代號', '基金代號', 'ETF代號', '證券代碼', '有價證券代號', '代號', 'SecuritiesCompanyCode', 'CompanyCode', 'SecurityCode', 'SecuritiesCode', 'Code')
NAME_KEYS = ('公司簡稱', '公司名稱', '股票名稱', '基金簡稱', '基金名稱', '證券簡稱', '證券名稱', 'ETF名稱', '有價證券名稱', '名稱', '基金中文名稱', '基金中文簡稱', '中文簡稱', '中文名稱', 'CompanyAbbreviation', 'CompanyName', 'SecurityName', 'FundName', 'Name')
UNIVERSE_NAME_KEYS = ('display_name_zh', 'name_zh', 'chinese_name', 'security_name_zh', 'stock_name', '基金簡稱', '基金名稱', '證券簡稱', '名稱', '股票名稱', '公司簡稱', '公司名稱', 'display_name', 'name', 'security_name')
STRENGTH_RANK = {'強勢': 3, '中性': 2, '弱勢': 1}
BUYABLE = {'偏多，可分批', '積極關注', '續抱觀察'}


def clean(v: Any) -> str:
    return '' if v is None else re.sub(r'\s+', ' ', str(v).replace('\ufeff', '').replace('\xa0', ' ')).strip()


def symbol(v: Any) -> str:
    s = clean(v).upper()
    for suffix in ('.TWSE', '.TPEX', '.TWO', '.TSE', '.OTC', '.TW'):
        if s.endswith(suffix):
            return s[:-len(suffix)]
    return s


def chinese(v: Any) -> bool:
    return bool(re.search(r'[\u3400-\u9fff]', clean(v)))


def numeric(v: Any):
    try:
        if v is None or isinstance(v, bool):
            return None
        n = float(str(v).replace(',', '').replace('%', '').strip())
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def load(path: Path):
    if not path.is_file():
        raise RuntimeError(f'缺少資料檔案：{path}')
    with path.open(encoding='utf-8-sig') as f:
        return json.load(f)


def atomic_write(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def fetch(url: str, label: str, attempts=5) -> bytes:
    last = None
    for i in range(1, attempts + 1):
        print(f'{label} 下載 {i}/{attempts}', flush=True)
        try:
            request = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; TaiwanStockAI/2.7)',
                'Accept': 'application/json,text/html,*/*',
                'Accept-Encoding': 'identity',
                'Connection': 'close',
            })
            with urllib.request.urlopen(request, timeout=45) as response:
                raw = response.read()
                expected = response.headers.get('Content-Length')
                if expected and len(raw) != int(expected):
                    raise RuntimeError(f'Content-Length 不符：{len(raw)}/{expected}')
            if not raw:
                raise RuntimeError('回傳空資料')
            print(f'{label} 下載完成：{len(raw)} bytes', flush=True)
            return raw
        except (http.client.IncompleteRead, urllib.error.URLError, socket.timeout,
                TimeoutError, OSError, RuntimeError, ValueError) as exc:
            last = exc
            print(f'WARNING {label}: {type(exc).__name__}: {exc}', flush=True)
            if i < attempts:
                time.sleep(min(3 * i, 12))
    raise RuntimeError(f'{label} 下載失敗：{last}')


def first(row: dict, keys: tuple) -> str:
    for k in keys:
        v = clean(row.get(k))
        if v:
            return v
    return ''


def parse_api(raw: bytes, label: str) -> dict[str, str]:
    rows = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(rows, list):
        raise RuntimeError(f'{label} JSON 不是 array')
    names = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = symbol(first(row, SYMBOL_KEYS))
        name = first(row, NAME_KEYS)
        if code and chinese(name):
            names[code] = name
    if len(names) < SOURCE_MINIMUM[label]:
        raise RuntimeError(f'{label} 有效中文名稱只有 {len(names)} 筆，低於 {SOURCE_MINIMUM[label]} 筆')
    print(f'{label} 中文名稱：{len(names)}', flush=True)
    return names


class ISINTableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.row = []
        elif tag in ('td', 'th') and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None and self.row is not None:
            self.row.append(clean(''.join(self.cell)))
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            if self.row:
                self.rows.append(self.row)
            self.row = None
            self.cell = None


def decode_isin(raw: bytes) -> str:
    # The TWSE ISIN service commonly serves Big5; inspect meta first.
    head = raw[:2048].decode('ascii', errors='ignore').lower()
    match = re.search(r'charset\s*=\s*["\']?([\w-]+)', head)
    declared = match.group(1).lower() if match else ''
    encodings = ['cp950', 'big5', 'utf-8-sig'] if 'big5' in declared else ['utf-8-sig', 'cp950', 'big5']
    for encoding in encodings:
        try:
            result = raw.decode(encoding)
            if chinese(result):
                return result
        except UnicodeError:
            continue
    raise RuntimeError('ISIN 網頁編碼無法可靠解碼')


def parse_isin(raw: bytes, label: str) -> dict[str, str]:
    parser = ISINTableParser()
    parser.feed(decode_isin(raw))
    result = {}
    for row in parser.rows:
        if not row:
            continue
        # First cell: 00679B　元大美債20年; keep exact symbol.
        match = re.match(r'^([0-9]{4,6}[A-Z]?)\s+(.+)$', row[0], re.I)
        if not match:
            continue
        code, name = symbol(match.group(1)), clean(match.group(2))
        if chinese(name):
            result[code] = name
    if len(result) < 100:
        raise RuntimeError(f'{label} 解析筆數異常：{len(result)}，可能網頁格式改變')
    print(f'{label} 中文名稱：{len(result)}', flush=True)
    return result


def official_names() -> dict[str, str]:
    sources = (('TWSE', TWSE), ('TPEX', TPEX), ('TWSE_FUND', FUND))
    result = {}
    errors = []
    for label, url in sources:
        try:
            result.update(parse_api(fetch(url, label), label))
        except Exception as exc:
            errors.append(f'{label}: {exc}')
    if errors:
        raise RuntimeError('必要官方 API 失敗：\n' + '\n'.join(errors))
    # ISIN augments the three APIs; an ISIN outage does not discard verified API data.
    isin_ok = 0
    for label, url in ISIN_URLS.items():
        try:
            names = parse_isin(fetch(url, label, attempts=3), label)
            # ISIN is a supplementary name source, not an identity classifier.
            result.update(names)
            isin_ok += 1
        except Exception as exc:
            print(f'WARNING {label} 無法補充中文名稱：{exc}', flush=True)
    print(f'ISIN 成功來源：{isin_ok}/2；官方名稱合計：{len(result)}', flush=True)
    return result


def normalize_stocks(path: Path, label: str) -> tuple[dict, dict]:
    root = load(path)
    if not isinstance(root, dict) or not isinstance(root.get('stocks'), dict):
        raise RuntimeError(f'{label} stocks 必須是 object')
    result = {}
    for key, row in root['stocks'].items():
        if not isinstance(row, dict):
            continue
        code = symbol(key)
        if not code:
            continue
        if code in result:
            raise RuntimeError(f'{label} 代號重複：{code}')
        result[code] = dict(row)
        if label == 'Universe':
            result[code]['symbol'] = code
    if not result:
        raise RuntimeError(f'{label} 無有效商品')
    return root, result


def instrument(u: dict) -> str:
    kind = ''
    for key in ('type', 'official_type', 'security_type'):
        value = clean(u.get(key)).upper()
        if value in ('STOCK', 'STOCKS', 'ETF'):
            kind = 'STOCK' if value in ('STOCK', 'STOCKS') else 'ETF'
            break
    if not kind:
        raise RuntimeError(f"{u['symbol']} Universe type 無效")
    if kind == 'STOCK':
        return 'stock'
    category = clean(u.get('category') or u.get('asset_category') or u.get('instrument_category')).upper()
    return 'bond' if 'BOND' in category or '債' in category else 'etf'


def market(u: dict) -> str:
    value = clean(u.get('market') or u.get('exchange') or u.get('market_type')).upper()
    if value in ('TWSE', 'TSE'):
        return 'TWSE'
    if value in ('TPEX', 'OTC'):
        return 'TPEX'
    raise RuntimeError(f"{u['symbol']} market 無效：{value}")


def get_name(code: str, u: dict, names: dict) -> str:
    for value in (names.get(code), VERIFIED_NAMES.get(code), *(u.get(k) for k in UNIVERSE_NAME_KEYS)):
        if chinese(value):
            return clean(value)
    return ''


def check_names(universe: dict, analysis: dict, names: dict):
    missing = []
    for code, u in universe.items():
        if code in analysis and not get_name(code, u, names):
            missing.append(code)
    print(f'中文名稱驗證商品數：{sum(c in analysis for c in universe)}', flush=True)
    print(f'中文名稱缺漏數：{len(missing)}', flush=True)
    if missing:
        print('MISSING CHINESE NAMES', flush=True)
        for code in missing:
            u = universe[code]
            print(f"{code} | market={u.get('market')} | type={u.get('type')} | name={u.get('name')}", flush=True)
        raise RuntimeError(f'仍有 {len(missing)} 檔商品缺少已驗證中文名稱；不輸出不完整 UI')


def metrics(a: dict) -> tuple[dict, dict]:
    m = a.get('metrics')
    s = a.get('short_term')
    return (m if isinstance(m, dict) else {}, s if isinstance(s, dict) else {})


def first_num(rows: tuple):
    for value in rows:
        n = numeric(value)
        if n is not None:
            return n
    return None


def strength(a: dict) -> str:
    m, s = metrics(a)
    for raw in (s.get('strength'), s.get('technical_strength'), a.get('strength'), a.get('strength_label'), m.get('strength')):
        v = clean(raw)
        low = v.lower()
        if not v:
            continue
        if '強' in v or '多' in v or low in ('strong', 'bullish'):
            return '強勢'
        if '弱' in v or '空' in v or low in ('weak', 'bearish'):
            return '弱勢'
        if '中' in v or low in ('neutral', 'flat'):
            return '中性'
    return '中性'


def recommendation(a: dict) -> str:
    m, s = metrics(a)
    for raw in (a.get('recommendation'), a.get('signal'), s.get('recommendation'), m.get('recommendation')):
        v = clean(raw)
        if not v:
            continue
        if '立即進場' in v:
            return '積極關注'
        if '買進' in v or '偏多' in v:
            return '偏多，可分批'
        if '回測' in v:
            return '等回測'
        if '減碼' in v:
            return '考慮減碼'
        if '暫停' in v:
            return '暫停操作'
        if '續抱' in v or '觀察' in v:
            return '續抱觀察'
    return '續抱觀察'


def score(a: dict) -> float:
    _, s = metrics(a)
    return first_num((s.get('core_score'), a.get('core_score'), a.get('score'))) or 0.0


def record(code: str, u: dict, a: dict, date: str, names: dict) -> dict:
    m, _ = metrics(a)
    return {
        'symbol': code, 'name': get_name(code, u, names), 'market': market(u),
        'instrument_type': instrument(u),
        'price': first_num((m.get('price'), m.get('close'), m.get('latest_price'), m.get('last_price'), a.get('price'), a.get('close'))),
        'change': first_num((m.get('change'), m.get('price_change'), a.get('change'))),
        'change_pct': first_num((m.get('change_pct'), m.get('change_percent'), m.get('change1_pct'), a.get('change_pct'))),
        'strength': strength(a), 'recommendation': recommendation(a),
        'backend': {'status': 'ok', 'latest_date': date},
        'holding': {'shares': None, 'average_cost': None, 'market_value': None, 'profit': None, 'return_pct': None},
    }


def candidates(root: dict) -> list[str]:
    raw = root.get('short_term_candidates') or []
    if isinstance(raw, dict):
        raw = list(raw.values())
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        code = symbol(item.get('symbol') or item.get('code') or item.get('ticker')) if isinstance(item, dict) else symbol(item)
        if code and code not in out:
            out.append(code)
    return out


def explicit(a: dict) -> bool:
    s = a.get('short_term') if isinstance(a.get('short_term'), dict) else {}
    return any(a.get(k) is True or s.get(k) is True for k in ('qualified', 'is_candidate', 'candidate', 'short_term_qualified'))


def validate(data: dict):
    if data.get('schema_version') != VERSION:
        raise RuntimeError('schema_version 錯誤')
    stocks = data.get('stocks')
    if not isinstance(stocks, dict) or not stocks:
        raise RuntimeError('UI stocks 無效')
    for code, r in stocks.items():
        if r.get('symbol') != code or not chinese(r.get('name')):
            raise RuntimeError(f'{code} 代號或中文名稱無效')
        if r.get('instrument_type') not in ('stock', 'etf', 'bond'):
            raise RuntimeError(f'{code} 商品類型無效')
    tabs = data.get('tabs')
    keys = {'today_picks', 'top10', 'etf', 'bond', 'watchlist'}
    if not isinstance(tabs, dict) or set(tabs) != keys or any(not isinstance(tabs[k], list) for k in keys):
        raise RuntimeError('UI tabs 無效')
    today, top = tabs['today_picks'], tabs['top10']
    if len(top) > 10 or not set(top).issubset(set(today)):
        raise RuntimeError('Top10 必須為 Today Picks 子集合且最多10檔')
    for tab, kind in (('today_picks', 'stock'), ('etf', 'etf'), ('bond', 'bond')):
        if tab in ('etf', 'bond') and len(tabs[tab]) > 10:
            raise RuntimeError(f'{tab} 超過10檔')
        for code in tabs[tab]:
            if code not in stocks or stocks[code]['instrument_type'] != kind:
                raise RuntimeError(f'{tab} 商品分類錯誤：{code}')
    if '00400A' in stocks and stocks['00400A']['instrument_type'] != 'etf':
        raise RuntimeError('00400A 必須分類為 ETF')


def main():
    print('BUILD UI DATA - ' + VERSION, flush=True)
    _, universe = normalize_stocks(DATA / 'universe.json', 'Universe')
    analysis_root, analysis = normalize_stocks(DATA / 'analysis.json', 'Analysis')
    mkt = load(DATA / 'market.json')
    if not isinstance(mkt, dict):
        raise RuntimeError('market.json 必須是 object')
    date = clean(mkt.get('latest_trading_date'))
    names = official_names()
    check_names(universe, analysis, names)
    public = {code: record(code, u, analysis[code], date, names) for code, u in universe.items() if code in analysis}
    if not public:
        raise RuntimeError('Universe 和 Analysis 無交集')
    missing_analysis = [code for code in universe if code not in analysis]
    if missing_analysis:
        print(f'WARNING Universe 未匹配 Analysis：{len(missing_analysis)}；' + ', '.join(missing_analysis[:30]), flush=True)
    today = [code for code in candidates(analysis_root) if code in public and public[code]['instrument_type'] == 'stock']
    if not today:
        today = [code for code in public if public[code]['instrument_type'] == 'stock' and explicit(analysis[code])]
    def rank(code):
        return (STRENGTH_RANK.get(public[code]['strength'], 0), score(analysis[code]), code)
    today = sorted(set(today), key=rank, reverse=True)
    etf = sorted((c for c, r in public.items() if r['instrument_type'] == 'etf' and (r['strength'] == '強勢' or r['recommendation'] in BUYABLE)), key=rank, reverse=True)[:10]
    bond = sorted((c for c, r in public.items() if r['instrument_type'] == 'bond' and (r['strength'] == '強勢' or r['recommendation'] in BUYABLE)), key=rank, reverse=True)[:10]
    index = mkt.get('index') if isinstance(mkt.get('index'), dict) else {}
    sentiment = mkt.get('sentiment')
    output = {
        'schema_version': VERSION, 'generated_at': datetime.now(timezone.utc).isoformat(), 'status': 'ok',
        'market': {'name': '台股市場', 'timezone': 'Asia/Taipei', 'status': mkt.get('market_status'), 'latest_trading_date': date, 'index': index, 'sentiment': sentiment},
        'summary': {'today_picks_count': len(today), 'holdings': False, 'index': index, 'sentiment': sentiment},
        'tabs': {'today_picks': today, 'top10': today[:10], 'etf': etf, 'bond': bond, 'watchlist': []},
        'stocks': public,
    }
    validate(output)
    dest = DATA / 'ui_data.json'
    atomic_write(dest, output)
    validate(load(dest))
    print('UI DATA BUILD PASS', flush=True)
    print(f'Version={VERSION} Universe={len(universe)} Analysis={len(analysis)} UI={len(public)} Today={len(today)} Top10={len(today[:10])} ETF={len(etf)} Bond={len(bond)}', flush=True)
    print('中文名稱 / 商品分類 / read-back validation PASS', flush=True)


if __name__ == '__main__':
    main()
