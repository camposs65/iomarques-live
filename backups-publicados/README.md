# Backups das lives

Copias incluidas a pedido do responsavel pelo projeto. Cada pasta corresponde a uma coleta; novos backups locais nao sao enviados automaticamente.

## Coleta de 2026-09-16

- `2026-09-16/aplicativo/`: historico completo, ultima planilha salva e backups do executavel utilizado pela loja.
- `2026-09-16/desenvolvimento/`: planilha e backups antigos da pasta do codigo-fonte, preservados separadamente.
- `2026-09-16/inventario.json`: lista dos arquivos copiados, tamanhos e hashes SHA-256.

## Restauracao

1. Feche o aplicativo e guarde uma copia dos dados atuais antes de substituir qualquer arquivo.
2. Para restaurar o historico, use `aplicativo/historico_lives.json` e coloque-o ao lado de `IoMarques Brecho.exe`, normalmente na pasta `dist`.
3. Para restaurar a ultima planilha, use `aplicativo/live_atual.json` no mesmo local.
4. Para usar uma versao anterior, escolha um arquivo de `aplicativo/backups/` e copie-o para o mesmo local com o nome `historico_lives.json` ou `live_atual.json`, conforme o tipo.
5. Abra o aplicativo e confira a planilha e o historico.

Os arquivos originais do computador foram mantidos. Esta coleta contem apenas dados das lives, sem configuracoes de integracao ou sessoes de acesso.
