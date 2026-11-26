#!/usr/bin/env python3
"""
Long-term share data collector -> stocks.json   (run once a day by GitHub Actions)
Standard library only.

Per stock (Yahoo Finance):
  * 5 years of daily prices -> 50/200-DMA, 52-week range, 1m/3m/6m/12m returns,
    12-1 month momentum, 3y/5y CAGR, RSI, 1-year volatility
  * fundamentals via quoteSummary (needs a cookie + crumb): PE, P/B, ROE, debt/equity,
    margins, growth, analyst target & rating, dividend yield, beta
If fundamentals are blocked the stock still gets price-based data (the app marks it "limited data").
"""
import json, os, math, time
import urllib.parse, urllib.request, urllib.error, http.cookiejar
from datetime import datetime, timezone

import fetch_data as fd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "stocks.json")

# (Yahoo symbol, display name, sector)  -- edit freely; tickers can change after corporate actions
UNIVERSE = [
    ("HDFCBANK.NS", "HDFC Bank", "Banks"), ("ICICIBANK.NS", "ICICI Bank", "Banks"),
    ("SBIN.NS", "State Bank of India", "Banks"), ("KOTAKBANK.NS", "Kotak Mahindra Bank", "Banks"),
    ("AXISBANK.NS", "Axis Bank", "Banks"), ("INDUSINDBK.NS", "IndusInd Bank", "Banks"),
    ("BAJFINANCE.NS", "Bajaj Finance", "NBFC & Insurance"), ("BAJAJFINSV.NS", "Bajaj Finserv", "NBFC & Insurance"),
    ("SHRIRAMFIN.NS", "Shriram Finance", "NBFC & Insurance"), ("JIOFIN.NS", "Jio Financial", "NBFC & Insurance"),
    ("SBILIFE.NS", "SBI Life Insurance", "NBFC & Insurance"), ("HDFCLIFE.NS", "HDFC Life", "NBFC & Insurance"),
    ("TCS.NS", "TCS", "IT"), ("INFY.NS", "Infosys", "IT"), ("HCLTECH.NS", "HCL Tech", "IT"),
    ("WIPRO.NS", "Wipro", "IT"), ("TECHM.NS", "Tech Mahindra", "IT"),
    ("RELIANCE.NS", "Reliance Industries", "Energy"), ("ONGC.NS", "ONGC", "Energy"),
    ("COALINDIA.NS", "Coal India", "Energy"),
    ("NTPC.NS", "NTPC", "Power"), ("POWERGRID.NS", "Power Grid", "Power"),
    ("HINDUNILVR.NS", "Hindustan Unilever", "FMCG"), ("ITC.NS", "ITC", "FMCG"),
    ("NESTLEIND.NS", "Nestle India", "FMCG"), ("BRITANNIA.NS", "Britannia", "FMCG"),
    ("TITAN.NS", "Titan", "Consumer"), ("ASIANPAINT.NS", "Asian Paints", "Consumer"),
    ("TRENT.NS", "Trent", "Consumer"), ("ETERNAL.NS", "Eternal (Zomato)", "Consumer"),
    ("MARUTI.NS", "Maruti Suzuki", "Auto"), ("M&M.NS", "Mahindra & Mahindra", "Auto"),
    ("EICHERMOT.NS", "Eicher Motors", "Auto"), ("HEROMOTOCO.NS", "Hero MotoCorp", "Auto"),
    ("BAJAJ-AUTO.NS", "Bajaj Auto", "Auto"),
    ("SUNPHARMA.NS", "Sun Pharma", "Pharma & Health"), ("DRREDDY.NS", "Dr Reddy's", "Pharma & Health"),
    ("CIPLA.NS", "Cipla", "Pharma & Health"), ("DIVISLAB.NS", "Divi's Labs", "Pharma & Health"),
    ("APOLLOHOSP.NS", "Apollo Hospitals", "Pharma & Health"),
    ("LT.NS", "Larsen & Toubro", "Infra & Defence"), ("ADANIPORTS.NS", "Adani Ports", "Infra & Defence"),
    ("BEL.NS", "Bharat Electronics", "Infra & Defence"),
    ("TATASTEEL.NS", "Tata Steel", "Metals"), ("JSWSTEEL.NS", "JSW Steel", "Metals"),
    ("HINDALCO.NS", "Hindalco", "Metals"),
    ("ULTRACEMCO.NS", "UltraTech Cement", "Cement"), ("GRASIM.NS", "Grasim", "Cement"),
    ("BHARTIARTL.NS", "Bharti Airtel", "Telecom"),
]


# ------------------------------------------------------------------ helpers
def num(v):
    if isinstance(v, dict):
        v = v.get("raw")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def r2(x, n=2):
    return None if x is None else round(x, n)


def yahoo_session():
    """Cookie + crumb that Yahoo's quoteSummary endpoint requires."""
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    try:
        fd.http_get("https://fc.yahoo.com", opener=op)       # answers 404 but sets the cookie
    except urllib.error.HTTPError:
        pass
    crumb = fd.http_get("https://query1.finance.yahoo.com/v1/test/getcrumb", opener=op).strip()
    if not crumb or "<" in crumb or " " in crumb or len(crumb) > 40:
        raise ValueError("no crumb")
    return op, crumb


