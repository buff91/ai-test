const token = document.querySelector('meta[name="session-token"]').content;
const names = {attack:'공격', dodge:'회피', reload:'재장전', retreat:'후퇴', advance:'전진', wait:'대기'};
const actions = ['attack','dodge','reload','retreat','advance','wait'];
let provider = 'jev';
let latest = null;
let lastDecisionSignature = '';
let polling = false;

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}

async function api(path, data) {
  const options = {headers:{'X-Session-Token':token}};
  if (data !== undefined) {
    options.method = 'POST';
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(data);
  }
  const response = await fetch(path, options);
  if (path === '/api/export' && response.ok) return response.blob();
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '요청에 실패했습니다.');
  return result;
}

function config() {
  return {provider, compare:document.getElementById('compare').checked,
          key:document.getElementById('api-key').value.trim()};
}

async function command(path, data) {
  try {
    await api(path, data);
    if (data && data.key) document.getElementById('api-key').value = '';
    await poll();
  } catch (error) {
    showMessage(error.message, true);
  }
}

function showMessage(message, error=false) {
  document.getElementById('status-message').textContent = message;
  document.querySelector('.status-light').classList.toggle('off', error);
}

function setupControls() {
  document.querySelectorAll('[data-provider]').forEach(button => button.addEventListener('click', () => {
    provider = button.dataset.provider;
    document.querySelectorAll('[data-provider]').forEach(other => other.classList.toggle('selected', other === button));
    document.getElementById('api-key').closest('label').style.opacity = provider === 'jev' ? '1' : '.55';
  }));
  document.getElementById('run').addEventListener('click', () => command('/api/run', {...config(), apply:true}));
  document.getElementById('analyze').addEventListener('click', () => command('/api/run', {...config(), apply:false}));
  document.getElementById('auto').addEventListener('click', () => {
    const enabled = !(latest && latest.auto);
    command('/api/auto', {...config(), enabled});
  });
  document.getElementById('reset').addEventListener('click', () =>
    command('/api/reset', {scenario:document.getElementById('scenario').value}));
  document.getElementById('scenario').addEventListener('change', event =>
    command('/api/reset', {scenario:event.target.value}));
  document.getElementById('export').addEventListener('click', async event => {
    event.preventDefault();
    try {
      const blob = await api('/api/export');
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'game-decisions.json';
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { showMessage(error.message, true); }
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && latest && latest.auto) command('/api/auto', {enabled:false});
  });
  const holder = document.getElementById('manual-actions');
  actions.forEach(action => {
    const button = document.createElement('button');
    button.textContent = names[action];
    button.addEventListener('click', () => command('/api/manual', {action}));
    holder.appendChild(button);
  });
}

function updateGame(data) {
  const game = data.game;
  if (!game) return;
  const s = game.snapshot;
  document.getElementById('game-turn').textContent = `TURN ${String(s.turn).padStart(2,'0')}`;
  document.getElementById('hp-value').innerHTML = `${s.hp} <em>/ 100</em>`;
  document.getElementById('ammo-value').innerHTML = `${s.ammo} <em>/ 3</em>`;
  document.getElementById('enemy-value').innerHTML = `${s.enemy_hp} <em>/ 60</em>`;
  document.getElementById('hp-bar').style.width = `${s.hp}%`;
  document.getElementById('ammo-bar').style.width = `${s.ammo / 3 * 100}%`;
  document.getElementById('enemy-bar').style.width = `${s.enemy_hp / 60 * 100}%`;
  document.getElementById('game-outcome').textContent = s.outcome === 'playing' ?
    (game.events.join(' ') || '포탑을 파괴하고 오른쪽 출구에 도착하세요') :
    (s.outcome === 'won' ? 'MISSION COMPLETE' : 'MISSION FAILED');
  document.getElementById('state-text').textContent = game.description;
  document.getElementById('stat-turn').textContent = String(s.turn).padStart(2,'0');
  document.getElementById('connection-text').textContent = data.error && data.error.startsWith('게임 연결') ? '게임 연결 끊김' : '게임 실행 파일 연결됨';
  document.querySelector('.pulse').classList.toggle('off', !!(data.error && data.error.startsWith('게임 연결')));
  drawGame(s);
}

