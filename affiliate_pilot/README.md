# Orion Afiliados — piloto Shopee Brasil

O módulo roda com FastAPI e reaproveita a folha de estilos do painel Orion, as fontes Figtree/Outfit e a marca do próprio projeto. A preparação de ofertas usa a experiência do GeekHunter: seleção de produtos, geração de link na conta de afiliado, texto com dados confirmados e imagem composta. O processo e a base deste piloto são independentes do painel de produção, do banco de clientes, do bridge e do GeekHunter. As sete áreas estão na navegação lateral.

## Rodar e testar

Na raiz do repositório, instale `affiliate_pilot/requirements.txt` em uma virtualenv. Configure `AFFILIATE_DATABASE` com um caminho privado próprio, `AFFILIATE_MASTER_KEY` com uma chave Fernet, `AFFILIATE_BOOTSTRAP_PASSWORD` (mínimo 24 caracteres), `AFFILIATE_BOOTSTRAP_USER` e `AFFILIATE_PUBLIC_ORIGIN`. Não reutilize senhas ou arquivos de produção. Rode:

```sh
python -m uvicorn affiliate_pilot.main:app --host 127.0.0.1 --port 8067
python -m pytest -q affiliate_pilot/tests
python -m affiliate_pilot.tests.browser_smoke
```

O teste de navegador usa Chrome com Playwright, servidor e banco temporários. Em **Ofertas → Ver exemplo**, o usuário pode abrir um rascunho explicitamente fictício e experimentar a prévia. Ele não pode entrar na fila. Preços, estoque, desconto anterior e cupom desconhecidos são omitidos ou sinalizados; faixa de preço é exibida como “A partir de”. A Shopee não entrega confirmação de estoque por variação no contrato usado aqui.

## Destinos

| Integração | Implementação no piloto | Limite de validação |
|---|---|---|
| Shopee Brasil | Busca, confirmação por ID, link curto gerado na conta do afiliado, catálogo e prévia. Credenciais criptografadas por empresa. | A API pode rejeitar metadados opcionais; sem imagem confirmada, o card mostra o estado ausente. |
| Telegram / grupo e canal | Busca de um bot dedicado, confirmação da identidade por link de início, verificação do cargo do publicador e do operador, fila persistente e envio pela Bot API. | Entrega de texto, imagem e recibos exercitada com transportes simulados. Destino real depende do bot de teste e da autorização. |
| WhatsApp / grupo | Vínculo por QR na sessão privativa do piloto, descoberta de grupos administrados, nova consulta de permissão ao cadastrar e publicação assistida. | Pareamento físico e permissões exigem teste com conta dedicada. O conector só retorna os grupos administrados, sem copiar a lista de participantes. |
| WhatsApp / canal | Verificação do cargo da conta vinculada pelo link do canal cadastrado. Publicação assistida com exportação de texto, link e card. | Requer resposta do WhatsApp com cargo de proprietário ou administrador. Publicação automática e recibos ainda dependem de homologação. |
| Amazon e Mercado Livre | Aparecem em Plataformas como planejadas. | Nenhuma automação ou atribuição de comissão habilitada. |

O backend tem uma barreira adicional: `AFFILIATE_ENABLE_TEST_SEND=0` no ambiente publicado. Ao autorizar um teste real, mantenha o escopo fechado, confirme por mensagem o destino e o bot dedicados, marque o destino de teste e só então considere liberar a barreira de envio. A aprovação é registrada. Resultados desconhecidos ficam no histórico sem reenvio automático da ocorrência.

O modelo **PromoTech com IA** usa os marcadores nativos do WhatsApp para negrito, itálico e riscado. No Telegram, a mesma legenda passa pela conversão HTML com escape. O rascunho tem um campo editável para a frase e um botão que usa a Groq configurada em **Plataformas**. O modelo recebe o nome público do produto. Preços, links, avaliação, volume de vendas e descontos vêm do catálogo. O preço anterior aparece riscado após uma redução de preço observada pela API no mesmo produto com preço único. O histórico de comparação dura até sete dias e guarda até dois mil produtos por conta. O seletor prioriza ofertas com desconto confirmado, preço anterior e volume de vendas. Linhas sem dados confirmados são omitidas.

Em **Automações**, selecione as 29 categorias da vitrine da Shopee individualmente ou usando **Selecionar todos**. A rotina alterna uma categoria por ocorrência. O filtro cruza os IDs da API de Afiliados com a categoria selecionada. Os IDs públicos da vitrine servem de referência e o item precisa passar pelo filtro do catálogo.

A regra autorizada do **PromoTech** envia uma oferta a cada 30 minutos, com um único canal e conta vinculados à autorização do piloto. A pausa e os filtros podem ser alterados em **Automações**. O processo valida a identidade do destino e registra o recibo do WhatsApp em **Fila e histórico**. Uma tentativa com resultado desconhecido permanece no histórico para conferência. Os demais destinos WhatsApp podem receber rascunhos para revisão.

