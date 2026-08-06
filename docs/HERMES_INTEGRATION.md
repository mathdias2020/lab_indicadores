# Hermes no laboratório de indicadores

## Estado desta integração

O laboratório agora possui o agente lógico hermes-indicadores, registrado no
control plane e acompanhado pelo dashboard. A primeira ativação é
observacional:

- status: observing quando o heartbeat está saudável;
- mode: observation;
- fonte: somente /srv/labs/datasets/canonical/normalized_sample_v1;
- escrita: somente /srv/labs/projects/lab-b/hermes/outbox;
- holdout_access: false;
- execution_enabled: false;
- network_access: false;
- docker_socket_access: false;
- nenhuma credencial Supabase é entregue ao runtime.

O orquestrador deste laboratório lê o heartbeat local e atualiza
lab_indicadores.agents. O browser acessa apenas
public.dashboard_list_agents() com uma sessão autenticada.

## O que ainda não está ativado

O motor que analisa dados e propõe hipóteses não é iniciado neste primeiro
passo. Ele será um processo separado, com:

1. contrato de contexto versionado;
2. artefato de proposta com hash;
3. revisão humana antes de qualquer alteração de indicador;
4. separação explícita entre observação, proposta, pesquisa e revisão;
5. nenhuma promoção automática para execução ou holdout.

Isso permite que o Hermes aprenda com resultados e proponha melhorias sem
alterar silenciosamente a definição de um indicador, contaminar o holdout ou
interferir no laboratório lab_automatizado.

## Serviços e fronteiras

| Item | Este laboratório | Outro laboratório |
| --- | --- | --- |
| agente | hermes-indicadores | fora do escopo deste projeto |
| diretório | /srv/labs/projects/lab-b/hermes | não tocar |
| tabela | lab_indicadores.agents | não tocar |
| serviço | lab-indicadores-hermes-runtime.service | não tocar |
| dados | /srv/labs/datasets, leitura | /srv/labs/datasets, leitura |

O runtime atual não é o motor de hipóteses completo; é a camada de observação
e prova de isolamento sobre a qual o motor será acoplado.
