/* Somente geometria. Centavos e totais chegam calculados pelo servidor. */
export function plotY(value, min, max, height) {
  if (![value,min,max,height].every(Number.isSafeInteger) || height <= 0 || max < min || value < min || value > max)
    throw new RangeError("Escala gráfica inválida.");
  const span = BigInt(max) - BigInt(min);
  if (span === 0n) return height;
  return Number((BigInt(max) - BigInt(value)) * BigInt(height) * 1000n / span) / 1000;
}
