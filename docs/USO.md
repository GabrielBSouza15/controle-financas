# Orientações de uso

## Começar sem dados

Crie o banco no modo `empty` e abra o servidor indicando o mesmo caminho. Na interface, prepare os cadastros iniciais informando a data de vigência. Os cartões didáticos possuem fechamento/vencimento no dia 05 (Nubank) e no dia 11 (Mercado Pago/Livre); compras no fechamento passam ao próximo ciclo no primeiro e permanecem no ciclo atual no segundo. Confira a configuração antes de utilizá-la em outro cenário.

Cadastre categorias e receitas/despesas, selecione responsável, data, meio de pagamento e parcelas. Crédito exige cartão; PIX/Débito não exige conta na interface. Marcar um compromisso pago é uma informação explícita, não uma consequência automática da data.

## Explorar o exemplo

Crie o modo `demo` e selecione o mês de referência na Visão geral. Alterne Eu/Pai-Mãe, abra os detalhes dos gráficos e consulte as parcelas nos ciclos dos cartões. Em Gastos previstos, explore as recorrências. Uma previsão aparece como estimativa até ser confirmada; confirmar não significa pagar.

São cinco lançamentos e três recorrências. A bolsa registrada pertence ao mês de referência; sua previsão mensal começa no mês seguinte, para não duplicar a receita do primeiro mês.

## Interpretar valores

- Orçamento: compromissos e previsões do período, separados por responsável.
- Fatura: cobrança do cartão; pagamento não duplica despesa.
- Caixa por conta: eventos efetivos informados, sem saldo absoluto presumido.
- Reinvestimento: controle manual independente, começando em zero.

Use o detalhamento para conferir as origens. Registros pagos sem conta não criam movimentos bancários fictícios. Repasse familiar não altera quem é responsável pela despesa.

## Preservar o trabalho local

Os bancos, planilhas, backups e dados de importação são ignorados pelo Git. Faça cópias pela funcionalidade da aplicação e confira a restauração para um arquivo novo. Não compartilhe uma cópia preenchida com dados reais como exemplo. Mantenha SQLite ativo fora de sincronização e não exponha o servidor à internet.

O preparador aceita apenas um destino inexistente. Para recomeçar um exercício, escolha outro nome de banco e use esse caminho também na abertura. Abrir a aplicação não preenche nem migra um banco automaticamente.
