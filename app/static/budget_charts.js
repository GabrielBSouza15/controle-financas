import {formatCents,formatSignedCents} from "/static/money.mjs";
import {canvas,node,cell,action,bar,label,monthText} from "/static/chart_render.js";
export function createBudgetCharts(open) {
 const $=id=>document.getElementById(id);let data=null,offset=0,capacity=6,resultOffset=0,resultCapacity=6,lastScope="";
 function redraw() {
  if(!data||$("dashboard-view").hidden)return;
  const available=$("budget-month-chart").clientWidth;if(!available)return;
  const all=data.selected_months;
  const step=Math.max(160,...all.flatMap(r=>[r.total_income_cents,r.total_expense_cents]).map(v=>formatCents(v).length*12+28));
  capacity=Math.max(1,Math.min(6,Math.floor((available-78)/step)));
  offset=Math.min(offset,Math.max(0,data.selected_months.length-1));
  const rows=data.selected_months.slice(offset,offset+capacity);
  const max=Math.max(0,...all.flatMap(r=>[r.total_income_cents,r.total_expense_cents]));
  const width=Math.max(available,86+rows.length*step),person=data.responsible_code==="parents"?"Pai/Mãe":"Eu";
  const chart=canvas($("budget-month-chart"),0,max,width,`Receitas e despesas previstas de ${person}. Sólido: lançado; listrado: estimativa.` ,"budget");
  rows.forEach((row,i)=>{const mid=86+(i+.5)*(width-94)/rows.length;
   for(const [metric,shift,name] of [["income",-step/4,"Receitas"],["expense",step/4,"Despesas"]]) {
    const total=row[`total_${metric}_cents`],registered=row[`registered_${metric}_cents`],forecast=row[`forecast_${metric}_cents`],x=mid+shift;
    const g=action(`${name} de ${person} em ${monthText(row.month)}: ${formatCents(total)}. Lançado ${formatCents(registered)}; estimativa ${formatCents(forecast)}. Ver composição.`,()=>open(metric,row.month.slice(0,7)));
    g.setAttribute("data-value-kind",metric);
    g.append(node("rect",{x:x-27,y:24,width:54,height:224,fill:"transparent"}));
    if(registered)bar(chart,g,x,38,0,registered,metric);
    if(forecast)bar(chart,g,x,38,registered,total,metric,true);
    if(!total)bar(chart,g,x,38,0,0,metric);
    if($("outlook-labels").checked)label(chart,g,x,total);
    g.append(node("text",{x,y:232,"text-anchor":"middle"},name));chart.svg.append(g);
   }
   chart.svg.append(node("text",{x:mid,y:258,"text-anchor":"middle",class:"finance-month"},monthText(row.month)));
  });
  const text=`${monthText(rows[0].month)} a ${monthText(rows.at(-1).month)} · ${offset+1}–${offset+rows.length} de ${all.length} meses`;
  $("budget-page-label").textContent=text;$("budget-prev").disabled=!offset;$("budget-next").disabled=offset+capacity>=all.length;
  result(all,person);
 }
 function result(all,person) {
  const min=Math.min(0,...all.map(r=>r.result_cents)),max=Math.max(0,...all.map(r=>r.result_cents));
  const container=$("budget-result-chart"),step=Math.max(110,...all.map(r=>formatSignedCents(r.result_cents).length*7+24));
  resultCapacity=Math.max(1,Math.min(6,Math.floor((container.clientWidth-78)/step)));resultOffset=Math.min(resultOffset,all.length-1);
  const rows=all.slice(resultOffset,resultOffset+resultCapacity),width=Math.max(container.clientWidth,78+rows.length*step);
  const chart=canvas(container,min,max,width,`Sobra ou falta prevista de ${person}. Cada mês é independente, não é saldo bancário.`,"result");
  rows.forEach((r,i)=>{const x=78+(i+.5)*(width-86)/rows.length,v=r.result_cents,status=v>0?"Sobra":v<0?"Falta":"Equilíbrio";
   const g=action(`${monthText(r.month)}: ${status}, ${formatSignedCents(v)}. Ver resultado previsto.`,()=>open("result",r.month.slice(0,7)));
   g.setAttribute("data-value-kind",v<0?"expense":v>0?"income":"neutral");
   g.append(node("rect",{x:x-28,y:20,width:56,height:250,fill:"transparent"}));bar(chart,g,x,38,0,v,v<0?"expense":"income");
   if($("outlook-labels").checked)label(chart,g,x,v,v<0?chart.y(v)+17:chart.y(v)-10,(v>0?"+ ":"")+formatSignedCents(v));
   g.append(node("text",{x,y:250,"text-anchor":"middle"},monthText(r.month)),node("text",{x,y:272,"text-anchor":"middle"},status));chart.svg.append(g);
  });
  $("result-page-label").textContent=`${monthText(rows[0].month)} a ${monthText(rows.at(-1).month)} · ${resultOffset+1}–${resultOffset+rows.length} de ${all.length} meses`;
  $("result-prev").disabled=!resultOffset;$("result-next").disabled=resultOffset+resultCapacity>=all.length;
  $("budget-result-note").textContent=rows.some(r=>!r.total_income_cents)?"Há mês sem receitas cadastradas para este responsável. O resultado reflete somente as informações disponíveis.":"Cada coluna abre a composição do resultado. Use os botões para navegar pelo período.";
 }
 function tables() {
  $("budget-month-values").replaceChildren();$("budget-result-values").replaceChildren();
  for(const r of data.selected_months) {
   const tr=document.createElement("tr");cell(tr,monthText(r.month));
   for(const [field,metric,phase] of [["registered_income_cents","income","registered"],["forecast_income_cents","income","forecast"],["registered_expense_cents","expense","registered"],["forecast_expense_cents","expense","forecast"],["result_cents","result","all"]]) {const td=cell(tr,"");const b=document.createElement("button");b.className="link-button";b.textContent=formatSignedCents(r[field]);b.addEventListener("click",()=>open(metric,r.month.slice(0,7),{phase}));td.append(b);}$("budget-month-values").append(tr);
   const result=document.createElement("tr");cell(result,monthText(r.month));cell(result,r.result_cents>0?"Sobra":r.result_cents<0?"Falta":"Equilíbrio");cell(result,(r.result_cents>0?"+ ":"")+formatSignedCents(r.result_cents));$("budget-result-values").append(result);
  }
 }
 $("budget-prev").addEventListener("click",()=>{offset=Math.max(0,offset-capacity);redraw();});$("budget-next").addEventListener("click",()=>{offset+=capacity;redraw();});
 $("result-prev").addEventListener("click",()=>{resultOffset=Math.max(0,resultOffset-resultCapacity);redraw();});$("result-next").addEventListener("click",()=>{resultOffset+=resultCapacity;redraw();});
 new ResizeObserver(()=>redraw()).observe($("budget-month-chart"));
 return {render(value){const scope=`${value.month}-${value.horizon}-${value.responsible_code}`;if(scope!==lastScope){offset=0;resultOffset=0;lastScope=scope;}data=value;tables();requestAnimationFrame(redraw);},redraw};
}
