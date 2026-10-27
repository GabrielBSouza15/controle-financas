import {parseReaisToCents} from "./money.mjs";

export function plainCents(cents) {
  if (!Number.isSafeInteger(cents) || cents < 0) throw new Error("Valor monetário inválido.");
  const value = BigInt(cents);
  return `${value/100n},${String(value%100n).padStart(2,"0")}`;
}

export function hasAllocationText(text) {
  return !!text.trim() && !/^0+(?:,0{1,2})?$/.test(text.trim());
}

export function sumAllocationTexts(texts) {
  let sum = 0n;
  for (const text of texts) if (hasAllocationText(text)) sum += BigInt(parseReaisToCents(text));
  if (sum > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error("A soma excede a faixa exata da interface.");
  return Number(sum);
}

export function distribute(cents, available) {
  if (!Number.isSafeInteger(cents) || cents < 0) throw new Error("Valor de distribuição inválido.");
  let remaining = BigInt(cents);
  return available.map(value => {
    if (!Number.isSafeInteger(value) || value < 0) throw new Error("Limite de parcela inválido.");
    const applied = remaining < BigInt(value) ? remaining : BigInt(value);
    remaining -= applied; return Number(applied);
  });
}
