# Dashboard MVP

Cockpit estático do laboratório de indicadores. Ele usa somente:

- Supabase Auth no browser;
- a chave publishable, que pode ser pública;
- RPCs `public.dashboard_*` `SECURITY INVOKER`;
- RLS do schema `lab_indicadores`.

Nenhuma chave `service_role`, secret key ou senha PostgreSQL entra no diretório.

## Executar localmente

Na raiz do repositório:

```bash
python3 -m http.server 4173 --directory dashboard
```

Abra `http://localhost:4173`. O arquivo `config.js` contém somente URL e chave
publishable do projeto Supabase.

O acesso depende de uma conta Supabase Auth. O botão de criação de acesso existe
para o primeiro usuário do laboratório; em produção, a política de cadastro deve
ser substituída por convite ou allowlist.

## Produção

- URL: <https://lab-indicadores.vercel.app>
- Projeto Vercel: `lab-indicadores`
- O deploy contém somente os cinco arquivos estáticos deste diretório.
- A VPS continua responsável pelo worker e pelo orquestrador; nenhuma porta da
  VPS é usada pelo painel.
