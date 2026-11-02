# Auditoria de confiabilidade e operação — 02/11/2026

## Resultado e alcance

Revisão do serviço STT: proxy de borda, admissão e upload, SQLite/fila, leases,
transferência, subprocessos Whisper/Qwen, entrega durável, retenção, autenticação,
frontend/admin, supervisão Windows e operação autônoma S7.

Foram combinados leitura de código, testes com falhas simuladas, consultas
operacionais sem conteúdo privado, auditoria de dependências e dois ensaios
reais pelo domínio público. Isso não equivale a teste de penetração, ensaio de
carga prolongado, validação de todos os modelos/GPU ou prova de recuperação
após falha física. Questões antigas foram reaproveitadas para evitar duplicação.

## Incidente

Um trabalho permaneceu em `preparing` com heartbeat ativo antes de iniciar
inferência. A configuração temporária apontava para um diretório legado sem
acesso. Depois de corrigido o caminho, a transferência longa foi interrompida
com `RemoteProtocolError`. Não houve evidência de falha de GPU nesse incidente.

A transferência em ranges de 1 MiB recuperou o trabalho: duração 637,87 s,
Turbo/CUDA, motor 31,06 s, estado `complete`. Os commits iniciais de recuperação
foram `c338228` e `0b1bf62`. Esta auditoria endurece essa mitigação.

## Achados e tratamento

