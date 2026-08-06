# Registro tecnico da VPS

Status: `PREFLIGHT_SUCCEEDED` (nenhum container persistente)

## 1. Identidade

- `project_id`: `lab-indicadores`
- nome tecnico: `Laboratorio de Indicadores WDO WIN`
- funcao: pesquisa de indicadores de fluxo, preco e fluxo + preco

## 2. Repositorio

- Git: `https://github.com/mathdias2020/lab_indicadores`
- branch de execucao: `main`
- branch/commit local de referencia: `main` / verifique o commit publicado antes do deploy
- observacao: o repositorio local tem o contrato; publicacao no GitHub depende da permissao de push da conta conectada

## 3. Supabase

- projeto: `bpomihgzoiefjblewyun`
- MCP: projeto Supabase compartilhado, usado por esta frente somente dentro do contrato desta pasta
- schema reservado: `lab_indicadores`
- estado: schema e control plane v1 aplicados; nenhum job persistente foi instalado na VPS
- migrations: `control_plane_v1` e `control_plane_v1_owner_policies`
- smoke control-plane: `control-plane-smoke-v1-20260806` concluido com sucesso
- proibido: schema `lab_automatizado`, worker `lab-automatizado-vps-linux` e tabelas do outro laboratorio

## 4. VPS

- caminho exclusivo: `/srv/labs/projects/lab-b`
- painel MVP: `/srv/labs/projects/lab-b/dashboard` (artefato estático; publicado na Vercel)
- datasets de entrada: `/srv/labs/datasets`
- montagem de dados: somente leitura
- diretorios de escrita: `runs/`, `logs/` e `work/`

## 5. Compose e entrada

- arquivo: `compose.yaml`
- projeto Compose: `lab_indicadores`
- servico: `indicator-worker`
- worker: `lab-indicadores-worker`
- entrada: `python /app/src/lab_indicadores/worker.py`
- primeiro comando permitido: `smoke`
- estado atual: registrado e executado como container descartavel (`run --rm`)
- orquestrador: host-side instalado como unidade systemd, com credencial server-side fora do Git

## 6. Dataset do preflight

- manifesto proprio: `manifests/indicator-lab-smoke-v1.json`
- ativo: WDO e WIN, sempre separados no relatorio
- periodos: 2012, 2016, 2020 e 2024
- 2025 e 2026: proibidos neste preflight
- holdout: `holdout_accessed=false`
- hashes e paths: registrados no manifesto versionado

Resultado do preflight: executado duas vezes com o mesmo resultado. O relatorio
esta em `/srv/labs/projects/lab-b/runs/indicator-lab-preflight-v1/preflight-report.json`
e o hash SHA-256 observado foi:

`49fd1b895ae3d72e88d1a4320d0f21a104c02b49030e994da477e5ddccd51100`

O relatorio registra `holdout_accessed=false`, oito arquivos com modo `0444`
e `worker_id=lab-indicadores-worker`. `docker ps -a` nao encontrou container
persistente do projeto.

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
