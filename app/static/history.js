import {formatCents,parseReaisToCents} from "/static/money.mjs";
import {createHistoryOperations} from "/static/history_operations.js";
import {plainCents} from "/static/settlement_money.mjs";
export function createHistoryView({api,notify,dateText,localDate,refreshAll}){
 const $=id=>document.getElementById(id),state={offset:0,eventOffset:0,sequence:0,catalogs:{accounts:[]},row:null,key:null,kind:null,reverseId:null,reverseKey:null};
 const operations=createHistoryOperations({api,notify,localDate,refreshAll:async()=>{await refresh();await refreshAll();}});
 const cards={nubank:"Nubank",mercado_pago:"Mercado Pago/Livre"};
 const kinds={invoice_payment:"Pagamento ao banco",expense_payment:"Despesa paga",income_receipt:"Receita recebida",repayment:"Repasse de Pai/Mãe"};
 const statuses={zeroed:"Compromisso reduzido a zero",credit:"Crédito a conferir",paid_undated:"Pago na planilha; data desconhecida",pending:"Pendente",partial:"Parcialmente pago",paid:"Quitado com novo registro",not_applicable:"Saldo inicial / exclusão"};
 function cell(tr,value){const td=document.createElement("td");td.textContent=value;tr.append(td);return td;}
 function button(parent,label,action){const b=document.createElement("button");b.type="button";b.className="link-button";b.textContent=label;b.addEventListener("click",()=>Promise.resolve().then(action).catch(e=>notify(e.message,true)));parent.append(b);}
 function updateCatalogs(c){state.catalogs=c;}
 function params(){const p=new URLSearchParams();for(const [k,v]of new FormData($("history-filters")))if(v)p.set(k,v);p.set("limit","50");p.set("offset",String(state.offset));return p;}
 async function refresh(){
  const sequence=++state.sequence;$("history-error").hidden=true;
  try{
   const [sum,data,events]=await Promise.all([api("/history/summary"),api("/history/records?"+params()),api("/history/events?limit=20&offset="+state.eventOffset)]);
   if(sequence!==state.sequence)return;
   $("history-no-data").hidden=!!sum.source_rows;$("history-content").hidden=!sum.source_rows;if(!sum.source_rows)return;
   $("history-pending").textContent=formatCents(sum.pending_cents);$("history-pending-count").textContent=sum.pending_rows+" parcela(s)";
   $("history-in").textContent=formatCents(sum.cash_in_cents);$("history-out").textContent=formatCents(sum.cash_out_cents);
   $("history-opening").textContent=sum.opening_balances.length?"Abertura confirmada: "+sum.opening_balances.map(r=>formatCents(r.amount_cents)+" em "+dateText(r.reference_on)).join("; ")+". O caixa antigo está incompleto; não há saldo bancário atual calculado.":"Sem abertura confirmada; valores de caixa são variações registradas.";
   $("history-rows").replaceChildren();
   for(const item of data.items){
    const tr=document.createElement("tr");cell(tr,"Linha "+item.source_row+" · "+dateText(item.reference_on));cell(tr,(item.description||"Descrição ausente")+" · "+(item.responsible_code==="self"?"Eu":"Pai/Mãe"));
    cell(tr,item.observed_number+"/"+item.observed_count);cell(tr,formatCents(item.effective_amount_cents)+(item.effective_amount_cents!==item.amount_cents?" · original "+formatCents(item.amount_cents):""));
    cell(tr,(item.role==="opening_balance"?"Saldo inicial":item.role==="excluded"?"Duplicidade excluída":statuses[item.payment_state]||item.payment_state)+(item.pending_cents>0?" · falta "+formatCents(item.pending_cents):""));
    const actions=cell(tr,"");button(actions,"Ver origem",()=>detail(item.id));
    if(item.role==="obligation"&&item.pending_cents>0)button(actions,item.kind==="income"?"Registrar recebimento":"Pagar parcela",()=>openPay(item,false));
    if(item.role==="obligation"&&item.kind==="expense"&&item.responsible_code==="parents"&&item.known_parents_covered_cents<item.amount_cents)button(actions,"Registrar novo repasse",()=>openPay(item,true));
    $("history-rows").append(tr);
   }
   $("history-count").textContent=data.total+" origens no filtro";$("history-records-empty").hidden=!!data.items.length;
   $("history-position").textContent=data.items.length?String(state.offset+1)+"–"+String(state.offset+data.items.length):"Sem registros";
   $("history-prev").disabled=state.offset===0;$("history-next").disabled=state.offset+50>=data.total;
   $("history-cycles").replaceChildren();const filters=new FormData($("history-filters"));
   for(const invoice of sum.invoices){
    if(filters.get("card_code")&&filters.get("card_code")!==invoice.card_code)continue;
    if(filters.get("month")&&filters.get("month")!==invoice.month.slice(0,7))continue;
    if(filters.get("pending_only")==="true"&&!invoice.pending_cents)continue;
    const tr=document.createElement("tr");cell(tr,cards[invoice.card_code]+" · "+invoice.month.slice(0,7));
    for(const k of ["total_cents","documented_paid_undated_cents","new_payment_cents","issuer_remaining_cents","unallocated_cents"])cell(tr,formatCents(invoice[k]));
    cell(tr,({missing:"A conferir",matched:"Total conferido",stale:"Conferência desatualizada",difference:"Diferença registrada"})[invoice.reconciliation.state]);
    $("history-cycles").append(tr);
   }
   $("history-events").replaceChildren();
   for(const event of events.items){const li=document.createElement("li");li.textContent=dateText(event.occurred_on)+" · "+kinds[event.kind]+" · "+formatCents(event.amount_cents)+" · "+(event.status==="active"?"Ativo":"Anulado")+" · "+event.reason;
    button(li,"Ver vínculos",()=>eventDetail(event));
    if(event.status==="active"&&!event.has_corrections)button(li,"Anular informação",()=>openReverse(event));$("history-events").append(li);
   }
   if(!events.items.length){const li=document.createElement("li");li.textContent="Nenhum novo fato registrado nesta página.";$("history-events").append(li);}
   $("history-events-position").textContent=events.total+" registros; "+events.items.length+" nesta página";
   $("history-events-prev").disabled=state.eventOffset===0;   $("history-events-next").disabled=state.eventOffset+20>=events.total;
   await operations.refresh();
  }catch(e){if(sequence===state.sequence){$("history-error").textContent=e.message;$("history-error").hidden=false;}}
 }
 async function detail(id){const item=await api("/history/records/"+id);$("history-detail-title").textContent="Linha "+item.source_row+" · "+(item.description||"Descrição ausente");$("history-detail-text").textContent="Parcela "+item.observed_number+"/"+item.observed_count+". "+statuses[item.payment_state]+". Grupo: "+({confirmed:"vínculo confirmado",candidate:"candidato a agrupamento",unmapped:"sem compra agrupada"}[item.group_state])+". Repasse anterior: "+(item.responsible_code==="parents"?"desconhecido":"não se aplica")+". Novos valores cobertos de Pai/Mãe: "+formatCents(item.known_parents_covered_cents);$("history-detail-evidence").textContent=JSON.stringify({origem:item.evidence,novos_registros:item.new_events,correcoes:item.operations},null,2);$("history-detail-dialog").showModal();}
 function eventDetail(event){$("history-detail-title").textContent="Registro histórico "+event.id;$("history-detail-text").textContent=kinds[event.kind]+" · "+dateText(event.occurred_on)+". "+event.reason;$("history-detail-evidence").textContent=JSON.stringify(event,null,2);$("history-detail-dialog").showModal();}
 function payerChanged(){
  const f=$("history-pay-form"),direct=state.kind!=="repayment"&&state.kind!=="income_receipt"&&f.elements.payer_code.value==="parents";
  $("history-pay-account-field").hidden=direct;f.elements.account_id.required=!direct;f.elements.account_id.disabled=direct;
  $("history-pay-warning").textContent=state.kind==="repayment"?"Registre somente um repasse comprovado nesta data. A ausência de evidência antiga não comprova dívida atual.":direct?"Pagamento direto de Pai/Mãe ao emissor/fornecedor: sem saída da sua conta.":"Este registro confirma uma movimentação efetiva da conta selecionada; não cria outra despesa ou receita.";
 }
 function openPay(row,repay){
  state.row=row;state.key=crypto.randomUUID();state.kind=repay?"repayment":row.kind==="income"?"income_receipt":row.card_code?"invoice_payment":"expense_payment";
  const f=$("history-pay-form");f.reset();f.elements.occurred_on.value=localDate();
  f.elements.payer_code.value=repay?"parents":"self";f.elements.payer_code.disabled=repay||state.kind==="income_receipt";
  f.elements.payer_code.querySelector('option[value="parents"]').disabled=row.responsible_code!=="parents";
  f.elements.amount.value=plainCents(repay?Math.max(0,row.effective_amount_cents-row.known_parents_covered_cents):row.pending_cents);
  $("history-pay-account").replaceChildren(new Option("Selecione uma conta sua",""));
  for(const account of state.catalogs.accounts)$("history-pay-account").add(new Option(account.name,String(account.id)));
  $("history-pay-title").textContent=kinds[state.kind];$("history-pay-context").textContent="Linha "+row.source_row+" · "+(row.description||"Descrição ausente")+" · parcela "+row.observed_number+"/"+row.observed_count;
  $("history-pay-error").hidden=true;payerChanged();$("history-pay-dialog").showModal();
 }
 function openReverse(event){state.reverseId=event.id;state.reverseKey=crypto.randomUUID();$("history-reverse-form").reset();$("history-reverse-error").hidden=true;$("history-reverse-dialog").showModal();}
 $("history-pay-form").elements.payer_code.addEventListener("change",payerChanged);
 async function submit(formId,dialogId,errorId,operation,success){
  const form=$(formId),b=form.querySelector('[type="submit"]');if(b.disabled)return;b.disabled=true;$(dialogId).dataset.busy="true";$(errorId).hidden=true;
  let saved=false;
  try{await operation(form);saved=true;$(dialogId).close();notify(success);await refresh();await refreshAll();}
  catch(e){if(saved)notify(success+" A consulta precisa de atualização: "+e.message,true);else{$(errorId).textContent=e.message;$(errorId).hidden=false;}}
  finally{b.disabled=false;$(dialogId).dataset.busy="false";}
 }
 $("history-pay-form").addEventListener("submit",e=>{e.preventDefault();submit("history-pay-form","history-pay-dialog","history-pay-error",f=>{
  const amount=parseReaisToCents(f.elements.amount.value);
  return api("/history/events",{request_key:state.key,kind:state.kind,occurred_on:f.elements.occurred_on.value,amount_cents:amount,
   payer_code:state.kind==="income_receipt"?null:f.elements.payer_code.value,account_id:f.elements.account_id.disabled?null:Number(f.elements.account_id.value),
   reason:f.elements.reason.value,allocations:[{record_id:state.row.id,amount_cents:amount}]});
 },"Fato registrado com data efetiva; evidência antiga preservada.");});
 $("history-reverse-form").addEventListener("submit",e=>{e.preventDefault();submit("history-reverse-form","history-reverse-dialog","history-reverse-error",f=>api("/history/events/"+state.reverseId+"/reverse",{request_key:state.reverseKey,reason:f.elements.reason.value}),"Informação anulada; histórico preservado.");});
 for(const id of ["history-pay-dialog","history-reverse-dialog"])$(id).addEventListener("cancel",e=>{if($(id).dataset.busy==="true")e.preventDefault();});
 $("history-refresh").addEventListener("click",refresh);
 $("history-filters").addEventListener("submit",e=>{e.preventDefault();state.offset=0;refresh();});
 $("history-filters").addEventListener("reset",()=>setTimeout(()=>{state.offset=0;refresh();},0));
 $("history-prev").addEventListener("click",()=>{state.offset=Math.max(0,state.offset-50);refresh();});
 $("history-next").addEventListener("click",()=>{state.offset+=50;refresh();});
 $("history-events-prev").addEventListener("click",()=>{state.eventOffset=Math.max(0,state.eventOffset-20);refresh();});
 $("history-events-next").addEventListener("click",()=>{state.eventOffset+=20;refresh();});
 return {refresh,updateCatalogs,open:detail};
}
