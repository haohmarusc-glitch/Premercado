import sys, json
import yfinance as yf

def get_earnings(tickers):
    result = []
    for t in tickers:
        try:
            tk = yf.Ticker(t)
            info = tk.info or {}
            cal = tk.calendar
            earnings_date = None
            if cal is not None:
                if hasattr(cal, 'empty') and not cal.empty:
                    dates = cal.columns.tolist()
                    if dates:
                        d = dates[0]
                        earnings_date = d.strftime("%Y-%m-%d") if hasattr(d, 'strftime') else str(d)[:10]
                elif isinstance(cal, dict) and cal.get('Earnings Date'):
                    earnings_date = str(cal['Earnings Date'][0])[:10]
            result.append({
                "ticker": t,
                "name": info.get("shortName", t),
                "earningsDate": earnings_date,
                "epsEstimate": info.get("epsForward"),
                "sector": info.get("sector"),
            })
        except Exception as e:
            result.append({"ticker": t, "name": t, "earningsDate": None, "epsEstimate": None})
    print(json.dumps(result))

if __name__ == "__main__":
    tickers = sys.argv[1].split(",") if len(sys.argv) > 1 else []
    get_earnings(tickers)
