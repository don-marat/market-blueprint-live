from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import JSONResponse, HTMLResponse
from finviz.screener import Screener
import finviz
import pandas as pd
from datetime import datetime
import os

app = FastAPI(title="Market Blueprint Desk LIVE", version="2.1")

CACHE = {"last_run": None, "leaders": [], "regime": "Unknown", "sectors": {}}

def fetch_leaders_job():
    try:
        print("Fetching live data from Finviz...")
        filters = [
            'sh_price_o5',
            'sh_avgvol_o500',
            'ta_sma20_pa',
            'ta_sma50_pa',
            'ta_highlow20d_nh',
            'sh_instown_o30'
        ]
        screener = Screener(filters=filters, table='Performance', order='-perf1m')
        df_perf = screener.to_dataframe()
        
        screener_tech = Screener(filters=filters, table='Technical', order='-perf1m')
        df_tech = screener_tech.to_dataframe()
        
        if df_perf.empty:
            return
        
        merged = pd.merge(df_perf.head(50), df_tech[['Ticker','RSI']], on='Ticker', how='left')
        
        leaders = []
        for _, row in merged.iterrows():
            try:
                perf_str = str(row.get('Perf Month','0%')).replace('%','')
                perf = float(perf_str)
                rsi = float(row.get('RSI', 50))
                trend = 85
                rs = min(95, max(20, 50 + perf*2))
                mom = 80 if 55 <= rsi <= 75 else 60
                tti = round((trend + rs + mom)/3, 1)
                
                leaders.append({
                    "ticker": row['Ticker'],
                    "company": row.get('Company',''),
                    "perf_month": row.get('Perf Month',''),
                    "perf_week": row.get('Perf Week',''),
                    "rsi": rsi,
                    "tti_score": tti,
                    "trend": trend,
                    "rs": round(rs,1),
                    "momentum": mom,
                    "price": row.get('Price',''),
                    "sector": row.get('Sector','') if 'Sector' in row else 'Unknown'
                })
            except:
                continue
        
        sectors = {}
        for l in leaders[:30]:
            sec = l['sector']
            sectors[sec] = sectors.get(sec, 0) + 1
        
        CACHE['leaders'] = sorted(leaders, key=lambda x: x['tti_score'], reverse=True)
        CACHE['last_run'] = datetime.utcnow().isoformat()
        CACHE['sectors'] = sectors
        CACHE['regime'] = "Bull - Risk On" if len(leaders) > 20 else "Neutral"
        print(f"Done. Found {len(leaders)} leaders")
    except Exception as e:
        print(f"Fetch error: {e}")
        import traceback; traceback.print_exc()

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
def home():
    return f"""
    <html><head><title>Market Blueprint LIVE</title>
    <style>body{{background:#0a0a0b;color:#e4e4e7;font-family:monospace;padding:20px}}
    .card{{background:#151519;border:1px solid #27272a;padding:16px;border-radius:12px;margin:12px 0}}
    .green{{color:#00ff88}}</style></head>
    <body>
    <h1>Market Blueprint Desk <span class="green">• LIVE</span></h1>
    <p>Last run: {CACHE['last_run']} | Regime: {CACHE['regime']}</p>
    <div class="card"><a href="/api/leaders" style="color:#00ff88">/api/leaders - JSON Hit List</a></div>
    <div class="card"><a href="/api/fetch" style="color:#00ff88">/api/fetch - Trigger manual refresh</a></div>
    <div class="card"><h3>Sectors (capital flow)</h3><pre>{CACHE['sectors']}</pre></div>
    <div class="card"><h3>Top 10 Leaders</h3><pre>{CACHE['leaders'][:10]}</pre></div>
    </body></html>
    """

@app.get("/api/leaders")
def get_leaders():
    return JSONResponse({"last_run": CACHE['last_run'], "regime": CACHE['regime'], "sectors": CACHE['sectors'], "leaders": CACHE['leaders']})

@app.get("/api/fetch")
def trigger_fetch(background_tasks: BackgroundTasks):
    background_tasks.add_task(fetch_leaders_job)
    return {"status": "fetching started", "check": "/api/leaders in 30s"}

@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return {"status": "ok", "last_run": CACHE['last_run']}

@app.on_event("startup")
def startup():
    fetch_leaders_job()
