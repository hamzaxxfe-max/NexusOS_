"use strict";

const state = {
  apps: [],
  filter: "all",
  search: "",
  queue: new Map(),
};

const $ = (sel) => document.querySelector(sel);

const ICONS = {
  steam: "🎮", lutris: "🐧", heroic: "⚡", bottles: "🍾", wine: "🍷",
  protonup: "🛠️", mangohud: "📊", gamescope: "🖥️", code: "🧑‍💻",
  docker: "🐳", git: "🔀", python: "🐍", firefox: "🦊",
  libreoffice: "📄", gimp: "🎨", obs: "🎥", retroarch: "👾",
  discord: "💬",
};

const CATEGORY_LABELS = {
  "game-platform": "Game Platforms",
  "gaming-tools": "Gaming Tools",
  compatibility: "Compatibility",
  development: "Development",
  productivity: "Productivity",
  creative: "Creative",
  communication: "Communication",
};

function iconOf(app) {
  return ICONS[app.icon] || "📦";
}

function providerLabel(app) {
  const labels = { pacman: "Arch", flatpak: "Flatpak", steam: "Steam", epic: "Epic" };
  return labels[app.provider] || app.provider;
}

function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.add("hidden"), 2600);
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

let cachedSecret = null;

async function ensureSecret() {
  const health = await api("/api/auth/check", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ secret: cachedSecret || "" }),
  });
  if (!health.enabled) return null;
  if (health.ok) return cachedSecret;
  const secret = prompt("Enter the system secret to continue:");
  if (secret === null) throw new Error("cancelled");
  const r = await api("/api/auth/check", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ secret }),
  });
  if (!r.ok) throw new Error("wrong secret");
  cachedSecret = secret;
  return secret;
}

async function installApi(appId, secret) {
  const res = await fetch(`/api/install/${appId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ secret: secret || "" }),
  });
  if (!res.ok) {
    if (res.status === 401) throw new Error("unauthorized");
    throw new Error(`HTTP ${res.status}`);
  }
  return res.json();
}

function renderFilters() {
  const cats = [...new Set(state.apps.map((a) => a.category))].sort();
  const labels = { all: "All" };
  for (const c of cats) labels[c] = CATEGORY_LABELS[c] || c;

  const box = $("#filters");
  box.innerHTML = "";
  for (const c of ["all", ...cats]) {
    const chip = document.createElement("button");
    chip.className = "chip" + (c === state.filter ? " active" : "");
    chip.textContent = labels[c];
    chip.onclick = () => {
      state.filter = c;
      box.querySelectorAll(".chip").forEach((x) => x.classList.remove("active"));
      chip.classList.add("active");
      renderGrid();
    };
    box.appendChild(chip);
  }
}

function statusFor(id) {
  return state.queue.get(id) || null;
}

function renderCard(app) {
  const card = document.createElement("div");
  card.className = "card";

  const st = statusFor(app.id);
  let btnLabel = "Install";
  let btnCls = "";
  let disabled = false;

  if (st) {
    if (st.status === "running") { btnLabel = "Installing…"; btnCls = "running"; disabled = true; }
    else if (st.status === "done") { btnLabel = "Installed ✓"; btnCls = "done"; disabled = true; }
    else if (st.status === "failed") { btnLabel = "Retry"; btnCls = "failed"; }
    else if (st.status === "queued") { btnLabel = "Queued…"; btnCls = "running"; disabled = true; }
  }

  const tags = (app.tags || []).slice(0, 3)
    .map((t) => `<span class="tag">${t}</span>`).join("");

  card.innerHTML = `
    <div class="card-head">
      <div class="thumb">${iconOf(app)}</div>
      <div>
        <h2>${app.name}</h2>
        <span class="cat">${CATEGORY_LABELS[app.category] || app.category}</span>
      </div>
      <span class="provider">${providerLabel(app)}</span>
    </div>
    <p class="desc">${app.description || ""}</p>
    <div class="tags">${tags}</div>
    <button class="install-btn ${btnCls}" ${disabled ? "disabled" : ""}>${btnLabel}</button>
  `;

  const btn = card.querySelector(".install-btn");
  btn.onclick = async () => {
    btn.disabled = true;
    btn.textContent = "Starting…";
    try {
      const secret = await ensureSecret();
      const r = await installApi(app.id, secret);
      toast(r.queued ? `Started: ${app.name}` : `${app.name} already in queue`);
    } catch (e) {
      btn.disabled = false;
      btn.textContent = "Retry";
      toast(e.message === "wrong secret" ? "Wrong secret" : `Failed: ${e.message}`);
    }
    refreshStatus();
  };

  return card;
}

function renderGrid() {
  const grid = $("#grid");
  const empty = $("#empty");
  grid.innerHTML = "";

  const q = state.search.toLowerCase();
  const visible = state.apps.filter((a) => {
    const catOk = state.filter === "all" || a.category === state.filter;
    const qOk = !q
      || a.name.toLowerCase().includes(q)
      || a.description.toLowerCase().includes(q)
      || (a.tags || []).some((t) => t.includes(q));
    return catOk && qOk;
  });

  empty.classList.toggle("hidden", visible.length > 0);
  for (const app of visible) grid.appendChild(renderCard(app));
}

function refreshStatus() {
  api("/api/status").then((data) => {
    state.queue = new Map((data.items || []).map((i) => [i.id, i]));
    const running = data.items.filter((i) => i.status === "running" || i.status === "queued").length;
    $("#queue").textContent = running ? `${running} installing` : "Idle";
    $("#queue").style.color = running ? "var(--warn)" : "var(--muted)";
    renderGrid();
    if (running) setTimeout(refreshStatus, 1500);
  }).catch(() => {});
}

async function init() {
  try {
    const [apps, health] = await Promise.all([api("/api/apps"), api("/api/health")]);
    state.apps = apps;
    $("#health").textContent = `${health.apps} apps`;
    renderFilters();
    renderGrid();
    refreshStatus();
  } catch (e) {
    toast("Aion Hub backend not reachable: " + e.message);
  }
}

$("#search").addEventListener("input", (e) => {
  state.search = e.target.value;
  renderGrid();
});

init();
