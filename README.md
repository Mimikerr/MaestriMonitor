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
