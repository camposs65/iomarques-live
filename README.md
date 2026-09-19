# IoMarques Brechó — Controle de Vendas da Live · v1.7.2

Aplicativo para Windows usado para anotar as peças durante as lives no Instagram. O Supabase guarda a live em andamento e o histórico completo; a instalação prepara o programa e cria o atalho na Área de Trabalho.

## Instalar no computador da loja

Requisitos: Windows 10 ou 11 de 64 bits (x64), internet e um usuário do Controle do Brechó. Não é necessário instalar Python manualmente ou executar comandos.

1. No GitHub, abra este repositório e escolha **Code → Download ZIP**.
2. Clique com o botão direito no ZIP e escolha **Extrair Tudo**. Não execute o instalador de dentro do arquivo compactado.
3. Abra a pasta extraída e dê dois cliques em **Instalar.bat**.
4. Aguarde a preparação inicial e a janela de progresso. Na primeira instalação, o download e a montagem do aplicativo podem levar alguns minutos; mantenha a internet conectada.
5. Quando aparecer `Pronto!`, clique em **Abrir aplicativo** ou use o atalho **IoMarques Brecho** na Área de Trabalho.
6. No primeiro acesso, entre com o mesmo usuário e senha do Controle do Brechó e aguarde o carregamento do Supabase.

O instalador usa um Python compatível já disponível ou baixa o instalador oficial quando necessário. As dependências ficam em um ambiente privado, sem instalar bibliotecas no Python usado por outros projetos.

O aplicativo fica em `%LOCALAPPDATA%\Programs\IoMarquesLive`. A Área de Trabalho recebe apenas o atalho, inclusive quando essa pasta é gerenciada pelo OneDrive. Depois de instalado, o atalho não depende da pasta baixada do GitHub.

A instalação não abre o aplicativo automaticamente nem acessa ou modifica o Supabase. O botão `Abrir aplicativo` é uma ação separada; ao abrir e entrar na conta, o programa consulta a nuvem e pode iniciar a migração dos arquivos antigos encontrados.

### Atualizar sem perder dados

1. Confira se o aplicativo terminou de enviar as alterações e feche-o antes de atualizar. Não mantenha a versão antiga e a nova abertas ao mesmo tempo.
2. Baixe e extraia a versão nova do repositório e execute novamente `Instalar.bat`.
3. A instalação existente tem prioridade: seus dados de uso diário não são substituídos pelos arquivos da pasta baixada.
4. Se ainda não houver dados na pasta de instalação permanente, o instalador procura a versão antiga pelo atalho da Área de Trabalho. Sem esse atalho, verifica a pasta do projeto e sua subpasta `dist`. Se houver mais de uma origem possível, pede que você escolha a pasta usada pelo aplicativo da loja.

Os dados antigos são copiados, nunca removidos da origem. Não apague a pasta antiga, `dist` ou seus backups até conferir a migração completa no Supabase. A sessão e a fila criptografada pertencem ao usuário do Windows daquele computador: não transporte esses arquivos para outro PC. Em outro computador, faça uma instalação nova e entre na conta para carregar os dados já confirmados na nuvem.

### Se a instalação falhar

Use **Ver relatório** na janela do instalador. O arquivo fica em `%LOCALAPPDATA%\IoMarquesLive\instalador\instalacao.log`; a preparação do Python pode gerar também `python-instalacao.log` nessa pasta. Informe a mensagem apresentada e guarde o relatório para diagnóstico. Não apague dados ou sessões para tentar corrigir a instalação.

O ambiente de dependências fica em `%LOCALAPPDATA%\IoMarquesLive\instalador\venv`. Esse ambiente é do instalador; o aplicativo instalado possui seu próprio executável. Atualizações também podem precisar de internet para baixar dependências.

## Dados locais e backups

O computador mantém somente os arquivos necessários para conexão e recuperação de alterações ainda não confirmadas:

- `cloud_pendencias.dat`: fila temporária criptografada pelo Windows. Uma alteração só sai da fila após a confirmação do Supabase.
- `cloud_migracao.json`: controle técnico da importação inicial dos arquivos antigos; não é a base de dados do histórico.
- `integracao_sessao.dat`: sessão do Controle do Brechó protegida pelo Windows para o usuário atual. A senha não é armazenada.

