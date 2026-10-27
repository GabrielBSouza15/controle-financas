import test from "node:test";
import assert from "node:assert/strict";
import {plainCents,sumAllocationTexts,distribute} from "../app/static/settlement_money.mjs";
test("vínculos são somados sem perda de centavos",()=>{assert.equal(sumAllocationTexts(["1500,00","1000,00","500,00","0","0,00",""]),300000);assert.equal(sumAllocationTexts(["33,34","33,33","33,33",""]),10000);assert.equal(plainCents(Number.MAX_SAFE_INTEGER),"90071992547409,91");});
test("distribuição explícita respeita limite e ordem da página",()=>{assert.deepEqual(distribute(40000,[100000,50000]),[40000,0]);assert.deepEqual(distribute(150001,[150000,1,100]),[150000,1,0]);assert.deepEqual(distribute(100,[30,20]),[30,20]);});
test("distribuição não aceita float, bool, excesso ou fração de centavo",()=>{for(const values of [["0,001"],["90071992547409,91","0,01"]])assert.throws(()=>sumAllocationTexts(values));for(const cents of [true,1.5,-1,Number.MAX_SAFE_INTEGER+1])assert.throws(()=>distribute(cents,[100]));assert.throws(()=>distribute(10,[1.5]));});
