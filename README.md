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

Esses arquivos não devem ir para Git/GitHub; eles são dados reais de operação da loja.

## Como usar durante a live

- Clique em `Iniciar live` no momento em que a gravação da live começar.
- O contador fica rodando no canto superior direito.
- Ao preencher `Valor` ou `Código`, o app salva automaticamente o `Tempo` daquela peça.
- Clique em `Finalizar live` quando a live terminar.
- Ao finalizar, a live entra no histórico automaticamente.
- Clique em uma célula para editar.
- Quando o campo `Cliente` estiver preenchido, a linha fica levemente verde.
- Digitar na última linha vazia cria outra linha automaticamente.
- Os dados ficam salvos automaticamente no arquivo `live_atual.json`.
- Ao digitar `39,90` no valor, o app transforma em `R$ 39,90`.
- Use as setas do teclado para navegar entre as células.
- Passe o mouse sobre `Cliente`, `Suplente 1` ou `Suplente 2` para mostrar o `X` de remoção.

Exemplo da IoMarques Brechó:

| Valor | Código | Tempo | Cliente | Suplente 1 | Suplente 2 |
| --- | --- | --- | --- | --- | --- |
| R$ 39,90 | 345 | 00:12:08 | chimabyliz | anapaula | |

## Botões

- Antes de começar: aparecem `Iniciar live`, `Resumo final`, `Exportar Excel`, `Imprimir resumo`, `Histórico de lives` e `Nova live / Limpar tudo`.
- Durante a live: aparece apenas `Finalizar live`.
- Depois de finalizar: aparecem `Resumo final`, `Exportar Excel`, `Imprimir resumo`, `Histórico de lives` e `Nova live / Limpar tudo`.

Depois de finalizar:

- `Histórico de lives`: mostra as lives finalizadas, duração, peças, clientes e total.
- `Resumo final`: mostra cada cliente, as peças, o tempo de aparição e o total a pagar.
- `Exportar Excel`: cria um `.xlsx` com as abas `Vendas`, `Resumo por cliente`, `Dados da live` e `Histórico de lives`.
- `Imprimir resumo`: envia o relatório da live para a impressora padrão.
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

- `X` em `Cliente`: remove a titular, promove `Suplente 1` para `Cliente` e promove `Suplente 2` para `Suplente 1`.
- `X` em `Suplente 1`: remove a suplente 1 e promove `Suplente 2`.
- `X` em `Suplente 2`: limpa apenas a suplente 2.