def fundamentals(op, crumb, sym):
    url = ("https://query1.finance.yahoo.com/v10/finance/quoteSummary/%s"
           "?modules=financialData,defaultKeyStatistics,summaryDetail&crumb=%s"
           % (urllib.parse.quote(sym), urllib.parse.quote(crumb)))
    j = json.loads(fd.http_get(url, opener=op))
    res = j["quoteSummary"]["result"][0]
    f, k, s = res.get("financialData") or {}, res.get("defaultKeyStatistics") or {}, res.get("summaryDetail") or {}
    roe, pm, rg, eg = num(f.get("returnOnEquity")), num(f.get("profitMargins")), num(f.get("revenueGrowth")), num(f.get("earningsGrowth"))
    de = num(f.get("debtToEquity"))
    dy = num(s.get("trailingAnnualDividendYield"))
    return {
        "pe": r2(num(s.get("trailingPE"))), "pb": r2(num(k.get("priceToBook"))),
        "dy": r2(dy * 100) if dy is not None and dy < 0.3 else None,
        "beta": r2(num(s.get("beta")) if num(s.get("beta")) is not None else num(k.get("beta"))),
        "roe": r2(roe * 100) if roe is not None else None,
        "de": r2(de / 100) if de is not None else None,       # Yahoo gives it in percent
        "pm": r2(pm * 100) if pm is not None else None,
        "rg": r2(rg * 100) if rg is not None else None,
        "eg": r2(eg * 100) if eg is not None else None,
        "target": r2(num(f.get("targetMeanPrice"))),
        "n_an": num(f.get("numberOfAnalystOpinions")),
        "rec": r2(num(f.get("recommendationMean"))),
        "mcap": num(s.get("marketCap")),
    }


def price_block(sym):
    c, h, l = fd.yahoo(sym, "5y")
    n = len(c)
    if n < 60:
        raise ValueError("too little history (%d bars)" % n)

    def back(k):                      # close k trading days ago
        return c[-1 - k] if n > k else None

    def ret(k):
        b = back(k)
        return (c[-1] / b - 1) * 100 if b else None

    yr = min(n, 252)
    rets = [math.log(c[i] / c[i - 1]) for i in range(n - yr + 1, n) if c[i - 1] > 0 and c[i] > 0]
    mean = sum(rets) / len(rets)
    vol = math.sqrt(sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)) * math.sqrt(252) * 100 if len(rets) > 20 else None
    b1, b12 = back(21), back(252)
    cagr3 = ((c[-1] / back(756)) ** (1 / 3) - 1) * 100 if back(756) else None
    cagr5 = ((c[-1] / c[0]) ** (252 / n) - 1) * 100 if n >= 1000 else None
    return {
        "price": r2(c[-1]),
        "hi52": r2(max(h[-yr:])), "lo52": r2(min(l[-yr:])),
        "sma50": r2(sum(c[-50:]) / 50), "sma200": r2(sum(c[-200:]) / 200) if n >= 200 else None,
        "ret1m": r2(ret(21)), "ret3m": r2(ret(63)), "ret6m": r2(ret(126)), "ret12m": r2(ret(252)),
        "mom12_1": r2((b1 / b12 - 1) * 100) if (b1 and b12) else None,
        "cagr3": r2(cagr3), "cagr5": r2(cagr5),
        "rsi": r2(fd.rsi(c[-120:])), "vol": r2(vol),
    }


def nifty_block():
    c, _, _ = fd.yahoo("^NSEI", "2y")
    n = len(c)
    return {"price": r2(c[-1]), "sma200": r2(sum(c[-200:]) / 200) if n >= 200 else None,
            "ret12m": r2((c[-1] / c[-253] - 1) * 100) if n > 253 else None}


# ------------------------------------------------------------------ main
def main():
    try:
        with open(OUT, encoding="utf-8") as f:
            old = {s["sym"]: s for s in json.load(f).get("stocks", [])}
    except Exception:
        old = {}
    errors, stocks = [], []

    try:
        nifty = nifty_block()
    except Exception as e:
        nifty = {}
        errors.append("nifty: %s" % e)

    op = crumb = None
    try:
        op, crumb = yahoo_session()
    except Exception as e:
        errors.append("yahoo crumb (fundamentals skipped): %s" % e)

    fund_ok = 0
    for sym, name, sector in UNIVERSE:
        rec = {"sym": sym, "name": name, "sector": sector}
        try:
            rec.update(price_block(sym))
        except Exception as e:
            errors.append("price %s: %s" % (sym, e))
            if sym in old:
                rec = dict(old[sym], stale=True)
                stocks.append(rec)
            time.sleep(0.3)
            continue
        if op:
            try:
                rec.update(fundamentals(op, crumb, sym))
                fund_ok += 1
            except Exception as e:
                errors.append("fundamentals %s: %s" % (sym, e))
                for k in ("pe", "pb", "dy", "beta", "roe", "de", "pm", "rg", "eg", "target", "n_an", "rec", "mcap"):
                    if k in old.get(sym, {}):
                        rec[k] = old[sym][k]
        stocks.append(rec)
        time.sleep(0.4)

    out = {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "nifty": nifty, "fund_ok": fund_ok, "total": len(UNIVERSE),
           "stocks": stocks, "errors": errors}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("stocks.json: %d stocks, fundamentals for %d, %d error(s)" % (len(stocks), fund_ok, len(errors)))
    for e in errors[:15]:
        print(" -", e)


if __name__ == "__main__":
    main()
