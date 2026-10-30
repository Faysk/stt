// Static frontend at the edge; API streamed to the notebook through Tunnel.
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!url.pathname.startsWith('/api/')) {
      if (url.pathname === '/') {
        const index = new URL('/index.html',url);
        return env.ASSETS.fetch(new Request(index,request));
      }
      return env.ASSETS.fetch(request);
    }
    if (!env.ORIGIN || !env.EDGE_KEY) return Response.json({detail:'Executor ainda não configurado.'}, {status:503});
    const headers = new Headers(request.headers);
    headers.set('x-edge-key', env.EDGE_KEY);
    headers.set('x-tts-client',request.headers.get('CF-Connecting-IP') || 'unknown');
    const target = new URL(url.pathname + url.search, env.ORIGIN);
    try {
      const response = await fetch(target, {method:request.method, headers, body:['GET','HEAD'].includes(request.method)?undefined:request.body, redirect:'manual'});
      const out = new Response(response.body, response);
      out.headers.set('Cache-Control','no-store');
      return out;
    } catch {
      return Response.json({detail:'Notebook indisponível. Tente novamente mais tarde.'},{status:503});
    }
  }
};
