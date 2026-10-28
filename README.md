# Monitor dos andares

Projeto local independente do Softcom Balança. O Monitor observa Ground, Dev 2 e Dev 3, atualiza um painel HTML e emite somente eventos novos. Não reserva trabalho nem altera o repositório do produto.

## Rodar

A entrada fixa é `python -I D:/Monitor/monitor_check.py`. O script lê o uso das contas em `%USERPROFILE%/.maestri/usage/providers/.status.json` e encontra o workspace `Smart-balança` pelo `payload.name` em `%USERPROFILE%/.maestri/workspaces/*/workspace.json`; essa fonte é somente leitura. O roadmap vem do repositório de produto definido por `repo_produto` em `monitor-config.json`.

O runtime fica em `D:/Monitor/runtime/`: `state.json` preserva o anti-spam, `painel.html` é regenerado, e `marcadores/*.avisado` controlam notificações de review. Abra `runtime/painel.html` pelo portal Maestri chamado **Painel**. O painel atualiza a cada 60 segundos.

## Alertas e configuração

Andar sem mudança por 30 minutos fica parado; `blocked` por 30 minutos ou o mesmo erro/status não-working sem mudança por 30 minutos fica travado. Uso de sessão/semanal avisa em 75% e fica crítico em 90%; também alerta quando o ritmo projeta esgotar a cota antes do reset. Eventos repetidos lembram após 2 horas; piora de nível alerta imediatamente.

Edite `monitor-config.json` para ajustar as trilhas, branches, contas vigiadas e itens com riscos exclusivos de schema, container ou baseline. O Monitor sugere o próximo item desbloqueado, respeita dependências e reservas do roadmap, e nunca faz a reserva.

## Testes

```powershell
cd D:\Monitor
python -I -m unittest discover -s tests
```

## Painel central via Brainiac (somente leitura)

`python painel_brainiac.py` abre http://127.0.0.1:8766/ com o uso das contas, o estado dos agentes de cada projeto, as ultimas linhas do terminal do maestro, o plano de acao (`PLANO.md` com checklist `[x]` feito, `[~]` em andamento, `[ ]` proximo) e as branches pendentes/prontas para limpar. Le tudo pela API do Brainiac (`brainiac_source.py`), sem gastar token e sem enviar comando a agente. Projetos, maestros e branches em `projetos.json`. Visual no estilo Caderno de Campo do Brainiac.
