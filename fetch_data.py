#!/usr/bin/env python3
"""
Market data collector for the F&O Signal app.
Only Python standard library is used (no pip install needed).
Writes data.json next to this file. Run by GitHub Actions on a schedule.

Sources (all free, best-effort):
  - Yahoo Finance chart API : indices, global markets, crude, gold, USDINR, DXY, US10Y, India VIX
  - NSE India               : FII/DII cash-market net flows, option chain (PCR, OI support/resistance)
  - Google News RSS         : India + global market headlines (keyword sentiment)
If a source fails, the previous value from data.json is kept and marked stale.
"""
import json, os, re, time, math, gzip, io
import urllib.request, urllib.parse, http.cookiejar
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def http_get(url, headers=None, opener=None, timeout=25):
    h = {"User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "gzip"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    op = opener.open if opener else urllib.request.urlopen
    with op(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        return raw.decode("utf-8", "replace")


# ---------------------------------------------------------------- indicators
def ema(vals, n):
    k = 2 / (n + 1)
    e = vals[0]
    out = [e]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def rsi(closes, n=14):
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def atr(highs, lows, closes, n=14):
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    if len(trs) < n:
        return None
    a = sum(trs[:n]) / n
    for t in trs[n:]:
        a = (a * (n - 1) + t) / n
    return a


def yahoo(sym, rng="1y"):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=1d"
           % (urllib.parse.quote(sym), rng))
    j = json.loads(http_get(url))
    r = j["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    rows = [(c, h, l) for c, h, l in zip(q["close"], q["high"], q["low"])
            if c is not None and h is not None and l is not None]
    if len(rows) < 3:
        raise ValueError("not enough rows for " + sym)
    closes = [x[0] for x in rows]
    highs = [x[1] for x in rows]
    lows = [x[2] for x in rows]
    return closes, highs, lows


def simple_quote(sym):
    c, _, _ = yahoo(sym, "1mo")
    return {"price": round(c[-1], 4), "prev": round(c[-2], 4),
            "chg_pct": round((c[-1] / c[-2] - 1) * 100, 3)}


def index_block(sym):
    c, h, l = yahoo(sym, "1y")
    e12, e26 = ema(c, 12), ema(c, 26)
    macd = [a - b for a, b in zip(e12, e26)]
    sig = ema(macd, 9)
    ph, pl, pc = h[-2], l[-2], c[-2]          # previous completed session
    p = (ph + pl + pc) / 3
    r1, s1 = 2 * p - pl, 2 * p - ph
    r2, s2 = p + (ph - pl), p - (ph - pl)
    return {
        "price": round(c[-1], 2), "prev": round(c[-2], 2),
        "chg_pct": round((c[-1] / c[-2] - 1) * 100, 3),
        "rsi": round(rsi(c) or 50, 2),
        "ema20": round(ema(c, 20)[-1], 2),
        "ema50": round(ema(c, 50)[-1], 2),
        "sma200": round(sum(c[-200:]) / 200, 2) if len(c) >= 200 else None,
        "macd_hist": round(macd[-1] - sig[-1], 3),
        "atr": round(atr(h, l, c) or 0, 2),
        "pivot": round(p, 2), "r1": round(r1, 2), "r2": round(r2, 2),
        "s1": round(s1, 2), "s2": round(s2, 2),
        "hi20": round(max(h[-20:]), 2), "lo20": round(min(l[-20:]), 2),
    }


# ---------------------------------------------------------------- intraday
def pivots(h, l, c):
    p = (h + l + c) / 3
    return {"p": round(p, 2), "r1": round(2 * p - l, 2), "s1": round(2 * p - h, 2),
            "r2": round(p + (h - l), 2), "s2": round(p - (h - l), 2)}


def intraday_block(sym):
    """15-minute candles (last 5 sessions) -> opening range, gap, EMA/RSI, pivots."""
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/%s?range=5d&interval=15m"
           % urllib.parse.quote(sym))
    j = json.loads(http_get(url))
    r = j["chart"]["result"][0]
    off = int(r["meta"].get("gmtoffset", 19800))
    q = r["indicators"]["quote"][0]
    days, closes = {}, []
    for t, o, h, l, c in zip(r["timestamp"], q["open"], q["high"], q["low"], q["close"]):
        if None in (o, h, l, c):
            continue
        dt = datetime.fromtimestamp(t + off, timezone.utc).replace(tzinfo=None)   # exchange-local time
        days.setdefault(dt.date().isoformat(), []).append((dt, o, h, l, c))
        closes.append(c)
    keys = sorted(days)
    if not keys:
        raise ValueError("no intraday bars")
    sess = days[keys[-1]]
    prev = days[keys[-2]] if len(keys) > 1 else None
    sh, sl_ = max(b[2] for b in sess), min(b[3] for b in sess)
    out = {
        "session_date": keys[-1], "bars": len(sess), "last_bar": sess[-1][0].strftime("%H:%M"),
        "open": round(sess[0][1], 2), "high": round(sh, 2), "low": round(sl_, 2),
        "last": round(sess[-1][4], 2),
        "ema9": round(ema(closes, 9)[-1], 2), "ema21": round(ema(closes, 21)[-1], 2),
        "rsi15": round(rsi(closes) or 50, 2),
        "or_ready": len(sess) >= 2,
        "lv_next": pivots(sh, sl_, sess[-1][4]),      # levels for the NEXT session
    }
    if len(sess) >= 2:                                 # first two 15-min bars = 9:15-9:45
        out["or_high"] = round(max(b[2] for b in sess[:2]), 2)
        out["or_low"] = round(min(b[3] for b in sess[:2]), 2)
    if prev:
        ph, pl, pc = max(b[2] for b in prev), min(b[3] for b in prev), prev[-1][4]
        out.update({"prev_close": round(pc, 2), "prev_high": round(ph, 2), "prev_low": round(pl, 2),
                    "gap_pct": round((sess[0][1] / pc - 1) * 100, 3),
                    "lv_prev": pivots(ph, pl, pc)})    # levels for the session in progress
    return out


