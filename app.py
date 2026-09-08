from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import JSONResponse, HTMLResponse
import pandas as pd
from datetime import datetime
import yfinance as yf
import traceback
import requests

app = FastAPI(title="Market Blueprint Desk LIVE", version="2.5 - TradingView + yfinance")

CACHE = {"last_run": None, "leaders": [], "regime": "Unknown", "sectors": {}, "universe_source": "static"}

# Твой базовый курированный список - всегда в основе
BASE_UNIVERSE = [
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

def get_tradingview_universe():
    """Тянем 100 самых горячих акций с TradingView сканера"""
    try:
        url = "https://scanner.tradingview.com/america/scan"
        payload = {
            "filter": [
                {"left": "market_cap_basic", "operation": "greater", "right": 500000000},
                {"left": "close", "operation": "greater", "right": 5},
                {"left": "average_volume_10d_calc", "operation": "greater", "right": 300000},
                {"left": "change_1M", "operation": "greater", "right": 8},
                {"left": "type", "operation": "in_range", "right": ["stock", "dr", "fund"]}
            ],
            "options": {"lang": "en"},
            "symbols": {"query": {"types": []}, "tickers": []},
            "columns": ["name", "close", "change_1M", "market_cap_basic"],
            "sort": {"sortBy": "change_1M", "sortOrder": "desc"},
            "range": {"from": 0, "to": 100}
        }
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json"
        }
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        tickers = []
        for row in data.get("data", []):
            # row = {"d": [name, close, change_1M, mcap], "s": "NASDAQ:COIN"}
            d = row.get("d", [])
            if d:
                name = row.get("s", "").split(":")[-1] if ":" in row.get("s","") else d[0]
                # d[0] иногда уже тикер
                ticker = d[0] if isinstance(d[0], str) else name
                # чистим
                ticker = str(ticker).replace("NASDAQ:","").replace("NYSE:","").replace("AMEX:","").strip()
                if ticker and len(ticker) <= 6 and ticker.isalpha():
                    tickers.append(ticker)
        
        print(f"TradingView found {len(tickers)} hot tickers: {tickers[:10]}")
        return tickers
    except Exception as e:
        print(f"TradingView scanner error: {e}")
        traceback.print_exc()
        return []

def fetch_leaders_job():
    try:
        print("Fetching universe...")
        # 1. Пробуем TradingView
        tv_tickers = get_tradingview_universe()
        
        # 2. Гибрид: база + горячие, без дублей
        combined = BASE_UNIVERSE.copy()
        new_from_tv = 0
        for t in tv_tickers:
            if t not in combined:
                combined.append(t)
                new_from_tv += 1
                if len(combined) >= 150:  # лимит чтобы не убить Render
                    break
        
        UNIVERSE = combined
        CACHE["universe_source"] = f"Base {len(BASE_UNIVERSE)} + TV {new_from_tv} = {len(UNIVERSE)}"
        print(f"Universe: {CACHE['universe_source']}")
        
        leaders = []
        sectors = {}
        
        # Загружаем пачкой по 15 тикеров
        for i in range(0, len(UNIVERSE), 15):
            batch = UNIVERSE[i:i+15]
            try:
                data = yf.download(batch, period="3mo", group_by='ticker', progress=False, threads=True, auto_adjust=True)
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
                    if "Close" not in hist.columns:
                        continue
                    close = hist['Close']
                    if len(close) < 22:
                        continue
                    price_now = float(close.iloc[-1])
                    price_1m = float(close.iloc[-22])
                    price_1w = float(close.iloc[-5]) if len(close) >= 5 else price_now
                    
                    perf_month = ((price_now / price_1m) - 1) * 100
                    perf_week = ((price_now / price_1w) - 1) * 100
                    
                    # RSI 14
                    delta = close.diff()
                    gain = delta.where(delta > 0, 0).rolling(14).mean()
                    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
                    rs = gain.iloc[-1] / (loss.iloc[-1] + 1e-9)
                    rsi = 100 - (100 / (1 + rs)) if loss.iloc[-1] != 0 else 70
                    
                    sma20 = close.rolling(20).mean().iloc[-1]
                    sma50 = close.rolling(50).mean().iloc[-1]
                    trend = 90 if price_now > sma20 and sma20 > sma50 else 70 if price_now > sma20 else 40
                    rs_score = min(95, max(20, 50 + perf_month * 2))
                    mom = 85 if 55 <= rsi <= 75 else 65 if rsi > 50 else 40
                    tti = round((trend + rs_score + mom)/3, 1)
                    
                    if tti < 60:
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
        print(f"Top: {[l['ticker'] for l in leaders_sorted[:10]]}")
        
    except Exception as e:
        print(f"Fetch error: {e}")
        traceback.print_exc()

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
def home():
    leaders_html = ""
    for l in CACHE['leaders']:
        # Подсветка новых из TV - если не в базе
        is_new = "🔥" if l['ticker'] not in BASE_UNIVERSE else ""
        leaders_html += f"<tr><td>{is_new} {l['ticker']}</td><td>{l['tti_score']}</td><td>{l['perf_month']}</td><td>{l['rsi']}</td><td>{l['sector']}</td></tr>"
    
    return f"""
    <html><head><title>Market Blueprint LIVE</title>
    <style>body{{background:#0a0a0b;color:#e4e4e7;font-family:monospace;padding:20px}}
    .card{{background:#151519;border:1px solid #27272a;padding:16px;border-radius:12px;margin:12px 0}}
    .green{{color:#00ff88}} .muted{{color:#888;font-size:12px}} table{{width:100%;border-collapse:collapse}} td,th{{padding:6px;border-bottom:1px solid #27272a}} th{{color:#888}}</style></head>
    <body>
    <h1>Market Blueprint Desk <span class="green">• LIVE v2.5 TV</span></h1>
    <p>Last run: {CACHE['last_run']} | Regime: {CACHE['regime']} | Leaders: {len(CACHE['leaders'])}<br>
    <span class="muted">{CACHE.get('universe_source','')} | 🔥 = New from TradingView</span></p>
    <div class="card"><a href="/api/leaders" style="color:#00ff88">/api/leaders - JSON Hit List</a> | <a href="/api/fetch" style="color:#00ff88">/api/fetch - Trigger refresh</a> | <a href="/health" style="color:#00ff88">/health</a></div>
    <div class="card"><h3>Sectors (capital flow)</h3><pre>{CACHE['sectors']}</pre></div>
    <div class="card"><h3>All Leaders ({len(CACHE['leaders'])} found) - Sorted by TTI Score</h3><table><tr><th>Ticker</th><th>TTI</th><th>1M</th><th>RSI</th><th>Sector</th></tr>{leaders_html}</table></div>
    </body></html>
    """

@app.get("/api/leaders")
def get_leaders():
    return JSONResponse({"last_run": CACHE['last_run'], "regime": CACHE['regime'], "sectors": CACHE['sectors'], "leaders": CACHE['leaders'], "universe": CACHE.get('universe_source')})

@app.get("/api/fetch")
def trigger_fetch(background_tasks: BackgroundTasks):
    background_tasks.add_task(fetch_leaders_job)
    return {"status": "fetching started", "check": "/api/leaders in 60-90s"}

@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return {"status": "ok", "last_run": CACHE['last_run'], "count": len(CACHE['leaders']), "source": CACHE.get('universe_source')}

@app.on_event("startup")
def startup():
    fetch_leaders_job()