Os antigos `live_atual.json`, `historico_lives.json`, `integracao_pendencias.json` e arquivos em `backups` não são apagados nem usados como base de gravação diária da nova versão. Guarde-os até conferir a migração completa. A fila criptografada só pode ser recuperada pelo mesmo usuário do Windows no mesmo computador; não use esse arquivo para transportar dados para outro PC.

Os arquivos de uso diário continuam ignorados pelo Git. A pasta `backups-publicados/` guarda as cópias incluídas anteriormente a pedido do responsável, com inventário de integridade. Esses arquivos foram preservados no repositório e no histórico do Git, mas `export-ignore` os exclui do ZIP gerado pelo GitHub. Um `git clone` ainda recebe os backups versionados. O instalador nunca inclui backups, sessões ou filas no executável distribuído.

## Como usar durante a live

- Clique em `Iniciar live` no momento em que a gravação da live começar.
- O contador fica rodando no canto superior direito.
- Ao preencher `Valor` ou `Código`, o app salva automaticamente o `Tempo` daquela peça.
- Clique em `Finalizar live` quando a live terminar.
- O app pede confirmação antes de finalizar a live.
- Ao finalizar, a live entra no histórico automaticamente.
- A live é salva a cada edição e também a cada 10 segundos enquanto o app estiver aberto.
- Clique em uma célula para editar.
- Quando o campo `Cliente` estiver preenchido, a linha fica levemente verde.
- Digitar na última linha vazia cria outra linha automaticamente.
- Os dados são enviados automaticamente ao Supabase. Se houver queda de conexão, ficam protegidos na fila temporária até o envio ser confirmado.
- Ao digitar `39,90` no valor, o app transforma em `R$ 39,90`.
- Ao apagar `Valor` e `Código`, o campo `Tempo` fica vazio novamente.
- Use as setas do teclado para navegar entre as células.
- Use `Ctrl+Z` para desfazer, `Ctrl+Y` para refazer e `Ctrl+F` para abrir a pesquisa.
- Use a setinha no cabeçalho de cada coluna para filtrar, como no Excel.
- Passe o mouse sobre `Cliente` ou `Suplente` para mostrar o `X` de remoção.
- Durante a live, o cabeçalho grande da marca fica escondido para dar mais espaço à planilha.

## Dados completos no Supabase

- Abra o botão de conexão/sincronização e entre com o mesmo usuário e senha do aplicativo Controle do Brechó.
- A senha não é armazenada. Depois do primeiro acesso, somente a sessão é guardada criptografada pelo Windows.
- O app salva o estado da live em andamento, cronômetro, ordem e conteúdo completo das peças: valor, código, cliente, suplente e tempo. Ao finalizar, salva também o histórico, os totais e as contagens.
- O valor da coluna `10% + R$ 50` corresponde a 10% do total vendido, arredondado em centavos, mais R$ 50,00 por live. A regra vale também para todas as lives antigas, sem alterar o total vendido nem os dados das peças. O resumo mensal soma os valores individuais, incluindo R$ 50,00 para cada live do mês. Exemplo: uma live de R$ 1.000,00 resulta em R$ 150,00. O Controle do Brechó calcula esse valor no Supabase com o mesmo arredondamento do histórico desktop.
- Clientes, suplentes e detalhes das vendidas passam a integrar os dados protegidos do desktop no Supabase. A tela de recortes da PWA continua recebendo apenas os resumos e as não vendidas. Esses dados não são ligados automaticamente aos cadastros do controle operacional.
- Os jogos continuam funcionando em memória, sem saves ou recordes persistidos. O vídeo nunca passa pelo desktop nem pelo Supabase. Exportações, impressão e arquivos temporários da automação de mensagens continuam sendo ações locais separadas.
- Lives antigas com apenas resumo também são enviadas, quando têm ID e datas válidas. Valores ausentes ou inválidos ficam como não informados, nunca como uma venda de valor zero. Lives com todas as peças vendidas continuam aparecendo no histórico.
- Alterações feitas depois da live são sincronizadas novamente. Se uma peça receber cliente, ela deixa de aparecer como não vendida.
- O histórico é consultado no Supabase, permitindo abri-lo em outro computador depois de entrar na conta. Não é mantida uma cópia permanente de todo o histórico no PC. Para carregar o histórico pela primeira vez após abrir o programa, é necessária uma conexão.
- Se a internet cair durante o uso, a live pode continuar sendo anotada e as mudanças ficam na fila protegida. O histórico já carregado permanece em memória enquanto o programa está aberto. Se fechar offline, somente os dados ainda pendentes têm recuperação local; o restante do histórico volta a carregar ao reconectar.
- A tela diferencia o que está salvo no Supabase do que está aguardando conexão. Nunca considere uma alteração pendente como já protegida na nuvem: se o computador falhar antes do envio, ela poderá ser perdida.
- `Sincronizar agora` tenta enviar as pendências e consultar os dados remotos novamente. A migração inicial usa os arquivos antigos sem sobrescrever lives que já tenham dados completos no banco. Se o backup e o banco divergirem, a diferença fica protegida como conflito para revisão.
- Se duas pessoas alterarem a mesma live, o app conserva a alteração pendente e avisa sobre o conflito. Não sobrescreve silenciosamente os dados do outro computador. Descartar a alteração local exige confirmação explícita.
- A exclusão completa exige conta administradora e confirmação do servidor. Uma live excluída não é recriada pela importação de um backup antigo.
- A importação manual de `historico_lives.json` na PWA continua disponível como alternativa, inclusive para o backup trazido da loja. A importação utiliza os mesmos resumos e não envia os nomes das clientes.

