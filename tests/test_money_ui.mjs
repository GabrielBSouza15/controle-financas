import test from "node:test";
import assert from "node:assert/strict";
import {parseReaisToCents,formatCents,formatSignedCents} from "../app/static/money.mjs";
test("entrada brasileira preserva centavos e limite seguro", () => {
  for (const [text, expected] of [["100,00",10000],["0,01",1],["61,12",6112],["52,3",5230],[" 12 ",1200],["90071992547409,91",Number.MAX_SAFE_INTEGER]]) assert.equal(parseReaisToCents(text), expected);
});
test("não inventa centavos ou aceita formatos ambíguos", () => {
  for (const value of ["61,125","100.00","1.000,00","0","-1","1e2","NaN","Infinity","","1,","90071992547409,92",true,1]) assert.throws(() => parseReaisToCents(value));
});
test("formata inclusive valores próximos ao limite sem ponto flutuante", () => {
  assert.equal(formatCents(3334),"R$ 33,34"); assert.equal(formatCents(0),"R$ 0,00");
  assert.equal(formatCents(Number.MAX_SAFE_INTEGER),"R$ 90.071.992.547.409,91");
  for (const value of [true,1.5,-1,Number.MAX_SAFE_INTEGER+1]) assert.throws(() => formatCents(value));
});

test("créditos e diferenças são exibidos com sinal e centavos exatos",()=>{
  assert.equal(formatSignedCents(-1),"−R$ 0,01");
  assert.equal(formatSignedCents(-Number.MAX_SAFE_INTEGER),"−R$ 90.071.992.547.409,91");
  assert.equal(formatSignedCents(0),"R$ 0,00");
  for(const invalid of [true,1.5,Number.MAX_SAFE_INTEGER+1]) assert.throws(()=>formatSignedCents(invalid));
});
