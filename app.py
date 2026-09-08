from fastapi import FastAPI, BackgroundTasks, Query
from fastapi.responses import JSONResponse, HTMLResponse
import pandas as pd
from datetime import datetime
import yfinance as yf
import traceback
import requests

app = FastAPI(title="Market Blueprint Desk LIVE", version="2.6 - Filters + TV")

CACHE = {"last_run": None, "leaders": [], "regime": "Unknown", "sectors": {}, "universe_source": "static"}

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
        r = requests.post(url, json=payload, headers={"User-Agent":"Mozilla/5.0","Content-Type":"application/json"}, timeout=15)
        r.raise_for_status()
        tickers = []
        for row in r.json().get("data", []):
            d = row.get("d", [])
            if d and isinstance(d[0], str):
                ticker = d[0].split(":")[-1].strip()
                if ticker and len(ticker)<=6 and ticker.replace(".","").isalpha():
                    tickers.append(ticker)
        print(f"TradingView found {len(tickers)}")
        return tickers
    except Exception as e:
        print(f"TV error: {e}")
        return []

def fetch_leaders_job():
    try:
        print("Fetching universe...")
        tv_tickers = get_tradingview_universe()
        combined = BASE_UNIVERSE.copy()
        new_cnt=0
        for t in tv_tickers:
            if t not in combined:
                combined.append(t)
                new_cnt+=1
                if len(combined)>=150: break
        UNIVERSE = combined
        CACHE["universe_source"] = f"Base {len(BASE_UNIVERSE)} + TV {new_cnt} = {len(UNIVERSE)}"
        print(f"Universe: {CACHE['universe_source']}")
        
        leaders = []
        sectors = {}
        for i in range(0, len(UNIVERSE), 15):
            batch = UNIVERSE[i:i+15]
            try:
                data = yf.download(batch, period="3mo", group_by='ticker', progress=False, threads=True, auto_adjust=True)
            except: continue
            for ticker in batch:
                try:
                    hist = data[ticker] if len(batch)>1 and ticker in data else data if len(batch)==1 else None
                    if hist is None or hist.empty or len(hist)<22 or "Close" not in hist: continue
                    hist=hist.dropna()
                    close=hist['Close']
                    price_now=float(close.iloc[-1])
                    price_1m=float(close.iloc[-22])
                    price_1w=float(close.iloc[-5]) if len(close)>=5 else price_now
                    perf_month=((price_now/price_1m)-1)*100
                    perf_week=((price_now/price_1w)-1)*100
                    delta=close.diff()
                    gain=delta.where(delta>0,0).rolling(14).mean()
                    loss=(-delta.where(delta<0,0)).rolling(14).mean()
                    rs=gain.iloc[-1]/(loss.iloc[-1]+1e-9)
                    rsi=100-(100/(1+rs)) if loss.iloc[-1]!=0 else 70
                    sma20=close.rolling(20).mean().iloc[-1]
                    sma50=close.rolling(50).mean().iloc[-1]
                    trend=90 if price_now>sma20 and sma20>sma50 else 70 if price_now>sma20 else 40
                    rs_score=min(95,max(20,50+perf_month*2))
                    mom=85 if 55<=rsi<=75 else 65 if rsi>50 else 40
                    tti=round((trend+rs_score+mom)/3,1)
                    if tti<50: continue # понизили порог чтобы было больше выбора для фильтров
                    sector=SECTOR_MAP.get(ticker,"Technology")
                    sectors[sector]=sectors.get(sector,0)+1
                    leaders.append({
                        "ticker": ticker,
                        "company": ticker,
                        "perf_month": f"{perf_month:.1f}%",
                        "perf_month_raw": round(perf_month,1),
                        "perf_week": f"{perf_week:.1f}%",
                        "perf_week_raw": round(perf_week,1),
                        "rsi": round(float(rsi),1),
                        "tti_score": tti,
                        "trend": trend,
                        "rs": round(rs_score,1),
                        "momentum": mom,
                        "price": round(price_now,2),
                        "sector": sector,
                        "is_new": ticker not in BASE_UNIVERSE
                    })
                except: continue
        leaders_sorted=sorted(leaders, key=lambda x: x['tti_score'], reverse=True)
        CACHE['leaders']=leaders_sorted
        CACHE['last_run']=datetime.utcnow().isoformat()
        CACHE['sectors']=dict(sorted(sectors.items(), key=lambda x: x[1], reverse=True))
        CACHE['regime']="Bull - Risk On" if len(leaders_sorted)>=25 else "Bull - Selective" if len(leaders_sorted)>=15 else "Neutral - Risk Off"
        print(f"Done. Found {len(leaders_sorted)} leaders. {CACHE['regime']}")
    except Exception as e:
        print(f"Fetch error: {e}")
        traceback.print_exc()

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
def home():
    # HTML с фильтрами
    return f"""
<html><head><title>Market Blueprint LIVE v2.6</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{{background:#0a0a0b;color:#e4e4e7;font-family:monospace;padding:16px;margin:0}}
.card{{background:#151519;border:1px solid #27272a;padding:14px;border-radius:12px;margin:12px 0}}
.green{{color:#00ff88}} .muted{{color:#888;font-size:12px}}
table{{width:100%;border-collapse:collapse}} td,th{{padding:8px;border-bottom:1px solid #27272a;text-align:left}} th{{color:#888;cursor:pointer;user-select:none}} th:hover{{color:#00ff88}}
.filter-row{{display:flex;flex-wrap:wrap;gap:12px;align-items:end;margin:10px 0}}
.filter-row label{{display:flex;flex-direction:column;font-size:12px;color:#888;gap:4px}}
.filter-row input, .filter-row select{{background:#0a0a0b;border:1px solid #27272a;color:#e4e4e7;padding:8px 10px;border-radius:8px;min-width:120px}}
.filter-row input:focus, .filter-row select:focus{{border-color:#00ff88;outline:none}}
.badge{{background:#00ff88;color:#000;padding:2px 6px;border-radius:10px;font-size:10px;margin-left:4px}}
.btn{{background:#00ff88;color:#000;border:0;padding:8px 14px;border-radius:8px;cursor:pointer;font-weight:bold}}
.btn:hover{{opacity:0.9}}
</style></head>
<body>
<h1>Market Blueprint Desk <span class="green">• LIVE v2.6 Filters + TV</span></h1>
<p>Last run: {CACHE['last_run']} | Regime: <span class="green">{CACHE['regime']}</span> | Leaders: {len(CACHE['leaders'])}<br>
<span class="muted">{CACHE.get('universe_source','')} | 🔥 = New from TradingView</span></p>

<div class="card"><a href="/api/leaders" style="color:#00ff88">/api/leaders JSON</a> | <a href="/api/fetch" style="color:#00ff88">/api/fetch Refresh</a> | <a href="/health" style="color:#00ff88">/health</a></div>

<div class="card">
<h3 style="margin:0 0 10px 0">Filters выбора (фильтры работают на лету)</h3>
<div class="filter-row">
<label>Min TTI Score<input type="range" id="f_tti" min="0" max="100" value="60" oninput="applyFilters()"><span id="f_tti_v">60</span></label>
<label>Min 1M %<input type="number" id="f_1m" value="0" step="1" oninput="applyFilters()" style="width:90px"></label>
<label>RSI от<input type="number" id="f_rsi_min" value="0" min="0" max="100" oninput="applyFilters()" style="width:80px"></label>
<label>RSI до<input type="number" id="f_rsi_max" value="100" min="0" max="100" oninput="applyFilters()" style="width:80px"></label>
<label>Sector<select id="f_sector" onchange="applyFilters()"><option value="">All</option><option>Technology</option><option>Communication</option><option>Consumer</option><option>Utilities</option><option>Healthcare</option></select></label>
<label>Search ticker<input type="text" id="f_search" placeholder="COIN, NVDA..." oninput="applyFilters()" style="width:140px"></label>
<label><span style="opacity:0">.</span><button class="btn" onclick="resetFilters()">Reset</button></label>
</div>
<div class="muted">Показано: <span id="count">0</span> / {len(CACHE['leaders'])} | Сортировка кликом по заголовку</div>
</div>

<div class="card"><h3>Sectors (capital flow)</h3><pre>{CACHE['sectors']}</pre></div>

<div class="card">
<h3>Leaders - отфильтровано <span id="title_count">{len(CACHE['leaders'])}</span></h3>
<table id="leadersTable"><thead><tr>
<th onclick="sortBy('ticker')">Ticker ↕</th>
<th onclick="sortBy('tti_score')">TTI ↕</th>
<th onclick="sortBy('perf_month_raw')">1M ↕</th>
<th onclick="sortBy('perf_week_raw')">1W ↕</th>
<th onclick="sortBy('rsi')">RSI ↕</th>
<th onclick="sortBy('price')">Price ↕</th>
<th>Sector</th>
</tr></thead><tbody id="tbody"></tbody></table>
</div>

<script>
const DATA = {CACHE['leaders']};
let sortKey='tti_score'; let sortAsc=false;

function applyFilters(){{
  document.getElementById('f_tti_v').innerText=document.getElementById('f_tti').value;
  const minTTI=parseFloat(document.getElementById('f_tti').value)||0;
  const min1M=parseFloat(document.getElementById('f_1m').value)||-999;
  const rsiMin=parseFloat(document.getElementById('f_rsi_min').value)||0;
  const rsiMax=parseFloat(document.getElementById('f_rsi_max').value)||100;
  const sector=document.getElementById('f_sector').value;
  const search=document.getElementById('f_search').value.toUpperCase().trim();

  let filtered=DATA.filter(l=> l.tti_score>=minTTI && l.perf_month_raw>=min1M && l.rsi>=rsiMin && l.rsi<=rsiMax && (sector===''||l.sector===sector) && (search===''||l.ticker.includes(search)) );

  filtered.sort((a,b)=>{{
    let av=a[sortKey], bv=b[sortKey];
    if(typeof av==='string'){{av=av.toUpperCase(); bv=bv.toUpperCase();}}
    if(sortAsc) return av>bv?1:-1; else return av<bv?1:-1;
  }});

  const tbody=document.getElementById('tbody');
  tbody.innerHTML='';
  filtered.forEach(l=>{{
    const isNew = l.is_new ? '🔥 ' : '';
    tbody.innerHTML+=`<tr><td>${{isNew}}${{l.ticker}}</td><td>${{l.tti_score}}</td><td>${{l.perf_month}}</td><td>${{l.perf_week}}</td><td>${{l.rsi}}</td><td>$${{l.price}}</td><td>${{l.sector}}</td></tr>`;
  }});
  document.getElementById('count').innerText=filtered.length;
  document.getElementById('title_count').innerText=filtered.length;
}}
function sortBy(key){{
  if(sortKey===key) sortAsc=!sortAsc; else {{sortKey=key; sortAsc=false;}}
  applyFilters();
}}
function resetFilters(){{
  document.getElementById('f_tti').value=60;
  document.getElementById('f_1m').value=0;
  document.getElementById('f_rsi_min').value=0;
  document.getElementById('f_rsi_max').value=100;
  document.getElementById('f_sector').value='';
  document.getElementById('f_search').value='';
  sortKey='tti_score'; sortAsc=false;
  applyFilters();
}}
applyFilters();
</script>
</body></html>
    """

@app.get("/api/leaders")
def get_leaders(
    min_tti: float = Query(0),
    min_1m: float = Query(-999),
    max_rsi: float = Query(100),
    min_rsi: float = Query(0),
    sector: str = Query(""),
    search: str = Query("")
):
    leaders = CACHE['leaders']
    filtered = []
    for l in leaders:
        if l['tti_score'] < min_tti: continue
        if l['perf_month_raw'] < min_1m: continue
        if l['rsi'] < min_rsi or l['rsi'] > max_rsi: continue
        if sector and l['sector'] != sector: continue
        if search and search.upper() not in l['ticker']: continue
        filtered.append(l)
    return JSONResponse({"last_run": CACHE['last_run'], "regime": CACHE['regime'], "sectors": CACHE['sectors'], "universe": CACHE.get('universe_source'), "count": len(filtered), "leaders": filtered})

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
