# Transcrição por link

Cole o endereço no campo **Link de vídeo** e selecione **Transcrever**. A
plataforma é reconhecida automaticamente. Não é necessário escolher uma rede.

| Plataforma | Endereços aceitos |
| --- | --- |
| YouTube | Vídeos e Shorts; links `youtu.be` |
| Instagram | Reels, vídeos e publicações |
| TikTok | Links de vídeo e links compartilhados `vm`, `vt` e `/t/` |
| Facebook | Vídeos, Watch, Reels e links compartilhados com redirecionamento válido |
| X / Twitter | Links de publicações com vídeo, incluindo seleção `/video/1` |

O serviço importa um vídeo por vez. Em publicações com vários itens, somente o
primeiro é considerado. Perfis, canais, listas de reprodução e transmissões ao
vivo não são importados. Uma publicação sem áudio utilizável pode não gerar texto.

O vídeo precisa estar acessível publicamente. Mesmo um link público pode ficar
indisponível por remoção, restrição regional, exigência de login ou bloqueio da
plataforma. O serviço não usa contas ou cookies pessoais dessas redes. Se o
link não puder ser importado, envie o arquivo de áudio ou vídeo diretamente.

Os limites de tamanho, duração, fila e disponibilidade exibidos no site também
valem para links. A importação depende de um executor completo disponível;
no modo limitado, utilize o envio de arquivo. Os arquivos e resultados expiram
em 24 horas, com acesso associado ao navegador que fez o envio.

## Contrato e operação

- Entrada única: `POST /api/jobs/link`, JSON `{"url":"https://…"}`, com a mesma
  sessão anônima e limites de admissão do upload.
- A rota anterior `/api/jobs/youtube` é preservada como alias de compatibilidade.
- Hosts e caminhos de vídeo são validados antes de entrar na fila. Parâmetros
  de rastreamento são removidos. Cada redirecionamento de link compartilhado é
  validado antes de ser seguido e deve permanecer na plataforma original.
- Somente os extratores das cinco plataformas são ativados; não há fallback
  genérico para baixar qualquer endereço fornecido.
- Whisper e Qwen compartilham a importação. O download mantém teto de 90 MiB,
  áudio de até duas horas, tentativas limitadas, fragmentos obrigatórios e prazo
  máximo de dez minutos, fiscalizado pelo processo supervisor do job.
- Os códigos fixos `LINK_UNAVAILABLE`, `LINK_LIMIT` e `LINK_NO_AUDIO` possuem mensagens nos dez
  idiomas da interface. Mensagens externas que podem conter URLs assinadas ou
  detalhes de acesso não são apresentadas nem registradas pelo downloader.

## Verificação de 02/11/2026

Validação offline cobre as cinco plataformas, normalização, isolamento de
sessão, quotas, modo limitado, redirecionamento inseguro, seleção restrita de
extratores e rejeição de transmissões/limites. O download real de amostras
públicas de YouTube, Instagram, Facebook e X funcionou. Duas amostras públicas
do TikTok retornaram indisponíveis; o suporte ao extrator não constitui garantia
de acesso a qualquer vídeo da plataforma.

O caminho público `link → fila → GPU → resultado` foi exercitado com YouTube,
X e Facebook; os quatro formatos de exportação e o isolamento entre sessões
foram conferidos. A amostra do Facebook não produziu fala reconhecida. As duas
amostras do Instagram disponibilizaram somente vídeo, sem faixa de áudio;
esse caso recebe `LINK_NO_AUDIO`, sem alocar o modelo. O erro do TikTok não
impediu os trabalhos seguintes de serem processados.

Suite final: 206 testes Python aprovados, cinco skips por plataforma/permissão;
checks JavaScript, localização, proxy e proteção de deploy aprovados.

Referência: [plataformas incluídas no yt-dlp](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md).
O comportamento pode mudar quando as plataformas alteram suas interfaces.
