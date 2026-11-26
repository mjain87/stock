# Market Signal – F&O · Intraday · Long-term Shares

Static web app (GitHub Pages) + GitHub Actions jo data files update karte hain.

## Files (repo ke root me, sirf `.github/workflows/` folder me jaate hain)
| File | Kaam |
|---|---|
| `index.html` | App: F&O positional, Intraday mode, Long-term Shares tab |
| `fetch_data.py` | Index data (daily + 15-min candles), global markets, FII/DII, option chain, news -> `data.json` |
| `fetch_stocks.py` | ~50 large-cap shares ka 5-saal price history + fundamentals -> `stocks.json` |
| `.github/workflows/update-data.yml` | `data.json` har 20 min (market hours) |
| `.github/workflows/update-stocks.yml` | `stocks.json` roz shaam ~5 PM IST |

## Setup
1. Upar ki saari files repo me daalo (nayi/badli: `index.html`, `fetch_data.py`, `fetch_stocks.py`, `update-stocks.yml`).
2. Settings -> Actions -> General -> Workflow permissions -> **Read and write**.
3. Actions tab -> **Update market data** -> Run workflow (intraday candles ke liye dobara chalana zaroori hai).
4. Actions tab -> **Update stock data** -> Run workflow (pehli baar `stocks.json` banane ke liye, ~2-3 minute lagte hain).
5. Settings -> Pages -> Branch `main` / root.

## Modes
- **Positional**: expiry tak ka signal + strike table (ITM/ATM/OTM, estimated premium, SL/target).
- **Intraday**: aaj ke liye CE trigger (opening range High ke upar) aur PE trigger (Low ke neeche), SL, Target 1/2, strike aur estimated premium. Market band ho to plan agle session ke liye last session ke levels par banta hai.
- **Long-term Shares**: har share ka 0-100 score (trend, valuation, quality, risk, analyst view) + verdict (Accumulate / Hold-Watch / Avoid-Wait), kaaran, kharidne ka tarika. Mehenge, overheated ya toote hue trend wale shares ko Accumulate nahi diya jaata.

## Zaroori baatein
- Data free sources (Yahoo, NSE, Google News) se aata hai. NSE kabhi GitHub ke servers ko block karta hai (FII/DII, PCR): tab app baaki parameters se chalti hai, aur FII/DII manual box me daal sakte ho.
- Long-term fundamentals Yahoo ke `quoteSummary` se aate hain (cookie + crumb chahiye). Block hone par shares "data kam" dikhte hain aur Accumulate nahi milta.
- Intraday data GitHub Action ke chalne par depend karta hai (har ~20 min, kabhi late). Page par "data N min purana" dikhta hai; trade se pehle live chart dekhein.
- Share ke tickers `fetch_stocks.py` ki `UNIVERSE` list me hain; corporate action ke baad badal sakte hain. Fail hone par `stocks.json` ke `errors` me dikhta hai.
- Expiry dates rule se bante hain; exchange holidays ke liye `index.html` ki `HOLIDAYS` list update karte rahein.
- Scores/thresholds judgment par bane hain, backtest nahi hue. Yeh educational tool hai, SEBI-registered advice nahi.