A estrutura de nuvem depende da migration `supabase/migrations/20260917000200_dados_completos_app_live.sql` do projeto `brecho-controle` e das migrations anteriores. Essa preparação é feita pelo responsável pelo banco, não pelo instalador. Sem a estrutura necessária, o aplicativo não confirma a gravação na nuvem. As importações antigas não podem sobrescrever os resumos das lives já gerenciadas pela versão com dados completos.

Para manter a comissão igual no Controle do Brechó e no desktop v1.7.2, aplique também `supabase/migrations/20260918000100_comissao_live_adicional.sql` no projeto `brecho-controle`. A migration recalcula a coluna de comissão das lives existentes e futuras; não é necessário reenviar históricos nem alterar os totais vendidos.

### Configuração da conexão

O repositório inclui `config_publica.json`, contendo somente a URL do projeto, a chave publicável e o e-mail-base usado para o login. Esses dados identificam a conexão, mas não substituem o login nem concedem acesso administrativo. Senhas, sessões, tokens de usuário, chaves secretas e `service_role` nunca devem entrar nesse arquivo ou no Git.

O instalador valida essa configuração e cria `integracao_supabase.json` na pasta permanente do aplicativo. Se a configuração estiver ausente ou inválida, a instalação não é declarada concluída. Para desenvolvimento, continuam disponíveis as variáveis `IOMARQUES_SUPABASE_URL`, `IOMARQUES_SUPABASE_PUBLISHABLE_KEY`, `IOMARQUES_AUTH_EMAIL_BASE`, arquivos locais de configuração e o `.env.local` do projeto irmão `brecho-controle`; a configuração pública é a alternativa incluída no download.

Exemplo da IoMarques Brechó:

| Peça | Valor | Código | Cliente | Suplente | Tempo |
| --- | --- | --- | --- | --- | --- |
| 1 | R$ 39,90 | 345 | chimabyliz | anapaula | 00:12:08 |

## Botões

- Antes de começar: aparecem `Iniciar live`, `Ações da live` e `Nova live / Limpar tudo`.
- Durante a live: aparece apenas `Finalizar live`.
- Depois de finalizar: aparecem `Ações da live` e `Nova live / Limpar tudo`.

Depois de finalizar:

