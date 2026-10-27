// Proporções visuais em inteiros; nenhum total financeiro é calculado aqui.
export function widthPercent(value, maximum) {
  if (!Number.isSafeInteger(value) || value < 0 ||
      !Number.isSafeInteger(maximum) || maximum < 0) throw new Error("Escala visual inválida.");
  if (!maximum) return "0.00%";
  const basis = BigInt(Math.min(value, maximum)) * 10000n / BigInt(maximum);
  return `${basis / 100n}.${String(basis % 100n).padStart(2, "0")}%`;
}
