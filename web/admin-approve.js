(() => {
  const byId=id=>document.getElementById(id);
  const request=new URLSearchParams(location.search).get('request');
  let pair=new URLSearchParams(location.hash.slice(1)).get('pair');
  if(location.hash) history.replaceState(null,'',location.pathname+location.search);
  let busy=false,expired=false,expiryTimer,finished=false;
  const text=message=>{byId('approval-status').textContent=message;};
  const encode=value=>btoa(String.fromCharCode(...new Uint8Array(value))).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,'');
  const decode=value=>Uint8Array.from(atob(value.replaceAll('-','+').replaceAll('_','/')+'='.repeat((4-value.length%4)%4)),c=>c.charCodeAt(0));
  async function auth(path,body,token) {
    const response=await fetch('/api/admin/auth/'+path,{method:'POST',credentials:'same-origin',cache:'no-store',
      headers:{'content-type':'application/json','x-stt-auth':'1',...(token?{'x-csrf-token':token}:{})},body:JSON.stringify(body)});
    const data=await response.json();
    if(!response.ok) throw Error(typeof data.detail==='string'?data.detail:'Não foi possível verificar o acesso.');
    return data;
  }
  function publicKey(options) {
    options.challenge=decode(options.challenge);
    if(options.user) options.user.id=decode(options.user.id);
    for(const name of ['allowCredentials','excludeCredentials']) for(const item of options[name]||[]) item.id=decode(item.id);
    return options;
  }
  function serialize(credential) {
    const response={};
    for(const field of ['clientDataJSON','attestationObject','authenticatorData','signature','userHandle'])
      if(credential.response[field]!=null) response[field]=encode(credential.response[field]);
    if(credential.response.getTransports) response.transports=credential.response.getTransports();
    return {id:credential.id,rawId:encode(credential.rawId),type:credential.type,response,
      clientExtensionResults:credential.getClientExtensionResults(),authenticatorAttachment:credential.authenticatorAttachment};
  }
  async function verify() {
    if(busy||expired||finished) return;
    busy=true;byId('approve-access').disabled=true;byId('approval-card').dataset.state='working';
    try {
      text('Confirme com sua digital ou PIN.');
      const options=await auth('ceremony/options',{purpose:pair?'enroll':'approve',pair,request,decision:'approve'});
      const credential=pair?await navigator.credentials.create({publicKey:publicKey(options.publicKey)}):
        await navigator.credentials.get({publicKey:publicKey(options.publicKey)});
      if(!credential) throw Error('Confirmação cancelada.');
      await auth('ceremony/finish',{id:options.id,credential:serialize(credential)},options.csrf);
      clearTimeout(expiryTimer);finished=true;pair=null;
      byId('approval-card').dataset.state='done';byId('approve-access').hidden=true;byId('approval-done').hidden=false;
      text('Acesso liberado.');
    } catch(error) {
      byId('approval-card').dataset.state='retry';byId('approve-access').disabled=expired;
      text(['NotAllowedError','AbortError'].includes(error.name)?'Toque no logo para confirmar com digital ou PIN.':error.message);
    } finally {busy=false;}
  }
  byId('approve-access').onclick=verify;
  (async()=>{
    if(!window.isSecureContext||!window.PublicKeyCredential){text('Use um navegador HTTPS com suporte a passkey.');return;}
    if(pair) {byId('approve-access').disabled=false;text('Toque no logo para cadastrar sua passkey.');return;}
    if(!request){text('Leia o QR na tela do PC.');return;}
    try {
      const data=await auth('approve/context',{request});
      expiryTimer=setTimeout(()=>{expired=true;byId('approve-access').disabled=true;text('Pedido expirado. Leia o novo QR no PC.');},Math.max(0,data.expires*1000-Date.now()));
      // One native biometric ceremony; successful verification immediately approves the PC.
      await verify();
    } catch(error){text(error.message);}
  })();
})();
