# stt / faysk

**Transforme áudio e vídeo em texto.**

[Usar o serviço](https://stt.faysk.dev) · [Guia de desenvolvimento](docs/DEVELOPMENT.md) · [Licença MIT](LICENSE)

stt / faysk é uma plataforma aberta de transcrição, com processamento acelerado por GPU, reconhecimento multilíngue e exportação em formatos prontos para leitura, edição e legendagem. O acesso público é gratuito e não exige cadastro.

## Como usar

1. Envie um arquivo de áudio ou vídeo, ou cole o link de um vídeo público do YouTube.
2. Acompanhe o processamento na sua lista de transcrições.
3. Leia o resultado com marcações de tempo ou em texto corrido. Baixe o formato desejado com um clique.

A interface se adapta ao idioma do navegador e oferece seleção manual em dez idiomas. O idioma falado no áudio é detectado de forma independente.

## Recursos

- Transcrição de áudio, vídeo e vídeos públicos do YouTube.
- Aceleração por GPU e modelos de reconhecimento de fala Whisper.
- Leitor com timestamps, texto corrido e cópia do conteúdo.
- Downloads diretos em TXT, Markdown, SRT e JSON.
- Histórico temporário com busca, filtros e acompanhamento da fila.
- Interface responsiva para computadores, celulares e dispositivos em orientação vertical.
- Console administrativo autenticado, com fila, prioridades, métricas de atividade e catálogo de modelos.

## Formatos de exportação

| Formato | Uso recomendado |
| --- | --- |
| **TXT** | Leitura, pesquisa e edição de texto simples. |
| **Markdown** | Notas, documentação e publicação. |
| **SRT** | Legendas com marcações de tempo. |
| **JSON** | Integrações, segmentos e processamento estruturado. |

## Arquivos e privacidade

Os arquivos e resultados ficam disponíveis por **24 horas** após o envio. O acesso às suas transcrições é associado a uma chave armazenada no navegador. Limpar os dados do site ou encerrar uma sessão anônima remove esse acesso.

Após a expiração, o acesso é bloqueado imediatamente. A limpeza física é feita pela manutenção do servidor, com novas tentativas se ocorrerem erros de I/O ou bloqueios temporários; se o servidor estiver desligado, a purga só pode retomar quando ele voltar. Métricas operacionais e identificadores pseudônimos de sessão e rede são conservados por até **30 dias** para diagnóstico e prevenção de abuso. Após esse prazo, eventos granulares e identificadores de sessão/rede são removidos. Contagens e estatísticas operacionais agregadas podem permanecer por até **5 anos** para acompanhar a evolução do serviço, sem fornecer acesso às transcrições. Não armazenamos endereços IP brutos no histórico de trabalhos.

## Limites e qualidade

Cada envio aceita até **90 MB** e **duas horas** de duração. Limites de frequência e capacidade de fila ajudam a distribuir os recursos do serviço. Vídeos do YouTube dependem de disponibilidade pública e das condições de acesso da plataforma.

A qualidade varia conforme ruído, idioma, sotaque e características da gravação. Revise o texto antes de usar em contextos que exigem precisão. A disponibilidade pode variar durante manutenção ou ajustes de capacidade.

## Projeto aberto

O código está disponível sob a licença MIT. Para contribuir, executar sua própria instalação ou verificar o projeto, consulte o [guia de desenvolvimento](docs/DEVELOPMENT.md).

As licenças dos modelos, bibliotecas e conteúdos utilizados continuam aplicáveis.

## Motores experimentais e validação

A operação pública utiliza Whisper. O suporte opcional a Qwen3-ASR e métricas detalhadas está em desenvolvimento, isolado do ambiente Whisper e sujeito a validação física antes de qualquer ativação pública. [Plano técnico, receitas e benchmark](docs/QWEN_AND_BENCHMARK.md).
