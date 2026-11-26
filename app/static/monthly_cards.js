import {formatSignedCents} from "/static/money.mjs";

const personNames = {all: "Todos", self: "Eu", parents: "Pai/Mãe"};
const valueFields = {
  all: ["statement_total_cents", "forecast_cents", "projected_total_cents"],
  self: ["self_cents", "forecast_self_cents", "projected_self_cents"],
  parents: ["parents_cents", "forecast_parents_cents", "projected_parents_cents"],
};

export function createMonthlyCards(root, {api, localDate, dateText, open}) {
  const form = root.querySelector("form");
  const grid = root.querySelector(".monthly-card-grid");
  const message = root.querySelector(".monthly-message");
  const controls = form.elements;
  controls.month.value = localDate().slice(0, 7);
  let cards = [], invoices = new Map(), loadedMonth = "", sequence = 0;

  function add(parent, tag, className, content) {
    const node = document.createElement(tag);
    node.className = className;
    if (content) node.textContent = content;
    parent.append(node);
    return node;
  }

  function render() {
    if (loadedMonth !== controls.month.value) return;
    const person = controls.responsible_code.value, phase = controls.phase.value;
    const [registered, forecast, projected] = valueFields[person];
    const display = {all: projected, registered, forecast}[phase];
    const label = phase === "forecast" ? "Estimativa do ciclo" : person !== "all" ? `Parte de ${personNames[person]}` : phase === "all" ? "Total com estimativas" : "Total lançado";
    const monthText = `${loadedMonth.slice(5, 7)}/${loadedMonth.slice(0, 4)}`;
    grid.replaceChildren();
    for (const card of cards) {
      const invoice = invoices.get(card.id);
      const panel = add(grid, "article", "monthly-card");
      const face = add(panel, "button", "credit-card-face");
      face.type = "button";
      face.title = "Ver composição da fatura";
      face.dataset.bank = card.name === "Nubank" ? "nubank" : card.name === "Mercado Pago/Livre" ? "mercado" : "other";
      const top = add(face, "span", "credit-card-top");
      add(top, "strong", "credit-card-bank", card.name);
      add(top, "span", "credit-card-type", "Crédito");
      const emblem = add(face, "span", "credit-card-emblem");
      emblem.setAttribute("aria-hidden", "true");
      add(emblem, "span", "credit-card-chip");
      const waves = add(emblem, "span", "credit-card-waves");
      for (let i = 0; i < 3; i++) add(waves, "i", "");
      const reading = add(face, "span", "credit-card-reading");
      add(reading, "span", "credit-card-amount-label", label);
      const amount = formatSignedCents(invoice?.[display] ?? 0);
      add(reading, "strong", "credit-card-amount", amount);
      add(reading, "span", "credit-card-cycle", monthText);
      face.setAttribute("aria-label", `${card.name}, ${monthText}. ${label}: ${amount}. Ver composição`);
      face.addEventListener("click", () => open({card, month: loadedMonth, person, phase}));

      const footer = add(panel, "div", "monthly-card-footer");
      const fields = [];
      if (phase === "all") fields.push(["Lançado", invoice?.[registered] ?? 0], ["Estimativa", invoice?.[forecast] ?? 0]);
      if (phase !== "forecast" && person === "all" && invoice?.registered_count)
        fields.push(["Restante lançado", invoice.payment_remaining_cents]);
      if (fields.length) {
        const figures = add(footer, "dl", "monthly-card-figures");
        for (const [title, value] of fields) {
          const field = add(figures, "div", "");
          add(field, "dt", "", title);
          add(field, "dd", "", formatSignedCents(value));
        }
      }
      const meta = add(footer, "div", "monthly-card-meta");
      const status = !invoice ? "Sem registros no ciclo"
        : phase === "forecast" ? (invoice[forecast] ? "" : "Sem estimativas")
        : person !== "all" ? "" : !invoice.registered_count ? "Apenas estimativas"
        : !invoice.payment_remaining_cents ? "Lançados quitados" : "Lançados a pagar";
      if (status) add(meta, "span", "card-payment-state", status);
      if (invoice?.due_date) add(meta, "span", "", `Vencimento ${dateText(invoice.due_date)}`);
      if (!card.active) add(meta, "span", "", "Cartão inativo");
      footer.hidden = !fields.length && !meta.children.length;
    }
    message.textContent = cards.length ? "" : "Nenhum cartão cadastrado.";
    message.hidden = !!cards.length;
  }

  async function refresh() {
    const month = controls.month.value, request = ++sequence;
    loadedMonth = "";
    if (!form.checkValidity()) {
      root.removeAttribute("aria-busy");
      grid.replaceChildren();
      message.textContent = "Escolha o mês da fatura.";
      message.hidden = false;
      return;
    }
    root.setAttribute("aria-busy", "true");
    grid.replaceChildren();
    message.textContent = "Consultando faturas…";
    message.hidden = false;
    try {
      const rows = new Map();
      const params = new URLSearchParams({month_from: month, month_to: month, include_forecasts: "true", limit: 100});
      for (let offset = 0; ; offset += 100) {
        params.set("offset", String(offset));
        const page = await api(`/invoices/overview?${params}`);
        if (request !== sequence) return;
        for (const row of page.items) rows.set(row.card_id, row);
        if (offset + page.limit >= page.total) break;
      }
      invoices = rows;
      loadedMonth = month;
      render();
    } catch (error) {
      if (request === sequence) message.textContent = error.message;
    } finally {
      if (request === sequence) root.removeAttribute("aria-busy");
    }
  }

  form.addEventListener("submit", event => { event.preventDefault(); refresh(); });
  form.addEventListener("change", event => {
    if (event.target === controls.month) refresh();
    else render();
  });
  return {refresh, updateCatalogs(catalogs) { cards = catalogs.cards; render(); }};
}
