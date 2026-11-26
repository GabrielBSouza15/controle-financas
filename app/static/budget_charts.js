import {formatCents,formatSignedCents} from "/static/money.mjs";
import {canvas,node,cell,action,bar,label,monthText} from "/static/chart_render.js";
export function createBudgetCharts(open) {
 const $=id=>document.getElementById(id);let data=null,offset=0,capacity=6,resultOffset=0,resultCapacity=6,lastScope="";
 function selection(chart) {
  const phase = $(chart+"-phase").value;
  const rows = data.variants[phase].months.slice(0, Number($(chart+"-horizon").value));
  const responsible_code = data.responsible_code;
  const person = {self:"Eu",parents:"Pai/Mãe",all:"Todos"}[responsible_code];
  return {phase, rows, person, responsible_code};
 }
 function budget() {
  if(!data||$("dashboard-view").hidden)return;
  const available=$("budget-month-chart").clientWidth;if(!available)return;
  const {rows: all, phase, person, responsible_code} = selection("budget");
  const step=Math.max(160,...all.flatMap(r=>[r.total_income_cents,r.total_expense_cents]).map(v=>formatCents(v).length*12+28));
  capacity=Math.max(1,Math.min(6,Math.floor((available-78)/step)));
  offset=Math.min(offset,Math.max(0,all.length-1));
  const rows=all.slice(offset,offset+capacity);
  const max=Math.max(0,...all.flatMap(r=>[r.total_income_cents,r.total_expense_cents]));
  const width=Math.max(available,86+rows.length*step);
  const chart=canvas($("budget-month-chart"),0,max,width,`Receitas e despesas de ${person}. Sólido: lançado; listrado: estimativa.` ,"budget");
  rows.forEach((row,i)=>{const mid=86+(i+.5)*(width-94)/rows.length;
   for(const [metric,shift,name] of [["income",-step/4,"Receitas"],["expense",step/4,"Despesas"]]) {
    const total=row[`total_${metric}_cents`],registered=row[`registered_${metric}_cents`],forecast=row[`forecast_${metric}_cents`],x=mid+shift;
    const composition = phase === "all" ? ` Lançado ${formatCents(registered)}; estimativa ${formatCents(forecast)}.` : "";
    const g=action(`${name} de ${person} em ${monthText(row.month)}: ${formatCents(total)}.${composition} Ver composição.`,()=>open(metric,row.month.slice(0,7),{phase,responsible_code}));
    g.setAttribute("data-value-kind",metric);
    g.append(node("rect",{x:x-27,y:24,width:54,height:224,fill:"transparent"}));
    if(registered)bar(chart,g,x,38,0,registered,metric);
    if(forecast)bar(chart,g,x,38,registered,total,metric,true);
    if(!total)bar(chart,g,x,38,0,0,metric);
    if($("budget-labels").checked)label(chart,g,x,total);
    g.append(node("text",{x,y:232,"text-anchor":"middle"},name));chart.svg.append(g);
   }
   chart.svg.append(node("text",{x:mid,y:258,"text-anchor":"middle",class:"finance-month"},monthText(row.month)));
  });
  const text=`${monthText(rows[0].month)} a ${monthText(rows.at(-1).month)} · ${offset+1}–${offset+rows.length} de ${all.length} meses`;
  $("budget-page-label").textContent=text;$("budget-prev").disabled=!offset;$("budget-next").disabled=offset+capacity>=all.length;
  $("dashboard-chart-person").textContent = person;
  $("dashboard-range").textContent = `${monthText(all[0].month)} a ${monthText(all.at(-1).month)}`;
 }
 function result() {
  if (!data || !$("budget-result-chart").clientWidth) return;
  const {rows: all, phase, person, responsible_code} = selection("result");
  const min=Math.min(0,...all.map(r=>r.result_cents)),max=Math.max(0,...all.map(r=>r.result_cents));
  const container=$("budget-result-chart"),step=Math.max(110,...all.map(r=>formatSignedCents(r.result_cents).length*7+24));
  resultCapacity=Math.max(1,Math.min(6,Math.floor((container.clientWidth-78)/step)));resultOffset=Math.min(resultOffset,all.length-1);
  const rows=all.slice(resultOffset,resultOffset+resultCapacity),width=Math.max(container.clientWidth,78+rows.length*step);
  const chart=canvas(container,min,max,width,`Sobra ou falta de ${person}. Cada mês é independente, não é saldo bancário.`,"result");
  rows.forEach((r,i)=>{const x=78+(i+.5)*(width-86)/rows.length,v=r.result_cents,status=v>0?"Sobra":v<0?"Falta":"Equilíbrio";
   const g=action(`${monthText(r.month)}: ${status}, ${formatSignedCents(v)}. Ver composição.`,()=>open("result",r.month.slice(0,7),{phase,responsible_code}));
   g.setAttribute("data-value-kind",v<0?"expense":v>0?"income":"neutral");
   g.append(node("rect",{x:x-28,y:20,width:56,height:250,fill:"transparent"}));bar(chart,g,x,38,0,v,v<0?"expense":"income");
   if($("result-labels").checked)label(chart,g,x,v,v<0?chart.y(v)+17:chart.y(v)-10,(v>0?"+ ":"")+formatSignedCents(v));
   g.append(node("text",{x,y:250,"text-anchor":"middle"},monthText(r.month)),node("text",{x,y:272,"text-anchor":"middle"},status));chart.svg.append(g);
  });
  $("result-page-label").textContent=`${monthText(rows[0].month)} a ${monthText(rows.at(-1).month)} · ${resultOffset+1}–${resultOffset+rows.length} de ${all.length} meses`;
  $("result-prev").disabled=!resultOffset;$("result-next").disabled=resultOffset+resultCapacity>=all.length;
  $("budget-result-note").textContent=rows.some(r=>!r.total_income_cents)?"Há mês sem receitas para a seleção. O resultado reflete os valores exibidos.":"Cada coluna abre a composição do resultado. Use os botões para navegar pelo período.";
 }
 function budgetTable() {
  const {rows, phase: selectedPhase, responsible_code} = selection("budget");
  $("budget-forecast-legend").hidden = selectedPhase === "registered";
  for (const header of document.querySelectorAll("[data-budget-phase]")) {
   header.hidden = selectedPhase !== "all" && header.dataset.budgetPhase !== selectedPhase;
  }
  $("budget-month-values").replaceChildren();
  for (const row of rows) {
   const tr = document.createElement("tr");
   cell(tr, monthText(row.month));
   for (const [field, metric, phase] of [
    ["registered_income_cents","income","registered"], ["forecast_income_cents","income","forecast"],
    ["registered_expense_cents","expense","registered"], ["forecast_expense_cents","expense","forecast"],
    ["result_cents","result",selectedPhase],
   ]) {
    if (selectedPhase !== "all" && phase !== selectedPhase) continue;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "link-button";
    button.textContent = formatSignedCents(row[field]);
    button.addEventListener("click", () => open(metric, row.month.slice(0,7), {phase,responsible_code}));
    cell(tr, "").append(button);
   }
   $("budget-month-values").append(tr);
  }
 }
 function resultTable() {
  const {rows} = selection("result");
  $("budget-result-values").replaceChildren();
  for (const row of rows) {
   const tr = document.createElement("tr");
   cell(tr, monthText(row.month));
   cell(tr, row.result_cents > 0 ? "Sobra" : row.result_cents < 0 ? "Falta" : "Equilíbrio");
   cell(tr, (row.result_cents > 0 ? "+ " : "") + formatSignedCents(row.result_cents));
   $("budget-result-values").append(tr);
  }
 }
 $("budget-prev").addEventListener("click", () => {offset = Math.max(0, offset-capacity); budget();});
 $("budget-next").addEventListener("click", () => {offset += capacity; budget();});
 $("result-prev").addEventListener("click", () => {resultOffset = Math.max(0, resultOffset-resultCapacity); result();});
 $("result-next").addEventListener("click", () => {resultOffset += resultCapacity; result();});
 $("budget-labels").addEventListener("change", budget);
 $("result-labels").addEventListener("change", result);
 for (const setting of ["phase", "horizon"]) {
  $("budget-"+setting).addEventListener("change", () => {
   offset = 0;
   if (data) {budgetTable(); budget();}
  });
  $("result-"+setting).addEventListener("change", () => {
   resultOffset = 0;
   if (data) {resultTable(); result();}
  });
 }
 new ResizeObserver(budget).observe($("budget-month-chart"));
 new ResizeObserver(result).observe($("budget-result-chart"));
 function redraw() {budget(); result();}
 return {
  render(value) {
   const scope = value.month + "-" + value.responsible_code;
   if (scope !== lastScope) {
    offset = 0; resultOffset = 0; lastScope = scope;
   }
   data = value;
   budgetTable();
   resultTable();
   requestAnimationFrame(redraw);
  },
  redraw,
 };
}
