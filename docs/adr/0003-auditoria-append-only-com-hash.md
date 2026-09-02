# Auditoria append-only com snapshots before/after e hash-encadeamento

A Res. CFMV 1321/2020 (Art. 2º VIII) exige registro clínico sem rasuras nem emendas, em ordem cronológica, com autenticidade e integridade garantidas — e o líder brasileiro vende exclusão de registro como recurso, o que transformamos em argumento de venda ("segurança jurídica"). Por isso `audit_entries` é **append-only imposta pelo banco** (trigger bloqueia UPDATE e DELETE, não apenas convenção de código), cada entrada carrega snapshots `before`/`after` da entidade (sem o antes, "sem rasuras" não significa nada) e um hash encadeado (`prev_hash` → `entry_hash`) que torna detectável qualquer remoção ou reescrita feita por fora da aplicação. Correção de registro clínico é **adendo versionado**, nunca edição destrutiva; e as entidades de domínio nunca são deletadas (`is_active`/`status`), para que `entity_id` da trilha jamais aponte para o nada.

## Consequences

- **Minimização é obrigatória, não opcional**: o que entra no payload é inapagável para sempre, então a trilha carrega ids e dados clínicos e **nunca dados de contato do tutor** (LGPD Art. 18 III — retificação/eliminação). Referenciar `patient_id`/`tutor_id`, jamais copiar telefone.
- A guarda legal de 5 anos (Art. 9º §3º) fundamenta negar pedidos de exclusão do prontuário, mas exige plano de expurgo **após** o prazo: particionamento por ano + exceção controlada ao trigger, documentado na semana 4.
- Toda escrita de domínio passa pelo `AuditService`, que precisa do estado anterior — serviços carregam a entidade antes de mutar.
- O hash encadeado serializa a escrita da trilha por clínica (a entrada N precisa do hash de N−1). Aceitável no volume de uma internação (centenas de entradas/dia); se virar gargalo, o encadeamento passa a ser por clínica com lock advisory.
