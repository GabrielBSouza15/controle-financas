import {formatCents,formatSignedCents} from "/static/money.mjs";
import {canvas,node,cell,action,bar,label,monthText} from "/static/chart_render.js";
export function createCardCharts(open) {
 const $=id=>document.getElementById(id);let data=null,offset=0,capacity=6,scope="";
 const personName=code=>code==="self"?"Eu":code==="parents"?"Pai/Mãe":"Todos";
 const bankColor=card=>card.name==="Nubank"?"nubank":"mercado";
 function filters(card){return {card_id:card.id,responsible_code:data.selected_responsible_code};}
 function redraw() {
  if(!data||$("dashboard-view").hidden)return;
  const container=$("outlook-card-chart");if(!container.clientWidth)return;
  container.replaceChildren();
  if(!data.cards.length){$("card-prev").disabled=true;$("card-next").disabled=true;$("card-page-label").textContent="";return;}
  const all=data.months.flatMap(r=>r.cards.map(c=>c.selected_plot));
  const columnStep=Math.max(82,...all.map(r=>formatSignedCents(r.total_cents).length*7+18));
  const step=columnStep*data.cards.length+24;
  capacity=Math.max(1,Math.min(6,Math.floor((container.clientWidth-78)/step)));offset=Math.min(offset,Math.max(0,data.months.length-1));
  const rows=data.months.slice(offset,offset+capacity);
  const min=Math.min(0,...all.map(r=>r.plot_min_cents)),max=Math.max(0,...all.map(r=>r.plot_max_cents));
  const width=Math.max(container.clientWidth,86+rows.length*step);
  const person=personName(data.selected_responsible_code);
  const chart=canvas(container,min,max,width,`Nubank e Mercado Pago/Livre por mês. Ver de: ${person}. Sólido: lançado; listrado: estimativa.`,"banks");
  rows.forEach((r,i)=>{
   const mid=86+(i+.5)*(width-94)/rows.length;
   data.cards.forEach((card,j)=>{
    const c=r.cards.find(c=>c.card_id===card.id),p=c.selected_plot,x=mid+(j-(data.cards.length-1)/2)*columnStep;
    const title=`${card.name}, ${monthText(r.month)}. ${person}. Lançado: ${formatSignedCents(c.selected_registered_cents)}. Estimativa: ${formatCents(c.selected_forecast_cents)}. ${data.selected_responsible_code==="all"?"Total previsto":"Parte prevista"}: ${formatSignedCents(p.total_cents)}. Ver composição.`;
    const g=action(title,()=>open("card",r.month.slice(0,7),filters(card),card.name+" · "+person));
    g.append(node("rect",{x:x-27,y:20,width:54,height:250,fill:"transparent"}));
    for(const s of p.segments)if(s.amount_cents)bar(chart,g,x,34,s.start_cents,s.end_cents,s.person==="credit"?"credit":bankColor(card),s.phase==="forecast");
    if(!p.plot_max_cents&&!p.plot_min_cents)bar(chart,g,x,34,0,0,bankColor(card));
    if($("outlook-labels").checked)label(chart,g,x,p.total_cents,p.total_cents<0?chart.y(p.total_cents)+18:chart.y(p.plot_max_cents)-10);
    g.append(node("text",{x,y:246,"text-anchor":"middle"},card.name==="Nubank"?"Nubank":"Mercado Pago"));chart.svg.append(g);
   });
   chart.svg.append(node("text",{x:mid,y:273,"text-anchor":"middle",class:"finance-month"},monthText(r.month)));
  });
  $("card-page-label").textContent=`${monthText(rows[0].month)} a ${monthText(rows.at(-1).month)} · mesma escala entre bancos`;
  $("card-prev").disabled=!offset;$("card-next").disabled=offset+capacity>=data.months.length;
 }
 $("card-prev").addEventListener("click",()=>{offset=Math.max(0,offset-capacity);redraw();});$("card-next").addEventListener("click",()=>{offset+=capacity;redraw();});
 let lastWidth=0;new ResizeObserver(entries=>{const width=Math.floor(entries[0].contentRect.width);if(width!==lastWidth){lastWidth=width;redraw();}}).observe($("outlook-card-chart"));
 return {render(value){const next=value.months[0].month+"-"+value.months.length+"-"+value.selected_responsible_code;if(next!==scope){offset=0;scope=next;}data=value;
  $("outlook-card-filter").value=value.selected_responsible_code;
  $("outlook-no-cards").hidden=!!value.cards.length;
  const partial=value.selected_responsible_code!=="all";
  $("outlook-credit-note").hidden=!partial&&!value.months.some(r=>r.cards.some(c=>c.credits_cents));
  $("outlook-credit-note").textContent=partial?"Parte prevista do responsável selecionado; não é o total cobrado pelo banco. Créditos entre faturas aparecem somente em Todos, sem atribuição a um responsável.":"Créditos entre faturas aparecem em cinza e podem reduzir a cobrança abaixo de zero. Eles não são uma nova redução de despesa.";
  $("outlook-card-values").replaceChildren();for(const month of value.months)for(const c of month.cards){const tr=document.createElement("tr");cell(tr,c.card_name+" · "+monthText(month.month));for(const f of ["selected_registered_cents","selected_forecast_cents","selected_credits_cents"])cell(tr,formatSignedCents(c[f]));const td=cell(tr,"");const b=document.createElement("button");b.className="link-button";b.textContent=formatSignedCents(c.selected_plot.total_cents);b.addEventListener("click",()=>open("card",month.month.slice(0,7),{card_id:c.card_id,responsible_code:value.selected_responsible_code},c.card_name+" · "+personName(value.selected_responsible_code)));td.append(b);$("outlook-card-values").append(tr);}
  requestAnimationFrame(redraw);
 },redraw};
}