## Infraestrutura e desativação

O lançador em `deploy/vps.py` registra o commit empacotado, testa o serviço, confirma o acesso restrito e publica em host e porta próprios. Dois serviços systemd com usuários dinâmicos, limites de CPU/RAM e estados em diretórios separados mantêm a API e a sessão do WhatsApp isoladas. O bridge usa o Baileys fixado no Orion e o mesmo armazenamento de sessão cifrado. A API expõe o QR somente à conta inicial do piloto e o bridge aceita chamadas autenticadas em loopback. No banco, intenção de envio e conteúdo ficam na mesma transação. A fila distingue preparação segura de uma tentativa que pode já ter chegado ao destino.

Para desativar, pare e desabilite os serviços `orion-afiliados-piloto.service` e `orion-afiliados-piloto-whatsapp.service`. Remova o link `/etc/nginx/sites-enabled/orion-afiliados-piloto`, valide com `nginx -t` e recarregue o Nginx. Preserve os diretórios de estado e as chaves de criptografia correspondentes para recuperação.

A importação usa a identidade da loja e do produto no link completo. Links curtos já gerados no piloto podem ser reconhecidos nos rascunhos recentes da conta. Links curtos de fora dependem de a Shopee expor a identidade no redirecionamento. O painel pede o link completo quando ela devolve somente sua página intermediária.

## Referências consultadas em 24/11/2026

| Referência | Licença ou evidência | Decisão |
|---|---|---|
| [shopee-afflib](https://github.com/Anthonyxda/shopee-afflib) | MIT; atividade GitHub em outubro de 2025. | Referência de assinatura e fluxos; usei o cliente próprio e o contrato medido na API brasileira. |
| [shopee-br-affiliate-mcp](https://github.com/jeffmonteiroo/shopee-br-affiliate-mcp) | MIT; atividade em outubro de 2026. | Referência de contratos, feeds e distinção entre teste sintético e real. Nenhum processo MCP em produção. |
| [BlueBot](https://github.com/SaulloGabryel/BlueBot) | README indica MIT; API do GitHub não identificou licença separada; atividade em outubro de 2025. | Referência de fluxo. Não copiado; depende de navegador para Mercado Livre e sessão web para WhatsApp. |
| [aiogram](https://github.com/aiogram/aiogram) | MIT; ativo em novembro de 2026. | Não incorporado. O cliente HTTP é suficiente para os métodos usados. |
| [Baileys](https://github.com/WhiskeySockets/Baileys) | MIT; ativo em novembro de 2026. O checkout do Orion fixa 6.7.24. | Referência do bridge existente; nenhuma sessão de produção é compartilhada. |
| [BullMQ](https://github.com/taskforcesh/bullmq) | MIT para o núcleo; ativo em novembro de 2026. | Não incorporado. A fila do piloto fica no banco isolado e dispensa Redis adicional. |
| [Satori](https://github.com/vercel/satori), [Sharp](https://github.com/lovell/sharp) | MPL-2.0 e Apache-2.0, respectivamente; atividade em novembro de 2026. | Avaliados para imagem. Pillow evita outro runtime e aceita composição de foto, texto e preço no mesmo cartão. |

O [AfiliadoFlow](https://www.afiliadoflow.com/) anuncia onboarding por marketplace e regras por destino. O [Achadinho PRO](https://achadinhopro.com.br/) divulga cupons, modelos e marca na imagem. O [Pro Afiliados](https://proafiliados.com/) divulga fila, horários e histórico por grupo. Foram consultadas páginas públicas, sem testar produtos pagos.

Documentação oficial consultada: [Shopee Open API Brasil](https://affiliate.shopee.com.br/open_api), [Telegram Bot API](https://core.telegram.org/bots/api), [Meta Groups API](https://developers.facebook.com/documentation/business-messaging/whatsapp/groups), [Amazon Creators API](https://associados.amazon.com.br/creatorsapi/docs/en-us/onboarding/register-for-creators-api) e [regras do Mercado Livre para destinos](https://www.mercadolivre.com.br/l/afiliados-onde-compartilhar-links). O portal Shopee exige JavaScript e conta para a documentação privada; o comportamento deste piloto foi validado diretamente no endpoint brasileiro.

Os identificadores de marca das integrações vêm das páginas oficiais. Arquivos locais: [Shopee](https://affiliate.shopee.com.br/favicon-br.png), [Amazon](https://m.media-amazon.com/images/G/01/creators/favicon.ico), [Mercado Livre](https://www.mercadolivre.com.br/favicon.ico), [Telegram](https://telegram.org/img/favicon-32x32.png), [WhatsApp](https://static.whatsapp.net/rsrc.php/y1/r/FJbTMJqMap7.svg). As marcas identificam os serviços e não representam endosso.
