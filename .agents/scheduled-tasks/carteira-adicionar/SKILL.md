---
name: carteira-adicionar
description: Adicionar nova operacao (compra ou venda) na planilha de carteira
---

Voce e um assistente de carteira de investimentos. Sua tarefa e adicionar uma nova operacao na planilha localizada em: C:\Users\Jefferson\Desktop\Carteira_Investimentos.xlsx

Siga estes passos:

1. Pergunte ao usuario o que ele quer fazer:
   - (1) Registrar nova COMPRA
   - (2) Registrar VENDA de uma posicao existente

2. Se for COMPRA, pergunte:
   - Qual o ativo? (ex: MU, AAPL, TSLA)
   - Qual a data da compra? (DD/MM/AAAA)
   - Qual o preco de compra?
   - Qual a quantidade?

3. Se for VENDA, pergunte:
   - Qual o ativo que foi vendido?
   - Qual a data da venda? (DD/MM/AAAA)
   - Qual o preco de venda?

4. Leia o arquivo Excel atual com openpyxl (load_workbook).
   - Para COMPRA: encontre a primeira linha vazia entre as linhas 3 e 22 (coluna A vazia) e insira os dados: Ativo em A, Data Compra em B (formato YYYY-MM-DD), Preco em C, QTD em D. Nao sobrescreva linhas existentes.
   - Para VENDA: encontre a linha onde o ativo (coluna A) corresponde ao informado e a coluna F (Data Venda) esta vazia. Preencha F com a data de venda e G com o preco de venda.

5. Salve o arquivo.

6. Confirme ao usuario o que foi registrado e mostre o resultado calculado (lucro ou perda se for venda).

Importante: use Python com openpyxl para ler e escrever o arquivo. Execute o codigo via Bash/PowerShell. O arquivo esta em: C:\Users\Jefferson\Desktop\Carteira_Investimentos.xlsx
