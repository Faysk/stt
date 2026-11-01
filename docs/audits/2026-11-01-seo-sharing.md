# Auditoria de compartilhamento e indexação — 1 de novembro de 2026

## Escopo e resultado

Repositório Faysk/stt, baseline `d61293c`. Revisão do HTML inicial, assets, manifest, roteamento Cloudflare, exclusão de dados privados, descoberta e inspeção real no Google Search Console. Não representa nova homologação de GPU, processamento de mídia ou todos os itens de confiabilidade do produto.

O PNG antigo ainda continha **tts / faysk**, embora o HTML já dissesse STT. Substituído no compartilhamento por `social-card-stt-v2.png`, inspecionado visualmente, com URL nova para invalidar caches. O caminho antigo redireciona para o novo. Sem alteração de credenciais, banco ou reinício dos executores.

## Achados, ações e evidências

| Área | Resultado |
| --- | --- |
| Branding visual | Nova imagem STT em cinza/azul; PNG público idêntico ao local por SHA-256, HTTP 200, image/png. |
| Open Graph / Twitter | Imagem absoluta, dimensões reais 1731×909, tipo, alt, site name e campos Twitter explícitos. |
| Canonical | Home aponta para `https://stt.faysk.dev/`; `/index.html` agora retorna 301 para `/`, preservando query. |
| Sitemap | XML válido, uma URL pública canônica. Enviado na propriedade existente faysk.dev; Google mostrou **Processado**, uma página encontrada. |
| Indexação | Antes: **Google não reconhece o URL**. Teste publicado após correção: **O URL está disponível para o Google / É possível indexar a página**. Pedido confirmado: **Indexação solicitada**, fila prioritária. Isso não comprova inclusão final no índice. |
| Robots / privacidade | Home index,follow; API Disallow e resposta noindex/nofollow/noarchive. Admin mantém meta e header noindex; retirado Disallow do admin para que o robô possa ler a exclusão. Robots não é controle de acesso. |
| HTTP / erros | Home 200; sitemap e robots 200; admin 200 com noindex; API health 200 com noindex; caminho inexistente 404. |
| Robôs sociais | Requisições com User-Agent Googlebot, facebookexternalhit e Twitterbot retornaram home 200 com a imagem nova. O teste real do Google confirmou acesso; User-Agent sozinho não comprova identidade de crawler. |
| HTML / conteúdo | Título, descrição e conteúdo inicial disponíveis sem execução de JavaScript. WebApplication JSON-LD coerente com nome e URL; sem avaliações ou promessas inventadas. Nenhum rich result foi comprovado. |
| Idiomas | Dez idiomas na interface, mas uma URL com HTML inicial inglês. Tradução no navegador não significa dez páginas indexáveis; não foram fabricados hreflang ou URLs sem conteúdo equivalente. |
| Migração | `tts.faysk.dev` sem resolução DNS nesta revisão, coerente com retirada anterior. Links antigos não transferem sinais por 301. Não recriar infraestrutura retirada sem decisão específica. |
| Proteção Cloudflare | Python-urllib padrão recebeu 403; curl e os UAs acima receberam 200. Registrar como comportamento da camada de proteção, sem desativá-la; revisar caso Search Console reporte bloqueios futuros. |

## Publicação e verificação

Worker final: `dc49851b-c9e0-42f7-b2fa-95055dc95b0f`, domínio `stt.faysk.dev`. Publicação do frontend/Worker usando o guard existente; sem publicação do backend. O deploy também sincronizou `admin.html` e `admin-enhancements.js` já existentes na baseline local, que diferiam dos assets remotos; não houve edição desses arquivos nesta tarefa.

A primeira configuração de `/index.html` em `_redirects` interagiu com o fetch interno do ASSETS e causou um ciclo na home durante a publicação. Detectado no smoke imediato e corrigido movendo essa regra para o Worker, com `run_worker_first` específico. Estado final confirmado: home 200, index 301. Teste de regressão proíbe recolocar essa regra em `_redirects`.

Checks: `npm run check` (inclui novo `tests/seo.test.cjs`), testes Edge e i18n; dry-run Wrangler antes da primeira publicação; smoke HTTP e comparação criptográfica da imagem após publicação. Evidência visual do Search Console armazenada apenas localmente em logs, sem publicar dados da conta.

## Pendências reais

1. Aguardar processamento da fila do Google e verificar URL canônica escolhida, cobertura e impressões no Search Console. Não repetir solicitações: elas não elevam prioridade.
2. Atualizações de previews antigos dependem do cache de cada plataforma; novos fetches recebem a imagem nova. Não foi comprovado purge em WhatsApp, Facebook ou X.
3. Para aquisição multilíngue, planejar URLs estáveis com HTML traduzido, canonical próprio, hreflang recíproco e sitemap correspondente. A interface atual continua multilíngue sem prometer SEO em dez idiomas.
4. Bing Webmaster Tools não foi configurado/inspecionado nesta tarefa; sitemap público disponível para descoberta, sem alegar submissão ao Bing.
5. Acompanhar backlog de produto já existente: #20 entrega/outbox, #22 orçamentos de mídia, #23 retenção de telemetria, #35 supervisão Windows, #36 recibos, #37 E2E físico, #38 limites JSON, #69 resultado 422 e #72 estabilidade. Esta revisão de SEO não encerra essas issues.

## Referências

- [Google: sitemap é sinal de descoberta, não garantia de indexação](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap).
- [Google: canonical e consolidação de URLs](https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls).
- [Google: conteúdo JavaScript e noindex](https://developers.google.com/search/docs/crawling-indexing/javascript/javascript-seo-basics).
- [Cloudflare: redirects de assets](https://developers.cloudflare.com/workers/static-assets/redirects/).
- [Cloudflare: run_worker_first](https://developers.cloudflare.com/workers/static-assets/binding/).
