# STT · prontidão de release, milestones e critérios de 100%

**Baseline:** 30/10/2026. **Repositório:** [Faysk/stt](https://github.com/Faysk/stt). **Estado:** produção HTTP operacional no momento do teste, mas **release 100% ainda não homologado**. Este documento não autoriza deploy, restart, criação de credenciais, Terraform nem alterações na Cloudflare. Evidências de alterações reais só são aceites quando fornecidas pelo operador ou por um sistema com acesso autorizado.

## Escopos diferentes — não somar percentagens

1. **Retirada do TTS antigo (feito):** operador confirmou exclusões de Worker e Tunnel `faysk-tts`, `db/tts.sqlite3` e DNS `tts.faysk.dev`/`tts-origin.faysk.dev`; consulta DNS pública sem A/AAAA/CNAME antigos e com os novos nomes `stt.*` presentes. Teste PowerShell **após DNS**: frontend 200, API 200, origem direta sem segredo 403. Fonte: [IaC #52](https://github.com/Faysk/iac/issues/52) e [runbook](https://github.com/Faysk/iac/blob/main/docs/operations/STT_RETIRE_LEGACY_TTS.md).
2. **STT público funcional (comprovado em um cenário):** YouTube público → job `done` → leitura → downloads confirmados pelo operador **antes** da retirada manual do DNS. Não há confirmação de uma **nova transcrição concluída depois** da limpeza.
3. **Código do console administrativo (integrado, não homologado no host):** [PR #59](https://github.com/Faysk/stt/pull/59), commit `3358a88f226fba1d91319490c77cd05c3da49d67`, inclui seleção de modelo por card, preload assíncrono no worker, cards CPU/GPU e compute types, histórico e identidade GPU, interações e testes. **Git main ≠ deploy Windows/Worker**, e CI sintético ≠ GPU real.
4. **Produção endurecida / 100% pronta (pendente):** persistência após logoff/reboot, recuperação de falhas, envio local e formatos, segurança de ingress/limites, outbox, validação de GPU/preload, retenção/restore, acessibilidade e monitorização conforme issues.
5. **IaC global (escopo maior):** [Faysk/iac#20](https://github.com/Faysk/iac/issues/20) e [MAPPING_GATE](https://github.com/Faysk/iac/blob/main/docs/MAPPING_GATE.md) continuam **bloqueados** até 100% do ecossistema mapeado e aprovação explícita; conclusão de STT **não** autoriza `terraform import/apply`.

## Milestones de release — plano de associação no GitHub

**Atenção:** esta é a matriz documental dos milestones. Criar/editar milestones nativos exige acesso à funcionalidade de milestones do GitHub; **não presumir que já estejam criados/atribuídos**. Cada issue tem um marco *principal* nesta tabela; referências cruzadas não duplicam o denominador.

| Marco proposto | Issues principais | Resultado exigido | Situação |
|---|---|---|---|
| **STT-M1 · Cutover e desativação TTS** | #11–#19, #28 (10) | Trajeto STT validado, remoção do legado documentada; reconciliar critérios históricos de sessão/rollback sem fabricar testes retroativos | Cutover/limpeza feitos, critérios históricos ainda por revisão |
| **STT-M2 · Segurança, dados e disponibilidade** | #1–#6, #9, #20–#24, #35, #36, #38 (15) | Testes de falha, recebimentos/digest, limites, recuperação, retenção, serviços Windows e alertas comprovados | Pendente de aceite por issue |
| **STT-M3 · Console admin, modelos e GPU** | #44–#57 (14) | PR #59 como base; UX, modelos, cache real por executor, comportamento CPU/GPU, CSV/histórico e teste no Windows com GPU aprovados | Código base integrado; produção não homologada |
| **STT-M4 · E2E físico e release** | #37 (1) | Matriz real de uploads, YouTube pós-DNS, permissões, validação GPU, concorrência, privacidade, rollback operacional e aceite do operador | Pendente |
| **STT-M5 · Pesquisa/benchmarks opcionais** | #7, #8 (2) | Qwen isolado, métricas/benchmarks auditáveis, ativação independente por decisão explícita | Pós-release; não bloquear produção Whisper somente por existir backlog opcional |

**Total da baseline:** 42 issues abertas verificadas em 30/10/2026. As 42 foram mapeadas uma vez por número. **42 abertas não significam 0% implementado**; o status final vem dos critérios de aceite e das evidências, não de um percentual derivado de contagens de issues.

## Portões de qualidade para declarar STT Whisper pronto

| Gate | Evidência exigida | Estado em 30/10 |
|---|---|---|
| HTTP após limpeza | GET frontend/API = 200; origem sem segredo = 403 | **Passou 3/3**, saída PowerShell do operador |
| Novo job após DNS | Gravação curta autorizada → done → TXT/SRT/JSON conferidos | **Pendente** |
| Deploy/revisão instalada | SHA/versão da API, worker local e Worker Cloudflare; comparar com main | **Pendente** (não inferir a partir do Git) |
| API/worker/Tunnel após boot | Autostart não interativo, watchdog, logs, restart controlado sem duplicar instâncias | **Pendente**, #35 |
| Entrega durável | Receipt correto / divergente, retry, 409, outbox e nenhuma perda confirmada | **Pendente**, #3/#20/#21/#36 |
| Envio e limites | WAV/MP3/MP4, arquivo inválido, limites 90 MB/2h, rate limit e retenção 24h | **Pendente**, #2/#22/#37/#38 |
| Modelos e GPU | Preload no worker correto, cache em disco, RTX 4070/CPU, compute types e histórico sem métricas inventadas | **Pendente**, #44–#57 |
| Segurança/privacidade | Sessões isoladas, origem protegida, segredos fora dos logs, logs e dados expiram conforme política, CI e revisão | **Parcial**, #23/#24/#37 |
| Release e reversão | Release checklist, snapshot/rollback atuais e decisão do operador (sem religar TTS retirado) | **Pendente**, #19/#28 |

**100% da versão pública Whisper** só será declarado quando todos os *gates obrigatórios* tiverem evidência datada, link para execução/commit, resultado e aprovação explícita. Qwen e melhorias de investigação podem permanecer em marco pós-release, **desde que** a decisão seja documentada e não esconda risco P1/P2 obrigatório.

## Próximo ciclo recomendado — ordem operacional

1. **P0 — rastreabilidade:** identificar SHA realmente implantado no Windows e versão do Worker, sem imprimir segredo. Verificar diferença contra `main`. Não assumir que o PR #59 chegou à produção.
2. **P0 — smoke pós-DNS:** realizar 1 novo job curto, com dados autorizados, conclusão e downloads; registrar apenas IDs anonimizados/tempo/status.
3. **P1 — sobrevivência:** abordar [#35](https://github.com/Faysk/stt/issues/35) com serviço/tarefa Windows e credenciais de conector de privilégio mínimo; ensaio de logoff/reboot/queda antes de dizer autostart concluído.
4. **P1 — integridade:** [#36](https://github.com/Faysk/stt/issues/36) + #3/#20/#21 para ACK/outbox, e [#37](https://github.com/Faysk/stt/issues/37) para upload/retention.
5. **P1/P2 — admin:** [#57](https://github.com/Faysk/stt/issues/57) e #44–#56: comparar DOM, capacidades, preload e histórico reais em CPU/GPU. Priorizar correções observadas, não encerrar em massa pelo merge do PR #59.
6. **Go/no-go:** coletar evidências sanitizadas, CI verde no SHA final, relatórios e decisão explícita; atualizar checkboxes/issues, milestone e esta página.

## Regra de manutenção

- Estado **implementado no Git** ≠ **testado em CI** ≠ **implantado** ≠ **observado em produção** ≠ **aceite final**.
- Cada issue exige link de PR, teste específico (inclusive teste físico se aplicável), riscos/remanescentes e decisão de encerramento.
- Não versionar transcrições privadas, áudio de utilizadores, tokens, IP privados desnecessários, credenciais de túnel, `.env`, bases SQLite, pesos nem estado Terraform.
- O repositório STT governa o código da aplicação; o IaC só referencia a infraestrutura e as evidências. O projeto `Faysk/tda` não faz parte desta migração.


---

## Atualização de triagem de issues concluídas — 30/10/2026

O operador autorizou encerrar **somente o trabalho já efetivamente entregue**. Após revisão de código, testes e checks, **10 issues foram encerradas como `completed`**, cada qual com uma nota de evidência no próprio ticket:

- **#1, #2, #3, #4, #5, #6:** defeitos originais de retenção, admissão de uploads, outbox, deadline/processos, lease/heartbeat e validade cronológica corrigidos na aplicação; regressões específicas em `tests/test_reliability.py` e `tests/test_release_safety.py`.
- **#15, #17:** branding/SEO STT e atualização de scripts/manifests/CI/documentação, com npm/uv e reinício/Cloudflare confirmados pelo operador.
- **#21, #24:** publicação de artefatos por geração e validação de segurança com testes de falha, CI `security.yml`, auditorias Python/Node, SAST e secret scanning.

**Evidência da implantação:** Cloudflare Worker `cf3aaf2b-2422-4216-acd6-ebfe1e379a1d`, frontend/API públicos HTTP 200; Windows local: `75 passed, 1 skipped`, `npm run check` aprovado. Referência: [PR #59](https://github.com/Faysk/stt/pull/59) e [#57](https://github.com/Faysk/stt/issues/57).

**Saldo atualizado da baseline de 42 issues: 10 concluídas, 32 abertas.** A tabela original acima registra o estado histórico antes desta triagem; não altera os marcos propostos nem remove tarefas pendentes.

**Principais critérios ainda não demonstrados:** #35 serviço Windows persistente após boot/logoff, #36 validação exata de digest de receipt, #37 E2E de upload/GPU/retention em Windows, #38 limites de body JSON, #46–#48 progresso/recuperação total de preload, #53 GPU por processo, #54 ordenação global, #19 rollback testado. Qwen (#7) continua pesquisa opcional; não declarar 100% do produto com base na proporção de tickets fechados.

**Escopo de #3:** o outbox durável e tratamento de timeout estão entregues; a comparação criptográfica exata de recibo permanece no item especializado #36. **Escopo de #4:** encerramento de filho testado em CI; autostart e comportamento real após reboot permanecem em #35. **Escopo de #21:** consistência por geração de arquivo entregue; ensaio operacional completo permanece em #19/#37.

## Incidentes após o smoke inicial — atualização 30/10/2026

O histórico inicial do documento permanece válido apenas para os instantes em que foi verificado. Uma tentativa posterior de `pwsh -File .\scripts\publish-stt.ps1 -Apply -QueueDrained` na `main` **7ad6d15578698ae223e8a552dc5d861e7e13cc83** terminou **antes** de publicar o Cloudflare Worker:

| Gate/ocorrência | Evidência do operador | Estado real |
|---|---|---|
| Testes pré-restart | **86 passed, 3 skipped**, Node checks e Wrangler dry-run; HTTP site/API/origem `200/200/403` antes do stop | Aprovados **nesse checkout**; não equivalem a deploy. |
| Release manual | `stop.ps1` executado; porta 8765 observada livre no gate e ocupada em `start.ps1:8` logo depois; release abortou antes de `npm run deploy` | **Falhou**. PID que ocupou a porta não identificado; observar apenas dados sanitizados. |
| Queda pós-release | Sem listener local 8765 e sem processos Python STT encontrados; API pública **HTTP 530** | **Indisponibilidade momentânea confirmada**, [#35](https://github.com/Faysk/stt/issues/35)/[#75](https://github.com/Faysk/stt/issues/75). |
| Recuperação manual | Operador executou `start.ps1`; após 10 segundos **API pública 200 / frontend 200**; `GET 127.0.0.1:8765/api/health` sem segredo devolveu **403 esperado** pois `STT_REQUIRE_EDGE=1` | **HTTP recuperado nesse instante**, não homologação E2E nem prova de autostart. |
| Resultado de inferência | Job real gerou 147 segmentos, mas os dois finais ultrapassaram duração declarada; **HTTP 422** e falso `heartbeat_timeout` | Causa raiz da entrega confirmada; [#69](https://github.com/Faysk/stt/issues/69)/[PR #71](https://github.com/Faysk/stt/pull/71) corrigem em código **ainda sem merge ou deploy**. |
| Windows BSOD | Dump analisado localmente em WinDbg: **30/10/2026 18:53:26.322 UTC**, `0xEF` e processo crítico `svchost.exe` | [#72](https://github.com/Faysk/stt/issues/72): causa do encerramento ainda desconhecida; não presumir falha NVIDIA, CUDA ou Whisper. |

**Separação de evidência:** os primeiros retornos HTTP 422 ocorreram antes do BSOD; resultados não são a mesma RCA. O patch de restart que preserva o Tunnel está versionado na `main` após o checkout `7ad6d155` ([#76](https://github.com/Faysk/stt/issues/76)), mas **a revisão nova ainda não foi submetida ao mesmo release físico após a recuperação**; não marcar o incidente de release como corrigido na instalação.

**Gates ainda abertos:** [#35](https://github.com/Faysk/stt/issues/35) estabilidade/serviço Windows, [#75](https://github.com/Faysk/stt/issues/75) Tunnel, [#69](https://github.com/Faysk/stt/issues/69) entrega HTTP 422 e E2E novo, [#72](https://github.com/Faysk/stt/issues/72) diagnóstico BSOD, [#37](https://github.com/Faysk/stt/issues/37) testes físicos. Não reexecutar `-Apply` apenas para reparar indisponibilidade; diagnosticar primeiro API/Tunnel/porta e executar recuperação controlada conforme runbook.

**Milestones:** os cinco marcos descritos neste documento continuam **propostos** e não representam criação ou atribuição nativa no GitHub STT. Nenhuma issue crítica foi declarada concluída por testes sintéticos; [IaC #52](https://github.com/Faysk/iac/issues/52) está vinculada ao milestone nativo nº 5 do IaC. A política [IaC MAPPING_GATE](https://github.com/Faysk/iac/blob/main/docs/MAPPING_GATE.md) segue bloqueada; documentação não autoriza mudanças de infraestrutura.

### Sincronização de milestones nativos

A [workflow de metadados](../.github/workflows/sync-release-milestones.yml) cria idempotentemente **STT-M1 a STT-M5** e associa as issues abertas conforme a matriz acima, incluindo os incidentes #69/#72/#75 no M2, #35 no M2, #37 no M4 e #66 no M3. Preserva marcos escolhidos manualmente, não altera issues fechadas e não executa deploy, Terraform nem alteração de servidores/Cloudflare. É acionada após merge de sua própria definição em `main`, ou por `workflow_dispatch` explícito; **somente resultado confirmado da Action e associação consultada no GitHub comprovam sincronização concluída**.

### Reprodução adicional após correção de preservação do Tunnel — issue #78

Uma [nova issue #78](https://github.com/Faysk/stt/issues/78) registra **segunda tentativa física de publicação** no checkout `3a8b23a14d4e866282231dcc60090f16ca2cd5ce`, já com `stop.ps1 -PreserveTunnel` e `start.ps1 -SkipTunnel`: **87 passed, 3 skipped**, checks Node e Wrangler dry-run aprovados, HTTP pré-restart 200/200/403. O `Wait-SttPortReleased` observou porta 8765 liberada, mas a checagem seguinte em `start.ps1` encontrou listener e abortou. **Ainda não há identidade do PID nem confirmação do estado pós-tentativa no relato da #78; não presumir que o status HTTP recuperado na tentativa anterior se manteve.** A tentativa também terminou **antes de publicar o Cloudflare Worker**. Portanto, a preservação do Tunnel foi **exercitada no host, mas não homologada com sucesso**, e o bug de disputa da porta continua aberto.

A issue [#78](https://github.com/Faysk/stt/issues/78) pertence ao marco nativo **STT-M2** junto de #35/#75. O critério de aceite agora inclui registrar PID/parent/creation time do listener reaparecido, observar estabilidade contínua da liberação e testar recuperação sem matar processos desconhecidos. A documentação anterior deve ser lida como snapshot da primeira tentativa `7ad6d155`, não como ausência absoluta de um segundo ensaio.
