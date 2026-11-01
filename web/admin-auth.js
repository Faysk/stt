/* QR is a public pointer; approval still requires the authorized passkey. */
(() => {
  const byId = id => document.getElementById(id);
  const recovery = new URLSearchParams(location.search).get('recovery') === '1';
  let bindingCsrf = '', currentRequest = '', pollTimer, renewTimer, generation = 0, busy = false, enabled = false;
  const statusText = message => { byId('qr-status').textContent = message; };
  async function auth(path, body, token = '') {
    const response = await fetch('/api/admin/auth/' + path, {
      method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: {'content-type':'application/json','x-stt-auth':'1',...(token?{'x-csrf-token':token}:{})},
      ...(body === undefined ? {} : {body:JSON.stringify(body)})
    });
    const data = await response.json();
    if (!response.ok) { const error = Error(typeof data.detail === 'string' ? data.detail : 'Conexão indisponível.'); error.status = response.status; throw error; }
    return data;
  }
  function stop(message, retry = false) {
    clearTimeout(pollTimer); clearTimeout(renewTimer); generation++;
    bindingCsrf = ''; currentRequest = ''; byId('qr-frame').hidden = true;
    byId('login-qr').removeAttribute('src'); byId('qr-link').removeAttribute('href');
    byId('qr-start').hidden = !retry; byId('qr-start').disabled = false;
    statusText(message);
  }
  async function poll(run, failures = 0) {
    if (run !== generation) return;
    try {
      const data = await auth('qr/status',{request:currentRequest},bindingCsrf);
      if (run !== generation) return;
      if (data.state === 'approved') {
        const session = await auth('qr/consume',{request:currentRequest},bindingCsrf);
        if (run !== generation) return;
        stop('Acesso liberado.'); await window.STTAdmin.signIn(session.csrf); return;
      }
      if (!['pending','scanned'].includes(data.state)) { await start(); return; }
      statusText(data.state === 'scanned' ? 'Confirme no celular.' : 'Leia o QR com o celular.');
      pollTimer = setTimeout(() => poll(run),1000);
    } catch (error) {
      if (run !== generation) return;
      if ([401,403,404,409].includes(error.status)) { await start(); return; }
      statusText('Reconectando…');
      pollTimer = setTimeout(() => poll(run,failures+1),Math.min(10000,2500*(failures+1)));
    }
  }
  async function start() {
    if (busy || recovery || window.STTAdmin.isAuthenticated()) return;
    busy = true; stop('Preparando acesso…');
    const run = generation;
    try {
      const data = await auth('qr/start',{});
      if (run !== generation || window.STTAdmin.isAuthenticated()) return;
      const approval = new URL(data.url);
      if (approval.origin !== location.origin || approval.pathname !== '/admin-approve.html') throw Error('Endereço inválido.');
      bindingCsrf=data.csrf; currentRequest=data.request;
      // Render the generated SVG as an inert image, never as HTML.
      byId('login-qr').src='data:image/svg+xml;base64,'+btoa(data.svg);
      byId('qr-link').href=approval.href; byId('qr-frame').hidden=false;
      renewTimer=setTimeout(() => start(),Math.max(1000,data.expires*1000-Date.now()));
      poll(run);
    } catch (error) {
      if (run === generation) { stop(error.message,true); renewTimer=setTimeout(() => start(),30000); }
    } finally { busy=false; }
  }
  async function init() {
    try {
      const config=await auth('config'); enabled=config.qr_enabled;
      byId('qr-login').hidden=!enabled || recovery;
      byId('legacy-login').hidden=!config.legacy_enabled || (enabled && !recovery);
      if (!enabled || recovery) return;
      try { const session=await auth('session'); await window.STTAdmin.signIn(session.csrf); return; }
      catch { /* An absent/expired session starts a fresh QR. */ }
      await start();
    } catch {
      byId('qr-login').hidden=false; stop('Conexão indisponível. Toque para tentar novamente.',true);
    }
  }
  byId('qr-start').onclick=() => enabled ? start() : init();
  window.addEventListener('stt:locked',() => { if(enabled && !recovery) start(); });
  new MutationObserver(() => { if(byId('admin-login').hidden && bindingCsrf) stop('Acesso liberado.'); })
    .observe(byId('admin-login'),{attributes:true,attributeFilter:['hidden']});
  init();
})();
