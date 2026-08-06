# Registro tecnico da VPS

Status: `REGISTERED_READY_FOR_PREFLIGHT` (nenhum container iniciado)

## 1. Identidade

- `project_id`: `lab-indicadores`
- nome tecnico: `Laboratorio de Indicadores WDO WIN`
- funcao: pesquisa de indicadores de fluxo, preco e fluxo + preco

## 2. Repositorio

- Git: `https://github.com/mathdias2020/lab_indicadores`
- branch de execucao: `main`
- branch/commit local de referencia: `main` / `4ad9abb` antes deste registro
- observacao: o repositorio local tem o contrato; publicacao no GitHub depende da permissao de push da conta conectada

## 3. Supabase

- projeto: `bpomihgzoiefjblewyun`
- MCP: projeto Supabase compartilhado, usado por esta frente somente apos aprovacao de contrato
- schema reservado: `lab_indicadores`
- estado: schema ainda nao criado; nenhuma migration, tabela, RLS ou job foi aplicado
- proibido: schema `lab_automatizado`, worker `lab-automatizado-vps-linux` e tabelas do outro laboratorio

## 4. VPS

- caminho exclusivo: `/srv/labs/projects/lab-b`
- datasets de entrada: `/srv/labs/datasets`
- montagem de dados: somente leitura
- diretorios de escrita: `runs/`, `logs/` e `work/`

## 5. Compose e entrada

- arquivo: `compose.yaml`
- projeto Compose: `lab_indicadores`
- servico: `indicator-worker`
- worker: `lab-indicadores-worker`
- entrada: `python -m lab_indicadores.worker`
- primeiro comando permitido: `smoke`
- estado atual: arquivo registrado localmente; nao implantado nem iniciado na VPS

## 6. Dataset do preflight

- manifesto proprio: `manifests/indicator-lab-smoke-v1.json`
- ativo: WDO e WIN, sempre separados no relatorio
- periodos: 2012, 2016, 2020 e 2024
- 2025 e 2026: proibidos neste preflight
- holdout: `holdout_accessed=false`
- hashes e paths: registrados no manifesto versionado

## 7. Limites

- CPU: 1 vCPU
- memoria: 2 GB
- PIDs: 128
- rede: `network_mode: none`
- root filesystem: somente leitura
- `/tmp`: tmpfs de 64 MB, sem execucao
- sem `privileged`
- sem `/var/run/docker.sock`
- sem portas publicas

## 8. Healthcheck e parada

- healthcheck: `docker compose -p lab_indicadores run --rm indicator-worker healthcheck`
- preflight: `docker compose -p lab_indicadores run --rm indicator-worker smoke`
- parada segura: `docker compose -p lab_indicadores stop`
- logs: `/srv/labs/projects/lab-b/logs`
- artefatos: `/srv/labs/projects/lab-b/runs`
- temporarios e DuckDB: `/srv/labs/projects/lab-b/work`

## Gate antes do primeiro job

O preflight so pode ser iniciado depois que este registro e o `compose.yaml`
estiverem presentes no caminho `/srv/labs/projects/lab-b`. O preflight nao
consulta Supabase, nao abre o holdout, nao executa ProfitDLL/replay/simulador
e nao altera os Parquets.
