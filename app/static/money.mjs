// Conversão da entrada brasileira com inteiros: nenhuma multiplicação monetária em float.
export function parseReaisToCents(text) {
  if (typeof text !== "string" || !/^[0-9]+(?:,[0-9]{1,2})?$/.test(text.trim())) {
    throw new Error("Informe o valor sem milhar, usando vírgula para os centavos. Ex.: 100,50.");
  }
  const [whole, fraction = ""] = text.trim().split(",");
  const cents = BigInt(whole) * 100n + BigInt(fraction.padEnd(2, "0"));
  if (cents <= 0n || cents > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error("Informe um valor positivo dentro do limite suportado.");
  return Number(cents);
}
export function formatCents(cents) {
  if (!Number.isSafeInteger(cents) || cents < 0) throw new Error("Valor monetário inválido.");
  const amount = BigInt(cents);
  return `R$ ${(amount / 100n).toLocaleString("pt-BR")},${String(amount % 100n).padStart(2, "0")}`;
}

export function formatSignedCents(cents) {
  if (!Number.isSafeInteger(cents)) throw new Error("Valor monetário inválido.");
  return `${cents < 0 ? "−" : ""}${formatCents(Math.abs(cents))}`;
}