function drawGame(s) {
  const canvas = document.getElementById('game-canvas');
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const width = canvas.clientWidth, height = canvas.clientHeight;
  if (canvas.width !== Math.round(width*dpr) || canvas.height !== Math.round(height*dpr)) {
    canvas.width = Math.round(width*dpr); canvas.height = Math.round(height*dpr);
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr,0,0,dpr,0,0);
  const floor = height - 58;
  const x = tile => 55 + (width - 110) * tile / 8;
  const gradient = ctx.createLinearGradient(0,0,0,height);
  gradient.addColorStop(0,'#0e1c2a'); gradient.addColorStop(1,'#172e3d');
  ctx.fillStyle = gradient; ctx.fillRect(0,0,width,height);
  ctx.strokeStyle = '#193244'; ctx.lineWidth = 1;
  for(let gx=0;gx<width;gx+=40){ctx.beginPath();ctx.moveTo(gx,0);ctx.lineTo(gx,height);ctx.stroke();}
  for(let gy=0;gy<height;gy+=40){ctx.beginPath();ctx.moveTo(0,gy);ctx.lineTo(width,gy);ctx.stroke();}
  ctx.fillStyle='#203748';ctx.fillRect(0,floor,width,height-floor);
  ctx.fillStyle='#62d9e6';ctx.fillRect(0,floor,width,2);
  ctx.font='10px Menlo, monospace';ctx.textAlign='center';
  for(let tile=0;tile<=8;tile++){ctx.fillStyle='#7795a6';ctx.fillText(String(tile),x(tile),floor+28);}
  ctx.fillStyle='#496879';ctx.strokeStyle='#62d9e6';ctx.lineWidth=2;
  ctx.fillRect(x(2)-21,floor-51,42,51);ctx.strokeRect(x(2)-21,floor-51,42,51);
  ctx.fillStyle='#65d9e8';ctx.fillText('COVER',x(2),floor-64);
  ctx.fillStyle='#164339';ctx.fillRect(x(8)-23,floor-118,46,118);
  ctx.strokeStyle='#8be2a3';ctx.strokeRect(x(8)-23,floor-118,46,118);
  ctx.fillStyle='#8be2a3';ctx.fillText('EXIT',x(8),floor-130);
  if(s.enemy_hp>0){
    const e=x(s.enemy_x),color=s.enemy_windup?'#f06d78':'#f2aa68';
    ctx.fillStyle='#784d58';ctx.fillRect(e-27,floor-84,54,65);
    ctx.strokeStyle=color;ctx.lineWidth=2;ctx.strokeRect(e-27,floor-84,54,65);
    ctx.fillStyle=color;ctx.beginPath();ctx.arc(e,floor-53,8,0,Math.PI*2);ctx.fill();
    ctx.fillText(s.enemy_windup?'CHARGING':'TURRET',e,floor-100);
    if(s.enemy_windup){ctx.setLineDash([5,7]);ctx.beginPath();ctx.moveTo(e-28,floor-52);ctx.lineTo(x(s.player_x),floor-55);ctx.stroke();ctx.setLineDash([]);}
  }
  const p=x(s.player_x);ctx.fillStyle='#3d96a8';ctx.strokeStyle='#65d9e8';ctx.lineWidth=2;
  ctx.beginPath();ctx.moveTo(p,floor-80);ctx.lineTo(p+19,floor-65);ctx.lineTo(p+15,floor-26);ctx.lineTo(p-15,floor-26);ctx.lineTo(p-19,floor-65);ctx.closePath();ctx.fill();ctx.stroke();
  ctx.fillStyle='#65d9e8';ctx.fillText('PLAYER',p,floor-95);
  if(s.outcome!=='playing'){
    ctx.fillStyle='#07111ad9';ctx.fillRect(0,0,width,height);
    ctx.fillStyle=s.outcome==='won'?'#8be2a3':'#f06d78';ctx.font='bold 26px -apple-system,sans-serif';
    ctx.fillText(s.outcome==='won'?'MISSION COMPLETE':'MISSION FAILED',width/2,height/2);
  }
}

