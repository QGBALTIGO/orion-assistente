# Encurtador de links — Orion Afiliados

Ferramenta independente de Ofertas. Aceita endereços públicos HTTP(S) permitidos
com alias personalizado ou automático em spoo.me. Histórico, busca, paginação,
cópia, cliques (totais no histórico e últimos sete dias por link), pausa, ativação
e exclusão utilizam a conta individual Spoo.me do usuário. Nenhum redirecionamento
é atendido pelo domínio Orion. Nenhum envio automático ou encurtamento de ofertas
é habilitado.

## Contas, custo e privacidade
O usuário fornece sua própria chave de API por sessão da tela. Não armazenamos
chaves, URLs ou estatísticas em banco/localStorage. O histórico é mantido pelo
provedor, inclusive links criados fora da Orion; não compartilhe chaves entre contas.
Desconectar ou sair da tela limpa a chave em memória. A chave trafega somente no
corpo JSON sob TLS e no cabeçalho Authorization da conexão fixa com o provedor.
Não registrar corpos de solicitação ou cabeçalhos de autenticação em observabilidade.
Os termos do Spoo.me impedem revenda/redistribuição de acesso sem autorização.
Confirmar por escrito a integração SaaS e o uso de chaves próprias antes de habilitar
ORION_AFFILIATE_SHORTENER_ENABLED=1. A flag vem desativada. Não vender API, criar
contas automaticamente, contornar quotas ou prometer disponibilidade gratuita.

## Segurança e limites
Proprietário autenticado, empresa ativa, nicho Afiliados e validação existente
de associação. HTTPS para a API, destino público sintaticamente válido, sem
credenciais, IPs literais, cadeias conhecidas, portas alternativas ou domínio
Mercado Livre. Nunca buscar/redirecionar o destino no servidor. A classificação
de abuso e malware também depende do provedor; para liberar comercialmente, obter
revisão de política de abuso e monitoramento de reclamações. Limites conservadores
Redis compartilhados (12 requisições/min por empresa; 50/min total), fail closed
na ausência de Redis, sem rotação de chaves/IPs nem fallback para contornar políticas.
Resposta de erro sanitizada, sem credenciais; não repetir automaticamente criações
com resultado incerto. Provedor é fonte de verdade para disponibilidade e cliques.

## Empacotamento
Base de referência consultada no Railway: imagem API publicada
sha256:8ddab344c7b5d564288d09edfc218374fcf2a6c31de6c7fd00bac149b32fc791.
O repositório tem branches incrementais que não refletem todos os overlays da imagem.
Por isso, deploy/Dockerfile.affiliate-shortener copia somente os novos arquivos e
aplica anchors de integração no conteúdo real da imagem, recusando divergências.
Não substituir integralmente o workspace de Afiliados ou o suite JS da produção.
Não há migrations ou mudanças no worker/QR bridge. Não publicar automaticamente.
Revalidar o digest ativo imediatamente antes de qualquer publicação.

## Validação
pytest tests/unit/test_affiliate_shortener.py
node --test ui-tests/affiliate-shortener.test.mjs

Testes unitários com httpx.MockTransport e autorização simulada; não criam links
públicos. Validar UI em Chromium/WebKit, tema claro/escuro, desktop/tablet/mobile,
teclado e ausência de overflow. Antes da liberação: confirmar autorização do
provedor, chave de conta de teste com escopos mínimos, teste real controlado com
https://example.com, alias de teste disponível, conferência do redirecionamento
e da remoção, inspeção de logs sem segredos e limites/isolamento reais em Redis.
Registrar evidências e, somente depois, habilitar a flag. A API pode mudar ou
recusar destinos; nenhuma comissão ou entrega é garantida.
