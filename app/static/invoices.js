// A interface apenas apresenta as consultas; a autoridade dos valores é o backend.
import {formatCents,formatSignedCents} from "/static/money.mjs";

export function createInvoiceView({api, showEntry, showHistory, showOccurrence, dateText, localDate, onPayment, onRepayment, onClose, onWholePayment}) {
  const $ = id => document.getElementById(id);
  const state = {offset: 0, sequence: 0, detailId: null, detailOffset: 0, detailSequence: 0, current:null};
  const cycleText = iso => `${iso.slice(5,7)}/${iso.slice(0,4)}`;
  function cell(row, text, className="") {
    const td = document.createElement("td"); td.textContent = text; td.className = className; row.append(td); return td;
  }
  function error(id, message) {$(id).textContent = message; $(id).hidden = !message;}
  function setOptions(id, items) {
    const element = $(id), previous = element.value; element.replaceChildren(new Option(id==="invoice-filter-card" ? "Todos os cartões" : "Todos", ""));
    for (const item of items) element.add(new Option(item.name, String(item.id)));
    if ([...element.options].some(option => option.value === previous)) element.value = previous;
  }
  function updateCatalogs(catalogs) {
    setOptions("invoice-filter-card",catalogs.cards);
    setOptions("invoice-filter-responsible",catalogs.responsibles);
  }
  const overview={card:null,month:null,name:"",offset:0,sequence:0,invoiceId:null};
  function link(parent,text,action){
    const b=document.createElement("button");b.type="button";b.className="link-button";b.textContent=text;
    b.addEventListener("click",()=>Promise.resolve(action()).catch(f=>error("invoice-error",f.message)));parent.append(b);
  }
  async function refresh() {
    const sequence=++state.sequence,form=$("invoice-filters"),params=new URLSearchParams();
    for(const [name,value] of new FormData(form))if(value)params.set(name,value);
    params.set("limit","50");params.set("offset",String(state.offset));
    params.set("include_forecasts","true");
    error("invoice-error","");$("invoice-table-wrap").hidden=true;$("invoice-empty").hidden=true;
    $("invoice-result-count").textContent="Buscando faturas…";
    $("invoice-previous").disabled=true;$("invoice-next").disabled=true;
    $("invoice-applied-filters").textContent="";
    if(!form.checkValidity()){$("invoice-result-count").textContent="Confira o período";return;}
    try{
      const data=await api(`/invoices/overview?${params}`);if(sequence!==state.sequence)return;
      if(state.offset>0&&state.offset>=data.total){state.offset=0;return refresh();}
      $("invoice-rows").replaceChildren();
      for(const row of data.items){
        const tr=document.createElement("tr"),name=cell(tr,row.card_name+" · "+cycleText(row.reference_month));
        const source=document.createElement("small");source.textContent={mixed:"Planilha e novos lançamentos",history:"Planilha",new:"Novos lançamentos",forecast:"Somente estimativas"}[row.source]+(row.forecast_count&&row.registered_count?" + estimativas":"");name.append(source);
        for(const key of ["projected_self_cents","projected_parents_cents","projected_total_cents"])cell(tr,formatSignedCents(row[key]),"number");
        if(row.forecast_count){const note=document.createElement("small");note.textContent=`Lançado: ${formatSignedCents(row.statement_total_cents)} · Estimado: ${formatCents(row.forecast_cents)}`;tr.children[3].append(note);}
        cell(tr,row.source==="mixed" ? (row.operational_status==="closed"?"Novos: fechada":"Novos: aberta") : {historical:"Não informado",open:"Aberta",closed:"Fechada",forecast:"Previsão"}[row.status]);
        const payment=cell(tr,"");
        if(row.registered_count){
        const check=document.createElement("input");check.type="checkbox";check.setAttribute("aria-label",`Marcar os valores lançados de ${row.card_name} de ${cycleText(row.reference_month)} como pagos`);check.checked=row.payment_remaining_cents===0;check.disabled=check.checked;
        const label=document.createElement("label");label.append(check,document.createTextNode(check.checked?"Lançados pagos":"Pagar valores lançados"));payment.append(label);
        check.addEventListener("change",()=>{check.checked=false;Promise.resolve(onWholePayment(row)).catch(e=>error("invoice-error",e.message));});
        }else payment.textContent="Estimativas não são pagamentos";
        link(cell(tr,""),"Ver detalhes",()=>{
          overview.card=row.card_id;overview.month=row.reference_month.slice(0,7);overview.name=row.card_name;overview.offset=0;
          return showOverview();
        });$("invoice-rows").append(tr);
      }
      $("invoice-result-count").textContent=`${data.total} fatura${data.total===1?"":"s"}`;
      const selected=form.elements.card_id.selectedOptions[0]?.textContent||"Todos os cartões";
      const from=form.elements.month_from.value,to=form.elements.month_to.value;
      $("invoice-applied-filters").textContent=selected+(from||to ? ` · ${from?cycleText(from):"início"} até ${to?cycleText(to):"último mês"}` : " · Todos os meses");
      const extra=[];
      if(form.elements.responsible_id.value)extra.push("Com despesas de "+form.elements.responsible_id.selectedOptions[0].textContent);
      if(form.elements.status.value)extra.push(form.elements.status.selectedOptions[0].textContent);
      if(extra.length)$("invoice-applied-filters").textContent+=" · "+extra.join(" · ");
      if(data.forecast_month_from)$("invoice-applied-filters").textContent+=` · Estimativas: ${cycleText(data.forecast_month_from)} até ${cycleText(data.forecast_month_to)}`;
      $("invoice-filters").querySelector(".invoice-more summary").textContent=extra.length?"Mais filtros ("+extra.length+" ativos)":"Mais filtros";
      $("invoice-empty").hidden=!!data.items.length;$("invoice-table-wrap").hidden=!data.items.length;
      $("invoice-page-position").textContent=data.total?`${state.offset+1}–${Math.min(state.offset+50,data.total)} de ${data.total}`:"";
      $("invoice-previous").disabled=!state.offset;$("invoice-next").disabled=state.offset+50>=data.total;
    }catch(f){
      if(sequence!==state.sequence)return;
      $("invoice-result-count").textContent="Consulta não concluída";$("invoice-page-position").textContent="";
      error("invoice-error",f.message);
    }
  }
  async function showOverview(){
    const sequence=++overview.sequence;
    $("bill-overview-title").textContent=overview.name+" · "+cycleText(overview.month);
    $("bill-overview-rows").replaceChildren();error("bill-overview-error","");
    for(const id of ["self","parents","total"])$("bill-overview-"+id).textContent="…";
    $("bill-overview-prev").disabled=true;$("bill-overview-next").disabled=true;$("bill-overview-new").hidden=true;
    if(!$("bill-overview-dialog").open)$("bill-overview-dialog").showModal();
    try{
      const p=new URLSearchParams({card_id:overview.card,month:overview.month,limit:50,offset:overview.offset,include_forecasts:"true"});
      const data=await api(`/invoices/overview/details?${p}`);if(sequence!==overview.sequence)return;
      if(overview.offset>0 && overview.offset>=data.total){overview.offset=0;return showOverview();}
      overview.invoiceId=data.invoice_id;
      for(const id of ["self","parents"])$("bill-overview-"+id).textContent=formatCents(data[id+"_cents"]);
      $("bill-overview-total").textContent=formatSignedCents(data.total_cents);
      $("bill-overview-breakdown").textContent=`Lançado: ${formatSignedCents(data.registered_cents)} · Estimado: ${formatCents(data.forecast_cents)}. Estimativas ainda não confirmam compra nem pagamento.`;
      for(const item of data.items){
        const tr=document.createElement("tr");
        const name=cell(tr,item.description||"Sem descrição"),small=document.createElement("small");
        small.textContent=item.phase==="forecast"?`Estimativa · data prevista ${dateText(item.scheduled_on)}`:item.source==="statement_credit"?"Crédito entre faturas":`Parcela ${item.number}/${item.installment_count}`;name.append(small);
        cell(tr,item.responsible_code==="self"?"Eu":item.responsible_code==="parents"?"Pai/Mãe":"—");
        cell(tr,item.phase==="forecast"?"Previsão mensal":item.history_record_id?`Planilha · linha ${item.source_row}`:item.source==="statement_credit"?"Crédito":"Novo lançamento");
        cell(tr,formatSignedCents(item.contribution_cents),"number");
        const action=cell(tr,"");
        if(item.phase==="forecast")link(action,"Ver previsão",()=>{$("bill-overview-dialog").close();return showOccurrence(item.series_id,item.reference_month.slice(0,7));});
        else if(item.history_record_id)link(action,"Ver na planilha",()=>{$("bill-overview-dialog").close();return showHistory(item.history_record_id);});
        else if(item.entry_id)link(action,"Ver lançamento",()=>{$("bill-overview-dialog").close();return showEntry(item.entry_id);});
        else if(item.invoice_id)link(action,"Ver fatura",()=>{$("bill-overview-dialog").close();return openOperational(item.invoice_id);});
        $("bill-overview-rows").append(tr);
      }
      $("bill-overview-position").textContent=data.total?`${overview.offset+1}–${Math.min(overview.offset+50,data.total)} de ${data.total}`:"Sem parcelas";
      $("bill-overview-prev").disabled=!overview.offset;$("bill-overview-next").disabled=overview.offset+50>=data.total;
      $("bill-overview-new").hidden=!data.invoice_id;
    }catch(f){if(sequence===overview.sequence)error("bill-overview-error",f.message);}
  }
  function openOperational(id){state.detailId=id;state.detailOffset=0;return showDetails();}
  $("bill-overview-prev").addEventListener("click",()=>{overview.offset=Math.max(0,overview.offset-50);showOverview();});
  $("bill-overview-next").addEventListener("click",()=>{overview.offset+=50;showOverview();});
  $("bill-overview-dialog").addEventListener("close",()=>++overview.sequence);
  $("bill-overview-new").addEventListener("click",()=>{const id=overview.invoiceId;$("bill-overview-dialog").close();openOperational(id);});
  const initialMonth=localDate().slice(0,7);
  $("invoice-filters").elements.month_from.value=initialMonth;
  $("invoice-filters").elements.month_to.value=initialMonth;
  $("invoice-filters").addEventListener("change",()=>{state.offset=0;refresh();});
  async function showDetails() {
    const sequence = ++state.detailSequence, id = state.detailId, offset = state.detailOffset;
    error("invoice-detail-error","");
    try {
      const data = await api(`/invoices/${id}?limit=50&offset=${offset}`);
      if (sequence !== state.detailSequence) return;
      if (offset > 0 && offset >= data.active_obligation_count) {state.detailOffset = 0; return showDetails();}
      state.current=data;
      $("invoice-title").textContent = `${data.card_name} · ${cycleText(data.reference_month)}`;
      $("invoice-dates").textContent = `Fechamento: ${dateText(data.closing_date)} · Vencimento: ${dateText(data.due_date)} · ${data.status === "closed" ? "Fechada" : "Aberta"}${data.closure ? ` · Confirmação em ${new Date(data.closure.recorded_at).toLocaleString("pt-BR")} · Total originalmente consolidado: ${formatCents(data.closure.total_cents)} · ${data.closure.reason}` : ""}`;
      $("invoice-total").textContent = formatSignedCents(data.statement_total_cents); $("invoice-self").textContent = formatCents(data.self_cents); $("invoice-parents").textContent = formatCents(data.parents_cents);
      $("invoice-liquidation").textContent = `Pago ao banco: ${formatCents(data.issuer_paid_cents)}. ${data.issuer_remaining_cents<0?"Crédito no banco: "+formatCents(Math.abs(data.issuer_remaining_cents)):"Falta pagar: "+formatCents(data.issuer_remaining_cents)}.`;
      $("invoice-extra-info").textContent = `Ao emissor: ${formatCents(data.issuer_paid_cents)} registrados · ${formatSignedCents(data.issuer_remaining_cents)} de saldo ao emissor (negativo indica crédito). Parte de Pai/Mãe: ${formatCents(data.parents_covered_cents)} com cobertura vinculada · ${formatSignedCents(data.parents_uncovered_cents)} sem cobertura vinculada (negativo indica crédito do grupo).`;
      $("invoice-extra-info").textContent+=` Créditos compensados: ${formatCents(data.credit_in_cents)} recebidos de outro ciclo · ${formatCents(data.credit_out_cents)} usados em outro ciclo. Pagamento ao banco ainda sem vínculo às parcelas: ${formatCents(data.issuer_unallocated_cents)}.`;
      if(data.reconciliation) $("invoice-extra-info").textContent+=` Conferência: ${data.reconciliation.current?"composição atual":"composição alterada após conferência"} · diferença ${formatSignedCents(data.reconciliation.difference_cents)}.`;
      if(data.adjustments?.length) $("invoice-extra-info").textContent+=` Ajustes: ${data.adjustments.map(a=>`${formatSignedCents(a.delta_cents)} (${a.reason})`).join("; ")}.`;
      $("pay-invoice").hidden=data.empty||data.issuer_remaining_cents<=0;
      $("repay-invoice").hidden=data.empty||data.parents_uncovered_cents<=0;
      $("close-invoice").hidden=data.status==="closed"||data.statement_total_cents<0;
      $("invoice-detail-rows").replaceChildren();
      for (const part of data.items) {
        const row = document.createElement("tr"); const name = cell(row,part.description || "Descrição ausente");
        const sub = document.createElement("small"); sub.textContent = `${dateText(part.occurred_on)} · ${part.category_name || "Sem categoria"}`; name.append(sub);
        cell(row,`${part.number}/${part.installment_count}`); cell(row,part.responsible_name); cell(row,formatCents(part.amount_cents),"number");
        const action = cell(row,""); const button = document.createElement("button"); button.className = "link-button"; button.textContent = "Ver lançamento";
        button.addEventListener("click", async () => {
          $("invoice-dialog").close();
          try {await showEntry(part.entry_id);} catch (failure) {error("invoice-error",failure.message);}
        }); action.append(button); $("invoice-detail-rows").append(row);
      }
      $("invoice-detail-table").hidden = data.empty; $("invoice-detail-empty").hidden = !data.empty;
      $("invoice-detail-position").textContent = data.active_obligation_count ? `${offset+1}–${Math.min(offset+50,data.active_obligation_count)} de ${data.active_obligation_count} parcelas` : "";
      $("invoice-detail-previous").disabled = offset === 0; $("invoice-detail-next").disabled = offset+50 >= data.active_obligation_count;
      if (!$("invoice-dialog").open) $("invoice-dialog").showModal();
    } catch (failure) {
      if (sequence !== state.detailSequence) return;
      if ($("invoice-dialog").open) {
        $("invoice-detail-table").hidden = true; $("invoice-detail-previous").disabled = true; $("invoice-detail-next").disabled = true;
        error("invoice-detail-error",failure.message);
      } else error("invoice-error",failure.message);
    }
  }
  $("invoice-filters").addEventListener("submit", event => {event.preventDefault(); state.offset = 0; refresh();});
  $("invoice-filters").addEventListener("reset", () => {state.offset = 0; setTimeout(refresh, 0);});
  $("invoice-previous").addEventListener("click", () => {state.offset = Math.max(0,state.offset-50); refresh();});
  $("invoice-next").addEventListener("click", () => {state.offset += 50; refresh();});
  $("invoice-detail-previous").addEventListener("click", () => {state.detailOffset = Math.max(0,state.detailOffset-50); showDetails();});
  $("invoice-detail-next").addEventListener("click", () => {state.detailOffset += 50; showDetails();});
  $("invoice-dialog").addEventListener("close", () => {++state.detailSequence;});
  for(const [id,action] of [["pay-invoice",onPayment],["repay-invoice",onRepayment],["close-invoice",onClose]]) $(id).addEventListener("click",()=>{
    const invoice=state.current;$("invoice-dialog").close();Promise.resolve(action(invoice)).catch(failure=>error("invoice-error",failure.message));
  });
  return {updateCatalogs,refresh,open:async id=>{state.detailId=id;state.detailOffset=0;await showDetails();}};
}
