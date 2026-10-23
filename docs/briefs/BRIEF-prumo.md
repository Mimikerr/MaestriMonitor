# Brief Prumo: documentar o Monitor

Contexto: novo terminal fixo "Monitor" (Codex, conta Padrao, Maestro Mode ligado) que vigia os orquestradores dos 3 andares. Plano: C:\Users\samuel.almeida\.claude\plans\crie-um-outro-terminal-golden-blossom.md. Role Maestri ja criada: `maestri role show "Monitor de Andares"`.
Trabalhe no Ground (dev); `git status` antes; sem push; nao toque em codigo de producao.

Tarefa (so docs):
1. CLAUDE.md: acrescente o Monitor na tabela de roles de "Delegacao" (papel: vigia parado/travado/ocioso dos 3 orquestradores, painel local, limites das contas, propostas por trilha; so observa e avisa; acionado por rotina a cada 10 min com pre-run que so o acorda na anomalia; nunca land/delete/codigo/reserva). Ajuste a frase "Nao ha subagentes..." se necessario e a subsecao "Andares em paralelo" com uma regra: o Monitor nao reserva itens e nao cria commits. Mantenha curto.
2. Procure em .claude/skills/, docs/ e nas roles Maestri (`maestri role list/show`) mencoes a rotina "Monitorar andares Dev 2 e Dev 3" ou a vigia por o Orquestrador; liste o que encontrar e atualize o que for texto de projeto (roles Maestri: so proponha o diff, o Maestro aplica).
3. Crie docs/monitor.md (<= 60 linhas): o que e, fluxo (script -> eventos -> Monitor), onde ficam painel/state/marcadores (fora do repo), limiares (30 min, 75%/90%, lembrete 2 h), como editar trilhas em tools/monitor/monitor-config.json, como abrir o painel.
Sem ADR (nao muda schema, contrato nem invariante). Commit: `docs: documenta o Monitor dos andares`. Reporte com `maestri ask "Claude Code" "<resultado>"`.
