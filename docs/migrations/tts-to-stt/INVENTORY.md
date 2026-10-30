# Inventário de migração TTS → STT (v1 — evidências estáticas)

**Data:** 30/10/2026 · **Épico:** [#11](https://github.com/Faysk/tts/issues/11) · **Subtarefa:** [#12](https://github.com/Faysk/tts/issues/12)

**Status:** mapa inicial **documentado**, não migração executada. Baseline: `main` e `fix/audit-20261030-reliability@0f9cc5b7`. Os exemplos são retirados do código versionado. Para o ambiente real, confirmar IDs/segredos/rotas no painel antes de modificar.

## 1. Matriz de compatibilidade

| Categoria | Identificador atual | Alvo | Decisão / condição de rollback |
|---|---|---|---|
| Produto/branding | `tts / faysk` | `stt / faysk` | Substituir copy visível, títulos e assets no lançamento |
| DNS público | `tts.faysk.dev` | `stt.faysk.dev` | Operar ambos na janela de transição; host antigo permite downloads de sessões legadas até TTL |
| Origin DNS | `tts-origin.faysk.dev` | `stt-origin.faysk.dev` (a decidir) | **Não obrigatório renomear** internamente: preservar se o nome existente for estável. Novo alias só com infraestrutura validada |
| Worker Cloudflare | `faysk-tts` | `faysk-stt` (proposto) | Não renomear/recriar Worker ativo sem teste de rota/secrets |
| Tunnel Cloudflare | `faysk-tts` | `faysk-stt` (proposto) | Não recriar nem sobrescrever config de tunnel ativo; staging independente |
| Python package | `faysk-tts` | `faysk-stt` | Regenerar uv.lock, validar instalação e CI |
| Node package | `faysk-tts-edge` | `faysk-stt-edge` | Regenerar package-lock.json |
| Env/config | `TTS_*` | `STT_*` | Dual-read durante transição, prioridade e conflitos explícitos; nunca logar secrets |
| Client IP proxy | `x-tts-client` | `x-stt-client` | Renomear somente junto ao backend/edge, com alias autenticado |
| Browser session | `tts.session` | `stt.session` | Preservar valor quando na mesma origem, mas **não transferir automaticamente entre hosts** |
| Browser language | `tts.language` | `stt.language` | Fallback da chave antiga, preferir valor preservado |
| SQLite | `db/tts.sqlite3` | `db/stt.sqlite3` (opcional, **adiado**) | Permanecer com caminho antigo inicialmente; qualquer rename exige backup/restauração e desativação coordenada de processos |
| Data/worker | `blob/`, `worker-outbox/`, `logs/`, `tts-` temp | Manter dados e IDs existentes | Não perder ACK, lease, retry, arquivos ou jobs ainda válidos |
| Repo | `github.com/Faysk/tts` | `github.com/Faysk/stt` | Renomear **depois** do cutover controlado, preservando PR #10 e hooks |
| Modelos Qwen | `TTS_QWEN_PYTHON`, `TTS_QWEN_MODELS` | `STT_QWEN_*` | Runtime separado; não baixar/alterar pesos automaticamente |
| TDA | `github.com/Faysk/tda` | Sem mudança | Referência somente leitura; outro produto |

## 2. Referências identificadas por arquivo

### Infraestrutura/edge
- `wrangler.jsonc`: `name: faysk-tts`; `ORIGIN=https://tts-origin.faysk.dev`; `routes: tts.faysk.dev`.
- `scripts/cloudflare.py`: túnel `faysk-tts`, ingress/DNS `tts-origin.faysk.dev`, `TTS_TUNNEL_TOKEN`, `TTS_EDGE_KEY`; hoje opera PUT da configuração ativa antes de deploy, sem dry-run.
- `edge/worker.js`: `x-tts-client`; exige atualizar contrato com o backend.
- `server/tunnel.py`: `TTS_TUNNEL_TOKEN`, `TTS_CLOUDFLARED`; não alterar variáveis até o alias.
- `.env.example`: `TTS_DATA`, `TTS_ADMIN_KEY`, `TTS_WORKER_KEY`, `TTS_EDGE_KEY`, `TTS_REQUIRE_EDGE`, `TTS_URL`, `TTS_WORKER_ID`.

### API, worker e motores
- `server/app.py`: `TTS_DATA`, `db/tts.sqlite3`, `TTS_REQUIRE_EDGE`, `TTS_EDGE_KEY`, `TTS_ADMIN_KEY`, `TTS_WORKER_KEY`, `x-tts-client`, `TTS_MODELS`; manter identidade dos jobs, schema, arquivos, heartbeat, receipts.
- `server/worker.py`: `TTS_URL`, `TTS_WORKER_KEY`, `TTS_EDGE_KEY`, `TTS_WORKER_ID`, `TTS_OUTBOX`, `TTS_TMP`, `TTS_QWEN_PYTHON`; prefixo temporário `tts-`.
- `server/infer.py`: `TTS_MODELS`.
- `server/qwen_engine.py`: `TTS_QWEN_MODELS`; recipe/marker não garantem integridade de peso, relacionado a #7.
- `runtime/qwen3-windows-x64.json`: schema `tts_qwen_runtime_recipe_v1`; em persistência/automação, introduzir nova versão compatível em vez de reescrever manifestos já emitidos.
- `server/metrics.py` e benchmarks: versões/schemas devem manter semântica dos formatos existentes; renomear brand, não alterar dados sem migração.

### Frontend, branding e SEO
- `web/index.html`: nome no title, brand, footer, canonical, OG, JSON-LD, URLs GitHub.
- `web/admin.html`: title, sidebar e header `tts`.
- `web/i18n.js`: `tts.language` e title dinâmico `tts / faysk`; 10 idiomas.
- `web/public.js`: `tts.session` (segredo persistido no browser); não pode ser lido pelo outro hostname.
- `web/site.webmanifest`: `tts / faysk`, `short_name: tts`.
- `web/sitemap.xml`, `web/robots.txt`: URL antiga em sitemap e descoberta.
- `web/_headers`: conferir que as regras de segurança/noindex continuam operando na hospedagem nova.
- `web/icon.svg`, PNG icons, `web/social-card.png`, `web/steps.svg`: revisar visualmente se existe texto gravado na imagem; não presumir que só o HTML precisa mudar.
- `web/dashboard.js`, `web/ui.css`: revisar strings de branding, links e nomes CSS quando fizer alterações.
- `README.md` e `docs/DEVELOPMENT.md`: URLs, snippets, instruções e política pública.

### Build, operação e integrações
- `pyproject.toml`, `uv.lock`: nome Python e dependências; manter resolução reprodutível.
- `package.json`, `package-lock.json`: nome Node e Wrangler.
- `scripts/start.ps1`: detecta `TTS_TUNNEL_TOKEN=`, URL antiga na mensagem, runtime `.venv`, porta local 8765.
- `scripts/stop.ps1`: PIDs sob `logs/`, Python `.venv`, verificação de processo e filhos; não quebrar parada segura por troca de nome.
- `.github/workflows/test.yml`: branch, ambiente, CI Python/Node e parser PowerShell; adicionar cenários mistos.
- `tests/*.py`, `tests/*.cjs`: fixtures dependem de `TTS_*` e hostname antigo; adicionar testes de compatibilidade em vez de apagar as fixtures.
- `docs/audits/*` e `tools/audit/*`: histórico imutável de achados antigos; **não** reescrever referências históricas de evidência como se fossem STT desde o início.

## 3. Inventário externo — limites da verificação

Consulta DNS de 30/10/2026:
- `tts.faysk.dev` e `tts-origin.faysk.dev`: A/AAAA observados, via Cloudflare.
- `stt.faysk.dev` e `stt-origin.faysk.dev`: sem A/AAAA/CNAME visíveis.

**Ainda não confirmados:** nomes reais/IDs de Worker/Tunnel no painel, custom domains efetivos, TLS, secrets, permissões do token de provisionamento, access policies, firewall, DNS cutover, Vercel (se participar), nomes reais de serviços Windows e agendamentos, backup externo, webhooks GitHub e analytics. O código é indício, não substituto do estado do provedor.

## 4. Sequência e gate de migração

1. Inventariar e exportar estado externo de maneira sanitizada (**#12/#16**).
2. Introduzir compatibilidade no backend e nos scripts antes da nomenclatura nova (**#13/#17**).
3. Validar acesso aos trabalhos pelo host antigo e UX de transição (**#14**).
4. Publicar novo branding/SEO em staging e preparar Cloudflare sem substituir origem antiga (**#15/#16**).
5. Ensaiar upgrade, resultados, outbox, timeout, cópia de banco consistente, coexistência, rollback e monitoramento (**#19**, mais **#20–#24**).
6. Só depois do GO, realizar transição controlada; renomear o GitHub (**#18**) sem perder PR e histórico.
7. Não retirar `TTS_*`, `tts.faysk.dev` e outros aliases antes de política explicitamente aceita e testada.

**Definição de pronto de #12:** inventário estático publicado (este documento), mas ainda exige descoberta da configuração real dos provedores e teste automatizado de referências/allowlist. #12 permanece aberta.
