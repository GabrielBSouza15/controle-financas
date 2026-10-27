export function createMaintenanceView({api, notify}) {
  const $ = id => document.getElementById(id);
  const state = {key: crypto.randomUUID(), sequence: 0};
  async function refresh() {
    const sequence = ++state.sequence;
    $("backup-error").hidden = true;
    try {
      const data = await api("/maintenance/backups");
      if (sequence !== state.sequence) return;
      $("backup-active").textContent = `Banco em uso: ${data.active_database_name}. O backup não inclui o código, os documentos ou a planilha original.`;
      $("backup-location").textContent = `Pasta local: ${data.backup_root}`;
      $("backup-count").textContent = `${data.total || 0} cópia(s) concluída(s); exibindo até 20 recentes. A lista inclui bancos pessoais e de ensaio: confira a origem.`;
      $("backup-list").replaceChildren();
      for (const item of data.items) {
        const li = document.createElement("li"), title = document.createElement("strong"), detail = document.createElement("p");
        title.textContent = `${new Date(item.created_at).toLocaleString("pt-BR")} · ${item.source_database_name}`;
        detail.className = "help"; detail.textContent = `${item.revision} · ${item.folder}`;
        const button = document.createElement("button"); button.className = "secondary"; button.textContent = "Conferir cópia";
        button.setAttribute("aria-label", `Conferir cópia de ${item.source_database_name} criada em ${new Date(item.created_at).toLocaleString("pt-BR")}`);
        button.addEventListener("click", async () => {
          button.disabled = true;
          try {
            await api(`/maintenance/backups/${item.id}`);
            detail.textContent = `Conferida neste acesso: hash, estrutura, conteúdo e relacionamentos coincidem. Pasta: ${item.folder}`;
          } catch (error) {detail.textContent = `Cópia não confirmada: ${error.message}`; notify(error.message, true);}
          finally {button.disabled = false;}
        });
        li.append(title, detail, button); $("backup-list").append(li);
      }
      if (!data.items.length) {const li = document.createElement("li"); li.textContent = "Nenhum backup deste procedimento ainda. As cópias anteriores de desenvolvimento permanecem preservadas."; $("backup-list").append(li);}
      $("backup-incomplete").hidden = !data.incomplete_count;
      $("backup-incomplete").textContent = `${data.incomplete_count} pasta(s) não concluída(s) ou com manifesto inválido. Não use essas pastas para recuperar dados; crie e confira uma nova cópia.`;
    } catch (error) {
      if (sequence === state.sequence) {$("backup-error").textContent = error.message; $("backup-error").hidden = false;}
    }
  }
  $("backup-create").addEventListener("click", async () => {
    const button = $("backup-create"); if (button.disabled) return;
    button.disabled = true; $("backup-error").hidden = true; $("backup-status").hidden = false;
    $("backup-status").textContent = "Criando e conferindo uma cópia consistente…";
    try {
      const result = await api("/maintenance/backups", {request_key: state.key});
      state.key = crypto.randomUUID();
      $("backup-status").textContent = `Backup criado e conferido em ${new Date(result.created_at).toLocaleString("pt-BR")}. Preserve a pasta inteira: ${result.folder}`;
      await refresh();
    } catch (error) {
      $("backup-status").hidden = true; $("backup-error").textContent = error.message; $("backup-error").hidden = false;
    } finally {button.disabled = false;}
  });
  $("backup-refresh").addEventListener("click", refresh);
  return {refresh};
}
