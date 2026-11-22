'use strict';

// UI enhancements: read-only dashboards, microphone-to-WAV capture, and canvas
// visualizations. The frozen E14 scoring and backend verification stay unchanged.
(() => {
  const $ = id => document.getElementById(id);
  const modeName = mode => mode === 'on' || mode === true || mode === 1 ? '开启降噪' : '关闭降噪';
  const state = { isReady: false, users: [], records: [], runtime: null, recording: null,
    requesting: false, finishing: false };
  const canvases = ['hero-wave', 'register-wave', 'verify-wave'];
  const escapeValue = value => value == null ? '—' : String(value);

  function setText(id, value) { const node = $(id); if (node) node.textContent = escapeValue(value); }
  function setClass(id, className, enable) { const node = $(id); if (node) node.classList.toggle(className, enable); }
  function safeDate(value) {
    if (!value) return '—';
    const date = new Date(value);
    return Number.isFinite(date.getTime()) ? date.toLocaleString('zh-CN') : String(value).slice(0, 22);
  }
  function scoreValue(row) { const n = Number(row.similarity ?? row.score ?? row.cosine_similarity); return Number.isFinite(n) ? n : null; }
  function thresholdValue(row) { const v = row.threshold; return v === null || v === undefined ? null : Number(v); }
  function userIds() { return [...new Set(state.users.map(u => String(u.user_id || '').replace(/-(off|on)$/, '')).filter(Boolean))]; }
  async function getData(path) {
    // `token` belongs to the existing study.js classic script; no persistence.
    const response = await fetch(path, {headers: {Authorization: 'Bearer ' + token}, cache: 'no-store'});
    if (!response.ok) throw new Error('接口读取失败：' + response.status);
    return response.json();
  }

  function refreshSelection() {
    const id = $('participant').value;
    const rows = ['off', 'on'].map(a => state.users.find(u => u.user_id === id + '-' + a));
    for (let i = 0; i < 2; i++) {
      const enabled = Boolean(rows[i]);
      const a = i === 0 ? 'off' : 'on';
      setText('arm-' + a + '-state', enabled ? '● 已注册' : '○ 未保存');
      setText('template-' + a, enabled ? '已生成' : '未注册');
      setText('withdraw-' + a, enabled ? '已存在' : '未保存');
      const bar = $('template-' + a + '-bar');
      if (bar) bar.style.width = enabled ? '100%' : '0%';
    }
    setText('verify-identity', identityOptionLabel(id) || '尚未选择');
    setText('register-identity', identityOptionLabel(id) || '尚未选择');
    setText('withdraw-identity', identityOptionLabel(id) || '尚未选择身份');
    const items = $('identity-cards')?.querySelectorAll('.identity-tile') || [];
    items.forEach(item => {
      item.classList.toggle('selected', item.dataset.id === id);
      item.setAttribute('aria-pressed', String(item.dataset.id === id));
      item.disabled = $('participant').disabled;
    });
    const mode = $('mode').value;
    setText('current-mode', identityDataUnavailable ? '暂时无法确认注册状态' :
      !id ? '请先选择身份' : mode ? modeName(mode) : '还未保存声纹');
  }

  function renderIdentityCards() {
    const holder = $('identity-cards');
    if (!holder) return;
    const q = $('identity-search').value.toLowerCase().trim();
    const ids = userIds().filter(id => id.includes(q) || state.users.some(u => u.user_id.startsWith(id + '-') && (u.username || '').toLowerCase().includes(q)));
    setText('identity-count', userIds().length + ' 个');
    holder.replaceChildren();
    if (!ids.length) {
      const empty = document.createElement('p'); empty.className = 'muted';
      empty.textContent = q ? '找不到这个身份' : '还没有身份，请先添加。';
      holder.append(empty);
      return;
    }
    for (const id of ids) {
      const off = state.users.some(u => u.user_id === id + '-off');
      const on = state.users.some(u => u.user_id === id + '-on');
      const node = document.createElement('button');
      node.type = 'button'; node.className = 'identity-tile'; node.dataset.id = id;
      const avatar = document.createElement('span'); avatar.className = 'avatar'; avatar.textContent = '♙';
      const text = document.createElement('span'); text.className = 'tile-text';
      const bold = document.createElement('b'); bold.textContent = identityOptionLabel(id);
      const sub = document.createElement('small'); sub.textContent = off && on ? '两种方式均已保存' : '待继续保存';
      text.append(bold, sub); node.append(avatar, text);
      node.addEventListener('click', () => {
        $('participant').value = id;
        $('participant').dispatchEvent(new Event('change', {bubbles: true}));
        refreshSelection();
      });
      holder.append(node);
    }
    refreshSelection();
  }

  function renderRecords() {
    const tbody = $('records-body');
    tbody.replaceChildren();
    if (!state.records.length) {
      const tr = tbody.insertRow(); const td = tr.insertCell(); td.colSpan = 6; td.textContent = '暂无可显示的历史记录'; return;
    }
    for (const row of state.records.slice(0, 35)) {
      const tr = tbody.insertRow();
      const cells = [safeDate(row.created_time ? row.created_time.replace(' ', 'T') + 'Z' : row.created_at),
        row.user_id ?? row.identity_id ?? '—', modeName(row.denoise ?? row.mode),
        scoreValue(row) === null ? '—' : scoreValue(row).toFixed(6),
        thresholdValue(row) === null ? '无判定' : thresholdValue(row).toFixed(6),
        row.accepted == null ? '相似度' : row.accepted ? '通过' : '拒绝'];
      cells.forEach((value, index) => {
        const td = tr.insertCell(); td.textContent = String(value);
        if (index === 5) td.className = row.accepted == null ? '' : row.accepted ? 'ok' : 'bad';
      });
    }
  }

  let refreshGeneration = 0;
  async function refreshData() {
    if (!state.isReady) return;
    const generation = ++refreshGeneration;
    try {
      const [userList, recordList] = await Promise.all([getData('/api/users'), getData('/api/records')]);
      if (generation !== refreshGeneration) return;
      state.users = Array.isArray(userList) ? userList : [];
      state.records = Array.isArray(recordList) ? recordList : [];
      setText('stat-users', userIds().length);
      setText('stat-templates', state.users.length);
      setText('stat-records', state.records.length);
      renderIdentityCards(); renderRecords(); controls();
    } catch (error) {
      setText('stat-health', '读取异常');
      setText('identity-count', '状态读取失败');
    }
  }

  function updateRuntime(s) {
    if (!state.isReady) return;
    try {
      state.runtime = s;
      const isReal = s.runtime_mode === 'E14';
      const model = isReal ? '声纹识别已就绪' : '演示模式';
      $('recording-mode-note').hidden = isReal;
      $('recording-mode-note').textContent = '当前是合成演示。请使用系统样例录音。';
      if (!isReal) { setText('register-live-text', '合成演示使用提供的录音文件'); $('record-enroll').disabled = true; $('record-verify').disabled = true; }
      const policy = Boolean(s.authentication_ready && s.denoise_ready);
      setText('model-badge', '● ' + model);
      setText('policy-badge', policy ? (isReal ? '已启用演示判定' : '合成演示') : '相似度模式');
      setText('stat-health', s.model_loaded || !isReal ? '运行正常' : '模型未就绪');
      setText('settings-model', model + ' · ' + (s.model_version ? String(s.model_version).slice(0, 12) : '无版本信息'));
      setText('settings-policy', policy ? (isReal ? '本地演示判定' : '合成演示') : '只显示相似度');
      setText('settings-preprocess', s.preprocessing_versions ? '双臂已绑定' : '等待状态信息');
      setText('hero-state', isReal ? '麦克风待开启' : '合成演示');
    } catch { setText('stat-health', '连接失败'); }
  }

  function drawOne(canvas, data, colorA, colorB) {
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const width = Math.max(100, Math.floor(rect.width || 460));
    const height = Math.max(40, Math.floor(rect.height || 140));
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    if (canvas.width !== Math.floor(width * ratio)) canvas.width = Math.floor(width * ratio);
    if (canvas.height !== Math.floor(height * ratio)) canvas.height = Math.floor(height * ratio);
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.strokeStyle = '#32618e55'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, height / 2); ctx.lineTo(width, height / 2); ctx.stroke();
    const gradient = ctx.createLinearGradient(0, 0, width, 0);
    gradient.addColorStop(0, colorA); gradient.addColorStop(.57, '#29c9fc'); gradient.addColorStop(1, colorB);
    ctx.beginPath(); ctx.strokeStyle = gradient; ctx.lineWidth = 2.1;
    ctx.shadowColor = '#17cfff'; ctx.shadowBlur = 11;
    for (let i = 0; i < width; i++) {
      const f = Math.floor(i / Math.max(1, width - 1) * (data.length - 1));
      const y = (height * .5) + Math.max(-1, Math.min(1, data[f])) * height * .40;
      if (i === 0) ctx.moveTo(i, y); else ctx.lineTo(i, y);
    }
    ctx.stroke(); ctx.shadowBlur = 0;
  }
  function drawAll(signal) {
    const buffer = signal && signal.length ? signal : new Float32Array(512);
    for (const id of canvases) drawOne($(id), buffer, '#06ebea', '#b56aff');
  }
  const waveformCache = new WeakMap();
  let playback, playbackURL;
  function stopPlayback() {
    if (playback) { playback.pause(); playback = null; }
    if (playbackURL) { URL.revokeObjectURL(playbackURL); playbackURL = null; }
  }
  function fileWave(file) {
    if (!waveformCache.has(file)) {
      waveformCache.set(file, file.arrayBuffer().then(async bytes => {
        const decoded = await new OfflineAudioContext(1, 1, 16000).decodeAudioData(bytes);
        const data = decoded.getChannelData(0);
        const envelope = new Float32Array(512);
        for (let i = 0; i < 256; i++) {
          const start = Math.floor(i * data.length / 256);
          const end = Math.max(start + 1, Math.floor((i + 1) * data.length / 256));
          let low = 1, high = -1;
          for (let j = start; j < end && j < data.length; j++) {
            low = Math.min(low, data[j]); high = Math.max(high, data[j]);
          }
          envelope[i * 2] = low; envelope[i * 2 + 1] = high;
        }
        return envelope;
      }));
    }
    return waveformCache.get(file);
  }
  async function drawFile(canvas, file) {
    if (!file) { drawOne(canvas, new Float32Array(2), '#06ebea', '#b56aff'); return; }
    try {
      const wave = await fileWave(file);
      if (canvas.isConnected) drawOne(canvas, wave, '#06ebea', '#b56aff');
    } catch { canvas.setAttribute('aria-label', '此录音无法预览'); }
  }
  function controls() {
    const locked = busy || identityDataUnavailable || window.zrsCaptureLocked();
    const canRecord = state.runtime?.runtime_mode === 'E14';
    $('record-enroll').disabled = locked || !canRecord || !$('participant').value || $('clips').files.length >= 5;
    $('record-verify').disabled = locked || !canRecord || !$('mode').value;
    $('stop-recording').disabled = state.recording?.kind !== 'enroll';
    $('stop-verify-recording').disabled = state.recording?.kind !== 'verify';
    $('clear-recordings').disabled = locked || !$('clips').files.length;
    const id = $('participant').value;
    $('compare-both').disabled = locked || !['off','on'].every(a => state.users.some(u => u.user_id === id + '-' + a)) || !$('probe').files.length;
    $('clip-cards').querySelectorAll('button').forEach(button => {
      button.disabled = locked || (button.dataset.retake === '1' && !canRecord);
    });
  }
  function updateClipCards() {
    const holder = $('clip-cards'); holder.replaceChildren();
    const files = Array.from($('clips').files);
    for (let i = 0; i < 5; i++) {
      const file = files[i];
      const box = document.createElement('div'); box.className = 'clip-tile' + (file ? ' ok' : '');
      const title = document.createElement('b'); title.textContent = '录音 ' + (i + 1);
      const canvas = document.createElement('canvas'); canvas.setAttribute('aria-hidden', 'true');
      const foot = document.createElement('span'); foot.className = 'clip-status'; foot.textContent = file ? '已就绪' : '待录制';
      box.append(title, canvas, foot); holder.append(box);
      drawFile(canvas, file);
      if (!file) continue;
      const actions = document.createElement('div'); actions.className = 'clip-actions';
      for (const [label, action] of [['试听', async () => {
        stopPlayback();
        playbackURL = URL.createObjectURL(file); playback = new Audio(playbackURL);
        playback.addEventListener('ended', stopPlayback, {once:true});
        try { await playback.play(); } catch { setText('result','无法播放此录音，请检查文件。'); stopPlayback(); }
      }], ['重录', () => startCapture('enroll', i)]]) {
        const button = document.createElement('button'); button.type = 'button'; button.textContent = label;
        button.dataset.retake = label === '重录' ? '1' : '0';
        button.setAttribute('aria-label', label + '录音 ' + (i + 1));
        button.addEventListener('click', action); actions.append(button);
      }
      box.append(actions);
    }
    setText('register-live-text', '注册进度 ' + files.length + ' / 5');
    controls();
  }

  function wavFromSamples(samples, incomingRate) {
    const sampleRate = 16000;
    const length = Math.round(samples.length * sampleRate / incomingRate);
    const view = new DataView(new ArrayBuffer(44 + length * 2));
    const four = (o, s) => { for (let i = 0; i < s.length; i++) view.setUint8(o + i, s.charCodeAt(i)); };
    four(0,'RIFF'); view.setUint32(4, 36 + length * 2, true); four(8,'WAVE'); four(12,'fmt ');
    view.setUint32(16,16,true); view.setUint16(20,1,true); view.setUint16(22,1,true);
    view.setUint32(24,sampleRate,true); view.setUint32(28,sampleRate*2,true); view.setUint16(32,2,true); view.setUint16(34,16,true);
    four(36,'data'); view.setUint32(40,length*2,true);
    for (let i = 0; i < length; i++) {
      const at = i * incomingRate / sampleRate, a = Math.floor(at), mix = at - a;
      const value = (samples[a] || 0) * (1-mix) + (samples[Math.min(a+1,samples.length-1)] || 0) * mix;
      view.setInt16(44 + i*2, Math.max(-32768,Math.min(32767,Math.round(value * 32767))), true);
    }
    return new Blob([view], {type:'audio/wav'});
  }

  async function startCapture(kind, slot = null) {
    if (busy || identityDataUnavailable || window.zrsCaptureLocked() || !$('participant').value || (kind === 'verify' && !$('mode').value)) return;
    stopPlayback();
    if (state.runtime && state.runtime.runtime_mode !== 'E14') { setText('result','请使用提供的演示录音文件。'); return; }
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      setText('result', '请在 localhost 或 HTTPS 环境打开页面，并使用支持麦克风的浏览器。'); return;
    }
    let stream, ctx, timer, expired = false;
    state.requesting = true; render();
    setText('result','正在请求麦克风权限…');
    try {
      const access = navigator.mediaDevices.getUserMedia({audio:{channelCount:1, echoCancellation:false, noiseSuppression:false},video:false})
        .then(source => { if (expired) source.getTracks().forEach(track => track.stop()); return source; });
      stream = await Promise.race([access, new Promise((_, reject) => {
        timer = setTimeout(() => { expired = true; reject(Error('timeout')); }, 20000);
      })]);
    } catch {
      state.requesting = false; render();
      setText('result', expired ? '麦克风授权超时，请允许权限后重试。' : '无法使用麦克风，请检查权限或设备后重试。');
      return;
    } finally { clearTimeout(timer); }
    try {
      ctx = new (window.AudioContext || window.webkitAudioContext)(); await ctx.resume();
      const source = ctx.createMediaStreamSource(stream);
      const processor = ctx.createScriptProcessor(4096,1,1);
      const analyser = ctx.createAnalyser(); analyser.fftSize = 1024;
      const silent = ctx.createGain(); silent.gain.value = 0;
      source.connect(analyser); source.connect(processor); processor.connect(silent); silent.connect(ctx.destination);
      const rec = {kind, slot, ctx, stream, source, processor, analyser, silent, chunks:[], samples:0, started:performance.now(), stopping:false};
      processor.onaudioprocess = evt => {
        const pcm = evt.inputBuffer.getChannelData(0).slice(); rec.chunks.push(pcm); rec.samples += pcm.length;
        if (rec.samples / ctx.sampleRate >= (kind === 'enroll' ? 5 : 6.5)) stopCapture();
      };
      state.recording = rec;
      state.requesting = false; render();
      document.body.classList.add('recording');
      setText('hero-state','正在录音');
      setClass('live-dot','green',true);
      setText('mic-live',kind === 'enroll' ? '正在采集注册音频' : '正在采集验证音频');
      setText('result','正在录音，完成后可以提交。');
      const wave = new Float32Array(analyser.fftSize);
      let lastFrame = 0;
      const refresh = now => {
        if (state.recording !== rec) return;
        if (now - lastFrame >= 33) {
          analyser.getFloatTimeDomainData(wave);
          for (const id of ['hero-wave', kind === 'enroll' ? 'register-wave' : 'verify-wave']) {
            const canvas = $(id), rect = canvas.getBoundingClientRect();
            if (rect.bottom > 0 && rect.top < innerHeight) drawOne(canvas, wave, '#06ebea', '#b56aff');
          }
          lastFrame = now;
        }
        const sec = rec.samples / ctx.sampleRate;
        setText('mic-timer',sec.toFixed(1) + ' s');
        setText('register-live-text', (kind === 'enroll' ? '正在录制注册音频' : '正在录制验证音频') + ' · ' + sec.toFixed(1) + '秒');
        requestAnimationFrame(refresh);
      };
      requestAnimationFrame(refresh);
    } catch (error) {
      stream.getTracks().forEach(t => t.stop()); if (ctx) await ctx.close();
      state.requesting = false; render(); setText('result','录音初始化失败，请重试。');
    }
  }
  async function stopCapture() {
    const rec = state.recording;
    if (!rec || rec.stopping) return;
    rec.stopping = true; state.recording = null; state.finishing = true; render();
    document.body.classList.remove('recording');
    setText('hero-state','录音已停止');
    rec.processor.onaudioprocess = null;
    rec.stream.getTracks().forEach(t => t.stop());
    rec.source.disconnect(); rec.processor.disconnect(); rec.analyser.disconnect(); rec.silent.disconnect();
    try { await rec.ctx.close(); } catch {}
    const count = rec.samples;
    const data = new Float32Array(count);
    let offset = 0; for (const chunk of rec.chunks) {data.set(chunk,offset);offset+=chunk.length;}
    rec.chunks.length = 0;
    state.finishing = false; render();
    setClass('live-dot','green',false); setText('mic-live','麦克风已停止'); setText('mic-timer','00:00');
    if (count / rec.ctx.sampleRate < 3) {
      setText('result','录音短于3秒，未采用此音频，请重新录制。');
      updateClipCards(); return;
    }
    const usable = rec.kind === 'enroll' ? data.subarray(0, Math.min(data.length, Math.floor(rec.ctx.sampleRate * 4.95))) : data;
    const blob = wavFromSamples(usable,rec.ctx.sampleRate);
    const name = (rec.kind==='enroll' ? 'enroll-' + (Array.from($('clips').files).length+1) : 'probe') + '.wav';
    const file = new File([blob], name, {type:'audio/wav'});
    const list = new DataTransfer();
    const input = $(rec.kind === 'enroll' ? 'clips' : 'probe');
    const files = rec.kind === 'enroll' ? Array.from(input.files) : [];
    if (rec.slot === null) files.push(file); else files[rec.slot] = file;
    for (const item of files) list.items.add(item);
    input.files = list.files;
    input.dispatchEvent(new Event('change',{bubbles:true}));
    setText('result','音频已保存到当前浏览器表单（' + (count / rec.ctx.sampleRate).toFixed(1) + '秒），请检查后提交。');
    drawFile($(rec.kind === 'enroll' ? 'register-wave' : 'verify-wave'), file);
    drawFile($('hero-wave'), file);
  }

  function reflectOutcome(data) {
    const value = data ? scoreValue(data) : null;
    setText('score-value', value === null ? '—' : value.toFixed(4));
    setText('result-mode', data ? modeName(data.denoise) : '—');
    setText('result-threshold', data ? (data.threshold == null ? '无判定' : data.threshold.toFixed(6)) : '—');
    setText('result-time', Number.isFinite(data?.processing_ms) ? data.processing_ms + ' ms' : '—');
    $('score-dial').style.setProperty('--score-angle', Math.round(Math.max(0, Math.min(value ?? 0, 1)) * 360) + 'deg');
    const banner = $('decision-banner');
    const outcome = data ? (data.accepted == null ? 'diagnostic' : data.accepted ? 'accepted' : 'rejected') : '';
    banner.className = 'decision-banner ' + outcome;
    banner.textContent = {accepted:'验证通过', rejected:'未通过', diagnostic:'只显示相似度'}[outcome] || '等待验证';
  }

  async function compareBoth() {
    const id = $('participant').value, file = $('probe').files[0];
    if (!id || !file) {setText('result','请先选择身份并上传一段验证 WAV。');return;}
    if (busy || identityDataUnavailable || window.zrsCaptureLocked()) return;
    if (!window.confirm('将使用同一段 WAV 分别调用两次真实验证，并写入两条评分记录。继续吗？'))return;
    const holder = $('compare-results');
    await run(async () => {
    holder.textContent='正在比较…';
    try {
      const results=[];
      for(const arm of ['off','on']){
        const form=new FormData();form.set('user_id',id+'-'+arm);form.set('denoise',String(arm==='on'));form.set('file',file);
        const response=await fetch('/api/verify',{method:'POST',headers:{Authorization:'Bearer '+token},body:form,cache:'no-store'});
        const data=await response.json();
        if(!response.ok)throw new Error(modeName(arm)+'：'+(data.detail||response.status));
        results.push({arm,data});
      }
      holder.replaceChildren();
      for(const entry of results){const box=document.createElement('div');box.className='mode-result';
        const d=entry.data;box.textContent=modeName(entry.arm)+' · '+Number(d.similarity).toFixed(6)+' · '+(d.accepted==null?'诊断':d.accepted?'接受':'拒绝');
        holder.append(box);}
      return '已比较开启和关闭降噪的结果。';
    } catch (error) { holder.textContent=error.message; throw error; }
    });
  }

  function activateNavigation() {
    const anchors = [...document.querySelectorAll('.nav-item')];
    const select = hash => anchors.forEach(anchor => {
      const current = anchor.hash === hash;
      anchor.classList.toggle('active', current);
      if (current) anchor.setAttribute('aria-current', 'location'); else anchor.removeAttribute('aria-current');
    });
    select(location.hash || '#section-dashboard');
    window.addEventListener('hashchange', () => select(location.hash || '#section-dashboard'));
    anchors.forEach(anchor => anchor.addEventListener('click', event => {
      if (!state.isReady) { event.preventDefault(); $('account').focus(); }
    }));
    for(const button of document.querySelectorAll('.quick-link'))button.addEventListener('click',event=>{
      if (!state.isReady) {
        event.preventDefault(); setText('session','请先登录。'); $('account').focus();
      } else if (button.hash === '#section-register' && !$('participant').value) {
        event.preventDefault();
        $('create').click();
      }
    });
  }
  window.zrsCaptureLocked = () => Boolean(state.recording || state.requesting || state.finishing);
  function drawHeroArt() {
    const ctx = $('hero-art').getContext('2d');
    const light = ctx.createLinearGradient(55, 105, 585, 310);
    light.addColorStop(0, '#5375ba00');
    light.addColorStop(.22, '#48c2d9');
    light.addColorStop(.51, '#dcfaff');
    light.addColorStop(.78, '#9b92ff');
    light.addColorStop(1, '#6376b500');
    ctx.globalCompositeOperation = 'screen';
    ctx.strokeStyle = light;
    for (let strand = 0; strand < 34; strand++) {
      ctx.beginPath();
      ctx.globalAlpha = .16 + .51 * strand / 34;
      ctx.lineWidth = strand % 7 === 0 ? 2.6 : 1.2;
      for (let x = 34; x <= 606; x += 4) {
        const t = (x - 34) / 572;
        const envelope = Math.sin(Math.PI * t) ** 1.55;
        const y = 204 + (.52 - t) * 42 + envelope *
          (75 * Math.sin(7.4 * t + .045 * strand) +
           (strand - 17) * 3.5 * Math.cos(8.2 * t - 1));
        if (x === 34) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.stroke();
    }
  }

  function boot() {
    drawHeroArt();updateClipCards();drawAll();activateNavigation();
    $('identity-search').addEventListener('input',renderIdentityCards);
    $('participant').addEventListener('change',() => {refreshSelection();});
    $('clips').addEventListener('change', () => { stopPlayback(); updateClipCards(); render(); });
    $('probe').addEventListener('change',async() => { const f=$('probe').files[0];if(f)setText('mic-live','已选择验证音频：'+f.name);drawFile($('verify-wave'),f);render();});
    $('record-enroll').addEventListener('click',()=>startCapture('enroll'));
    $('record-verify').addEventListener('click',()=>startCapture('verify'));
    $('stop-recording').addEventListener('click',stopCapture);
    $('stop-verify-recording').addEventListener('click',stopCapture);
    $('clear-recordings').addEventListener('click',()=>{$('clips').value='';stopPlayback();updateClipCards();render();});
    $('compare-both').addEventListener('click',compareBoth);
    $('refresh-records').addEventListener('click',refreshData);
    $('logout').addEventListener('click', async () => {
      $('logout').disabled = true;
      try {
        const response = await fetch('/session/logout', {method:'POST', headers:{Authorization:'Bearer '+token}});
        if (!response.ok) throw new Error('退出失败，请重试。');
        token = '';
        location.assign('/');
      } catch {
        setText('result','暂时无法退出，请重试。');
        $('logout').disabled = false;
      }
    });
    document.addEventListener('zrs:ready', event => {
      state.isReady = true;
      $('login-panel').hidden = true;
      updateRuntime(event.detail);
      refreshData();
    });
    document.addEventListener('zrs:data', () => { updateClipCards(); refreshData(); });
    document.addEventListener('zrs:controls', () => { controls(); refreshSelection(); });
    document.addEventListener('zrs:outcome', event => reflectOutcome(event.detail));
    document.addEventListener('zrs:selection', () => {
      stopPlayback(); updateClipCards(); reflectOutcome(null); drawAll();
      setText('compare-results','尚无对照结果');
    });
    $('mode').addEventListener('change', refreshSelection);
    window.addEventListener('pagehide',()=>{if(state.recording)stopCapture();});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);else boot();
})();
