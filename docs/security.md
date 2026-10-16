# Segurança da FATIA 0

- **Não armazenar credenciais reais** no GitHub, testes, logs ou no chat.
  A variável `APP_DATABASE_URL` aponta exclusivamente para `app_runtime`;
  `MIGRATION_DATABASE_URL` exclusivamente para `app_migrator`.
- Ambos sem SUPERUSER, BYPASSRLS, CREATE ROLE ou posse cruzada de tabelas;
  RLS FORCE e políticas com `WITH CHECK` restringem DML.
- A ausência de setting usa `NULLIF(current_setting(..., true), '')::uuid`,
  resultando em falso em expressões de igualdade (fail-closed).
- O setting só pode ser aplicado **após identificar/autenticar o tenant
  no servidor**. Não há endpoint de tenant nem qualquer entrada HTTP de UUID
  nesta fatia. Não confundir UUID com credencial/autorização.
- `SET LOCAL` não é `SET` global: a transação termina antes de devolver
  conexão ao pool.
- HTTPS público será fornecido pela Railway (ou Caddy configurado com domínio
  em outra hospedagem). O proxy opcional do Compose é HTTP e local.
- `/health` é apenas liveness, não revela versão de banco, usuário ou segredos.
- Nunca utilizar URL superuser/senha pessoal como `APP_DATABASE_URL`.
- Sementes e dados de teste são sintéticos; sem clientes reais.

**Limitações deliberadas:** não há autenticação/RBAC de usuários, APIs de
dados, verificação de assinatura Meta, backup externo, retenção de dados,
limites de taxa ou auditoria de operações; serão exigidos antes do piloto.
RLS protege dados na consulta SQL, mas não torna segura uma aplicação
que aceite SQL arbitrário ou contexto informado pelo cliente.
