import {formatCents,formatSignedCents} from "/static/money.mjs";
import {canvas,node,cell,action,bar,label,monthText} from "/static/chart_render.js";
export function createCardCharts(open) {
 const $=id=>document.getElementById(id);let data=null,offset=0,capacity=6,scope="";
 function redraw() {
  if(!data||$("dashboard-view").hidden)return;
  const container=$("outlook-card-chart");if(!container.clientWidth)return;
  const all=data.months.flatMap(r=>r.cards.map(c=>c.plot));
  const step=Math.max(116,...all.map(r=>formatSignedCents(r.total_cents).length*7+20));
  capacity=Math.max(1,Math.min(6,Math.floor((container.clientWidth-78)/step)));offset=Math.min(offset,Math.max(0,data.months.length-1));
  const rows=data.months.slice(offset,offset+capacity);container.replaceChildren();
  const min=Math.min(0,...all.map(r=>r.plot_min_cents)),max=Math.max(0,...all.map(r=>r.plot_max_cents));
  const width=Math.max(container.clientWidth,86+rows.length*step);
  for(const card of data.cards) {
   const panel=document.createElement("section"),heading=document.createElement("h3"),plot=document.createElement("div");panel.className="card-chart-panel";plot.className="chart-viewport";heading.textContent=card.name;panel.append(heading,plot);container.append(panel);
   const chart=canvas(plot,min,max,width,`${card.name}: Eu e Pai/Mãe, cobrança completa. Sólido: lançado, listrado: estimativa.`,"card-"+card.id);
   rows.forEach((r,i)=>{const c=r.cards.find(c=>c.card_id===card.id),x=86+(i+.5)*(width-94)/rows.length;
    const g=action(`${card.name}, ${monthText(r.month)}. Eu: ${formatCents(c.self_cents)}. Pai/Mãe: ${formatCents(c.parents_cents)}. Crédito: ${formatSignedCents(c.credits_cents)}. Total previsto: ${formatSignedCents(c.total_cents)}. Ver composição.`,()=>open("card",r.month.slice(0,7),{card_id:card.id},card.name));
    g.append(node("rect",{x:x-27,y:20,width:54,height:250,fill:"transparent"}));
    for(const s of c.plot.segments)if(s.amount_cents)bar(chart,g,x,42,s.start_cents,s.end_cents,s.person,s.phase==="forecast");
    if(!c.plot.plot_max_cents&&!c.plot.plot_min_cents)bar(chart,g,x,42,0,0,"self");
    if($("outlook-labels").checked)label(chart,g,x,c.total_cents,chart.y(c.plot.plot_max_cents)-10);
    g.append(node("text",{x,y:250,"text-anchor":"middle"},monthText(r.month)));chart.svg.append(g);
   });
  }
  $("card-page-label").textContent=`${monthText(rows[0].month)} a ${monthText(rows.at(-1).month)} · mesma escala entre cartões`;
  $("card-prev").disabled=!offset;$("card-next").disabled=offset+capacity>=data.months.length;
 }
 $("card-prev").addEventListener("click",()=>{offset=Math.max(0,offset-capacity);redraw();});$("card-next").addEventListener("click",()=>{offset+=capacity;redraw();});
 let lastWidth=0;new ResizeObserver(entries=>{const width=Math.floor(entries[0].contentRect.width);if(width!==lastWidth){lastWidth=width;redraw();}}).observe($("outlook-card-chart"));
 return {render(value){const next=value.months[0].month+"-"+value.months.length+"-"+value.selected_card_id;if(next!==scope){offset=0;scope=next;}data=value;
  const select=$("outlook-card-filter"),selected=value.selected_card_id;select.replaceChildren(new Option("Todos os cartões",""));for(const card of value.available_cards)select.add(new Option(card.name,card.id));select.value=selected===null?"":String(selected);
  $("outlook-no-cards").hidden=!!value.cards.length;$("outlook-credit-note").hidden=!value.months.some(r=>r.cards.some(c=>c.credits_cents));
  $("outlook-card-values").replaceChildren();for(const month of value.months)for(const c of month.cards){const tr=document.createElement("tr");cell(tr,c.card_name+" · "+monthText(month.month));for(const f of ["self_cents","parents_cents","credits_cents"])cell(tr,formatSignedCents(c[f]));const td=cell(tr,"");const b=document.createElement("button");b.className="link-button";b.textContent=formatSignedCents(c.total_cents);b.addEventListener("click",()=>open("card",month.month.slice(0,7),{card_id:c.card_id},c.card_name));td.append(b);$("outlook-card-values").append(tr);}
  requestAnimationFrame(redraw);
 },redraw};
}
