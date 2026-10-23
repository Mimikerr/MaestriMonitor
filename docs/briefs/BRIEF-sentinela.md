# Brief Sentinela: auditoria SOMENTE LEITURA do Monitor (script + painel)

Checkout: C:\Users\samuel.almeida\StudioProjects\softcom-balanca (Ground, branch dev). Rode `git status` e `git log -5` primeiro.
NAO altere nenhum arquivo, NAO toque na rotina 8a9e02, NAO commite, NAO rode maestri floor land/delete.

Escopo (4 caminhos do Martelo): tools/monitor/monitor_check.py, tools/monitor/monitor-config.json, tools/monitor/painel_template.html, tools/tests/test_monitor_check.py.
Criterios: C:/Users/samuel.almeida/StudioProjects/.maestri/monitor/BRIEF-martelo.md (contrato do script, painel, testes). Plano: C:/Users/samuel.almeida/.claude/plans/crie-um-outro-terminal-golden-blossom.md.

Verifique com evidencia (comando + resultado):
1. `python -I -m unittest discover -s tools/tests` (Martelo registrou 137 testes verdes) e `python tools/check_file_size.py`.
2. Cada item do contrato do BRIEF: contas (75/90, ritmo >100 e >=40, recomendacao sem executar troca), roadmap via `git show origin/dev:docs/roadmap.md` (falha de rede nao derruba), propostas por trilha + donos unicos, sinais por andar (a fonte de status e ~/.maestri/workspaces/<id>/workspace.json, SEM CLI maestri: pre-run nao tem acesso), parado >=30 min, travado, ocioso, review + marcadores .avisado, anti-spam (lembrete 2 h, reenvio na piora), exit 0 com evento e exit 1 sem evento, grava state.json e painel.html sempre, falha parcial nao derruba.
3. Execucao real: rode o script uma vez, confira exit code, eventos, e abra painel.html: 4 abas (Andares, Demandas, Propostos, Limites das contas), claro/escuro, refresh 60 s, "atualizado em", sem dependencia externa e sem segredo (so nomes de conta e %). Compare os % com ~/.maestri/usage/providers/.status.json.
4. Higiene: arquivo < 800 linhas, stdlib only, sem credencial/URL real, o script NUNCA escreve em workspace.json (somente leitura), `git status` mostra so os 4 caminhos (+ docs ja commitados em 47dad88).
5. Se o Martelo ainda nao commitou, diga; nao commite por ele.

Parecer em tres blocos: Verificado / Nao verificado / Pendencias (cada pendencia com severidade e arquivo:linha). Entregue com: maestri ask "Claude Code" "<parecer>" (eu repasso ao Martelo).
