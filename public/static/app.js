(() => {
  'use strict';

  const $ = (s, root=document) => root.querySelector(s);
  const $$ = (s, root=document) => [...root.querySelectorAll(s)];
  const esc = (v='') => String(v ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const tokenKey='polarops_token_v1', cacheKey='polarops_cache_v1', queueKey='polarops_queue_v1';
  let persistentCache={};try{persistentCache=JSON.parse(localStorage.getItem(cacheKey)||'{}')}catch{}
  let cacheWriteTimer=null;
  const state = {
    token: localStorage.getItem(tokenKey) || '', user:null, expeditions:[], expeditionId:null,
    view:'overview', online:navigator.onLine, pending: JSON.parse(localStorage.getItem(queueKey)||'[]'),
    serviceWorker:false,
    realtimeStatus:'disconnected', ws:null, wsGeneration:0, wsReconnect:null, wsPing:null, realtimeRender:null,
    fallbackTimer:null, deferredRealtime:false,
    gpsWatchId:null, gpsPersonnelId:null, gpsLastSent:0,
    vehicleSimTimer:null, vehicleSimId:null, vehicleSimStep:0, vehicleSimBase:null,
    liveMap:null, liveMarkers:{personnel:new Map(),vehicle:new Map()}, renderInProgress:false, renderQueued:false,
    memoryCache:new Map(), prefetchGeneration:0
  };

  const navItems=[
    ['overview','⌂','Dashboard'],['operations','◫','Operations'],['personnel','◎','Personnel'],['cargo','▣','Cargo'],['inventory','▤','Inventory'],
    ['routes','⌁','Routes & Zones'],['science','✧','Science'],['comms','◌','Communications'],['readiness','✓','Readiness'],
    ['assets','◇','Assets'],['vehicles','▱','Vehicles'],['emergency','△','Incidents'],['environment','◉','Environment'],['network','⌖','Facility Network'],
    ['alerts','!','Alert Center'],['activity','≡','Audit Trail'],['settings','⚙','Settings']
  ];

  const FAST_CACHE_MS=15000;
  function saveQueue(){ localStorage.setItem(queueKey,JSON.stringify(state.pending)); updateSync(); }
  function getCache(){ return persistentCache; }
  function flushPersistentCache(){ clearTimeout(cacheWriteTimer);cacheWriteTimer=null;try{localStorage.setItem(cacheKey,JSON.stringify(persistentCache))}catch{} }
  function setCache(path,data){
    persistentCache[path]={ts:Date.now(),data};
    clearTimeout(cacheWriteTimer);
    cacheWriteTimer=setTimeout(flushPersistentCache,350);
  }
  function fromCache(path){ return persistentCache[path]?.data; }
  window.addEventListener('pagehide',flushPersistentCache);
  function fastCacheable(path){
    return !path.includes('force=true') && !path.startsWith('/api/realtime/ticket') && !path.startsWith('/api/ops/search') && !path.startsWith('/api/public/facilities/') && !path.includes('/weather');
  }
  function fastCacheGet(path){
    const hit=state.memoryCache.get(path);
    return hit && Date.now()-hit.ts<FAST_CACHE_MS ? hit.data : undefined;
  }
  function fastCacheSet(path,data){ if(fastCacheable(path))state.memoryCache.set(path,{ts:Date.now(),data}); }
  function clearFastCache(){ state.memoryCache.clear(); }
  function toast(title,detail='',kind='good',ms=2800){
    const root=$('#toastRoot'); if(!root)return;
    const el=document.createElement('div'); el.className=`toast ${kind}`; el.innerHTML=`<strong>${esc(title)}</strong>${detail?`<span>${esc(detail)}</span>`:''}`;
    root.appendChild(el); setTimeout(()=>el.remove(),ms);
  }

  async function api(path,opts={}){
    const method=(opts.method||'GET').toUpperCase();
    const useFastCache=method==='GET' && opts.noFastCache!==true && fastCacheable(path);
    if(useFastCache){
      const cached=fastCacheGet(path);
      if(cached!==undefined)return cached;
    }
    const headers={'Content-Type':'application/json',...(opts.headers||{})};
    if(state.token) headers.Authorization=`Bearer ${state.token}`;
    const fetchOpts={...opts,method,headers};
    delete fetchOpts.noFastCache;
    try{
      const res=await fetch(path,fetchOpts);
      if(res.status===401 && path!='/api/auth/login'){ logout(false); throw new Error('Your session expired. Please sign in again.'); }
      let data=null; const ct=res.headers.get('content-type')||'';
      if(ct.includes('application/json')) data=await res.json(); else data=await res.text();
      if(!res.ok) throw new Error(data?.detail || data || `Request failed (${res.status})`);
      state.online=true;
      if(method==='GET'){setCache(path,data);fastCacheSet(path,data)}
      else clearFastCache();
      updateSync();
      return data;
    }catch(err){
      state.online=navigator.onLine;
      updateSync();
      if(method==='GET'){
        const cached=fromCache(path); if(cached!==undefined){ toast('Offline data shown','Using the latest cached mission snapshot.','warn'); return cached; }
      } else if(!navigator.onLine && path!='/api/auth/login'){
        const item={id:crypto.randomUUID?.()||`${Date.now()}-${Math.random()}`,path,method,body:opts.body||null,status:'PENDING',attempts:0,created_at:new Date().toISOString()};
        state.pending.push(item); saveQueue(); toast('Action queued for sync','It will be sent when connectivity returns.','warn'); return {queued:true};
      }
      throw err;
    }
  }

  async function flushQueue(){
    if(!navigator.onLine || !state.token || !state.pending.length) return;
    const queued=[...state.pending], remaining=[]; let synced=0;
    for(let i=0;i<queued.length;i++){
      const q=queued[i];
      try{
        q.status='SYNCING';q.attempts=(q.attempts||0)+1;
        const res=await fetch(q.path,{method:q.method,headers:{'Content-Type':'application/json','Authorization':`Bearer ${state.token}`,'X-PolarOps-Mutation-ID':q.id},body:q.body});
        if(res.ok){q.status='SYNCED';synced++;continue}
        let detail='';
        try{const body=await res.json();detail=body?.detail||body?.error?.message||''}catch{}
        if(res.status===401){
          q.status='FAILED';q.last_error=detail||'Authentication expired';
          remaining.push(q,...queued.slice(i+1));
          logout(false);break;
        }
        if(res.status===409){q.status='CONFLICT';q.last_error=detail||'Conflict requires review';remaining.push(q);continue}
        if(res.status===429||res.status>=500){q.status='PENDING';q.last_error=detail||`HTTP ${res.status}; retry later`;remaining.push(q);continue}
        q.status='FAILED';q.last_error=detail||`HTTP ${res.status}`;remaining.push(q);
      }catch(err){
        q.status='PENDING';q.last_error=err?.message||'Network failure';remaining.push(q);
      }
    }
    state.pending=remaining; saveQueue();
    if(synced){ toast('Offline actions synchronized',`${synced} queued action${synced===1?'':'s'} uploaded.`); if(state.view!=='overview')renderView(); }
    if(remaining.some(q=>q.status==='CONFLICT'))toast('Sync conflict','One or more offline actions need review.','warn',4500);
  }

  function updateSync(){
    const dot=$('.sync-dot'), a=$('#syncLabel'), b=$('#syncDetail'); if(dot){
      dot.className='sync-dot';
      if(!navigator.onLine){dot.classList.add('offline'); if(a)a.textContent='OFFLINE';if(b)b.textContent=`${state.pending.length} queued`}
      else if(state.pending.length){dot.classList.add('pending');if(a)a.textContent='SYNC PENDING';if(b)b.textContent=`${state.pending.length} queued`}
      else{if(a)a.textContent='SYNC ONLINE';if(b)b.textContent='Central database connected'}
    }
    updateRealtimeIndicator();
  }

  function updateRealtimeIndicator(){
    const pill=$('#realtimePill'),label=$('#realtimeLabel'); if(!pill)return;
    pill.className=`realtime-pill ${state.realtimeStatus}`;
    if(label) label.textContent=state.realtimeStatus==='live'?'LIVE':state.realtimeStatus==='connecting'?'CONNECTING':!navigator.onLine?'OFFLINE':'FALLBACK';
  }

  function disconnectRealtime(){
    state.wsGeneration++;
    clearTimeout(state.wsReconnect);clearInterval(state.wsPing);
    state.wsReconnect=null;state.wsPing=null;
    if(state.ws){ try{state.ws.close()}catch{} }
    state.ws=null;state.realtimeStatus=navigator.onLine?'disconnected':'offline';updateRealtimeIndicator();
  }

  async function connectRealtime(){
    if(!state.token||!state.expeditionId||!navigator.onLine)return;
    disconnectRealtime();
    const generation=++state.wsGeneration;
    const expeditionId=Number(state.expeditionId);
    state.realtimeStatus='connecting';updateRealtimeIndicator();
    let ticket;
    try{
      const issued=await api(`/api/realtime/ticket?expedition_id=${expeditionId}`);
      ticket=issued.ticket;
    }catch(err){
      if(generation===state.wsGeneration&&expeditionId===Number(state.expeditionId)){state.realtimeStatus='disconnected';updateRealtimeIndicator();clearTimeout(state.wsReconnect);state.wsReconnect=setTimeout(connectRealtime,5000)}
      return;
    }
    if(generation!==state.wsGeneration||expeditionId!==Number(state.expeditionId)||!ticket)return;
    const protocol=location.protocol==='https:'?'wss':'ws';
    const socket=new WebSocket(`${protocol}://${location.host}/ws/expeditions/${expeditionId}?ticket=${encodeURIComponent(ticket)}`);
    state.ws=socket;
    socket.onopen=()=>{ if(generation!==state.wsGeneration)return; socket.send(JSON.stringify({type:'auth',token:state.token})); };
    socket.onmessage=e=>{ if(generation!==state.wsGeneration)return; let msg;try{msg=JSON.parse(e.data)}catch{return}
      if(msg.type==='auth.ok'){state.realtimeStatus='live';updateRealtimeIndicator();clearInterval(state.wsPing);state.wsPing=setInterval(()=>{if(socket.readyState===WebSocket.OPEN)socket.send(JSON.stringify({type:'ping'}))},25000);return}
      if(msg.type==='auth.error'){state.realtimeStatus='disconnected';updateRealtimeIndicator();return}
      if(msg.type==='pong')return;
      handleRealtimeEvent(msg);
    };
    socket.onerror=()=>{if(generation===state.wsGeneration){state.realtimeStatus='disconnected';updateRealtimeIndicator()}};
    socket.onclose=()=>{if(generation!==state.wsGeneration)return;state.ws=null;clearInterval(state.wsPing);state.wsPing=null;state.realtimeStatus=navigator.onLine?'disconnected':'offline';updateRealtimeIndicator();if(state.token&&navigator.onLine){clearTimeout(state.wsReconnect);state.wsReconnect=setTimeout(connectRealtime,3000)}};
  }

  function handleRealtimeEvent(message){
    if(Number(message.expedition_id)!==Number(state.expeditionId))return;
    if(!message.type?.startsWith('telemetry.'))clearFastCache();
    if(message.type==='incident.created')toast('Live SOS received',message.data?.code||'New incident','danger',4200);

    // Telemetry is high-frequency. Never rebuild the dashboard for telemetry:
    // move existing markers in place and wait for the next intentional data
    // refresh to reconcile any newly-added telemetry entities.
    if(message.type?.startsWith('telemetry.')){
      if(message.type==='telemetry.updated' && state.view==='overview' && state.liveMap){
        updateLiveTelemetryMarker(message);
      }
      return;
    }

    if(state.view==='overview')return;
    if($('#modalRoot')?.children.length){state.deferredRealtime=true;return}
    clearTimeout(state.realtimeRender);
    state.realtimeRender=setTimeout(()=>renderView(),450);
  }

  function updateLiveTelemetryMarker(message){
    const kind=message.entity_type, id=Number(message.entity_id), data=message.data||{};
    if(!['personnel','vehicle'].includes(kind)||!Number.isFinite(+data.latitude)||!Number.isFinite(+data.longitude))return false;
    const marker=state.liveMarkers?.[kind]?.get(id);
    if(!marker)return false;
    marker.setLatLng([+data.latitude,+data.longitude]);
    const when=data.recorded_at?ago(data.recorded_at):'just now';
    if(kind==='vehicle'){
      marker.setPopupContent(`<strong>Vehicle telemetry</strong><br><b>LIVE GPS</b> · ${when}${data.fuel_percent!=null?`<br>${n(data.fuel_percent)}% fuel`:''}`);
    }else{
      marker.setPopupContent(`<strong>Personnel telemetry</strong><br><b>LIVE GPS</b> · ${when}${data.accuracy_m!=null?`<br>Accuracy ±${n(data.accuracy_m)}m`:''}`);
    }
    return true;
  }

  function startFallbackRefresh(){
    clearInterval(state.fallbackTimer);
    state.fallbackTimer=setInterval(()=>{
      if(!state.token||!navigator.onLine||state.realtimeStatus==='live'||document.hidden||$('#modalRoot')?.children.length)return;

      // A complete overview rebuild destroys/recreates Leaflet and looks like
      // a page refresh. Keep the command dashboard stable while WebSocket is
      // reconnecting. Other list views can still use the fallback refresh.
      if(state.view==='overview')return;
      renderView();
    },30000);
  }

  async function prefetchMissionData(){
    const expeditionId=Number(state.expeditionId), generation=++state.prefetchGeneration;
    if(!expeditionId||!state.token||!navigator.onLine)return;
    const pole=currentPole();
    const paths=[
      `/api/locations?expedition_id=${expeditionId}`,
      `/api/personnel?expedition_id=${expeditionId}`,
      `/api/cargo?expedition_id=${expeditionId}`,
      `/api/inventory?expedition_id=${expeditionId}`,
      `/api/vehicles?expedition_id=${expeditionId}`,
      `/api/assets?expedition_id=${expeditionId}`,
      `/api/incidents?expedition_id=${expeditionId}`,
      `/api/ops/summary?expedition_id=${expeditionId}`,
      `/api/ops/routes?expedition_id=${expeditionId}`,
      `/api/ops/science?expedition_id=${expeditionId}`,
      `/api/ops/comms?expedition_id=${expeditionId}`,
      `/api/ops/readiness?expedition_id=${expeditionId}`,
      `/api/ops/alerts?expedition_id=${expeditionId}`,
      `/api/ops/audit?expedition_id=${expeditionId}&limit=300`,
      `/api/integrations/workers/status?expedition_id=${expeditionId}`,
      `/api/environment/overview?expedition_id=${expeditionId}`,
      '/api/data-sources',
      pole==='south'?'/api/public/facilities?limit=1000':'/api/public/arctic-research-stations'
    ];
    if(roleCan('commander'))paths.push('/api/users');
    for(let i=0;i<paths.length;i+=4){
      if(generation!==state.prefetchGeneration||expeditionId!==Number(state.expeditionId))return;
      await Promise.allSettled(paths.slice(i,i+4).map(path=>api(path)));
      await new Promise(resolve=>setTimeout(resolve,25));
    }
  }

  window.addEventListener('online',()=>{state.online=true;updateSync();flushQueue();connectRealtime()});
  window.addEventListener('offline',()=>{state.online=false;disconnectRealtime();updateSync()});

  function initials(name=''){ return name.split(/\s+/).slice(0,2).map(x=>x[0]).join('').toUpperCase() || 'PO'; }
  function fmtDate(v){ if(!v)return '—'; const d=new Date(v); if(Number.isNaN(+d))return esc(v); return d.toLocaleString([], {month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit'}); }
  function fmtTime(v){ if(!v)return '—'; const d=new Date(v); return d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}); }
  function ago(v){ if(!v)return 'No live fix';const sec=Math.max(0,Math.round((Date.now()-new Date(v).getTime())/1000));if(sec<60)return `${sec}s ago`;if(sec<3600)return `${Math.round(sec/60)}m ago`;return `${Math.round(sec/3600)}h ago`; }
  function n(v,d=0){ const x=Number(v); return Number.isFinite(x)?x.toFixed(d).replace(/\.0$/,''):'0'; }
  function badge(text,kind){ return `<span class="badge ${kind||''}"><i class="dot"></i>${esc(text)}</span>`; }
  function statusKind(s=''){ s=s.toLowerCase(); if(/safe|delivered|operational|available|cleared|resolved|ok/.test(s))return'good'; if(/critical|active|low|overdue|due|maintenance|high/.test(s))return'danger'; if(/transit|moving|response|deployed|medium/.test(s))return'info'; return'warn'; }
  function roleCan(...roles){ return !!state.user && roles.includes(state.user.role); }

  function poleForRegion(region=''){
    const r=String(region||'').toLowerCase();
    if(r.includes('antarctic')||r.includes('south'))return 'south';
    if(r.includes('arctic')||r.includes('north'))return 'north';
    return 'south';
  }
  function currentPole(){
    const exp=state.expeditions.find(e=>e.id===state.expeditionId);
    return poleForRegion(exp?.region);
  }
  function applyPolarTheme(){ document.body.dataset.pole=currentPole(); }
  function destroyLiveMap(){
    if(state.liveMap){
      const map=state.liveMap; state.liveMap=null;
      try{ map.stop(); map.off(); map.remove(); }catch{}
    }
    state.liveMarkers={personnel:new Map(),vehicle:new Map()};
  }
  async function switchExpedition(id){
    const next=state.expeditions.find(e=>e.id===Number(id)); if(!next)return;
    stopPersonnelGps(false); stopVehicleSimulation(false); destroyLiveMap();
    state.prefetchGeneration++;clearFastCache();
    state.expeditionId=Number(next.id);
    localStorage.setItem('polarops_expedition',state.expeditionId);
    applyPolarTheme(); renderShell(); await renderView(); connectRealtime();setTimeout(prefetchMissionData,80);
  }
  async function switchPole(pole){
    const next=state.expeditions.find(e=>poleForRegion(e.region)===pole);
    if(!next){toast(pole==='north'?'No Arctic mission':'No Antarctic mission','Create or import an expedition for this polar region.','warn');return}
    await switchExpedition(next.id);
  }

  function renderLogin(){
    document.title='PolarOps — Sign in';
    $('#app').innerHTML=`
      <div class="login-shell">
        <section class="login-visual">
          <div class="login-brand brand-lockup"><div class="brand-mark">✦</div><div><strong>POLAROPS</strong><span>Expedition Operations Platform</span></div></div>
          <div class="login-copy">
            <span class="eyebrow">Integrated mission command</span>
            <h1>One operational picture for both polar regions.</h1>
            <p>Separate Arctic and Antarctic operations while keeping personnel, cargo, assets, vehicles and emergency response in one platform.</p>
            <div class="login-region-gallery">
              <div class="login-region-photo south"><img src="/media/antarctica-nasa.jpg" alt="Antarctica, NASA/JPL imagery"><span><strong>ANTARCTIC</strong>South polar operations</span></div>
              <div class="login-region-photo north"><img src="/media/arctic-nasa.jpg" alt="Arctic sea ice, NASA Scientific Visualization Studio"><span><strong>ARCTIC</strong>North polar operations</span></div>
            </div>
            <div class="polar-credit">Polar imagery: NASA/JPL · NASA Scientific Visualization Studio</div>
          </div>
        </section>
        <section class="login-panel">
          <form id="loginForm" class="login-card">
            <span class="eyebrow">Secure operations access</span><h2>Welcome back</h2><p>Sign in to your PolarOps command environment.</p>
            <div class="field"><label>Email</label><input id="loginEmail" type="email" autocomplete="username" required placeholder="commander@polarops.local" /></div>
            <div class="field"><label>Password</label><input id="loginPassword" type="password" autocomplete="current-password" required placeholder="Password" /></div>
            <button class="button primary block" type="submit">Sign in</button>
            <div class="login-help"><strong>Demo accounts</strong><div class="demo-grid">
              ${demoAccount('Commander','commander@polarops.local','PolarOps123!')}
              ${demoAccount('Logistics','logistics@polarops.local','Logistics123!')}
              ${demoAccount('Field lead','field@polarops.local','Field123!')}
            </div><p class="security-note">Demo credentials are seeded for immediate use. Change passwords and POLAROPS_SECRET before any shared deployment.</p></div>
          </form>
        </section>
      </div>`;
    $('#loginForm').addEventListener('submit',async e=>{e.preventDefault(); await doLogin($('#loginEmail').value,$('#loginPassword').value)});
    $$('.demo-account button').forEach(b=>b.addEventListener('click',()=>{ const row=b.closest('.demo-account'); doLogin(row.dataset.email,row.dataset.password); }));
  }
  function demoAccount(label,email,pw){ return `<div class="demo-account" data-email="${esc(email)}" data-password="${esc(pw)}"><div><strong>${esc(label)}</strong><span>${esc(email)}</span></div><button type="button">Use account</button></div>`; }
  async function doLogin(email,password){
    const btn=$('#loginForm button[type=submit]'); if(btn){btn.disabled=true;btn.textContent='Signing in…'}
    try{ const r=await api('/api/auth/login',{method:'POST',body:JSON.stringify({email,password})}); if(!r?.token||!r?.user)throw new Error('Invalid login response'); const signedInRole=r.user.role||'user'; state.token=r.token;state.user=r.user;localStorage.setItem(tokenKey,state.token);await bootAuthed(); if(state.user)toast('Signed in',`Role: ${signedInRole}`); }
    catch(e){toast('Sign-in failed',e.message,'danger'); if(btn){btn.disabled=false;btn.textContent='Sign in'}}
  }
  function logout(show=true){ stopPersonnelGps(false);stopVehicleSimulation(false);disconnectRealtime();clearInterval(state.fallbackTimer);state.prefetchGeneration++;clearFastCache();localStorage.removeItem(tokenKey);state.token='';state.user=null;state.expeditions=[];state.expeditionId=null; if(show)toast('Signed out');renderLogin(); }

  async function bootAuthed(){
    try{
      const preferred=Number(localStorage.getItem('polarops_expedition'))||0;
      const boot=await api(`/api/bootstrap${preferred?`?expedition_id=${preferred}`:''}`,{noFastCache:true});
      state.user=boot.user;state.expeditions=boot.expeditions||[];
      if(!state.expeditions.length)throw new Error('No expedition exists. Create one through the API or reseed demo mode.');
      state.expeditionId=Number(boot.expedition_id)||state.expeditions[0].id;
      localStorage.setItem('polarops_expedition',state.expeditionId);
      if(boot.dashboard)fastCacheSet(`/api/dashboard?expedition_id=${state.expeditionId}`,boot.dashboard);
      renderShell();await renderView();connectRealtime();startFallbackRefresh();setTimeout(prefetchMissionData,80);
    }catch(e){ toast('Unable to start platform',e.message,'danger',4500); logout(false); }
  }

  function renderShell(){
    const exp=state.expeditions.find(e=>e.id===state.expeditionId)||state.expeditions[0];
    $('#app').innerHTML=`<div class="shell">
      <aside class="sidebar">
        <div class="brand-lockup"><div class="brand-mark">✦</div><div><strong>POLAROPS</strong><span>Operations platform</span></div></div>
        <div class="polar-switch" aria-label="Polar region">
          <button data-pole="south" class="${poleForRegion(exp.region)==='south'?'active':''}"><span>▼</span><b>ANTARCTIC</b><small>SOUTH</small></button>
          <button data-pole="north" class="${poleForRegion(exp.region)==='north'?'active':''}"><span>▲</span><b>ARCTIC</b><small>NORTH</small></button>
        </div>
        <div class="mission-card"><small>ACTIVE MISSION</small><strong id="missionName">${esc(exp.name)}</strong><span id="missionRegion">${esc(exp.region)}</span></div>
        <nav class="nav">${navItems.map(([id,ico,label])=>`<button data-view="${id}" class="${id===state.view?'active':''}"><span class="ico">${ico}</span><span>${id==='network'?(poleForRegion(exp.region)==='north'?'Arctic Research':'Antarctic Network'):label}</span></button>`).join('')}</nav>
        <div class="sidebar-bottom"><div class="sync-box"><i class="sync-dot"></i><div><strong id="syncLabel">SYNC ONLINE</strong><span id="syncDetail">Central database connected</span></div></div>
          <div class="user-chip"><div class="avatar">${esc(initials(state.user.name))}</div><div><strong>${esc(state.user.name)}</strong><span>${esc(state.user.role)}</span></div><button class="logout-btn" title="Sign out">↪</button></div>
        </div>
      </aside>
      <main class="main"><header class="topbar"><div><span class="eyebrow" id="crumb">POLAR OPERATIONS / ${esc(exp.name)}</span><h1 id="pageTitle">Overview</h1><div id="pageSubtitle" class="page-subtitle">Live expedition status and exceptions.</div></div>
        <div class="topbar-actions"><button class="button ghost small global-search-btn" id="globalSearchButton" type="button">⌕ Search</button><span class="realtime-pill connecting" id="realtimePill"><i></i><span id="realtimeLabel">CONNECTING</span></span><select class="expedition-select" id="expeditionSelect">${state.expeditions.map(e=>`<option value="${e.id}" ${e.id===state.expeditionId?'selected':''}>${esc(e.name)}</option>`).join('')}</select><button class="sos-btn" id="globalSOS">⚠ TRIGGER SOS</button></div>
      </header><section id="view"></section></main></div>`;
    applyPolarTheme();
    $$('.nav button').forEach(b=>b.addEventListener('click',()=>navigate(b.dataset.view)));
    $$('.polar-switch button[data-pole]').forEach(b=>b.addEventListener('click',e=>{e.stopPropagation();switchPole(b.dataset.pole)}));
    $('.logout-btn').addEventListener('click',()=>logout());
    $('#expeditionSelect').addEventListener('change',e=>switchExpedition(Number(e.target.value)));
    $('#globalSOS').addEventListener('click',()=>openIncidentCreate());
    $('#globalSearchButton').addEventListener('click',()=>window.PolarOpsFeatures?.openGlobalSearch?.());
    updateSync();updateRealtimeIndicator();
  }

  async function navigate(view){ state.view=view; $$('.nav button').forEach(b=>b.classList.toggle('active',b.dataset.view===view)); await renderView(); }
  function setHeader(title,subtitle){ const exp=state.expeditions.find(e=>e.id===state.expeditionId); $('#pageTitle').textContent=title;$('#pageSubtitle').textContent=subtitle||'';$('#crumb').textContent=`POLAR OPERATIONS / ${exp?.name||''}`; document.title=`${title} — PolarOps`; }
  async function renderView(){
    if(state.renderInProgress){state.renderQueued=true;return}
    state.renderInProgress=true;
    const requestedView=state.view;
    const v=$('#view');if(!v){state.renderInProgress=false;return}
    const hadContent=!!v.children.length;
    const loadingTimer=setTimeout(()=>{
      if(!state.renderInProgress||state.view!==requestedView||!v.isConnected)return;
      v.classList.add('view-loading');
      if(!hadContent)v.innerHTML='<div class="panel"><div class="empty"><div><strong>Loading mission data…</strong>Connecting to central operations database.</div></div></div>';
    },120);
    try{
      destroyLiveMap();
      try{
        if(requestedView==='overview')await renderOverview(); else if(requestedView==='operations')await window.PolarOpsFeatures.renderOperations(); else if(requestedView==='personnel')await renderPersonnel(); else if(requestedView==='cargo')await renderCargo(); else if(requestedView==='inventory')await renderInventory(); else if(requestedView==='routes')await window.PolarOpsFeatures.renderRoutes(); else if(requestedView==='science')await window.PolarOpsFeatures.renderScience(); else if(requestedView==='comms')await window.PolarOpsFeatures.renderComms(); else if(requestedView==='readiness')await window.PolarOpsFeatures.renderReadiness(); else if(requestedView==='assets')await renderAssets(); else if(requestedView==='vehicles')await renderVehicles(); else if(requestedView==='emergency')await renderEmergency(); else if(requestedView==='environment')await renderEnvironment(); else if(requestedView==='network')await renderNetwork(); else if(requestedView==='alerts')await window.PolarOpsFeatures.renderAlerts(); else if(requestedView==='activity')await window.PolarOpsFeatures.renderAudit(); else if(requestedView==='settings')await renderSettings();
      }catch(e){ if(state.view===requestedView&&v.isConnected){v.innerHTML=`<div class="panel"><div class="empty"><div><strong>Could not load this section</strong>${esc(e.message)}</div></div></div>`;toast('Section load failed',e.message,'danger')} }
    }finally{
      clearTimeout(loadingTimer);
      if(v.isConnected)v.classList.remove('view-loading');
      state.renderInProgress=false;
      if(state.renderQueued||state.view!==requestedView){state.renderQueued=false;setTimeout(()=>renderView(),0)}
    }
  }

  async function renderOverview(){
    const selectedExp=state.expeditions.find(e=>e.id===state.expeditionId);
    const selectedPole=poleForRegion(selectedExp?.region);
    const [d,facilityResponse,arcticResponse]=await Promise.all([
      api(`/api/dashboard?expedition_id=${state.expeditionId}`),
      selectedPole==='south'
        ? api('/api/public/facilities?limit=1000').catch(()=>({items:[]}))
        : Promise.resolve({items:[]}),
      selectedPole==='north'
        ? api('/api/public/arctic-research-stations').catch(()=>({items:[],summary:{}}))
        : Promise.resolve({items:[],summary:{}})
    ]);
    const s=d.stats;
    const publicFacilities=(facilityResponse.items||[]).filter(f=>f.geographic_scope==='antarctic_treaty_area');
    const researchBases=publicFacilities.filter(f=>f.facility_type==='Station');
    const supportFacilities=publicFacilities.filter(f=>f.facility_type!=='Station');
    const arcticStations=arcticResponse.items||[];
    const arcticVerified=arcticStations.filter(f=>String(f.verification_status||'').startsWith('verified_')&&Number.isFinite(+f.latitude)&&Number.isFinite(+f.longitude)&&+f.latitude>=55);
    const arcticReference=arcticStations.filter(f=>!String(f.verification_status||'').startsWith('verified_')&&Number.isFinite(+f.latitude)&&Number.isFinite(+f.longitude)&&+f.latitude>=55);
    setHeader(`${d.expedition.name} — Command Dashboard`, `${d.expedition.start_date||'—'} – ${d.expedition.end_date||'—'}  |  ${d.expedition.region}`);
    const riskScore=Math.min(100,d.risks.reduce((a,r)=>a+(r.severity==='high'?28:14),0));
    const pole=poleForRegion(d.expedition.region), regionName=pole==='north'?'ARCTIC / NORTH POLAR OPERATIONS':'ANTARCTIC / SOUTH POLAR OPERATIONS';
    const regionImage=pole==='north'?'/media/arctic-nasa.jpg':'/media/antarctica-nasa.jpg';
    $('#view').innerHTML=`
      <div class="region-banner ${pole}" style="background-image:linear-gradient(90deg,rgba(5,39,66,.88),rgba(5,39,66,.30)),url('${regionImage}')">
        <div><span class="region-kicker">${regionName}</span><strong>${esc(d.expedition.name)}</strong><small>${esc(d.expedition.region)} · Real map + live operational overlays</small></div>
        <div class="region-source">${pole==='north'?'NASA SVS Arctic sea-ice imagery':'NASA/JPL Antarctic imagery'}</div>
      </div>
      <div class="stats">
        ${stat('Personnel',`${s.personnel_total-s.personnel_overdue} / ${s.personnel_total}`,s.personnel_overdue?`${s.personnel_overdue} check-in overdue`:'Safe / accounted for','◎',s.personnel_overdue?'danger':'good')}
        ${stat('Cargo',`${s.cargo_delivered} / ${s.cargo_total}`,'Delivered','▣','info')}
        ${stat('Fuel',`${s.fuel_avg}%`,'Average reserve','◫',s.fuel_avg<35?'danger':'warn')}
        ${stat('Inventory alerts',s.inventory_alerts,s.inventory_alerts?'Items below minimum':'No low stock','▤',s.inventory_alerts?'danger':'good')}
        ${stat('Vehicles',`${s.vehicles_operational} / ${s.vehicles_total}`,'Operational','▱',s.vehicles_operational<s.vehicles_total?'warn':'good')}
        ${stat('Active incidents',s.active_incidents,s.active_incidents?'Response required':'No active incidents','△',s.active_incidents?'danger':'good')}
      </div>
      <div class="grid-2">
        <div class="panel dashboard-map-panel" id="dashboardMapPanel">
          <div class="panel-head"><div><h2>Expedition Map</h2><p>${pole==='south'?'Live mission operations plus verified COMNAP Antarctic research bases.':`Live mission operations plus ${arcticVerified.length} mapped, independently verified Arctic research sites.`}</p></div><div class="panel-actions"><span class="badge info"><i class="dot"></i>LIVE</span>${pole==='south'?`<span class="badge violet">${researchBases.length} RESEARCH BASES</span>`:`<span class="badge violet">${arcticVerified.length} VERIFIED SITES</span>`}<button class="button ghost small map-fullscreen-btn" id="mapFullscreen" type="button" title="Open map fullscreen">⛶ Fullscreen</button></div></div>
          <div id="liveMissionMap" class="live-mission-map" role="application" aria-label="Live expedition map"></div>
          <div class="live-map-note"><span>● Live GPS updates through PolarOps WebSockets</span><span>${pole==='south'?`COMNAP Nov 2024 · ${researchBases.length} research bases · ${supportFacilities.length} other Treaty-area facilities`:`Arctic reference · ${arcticVerified.length} verified mapped · ${arcticReference.length} reference-only mapped`}</span></div>
        </div>
        <div class="dashboard-side">
          <div class="panel">
            <div class="panel-head"><div><h2>Recent Activity</h2><p>Latest cross-module events.</p></div><button class="button ghost small" data-go="activity">View all</button></div>
            <div class="activity-list">${d.activity.slice(0,6).map(a=>activityRow(a)).join('')||'<div class="empty" style="min-height:130px"><div><strong>No recent activity</strong>Mission events will appear here.</div></div>'}</div>
          </div>
          <div class="panel">
            <div class="panel-head"><div><h2>Top Operational Risks</h2><p>Exceptions requiring attention.</p></div></div>
            <div class="risk-list">${d.risks.length?d.risks.slice(0,5).map(r=>`<div class="risk-item ${r.severity}"><div class="risk-icon">!</div><div><strong>${esc(r.title)}</strong><span>${esc(r.detail)}</span></div></div>`).join(''):`<div class="risk-summary"><div class="risk-ring">${riskScore}</div><div><strong>Controlled</strong><p>No current threshold exceptions.</p></div></div>`}</div>
          </div>
        </div>
      </div>`;
    $$('[data-go]').forEach(b=>b.onclick=()=>navigate(b.dataset.go));
    initLiveMissionMap(d.locations,d.vehicles,d.personnel,d.expedition,publicFacilities,arcticStations);
    bindMapFullscreen();
  }

  function polarMarker(kind,label){
    const glyph=kind==='vehicle'?'▣':kind==='person'?'●':kind==='camp'?'▲':'⌂';
    return L.divIcon({className:'polar-leaflet-icon',html:`<span class="pm ${kind}">${glyph}</span><em>${esc(label)}</em>`,iconSize:[120,34],iconAnchor:[17,17]});
  }
  function initLiveMissionMap(locations,vehicles,personnel,expedition,publicFacilities=[],arcticStations=[]){
    const el=$('#liveMissionMap');
    if(!el||!window.L)return;
    destroyLiveMap();
    const fixed=locations.filter(x=>Number.isFinite(Number(x.latitude))&&Number.isFinite(Number(x.longitude)));
    const vehiclePoints=vehicles.filter(v=>Number.isFinite(Number(v.latitude))&&Number.isFinite(Number(v.longitude)));
    const peoplePoints=personnel.filter(p=>Number.isFinite(Number(p.live_latitude))&&Number.isFinite(Number(p.live_longitude)));
    const researchBases=publicFacilities.filter(f=>f.facility_type==='Station'&&Number.isFinite(Number(f.latitude))&&Number.isFinite(Number(f.longitude)));
    const supportFacilities=publicFacilities.filter(f=>f.facility_type!=='Station'&&Number.isFinite(Number(f.latitude))&&Number.isFinite(Number(f.longitude)));
    const arcticVerified=arcticStations.filter(f=>String(f.verification_status||'').startsWith('verified_')&&Number.isFinite(+f.latitude)&&Number.isFinite(+f.longitude)&&+f.latitude>=55);
    const arcticReference=arcticStations.filter(f=>!String(f.verification_status||'').startsWith('verified_')&&Number.isFinite(+f.latitude)&&Number.isFinite(+f.longitude)&&+f.latitude>=55);
    const missionCoords=[...fixed.map(x=>[+x.latitude,+x.longitude]),...vehiclePoints.map(x=>[+x.latitude,+x.longitude]),...peoplePoints.map(x=>[+x.live_latitude,+x.live_longitude])];
    const pole=poleForRegion(expedition?.region);
    const fallback=pole==='north'?[78.7,15]:[-75,40];
    state.liveMap=L.map(el,{zoomControl:true,attributionControl:false,worldCopyJump:false,minZoom:2,maxZoom:18,zoomAnimation:false,fadeAnimation:false,markerZoomAnimation:false}).setView(fallback,pole==='north'?4:3);
    const satellite=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{
      maxZoom:18,updateWhenIdle:true,keepBuffer:1
    });
    const topo=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',{
      maxZoom:18,updateWhenIdle:true,keepBuffer:1
    });
    const missionLayer=L.layerGroup().addTo(state.liveMap);
    const researchLayer=L.layerGroup();
    const supportLayer=L.layerGroup();
    const arcticVerifiedLayer=L.layerGroup();
    const arcticReferenceLayer=L.layerGroup();
    satellite.addTo(state.liveMap);
    if(pole==='south'&&researchBases.length)researchLayer.addTo(state.liveMap);
    if(pole==='north'&&arcticVerified.length)arcticVerifiedLayer.addTo(state.liveMap);
    const overlays={'Mission operations':missionLayer};
    if(pole==='south'&&researchBases.length)overlays[`Research bases (${researchBases.length})`]=researchLayer;
    if(pole==='south'&&supportFacilities.length)overlays[`Other facilities (${supportFacilities.length})`]=supportLayer;
    if(pole==='north'&&arcticVerified.length)overlays[`Verified research sites (${arcticVerified.length})`]=arcticVerifiedLayer;
    if(pole==='north'&&arcticReference.length)overlays[`Reference-only sites (${arcticReference.length})`]=arcticReferenceLayer;
    L.control.layers({'Satellite':satellite,'Topographic':topo},overlays,{position:'topright',collapsed:false}).addTo(state.liveMap);
    addMapDataControl(state.liveMap);
    const base=fixed.find(x=>/base|station|hub/i.test(`${x.name} ${x.type}`))||fixed[0];
    if(base){
      fixed.filter(x=>x.id!==base.id).forEach(x=>L.polyline([[+base.latitude,+base.longitude],[+x.latitude,+x.longitude]],{color:'#0b78e3',weight:2,dashArray:'7 7',opacity:.65}).addTo(missionLayer));
    }
    fixed.forEach(x=>{
      const kind=/camp/i.test(x.type)?'camp':'location';
      L.marker([+x.latitude,+x.longitude],{icon:polarMarker(kind,x.name)}).addTo(missionLayer)
        .bindPopup(`<strong>${esc(x.name)}</strong><br>${esc(x.type)}<br><small>${n(x.latitude,5)}, ${n(x.longitude,5)}</small>`);
    });
    vehiclePoints.forEach(v=>{
      const marker=L.marker([+v.latitude,+v.longitude],{icon:polarMarker('vehicle',`${v.code} ${v.telemetry_recorded_at?'LIVE':''}`)}).addTo(missionLayer)
        .bindPopup(`<strong>${esc(v.code)} · ${esc(v.name)}</strong><br>${esc(v.status)} · ${n(v.fuel_percent)}% fuel${v.telemetry_recorded_at?`<br><b>LIVE GPS</b> · ${ago(v.telemetry_recorded_at)}`:''}`);
      state.liveMarkers.vehicle.set(Number(v.id),marker);
    });
    peoplePoints.forEach(p=>{
      const marker=L.marker([+p.live_latitude,+p.live_longitude],{icon:polarMarker('person',`${p.name} LIVE`)}).addTo(missionLayer)
        .bindPopup(`<strong>${esc(p.name)}</strong><br>${esc(p.role)}<br><b>LIVE GPS</b> · ${ago(p.telemetry_recorded_at)}`);
      state.liveMarkers.personnel.set(Number(p.id),marker);
    });
    if(pole==='south'){
      researchBases.forEach(f=>{
        const open=String(f.status||'').toLowerCase()==='open';
        L.circleMarker([+f.latitude,+f.longitude],{
          radius:5,weight:1.5,color:open?'#6b46ce':'#d48a16',
          fillColor:open?'#8a63df':'#f0a52a',fillOpacity:.88
        }).addTo(researchLayer)
          .bindTooltip(esc(f.name),{direction:'top',sticky:true,opacity:.95})
          .bindPopup(`<div class="public-facility-popup"><span class="popup-kicker">COMNAP RESEARCH BASE</span><strong>${esc(f.name)}</strong><br>${esc(f.country||f.programme||'Antarctic programme')}<br>${esc(f.seasonality||'')} · ${esc(f.status||'Status not supplied')}<br><small>${n(f.latitude,5)}, ${n(f.longitude,5)}</small><br><button class="popup-profile-btn" onclick="window.PolarOpsFeatures.openFacilityProfile('south',${f.id})">Facility profile</button></div>`);
      });
      supportFacilities.forEach(f=>{
        L.circleMarker([+f.latitude,+f.longitude],{
          radius:4,weight:1,color:'#0f7e9f',fillColor:'#25a9c7',fillOpacity:.72
        }).addTo(supportLayer)
          .bindTooltip(esc(f.name),{direction:'top',sticky:true,opacity:.95})
          .bindPopup(`<div class="public-facility-popup"><span class="popup-kicker">COMNAP ${esc(String(f.facility_type||'FACILITY').toUpperCase())}</span><strong>${esc(f.name)}</strong><br>${esc(f.country||f.programme||'Antarctic programme')}<br>${esc(f.seasonality||'')} · ${esc(f.status||'Status not supplied')}<br><small>${n(f.latitude,5)}, ${n(f.longitude,5)}</small></div>`);
      });
    }
    if(pole==='north'){
      arcticVerified.forEach(f=>{
        L.circleMarker([+f.latitude,+f.longitude],{radius:5,weight:1.5,color:'#6b46ce',fillColor:'#8a63df',fillOpacity:.88})
          .addTo(arcticVerifiedLayer)
          .bindTooltip(esc(f.name),{direction:'top',sticky:true,opacity:.95})
          .bindPopup(`<div class="public-facility-popup"><span class="popup-kicker">VERIFIED ARCTIC RESEARCH SITE</span><strong>${esc(f.name)}</strong><br>${esc(f.location||'Location not supplied')}<br>${esc(f.operating_country||'Operator/country not supplied')}<br><small>${esc(f.verification_source||'Current verification source')}</small><br><small>${n(f.latitude,5)}, ${n(f.longitude,5)} · ${esc(f.coordinate_precision||'reference coordinates')}</small>${f.verification_url?`<br><a href="${esc(f.verification_url)}" target="_blank" rel="noopener">Verification proof ↗</a>`:''}<br><button class="popup-profile-btn" onclick="window.PolarOpsFeatures.openFacilityProfile('north',${f.id})">Facility profile</button></div>`);
      });
      arcticReference.forEach(f=>{
        L.circleMarker([+f.latitude,+f.longitude],{radius:4,weight:1.2,color:'#7d8994',fillColor:'#a8b1b8',fillOpacity:.72})
          .addTo(arcticReferenceLayer)
          .bindTooltip(`${esc(f.name)} · reference only`,{direction:'top',sticky:true,opacity:.95})
          .bindPopup(`<div class="public-facility-popup"><span class="popup-kicker">REFERENCE — NOT VERIFIED CURRENT</span><strong>${esc(f.name)}</strong><br>${esc(f.location||'Location not supplied')}<br>${esc(f.operating_country||'Country not supplied')}<br><small>${esc(f.verification_note||'Reference data only')}</small><br><small>${n(f.latitude,5)}, ${n(f.longitude,5)} · ${esc(f.coordinate_precision||'reference coordinates')}</small></div>`);
      });
    }
    addMissionMapLegend(state.liveMap,pole,{
      research: pole==='south'?researchBases.length:arcticVerified.length,
      support: pole==='south'?supportFacilities.length:0,
      reference: pole==='north'?arcticReference.length:0
    });
    const overviewCoords=pole==='south'&&researchBases.length
      ? [...missionCoords,...researchBases.map(f=>[+f.latitude,+f.longitude])]
      : pole==='north'&&arcticVerified.length
        ? [...missionCoords,...arcticVerified.map(f=>[+f.latitude,+f.longitude])]
        : missionCoords;
    if(overviewCoords.length===1)state.liveMap.setView(overviewCoords[0],7);
    else if(overviewCoords.length>1)state.liveMap.fitBounds(L.latLngBounds(overviewCoords).pad(.08),{maxZoom:pole==='south'?4:pole==='north'?4:7,animate:false});
    setTimeout(()=>state.liveMap?.invalidateSize(),50);
  }

  function addMapDataControl(){
    // Map-data attribution button intentionally removed from all PolarOps maps.
  }

  function addMissionMapLegend(map,pole,counts={}){
    if(!map||!window.L)return;
    const control=L.control({position:'bottomleft'});
    control.onAdd=()=>{
      const div=L.DomUtil.create('div','mission-map-legend');
      div.innerHTML=`<strong>Map legend</strong>
        <span><i class="legend-symbol location">⌂</i>Mission base / location</span>
        <span><i class="legend-symbol vehicle">▣</i>Vehicle / mobile asset</span>
        <span><i class="legend-symbol person">●</i>Personnel live GPS</span>
        ${counts.research?`<span><i class="legend-dot research"></i>${pole==='south'?'COMNAP research base':'Verified research site'}</span>`:''}
        ${pole==='south'&&counts.research?'<span><i class="legend-dot closed"></i>Temporarily closed base</span>':''}
        ${counts.support?'<span><i class="legend-dot support"></i>Other public facility</span>':''}
        ${counts.reference?'<span><i class="legend-dot reference"></i>Reference only — not verified current</span>':''}`;
      L.DomEvent.disableClickPropagation(div);
      return div;
    };
    control.addTo(map);
  }

  function bindPanelMapFullscreen(panelId,buttonId){
    const panel=$('#'+panelId),button=$('#'+buttonId);
    if(!panel||!button)return;
    const refresh=()=>{
      const active=document.fullscreenElement===panel||panel.classList.contains('map-panel-fullscreen');
      button.textContent=active?'⛶ Exit fullscreen':'⛶ Fullscreen';
      button.title=active?'Exit fullscreen map':'Open map fullscreen';
      setTimeout(()=>state.liveMap?.invalidateSize(),80);
    };
    button.onclick=async()=>{
      try{
        if(document.fullscreenEnabled&&panel.requestFullscreen){
          if(document.fullscreenElement===panel)await document.exitFullscreen();
          else await panel.requestFullscreen();
        }else{
          panel.classList.toggle('map-panel-fullscreen');
          document.body.classList.toggle('map-fullscreen-open',panel.classList.contains('map-panel-fullscreen'));
          refresh();
        }
      }catch{
        panel.classList.toggle('map-panel-fullscreen');
        document.body.classList.toggle('map-fullscreen-open',panel.classList.contains('map-panel-fullscreen'));
        refresh();
      }
    };
    document.onfullscreenchange=refresh;
  }
  function bindMapFullscreen(){bindPanelMapFullscreen('dashboardMapPanel','mapFullscreen')}

  function stat(label,value,detail,ico,kind=''){return `<div class="stat ${kind}"><div class="stat-head"><span class="stat-label">${label}</span><span class="stat-icon">${ico}</span></div><div class="stat-value">${value}</div><div class="stat-detail">${esc(detail)}</div></div>`}
  function activityRow(a){return `<div class="activity-item"><div class="activity-icon">${({personnel:'◎',cargo:'▣',inventory:'▤',vehicle:'▱',incident:'△',asset:'◇',location:'⌖'})[a.category]||'•'}</div><div><strong>${esc(a.message)}</strong><span>${a.user_name?`By ${esc(a.user_name)}`:'System event'}</span></div><time>${fmtTime(a.created_at)}</time></div>`}

  function renderMap(locations,vehicles,personnel=[]){
    const fixed=locations.filter(x=>Number.isFinite(Number(x.latitude))&&Number.isFinite(Number(x.longitude)));
    const vehiclePoints=vehicles.filter(v=>Number.isFinite(Number(v.latitude))&&Number.isFinite(Number(v.longitude)));
    const peoplePoints=personnel.filter(p=>Number.isFinite(Number(p.live_latitude))&&Number.isFinite(Number(p.live_longitude)));
    const coords=[...fixed.map(x=>[+x.latitude,+x.longitude]),...vehiclePoints.map(x=>[+x.latitude,+x.longitude]),...peoplePoints.map(x=>[+x.live_latitude,+x.live_longitude])];
    if(!coords.length)return '<div class="empty"><div><strong>No mapped positions</strong>Add mission coordinates or start a live GPS feed.</div></div>';
    const lats=coords.map(x=>x[0]),lons=coords.map(x=>x[1]);let minLat=Math.min(...lats),maxLat=Math.max(...lats),minLon=Math.min(...lons),maxLon=Math.max(...lons);if(minLat===maxLat){minLat-=.1;maxLat+=.1}if(minLon===maxLon){minLon-=.1;maxLon+=.1}
    const pos=(lat,lon)=>({x:9+82*((lon-minLon)/(maxLon-minLon)),y:10+75*(1-(lat-minLat)/(maxLat-minLat))});
    let lines='';const base=fixed.find(x=>/base|station/i.test(x.name))||fixed[0];if(base){const bp=pos(+base.latitude,+base.longitude);fixed.filter(x=>x.id!==base.id).slice(0,5).forEach(x=>{const q=pos(+x.latitude,+x.longitude),dx=q.x-bp.x,dy=q.y-bp.y,len=Math.sqrt(dx*dx+dy*dy),ang=Math.atan2(dy,dx)*180/Math.PI;lines+=`<i class="map-line" style="left:${bp.x}%;top:${bp.y}%;width:${len}%;transform:rotate(${ang}deg)"></i>`})}
    const points=fixed.map(x=>{const q=pos(+x.latitude,+x.longitude);return `<div class="map-point" style="left:${q.x}%;top:${q.y}%"><div class="map-dot"><span>${/camp/i.test(x.type)?'▲':/station/i.test(x.type)?'⌂':'⌖'}</span></div><div class="map-label"><strong>${esc(x.name)}</strong><small>${esc(x.type)}</small></div></div>`}).join('');
    const vpoints=vehiclePoints.map(v=>{const q=pos(+v.latitude,+v.longitude),live=!!v.telemetry_recorded_at;return `<div class="map-point vehicle ${live?'live':''}" style="left:${q.x}%;top:${q.y}%"><div class="map-dot"><span>▱</span></div><div class="map-label"><strong>${esc(v.code)} ${live?'• LIVE':''}</strong><small>${live?ago(v.telemetry_recorded_at):esc(v.location_name||v.status)} · ${n(v.fuel_percent)}% fuel</small></div></div>`}).join('');
    const ppoints=peoplePoints.map(p=>{const q=pos(+p.live_latitude,+p.live_longitude);return `<div class="map-point person live" style="left:${q.x}%;top:${q.y}%"><div class="map-dot"><span>◎</span></div><div class="map-label"><strong>${esc(p.name)} • LIVE</strong><small>${ago(p.telemetry_recorded_at)}${p.accuracy_m!=null?` · ±${n(p.accuracy_m)}m`:''}</small></div></div>`}).join('');
    return `<div class="map"><span class="map-grid-label a">LIVE OPS</span><span class="map-grid-label b">POLAR GRID</span>${lines}${points}${vpoints}${ppoints}<div class="map-legend"><span><i></i> Location</span><span><i class="v"></i> Vehicle</span><span><i class="p"></i> Person GPS</span></div></div>`;
  }

  async function loadLocations(){ return api(`/api/locations?expedition_id=${state.expeditionId}`); }
  function locationOptions(locations,selected){ return `<option value="">Unassigned</option>`+locations.map(l=>`<option value="${l.id}" ${Number(selected)===l.id?'selected':''}>${esc(l.name)}</option>`).join(''); }
  function modal(title,subtitle,body,wide=false){ $('#modalRoot').innerHTML=`<div class="modal-backdrop"><div class="modal ${wide?'wide':''}"><div class="modal-head"><div><span class="eyebrow">POLAROPS WORKFLOW</span><h2>${esc(title)}</h2>${subtitle?`<p>${esc(subtitle)}</p>`:''}</div><button class="modal-close">×</button></div>${body}</div></div>`; $('.modal-close').onclick=closeModal; $('.modal-backdrop').onclick=e=>{if(e.target.classList.contains('modal-backdrop'))closeModal()}; }
  function closeModal(){ $('#modalRoot').innerHTML=''; if(state.deferredRealtime){state.deferredRealtime=false;clearTimeout(state.realtimeRender);if(state.view!=='overview')state.realtimeRender=setTimeout(()=>renderView(),250)} }
  function formVal(form,name){ return form.elements[name]?.value ?? ''; }
  function numOrNull(v){ return v===''?null:Number(v); }

  async function renderPersonnel(){
    setHeader('Personnel','Accountability, live worker positions, authorized roster feeds and field check-ins.');
    const [people,locs,feed]=await Promise.all([
      api(`/api/personnel?expedition_id=${state.expeditionId}`),
      loadLocations(),
      api(`/api/integrations/workers/status?expedition_id=${state.expeditionId}`).catch(()=>({configured:false,workers:0}))
    ]);
    const external=people.filter(p=>(p.source||'').startsWith('feed:')).length;
    const synthetic=people.filter(p=>+p.is_synthetic===1).length;
    const live=people.filter(p=>p.telemetry_recorded_at).length;
    $('#view').innerHTML=`
      <div class="source-strip">
        <div><span>Roster records</span><strong>${people.length}</strong></div>
        <div><span>Live GPS workers</span><strong>${live}</strong></div>
        <div><span>Authorized-feed workers</span><strong>${external}</strong></div>
        <div><span>Demo/synthetic</span><strong>${synthetic}</strong></div>
        <div class="source-note"><strong>${feed.configured?'AUTHORIZED FEED CONFIGURED':'NO EXTERNAL WORKER FEED'}</strong><span>${feed.configured?`Source: ${esc(feed.url_host||'operator endpoint')}`:'Public live worker rosters are not scraped. Connect an operator-authorized feed or use worker check-in/GPS.'}</span></div>
      </div>
      <div class="panel"><div class="panel-head"><div><h2>Personnel Roster</h2><p>${people.length} people registered to this expedition. Live positions come only from authorized devices/feeds.</p></div><div class="panel-actions"><input class="search" id="peopleSearch" placeholder="Search personnel…">${roleCan('commander','logistics')?'<button class="button secondary" id="syncWorkers">↻ Sync authorized workers</button><button class="button primary" id="addPerson">+ Add person</button>':''}</div></div><div class="table-wrap"><table><thead><tr><th>Name</th><th>Role / Team</th><th>Location</th><th>Status</th><th>Last check-in</th><th>Source</th><th>Clearance</th><th>Actions</th></tr></thead><tbody id="peopleRows">${people.map(p=>personRow(p)).join('')}</tbody></table></div></div>`;
    const paint=(q='')=>{$('#peopleRows').innerHTML=people.filter(p=>[p.name,p.role,p.team,p.location_name,p.status,p.source].join(' ').toLowerCase().includes(q.toLowerCase())).map(personRow).join('')||'<tr><td colspan="8">No matching personnel.</td></tr>';wirePeople()};
    $('#peopleSearch').oninput=e=>paint(e.target.value);
    if($('#addPerson'))$('#addPerson').onclick=()=>openPersonForm(null,locs);
    if($('#syncWorkers'))$('#syncWorkers').onclick=async()=>{
      if(!feed.configured){
        modal('Authorized worker feed not configured','PolarOps intentionally does not scrape public worker locations.',`<div class="notice-card"><strong>Configure an operator-controlled JSON feed</strong><p>Set <code>POLAROPS_OPERATIONS_FEED_URL</code> and optionally <code>POLAROPS_OPERATIONS_FEED_TOKEN</code> in your environment. The feed can provide locations/camps, worker roster fields, latitude/longitude and timestamps.</p><p>This keeps worker location data consent-based and organization-controlled.</p></div><div class="modal-actions"><button class="button primary" data-cancel>Close</button></div>`,true);$('[data-cancel]').onclick=closeModal;return;
      }
      const b=$('#syncWorkers');b.disabled=true;b.textContent='Syncing…';
      try{const r=await api(`/api/integrations/workers/sync?expedition_id=${state.expeditionId}`,{method:'POST'});toast('Authorized worker feed synchronized',`${r.locations||0} locations · ${r.created} added · ${r.updated} updated · ${r.telemetry} live positions`);await renderPersonnel()}
      catch(err){toast('Worker feed sync failed',err.message,'danger',5000);b.disabled=false;b.textContent='↻ Sync authorized workers'}
    };
    function wirePeople(){ $$('[data-checkin]').forEach(b=>b.onclick=()=>openCheckin(people.find(p=>p.id===+b.dataset.checkin),locs)); $$('[data-live-gps]').forEach(b=>b.onclick=()=>togglePersonnelGps(people.find(p=>p.id===+b.dataset.liveGps))); $$('[data-edit-person]').forEach(b=>b.onclick=()=>openPersonForm(people.find(p=>p.id===+b.dataset.editPerson),locs)); }
    wirePeople();
  }
  function personRow(p){
    const due=p.checkin_minutes!=null&&p.checkin_minutes>30,tracking=state.gpsPersonnelId===p.id;
    const source=+p.is_synthetic===1?badge('Demo data','warn'):(p.source||'').startsWith('feed:')?badge('Authorized feed','info'):badge('Manual / device','good');
    return `<tr><td><strong>${esc(p.name)}</strong><small>#P-${String(p.id).padStart(3,'0')}${p.external_id?` · ${esc(p.external_id)}`:''}</small></td><td><strong>${esc(p.role)}</strong><small>${esc(p.team||'No team')}</small></td><td><strong>${esc(p.location_name||'Unassigned')}</strong><small>${p.telemetry_recorded_at?`GPS ${ago(p.telemetry_recorded_at)} · ${esc(p.telemetry_source||'gps')}`:'No live GPS'}</small></td><td>${badge(p.status,due?'danger':statusKind(p.status))}</td><td><strong>${p.checkin_minutes==null?'—':`${p.checkin_minutes} min ago`}</strong><small>${fmtDate(p.last_checkin)}</small></td><td>${source}</td><td>${badge(p.clearance_status,statusKind(p.clearance_status))}</td><td><div class="row-actions"><button class="icon-btn" data-checkin="${p.id}">Check in</button><button class="icon-btn ${tracking?'live-action':''}" data-live-gps="${p.id}">${tracking?'Stop GPS':'Live GPS'}</button>${roleCan('commander','logistics')?`<button class="icon-btn" data-edit-person="${p.id}">Edit</button>`:''}</div></td></tr>`;
  }
  function openPersonForm(p,locs){
    modal(p?'Edit personnel':'Add personnel',p?'Update team, location and readiness status.':'Register a person to the current expedition.',`<form id="personForm"><div class="form-grid"><div class="field"><label>Name</label><input name="name" required value="${esc(p?.name||'')}"></div><div class="field"><label>Role</label><input name="role" required value="${esc(p?.role||'')}"></div><div class="field"><label>Team</label><input name="team" value="${esc(p?.team||'')}"></div><div class="field"><label>Location</label><select name="location_id">${locationOptions(locs,p?.location_id)}</select></div><div class="field"><label>Status</label><select name="status">${['Safe','Moving','Check-in due','Evacuating','Unknown'].map(x=>`<option ${p?.status===x?'selected':''}>${x}</option>`).join('')}</select></div><div class="field"><label>Clearance</label><select name="clearance_status">${['Cleared','Pending','Restricted'].map(x=>`<option ${p?.clearance_status===x?'selected':''}>${x}</option>`).join('')}</select></div><div class="field full"><label>Contact</label><input name="contact" value="${esc(p?.contact||'')}" placeholder="Optional radio / satphone identifier"></div></div><div class="modal-actions"><button type="button" class="button ghost" data-cancel>Cancel</button><button class="button primary">${p?'Save changes':'Add person'}</button></div></form>`);
    $('[data-cancel]').onclick=closeModal; $('#personForm').onsubmit=async e=>{e.preventDefault();const f=e.currentTarget,payload={name:formVal(f,'name'),role:formVal(f,'role'),team:formVal(f,'team'),location_id:numOrNull(formVal(f,'location_id')),status:formVal(f,'status'),contact:formVal(f,'contact'),clearance_status:formVal(f,'clearance_status')};try{if(p)await api(`/api/personnel/${p.id}`,{method:'PATCH',body:JSON.stringify(payload)});else await api('/api/personnel',{method:'POST',body:JSON.stringify({expedition_id:state.expeditionId,...payload})});closeModal();toast(p?'Personnel updated':'Personnel added');renderPersonnel()}catch(err){toast('Save failed',err.message,'danger')}};
  }
  function openCheckin(p,locs){ modal('Personnel check-in',`${p.name} · ${p.role}`,`<form id="checkinForm"><div class="form-grid"><div class="field"><label>Location</label><select name="location_id">${locationOptions(locs,p.location_id)}</select></div><div class="field"><label>Status</label><select name="status">${['Safe','Moving','Evacuating','Unknown'].map(x=>`<option ${p.status===x?'selected':''}>${x}</option>`).join('')}</select></div></div><div class="modal-actions"><button type="button" class="button ghost" data-cancel>Cancel</button><button class="button good">Confirm check-in</button></div></form>`);$('[data-cancel]').onclick=closeModal;$('#checkinForm').onsubmit=async e=>{e.preventDefault();const f=e.currentTarget;try{await api(`/api/personnel/${p.id}/checkin`,{method:'POST',body:JSON.stringify({location_id:numOrNull(formVal(f,'location_id')),status:formVal(f,'status')})});closeModal();toast('Check-in recorded',p.name);renderPersonnel()}catch(err){toast('Check-in failed',err.message,'danger')}}; }

  function stopPersonnelGps(show=true){
    if(state.gpsWatchId!=null&&navigator.geolocation)navigator.geolocation.clearWatch(state.gpsWatchId);
    const previous=state.gpsPersonnelId;state.gpsWatchId=null;state.gpsPersonnelId=null;state.gpsLastSent=0;
    if(show&&previous)toast('Live GPS stopped');
  }
  function togglePersonnelGps(person){
    if(!person)return;
    if(state.gpsPersonnelId===person.id){stopPersonnelGps();renderPersonnel();return}
    if(!navigator.geolocation){toast('GPS unavailable','This browser does not expose geolocation.','danger');return}
    stopPersonnelGps(false);state.gpsPersonnelId=person.id;state.gpsLastSent=0;toast('Starting live GPS',`${person.name} · allow location access in the browser.`,'info',4000);renderPersonnel();
    state.gpsWatchId=navigator.geolocation.watchPosition(async position=>{
      const now=Date.now();if(now-state.gpsLastSent<8000)return;state.gpsLastSent=now;
      const c=position.coords;
      try{await api('/api/telemetry/position',{method:'POST',body:JSON.stringify({expedition_id:state.expeditionId,entity_type:'personnel',entity_id:person.id,latitude:c.latitude,longitude:c.longitude,altitude_m:c.altitude,accuracy_m:c.accuracy,speed_kph:c.speed==null?null:Math.max(0,c.speed*3.6),heading:c.heading,source:'browser-gps',recorded_at:new Date(position.timestamp).toISOString()})})}
      catch(err){if(navigator.onLine)toast('GPS upload failed',err.message,'danger')}
    },err=>{stopPersonnelGps(false);toast('GPS permission/error',err.message,'danger',4500);if(state.view==='personnel')renderPersonnel()},{enableHighAccuracy:true,maximumAge:5000,timeout:15000});
  }

  async function renderCargo(){
    setHeader('Cargo','Chain-of-custody tracking from registration through field delivery.'); const [items,locs]=await Promise.all([api(`/api/cargo?expedition_id=${state.expeditionId}`),loadLocations()]);
    $('#view').innerHTML=`<div class="panel"><div class="panel-head"><div><h2>Cargo Registry</h2><p>Track critical consignments, current location and destination.</p></div><div class="panel-actions"><input class="search" id="cargoSearch" placeholder="Cargo ID or item…"><button class="button secondary" id="scanCargo">▦ Scan / enter code</button>${roleCan('commander','logistics')?'<button class="button primary" id="addCargo">+ Add cargo</button>':''}</div></div><div class="table-wrap"><table><thead><tr><th>Cargo ID</th><th>Item</th><th>Priority</th><th>Current location</th><th>Destination</th><th>Status</th><th>Qty</th><th>Actions</th></tr></thead><tbody id="cargoRows">${items.map(c=>cargoRow(c)).join('')}</tbody></table></div></div>`;
    const paint=(q='')=>{$('#cargoRows').innerHTML=items.filter(c=>[c.code,c.name,c.location_name,c.destination_name,c.status].join(' ').toLowerCase().includes(q.toLowerCase())).map(cargoRow).join('')||'<tr><td colspan="8">No matching cargo.</td></tr>';wire()};
    function wire(){ $$('[data-move-cargo]').forEach(b=>b.onclick=()=>openCargoMove(items.find(x=>x.id===+b.dataset.moveCargo),locs)); $$('[data-cargo-history]').forEach(b=>b.onclick=()=>openCargoHistory(items.find(x=>x.id===+b.dataset.cargoHistory))); $$('[data-edit-cargo]').forEach(b=>b.onclick=()=>openCargoEdit(items.find(x=>x.id===+b.dataset.editCargo),locs)); }
    $('#cargoSearch').oninput=e=>paint(e.target.value);$('#scanCargo').onclick=()=>openCargoScanner(items,locs);if($('#addCargo'))$('#addCargo').onclick=()=>openCargoCreate(locs);wire();
  }
  function cargoRow(c){return `<tr><td class="mono"><strong>${esc(c.code)}</strong></td><td><strong>${esc(c.name)}</strong><small>${esc(c.assigned_to||'Unassigned')}</small></td><td>${badge(c.priority,statusKind(c.priority))}</td><td>${esc(c.location_name||'Unknown')}</td><td>${esc(c.destination_name||'—')}</td><td>${badge(c.status,statusKind(c.status))}</td><td>${n(c.quantity)} ${esc(c.unit)}</td><td><div class="row-actions"><button class="icon-btn" data-move-cargo="${c.id}">Move</button>${roleCan('commander','logistics')?`<button class="icon-btn" data-edit-cargo="${c.id}">Edit</button>`:''}<button class="icon-btn" data-cargo-history="${c.id}">History</button></div></td></tr>`}
  function openCargoCreate(locs){ modal('Register cargo','Create a trackable cargo unit and destination.',`<form id="cargoForm"><div class="form-grid"><div class="field"><label>Cargo ID</label><input name="code" required placeholder="CRG-200"></div><div class="field"><label>Item name</label><input name="name" required></div><div class="field"><label>Priority</label><select name="priority">${['Critical','High','Medium','Low'].map(x=>`<option>${x}</option>`).join('')}</select></div><div class="field"><label>Status</label><select name="status">${['Registered','In Transit','Delivered','Held'].map(x=>`<option>${x}</option>`).join('')}</select></div><div class="field"><label>Origin</label><select name="origin_location_id">${locationOptions(locs)}</select></div><div class="field"><label>Destination</label><select name="destination_location_id">${locationOptions(locs)}</select></div><div class="field"><label>Current location</label><select name="current_location_id">${locationOptions(locs)}</select></div><div class="field"><label>Assigned to</label><input name="assigned_to" placeholder="Team / custodian"></div><div class="field"><label>Quantity</label><input type="number" step="any" min="0" name="quantity" value="1"></div><div class="field"><label>Unit</label><input name="unit" value="unit"></div></div><div class="modal-actions"><button type="button" class="button ghost" data-cancel>Cancel</button><button class="button primary">Register cargo</button></div></form>`);$('[data-cancel]').onclick=closeModal;$('#cargoForm').onsubmit=async e=>{e.preventDefault();const f=e.currentTarget,p={expedition_id:state.expeditionId,code:formVal(f,'code').trim().toUpperCase(),name:formVal(f,'name'),priority:formVal(f,'priority'),origin_location_id:numOrNull(formVal(f,'origin_location_id')),destination_location_id:numOrNull(formVal(f,'destination_location_id')),current_location_id:numOrNull(formVal(f,'current_location_id')),status:formVal(f,'status'),quantity:Number(formVal(f,'quantity')||1),unit:formVal(f,'unit'),assigned_to:formVal(f,'assigned_to')};try{await api('/api/cargo',{method:'POST',body:JSON.stringify(p)});closeModal();toast('Cargo registered',p.code);renderCargo()}catch(err){toast('Registration failed',err.message,'danger')}}; }
  function openCargoMove(c,locs){ modal('Update cargo movement',`${c.code} · ${c.name}`,`<form id="moveCargoForm"><div class="form-grid"><div class="field"><label>New location</label><select name="location_id" required>${locationOptions(locs,c.current_location_id)}</select></div><div class="field"><label>Status</label><select name="status">${['In Transit','Delivered','Held','Returned'].map(x=>`<option ${c.status===x?'selected':''}>${x}</option>`).join('')}</select></div><div class="field full"><label>Custody / movement note</label><input name="note" placeholder="Received by Camp Alpha logistics"></div></div><div class="modal-actions"><button type="button" class="button ghost" data-cancel>Cancel</button><button class="button primary">Record movement</button></div></form>`);$('[data-cancel]').onclick=closeModal;$('#moveCargoForm').onsubmit=async e=>{e.preventDefault();const f=e.currentTarget;try{const r=await api(`/api/cargo/${c.id}/move`,{method:'POST',body:JSON.stringify({location_id:Number(formVal(f,'location_id')),status:formVal(f,'status'),note:formVal(f,'note')})});closeModal();toast('Cargo movement recorded',`${c.code} · ${r.status||formVal(f,'status')}`);renderCargo()}catch(err){toast('Movement failed',err.message,'danger')}}; }
  function openCargoEdit(c,locs){ modal('Edit cargo',`${c.code} · shipment settings`,`<form id="editCargoForm"><div class="form-grid"><div class="field"><label>Item name</label><input name="name" value="${esc(c.name)}" required></div><div class="field"><label>Priority</label><select name="priority">${['Critical','High','Medium','Low'].map(x=>`<option ${c.priority===x?'selected':''}>${x}</option>`).join('')}</select></div><div class="field"><label>Destination</label><select name="destination_location_id">${locationOptions(locs,c.destination_location_id)}</select></div><div class="field"><label>Assigned to</label><input name="assigned_to" value="${esc(c.assigned_to||'')}"></div></div><div class="modal-actions"><button type="button" class="button ghost" data-cancel>Cancel</button><button class="button primary">Save cargo</button></div></form>`);$('[data-cancel]').onclick=closeModal;$('#editCargoForm').onsubmit=async e=>{e.preventDefault();const f=e.currentTarget;try{await api(`/api/cargo/${c.id}`,{method:'PATCH',body:JSON.stringify({name:formVal(f,'name'),priority:formVal(f,'priority'),destination_location_id:numOrNull(formVal(f,'destination_location_id')),assigned_to:formVal(f,'assigned_to')})});closeModal();toast('Cargo updated',c.code);renderCargo()}catch(err){toast('Cargo update failed',err.message,'danger')}}; }
  async function openCargoHistory(c){ try{const ev=await api(`/api/cargo/${c.id}/events`); modal(`Cargo history — ${c.code}`,c.name,`<div class="incident-timeline">${ev.length?ev.map(x=>`<div class="timeline-event"><time>${fmtTime(x.created_at)}</time><div class="timeline-track"><i></i></div><p><strong>${esc(x.event_type)}</strong><br>${esc(x.location_name||'Unknown location')} · ${esc(x.note||'')}</p></div>`).join(''):'<div class="empty">No movement events.</div>'}</div>`,true)}catch(err){toast('History failed',err.message,'danger')} }
  function openCargoScanner(items,locs){
    modal('Scan cargo','Use a supported camera scanner or enter a cargo ID manually.',`<div class="scanner" id="scanner"><div class="scanner-overlay"><strong>Camera scanner ready when supported</strong><span>BarcodeDetector requires HTTPS or localhost.</span></div></div><form id="scanForm"><div class="field"><label>Cargo ID / QR payload</label><input name="code" autocomplete="off" placeholder="CRG-102" required></div><div class="modal-actions"><button type="button" class="button secondary" id="cameraScan">Start camera</button><button class="button primary">Find cargo</button></div></form>`);
    let stream=null;$('.modal-close').onclick=()=>{stream?.getTracks().forEach(t=>t.stop());closeModal()};
    $('#cameraScan').onclick=async()=>{ if(!('BarcodeDetector'in window)){toast('Camera scanning unavailable','Enter the cargo ID manually in this browser.','warn');return;} try{stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'environment'}});const v=document.createElement('video');v.autoplay=true;v.playsInline=true;v.srcObject=stream;$('#scanner').prepend(v);const det=new BarcodeDetector({formats:['qr_code','code_128','data_matrix']});const loop=async()=>{if(!stream)return;try{const codes=await det.detect(v);if(codes[0]){form.elements.code.value=codes[0].rawValue;stream.getTracks().forEach(t=>t.stop());stream=null;return}}catch{}requestAnimationFrame(loop)};requestAnimationFrame(loop)}catch(err){toast('Camera unavailable',err.message,'danger')}};
    const form=$('#scanForm');form.onsubmit=e=>{e.preventDefault();const code=formVal(form,'code').trim().toUpperCase();const c=items.find(x=>x.code.toUpperCase()===code);if(!c){toast('Cargo not found',code,'danger');return;}stream?.getTracks().forEach(t=>t.stop());closeModal();openCargoMove(c,locs)};
  }

  async function renderInventory(){
    setHeader('Inventory','Safety stock, consumption, resupply and field resource control.'); const [items,locs]=await Promise.all([api(`/api/inventory?expedition_id=${state.expeditionId}`),loadLocations()]);
    $('#view').innerHTML=`<div class="panel"><div class="panel-head"><div><h2>Inventory Control</h2><p>Threshold-based resource management across mission locations.</p></div><div class="panel-actions"><input class="search" id="invSearch" placeholder="Search inventory…">${roleCan('commander','logistics')?'<button class="button primary" id="addInv">+ Add item</button>':''}</div></div><div class="card-grid" id="invGrid">${items.map(invCard).join('')}</div></div>`;
    const paint=(q='')=>{$('#invGrid').innerHTML=items.filter(x=>[x.name,x.sku,x.location_name].join(' ').toLowerCase().includes(q.toLowerCase())).map(invCard).join('')||'<div class="empty"><div><strong>No matching items</strong>Change the search term.</div></div>';wire()};
    function wire(){ $$('[data-adjust-inv]').forEach(b=>b.onclick=()=>openInvAdjust(items.find(x=>x.id===+b.dataset.adjustInv))); $$('[data-edit-inv]').forEach(b=>b.onclick=()=>openInvEdit(items.find(x=>x.id===+b.dataset.editInv),locs)); }
    $('#invSearch').oninput=e=>paint(e.target.value);if($('#addInv'))$('#addInv').onclick=()=>openInvCreate(locs);wire();
  }
  function invCard(i){ const low=+i.quantity<+i.min_quantity, pct=Math.max(0,Math.min(100,(+i.quantity/Math.max(+i.min_quantity,1))*70));return `<article class="item-card"><div class="item-card-head"><div><span class="code mono">${esc(i.sku)}</span><h3>${esc(i.name)}</h3></div>${badge(low?'LOW':'OK',low?'danger':'good')}</div><p>${esc(i.location_name||'Unassigned location')} · Minimum ${n(i.min_quantity)} ${esc(i.unit)}</p><div class="metric-row"><div class="metric-big">${n(i.quantity)} <small>${esc(i.unit)}</small></div><span class="${low?'danger-text':'good-text'}">${low?'Below safety stock':'Within threshold'}</span></div><div class="progress"><i class="${low?'low':''}" style="width:${pct}%"></i></div><div class="card-actions"><button class="button secondary small" data-adjust-inv="${i.id}">Adjust stock</button>${roleCan('commander','logistics')?`<button class="button ghost small" data-edit-inv="${i.id}">Settings</button>`:''}</div></article>`}
  function openInvCreate(locs){ modal('Add inventory item','Register a resource and its minimum safety stock.',`<form id="invForm"><div class="form-grid"><div class="field"><label>SKU</label><input name="sku" required placeholder="INV-MED-02"></div><div class="field"><label>Item</label><input name="name" required></div><div class="field"><label>Location</label><select name="location_id">${locationOptions(locs)}</select></div><div class="field"><label>Unit</label><input name="unit" value="units"></div><div class="field"><label>Opening quantity</label><input type="number" step="any" name="quantity" value="0"></div><div class="field"><label>Minimum quantity</label><input type="number" step="any" name="min_quantity" value="0"></div><div class="field full"><label>Expiry date</label><input type="date" name="expiry_date"></div></div><div class="modal-actions"><button type="button" class="button ghost" data-cancel>Cancel</button><button class="button primary">Add item</button></div></form>`);$('[data-cancel]').onclick=closeModal;$('#invForm').onsubmit=async e=>{e.preventDefault();const f=e.currentTarget,p={expedition_id:state.expeditionId,sku:formVal(f,'sku').trim().toUpperCase(),name:formVal(f,'name'),location_id:numOrNull(formVal(f,'location_id')),quantity:Number(formVal(f,'quantity')||0),min_quantity:Number(formVal(f,'min_quantity')||0),unit:formVal(f,'unit'),expiry_date:formVal(f,'expiry_date')||null};try{await api('/api/inventory',{method:'POST',body:JSON.stringify(p)});closeModal();toast('Inventory item added',p.name);renderInventory()}catch(err){toast('Save failed',err.message,'danger')}}; }
  function openInvAdjust(i){ modal('Adjust inventory',`${i.name} · current ${n(i.quantity)} ${i.unit}`,`<form id="adjForm"><div class="form-grid"><div class="field"><label>Adjustment</label><input type="number" step="any" name="delta" required placeholder="Use + for resupply, - for consumption"></div><div class="field"><label>Reason</label><select name="reason"><option>Resupply</option><option>Field consumption</option><option>Transfer correction</option><option>Damaged / lost</option><option>Stock count correction</option></select></div></div><div class="modal-actions"><button type="button" class="button ghost" data-cancel>Cancel</button><button class="button primary">Record adjustment</button></div></form>`);$('[data-cancel]').onclick=closeModal;$('#adjForm').onsubmit=async e=>{e.preventDefault();const f=e.currentTarget;try{await api(`/api/inventory/${i.id}/adjust`,{method:'POST',body:JSON.stringify({delta:Number(formVal(f,'delta')),reason:formVal(f,'reason')})});closeModal();toast('Inventory adjusted',i.name);renderInventory()}catch(err){toast('Adjustment failed',err.message,'danger')}}; }
  function openInvEdit(i,locs){ modal('Inventory settings',`${i.sku} · ${i.name}`,`<form id="editInvForm"><div class="form-grid"><div class="field"><label>Item name</label><input name="name" value="${esc(i.name)}" required></div><div class="field"><label>Location</label><select name="location_id">${locationOptions(locs,i.location_id)}</select></div><div class="field"><label>Minimum safety stock</label><input type="number" step="any" name="min_quantity" value="${n(i.min_quantity)}"></div><div class="field"><label>Unit</label><input name="unit" value="${esc(i.unit)}"></div><div class="field full"><label>Expiry date</label><input type="date" name="expiry_date" value="${esc(i.expiry_date||'')}"></div></div><div class="modal-actions"><button type="button" class="button ghost" data-cancel>Cancel</button><button class="button primary">Save settings</button></div></form>`);$('[data-cancel]').onclick=closeModal;$('#editInvForm').onsubmit=async e=>{e.preventDefault();const f=e.currentTarget;try{await api(`/api/inventory/${i.id}`,{method:'PATCH',body:JSON.stringify({name:formVal(f,'name'),location_id:numOrNull(formVal(f,'location_id')),min_quantity:Number(formVal(f,'min_quantity')||0),unit:formVal(f,'unit'),expiry_date:formVal(f,'expiry_date')||null})});closeModal();toast('Inventory settings updated',i.name);renderInventory()}catch(err){toast('Update failed',err.message,'danger')}}; }

  async function renderVehicles(){
    setHeader('Vehicles','Fleet readiness, live telemetry, fuel posture and response range.');const [items,locs]=await Promise.all([api(`/api/vehicles?expedition_id=${state.expeditionId}`),loadLocations()]);
    const simRunning=!!state.vehicleSimTimer;
    $('#view').innerHTML=`<div class="panel"><div class="panel-head"><div><h2>Vehicle Fleet</h2><p>Operational response assets with latest GPS telemetry.</p></div><div class="panel-actions">${roleCan('commander','logistics')?`<button class="button ${simRunning?'danger':'secondary'}" id="vehicleSimulator">${simRunning?'■ Stop live simulation':'▶ Start live simulation'}</button><button class="button primary" id="addVehicle">+ Add vehicle</button>`:''}</div></div><div class="table-wrap"><table><thead><tr><th>Code</th><th>Vehicle</th><th>Location</th><th>Status</th><th>Fuel</th><th>Live position</th><th>Speed</th><th>Range</th><th>Actions</th></tr></thead><tbody>${items.map(v=>`<tr><td class="mono"><strong>${esc(v.code)}</strong></td><td><strong>${esc(v.name)}</strong><small>${esc(v.type)}</small></td><td>${esc(v.location_name||'Unassigned')}</td><td>${badge(v.status,statusKind(v.status))}</td><td>${badge(`${n(v.fuel_percent)}%`,+v.fuel_percent<30?'danger':+v.fuel_percent<50?'warn':'good')}</td><td>${v.telemetry_recorded_at?`<strong class="good-text">● LIVE</strong><small>${n(v.latitude,5)}, ${n(v.longitude,5)} · ${ago(v.telemetry_recorded_at)}</small>`:'<span class="muted">No GPS feed</span>'}</td><td>${v.speed_kph!=null?`${n(v.speed_kph,1)} km/h`:'—'}</td><td>${n(v.range_km)} km</td><td>${roleCan('commander','logistics')?`<button class="icon-btn" data-edit-vehicle="${v.id}">Update</button>`:'—'}</td></tr>`).join('')}</tbody></table></div></div>`;
    if($('#addVehicle'))$('#addVehicle').onclick=()=>openVehicleForm(null,locs);$$('[data-edit-vehicle]').forEach(btn=>btn.onclick=()=>openVehicleForm(items.find(x=>x.id===+btn.dataset.editVehicle),locs));if($('#vehicleSimulator'))$('#vehicleSimulator').onclick=()=>toggleVehicleSimulation(items);
  }
  function stopVehicleSimulation(show=true){if(state.vehicleSimTimer)clearInterval(state.vehicleSimTimer);const had=state.vehicleSimId;state.vehicleSimTimer=null;state.vehicleSimId=null;state.vehicleSimStep=0;state.vehicleSimBase=null;if(show&&had)toast('Vehicle simulation stopped')}
  function toggleVehicleSimulation(items){
    if(state.vehicleSimTimer){stopVehicleSimulation();renderVehicles();return}
    const vehicle=items.find(v=>v.code==='V03'&&v.status.toLowerCase()==='operational')||items.find(v=>v.status.toLowerCase()==='operational'&&Number.isFinite(Number(v.latitude))&&Number.isFinite(Number(v.longitude)));
    if(!vehicle||!Number.isFinite(Number(vehicle.latitude))||!Number.isFinite(Number(vehicle.longitude))){toast('Simulation unavailable','Add coordinates to an operational vehicle location first.','danger');return}
    state.vehicleSimId=vehicle.id;state.vehicleSimStep=0;state.vehicleSimBase={lat:+vehicle.latitude,lon:+vehicle.longitude,fuel:+vehicle.fuel_percent};
    const tick=async()=>{state.vehicleSimStep++;const q=state.vehicleSimStep,base=state.vehicleSimBase;const lat=base.lat+0.012*Math.sin(q*.38),lon=base.lon+0.020*Math.cos(q*.34),fuel=Math.max(8,base.fuel-q*.18),speed=18+7*Math.abs(Math.sin(q*.5)),heading=(q*23)%360;try{await api('/api/telemetry/position',{method:'POST',body:JSON.stringify({expedition_id:state.expeditionId,entity_type:'vehicle',entity_id:vehicle.id,latitude:lat,longitude:lon,accuracy_m:4.5,speed_kph:speed,heading,fuel_percent:fuel,source:'demo-simulator',recorded_at:new Date().toISOString()})})}catch(err){if(navigator.onLine)toast('Simulator update failed',err.message,'danger')}};
    tick();state.vehicleSimTimer=setInterval(tick,3000);toast('Live vehicle feed started',`${vehicle.code} publishes GPS every 3 seconds.`,'good',4200);renderVehicles();
  }
  function openVehicleForm(v,locs){ modal(v?'Update vehicle':'Add vehicle',v?`${v.code} · ${v.name}`:'Register a transport or response vehicle.',`<form id="vehicleForm"><div class="form-grid">${!v?`<div class="field"><label>Code</label><input name="code" required placeholder="V05"></div>`:''}<div class="field"><label>Name</label><input name="name" required value="${esc(v?.name||'')}"></div><div class="field"><label>Type</label><input name="type" value="${esc(v?.type||'Ground')}"></div><div class="field"><label>Location</label><select name="location_id">${locationOptions(locs,v?.location_id)}</select></div><div class="field"><label>Status</label><select name="status">${['Operational','Maintenance','Unavailable','Deployed'].map(x=>`<option ${v?.status===x?'selected':''}>${x}</option>`).join('')}</select></div><div class="field"><label>Fuel %</label><input type="number" min="0" max="100" step="1" name="fuel_percent" value="${n(v?.fuel_percent??100)}"></div><div class="field"><label>Range km</label><input type="number" min="0" step="any" name="range_km" value="${n(v?.range_km??0)}"></div></div><div class="modal-actions"><button type="button" class="button ghost" data-cancel>Cancel</button><button class="button primary">${v?'Save vehicle':'Add vehicle'}</button></div></form>`);$('[data-cancel]').onclick=closeModal;$('#vehicleForm').onsubmit=async e=>{e.preventDefault();const f=e.currentTarget,p={name:formVal(f,'name'),type:formVal(f,'type'),location_id:numOrNull(formVal(f,'location_id')),status:formVal(f,'status'),fuel_percent:Number(formVal(f,'fuel_percent')),range_km:Number(formVal(f,'range_km'))};try{if(v)await api(`/api/vehicles/${v.id}`,{method:'PATCH',body:JSON.stringify(p)});else await api('/api/vehicles',{method:'POST',body:JSON.stringify({expedition_id:state.expeditionId,code:formVal(f,'code').trim().toUpperCase(),...p})});closeModal();toast(v?'Vehicle updated':'Vehicle added');renderVehicles()}catch(err){toast('Vehicle save failed',err.message,'danger')}}; }

  async function renderAssets(){
    setHeader('Assets','Track reusable expedition equipment, assignment and current location.');const [items,locs,people]=await Promise.all([api(`/api/assets?expedition_id=${state.expeditionId}`),loadLocations(),api(`/api/personnel?expedition_id=${state.expeditionId}`)]);
    $('#view').innerHTML=`<div class="panel"><div class="panel-head"><div><h2>Asset Register</h2><p>Reusable scientific, communications, power and field equipment.</p></div><div class="panel-actions"><input class="search" id="assetSearch" placeholder="Asset code or name…">${roleCan('commander','logistics')?'<button class="button primary" id="addAsset">+ Add asset</button>':''}</div></div><div class="table-wrap"><table><thead><tr><th>Code</th><th>Asset</th><th>Category</th><th>Location</th><th>Status</th><th>Assigned to</th><th>Serial</th><th>Actions</th></tr></thead><tbody id="assetRows">${items.map(assetRow).join('')}</tbody></table></div></div>`;
    const paint=q=>{$('#assetRows').innerHTML=items.filter(x=>[x.code,x.name,x.category,x.location_name,x.assigned_to_name].join(' ').toLowerCase().includes(q.toLowerCase())).map(assetRow).join('');wire()};
    function wire(){$$('[data-edit-asset]').forEach(b=>b.onclick=()=>openAssetForm(items.find(x=>x.id===+b.dataset.editAsset),locs,people))}$('#assetSearch').oninput=e=>paint(e.target.value);if($('#addAsset'))$('#addAsset').onclick=()=>openAssetForm(null,locs,people);wire();
  }
  function assetRow(a){return `<tr><td class="mono"><strong>${esc(a.code)}</strong></td><td>${esc(a.name)}</td><td>${esc(a.category)}</td><td>${esc(a.location_name||'Unassigned')}</td><td>${badge(a.status,statusKind(a.status))}</td><td>${esc(a.assigned_to_name||'—')}</td><td class="mono">${esc(a.serial_number||'—')}</td><td>${roleCan('commander','logistics')?`<button class="icon-btn" data-edit-asset="${a.id}">Update</button>`:'—'}</td></tr>`}
  function openAssetForm(a,locs,people){const personOpts='<option value="">Unassigned</option>'+people.map(p=>`<option value="${p.id}" ${a?.assigned_to_personnel_id===p.id?'selected':''}>${esc(p.name)}</option>`).join('');modal(a?'Update asset':'Add asset',a?`${a.code} · ${a.name}`:'Register reusable equipment.',`<form id="assetForm"><div class="form-grid">${!a?'<div class="field"><label>Asset code</label><input name="code" required placeholder="AST-006"></div>':''}<div class="field"><label>Name</label><input name="name" required value="${esc(a?.name||'')}"></div><div class="field"><label>Category</label><input name="category" value="${esc(a?.category||'Equipment')}"></div><div class="field"><label>Location</label><select name="location_id">${locationOptions(locs,a?.location_id)}</select></div><div class="field"><label>Status</label><select name="status">${['Available','Deployed','Maintenance','Unavailable'].map(x=>`<option ${a?.status===x?'selected':''}>${x}</option>`).join('')}</select></div><div class="field"><label>Assigned to</label><select name="assigned_to_personnel_id">${personOpts}</select></div><div class="field full"><label>Serial number</label><input name="serial_number" value="${esc(a?.serial_number||'')}"></div></div><div class="modal-actions"><button type="button" class="button ghost" data-cancel>Cancel</button><button class="button primary">${a?'Save asset':'Add asset'}</button></div></form>`);$('[data-cancel]').onclick=closeModal;$('#assetForm').onsubmit=async e=>{e.preventDefault();const f=e.currentTarget,p={name:formVal(f,'name'),category:formVal(f,'category'),location_id:numOrNull(formVal(f,'location_id')),status:formVal(f,'status'),serial_number:formVal(f,'serial_number'),assigned_to_personnel_id:numOrNull(formVal(f,'assigned_to_personnel_id'))};try{if(a)await api(`/api/assets/${a.id}`,{method:'PATCH',body:JSON.stringify(p)});else await api('/api/assets',{method:'POST',body:JSON.stringify({expedition_id:state.expeditionId,code:formVal(f,'code').trim().toUpperCase(),...p})});closeModal();toast(a?'Asset updated':'Asset registered');renderAssets()}catch(err){toast('Asset save failed',err.message,'danger')}};}

  async function renderEmergency(){
    setHeader('Emergency Response','Incident command, responder assignment and operational timeline.');const incidents=await api(`/api/incidents?expedition_id=${state.expeditionId}`);const active=incidents.find(i=>!['resolved','closed'].includes(i.status.toLowerCase()));
    if(!active){ $('#view').innerHTML=`<div class="panel sos-empty"><div class="sos-symbol">△</div><h2>No active incidents</h2><p>PolarOps is continuously maintaining personnel, inventory, vehicle and location context so the commander can build a response picture immediately when an SOS is raised.</p><button class="button danger" id="emptySOS">Trigger emergency</button></div>${incidents.length?`<div class="panel" style="margin-top:13px"><div class="panel-head"><div><h2>Incident History</h2><p>${incidents.length} incidents recorded.</p></div></div><div class="table-wrap"><table><thead><tr><th>Code</th><th>Title</th><th>Location</th><th>Severity</th><th>Status</th><th>Created</th></tr></thead><tbody>${incidents.map(i=>`<tr><td class="mono">${esc(i.code)}</td><td>${esc(i.title)}</td><td>${esc(i.location_name||'—')}</td><td>${badge(i.severity,statusKind(i.severity))}</td><td>${badge(i.status,statusKind(i.status))}</td><td>${fmtDate(i.created_at)}</td></tr>`).join('')}</tbody></table></div></div>`:''}`;$('#emptySOS').onclick=openIncidentCreate;return; }
    const d=await api(`/api/incidents/${active.id}`), i=d.incident, nv=d.nearest_vehicle, meds=d.medical_inventory.reduce((a,x)=>a+Number(x.quantity),0);
    $('#view').innerHTML=`<div class="incident-layout"><div class="incident-hero"><div class="incident-title"><div><span class="eyebrow">${esc(i.code)} · ${esc(i.status)}</span><h2>${esc(i.title)}</h2><small>${esc(i.type)} · ${fmtDate(i.created_at)}</small></div>${badge(i.severity,'danger')}</div><div class="incident-kpis"><div class="incident-kpi"><strong>${i.affected_count||d.personnel_at_location.length}</strong><span>Personnel affected</span></div><div class="incident-kpi"><strong>${esc(nv?.vehicle?.code||i.vehicle_code||'—')}</strong><span>Nearest / assigned vehicle</span></div><div class="incident-kpi"><strong>${nv?`${n(nv.distance_km,1)} km`:'—'}</strong><span>Response distance</span></div><div class="incident-kpi"><strong>${n(meds)}</strong><span>Medical stock units</span></div></div><div class="recommendation"><strong>Recommended action</strong><p>${nv?`Dispatch ${esc(nv.vehicle.code)} from ${esc(nv.vehicle.location_name||'its current location')}. It has ${n(nv.vehicle.fuel_percent)}% fuel and an estimated straight-line response distance of ${n(nv.distance_km,1)} km.`:'No operational vehicle with mapped coordinates is currently available. Escalate to alternate transport.'}</p></div><div class="card-actions" style="margin-top:14px">${roleCan('commander','logistics')&&i.status==='Active'?'<button class="button good" id="dispatch">Dispatch nearest response vehicle</button>':''}${roleCan('commander')?'<button class="button secondary" id="resolve">Mark resolved</button>':''}<button class="button ghost" id="addEvent">Add timeline note</button></div><div class="incident-timeline"><h3 style="font-size:12px">Incident timeline</h3>${d.events.map(ev=>`<div class="timeline-event"><time>${fmtTime(ev.created_at)}</time><div class="timeline-track"><i></i></div><p><strong>${esc(ev.event_type)}</strong><br>${esc(ev.note)}${ev.user_name?` · ${esc(ev.user_name)}`:''}</p></div>`).join('')}</div></div>
      <aside class="panel"><div class="panel-head"><div><h2>Incident Context</h2><p>Resources automatically assembled from mission data.</p></div></div><div class="info-list"><div class="info-row"><span>Location</span><strong>${esc(i.location_name||'Unknown')}</strong></div><div class="info-row"><span>Status</span><strong>${esc(i.status)}</strong></div><div class="info-row"><span>Assigned vehicle</span><strong>${esc(i.vehicle_code||'Not assigned')}</strong></div><div class="info-row"><span>People at location</span><strong>${d.personnel_at_location.length}</strong></div><div class="info-row"><span>Medical inventory lines</span><strong>${d.medical_inventory.length}</strong></div></div><h3 style="font-size:11px;margin-top:18px">Personnel at incident location</h3><div class="activity-list">${d.personnel_at_location.length?d.personnel_at_location.map(p=>`<div class="activity-item"><div class="activity-icon">◎</div><div><strong>${esc(p.name)}</strong><span>${esc(p.role)} · ${esc(p.status)}</span></div></div>`).join(''):'<div class="empty" style="min-height:120px">No roster members assigned to this location.</div>'}</div></aside></div>`;
    if($('#dispatch'))$('#dispatch').onclick=async()=>{try{const r=await api(`/api/incidents/${i.id}/dispatch`,{method:'POST'});toast('Response dispatched',`${r.vehicle_code} · ${r.distance_km} km`);renderEmergency()}catch(err){toast('Dispatch failed',err.message,'danger')}};
    if($('#resolve'))$('#resolve').onclick=async()=>{if(!confirm(`Resolve ${i.code}?`))return;try{await api(`/api/incidents/${i.id}/resolve`,{method:'POST'});toast('Incident resolved',i.code);renderEmergency()}catch(err){toast('Resolve failed',err.message,'danger')}};
    $('#addEvent').onclick=()=>openIncidentNote(i);
    window.PolarOpsFeatures?.enhanceIncidentCommand?.(i);
  }
  async function openIncidentCreate(){ const locs=await loadLocations(); modal('Trigger emergency','Create an incident and assemble response context immediately.',`<form id="incidentForm"><div class="form-grid"><div class="field"><label>Incident title</label><input name="title" value="Field Emergency" required></div><div class="field"><label>Type</label><select name="type"><option>Field Emergency</option><option>Medical</option><option>Vehicle</option><option>Weather</option><option>Missing Personnel</option><option>Fire</option></select></div><div class="field"><label>Severity</label><select name="severity"><option>Critical</option><option selected>High</option><option>Medium</option><option>Low</option></select></div><div class="field"><label>Location</label><select name="location_id" required>${locationOptions(locs)}</select></div><div class="field"><label>Personnel affected</label><input type="number" name="affected_count" min="0" value="0"></div><div class="field full"><label>Description</label><textarea name="description" placeholder="What happened and what is known right now?"></textarea></div></div><div class="modal-actions"><button type="button" class="button ghost" data-cancel>Cancel</button><button class="button danger">Trigger incident</button></div></form>`);$('[data-cancel]').onclick=closeModal;$('#incidentForm').onsubmit=async e=>{e.preventDefault();const f=e.currentTarget,p={expedition_id:state.expeditionId,title:formVal(f,'title'),type:formVal(f,'type'),severity:formVal(f,'severity'),location_id:Number(formVal(f,'location_id')),description:formVal(f,'description'),affected_count:Number(formVal(f,'affected_count')||0)};if(!p.location_id){toast('Location required','Select the incident location.','danger');return}try{const r=await api('/api/incidents',{method:'POST',body:JSON.stringify(p)});closeModal();state.view='emergency';$$('.nav button').forEach(b=>b.classList.toggle('active',b.dataset.view==='emergency'));toast('Emergency activated',r.code,'danger');renderEmergency()}catch(err){toast('Incident creation failed',err.message,'danger')}}; }
  function openIncidentNote(i){ modal('Add incident timeline note',i.code,`<form id="eventForm"><div class="field"><label>Event type</label><input name="event_type" value="Update"></div><div class="field"><label>Operational note</label><textarea name="note" required placeholder="Response team reached staging point…"></textarea></div><div class="modal-actions"><button type="button" class="button ghost" data-cancel>Cancel</button><button class="button primary">Add note</button></div></form>`);$('[data-cancel]').onclick=closeModal;$('#eventForm').onsubmit=async e=>{e.preventDefault();const f=e.currentTarget;try{await api(`/api/incidents/${i.id}/events`,{method:'POST',body:JSON.stringify({event_type:formVal(f,'event_type'),note:formVal(f,'note')})});closeModal();toast('Timeline updated');renderEmergency()}catch(err){toast('Update failed',err.message,'danger')}}; }

  async function renderEnvironment(force=false){
    const exp=state.expeditions.find(e=>e.id===state.expeditionId);
    setHeader('Environment & Science',`${exp?.region||'Polar region'} · live and near-real-time environmental intelligence.`);
    $('#view').innerHTML='<div class="panel"><div class="empty" style="min-height:300px"><div><strong>Loading polar environment…</strong>Checking weather, sea ice, space weather and seismic feeds.</div></div></div>';
    let r;
    try{
      r=await api(`/api/environment/overview?expedition_id=${state.expeditionId}${force?'&force=true':''}`);
    }catch(err){
      $('#view').innerHTML=`<div class="panel"><div class="empty" style="min-height:300px"><div><strong>Environmental feeds unavailable</strong>${esc(err.message)}</div></div></div>`;
      return;
    }
    const d=r.data||{}, w=d.weather, sea=d.sea_ice, space=d.space_weather, quakes=d.earthquakes;
    const pole=r.pole==='north'?'Arctic / North':'Antarctic / South';
    const errors=Object.keys(r.errors||{});
    const base=r.primary_location?.name||'No mapped base';
    const kp=Number(space?.estimated_kp);
    const kpKind=Number.isFinite(kp)&&kp>=5?'danger':Number.isFinite(kp)&&kp>=4?'warn':'good';
    const quakeCount=quakes?.count??0;
    $('#view').innerHTML=`
      <div class="science-hero ${r.pole}">
        <div><span class="eyebrow">POLAR ENVIRONMENT / ${esc(pole.toUpperCase())}</span><h2>Operational environment + science picture</h2><p>Public environmental feeds are separated from private personnel and logistics telemetry. Every external product is labelled with its source and update cadence.</p></div>
        <div class="science-actions"><span class="badge ${errors.length?'warn':'good'}">${errors.length?`${errors.length} feed issue${errors.length===1?'':'s'}`:'Feeds connected'}</span><button class="button secondary" id="refreshEnvironment">↻ Refresh live data</button></div>
      </div>
      <div class="environment-stats">
        <div class="env-stat ${w?'good':'warn'}"><span>Weather · ${esc(base)}</span><strong>${w?.temperature_c==null?'—':`${n(w.temperature_c,1)} °C`}</strong><small>${w?`${n(w.wind_speed_kph,1)} km/h wind · gusts ${n(w.wind_gusts_kph,1)} km/h`:'Feed unavailable'}</small></div>
        <div class="env-stat info"><span>Sea ice product</span><strong>${sea?.date||'—'}</strong><small>${sea?.freshness||'NOAA/NSIDC daily product'}</small></div>
        <div class="env-stat ${kpKind}"><span>Geomagnetic Kp</span><strong>${Number.isFinite(kp)?n(kp,2):'—'}</strong><small>${space?`${esc(space.communications_risk)} level · ${fmtDate(space.time_tag)}`:'NOAA SWPC unavailable'}</small></div>
        <div class="env-stat ${quakeCount?'warn':'good'}"><span>Polar earthquakes</span><strong>${quakeCount}</strong><small>M4+ · last ${quakes?.period_days||30} days</small></div>
      </div>
      <div class="science-grid">
        <section class="panel science-visual">
          <div class="panel-head"><div><h2>Daily sea-ice concentration</h2><p>NOAA/NSIDC Sea Ice Index v4 · ${esc(pole)}</p></div>${sea?.date?`<span class="badge info">${esc(sea.date)}</span>`:''}</div>
          ${sea?.concentration_image?`<img class="science-feed-image" src="${esc(sea.concentration_image)}" alt="Latest ${esc(pole)} sea ice concentration from NOAA NSIDC" loading="lazy">`:`<div class="empty" style="min-height:260px"><div><strong>Sea-ice image unavailable</strong>${esc(r.errors?.sea_ice||'')}</div></div>`}
          <div class="feed-caption"><span>Daily satellite-derived concentration; not a certified navigation chart.</span>${sea?.source_url?`<a href="${esc(sea.source_url)}" target="_blank" rel="noopener">Open NSIDC source ↗</a>`:''}</div>
        </section>
        <section class="panel science-visual">
          <div class="panel-head"><div><h2>Latest auroral forecast</h2><p>NOAA SWPC OVATION · high-latitude space-weather context</p></div>${space?.aurora?.time_tag?`<span class="badge ${kpKind}">${fmtDate(space.aurora.time_tag)}</span>`:''}</div>
          ${space?.aurora?.image_url?`<img class="science-feed-image" src="${esc(space.aurora.image_url)}" alt="Latest ${esc(pole)} NOAA SWPC auroral forecast" loading="lazy">`:`<div class="empty" style="min-height:260px"><div><strong>Aurora image unavailable</strong>${esc(r.errors?.space_weather||'')}</div></div>`}
          <div class="feed-caption"><span>Kp/OVATION are indicators; radio impact depends on frequency, equipment and local conditions.</span>${space?.source_url?`<a href="${esc(space.source_url)}" target="_blank" rel="noopener">Open NOAA SWPC ↗</a>`:''}</div>
        </section>
      </div>
      <div class="science-grid lower">
        <section class="panel">
          <div class="panel-head"><div><h2>Recent polar earthquakes</h2><p>USGS M4+ events · last ${quakes?.period_days||30} days · ${esc(pole)}</p></div><span class="badge info">${quakeCount} events</span></div>
          ${quakes?.events?.length?`<div class="table-wrap compact-table"><table><thead><tr><th>UTC</th><th>Magnitude</th><th>Location</th><th>Depth</th></tr></thead><tbody>${quakes.events.slice(0,10).map(q=>`<tr><td>${fmtDate(q.time)}</td><td><strong>M${n(q.magnitude,1)}</strong></td><td>${q.detail_url?`<a href="${esc(q.detail_url)}" target="_blank" rel="noopener">${esc(q.place||'Polar region')}</a>`:esc(q.place||'Polar region')}</td><td>${n(q.depth_km,1)} km</td></tr>`).join('')}</tbody></table></div>`:`<div class="empty" style="min-height:150px"><div><strong>No M4+ events returned</strong>${esc(r.errors?.earthquakes||'No qualifying events in this period.')}</div></div>`}
        </section>
        <section class="panel">
          <div class="panel-head"><div><h2>Current conditions</h2><p>Model current conditions at the primary mapped mission location.</p></div></div>
          ${w?`<div class="weather-grid environment-weather">
            <div><span>Temperature</span><strong>${w.temperature_c==null?'—':`${n(w.temperature_c,1)} °C`}</strong></div>
            <div><span>Feels like</span><strong>${w.apparent_temperature_c==null?'—':`${n(w.apparent_temperature_c,1)} °C`}</strong></div>
            <div><span>Humidity</span><strong>${w.relative_humidity==null?'—':`${n(w.relative_humidity)}%`}</strong></div>
            <div><span>Wind</span><strong>${w.wind_speed_kph==null?'—':`${n(w.wind_speed_kph,1)} km/h`}</strong></div>
            <div><span>Gusts</span><strong>${w.wind_gusts_kph==null?'—':`${n(w.wind_gusts_kph,1)} km/h`}</strong></div>
            <div><span>Pressure</span><strong>${w.surface_pressure_hpa==null?'—':`${n(w.surface_pressure_hpa,1)} hPa`}</strong></div>
          </div><div class="feed-caption"><span>${esc(w.source)} · ${fmtDate(w.observed_at)} UTC</span><a href="${esc(w.source_url)}" target="_blank" rel="noopener">Weather source ↗</a></div>`:`<div class="empty" style="min-height:180px"><div><strong>No weather coordinates available</strong>Add a mapped station/base/camp to this expedition.</div></div>`}
        </section>
      </div>
      <section class="panel" style="margin-top:10px">
        <div class="panel-head"><div><h2>Authoritative polar data & mapping tools</h2><p>Selected operational and scientific resources for commanders, logistics teams and researchers.</p></div></div>
        <div class="resource-grid">${(r.resources||[]).map(x=>`<a class="resource-card" href="${esc(x.url)}" target="_blank" rel="noopener"><span>${esc(x.category)}</span><strong>${esc(x.name)}</strong><p>${esc(x.detail)}</p><small>${esc(x.update)}</small></a>`).join('')}</div>
      </section>
      <div class="environment-disclaimer"><strong>Operational boundary:</strong> these public feeds support situational awareness and science planning. They do not replace national programme instructions, certified navigation products, local observations, aviation briefings, medical protocols or authorized worker/vehicle telemetry.</div>
    `;
    const refresh=$('#refreshEnvironment');
    if(refresh)refresh.onclick=()=>renderEnvironment(true);
  }

  function arcticVerificationBadge(r){
    const status=String(r.verification_status||'');
    if(status==='verified_current')return badge('Verified current','good');
    if(status==='verified_component')return badge('Current component','info');
    if(status==='current_network_addition')return badge('Current addition','good');
    return badge('Reference only','warn');
  }

  function arcticCoordinateBadge(r){
    const p=String(r.coordinate_precision||'');
    if(p==='official_facility')return badge('Official coordinates','good');
    if(p==='station_page')return badge('Station reference','info');
    if(p==='location_reference')return badge('Approx. location','warn');
    return badge('Unmapped','warn');
  }

  async function renderArcticNetwork(){
    setHeader('Arctic Research Network','Verified current research infrastructure separated from reference-only station records.');
    const [locs,env,network]=await Promise.all([
      loadLocations(),
      api(`/api/environment/overview?expedition_id=${state.expeditionId}`),
      api('/api/public/arctic-research-stations')
    ]);
    const resources=env.resources||[];
    const summary=network.summary||{};
    const referenceItems=network.reference_items||[];
    const additions=network.current_additions||[];
    const mappedVerified=(network.items||[]).filter(r=>String(r.verification_status||'').startsWith('verified_')&&Number.isFinite(+r.latitude)&&Number.isFinite(+r.longitude)&&+r.latitude>=55);
    const mappedReference=referenceItems.filter(r=>!String(r.verification_status||'').startsWith('verified_')&&Number.isFinite(+r.latitude)&&Number.isFinite(+r.longitude)&&+r.latitude>=55);
    $('#view').innerHTML=`
      <div class="source-strip network-source-strip">
        <div><span>Supplied reference rows</span><strong>${summary.user_reference_rows??referenceItems.length}</strong></div>
        <div><span>Independently verified</span><strong>${summary.verified_reference_rows??'—'}</strong></div>
        <div><span>Current additions found</span><strong>${summary.current_network_additions??additions.length}</strong></div>
        <div><span>Mapped reference rows</span><strong>${summary.mapped_rows??'—'}</strong></div>
        <div class="source-note"><strong>ARCTIC / NORTH</strong><span>Population values are reference values, not live occupancy. Verified-current labels require a named current research-network or operator source.</span></div>
      </div>
      <section class="panel network-map-panel dashboard-map-panel" id="arcticNetworkPanel">
        <div class="panel-head"><div><h2>Arctic Research Stations Map</h2><p>Verified current stations are enabled by default. Reference-only records are a separate optional layer.</p></div><div class="panel-actions"><span class="badge violet">${mappedVerified.length} VERIFIED MAPPED</span><button class="button ghost small" id="arcticNetworkFullscreen" type="button">⛶ Fullscreen</button></div></div>
        <div id="arcticMissionMap" class="facility-network-map arctic-research-map"></div>
        <div class="live-map-note"><span>● Mission coordinates remain private/operator-controlled</span><span>${mappedVerified.length} verified mapped · ${mappedReference.length} reference-only mapped</span></div>
      </section>
      <section class="panel" style="margin-top:13px">
        <div class="panel-head"><div><h2>Verification sources</h2><p>Proof links used to distinguish current research infrastructure from reference-only records.</p></div></div>
        <div class="resource-grid">${(network.sources||[]).map(x=>`<a class="resource-card" href="${esc(x.url)}" target="_blank" rel="noopener"><span>VERIFICATION SOURCE</span><strong>${esc(x.name)}</strong><p>${esc(x.role)}</p><small>Open source ↗</small></a>`).join('')}</div>
        <div class="environment-disclaimer" style="margin-top:10px"><strong>Verification rule:</strong> ${esc(network.warning||'Reference rows are not assumed to be currently operational.')}</div>
      </section>
      <section class="panel" style="margin-top:13px">
        <div class="panel-head"><div><h2>Your Arctic station reference — checked</h2><p>All ${referenceItems.length} supplied rows are retained. Verified/current status is shown separately from the supplied name, establishment year and reference population.</p></div></div>
        <div class="table-wrap arctic-reference-table"><table><thead><tr><th>Station</th><th>Location</th><th>Operating country</th><th>Established</th><th>Reference population</th><th>Verification</th><th>Map precision</th></tr></thead><tbody>
          ${referenceItems.map(r=>`<tr><td><strong>${esc(r.name)}</strong><small>${esc(r.verification_note||'')}</small></td><td>${esc(r.location||'—')}</td><td>${esc(r.operating_country||'—')}</td><td>${esc(r.established||'—')}</td><td><strong>${esc(r.summer_population||'—')}</strong><small>summer · winter ${esc(r.winter_population||'—')}</small></td><td>${arcticVerificationBadge(r)}${r.verification_url?`<small><a href="${esc(r.verification_url)}" target="_blank" rel="noopener">Proof ↗</a></small>`:''}</td><td>${arcticCoordinateBadge(r)}</td></tr>`).join('')}
        </tbody></table></div>
      </section>
      <section class="panel current-additions-panel" style="margin-top:13px">
        <div class="panel-head"><div><h2>Current network additions absent from your supplied table</h2><p>These are current INTERACT or Ny-Ålesund network entries that were not represented as distinct rows in your 58-station reference list.</p></div><span class="badge good">${additions.length} CURRENT ADDITIONS</span></div>
        <div class="table-wrap"><table><thead><tr><th>Station / infrastructure</th><th>Current network location</th><th>Verification source</th><th>Map status</th></tr></thead><tbody>
          ${additions.map(r=>`<tr><td><strong>${esc(r.name)}</strong></td><td>${esc(r.location||'—')}</td><td><strong>${esc(r.verification_source||'Current network')}</strong><small><a href="${esc(r.verification_url)}" target="_blank" rel="noopener">Proof ↗</a></small></td><td>${arcticCoordinateBadge(r)}</td></tr>`).join('')}
        </tbody></table></div>
      </section>
      <section class="panel" style="margin-top:13px"><div class="panel-head"><div><h2>Arctic science & observing resources</h2><p>Public portals for research datasets, observing networks, sea ice and satellite context.</p></div></div><div class="resource-grid">${resources.map(x=>`<a class="resource-card" href="${esc(x.url)}" target="_blank" rel="noopener"><span>${esc(x.category)}</span><strong>${esc(x.name)}</strong><p>${esc(x.detail)}</p><small>${esc(x.update)}</small></a>`).join('')}</div></section>
      <section class="panel source-disclaimer" style="margin-top:13px"><div class="panel-head"><div><h2>Data boundaries</h2><p>Reference infrastructure is never treated as private live operations.</p></div></div><div class="data-boundary-grid"><div><strong>Mission operations</strong><span>Your roster, vehicle GPS, cargo, inventory and incident data remain organization-controlled.</span></div><div><strong>Research infrastructure</strong><span>INTERACT and official operator sources verify current research infrastructure. The supplied table remains separately attributable reference data.</span></div><div><strong>Occupancy</strong><span>Summer/winter population numbers from the supplied table are not live personnel counts and are never displayed as such.</span></div></div></section>`;
    initArcticMissionMap(locs,network.items||[]);
    bindPanelMapFullscreen('arcticNetworkPanel','arcticNetworkFullscreen');
  }

  function initArcticMissionMap(locs,stations=[]){
    const el=$('#arcticMissionMap');
    if(!el||!window.L)return;
    destroyLiveMap();
    const mission=locs.filter(l=>Number.isFinite(+l.latitude)&&Number.isFinite(+l.longitude)&&+l.latitude>=50);
    const verified=stations.filter(r=>String(r.verification_status||'').startsWith('verified_')&&Number.isFinite(+r.latitude)&&Number.isFinite(+r.longitude)&&+r.latitude>=55);
    const reference=stations.filter(r=>!String(r.verification_status||'').startsWith('verified_')&&Number.isFinite(+r.latitude)&&Number.isFinite(+r.longitude)&&+r.latitude>=55);
    state.liveMap=L.map(el,{zoomControl:true,attributionControl:false,minZoom:2,maxZoom:18,worldCopyJump:false,zoomAnimation:false,fadeAnimation:false,markerZoomAnimation:false}).setView([72,0],3);
    const topo=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',{maxZoom:18,updateWhenIdle:true,keepBuffer:1});
    const satellite=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{maxZoom:18,updateWhenIdle:true,keepBuffer:1});
    const missionLayer=L.layerGroup().addTo(state.liveMap);
    const verifiedLayer=L.layerGroup().addTo(state.liveMap);
    const referenceLayer=L.layerGroup();
    topo.addTo(state.liveMap);
    L.control.layers({'Topographic':topo,'Satellite':satellite},{'Mission locations':missionLayer,[`Verified current research (${verified.length})`]:verifiedLayer,[`Reference only (${reference.length})`]:referenceLayer},{position:'topright',collapsed:false}).addTo(state.liveMap);
    addMapDataControl(state.liveMap);
    mission.forEach(l=>L.circleMarker([+l.latitude,+l.longitude],{radius:6,weight:1.5,color:'#087f9d',fillColor:'#0ca8c1',fillOpacity:.88}).addTo(missionLayer).bindPopup(`<strong>${esc(l.name)}</strong><br>${esc(l.type||'Mission location')}<br><small>${n(l.latitude,5)}, ${n(l.longitude,5)}</small>`));
    verified.forEach(r=>L.circleMarker([+r.latitude,+r.longitude],{radius:5,weight:1.4,color:'#6b46ce',fillColor:'#8a63df',fillOpacity:.88}).addTo(verifiedLayer).bindTooltip(esc(r.name),{direction:'top',sticky:true}).bindPopup(`<div class="public-facility-popup"><span class="popup-kicker">VERIFIED CURRENT</span><strong>${esc(r.name)}</strong><br>${esc(r.location||'')}<br>${esc(r.operating_country||'')}<br><small>${esc(r.verification_source||'')}</small>${r.verification_url?`<br><a href="${esc(r.verification_url)}" target="_blank" rel="noopener">Proof ↗</a>`:''}<br><button class="popup-profile-btn" onclick="window.PolarOpsFeatures.openFacilityProfile('north',${r.id})">Facility profile</button></div>`));
    reference.forEach(r=>L.circleMarker([+r.latitude,+r.longitude],{radius:4,weight:1.1,color:'#7d8994',fillColor:'#a8b1b8',fillOpacity:.70}).addTo(referenceLayer).bindTooltip(`${esc(r.name)} · reference only`,{direction:'top',sticky:true}).bindPopup(`<div class="public-facility-popup"><span class="popup-kicker">REFERENCE ONLY</span><strong>${esc(r.name)}</strong><br>${esc(r.location||'')}<br><small>${esc(r.verification_note||'Not independently verified current.')}</small></div>`));
    const legend=L.control({position:'bottomleft'});
    legend.onAdd=()=>{const d=L.DomUtil.create('div','mission-map-legend');d.innerHTML='<strong>Map legend</strong><span><i class="legend-dot mission"></i>Mission location</span><span><i class="legend-dot research"></i>Verified current research</span><span><i class="legend-dot reference"></i>Reference only</span>';L.DomEvent.disableClickPropagation(d);return d};legend.addTo(state.liveMap);
    const bounds=[...mission.map(x=>[+x.latitude,+x.longitude]),...verified.map(x=>[+x.latitude,+x.longitude])];
    if(bounds.length>1)state.liveMap.fitBounds(L.latLngBounds(bounds).pad(.10),{maxZoom:4,animate:false});
    else if(bounds.length===1)state.liveMap.setView(bounds[0],6);
    setTimeout(()=>state.liveMap?.invalidateSize(),60);
  }

  function historicalVerificationBadge(r){
    const status=String(r.verification_status||'');
    if(status==='current_successor')return badge('Current successor',r.current_status==='Temporarily Closed'?'warn':'good');
    if(status==='current_joint_facility')return badge('Current joint facility',r.current_status==='Temporarily Closed'?'warn':'good');
    if(status==='current_subantarctic')return badge('Current · subantarctic','info');
    if(status.startsWith('current_'))return badge(r.current_status==='Temporarily Closed'?'Current · temporarily closed':'Verified current',r.current_status==='Temporarily Closed'?'warn':'good');
    if(status==='historical_subantarctic_not_current')return badge('Historical · subantarctic','info');
    return badge('Historical only','warn');
  }

  async function renderNetwork(){
    if(currentPole()==='north'){await renderArcticNetwork();return}
    setHeader('Antarctic Network','Official public facilities reference data, live environmental conditions and mission-base import.');
    const [sources,data,reference]=await Promise.all([
      api('/api/data-sources'),
      api('/api/public/facilities?limit=1000'),
      api('/api/public/research-stations-reference')
    ]);
    const facilities=data.items||[], countries=data.countries||[];
    const referenceStations=reference.items||[];
    const currentOnly=reference.current_only||[];
    const verificationSummary=reference.summary||{};
    const scopeCounts=verificationSummary.scope_counts||{};
    const comnap=(sources.sources||[]).find(x=>x.name==='COMNAP Facilities');
    $('#view').innerHTML=`
      <div class="source-strip network-source-strip">
        <div><span>Public facilities</span><strong>${data.total||0}</strong></div>
        <div><span>Countries/programmes</span><strong>${countries.length}</strong></div>
        <div><span>Facility source</span><strong>COMNAP</strong></div>
        <div><span>Historical map rows</span><strong>${reference.total||referenceStations.length}</strong></div>
        <div><span>Verified current rows</span><strong>${verificationSummary.current_rows??'—'}</strong></div>
        <div><span>Current-only facilities</span><strong>${verificationSummary.current_only_facilities??'—'}</strong></div>
        <div><span>Current weather</span><strong>Open-Meteo</strong></div>
        <div class="source-note"><strong>${esc(comnap?.last_status||'Never synced')}</strong><span>${comnap?.last_sync?`Last facility sync ${fmtDate(comnap.last_sync)}`:'Sync the official COMNAP facilities CSV to populate the global directory.'}</span></div>
      </div>
      <section class="panel network-map-panel"><div class="panel-head"><div><h2>Antarctic Treaty-area Facilities Map</h2><p>Current COMNAP facilities at or south of 60°S. Subantarctic records and coordinate anomalies stay in the directory but are not silently plotted as Antarctic bases.</p></div><span class="badge info">${scopeCounts.antarctic_treaty_area??0} Treaty-area</span></div>${facilities.length?'<div id="facilityNetworkMap" class="facility-network-map"></div><div class="live-map-note"><span>● Public infrastructure reference — not live occupancy</span><span>COMNAP Nov 2024 · '+(scopeCounts.subantarctic_reference||0)+' subantarctic · '+(scopeCounts.coordinate_anomaly||0)+' coordinate review</span></div>':'<div class="empty" style="min-height:220px"><div><strong>No facility map data yet</strong>Sync COMNAP to populate the official COMNAP directory.</div></div>'}</section>
      <section class="panel"><div class="panel-head"><div><h2>Antarctic Facilities Directory</h2><p>Reference facilities from national Antarctic programmes. This directory is public infrastructure metadata—not permission to use another operator's facility.</p></div><div class="panel-actions"><input class="search" id="facilitySearch" placeholder="Station, country or programme…"><select class="search" id="facilityCountry"><option value="">All countries</option>${countries.map(c=>`<option value="${esc(c.country)}">${esc(c.country)} (${c.count})</option>`).join('')}</select>${roleCan('commander','logistics')?'<button class="button secondary" id="syncFacilities">↻ Sync COMNAP</button>':''}</div></div>
        ${facilities.length?`<div class="table-wrap"><table><thead><tr><th>Facility</th><th>Country / Programme</th><th>Type</th><th>Operation</th><th>Coordinates</th><th>Current conditions</th><th>Actions</th></tr></thead><tbody id="facilityRows"></tbody></table></div>`:`<div class="empty" style="min-height:320px"><div><strong>No public facilities synchronized yet</strong>Press “Sync COMNAP” to download the official COMNAP Antarctic Facilities List into PolarOps. Internet access is required for the first sync.</div></div>`}
      </section>
      <section class="panel historical-stations-panel" style="margin-top:13px"><div class="panel-head"><div><h2>Research Stations Map — Verified Reference</h2><p>${esc(reference.warning||'Historical reference only.')} The supplied map lists ${referenceStations.length} numbered rows.</p></div><div class="panel-actions"><a class="button secondary" href="${esc(reference.source_url||'/research-stations-map.pdf')}" target="_blank" rel="noopener">Historical PDF</a><a class="button secondary" href="${esc(reference.current_source_info_url||'https://www.comnap.aq/antarctic-facilities-information')}" target="_blank" rel="noopener">Current COMNAP proof</a><a class="button secondary" href="https://add.scar.org/" target="_blank" rel="noopener">BAS/SCAR map</a></div></div>
        <div class="reference-source-note"><strong>${esc(reference.source||'COMNAP Research Stations Map')}</strong><span>Historical source: ${esc(reference.source_period||'1998-2005')} · verified against ${esc(reference.current_source_period||'November 2024')} current COMNAP facilities. Historical names never overwrite current operational status.</span></div>
        <div class="verification-summary"><div><span>Historical rows</span><strong>${verificationSummary.historical_rows??referenceStations.length}</strong></div><div><span>Current counterparts</span><strong>${verificationSummary.current_rows??'—'}</strong></div><div><span>Unique current facilities</span><strong>${verificationSummary.unique_current_matches??'—'}</strong></div><div><span>Historical only</span><strong>${verificationSummary.historical_only_rows??'—'}</strong></div><div><span>Temporarily closed</span><strong>${verificationSummary.temporarily_closed_matches??'—'}</strong></div></div>
        <div class="table-wrap reference-table-wrap"><table><thead><tr><th>Map #</th><th>Historical station</th><th>Country</th><th>Verification</th><th>Current COMNAP counterpart</th></tr></thead><tbody>${referenceStations.map(r=>`<tr><td class="mono">${r.map_number}</td><td><strong>${esc(r.station_name)}</strong><small>${esc(r.verification_note||'')}</small></td><td>${esc(r.country)}</td><td>${historicalVerificationBadge(r)}</td><td>${r.current_name?`<strong>${esc(r.current_name)}</strong><small>${esc(r.current_type||'Facility')} · ${esc(r.current_seasonality||'')} · ${esc(r.current_status||'')}</small>`:'<span class="muted">Not in Nov 2024 current directory</span>'}</td></tr>`).join('')}</tbody></table></div>
      </section>
      <section class="panel current-additions-panel" style="margin-top:13px"><div class="panel-head"><div><h2>Current COMNAP entries absent from the 1998–2005 map</h2><p>Verified current-directory facilities not represented by the supplied historical map. Absence from the old map does not necessarily mean a facility was built after 2005.</p></div><div class="panel-actions"><span class="badge info">${verificationSummary.current_only_facilities??currentOnly.length} facilities</span><span class="badge good">${verificationSummary.current_only_stations??0} stations</span></div></div>
        <div class="table-wrap"><table><thead><tr><th>Facility</th><th>Operator</th><th>Type</th><th>Established</th><th>Operation</th><th>Source scope</th></tr></thead><tbody>${currentOnly.map(f=>`<tr><td><strong>${esc(f.name)}</strong><small>${esc(f.antarctic_region||'Region not supplied')}</small></td><td>${esc(f.country||f.programme||'—')}</td><td>${badge(f.facility_type||'Facility','info')}</td><td>${esc(f.year_established||'—')}</td><td><strong>${esc(f.status||'—')}</strong><small>${esc(f.seasonality||'')}</small></td><td>${f.geographic_scope==='antarctic_treaty_area'?badge('Treaty area','good'):f.geographic_scope==='subantarctic_reference'?badge('Subantarctic','info'):badge('Coordinate review','danger')}${f.coordinate_warning?`<small>${esc(f.coordinate_warning)}</small>`:''}</td></tr>`).join('')}</tbody></table></div>
      </section>
      <section class="panel source-disclaimer" style="margin-top:13px"><div class="panel-head"><div><h2>Data boundaries</h2><p>Keep public reference data separate from private operational data.</p></div></div><div class="data-boundary-grid"><div><strong>Current public reference</strong><span>Facility names, operators, coordinates and status from the November 2024 COMNAP facilities data.</span></div><div><strong>Historical map reference</strong><span>The supplied COMNAP research-stations map is preserved as a separate 1998–2005 reference and never overwrites current facility status.</span></div><div><strong>Workers</strong><span>Live worker rosters/locations come only from your authorized feed, field check-ins or consented device GPS. PolarOps does not scrape people.</span></div></div></section>`;
    if(facilities.length){
      initFacilityNetworkMap(facilities);
      const paint=()=>{
        const q=($('#facilitySearch')?.value||'').toLowerCase(), country=$('#facilityCountry')?.value||'';
        const shown=facilities.filter(f=>(!country||f.country===country)&&[f.name,f.country,f.programme,f.facility_type,f.status].join(' ').toLowerCase().includes(q));
        $('#facilityRows').innerHTML=shown.map(f=>facilityRow(f)).join('')||'<tr><td colspan="7">No matching facilities.</td></tr>';
        $$('[data-facility-weather]').forEach(b=>b.onclick=()=>openFacilityWeather(facilities.find(x=>x.id===+b.dataset.facilityWeather)));
        $$('[data-facility-profile]').forEach(b=>b.onclick=()=>window.PolarOpsFeatures.openFacilityProfile('south',+b.dataset.facilityProfile));
        $$('[data-facility-import]').forEach(b=>b.onclick=()=>importFacility(facilities.find(x=>x.id===+b.dataset.facilityImport)));
      };
      $('#facilitySearch').oninput=paint;$('#facilityCountry').onchange=paint;paint();
    }
    if($('#syncFacilities'))$('#syncFacilities').onclick=async()=>{
      const b=$('#syncFacilities');b.disabled=true;let offset=0,total=0;
      try{
        while(true){
          b.textContent=`Syncing official data… ${total}`;
          const r=await api(`/api/public/facilities/sync?offset=${offset}&limit=35`,{method:'POST'});
          total+=r.synced||0;offset=r.next_offset||offset;
          if(!r.has_more)break;
        }
        toast('COMNAP facilities synchronized',`${total} facilities loaded`);await renderNetwork();
      } catch(err){toast('COMNAP sync failed',err.message,'danger',6000);b.disabled=false;b.textContent='↻ Sync COMNAP'}
    };
  }
  function initFacilityNetworkMap(facilities){
    const el=$('#facilityNetworkMap');
    if(!el||!window.L)return;
    destroyLiveMap();
    const points=facilities.filter(f=>f.geographic_scope==='antarctic_treaty_area'&&Number.isFinite(+f.latitude)&&Number.isFinite(+f.longitude));
    state.liveMap=L.map(el,{zoomControl:true,attributionControl:false,minZoom:2,maxZoom:18,worldCopyJump:false,zoomAnimation:false,fadeAnimation:false,markerZoomAnimation:false}).setView([-74,20],2);
    const satellite=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{maxZoom:18,updateWhenIdle:true,keepBuffer:1});
    const topo=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',{maxZoom:18,updateWhenIdle:true,keepBuffer:1});
    topo.addTo(state.liveMap);
    L.control.layers({'Topographic':topo,'Satellite':satellite},null,{position:'topright',collapsed:true}).addTo(state.liveMap);
    addMapDataControl(state.liveMap);
    points.forEach(f=>{
      const open=String(f.status||'').toLowerCase()==='open';
      L.circleMarker([+f.latitude,+f.longitude],{radius:5,weight:1.5,color:open?'#0877d4':'#d48a16',fillColor:open?'#118bea':'#f2a52a',fillOpacity:.86}).addTo(state.liveMap)
        .bindPopup(`<strong>${esc(f.name)}</strong><br>${esc(f.country||'Antarctic programme')} · ${esc(f.facility_type||'Facility')}<br>${esc(f.seasonality||'')} · ${esc(f.status||'Status not supplied')}<br><small>${n(f.latitude,5)}, ${n(f.longitude,5)}</small>`);
    });
    if(points.length>1)state.liveMap.fitBounds(L.latLngBounds(points.map(f=>[+f.latitude,+f.longitude])).pad(.04),{maxZoom:4,animate:false});
    setTimeout(()=>state.liveMap?.invalidateSize(),60);
  }

  function facilityRow(f){
    const weather=f.weather_observed_at?`<strong>${f.temperature_c==null?'—':`${n(f.temperature_c,1)} °C`}</strong><small>${f.wind_speed_kph==null?'':`${n(f.wind_speed_kph,1)} km/h wind · `}${fmtDate(f.weather_observed_at)} UTC</small>`:'<span class="muted">Not loaded</span>';
    return `<tr><td><strong>${esc(f.name)}</strong><small>${esc(f.status||'Status not supplied')}</small></td><td><strong>${esc(f.country||'—')}</strong><small>${esc(f.programme||'Programme not supplied')}</small></td><td>${badge(f.facility_type||'Facility','info')}</td><td>${esc(f.seasonality||'—')}</td><td class="mono"><strong>${f.latitude==null?'—':n(f.latitude,5)}</strong><small>${f.longitude==null?'—':n(f.longitude,5)}</small></td><td>${weather}</td><td><div class="row-actions"><button class="icon-btn" data-facility-profile="${f.id}">Profile</button><button class="icon-btn" data-facility-weather="${f.id}">Weather</button>${roleCan('commander','logistics')?`<button class="icon-btn" data-facility-import="${f.id}">Add to mission</button>`:''}</div></td></tr>`;
  }
  async function openFacilityWeather(f){
    if(!f)return;
    modal(`Current conditions — ${f.name}`,`${f.country||'Antarctica'} · ${f.programme||'National Antarctic Programme'}`,`<div class="empty" style="min-height:220px"><div><strong>Loading current conditions…</strong>Querying weather model at the facility coordinates.</div></div>`,true);
    try{
      const r=await api(`/api/public/facilities/${f.id}/weather?force=true`),w=r.weather;
      modal(`Current conditions — ${f.name}`,`${f.country||'Antarctica'} · ${n(f.latitude,4)}, ${n(f.longitude,4)}`,`
        <div class="weather-grid">
          <div><span>Temperature</span><strong>${w.temperature_c==null?'—':`${n(w.temperature_c,1)} °C`}</strong></div>
          <div><span>Feels like</span><strong>${w.apparent_temperature_c==null?'—':`${n(w.apparent_temperature_c,1)} °C`}</strong></div>
          <div><span>Humidity</span><strong>${w.relative_humidity==null?'—':`${n(w.relative_humidity)}%`}</strong></div>
          <div><span>Wind</span><strong>${w.wind_speed_kph==null?'—':`${n(w.wind_speed_kph,1)} km/h`}</strong></div>
          <div><span>Gusts</span><strong>${w.wind_gusts_kph==null?'—':`${n(w.wind_gusts_kph,1)} km/h`}</strong></div>
          <div><span>Pressure</span><strong>${w.surface_pressure_hpa==null?'—':`${n(w.surface_pressure_hpa,1)} hPa`}</strong></div>
        </div><div class="notice-card" style="margin-top:14px"><strong>${esc(w.source)}</strong><p>Observed/model timestamp: ${esc(w.observed_at||'—')} UTC. This is model-based current weather for the coordinates, not a direct sensor reading from the station.</p></div><div class="modal-actions"><button class="button primary" data-cancel>Close</button></div>`,true);$('[data-cancel]').onclick=closeModal;
    }catch(err){modal(`Weather unavailable — ${f.name}`,'The external weather source could not be reached.',`<div class="notice-card danger"><strong>Refresh failed</strong><p>${esc(err.message)}</p></div><div class="modal-actions"><button class="button primary" data-cancel>Close</button></div>`,true);$('[data-cancel]').onclick=closeModal;}
  }
  async function importFacility(f){
    if(!f)return;
    if(!confirm(`Add ${f.name} (${f.country||'Antarctica'}) to the current mission locations?`))return;
    try{const r=await api(`/api/public/facilities/${f.id}/import?expedition_id=${state.expeditionId}`,{method:'POST'});toast(r.created?'Facility added to mission':'Mission location updated',f.name);}
    catch(err){toast('Facility import failed',err.message,'danger')}
  }

  async function renderActivity(){ setHeader('Activity','Audit-friendly cross-module mission timeline.');const rows=await api(`/api/activity?expedition_id=${state.expeditionId}&limit=200`);$('#view').innerHTML=`<div class="panel"><div class="panel-head"><div><h2>Mission Activity Log</h2><p>${rows.length} latest operational events.</p></div></div><div class="table-wrap"><table><thead><tr><th>Time</th><th>Category</th><th>Event</th><th>Actor</th></tr></thead><tbody>${rows.map(a=>`<tr><td>${fmtDate(a.created_at)}</td><td>${badge(a.category,'info')}</td><td>${esc(a.message)}</td><td>${esc(a.user_name||'System')}</td></tr>`).join('')}</tbody></table></div></div>`; }

  async function renderSettings(){
    setHeader('Settings','Mission configuration, access control, backup and account security.');
    let users=[]; if(roleCan('commander')){try{users=await api('/api/users')}catch{}}
    const locs=await loadLocations();
    const exp=state.expeditions.find(e=>e.id===state.expeditionId);
    $('#view').innerHTML=`
      <div class="settings-grid">
        <section class="panel settings-section"><h3>Account</h3><p>Your signed-in identity and security controls.</p><div class="info-list"><div class="info-row"><span>Name</span><strong>${esc(state.user.name)}</strong></div><div class="info-row"><span>Email</span><strong>${esc(state.user.email)}</strong></div><div class="info-row"><span>Role</span><strong>${esc(state.user.role)}</strong></div></div><div class="card-actions"><button class="button secondary" id="changePassword">Change password</button></div></section>
        <section class="panel settings-section"><h3>Data & backup</h3><p>Export the central mission database as a portable JSON backup.</p><div class="info-list"><div class="info-row"><span>Storage</span><strong>Cloudflare D1 + R2 backups</strong></div><div class="info-row"><span>Offline support</span><strong>PWA shell + mutation queue</strong></div><div class="info-row"><span>Queued mutations</span><strong>${state.pending.length}</strong></div></div>${roleCan('commander')?'<div class="card-actions"><button class="button primary" id="downloadBackup">Download backup</button></div>':''}</section>
      </div>
      ${roleCan('commander')?`<section class="panel" style="margin-top:13px"><div class="panel-head"><div><h2>Mission Configuration</h2><p>Edit the current expedition or create another mission.</p></div><div class="panel-actions"><button class="button secondary" id="editExpedition">Edit mission</button><button class="button primary" id="newExpedition">+ New expedition</button></div></div><div class="info-list"><div class="info-row"><span>Name</span><strong>${esc(exp.name)}</strong></div><div class="info-row"><span>Region</span><strong>${esc(exp.region)}</strong></div><div class="info-row"><span>Status</span><strong>${esc(exp.status)}</strong></div><div class="info-row"><span>Mission window</span><strong>${esc(exp.start_date||'—')} → ${esc(exp.end_date||'—')}</strong></div></div></section>`:''}
      <section class="panel" style="margin-top:13px"><div class="panel-head"><div><h2>Mission Locations</h2><p>Mapped stations, camps, routes and transport points.</p></div>${roleCan('commander','logistics')?'<button class="button primary" id="addLocation">+ Add location</button>':''}</div><div class="table-wrap"><table><thead><tr><th>Name</th><th>Type</th><th>Latitude</th><th>Longitude</th><th>Actions</th></tr></thead><tbody>${locs.map(l=>`<tr><td><strong>${esc(l.name)}</strong></td><td>${badge(l.type,'info')}</td><td class="mono">${l.latitude??'—'}</td><td class="mono">${l.longitude??'—'}</td><td>${roleCan('commander','logistics')?`<button class="icon-btn" data-edit-location="${l.id}">Edit</button>`:'—'}</td></tr>`).join('')}</tbody></table></div></section>
      ${roleCan('commander')?`<section class="panel" style="margin-top:13px"><div class="panel-head"><div><h2>User Access</h2><p>Create role-based platform accounts.</p></div><button class="button primary" id="addUser">+ Add user</button></div><div class="table-wrap"><table><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th>Created</th></tr></thead><tbody>${users.map(u=>`<tr><td>${esc(u.name)}</td><td>${esc(u.email)}</td><td>${badge(u.role,'info')}</td><td>${badge(u.active?'Active':'Inactive',u.active?'good':'danger')}</td><td>${fmtDate(u.created_at)}</td></tr>`).join('')}</tbody></table></div></section>`:''}`;
    $('#changePassword').onclick=openPasswordChange;
    if($('#downloadBackup'))$('#downloadBackup').onclick=downloadBackup;
    if($('#addUser'))$('#addUser').onclick=openUserCreate;
    if($('#editExpedition'))$('#editExpedition').onclick=()=>openExpeditionForm(exp);
    if($('#newExpedition'))$('#newExpedition').onclick=()=>openExpeditionForm(null);
    if($('#addLocation'))$('#addLocation').onclick=()=>openLocationForm(null);
    $$('[data-edit-location]').forEach(b=>b.onclick=()=>openLocationForm(locs.find(x=>x.id===+b.dataset.editLocation)));
  }
  function openExpeditionForm(exp){
    modal(exp?'Edit expedition':'Create expedition',exp?'Update mission identity, dates and status.':'Create a new operational workspace.',`<form id="expForm"><div class="form-grid"><div class="field"><label>Name</label><input name="name" required value="${esc(exp?.name||'')}"></div><div class="field"><label>Region</label><input name="region" required value="${esc(exp?.region||'')}"></div><div class="field"><label>Start date</label><input type="date" name="start_date" value="${esc(exp?.start_date||'')}"></div><div class="field"><label>End date</label><input type="date" name="end_date" value="${esc(exp?.end_date||'')}"></div><div class="field"><label>Status</label><select name="status">${['Planning','Active','Paused','Completed','Archived'].map(x=>`<option ${exp?.status===x?'selected':''}>${x}</option>`).join('')}</select></div><div class="field full"><label>Description</label><textarea name="description">${esc(exp?.description||'')}</textarea></div></div><div class="modal-actions"><button type="button" class="button ghost" data-cancel>Cancel</button><button class="button primary">${exp?'Save mission':'Create expedition'}</button></div></form>`);
    $('[data-cancel]').onclick=closeModal;
    $('#expForm').onsubmit=async e=>{e.preventDefault();const f=e.currentTarget,p={name:formVal(f,'name'),region:formVal(f,'region'),start_date:formVal(f,'start_date')||null,end_date:formVal(f,'end_date')||null,status:formVal(f,'status'),description:formVal(f,'description')};try{if(exp){await api(`/api/expeditions/${exp.id}`,{method:'PATCH',body:JSON.stringify(p)});}else{const r=await api('/api/expeditions',{method:'POST',body:JSON.stringify(p)});state.expeditionId=r.id;localStorage.setItem('polarops_expedition',r.id);}state.expeditions=await api('/api/expeditions');closeModal();renderShell();await renderSettings();toast(exp?'Mission updated':'Expedition created',p.name)}catch(err){toast('Mission save failed',err.message,'danger')}};
  }
  function openLocationForm(loc){
    modal(loc?'Edit location':'Add mission location',loc?'Correct mapped coordinates or location type.':'Add a station, camp, route point or transport node.',`<form id="locForm"><div class="form-grid"><div class="field"><label>Name</label><input name="name" required value="${esc(loc?.name||'')}"></div><div class="field"><label>Type</label><select name="type">${['Station','Camp','Route','Transport','Cache','Research Site'].map(x=>`<option ${loc?.type===x?'selected':''}>${x}</option>`).join('')}</select></div><div class="field"><label>Latitude</label><input type="number" step="any" name="latitude" value="${loc?.latitude??''}" placeholder="-75.480"></div><div class="field"><label>Longitude</label><input type="number" step="any" name="longitude" value="${loc?.longitude??''}" placeholder="124.120"></div></div><div class="modal-actions"><button type="button" class="button ghost" data-cancel>Cancel</button><button class="button primary">${loc?'Save location':'Add location'}</button></div></form>`);
    $('[data-cancel]').onclick=closeModal;
    $('#locForm').onsubmit=async e=>{e.preventDefault();const f=e.currentTarget,p={expedition_id:state.expeditionId,name:formVal(f,'name'),type:formVal(f,'type'),latitude:numOrNull(formVal(f,'latitude')),longitude:numOrNull(formVal(f,'longitude'))};try{if(loc)await api(`/api/locations/${loc.id}`,{method:'PATCH',body:JSON.stringify(p)});else await api('/api/locations',{method:'POST',body:JSON.stringify(p)});closeModal();toast(loc?'Location updated':'Location added',p.name);renderSettings()}catch(err){toast('Location save failed',err.message,'danger')}};
  }
  function openPasswordChange(){ modal('Change password','Use at least eight characters.',`<form id="pwForm"><div class="field"><label>Current password</label><input name="current_password" type="password" required></div><div class="field"><label>New password</label><input name="new_password" type="password" minlength="8" required></div><div class="modal-actions"><button type="button" class="button ghost" data-cancel>Cancel</button><button class="button primary">Update password</button></div></form>`);$('[data-cancel]').onclick=closeModal;$('#pwForm').onsubmit=async e=>{e.preventDefault();const f=e.currentTarget;try{await api('/api/me/password',{method:'POST',body:JSON.stringify({current_password:formVal(f,'current_password'),new_password:formVal(f,'new_password')})});closeModal();toast('Password changed')}catch(err){toast('Password change failed',err.message,'danger')}}; }
  function openUserCreate(){ modal('Create user','Assign the minimum role needed for expedition work.',`<form id="userForm"><div class="form-grid"><div class="field"><label>Name</label><input name="name" required></div><div class="field"><label>Email</label><input name="email" type="email" required></div><div class="field"><label>Role</label><select name="role"><option value="field">Field</option><option value="logistics">Logistics</option><option value="commander">Commander</option></select></div><div class="field"><label>Temporary password</label><input name="password" type="password" minlength="8" required></div></div><div class="modal-actions"><button type="button" class="button ghost" data-cancel>Cancel</button><button class="button primary">Create user</button></div></form>`);$('[data-cancel]').onclick=closeModal;$('#userForm').onsubmit=async e=>{e.preventDefault();const f=e.currentTarget;try{await api('/api/users',{method:'POST',body:JSON.stringify({name:formVal(f,'name'),email:formVal(f,'email'),role:formVal(f,'role'),password:formVal(f,'password')})});closeModal();toast('User created');renderSettings()}catch(err){toast('User creation failed',err.message,'danger')}}; }
  async function downloadBackup(){ try{const res=await fetch('/api/backup',{headers:{Authorization:`Bearer ${state.token}`}});if(!res.ok)throw new Error('Backup request failed');const blob=await res.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`polarops-backup-${new Date().toISOString().slice(0,10)}.json`;a.click();URL.revokeObjectURL(url);toast('Backup downloaded')}catch(err){toast('Backup failed',err.message,'danger')} }

  async function init(){
    if('serviceWorker'in navigator){
      navigator.serviceWorker.register('/service-worker.js').then(()=>{state.serviceWorker=true}).catch(()=>{});
    }
    if(!state.token){renderLogin();return}
    try{await bootAuthed()}catch{renderLogin()}
  }

  window.PolarOpsCore={state,api,$,$$,esc,toast,modal,closeModal,navigate,setHeader,stat,badge,statusKind,fmtDate,fmtTime,n,numOrNull,formVal,loadLocations,destroyLiveMap,addMapDataControl,bindPanelMapFullscreen,roleCan};
  window.PolarOps={navigate,logout,renderView,connectRealtime,stopPersonnelGps,stopVehicleSimulation};
  init();
})();
