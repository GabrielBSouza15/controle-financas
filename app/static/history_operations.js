import {formatCents,formatSignedCents,parseReaisToCents} from "/static/money.mjs";
export function createHistoryOperations({api,notify,localDate,refreshAll}){
 const $=id=>document.getElementById(id),state={key:crypto.randomUUID(),data:{records:[],links:[],event_options:[]},offset:0};
 const eventNames={invoice_payment:"Pagamento ao banco",expense_payment:"Despesa paga",income_receipt:"Receita recebida",repayment:"Repasse de Pai/Mae"};
 const labels={adjustment:"Alterar compromisso / estorno",baseline_refund:"Dinheiro devolvido de pagamento antigo",release:"Liberar vínculo equivocado",allocate:"Reaplicar valor sem vínculo",refund:"Dinheiro devolvido de fato novo",refund_credit:"Devolver dinheiro sem vínculo",move:"Corrigir ciclo",reconcile:"Conferir total do ciclo"};
 const hints={adjustment:"Reduzir a zero cancela o compromisso observado. Estorno do emissor reduz a despesa, sem inventar entrada de dinheiro. Para desfazer um ajuste, registre o valor inverso com justificativa.",
 baseline_refund:"Use somente para dinheiro efetivamente devolvido à sua conta (ou devolvido por você em receita antiga). A data do pagamento original continua desconhecida. O estorno da despesa é uma operação separada.",
 release:"Corrige a distribuição, sem apagar o pagamento ou mudar o caixa. Reaplique o valor disponível na origem correta; não pague o banco novamente.",
 allocate:"Vincula dinheiro já registrado. Não cria outro pagamento. Pagamentos de cartão só podem ser reaplicados no mesmo cartão e ciclo.",
 refund:"Registra dinheiro efetivamente devolvido e reduz o vínculo selecionado. Pagamentos diretos de Pai/Mãe não usam conta própria.",
 refund_credit:"Devolve dinheiro que já ficou sem vínculo após uma liberação. Não modifica o orçamento da compra.",
 move:"Permitido somente para parcela de cartão não paga, sem vínculos ou ajustes. O ciclo de origem fica preservado.",
 reconcile:"Informe o total conferido no documento do emissor. Diferenças e mudanças posteriores ficam sinalizadas; esta conferência não confirma pagamento."};
 function choices(id,items,label){const s=$(id),old=s.value;s.replaceChildren(new Option("Selecione",""));for(const item of items)s.add(new Option(label(item),String(item.id??item.token)));if([...s.options].some(o=>o.value===old))s.value=old;}
 function mode(){
  const f=$("history-operation-form"),kind=f.elements.kind.value,link=state.data.links.find(x=>x.token===f.elements.link.value);
  const fields={record:["adjustment","baseline_refund","allocate","move"].includes(kind),event:["allocate","refund_credit"].includes(kind),link:["release","refund"].includes(kind),account:kind==="baseline_refund"||kind==="refund_credit"||kind==="refund",
   direction:kind==="adjustment",month:["move","reconcile"].includes(kind),card:kind==="reconcile",amount:kind!=="move"};
  if(kind==="refund"){const e=state.data.event_options.find(e=>e.id===link?.event_id);fields.account=!!e?.account_id;}
  if(kind==="refund_credit"){const e=state.data.event_options.find(e=>String(e.id)===f.elements.event.value);fields.account=!!e?.account_id;}
  for(const [name,visible]of Object.entries(fields)){const label=$("history-op-"+name+"-field");label.hidden=!visible;for(const el of label.querySelectorAll("input,select")){el.disabled=!visible;el.required=visible;}}
  $("history-operation-hint").textContent=hints[kind];
 }
 async function refresh(){
  state.data=await api("/history/operations?limit=30&offset="+state.offset);
  choices("history-op-record",state.data.records,r=>"Linha "+r.source_row+" · "+(r.description||"Sem descrição")+" · "+formatCents(r.effective_cents));
  choices("history-op-event",state.data.event_options,e=>"#"+e.id+" · "+(eventNames[e.kind]||e.kind)+" · disponível "+formatCents(e.available_cents));
  choices("history-op-link",state.data.links.filter(l=>l.remaining_cents>0).map(l=>({...l,id:l.token})),l=>"Fato #"+l.event_id+" · linha "+(state.data.records.find(r=>r.id===l.record_id)?.source_row||l.record_id)+" · "+formatCents(l.remaining_cents));
  const catalogs=await api("/catalogs");choices("history-op-account",catalogs.accounts,a=>a.name);
  $("history-operations-list").replaceChildren();
  for(const op of state.data.items){const li=document.createElement("li");li.textContent="#"+op.id+" · "+labels[op.kind]+" · "+formatSignedCents(op.amount_cents)+" · "+op.occurred_on+" · "+op.reason;$("history-operations-list").append(li);}
  $("history-operations-position").textContent=state.data.total+" operações; "+state.data.items.length+" nesta página";
  $("history-operations-prev").disabled=state.offset===0;$("history-operations-next").disabled=state.offset+30>=state.data.total;mode();
 }
 $("history-operation-form").elements.occurred_on.value=localDate();
 $("history-operation-form").addEventListener("change",mode);
 $("history-operation-form").addEventListener("submit",async e=>{
  e.preventDefault();const f=e.currentTarget,b=f.querySelector("button.primary");if(b.disabled)return;b.disabled=true;
  let saved=false;
  try{
   const k=f.elements.kind.value,amount=k==="move"||k==="reconcile"&&/^0+(?:,0{1,2})?$/.test(f.elements.amount.value.trim())?0:parseReaisToCents(f.elements.amount.value);
   const data={request_key:state.key,kind:k,occurred_on:f.elements.occurred_on.value,reason:f.elements.reason.value,amount_cents:k==="adjustment"&&f.elements.direction.value==="decrease"?-amount:amount};
   if(!f.elements.record.disabled)data.record_id=Number(f.elements.record.value);
   if(!f.elements.event.disabled)data.event_id=Number(f.elements.event.value);
   if(!f.elements.account.disabled)data.account_id=Number(f.elements.account.value);
   if(!f.elements.month.disabled)data.target_month=f.elements.month.value;
   if(!f.elements.card.disabled)data.card_code=f.elements.card.value;
   if(!f.elements.link.disabled){const l=state.data.links.find(x=>x.token===f.elements.link.value);if(!l)throw new Error("Atualize e escolha um vínculo.");Object.assign(data,{record_id:l.record_id,event_id:l.event_id,allocation_id:l.allocation_id,source_operation_id:l.operation_id});}
   await api("/history/operations",data);saved=true;state.key=crypto.randomUUID();f.elements.reason.value="";notify("Operação registrada; origem e histórico preservados.");
   await refreshAll();
  }catch(error){notify((saved?"Operação salva; a consulta não foi atualizada. ":"")+error.message,true);}finally{b.disabled=false;}
 });
 for(const [id,delta]of [["history-operations-prev",-30],["history-operations-next",30]])$(id).addEventListener("click",()=>{state.offset=Math.max(0,state.offset+delta);refresh().catch(e=>notify(e.message,true));});
 return {refresh};
}
