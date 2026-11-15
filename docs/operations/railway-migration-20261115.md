# Conferência da migração Railway — 15/11/2026

Destino: projeto `Orion`, conta Gabrieli, ambiente `production`.
Origem: projeto `strong-creation`, conta Orion, ambiente `production`.

## Evidências observadas

- API, worker, QR bridge, PostgreSQL e Redis estão online no destino.
- Os cinco serviços da origem estão offline; não há implantação pendente no destino.
- `/health`, `/health/whatsapp`, `/painel` e `/s/barbearia-peixoto` responderam HTTP 200.
- Os registros da transferência confirmam 79 objetos de mídia e 48 backups
  com SHA-256 verificado. O snapshot final de sessão registra 3.417 arquivos
  e SHA-256 verificado.
- A conexão principal recebeu `401`, `reason=logged_out`,
  `action=reauthenticate`, em `2026-11-15T00:27:38Z`. As amostras posteriores
  permanecem em `needs_attention`. Outro canal apresenta `connected`.
- O DNS público aponta para a entrada Hostinger, conforme a arquitetura em
  `hostinger-ingress.md`. O cadastro antigo do domínio na Railway, por si só,
  não demonstra dependência de processamento da conta antiga.

`/health/whatsapp` confirma que a ponte responde; não confirma que todas as
sessões estejam conectadas ou que uma mensagem tenha sido entregue.

## Verificador preparado

`scripts/verify_railway_migration.py` inspeciona o banco em transação
`READ ONLY`, com limites de conexão, consulta e espera por lock. Lista tabelas,
contagens, papéis e metadados dos canais. Não altera schema, credenciais ou
registros. Usa apenas GET nos endpoints privados de status do bridge e
listagem/leitura no S3. As leituras dos objetos comparam tamanho e SHA-256.
Os logs do bridge têm uma lista explícita de campos permitidos; não incluem
número, perfil, QR, código de vínculo, mensagens ou credenciais.

Esse diagnóstico não é uma comparação integral do banco com o snapshot da
origem. As contagens e os metadados precisam ser confrontados com o inventário
final da migração antes de certificar preservação integral dos registros.

Validação local:

```sh
python -m unittest discover -s tests/unit -p test_verify_railway_migration.py -v
```

Oito testes aprovados: detecção de objeto ausente, corrupção com tamanho
igual, exclusão restrita ao prefixo configurado, digest/fechamento de stream,
remoção de dados sensíveis e falhas sem exposição da mensagem da exceção.

## Pendências de execução

O verificador não foi executado em produção. A criação de um serviço isolado
foi recusada pelo limite de recursos do plano gratuito. A alternativa de
adicioná-lo temporariamente ao pré-deploy do worker foi recusada pela revisão
automática de aprovação; a configuração original do worker foi relida e
permanece intacta. Nenhuma republicação foi disparada por esta conferência.

Após aprovação específica, a execução proposta usa a mesma imagem atual do
worker e preserva todas as verificações de pré-deploy existentes. Ao terminar,
remove-se somente a configuração temporária do diagnóstico. Qualquer falha
de acesso ou integridade interrompe o diagnóstico; ela não autoriza ampliar
permissões ou restaurar dados automaticamente.

Separadamente, a sessão revogada exige a confirmação de um novo vínculo pelo
responsável no WhatsApp. Não apagar a sessão preservada nem recriar a identidade
automaticamente para tentar contornar a revogação do provedor.
