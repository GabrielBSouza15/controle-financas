// SVG helpers: only geometry/formatting; financial totals come from Python.
import {formatSignedCents} from "/static/money.mjs";
import {plotY} from "/static/chart_geometry.mjs";
const NS="http://www.w3.org/2000/svg";
export const monthText=s=>s.slice(5,7)+"/"+s.slice(0,4);
export function node(tag,attrs={},text="") {const el=document.createElementNS(NS,tag);for(const [key,value] of Object.entries(attrs))el.setAttribute(key,value);if(text)el.textContent=text;return el;}
export function cell(row,text) {const td=document.createElement("td");td.textContent=text;row.append(td);return td;}
export function action(label,open) {const g=node("g",{role:"button",tabindex:0,"aria-label":label,class:"finance-column"});g.append(node("title",{},label));g.addEventListener("click",open);g.addEventListener("keydown",e=>{if(["Enter"," "].includes(e.key)){e.preventDefault();open();}});return g;}
function axisText(value) {const sign=value<0?"−":"",v=BigInt(Math.abs(value));const unit=v>=100000000n?100000000n:v>=100000n?100000n:0n;return unit?`${sign}R$ ${v/unit},${v%unit*10n/unit} ${unit===100000n?"mil":"mi"}`:formatSignedCents(value);}
export function canvas(container,minimum,maximum,width,label,prefix) {
 const min=Math.min(0,minimum),max=Math.max(0,maximum),svg=node("svg",{viewBox:`0 0 ${width} 290`,class:"finance-svg",role:"group","aria-label":label});svg.style.minWidth=width+"px";
 const y=v=>40+plotY(v,min,max===min?1:max,170);
 const defs=node("defs");
 for(const series of ["income","expense","self","parents"]) {const pattern=node("pattern",{id:prefix+"-"+series,width:7,height:7,patternUnits:"userSpaceOnUse"});pattern.style.color=`var(--chart-${series})`;pattern.append(node("rect",{width:7,height:7,fill:"var(--surface)"}),node("path",{d:"M-2 2L2 -2M0 7L7 0M5 9L9 5",stroke:"currentColor","stroke-width":2}));defs.append(pattern);}svg.append(defs);
 for(const value of [...new Set([max,0,min])]) {const yy=y(value);svg.append(node("line",{x1:78,x2:width-8,y1:yy,y2:yy,class:value===0?"finance-zero":"finance-gridline"}),node("text",{x:70,y:yy+4,"text-anchor":"end"},axisText(value)));}
 container.replaceChildren(svg);return {svg,y,prefix};
}
export function bar(chart,group,x,width,start,end,series,forecast=false) {const a=chart.y(start),b=chart.y(end);group.append(node("rect",{x:x-width/2,y:Math.min(a,b),width,height:Math.max(1,Math.abs(a-b)),rx:2,fill:forecast?`url(#${chart.prefix}-${series})`:`var(--chart-${series})`,...(forecast?{stroke:`var(--chart-${series})`,"stroke-width":1}: {stroke:"var(--surface)","stroke-width":1})}));}
export function label(chart,group,x,value,y,text=formatSignedCents(value)) {group.append(node("text",{x,y:y??chart.y(value)-10,"text-anchor":"middle",class:"finance-value"},text));}
