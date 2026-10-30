# STT cutover — roteiro controlado (NO-GO até validação)

**Issue:** #19 · **Épico:** #11 · **Versão:** 30/10/2026.

> Isto é um plano. **Não executar automaticamente na produção.** Substituir placeholders por recursos verificados, aplicar com operadores autorizados e anexar evidências sanitizadas. Não escrever credenciais, transcrições, mídia ou cópias de bancos no GitHub.

## Pré-condições obrigatórias

- [ ] Confirmar merge/review de PR #10 (correções base), PR #26 (#20–#24) e PR #27 (compatibilidade #13/#14), respeitando a ordem de dependência; checks verdes **não bastam**.
- [ ] Levantar inventário **real** do ambiente: `wrangler.jsonc` e deployed Worker, DNS zone/records, custom domains, SSL/TLS, tunnel ID/ingress, secret bindings, tokens, proxy e Windows service/scheduler.
- [ ] Fazer smoke de upload/download e health no serviço atual; registrar HTTP status, tempos, quantidade de jobs ativos, espaço em disco, versão implantada e logs sanitizados.
- [ ] Definir hora/canal de comunicação, operador, janela de reversão e plano de redução de tráfego sem interromper trabalhos válidos.
- [ ] Verificar capacidade de rollback **incluindo o novo layout `generations-v1`**, que versões antigas da API não leem diretamente. Ver ROLLBACK.md.
- [ ] Confirmar que `stt.faysk.dev` tem DNS/SSL/Worker prontos **antes** de atualizar canonical ou direcionar usuários.

## Fase 1 — snapshot consistente, sem publicar conteúdos

1. Congelar versão de código/infra e IDs de recurso; **não copiar secrets para documento**.
2. Guardar backup transacional do SQLite sob armazenamento privado. Com WAL ativo, **não** copiar só o arquivo `.sqlite3` enquanto há writes. Usar API `sqlite3.Connection.backup` ou parada coordenada dos processos, e verificar `PRAGMA integrity_check` na cópia.
3. Copiar de forma consistente os diretórios `blob/`, `worker-outbox/` e eventuais resultados/recibos, respeitando jobs in-flight; operações em curso precisam ser drenadas ou a cópia reconciliada.
4. Confirmar tamanho, permissões, integridade dos arquivos e política de retenção dos backups. Cópias temporárias também são dados de usuários.
5. Registrar referência do commit, checks e hashes de artefatos de implantação — não de transcrições pessoais.

## Fase 2 — coexistência sem migrar dados

1. Publicar código compatível com `STT_*`/`TTS_*` em **staging** com dados de teste e configurações isoladas.
2. Publicar `stt.faysk.dev` de forma paralela e TLS válido; decidir explicitamente se `stt-origin.faysk.dev` é necessário ou se a origem antiga permanece alias interno.
3. Provar que Edge sobrescreve cabeçalhos de identidade fornecidos por cliente e a origem recusa acesso não autorizado. Não alterar segredo ativo/rotas antigas durante o ensaio.
4. Testar `/api/health`, status 429, POST/upload pequeno e limite de 90 MiB, YouTube público autorizado, listagem, recibo, TXT/MD/JSON/SRT quando aplicável, admin e worker em ambiente separado.
5. Criar teste de sessão em `tts.faysk.dev` e ver transcrição pelo host antigo até expirar; storage de `stt.faysk.dev` é independente. **Não fazer 301/302/308 geral em /api/**.
6. Conferir `Cache-Control`, CSP, CORS (se implementado), X-Robots, robots.txt, sitemap, canonical, manifest, ícones, 10 idiomas e mobile.

## Fase 3 — troca progressiva

1. Obter **GO documentado** em #19. Se algum requisito não passou: **NO-GO**.
2. Habilitar novo host para novos acessos mantendo legado navegável e seus endpoints de leitura/worker durante a drenagem.
3. Manter jobs/sessões antigas acessíveis até TTL de 24 horas **e margem operacional documentada**, sem estender TTL dos dados.
4. Redirecionar apenas requisições de navegação elegíveis; **nunca redirecionar POST multipart/body sem ensaio e confirmação de headers/método**.
5. Medir erros 4xx/5xx, uploads, fila, tempo por job, outbox, disco, CPU/GPU, sessões e downloads nos dois hosts; abortar em regressão.
6. Atualizar identidade/SEO, links e repositório somente após validar o domínio público e garantir que PRs/webhooks remotos persistam.
7. Documentar data/hora de encerramento do legado e comunicação transparente aos usuários.

## Evidência e encerramento

- [ ] Resultado de testes HTTP/DNS/TLS e screenshot/config saneada, commits exatos, status checks.
- [ ] Backup restaurado em staging, integrações de worker e downloads verificados.
- [ ] Rollback ensaiado com *novo e antigo layout* (ver ROLLBACK.md).
- [ ] Incidentes observados e resolução documentada antes do fechamento de #11/#19.

**Nunca concluir migração somente porque o GitHub Actions está verde.**