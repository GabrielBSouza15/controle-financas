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

## Consultar e exportar despesas

Na Visão geral, escolha o mês e Eu, Pai/Mãe ou Todos. Os filtros das faturas são independentes dos indicadores de orçamento. A lista Contas a cadastrar e pagar reúne recorrências e faturas pendentes do mês atual e do seguinte; confirmar uma previsão não significa pagá-la.

Abra Ver despesas e use Exportar PDF para baixar o relatório A4 completo, mesmo quando a tela tem várias páginas. O documento identifica os filtros e as estimativas, separa PIX/Débito e cartões e informa o total previsto do mês seguinte. Todos inclui os responsáveis e seus subtotais. O relatório é gerado localmente em memória, sem gravar alterações financeiras.

## Editar e informar pagamentos

Receitas e despesas permite Editar e Remover, com confirmação e histórico das versões. Remover retira o compromisso dos totais ativos e preserva a evidência; não registra devolução de dinheiro. Despesas próprias via PIX/Débito pagas sem conta mantêm essa condição ao editar se continuarem no mesmo tipo, responsável e meio, com data não futura. Outros vínculos financeiros e faturas fechadas preservam seus bloqueios.

Marcar fatura paga pede a data efetiva e quita apenas o restante dos compromissos lançados. Estimativas e compras acrescentadas depois permanecem pendentes. A indicação sem conta não cria movimento bancário, outra despesa ou repasse familiar. A evidência é imutável e não oferece desmarcação. A API com conta explícita continua disponível em modo separado.

Motivos e observações são opcionais; a auditoria registra Não informado quando não houver texto. Confirmações explícitas de compromissos distintos continuam necessárias quando houver possível sobreposição histórica. O Fluxo de caixa apresenta as datas mais recentes primeiro.

## Atualizar um banco de exercício existente

Esta versão usa o esquema `0017_bill_payment_marks`. A abertura verifica a revisão, sem migrar automaticamente. Para um banco de exercício de versão anterior, encerre o servidor, faça backup consistente e confira a restauração para outro arquivo antes de executar `python -m app.persistence upgrade --database CAMINHO_DO_BANCO`. Confira a revisão com `python -m app.persistence status --database CAMINHO_DO_BANCO`. O preparador vazio/fictício permanece exclusivo para destinos inexistentes.

## Preservar o trabalho local

Os bancos, planilhas, backups e dados de importação são ignorados pelo Git. Faça cópias pela funcionalidade da aplicação e confira a restauração para um arquivo novo. Não compartilhe uma cópia preenchida com dados reais como exemplo. Mantenha SQLite ativo fora de sincronização e não exponha o servidor à internet.

O preparador aceita apenas um destino inexistente. Para recomeçar um exercício, escolha outro nome de banco e use esse caminho também na abertura. Abrir a aplicação não preenche nem migra um banco automaticamente.
