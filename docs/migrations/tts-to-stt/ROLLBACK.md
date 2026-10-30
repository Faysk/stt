# STT rollback — roteiro e armadilhas de compatibilidade

**Issue:** #19 · **Épico:** #11 · **Versão:** 30/10/2026.

## Regra central

**Rollback de código não é rollback de dados.** As versões da API anteriores ao draft PR #26 escrevem/leem arquivos como `blob/<job>/transcript.txt` etc. A nova publicação usa `blob/<job>/results/<receipt>/transcript.*` e um ponteiro `artifact_layout='generations-v1'` no SQLite.

Uma API **nova lê arquivos legados**, mas uma API **antiga não encontra automaticamente as gerações novas**. Voltar o executável sem um plano adicional **pode ocultar resultados válidos de usuários**. Este é bloqueador de produção até existir uma conversão/reversão validada.

## Antes de qualquer rollback

1. Suspender novos uploads/claims no caminho afetado e observar jobs em andamento. Preservar recibos e outbox, nunca descartar arquivos em trânsito.
2. Registrar commit implantado, host/Worker/Tunnel/segredos e estado dos jobs (só identificadores operacionais sanitizados).
3. Garantir snapshots privados de SQLite consistente, pastas `blob/` e `worker-outbox/`, testes de integridade e trilha de hashes de artefatos de implantação.
4. Separar problema de roteamento (Cloudflare/worker) de problema de código/dados: revertendo somente Cloudflare, a API atual deve continuar compatível com a mesma base.
5. Confirmar plano de leitura dos resultados novos **antes** de retornar à versão antiga.

## Ferramenta de preparação de exportações legadas (código em draft PR #29)

O [draft PR #29](https://github.com/Faysk/tts/pull/29) acrescentou `scripts/restore_legacy_exports.py`, com testes sintéticos. A ferramenta materializa **somente** exportações de jobs válidos e não expirados em `generations-v1`; não apaga gerações, não altera SQLite e bloqueia SRT antigo quando o resultado novo não tem tempos.

**Inspeção sem escrita, antes de qualquer intervenção** (substituir o caminho de exemplo pelo DATA real sem divulgar no GitHub):

```powershell
py scripts/restore_legacy_exports.py --data-root "D:\\dados-privados\\transcricao"
```

**Aplicação controlada APENAS após interromper todos os writers, fazer backup consistente e testar a restauração**:

```powershell
py scripts/restore_legacy_exports.py --data-root "D:\\dados-privados\\transcricao" --apply --ack-offline-and-backed-up
```

Um retorno `status=blocked` é **NO-GO**, não autorização para remover arquivos manualmente. O script verifica antes todos os resultados legados previstos e reutiliza cópias idênticas; numa falha após copiar alguns arquivos, **não reiniciar a API antiga** até inspecionar o erro, restaurar ou executar novamente e ter sucesso. Não cria snapshot por conta própria: o operador é responsável pelo backup e pela janela sem novos uploads/claims.

**Limite importante:** CI verde prova cenários sintéticos; não comprova que rollback em volume NTFS/banco real/Cloudflare é seguro. A issue #28 centraliza os testes humanos obrigatórios.

## Ordem de rollback recomendada

- **Apenas DNS/Cloudflare:** restaurar bindings, rotas, origin, Worker e Tunnel a partir da configuração exportada, **sem apagar** recursos novos nem sobrescrever ingress de serviço compartilhado. Checar TLS/hostname e chamadas não-GET. Nunca publicar secrets em logs.
- **Apenas branding/JS:** restaurar host antigo para navegação, preservar `localStorage` e acesso a transcrições que ainda não expiraram. Evitar 301/302 genérico de endpoints API.
- **Backend/worker sem mudança de contrato:** reverter os binários somente se a versão destino foi comprovada compatível com o banco e artefatos já escritos. Validar `x-tts-client`, credenciais, schema SQL e leases.
- **Rollback de `generations-v1` para API antiga:** **NO-GO sem utilitário de downgrade testado**. Será preciso, numa manutenção sem writers, materializar um conjunto consistente de `transcript.*` flat a partir da geração confirmada no DB, com verificação de recibo/timing, flush, rename atômico por arquivo e relatório de divergências. Nunca copiar diretórios órfãos ou SRT de `timing='none'`. Preferir voltar a uma versão **nova compatível**, não à versão flat antiga, quando possível.
- **Restauração integral de backup:** somente como último recurso, com RPO/RTO acordado, teste de restore e aviso sobre jobs enviados após o snapshot. Não substituir banco isoladamente sem restaurar mídia/outbox correspondentes.

## Matriz de compatibilidade necessária para aprovação

| Estado de dados | API nova | API antiga |
|---|---|---|
| `db/tts.sqlite3` + artefatos flat | Suportada por fallback | Suportada |
| `artifact_layout=generations-v1` e artefatos somente em `results/<receipt>/` | Suportada | **Não suportada sem downgrade** |
| Jobs em andamento e outbox com receipt/idempotência | Exige teste de fencing | **Risco de contrato divergente** |
| Config `STT_*` somente | Suportada após PR #27 | Pode exigir `TTS_*` explícitas |
| Sessão browser `stt.session` sem `tts.session` | Suportada após PR #27 | **Não presumir compatibilidade** |

## Critérios para dizer “rollback testado”

- [ ] Ensaio com fila ativa, conclusão de resultado, exportações e reinício do executor.
- [ ] Simulação de falha de disco, SQL/arquivo, Cloudflare 5xx, sessão em dois hosts e worker antigo/novo.
- [ ] Comparar dados/arquivos antes/depois: contagem de jobs, estado/lease, caminhos e hashes internos dos mesmos resultados, sem publicá-los.
- [ ] Downloads de trabalhos antigos **e novos** funcionam após reverter; nenhuma SRT inexistente reaparece.
- [ ] Registrar duração, RPO/RTO real, operador e critérios de abortar o rollback.

**Estado:** plano documentado, **não ensaiado** em Windows/Cloudflare nem autorizado para produção.