| Prioridade | Achado | Tratamento / acompanhamento |
| --- | --- | --- |
| P1 | Claim antes de validar escrita, remoção e espaço temporário; repetição de acesso negado no Windows | Corrigido: preflight real, reserva de 512 MiB, criação com até oito colisões; PermissionError falha imediatamente. Prontidão é publicada antes do claim. [#91](https://github.com/Faysk/stt/issues/91) |
| P1 | Range sem consistência de total/representação; transporte longo frágil | Endurecido: total e ETag consistentes quando informado, contiguidade, até 1 MiB por resposta parcial, comprimento/bytes, recusa de vazio, três tentativas por trecho, HTTP transitório e prazo de transferência de 600 s. Progresso real em `downloading`. Hash completo/paridade S7 pendentes. [#92](https://github.com/Faysk/stt/issues/92) |
| P1 | Qualquer digest hexadecimal era aceito como ACK | Corrigido: contrato canônico compartilhado API/worker; S7 stdlib compatível e testado. Digest divergente preserva resultado em quarentena. Conflito preserva resultado até TTL. [#36](https://github.com/Faysk/stt/issues/36) |
| P1 | Entrega pendente sem renovação de lease | Corrigido nos dois clientes: heartbeat de publicação pendente, timeout de entrega limitado e tratamento de ACK perdido. Heartbeat 409 não impede repetir o endpoint idempotente de resultado. [#93](https://github.com/Faysk/stt/issues/93) |
| P1 | Heartbeat NULL não era recuperado; heartbeat vivo podia manter execução indefinidamente no coordenador | Corrigido: fallback para início/criação e prazo autoritativo de duas horas por tentativa. Política detalhada por etapa e diagnóstico de filas bloqueadas continuam pendentes. [#93](https://github.com/Faysk/stt/issues/93) |
| P1 | Outbox permanente/corrompida podia bloquear todo o S7 | Corrigido: quarentena não bloqueia novos jobs; corrupção própria expira após 24h; controle de potência ocorre antes da drenagem. Erro de autenticação continua impedindo novos trabalhos. [#20](https://github.com/Faysk/stt/issues/20) |
| P2 | JSON de resultados podia usar orçamento de upload, com metadados arbitrários | Mitigado: teto de resultado 2 MiB, demais JSON 32 KiB, profundidade 32 antes de parse inclusive chunked; runtime limitado a 8 KiB, 256 itens e seis níveis. Prova com saídas reais de duas horas permanece pendente. [#38](https://github.com/Faysk/stt/issues/38) |
| P1 | Presença do agente anunciada como disponibilidade apesar de armazenamento bloqueado | Corrigido para prontidão de armazenamento: claim, health e capacidades respeitam `ready=false`; admin apresenta motivo sanitizado. Isso não prova inferência GPU nem disponibilidade futura de VRAM. [#91](https://github.com/Faysk/stt/issues/91), [#93](https://github.com/Faysk/stt/issues/93) |
| P1 | Reserva de upload não contabiliza atomicamente todos os concorrentes; I/O/probe/limpeza podem atrasar API/SQLite | Aberto: limites concorrentes, retirada de I/O bloqueante e medição de latência sob carga. [#94](https://github.com/Faysk/stt/issues/94) |
| P1 | SD é ponto único de falha; backup/restauração externa não comprovados | Aberto: cópia consistente e privada fora do volume e restauração isolada. Não usar banco antigo como failover. [#94](https://github.com/Faysk/stt/issues/94) |
| P1 | Retorno após reboot/logoff e Doze não homologado | Aberto: testes físicos, supervisão e alertas externos. Não foram reiniciados os dispositivos nesta auditoria. [#35](https://github.com/Faysk/stt/issues/35), [#90](https://github.com/Faysk/stt/issues/90) |
| P2 | Decoder PyAV sem política explícita uniforme de protocolos/demuxers | Hardening aberto; exploração não demonstrada. Extensão não comprova formato. [#95](https://github.com/Faysk/stt/issues/95) |
| P1/P2 | OOM, troca de GPU, pesos inválidos, rejeições 422 e qualidade linguística não têm matriz completa | Reaproveitar regressões existentes e completar ensaios reais autorizados. [#22](https://github.com/Faysk/stt/issues/22), [#37](https://github.com/Faysk/stt/issues/37), [#69](https://github.com/Faysk/stt/issues/69), [#72](https://github.com/Faysk/stt/issues/72) |

O limite de duas horas acima é tempo máximo por tentativa no coordenador, além
do teto de duas horas de duração de áudio. São limites distintos. Ranges/ETag
detectam inconsistências, mas não substituem um hash completo do original.

## Proteções verificadas

- Sessões públicas anônimas e autorização por proprietário; outra sessão não
  consegue consultar o job nem seus resultados.
- Origem protegida por chave de borda, segredo S7 com escopo próprio, sessão
  administrativa/cookies, CSRF e fluxo QR/passkey nos testes de regressão.
- Publicação atômica por geração de resultado e idempotência de recibos.
- Fencing por lease, desligamento com devolução à fila e proteção contra ACK
  falso sem apagar a outbox.
- Retenção de arquivos 24h, retries de purge, limites de histórico identificável
  e defesa contra symlinks nos testes disponíveis.
- Recursos do S7 mantêm piso interno de 2 GiB, cache próprio limitado e dados
  persistentes no volume designado; nenhuma alteração em outros serviços.
- SEO, localização, deploy guard e proxy continuam passando nos testes. Não foi
  solicitada nova indexação nem alterada a política Cloudflare.

## Validação

### Automatizada

- Python completo: **163 passed, 5 skipped** no Windows. Duas verificações de
  symlink sem privilégio e três verificações condicionais de plataforma não
  executaram; não são apresentadas como aprovadas. Os testes de symlink seguem
  ativos no CI Linux. Base temporária de testes isolada do sistema e do serviço.
- Regressões novas: acesso negado sem repetição, falta de espaço/recuperação,
  range inconsistente, ETag alterado, conteúdo vazio/truncado, HTTP transitório
  e permanente, recibo errado, heartbeat de publicação/ACK perdido, paridade de
  recibo S7, quarentena, prontidão, deadline e limites JSON antes de parse.
- `npm run check`, testes de localização, proxy e deploy guard aprovados;
  `compileall` em server/s7/scripts aprovado.
- `npm audit`: zero vulnerabilidades reportadas no lockfile consultado.
- `pip-audit` das dependências de produção exportadas do `uv.lock`: nenhuma
  vulnerabilidade conhecida reportada. Isso não cobre drivers, firmware ou
  pacotes nativos do sistema do S7.

### Caminho público e hardware reais

| Executor | Modelo/dispositivo | Áudio | Tempo do motor/worker | Resultado |
| --- | --- | --- | --- | --- |
| Principal, `c86d4235` | Turbo / CUDA | 11,008 s | 11,808 s | complete |
| S7, `bb8cf1eb` | Tiny / CPU | 11 s | 13,907 s | complete |

Os dois ensaios usaram mídia pública de referência, sem reenviar áudio privado.
TXT, Markdown, SRT e JSON foram obtidos pela sessão proprietária; uma sessão
diferente recebeu 404. Confirmação de recibo estrito foi exercitada nos dois
clientes; outboxes ficaram vazias. SQLite `quick_check` retornou `ok`.

A atualização ocorreu com fila vazia e temporariamente pausada. A configuração
original foi restaurada: principal selecionado, Turbo/CUDA/float16, fila
liberada; ambos os agentes prontos. Não houve mudança de DNS nem de conector.

Entrega de código: `707d27a`. Frontend publicado no Worker STT, versão
`5448f31a-fb0f-426e-be89-91a7b66c08f6`. Verificação final: health público 200,
origem sem segredo 403, fila vazia e capacidades completas 90 MiB/7200 s.
Backend e clientes foram conferidos separadamente do deploy de assets.

## Ordem de trabalho restante

1. Reservas concorrentes, I/O do coordenador, backup e restauração (#94).
2. Boot real e ensaio prolongado com falha de rede/energia/Doze (#35/#90).
3. Hash de entrada e transferência resiliente uniforme nos clientes (#92).
4. Timeouts por etapa, diagnóstico da fila e prontidão GPU por configuração
   (#93/#22), incluindo rejeição de resultado e recuperação sem reprocessamento.
5. Allowlist do decoder (#95), saídas próximas de duas horas, carga concorrente
   e retenção física em instalação real (#38/#37).

Referência de decoder: [documentação primária do FFmpeg](https://github.com/FFmpeg/FFmpeg/blob/master/doc/protocols.texi).
Os defaults de protocolos aninhados não comprovam uma exploração; a issue #95
registra explicitamente a necessidade de teste isolado.
