/* AION research dashboard — no fabricated market or backtest performance. */
'use strict';
const $ = s => document.querySelector(s);
const state={bars:[],runs:[],run:null,algorithms:[],loading:false};
const money = x => x==null || !Number.isFinite(+x) ? '—' : (+x).toLocaleString('en-US',{style:'currency',currency:'USD',minimumFractionDigits:2,maximumFractionDigits:2});
const num = (x,n=2) => x==null || !Number.isFinite(+x) ? '—' : (+x).toLocaleString('en-US',{maximumFractionDigits:n});
const esc = s => String(s??'').replace(/[&<>"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
async function api(url,opts){const res=await fetch(url,opts);const data=await res.json().catch(()=>({}));if(!res.ok)throw Error(data.detail||`HTTP ${res.status}`);return data;}
function warn(msg){const el=$('#error');el.hidden=!msg;el.textContent=msg||'';}
function busy(val){state.loading=val;$('#refresh').disabled=val;$('#refresh').innerHTML=val?'<span class="spinner"></span> Loading…':'↻ &nbsp;Refresh data';}
function svgLine(values,{color='#50e6b2',width=800,height=235,fill=true,zero=false}={}){
  const data=values.map((v,i)=>({x:i,v:Number(v)})).filter(x=>Number.isFinite(x.v));
  if(data.length<2)return '<div class="empty">Not enough data to plot an equity or market curve.</div>';
  let minimum=Math.min(...data.map(d=>d.v)),maximum=Math.max(...data.map(d=>d.v));
  if(zero){minimum=Math.min(0,minimum);maximum=Math.max(0,maximum);}
  let spread=maximum-minimum||1, pad=spread*.12;minimum-=pad;maximum+=pad;spread=maximum-minimum;
  const l=58,r=10,t=10,b=29,plotW=width-l-r,plotH=height-t-b;
  const xs=i=>l+i/(values.length-1)*plotW,ys=v=>t+(maximum-v)/spread*plotH;
  const pts=data.map(d=>[xs(d.x),ys(d.v)]);const path=pts.map((p,i)=>(i?'L':'M')+p[0].toFixed(2)+','+p[1].toFixed(2)).join(' ');
  const base=ys(minimum);const area=path+` L${pts.at(-1)[0]},${base} L${pts[0][0]},${base} Z`;
  const uid='g'+(Math.abs(Math.round(values[0]*97+values.length*37))%100000);
  const ticks=Array.from({length:5},(_,i)=>minimum+(maximum-minimum)*i/4);
  return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-label="Trend chart">
    <defs><linearGradient id="${uid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".20"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
    ${ticks.map(v=>`<line x1="${l}" x2="${width-r}" y1="${ys(v)}" y2="${ys(v)}" stroke="#26384b" stroke-width=".7" stroke-dasharray="3 5"/><text x="${l-9}" y="${ys(v)+3}" fill="#71849b" text-anchor="end">${num(v,2)}</text>`).join('')}
    ${fill?`<path d="${area}" fill="url(#${uid})"/>`:''}<path d="${path}" fill="none" stroke="${color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>
    <circle cx="${pts.at(-1)[0]}" cy="${pts.at(-1)[1]}" r="4" fill="${color}" stroke="#0e1622" stroke-width="2"/>
    <text x="${l}" y="${height-3}" fill="#71849b">START</text><text x="${width-r}" y="${height-3}" fill="#71849b" text-anchor="end">LATEST</text>
  </svg>`;
}
function quoteView(d){
  $('#quote-symbol').textContent=d.symbol;$('#price-title').textContent=d.symbol+' price action';
  $('#quote-price').textContent=money(d.quote.price);const change=d.quote.change_pct;
  const c=$('#quote-change');c.textContent=(change>=0?'+':'')+num(change)+'% / bar';c.classList.toggle('negative',change<0);
  $('#quote-high').textContent=money(d.quote.high);$('#quote-low').textContent=money(d.quote.low);$('#quote-volume').textContent=num(d.quote.volume,0);
  state.bars=d.bars||[];const closes=state.bars.map(x=>x.c);const rising=closes.length>1&&closes.at(-1)>=closes[0];
  $('#price-chart').innerHTML=svgLine(closes,{color:rising?'#50e6b2':'#fa777e'});
  $('#price-bars').textContent=closes.length+' bars';
  $('#price-caption').textContent=`${d.interval} · ${d.period} · ${d.source?.source||'Market provider'}`;
  $('#source-status').textContent=d.source?.source||'Provider available';
  $('#source-help').textContent=d.source?.notice||'Market prices may be delayed.';
  $('#last-update').textContent='Latest bar · '+new Date(d.source.last_bar).toLocaleString();
}
function regimeView(r){
  const storm=r?.storm||{};const risk=Number(storm.score);
  $('#storm-score').textContent=Number.isFinite(risk)?num(risk,0):'—';
  $('#storm-label').textContent=storm.label||r?.status||'Unavailable';
  $('#storm-explain').textContent=storm.note || storm.formula || r?.note || 'No result';
  const ring=$('#regime-ring');ring.style.setProperty('--risk',Number.isFinite(risk)?`${Math.max(0,Math.min(100,risk))}%`:'0%');
  ring.style.background=`conic-gradient(${risk>=67?'#fa777e':risk>=33?'#f9c47d':'#50e6b2'} ${Number.isFinite(risk)?risk:0}%, #223448 0)`;
  $('#hmm-label').textContent=r?.hmm?.label||'Unavailable';
  $('#hmm-confidence').textContent=r?.hmm?.confidence!=null?`${num(r.hmm.confidence,1)}%`: '—';
  $('#hmm-track').style.width=r?.hmm?.confidence!=null?`${r.hmm.confidence}%`:'0%';
  $('#garch-vol').textContent=r?.garch?.sigma_pct_per_bar!=null?`${num(r.garch.sigma_pct_per_bar,3)}%`:'—';
  $('#garch-track').style.width=r?.garch?.vol_percentile!=null?`${r.garch.vol_percentile}%`:'0%';
}
function sentimentView(s){
  $('#sentiment-score').textContent=s?.score==null?'—':(s.score>0?'+':'')+num(s.score,1);
  $('#sentiment-score').style.color=s?.score>15?'#50e6b2':s?.score< -15?'#fa777e':'#e8edf5';
  $('#sentiment-label').textContent=s?.tone||'Unavailable';
  $('#sentiment-marker').style.left=`${s?.score==null?50:Math.max(0,Math.min(100,50+s.score/2))}%`;
  $('#sentiment-count').textContent=`${s?.count||0} headlines assessed`;
  $('#sentiment-model').textContent=s?.model||'HG indicator requires separate methodology.';
  $('#sentiment-confidence').textContent=(s?.confidence||'Low confidence')+' · '+(s?.note||'');
}
function newsView(news){
  $('#news-list').innerHTML=(news&&news.length)?news.map(x=>{
    const tag=x.tone||'neutral',provider=x.publisher||'News provider',date=x.published?new Date(x.published).toLocaleDateString():'Recent';
    const tagEl=`<div class="news-top"><span>${esc(provider)} · ${esc(date)}</span><span class="news-tone ${esc(tag)}">${tag==='positive'?'▲':tag==='negative'?'▼':'●'} ${tag.toUpperCase()}</span></div>`;
    return x.url?`<a class="news-item" href="${esc(x.url)}" rel="noopener noreferrer" target="_blank">${tagEl}<div class="news-title">${esc(x.title)}</div></a>`:`<div class="news-item">${tagEl}<div class="news-title">${esc(x.title)}</div></div>`;
  }).join(''):'<div class="news-empty">No news was returned for this ticker. Sentiment is unavailable rather than inferred from nonexistent articles.</div>';
}
async function refresh(){
  const symbol=$('#symbol').value.trim().toUpperCase(),interval=$('#interval').value,period=$('#period').value;
  warn('');busy(true);
  try{
    const d=await api(`/lab/api/overview?symbol=${encodeURIComponent(symbol)}&interval=${interval}&period=${period}`);
    quoteView(d);regimeView(d.regime);sentimentView(d.sentiment);newsView(d.news);
    document.querySelectorAll('.chip').forEach(b=>b.classList.toggle('active',b.dataset.symbol===symbol));
  }catch(err){warn('Data unavailable: '+err.message+' Change the lookback/timeframe or check the market-data service. Existing backtests are unaffected.');$('#source-status').textContent='Connection issue';$('#last-update').textContent='Feed unavailable';}
  finally{busy(false)}
}
function updateSelectedRun(){
  const id=Number($('#run-select').value);state.run=state.runs.find(r=>r.id===id)||null;const m=state.run?.metrics;
  const set=(id,value)=>{document.querySelector(id).textContent=value};
  set('#metric-pnl',m?money(m.net_pnl):'—');
  set('#metric-win',m?num(m.win_rate,1)+'%':'—');
  set('#metric-trades',m?num(m.trades,0):'—');
  set('#metric-pf',m?.profit_factor!=null?num(m.profit_factor,2):m?'∞':'—');
  set('#metric-dd',m?money(m.max_drawdown_usd):'—');
  $('#metric-pnl').className='metric-value '+(m?(m.net_pnl>=0?'green':'red'):'');
  $('#metric-pnl-sub').textContent=state.run?`${state.run.algorithm} · ${state.run.symbol} ${state.run.timeframe}`:'No backtest imported';
  $('#metric-win-sub').textContent=state.run?`${m.wins} winning trades`:'Completed trades only';
  $('#metric-trades-sub').textContent=state.run?state.run.source:'Historical test output';
  $('#metric-pf-sub').textContent=m?`Avg trade ${money(m.avg_trade)}`:'Gross gains / losses';
  $('#equity-chart').innerHTML=m?svgLine([0,...m.equity.map(x=>x.pnl)],{height:143,color:m.net_pnl>=0?'#50e6b2':'#fa777e',zero:true}):'<div class="empty">Import an actual TradingView backtest CSV to see your equity curve.</div>';
  $('#run-description').textContent=state.run?`${state.run.label} · ${state.run.source}`:'No strategy run selected';
}
async function loadRuns(selectID){
  try{
    state.runs=await api('/lab/api/backtests');
    $('#run-select').innerHTML=state.runs.length?state.runs.map(r=>`<option value="${r.id}">${esc(r.label)}</option>`).join(''):'<option value="">No runs available</option>';
    if(selectID && state.runs.some(r=>r.id===selectID))$('#run-select').value=String(selectID);
    updateSelectedRun();
  }catch(e){warn('Could not load saved backtests: '+e.message)}
}
async function loadAlgorithms(){
  try{
    state.algorithms=await api('/lab/api/algorithms');
    $('#algorithm').innerHTML=state.algorithms.map(a=>`<option value="${esc(a.id)}">${esc(a.name)}</option>`).join('');
    $('#algorithm').value='KAIROS_V5';updateAlgoHint();
  }catch(e){warn('Could not load strategy list: '+e.message)}
}
function updateAlgoHint(){
  const a=state.algorithms.find(x=>x.id===$('#algorithm').value),isResearch=a?.status==='runnable';
  $('#run-btn').disabled=!isResearch;$('#risk').disabled=!isResearch;
  $('#lab-hint').textContent=isResearch?'Independent bar-based shares research test (1R brackets, conservative stop-first fills, commissions and slippage). NOT exact Pine or 0DTE options.':(a?.description||'Import a completed TradingView Strategy Tester trade export.');
}
async function importFile(file){
  if(!file)return;
  const algorithm=$('#algorithm').value,symbol=$('#symbol').value.trim().toUpperCase(),timeframe=$('#interval').value;
  const form=new FormData();form.append('file',file);form.append('algorithm',algorithm);form.append('symbol',symbol);form.append('timeframe',timeframe);
  form.append('label',`${algorithm} · ${symbol} · ${timeframe} · ${new Date().toLocaleDateString()}`);
  warn('');$('#import-btn').disabled=true;
  try{const d=await api('/lab/api/backtests/import',{method:'POST',body:form});await loadRuns(d.id);$('#backtests').scrollIntoView({behavior:'smooth'});}
  catch(e){warn('CSV import failed: '+e.message)}finally{$('#import-btn').disabled=false;$('#file-picker').value=''}
}
async function runResearch(){
  const a=$('#algorithm').value;if(!a.endsWith('_RESEARCH'))return;
  const query=new URLSearchParams({algorithm:a,symbol:$('#symbol').value.trim(),interval:$('#interval').value,period:$('#period').value,risk_usd:$('#risk').value});
  warn('');$('#run-btn').disabled=true;$('#run-btn').textContent='Calculating…';
  try{const d=await api('/lab/api/backtests/research?'+query,{method:'POST'});if(d.status==='no_trades')warn(d.note);else{await loadRuns(d.run.id);warn('Research-only results stored. '+d.warning)}}
  catch(e){warn('Research test unavailable: '+e.message)}finally{$('#run-btn').textContent='▶ Run research test';updateAlgoHint()}
}
function initialize(){
  $('#refresh').addEventListener('click',refresh);$('#symbol').addEventListener('keydown',e=>{if(e.key==='Enter')refresh()});
  $('#interval').addEventListener('change',refresh);$('#period').addEventListener('change',refresh);
  document.querySelectorAll('.chip').forEach(b=>b.addEventListener('click',()=>{$('#symbol').value=b.dataset.symbol;refresh()}));
  $('#algorithm').addEventListener('change',updateAlgoHint);$('#run-select').addEventListener('change',updateSelectedRun);
  $('#import-btn').addEventListener('click',()=>$('#file-picker').click());$('#file-picker').addEventListener('change',e=>importFile(e.target.files[0]));
  $('#run-btn').addEventListener('click',runResearch);
  document.querySelectorAll('.nav a').forEach(a=>a.addEventListener('click',()=>{document.querySelectorAll('.nav a').forEach(x=>x.classList.remove('active'));a.classList.add('active')}));
  loadAlgorithms();loadRuns();refresh();
  // Poll only while the tab is visible to reduce provider traffic. Prices may be delayed.
  setInterval(()=>{if(!document.hidden&&!state.loading)refresh()},120000);
}
initialize();

// -- V2 historical + stock / options backtesting --------------------------------
const v2 = {signals:[],data:null};
function v2Status(id,message,isError=false){let el=$(id);el.textContent=message;el.classList.toggle('error',isError);el.classList.toggle('ok',!isError);}
function v2Selection(){return {symbol:$('#symbol').value.trim().toUpperCase(),interval:$('#interval').value,period:$('#period').value,provider:$('#v2-provider').value,start:$('#v2-start').value||null,end:$('#v2-end').value||null};}
async function v2DataStatus(){
  const x=v2Selection();try{let d=await api(`/lab/api/data/status?symbol=${encodeURIComponent(x.symbol)}&interval=${encodeURIComponent(x.interval)}`);
    v2Status('#v2-data-status',`LOCAL DATASET: ${num(d.bars,0)} bars (${x.symbol} · ${x.interval}) · ${d.first_bar||'—'} → ${d.last_bar||'—'}\nHistorical option contracts saved: ${d.option_contracts_cached} · Massive key: ${d.massive_api_configured?'configured':'not configured'}`);
  }catch(e){v2Status('#v2-data-status',e.message,true)}
}
async function v2Download(){const btn=$('#v2-download');btn.disabled=true;v2Status('#v2-data-status','Downloading actual historical price bars…');
  try{const d=await api('/lab/api/data/download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(v2Selection())});
    v2Status('#v2-data-status',`Saved ${d.count} genuine OHLCV bars · ${d.first_bar} → ${d.last_bar}\nSOURCE: ${d.source}\n${d.warning}`);}
  catch(e){v2Status('#v2-data-status','Data download failed: '+e.message,true)}finally{btn.disabled=false;}
}
async function v2Upload(kind,file){if(!file)return;
  let ep,el,frm=new FormData();frm.append('file',file);
  if(kind==='bars') {const x=v2Selection();ep='/lab/api/data/import';el='#v2-data-status';frm.append('symbol',x.symbol);frm.append('interval',x.interval);}
  else if(kind==='quotes') {ep='/lab/api/options/quotes/import';el='#v2-options-status';}
  else {ep='/lab/api/signals/import';el='#v2-options-status';frm.append('symbol',v2Selection().symbol);frm.append('algorithm',$('#algorithm').value);}
  v2Status(el,'Importing and validating '+kind+'…');
  try{let d=await api(ep,{method:'POST',body:frm});
    if(kind==='bars'){v2Status(el,`Imported ${d.count} candles: ${d.first_bar} → ${d.last_bar}. Stored in local cache.`);}
    if(kind==='quotes')v2Status(el,`Imported ${d.quotes} actual two-sided quotes across ${d.contracts} contracts. 0DTE replay can now use these snapshots.`);
    if(kind==='signals'){v2Status(el,`Imported ${d.rows} entry/exit signals for ${d.algorithm} (set #${d.id}). Select this signal source before running 0DTE.`);await v2LoadSignals();$('#v2-option-source').value='signal_set';$('#v2-signal-set').value=String(d.id);}
  }catch(e){v2Status(el,'Import failed: '+e.message,true)}
  finally{if(kind==='bars')$('#v2-bars-file').value='';if(kind==='quotes')$('#v2-quotes-file').value='';if(kind==='signals')$('#v2-signals-file').value='';}
}
async function v2LoadSignals(){
  try{v2.signals=await api('/lab/api/signals');$('#v2-signal-set').innerHTML=v2.signals.length?v2.signals.map(s=>`<option value="${s.id}">${esc(s.label)} · ${esc(s.symbol)} · #${s.id}</option>`).join(''):'<option value="">No imported signals</option>';}
  catch(e){v2Status('#v2-options-status','Could not load signal sets: '+e.message,true)}
}
async function v2RunStock(){const btn=$('#v2-run-stock');btn.disabled=true;v2Status('#v2-stock-status','Running KAIROS V5 core port on actual historical bars…');
  try{const x=v2Selection();const body={...x,algorithm:'KAIROS_V5_CORE_PORT',parameters:{
    notional_usd:Number($('#v2-notional').value),reward_r:Number($('#v2-r').value),atr_multiple:Number($('#v2-atr').value),
    cutoff:$('#v2-cutoff').value,smoothing:$('#v2-smoothing').checked,use_vp:$('#v2-vp').checked,
    use_slope:$('#v2-slope').checked,use_ema:$('#v2-ema').checked,use_sma:$('#v2-sma').checked}};
    const d=await api('/lab/api/strategy/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    if(d.status==='ok'){
      await loadRuns(d.run.id);
      v2Status('#v2-stock-status',`Stock shares: ${d.run.metrics.trades} completed · Net P&L ${money(d.run.metrics.net_pnl)} · Win rate ${num(d.run.metrics.win_rate,1)}%\n${d.coverage.count} OHLCV bars · ${d.coverage.first_bar} → ${d.coverage.last_bar}\n${d.coverage.source}\n${d.warning}`);
    } else v2Status('#v2-stock-status',`${d.note}\n${d.coverage?.count||0} bars\n${d.warning}`);
  }catch(e){v2Status('#v2-stock-status','Stock backtest failed: '+e.message,true)}finally{btn.disabled=false}
}
async function v2RunOptions(){const btn=$('#v2-run-options');btn.disabled=true;v2Status('#v2-options-status','Matching every underlying signal to actual 0DTE historical bid/ask quotes…');
  try{const x=v2Selection();const source=$('#v2-option-source').value;
    const payload={source,symbol:x.symbol,run_id:Number($('#run-select').value),signal_set_id:Number($('#v2-signal-set').value),contracts:$('#v2-contract-mode').value,
      strike_step:Number($('#v2-strike-step').value),qty:Number($('#v2-contracts').value),auto_download:$('#v2-auto-quotes').checked,
      exit_mode:$('#v2-exit-mode').value,premium_stop_pct:Number($('#v2-premium-stop').value)/100,premium_reward_r:Number($('#v2-premium-r').value)};
    const d=await api('/lab/api/options/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    if(d.status==='ok')await loadRuns(d.run.id);
    const c=d.coverage||{};const errors=(c.errors||[]).slice(0,4).map(e=>e.reason+(e.contract?' ('+e.contract+')':''));
    v2Status('#v2-options-status',`${d.status==='ok'?`Recorded real-quote 0DTE P&L: ${money(d.run.metrics.net_pnl)} · ${d.run.metrics.trades} trades`:'No executable option trade pairs.'}\nCoverage: ${c.filled||0} filled / ${c.requested||0} signals · ${c.skipped||0} skipped\n${errors.join('\n')||'All signal entry/exit quote matches found.'}\n${d.warning}`);
  }catch(e){v2Status('#v2-options-status','0DTE backtest failed: '+e.message,true)}finally{btn.disabled=false}
}
function initV2(){
  $('#v2-download').addEventListener('click',v2Download);$('#v2-run-stock').addEventListener('click',v2RunStock);$('#v2-run-options').addEventListener('click',v2RunOptions);
  for(const [button,input,kind] of [['#v2-import-bars','#v2-bars-file','bars'],['#v2-import-quotes','#v2-quotes-file','quotes'],['#v2-import-signals','#v2-signals-file','signals']]){
    $(button).addEventListener('click',()=>$(input).click());$(input).addEventListener('change',e=>v2Upload(kind,e.target.files[0]));
  }
  $('#symbol').addEventListener('change',v2DataStatus);$('#interval').addEventListener('change',v2DataStatus);
  v2DataStatus();v2LoadSignals();
}
initV2();
