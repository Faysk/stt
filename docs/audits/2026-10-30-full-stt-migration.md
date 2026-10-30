# Auditoria estática abrangente — transcrição STT e migração TTS → STT

**Data:** 30/10/2026 · **Repositório:** `Faysk/tts` · **Objetivo:** identificar bloqueadores reais de segurança, disponibilidade, integridade, privacidade e compatibilidade antes de renomear o serviço para **STT — Speech-to-Text**.

## 1. Escopo, versões e limites da evidência

| Referência | Estado observado |
|---|---|
| `main` | Base anterior à rodada de correções do PR #10; relatório/índice original #9 |
| `fix/audit-20261030-reliability` | Commit `0f9cc5b7172a7ab48f986ada88f413229724d0aa` |
| [PR #10](https://github.com/Faysk/tts/pull/10) | **Draft aberto**, 17 arquivos alterados; ainda não mesclado |
| [CI da branch](https://github.com/Faysk/tts/actions/runs/41007752665) | Workflow `Tests` sinalizado como **success** em 30/10; a rodada anterior documenta 28 testes Python, mais testes Node e parser PowerShell |
| [Épico de migração #11](https://github.com/Faysk/tts/issues/11) | Issues #12–#19 criadas e relacionadas |

**Método:** inspeção do inventário de arquivos em ambas as refs; leitura de API, workers, motores Whisper/Qwen, proxy Edge, provisionador Cloudflare, scripts operacionais, testes, CI, frontend, manifests, README e documentação; comparação com issues #1–#19; DNS público em 30/10/2026. Não foram executadas operações de escrita em infraestrutura, submissões de trabalhos públicos, carga, fuzzing, pentest, downloads de pesos ou testes físicos GPU/Windows. Este é um relatório **abrangente de código e desenho**, não certificação de produção ou de ausência de vulnerabilidades.

### Estado DNS externo (consulta do resolvedor, 30/10/2026)

| Nome | Resultado observado |
|---|---|
| `tts.faysk.dev` | A: `172.67.177.237`, `104.21.59.136`; AAAA Cloudflare |
| `tts-origin.faysk.dev` | A/AAAA Cloudflare |
| `stt.faysk.dev` | Sem A, AAAA ou CNAME encontrados |
| `stt-origin.faysk.dev` | Sem A, AAAA ou CNAME encontrados |

A consulta prova apenas a resolução DNS naquele momento; **não** revela o estado do painel Cloudflare, certificado, rota Worker, Tunnel, runtime, firewall ou o conteúdo do serviço. Tentativas de leitura do site via ferramenta de navegação não forneceram resposta verificável; portanto nenhum teste HTTP de produção é reivindicado.

## 2. Resumo executivo de risco

**Decisão:** **NO-GO para renomeação/cutover imediato e NO-GO para declarar o PR #10 pronto para produção**. Preparar compatibilidade e corrigir os bloqueadores antes de publicar.

| Pri. | Achado | Evidência | Rastreio |
|---|---|---|---|
| P1 | Um resultado permanentemente recusado pela API (ex.: 422) mantém o worker no retry e pode impedir novos claims até expirar | `server/worker.py`: `_deliver`/`_drain_outbox`/`main` | [#20](https://github.com/Faysk/tts/issues/20), relacionado a #3 |
| P1 | Arquivos TXT/MD/SRT/JSON são publicados antes do commit SQLite; falhas/crash podem deixar gerações incompletas ou SRT antigo | `server/app.py`: `result` | [#21](https://github.com/Faysk/tts/issues/21), #3/#6 |
| P1/P2 | Qwen confere duração decodificada só no fim; download YouTube Qwen sem progress hook de bytes, necessitando limites incrementais | `server/qwen_engine.py`: `iter_windows`/`run` | [#22](https://github.com/Faysk/tts/issues/22), #2/#4/#7 |
| P1 | Mudar domínio diretamente oculta o histórico do navegador: `localStorage` é isolado por origem | `web/public.js`, `web/i18n.js` | [#14](https://github.com/Faysk/tts/issues/14) |
| P1 | Provisionador existente altera configuração de Tunnel e publica Worker antes do secret; não oferece `dry-run`/rollback transacional | `scripts/cloudflare.py` | [#16](https://github.com/Faysk/tts/issues/16), #19 |
| P1 | Faltam ensaios de migração DB, outbox, sessão, DNS/TLS, worker misto e rollback real | CI unitário, nenhum staging observado | [#19](https://github.com/Faysk/tts/issues/19) |
| P2 | Eventos/linhas administrativas persistem sem limite explícito documentado; payloads e pseudônimos devem ter política clara | `server/app.py`, `server/observability.py` | [#23](https://github.com/Faysk/tts/issues/23), #1 |
| P2 | Scans automáticos de dependências, SAST e segredos não estão no workflow atual; CI positivo não atesta isso | `.github/workflows/test.yml` | [#24](https://github.com/Faysk/tts/issues/24) |
| P2 | A receita Qwen valida marcador textual de revisão, não hashes de pesos; nenhum benchmark físico nem alinhamento SRT | `server/qwen_engine.py`, `runtime/qwen3-windows-x64.json` | #7, #8 |
| P2 | Limpeza de muitos arquivos durante transação `BEGIN IMMEDIATE` pode ampliar contenção SQLite e atrasar requests; medir antes de mudar | `server/app.py`: `maintenance`/`_purge_job` | #1, #19 |

**Classificação:** P1 = corrigir/validar antes do cutover; P2 = resolver ou obter aceitação documentada antes de afirmar prontidão operacional. Os achados são **confirmados por inspeção do fluxo de código** quando indicado; impacto real e exploração externa não foram demonstrados.

## 3. Segurança — superfície e controles existentes

### Pontos positivos observados no código
- API exige `x-session-key` com formato restrito e compara hash de titularidade para resultados; IDs aleatórios de job/lease.
- Auth administrativa e de worker depende de secrets com comparação constante; configuração `TTS_REQUIRE_EDGE=1` exige cabeçalho de origem. Confirmar **valor real** na produção.
- Edge sobrescreve `x-edge-key` e `x-tts-client` ao encaminhar API; existe teste Node de spoofing, mas não prova a configuração real da borda.
- YouTube aceita apenas link HTTPS de vídeos com ID canônico dos domínios previstos, reduzindo a superfície de SSRF; não substitui testes do downloader/redirecionamentos.
- Frontend constrói texto com `textContent` em fluxos importantes; CSP, `nosniff`, `no-store` e `no-referrer` aparecem nos caminhos analisados. Necessária verificação de headers HTTP reais, incluindo respostas precoces de erro.
- `.env`, `db`, `blob`, `models`, `logs`, `worker-outbox` ficam excluídos por `.gitignore`. Isso não protege segredos já publicados em histórico Git ou artefatos CI.
- Leases e heartbeats na branch do PR #10 têm fencing transacional; o PR também adiciona outbox durável e tentativas de purge.

### Lacunas que exigem validação
- Rate limiting de abuso efetivo na borda, headers confiáveis, segredo da origem, acesso direto à origem e política de proxy.
- Limites de upload/download/decode em tráfego real e falhas de disco (sem testar a produção).
- Gestão de segredos e acesso aos arquivos de dados/outbox no host Windows; backup, restauração, permissões de arquivo, logs e serviço após reinício.
- Dependências/terceiros, origem e integridade dos pesos locais; versão instalada pode diferir do manifesto.
- Nenhuma varredura atual de CVEs/pentest foi executada nesta rodada; a auditoria anterior #9 registrou uma verificação **pontual**.

## 4. Dados, fila e lifecycle

**Mudanças presentes apenas no PR #10:** retenção física retry, admissão antes de multipart, receipts idempotentes, timeout/kill de inferência, heartbeat cercado por lease, segmentos validados, Qwen experimental e métricas versionadas. A leitura da `main` **não deve ser interpretada como se essas correções já estivessem publicadas**.

**Falhas adicionais nesta revisão:**
1. `_deliver` coloca respostas permanentes (ex. HTTP 422) na mesma fila de retry que uma desconexão transitória, e `main` suspende claims quando existe qualquer pendência. Caso concreto de teste a acrescentar em #20.
2. O endpoint de resultado grava vários artefatos no filesystem enquanto a transação SQL ainda está aberta. Falhar ou reiniciar no meio pode deixar resultado do disco diferente do recibo/status no banco; falta publicação por geração, recuperação de órfãos e limpeza de SRT legado (#21).
3. Downloads YouTube Qwen e duração decode longo não têm orçamento incremental completo (#22). Whisper possui `progress_hooks` e guardas, porém precisam de testes sintéticos de metadados enganadores.
4. A limpeza física da pasta de cada job ocorre sob lock de escrita SQLite; backlog grande pode prejudicar latência da API. Medir com fixture e registrar em #1.

**Qualidade do motor:** Whisper e Qwen não produzem resultados intercambiáveis sem avaliação. Qwen preview não gera timestamps e corretamente não publica SRT; faltam pesos verificados por hash, teste de VRAM e precisão com corpus real e deduplicação de janelas (#7/#8).

## 5. Migração STT e compatibilidade

Ver [inventário da migração](../migrations/tts-to-stt/INVENTORY.md), com referências por caminho, contratos e sequência.

- **Marca:** `tts / faysk` → `stt / faysk`; URL pública `https://stt.faysk.dev`.
- **Sessões:** manter antiga origem atendendo resultados e downloads de usuários com jobs válidos. Não usar redirecionamento irrestrito; não transmitir tokens via query string.
- **Config:** resolvedor centralizado deve suportar `STT_*` e `TTS_*` até migração validada. Nunca gerar banco vazio ignorando `db/tts.sqlite3`.
- **Protocolos:** `x-tts-client` é usado entre Worker e API; exigir alias coordenado, jamais apenas renomear header no edge.
- **Infra:** preparar novo domínio em paralelo, com DNS/TLS, Edge, Tunnel e origem confirmados; preservar aliases/recursos antigos e backups. O atual `scripts/cloudflare.py` **não** é um mecanismo seguro de migração.
- **GitHub:** renomear `Faysk/tts` → `Faysk/stt` depois de preparar PRs, remotes, webhooks, CI e referências; não recriar histórico; manter PR #10.
- **Projeto separado:** `Faysk/tda` é apenas referência de Qwen e permanece fora do escopo.

## 6. Cobertura e evidência de teste

| Subárea | Evidência existente | Falta para aceitar |
|---|---|---|
| API/sessões/edge | pytest e Node existentes, CI verde | testes com hosts diferentes, headers reais, carga e segurança em staging |
| Retenção/leases/worker | testes determinísticos #1–#6 no PR #10 | crash/lock Windows, 422 dead-letter, disco cheio, DB + artefatos atômicos |
| Qwen/Whisper | testes unitários de recipe e métricas | modelos físicos 4070/3090, comparações com referência humana, limite de mídia |
| Windows/Cloudflare | parser PowerShell no CI; script de provisionamento | startup/shutdown real, secrets, DNS/TLS, upload completo, rollback |
| UI/SEO/i18n | Node i18n + checagem sintática | navegador real, storage cross-origin, responsividade, assets embutidos, canonical/redirect |
| Segurança | controles estáticos e auditoria anterior | SAST, scans atuais de dependência, segredo e pentest autorizado |
| Observabilidade/privacidade | eventos e métricas persistentes | retenção pseudônima explícita, logs, backups, restore e alertas |

**Limite da palavra “completa”:** completude do inventário e da análise estática nos componentes versionados, **não** 100% dos comportamentos de infraestrutura/produção que exigem acesso ou hardware. Não alegar 100% validado com base no CI.

## 7. Plano operacional de correção (ordem)

1. **P1 estabilidade:** #20 e #21; somar #22 aos gates de mídia; revisar #1–#6 do draft PR #10, executar CI/review/ensaios de falha.
2. **Compatibilidade STT:** #12 inventário, #13 backend/DB, #14 sessões e #17 scripts/CI; *sem* tocar em produção.
3. **Ambiente novo:** #15 assets/SEO e #16 Cloudflare; testar lado a lado e autenticação de origem.
4. **Prontidão:** #19 com restore de banco, downloads durante TTL, stress, edge/TLS, monitoramento e rollback; #23 privacidade, #24 supply chain e gates #7/#8 em paralelo.
5. **Cutover só com GO documentado:** começar pelo novo host, observar o ciclo de retenção e só então #18 renomear GitHub. Manter o host antigo conforme política de sessão.
6. **Encerrar itens apenas com evidência:** linkar commits, testes, checks, resultados de hardware e deploy real. Não encerrar #11/#19 pela simples mudança de strings.

## 8. O que NÃO foi alterado nesta auditoria

Nenhuma alteração no site em produção, Cloudflare, DNS, pesos, transcrições, dados do SQLite, secrets, GitHub repository slug ou projeto TDA. Este documento é rastreio técnico e não execução de cutover.

**Índice:** [#9 auditoria inicial](https://github.com/Faysk/tts/issues/9) · [#11 migração](https://github.com/Faysk/tts/issues/11) · [#20 outbox](https://github.com/Faysk/tts/issues/20) · [#21 atomicidade](https://github.com/Faysk/tts/issues/21) · [#22 mídia](https://github.com/Faysk/tts/issues/22) · [#23 privacidade](https://github.com/Faysk/tts/issues/23) · [#24 CI](https://github.com/Faysk/tts/issues/24)


## 9. Atualização de implementação após a auditoria (30/10/2026)

A auditoria original era um *baseline* pré-correção. **Após este relatório**, o trabalho avançou por PRs **ainda em draft e não publicados**:

| PR | Implementações em branch | Evidência |
|---|---|---|
| [#26](https://github.com/Faysk/tts/pull/26) | Dead-letter da outbox (#20), gerações de resultado e rollback SQL (#21), budgets de decode/download (#22), retenção de eventos (#23), workflow de segurança (#24) | CI verde nas branches |
| [#27](https://github.com/Faysk/tts/pull/27) | Aliases STT_/TTS_ com erro em conflito, manutenção da base antiga, sessão/idioma no mesmo origin, testes de upgrade | CI verde |
| [#29](https://github.com/Faysk/tts/pull/29) | Branding STT e SEO, Worker STT isolado em `wrangler.stt.jsonc`, antigo provisionador com plano sem escrita por padrão, guardião de `npm run deploy`, dry-run de Wrangler, helper de rollback offline e testes | **49 pytest passaram**, testes JS, parser PowerShell, build dry-run Cloudflare e Security audit verdes em 30/10/2026 |
| [#25](https://github.com/Faysk/tts/pull/25) | Inventário, plano de cutover e rollback, este relatório | Documentação, CI verde |

A ferramenta de rollback agora existe como `scripts/restore_legacy_exports.py` (PR #29), com comportamento *read-only* por padrão e opção explícita de escrita **somente offline e após backup**; isso **não significa** que o rollback real foi ensaiado em NTFS/produção.

O novo Worker é independente, `faysk-stt`, usando temporariamente a origem antiga apenas como proposta sujeita a teste real. O provisionador original `scripts/cloudflare.py` passou a bloquear mutações por padrão. `npm run deploy` exige alvo STT digitado explicitamente. **Nenhum deploy foi realizado.**

A issue **[#28 — ações humanas e bloqueios externos](https://github.com/Faysk/tts/issues/28)** reúne passo a passo completo para Cloudflare real, Windows/backup, GPUs/modelos Qwen, duas origens do navegador, release/rename, evidências e política de não divulgar segredos. A execução dessas tarefas é a diferença entre **código preparado** e **produção validada**.

**Estado de decisão permanece NO-GO até #19 e #28 aprovadas.** Testes sintéticos/CI verdes não substituem hardware, tráfego ou rollback real.


## 10. Verificação adicional de prontidão (30/10/2026 — draft PR #30)

A revisão posterior encontrou uma distinção operacional crítica: respostas **401/403 da API para o worker** significam falha de autenticação, não transcrição inválida. No PR [#30](https://github.com/Faysk/tts/pull/30), a outbox agora permanece íntegra, faz retry com backoff e interrompe novos claims até correção da credencial; **422** continua com quarentena por payload inválido. Isso evita processar dezenas de trabalhos com um segredo inválido sem conseguir entregar resultados.

O limite de upload foi exercitado com multipart transferido em chunks sem `Content-Length`. Inicialmente, o parser devolvia **400** em vez do **413** correto; a camada ASGI foi corrigida, mantendo headers `no-store`, `noindex`, `nosniff`, CSP e referrer-policy nas rejeições. Edge faz o mesmo nas respostas upstream e 503 sem configuração. Log Qwen foi sanitizado para não registar mensagens de exceção contendo paths locais.

O teste de rollback foi ampliado para **FastAPI + SQLite de verdade no ambiente temporário**: criar job, reivindicar lease, publicar resultado sintético por API, executar helper de rollback (dry-run e depois aplicação no fixture offline) e confirmar TXT/SRT nas duas disposições de arquivos. Isso **não é** um ensaio de produção/NTFS.

**Evidência verificada:** commit `1a7d7df67eda8cffed5683f1b568bfb81716902f`, [Tests success — 57 Python + JS/PowerShell/Worker dry-run](https://github.com/Faysk/tts/actions/runs/41057612418), [Security audit success](https://github.com/Faysk/tts/actions/runs/41057612526). Nenhum PR foi mesclado, nenhum dado privado foi lido e nenhum recurso Cloudflare ativo foi alterado. Bloqueios externos continuam listados na **[#28](https://github.com/Faysk/tts/issues/28)**, e a decisão de produção segue **NO-GO** sem #19.


## 11. Durabilidade e media preflight — atualização do PR #31

O [draft PR #31](https://github.com/Faysk/tts/pull/31) avançou na segurança de arquivos e execução:

- **Outbox:** após sincronizar bytes do JSON e fazer `rename`, o worker sincroniza também o diretório pai em POSIX. Isso reduz o risco de perder o registro do resultado após falha de energia entre o rename e o commit do diretório.
- **Geração de resultado:** a primeira criação de `results/` também sincroniza a entrada do diretório pai antes de o SQLite apontar para a geração, e links simbólicos no caminho de resultados são rejeitados.
- **Rollback:** recusa exportações flat existentes que não tenham os mesmos bytes da geração confirmada; verifica SHA-256 do staging e recusa destino criado por outra operação durante a cópia, evitando substituir uma transcrição potencialmente única.
- **Qwen:** toda a mídia, inclusive sua duração efetivamente decodificada, é examinada em streaming **antes de importar Torch/Transformers, reservar VRAM ou carregar pesos**. Um vídeo >7200 s, vazio, sem áudio ou inválido é rejeitado antes de consumir GPU. Métrica `media_probe_s` explicita o custo de decode extra.
- **Criação de jobs:** erro de filesystem em `mkdir` não deve criar um trabalho de YouTube sem workspace nem excluir diretório que não foi criado pelo upload atual.

Os testes simulam falhas, duração falsa e diretórios divergentes, **mas não substituem ensaios reais de energia/NTFS/GPU**.

**Evidência do commit `af394fb4b28de66bf293462b6a0049d7424aac4c`:** [Tests 67/67 Python e JS/PowerShell/build Worker aprovados](https://github.com/Faysk/tts/actions/runs/41059872592), [Security audit success](https://github.com/Faysk/tts/actions/runs/41059872473). Sem produção, sem merge e sem dados privados usados. As tarefas humanas pendentes foram pormenorizadas na [#28](https://github.com/Faysk/tts/issues/28).
