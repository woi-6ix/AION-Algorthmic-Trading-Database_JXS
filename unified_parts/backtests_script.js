// AION Unified Lab: runs the user's original v0.2 backtest engine inside the same site.
// This is research-only. No SnapTrade or brokerage order endpoints are imported.
const integratedLab=(()=>{
 const prefix='/lab/api/';let initialized=false,runs=[],selectedRun=null;
 const text=(id,s)=>{const n=$(id);if(n)n.textContent=String(s??'')};
 const html=(id,s)=>{const n=$(id);if(n)n.innerHTML=s};
 const str=id=>($(id)?.value||'').trim();
 const bool=id=>!!$(id)?.checked;
 const moneyLab=n=>Number.isFinite(Number(n))?'$'+Number(n).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}):'—';
 const fmt=n=>Number.isFinite(Number(n))?Number(n).toLocaleString('en-US',{maximumFractionDigits:2}):'—';
 const status=(id,msg,fail=false)=>{const n=$(id);if(!n)return;n.textContent=msg;n.classList.toggle('error',fail);n.classList.toggle('ok',!fail)};
 async function api(path,opts={}){
   const resp=await fetch(prefix+path,{cache:'no-store',...opts});let data={};
   try{data=await resp.json()}catch{}
   if(!resp.ok){let d=data?.detail;throw Error(typeof d==='string'?d:`Backtest API error ${resp.status}`)}
   return data;
 }
 function sel(){return {symbol:str('lab-symbol').toUpperCase(),interval:str('lab-interval'),period:str('lab-period'),provider:str('lab-provider'),start:str('lab-start')||null,end:str('lab-end')||null}}
 function requestJSON(body){return {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}}
 function valid(){const s=sel();if(!/^[A-Z][A-Z0-9.^=-]{0,11}$/.test(s.symbol))throw Error('Enter a valid ticker');return s;}
 async function dataStatus(){try{const s=valid();const d=await api(`data/status?symbol=${encodeURIComponent(s.symbol)}&interval=${s.interval}`);text('lab-data-count',`${d.bars} bars cached`);}catch(e){text('lab-data-count','Data unavailable')}}
 async function download(){const b=$('lab-download');b.disabled=true;status('lab-data-status','Loading OHLCV from configured provider…');try{const d=await api('data/download',requestJSON(valid()));status('lab-data-status',`Cached ${d.count} real bars: ${d.first_bar} → ${d.last_bar}\nProvider: ${d.source}\n${d.warning}`,false);await dataStatus()}catch(e){status('lab-data-status',e.message,true)}finally{b.disabled=false}}
 async function upload(kind,file){if(!file)return;const ids={bars:'lab-data-status',signals:'lab-options-status',quotes:'lab-options-status',trades:'lab-run-status'};const id=ids[kind];status(id,`Importing ${kind}…`);const f=new FormData();f.append('file',file);
  let ep='';const s=sel();if(kind==='bars'){ep='data/import';f.append('symbol',s.symbol);f.append('interval',s.interval)}
  if(kind==='signals'){ep='signals/import';f.append('symbol',s.symbol);f.append('algorithm','KAIROS_V5')}
  if(kind==='quotes')ep='options/quotes/import';
  if(kind==='trades'){ep='backtests/import';f.append('symbol',s.symbol);f.append('timeframe',s.interval);f.append('algorithm',str('lab-algorithm'));f.append('label',`${str('lab-algorithm')} · ${s.symbol} · imported`)}
  try{const d=await api(ep,{method:'POST',body:f});let msg='Imported successfully.';
    if(kind==='bars'){msg=`Imported ${d.count} real bars, ${d.first_bar} → ${d.last_bar}`;await dataStatus()}
    else if(kind==='signals'){msg=`Imported ${d.rows} historical Pine signal(s) to signal set #${d.id}. Option premiums still require real quotes.`;await loadSignals();$('lab-signal-source').value='signal_set';$('lab-signal-set').value=String(d.id)}
    else if(kind==='quotes')msg=`Imported ${d.quotes} real bid/ask quotes from ${d.contracts} contract(s).`;
    else if(kind==='trades'){msg=`Imported ${d.metrics?.trades||0} actual reported trade rows. Source: user-provided CSV.`;await loadRuns(d.id)}
    status(id,msg,false);
  }catch(e){status(id,e.message,true)}finally{const x=$('lab-'+kind+'-file');if(x)x.value=''}
 }
 async function runStock(){const b=$('lab-run-stock');b.disabled=true;status('lab-stock-status','Running KAIROS V5 CORE PORT on historical share candles…');try{const s=valid();if(['1d','1wk'].includes(s.interval))throw Error('Choose an intraday interval for KAIROS core port.');
  const payload={...s,algorithm:'KAIROS_V5_CORE_PORT',parameters:{notional_usd:Number(str('lab-notional')),reward_r:Number(str('lab-r')),atr_multiple:Number(str('lab-atr')),cutoff:str('lab-cutoff'),smoothing:bool('lab-smoothing'),use_vp:bool('lab-vp'),use_slope:bool('lab-slope'),use_ema:bool('lab-ema'),use_sma:bool('lab-sma')}};
  const d=await api('strategy/run',requestJSON(payload));if(d.status!=='ok'){status('lab-stock-status',`${d.note}\n${d.warning}`);return}
  await loadRuns(d.run.id);status('lab-stock-status',`Saved run #${d.run.id} · ${d.run.metrics.trades} trades · P&L ${moneyLab(d.run.metrics.net_pnl)} · ${d.coverage.count} stock candles\n${d.warning}`);
 }catch(e){status('lab-stock-status',e.message,true)}finally{b.disabled=false}}
 async function loadSignals(){try{const out=await api('signals');const list=Array.isArray(out)?out:[];html('lab-signal-set',list.length?list.map(x=>`<option value="${x.id}">${escape(x.label)} (${escape(x.symbol)})</option>`).join(''):'<option value="">None imported</option>')}catch(e){status('lab-options-status','Signal sets unavailable: '+e.message,true)}}
 async function runOptions(){const b=$('lab-run-options');b.disabled=true;status('lab-options-status','Matching real options bid/ask quotes against your historical signals…');try{const s=valid();const source=str('lab-signal-source');const payload={symbol:s.symbol,source,run_id:Number(str('lab-run-select')),signal_set_id:Number(str('lab-signal-set')),contracts:str('lab-contract-mode'),strike_step:Number(str('lab-step')),qty:Number(str('lab-qty')),exit_mode:str('lab-exit-mode'),premium_stop_pct:Number(str('lab-premium-stop'))/100,premium_reward_r:Number(str('lab-premium-r')),auto_download:bool('lab-auto-quotes')};const d=await api('options/run',requestJSON(payload));const c=d.coverage||{};const errors=(c.errors||[]).slice(0,5).map(e=>e.reason||'Missing option quote');
 if(d.status==='ok'){await loadRuns(d.run.id)}
 status('lab-options-status',`${d.status==='ok'?`Completed ${d.run.metrics.trades} quote-covered option trade(s). P&L ${moneyLab(d.run.metrics.net_pnl)}.`:'No quote-covered options trades; no synthetic options P&L created.'}\nCoverage: ${c.filled||0}/${c.requested||0} matched, ${c.skipped||0} skipped.\n${errors.join('\n')}\n${d.warning}`);
 }catch(e){status('lab-options-status',e.message,true)}finally{b.disabled=false}}
 async function loadAlgorithms(){try{const d=await api('algorithms');html('lab-algorithm',(d||[]).map(x=>`<option value="${escape(x.id)}">${escape(x.name)}</option>`).join(''));$('lab-algorithm').value='KAIROS_V5'}catch(e){status('lab-run-status','Could not load algorithm list: '+e.message,true)}}
 async function loadRuns(id){try{const d=await api('backtests');runs=Array.isArray(d)?d:[];html('lab-run-select',runs.length?runs.map(x=>`<option value="${x.id}">#${x.id} · ${escape(x.label)} · ${escape(x.source)}</option>`).join(''):'<option value="">No runs yet</option>');if(id&&runs.some(x=>x.id===id))$('lab-run-select').value=String(id);else if(runs.length)$('lab-run-select').value=String(runs[0].id);if(runs.length)await showRun();else{html('lab-metrics','');html('lab-equity','<div class="empty">Run or import a backtest to see the equity curve.</div>');html('lab-trades-rows','')}}catch(e){status('lab-run-status','Could not load saved backtests: '+e.message,true)}}
 function curve(seq){if(!Array.isArray(seq)||seq.length<2)return '<div class="empty">Not enough completed trades to draw an equity curve.</div>';const values=seq.map(x=>Number(x)).filter(Number.isFinite);if(values.length<2)return '<div class="empty">No equity points.</div>';const lo=Math.min(0,...values),hi=Math.max(0,...values),span=hi-lo||1;const pts=values.map((v,i)=>`${30+i/(values.length-1)*760},${15+(hi-v)/span*175}`).join(' ');return `<svg viewBox="0 0 820 220" role="img" aria-label="Trade-by-trade cumulative profit and loss"><line x1="30" y1="${15+hi/span*175}" x2="790" y2="${15+hi/span*175}" stroke="#4b6379" stroke-dasharray="5 5"/><polyline points="${pts}" stroke="#d7b675" fill="none" stroke-width="3" stroke-linejoin="round"/><text x="30" y="212" fill="#9eb2c8" font-size="13">START</text><text x="790" y="212" text-anchor="end" fill="#9eb2c8" font-size="13">LAST TRADE · ${moneyLab(values.at(-1))}</text></svg>`}
 async function showRun(){const id=Number(str('lab-run-select'));if(!id)return;try{const d=await api('backtests/'+id);selectedRun=d;const m=d.metrics||{};status('lab-run-status',`Run #${id} · ${d.label} · ${d.source}\n${d.start_date||'—'} → ${d.end_date||'—'}`);
  const fields=[['Net P&L',moneyLab(m.net_pnl)],['Trades',fmt(m.trades)],['Win rate',fmt(m.win_rate)+'%'],['Profit factor',m.profit_factor===null?'n/a':fmt(m.profit_factor)],['Max drawdown',moneyLab(m.max_drawdown_usd)]];
  html('lab-metrics',fields.map(([name,val])=>`<div class="metric"><div class="metlabel">${escape(name)}</div><div class="metvalue">${escape(val)}</div></div>`).join(''));
  const line=[0,...((m.equity||[]).map(p=>p.pnl))];html('lab-equity',curve(line));
  html('lab-trades-rows',(d.trades||[]).slice(0,120).map(t=>`<tr><td>${escape(t.exit_at||t.date||'—')}</td><td>${escape(t.side||'—')}</td><td>${escape(t.contract||'Shares / imported')}</td><td>${t.entry_price===undefined?'—':moneyLab(t.entry_price)}</td><td>${t.exit_price===undefined?'—':moneyLab(t.exit_price)}</td><td style="color:${Number(t.pnl)>=0?'#7ce5c0':'#ff9eaa'}">${moneyLab(t.pnl)}</td></tr>`).join(''));
 }catch(e){status('lab-run-status',e.message,true)}}
 function hookUpload(id,kind){$(id).addEventListener('click',()=>$('lab-'+kind+'-file').click());$('lab-'+kind+'-file').addEventListener('change',e=>upload(kind,e.target.files?.[0]))}
 function init(){if(initialized)return;initialized=true;
  $('lab-download').addEventListener('click',download);$('lab-run-stock').addEventListener('click',runStock);$('lab-run-options').addEventListener('click',runOptions);
  $('lab-refresh-runs').addEventListener('click',()=>loadRuns());$('lab-run-select').addEventListener('change',showRun);
  hookUpload('lab-import-bars','bars');hookUpload('lab-import-signals','signals');hookUpload('lab-import-quotes','quotes');hookUpload('lab-import-trades','trades');
  $('lab-symbol').addEventListener('change',dataStatus);$('lab-interval').addEventListener('change',dataStatus);
  loadAlgorithms();loadRuns();loadSignals();dataStatus();
 }
 document.querySelectorAll('button[data-section="backtests"]').forEach(b=>b.addEventListener('click',init));
 return {init};
})();
