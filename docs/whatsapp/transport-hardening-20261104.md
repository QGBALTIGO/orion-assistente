# Transporte WhatsApp — correção global, 04/11/2026

Base investigada: `2d20427f23d3e380880de734d11998b56de7305b`.
Escopo: sessão central e todos os canais Baileys, sem filtro de nicho.

## Evidência e limites de diagnóstico

Os logs de produção registraram `bad_mac` em 04/11 às 20:38 UTC, coincidente com o print das 16:38 no fuso do usuário. Trata-se de evidência de falha de descriptografia, não prova de sessão duplicada nem prova de qual resposta visível originou o aviso. A ponte tinha uma réplica com volume persistente. A adoção do canal central também emitia aviso recorrente; agora registra apenas categoria e status HTTP, sem segredo ou payload, para distinguir configuração de indisponibilidade.

## Mudanças

- Credenciais e chaves centrais: AES-256-GCM, chave derivada por HKDF e domínio/pasta, escrita atômica e fsync. Migração preserva identidade e exige backup cifrado verificado; arquivo inválido, chave errada ou identidade ausente jamais gera conta nova silenciosamente.
- Journal cifrado de lotes de chaves, também nos canais gerenciados. Atualização de credenciais é persistida no mesmo turno de evento, antes de close/reconnect, sem aguardar HTTP do broker. O fence/lease monotônico e a geração continuam obrigatórios.
- Todos os envios centrais usam cache cifrado antes do primeiro relay e lista atualizada de dispositivos; flags antigas não rebaixam essa garantia. Permissões, opt-in e limites de capacidade não são ampliados.
- Recuperação de mensagem vinculada ao ID, sessão, destinatário e direção. PN/LID exige prova contemporânea do provedor. Participante de retry não pode redirecionar conteúdo a terceiro. Cache legado sem destinatário não autoriza replay pela rede.
- Contadores de retry e caches de dispositivos com TTL e limite rígido de memória; cache de retry dos canais persiste no contexto durante reconexões.
- Listas e carrosséis ganham os metadados de stanza nativa; carrossel não usa wrapper viewOnce. Usa `additionalNodes` público do Baileys, sem alterar Signal. Referência de interoperabilidade: release oficial Evolution API 2.4.0-rc1, https://github.com/EvolutionAPI/evolution-api/releases/tag/2.4.0-rc1 . A aceitação pelo cliente real ainda requer teste de entrega/renderização.
- URLs públicas, webhook e proxy externo exigem HTTPS e certificado válido; mantém DNS/IP público fixado, bloqueio de endereços internos/metadata, redirects proibidos e limites de tempo/tamanho. URL HTTP antiga é recusada, não corrigida silenciosamente. Comunicação fixa na rede privada Railway mantém o contrato existente.
- Dependências npm travadas em package-lock, build com npm ci, sem atualização cega para Baileys pré-release. Diagnósticos não imprimem conteúdo, números, tokens ou chaves.

## Validação

A suíte completa é comparada com a base publicada, mantendo falhas antigas visíveis. O fixture de áudio tinha dependências ausentes: foi atualizado com o helper real, restabelecendo 30 testes de áudio/protocolo/agendamento sem mudar o comportamento do produto para satisfazer o teste. Os testes legados de panel-ui não são declarados aprovados.

Os testes novos incluem Signal real com pares sintéticos: texto/URL, lista, protocolo de áudio e documento, resposta bidirecional e reinício dos dois estados. Mensagem adulterada deve falhar. Isso valida criptografia/persistência, **não** transporte Meta nem renderização no iPhone. Outras verificações cobrem migração e backup, falha de disco e journal, identidade ausente, cache por destinatário, adulteração, reconexão, HTTP recusado e metadados nativos.

## Operação e recuperação

Nenhuma sessão é apagada ou repareada e nenhuma conversa real recebe teste automático nesta entrega. Mensagens com resultado ambíguo não são reenviadas cegamente. `relay` aceito não equivale a entregue/lido; apenas recibos observados avançam o estado.

Após migrar a sessão central, **não reverter isoladamente ao loader antigo de JSON puro**, nem trocar QR_BRIDGE_TOKEN ou mover a pasta de auth sem migração. Uma recuperação exige parar o writer, validar o backup AES-GCM/checksums/identidade e restaurar de maneira controlada. A opção preferida é corrigir adiante mantendo o loader compatível. Backup anterior à migração fica privado no volume, sem exportar segredos para CI.

Não há promessa de zero falhas, de recuperar todos os placeholders antigos ou de controlar alterações/indisponibilidade do WhatsApp. A assinatura exigida pelo receptor de webhook externo não é inventada: TLS não substitui um contrato de autenticação do terceiro. Revalidação de produção e entrega real deve ser registrada na PR antes de declarar o sintoma encerrado.
