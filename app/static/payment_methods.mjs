export function paymentMethodLabel(value) {
  return value === "credit" ? "Crédito" : ["pix","cash","pix_debit"].includes(value) ? "PIX/Débito" : "Meio não informado";
}
export function paymentMethodValue(value) {
  return value === "credit" ? "credit" : ["pix","cash","pix_debit"].includes(value) ? "pix_debit" : "";
}

export function needsParentsRepayment(person, kind, method) {
  return person === "parents" && kind === "expense" && ["pix","cash","pix_debit"].includes(method);
}