- `Ações da live`: abre um menu limpo com `Resumo final`, `Mensagens clientes`, `Exportar Excel`, `Imprimir todos`, `Imprimir resumo`, `Imprimir planilha`, `Imprimir não vendidas` e `Histórico de lives`.
- `Histórico de lives`: agrupa as lives por mês e ano de finalização, com os meses mais recentes primeiro. Cada mês mostra a soma do total vendido e a soma de `10% + R$ 50` de cada live. Use a setinha do mês para recolher ou expandir as lives, que continuam mostrando duração, peças, clientes, total e comissão individuais. A mesma regra aparece na exportação Excel. Registros sem data de finalização usam a data de início; sem nenhuma data válida, ficam em `Sem data`. Somente administradores podem excluir uma live; a exclusão confirmada remove seus dados completos e o resumo no banco, recalculando as somas do mês.
- `Histórico de lives`: use `Abrir na planilha principal` ou dê dois cliques para carregar a planilha daquela live na tela principal quando ela tiver dados detalhados salvos. O app bloqueia essa abertura se houver uma live em andamento ou dados já preenchidos na planilha principal.
- `Resumo final`: mostra cada cliente em destaque, os códigos das peças, tempos, suplentes, checkbox por peça e total da cliente. No final, mostra clientes, peças vendidas e total vendido.
- `Mensagens clientes`: mostra o `@` de cada cliente na lista e no início da mensagem copiada, seguido das peças arrematadas, total e instruções de pagamento. O prefixo não é duplicado quando o nome já foi preenchido com `@`.
- `Exportar Excel`: cria um `.xlsx` com índice das peças, filtros nas colunas e as abas `Vendas`, `Resumo por cliente`, `Suplentes`, `Dados da live` e `Histórico de lives`.
- `Imprimir todos`: envia o resumo, a planilha e as peças não vendidas para a impressora padrão, ignorando automaticamente os itens que não tiverem dados.
- `Imprimir resumo`: envia um resumo por cliente com checkbox, nome da cliente em negrito, suplente na mesma linha da peça e totais finais. Quando não couber em uma folha, continua em páginas seguintes com fonte legível.
- `Imprimir planilha`: imprime a planilha em A4, retrato, ajustada para caber em uma página.
- `Imprimir não vendidas`: imprime apenas as peças preenchidas que ainda não têm cliente, com checkbox e o índice original da planilha principal (por exemplo, 3, 5 e 10), sem renumerar as peças. A opção `Imprimir todos` mantém a mesma numeração nesse relatório.
- `Nova live / Limpar tudo`: guarda a planilha atual e abre outra vazia com o contador zerado. Não exclui o histórico ou os rascunhos do Supabase; exclusão definitiva é uma ação separada de administrador.

## Logo e ícone

Os arquivos de marca ficam na pasta `assets`.

- Salve a logo original enviada como `assets/logo_original.png`.
- Rode `python gerar_assets_logo.py`.
- `logo_round.png`: logo original ajustado para o cabeçalho do app.
- `app_icon.ico`: ícone do aplicativo no Windows.

## Remover cliente ou suplente

O `X` aparece somente quando o mouse está em cima de uma célula de cliente ou suplente preenchida.

- `X` em `Cliente`: remove a titular, promove `Suplente` para `Cliente` e limpa `Suplente`.
- `X` em `Suplente`: limpa apenas a suplente.

## Desenvolvimento

O fluxo de instalação tem uma entrada: `Instalar.bat`. Ele chama `instalar.ps1`, que prepara o Python e o ambiente privado. A janela está em `instalar_app_windows.py`; a montagem do executável, preservação de dados e criação do atalho ficam em `installation.py`. Os antigos `criar_app_windows.bat` e `criar_app_windows.ps1` foram substituídos.

Para trabalhar no código com Python já instalado, use PowerShell na pasta do projeto:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe app.py
```

Abrir `app.py` inicia o aplicativo real, incluindo sua sincronização; não use essa ação como teste isolado de banco. A suíte automatizada utiliza serviços simulados. `requirements-build.txt` reúne as dependências da instalação e da geração do executável. Não é necessário executá-las manualmente para usar o programa.

A automação de Directs é um projeto separado, `iomarques-instagram-direct`, com requisitos próprios. A instalação preserva seu caminho quando encontra esse projeto; não instala o automatizador nem copia credenciais dele. Jogos, exportação e impressão continuam incluídos no aplicativo principal.
