# Guia de desenvolvimento

Este guia descreve uma instalação independente do projeto. Exemplos e configurações não representam a infraestrutura de produção do serviço público.

## Componentes

- **Interface web:** HTML, CSS e JavaScript, com layout adaptativo e dez idiomas.
- **API:** FastAPI, armazenamento de trabalhos e métricas, autenticação administrativa e sessões por chave de navegador.
- **Executor:** agente que recebe trabalhos por conexão de saída e executa o reconhecimento de fala com faster-whisper.
- **Publicação:** Worker para distribuição de assets e encaminhamento das requisições da API.

## Preparação

Requisitos: Python 3.11 ou superior, `uv` e Node.js 22 ou superior. A aceleração CUDA requer hardware e drivers compatíveis. Alguns fluxos de mídia podem precisar de FFmpeg.

```powershell
uv sync --group dev
Copy-Item .env.example .env
```

Configure caminhos de armazenamento adequados ao seu ambiente e gere segredos independentes para administração, executores e comunicação com a API. Nunca versione credenciais, arquivos enviados ou dados operacionais.

Para desenvolvimento, configure `TTS_REQUIRE_EDGE=0` e execute:

```powershell
python -m uvicorn server.app:app --host 127.0.0.1 --port 8765
# Em outro terminal com o mesmo ambiente virtual:
python -m server.worker
```

Para habilitar as dependências CUDA opcionais:

```powershell
uv sync --extra gpu --group dev
```

O console em `/admin.html` exige `TTS_ADMIN_KEY`. A navegação administrativa é exibida após a autenticação; a autorização dos dados é validada pela API. A chave permanece somente na memória da aba.

## Execução e modelos

Antes de processar, selecione no console o identificador configurado em `TTS_WORKER_ID`. O console permite escolher executor, modelo, dispositivo e precisão para os próximos trabalhos. A prioridade varia de 0 a 9, com maior valor primeiro e ordem de envio nos empates. Alterações não interrompem trabalhos em execução.

Novos agentes precisam de um identificador próprio, URL de API e credencial de executor. O protocolo usa claim, heartbeat e resultado. Trate os agentes como componentes confiáveis: eles recebem acesso à mídia atribuída. As revisões dos pesos e versões do motor são registradas nas novas execuções.

As referências de memória exibidas no catálogo são estimativas dos modelos originais; o consumo real depende do motor, da precisão e da mídia. Use medições do seu ambiente antes de definir capacidade.

## Histórico e métricas

O histórico registra eventos, horários, duração, tentativas, modelo e recursos por trabalho. Métricas do serviço são coletadas periodicamente com agregações por diferentes resoluções. Valores ausentes são apresentados como lacunas. Não preencha períodos anteriores à coleta com dados atuais.

A expiração bloqueia o acesso aos resultados após 24 horas. A limpeza física ocorre durante a execução da API, incluindo a retomada após indisponibilidade. Métricas e identificadores pseudônimos permanecem separados do conteúdo.

## Verificação

```powershell
uv run pytest -q
npm run check
node tests/i18n.test.cjs
node tests/edge.test.cjs
```

Os testes cobrem autorização, isolamento de sessões, expiração, prioridade, consultas administrativas, agregação de métricas, idiomas e roteamento. Valide também o fluxo completo com uma mídia de referência. Resultados de desempenho são específicos do ensaio, não garantias de capacidade.

## Publicação

Instale as dependências JavaScript com `npm ci`. Ajuste as rotas e variáveis do Worker para a sua instalação, configure credenciais Cloudflare no ambiente e use `npm run deploy`. O provisionador em `scripts/cloudflare.py` é específico da configuração deste projeto; revise-o antes de reutilizar em outro domínio.

Em produção, habilite a autenticação da origem com `TTS_REQUIRE_EDGE=1`. Assets públicos podem ser indexados; console administrativo e API devem permanecer fora dos mecanismos de busca. Não exponha diretórios de dados por HTTP.


## Auditorias de implementação

- [Auditoria TTS de 30/10/2026](audits/2026-10-30-tts.md): baseline, falhas confirmadas, reproduções isoladas e plano de evolução dos motores.

Achados de auditoria não significam correções implementadas. O relatório distingue bugs, melhorias planejadas e verificações que não foram executadas.

## Novas garantias de confiabilidade (branch experimental)

- Admissão de upload é realizada **antes do parser multipart**, com reserva
  transacional por rede/sessão e até 4 uploads simultâneos.
- A expiração da API bloqueia o acesso imediatamente, mesmo que a remoção
  física falhe. `purged_at`, `purge_attempts` e `purge_error` permitem
  diagnóstico; novos passes respeitam backoff. Não apague manualmente o banco
  para "resolver" um erro de purga.
- O executor escreve resultados em uma **outbox local persistente**
  (`TTS_OUTBOX`, por padrão `worker-outbox/`) antes de enviar. Não coloque
  esse diretório em cloud sync, Git ou em pasta pública. Configure volume
  persistente com ACL restrita e política de backup/eliminação. A confirmação
  por recibo permite reenvio após restart sem perder resultado válido.
- Se o executor ficar desligado, limpezas pendentes só retomam ao reiniciar;
  a outbox é saneada por expiração quando o executor volta.
- A tentativa de inferência tem deadline desde o início da preparação. Os
  subprocessos são geridos por grupo/árvore própria, sem `taskkill` global
  por nome. O script `stop.ps1` valida a identidade dos processos raiz.
- Heartbeats são serializados com a manutenção e condicionados ao lease atual.
- Resultados com cronologia inválida recebem 422; resultados idênticos
  enviados novamente recebem o mesmo recibo sem criar outra conclusão.
- [Operação Qwen3 e benchmark v2](QWEN_AND_BENCHMARK.md) descreve receitas
  isoladas, limitações, métricas nulas e requisitos antes de produção.

**Importante:** a aprovação da CI valida mocks e testes isolados. Não implica
deploy, ensaio com GPU, teste do Cloudflare em produção ou validação de
`taskkill` com árvore de processos reais.
