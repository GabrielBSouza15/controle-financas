import {createPersonalView} from "/static/personal.js";
import {paymentMethodValue, needsParentsRepayment} from "/static/payment_methods.mjs";
import {createHistoryView} from "/static/history.js";
import {createMaintenanceView} from "/static/maintenance.js";
import {createDashboardView} from "/static/dashboard.js";
import {createLegacyView} from "/static/legacy.js";
import {createRecurrenceView} from "/static/recurrences.js";
import {createAdjustmentView} from "/static/adjustments.js";
import {createSettlementView} from "/static/settlements.js";
import {createInvoiceView} from "/static/invoices.js";
import {resolveCategory} from "/static/entry_categories.mjs";
import {parseReaisToCents, formatCents} from "/static/money.mjs";
const $ = (id) => document.getElementById(id);
const state = {catalogs: null, offset: 0, total: 0, sequence: 0, key: null, editing: null, details: null, cancelKey: null, cancelSource: null};
const statuses = {active: "Ativo", cancelled: "Cancelado", superseded: "Versão anterior"};
const methods = {credit: "Crédito", pix: "PIX/Débito", cash: "PIX/Débito", pix_debit:"PIX/Débito"};
const dateText = (iso) => iso ? iso.split("-").reverse().join("/") : "—";
const localDate = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`; };
function notify(message, error = false) {
  $("notice").textContent = message; $("notice").classList.toggle("error", error); $("notice").hidden = false;
}
async function api(path, body) {
  const options = body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]').content}, body: JSON.stringify(body)};
  let response;
  try { response = await fetch(`/api${path}`, options); }
  catch { throw new Error("Não foi possível conectar. Mantenha os dados e tente novamente quando o aplicativo estiver disponível."); }
  let result;
  try { result = await response.json(); } catch { throw new Error("Resposta inesperada. Recarregue a página antes de continuar."); }
  if (!response.ok) {
    const fields = (result.error?.fields || []).map(f => `${f.field || "Lançamento"}: ${f.message}`).join(" ");
    const error = new Error(`${result.error?.message || "Não foi possível concluir."} ${fields}`.trim());
    error.code = result.error?.code; error.matches = result.error?.matches; throw error;
  }
  return result;
}
function optionList(id, items, emptyLabel = null) {
  const select = $(id), old = select.value;
  select.replaceChildren();
  if (emptyLabel !== null) select.add(new Option(emptyLabel, ""));
  for (const item of items) select.add(new Option(item.name, String(item.id)));
  if ([...select.options].some(o => o.value === old)) select.value = old;
}
function showView(name) {
  for (const key of ["dashboard", "entries", "flow", "invoices", "cash", "adjustments", "recurrences", "legacy", "history", "maintenance", "catalogs"]) {
    $(`${key}-view`).hidden = key !== name;
    $(`nav-${key}`).classList.toggle("active", key === name);
    if (key === name) $(`nav-${key}`).setAttribute("aria-current", "page"); else $(`nav-${key}`).removeAttribute("aria-current");
  }
}
async function refreshFinancialViews() {
  await loadEntries();
  if (!$("dashboard-view").hidden) await dashboardView.refresh();
  if (!$("invoices-view").hidden) await invoiceView.refresh();
  if (!$("flow-view").hidden) await personalView.refresh();
  if (!$("cash-view").hidden) await settlementView.refresh();
}
const historyView = createHistoryView({api,notify,dateText,localDate,refreshAll:refreshFinancialViews});
const maintenanceView = createMaintenanceView({api,notify});
const legacyView = createLegacyView({api,notify,dateText});
const recurrenceView = createRecurrenceView({api,notify,dateText,localDate,resolveCategoryName,showEntry:showDetails,showHistory:id=>historyView.open(id),refreshAll:refreshFinancialViews});
const adjustmentView = createAdjustmentView({api,notify,dateText,localDate,refreshAll:refreshFinancialViews});
const settlementView = createSettlementView({api,notify,dateText,localDate,refreshAll:refreshFinancialViews});
const invoiceView = createInvoiceView({api, onWholePayment:row=>personalView.openBill(row), showEntry:showDetails, showHistory:id=>historyView.open(id), showOccurrence:(id,month)=>recurrenceView.openOccurrences(id,month), dateText, localDate,
  onPayment:invoice=>settlementView.openEvent({kind:"invoice_payment",invoiceId:invoice.id,amount:invoice.issuer_remaining_cents,label:`${invoice.card_name} · ${dateText(invoice.closing_date)}.`}),
  onRepayment:invoice=>settlementView.openEvent({kind:"repayment",invoiceId:invoice.id,label:`Parte de Pai/Mãe em ${invoice.card_name} · ${dateText(invoice.closing_date)}.`}),
  onClose:invoice=>settlementView.openClosure(invoice)});

const dashboardView = createDashboardView({api,notify,dateText,localDate,showEntry:showDetails,
  showInvoice:id=>invoiceView.open(id),showHistory:id=>historyView.open(id),showOccurrence:(id,month)=>recurrenceView.openOccurrences(id,month)});

const personalView=createPersonalView({api,localDate,dateText,notify,refreshAll:refreshFinancialViews,showEntry:showDetails,showHistory:id=>historyView.open(id),showOccurrence:(id,month)=>recurrenceView.openOccurrences(id,month)});

function listRecords(id, records, empty = "Nenhum cadastro ainda.") {
  const list = $(id); list.replaceChildren();
  for (const name of records.length ? records : [empty]) { const li = document.createElement("li"); li.textContent = name; list.append(li); }
}
function resolveCategoryName(name) {
  return resolveCategory(name, {
    getCategories: () => state.catalogs.categories,
    createCategory: name => api("/categories", {name, parent_id: null}),
    reloadCategories: async () => { await loadCatalogs(); },
    onCreated: category => {
      state.catalogs.categories.push(category);
      const suggestions = new Set([...(state.catalogs.category_suggestions || []), category.name]);
      state.catalogs.category_suggestions = [...suggestions].sort((a,b) => a.localeCompare(b, "pt-BR"));
      applyCatalogs(state.catalogs);
    },
  });
}
function renderCategories(c) {
  const suggestions = $("entry-category-options"); suggestions.replaceChildren();
  for (const name of c.category_suggestions || c.categories.map(category => category.name)) {
    const option = document.createElement("option"); option.value = name; suggestions.append(option);
  }
  optionList("category-parent", c.categories, "Sem grupo de categorias");
  listRecords("category-list", c.categories.map(a => a.parent_id ? `${a.name} · ${c.categories.find(p => p.id === a.parent_id)?.name || "Grupo de categorias"}` : a.name));
}
async function loadCatalogs() { applyCatalogs(await api("/catalogs")); }
function applyCatalogs(c) {
  state.catalogs = c; historyView.updateCatalogs(c); invoiceView.updateCatalogs(c); personalView.updateCatalogs(state.catalogs); settlementView.updateCatalogs(c); adjustmentView.updateCatalogs(c); recurrenceView.updateCatalogs(c);
  optionList("entry-responsible", c.responsibles);
  optionList("filter-responsible", c.responsibles, "Todos");
  optionList("entry-card", c.cards.filter(card => card.active));
  optionList("filter-card", c.cards, "Todos");

  renderCategories(c);
  $("new-entry").disabled = !c.ready;
  $("setup-form").hidden = c.ready;
  listRecords("responsible-list", c.responsibles.map(r => r.name));
  $("card-list").replaceChildren();
  if (!c.cards.length) { const p = document.createElement("p"); p.textContent = "Os cartões aparecerão após a preparação inicial."; $("card-list").append(p); }
  for (const card of c.cards) {
    const block = document.createElement("div"); block.className = "card-record";
    const title = document.createElement("strong"); title.textContent = card.name; block.append(title);
    for (const rule of card.rules) {
      const p = document.createElement("p");
      p.textContent = `Fecha dia ${String(rule.closing_day).padStart(2,"0")} · Vence dia ${String(rule.due_day).padStart(2,"0")} · Vigência: ${dateText(rule.effective_from)}. Compra no fechamento: ${rule.include_closing_day ? "ciclo atual" : "próximo ciclo"}.`;
      block.append(p);
    }
    $("card-list").append(block);
  }
}
function cell(row, text, className = "") { const td = document.createElement("td"); td.textContent = text; td.className = className; row.append(td); return td; }
async function loadEntries() {
  const sequence = ++state.sequence;
  const params = new URLSearchParams();
  for (const [name, value] of new FormData($("filters"))) if (value) params.set(name, value);
  params.set("limit", "50"); params.set("offset", String(state.offset));
  const data = await api(`/entries?${params}`);
  if (sequence !== state.sequence) return;
  state.total = data.total;
  $("entry-rows").replaceChildren();
  for (const entry of data.items) {
    const row = document.createElement("tr");
    const title = cell(row, entry.description || "Descrição ausente");
    const type = document.createElement("span"); type.className = "type"; type.textContent = entry.kind === "income" ? "Receita" : "Despesa"; title.append(type);
    const statusLabel = document.createElement("span"); statusLabel.className = "status-label"; statusLabel.textContent = statuses[entry.status]; title.append(statusLabel);
    const sub = document.createElement("small"); sub.textContent = entry.category_name || "Sem categoria"; title.append(sub);
    cell(row, dateText(entry.occurred_on)); cell(row, entry.responsible_name);
    cell(row, entry.card_name || methods[entry.payment_method]);
    const value = cell(row, formatCents(entry.total_cents), "number");
    const parts = document.createElement("small"); parts.textContent = `${entry.installment_count} parcela${entry.installment_count > 1 ? "s" : ""}`; value.append(parts);
    const action = cell(row, ""); const button = document.createElement("button"); button.className = "link-button"; button.textContent = "Detalhes";
    button.setAttribute("aria-label", `Detalhes de ${entry.description || "lançamento"}`);
    button.addEventListener("click", () => showDetails(entry.id).catch(e => notify(e.message, true))); action.append(button);
    $("entry-rows").append(row);
  }
  $("result-count").textContent = `${data.total} registro${data.total === 1 ? "" : "s"}`;
  $("entries-table-wrap").hidden = !data.items.length; $("entry-empty").hidden = !!data.items.length;
  const ready = state.catalogs?.ready;
  $("empty-title").textContent = !ready ? "Seu controle começa aqui" : data.total ? "Sem registros nesta página" : "Nenhum lançamento encontrado";
  $("empty-text").textContent = !ready ? "Prepare os responsáveis e cartões para começar a registrar suas finanças." : "Cadastre um lançamento ou ajuste os filtros para encontrar seus registros.";
  $("empty-setup").hidden = !!ready;
  $("previous").disabled = state.offset === 0; $("next").disabled = state.offset + 50 >= data.total;
  $("page-position").textContent = data.total ? `${state.offset + 1}–${Math.min(state.offset + 50, data.total)} de ${data.total}` : "";
}
async function showDetails(id) {
  const entry = await api(`/entries/${id}`); state.details = entry;
  $("detail-title").textContent = entry.description || "Descrição ausente";
  $("detail-summary").textContent = `${formatCents(entry.total_cents)} · ${entry.responsible_name} · ${entry.card_name || methods[entry.payment_method]} · Data original: ${dateText(entry.occurred_on)}`;
  $("detail-overlap-review").hidden = !entry.history_overlap_review;
  if (entry.history_overlap_review) $("detail-overlap-review").textContent = `Coincidência histórica conferida como compromisso distinto em ${new Date(entry.history_overlap_review.recorded_at).toLocaleString("pt-BR")}. Motivo: ${entry.history_overlap_review.reason}. Origens conferidas: ${entry.history_overlap_review.record_ids.map(id=>"#"+id).join(", ")}.`;
  $("detail-rows").replaceChildren();
  for (const part of entry.obligations) {
    const row = document.createElement("tr"); cell(row, `${part.number}/${entry.installment_count}`); cell(row, formatCents(part.amount_cents), "number");
    const scheduled = cell(row, dateText(part.scheduled_date));
    if (entry.parents_repayment) {
      const budget = document.createElement("small");
      budget.textContent = `Orçamento: ${part.budget_month.slice(5,7)}/${part.budget_month.slice(0,4)} · ${entry.parents_repayment === "pending" ? "repasse pendente" : "repasse informado como recebido"}`;
      scheduled.append(budget);
    }
    cell(row, dateText(part.closing_date));
    cell(row, part.invoice_id ? part.invoice_status === "closed" ? "Fechada" : "Aberta" : "Sem fatura"); const settlement = cell(row, part.payment_cents ? `${entry.payment_method === "credit" ? "Ao emissor" : entry.kind === "income" ? "Recebido" : "Pago"}: ${formatCents(part.payment_cents)}` : "Sem liquidação registrada");
    if(part.credit_applied_cents) settlement.textContent+=` · Crédito compensado: ${formatCents(part.credit_applied_cents)}`;
    if(entry.paid_without_account) settlement.textContent+=` · Em ${dateText(entry.occurred_on)}, sem conta bancária vinculada`;
    if (part.parents_covered_cents) {const coverage = document.createElement("small"); coverage.textContent = `Cobertura do grupo: ${formatCents(part.parents_covered_cents)}`; settlement.append(coverage);}
    $("detail-rows").append(row);
  }
  $("history-list").replaceChildren();
  for (const version of entry.history) {
    const li = document.createElement("li");
    li.textContent = `Registro #${version.id} · ${statuses[version.status]} · ${version.description || "Descrição ausente"} · ${formatCents(version.total_cents)} · ${version.installment_count} parcela(s) · ${dateText(version.occurred_on)} · ${version.responsible_name} · ${version.card_name || methods[version.payment_method]}${version.reason ? ` · Motivo: ${version.reason} · Registrado em: ${new Date(version.recorded_at).toLocaleString("pt-BR")}` : ""}`;
    if (version.id !== entry.id) {
      const button = document.createElement("button"); button.className = "link-button"; button.textContent = "Consultar versão";
      button.addEventListener("click", () => showDetails(version.id).catch(e => notify(e.message,true))); li.append(button);
    }
    $("history-list").append(li);
  }
  $("edit-entry").hidden = !entry.can_modify; $("cancel-entry").hidden = !entry.can_modify;
  $("modification-note").textContent = entry.can_modify ? "Você pode revisar este compromisso; o motivo e a versão anterior ficarão registrados." : entry.status !== "active" ? "Este registro pertence ao histórico. Consulte a versão ativa, se houver, para fazer alterações." : entry.has_settlement_links ? "Este compromisso possui vínculos financeiros e não pode ser reescrito, mesmo após anulação de um registro. Preserve o histórico ao corrigir." : "Alterações bloqueadas: há parcela em fatura consolidada. A correção precisa de um ajuste específico.";
  $("settle-entry").hidden = entry.status !== "active" || entry.payment_method === "credit" || !entry.obligations.some(part=>part.payment_cents < part.amount_cents);
  $("settle-entry").textContent = entry.kind === "income" ? "Registrar recebimento" : "Registrar pagamento da despesa";
  if (!$("details-dialog").open) $("details-dialog").showModal();
}
function updateMethod() {
  const form = $("entry-form"), income = form.elements.kind.value === "income";
  const method = form.elements.payment_method;
  method.querySelector('option[value="credit"]').disabled = income;
  if (income && method.value === "credit") method.value = "pix_debit";
  const credit = method.value === "credit";
  $("card-field").hidden = !credit; $("entry-card").disabled = !credit; $("entry-card").required = credit;
  const person = state.catalogs?.responsibles.find(r => String(r.id) === form.elements.responsible_id.value)?.code;
  const eligible = needsParentsRepayment(person, form.elements.kind.value, method.value);
  $("entry-repayment-field").hidden = !eligible;
  $("entry-repayment").disabled = !eligible;
  const legacy = state.editing !== null && !state.details?.parents_repayment;
  $("entry-repayment").required = eligible && !legacy;
  if (!eligible) $("entry-repayment").value = "";
  $("entry-repayment-help").textContent = legacy
    ? "Sem indicação no cadastro antigo: deixe em branco para manter o mês original. Escolher uma opção revisa o planejamento; não registra entrada de dinheiro."
    : "Define somente o mês do orçamento. Registre a entrada real de dinheiro separadamente nos detalhes da origem.";
}
function newEntry() {
  $("entry-form").reset(); state.historyMatches = null; $("history-overlap").hidden = true; state.editing = null; $("entry-title").textContent = "Adicionar receita ou despesa";
  $("edit-reason-field").hidden = true; $("entry-form").elements.reason.disabled = true; $("entry-form").elements.reason.required = false;
  state.key = crypto.randomUUID(); $("entry-form").elements.occurred_on.value = localDate();
  $("entry-error").hidden = true; updateMethod(); $("entry-dialog").showModal();
}
for (const name of ["dashboard", "entries", "flow", "invoices", "cash", "adjustments", "recurrences", "legacy", "history", "maintenance", "catalogs"]) $(`nav-${name}`).addEventListener("click", () => {showView(name); if (name === "flow") personalView.refresh(); if (name === "dashboard") dashboardView.refresh(); if (name === "invoices") invoiceView.refresh(); if (name === "cash") settlementView.refresh(); if (name === "adjustments") adjustmentView.refresh().catch(e=>notify(e.message,true)); if (name === "recurrences") recurrenceView.refresh().catch(e=>notify(e.message,true)); if (name === "history") historyView.refresh(); if (name === "maintenance") maintenanceView.refresh(); if (name === "legacy") legacyView.refresh().catch(e=>notify(e.message,true));});
$("empty-setup").addEventListener("click", () => showView("catalogs"));
$("new-entry").addEventListener("click", newEntry);
for (const button of document.querySelectorAll("[data-close]")) button.addEventListener("click", () => {const dialog = $(button.dataset.close); if (dialog.dataset.busy !== "true") dialog.close();});
$("entry-responsible").addEventListener("change", updateMethod);
$("entry-form").elements.kind.addEventListener("change", updateMethod);
$("entry-form").elements.payment_method.addEventListener("change", updateMethod);

