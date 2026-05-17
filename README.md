# IoMarques Brechó - Controle de Vendas da Live

Programa desktop em Python para registrar vendas durante as lives no Instagram.

## Como instalar

```powershell
pip install -r requirements.txt
```

## Como abrir

```powershell
python app.py
```

## Como transformar em app de Área de Trabalho no Windows

### Opção 1: mandar o projeto para o computador da loja

1. Coloque esta pasta em um `.zip`, pendrive, Google Drive ou OneDrive.
2. No computador da loja, extraia a pasta em um lugar fixo, por exemplo:

```text
Documentos\IoMarques Lives
```

3. Instale o Python pelo site oficial:

```text
https://www.python.org/downloads/
```

Na instalação, marque a opção `Add python.exe to PATH`.

4. Dentro da pasta do projeto, dê dois cliques em:

```text
criar_app_windows.bat
```

O instalador cria o arquivo `dist\IoMarques Brecho.exe` e um atalho `IoMarques Brecho` na Área de Trabalho.

### Opção 2: mandar só o app pronto

Se você já gerou o app neste computador, pode mandar apenas a pasta `dist` para o computador da loja. Nesse caso, o computador da loja não precisa ter Python instalado para abrir o app.

Para gerar o app pronto neste computador, rode:

```powershell
.\criar_app_windows.ps1
```

Ao clicar no `.bat`, aparece uma janela de instalação com progresso. O terminal não fica como tela principal.

Depois de instalado, o app abre direto pela Área de Trabalho, sem tela de carregamento.

Os dados ficam salvos junto do executável:

- `live_atual.json`: live em andamento.
- `historico_lives.json`: histórico das lives finalizadas.
- `backups\live_atual.bak.json`: cópia de recuperação da live atual.
- `backups\historico_lives.bak.json`: cópia de recuperação do histórico.
- `backups\live_atual_*.json`: snapshots recentes da live atual, mantidos automaticamente.

Esses arquivos não devem ir para Git/GitHub; eles são dados reais de operação da loja.

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
- Os dados ficam salvos automaticamente no arquivo `live_atual.json`.
- Ao digitar `39,90` no valor, o app transforma em `R$ 39,90`.
- Ao apagar `Valor` e `Código`, o campo `Tempo` fica vazio novamente.
- Use as setas do teclado para navegar entre as células.
- Use `Ctrl+Z` para desfazer, `Ctrl+Y` para refazer e `Ctrl+F` para abrir a pesquisa.
- Use a setinha no cabeçalho de cada coluna para filtrar, como no Excel.
- Passe o mouse sobre `Cliente` ou `Suplente` para mostrar o `X` de remoção.
- Durante a live, o cabeçalho grande da marca fica escondido para dar mais espaço à planilha.

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
- `Histórico de lives`: mostra as lives finalizadas, duração, peças, clientes e total. Selecione uma live e clique em `Excluir live selecionada` para remover apenas o registro do histórico.
- `Histórico de lives`: use `Abrir na planilha principal` ou dê dois cliques para carregar a planilha daquela live na tela principal quando ela tiver dados detalhados salvos. O app bloqueia essa abertura se houver uma live em andamento ou dados já preenchidos na planilha principal.
- `Resumo final`: mostra cada cliente em destaque, os códigos das peças, tempos, suplentes, checkbox por peça e total da cliente. No final, mostra clientes, peças vendidas e total vendido.
- `Mensagens clientes`: cria mensagens prontas para enviar às clientes com peças arrematadas, total e instruções de pagamento.
- `Exportar Excel`: cria um `.xlsx` com índice das peças, filtros nas colunas e as abas `Vendas`, `Resumo por cliente`, `Suplentes`, `Dados da live` e `Histórico de lives`.
- `Imprimir todos`: envia o resumo, a planilha e as peças não vendidas para a impressora padrão, ignorando automaticamente os itens que não tiverem dados.
- `Imprimir resumo`: envia um resumo por cliente com checkbox, nome da cliente em negrito, suplente na mesma linha da peça e totais finais. Quando não couber em uma folha, continua em páginas seguintes com fonte legível.
- `Imprimir planilha`: imprime a planilha em A4, retrato, ajustada para caber em uma página.
- `Imprimir não vendidas`: imprime apenas as peças preenchidas que ainda não têm cliente, também com checkbox.
- `Nova live / Limpar tudo`: apaga os dados e zera o contador para começar outra live.

## Logo e carregamento

Os arquivos de marca ficam na pasta `assets`.

- Salve a logo original enviada como `assets/logo_original.png`.
- Rode `python gerar_assets_logo.py`.
- `logo_round.png`: logo original ajustado para o cabeçalho do app.
- `install_splash.png`: tela de carregamento do instalador.
- `app_icon.ico`: ícone do aplicativo no Windows.

## Remover cliente ou suplente

O `X` aparece somente quando o mouse está em cima de uma célula de cliente ou suplente preenchida.

- `X` em `Cliente`: remove a titular, promove `Suplente` para `Cliente` e limpa `Suplente`.
- `X` em `Suplente`: limpa apenas a suplente.
