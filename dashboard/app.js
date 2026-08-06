import { createClient } from "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.57.0/+esm";

const config = window.__LAB_CONFIG__ || {};
const supabase = createClient(config.supabaseUrl, config.supabasePublishableKey);
const $ = (selector) => document.querySelector(selector);
const state = { session: null, runs: [], selectedRun: null, timer: null };

const authPanel = $("#auth-panel");
const dashboard = $("#dashboard");
const authMessage = $("#auth-message");
const runMessage = $("#run-message");

function setMessage(element, message, kind = "") {
  element.textContent = message;
  element.className = `form-message${kind ? ` is-${kind}` : ""}`;
}

function setToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("is-visible");
  window.setTimeout(() => toast.classList.remove("is-visible"), 3200);
}

function makeRunKey() {
  const stamp = new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 14);
  return `dashboard-preflight-${stamp}`;
}

function setSessionView(session) {
  state.session = session;
  authPanel.hidden = Boolean(session);
  dashboard.hidden = !session;
  $("#sign-out").hidden = !session;
  if (session) {
    $("#run-key").value = makeRunKey();
    loadDashboard();
  } else {
    window.clearTimeout(state.timer);
  }
}

function statusClass(status) {
  return `status-pill status-pill--${status || "queued"}`;
}

function statusLabel(status) {
  return { queued: "queued", claimed: "claimed", running: "running", succeeded: "succeeded", failed: "failed", cancelled: "cancelled" }[status] || status || "unknown";
}

