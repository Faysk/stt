# Administração interativa — contrato e validação

**Estado (30/10/2026):** a implementação base do [PR #59](https://github.com/Faysk/stt/pull/59), commit `3358a88f226fba1d91319490c77cd05c3da49d67`, está integrada em `main`. **Não há prova de deploy dessa revisão no host Windows, nem homologação física do preload CPU/GPU ou do console.** Esta página descreve contrato implementado e testes ainda exigidos; ver [prontidão de release](RELEASE_READINESS.md).

## Semântica

- Clicar no card do modelo altera o formulário local, que só é ativado nas próximas execuções após Salvar.
- O clique também solicita preload assíncrono em disco no worker selecionado, sem ocupar VRAM permanentemente.
- A API retorna operação pendente, não confirmação de disponibilidade. Só o worker atesta ready após validar os pesos.
- Os estados são pending, running, ready e failed, com recuperação após 10 minutos sem heartbeat e limpeza de operações terminais após sete dias.
- O executor pode continuar recebendo transcrições enquanto prepara pesos em uma thread secundária. Existe potencial de contenção de banda e disco, portanto validar com o hardware real.
- A compatibilidade de novos compute types depende do CTranslate2 instalado no worker e é verificada também no servidor. Tipos legados continuam por compatibilidade.
- A API e o worker podem viver em máquinas diferentes. Por isso o cache do host da API não comprova que o executor tem os pesos.
- gpu_name em cada job é uma fotografia da identificação reportada pelo worker ao aceitar trabalho CUDA; não mede utilização exclusiva de GPU ou VRAM.
- Qwen permanece experimental e fora deste fluxo.

## Matriz de aceitação

1. Login/autorização: erro 401 encerra a sessão, sem mostrar dados privados ou segredos.
2. Seleção de modelo com mouse, touch e teclado; clicar na documentação não seleciona nem baixa modelo.
3. Preload de tiny/small fora do cache, verificando revisão e arquivo completo no executor certo.
4. Desligar/reiniciar o worker durante download e verificar recuperação sem status pronto falso.
5. Enviar trabalho autorizado enquanto baixa modelo; comprovar que o loop principal continua reivindicando jobs.
6. Alternar executor com GPU e CPU-only, verificando tipos realmente disponíveis e rejeição de combinações sem suporte.
7. Filtrar e paginar histórico com rede atrasada, sem resultados de busca antigos substituindo a busca atual.
8. Verificar CSV, detalhe de execução, nome GPU por job, dados antigos e VRAM indisponível n/d.
9. Testar viewport 375, 768, 1366 e 1920 px, reduced-motion, foco, CI Linux e Windows.
10. Não versionar áudios, textos, credenciais nem pesos de modelo.

## Pendências

Progresso em bytes/percentual, cancelamento de preload, quota mínima de disco, validação de VRAM por processo em Windows e ensaios físicos na RTX 4070 permanecem a comprovar. **Não declarar o painel homologado nem fazer deploy automático só porque o PR foi integrado.**


## Política de atualização proposta — STT #100 (03/11/2026)

**Pendente de publicação/medição**: o cliente público reduz o refresh em idle para **30 s** (2 chamadas por ciclo: health + lista do próprio usuário) e mantém **5 s** se houver job `queued/running/uploading` na sessão. O admin reduz para **30 s** quando não houver trabalhos ativos e preserva **10 s** durante trabalho ativo; histórico e métricas de clientes ficam limitados a **1 atualização/min** sem mudança manual de período. Abas ocultas suspendem consultas e sincronizam ao voltar a ficar visíveis.

Falhas sucessivas do HTTP fazem backoff até 120 s em vez de requisições agressivas. O painel admin reconhece HTML da página Cloudflare `Error 1027` e apresenta mensagem específica, sem encerrar a sessão por falso 401. Na UI pública, erros continuam informando indisponibilidade sem inventar estado de execução da GPU. Os dados da biblioteca já carregados não são apagados por falha de polling.

**Testes/aceite ainda necessários:** fake timer/DOM em browsers reais, upload/console longos, duas abas, i18n, logout e retoma, diagnóstico de tráfego antes/depois por invocações Worker (não downloads de assets). Revisar latência percebida quando novos jobs forem criados noutras abas e garantir refresh imediato após ações do usuário. A referência de segurança/quotas é [IaC #64](https://github.com/Faysk/iac/issues/64), incidente [STT #97](https://github.com/Faysk/stt/issues/97).

Nenhuma mudança de Cloudflare, Tunnel, DB, Worker de inferência ou sessão autorizada decorre deste PR; rollback é publicar a revisão frontend anterior sob procedimento de release supervisionado.


## Timer lifecycle/histórico entre sessões (correção STT #100)

O scheduler público mantém **um único timer futuro** e cancela atualizações ao ocultar a aba; ao voltar a ficar visível, atualiza imediatamente. No admin, o timer **não é mantido** quando não há sessão autenticada ou a página fica oculta. Logout invalida o timer, reseta `lastPeriodLoad`/estado de erro/atividade e limpa dados administrativos anteriores. Um login posterior deve buscar histórico/clientes na primeira atualização, mesmo que a sessão anterior tenha carregado esses dados há menos de 60 segundos.

Os testes de fake timers em `tests/polling_scheduler_lifecycle.test.cjs` garantem ausência de timers duplicados e suspensão sem network enquanto oculto/desconectado. Ainda é necessário testar no browser real (várias abas, sessão Passkey/QR, tempo de espera e estados 1027). Nenhum deploy no Worker Cloudflare ocorre por commit Git.
