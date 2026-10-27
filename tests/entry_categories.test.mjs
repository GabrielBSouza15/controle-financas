import test from "node:test";
import assert from "node:assert/strict";
import {resolveCategory} from "../app/static/entry_categories.mjs";

function context(categories = []) {
  return {
    getCategories: () => categories,
    createCategory: async () => {throw new Error("Criação inesperada");},
    reloadCategories: async () => {},
    onCreated: category => categories.push(category),
  };
}
test("categoria opcional e categoria existente não criam outro cadastro", async () => {
  const c = context([{id: 7, name: "Educação"}]);
  assert.equal(await resolveCategory("   ", c), null);
  assert.equal(await resolveCategory(" educação ", c), 7);
});
test("nome novo é criado uma vez e reutilizado em nova tentativa", async () => {
  const c = context(); let calls = 0;
  c.createCategory = async name => {calls++; assert.equal(name, "Lazer"); return {id: 8, name};};
  assert.equal(await resolveCategory(" Lazer ", c), 8);
  assert.equal(await resolveCategory("Lazer", c), 8);
  assert.equal(calls, 1);
});
test("resposta perdida é recuperada pela consulta sem duplicar cadastro", async () => {
  let categories = [];
  const c = {...context(), getCategories: () => categories,
    createCategory: async () => {throw new Error("Resposta perdida");},
    reloadCategories: async () => {categories = [{id: 9, name: "Casa"}];}};
  assert.equal(await resolveCategory("Casa", c), 9);
});
test("falha de criação ou de recarga permanece erro e não retorna categoria falsa", async () => {
  const original = new Error("Sem conexão");
  const c = {...context(), createCategory: async () => {throw original;}};
  await assert.rejects(resolveCategory("Casa", c), error => error === original);
  c.reloadCategories = async () => {throw new Error("Consulta indisponível");};
  await assert.rejects(resolveCategory("Casa", c), error => error === original);
});
test("nomes semelhantes preservam a escolha exata e pedem conferir ambiguidades", async () => {
  const c = context([{id: 1, name: "Casa"}, {id: 2, name: "CASA"}]);
  assert.equal(await resolveCategory("CASA", c), 2);
  await assert.rejects(resolveCategory("casa", c), /grafia exata/);
});
