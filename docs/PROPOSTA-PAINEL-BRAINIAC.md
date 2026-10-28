# Painel central dentro do Brainiac — proposta e protótipo funcionando

## Em uma frase
Uma tela acima dos cadernos (workspaces) onde se vê o estado de todos os projetos e se manda ordens ao
maestro de cada um, sem abrir caderno por caderno e sem gastar token para olhar.

Já existe um **protótipo funcionando**, feito sobre o MaestriMonitor, na branch `feat/painel-brainiac`
deste repositório. Ele lê o Brainiac pela CLI (`brainiac call …`). Este documento diz o que o protótipo já
faz, o que aprendi e o que seria melhor fazer **nativamente** dentro do Brainiac.

## O problema
Vários projetos, cada um com seu caderno e seu maestro. Para saber o que está acontecendo é preciso abrir
cada um; para mandar algo, saber qual terminal é o certo; e nada mostra de uma vez quem está
trabalhando, quem deu erro, qual é o plano e o que falta entregar.

## Como rodar o protótipo
```
python painel_brainiac.py        # http://127.0.0.1:8766/
```
Projetos, maestros, cores e branches em `projetos.json`. Testes: `python -I -m unittest discover -s tests`
(80 testes). Os dados de execução ficam em `runtime/` (fora do Git).

## O que o painel faz hoje

### 1. Estante de cadernos (tela inicial)
- Cada projeto é um **livro**; a lombada tem a cor do caderno no Brainiac.
- **Agrupada pelos grupos da barra lateral do Brainiac** (`sidebarLayout.get`), com o nome do grupo e a
  contagem. Cabem quantos livros couberem na largura.
- Selo de estado em cada livro: **trabalhando**, em andamento, parado ou **com erro**; marcador vermelho
  nos que têm agente em erro. Mostra também nº de agentes, estado do maestro, situação do release e do plano.
- **Organizar estante:** reordenar grupos e livros por setas ou arrastando. A ordem fica salva no navegador.
- **Uso das contas** recolhido numa etiqueta discreta (`usage.getState`).

### 2. Caderno aberto
- **Conversa com o maestro** em linha do tempo: ordens enviadas e respostas, a última destacada, as
  anteriores atenuadas. O texto é reorganizado em parágrafos e listas (o terminal quebra em colunas).
- **Estado "trabalhando"**: cartão com a atividade atual (por exemplo "Enchanting… 7s · 142 tokens").
- **Plano de ação** lido de um `PLANO.md` do projeto (`[x]` feito, `[~]` em andamento, `[ ]` próximo).
- **Entregas:** branches aguardando release, em andamento e prontas para limpar (git local, sem rede).
- **Agentes** do caderno, com status e último turno.
- Tudo cabe na altura da tela; a conversa rola por dentro e abre no final.

### 3. Ordens
- Dentro do caderno: canal **só daquele maestro**, em barra fixa no rodapé.
- Na estante: **mesa de ordens** recolhível, com o caderno de destino escolhido por botões.
- **Confirmação antes de enviar** (mostra texto e imagens). Recusa maestro parado.
- **Imagens:** colar (Ctrl+V), arrastar ou escolher. O servidor salva o arquivo e a ordem leva o caminho;
  o agente lê o arquivo. Testado: o Maestro Central leu uma imagem e descreveu o conteúdo.
- O texto da ordem nunca vai para log; só horário, destino e tamanho.

### 4. Maestro Central (agente)
Um agente num caderno "Central" que supervisiona os outros: lista agentes, lê terminais, **reinicia os que
estão em erro** e **inicia qualquer agente a pedido do dono**. Não envia tarefas, nem edita código, nem
faz commit sem ordem. Instruções em `MAESTRO-CENTRAL.md`.
Já corrigiu, por receita, o erro "Session ID … is already in use" (ver "Armadilhas").

## Métodos do Brainiac que o protótipo usa (conferidos)
| Para quê | Método |
|---|---|
| Cadernos e grupos | `workspaces.list`, `sidebarLayout.get` |
| Agentes e estado | `agents.list`, `agents.restart`, `agents.configure` |
| Tela do terminal | `terminals.list` (cols/rows), `terminals.scrollback` |
| Enviar ordem | `terminals.input` (texto, depois Enter) |
| Uso das contas | `usage.getState` |
| Canvas (Maestro Central) | `canvas.nodes.create`, `canvas.snapshot` |

## Armadilhas encontradas (úteis para quem implementar nativamente)
1. **O terminal de tela cheia só guarda na tela as últimas linhas.** Ao chegar uma resposta nova, a antiga
   some do `terminals.scrollback`. Hoje o painel lê a tela a cada ~3 s e **acumula o histórico no servidor**.
   O scrollback também é cortado em 512 KB, então o começo do fluxo se perde.
