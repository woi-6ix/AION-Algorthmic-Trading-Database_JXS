/* AION Alpaca PAPER manual trade controls. Token stays only in JS memory. */
(() => {
  "use strict";
  const css = `
    #aion-ctl-root, #aion-ctl-root * {box-sizing:border-box;}
    #aion-ctl-root{position:fixed;right:20px;bottom:20px;z-index:2147483000;font:13px/1.45 system-ui,Segoe UI,sans-serif;color:#e4edfa;}
    #aion-ctl-root button{font:inherit;cursor:pointer;}
    #aion-ctl-toggle{border:1px solid #426c9c;border-radius:12px;padding:11px 16px;background:#101c35;color:#e7f2ff;box-shadow:0 12px 35px #02050cb8;font-weight:700;letter-spacing:.03em;}
    #aion-ctl-panel{position:absolute;right:0;bottom:58px;width:min(440px,calc(100vw - 24px));max-height:calc(100dvh - 100px);overflow:auto;border:1px solid #35516d;border-radius:16px;background:#0b1426;box-shadow:0 20px 60px #000b;padding:18px;}
    #aion-ctl-panel[hidden]{display:none;}
    #aion-ctl-root h3{font-size:17px;margin:0 0 2px;font-weight:700;}
    #aion-ctl-root .sub{color:#9db5cb;font-size:12px;margin:0 0 14px;}
    #aion-ctl-root .pill{display:inline-block;border:1px solid #556e83;border-radius:20px;padding:2px 9px;color:#b7c6da;font-size:11px;}
    #aion-ctl-root .pill.good{border-color:#268b78;color:#71ddc0;}
    #aion-ctl-root .pill.bad{border-color:#a34e58;color:#ff9aa6;}
    #aion-ctl-root .line{margin:9px 0;display:flex;justify-content:space-between;align-items:center;gap:10px;}
    #aion-ctl-root input{width:100%;background:#121e32;color:#f4f8fc;border:1px solid #46617d;border-radius:8px;padding:9px;outline-offset:2px;}
    #aion-ctl-root .actions{display:flex;gap:8px;margin:12px 0;}
    #aion-ctl-root .btn{border:1px solid #405d78;color:#eef7ff;background:#1b2b45;border-radius:8px;padding:9px 12px;font-weight:650;flex:1;}
    #aion-ctl-root .btn.pause{background:#695022;border-color:#b08740;}
    #aion-ctl-root .btn.resume{background:#176550;border-color:#2b9b77;}
    #aion-ctl-root .btn.danger{background:#702f3e;border-color:#ab5061;}
    #aion-ctl-root .btn:disabled{opacity:.42;cursor:not-allowed;}
    #aion-ctl-root .small{font-size:11px;color:#93abc2;}
    #aion-ctl-root .error{color:#ff9aa6;white-space:pre-wrap;overflow-wrap:anywhere;}
    #aion-ctl-root .success{color:#75dbc1;white-space:pre-wrap;overflow-wrap:anywhere;}
    #aion-ctl-root .pos{border:1px solid #31455b;border-radius:9px;padding:10px;margin:8px 0;}
    #aion-ctl-root .posinfo{display:flex;justify-content:space-between;gap:8px;align-items:center;}
    #aion-ctl-root .pos .btn{flex:0 0 auto;padding:5px 9px;font-size:12px;}
    #aion-ctl-root .hr{border-top:1px solid #263b50;margin:12px 0;}
    @media(max-width:520px){#aion-ctl-root{right:12px;bottom:12px;}#aion-ctl-panel{bottom:54px;padding:12px;}}
  `;
  const el = document.createElement('div');
  el.id = 'aion-ctl-root';
  el.innerHTML = `
    <button id="aion-ctl-toggle" type="button" aria-expanded="false">◉ &nbsp;ALPACA PAPER CONTROLS</button>
    <section id="aion-ctl-panel" hidden aria-label="Alpaca Paper Trade Controls">
      <h3>Alpaca • Manual Override <span class="pill">PAPER ONLY</span></h3>
      <p class="sub">AION LTM · Position management and algorithm arming</p>
      <div class="line"><input id="aion-ctl-token" type="password" autocomplete="off" placeholder="Enter your AION control token" aria-label="Control token"><button class="btn" id="aion-ctl-auth" type="button">Unlock</button></div>
      <div class="line"><span>Bot</span><span class="pill" id="aion-ctl-bot">Locked</span></div>
      <div class="line"><span>Worker heartbeat</span><span id="aion-ctl-heartbeat" class="small">—</span></div>
      <div class="line"><span>Broker</span><span id="aion-ctl-broker" class="small">—</span></div>
      <div class="actions"><button class="btn pause" id="aion-ctl-pause" type="button" disabled>Pause automation</button><button class="btn resume" id="aion-ctl-resume" type="button" disabled>Resume automation</button></div>
      <p class="small">Pausing disarms the worker. Open positions remain; automated exits may also stop. Resume arms the worker but does not start a stopped process.</p>
      <div class="hr"></div>
      <div class="line"><strong>Open PAPER positions</strong><button class="btn" id="aion-ctl-refresh" type="button" disabled>Refresh</button></div>
      <div id="aion-ctl-positions"><span class="small">Unlock to view broker positions.</span></div>
      <button class="btn danger" id="aion-ctl-all" type="button" disabled style="width:100%;margin-top:10px">CLOSE ALL PAPER POSITIONS + CANCEL ORDERS</button>
      <p class="small">Manual close automatically pauses the bot first. Account-wide CLOSE ALL affects every Alpaca PAPER holding, not just KAIROS. Requests submit orders; fills are not guaranteed.</p>
      <p id="aion-ctl-msg" role="status" aria-live="polite"></p>
    </section>
  `;
  document.head.appendChild(Object.assign(document.createElement('style'), {textContent: css}));
  document.body.appendChild(el);
  const $ = id => document.getElementById('aion-ctl-' + id);
  let token = '';
  let state = null;
  let busy = false;
  const safe = value => String(value ?? '—');
  function message(text, success = false) { $('msg').textContent = text; $('msg').className = success ? 'success' : 'error'; }
  function render() {
    const connected = !!state && !!token;
    $('pause').disabled = busy || !connected || !state.account_bound || (!state.armed && state.kill_switch);
    $('resume').disabled = busy || !connected || !state.account_bound || (state.armed && !state.kill_switch);
    $('refresh').disabled = busy || !token;
    $('all').disabled = busy || !connected || !state.broker_connected || !state.positions.length;
    $('bot').textContent = !connected ? 'Locked' : (state.auto_trading_active ? 'Active' : state.armed && !state.kill_switch ? 'Armed (worker not confirmed)' : 'Disarmed');
    $('bot').className = 'pill ' + (!connected ? '' : state.auto_trading_active ? 'good' : 'bad');
    $('heartbeat').textContent = connected ? (state.worker_recent ? 'Recent' : 'Stale / offline') : '—';
    $('broker').textContent = connected ? (state.broker_connected ? 'Alpaca PAPER connected' : safe(state.broker_error)) : '—';
    const container = $('positions');
    container.replaceChildren();
    if (!connected) { container.textContent = 'Unlock to view broker positions.'; return; }
    if (!state.broker_connected) { container.textContent = 'Broker data unavailable; no position actions allowed.'; return; }
    if (!state.positions.length) { container.textContent = 'No open positions reported by Alpaca PAPER.'; return; }
    for (const pos of state.positions) {
      const box = document.createElement('div'); box.className = 'pos';
      const upper = document.createElement('div'); upper.className = 'posinfo';
      const info = document.createElement('div');
      const title = document.createElement('strong'); title.textContent = safe(pos.symbol) + ' · ' + safe(pos.qty);
      const detail = document.createElement('div'); detail.className = 'small';
      detail.textContent = safe(pos.asset_class) + ' | P/L: $' + safe(pos.unrealized_pl) + ' | Value: $' + safe(pos.market_value);
      info.append(title, detail);
      const close = document.createElement('button'); close.className = 'btn danger'; close.textContent = 'Close'; close.disabled = busy;
      close.addEventListener('click', () => {
        const symbol = String(pos.symbol || '');
        if (!/^[A-Z0-9._-]{1,40}$/i.test(symbol)) { message('Unexpected position symbol; action blocked.'); return; }
        if (!confirm(`CLOSE ${symbol} (${pos.qty} units) on Alpaca PAPER?\nThe bot will be DISARMED first. A broker liquidation order will be submitted; a fill is not guaranteed.`)) return;
        action('/close/' + encodeURIComponent(symbol), 'CLOSE ' + symbol);
      });
      upper.append(info, close); box.append(upper); container.append(box);
    }
  }
  async function api(path, method='GET', body) {
    const response = await fetch('/api/alpaca-control' + path, {
      method, mode:'same-origin', cache:'no-store', credentials:'same-origin',
      headers: {'X-Aion-Control-Token': token, ...(body ? {'Content-Type':'application/json'} : {})},
      ...(body ? {body: JSON.stringify(body)} : {}),
    });
    const raw = await response.json().catch(()=>({detail:'Invalid server response'}));
    if (!response.ok) throw new Error(safe(raw.detail || ('Request failed (' + response.status + ')')));
    return raw;
  }
  async function refresh() {
    if (!token) { message('Enter a control token to unlock.'); return; }
    busy = true; render();
    try { state = await api('/state'); message('PAPER account status refreshed.', true); }
    catch(err) {state = null; message(err.message);}
    finally {busy = false; render();}
  }
  async function action(path, confirmation) {
    busy = true; render();
    try {
      const result = await api(path, 'POST', {confirm: confirmation});
      message(result.message + (result.outcomes ? '\nPer-position close requests: ' + result.outcomes.map(x => safe(x.symbol)+': '+safe(x.status)).join(' | ') : ''), true);
      // Fetch the broker's actual status; the close order need not fill instantly.
      state = await api('/state');
    } catch(err) {message(err.message); try {state = await api('/state');} catch(_e) {state = null;}}
    finally {busy = false; render();}
  }
  $('toggle').addEventListener('click', () => {const hidden = !$('panel').hidden; $('panel').hidden = hidden; $('toggle').setAttribute('aria-expanded', String(!hidden));});
  $('auth').addEventListener('click', () => {token = $('token').value.trim(); $('token').value = ''; refresh();});
  $('token').addEventListener('keydown', e => {if(e.key==='Enter') $('auth').click();});
  $('refresh').addEventListener('click', refresh);
  $('pause').addEventListener('click', () => {if(confirm('DISARM the Alpaca PAPER worker? Existing positions stay open, and automated exits may stop.')) action('/pause','PAUSE PAPER BOT');});
  $('resume').addEventListener('click', () => {if(confirm('ARM Alpaca PAPER automated trading? Make sure your worker is running and open positions are understood.')) action('/resume','RESUME PAPER BOT');});
  $('all').addEventListener('click', () => {
    const required = 'CLOSE ALL PAPER POSITIONS';
    const typed = prompt('This will DISARM AION, CANCEL ALL open orders, and send liquidation orders for ALL Alpaca PAPER positions (including non-KAIROS holdings).\n\nType exactly: ' + required);
    if(typed === required) action('/close-all', typed);
    else if(typed !== null) message('Exact confirmation not provided. No order sent.');
  });
  render();
})();