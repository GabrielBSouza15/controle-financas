import {formatCents,parseReaisToCents} from "/static/money.mjs";
export function createPersonalView({api,localDate,dateText,notify,refreshAll}) {
 const $=id=>document.getElementById(id); let offset=0,sequence=0,reinvestment=null,reinvestKey=null,bill=null;
 const cell=(tr,text)=>{const td=document.createElement("td");td.textContent=text;tr.append(td);return td;};
 function updateCatalogs(catalogs){}
 async function loadReinvestment(){reinvestment=await api("/personal/reinvestment");$("reinvestment-value").textContent=formatCents(reinvestment.amount_cents);}
 $("reinvestment-value").addEventListener("click",()=>$("edit-reinvestment").click());
 $("reinvestment-value").addEventListener("keydown",e=>{if(["Enter"," "].includes(e.key)){e.preventDefault();$("edit-reinvestment").click();}});
 $("edit-reinvestment").addEventListener("click",async()=>{try{await loadReinvestment();reinvestKey=crypto.randomUUID();$("reinvestment-form").reset();$("reinvestment-error").textContent="";$("reinvestment-error").hidden=true;$("reinvestment-dialog").showModal();}catch(e){notify(e.message,true);}});
 $("reinvestment-form").addEventListener("submit",async e=>{e.preventDefault();const f=e.currentTarget,b=f.querySelector('[type="submit"]');b.disabled=true;$("reinvestment-dialog").dataset.busy="true";try{const text=f.elements.amount.value.trim();const amount=text==="0"||text==="0,00"?0:parseReaisToCents(text);reinvestment=await api("/personal/reinvestment",{request_key:reinvestKey,version:reinvestment.version,mode:f.elements.mode.value,amount_cents:amount});$("reinvestment-value").textContent=formatCents(reinvestment.amount_cents);$("reinvestment-dialog").close();}catch(err){$("reinvestment-error").textContent=err.message;$("reinvestment-error").hidden=false;}finally{b.disabled=false;$("reinvestment-dialog").dataset.busy="false";}});
 const flowForm = $("flow-filters");
 const today = localDate();
 flowForm.elements.date_from.value = today.slice(0, 7) + "-01";
 const endOfNextMonth = new Date(Number(today.slice(0, 4)), Number(today.slice(5, 7)) + 1, 0);
 flowForm.elements.date_to.value = `${endOfNextMonth.getFullYear()}-${String(endOfNextMonth.getMonth() + 1).padStart(2, "0")}-${endOfNextMonth.getDate()}`;
 let appliedFilters = new FormData(flowForm);

 async function refresh() {
   const seq = ++sequence;
   $("flow-error").hidden = true;
   const params = new URLSearchParams(appliedFilters);
   params.set("offset", offset);
   try {
     const data = await api(`/personal/flow?${params}`);
     if (seq !== sequence) return;
     const rows = document.createDocumentFragment();
     for (const row of data.items) {
       const tr = document.createElement("tr");
       const day = cell(tr, dateText(row.effective_on || row.purchase_on || row.scheduled_on));
       if (row.effective_on) {
         const caption = document.createElement("small");
         caption.textContent = row.kind === "income" ? "Recebimento" : "Pagamento";
         day.append(caption);
       }
       const invoiceMonth = row.card_id ? row.date.slice(5, 7) + "/" + row.date.slice(0, 4) : "—";
       cell(tr, invoiceMonth);
       cell(tr, row.description || "—");
       cell(tr, row.responsible_code === "self" ? "Eu" : "Pai/Mãe");
       cell(tr, row.card_name || "PIX/Débito");
       cell(tr, row.kind === "income" ? "Receita" : "Despesa");
       const status = cell(tr, "");
       const badge = document.createElement("span");
       badge.className = "flow-status";
       badge.textContent = row.payment_status;
       status.append(badge);
       cell(tr, formatCents(row.amount_cents)).className = row.kind === "income" ? "money-income" : "money-expense";
       rows.append(tr);
     }
     $("flow-rows").replaceChildren(rows);
     const emptyMessage = params.get("q")?.trim()
       ? "Nenhum lançamento com essa descrição no período selecionado."
       : "Nenhum lançamento neste período.";
     $("flow-position").textContent = data.items.length
       ? `${data.offset + 1}–${data.offset + data.items.length} de ${data.total}`
       : emptyMessage;
     $("flow-prev").disabled = data.offset === 0;
     $("flow-next").disabled = data.offset + data.limit >= data.total;
   } catch (error) {
     if (seq === sequence) {
       $("flow-error").textContent = error.message;
       $("flow-error").hidden = false;
     }
   }
 }

 flowForm.addEventListener("submit", event => {
   event.preventDefault();
   appliedFilters = new FormData(flowForm);
   offset = 0;
   refresh();
 });
 $("flow-prev").addEventListener("click", () => {
   offset = Math.max(0, offset - 50);
   refresh();
 });
 $("flow-next").addEventListener("click", () => {
   offset += 50;
   refresh();
 });
 async function openBill(row){const month=row.reference_month.slice(0,7),data=await api(`/personal/bill?card_id=${row.card_id}&month=${month}`);if(!data.remaining_cents){notify("Esta fatura já está paga ou não tem valor pendente.");return;}bill={card_id:row.card_id,month,expected_remaining_cents:data.remaining_cents,request_key:crypto.randomUUID()};$("bill-payment-form").reset();$("bill-payment-form").elements.occurred_on.value=localDate();$("bill-payment-form").elements.occurred_on.max=localDate();$("bill-payment-summary").textContent=`${row.card_name} · ${month} · restante ${formatCents(data.remaining_cents)}`;$("bill-payment-error").textContent="";$("bill-payment-error").hidden=true;$("bill-payment-dialog").showModal();}
 $("bill-payment-form").addEventListener("submit",async e=>{e.preventDefault();const f=e.currentTarget,b=f.querySelector('[type="submit"]');b.disabled=true;$("bill-payment-dialog").dataset.busy="true";try{await api("/personal/bill",{...bill,occurred_on:f.elements.occurred_on.value,paid_without_account:true});$("bill-payment-dialog").close();notify("Pagamento da fatura inteira registrado.");await refreshAll();}catch(err){$("bill-payment-error").textContent=err.message;$("bill-payment-error").hidden=false;}finally{b.disabled=false;$("bill-payment-dialog").dataset.busy="false";}});
 for(const id of ["bill-payment-dialog","reinvestment-dialog"])$(id).addEventListener("cancel",e=>{if($(id).dataset.busy==="true")e.preventDefault();});
 return {refresh,updateCatalogs,loadReinvestment,openBill};
}
