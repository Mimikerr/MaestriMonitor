# Brief Martelo: script do Monitor (TDD, Python, sem Gradle)

Plano aprovado: C:\Users\samuel.almeida\.claude\plans\crie-um-outro-terminal-golden-blossom.md (leia inteiro).
Trabalhe no Ground (branch dev, C:\Users\samuel.almeida\StudioProjects\softcom-balanca). Rode `git status` antes. Nao faca push. Um commit so: `feat(tools): monitor dos andares e painel`.

## Passo 0 - spike (10 min, reporte antes de seguir)
Descubra o que um `--pre-run` de rotina Maestri enxerga. Crie uma rotina descartavel `--disabled` com pre-run `maestri floor status --floor "Dev 2" & maestri check "Claude Code #2"` gravando a saida em arquivo, rode `maestri routine run`, leia o resultado e DELETE a rotina de teste (so a sua). Pergunta: o pre-run tem o CLI maestri no PATH e acesso a floor status/check? Se nao, o script le so arquivos/git e a coleta de status fica para o LLM na anomalia. Registre a resposta no topo do script (docstring).

## Entregaveis
- tools/monitor/monitor_check.py (stdlib only; `python -I`), tools/monitor/monitor-config.json, tools/monitor/painel_template.html
- tools/tests/test_monitor_check.py (unittest; entra no `python -m unittest discover -s tools/tests`)
- Nenhum .kt. Arquivos < 800 linhas. Sem credencial/URL real.
- Runtime fora do repo: C:\Users\samuel.almeida\StudioProjects\.maestri\monitor\{state.json,painel.html}. Marcadores existentes: ...\.maestri\routine-andares\<Dev2|Dev3>.avisado (e crie Ground.avisado do mesmo jeito).

## Contrato do script
Entradas (todas injetaveis para teste: now, caminhos, runner de comandos):
1. Contas: ~/.maestri/usage/providers/.status.json -> providers[].{id,name,plan,state,meters[].windows[].{id,ring,usedPercent,resetsAt,durationSeconds}}. Nivel por janela: >=75 AVISO, >=90 CRITICO. Ritmo: projecao = usedPercent / fracao_decorrida_da_janela; se >100 e usedPercent>=40 -> evento "ritmo". Conta = providers[].name (+ plan). Recomendacao de troca (texto): sugerir conta/agente com menor uso semanal (ex.: "Claude 92% semanal -> mover Prova/Lupa para Codex Squad IA (31%)"). Nunca executa a troca.
2. Roadmap: `git fetch origin dev` (falha de rede nao derruba o script) e `git show origin/dev:docs/roadmap.md`. Parse das linhas `- [x|~|!| ] [hash] Rnn titulo · dep Rxx,Ryy`; reserva `[~] (andar)`. Desbloqueado = `[ ]` com todas as deps `[x]`. Propostas por andar = trilha em monitor-config.json (Ground: R23-R28,R55,R51; Dev 2: R29,R30,R49; Dev 3: R38-R41 e correlatas; configuravel), primeiro desbloqueado nao reservado; se trilha esgotada, sugerir de outra trilha marcando "fora da trilha". Respeitar donos unicos (CLAUDE.md "Andares em paralelo"): nao propor 2 itens que mexam em schema Room/AppContainer ao mesmo tempo (config marca itens "schema" e "container").
3. Andares: Ground (orq "Claude Code", branch dev), Dev 2 ("Claude Code #2", dev-home), Dev 3 ("Claude Code #3", dev-3). Sinais: texto/estado de `maestri floor status --floor <andar>` (se o spike permitir), HEAD da branch (`git rev-parse`), hash da saida de `maestri check <orq>` (se permitir). state.json guarda, por andar, ultimo valor de cada sinal e `last_change`. Parado = nenhum sinal mudou ha >=30 min e estado working. Travado = estado blocked ha >=30 min (ou mesma saida de erro). Ocioso = done/sem status/sem tarefa. Review = estado review (reaproveita marcador .avisado). 
4. Anti-spam: evento = (andar|conta, tipo, nivel). Um aviso por evento, lembrete a cada 2 h se persistir, reenvio imediato se piorar (AVISO->CRITICO). Guardar em state.json.

Saida: imprime so EVENTOS NOVOS, uma linha cada, formato `[tipo] alvo | detalhe | acao sugerida`; exit 0 se houver evento, exit 1 se nao houver (rotina pula, zero token). SEMPRE regrava painel.html e state.json, haja evento ou nao. Falha parcial de uma fonte nao derruba as outras (mostra "indisponivel" no painel).

## Painel (painel_template.html + render no script)
HTML unico, sem dependencia externa, claro/escuro (prefers-color-scheme), meta refresh 60 s, largura mobile ok. Abas (CSS/JS puro): Andares (estado, item atual, ha quanto tempo sem mudar, alerta), Demandas (lista do roadmap: feito/em andamento/a fazer/bloqueado [!], contadores), Propostos (por andar: item, deps, motivo, risco de conflito), Limites das contas (barras sessao/semanal por conta com cor OK/AVISO/CRITICO, reset, ritmo, recomendacao de troca). Mostrar "atualizado em" e a idade dos dados. Sem segredos (so nomes de conta e %).

## Testes (vermelho antes do verde)
sem anomalia -> exit 1 e nenhum evento; andar 29 min sem mudanca -> nada, 31 min -> parado; blocked 31 min; review sem/com marcador; conta 74/75/90 e ritmo; lembrete antes/depois de 2 h; piora AVISO->CRITICO reenvia; roadmap com dep aberta nao propoe; item reservado nao propoe; roadmap ilegivel nao derruba; painel contem as 4 abas e nenhum segredo.

## Ao terminar
`python -I -m unittest discover -s tools/tests` verde, `python tools/check_file_size.py`, rode o script uma vez no estado real e mostre as primeiras linhas + caminho do painel. NAO crie rotina nem portal (o Maestro faz). Reporte com `maestri ask "Claude Code" "<resultado>"`.
