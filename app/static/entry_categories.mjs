// A categoria é um cadastro auxiliar; a API financeira continua recebendo seu ID.
function findCategory(categories, name) {
  const exact = categories.find(category => category.name === name);
  if (exact) return exact;
  const matches = categories.filter(category => category.name.toLocaleLowerCase("pt-BR") === name.toLocaleLowerCase("pt-BR"));
  if (matches.length > 1) throw new Error("Há categorias com nomes semelhantes. Escolha uma sugestão com a grafia exata.");
  return matches[0];
}

export async function resolveCategory(value, {getCategories, createCategory, reloadCategories, onCreated}) {
  const name = value.trim();
  if (!name) return null;
  const existing = findCategory(getCategories(), name);
  if (existing) return existing.id;
  let category;
  try {
    category = await createCategory(name);
  } catch (error) {
    // Uma resposta perdida ou outro cadastro simultâneo não exige criar outro nome.
    try { await reloadCategories(); } catch { throw error; }
    const recovered = findCategory(getCategories(), name);
    if (!recovered) throw error;
    return recovered.id;
  }
  onCreated(category);
  return category.id;
}
