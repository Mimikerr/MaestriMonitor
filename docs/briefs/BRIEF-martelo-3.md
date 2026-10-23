# Martelo - mover o Monitor para o projeto separado D:\Monitor

Decisoes do usuario (confirmadas): projeto separado em D:\Monitor (pasta existe, vazia), repositorio git proprio (local, sem remoto), runtime tambem la, e REMOVER do repo softcom-balanca com commit de revert (sem reescrever historico).
Voce so mexe em D:\Monitor e no checkout Ground (C:\Users\samuel.almeida\StudioProjects\softcom-balanca, dev). NAO toque na rotina 8a9e02, no portal Painel nem no terminal Monitor (o Maestro faz isso depois). NAO faca push.

## 1. Montar D:\Monitor (antes de reverter qualquer coisa)
Estrutura:
- monitor_check.py, monitor-config.json, painel_template.html (na raiz ou src/; escolha simples e mantenha o `python -I D:/Monitor/monitor_check.py` como entrada fixa)
- tests/test_monitor_check.py (rodar com: cd D:\Monitor; python -I -m unittest discover -s tests)
- README.md (a partir de docs/monitor.md do repo: adapte caminhos, fluxo, limiares 30 min / 75-90 / lembrete 2 h, como editar trilhas, como abrir o painel pelo portal "Painel", como rodar os testes; corrija "Vitrine" -> "Painel")
- docs/briefs/ (mova os BRIEF-*.md e probe.py de C:\Users\samuel.almeida\StudioProjects\.maestri\monitor\; copie, nao apague os originais ainda)
- runtime/ (state.json, painel.html, marcadores): copie state.json atual e os .avisado de C:\Users\samuel.almeida\StudioProjects\.maestri\routine-andares\ para D:\Monitor\runtime\marcadores\ (mantem o anti-spam sem reenviar tudo)
- .gitignore: runtime/, __pycache__/, *.pyc ; git init, branch main, commit inicial `feat: Monitor dos andares como projeto separado`.

## 2. Ajustar caminhos no codigo (TDD: teste vermelho, depois verde)
- Todos os caminhos de runtime relativos a raiz do projeto Monitor (Path(__file__).parent): runtime/state.json, runtime/painel.html, runtime/marcadores/*.avisado. Nada de C:\Users\...\StudioProjects\.maestri\monitor nem routine-andares.
- O roadmap vem do repo do produto: novo campo em monitor-config.json `repo_produto` = C:/Users/samuel.almeida/StudioProjects/softcom-balanca; `git -C <repo_produto> fetch origin dev` e `git -C <repo_produto> show origin/dev:docs/roadmap.md`. Teste com repo falso.
- Leituras externas inalteradas (somente leitura): ~/.maestri/usage/providers/.status.json e ~/.maestri/workspaces/<id>/workspace.json.
- Aproveite e fecha a pendencia media da Sentinela (monitor_check.py ~372-376): a doc diz que erro repetido marca "travado" mas o codigo so trata blocked >=30 min. Implemente (mesma saida de erro/status repetida >= 30 min com estado nao-working marca travado) com teste, OU tire a frase do README se nao for viavel; diga qual escolheu.
- Rode no D:\Monitor: unittest verde, uma execucao real (`python -I D:/Monitor/monitor_check.py`, exit code + painel em D:\Monitor\runtime\painel.html, 10 percentuais batendo com .status.json) e uma segunda execucao (nao repete avisos). Commit no repo Monitor.

## 3. Limpar o repo softcom-balanca
Somente depois de 1 e 2 verdes: `git status` limpo de mudancas rastreadas, entao `git revert --no-commit 6dcaa75 fec6e81 47dad88` (ordem do mais novo ao mais antigo; resolva conflito no CLAUDE.md se houver, preservando as outras mudancas dos andares) e um unico commit `revert: move o Monitor dos andares para D:\Monitor`. Confira: tools/monitor, tools/tests/test_monitor_check.py, docs/monitor.md e as linhas do Monitor no CLAUDE.md sumiram; `python -I -m unittest discover -s tools/tests`, `python tools/check_file_size.py` e `python tools/check_e2e_tags.py` continuam verdes; nao apague os untracked pre-existentes (.maestri/roles/916f0334..., __pycache__).

## 4. Reporte
`maestri ask "Claude Code" "<resultado>"` com: estrutura criada, hash do commit inicial do Monitor, hash do revert, resultados dos testes e das duas execucoes, qual escolha na pendencia "travado", e qualquer ponto em que o caminho antigo ainda e referenciado.