function modelHtml(result, secondary=false) {
  if(result.status!=='ok') return `<div class="model-block"><div class="model-name">${esc(result.provider)}</div><div class="decision-event">${esc(result.error)}</div></div>`;
  const rows=actions.map(action=>{
    const value=Math.max(0,Math.min(1,Number(result.probabilities?.[action]||0)));
    return `<div class="choice-row ${result.action===action?'winner':''}"><span>${names[action]}</span><div class="choice-track"><div class="choice-fill" style="width:${value*100}%"></div></div><span class="score">${(value*100).toFixed(0)}%</span></div>`;
  }).join('');
  return `<div class="model-block ${secondary?'secondary-block':''}"><div class="model-name ${secondary?'secondary':''}"><span>${esc(result.provider.toUpperCase())}</span><span>${esc(result.elapsed_ms)} ms</span></div>${rows}</div>`;
}

function updateDecisions(data) {
  const signature=JSON.stringify(data.decisions);
  if(signature===lastDecisionSignature) return;
  lastDecisionSignature=signature;
  const holder=document.getElementById('decisions');
  const records=data.decisions.slice(0,20);
  document.getElementById('decision-count').textContent=String(data.decisions.length);
  if(!records.length){holder.innerHTML='<div class="empty">모델을 실행하면 선택지별 판단이 이곳에 쌓입니다.</div>';return;}
  holder.innerHTML=records.map(record=>{
    const primary=record.results[0];
    const title=primary.status==='ok' ? `${esc(primary.provider.toUpperCase())} → ${names[primary.action]}` : '모델 호출 실패';
    return `<article class="decision-card ${primary.status==='ok'?'primary':'error'}"><div class="decision-head"><span class="decision-turn">TURN ${String(record.turn).padStart(2,'0')}</span><span>${record.applied?'GAME ACTION':'ANALYSIS ONLY'}</span><time>${esc(record.at)}</time></div><div class="decision-summary"><strong>${title}</strong><span>${record.applied?'게임에 적용됨':'게임 상태 유지'}</span></div>${record.results.map((r,i)=>modelHtml(r,i>0)).join('')}<div class="decision-event">${esc(record.events.join(' ')||'모델 판단 결과를 기록했습니다.')}</div></article>`;
  }).join('');
}

function updateMeta(data) {
  document.getElementById('stat-calls').textContent=String(data.decisions.reduce((total, item) => total + item.results.length, 0));
  const last=data.decisions[0]?.results[0];
  document.getElementById('stat-action').textContent=last?.status==='ok'?names[last.action]:'—';
  document.getElementById('stat-latency').textContent=last?`${Math.round(last.elapsed_ms)} ms`:'—';
  document.getElementById('key-status').textContent=data.key_set?'키가 메모리에 설정됨':'키가 설정되지 않았습니다';
  document.getElementById('auto').textContent=data.auto?'자동 플레이 중지':'자동 플레이';
  const message=data.error || (data.busy?'모델이 다음 행동을 판단하는 중입니다…':data.auto?'자동 플레이 중 · Esc로 중지할 수 있습니다':'게임을 관찰 중입니다. 모델을 실행해보세요.');
  showMessage(message,!!data.error);
  document.querySelectorAll('#manual-actions button').forEach(button=>button.disabled=!!data.busy);
  document.getElementById('run').disabled=!!data.busy;
  document.getElementById('analyze').disabled=!!data.busy;
  document.getElementById('events').innerHTML=data.events.map(event=>`<div class="event"><time>${esc(event.at)}</time><span>${esc(event.text)}</span></div>`).join('');
  const select=document.getElementById('scenario');
  if(!select.options.length) data.scenarios.forEach(name=>select.add(new Option(name,name)));
  if(data.game) select.value=data.game.snapshot.scenario;
}

async function poll() {
  if(polling) return;
  polling=true;
  try {
    const data=await api('/api/status');
    latest=data;
    updateGame(data);
    updateDecisions(data);
    updateMeta(data);
  } catch(error) {showMessage('WebView 연결 실패: '+error.message,true);}
  finally {polling=false;}
}

setupControls();
poll();
setInterval(poll,350);
window.addEventListener('resize',()=>{if(latest?.game)drawGame(latest.game.snapshot);});
