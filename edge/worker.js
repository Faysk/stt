// Static frontend at the edge; API streamed to the notebook through Tunnel.
const apiError = detail => Response.json({detail},{status:503,headers:{
  'Cache-Control':'no-store','X-Robots-Tag':'noindex, nofollow, noarchive',
  'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'
}});

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
    if (!env.ORIGIN || !env.EDGE_KEY) return apiError('Executor ainda não configurado.');
    const headers = new Headers(request.headers);
    headers.set('x-edge-key', env.EDGE_KEY);
    headers.set('x-tts-client',request.headers.get('CF-Connecting-IP') || 'unknown');
    const target = new URL(url.pathname + url.search, env.ORIGIN);
    try {
      const response = await fetch(target, {method:request.method, headers, body:['GET','HEAD'].includes(request.method)?undefined:request.body, redirect:'manual'});
      const out = new Response(response.body, response);
      out.headers.set('Cache-Control','no-store');
      out.headers.set('X-Robots-Tag','noindex, nofollow, noarchive');
      out.headers.set('X-Content-Type-Options','nosniff');
      out.headers.set('Referrer-Policy','no-referrer');
      return out;
    } catch {
      return apiError('Notebook indisponível. Tente novamente mais tarde.');
    }
  }
};