function formatDate(value, withDate = false) {
  if (!value) return "—";
  const date = new Date(value);
  return new Intl.DateTimeFormat("pt-BR", withDate ? { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" } : { hour: "2-digit", minute: "2-digit" }).format(date);
}

function manifestLabel(value) {
  return value ? value.replace("indicator-lab-", "").replaceAll("-", " ") : "—";
}

async function loadDashboard() {
  if (!state.session) return;
  const refreshState = $("#refresh-state");
  refreshState.classList.add("is-syncing");
  try {
    const [runsResult, workersResult] = await Promise.all([
      supabase.rpc("dashboard_list_runs", { p_limit: 30 }),
      supabase.rpc("dashboard_list_workers"),
    ]);
    if (runsResult.error) throw runsResult.error;
    if (workersResult.error) throw workersResult.error;
    state.runs = runsResult.data || [];
    renderRuns();
    renderWorkers(workersResult.data || []);
    renderPulse();
    if (state.selectedRun) await loadRunDetail(state.selectedRun.run_id, false);
    refreshState.innerHTML = '<span class="status-dot status-dot--live"></span>Atualizado agora';
  } catch (error) {
    refreshState.innerHTML = '<span class="status-dot"></span>Falha de leitura';
    setToast(error.message || "Não foi possível ler o control plane.");
  } finally {
    refreshState.classList.remove("is-syncing");
    window.clearTimeout(state.timer);
    state.timer = window.setTimeout(loadDashboard, 5000);
  }
}

function renderWorkers(workers) {
  const worker = workers[0];
  $("#worker-status").textContent = worker?.status || "offline";
  $("#worker-heartbeat").textContent = worker?.last_heartbeat_at ? `heartbeat ${formatDate(worker.last_heartbeat_at)}` : "sem heartbeat";
  $("#queue-count").textContent = state.runs.filter((run) => run.status === "queued").length;
}

function renderRuns() {
  const body = $("#runs-body");
  $("#run-count").textContent = `${state.runs.length} registro${state.runs.length === 1 ? "" : "s"}`;
  if (!state.runs.length) {
    body.innerHTML = '<tr><td colspan="6" class="table-empty">Nenhuma run pertence a esta sessão ainda.</td></tr>';
    return;
  }
  body.innerHTML = state.runs.map((run) => `
    <tr data-run-id="${run.run_id}">
      <td><span class="run-name">${escapeHtml(run.run_key)}</span><span class="table-subline">${escapeHtml(run.run_type)}</span></td>
      <td><span class="${statusClass(run.status)}"><span class="status-dot"></span>${statusLabel(run.status)}</span></td>
      <td>${escapeHtml(manifestLabel(run.dataset_manifest))}</td>
      <td class="mono">${escapeHtml(run.worker_id || "—")}</td>
      <td>${formatDate(run.updated_at, true)}</td>
      <td><button class="row-open" type="button" data-run-id="${run.run_id}" aria-label="Abrir detalhes de ${escapeHtml(run.run_key)}">→</button></td>
    </tr>`).join("");
  body.querySelectorAll("button[data-run-id]").forEach((button) => button.addEventListener("click", () => loadRunDetail(button.dataset.runId)));
}

function renderPulse() {
  const rail = $("#pulse-rail");
  const latest = state.runs[0];
  if (!latest) {
    rail.innerHTML = '<div class="pulse-line"></div><div class="pulse-empty">Enfileire um comando para acompanhar sua passagem pela máquina.</div>';
    return;
  }
  const steps = [
    ["queued", "Comando recebido", "Idempotência registrada"],
    ["claimed", "Worker reivindicou", latest.worker_id || "Aguardando worker"],
    ["running", "Execução em curso", latest.dataset_manifest || "Manifesto desconhecido"],
    [latest.status, latest.status === "succeeded" ? "Artefato validado" : latest.status === "failed" ? "Execução interrompida" : "Aguardando conclusão", latest.finished_at ? formatDate(latest.finished_at, true) : "estado atual"],
  ];
  const terminalIndex = steps.findIndex((step) => step[0] === latest.status);
  const visible = steps.slice(0, Math.max(terminalIndex + 1, 2));
  rail.innerHTML = `<div class="pulse-line"></div>${visible.map(([status, title, copy]) => `<div class="pulse-step ${status === "failed" ? "pulse-step--failed" : ""}"><time class="pulse-time">${status}</time><div class="pulse-content"><strong>${title}</strong><span>${escapeHtml(copy)}</span></div></div>`).join("")}`;
}

async function loadRunDetail(runId, scroll = true) {
  const run = state.runs.find((item) => item.run_id === runId);
  if (!run) return;
  state.selectedRun = run;
  const [eventsResult, artifactsResult] = await Promise.all([
    supabase.rpc("dashboard_list_run_events", { p_run_id: runId }),
    supabase.rpc("dashboard_list_run_artifacts", { p_run_id: runId }),
  ]);
  if (eventsResult.error || artifactsResult.error) {
    setToast((eventsResult.error || artifactsResult.error).message);
    return;
  }
  $("#detail-section").hidden = false;
  $("#detail-title").textContent = run.run_key;
  const events = eventsResult.data || [];
  const artifacts = artifactsResult.data || [];
  $("#event-list").innerHTML = events.length ? events.map((event) => `<div class="event-row"><time class="event-time">${formatDate(event.created_at)}</time><div class="event-copy"><strong>${escapeHtml(event.event_type)}</strong><span>${escapeHtml(event.message)}</span></div></div>`).join("") : '<p class="detail-empty">Nenhum evento visível.</p>';
  $("#artifact-list").innerHTML = artifacts.length ? artifacts.map((artifact) => `<div class="artifact-row"><strong>${escapeHtml(artifact.artifact_type)}</strong><span title="${escapeHtml(artifact.sha256 || "")}">${escapeHtml(artifact.sha256 || "sem hash")}</span></div>`).join("") : '<p class="detail-empty">Nenhum artefato registrado.</p>';
  if (scroll) $("#detail-section").scrollIntoView({ behavior: "smooth", block: "start" });
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
}

async function authenticate(mode) {
  const email = $("#auth-email").value.trim();
  const password = $("#auth-password").value;
  setMessage(authMessage, "Processando…");
  const result = mode === "signup"
    ? await supabase.auth.signUp({ email, password })
    : await supabase.auth.signInWithPassword({ email, password });
  if (result.error) {
    setMessage(authMessage, result.error.message, "error");
    return;
  }
  if (mode === "signup" && !result.data.session) {
    setMessage(authMessage, "Acesso criado. Confirme o e-mail para entrar.", "success");
    return;
  }
  setMessage(authMessage, "");
}

$("#auth-form").addEventListener("submit", (event) => { event.preventDefault(); authenticate("signin"); });
$("#create-account").addEventListener("click", () => authenticate("signup"));
$("#sign-out").addEventListener("click", () => supabase.auth.signOut());
$("#refresh-button").addEventListener("click", loadDashboard);
$("#regenerate-key").addEventListener("click", () => { $("#run-key").value = makeRunKey(); });
$("#close-detail").addEventListener("click", () => { state.selectedRun = null; $("#detail-section").hidden = true; });
$("#run-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const key = $("#run-key").value.trim();
  if (!key) {
    $("#run-key").setAttribute("aria-invalid", "true");
    return setMessage(runMessage, "Informe uma chave de idempotência.", "error");
  }
  $("#run-key").removeAttribute("aria-invalid");
  setMessage(runMessage, "Enfileirando…");
  const { data, error } = await supabase.rpc("dashboard_enqueue_preflight", { p_idempotency_key: key, p_run_key: key, p_requested_by: "dashboard" });
  if (error) return setMessage(runMessage, error.message, "error");
  setMessage(runMessage, data?.[0]?.existing ? "Essa chave já existe; a run original foi preservada." : "Preflight enfileirado.", "success");
  setToast(data?.[0]?.existing ? "Idempotência preservada" : "Comando enviado ao worker");
  await loadDashboard();
});

window.addEventListener("keydown", (event) => { if (event.key.toLowerCase() === "r" && !event.metaKey && !event.ctrlKey && document.activeElement.tagName !== "INPUT") loadDashboard(); });

supabase.auth.onAuthStateChange((_event, session) => setSessionView(session));
supabase.auth.getSession().then(({ data }) => setSessionView(data.session));
