# Cenário fictício

Execute `python -m scripts.prepare_database demo --database data/demo.db` na raiz do projeto, com o ambiente instalado. A opção `--month AAAA-MM` define o mês de referência; sem ela, utiliza o mês local atual.

| Lançamento | Valor inventado | Forma |
|---|---|---|
| Bolsa de estudos fictícia | R$ 2.800,00 | Receita PIX/Débito |
| Mercado de exemplo | R$ 186,40 | Crédito em uma parcela |
| Curso de exemplo | R$ 720,00 | Crédito em três parcelas |
| Transporte de exemplo | R$ 18,50 | PIX/Débito |
| Compra familiar de exemplo | R$ 360,00 | Crédito em duas parcelas, responsabilidade Pai/Mãe |

Previsões mensais: internet de R$ 99,00, bolsa de R$ 2.800,00 (a partir do mês seguinte) e aluguel de R$ 850,00. Todos os nomes, valores e datas do cenário são construídos pelo gerador. Nenhum pagamento ou repasse é inventado a partir de uma previsão. O indicador de reinvestimento começa em zero.

Para experimentar a modalidade sem conteúdo, use `python -m scripts.prepare_database empty --database data/empty.db`. Ela mantém todas as tabelas de negócio vazias até uma ação explícita do usuário.