# ---------------------------------------------------------------- NSE
def nse_session():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    hdr = {"Referer": "https://www.nseindia.com/", "Accept-Language": "en-US,en;q=0.9",
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
           "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
           "Sec-Fetch-Site": "none", "Upgrade-Insecure-Requests": "1"}
    last = None
    for i in range(3):                      # NSE often rejects the first try
        try:
            http_get("https://www.nseindia.com/", hdr, op)
            http_get("https://www.nseindia.com/market-data/live-equity-market", hdr, op)
            break
        except Exception as e:
            last = e
            time.sleep(3 * (i + 1))
    else:
        raise last
    time.sleep(1)
    api = dict(hdr)
    api.update({"Accept": "application/json, text/plain, */*", "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin"})
    return op, api


def to_num(x):
    try:
        return float(str(x).replace(",", ""))
    except Exception:
        return 0.0


def fii_dii(op, hdr):
    j = json.loads(http_get("https://www.nseindia.com/api/fiidiiTradeReact", hdr, op))
    out = {"date": None, "fii_net": None, "dii_net": None}
    for row in j:
        cat = str(row.get("category", "")).upper()
        net = to_num(row.get("netValue"))
        out["date"] = row.get("date", out["date"])
        if cat.startswith("FII"):
            out["fii_net"] = net
        elif cat.startswith("DII"):
            out["dii_net"] = net
    if out["fii_net"] is None and out["dii_net"] is None:
        raise ValueError("empty FII/DII")
    return out


def option_chain(op, hdr, symbol):
    url = "https://www.nseindia.com/api/option-chain-indices?symbol=" + symbol
    j = json.loads(http_get(url, hdr, op))
    exp = j["records"]["expiryDates"][0]
    ce_tot = pe_tot = 0.0
    best_ce = (0, None)
    best_pe = (0, None)
    for row in j["records"]["data"]:
        if row.get("expiryDate") != exp:
            continue
        k = row["strikePrice"]
        ce = row.get("CE", {}).get("openInterest", 0) or 0
        pe = row.get("PE", {}).get("openInterest", 0) or 0
        ce_tot += ce
        pe_tot += pe
        if ce > best_ce[0]:
            best_ce = (ce, k)
        if pe > best_pe[0]:
            best_pe = (pe, k)
    if ce_tot == 0:
        raise ValueError("no OI")
    return {"expiry": exp, "pcr": round(pe_tot / ce_tot, 3),
            "max_call_strike": best_ce[1], "max_put_strike": best_pe[1]}


