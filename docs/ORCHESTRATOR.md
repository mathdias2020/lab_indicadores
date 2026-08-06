# Orquestrador host-side

O primeiro orquestrador é um processo host-side, não um container. Ele é o
único componente autorizado a invocar o Docker Compose local. O worker continua
sem Supabase, sem `docker.sock`, sem rede e com Parquets somente leitura.

## Contrato

1. Conecta ao Postgres do projeto por `LAB_INDICADORES_DATABASE_URL`, somente
   em ambiente server-side.
2. Faz claim atômico em `lab_indicadores.claim_next_command`.
3. Aceita, nesta versão, apenas `indicator-lab-smoke-v1`.
4. Executa uma lista fixa equivalente a:

   ```text
   sudo -n docker compose -p lab_indicadores run --rm indicator-worker smoke
   ```

5. Valida o relatório, `holdout_accessed=false` e o caminho esperado.
6. Persiste evento diretamente sob a política RLS do usuário dedicado, além do
   artefato com SHA-256, sucesso ou erro.

O payload do comando não vira shell livre. Novos tipos de job precisam de um
novo contrato versionado e de uma allowlist explícita.

## Instalação na VPS

A credencial server-side foi provisionada fora do Git, em
`/etc/lab-indicadores/orchestrator.env`, com modo `600`. Ela não deve ser
enviada no chat, versionada ou exposta ao painel.

```bash
cd /srv/labs/projects/lab-b
/usr/bin/python3 -m venv work/orchestrator-venv
work/orchestrator-venv/bin/pip install -r requirements-orchestrator.txt
```

Depois, o processo deve ser executado com `PYTHONPATH=/srv/labs/projects/lab-b/src`
e `LAB_INDICADORES_DATABASE_URL` injetada por um arquivo de ambiente protegido.

A unidade versionada em `ops/lab-indicadores-orchestrator.service` mantém um
único processo contínuo, como `labadmin`, com reinício em falha e escrita apenas
na árvore do laboratório.

Para um diagnóstico sem consumir a fila, use a opção `--once`; ela registra o
worker como online, verifica a conexão e retorna `orchestrator_once=idle` se não
houver comando pendente.