2. **O scrollback é texto cru com códigos ANSI** (posição do cursor, apagar linha). Arrancar os códigos junta
   as palavras. O protótipo reconstrói a tela com um emulador mínimo (`terminal_screen.py`) usando o tamanho
   real do terminal.
3. **Ruído da interface do Claude Code** que não é resposta: chamadas de ferramenta (`●` com saída `⎿`),
   "Running…", resumos "Ran 1 shell command", pesquisa de satisfação, aviso de atualização, caixa de entrada.
   O filtro é específico do Claude Code; Codex tem outra interface.
4. **Resposta com o começo fora da tela:** a bolinha que marca o início já saiu do topo. O protótipo trata o
   trecho como "parcial" e o junta à resposta guardada.
5. **Agentes vindos do Maestri** trazem um `sessionId` que só existe no `~/.claude`; ao iniciar, o Claude
   recusa com "Session ID … is already in use". Conserto: trocar o `sessionId` do agente por um novo
   (`agents.configure`, campo direto em `patch`).
6. **`agents.create` não cria o bloco no canvas**; é preciso `canvas.nodes.create` com `agentId` e `terminalId`.
7. `terminals.input` com texto + `\r` funciona para o Claude Code; o texto vai numa linha só.

## O que o Brainiac nativo faria melhor que o protótipo
O protótipo adivinha o que o agente fez lendo a tela. Nativamente isso seria bem mais robusto:
1. **Transcrição estruturada por agente** (turnos de ordem/resposta/ferramenta com hora), em vez de raspar a
   tela. Resolve histórico, "começo fora da tela", ruído e horário de cada resposta.
2. **Estado "trabalhando" como campo do agente** (hoje inferido da linha de status do terminal).
3. **Eventos em tempo real** (`events.subscribe`) no lugar de leitura periódica.
4. **Entrada de imagem** no canal de ordens sem depender de caminho de arquivo.
5. **Ordem de grupos e livros** persistida no Brainiac, junto do `sidebarLayout`.
6. **Roteamento de ordens** para o maestro certo como recurso do app, com confirmação e registro.

## Configuração por projeto (sugestão)
Cada projeto declara o seu critério; o do Provisioner serve de exemplo:
```
apelido: provisioner        # prefixo das ordens
workspace: <nome do caderno>
maestro: <nome do agente>
plano: PLANO.md             # checklist [x] [~] [ ]
branch_integracao: DEV      # entregas pendentes = na integração e ainda não na release
branch_release: main
cor: #5F6B2A
```
"Pronta para limpar" = branch já incorporada à release. Release é disparada pelo dono quando avisa que terminou.

## Perguntas em aberto
1. O plano fica em arquivo do projeto (`PLANO.md`) ou numa nota do workspace?
2. Apagar branches incorporadas deve ser feito pelo painel (sempre com confirmação) ou só sugerido?
3. O painel pode disparar o release ou só avisa que está pronto? (sugestão: só avisa)
4. Como tratar projetos no Azure DevOps (Smart), que têm outro fluxo de PR?
5. O Maestro Central deve ser um recurso nativo (agente de supervisão por workspace) ou continuar como
   agente comum do usuário?
6. A ordem das estantes é do usuário (por máquina) ou do workspace (compartilhada)?

## Limites do protótipo
- Depende do painel ficar rodando para não perder respostas que saem da tela do terminal.
- A ordem da estante fica no navegador onde foi feita.
- O filtro de respostas foi feito para o Claude Code; para Codex aparece mais ruído.
- Não há hora por resposta (só ordem e destaque da última).
- Cadernos duplicados com o mesmo nome (ex.: dois "Softcom Smart Express") precisam ser desambiguados à mão.

## Arquivos
| Arquivo | Papel |
|---|---|
| `painel_brainiac.py` | servidor local (somente `127.0.0.1`), rotas `/api/snapshot`, `/api/enviar`, `/api/anexo` |
| `painel_brainiac.html` | a tela (estante, caderno, mesa de ordens) no estilo "Caderno de Campo" |
| `brainiac_source.py` | lê o Brainiac e monta o retrato dos projetos; envia ordens |
| `terminal_screen.py` | reconstrói a tela do terminal e extrai ordens/respostas |
| `conversa.py` | histórico da conversa acumulado entre leituras |
| `anexos.py` | salva imagens anexadas (valida pelo conteúdo) |
| `projetos.json` | projetos, maestros, cores, branches |
| `MAESTRO-CENTRAL.md` | instruções do agente supervisor |
