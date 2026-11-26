import {formatCents, formatSignedCents} from "/static/money.mjs";
import {createMonthlyCards} from "/static/monthly_cards.js";
import {createBudgetCharts} from "/static/budget_charts.js";
import {widthPercent} from "/static/dashboard_chart.mjs";

export function createDashboardView({api, dateText, localDate, showEntry, showInvoice, showOccurrence, showHistory, showBill, notify}) {
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
  const phaseNames = {all: "Lançados e estimados", registered: "Só lançados", forecast: "Só estimados"};
  const budgetCharts = createBudgetCharts(openDetails);
  const cards = createMonthlyCards($("dashboard-monthly-cards"), {api, localDate, dateText, open: openCard});
  let chartData;

  function openCard({card, month, person, phase}) {
    return openDetails("card", month, {card_id: card.id, responsible_code: person, phase}, card.name);
  }
  function render(data) {
    if (!data.selected_budget || !data.chart_variants) throw new Error("Para carregar a atualização, encerre a aplicação com Ctrl+C no terminal e abra novamente pelo ícone Controle de finanças.");
    const row = data.selected_budget; state.month = data.month.slice(0,7); state.responsible = data.responsible_code;
    $("dashboard-expense-title").textContent = state.responsible === "all" ? "Despesas previstas de todos" : state.responsible === "parents" ? "Despesas previstas de Pai/Mãe" : "Minhas despesas previstas";
    $("dashboard-income-title").textContent = state.responsible === "all" ? "Receitas previstas de todos" : state.responsible === "parents" ? "Receitas previstas de Pai/Mãe" : "Minhas receitas previstas";
    $("dashboard-expense").textContent = formatCents(row.total_expense_cents);
    $("dashboard-income").textContent = formatCents(row.total_income_cents);
    $("dashboard-result").textContent = formatSignedCents(row.result_cents);
    $("dashboard-result").classList.toggle("money-expense", row.result_cents < 0);
    $("dashboard-result").classList.toggle("money-income", row.result_cents > 0);
    $("dashboard-result-note").textContent = row.total_income_cents === 0 ? "Sem receitas cadastradas para a seleção no mês. Resultado dos registros; não é saldo bancário." : "Receitas menos despesas cadastradas. Não é saldo bancário.";
    $("dashboard-cash").textContent = formatSignedCents(data.budget.cash_variation_cents);
    $("dashboard-expense-parts").textContent = split(row.registered_expense_cents, row.forecast_expense_cents);
    $("dashboard-income-parts").textContent = split(row.registered_income_cents, row.forecast_income_cents);
    $("dashboard-cash-parts").textContent = `${formatCents(data.budget.cash_in_cents)} de entradas · ${formatCents(data.budget.cash_out_cents)} de saídas efetivas. Sem saldo inicial.`;
    $("dashboard-empty").hidden = !data.empty;
    const tasks = data.pending_tasks;
    $("dashboard-tasks-period").textContent = `${monthText(tasks.month_from)} a ${monthText(tasks.month_to)}`;
    $("dashboard-tasks-count").textContent = `${tasks.count} ${tasks.count === 1 ? "pendência" : "pendências"}`;
    $("dashboard-tasks-empty").hidden = tasks.count > 0;
    const list = $("dashboard-tasks-rows"), rows = document.createDocumentFragment();
    for (const item of tasks.items) {
      const recurring = item.kind === "recurrence";
      const row = document.createElement("li");
      const identity = document.createElement("div");
      const name = document.createElement("strong");
      name.textContent = item.description;
      const note = document.createElement("small");
      const status = recurring ? item.entry_kind === "income" ? "Receita a confirmar" : "Despesa a confirmar" : "Fatura a pagar";
      const day = recurring ? dateText(item.scheduled_on) : monthText(item.reference_month);
      const person = item.responsible_code === "parents" ? "Pai/Mãe" : "Eu";
      note.textContent = recurring ? [day, status, person, item.card_name].filter(Boolean).join(" · ") : `${day} · ${status}`;
      identity.append(name, note);
      const amount = document.createElement("span");
      amount.className = "task-amount";
      amount.textContent = formatCents(item.amount_cents);
      row.append(identity, amount);
      button(row, recurring ? "Ver previsão" : "Registrar pagamento", () => recurring ? showOccurrence(item.series_id, item.reference_month.slice(0,7)) : showBill(item));
      rows.append(row);
    }
    list.replaceChildren(rows);
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
  function renderCategories() {
    if (!chartData) return;
    const {month: ref, responsible_code} = chartData;
    const month = ref.slice(0,7), phase = $("category-phase").value;
    const {categories, months} = chartData.variants[phase];
    $("dashboard-categories").replaceChildren();
    const visiblePhases = phase === "all" ? ["registered", "forecast"] : [phase];
    for (const category of categories) {
      const li = document.createElement("li");
      const open = button(li, "", () => openDetails("expense", month, {...category.filters, phase, responsible_code}, category.name));
      open.className = "dashboard-category-button";
      const name = document.createElement("span"); name.textContent = category.name;
      const value = document.createElement("strong"); value.textContent = formatCents(category.total_cents); open.append(name, value);
      const share=document.createElement("span");share.className="category-share";share.textContent=`${Math.floor(category.share_basis_points/100)},${String(category.share_basis_points%100).padStart(2,"0")}% do mês`;open.append(share);
      const track = document.createElement("div"); track.className = "dashboard-track";
      for (const itemPhase of visiblePhases) {
        const segment = document.createElement("span"); segment.className = `dashboard-segment expense ${itemPhase}`;
        segment.style.width = widthPercent(category[`${itemPhase}_cents`], months[0].total_expense_cents); track.append(segment);
      }
      li.append(track);
      if (phase === "all") {
        const sub = document.createElement("small");
        sub.textContent = split(category.registered_cents, category.forecast_cents);
        li.append(sub);
      }
      $("dashboard-categories").append(li);
    }
    if (!categories.length) {const li = document.createElement("li"); li.textContent = "Sem despesas para a seleção neste mês."; $("dashboard-categories").append(li);}
  }
  async function refreshBudget() {
    if (!$("dashboard-filters").checkValidity()) return;
    const sequence = ++state.sequence;
    $("dashboard-loading").hidden = false;
    $("dashboard-error").hidden = true;
    const params = new URLSearchParams(new FormData($("dashboard-filters")));
    params.set("months", "12");
    params.set("include_chart_variants", "true");
    try {
      const data = await api(`/dashboard?${params}`);
      if (sequence !== state.sequence) return;
      render(data);
      chartData = {month: data.month, responsible_code: data.responsible_code, variants: data.chart_variants};
      budgetCharts.render(chartData);
      renderCategories();
      $("dashboard-content").hidden = false;
    } catch (error) {
      if (sequence === state.sequence) {
        $("dashboard-error").textContent = error.message;
        $("dashboard-error").hidden = false;
      }
    } finally {
      if (sequence === state.sequence) $("dashboard-loading").hidden = true;
    }
  }
  async function openDetails(metric, month = state.month, filters = {}, categoryName = "") {
    const comparison = ["card", "all_cards", "card_expense", "all_expense", "all_income"].includes(metric);
    const person = filters.responsible_code ?? (metric === "cash" ? "self" : metric === "parents_expense" ? "parents" : state.responsible);
    state.detail = {metric, month, ...(!comparison ? {responsible_code: person} : {}), ...filters}; state.offset = 0;
    const title = metric === "card" ? "Fatura" : metric === "result" ? "Sobra ou falta" : labels[metric];
    const phaseLabel = filters.phase && filters.phase !== "all" ? ` · ${phaseNames[filters.phase]}` : "";
    $("dashboard-detail-title").textContent = `${title}${!comparison || metric === "card" ? " · " + (person === "all" ? "Todos" : person === "parents" ? "Pai/Mãe" : "Eu") : ""}${categoryName ? " · " + categoryName : ""} · ${monthText(month)}${phaseLabel}`;
    if (!$("dashboard-detail-dialog").open) $("dashboard-detail-dialog").showModal();
    $("dashboard-export-pdf").hidden = !["expense", "self_expense", "parents_expense"].includes(metric);
    await loadDetails();
  }
  async function loadDetails() {
    const sequence = ++state.detailSequence;
    $("dashboard-export-pdf").disabled = true;
    $("dashboard-detail-rows").replaceChildren(); $("dashboard-detail-error").hidden = true;
    $("dashboard-detail-total").textContent = "Buscando os detalhes…"; $("dashboard-detail-empty").hidden = true;
    $("dashboard-detail-previous").disabled = true; $("dashboard-detail-next").disabled = true;
    try {
      const params = new URLSearchParams({...state.detail, limit: 50, offset: state.offset});
      const path = ["card", "all_cards", "card_expense", "all_expense", "all_income"].includes(state.detail.metric) ? "/dashboard/outlook/details" : "/dashboard/details";
      const data = await api(`${path}?${params}`);
      if (sequence !== state.detailSequence) return;
      $("dashboard-export-pdf").disabled = false;
      const cash = data.metric === "cash";
      $("dashboard-detail-total").textContent = `${formatSignedCents(data.total_cents)} · soma de ${data.total} item(ns), em todas as páginas.`;
      $("dashboard-detail-note").textContent = cash ? "Entradas positivas, saídas negativas. Transferências aparecem nas duas contas e somam zero. Repasses não são receita pessoal. Esta composição não inclui saldo inicial." : ["card","all_cards"].includes(data.metric) ? data.responsible_code && data.responsible_code !== "all" ? "Parte do responsável selecionado. Consulte Todos para ver a cobrança completa e os créditos entre faturas." : "Total da fatura conforme os valores selecionados. Consulte o restante a pagar em Cartões e faturas." : data.metric === "card_expense" ? "Despesas do cartão selecionado, com Eu e Pai/Mãe. Créditos entre faturas mudam a cobrança, sem reduzir a despesa novamente." : data.metric === "all_income" ? "Receitas lançadas e estimativas de Eu e Pai/Mãe, em todos os meios. Estimativa não confirma recebimento; repasses não são receitas pessoais." : data.metric === "all_expense" ? "Despesas de Eu e Pai/Mãe em todos os meios de pagamento. No cartão, vale o mês da fatura. Estimativas ainda podem mudar. Pagar uma fatura não cria outra despesa." : "Valores por parcela/mês da fatura, já com ajustes. Estimativa não é pagamento. Para resultado, despesas contribuem com sinal negativo; receitas com sinal positivo.";
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
        cell(tr, item.source === "statement_credit" ? "Crédito entre faturas" : cash ? (kinds[item.kind]||("Histórico: "+(kinds[item.source_kind]||item.kind.replace("history_","")))) : item.phase === "forecast" ? "Estimativa mensal" + (item.responsible_code === "parents" ? " · Pai/Mãe" : " · Eu") : item.source === "history" ? `Planilha · ${item.responsible_code === "parents" ? "Pai/Mãe" : "Eu"}` : "Novo lançamento" + (item.responsible_code === "parents" ? " · Pai/Mãe" : " · Eu"));
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
  $("dashboard-export-pdf").addEventListener("click", async () => {
    const button = $("dashboard-export-pdf");
    const detail = {...state.detail};
    if (!["expense", "self_expense", "parents_expense"].includes(detail.metric)) return;
    delete detail.metric;
    const params = new URLSearchParams(detail);
    button.disabled = true;
    $("dashboard-detail-error").hidden = true;
    try {
      const response = await fetch(`/api/dashboard/expenses.pdf?${params}`);
      if (!response.ok) {
        const error = await response.json().catch(() => null);
        throw new Error(error?.error?.message || "Não foi possível exportar o PDF.");
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `despesas-${detail.month}-${detail.responsible_code || "self"}.pdf`;
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (error) {
      $("dashboard-detail-error").textContent = error.message;
      $("dashboard-detail-error").hidden = false;
    } finally { button.disabled = false; }
  });
  const filters = $("dashboard-filters");
  const preferenceKey = "finance-dashboard-filters";
  const validMonth = value => typeof value === "string" && /^(?!0000)\d{4}-(0[1-9]|1[0-2])$/.test(value);
  const validMonths = value => ["3", "6", "12"].includes(value);
  filters.elements.month.value = localDate().slice(0,7);
  try {
    const saved = JSON.parse(localStorage.getItem(preferenceKey));
    if (validMonth(saved?.month)) filters.elements.month.value = saved.month;
    for (const chart of ["budget", "result"]) {
      const months = saved?.[chart + "_months"] ?? saved?.months;
      if (validMonths(months)) $(chart + "-horizon").value = months;
    }
    if (["self", "parents", "all"].includes(saved?.responsible_code)) filters.elements.responsible_code.value = saved.responsible_code;
  } catch {} // Armazenamento bloqueado ou inválido não impede abrir o painel.
  function rememberFilters() {
    const month = filters.elements.month.value;
    if (!validMonth(month)) return;
    const data = {month, responsible_code: filters.elements.responsible_code.value,
      budget_months: $("budget-horizon").value, result_months: $("result-horizon").value};
    try { localStorage.setItem(preferenceKey, JSON.stringify(data)); } catch {}
  }
  filters.addEventListener("change", () => { rememberFilters(); refreshBudget(); });
  filters.addEventListener("submit", event => { event.preventDefault(); rememberFilters(); refreshBudget(); });
  $("category-phase").addEventListener("change", renderCategories);
  for (const chart of ["budget", "result"]) $(chart + "-horizon").addEventListener("change", rememberFilters);
  for (const element of document.querySelectorAll("[data-dashboard-metric]")) element.addEventListener("click", () => openDetails(element.dataset.dashboardMetric));
  $("dashboard-detail-previous").addEventListener("click", () => {state.offset = Math.max(0, state.offset-50); loadDetails();});
  $("dashboard-detail-next").addEventListener("click", () => {state.offset += 50; loadDetails();});
  $("dashboard-detail-dialog").addEventListener("close", () => {++state.detailSequence;});
  return {
    openCard,
    updateCatalogs: cards.updateCatalogs,
    refresh: () => Promise.all([refreshBudget(), cards.refresh()]),
  };
}
