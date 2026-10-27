import {formatCents,parseReaisToCents} from "/static/money.mjs";
import {plainCents,sumAllocationTexts,distribute,hasAllocationText} from "/static/settlement_money.mjs";

export function createSettlementView({api,notify,dateText,localDate,refreshAll}) {
  const $ = id => document.getElementById(id);
  const names = {invoice_payment:"Pagamento ao banco",repayment:"Repasse de Pai/Mãe",expense_payment:"Despesa sem cartão paga",income_receipt:"Receita recebida"};
  const state = {catalogs:null, offset:0, sequence:0, mode:"create", key:null, kind:null, invoiceId:null, entryId:null,
    event:null, candidates:[], candidateOffset:0,candidateSequence:0, selected:new Map(), detailId:null,detailOffset:0,detailSequence:0, action:null};
  function error(id,message) {$(id).textContent=message;$(id).hidden=!message;}
  function cell(row,text,className="") {const td=document.createElement("td");td.textContent=text;td.className=className;row.append(td);return td;}
  function options(id,items,empty) {const element=$(id),old=element.value;element.replaceChildren();if(empty!==null)element.add(new Option(empty,""));for(const item of items)element.add(new Option(item.name,String(item.id)));if([...element.options].some(o=>o.value===old))element.value=old;}
  function signed(value) {return `${value<0?"−":""}${formatCents(Math.abs(value))}`;}
  function updateCatalogs(catalogs) {
    state.catalogs=catalogs;options("cash-filter-account",catalogs.accounts,"Todas, inclusive pagamentos externos");
    options("settlement-account",catalogs.accounts,"Selecione uma conta sua");options("settlement-payer",catalogs.responsibles,null);
    $("new-repayment").disabled=!catalogs.ready||!catalogs.accounts.length;
  }
  async function refresh() {
    const sequence=++state.sequence,params=new URLSearchParams();
    for(const [name,value] of new FormData($("cash-filters")))if(value)params.set(name,value);
    params.set("limit","50");params.set("offset",String(state.offset));error("cash-error","");
    $("cash-previous").disabled=true;$("cash-next").disabled=true;$("cash-table-wrap").hidden=true;$("cash-empty").hidden=true;$("cash-count").textContent="Consultando…";for(const id of ["cash-in","cash-out","cash-net","cash-credit"])$(id).textContent="—";
    try {
      const data=await api(`/settlements?${params}`);if(sequence!==state.sequence)return;
      const pageTotal=Math.max(data.total,data.cash_extra_count||0);
      if(state.offset>0&&state.offset>=pageTotal){state.offset=0;return refresh();}
      $("cash-in").textContent=formatCents(data.cash_in_cents);$("cash-out").textContent=formatCents(data.cash_out_cents);
      $("cash-net").textContent=signed(data.cash_variation_cents);$("cash-credit").textContent=formatCents(data.available_credit_cents);
      $("cash-extras").replaceChildren();
      for(const item of data.cash_extras||[]){const li=document.createElement("li");li.textContent=`${dateText(item.occurred_on)} · ${item.kind==="transfer"?"Transferência própria":item.kind.startsWith("history_")?"Registro do histórico":"Devolução efetiva"} · ${state.catalogs.accounts.find(a=>a.id===item.account_id)?.name||"Conta não informada"} · ${item.direction==="in"?"+":"−"} ${formatCents(item.amount_cents)} · ${item.reason}`;$("cash-extras").append(li);}
      $("cash-rows").replaceChildren();
      for(const item of data.items){
        const row=document.createElement("tr");cell(row,dateText(item.occurred_on));
        const kind=cell(row,names[item.kind]),reason=document.createElement("small");reason.textContent=item.reason;kind.append(reason);
        const account=cell(row,item.account_name||"Sem caixa próprio"),payer=document.createElement("small");payer.textContent=item.payer_name||"Recebimento em conta própria";account.append(payer);
        cell(row,`${item.cash_direction==="in"?"+ ":item.cash_direction==="out"?"− ":""}${formatCents(item.amount_cents)}`,"number");
        cell(row,item.status==="active"?"Ativo":"Anulado");const action=cell(row,"");const button=document.createElement("button");button.className="link-button";button.textContent="Detalhes";
        button.setAttribute("aria-label",`Detalhes de ${names[item.kind]} em ${dateText(item.occurred_on)}`);
        button.addEventListener("click",()=>{state.detailId=item.id;state.detailOffset=0;showDetail().catch(e=>notify(e.message,true));});action.append(button);$("cash-rows").append(row);
      }
      $("cash-table-wrap").hidden=!data.items.length;$("cash-empty").hidden=!!data.items.length;
      $("cash-count").textContent=`${data.total} registro(s)`;$("cash-position").textContent=data.total?`${state.offset+1}–${Math.min(state.offset+50,data.total)} de ${data.total}`:"";
      $("cash-previous").disabled=state.offset===0;$("cash-next").disabled=state.offset+50>=pageTotal;
    }catch(failure){if(sequence!==state.sequence)return;error("cash-error",failure.message);$("cash-count").textContent="";$("cash-position").textContent="";$("cash-table-wrap").hidden=true;$("cash-empty").hidden=true;for(const id of ["cash-in","cash-out","cash-net","cash-credit"])$(id).textContent="—";}
  }
  function payerValue(){return state.kind==="income_receipt"?null:Number($("settlement-payer").value);}
  function updateFields(){
    const form=$("settlement-form"),isCredit=state.mode==="credit",income=state.kind==="income_receipt";
    if(state.kind==="repayment")$("settlement-payer").value=String(state.catalogs.responsibles.find(p=>p.code==="parents").id);
    const person=state.catalogs.responsibles.find(p=>p.id===payerValue()),external=person?.code==="parents"&&state.kind!=="repayment";
    $("settlement-payer-field").hidden=income;$("settlement-payer").disabled=income||state.kind==="repayment"||isCredit;
    $("settlement-account-field").hidden=external||isCredit;$("settlement-account").disabled=external||isCredit;$("settlement-account").required=!external&&!isCredit;
    form.elements.occurred_on.disabled=isCredit;form.elements.amount.disabled=isCredit;
  }
  function selectionPayload(){return [...state.selected.entries()].filter(([,v])=>hasAllocationText(v.text)).map(([id,value])=>({obligation_id:id,amount_cents:parseReaisToCents(value.text)})).sort((a,b)=>a.obligation_id-b.obligation_id);}
  function selectionSummary(){
    $("selected-allocations").replaceChildren();
    for(const [id,value]of state.selected){if(!hasAllocationText(value.text))continue;const li=document.createElement("li");li.textContent=`${value.part.description||"Descrição ausente"} · ${value.part.number}/${value.part.installment_count} · R$ ${value.text}`;
      const remove=document.createElement("button");remove.type="button";remove.className="link-button";remove.textContent="Remover";remove.addEventListener("click",()=>{state.selected.delete(id);renderCandidates();selectionSummary();});li.append(remove);$("selected-allocations").append(li);}
    try{const sum=sumAllocationTexts([...state.selected.values()].map(x=>x.text));const amount=state.mode==="credit"?state.event.available_credit_cents:parseReaisToCents($("settlement-form").elements.amount.value);
      const remainder=amount-sum;$("allocation-summary").textContent=`Aplicado: ${formatCents(sum)}. ${remainder<0?"A distribuição excede o valor efetivo.":state.kind==="repayment"?`${state.mode==="credit"?"Crédito restante":"Sem vínculo / crédito disponível"}: ${formatCents(remainder)}.`:`Falta vincular: ${formatCents(remainder)}.`}`;
    }catch{ $("allocation-summary").textContent="Informe o valor efetivo e os centavos aplicados para conferir a distribuição.";}
  }
  function renderCandidates(){
    $("candidate-rows").replaceChildren();
    for(const part of state.candidates){const row=document.createElement("tr");const name=cell(row,part.description||"Descrição ausente"),sub=document.createElement("small");sub.textContent=`${part.number}/${part.installment_count} · ${part.responsible_name} · ${dateText(part.scheduled_date)}`;name.append(sub);cell(row,formatCents(part.available_cents),"number");
      const field=cell(row,""),input=document.createElement("input");input.inputMode="decimal";input.placeholder="0,00";input.value=state.selected.get(part.obligation_id)?.text||"";input.setAttribute("aria-label",`Aplicar à parcela ${part.number} de ${part.description||"lançamento"}`);
      input.addEventListener("input",()=>{if(hasAllocationText(input.value))state.selected.set(part.obligation_id,{text:input.value,part});else state.selected.delete(part.obligation_id);selectionSummary();});field.append(input);$("candidate-rows").append(row);}
  }
  async function loadCandidates(){
    const sequence=++state.candidateSequence,params=new URLSearchParams({kind:state.kind,limit:"50",offset:String(state.candidateOffset)});
    if(payerValue()!==null)params.set("payer_id",String(payerValue()));if(state.invoiceId)params.set("invoice_id",String(state.invoiceId));if(state.entryId)params.set("entry_id",String(state.entryId));if($("settlement-search").value)params.set("q",$("settlement-search").value);
    $("candidate-previous").disabled=true;$("candidate-next").disabled=true;$("settlement-distribute").disabled=true;state.candidates=[];renderCandidates();$("candidate-empty").hidden=true;$("candidate-position").textContent="Consultando…";error("candidate-error","");
    try{const data=await api(`/settlements/obligations?${params}`);if(sequence!==state.candidateSequence)return;if(state.candidateOffset>0&&state.candidateOffset>=data.total){state.candidateOffset=0;return loadCandidates();}state.candidates=data.items;$("settlement-distribute").disabled=!data.items.length;renderCandidates();$("candidate-empty").hidden=!!data.items.length;$("candidate-position").textContent=data.total?`${state.candidateOffset+1}–${Math.min(state.candidateOffset+50,data.total)} de ${data.total}`:"";$("candidate-previous").disabled=state.candidateOffset===0;$("candidate-next").disabled=state.candidateOffset+50>=data.total;}
    catch(failure){if(sequence!==state.candidateSequence)return;state.candidates=[];renderCandidates();$("candidate-position").textContent="";error("candidate-error",failure.message);}
  }
  async function openEvent({kind,invoiceId=null,entryId=null,amount=null,label=""},creditEvent=null){
    state.mode=creditEvent?"credit":"create";state.event=creditEvent;state.kind=kind;state.invoiceId=invoiceId;state.entryId=entryId;state.key=crypto.randomUUID();state.selected.clear();state.candidates=[];state.candidateOffset=0;
    const form=$("settlement-form");form.reset();$("settlement-search").value="";form.elements.occurred_on.value=localDate();form.elements.occurred_on.max=localDate();form.elements.amount.value=amount!==null?plainCents(amount):"";
    $("settlement-payer").value=String(state.catalogs.responsibles.find(p=>p.code==="self")?.id||"");
    $("settlement-title").textContent=creditEvent?"Aplicar valor disponível":names[kind];$("settlement-context").textContent=creditEvent?`Crédito disponível: ${formatCents(creditEvent.available_credit_cents)}. Esta aplicação não registra nova entrada de dinheiro.`:`${label} Registre somente o valor efetivamente pago ou recebido e a data real. Vínculos podem cobrir compromissos ainda não vencidos.`;
    if(creditEvent){form.elements.amount.value=plainCents(creditEvent.available_credit_cents);$("settlement-payer").value=String(creditEvent.payer_id||"");}
    error("settlement-error","");updateFields();selectionSummary();$("settlement-dialog").showModal();await loadCandidates();
  }
  async function showDetail(){
    const sequence=++state.detailSequence;const data=await api(`/settlements/${state.detailId}?limit=50&offset=${state.detailOffset}`);if(sequence!==state.detailSequence)return;state.event=data;
    $("financial-detail-title").textContent=names[data.kind];$("financial-detail-summary").textContent=`${formatCents(data.amount_cents)} · ${dateText(data.occurred_on)} · ${data.account_name||"Sem caixa próprio"} · ${data.payer_name||"Recebimento"} · ${data.status==="active"?"Ativo":"Anulado"}${data.available_credit_cents?` · Crédito disponível: ${formatCents(data.available_credit_cents)}`:""}`;
    $("financial-detail-reason").textContent=`Motivo: ${data.reason} · Registro: ${new Date(data.recorded_at).toLocaleString("pt-BR")}${data.reversal?` · Anulação: ${data.reversal.reason} (${new Date(data.reversal.recorded_at).toLocaleString("pt-BR")})`:""}`;
    $("financial-detail-rows").replaceChildren();for(const a of data.allocations){const row=document.createElement("tr");cell(row,`${a.description||"Descrição ausente"} · ${a.number}/${a.installment_count}`);cell(row,`${formatCents(a.amount_cents)} original · ${formatCents(a.active_cents)} ativo`,"number");cell(row,a.reason);$("financial-detail-rows").append(row);}
    $("financial-detail-position").textContent=`${data.allocation_count?state.detailOffset+1:0}–${Math.min(state.detailOffset+50,data.allocation_count)} de ${data.allocation_count} vínculos`;
    $("financial-detail-previous").disabled=state.detailOffset===0;$("financial-detail-next").disabled=state.detailOffset+50>=data.allocation_count;
    $("apply-credit").hidden=data.status!=="active"||!data.available_credit_cents;$("reverse-financial").hidden=data.status!=="active"||data.refunded_cents>0;
    if(!$("financial-detail-dialog").open)$("financial-detail-dialog").showModal();
  }
  function openAction(kind,target){
    state.action={kind,target,key:crypto.randomUUID()};const form=$("financial-action-form");form.reset();error("financial-action-error","");
    const closing=kind==="close";$("financial-action-title").textContent=closing?"Confirmar fechamento da fatura":"Anular registro equivocado";
    $("financial-action-warning").textContent=closing?`Confira ${formatCents(target.statement_total_cents)} com a fatura real do emissor. O fechamento preserva os compromissos deste ciclo e impede novas edições. Não confirma pagamento e não pode ser desfeito por esta tela.`:"Use somente para corrigir um registro lançado por engano. O histórico permanece; seus efeitos deixam os totais ativos. Isso não registra devolução de dinheiro, estorno ou transferência real. Compromissos vinculados continuam protegidos.";
    $("closure-total-field").hidden=!closing;form.elements.amount.disabled=!closing;form.elements.amount.required=closing;$("financial-action-submit").textContent=closing?"Confirmar fechamento":"Confirmar anulação";$("financial-action-dialog").showModal();
  }
  $("new-repayment").addEventListener("click",()=>openEvent({kind:"repayment"}).catch(e=>notify(e.message,true)));
  $("cash-filters").addEventListener("submit",e=>{e.preventDefault();state.offset=0;refresh();});$("cash-filters").addEventListener("reset",()=>{state.offset=0;setTimeout(refresh,0);});
  $("cash-previous").addEventListener("click",()=>{state.offset=Math.max(0,state.offset-50);refresh();});$("cash-next").addEventListener("click",()=>{state.offset+=50;refresh();});
  $("settlement-payer").addEventListener("change",()=>{state.selected.clear();state.candidateOffset=0;updateFields();selectionSummary();loadCandidates();});
  $("settlement-form").elements.amount.addEventListener("input",selectionSummary);
  $("settlement-search-button").addEventListener("click",()=>{state.candidateOffset=0;loadCandidates();});
  $("settlement-search").addEventListener("keydown",e=>{if(e.key==="Enter"){e.preventDefault();state.candidateOffset=0;loadCandidates();}});
  $("candidate-previous").addEventListener("click",()=>{state.candidateOffset=Math.max(0,state.candidateOffset-50);loadCandidates();});$("candidate-next").addEventListener("click",()=>{state.candidateOffset+=50;loadCandidates();});
  $("settlement-distribute").addEventListener("click",()=>{try{const total=state.mode==="credit"?state.event.available_credit_cents:parseReaisToCents($("settlement-form").elements.amount.value);const ids=new Set(state.candidates.map(p=>p.obligation_id));const other=sumAllocationTexts([...state.selected.entries()].filter(([id])=>!ids.has(id)).map(([,v])=>v.text));if(other>total)throw new Error("Os vínculos de outras páginas já excedem o valor efetivo.");const amounts=distribute(total-other,state.candidates.map(p=>p.available_cents));state.candidates.forEach((part,index)=>{if(amounts[index])state.selected.set(part.obligation_id,{part,text:plainCents(amounts[index])});else state.selected.delete(part.obligation_id);});renderCandidates();selectionSummary();error("settlement-error","");}catch(failure){error("settlement-error",failure.message);}});
  $("settlement-form").addEventListener("submit",async e=>{
    e.preventDefault();const form=e.currentTarget,button=form.querySelector('[type="submit"]');if(button.disabled)return;button.disabled=true;$("settlement-dialog").dataset.busy="true";error("settlement-error","");let saved=false;
    try{const allocations=selectionPayload();if(allocations.length>100)throw new Error("Selecione até 100 parcelas por operação.");
      const data={request_key:state.key,reason:form.elements.reason.value,allocations};
      let path;
      if(state.mode==="credit")path=`/settlements/${state.event.id}/allocate`;
      else{Object.assign(data,{kind:state.kind,occurred_on:form.elements.occurred_on.value,amount_cents:parseReaisToCents(form.elements.amount.value),payer_id:payerValue(),account_id:$("settlement-account").disabled?null:Number($("settlement-account").value),invoice_id:state.kind==="invoice_payment"?state.invoiceId:null});path="/settlements";}
      await api(path,data);saved=true;$("settlement-dialog").close();notify("Registro confirmado. Caixa, alocações e histórico foram atualizados.");await refreshAll();
    }catch(failure){if(saved)notify(`Registro salvo; consulta não atualizada. ${failure.message}`,true);else error("settlement-error",failure.message);}
    finally{button.disabled=false;$("settlement-dialog").dataset.busy="false";}
  });
  $("financial-detail-previous").addEventListener("click",()=>{state.detailOffset=Math.max(0,state.detailOffset-50);showDetail().catch(e=>notify(e.message,true));});$("financial-detail-next").addEventListener("click",()=>{state.detailOffset+=50;showDetail().catch(e=>notify(e.message,true));});
  $("apply-credit").addEventListener("click",()=>{const event=state.event;$("financial-detail-dialog").close();openEvent({kind:event.kind,invoiceId:event.invoice_id},event).catch(e=>notify(e.message,true));});
  $("reverse-financial").addEventListener("click",()=>{const event=state.event;$("financial-detail-dialog").close();openAction("reverse",event);});
  $("financial-action-form").addEventListener("submit",async e=>{e.preventDefault();const form=e.currentTarget,button=form.querySelector('[type="submit"]');if(button.disabled)return;button.disabled=true;$("financial-action-dialog").dataset.busy="true";error("financial-action-error","");let saved=false;
    try{const {kind,target,key}=state.action;const data={request_key:key,reason:form.elements.reason.value};if(kind==="close"){data.expected_total_cents=form.elements.amount.value.trim()==="0,00"||form.elements.amount.value.trim()==="0"?0:parseReaisToCents(form.elements.amount.value);}
      await api(kind==="close"?`/invoices/${target.id}/close`:`/settlements/${target.id}/reverse`,data);saved=true;$("financial-action-dialog").close();notify(kind==="close"?"Fatura consolidada. Pagamento continua separado.":"Registro anulado com histórico preservado.");await refreshAll();
    }catch(failure){if(saved)notify(`Confirmação salva; consulta não atualizada. ${failure.message}`,true);else error("financial-action-error",failure.message);}
    finally{button.disabled=false;$("financial-action-dialog").dataset.busy="false";}
  });
  for(const id of ["settlement-dialog","financial-action-dialog"])$(id).addEventListener("cancel",e=>{if($(id).dataset.busy==="true")e.preventDefault();});
  $("settlement-dialog").addEventListener("close",()=>{++state.candidateSequence;});$("financial-detail-dialog").addEventListener("close",()=>{++state.detailSequence;});
  return {updateCatalogs,refresh,openEvent,openClosure:invoice=>openAction("close",invoice)};
}
