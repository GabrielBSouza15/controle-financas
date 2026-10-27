import {formatCents, formatSignedCents} from "/static/money.mjs";
import {createCardCharts} from "/static/outlook.js";
import {createBudgetCharts} from "/static/budget_charts.js";
import {widthPercent} from "/static/dashboard_chart.mjs";

export function createDashboardView({api, dateText, localDate, showEntry, showInvoice, showOccurrence, showHistory, notify}) {
  const $ = id => document.getElementById(id);
  const state = {sequence: 0, detailSequence: 0, month: null, responsible: "self", detail: null, offset: 0};
  const labels = {all_income:"Receitas previstas · Eu e Pai/Mãe",expense:"Despesas",income:"Receitas",result:"Sobra ou falta prevista",card_expense:"Despesas no cartão",all_cards:"Faturas previstas · todos os cartões",self_expense: "Despesas pessoais", self_income: "Receitas pessoais", self_result: "Sobra ou falta prevista", parents_expense: "Compromissos de Pai/Mãe", cash: "Dinheiro movimentado", card: "Fatura prevista", all_expense: "Despesas previstas · Eu e Pai/Mãe"};
  const kinds = {invoice_payment: "Pagamento ao banco", repayment: "Repasse de Pai/Mãe", expense_payment: "Despesa paga", income_receipt: "Receita recebida", refund: "Devolução efetiva", transfer: "Transferência própria"};
  const reviews = {missing: "A conferir", stale: "Conferência desatualizada", difference: "Divergência registrada", matched: "Total conferido"};
  const monthText = iso => `${iso.slice(5,7)}/${iso.slice(0,4)}`;
  function cell(row, text, className = "") {
    const td = document.createElement("td"); td.textContent = text; td.className = className; row.append(td); return td;
  }
  function button(parent, text, fn) {
    const element = document.createElement("button"); element.type = "button"; element.className = "link-button"; element.textContent = text;
    element.addEventListener("click", () => Promise.resolve(fn()).catch(error => notify(error.message, true))); parent.append(element); return element;
  }
  function split(registered, forecast) {return `${formatCents(registered)} já lançados · ${formatCents(forecast)} em estimativas`;}
  const budgetCharts=createBudgetCharts((metric,month,filters={})=>openDetails(metric,month,filters));
  const cardCharts=createCardCharts((metric,month,filters,title)=>openDetails(metric,month,filters,title));
  function render(data) {
    if (!data.selected_budget || !data.selected_months) throw new Error("Para carregar a atualização, encerre a aplicação com Ctrl+C no terminal e abra novamente pelo ícone Controle de finanças.");
    const row = data.selected_budget; state.month = data.month.slice(0,7); state.responsible = data.responsible_code;
    const person = state.responsible === "parents" ? "Pai/Mãe" : "Eu";
    $("dashboard-expense-title").textContent = state.responsible === "parents" ? "Despesas previstas de Pai/Mãe" : "Minhas despesas previstas";
    $("dashboard-income-title").textContent = state.responsible === "parents" ? "Receitas previstas de Pai/Mãe" : "Minhas receitas previstas";
    $("dashboard-chart-title").textContent = `Receitas e despesas previstas · ${person}`;
    $("dashboard-chart-person").textContent = person;
    $("dashboard-category-title").textContent = `Despesas por categoria · ${person}`;
    $("dashboard-expense").textContent = formatCents(row.total_expense_cents);
    $("dashboard-income").textContent = formatCents(row.total_income_cents);
    $("dashboard-result").textContent = formatSignedCents(row.result_cents);
    $("dashboard-result").classList.toggle("money-expense", row.result_cents < 0);
    $("dashboard-result").classList.toggle("money-income", row.result_cents > 0);
    $("dashboard-result-note").textContent = row.total_income_cents === 0 ? "Sem receitas cadastradas para este responsável no mês. Resultado dos registros; não é saldo bancário." : "Receitas menos despesas cadastradas. Não é saldo bancário.";
    $("dashboard-cash").textContent = formatSignedCents(data.budget.cash_variation_cents);
    $("dashboard-expense-parts").textContent = split(row.registered_expense_cents, row.forecast_expense_cents);
    $("dashboard-income-parts").textContent = split(row.registered_income_cents, row.forecast_income_cents);
    $("dashboard-cash-parts").textContent = `${formatCents(data.budget.cash_in_cents)} de entradas · ${formatCents(data.budget.cash_out_cents)} de saídas efetivas. Sem saldo inicial.`;
    $("dashboard-empty").hidden = !data.empty;
    $("dashboard-range").textContent = `${monthText(data.months[0].month)} a ${monthText(data.months.at(-1).month)}`;
    budgetCharts.render(data);
    state.outlook = data.outlook;
    cardCharts.render(data.outlook);
    $("dashboard-categories").replaceChildren();
    for (const category of data.category_chart) {
      const li = document.createElement("li");
      const open = button(li, "", () => openDetails("expense", state.month, category.filters, category.name));
      open.className = "dashboard-category-button";
      const name = document.createElement("span"); name.textContent = category.name;
      const value = document.createElement("strong"); value.textContent = formatCents(category.total_cents); open.append(name, value);
      const share=document.createElement("span");share.className="category-share";share.textContent=`${Math.floor(category.share_basis_points/100)},${String(category.share_basis_points%100).padStart(2,"0")}% do mês`;open.append(share);
      const track = document.createElement("div"); track.className = "dashboard-track";
      for (const phase of ["registered", "forecast"]) {
        const segment = document.createElement("span"); segment.className = `dashboard-segment expense ${phase}`;
        segment.style.width = widthPercent(category[`${phase}_cents`], row.total_expense_cents); track.append(segment);
      }
      const sub = document.createElement("small"); sub.textContent = split(category.registered_cents, category.forecast_cents);
      li.append(track, sub); $("dashboard-categories").append(li);
    }
    if (!data.categories.length) {const li = document.createElement("li"); li.textContent = "Sem despesas deste responsável neste mês."; $("dashboard-categories").append(li);}
    $("dashboard-invoices").replaceChildren();
    for (const invoice of data.invoices) {
      const tr = document.createElement("tr"), name = cell(tr, invoice.card_name);
      const sub = document.createElement("small"); sub.textContent = `${invoice.status === "closed" ? "Fechada" : "Aberta"} · vence ${dateText(invoice.due_date)}`; name.append(sub);
      for (const field of ["statement_total_cents", "self_cents", "parents_cents", "issuer_paid_cents", "issuer_remaining_cents"]) cell(tr, formatSignedCents(invoice[field]), "number");
      cell(tr, reviews[invoice.reconciliation_state]); button(cell(tr, ""), "Ver fatura", () => showInvoice(invoice.id)); $("dashboard-invoices").append(tr);
    }
    $("dashboard-history-cycles").replaceChildren();
    for (const cycle of data.historical_invoices) {
      const tr = document.createElement("tr");
      cell(tr, cycle.card_name + " · ciclo simulado");
      for (const field of ["total_cents", "self_cents", "parents_cents", "paid_undated_cents", "new_payment_cents", "issuer_remaining_cents"]) cell(tr, formatCents(cycle[field]), "number");
      cell(tr, ({missing:"A conferir",matched:"Total conferido",stale:"Conferência desatualizada",difference:"Diferença registrada"})[cycle.reconciliation.state]); $("dashboard-history-cycles").append(tr);
    }
    $("dashboard-history-wrap").hidden = !data.historical_invoices.length;
    $("dashboard-no-invoices").hidden = !!data.invoices.length; $("dashboard-invoice-wrap").hidden = !data.invoices.length;
    $("dashboard-invoice-count").textContent = `${data.invoices.length} fatura(s) · ${monthText(data.month)}`;
    $("dashboard-quality").replaceChildren();
    const q = data.quality;
    for (const text of [
      ...(q.forecast_items ? [`${q.forecast_items} previsões mensais aguardam confirmação.`] : []),
      ...(q.historical_items ? [`${q.historical_items} registros vieram da planilha; ${q.historical_simulated_cycles} ciclos ainda são previsões. Pagamentos antigos não têm data e repasses antigos continuam desconhecidos.`] : []),
      ...(q.uncategorized_expense_items ? [`${q.uncategorized_expense_items} ${q.uncategorized_expense_items === 1 ? "despesa está" : "despesas estão"} sem categoria.`] : []),
      ...(q.missing_description_items ? [`${q.missing_description_items} registros estão sem descrição.`] : []),
      ...(q.invoice_ids_needing_review.length ? [`${q.invoice_ids_needing_review.length} faturas precisam de conferência com o banco.`] : []),
      ...(q.missing_card_rule_items ? [`${q.missing_card_rule_items} previsões precisam de uma regra de fechamento do cartão.`] : []),
      ...(data.scope.legacy_batches_excluded ? ["A importação antiga de teste fica fora destes valores."] : []),
      ...(!q.forecast_items && !q.historical_items && !q.uncategorized_expense_items && !q.missing_description_items && !q.invoice_ids_needing_review.length && !q.missing_card_rule_items ? ["Nenhum aviso nos registros deste mês."] : []),
    ]) {const li = document.createElement("li"); li.textContent = text; $("dashboard-quality").append(li);}
  }
  async function refresh() {
    const sequence = ++state.sequence;
    $("dashboard-loading").hidden = false; $("dashboard-content").hidden = true; $("dashboard-error").hidden = true;
    try {
      const params = new URLSearchParams(new FormData($("dashboard-filters")));
      if ($("outlook-card-filter").value) params.set("chart_card_id", $("outlook-card-filter").value);
      const data = await api(`/dashboard?${params}`);
      if (sequence !== state.sequence) return;
      render(data); $("dashboard-content").hidden = false;
    } catch (error) {if (sequence === state.sequence) {$("dashboard-error").textContent = error.message; $("dashboard-error").hidden = false;}}
    finally {if (sequence === state.sequence) $("dashboard-loading").hidden = true;}
  }
  async function openDetails(metric, month = state.month, filters = {}, categoryName = "") {
    const comparison = ["card", "all_cards", "card_expense", "all_expense", "all_income"].includes(metric);
    const person = metric === "cash" ? "self" : metric === "parents_expense" ? "parents" : state.responsible;
    state.detail = {metric, month, ...(!comparison ? {responsible_code: person} : {}), ...filters}; state.offset = 0;
    $("dashboard-detail-title").textContent = `${labels[metric]}${!comparison ? " · " + (person === "parents" ? "Pai/Mãe" : "Eu") : ""}${categoryName ? " · " + categoryName : ""} · ${monthText(month)}`;
    if (!$("dashboard-detail-dialog").open) $("dashboard-detail-dialog").showModal();
    await loadDetails();
  }
  async function loadDetails() {
    const sequence = ++state.detailSequence;
    $("dashboard-detail-rows").replaceChildren(); $("dashboard-detail-error").hidden = true;
    $("dashboard-detail-total").textContent = "Buscando os detalhes…"; $("dashboard-detail-empty").hidden = true;
    $("dashboard-detail-previous").disabled = true; $("dashboard-detail-next").disabled = true;
    try {
      const params = new URLSearchParams({...state.detail, limit: 50, offset: state.offset});
      const path = ["card", "all_cards", "card_expense", "all_expense", "all_income"].includes(state.detail.metric) ? "/dashboard/outlook/details" : "/dashboard/details";
      const data = await api(`${path}?${params}`);
      if (sequence !== state.detailSequence) return;
      const cash = data.metric === "cash";
      $("dashboard-detail-total").textContent = `${formatSignedCents(data.total_cents)} · soma de ${data.total} item(ns), em todas as páginas.`;
      $("dashboard-detail-note").textContent = cash ? "Entradas positivas, saídas negativas. Transferências aparecem nas duas contas e somam zero. Repasses não são receita pessoal. Esta composição não inclui saldo inicial." : ["card","all_cards"].includes(data.metric) ? "Total previsto da fatura, com Eu e Pai/Mãe, histórico e estimativas. Créditos entre faturas alteram a cobrança. Pagamentos não diminuem este total; consulte o restante a pagar em Cartões e faturas." : data.metric === "card_expense" ? "Despesas do cartão selecionado, com Eu e Pai/Mãe. Créditos entre faturas mudam a cobrança, sem reduzir a despesa novamente." : data.metric === "all_income" ? "Receitas lançadas e estimativas de Eu e Pai/Mãe, em todos os meios. Estimativa não confirma recebimento; repasses não são receitas pessoais." : data.metric === "all_expense" ? "Despesas de Eu e Pai/Mãe em todos os meios de pagamento. No cartão, vale o mês da fatura. Estimativas ainda podem mudar. Pagar uma fatura não cria outra despesa." : "Valores por parcela/mês da fatura, já com ajustes. Estimativa não é pagamento. Para resultado, despesas contribuem com sinal negativo; receitas com sinal positivo.";
      for (const item of data.items) {
        const tr = document.createElement("tr");
        const name = cell(tr, cash ? item.reason : item.description || "Descrição ausente");
        const sub = document.createElement("small");
        sub.textContent = item.source === "statement_credit" ? `Fatura de ${monthText(item.month)}` : cash ? `${dateText(item.occurred_on)} · ${item.account_name} · registro #${item.id}` : `${dateText(item.scheduled_on)} · ${(item.category_name || "Crédito entre faturas")} · ${item.card_name || "PIX/Débito"} · ${item.number}/${item.installment_count}`; name.append(sub);
        if (item.parents_repayment) {
          const timing=document.createElement("small");
          timing.textContent=item.parents_repayment === "pending" ? "Repasse pendente: orçamento no mês seguinte à despesa." : "Repasse informado como recebido; caixa registrado separadamente.";
          name.append(timing);
        }
        cell(tr, item.source === "statement_credit" ? "Crédito entre faturas" : cash ? (kinds[item.kind]||("Histórico: "+(kinds[item.source_kind]||item.kind.replace("history_","")))) : item.phase === "forecast" ? "Estimativa mensal" + (item.responsible_code === "parents" ? " · Pai/Mãe" : " · Eu") : item.source === "history" ? `Planilha · linha ${item.source_row} · ${item.responsible_code === "parents" ? "Pai/Mãe" : "Eu"}` : "Novo lançamento" + (item.responsible_code === "parents" ? " · Pai/Mãe" : " · Eu"));
        cell(tr, formatSignedCents(item.contribution_cents), "number"); const actions = cell(tr, "");
        if (item.invoice_id && item.source === "statement_credit") button(actions, "Ver fatura", () => { $("dashboard-detail-dialog").close(); return showInvoice(item.invoice_id); });
        else if (item.entry_id) button(actions, "Ver lançamento", () => {$("dashboard-detail-dialog").close(); return showEntry(item.entry_id);});
        else if (item.history_record_id) button(actions, "Ver origem histórica", () => {$("dashboard-detail-dialog").close(); return showHistory(item.history_record_id);});
        else if (item.series_id) button(actions, "Ver ocorrência", () => {$("dashboard-detail-dialog").close(); return showOccurrence(item.series_id, item.reference_month.slice(0,7));});
        $("dashboard-detail-rows").append(tr);
      }
      $("dashboard-detail-empty").hidden = !!data.total;
      $("dashboard-detail-page").textContent = data.total ? `${Math.min(state.offset+1, data.total)}–${Math.min(state.offset+data.items.length, data.total)} de ${data.total}` : "Nenhum item";
      $("dashboard-detail-previous").disabled = state.offset === 0; $("dashboard-detail-next").disabled = state.offset + 50 >= data.total;
    } catch (error) {if (sequence === state.detailSequence) {$("dashboard-detail-total").textContent = "Consulta não concluída."; $("dashboard-detail-error").textContent = error.message; $("dashboard-detail-error").hidden = false;}}
  }
  const filters = $("dashboard-filters");
  const preferenceKey = "finance-dashboard-filters";
  const validMonth = value => typeof value === "string" && /^(?!0000)\d{4}-(0[1-9]|1[0-2])$/.test(value);
  const validMonths = value => ["3", "6", "12"].includes(value);
  filters.elements.month.value = localDate().slice(0,7);
  try {
    const saved = JSON.parse(localStorage.getItem(preferenceKey));
    if (validMonth(saved?.month)) filters.elements.month.value = saved.month;
    if (validMonths(saved?.months)) filters.elements.months.value = saved.months;
    if (["self", "parents"].includes(saved?.responsible_code)) filters.elements.responsible_code.value = saved.responsible_code;
  } catch {} // Armazenamento bloqueado ou inválido não impede abrir o painel.
  function rememberFilters() {
    const month = filters.elements.month.value, months = filters.elements.months.value;
    if (!validMonth(month) || !validMonths(months)) return;
    try { localStorage.setItem(preferenceKey, JSON.stringify({month, months, responsible_code: filters.elements.responsible_code.value})); } catch {}
  }
  filters.addEventListener("change", event => {rememberFilters(); if (event.target.name === "responsible_code") refresh();});
  filters.addEventListener("submit", event => {event.preventDefault(); rememberFilters(); refresh();});
  for (const element of document.querySelectorAll("[data-dashboard-metric]")) element.addEventListener("click", () => openDetails(element.dataset.dashboardMetric));
  $("dashboard-detail-previous").addEventListener("click", () => {state.offset = Math.max(0, state.offset-50); loadDetails();});
  $("dashboard-detail-next").addEventListener("click", () => {state.offset += 50; loadDetails();});
  $("dashboard-detail-dialog").addEventListener("close", () => {++state.detailSequence;});
  $("outlook-card-filter").addEventListener("change", refresh);
  $("outlook-labels").addEventListener("change", () => {budgetCharts.redraw();cardCharts.redraw();});
  return {refresh};
}