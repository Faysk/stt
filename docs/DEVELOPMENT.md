# STT / Faysk - desenvolvimento

O projeto de transcrição usa FastAPI, SQLite, um executor local Whisper/Qwen e Cloudflare Worker + Tunnel.

## Instalação

Requisitos: Python 3.11+, uv, Node.js 22+ e, para GPU, drivers NVIDIA compatíveis.

```powershell
uv sync --locked --group dev --extra gpu
npm ci
uv run pytest -q
npm run check
npm run deploy:stt:dry-run
```

## Configuração (somente STT)

Crie `.env` privado a partir de `.env.example`. Use apenas variáveis `STT_*`. Defina `STT_DATA=.` caso a base local seja a própria raiz do projeto; o banco de testes novo é `db/stt.sqlite3`. Nenhuma variável de outro serviço é usada. Mantenha `STT_ADMIN_KEY`, `STT_WORKER_KEY` e `STT_EDGE_KEY` privadas e distintas; use `STT_REQUIRE_EDGE=1` em produção.

Para o Tunnel remoto **sem copiar token**, autentique o Wrangler (`npx wrangler login`), crie ou localize o Tunnel remoto `faysk-stt` e configure no painel o hostname `stt-origin.faysk.dev` → `http://127.0.0.1:8765`. Configure `STT_TUNNEL_NAME=faysk-stt` na `.env`, sem `STT_TUNNEL_TOKEN`. O módulo `server.tunnel` chamará `wrangler tunnel run faysk-stt`, que obtém o token pela sessão OAuth do Wrangler. Alternativamente, para execução não interativa configure **somente** `STT_CLOUDFLARED` e `STT_TUNNEL_TOKEN` privados. Não configure os dois modos simultaneamente. Este modo por nome depende de a sessão Wrangler continuar autenticada e não substitui credenciais de serviço para funcionamento permanente. O Worker `faysk-stt` usa o `wrangler.stt.jsonc` e disponibiliza `https://stt.faysk.dev`.

## Executar

```powershell
pwsh -File scripts/start.ps1
# Para encerrar somente processos do projeto
pwsh -File scripts/stop.ps1
```

O serviço trabalha com jobs de até 90 MiB/duas horas e resultados de acesso privado por sessão. A limpeza automática expira arquivos após 24 horas. Um novo ambiente de teste pode começar com os diretórios `db/`, `blob/` e `worker-outbox/` vazios, mas **somente com os processos parados**.

## Testes de publicação

```powershell
uv run pytest -q
npm run check
node tests/i18n.test.cjs
node tests/edge.test.cjs
node tests/deploy_guard.test.cjs
npm run deploy:stt:dry-run
```

Antes da publicação, aponte `stt-origin.faysk.dev` para uma API autenticada por Tunnel. Verifique a resposta da origem sem secret (HTTP 403) e `https://stt.faysk.dev/api/health` (HTTP 200). Só depois execute `npm run deploy` com `STT_APPROVED_CUTOVER=stt.faysk.dev` e `STT_APPROVED_WORKER=faysk-stt`, e cadastre o segredo privado `EDGE_KEY` no Worker. Não exiba tokens nem transcrições em relatórios.

A validação real de Whisper/CUDA e das transcrições continua obrigatória; CI isolado não testa a GPU do operador.

## Gerenciamento SSH do Galaxy S7 Edge

O card **Galaxy S7 Edge · SSH** aparece no console administrativo. Os dados de ligação só são enviados na API autenticada do painel. No `.env` privado do host da API, configure `STT_S7_SSH_HOST`, `STT_S7_SSH_USER` e, opcionalmente, `STT_S7_SSH_PORT=8022`. Reinicie a API para carregar o `.env`. O card permite copiar o comando para executar num terminal ligado à LAN; o navegador não abre sessões SSH.

O SSH **não** instala Python, modelos, CTranslate2 ou um worker. O dispositivo somente poderá ser selecionado como executor quando `STT_WORKER_ID=s7-edge` estiver registrado/online, anunciar CPU e formato compatíveis e tiver o modelo Whisper em cache. A API recusa escolher o S7 sem essas condições e preserva o executor atual. Android 7/Termux exige ensaio de compatibilidade e desempenho próprio antes de receber trabalhos públicos; não abrir a porta SSH para a Internet.

## Mais detalhes

- [S7 Edge: evidências de hardware, telemetria e viabilidade como worker](S7_EDGE_INTEGRATION.md).

- [Qwen e benchmarks](QWEN_AND_BENCHMARK.md)