# ---------------------------------------------------------------- news
POS = ["rally", "surge", "gain", "jump", "record high", "rise", "soar", "bull", "upbeat",
       "rate cut", "buying", "inflow", "strong", "optimis", "recover", "rebound", "boost",
       "ceasefire", "deal", "outperform", "higher", "climb", "positive"]
NEG = ["fall", "slump", "crash", "plunge", "drop", "sell-off", "selloff", "bear", "rate hike",
       "outflow", "selling", "weak", "fear", "war", "tariff", "inflation", "recession",
       "downgrade", "slide", "tumble", "loss", "pressure", "concern", "risk", "default",
       "sanction", "conflict", "lower", "negative", "cut estimates"]


def sentiment(title):
    t = title.lower()
    p = sum(1 for w in POS if w in t)
    n = sum(1 for w in NEG if w in t)
    return round((p - n) / max(1, p + n), 2)


def news(query, limit=12):
    url = ("https://news.google.com/rss/search?q=%s&hl=en-IN&gl=IN&ceid=IN:en"
           % urllib.parse.quote(query))
    root = ET.fromstring(http_get(url))
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        if not title:
            continue
        src = it.find("source")
        items.append({"title": title,
                      "source": (src.text if src is not None else ""),
                      "link": it.findtext("link") or "",
                      "date": it.findtext("pubDate") or "",
                      "score": sentiment(title)})
        if len(items) >= limit:
            break
    if not items:
        raise ValueError("no news")
    avg = round(sum(i["score"] for i in items) / len(items), 3)
    return items, avg


# ---------------------------------------------------------------- main
def main():
    try:
        with open(OUT, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    data.setdefault("indices", {})
    data.setdefault("global", {})
    data.setdefault("oi", {})
    data.setdefault("fii_dii", {})
    data.setdefault("news", {})
    errors = []

    for key, sym in [("nifty", "^NSEI"), ("banknifty", "^NSEBANK"), ("sensex", "^BSESN")]:
        old_intra = (data["indices"].get(key) or {}).get("intra")
        try:
            data["indices"][key] = index_block(sym)
            if old_intra:
                data["indices"][key]["intra"] = old_intra      # keep old if the new fetch fails
        except Exception as e:
            errors.append("index %s: %s" % (key, e))
        try:
            if key in data["indices"]:
                data["indices"][key]["intra"] = intraday_block(sym)
        except Exception as e:
            errors.append("intraday %s: %s" % (key, e))

    G = [("sp500", "^GSPC"), ("nasdaq", "^IXIC"), ("dow", "^DJI"), ("nikkei", "^N225"),
         ("hangseng", "^HSI"), ("ftse", "^FTSE"), ("dax", "^GDAXI"),
         ("crude", "CL=F"), ("gold", "GC=F"), ("usdinr", "INR=X"), ("dxy", "DX-Y.NYB"),
         ("us10y", "^TNX"), ("vix", "^INDIAVIX")]
    for key, sym in G:
        try:
            data["global"][key] = simple_quote(sym)
        except Exception as e:
            errors.append("global %s: %s" % (key, e))

    try:
        op, hdr = nse_session()
        try:
            data["fii_dii"] = fii_dii(op, hdr)
        except Exception as e:
            errors.append("fii_dii: %s" % e)
        for key, sym in [("nifty", "NIFTY"), ("banknifty", "BANKNIFTY")]:
            try:
                data["oi"][key] = option_chain(op, hdr, sym)
            except Exception as e:
                errors.append("option chain %s: %s" % (key, e))
    except Exception as e:
        errors.append("nse session: %s" % e)

    try:
        items, avg = news("Nifty OR Sensex OR FII OR \"Indian stock market\" when:1d")
        data["news"]["india"] = items
        data["news"]["india_score"] = avg
    except Exception as e:
        errors.append("news india: %s" % e)
    try:
        items, avg = news("\"Wall Street\" OR \"crude oil\" OR \"Federal Reserve\" OR \"Asian markets\" when:1d")
        data["news"]["global"] = items
        data["news"]["global_score"] = avg
    except Exception as e:
        errors.append("news global: %s" % e)

    data["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data["errors"] = errors
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("data.json written; %d source error(s)" % len(errors))
    for e in errors:
        print(" -", e)


if __name__ == "__main__":
    main()
