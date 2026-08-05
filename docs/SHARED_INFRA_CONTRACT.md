# Contrato de infraestrutura compartilhada

## Fonte de dados

Os Parquets históricos compartilhados são tratados como dados brutos imutáveis.

Montagem esperada no container:

```text
/data/canonical:ro
/data/manifests:ro
/data/holdout:ro
```

Cada laboratório mantém suas próprias áreas graváveis para DuckDB, resultados, logs, checkpoints e artefatos.

## Isolamento

- O laboratório de indicadores não escreve em `/srv/labs/datasets`.
- O laboratório de indicadores não escreve em diretórios do outro laboratório.
- Containers, nomes de Compose, redes e volumes graváveis devem ser próprios.
- Nenhuma credencial deve ser colocada nos Parquets, no Git ou no painel.

## Supabase

O schema `lab_automatizado` pertence ao outro laboratório e não será reutilizado por este projeto.

Qualquer área deste laboratório deverá ser criada somente após revisão do contrato compartilhado e aprovação explícita da migração.

## Acesso remoto

O acesso da aplicação deve ser separado do acesso de manutenção. O worker não deve receber uma chave de administrador da VPS. Diagnóstico via chat deve usar credencial dedicada e permissões mínimas.

