import test from "node:test";
import assert from "node:assert/strict";
import {plotY} from "../app/static/chart_geometry.mjs";
test("zero, positive and negative credits stay on an exact bounded scale",()=>{
  assert.equal(plotY(0,0,10000,200),200);
  assert.equal(plotY(10000,0,10000,200),0);
  assert.equal(plotY(2500,0,10000,200),150);
  assert.equal(plotY(0,-10000,10000,200),100);
  assert.equal(plotY(-10000,-10000,10000,200),200);
});
test("mixed extremes exceed Number span safely using BigInt",()=>{
  const max=Number.MAX_SAFE_INTEGER;
  assert.equal(plotY(0,-max,max,200),100);
  assert.equal(plotY(max,-max,max,200),0);
  assert.equal(plotY(-max,-max,max,200),200);
});
test("invalid geometry is rejected; a flat scale remains visible",()=>{
  assert.equal(plotY(0,0,0,200),200);
  for(const args of [[0,0,1,0],[2,0,1,200],[0.1,0,1,200],[0,1,0,200],[NaN,0,1,200]])
    assert.throws(()=>plotY(...args),RangeError);
});
