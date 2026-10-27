import test from "node:test";
import assert from "node:assert/strict";
import {widthPercent} from "../app/static/dashboard_chart.mjs";
test("proporções visuais preservam limites e centavos sem multiplicar floats", () => {
  assert.equal(widthPercent(1, 3), "33.33%");
  assert.equal(widthPercent(0, 0), "0.00%");
  assert.equal(widthPercent(4, 3), "100.00%");
  assert.equal(widthPercent(Number.MAX_SAFE_INTEGER - 1, Number.MAX_SAFE_INTEGER), "99.99%");
});
test("escala visual rejeita valores monetários inválidos", () => {
  for (const value of [-1, true, 0.1, Infinity, Number.MAX_SAFE_INTEGER + 1]) assert.throws(() => widthPercent(value, 100));
  assert.throws(() => widthPercent(1, -1));
});