$("filters").addEventListener("submit", e => {e.preventDefault(); state.offset = 0; loadEntries().catch(e => notify(e.message, true));});
$("filters").addEventListener("reset", () => {state.offset = 0; setTimeout(() => loadEntries().catch(e => notify(e.message, true)), 0);});
$("previous").addEventListener("click", () => {state.offset = Math.max(0,state.offset-50); loadEntries().catch(e => notify(e.message,true));});
$("next").addEventListener("click", () => {state.offset += 50; loadEntries().catch(e => notify(e.message,true));});
$("entry-form").addEventListener("submit", async e => {
  e.preventDefault(); const form = e.currentTarget, button = form.querySelector('[type="submit"]'); if (button.disabled) return;
  button.disabled = true; $("entry-category").readOnly = true; $("entry-dialog").dataset.busy = "true"; $("entry-error").hidden = true;
  let saved = false;
  try {
    const data = Object.fromEntries(new FormData(form));
    const total = parseReaisToCents(data.amount);
    const payload = {request_key: state.key, description: data.description, occurred_on: data.occurred_on, total_cents: total,
      installment_count: Number(data.installment_count), responsible_id: Number(data.responsible_id), kind: data.kind, payment_method: data.payment_method,
      category_id: null, card_id: data.payment_method === "credit" ? Number(data.card_id) : null,
      account_id: state.editing !== null && data.payment_method !== "credit" ? state.details.account_id : null,
      parents_repayment: data.parents_repayment || null};
    if (state.editing === null && data.kind === "expense" && data.payment_method === "pix_debit" && state.catalogs.responsibles.find(p=>p.id===Number(data.responsible_id))?.code === "self") payload.paid_without_account=true;
    if (state.editing !== null) payload.reason = data.reason;
    if (state.historyMatches && $("history-distinct").checked) payload.history_confirmation = {record_ids: state.historyMatches.map(item=>item.record_id).sort((a,b)=>a-b), reason: $("history-distinct-reason").value};
    payload.category_id = await resolveCategoryName(data.category_name);
    const path = state.editing === null ? "/entries" : `/entries/${state.editing}/edit`;
    await api(path, payload); saved = true; $("entry-dialog").close(); state.key = null;
    notify(state.editing === null ? "Lançamento salvo. Confira a distribuição das parcelas em Detalhes." : "Alteração salva. A versão anterior está preservada no histórico."); state.offset = 0; await refreshFinancialViews();
  } catch (error) {
    if (saved) notify(`Lançamento salvo, mas a consulta não foi atualizada. ${error.message}`, true);
    else {
      if (error.code === "history_overlap") {
        state.historyMatches = error.matches; $("history-overlap").hidden = false; $("history-distinct").checked = false;
        $("history-overlap-items").replaceChildren();
        for (const item of error.matches) { const li = document.createElement("li"); li.textContent = `Linha ${item.source_row}: ${item.description} · ${formatCents(item.amount_cents)} · ${dateText(item.reference_on)}`; $("history-overlap-items").append(li); }
      }
      $("entry-error").textContent = error.message; $("entry-error").hidden = false;
    }
  } finally {button.disabled = false; $("entry-category").readOnly = false; $("entry-dialog").dataset.busy = "false";}
});
function bindCatalogForm(id, path, payload) {
  $(id).addEventListener("submit", async e => {
    e.preventDefault(); const form = e.currentTarget, button = form.querySelector("button"); if (button.disabled) return;
    button.disabled = true;
    try { await api(path, payload(new FormData(form))); form.reset(); await loadCatalogs(); await loadEntries(); notify("Cadastro salvo."); }
    catch (error) {notify(error.message, true);}
    finally {button.disabled = false;}
  });
}
bindCatalogForm("setup-form", "/setup", data => ({effective_from: data.get("effective_from")}));
bindCatalogForm("category-form", "/categories", data => ({name: data.get("name"), parent_id: data.get("parent_id") ? Number(data.get("parent_id")) : null}));
$("setup-form").elements.effective_from.value = localDate();
try { await loadCatalogs(); await loadEntries(); await dashboardView.refresh(); await personalView.loadReinvestment(); } catch (error) {notify(error.message, true);}

