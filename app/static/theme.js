/* Preferência somente neste navegador. Sem acesso a dados financeiros. */
(() => {
  let theme = "dark";
  try { if (localStorage.getItem("finance-theme") === "light") theme = "light"; } catch {}
  document.documentElement.dataset.theme = theme;
  document.addEventListener("DOMContentLoaded", () => {
    const button = document.getElementById("theme-toggle");
    function label() {
      const dark = document.documentElement.dataset.theme === "dark";
      button.textContent = dark ? "☀ Tema claro" : "☾ Tema escuro";
      button.setAttribute("aria-label", dark ? "Ativar tema claro" : "Ativar tema escuro");
    }
    button.addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("finance-theme", next); } catch {}
      label();
    });
    label();
  });
})();
