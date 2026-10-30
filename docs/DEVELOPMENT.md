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

Para comunicação por Tunnel configure `STT_CLOUDFLARED` e `STT_TUNNEL_TOKEN` privados, apontando exclusivamente para a origem `stt-origin.faysk.dev`. O Worker `faysk-stt` usa o `wrangler.stt.jsonc` e disponibiliza `https://stt.faysk.dev`.

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

## Mais detalhes

- [Qwen e benchmarks](QWEN_AND_BENCHMARK.md)