$("edit-entry").addEventListener("click", () => {
  const entry = state.details; $("details-dialog").close(); newEntry(); state.editing = entry.id;
  $("entry-title").textContent = "Editar lançamento";
  const form = $("entry-form");
  for (const name of ["description","kind","occurred_on","installment_count","responsible_id","card_id"]) form.elements[name].value = entry[name] === null ? "" : String(entry[name]);
  form.elements.payment_method.value = paymentMethodValue(entry.payment_method);
  $("entry-repayment").value = entry.parents_repayment || "";
  $("entry-category").value = entry.category_name || "";
  const cents = BigInt(entry.total_cents); form.elements.amount.value = `${cents/100n},${String(cents%100n).padStart(2,"0")}`;
  $("edit-reason-field").hidden = false; form.elements.reason.disabled = false; form.elements.reason.required = true;
  updateMethod();
});
$("cancel-entry").addEventListener("click", () => {
  state.cancelSource = state.details.id; state.cancelKey = crypto.randomUUID();
  $("cancel-form").reset(); $("cancel-error").hidden = true;
  $("cancel-description").textContent = `${state.details.description} · ${formatCents(state.details.total_cents)}`;
  $("details-dialog").close(); $("cancel-dialog").showModal();
});
$("cancel-form").addEventListener("submit", async event => {
  event.preventDefault(); const form = event.currentTarget, button = form.querySelector('[type="submit"]'); if (button.disabled) return;
  button.disabled = true; $("cancel-dialog").dataset.busy = "true"; $("cancel-error").hidden = true; let saved = false;
  try {
    await api(`/entries/${state.cancelSource}/cancel`, {request_key: state.cancelKey, reason: form.elements.reason.value});
    saved = true; $("cancel-dialog").close(); notify("Compromisso cancelado no planejamento. O histórico foi preservado."); state.offset = 0; await refreshFinancialViews();
  } catch (error) {
    if (saved) notify(`Cancelamento registrado; a consulta não foi atualizada. ${error.message}`,true);
    else {$("cancel-error").textContent = error.message; $("cancel-error").hidden = false;}
  } finally {button.disabled = false; $("cancel-dialog").dataset.busy = "false";}
});

for (const id of ["entry-dialog","cancel-dialog"]) $(id).addEventListener("cancel", event => {if ($(id).dataset.busy === "true") event.preventDefault();});

$("settle-entry").addEventListener("click",()=>{
  const entry=state.details;$("details-dialog").close();
  const remaining=entry.obligations.reduce((sum,part)=>sum+BigInt(part.amount_cents-part.payment_cents-(part.credit_applied_cents||0)),0n);
  settlementView.openEvent({kind:entry.kind==="income"?"income_receipt":"expense_payment",entryId:entry.id,
    amount:Number(remaining > 0n ? remaining : 0n),label:entry.description||"Lançamento sem descrição"}).catch(e=>notify(e.message,true));
});
