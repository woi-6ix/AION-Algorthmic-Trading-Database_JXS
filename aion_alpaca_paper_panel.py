"""Read-only Alpaca Paper options chart and worker status for the private AION UI.

No POST/DELETE routes, no order placement, credentials never exposed to client.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from aion_alpaca_paper_algo import (
    AlpacaPaper, OCC, armed, dt, now_utc, get_cfg, orders_db,
    quote_ok, eligible_contract, amount, NY,
)

app = FastAPI(title='AION Alpaca PAPER read-only chart', docs_url=None, redoc_url=None, openapi_url=None)


def safe_api(f):
    try:
        return f()
    except Exception as e:
        raise HTTPException(503, 'Alpaca Paper data unavailable: '+type(e).__name__) from None


@app.get('/api/status')
def status():
    a = safe_api(lambda: AlpacaPaper().account())
    return {
        'broker': 'ALPACA PAPER', 'paper_only': True,
        'account_status':a.get('status'), 'options_trading_level':a.get('options_trading_level'),
        'paper_cash':a.get('cash'), 'armed':armed(), 'worker_heartbeat':get_cfg('last_heartbeat'),
        'orders':[{k:r[k] for k in ('action','option_symbol','state','filled_qty','fill_price','submitted_at')}
                  for r in orders_db()[:20]],
    }


@app.get('/api/chain')
def chain():
    def load():
        api=AlpacaPaper()
        today=now_utc().astimezone(NY).date().isoformat()
        # Outside session the latest snapshots can be stale; label with timestamps.
        spot=api.stock_spy() if time(9,30) <= now_utc().astimezone(NY).time()<time(16,1) else None
        if spot is None:
            # Show a clear unavailable state instead of fabricating 'live' quotes.
            return {'date':today,'quote_feed':'indicative','spot':None,'contracts':[],
                    'notice':'SPY IEX spot is only loaded here during regular trading hours.'}
        contracts=[]
        for right in ('call','put'):
            sub=[c for c in api.contracts(today,right) if eligible_contract(c,today,right)]
            sub.sort(key=lambda x:abs(amount(x['strike_price'])-spot))
            contracts.extend(sub[:12])
        snaps=api.snapshot([c['symbol'] for c in contracts]) if contracts else {}
        rows=[]
        for c in contracts:
            snap=snaps.get(c['symbol']) or {}
            q=snap.get('latestQuote') or {}
            rows.append({
                'symbol':c['symbol'],'side':c['type'],'strike':c['strike_price'],
                'bid':q.get('bp'),'ask':q.get('ap'),'time':q.get('t'),
                'delta':(snap.get('greeks') or {}).get('delta'),
                'iv':snap.get('impliedVolatility'),
            })
        return {'date':today,'quote_feed':'indicative','spot':str(spot),'contracts':rows,
                'notice':'Indicative option quotes may be modified; not OPRA executable quotes. Bars may be delayed.'}
    return safe_api(load)


@app.get('/api/bars')
def bars(symbol: str=Query(pattern=r'^SPY\d{6}[CP]\d{8}$')):
    if not OCC.fullmatch(symbol):
        raise HTTPException(422,'Invalid SPY OCC symbol')
    try:
        exp=datetime.strptime(symbol[3:9],'%y%m%d').date()
    except ValueError:
        raise HTTPException(422,'Invalid expiration') from None
    today=now_utc().astimezone(NY).date()
    if exp>today or (today-exp).days>30:
        raise HTTPException(422,'Only recent SPY option contracts are supported')
    def load():
        api=AlpacaPaper()
        start=datetime.combine(exp,time(9,30),NY).astimezone(timezone.utc).isoformat()
        end=datetime.combine(exp,time(16,0),NY).astimezone(timezone.utc).isoformat()
        raw=api.bars(symbol,start,end if exp<today else None)
        return {'symbol':symbol,'feed':'indicative','bars':[
          {k:v for k,v in b.items() if k in ('t','o','h','l','c','v')}
          for b in raw
        ]}
    return safe_api(load)


PAGE = '''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AION · Alpaca PAPER Options</title><style>
:root{color-scheme:dark}body{font:14px system-ui,-apple-system,sans-serif;background:#0b1427;color:#e8eefc;margin:0;padding:16px}
h2{color:#e6c574;margin:3px 0 7px}h3{color:#e6c574}p{line-height:1.5;color:#bcc8d9}
.bar{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin:12px 0}button{background:#172744;color:#f4d583;border:1px solid #64758e;border-radius:9px;padding:9px 12px;cursor:pointer}button:hover{background:#263d63}
.card{background:#101e34;border:1px solid #35425e;border-radius:12px;padding:14px;margin:13px 0;overflow:auto}
table{width:100%;border-collapse:collapse;min-width:570px;font-size:12px}th,td{text-align:left;border-bottom:1px solid #34415b;padding:9px 8px}tr:hover{background:#1b304e}
a{color:#f2d77c}.notice{color:#f5cb86}.good{color:#8cddb6}.err{color:#ff9e9e}
#plot{width:100%;height:260px;display:block;background:#0d192d;border-radius:8px}#plot text{fill:#dbe6f4;font:11px system-ui}
</style></head><body>
<h2>Alpaca PAPER · SPY 0DTE</h2>
<p>Broker-hosted PAPER orders only. Quotes are free indicative market data (not OPRA); option trade bars may lag. Trading is controlled by the local AION worker, not this website.</p>
<div class="bar"><button onclick="refresh()">Refresh chain / status</button><span id="state">Loading…</span></div>
<div class="card"><h3>Alpaca PAPER orders and worker</h3><div id="orders">Loading…</div></div>
<div class="card"><h3>Today's SPY 0DTE calls and puts</h3><p id="chain-note"></p><table><thead><tr><th>Contract</th><th>Side</th><th>Strike</th><th>Bid</th><th>Ask</th><th>Delta</th><th>IV</th><th>Quote time</th><th>Chart</th></tr></thead><tbody id="chain"></tbody></table></div>
<div class="card"><h3 id="chart-title">3-minute option premium chart</h3><svg id="plot" viewBox="0 0 750 260" role="img" aria-label="Option 3-minute close price chart"><text x="20" y="130">Select a contract above to load its historical option bars.</text></svg><p id="chart-note"></p></div>
<script>
const el=id=>document.getElementById(id);
const text=(v)=>v==null?'—':String(v);
const fmt=v=>v==null?'—':Number(v).toFixed(2);
async function api(path){const r=await fetch(path,{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);return await r.json()}
async function refresh(){
 el('state').textContent='Refreshing…';
 try{const s=await api('api/status');
  el('state').textContent='Account '+s.account_status+' · worker '+(s.armed?'ARMED':'DISARMED')+' · cash $'+fmt(s.paper_cash);
  const table=document.createElement('table');const head=document.createElement('tr');
  ['Time','Action','Contract','State','Filled','Price'].forEach(x=>{const th=document.createElement('th');th.textContent=x;head.appendChild(th)});table.appendChild(head);
  for(const o of s.orders){const tr=document.createElement('tr');
   [o.submitted_at,o.action,o.option_symbol,o.state,o.filled_qty,o.fill_price].forEach(x=>{const td=document.createElement('td');td.textContent=text(x);tr.appendChild(td)});table.appendChild(tr)}
  el('orders').replaceChildren(table);
 }catch(e){el('state').textContent='Account unavailable: '+e.message}
 try{const d=await api('api/chain');el('chain-note').textContent=(d.spot?'SPY IEX: $'+d.spot+' · ':'')+d.notice;
  el('chain').replaceChildren();
  for(const c of d.contracts){const tr=document.createElement('tr');
   [c.symbol,c.side,c.strike,fmt(c.bid),fmt(c.ask),fmt(c.delta),fmt(c.iv),text(c.time)].forEach(v=>{let td=document.createElement('td');td.textContent=v;tr.appendChild(td)});
   const td=document.createElement('td');const button=document.createElement('button');button.textContent='View';button.addEventListener('click',()=>chart(c.symbol));td.appendChild(button);tr.appendChild(td);el('chain').appendChild(tr)}
 }catch(e){el('chain-note').textContent='Chain unavailable: '+e.message}
}
async function chart(symbol){el('chart-title').textContent='3-minute option bars · '+symbol;el('chart-note').textContent='Loading Alpaca historical options bars…';
 try{const data=await api('api/bars?symbol='+encodeURIComponent(symbol));const points=data.bars.filter(b=>Number.isFinite(Number(b.c))&&Number(b.c)>0);draw(points);el('chart-note').textContent=points.length+' bars · Indicative historical options feed; bars can lag by 15 minutes.'}
 catch(e){el('chart-note').textContent='Chart unavailable: '+e.message}}
function draw(arr){const s=el('plot');while(s.firstChild)s.removeChild(s.firstChild);const NS='http://www.w3.org/2000/svg';
 function add(tag,attrs){const x=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))x.setAttribute(k,v);s.appendChild(x);return x}
 if(!arr.length){add('text',{x:20,y:125}).textContent='No 3-minute bars returned for this contract/entitlement.';return}
 const vals=arr.map(b=>Number(b.c));let lo=Math.min(...vals),hi=Math.max(...vals);if(hi===lo)hi=lo+.1;
 const w=710,h=208,x0=24,y0=16;const path=vals.map((v,i)=>(i?'L':'M')+(x0+i*w/Math.max(1,vals.length-1)).toFixed(2)+','+(y0+h-(v-lo)/(hi-lo)*h).toFixed(2)).join(' ');
 add('path',{d:path,stroke:'#e6c574','stroke-width':'2',fill:'none'});
 add('text',{x:20,y:251}).textContent='First: '+text(arr[0].t)+' · Last: '+text(arr[arr.length-1].t);
 add('text',{x:20,y:14}).textContent='$'+hi.toFixed(2);
 add('text',{x:20,y:236}).textContent='$'+lo.toFixed(2)
}
refresh();
</script></body></html>'''


@app.get('/',response_class=HTMLResponse)
def homepage():return PAGE
