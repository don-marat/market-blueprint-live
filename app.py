from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import JSONResponse, HTMLResponse
import pandas as pd
from datetime import datetime
import yfinance as yf
import traceback

app = FastAPI(title="Market Blueprint Desk LIVE", version="2.2 - yfinance")

CACHE = {"last_run": None, "leaders": [], "regime": "Unknown", "sectors": {}}

# Курированный список лидеров - то что ТТИ обычно сканирует
UNIVERSE = [
    "NVDA","META","MSFT","AVGO","NFLX","TSLA","AMD","PLTR","ARM","SMCI",
    "ANET","CRWD","PANW","NOW","APP","SPOT","COIN","HOOD","MSTR","DKNG",
    "LLY","VRT","GEV","NRG","CEG","VST","STX","WDC","MU","CLS","JBL","PWR",
    "HWM","EME","FIX","LEU","OKLO","SMR","VRTX","REGN","AXON","TDG","KTOS",
    "IESC","GWRE","DOCS","ALAB","MRVL","AVTR","NTRA","TEM","UPST","AFRM"
]

SECTOR_MAP = {
    "NVDA":"Technology","META":"Technology","MSFT":"Technology","AVGO":"Technology",
    "NFLX":"Communication","TSLA":"Consumer","AMD":"Technology","PLTR":"Technology",
    "LLY":"Healthcare","GEV":"Utilities","VST":"Utilities","MU":"Technology"
}

def fetch_leaders_job():
    try:
        print("Fetching via yfinance fallback...")
        leaders = []
        sectors = {}
        
        # Загружаем пачкой по 10 тикеров чтобы не упереться в лимиты
        for i in range(0, len(UNIVERSE), 10):
            batch = UNIVERSE[i:i+10]
            try:
                data = yf.download(batch, period="3mo", group_by='ticker', progress=False, threads=True)
            except Exception as e:
                print(f"Batch error {batch}: {e}")
                continue
            
            for ticker in batch:
                try:
                    if len(batch) == 1:
                        hist = data
                    else:
                        hist = data[ticker] if ticker in data else None
                    
                    if hist is None or hist.empty or len(hist) < 22:
                        continue
                    
                    hist = hist.dropna()
                    close = hist['Close']
                    price_now = float(close.iloc[-1])
                    price_1m = float(close.iloc[-22]) if len(close) >= 22 else float(close.iloc[0])
                    price_1w = float(close.iloc[-5]) if len(close) >= 5 else price_now
                    
                    perf_month = ((price_now / price_1m) - 1) * 100
                    perf_week = ((price_now / price_1w) - 1) * 100
                    
                    # RSI 14
                    delta = close.diff()
                    gain = delta.where(delta > 0, 0).rolling(14).mean()
                    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
                    rs = gain.iloc[-1] / (loss.iloc[-1] + 1e-9)
                    rsi = 100 - (100 / (1 + rs)) if loss.iloc[-1] != 0 else 70
                    
                    # TTI Score аппроксимация
                    # Trend: выше SMA20 и SMA50 ?
                    sma20 = close.rolling(20).mean().iloc[-1]
                    sma50 = close.rolling(50).mean().iloc[-1]
                    trend = 90 if price_now > sma20 and sma20 > sma50 else 70 if price_now > sma20 else 40
                    rs_score = min(95, max(20, 50 + perf_month * 2))
                    mom = 85 if 55 <= rsi <= 75 else 65 if rsi > 50 else 40
                    tti = round((trend + rs_score + mom)/3, 1)
                    
                    if tti < 60:  # фильтр только сильных
                        continue
                    
                    sector = SECTOR_MAP.get(ticker, "Technology")
                    sectors[sector] = sectors.get(sector, 0) + 1
                    
                    leaders.append({
                        "ticker": ticker,
                        "company": ticker,
                        "perf_month": f"{perf_month:.1f}%",
                        "perf_week": f"{perf_week:.1f}%",
                        "perf_month_raw": perf_month,
                        "rsi": round(float(rsi),1),
                        "tti_score": tti,
                        "trend": trend,
                        "rs": round(rs_score,1),
                        "momentum": mom,
                        "price": round(price_now,2),
                        "sector": sector
                    })
                except Exception as inner_e:
                    print(f"Ticker {ticker} error: {inner_e}")
                    continue
        
        leaders_sorted = sorted(leaders, key=lambda x: x['tti_score'], reverse=True)
        
        CACHE['leaders'] = leaders_sorted
        CACHE['last_run'] = datetime.utcnow().isoformat()
        CACHE['sectors'] = dict(sorted(sectors.items(), key=lambda x: x[1], reverse=True))
        
        if len(leaders_sorted) >= 25:
            CACHE['regime'] = "Bull - Risk On"
        elif len(leaders_sorted) >= 15:
            CACHE['regime'] = "Bull - Selective"
        else:
            CACHE['regime'] = "Neutral - Risk Off"
        
        print(f"Done. Found {len(leaders_sorted)} leaders. Regime: {CACHE['regime']}")
        print(f"Top: {[l['ticker'] for l in leaders_sorted[:5]]}")
        
    except Exception as e:
        print(f"Fetch error: {e}")
        traceback.print_exc()

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
def home():
    leaders_html = ""
    for l in CACHE['leaders'][:15]:
        leaders_html += f"<tr><td>{l['ticker']}</td><td>{l['tti_score']}</td><td>{l['perf_month']}</td><td>{l['rsi']}</td><td>{l['sector']}</td></tr>"
    
    return f"""
    <html><head><title>Market Blueprint LIVE</title>
    <style>body{{background:#0a0a0b;color:#e4e4e7;font-family:monospace;padding:20px}}
    .card{{background:#151519;border:1px solid #27272a;padding:16px;border-radius:12px;margin:12px 0}}
    .green{{color:#00ff88}} table{{width:100%;border-collapse:collapse}} td,th{{padding:6px;border-bottom:1px solid #27272a}} th{{color:#888}}</style></head>
    <body>
    <h1>Market Blueprint Desk <span class="green">• LIVE v2.2</span></h1>
    <p>Last run: {CACHE['last_run']} | Regime: {CACHE['regime']} | Leaders: {len(CACHE['leaders'])}</p>
    <div class="card"><a href="/api/leaders" style="color:#00ff88">/api/leaders - JSON Hit List</a> | <a href="/api/fetch" style="color:#00ff88">/api/fetch - Trigger refresh</a> | <a href="/health" style="color:#00ff88">/health</a></div>
    <div class="card"><h3>Sectors (capital flow)</h3><pre>{CACHE['sectors']}</pre></div>
    <div class="card"><h3>Top 15 Leaders (TTI Score)</h3><table><tr><th>Ticker</th><th>TTI</th><th>1M</th><th>RSI</th><th>Sector</th></tr>{leaders_html}</table></div>
    </body></html>
    """

@app.get("/api/leaders")
def get_leaders():
    return JSONResponse({"last_run": CACHE['last_run'], "regime": CACHE['regime'], "sectors": CACHE['sectors'], "leaders": CACHE['leaders']})

@app.get("/api/fetch")
def trigger_fetch(background_tasks: BackgroundTasks):
    background_tasks.add_task(fetch_leaders_job)
    return {"status": "fetching started", "check": "/api/leaders in 40s"}

@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return {"status": "ok", "last_run": CACHE['last_run'], "count": len(CACHE['leaders'])}

@app.on_event("startup")
def startup():
    fetch_leaders_job()
