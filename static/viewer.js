// remote-screen viewer: the live screen on a canvas, mouse + keyboard back to it (Control), its microphone (Listen).
// Access key: from #key=… in the address (then kept in this browser, and taken out of the address), else asked.
// Embedded in another page it talks to that page with postMessage:
//   parent -> {type:'desktop-hello'}            : start telling it {type:'desktop-state', connected, screens}
//   parent -> {type:'desktop-shot', id, edge, mon?} : (mon = 1, 2… for that screen, else the one shown) reply {type:'desktop-shot', id, url (JPEG data URL) | error}
(function(){
const $ = id => document.getElementById(id);
const canvas = $('view'), ctx = canvas.getContext('2d'), stage = $('stage');
const ui = {perm: $('perm'), name: $('name'), state: $('state'), mon: $('mon'), ctl: $('ctl'), mic: $('mic'), clip: $('clip'), full: $('full'), gate: $('gate'), key: $('key'), gateMsg: $('gate-msg')};

// ---- key ----
const KEY = 'rs-key';
let key = '';
{ const m = /(?:^#|&)key=([^&]+)/.exec(location.hash);
  if(m){ key = decodeURIComponent(m[1]); try{ localStorage.setItem(KEY, key); }catch(e){}
         history.replaceState(null, '', location.pathname + location.search); }
  else try{ key = localStorage.getItem(KEY) || ''; }catch(e){} }

function askKey(msg){
  ui.gate.hidden = false; canvas.hidden = true;
  ui.gateMsg.textContent = msg || ui.gateMsg.textContent; ui.gateMsg.classList.toggle('bad', !!msg);
  ui.key.value = ''; ui.key.focus();
  setState('Needs the access key', 'bad');
}
ui.gate.addEventListener('submit', e => {
  e.preventDefault();
  key = ui.key.value.trim(); if(!key) return;
  try{ localStorage.setItem(KEY, key); }catch(e){}
  ui.gate.hidden = true; canvas.hidden = false; connect();
});

// ---- state ----
let ws = null, info = null, mon = 1, live = false, control = true, retry = 0, imgW = 0, imgH = 0;
function setState(text, cls){ ui.state.textContent = text; ui.state.className = 'state' + (cls ? ' ' + cls : ''); }
function setLive(on){ if(on === live) return; live = on; tellParent(); }

// ---- connection ----
const LOCAL = /^(127\.0\.0\.1|localhost|\[::1\])$/.test(location.hostname);   // on that computer itself: no key needed
function connect(){
  if(!key && !LOCAL){ askKey(); return; }
  setState('Connecting…');
  const s = ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');
  s.binaryType = 'arraybuffer';
  s.onopen = () => s.send(JSON.stringify({t: 'hello', key}));
  s.onmessage = e => { if(s === ws) (typeof e.data === 'string' ? onText(JSON.parse(e.data)) : onBinary(e.data)); };
  s.onclose = () => {
    if(s !== ws) return;
    ws = null; setLive(false); stopAudio(true);
    if(!ui.gate.hidden) return;
    const wait = Math.min(10, 1 + retry++) * 1000;
    setState('Reconnecting…', 'bad');
    setTimeout(connect, wait);
  };
}
function send(o){ if(ws && ws.readyState === 1) ws.send(JSON.stringify(o)); }

function onText(d){
  if(d.t === 'err' && d.msg === 'bad key'){
    try{ localStorage.removeItem(KEY); }catch(e){}
    key = ''; const s = ws; ws = null; s.close(); askKey('That key didn\'t work. Try again.'); return;
  }
  if(d.t === 'err'){ setState(d.msg, 'bad'); if(/Control/.test(d.msg)){ control = false; drawCtl(); } return; }
  if(d.t === 'info'){
    info = d; retry = 0;
    ui.name.textContent = d.name; document.title = d.name + ' · Remote screen';
    ui.mon.innerHTML = d.monitors.map((m, i) => '<option value="' + (i + 1) + '">Screen ' + (i + 1) + ' · ' + m.w + '×' + m.h + '</option>').join('');
    ui.mon.hidden = d.monitors.length < 2; if(mon > d.monitors.length) mon = 1; ui.mon.value = mon;
    ui.ctl.hidden = !d.control; control = d.control && control; drawCtl();
    ui.mic.hidden = !d.mic;
    setState('Connected', 'live');
    send({t: 'view', mon, on: true});
    if(wantAudio) send({t: 'mic', on: true});
    drawPerms(d.perms);
  }
  if(d.t === 'mic') onMic(d);
  if(d.t === 'perms') drawPerms(d.perms);
  if(d.t === 'clip') onClip(d.text);
}

// ---- clipboard: text copied there lands on this device's clipboard ----
// Browsers allow that only while this page has focus; otherwise (or when blocked) a Copy button does it on click.
let clipText = '', clipTimer = 0;
function onClip(text){
  clipText = text;
  navigator.clipboard.writeText(text).then(() => { ui.clip.hidden = true; flash('Copied to your clipboard'); },
                                           () => { ui.clip.hidden = false; flash('Text copied there: press Copy'); });
}
function flash(msg){
  clearTimeout(clipTimer); setState(msg, 'live');
  clipTimer = setTimeout(() => { if(ws && ws.readyState === 1) setState('Connected', 'live'); }, 2500);
}
ui.clip.addEventListener('click', () => navigator.clipboard.writeText(clipText).then(
  () => { ui.clip.hidden = true; flash('Copied to your clipboard'); }, () => flash('This browser blocked the clipboard')));

function onBinary(buf){
  const kind = new Uint8Array(buf, 0, 1)[0];
  if(kind === 1){
    createImageBitmap(new Blob([new Uint8Array(buf, 1)], {type: 'image/jpeg'})).then(bmp => {
      send({t: 'ack'});
      if(bmp.width !== imgW || bmp.height !== imgH){ imgW = canvas.width = bmp.width; imgH = canvas.height = bmp.height; fit(); }
      ctx.drawImage(bmp, 0, 0); bmp.close();
      setLive(true);
    }).catch(() => send({t: 'ack'}));
  }else if(kind === 2) playAudio(buf);
}

// the canvas fills the stage keeping its shape, so its box is exactly the picture
function fit(){
  if(!imgW) return;
  const W = stage.clientWidth, H = stage.clientHeight, k = Math.min(W / imgW, H / imgH);
  const w = Math.round(imgW * k), h = Math.round(imgH * k);
  Object.assign(canvas.style, {width: w + 'px', height: h + 'px', left: Math.round((W - w) / 2) + 'px', top: Math.round((H - h) / 2) + 'px'});
}
new ResizeObserver(fit).observe(stage);

ui.mon.addEventListener('change', () => { mon = +ui.mon.value; send({t: 'view', mon, on: true}); });
ui.full.addEventListener('click', () => document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen().catch(() => {}));

// ---- permissions (macOS): what's missing on that computer, ask again, restart it ----
const PERM = {screen: ['Screen Recording', 'only the desktop picture shows, not the windows'],
              control: ['Accessibility', 'Control can\'t move the mouse or type'],
              mic: ['Microphone', 'Listen stays silent']};
function drawPerms(p){
  const missing = Object.keys(PERM).filter(k => p && p[k] !== undefined && p[k] !== true);
  ui.perm.hidden = !missing.length;
  if(!missing.length) return;
  const who = info ? info.name : 'that computer';
  ui.perm.innerHTML = '';
  const t = document.createElement('span');
  t.textContent = 'On ' + who + ', allow ' + missing.map(k => PERM[k][0] + ' (' + PERM[k][1] + ')').join(', ') + ' for Remote Screen.';
  ui.perm.append(t);
  missing.forEach(k => {
    const b = document.createElement('button'); b.type = 'button'; b.textContent = 'Ask for ' + PERM[k][0];
    b.title = 'Shows macOS\'s prompt on ' + who + ', or opens the place in System Settings if it was turned down before';
    b.onclick = () => send({t: 'perms', ask: k}); ui.perm.append(b);
  });
  const g = document.createElement('span'); g.className = 'grow'; ui.perm.append(g);
  const r = document.createElement('button'); r.type = 'button'; r.textContent = 'Restart it';
  r.title = 'macOS uses a new permission after a restart'; r.onclick = () => { send({t: 'restart'}); setState('Restarting…'); };
  ui.perm.append(r);
}

// ---- control ----
function drawCtl(){ ui.ctl.classList.toggle('on', control); canvas.classList.toggle('ctl', control); }
ui.ctl.addEventListener('click', () => { control = !control; drawCtl(); if(control) canvas.focus(); });
const pos = e => { const r = canvas.getBoundingClientRect(); return {x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height}; };
let moveQ = null;
canvas.addEventListener('mousemove', e => {
  if(!control) return;
  if(!moveQ) requestAnimationFrame(() => { send(Object.assign({t: 'move'}, moveQ)); moveQ = null; });
  moveQ = pos(e);
});
canvas.addEventListener('mousedown', e => { if(!control) return; e.preventDefault(); canvas.focus(); send(Object.assign({t: 'btn', b: e.button, down: true}, pos(e))); });
canvas.addEventListener('mouseup', e => { if(!control) return; e.preventDefault(); send(Object.assign({t: 'btn', b: e.button, down: false}, pos(e))); });
canvas.addEventListener('contextmenu', e => { if(control) e.preventDefault(); });
let wheelX = 0, wheelY = 0;
canvas.addEventListener('wheel', e => {
  if(!control) return; e.preventDefault();
  const unit = e.deltaMode === 1 ? 1 : e.deltaMode === 2 ? 10 : 1 / 50;   // lines
  wheelX += e.deltaX * unit; wheelY += e.deltaY * unit;
  const dx = Math.trunc(wheelX), dy = Math.trunc(wheelY);
  if(dx || dy){ wheelX -= dx; wheelY -= dy; send({t: 'wheel', dx, dy: -dy}); }
}, {passive: false});
const held = new Set();
// paste (⌘V / Ctrl+V): this device's clipboard text goes to that computer's clipboard first, then the V key
// (held back until then). The browser's paste event carries the text; without one (e.g. Ctrl+V on a Mac), ask for it.
let pasting = null;
canvas.addEventListener('keydown', e => {
  if(!control) return;
  if(e.code === 'KeyV' && (e.metaKey || e.ctrlKey) && !e.altKey && info && info.clipboard){
    if(!e.repeat && !pasting){
      pasting = {code: e.code, key: e.key, done: false};
      setTimeout(() => { if(pasting && !pasting.done) navigator.clipboard.readText().then(t => finishPaste(t), () => finishPaste(null)); }, 150);
    }
    return;   // no preventDefault: the browser fires its paste event
  }
  e.preventDefault();
  held.add(e.code); send({t: 'key', code: e.code, key: e.key, down: true});
});
document.addEventListener('paste', e => {
  if(!pasting || pasting.done) return;
  e.preventDefault(); finishPaste(e.clipboardData ? e.clipboardData.getData('text/plain') : null);
});
function finishPaste(text){
  const p = pasting; if(!p || p.done) return;
  p.done = true; pasting = null;
  if(text) send({t: 'clipset', text});
  send({t: 'key', code: p.code, key: p.key, down: true});
  send({t: 'key', code: p.code, key: p.key, down: false});
}
canvas.addEventListener('keyup', e => {
  if(!control) return; e.preventDefault();
  if(e.code === 'KeyV' && !held.has('KeyV')) return;   // its press went out with the paste
  held.delete(e.code); send({t: 'key', code: e.code, key: e.key, down: false});
});
canvas.addEventListener('blur', () => { held.forEach(code => send({t: 'key', code, key: '', down: false})); held.clear(); });

// ---- microphone ----
let wantAudio = false, actx = null, playAt = 0, rate = 24000;
ui.mic.addEventListener('click', () => {
  wantAudio = !wantAudio;
  if(wantAudio){
    actx = actx || new AudioContext(); actx.resume();
    send({t: 'mic', on: true}); ui.mic.textContent = 'Listen…';
  }else{ send({t: 'mic', on: false}); stopAudio(); }
  drawMic();
});
function drawMic(){ ui.mic.classList.toggle('on', wantAudio); if(!wantAudio) ui.mic.textContent = 'Listen'; }
function onMic(d){
  if(d.on){ rate = d.rate; playAt = 0; ui.mic.textContent = 'Listening'; }
  else if(d.msg){ wantAudio = false; drawMic(); setState(d.msg, 'bad'); }
}
function playAudio(buf){
  if(!wantAudio || !actx) return;
  const pcm = new Int16Array(buf.slice(1)), n = pcm.length;
  if(!n) return;
  const b = actx.createBuffer(1, n, rate), ch = b.getChannelData(0);
  for(let i = 0; i < n; i++) ch[i] = pcm[i] / 32768;
  const now = actx.currentTime;
  if(playAt < now + 0.05 || playAt > now + 0.6) playAt = now + 0.15;   // start / catch up: keep the delay short
  const src = actx.createBufferSource(); src.buffer = b; src.connect(actx.destination); src.start(playAt);
  playAt += b.duration;
}
function stopAudio(keepWanted){ playAt = 0; if(!keepWanted){ wantAudio = false; drawMic(); } }

// ---- embedding page ----
let parentOrigin = null;
function tellParent(){
  if(parentOrigin) try{ parent.postMessage({type: 'desktop-state', connected: live, screens: info ? info.monitors.length : 0}, parentOrigin); }catch(e){}
}
addEventListener('message', e => {
  if(window.parent === window || e.source !== window.parent || !e.data) return;
  const d = e.data;
  if(d.type === 'desktop-hello'){ parentOrigin = e.origin; tellParent(); }
  if(d.type === 'desktop-shot'){
    const reply = o => parent.postMessage(Object.assign({type: 'desktop-shot', id: d.id}, o), e.origin);
    if(!live){ reply({error: 'not connected'}); return; }
    const edge = Math.max(64, Math.min(+d.edge || 1568, 8000));
    fetch('shot?edge=' + edge + '&mon=' + (+d.mon || mon), {headers: {'X-Key': key}, cache: 'no-store'})
      .then(r => r.ok ? r.blob() : Promise.reject(new Error('HTTP ' + r.status)))
      .then(b => new Promise(ok => { const f = new FileReader(); f.onload = () => ok(f.result); f.readAsDataURL(b); }))
      .then(url => reply({url}), err => reply({error: String(err.message || err)}));
  }
});
setInterval(tellParent, 3000);   // cheap; covers reloads of either side

connect();
})();
