# Login administrativo por QR e passkey

Implementação da [issue #84](https://github.com/Faysk/stt/issues/84), entregue no [PR #86](https://github.com/Faysk/stt/pull/86). **Desativada por padrão em instalações novas; ativada em produção em 01/11/2026, com pareamento e duas entradas confirmadas pelo proprietário no S23+.** A autenticação pública das transcrições e a dos executores continuam independentes.

## Publicação observada em 01/11/2026

- Código da API: `e9dfe9b` (merge do PR #86); Worker publicado: `1180254d-2a10-4153-b41c-e81280d3cee4`.
- Flags efetivas: QR habilitado e recuperação legada habilitada. O proprietário criou a passkey no aparelho, sem transmitir PIN, biometria ou chave privada ao serviço.
- Duas aprovações reais pelo S23+ abriram o console no navegador desktop. Recarregar restaurou a sessão; logout removeu a sessão no servidor e bloqueou o painel; nova entrada exigiu nova aprovação.
- O link correspondente ao QR foi aberto via USB para este ensaio. A leitura óptica pela câmera não foi comprovada neste teste.
- Frontend/API públicos responderam 200; admin anônimo 401; origem direta sem segredo 403. Snapshot SQLite consistente antes da migração e outro pelo procedimento de pareamento. Fila e configuração anteriores restauradas; executor e Tunnel preservados.
- Somente a API foi reiniciada. O gate CIM do publicador padrão estava indisponível nesta sessão; os processos da API foram identificados individualmente e a liberação TCP foi conferida antes de iniciar a substituta. O encerramento do launcher retornou AccessDenied após o filho já ter parado; a API foi reiniciada e os checks local/público confirmaram recuperação.
- Nenhuma nova inferência de áudio foi executada neste ensaio de autenticação. A matriz completa de processamento continua no item #37; recuperação destrutiva de credenciais permanece validada em fixture/staging, sem revogar a passkey recém-cadastrada em produção.
- Capturas e eventos de prova permanecem privados, fora do Git. Não publicar credencial, token de pareamento, cookie, código de pedido ou transcrição em tickets.

## Experiência

O PC exibe apenas um QR com o logo no centro, renovado automaticamente a cada expiração. Não há título, código, cronômetro nem controles visíveis no fluxo normal. O celular abre a página do mesmo domínio e inicia a autenticação nativa; após digital/PIN, a assertion é enviada imediatamente e o PC recebe sua sessão. Não há checkbox nem confirmação adicional do aplicativo. Se o navegador cancelar/exigir gesto, um toque no logo tenta novamente. Prompts do autenticador/gerenciador de passkeys pertencem ao navegador e não podem ser suprimidos pelo STT.

O QR contém uma URL com ID público aleatório de 256 bits. O vínculo secreto do PC fica em cookie HttpOnly independente. O sistema mantém uma única credencial autorizada. Ela pode ser sincronizada pelo gerenciador de passkeys: **isso não comprova exclusividade física do S23+**. O QR customizado também não comprova proximidade Bluetooth. A conferência humana de código foi retirada por decisão explícita do operador em 01/11/2026; permanece o limite de relay/phishing de um QR compartilhado, sem alegar prova de proximidade. Assinatura, UV, origin, RP ID, expiração e vínculo de browser continuam obrigatórios.

O QR usa correção de erro H e logo com área restrita, preservando a margem de leitura. A captura renderizada com logo foi decodificada com ZXing no teste local. Textos de estado permanecem disponíveis para leitores de tela. Falhas podem mostrar apenas o logo como botão de nova tentativa.

## Configuração e ativação em staging

```dotenv
STT_ADMIN_QR_ENABLED=0
STT_ADMIN_LEGACY_ENABLED=1
```

- QR desligado: fluxo antigo permanece disponível, sem cadastro público.
- QR ligado: a página cria automaticamente um pedido e tenta restaurar uma sessão válida.
- Legado desligado: `x-admin-key` deixa de autorizar rotas administrativas. Só desligar após pareamento e validação de recuperação; a CLI local continua disponível.
- Recuperação legada não aparece na tela QR. Quando habilitada, abrir `/admin.html?recovery=1` para acessar o campo de chave.
- RP ID fixo: `stt.faysk.dev`; origin exata: `https://stt.faysk.dev`. Não há variável que libere origins arbitrárias.

Aplicar primeiro dependências e backend em ambiente isolado, com snapshot SQLite, depois assets. O deploy Windows e o Worker são etapas diferentes; seguir `OPERATOR_PUBLISH.md`, preservar Tunnel e trabalhos em execução. A implementação não autoriza restart ou ativação automática. Teste local com RP substituído em memória serve apenas para QA da interface, não para cadastrar passkeys de produção.

## Primeiro pareamento e recuperação local

No ambiente do serviço, com acesso local confiável e as dependências bloqueadas por `uv.lock`:

```powershell
uv run python -m scripts.admin_pair
```

A CLI faz snapshot consistente via SQLite backup antes da migração e gera uma URL/QR privado de pareamento, válido por cinco minutos. Abrir no celular e cadastrar a passkey. O token está no fragmento da URL, removido imediatamente da página e enviado somente no corpo do pedido de opções. Não compartilhar, capturar em logs ou guardar esse QR. A aprovação de cadastro pelo autenticador deve ser feita pelo proprietário.

Sem credencial já autorizada, ninguém pode abrir cadastro público. Após o primeiro cadastro, nova inscrição online é recusada, inclusive para uma sessão administrativa comum. Administração de credenciais nesta versão é **somente pela CLI local**; não há inclusão silenciosa pelo painel.

```powershell
uv run python -m scripts.admin_pair --recover
uv run python -m scripts.admin_pair --revoke-all
```

`--recover` revoga credencial, sessões, pedidos e desafios e abre um novo pareamento. `--revoke-all` revoga tudo sem novo pareamento. São ações destrutivas de autenticação para execução consciente pelo operador; não apagam transcrições, fila ou outbox. A chave legada, quando habilitada, é outro meio de recuperação e não é revogada por esses comandos.

Snapshots em `db/auth-backups` têm o mesmo grau de sensibilidade da base. Preservar ACL restrita, não versionar nem servir por HTTP. Após restaurar uma base, executar recuperação/revogação antes de reativar acesso externo, para não ressuscitar sessões antigas. A migração é aditiva e idempotente, versão 1 em `admin_auth_migrations`; não exige apagar tabelas de jobs para rollback. Desabilitar a feature e voltar código/dependências/frontend compatíveis mantém o acesso legado apenas se sua flag estiver habilitada.

## Contrato e controles

| Rotas sob `/api/admin/auth` | Função |
| --- | --- |
| `GET config` | Flags de capacidade; sem dados de credenciais |
| `POST qr/start`, `qr/status`, `qr/cancel`, `qr/consume` | Pedido vinculado ao cookie do PC, polling, cancelamento e consumo atômico |
| `POST approve/context` | Contexto reduzido e código; nunca aprova |
| `POST ceremony/options`, `ceremony/finish` | Cadastro autorizado por token local ou aprovação/rejeição assinada |
| `GET session`, `POST logout` | Recuperação de CSRF e revogação de sessão |

- Criptografia delegada a `webauthn` (lockfile); valida origin, RP ID, challenge, assinatura, presença e UV. A aplicação exige credencial autorizada e userHandle do único admin, quando enviado. Contadores são verificados pela biblioteca, incluindo a regra de contador zero para passkeys sincronizáveis.
- Desafios são vinculados ao pedido, decisão e cookie da cerimônia; expiram em no máximo 120s e no prazo do QR/pareamento. Tentativa inválida consome o desafio. Trocar de QR invalida o anterior.
- `__Host-stt_admin`: Secure, HttpOnly, SameSite=Strict, Path=/, sem Domain; token aleatório de 384 bits, apenas hash persistido. Inatividade de 30 min, duração absoluta de 8h. Logout e recuperação revogam no servidor. Nenhum token administrativo no QR ou Web Storage.
- Operações de cookie exigem origin exata e custom header; mutações administrativas e pedidos vinculados exigem também CSRF sincronizado derivado do token secreto. Sem CORS permissivo. Opções/finish têm payload limitado a 32 KiB, inclusive streaming.
- Limites por rede e globais persistidos no SQLite; endereço de borda só é aceito após guard de EDGE_KEY. Há limite de cardinalidade de pedidos/desafios. Polling de 2,5s com backoff.
- Logs de autenticação usam apenas vocabulário de eventos, expiram em 30 dias. Desafios e pareamentos expiram; pedidos encerrados são purgados após uma janela curta de diagnóstico. Limpeza periódica também executa sem chamadas de login.
- Anti-cache/noindex, no-referrer, CSP e HSTS. QR gerado por biblioteca local `qrcode`, sem serviço externo. Aprovação é sempre explícita; GET nunca cria sessão.

## Evidência e homologação pendente

Testes Python geram um autenticador ES256 de fixture e executam cadastro e assertions reais contra o verificador WebAuthn, sem mocks da assinatura. Cobrem UV/origin/RP/challenge/userHandle/assinatura inválidos, chave desconhecida, replay, contador, vínculo de navegador, concorrência, expiração, cancelamento, rejeição, recuperação, CSRF, quotas, origem protegida e preservação das tabelas existentes. Testes Edge verificam múltiplos Set-Cookie. QA visual local cobre QR, cancelar e regenerar em desktop e viewport 390px.

Em 01/11/2026, **31 testes direcionados passaram**, incluindo a execução real da CLI em subprocesso contra uma base temporária: snapshot, primeiro cadastro, bloqueio de novo cadastro, recuperação, revogação e preservação dos dados da aplicação. A suíte ampliada local anterior teve 105 passed, 3 skipped, 1 deselected e uma falha em teste antigo de symlink por WinError 1314; outra verificação de symlink foi excluída somente desse comando local pela mesma limitação. Os testes não foram desativados no código/CI. Node/Edge/i18n/deploy guard, dry-run Wrangler, Bandit e scanners de dependências também foram executados. Acompanhar o resultado do SHA final no [PR #86](https://github.com/Faysk/stt/pull/86).

Critérios de publicação e acompanhamento:

1. Dependências, migração, snapshot e CLI de recuperação testados em base isolada; migração e snapshots aplicados em produção. Não executar recuperação destrutiva só para teste na credencial ativa.
2. Pareamento, duas aprovações, logout e restauração após recarregar comprovados no S23+/Chrome e desktop. Rejeição, timeout, replay e credencial desconhecida cobertos por testes isolados; ampliar a matriz física de câmera/outro aparelho conforme #84.
3. Backend/Worker publicados e fluxo HTTPS validado; origem direta permanece bloqueada.
4. Executor principal preservado, saúde pública conferida; novo E2E de mídia/exports continua no escopo #37.

## Referências

[Registro WebAuthn](https://duo-labs.github.io/py_webauthn/registration.html), [autenticação WebAuthn](https://duo-labs.github.io/py_webauthn/authentication.html) e [OWASP Passkey Security](https://cheatsheetseries.owasp.org/cheatsheets/Passkey_Security_Cheat_Sheet.html).
