# Administração interativa — contrato e validação

Esta documentação descreve a branch de revisão do épico #57, não a versão publicada.

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

Progresso em bytes/percentual, cancelamento de preload, quota mínima de disco, validação de VRAM por processo em Windows e ensaios físicos na RTX 4070 permanecem a comprovar. Não publicar sem revisão e testes.
