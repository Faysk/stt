(() => {
  const byId = id => document.getElementById(id);
  const query = new URLSearchParams(location.search);
  let pair = new URLSearchParams(location.hash.slice(1)).get('pair');
  const request = query.get('request');
  // Never persist a pairing capability in history/storage or transmit it in a URL.
  if (location.hash) history.replaceState(null, '', location.pathname + location.search);
  let busy = false, expired = false, expiryTimer;
  const text = message => { byId('approval-status').textContent = message; };
  const encode = value => btoa(String.fromCharCode(...new Uint8Array(value))).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '');
  const decode = value => Uint8Array.from(atob(value.replaceAll('-', '+').replaceAll('_', '/') + '='.repeat((4 - value.length % 4) % 4)), c => c.charCodeAt(0));
  async function auth(path, body, token) {
    const response = await fetch('/api/admin/auth/' + path, {method:'POST', credentials:'same-origin',cache:'no-store',
      headers:{'content-type':'application/json','x-stt-auth':'1',...(token?{'x-csrf-token':token}:{})},body:JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'Não foi possível verificar o pedido.');
    return data;
  }
  function publicKey(options) {
    options.challenge = decode(options.challenge);
    if (options.user) options.user.id = decode(options.user.id);
    for (const name of ['allowCredentials','excludeCredentials']) for (const item of options[name] || []) item.id = decode(item.id);
    return options;
  }
  function serialize(credential) {
    const response = {};
    for (const field of ['clientDataJSON','attestationObject','authenticatorData','signature','userHandle']) {
      if (credential.response[field] != null) response[field] = encode(credential.response[field]);
    }
    if (credential.response.getTransports) response.transports = credential.response.getTransports();
    return {id:credential.id,rawId:encode(credential.rawId),type:credential.type,response,
      clientExtensionResults:credential.getClientExtensionResults(),authenticatorAttachment:credential.authenticatorAttachment};
  }
  async function verify(decision) {
    if (busy || expired) return;
    busy = true; byId('approve-access').disabled = true; byId('reject-access').disabled = true;
    try {
      text('Confirme com sua digital ou PIN…');
      const options = await auth('ceremony/options', {purpose:pair?'enroll':'approve',pair,request,decision});
      const credential = pair ? await navigator.credentials.create({publicKey:publicKey(options.publicKey)}) :
        await navigator.credentials.get({publicKey:publicKey(options.publicKey)});
      if (!credential) throw Error('Confirmação cancelada.');
      await auth('ceremony/finish',{id:options.id,credential:serialize(credential)},options.csrf);
      clearTimeout(expiryTimer);
      text(pair ? 'Passkey cadastrada. Você já pode ler o QR na tela do PC.' : decision === 'reject' ? 'Acesso rejeitado.' : 'Aprovado. Continue no PC.');
      pair = null; byId('approval-context').hidden = true;
      byId('approve-access').hidden = true; byId('reject-access').hidden = true;
    } catch (error) {
      text(['NotAllowedError','AbortError'].includes(error.name) ? 'Confirmação cancelada ou expirada. Tente novamente.' : error.message);
      byId('approve-access').disabled = expired || (!pair && !byId('code-confirmed').checked);
      byId('reject-access').disabled = expired;
    } finally { busy = false; }
  }
  byId('approve-access').onclick = () => verify('approve');
  byId('reject-access').onclick = () => verify('reject');
  byId('code-confirmed').onchange = () => { if (!busy && !expired) byId('approve-access').disabled = !byId('code-confirmed').checked; };
  (async () => {
    if (!window.isSecureContext || !window.PublicKeyCredential) { text('Abra este endereço HTTPS em um navegador com suporte a passkey.'); return; }
    if (pair) {
      byId('approval-title').textContent = 'Cadastrar sua passkey';
      byId('approve-access').textContent = 'Cadastrar com digital/PIN'; byId('approve-access').disabled = false;
      text('Pareamento iniciado pelo administrador. Cadastre a passkey que autorizará os próximos acessos.');
    } else if (request) {
      try {
        const data = await auth('approve/context',{request});
        byId('approval-code').textContent = data.code; byId('desktop-context').textContent = data.context;
        byId('approval-context').hidden = false; byId('reject-access').hidden = false;
        text('Confira o código antes de aprovar.');
        expiryTimer = setTimeout(() => {
          expired = true;
          byId('approve-access').disabled = true; byId('reject-access').disabled = true;
          text('Este pedido expirou. Gere outro QR no PC.');
        }, Math.max(0, data.expires * 1000 - Date.now()));
      } catch (error) { text(error.message); }
    } else text('Pedido ausente. Leia o QR na tela do PC.');
  })();
})();
