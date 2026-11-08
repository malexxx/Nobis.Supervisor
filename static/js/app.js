// =============================================================================
// NOBIS A10 C LIGHT STYLE COAX — CONTRÔLEUR UI, COMMANDES & FLUX SSE TEMPS RÉEL
// =============================================================================

let installerAuthToken = sessionStorage.getItem("nobis_installer_token") || "";

function getInstallerHeaders(extra = {}) {
  const h = { ...extra };
  if (installerAuthToken) {
    h["X-Installer-Token"] = installerAuthToken;
  }
  return h;
}

function showToast(msg, isError = false) {
  const box = document.getElementById("toast-box");
  if (!box) return;
  box.classList.remove("hidden");
  box.className = "fixed bottom-20 md:bottom-5 right-4 z-50 px-4 py-2.5 rounded-xl shadow-2xl border text-xs font-semibold flex items-center gap-2 transition-all " +
    (isError ? "bg-rose-700 border-rose-400 text-white" : "bg-emerald-700 border-emerald-400 text-white");
  box.innerHTML = `<span>${isError ? "⚠️" : "✅"}</span><span>${escapeHtml(msg)}</span>`;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.add("hidden"), 4000);
}

// Gestionnaire de modale non-bloquante (remplace confirm / prompt / alert)
function openActionModal({
  title,
  message,
  htmlContent = null,
  showInput = false,
  defaultValue = "",
  confirmText = "Confirmer",
  cancelText = "Annuler",
  showCancel = true,
  onConfirm,
  onCancel
}) {
  document.getElementById("action-modal-title").textContent = title || "Confirmation";
  const msgEl = document.getElementById("action-modal-message");
  if (htmlContent) {
    msgEl.innerHTML = htmlContent;
  } else {
    msgEl.textContent = message || "";
  }
  const inpWrap = document.getElementById("action-modal-input-wrap");
  const inp = document.getElementById("action-modal-input");
  if (showInput) {
    inpWrap.classList.remove("hidden");
    inp.value = defaultValue;
  } else {
    inpWrap.classList.add("hidden");
  }

  const cancelBtn = document.getElementById("action-modal-cancel-btn");
  if (cancelBtn) {
    cancelBtn.textContent = cancelText;
    if (showCancel) {
      cancelBtn.classList.remove("hidden");
      cancelBtn.onclick = () => {
        closeActionModal();
        if (onCancel) onCancel();
      };
    } else {
      cancelBtn.classList.add("hidden");
    }
  }

  const btn = document.getElementById("action-modal-confirm-btn");
  btn.textContent = confirmText;
  actionModalCallback = () => {
    const val = showInput ? inp.value : true;
    closeActionModal();
    if (onConfirm) onConfirm(val);
  };
  btn.onclick = actionModalCallback;
  document.getElementById("action-modal").classList.remove("hidden");
  if (showInput) setTimeout(() => inp.focus(), 50);
}

function closeActionModal() {
  document.getElementById("action-modal").classList.add("hidden");
  actionModalCallback = null;
}

// Gestion de la pop-up Configuration avec champs dynamiques selon le protocole choisi
function openConfigModal(highlightFieldId = null) {
  updateConfigModalFields();
  const modal = document.getElementById("config-modal");
  modal.classList.remove("hidden");

  if (highlightFieldId) {
    setTimeout(() => {
      const el = document.getElementById(highlightFieldId);
      if (el) {
        el.focus();
        if (el.select) el.select();
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        el.classList.add("ring-2", "ring-amber-400", "border-amber-400", "bg-amber-950/80", "shadow-[0_0_15px_rgba(245,158,11,0.5)]");
        setTimeout(() => {
          el.classList.remove("ring-2", "ring-amber-400", "border-amber-400", "bg-amber-950/80", "shadow-[0_0_15px_rgba(245,158,11,0.5)]");
        }, 3000);
      }
    }, 100);
  }
}

function closeConfigModal() {
  document.getElementById("config-modal").classList.add("hidden");
}

function updateConfigModalFields() {
  const mode = document.getElementById("cfg-mode").value;
  document.getElementById("cfg-fields-agua").classList.toggle("hidden", mode !== "agua_cloud");
  document.getElementById("cfg-fields-gateway").classList.toggle("hidden", mode !== "local_gateway");
  document.getElementById("cfg-fields-sim").classList.toggle("hidden", mode !== "simulation");
}

function toggleMobileSeriesDrawer() {
  const drawer = document.getElementById("series-filter-drawer");
  const icon = document.getElementById("mob-series-drawer-icon");
  const isHidden = drawer.classList.contains("hidden");
  drawer.classList.toggle("hidden", !isHidden);
  icon.textContent = isHidden ? "▲" : "▼";
}

function switchTab(tabId) {
  // Remet l'ascenseur tout en haut de page lors du changement d'onglet
  window.scrollTo({ top: 0, left: 0, behavior: "instant" });
  document.documentElement.scrollTop = 0;
  document.body.scrollTop = 0;
  const mainEl = document.querySelector("main");
  if (mainEl) mainEl.scrollTop = 0;

  ["tab-control", "tab-chrono", "tab-dashboard", "tab-installer", "tab-logs"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.toggle("hidden", id !== tabId);
    const btn = document.getElementById("btn-" + id);
    if (btn) {
      if (id === tabId) {
        btn.classList.add("tab-active");
        btn.classList.remove("text-gray-400");
      } else {
        btn.classList.remove("tab-active");
        btn.classList.add("text-gray-400");
      }
    }
    const mobBtn = document.getElementById("mob-btn-" + id);
    if (mobBtn) {
      if (id === tabId) {
        mobBtn.classList.add("mob-tab-active");
        mobBtn.classList.remove("text-gray-400");
      } else {
        mobBtn.classList.remove("mob-tab-active");
        mobBtn.classList.add("text-gray-400");
      }
    }
  });
  if (tabId === "tab-chrono") {
    renderChronoPrograms();
  }
  if (tabId === "tab-dashboard") {
    setTimeout(() => {
      if (chartPane1) chartPane1.resize();
      if (chartPane2) chartPane2.resize();
    }, 40);
  }
  if (tabId === "tab-installer") {
    checkInstallerAuthStatus();
  }
  if (tabId === "tab-logs") {
    loadLogs(currentLogSeverity);
    loadAudit();
  }
}

// Configuration des jours de la semaine pour la frise Gantt
const GANTT_DAYS = [
  { bit: 1, name: "Lun", full: "Lundi", jsDay: 1 },
  { bit: 2, name: "Mar", full: "Mardi", jsDay: 2 },
  { bit: 4, name: "Mer", full: "Mercredi", jsDay: 3 },
  { bit: 8, name: "Jeu", full: "Jeudi", jsDay: 4 },
  { bit: 16, name: "Ven", full: "Vendredi", jsDay: 5 },
  { bit: 32, name: "Sam", full: "Samedi", jsDay: 6 },
  { bit: 64, name: "Dim", full: "Dimanche", jsDay: 0 },
];
let selectedGanttDayBit = null;

function setGanttDay(bit) {
  selectedGanttDayBit = Number(bit);
  renderChronoPrograms();
}

// Rendu de la Frise Gantt 24h et des 4 cartes Programmes Chrono P1..P4
function renderChronoPrograms() {
  const container = document.getElementById("chrono-programs-grid");
  const ganttTracks = document.getElementById("chrono-gantt-tracks");
  if (!container) return;

  const dayLabels = [
    { bit: 1, name: "Lun" }, { bit: 2, name: "Mar" }, { bit: 4, name: "Mer" },
    { bit: 8, name: "Jeu" }, { bit: 16, name: "Ven" }, { bit: 32, name: "Sam" }, { bit: 64, name: "Dim" }
  ];
  const progColors = {
    1: "bg-emerald-500/85 border-emerald-400",
    2: "bg-orange-500/85 border-orange-400",
    3: "bg-cyan-500/85 border-cyan-400",
    4: "bg-purple-500/85 border-purple-400"
  };

  // Détermination du jour courant (par défaut) et du jour sélectionné
  const now = new Date();
  const todayJsDay = now.getDay();
  const todayDayObj = GANTT_DAYS.find(d => d.jsDay === todayJsDay) || GANTT_DAYS[0];

  if (selectedGanttDayBit === null) {
    selectedGanttDayBit = todayDayObj.bit;
  }
  const selectedDayIdx = GANTT_DAYS.findIndex(d => d.bit === selectedGanttDayBit);
  const selectedDayObj = (selectedDayIdx >= 0) ? GANTT_DAYS[selectedDayIdx] : todayDayObj;
  const isTodaySelected = (selectedDayObj.bit === todayDayObj.bit);

  // Jour précédent pour continuité des créneaux chevauchant minuit
  const prevDayIdx = (selectedDayIdx >= 0 ? selectedDayIdx + 6 : 6) % 7;
  const prevDayObj = GANTT_DAYS[prevDayIdx];

  // Rendu du sélecteur de jour dans l'en-tête de la frise
  const daySelector = document.getElementById("chrono-day-selector");
  if (daySelector) {
    daySelector.innerHTML = GANTT_DAYS.map(d => {
      const isSel = (d.bit === selectedDayObj.bit);
      const isToday = (d.bit === todayDayObj.bit);
      return `<button type="button" onclick="setGanttDay(${d.bit})"
        class="min-h-[28px] px-2.5 py-1 rounded-md text-xs font-semibold flex items-center gap-1 transition-all cursor-pointer ${isSel
          ? 'bg-orange-600 text-white font-bold shadow-md shadow-orange-950/50 border border-orange-500'
          : 'bg-gray-900 text-gray-400 hover:text-white hover:bg-gray-800 border border-gray-800'
        }"
        title="${d.full}${isToday ? ' (Aujourd\'hui)' : ''}">
        <span>${d.name}</span>
        ${isToday ? '<span class="w-1.5 h-1.5 rounded-full bg-emerald-400 inline-block" title="Aujourd\'hui"></span>' : ''}
      </button>`;
    }).join("");
  }

  // Badges d'information sur le jour affiché
  const dayBadge = document.getElementById("gantt-selected-day-badge");
  if (dayBadge) {
    dayBadge.textContent = isTodaySelected ? `${selectedDayObj.full} (Aujourd'hui)` : selectedDayObj.full;
  }
  const daySubtext = document.getElementById("gantt-day-subtext");
  if (daySubtext) {
    daySubtext.textContent = isTodaySelected
      ? `Créneaux actifs aujourd'hui (${selectedDayObj.full})`
      : `Créneaux actifs programmés pour le ${selectedDayObj.full}`;
  }

  // Position du marqueur de l'heure actuelle sur la frise 24h
  const nowMin = now.getHours() * 60 + now.getMinutes();
  const nowPct = ((nowMin / 1440) * 100).toFixed(2);
  const nowStr = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;

  const nowLine = document.getElementById("gantt-now-line");
  if (nowLine) {
    if (isTodaySelected) {
      nowLine.style.display = "block";
      nowLine.style.left = `${nowPct}%`;
    } else {
      nowLine.style.display = "none";
    }
  }

  const nowLbl = document.getElementById("gantt-now-label");
  if (nowLbl) {
    if (isTodaySelected) {
      nowLbl.style.display = "inline";
      nowLbl.textContent = `Heure actuelle : ${nowStr}`;
    } else {
      nowLbl.style.display = "none";
      nowLbl.textContent = "";
    }
  }

  let html = "";
  let ganttHtml = "";

  for (let p = 1; p <= 4; p++) {
    const startSlot = Number(getRegVal(`chrono_p${p}_start_set`, latestData[`chrono_p${p}_start`] ?? 144));
    const stopSlot = Number(getRegVal(`chrono_p${p}_stop_set`, latestData[`chrono_p${p}_stop`] ?? 144));
    const mask = Number(getRegVal(`chrono_p${p}_days_mask`, latestData[`chrono_p${p}_days`] ?? 0));
    const pwr = Number(getRegVal(`chrono_p${p}_power_set`, latestData[`chrono_p${p}_power`] ?? 7));
    const temp = Number(getRegVal(`chrono_p${p}_temperature_set`, latestData[`chrono_p${p}_temp`] ?? 20));

    const fanFront = Number(getRegVal(`chrono_p${p}_fan_front_set`, 0));
    const tCanL = Number(getRegVal(`chrono_p${p}_tcanl_set`, 20));
    const vCanL = Number(getRegVal(`chrono_p${p}_canl_set`, 0));
    const tCanR = Number(getRegVal(`chrono_p${p}_tcanr_set`, 20));
    const vCanR = Number(getRegVal(`chrono_p${p}_canr_set`, 0));

    const isDayActive = (mask & selectedDayObj.bit) !== 0;
    const wasPrevDayActive = (mask & prevDayObj.bit) !== 0;

    // Calcul du segment Gantt filtré sur le jour sélectionné (sur 144 pas de 10 min)
    let barSegments = "";
    if (startSlot < 144 && stopSlot < 144 && startSlot !== stopSlot) {
      if (stopSlot > startSlot) {
        if (isDayActive) {
          const leftPct = ((startSlot / 144) * 100).toFixed(2);
          const widthPct = (((stopSlot - startSlot) / 144) * 100).toFixed(2);
          barSegments = `<div style="left:${leftPct}%;width:${widthPct}%" class="absolute inset-y-0.5 rounded border ${progColors[p]} flex items-center justify-center text-[9px] font-bold text-white truncate px-1 shadow-sm">P${p}: ${slotToTime(startSlot)}-${slotToTime(stopSlot)} (${temp}°C / P${pwr})</div>`;
        }
      } else {
        // Créneau chevauchant minuit (ex: 22h00 -> 06h00)
        // 1. Matinée (00h00 -> stopSlot), suite du démarrage de la veille
        if (wasPrevDayActive) {
          const wMorning = ((stopSlot / 144) * 100).toFixed(2);
          barSegments += `<div style="left:0%;width:${wMorning}%" class="absolute inset-y-0.5 rounded-r border ${progColors[p]} flex items-center justify-center text-[9px] font-bold text-white truncate px-1 shadow-sm" title="Suite créneau veille (${prevDayObj.name})">P${p}: 00h-${slotToTime(stopSlot)} (${temp}°C)</div>`;
        }
        // 2. Soirée (startSlot -> 24h00), démarrage du créneau aujourd'hui
        if (isDayActive) {
          const lEvening = ((startSlot / 144) * 100).toFixed(2);
          const wEvening = (((144 - startSlot) / 144) * 100).toFixed(2);
          barSegments += `<div style="left:${lEvening}%;width:${wEvening}%" class="absolute inset-y-0.5 rounded-l border ${progColors[p]} flex items-center justify-center text-[9px] font-bold text-white truncate px-1 shadow-sm" title="Début créneau soirée">P${p}: ${slotToTime(startSlot)}-24h (${temp}°C / P${pwr})</div>`;
        }
      }
    }

    ganttHtml += `
      <div class="flex items-center gap-2">
        <span class="w-8 text-[10px] font-bold text-gray-400">P${p}</span>
        <div class="relative flex-1 h-5 bg-gray-900 rounded border border-gray-800 overflow-hidden">
          ${barSegments}
        </div>
      </div>
    `;

    const dayBtns = dayLabels.map(d => {
      const isOn = (mask & d.bit) !== 0;
      const nextMask = isOn ? (mask & ~d.bit) : (mask | d.bit);
      return `<button onclick="sendCommand('chrono_p${p}_days_mask', ${nextMask})"
        class="min-h-[30px] px-1.5 py-1 rounded text-[10px] font-bold ${isOn ? 'bg-orange-600 text-white' : 'bg-gray-800 text-gray-500'}">${d.name}</button>`;
    }).join("");

    let slotOptionsStart = `<option value="144" ${startSlot === 144 ? 'selected' : ''}>OFF (144)</option>`;
    let slotOptionsStop = `<option value="144" ${stopSlot === 144 ? 'selected' : ''}>OFF (144)</option>`;
    for (let s = 0; s < 144; s++) {
      const tStr = slotToTime(s);
      slotOptionsStart += `<option value="${s}" ${startSlot === s ? 'selected' : ''}>${tStr}</option>`;
      slotOptionsStop += `<option value="${s}" ${stopSlot === s ? 'selected' : ''}>${tStr}</option>`;
    }

    html += `
      <div class="bg-gray-950/70 border border-gray-800 rounded-lg p-3.5 space-y-2.5 text-xs tabular-nums">
        <div class="flex items-center justify-between border-b border-gray-800 pb-2">
          <strong class="text-white text-sm">📅 Programme P${p}</strong>
          <span class="px-2 py-0.5 rounded ${startSlot < 144 && stopSlot < 144 ? 'bg-emerald-950 text-emerald-300' : 'bg-gray-800 text-gray-400'}">
            ${slotToTime(startSlot)} ➔ ${slotToTime(stopSlot)}
          </span>
        </div>
        <div class="grid grid-cols-2 gap-2">
          <div>
            <label class="text-gray-400 block mb-0.5">Allumage (P${p})</label>
            <select onchange="sendCommand('chrono_p${p}_start_set', this.value)" class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white">
              ${slotOptionsStart}
            </select>
          </div>
          <div>
            <label class="text-gray-400 block mb-0.5">Arrêt (P${p})</label>
            <select onchange="sendCommand('chrono_p${p}_stop_set', this.value)" class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white">
              ${slotOptionsStop}
            </select>
          </div>
        </div>
        <div class="grid grid-cols-3 gap-1.5">
          <div>
            <label class="text-gray-400 block mb-0.5" title="Puissance de flamme (1..7)">Puiss. (1-7)</label>
            <input type="number" min="1" max="7" value="${pwr}" onchange="sendCommand('chrono_p${p}_power_set', this.value)"
              class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white" />
          </div>
          <div>
            <label class="text-gray-400 block mb-0.5" title="Consigne T° Ambiante (7..40°C, 41=MAX)">T° Salon</label>
            <input type="number" min="7" max="41" value="${temp}" onchange="sendCommand('chrono_p${p}_temperature_set', this.value)"
              class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white" />
          </div>
          <div>
            <label class="text-gray-400 block mb-0.5" title="Ventilation Façade (0=OFF, 1..7, 8=PS)">Vent. Façade</label>
            <input type="number" min="0" max="8" value="${fanFront}" onchange="sendCommand('chrono_p${p}_fan_front_set', this.value)"
              class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white" />
          </div>
        </div>
        <div class="grid grid-cols-2 gap-1.5 pt-1 border-t border-gray-900">
          <div class="bg-gray-900/60 p-2 rounded-lg border border-gray-800 space-y-1">
            <div class="text-[10px] text-cyan-400 font-semibold flex items-center justify-between">
              <span>⬅️ Canal. Gauche SX</span>
              <span class="text-[9px] text-gray-500 font-mono">Cons / Vit</span>
            </div>
            <div class="flex gap-1.5 items-center">
              <div class="w-1/2">
                <input type="number" min="7" max="41" value="${tCanL}" title="Consigne T° Canalisation Gauche SX (7..40°C, 41=MAX)" onchange="sendCommand('chrono_p${p}_tcanl_set', this.value)"
                  class="w-full bg-gray-800 border border-gray-700 rounded px-1.5 py-1 text-white text-[11px] tabular-nums" placeholder="T°C" />
              </div>
              <div class="w-1/2">
                <input type="number" min="0" max="8" value="${vCanL}" title="Vitesse Soufflerie Gauche SX (0=OFF, 1..7, 8=PS)" onchange="sendCommand('chrono_p${p}_canl_set', this.value)"
                  class="w-full bg-gray-800 border border-gray-700 rounded px-1.5 py-1 text-cyan-300 font-bold text-[11px] tabular-nums" placeholder="Vit" />
              </div>
            </div>
          </div>
          <div class="bg-gray-900/60 p-2 rounded-lg border border-gray-800 space-y-1">
            <div class="text-[10px] text-sky-400 font-semibold flex items-center justify-between">
              <span>➡️ Canal. Droite DX</span>
              <span class="text-[9px] text-gray-500 font-mono">Cons / Vit</span>
            </div>
            <div class="flex gap-1.5 items-center">
              <div class="w-1/2">
                <input type="number" min="7" max="41" value="${tCanR}" title="Consigne T° Canalisation Droite DX (7..40°C, 41=MAX)" onchange="sendCommand('chrono_p${p}_tcanr_set', this.value)"
                  class="w-full bg-gray-800 border border-gray-700 rounded px-1.5 py-1 text-white text-[11px] tabular-nums" placeholder="T°C" />
              </div>
              <div class="w-1/2">
                <input type="number" min="0" max="8" value="${vCanR}" title="Vitesse Soufflerie Droite DX (0=OFF, 1..7, 8=PS)" onchange="sendCommand('chrono_p${p}_canr_set', this.value)"
                  class="w-full bg-gray-800 border border-gray-700 rounded px-1.5 py-1 text-sky-300 font-bold text-[11px] tabular-nums" placeholder="Vit" />
              </div>
            </div>
          </div>
        </div>
        <div>
          <label class="text-gray-400 block mb-1">Jours actifs (Masque = ${mask})</label>
          <div class="grid grid-cols-7 gap-1">${dayBtns}</div>
        </div>
      </div>
    `;
  }

  if (ganttTracks) ganttTracks.innerHTML = ganttHtml;
  container.innerHTML = html;
}

function renderHardwareMasks() {
  const grid = document.getElementById("hardware-masks-grid");
  if (!grid) return;
  const m105 = Number(latestData.enable_mask_105 ?? 18479);
  const m107 = Number(latestData.enable_mask_107 ?? 2);
  const features = [
    { name: "Affichage Puissance Réelle", on: (m105 & 2) !== 0, src: "105:bit1" },
    { name: "Sonde Ambiante 1", on: (m105 & 8) !== 0, src: "105:bit3" },
    { name: "Contact Thermostat Principal", on: (m105 & 16) !== 0, src: "105:bit4" },
    { name: "Lecture Ventil. Façade 2", on: (m105 & 32) !== 0, src: "105:bit5" },
    { name: "Sonde T° Canal. DX 2", on: (m105 & 512) !== 0, src: "105:bit9" },
    { name: "Contact Thermostat DX", on: (m105 & 1024) !== 0, src: "105:bit10" },
    { name: "Canalisation Droite DX 2", on: (m105 & 2048) !== 0, src: "105:bit11" },
    { name: "Sonde T° Canal. SX 2", on: (m105 & 4096) !== 0, src: "105:bit12" },
    { name: "Contact Thermostat SX", on: (m105 & 8192) !== 0, src: "105:bit13" },
    { name: "Canalisation Gauche SX 2", on: (m105 & 16384) !== 0, src: "105:bit14" },
    { name: "Sonde Ambiante 2 (Salon)", on: (m107 & 2) !== 0, src: "107:bit1" },
    { name: "Option Mode Silence", on: (m107 & 2048) !== 0, src: "107:bit11" }
  ];
  grid.innerHTML = features.map(f => `
    <div class="p-2 rounded border flex items-center justify-between ${f.on ? 'bg-emerald-950/30 border-emerald-800/70 text-emerald-300' : 'bg-gray-950 border-gray-800 text-gray-500'}">
      <span class="truncate" title="${f.src}">${f.name}</span>
      <strong class="ml-1">${f.on ? "ACTIF" : "OFF"}</strong>
    </div>
  `).join("");
}

let currentOffsetsFilter = "";

function openOffsetsModal() {
  const m = document.getElementById("offsets-modal");
  if (m) {
    m.classList.remove("hidden");
    const input = document.getElementById("offsets-modal-search");
    if (input) {
      input.value = "";
      currentOffsetsFilter = "";
    }
    renderRawBufferInspector();
  }
}

function closeOffsetsModal() {
  const m = document.getElementById("offsets-modal");
  if (m) m.classList.add("hidden");
}

function filterOffsetsModal(query) {
  currentOffsetsFilter = (query || "").toLowerCase().trim();
  renderRawBufferInspector();
}

function renderRawBufferInspector() {
  let rawObj = {};
  try {
    rawObj = JSON.parse(latestData.raw_buffer_json || "{}");
  } catch (e) { }
  const keys = Object.keys(rawObj).sort((a, b) => Number(a) - Number(b));

  // Mise à jour des compteurs
  const countBadge = document.getElementById("raw-buf-count");
  if (countBadge) countBadge.textContent = keys.length;
  const modalBadge = document.getElementById("modal-offsets-badge");
  if (modalBadge) modalBadge.textContent = `${keys.length} offsets`;

  const grid = document.getElementById("modal-offsets-grid");
  if (!grid) return;

  if (keys.length === 0) {
    grid.innerHTML = `<div class="col-span-full py-10 text-center text-gray-500">
      <span class="text-3xl block mb-2">📡</span>
      Aucun buffer brut disponible dans ce mode.<br>
      <button onclick="pollNow(); setTimeout(renderRawBufferInspector, 600);" class="mt-3 px-3 py-1.5 rounded bg-cyan-800 hover:bg-cyan-700 text-cyan-200 text-xs font-semibold cursor-pointer">
        ⟳ Interroger le poêle maintenant
      </button>
    </div>`;
    const countLabel = document.getElementById("modal-offsets-count-label");
    if (countLabel) countLabel.textContent = "0 offset disponible";
    return;
  }

  // Filtrage actif dans la popup
  const q = currentOffsetsFilter;
  const filteredKeys = keys.filter(k => {
    if (!q) return true;
    const desc = (OFFSET_NAMES[Number(k)] || "").toLowerCase();
    const val = String(rawObj[k]).toLowerCase();
    return k.includes(q) || desc.includes(q) || val.includes(q);
  });

  const countLabel = document.getElementById("modal-offsets-count-label");
  if (countLabel) {
    countLabel.textContent = q ? `${filteredKeys.length} / ${keys.length} offsets affichés` : `${keys.length} offsets au total`;
  }

  grid.innerHTML = filteredKeys.map(k => {
    const num = Number(k);
    const desc = OFFSET_NAMES[num] || `Offset #${escapeHtml(k)}`;
    const val = rawObj[k];
    const isSpecial = [8, 34, 103, 115, 232, 33120, 33122, 33124, 33126].includes(num);
    const cardBorder = isSpecial ? "border-cyan-700/80 bg-gray-900/90 ring-1 ring-cyan-500/30" : "border-gray-800/80 bg-gray-950/80";
    const valColor = isSpecial ? "text-cyan-400 font-black" : "text-orange-400 font-bold";

    return `
      <div class="${cardBorder} border rounded-lg p-2.5 flex flex-col justify-between hover:border-cyan-500 transition shadow-sm">
        <div class="flex items-center justify-between gap-1 mb-1 border-b border-gray-800/60 pb-1">
          <span class="text-[11px] font-mono text-cyan-300 font-bold">#${escapeHtml(k)}</span>
          <span class="text-[9px] text-gray-500 font-mono">0x${num.toString(16).toUpperCase()}</span>
        </div>
        <div class="text-[10px] text-gray-300 leading-tight font-medium line-clamp-2 min-h-[26px] mb-1.5" title="${escapeHtml(desc)}">
          ${escapeHtml(desc)}
        </div>
        <div class="text-sm ${valColor} text-right tabular-nums">
          ${escapeHtml(val)}
        </div>
      </div>
    `;
  }).join("");
}

// État de connexion globale au serveur et au flux temps réel SSE
const connectionState = {
  isServerConnected: true,
  isSSEConnected: false,
};

// Met à jour l'indicateur visuel de connexion situé à droite du titre
function updateConnectionIndicator(overrideStatus, message) {
  const connEl = document.getElementById("hdr-conn");
  const connDot = document.getElementById("hdr-conn-dot");
  const connText = document.getElementById("hdr-conn-text");
  if (!connEl || !connDot || !connText) return;

  const st = Number(latestData.status_code ?? 0);
  const activeMode = latestData.comm_mode || (currentSettings && currentSettings.comm_mode) || "agua_cloud";

  // Cas 1 : Déconnexion réseau ou serveur injoignable
  if (overrideStatus === "disconnected" || !connectionState.isServerConnected) {
    connEl.className = "flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold border bg-rose-950/70 border-rose-700 text-rose-300 shadow flex-shrink-0 cursor-pointer hover:bg-rose-900/80 transition-all";
    connDot.className = "w-2 h-2 rounded-full bg-rose-500 animate-ping shadow-[0_0_8px_#f43f5e]";
    connText.textContent = message || "Déconnecté";
    connEl.title = "Serveur ou réseau injoignable • Cliquez pour forcer la reconnexion";
    return;
  }

  // Cas 2 : Reconnexion / négociation en cours
  if (overrideStatus === "connecting") {
    connEl.className = "flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold border bg-sky-950/60 border-sky-700 text-sky-300 shadow flex-shrink-0 cursor-pointer hover:bg-sky-900/80 transition-all";
    connDot.className = "w-2 h-2 rounded-full bg-sky-400 animate-pulse shadow-[0_0_8px_#38bdf8]";
    connText.textContent = message || "Connexion...";
    connEl.title = "Tentative de connexion en cours...";
    return;
  }

  // Cas 3 : Serveur OK mais liaison avec le poêle perdue (st === -1)
  if (st === -1) {
    connEl.className = "flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold border bg-amber-950/70 border-amber-600 text-amber-300 shadow flex-shrink-0 cursor-pointer hover:bg-amber-900/80 transition-all";
    connDot.className = "w-2 h-2 rounded-full bg-amber-400 animate-pulse shadow-[0_0_8px_#fbbf24]";
    connText.textContent = "Poêle hors ligne";
    connEl.title = `Serveur connecté, mais perte de communication avec le poêle (${activeMode}) • Cliquez pour actualiser`;
    return;
  }

  // Cas 4 : Tout est connecté et opérationnel
  connEl.className = "flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold border bg-emerald-950/60 border-emerald-700 text-emerald-300 shadow flex-shrink-0 cursor-pointer hover:border-emerald-500 transition-all";
  connDot.className = "w-2 h-2 rounded-full bg-emerald-400 shadow-[0_0_8px_#34d399]";
  const cloudIcon = document.getElementById("hdr-cloud-icon");
  if (cloudIcon) {
    cloudIcon.textContent = activeMode === "agua_cloud" ? "☁️" : (activeMode === "local_gateway" ? "🔌" : "🧪");
    cloudIcon.title = `Protocole : ${activeMode}`;
  }
  connText.textContent = "Connecté";
  connEl.title = `Connexion active • Mode : ${activeMode} • Flux temps réel synchronisé • Cliquez pour actualiser`;
}

// Mise à jour dynamique du voyant d'état (#hdr-dot avec pictos de la phase en cours à gauche du titre), de la carte État du Poêle et du bandeau d'alerte critique
function updateStatusIndicatorAndAlerts() {
  const dot = document.getElementById("hdr-dot");
  const dotIcon = document.getElementById("hdr-dot-icon");
  const kpiDot = document.getElementById("kpi-state-dot");
  const stateEl = document.getElementById("hdr-state");
  const codeEl = document.getElementById("kpi-status-code");
  const banner = document.getElementById("critical-alert-banner");
  const bannerText = document.getElementById("critical-alert-text");
  const bannerBtnText = document.getElementById("critical-alert-btn-text");

  const st = Number(latestData.status_code ?? 0);
  const alarm = Number(latestData.alarm_code ?? 0);
  const popups = Number(latestData.popup_flags ?? 0);
  const blackout = Number(latestData.blackout_popup ?? 0);
  const isAlarm = (alarm > 0 || st === 10 || st === 11 || popups > 0 || blackout > 0);
  const effectivePhase = isAlarm ? (st === 11 ? 11 : 10) : st;
  const meta = getPhaseMeta(effectivePhase);

  // Mise à jour du pictogramme du chevron correspondant à la phase active
  if (dotIcon) {
    dotIcon.textContent = meta.icon;
  } else if (dot) {
    dot.innerHTML = `<span id="hdr-dot-icon" class="select-none leading-none">${meta.icon}</span>`;
  }
  if (dot) {
    dot.title = `Voyant d'état : ${meta.label} (#${st === -1 ? 'Hors ligne' : st}) - ${meta.desc}`;
  }

  if (codeEl) codeEl.textContent = st === -1 ? "HORS LIGNE" : st;

  if (isAlarm) {
    if (dot) dot.className = "w-7 h-7 sm:w-8 sm:h-8 rounded-full flex items-center justify-center text-sm sm:text-base bg-rose-600 text-white shadow-[0_0_12px_#f43f5e] animate-pulse flex-shrink-0 cursor-help border border-rose-300";
    if (kpiDot) kpiDot.className = "w-2.5 h-2.5 rounded-full bg-rose-500 animate-ping shrink-0";
    stateEl.className = "text-sm sm:text-base font-bold text-rose-400 leading-tight truncate";
    banner.classList.remove("hidden");
    const reasons = [];
    let customBtn = "Guide Réarmement (P3)";
    const tf = Number(latestData.temp_fumes ?? 0);

    if (latestData.popup_pellet_open || alarm === 16) {
      reasons.push("Trappe granulés ouverte");
      customBtn = "Fermer la Trappe";
    }
    if (latestData.popup_door_open || alarm === 15) {
      reasons.push("Porte foyer ouverte");
      customBtn = "Fermer la Porte";
    }
    if (alarm === 3) {
      reasons.push("AL03 - PELLET FINI");
      customBtn = (st === 10 && tf > 65) ? `Refroidissement (${tf}°C)` : "Guide Réarmement AL03 (P3)";
    } else if (alarm > 0 && alarm !== 15 && alarm !== 16) {
      reasons.push(latestData.alarm_label || `Alarme AL${alarm}`);
      customBtn = `Guide Alarme AL${alarm < 10 ? '0' + alarm : alarm}`;
    }
    if (latestData.popup_pellet_empty) reasons.push("Trémie granulés vide");
    if (latestData.popup_pa_fault) reasons.push("Défaut Pressostat Pa");
    if (latestData.popup_tck_fault) reasons.push("Défaut Sonde Flamme K");
    if (latestData.popup_tcj_fault) reasons.push("Défaut Sonde Fumées");
    if (blackout > 0) reasons.push("Coupure secteur détectée (AL01)");

    const baseText = reasons.length ? `ALERTE SÉCURITÉ : ${reasons.join(" • ")}` : "ALERTE SÉCURITÉ ACTIVE SUR LE POÊLE";
    if (st === 10 && tf > 65) {
      bannerText.textContent = `${baseText} • ⏳ Refroidissement en cours (${tf}°C, extracteur actif)`;
    } else if (st === 11) {
      bannerText.textContent = `${baseText} • ❄️ Poêle refroidi (Mémoire Alarme) : Réarmement P3 requis`;
    } else {
      bannerText.textContent = baseText;
    }
    if (bannerBtnText) bannerBtnText.textContent = customBtn;
  } else {
    banner.classList.add("hidden");
    if (st === -1) {
      if (dot) dot.className = "w-7 h-7 sm:w-8 sm:h-8 rounded-full flex items-center justify-center text-sm sm:text-base bg-amber-600/90 text-white shadow-[0_0_10px_#f59e0b] animate-pulse flex-shrink-0 cursor-help border border-amber-400";
      if (kpiDot) kpiDot.className = "w-2.5 h-2.5 rounded-full bg-amber-500 animate-pulse shrink-0";
      stateEl.className = "text-sm sm:text-base font-bold text-amber-400 leading-tight truncate";
    } else if (st === 0) {
      if (dot) dot.className = "w-7 h-7 sm:w-8 sm:h-8 rounded-full flex items-center justify-center text-sm sm:text-base bg-slate-800 text-slate-300 border border-slate-700 shadow-inner flex-shrink-0 cursor-help";
      if (kpiDot) kpiDot.className = "w-2.5 h-2.5 rounded-full bg-gray-500 shrink-0";
      stateEl.className = "text-sm sm:text-base font-bold text-gray-300 leading-tight truncate";
    } else if (st >= 1 && st <= 4) {
      if (dot) dot.className = "w-7 h-7 sm:w-8 sm:h-8 rounded-full flex items-center justify-center text-sm sm:text-base bg-amber-500 text-gray-950 font-bold shadow-[0_0_12px_#f59e0b] animate-pulse flex-shrink-0 cursor-help border border-amber-300";
      if (kpiDot) kpiDot.className = "w-2.5 h-2.5 rounded-full bg-amber-400 animate-pulse shrink-0";
      stateEl.className = "text-sm sm:text-base font-bold text-amber-400 leading-tight truncate";
    } else if (st === 5) {
      if (dot) dot.className = "w-7 h-7 sm:w-8 sm:h-8 rounded-full flex items-center justify-center text-sm sm:text-base bg-emerald-600 text-white shadow-[0_0_12px_#10b981] flex-shrink-0 cursor-help border border-emerald-400";
      if (kpiDot) kpiDot.className = "w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse shrink-0";
      stateEl.className = "text-sm sm:text-base font-bold text-emerald-400 leading-tight truncate";
    } else if (st === 6) {
      if (dot) dot.className = "w-7 h-7 sm:w-8 sm:h-8 rounded-full flex items-center justify-center text-sm sm:text-base bg-purple-600 text-white shadow-[0_0_12px_#a855f7] animate-pulse flex-shrink-0 cursor-help border border-purple-400";
      if (kpiDot) kpiDot.className = "w-2.5 h-2.5 rounded-full bg-purple-500 animate-pulse shrink-0";
      stateEl.className = "text-sm sm:text-base font-bold text-purple-400 leading-tight truncate";
    } else if (st === 7) {
      if (dot) dot.className = "w-7 h-7 sm:w-8 sm:h-8 rounded-full flex items-center justify-center text-sm sm:text-base bg-sky-600 text-white shadow-[0_0_10px_#0ea5e9] flex-shrink-0 cursor-help border border-sky-400";
      if (kpiDot) kpiDot.className = "w-2.5 h-2.5 rounded-full bg-sky-400 shrink-0";
      stateEl.className = "text-sm sm:text-base font-bold text-sky-400 leading-tight truncate";
    } else if (st === 8 || st === 9) {
      if (dot) dot.className = "w-7 h-7 sm:w-8 sm:h-8 rounded-full flex items-center justify-center text-sm sm:text-base bg-cyan-700 text-white shadow-[0_0_10px_#06b6d4] flex-shrink-0 cursor-help border border-cyan-400";
      if (kpiDot) kpiDot.className = "w-2.5 h-2.5 rounded-full bg-cyan-400 shrink-0";
      stateEl.className = "text-sm sm:text-base font-bold text-cyan-400 leading-tight truncate";
    } else {
      if (dot) dot.className = "w-7 h-7 sm:w-8 sm:h-8 rounded-full flex items-center justify-center text-sm sm:text-base bg-orange-500 text-white shadow-[0_0_10px_#f97316] flex-shrink-0 cursor-help border border-orange-400";
      if (kpiDot) kpiDot.className = "w-2.5 h-2.5 rounded-full bg-orange-400 shrink-0";
      stateEl.className = "text-sm sm:text-base font-bold text-orange-400 leading-tight truncate";
    }
  }
  updateConnectionIndicator();
  updateDynamicPowerButton();
}

function applyDashboardState(data, updateSeries = true) {
  latestData = data.latest || latestData || {};
  if (data.consumption_stats) {
    if (data.consumption_stats.conso_week_kg !== undefined) {
      latestData.conso_week_kg = data.consumption_stats.conso_week_kg;
    }
    if (data.consumption_stats.conso_month_kg !== undefined) {
      latestData.conso_month_kg = data.consumption_stats.conso_month_kg;
    }
  }
  if (data.registers) {
    registersCatalog = data.registers;
  }
  if (data.settings) {
    currentSettings = data.settings;
  }
  if (data.silence_mode) {
    silenceModeState = data.silence_mode;
  }
  if (data.pellet_quality) {
    latestData.pellet_quality = data.pellet_quality;
  }
  if (data.exhaust_quality) {
    latestData.exhaust_quality = data.exhaust_quality;
  }
  if (updateSeries && Array.isArray(data.series)) {
    cachedSeriesRows = typeof normalizeSeriesWithGaps === "function"
      ? normalizeSeriesWithGaps(data.series)
      : data.series;
  }

  const activeMode = data.active_mode || (currentSettings && currentSettings.comm_mode) || latestData.comm_mode || "agua_cloud";
  const activeDbFile = data.active_db_file || `nobis_a10_${activeMode}.db`;

  const dbEl = document.getElementById("hdr-db");
  if (dbEl) dbEl.textContent = activeDbFile;
  const modeEl = document.getElementById("hdr-mode");
  if (modeEl) modeEl.textContent = activeMode;
  document.getElementById("hdr-state").textContent = latestData.status_label || "--";
  updateStatusIndicatorAndAlerts();

  let clockStr = latestData.stove_clock_str;
  if (!clockStr) {
    const wdayVal = Math.round(getRegVal("calendar_day_week_set", 1));
    const daysFr = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"];
    const wdayStr = (wdayVal >= 1 && wdayVal <= 7) ? daysFr[wdayVal - 1] : "---";
    const hh = String(Math.round(getRegVal("clock_hour_set", 17))).padStart(2, "0");
    const mm = String(Math.round(getRegVal("clock_minute_set", 42))).padStart(2, "0");
    const dd = String(Math.round(getRegVal("calendar_day_set", 1))).padStart(2, "0");
    const mo = String(Math.round(getRegVal("calendar_month_set", 11))).padStart(2, "0");
    const yy = Math.round(getRegVal("calendar_year_set", 26)) + 2000;
    clockStr = `${wdayStr} ${hh}:${mm} ${dd}/${mo}/${yy}`;
  }
  const hdrClockEl = document.getElementById("hdr-clock");
  if (hdrClockEl) hdrClockEl.textContent = clockStr;
  const instClockEl = document.getElementById("inst-clock-val");
  if (instClockEl) instClockEl.textContent = clockStr;
  const hdrCityEl = document.getElementById("hdr-city");
  if (hdrCityEl) hdrCityEl.textContent = (currentSettings && currentSettings.weather_city) || "Bazoches-les-Gallerandes";

  const pwfVal = Number(getRegVal("fun_pwf_set", 0));
  const autoVal = Number(getRegVal("fun_auto_set", latestData.fun_auto_set ?? 1));
  const stCode = Number(latestData.status_code ?? 0);
  const climVal = (stCode === 8 || stCode === 9 || Number(latestData.standby_enabled ?? 0) === 1) ? 1 : 0;
  const silVal = Number(getRegVal("set_silece_set", 0));
  const chrVal = Number(getRegVal("chrono_week_enable_set", latestData.chrono_week_enable ?? 0));
  const pwrSetVal = Number(getRegVal("power_level", 4));
  const fanSetVal = Number(getRegVal("fan_front_speed", 3));
  const dlSpdVal = Number(getRegVal("duct_left_speed", 0));
  const drSpdVal = Number(getRegVal("duct_right_speed", 0));

  // Carte 1 : Ambiance Salon + Écart thermique Delta T
  const ambMeas = Number(latestData.temp_ambient ?? 19);
  const ambSp = Number(getRegVal("temp_amb_setpoint", 19));
  document.getElementById("kpi-amb").textContent = latestData.temp_ambient ?? "--";
  document.getElementById("kpi-amb-sp").textContent = formatTempSetpoint(ambSp);
  const elThM = document.getElementById("kpi-th-m");
  if (elThM) elThM.textContent = latestData.thermostat_main ? "ON" : "OFF";

  const deltaBadge = document.getElementById("kpi-amb-delta");
  if (deltaBadge) {
    deltaBadge.className = "hidden";
  }

  // Cartes 3 à 7
  document.getElementById("kpi-dl-t").textContent = latestData.duct_left_temp ?? "--";
  document.getElementById("kpi-dl-sp").textContent = formatTempSetpoint(getRegVal("duct_left_setpoint", 20));
  document.getElementById("kpi-dl-v").textContent = formatFanSpeed(dlSpdVal);
  document.getElementById("kpi-dl-act").textContent = formatRealFan(latestData.duct_left_actual ?? 0);

  document.getElementById("kpi-dr-t").textContent = latestData.duct_right_temp ?? "--";
  document.getElementById("kpi-dr-sp").textContent = formatTempSetpoint(getRegVal("duct_right_setpoint", 20));
  document.getElementById("kpi-dr-v").textContent = formatFanSpeed(drSpdVal);
  document.getElementById("kpi-dr-act").textContent = formatRealFan(latestData.duct_right_actual ?? 0);

  document.getElementById("kpi-w-t").textContent = latestData.weather_temp_ext ?? "--";
  document.getElementById("kpi-w-h").textContent = latestData.weather_humidity ?? "--";
  document.getElementById("kpi-w-w").textContent = latestData.weather_wind_kmh ?? "--";

  const fumesEl = document.getElementById("kpi-fumes");
  if (fumesEl) fumesEl.textContent = latestData.temp_fumes ?? "--";
  const tkEl = document.getElementById("kpi-tk");
  if (tkEl) tkEl.textContent = latestData.temp_probe_k ?? latestData.temp_board_ntc ?? "--";
  const rpmEl = document.getElementById("kpi-rpm");
  if (rpmEl) rpmEl.textContent = latestData.exhaust_rpm ?? "--";
  const paEl = document.getElementById("kpi-pa");
  if (paEl) paEl.textContent = latestData.depression_pa ?? "--";

  const flameEl = document.getElementById("kpi-flame-idx");
  if (flameEl) {
    flameEl.textContent = latestData.flame_light_idx ?? latestData.temp_probe_k ?? "--";
  }

  const glowBadge = document.getElementById("kpi-glow-badge");
  if (glowBadge) {
    if (Number(latestData.glow_plug_on) === 1) {
      glowBadge.classList.remove("hidden");
    } else {
      glowBadge.classList.add("hidden");
    }
  }

  document.getElementById("kpi-pwr").textContent = formatRealPower(latestData.power_actual ?? pwrSetVal);
  document.getElementById("kpi-fan").textContent = formatFanSpeed(fanSetVal);
  document.getElementById("kpi-auto").textContent = autoVal ? "ON" : "OFF";
  const kpiClimEl = document.getElementById("kpi-clim");
  if (kpiClimEl) kpiClimEl.textContent = climVal ? "ON" : "OFF";
  document.getElementById("kpi-pwf").textContent = pwfVal === 2 ? "CRONO" : (pwfVal === 1 ? "ON" : "OFF");
  document.getElementById("kpi-sil").textContent = silVal ? "ON" : "OFF";

  // Carte 4 : Pellets - Jauge visuelle, Consommations (Jour, Semaine 7j, Mois 30j) & Stock
  const hopPct = Number(latestData.pellet_hopper_pct ?? 78);
  const rateKgh = Number(latestData.pellet_rate_kgh ?? 0);
  const dailyKg = Number(latestData.pellet_daily_kg ?? 0);
  const weeklyKg = Math.max(dailyKg, Number(latestData.conso_week_kg ?? (latestData.consumption_stats?.conso_week_kg ?? 0)));
  const monthlyKg = Math.max(weeklyKg, Number(latestData.conso_month_kg ?? (latestData.consumption_stats?.conso_month_kg ?? 0)));

  const bagPriceEur = Number((currentSettings && currentSettings.pellet_bag_price_eur) || 5.80);
  const bagsStock = Number((currentSettings && currentSettings.pellet_bags_stock) ?? 40);
  const kgPriceEur = bagPriceEur / BAG_WEIGHT_KG;
  const dailyCostEur = (dailyKg * kgPriceEur).toFixed(2);
  const weeklyCostEur = (weeklyKg * kgPriceEur).toFixed(2);
  const monthlyCostEur = (monthlyKg * kgPriceEur).toFixed(2);

  document.getElementById("kpi-hop").textContent = hopPct;
  document.getElementById("kpi-kgh").textContent = rateKgh;
  document.getElementById("kpi-daily-kg").textContent = dailyKg.toFixed(1);
  const costEl = document.getElementById("kpi-daily-cost");
  if (costEl) costEl.textContent = `${dailyCostEur}€`;

  const weeklyKgEl = document.getElementById("kpi-weekly-kg");
  if (weeklyKgEl) weeklyKgEl.textContent = weeklyKg.toFixed(1);
  const weeklyCostEl = document.getElementById("kpi-weekly-cost");
  if (weeklyCostEl) weeklyCostEl.textContent = `${weeklyCostEur}€`;

  const monthlyKgEl = document.getElementById("kpi-monthly-kg");
  if (monthlyKgEl) monthlyKgEl.textContent = monthlyKg.toFixed(1);
  const monthlyCostEl = document.getElementById("kpi-monthly-cost");
  if (monthlyCostEl) monthlyCostEl.textContent = `${monthlyCostEur}€`;

  const stockEl = document.getElementById("kpi-bags-stock");
  if (stockEl) stockEl.textContent = `${bagsStock} sacs`;
  const chrEl = document.getElementById("kpi-chr");
  if (chrEl) chrEl.textContent = chrVal ? "ON" : "OFF";

  const augerDutyEl = document.getElementById("kpi-auger-duty");
  if (augerDutyEl) {
    augerDutyEl.textContent = latestData.auger_duty_pct != null ? `${Number(latestData.auger_duty_pct).toFixed(1)}%` : "--";
  }
  const augerSEl = document.getElementById("kpi-auger-s");
  if (augerSEl) {
    augerSEl.textContent = latestData.auger_on_time_s != null ? Number(latestData.auger_on_time_s).toFixed(1) : "--";
  }

  const hopBar = document.getElementById("kpi-hop-bar");
  hopBar.style.width = `${Math.max(0, Math.min(100, hopPct))}%`;
  hopBar.className = "h-full transition-all duration-500 " + (hopPct <= 15 ? "bg-rose-500" : (hopPct <= 35 ? "bg-amber-500" : "bg-[#96562c]"));

  const kgRemaining = (hopPct / 100.0) * HOPPER_CAPACITY_KG;
  const effectiveRate = rateKgh > 0.1 ? rateKgh : 0.95;
  const hoursLeft = Math.round(kgRemaining / effectiveRate);
  document.getElementById("kpi-hop-autonomy").textContent = `~${hoursLeft}h (${kgRemaining.toFixed(1)}kg)`;

  // Prédiction Runway Stock (DJU & Météo)
  const runwayDays = Number(latestData.runway_days_est ?? 15.0);
  const runwayDaysEl = document.getElementById("kpi-pellet-runway-days");
  const runwayDateEl = document.getElementById("kpi-pellet-runway-date");
  if (runwayDaysEl && runwayDateEl) {
    runwayDaysEl.textContent = `~${runwayDays.toFixed(0)} jours`;
    if (runwayDays <= 5) {
      runwayDaysEl.className = "text-xs font-bold text-rose-400 tabular-nums animate-pulse";
    } else if (runwayDays <= 12) {
      runwayDaysEl.className = "text-xs font-bold text-amber-400 tabular-nums";
    } else {
      runwayDaysEl.className = "text-xs font-semibold text-emerald-400 tabular-nums";
    }
    const targetDate = new Date();
    targetDate.setDate(targetDate.getDate() + Math.round(runwayDays));
    const dayNames = ["Dim", "Lun", "Mar", "Mer", "Jeu", "Ven", "Sam"];
    const monthNames = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."];
    runwayDateEl.textContent = `${dayNames[targetDate.getDay()]} ${targetDate.getDate()} ${monthNames[targetDate.getMonth()]}`;
  }

  // Diagnostics Optimisation Tarages IA (Granulés & Fumées)
  renderPelletQuality(latestData);
  renderExhaustQuality(latestData);

  // Onglet Pilotage Direct
  document.getElementById("ctl-amb-val").textContent = formatTempSetpoint(ambSp);
  document.getElementById("ctl-amb-slider").value = ambSp;
  const pActualVal = latestData.power_actual ?? pwrSetVal;
  document.getElementById("ctl-real-pwr").textContent = formatRealPower(pActualVal);
  const fanEl = document.getElementById("ctl-real-fan");
  if (fanEl) fanEl.textContent = formatRealFan(latestData.vent_front_actual ?? 0);

  const pwrModeDescEl = document.getElementById("ctl-power-mode-desc");
  if (pwrModeDescEl) {
    const stCode = Number(latestData.status_code ?? 0);
    const pActualNum = Number(pActualVal);
    const sbEnabled = Number(getRegVal("standby_set", latestData.standby_enabled ?? 0)) === 1;

    if (stCode === 5) {
      if (pActualNum === 0) {
        pwrModeDescEl.className = "text-sky-300 text-[10px] font-medium";
        pwrModeDescEl.textContent = sbEnabled ? "⏳ Stand-by" : "🌱 Modulation P0";
      } else if (pActualNum === 8) {
        pwrModeDescEl.className = "text-rose-400 text-[10px] font-medium";
        pwrModeDescEl.textContent = "⚡ BOOST";
      } else {
        pwrModeDescEl.textContent = "";
      }
    } else {
      pwrModeDescEl.textContent = "";
    }
  }

  document.getElementById("ctl-dl-meas").textContent = latestData.duct_left_temp ?? "--";
  document.getElementById("ctl-dl-sp").textContent = formatTempSetpoint(getRegVal("duct_left_setpoint", 20));
  document.getElementById("ctl-dl-slider").value = getRegVal("duct_left_setpoint", 20);

  document.getElementById("ctl-dr-meas").textContent = latestData.duct_right_temp ?? "--";
  document.getElementById("ctl-dr-sp").textContent = formatTempSetpoint(getRegVal("duct_right_setpoint", 20));
  document.getElementById("ctl-dr-slider").value = getRegVal("duct_right_setpoint", 20);

  const dlSpVal = Number(getRegVal("duct_left_setpoint", 20));
  const drSpVal = Number(getRegVal("duct_right_setpoint", 20));
  const btnAmbMax = document.getElementById("btn-amb-max");
  if (btnAmbMax) btnAmbMax.classList.toggle("btn-active-glow", ambSp >= 41);
  const btnDlMax = document.getElementById("btn-dl-max");
  if (btnDlMax) btnDlMax.classList.toggle("btn-active-glow", dlSpVal >= 41);
  const btnDrMax = document.getElementById("btn-dr-max");
  if (btnDrMax) btnDrMax.classList.toggle("btn-active-glow", drSpVal >= 41);

  const climBadge = document.getElementById("ctl-clim-badge");
  const isSilActive = Boolean(silenceModeState && silenceModeState.active);
  if (climBadge) {
    const isStandbyPhase = (stCode === 8 || stCode === 9);
    const isEcoActive = !isSilActive && (Number(getRegVal("standby_set", 0)) === 1);
    climBadge.textContent = isStandbyPhase ? (stCode === 8 ? "ACTIF (8 = ECO STOP)" : "ACTIF (9 = STAND BY)") : (isEcoActive ? "STAND-BY ARMÉ" : "VEILLE INACTIVE");
    climBadge.className = `min-h-[36px] px-3 py-1.5 rounded-lg font-semibold text-xs flex items-center border ${isStandbyPhase || isEcoActive ? "bg-cyan-950/80 border-cyan-500 text-cyan-300 shadow" : "bg-gray-900 border-gray-700 text-gray-400"
      }`;
  }

  updateModeRing();

  const chrBtn = document.getElementById("ctl-chrono-enable-btn");
  if (chrBtn) {
    chrBtn.textContent = chrVal ? "ON" : "OFF";
    chrBtn.title = `Chrono Général : ${chrVal ? "Actif (ON)" : "Inactif (OFF)"} (offset 33162) - Cliquer pour basculer`;
    chrBtn.className = `min-h-[36px] min-w-[72px] px-4 py-1.5 rounded-lg font-bold text-xs transition-all cursor-pointer text-center shadow-sm ${chrVal
      ? "bg-emerald-600 hover:bg-emerald-500 text-white border border-emerald-500 shadow-emerald-950/40"
      : "bg-gray-800 hover:bg-gray-700 text-gray-400 border border-gray-700"
      }`;
  }

  const pwfStat = document.getElementById("ctl-pwf-status-label");
  if (pwfStat) {
    const pwfLabels = { 0: "0", 1: "1", 2: "2" };
    pwfStat.textContent = "Actuel : " + (pwfLabels[pwfVal] || "0");
  }
  // Mise à jour Double-État : Anneau = Consigne / Fond plein = Réel
  const isBoostOn = pwfVal > 0;
  const pwrSpTarget = isBoostOn ? 8 : pwrSetVal;
  const pwrActTarget = Number(latestData.power_actual ?? (isBoostOn ? 8 : pwrSetVal));
  updateDualStateButtons("ctl-power-btns", pwrSpTarget, pwrActTarget, "amber");

  const fanActTarget = getActualFanButtonVal(latestData.vent_front_actual, fanSetVal);
  updateDualStateButtons("ctl-fan-btns", fanSetVal, fanActTarget, "emerald");

  const dlActTarget = getActualFanButtonVal(latestData.duct_left_actual, dlSpdVal);
  updateDualStateButtons("ctl-dl-spd-btns", dlSpdVal, dlActTarget, "cyan");

  const drActTarget = getActualFanButtonVal(latestData.duct_right_actual, drSpdVal);
  updateDualStateButtons("ctl-dr-spd-btns", drSpdVal, drActTarget, "sky");

  updateAutoControlLock(Boolean(autoVal));

  // Badges de sécurité complets Volet 4 (Foyer, Sécurités, Thermostats & Trémie)
  if (typeof renderSecurityBadges === "function") {
    renderSecurityBadges(latestData, false);
  }

  // Badges sondes spécifiques Trémie / Pellets (déplacés dans la carte Pellets)
  const pelletSecBox = document.getElementById("pellet-security-badges");
  if (pelletSecBox) {
    const itemsPelletSec = [
      { label: "Trappe pellet", fault: latestData.popup_pellet_open, okTxt: "Fermée", errTxt: "OUVERTE !" },
      { label: "Trémie", fault: latestData.popup_pellet_empty, okTxt: "Niveau OK", errTxt: "VIDE !" }
    ];
    pelletSecBox.innerHTML = itemsPelletSec.map(i => `
      <div class="px-2 py-1 rounded border flex justify-between items-center ${i.fault ? 'bg-rose-950/60 border-rose-600 text-rose-300 font-bold' : 'bg-gray-950/90 border-[#6e3b1f]/60 text-gray-300'}">
        <span class="truncate">${i.label} :</span>
        <span class="${i.fault ? 'text-rose-400 font-bold' : 'text-[#d4976a] font-semibold'}">${i.fault ? i.errTxt : i.okTxt}</span>
      </div>
    `).join("");
  }

  renderChronoPrograms();
  renderHardwareMasks();
  renderRawBufferInspector();

  // Onglet Paramètres (Menu Installateur)
  const trimPelletInp = document.getElementById("inst-trim-pellet");
  if (trimPelletInp && document.activeElement !== trimPelletInp) {
    trimPelletInp.value = getRegVal("trim_pellet_pct", 0);
  }
  const trimExhaustInp = document.getElementById("inst-trim-exhaust");
  if (trimExhaustInp && document.activeElement !== trimExhaustInp) {
    trimExhaustInp.value = getRegVal("trim_exhaust_pct", 0);
  }

  // Compteurs Horaires & Révision CAT
  const catHours = Number(latestData.cat_service_hours ?? getRegVal("cat_service_hours", 1420));
  const catEl = document.getElementById("kpi-cat-hours");
  if (catEl) catEl.textContent = Math.round(catHours);

  const workHours = Number(latestData.total_work_hours ?? 2840.5);
  const workEl = document.getElementById("kpi-total-work-hours");
  if (workEl) workEl.textContent = workHours.toFixed(1);

  const ignCount = Number(latestData.ignitions_count ?? 612);
  const ignEl = document.getElementById("kpi-ignitions-count");
  if (ignEl) ignEl.textContent = ignCount;

  const catPill = document.getElementById("cat-alert-pill");
  if (catPill) {
    if (catHours <= 50) {
      catPill.textContent = `⚠️ CAT IMMINENT (${Math.round(catHours)}h)`;
      catPill.className = "text-[11px] font-bold px-2.5 py-1 rounded-full bg-rose-950 text-rose-300 border border-rose-700 animate-pulse";
    } else if (catHours <= 150) {
      catPill.textContent = `⚡ Prévoir entretien CAT (${Math.round(catHours)}h)`;
      catPill.className = "text-[11px] font-bold px-2.5 py-1 rounded-full bg-amber-950 text-amber-300 border border-amber-700";
    } else {
      catPill.textContent = `✅ CAT OK (${Math.round(catHours)}h)`;
      catPill.className = "text-[11px] font-bold px-2.5 py-1 rounded-full bg-emerald-950 text-emerald-300 border border-emerald-700";
    }
  }

  renderEepromTable();

  // Pop-up Configuration (ne pas écraser si la modale est déjà ouverte en cours de saisie)
  const cfgModal = document.getElementById("config-modal");
  if (currentSettings && cfgModal && cfgModal.classList.contains("hidden")) {
    document.getElementById("cfg-mode").value = currentSettings.comm_mode || "agua_cloud";
    document.getElementById("cfg-interval").value = currentSettings.poll_interval_s || "120";
    document.getElementById("cfg-city").value = currentSettings.weather_city || "";
    document.getElementById("cfg-lat").value = currentSettings.weather_lat || "";
    document.getElementById("cfg-lon").value = currentSettings.weather_lon || "";
    document.getElementById("cfg-email").value = currentSettings.agua_email || "";
    document.getElementById("cfg-gw").value = currentSettings.local_gateway_url || "";
    const bagPriceInp = document.getElementById("cfg-bag-price");
    if (bagPriceInp) bagPriceInp.value = currentSettings.pellet_bag_price_eur || "5.80";
    const bagsStockInp = document.getElementById("cfg-bags-stock");
    if (bagsStockInp) bagsStockInp.value = currentSettings.pellet_bags_stock ?? "40";
    const webhookInp = document.getElementById("cfg-webhook");
    if (webhookInp) webhookInp.value = currentSettings.webhook_url || "";
    const matterEnInp = document.getElementById("cfg-matter-enabled");
    if (matterEnInp) matterEnInp.value = currentSettings.matter_enabled ?? "1";
    const matterNameInp = document.getElementById("cfg-matter-name");
    if (matterNameInp) matterNameInp.value = currentSettings.matter_device_name || "Poêle Nobis A10";
    const matterPortInp = document.getElementById("cfg-matter-port");
    if (matterPortInp) matterPortInp.value = currentSettings.matter_port || "5540";
    updateConfigModalFields();
  }

  renderSeriesToggles();
  if (updateSeries) {
    updateChartDatasets();
  }
}

async function loadDashboard() {
  try {
    const res = await fetch(`/api/dashboard?range_key=${currentRange}`);
    if (!res.ok) {
      connectionState.isServerConnected = false;
      updateConnectionIndicator("disconnected", "Erreur HTTP");
      return;
    }
    const data = await res.json();
    connectionState.isServerConnected = true;
    applyDashboardState(data, true);
  } catch (e) {
    connectionState.isServerConnected = false;
    updateConnectionIndicator("disconnected", "Réseau KO");
  }
}

async function refreshLatestOnly() {
  try {
    const res = await fetch("/api/latest");
    if (!res.ok) {
      connectionState.isServerConnected = false;
      updateConnectionIndicator("disconnected", "Erreur HTTP");
      return;
    }
    const data = await res.json();
    connectionState.isServerConnected = true;
    // Si un nouveau point temporel est arrivé, on l'ajoute en fin de courbe et on recalcule les espaces de perte de connexion
    if (data.latest && data.latest.timestamp) {
      const rawOnly = cachedSeriesRows.filter(r => !r._gapDurationMin);
      const lastCached = rawOnly[rawOnly.length - 1];
      if (!lastCached || lastCached.timestamp !== data.latest.timestamp) {
        rawOnly.push(data.latest);
        if (rawOnly.length > 600) rawOnly.shift();
        data.series = rawOnly;
        applyDashboardState(data, true);
        return;
      }
    }
    applyDashboardState(data, false);
  } catch (e) {
    connectionState.isServerConnected = false;
    updateConnectionIndicator("disconnected", "Réseau KO");
  }
}

// Connexion Server-Sent Events (SSE) pour mise à jour instantanée sans polling lourd
function initSSEStream() {
  if (sseSource) {
    try { sseSource.close(); } catch (e) { }
  }
  if (!window.EventSource) return;

  sseSource = new EventSource("/api/events");
  sseSource.onopen = () => {
    connectionState.isServerConnected = true;
    connectionState.isSSEConnected = true;
    updateConnectionIndicator();
  };
  sseSource.onmessage = (evt) => {
    try {
      const msg = JSON.parse(evt.data);
      connectionState.isServerConnected = true;
      connectionState.isSSEConnected = true;
      updateConnectionIndicator();
      if (msg.event === "telemetry" || msg.type === "telemetry_update" || msg.type === "register_update") {
        refreshLatestOnly();
      }
    } catch (e) { }
  };
  sseSource.onerror = () => {
    connectionState.isSSEConnected = false;
    try { sseSource.close(); } catch (e) { }
    updateConnectionIndicator("connecting", "Reconnexion...");
    setTimeout(initSSEStream, 10000);
  };
}

function getActualFanButtonVal(rawActual, setpoint) {
  const raw = Number(rawActual ?? 0);
  const sp = Number(setpoint ?? 0);
  if (sp !== 8) {
    if (raw === 0 && sp === 0) return 0;
    if (raw >= 2 && raw <= 8) return raw - 1;
    return sp;
  }
  // En mode PS (consigne = 8) : la vitesse réelle varie de 0 (OFF) ou 1 à 7
  if (raw === 0) return 0;
  if (raw === 1) return 1;
  if (raw >= 2 && raw <= 8) return raw - 1;
  return raw;
}

function updateDualStateButtons(containerId, setpointVal, actualVal, theme = "amber") {
  const container = document.getElementById(containerId);
  if (!container) return;
  const sp = Number(setpointVal);
  const act = Number(actualVal);

  const fillClass = `btn-actual-fill-${theme}`;

  container.querySelectorAll("button").forEach(btn => {
    const val = Number(btn.getAttribute("data-val"));
    const isSetpoint = (val === sp);
    const isActual = (val === act);

    // Nettoyage des styles dynamiques précédents
    btn.classList.remove(
      "btn-setpoint-ring",
      "btn-active-glow",
      "btn-actual-fill-amber",
      "btn-actual-fill-emerald",
      "btn-actual-fill-cyan",
      "btn-actual-fill-sky",
      "btn-actual-fill-rose",
      "animate-pulse"
    );

    // 1. Fond coloré = État réel physique
    if (isActual) {
      if (val === 0 && containerId === "ctl-power-btns") {
        btn.classList.add("btn-actual-fill-sky");
      } else if (val === 8 && containerId === "ctl-power-btns") {
        btn.classList.add("btn-actual-fill-rose", "animate-pulse");
      } else {
        btn.classList.add(fillClass);
      }
    }

    // 2. Anneau blanc contrasté = Consigne cible demandée
    if (isSetpoint) {
      btn.classList.add("btn-setpoint-ring");
    }
  });
}

function highlightActiveButtons(containerId, activeVal) {
  updateDualStateButtons(containerId, activeVal, activeVal, "amber");
}

async function handlePowerButtonClick(val) {
  const v = Number(val);
  const isAutoActive = Boolean(Number(getRegVal("fun_auto_set", latestData.fun_auto_set ?? 1)));

  if (v === 0) {
    showToast("P0 (Éco) est un ralenti automatique enclenché lorsque la température atteint la consigne.", "info");
    return;
  }
  if (v === 8) {
    cycleBoostMode();
    return;
  }

  // P1 à P7 :
  if (isAutoActive) {
    promptDisableAutoMode("power_level", v);
    return;
  }

  // Si BOOST était actif, le désactiver pour revenir à la puissance demandée
  const pwfCur = Number(getRegVal("fun_pwf_set", latestData.fun_pwf_set ?? 0));
  if (pwfCur > 0) {
    await sendCommand("fun_pwf_set", 0);
  }
  await sendCommand("power_level", v);
}

function promptDisableAutoMode(intendedKey, intendedValue) {
  const label = intendedKey === "power_level"
    ? `la consigne de puissance P${intendedValue}`
    : `la ventilation frontale (${intendedValue === 0 ? 'OFF' : (intendedValue === 8 ? 'PS' : intendedValue)})`;

  openActionModal({
    title: "🔥 Mode Auto Actif",
    message: `Le poêle est actuellement en Mode Auto (la puissance et la ventilation sont gérées automatiquement selon la température ambiante).\n\nPour modifier manuellement ${label}, le Mode Auto doit être désactivé.\n\nSouhaitez-vous désactiver le Mode Auto et appliquer ce réglage ?`,
    confirmText: "Désactiver Auto et Appliquer",
    onConfirm: async () => {
      // 1. Désactiver le mode auto
      await sendCommand("fun_auto_set", 0);
      // 2. Appliquer la consigne demandée
      if (intendedKey === "power_level") {
        await handlePowerButtonClick(intendedValue);
      } else {
        await sendCommand(intendedKey, intendedValue);
      }
    }
  });
}

function updateAutoControlLock(isAutoActive) {
  const pwrContainer = document.getElementById("ctl-power-btns");
  const fanContainer = document.getElementById("ctl-fan-btns");

  // Puissance : clic géré par handlePowerButtonClick
  if (pwrContainer) {
    pwrContainer.querySelectorAll("button").forEach(btn => {
      const val = Number(btn.getAttribute("data-val"));
      btn.onclick = () => handlePowerButtonClick(val);
    });
  }

  // Ventilation frontale
  if (fanContainer) {
    fanContainer.querySelectorAll("button").forEach(btn => {
      const val = Number(btn.getAttribute("data-val"));
      if (isAutoActive) {
        btn.classList.add("cursor-pointer");
        btn.setAttribute("data-auto-locked", "true");
        btn.title = "Mode Auto actif — Cliquer pour ajuster la ventilation";
        btn.onclick = (e) => {
          e.preventDefault();
          e.stopPropagation();
          promptDisableAutoMode("fan_front_speed", val);
        };
      } else {
        btn.classList.remove("cursor-pointer");
        btn.removeAttribute("data-auto-locked");
        btn.title = "";
        btn.onclick = () => sendCommand("fan_front_speed", val);
      }
    });
  }
}

let isCatalogCollapsed = false;

function toggleCatalogCollapse() {
  const wrap = document.getElementById("catalog-table-wrap");
  const btn = document.getElementById("btn-toggle-catalog");
  if (!wrap) return;
  isCatalogCollapsed = !isCatalogCollapsed;
  if (isCatalogCollapsed) {
    wrap.style.display = "none";
    if (btn) btn.innerHTML = "↕️ Déplier (masqué)";
  } else {
    wrap.style.display = "block";
    if (btn) btn.innerHTML = "↕️ Réduire";
  }
}

function filterCatalogTable(query) {
  const q = (query || "").toLowerCase().trim();
  const rows = document.querySelectorAll("#eeprom-table-body tr");
  let visibleCount = 0;
  rows.forEach(tr => {
    const text = tr.textContent.toLowerCase();
    const match = !q || text.includes(q);
    tr.style.display = match ? "" : "none";
    if (match) visibleCount++;
  });
  const countEl = document.getElementById("catalog-reg-count");
  if (countEl) {
    countEl.textContent = q ? `${visibleCount} / ${rows.length}` : `${rows.length} registres`;
  }
}

function renderEepromTable() {
  const tbody = document.getElementById("eeprom-table-body");
  if (!tbody) return;
  if (document.activeElement && tbody.contains(document.activeElement)) return;
  tbody.innerHTML = "";
  const regs = Object.values(registersCatalog);
  const countEl = document.getElementById("catalog-reg-count");
  if (countEl) countEl.textContent = `${regs.length} registres`;

  regs.forEach(r => {
    const tr = document.createElement("tr");
    tr.className = "hover:bg-gray-800/40 transition";
    tr.innerHTML = `
      <td class="py-1.5 px-2 font-mono text-orange-400 font-semibold">${escapeHtml(r.reg_key)}</td>
      <td class="py-1.5 px-2 font-mono text-cyan-300">#${Number(r.offset)}</td>
      <td class="py-1.5 px-2"><span class="px-2 py-0.5 rounded bg-gray-800 text-gray-300 text-[10px] font-medium">${escapeHtml(r.category)}</span></td>
      <td class="py-1.5 px-2 text-gray-200 max-w-[220px] truncate" title="${escapeHtml(r.label)}">${escapeHtml(r.label)}</td>
      <td class="py-1.5 px-2 text-gray-400">[${Number(r.min_val)}..${Number(r.max_val)}] ${escapeHtml(r.unit)}</td>
      <td class="py-1.5 px-2">
        <input id="inp-${escapeHtml(r.reg_key)}" type="number" step="${Number(r.step)}" min="${Number(r.min_val)}" max="${Number(r.max_val)}" value="${Number(r.current_val)}"
          class="inst-input bg-gray-800 border border-gray-700 rounded px-2 py-0.5 w-20 text-white tabular-nums text-xs" />
      </td>
      <td class="py-1.5 px-2">
        <button data-reg="${escapeHtml(r.reg_key)}" onclick="sendCommand(this.dataset.reg, document.getElementById('inp-' + this.dataset.reg).value)"
          class="inst-input px-2.5 py-0.5 rounded bg-orange-600 hover:bg-orange-500 text-white text-[11px] font-semibold cursor-pointer transition">Écrire</button>
      </td>
    `;
    tbody.appendChild(tr);
  });

  const searchInp = document.getElementById("catalog-search");
  if (searchInp && searchInp.value) {
    filterCatalogTable(searchInp.value);
  }
}

async function loadSnapshots() {
  const res = await fetch("/api/snapshots", { headers: getInstallerHeaders() });
  if (!res.ok) return;
  const snaps = await res.json();
  const tbody = document.getElementById("snapshots-table-body");
  if (snaps.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" class="py-3 text-gray-500">Aucun snapshot enregistré dans cette base. Cliquez sur "📸 Capturer un Snapshot".</td></tr>`;
    return;
  }
  tbody.innerHTML = snaps.map(s => `
    <tr>
      <td class="py-2 text-gray-400 font-mono">#${Number(s.id)} — ${escapeHtml(s.timestamp)}</td>
      <td><strong class="text-white">${escapeHtml(s.name)}</strong></td>
      <td class="uppercase text-cyan-400 font-mono">${escapeHtml(s.comm_mode)}</td>
      <td class="text-gray-300">${Number(s.registers_count)} registres / ${Number(s.raw_offsets_count)} offsets bruts</td>
      <td class="flex flex-wrap gap-1.5 py-1.5">
        <a href="/api/snapshots/${Number(s.id)}/export-csv" class="px-2.5 py-1 rounded bg-emerald-700 hover:bg-emerald-600 text-white text-xs font-semibold">
          📥 CSV
        </a>
        <button data-snap-id="${Number(s.id)}" data-snap-name="${escapeHtml(s.name)}" onclick="restoreSnapshot(Number(this.dataset.snapId), this.dataset.snapName)" class="px-2.5 py-1 rounded bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold">
          ↩ Restaurer (Batch)
        </button>
        <button onclick="deleteSnapshot(${Number(s.id)})" class="px-2 py-1 rounded bg-rose-900/70 hover:bg-rose-700 text-rose-200 text-xs">
          🗑️
        </button>
      </td>
    </tr>
  `).join("");
}

async function createSnapshot() {
  const nameInp = document.getElementById("snap-name-input");
  const name = nameInp ? nameInp.value : "";
  const res = await fetch("/api/snapshots", {
    method: "POST",
    headers: getInstallerHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ name, refresh_live: false })
  });
  if (res.ok) {
    if (nameInp) nameInp.value = "";
    await loadSnapshots();
    showToast("Snapshot des registres et paramètres enregistré !");
  }
}

function restoreSnapshot(id, name) {
  openActionModal({
    title: "↩ Restauration Groupée (Batch)",
    message: `Confirmez-vous la restauration complète du Snapshot #${id} (${name}) vers les registres du poêle ?`,
    confirmText: "Restaurer",
    onConfirm: async () => {
      const res = await fetch(`/api/snapshots/${id}/restore`, { method: "POST", headers: getInstallerHeaders() });
      if (res.ok) {
        const j = await res.json();
        await loadDashboard();
        await loadAudit();
        showToast(`Snapshot restauré (${j.restored_registers} registres réécrits en batch).`);
      } else {
        const err = await res.json().catch(() => ({}));
        showToast("Erreur restauration : " + (err.detail || res.statusText), true);
      }
    }
  });
}

function deleteSnapshot(id) {
  openActionModal({
    title: "🗑️ Supprimer le Snapshot",
    message: `Supprimer définitivement le Snapshot #${id} de la base SQLite ?`,
    confirmText: "Supprimer",
    onConfirm: async () => {
      await fetch(`/api/snapshots/${id}`, { method: "DELETE", headers: getInstallerHeaders() });
      await loadSnapshots();
      showToast(`Snapshot #${id} supprimé.`);
    }
  });
}

function toggleInstallerLock() {
  installerUnlocked = true;
}

// =============================================================================
// AUTHENTIFICATION SÉCURISÉE MENU INSTALLATEUR (CLOUD AGUA PASSWORD)
// =============================================================================

async function checkInstallerAuthStatus(forceRefresh = false) {
  const lockScreen = document.getElementById("installer-lock-screen");
  const unlockedContent = document.getElementById("installer-unlocked-content");
  const noPwdAlert = document.getElementById("installer-no-pwd-alert");
  const errBox = document.getElementById("installer-auth-error");

  if (!lockScreen || !unlockedContent) return;

  try {
    const headers = {};
    if (installerAuthToken) {
      headers["X-Installer-Token"] = installerAuthToken;
    }
    const res = await fetch("/api/installer/status", { headers });
    if (!res.ok) {
      lockScreen.classList.remove("hidden");
      unlockedContent.classList.add("hidden");
      return;
    }
    const data = await res.json();

    if (data.authenticated) {
      lockScreen.classList.add("hidden");
      unlockedContent.classList.remove("hidden");
      if (errBox) errBox.classList.add("hidden");
      document.querySelectorAll(".inst-input").forEach(el => { el.disabled = false; });
      loadSnapshots();
    } else {
      if (installerAuthToken) {
        installerAuthToken = "";
        sessionStorage.removeItem("nobis_installer_token");
      }
      lockScreen.classList.remove("hidden");
      unlockedContent.classList.add("hidden");

      if (noPwdAlert) {
        noPwdAlert.classList.toggle("hidden", !!data.has_password_configured);
      }
      const pwdInput = document.getElementById("installer-password-input");
      if (pwdInput) {
        pwdInput.disabled = !data.has_password_configured;
        if (data.has_password_configured) {
          setTimeout(() => pwdInput.focus(), 100);
        }
      }
    }
  } catch (err) {
    console.error("Erreur vérification statut installateur:", err);
  }
}

async function submitInstallerAuth(event) {
  if (event) event.preventDefault();
  const pwdInput = document.getElementById("installer-password-input");
  const errBox = document.getElementById("installer-auth-error");
  const btn = document.getElementById("installer-auth-submit-btn");
  if (!pwdInput) return;

  const password = pwdInput.value;
  if (!password) {
    if (errBox) {
      errBox.textContent = "Veuillez saisir votre mot de passe Agua Cloud.";
      errBox.classList.remove("hidden");
    }
    return;
  }

  if (errBox) errBox.classList.add("hidden");
  if (btn) {
    btn.disabled = true;
    btn.classList.add("opacity-60", "cursor-wait");
  }

  try {
    const res = await fetch("/api/installer/verify", {
      method: "POST",
      headers: getInstallerHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ password })
    });

    const data = await res.json().catch(() => ({}));

    if (res.ok && (data.authenticated || data.success || data.status === "ok") && data.token) {
      installerAuthToken = data.token;
      sessionStorage.setItem("nobis_installer_token", installerAuthToken);
      pwdInput.value = "";
      if (errBox) errBox.classList.add("hidden");
      showToast("Accès Installateur déverrouillé !");
      await checkInstallerAuthStatus();
    } else {
      let errMsg = "Mot de passe Cloud incorrect.";
      if (res.status === 429) {
        errMsg = data.detail || "Trop de tentatives échouées. Veuillez patienter 60 secondes.";
      } else if (data.detail) {
        errMsg = data.detail;
      }
      if (errBox) {
        errBox.textContent = errMsg;
        errBox.classList.remove("hidden");
      }
      pwdInput.focus();
      pwdInput.select();
    }
  } catch (err) {
    console.error("Erreur authentification installateur:", err);
    if (errBox) {
      errBox.textContent = "Erreur de communication avec le serveur.";
      errBox.classList.remove("hidden");
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.classList.remove("opacity-60", "cursor-wait");
    }
  }
}

async function lockInstallerMenu() {
  try {
    if (installerAuthToken) {
      await fetch("/api/installer/lock", {
        method: "POST",
        headers: { "X-Installer-Token": installerAuthToken }
      });
    }
  } catch (err) {
    console.warn("Erreur lock installer:", err);
  } finally {
    installerAuthToken = "";
    sessionStorage.removeItem("nobis_installer_token");
    showToast("Menu installateur reverrouillé.");
    await checkInstallerAuthStatus();
  }
}

function toggleInstallerPasswordVisibility() {
  const inp = document.getElementById("installer-password-input");
  const icon = document.getElementById("installer-pwd-toggle-icon");
  if (!inp) return;
  if (inp.type === "password") {
    inp.type = "text";
    if (icon) icon.textContent = "🙈";
  } else {
    inp.type = "password";
    if (icon) icon.textContent = "👁️";
  }
}

const SILENCE_OVERRIDABLE_KEYS = {
  "power_level": "la puissance de chauffe",
  "fan_front_speed": "la ventilation frontale",
  "duct_left_speed": "la canalisation gauche",
  "duct_right_speed": "la canalisation droite",
  "fun_auto_set": "le mode Auto",
  "standby_set": "le mode ECO",
  "fun_pwf_set": "le mode BOOST"
};

async function sendCommand(reg_key, value, forceOverride = false) {
  if (!forceOverride && silenceModeState && silenceModeState.active && SILENCE_OVERRIDABLE_KEYS[reg_key]) {
    let targetLabel = "";
    if (reg_key === "power_level") {
      targetLabel = `la puissance de combustion sur P${value}`;
    } else if (reg_key.includes("speed")) {
      const spdText = Number(value) === 0 ? "OFF" : (Number(value) === 8 ? "PS Auto" : `vitesse ${value}`);
      targetLabel = `${SILENCE_OVERRIDABLE_KEYS[reg_key]} sur ${spdText}`;
    } else if (reg_key === "fun_auto_set") {
      targetLabel = Number(value) === 1 ? "l'activation du mode Auto" : "la désactivation du mode Auto";
    } else if (reg_key === "standby_set") {
      targetLabel = Number(value) === 1 ? "l'activation du mode ECO" : "la désactivation du mode ECO";
    } else if (reg_key === "fun_pwf_set") {
      targetLabel = Number(value) > 0 ? "l'activation du mode BOOST" : "la désactivation du mode BOOST";
    } else {
      targetLabel = `${SILENCE_OVERRIDABLE_KEYS[reg_key]} sur ${value}`;
    }

    openActionModal({
      title: "🤫 Mode Silence ! en cours",
      message: `Le mode Silence ! est actuellement actif (temporisé).\n\nPour appliquer manuellement ${targetLabel}, le mode Silence ! doit être désactivé.\n\nConfirmez-vous la désactivation du mode Silence ! et l'application de cette consigne ?`,
      confirmText: "Désactiver et Appliquer",
      onConfirm: async () => {
        await deactivateSilenceMode([{ reg_key, value: Number(value) }]);
      }
    });
    return;
  }

  showToast(`Envoi commande ${reg_key} = ${value}...`);
  try {
    const res = await fetch("/api/command", {
      method: "POST",
      headers: getInstallerHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ reg_key, value: Number(value) })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      const msg = err.detail || res.statusText || "Commande refusée";
      showToast("Erreur : " + msg, true);
      reportClientError("PILOTAGE", `Échec commande ${reg_key}=${value} : ${msg}`);
    } else {
      showToast(`Commande ${reg_key} (${value}) appliquée avec succès !`);
    }
  } catch (e) {
    showToast("Erreur réseau : " + e.message, true);
    reportClientError("RESEAU", `Erreur réseau envoi ${reg_key}=${value} : ${e.message}`);
  }
  await refreshLatestOnly();
  if (!document.getElementById("tab-logs").classList.contains("hidden")) loadAudit();
}

function setAllFansMode(targetVal) {
  const label = targetVal === 0 ? "Convection Naturelle (OFF = 0)" : "Mode PS Automatique (PS = 8)";
  if (silenceModeState && silenceModeState.active && targetVal !== 0) {
    openActionModal({
      title: "🤫 Mode Silence ! en cours",
      message: `Le mode Silence ! est actuellement actif (ventilations coupées).\n\nPasser en ${label} nécessite de désactiver le mode Silence !.\n\nConfirmez-vous la désactivation du mode Silence ! et l'application de cette ventilation ?`,
      confirmText: "Désactiver et Appliquer",
      onConfirm: async () => {
        await deactivateSilenceMode([
          { reg_key: "fan_front_speed", value: targetVal },
          { reg_key: "duct_left_speed", value: targetVal },
          { reg_key: "duct_right_speed", value: targetVal }
        ]);
      }
    });
    return;
  }
  openActionModal({
    title: "🌀 Mode de Ventilation Global (Batch)",
    message: `Passer la soufflerie frontale et les 2 canalisations (SX/DX) en ${label} en une seule écriture groupée ?`,
    confirmText: "Appliquer",
    onConfirm: async () => {
      showToast(`Application groupée : ${label}...`);
      try {
        const res = await fetch("/api/command-batch", {
          method: "POST",
          headers: getInstallerHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({
            commands: [
              { reg_key: "fan_front_speed", value: targetVal },
              { reg_key: "duct_left_speed", value: targetVal },
              { reg_key: "duct_right_speed", value: targetVal }
            ]
          })
        });
        if (res.ok) {
          showToast(`Ventilation globale passée en ${label} !`);
        } else {
          const err = await res.json().catch(() => ({}));
          showToast("Erreur batch : " + (err.detail || res.statusText), true);
        }
      } catch (e) {
        showToast("Erreur réseau : " + e.message, true);
      }
      await refreshLatestOnly();
    }
  });
}

function syncStoveClock() {
  openActionModal({
    title: "⌚ Synchronisation RTC Batch",
    message: "Synchroniser l'horloge interne RTC et le calendrier du poêle (6 registres en une seule écriture groupée) avec l'heure actuelle de Paris ?",
    confirmText: "Synchroniser",
    onConfirm: async () => {
      showToast("Synchronisation de l'horloge RTC en cours...");
      const res = await fetch("/api/sync-clock", { method: "POST" });
      if (res.ok) {
        const j = await res.json();
        await refreshLatestOnly();
        showToast("Horloge synchronisée sur : " + j.synced_to);
      } else {
        const err = await res.json().catch(() => ({}));
        showToast("Erreur sync horloge : " + (err.detail || res.statusText), true);
      }
    }
  });
}

function refillHopper(bagsAdded = 2) {
  const isOneBag = Number(bagsAdded) === 1;
  openActionModal({
    title: isOneBag ? "🌾 Ajout +1 Sac (15 kg / +43%)" : "🌾 Trémie Pleine 100% (35 kg / ~2.3 sacs)",
    message: isOneBag
      ? "Ajouter 1 sac de 15 kg (+42.9% dans la trémie de 35 kg) et décrémenter le stock de granulés de 1 sac ?"
      : "Remplir la trémie à 100 % (capacité certifiée 35 kg) et mettre à jour le stock de sacs de 15 kg ?",
    confirmText: isOneBag ? "+1 Sac (15 kg)" : "Trémie Pleine (35 kg)",
    onConfirm: async () => {
      const addedPct = (15.0 / HOPPER_CAPACITY_KG) * 100.0;
      const targetPct = isOneBag
        ? Math.min(100.0, Number(latestData.pellet_hopper_pct ?? 50) + addedPct)
        : 100.0;
      const payload = isOneBag
        ? { target_pct: targetPct, level_pct: targetPct, bags_added: 1 }
        : { target_pct: 100.0, level_pct: 100.0, bags_added: 2.33 };
      const res = await fetch("/api/refill-hopper", {
        method: "POST",
        headers: getInstallerHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(payload)
      });
      if (res.ok) {
        const j = await res.json();
        await refreshLatestOnly();
        const capKg = j.hopper_capacity_kg ?? HOPPER_CAPACITY_KG;
        const stockBags = j.pellet_bags_stock ?? j.bags_stock ?? "--";
        showToast(`Trémie à ${j.pellet_hopper_pct}% (${capKg} kg max) — Stock restant : ${stockBags} sacs`);
      }
    }
  });
}

let currentPelletAiDiagnosis = null;

function renderPelletQuality(data) {
  const latest = data?.latest || latestData || {};
  let q = data?.pellet_quality || latest.pellet_quality;

  // Calcul dynamique côté client si non fourni directement par le point
  if (!q) {
    const state = Number(latest.status_code ?? 0);
    const pwr = Number(latest.power_actual ?? latest.power_level ?? 0);
    const flame = Number(latest.temp_probe_k ?? 0);
    const curTrim = Number(latest.trim_pellet_pct ?? getRegVal("trim_pellet_pct", 0));

    if (state !== 5 || pwr <= 0) {
      if (state === 5 && pwr === 0) {
        q = {
          status: "modulating",
          quality_score: null,
          quality_label: "Modulation P0 (Éco)",
          quality_color: "sky",
          target_flame_temp: null,
          flame_delta: null,
          current_trim: curTrim,
          suggested_trim: curTrim,
          explanation: `Poêle en modulation P0 (Ralenti / Consigne atteinte). L'apport de granulés est réduit au strict minimum de maintien. L'analyse IA de calibrage requiert un régime de travail nominal P1 à P7.`,
          action_needed: false
        };
      } else {
        q = {
          status: "idle",
          quality_score: null,
          quality_label: "En attente combustion",
          quality_color: "gray",
          target_flame_temp: null,
          flame_delta: null,
          current_trim: curTrim,
          suggested_trim: curTrim,
          explanation: curTrim !== 0
            ? `L'évaluation thermique requiert que le poêle soit en phase active de travail (P1 à P7). Tarage granulés en mémoire : ${curTrim > 0 ? "+" + curTrim : curTrim}%.`
            : "L'évaluation thermique requiert que le poêle soit en phase active de travail (P1 à P7). Tarage granulés : 0%.",
          action_needed: false
        };
      }
    } else if (latest.flame_delta_avg === null || latest.flame_delta_avg === undefined) {
      // Vérification sécurité ventilation : ventilateurs coupés ?
      const vf = Number(latest.vent_front_actual ?? latest.fan_front_speed ?? 0);
      const vdl = Number(latest.duct_left_actual ?? latest.duct_left_speed ?? 0);
      const vdr = Number(latest.duct_right_actual ?? latest.duct_right_speed ?? 0);
      if (vf === 0 && vdl === 0 && vdr === 0) {
        q = {
          status: "natural_convection",
          quality_score: null,
          quality_label: "Ventilation coupée (Convection)",
          quality_color: "amber",
          target_flame_temp: 430.0 + (pwr * 40.0),
          flame_delta: Math.round((flame - (430.0 + (pwr * 40.0))) * 10) / 10,
          flame_delta_avg: null,
          current_trim: curTrim,
          suggested_trim: curTrim,
          explanation: `Ventilateurs de convection coupés (offsets 16, 31, 32 à 0). En convection naturelle, la chaleur s'accumule sans échange pulsé. L'ajustement automatique du tarage est verrouillé par sécurité.`,
          action_needed: false
        };
      } else {
        q = {
          status: "stabilizing",
          quality_score: null,
          quality_label: `Stabilisation P${pwr}`,
          quality_color: "sky",
          target_flame_temp: 430.0 + (pwr * 40.0),
          flame_delta: Math.round((flame - (430.0 + (pwr * 40.0))) * 10) / 10,
          flame_delta_avg: null,
          current_trim: curTrim,
          suggested_trim: curTrim,
          explanation: `Consigne P${pwr} en cours de stabilisation thermique. Le lissage sur 30 minutes requiert que la consigne de puissance et la ventilation ne varient pas dans l'intervalle avant d'évaluer le granulé.`,
          action_needed: false
        };
      }
    } else {
      const targetFlame = 430.0 + (pwr * 40.0);
      const instantDelta = Math.round((flame - targetFlame) * 10) / 10;
      const delta = Math.round(Number(latest.flame_delta_avg) * 10) / 10;
      if (delta > 30.0) {
        const suggested = Math.max(-5, curTrim - (delta > 50.0 ? 2 : 1));
        q = {
          status: "active",
          quality_score: Math.max(60, Math.round(100 - Math.abs(delta) * 0.5)),
          quality_label: "Haute Énergie (Très sec / PCI élevé)",
          quality_color: "amber",
          target_flame_temp: targetFlame,
          flame_delta: instantDelta,
          flame_delta_avg: delta,
          current_trim: curTrim,
          suggested_trim: suggested,
          explanation: `Combustion vive (+${delta}°C vs cible P${pwr}, moyenne 30 min). Granulé très dense et sec (PCI élevé). Conseil : reporter le tarage granulés à ${suggested > 0 ? "+" + suggested : suggested}% sur la radiocommande (MENU ▸ PROGRAMMATION UTILISATEUR ▸ MÉLANGE G/A).`,
          action_needed: suggested !== curTrim
        };
      } else if (delta < -25.0) {
        const suggested = Math.min(5, curTrim + (delta < -45.0 ? 2 : 1));
        q = {
          status: "active",
          quality_score: Math.max(50, Math.round(100 - Math.abs(delta) * 0.7)),
          quality_label: "Qualité Dégradée (Humide / Faible PCI)",
          quality_color: "rose",
          target_flame_temp: targetFlame,
          flame_delta: instantDelta,
          flame_delta_avg: delta,
          current_trim: curTrim,
          suggested_trim: suggested,
          explanation: `Rendement thermique en retrait (${delta}°C vs cible P${pwr}, moyenne 30 min). Granulé potentiellement humide (>10%) ou poussiéreux. Conseil : reporter le tarage granulés à ${suggested > 0 ? "+" + suggested : suggested}% sur la radiocommande (MENU ▸ PROGRAMMATION UTILISATEUR ▸ MÉLANGE G/A).`,
          action_needed: suggested !== curTrim
        };
      } else {
        q = {
          status: "active",
          quality_score: Math.min(100, Math.round(96 - Math.abs(delta) * 0.3)),
          quality_label: "Optimale (Standard DIN+ / ENplus)",
          quality_color: "emerald",
          target_flame_temp: targetFlame,
          flame_delta: instantDelta,
          flame_delta_avg: delta,
          current_trim: curTrim,
          suggested_trim: curTrim,
          explanation: `Combustion équilibrée et propre (écart thermique moyen de ${delta >= 0 ? "+" + delta : delta}°C en P${pwr} sur 30 min stables). Pouvoir calorifique nominal. Aucun ajustement de tarage requis.`,
          action_needed: false
        };
      }
    }
  }

  currentPelletAiDiagnosis = q;

  // Mise à jour du badge d'état
  const badgeEl = document.getElementById("pellet-quality-badge");
  if (badgeEl) {
    badgeEl.textContent = q.quality_label;
    if (q.quality_color === "emerald") {
      badgeEl.className = "text-[10px] font-semibold px-2 py-0.5 rounded-full bg-emerald-950/80 text-emerald-400 border border-emerald-700/50";
    } else if (q.quality_color === "amber") {
      badgeEl.className = "text-[10px] font-semibold px-2 py-0.5 rounded-full bg-amber-950/80 text-amber-400 border border-amber-700/50";
    } else if (q.quality_color === "rose") {
      badgeEl.className = "text-[10px] font-semibold px-2 py-0.5 rounded-full bg-rose-950/80 text-rose-400 border border-rose-700/50";
    } else if (q.quality_color === "sky") {
      badgeEl.className = "text-[10px] font-semibold px-2 py-0.5 rounded-full bg-sky-950/80 text-sky-400 border border-sky-700/50";
    } else {
      badgeEl.className = "text-[10px] font-semibold px-2 py-0.5 rounded-full bg-gray-800 text-gray-300 border border-gray-700";
    }
  }

  // Métriques thermiques
  const flameEl = document.getElementById("pellet-flame-val");
  if (flameEl) {
    if ((q.status === "active" || q.status === "stabilizing" || q.status === "natural_convection") && q.target_flame_temp) {
      flameEl.textContent = `${Math.round(latest.temp_probe_k || 0)} / ${Math.round(q.target_flame_temp)}`;
    } else {
      flameEl.textContent = `${Math.round(latest.temp_probe_k || 0)} / --`;
    }
  }

  const deltaEl = document.getElementById("pellet-delta-val");
  if (deltaEl) {
    if (q.status === "stabilizing") {
      deltaEl.textContent = q.stable_minutes ? `${q.stable_minutes}/30m` : "En cours";
      deltaEl.className = "font-bold text-sky-400 text-xs";
    } else if (q.status === "natural_convection") {
      deltaEl.textContent = "Conv. Nat.";
      deltaEl.className = "font-bold text-amber-400 text-xs";
    } else if (q.status === "active" && q.flame_delta_avg !== null && q.flame_delta_avg !== undefined) {
      const dVal = q.flame_delta_avg;
      deltaEl.textContent = `${dVal >= 0 ? "+" : ""}${Number(dVal).toFixed(1)}°C`;
      deltaEl.className = "font-bold text-xs " + (q.quality_color === "emerald" ? "text-emerald-400" : (q.quality_color === "amber" ? "text-amber-400" : "text-rose-400"));
    } else {
      deltaEl.textContent = "--";
      deltaEl.className = "font-bold text-gray-400 text-xs";
    }
  }

  const curTrimEl = document.getElementById("pellet-cur-trim");
  if (curTrimEl) {
    const curVal = q.current_trim ?? 0;
    curTrimEl.textContent = curVal > 0 ? `+${curVal}` : `${curVal}`;
  }

  // Diagnostic explicatif IA
  const expEl = document.getElementById("pellet-ai-explanation");
  if (expEl) {
    expEl.textContent = q.explanation;
  }

  // Proposition & Action de tarage IA
  const propEl = document.getElementById("pellet-ai-trim-proposal");
  const applyBtn = document.getElementById("pellet-ai-apply-btn");
  if (propEl) {
    if (q.action_needed) {
      const sign = q.suggested_trim > 0 ? "+" : "";
      propEl.textContent = `Tarage recommandé : ${sign}${q.suggested_trim}% (Actuel : ${q.current_trim}%)`;
      if (applyBtn) {
        applyBtn.classList.remove("hidden");
        applyBtn.textContent = `Appliquer (${sign}${q.suggested_trim}%)`;
      }
    } else if (q.status === "stabilizing") {
      propEl.textContent = `Stabilisation thermique en cours (${q.stable_minutes || 0}/30 min sans variation)`;
      if (applyBtn) {
        applyBtn.classList.add("hidden");
      }
    } else if (q.status === "natural_convection") {
      propEl.textContent = "Tarage verrouillé : ventilateurs à l'arrêt (convection naturelle)";
      if (applyBtn) {
        applyBtn.classList.add("hidden");
      }
    } else {
      propEl.textContent = q.status === "active" ? "Tarage optimal (aucun ajustement requis)" : "Tarage en attente de combustion active";
      if (applyBtn) {
        applyBtn.classList.add("hidden");
      }
    }
  }
}

function applyAiPelletTrim() {
  if (!currentPelletAiDiagnosis || !currentPelletAiDiagnosis.action_needed) {
    showToast("Aucun ajustement de tarage requis actuellement.");
    return;
  }
  const targetVal = Number(currentPelletAiDiagnosis.suggested_trim);
  const sign = targetVal > 0 ? "+" : "";
  const activeMode = latestData.comm_mode || (currentSettings && currentSettings.comm_mode) || "agua_cloud";
  const isAgua = (activeMode === "agua_cloud");
  const remoteNote = isAgua
    ? `\n\n⚠️ IMPORTANT (Mode Cloud Agua) :\nLes offsets 70 et 71 ne figurent pas dans la table Cloud Agua IOT. L'application enregistre cette valeur en mémoire locale, mais vous devez la valider sur votre radiocommande :\nMENU ▸ PROGRAMMATION UTILISATEUR ▸ MÉLANGE G/A.`
    : "";

  openActionModal({
    title: "✨ Tarage Granulés Optimisé par IA",
    message: `Appliquer la recommandation IA sur le tarage d'alimentation en granulés ?\n\n• Nouveau tarage recommandé : ${sign}${targetVal} %\n• Tarage actuel : ${currentPelletAiDiagnosis.current_trim} %\n\nMotif : ${currentPelletAiDiagnosis.explanation}${remoteNote}`,
    confirmText: isAgua ? `Mémoriser (${sign}${targetVal}%)` : `Appliquer (${sign}${targetVal}%)`,
    onConfirm: async () => {
      await sendCommand("trim_pellet_pct", targetVal);
      showToast(`Tarage granulés ${sign}${targetVal}% enregistré.${isAgua ? " Validez sur votre radiocommande (MÉLANGE G/A)." : ""}`);
      await refreshLatestOnly();
    }
  });
}

let currentExhaustAiDiagnosis = null;

function renderExhaustQuality(data) {
  const latest = data?.latest || latestData || {};
  let q = data?.exhaust_quality || latest.exhaust_quality;

  // Calcul dynamique côté client si non fourni directement
  if (!q) {
    const state = Number(latest.status_code ?? 0);
    const pwr = Number(latest.power_actual ?? latest.power_level ?? 0);
    const dep = Number(latest.depression_pa ?? 0);
    const rpm = Number(latest.exhaust_rpm ?? 0);
    const curTrim = Number(latest.trim_exhaust_pct ?? getRegVal("trim_exhaust_pct", 0));

    if (state !== 5 || pwr <= 0) {
      if (state === 5 && pwr === 0) {
        q = {
          status: "modulating",
          quality_score: null,
          quality_label: "Modulation P0 (Éco)",
          quality_color: "sky",
          target_depression_pa: null,
          depression_pa: dep,
          dep_delta: null,
          dep_delta_avg: null,
          exhaust_rpm: rpm,
          current_trim: curTrim,
          suggested_trim: curTrim,
          explanation: `Poêle en modulation P0 (Ralenti / Consigne atteinte). L'extracteur tourne à vitesse réduite de maintien (${rpm} RPM). L'analyse aéraulique de calibrage requiert un régime nominal P1 à P7.`,
          action_needed: false
        };
      } else {
        q = {
          status: "idle",
          quality_score: null,
          quality_label: "En attente tirage actif",
          quality_color: "gray",
          target_depression_pa: null,
          depression_pa: dep,
          dep_delta: null,
          dep_delta_avg: null,
          exhaust_rpm: rpm,
          current_trim: curTrim,
          suggested_trim: curTrim,
          explanation: curTrim !== 0
            ? `L'analyse aéraulique requiert que le poêle soit en phase active de travail (P1 à P7). Tarage extracteur fumées en mémoire : ${curTrim > 0 ? "+" + curTrim : curTrim}%.`
            : "L'analyse aéraulique requiert que le poêle soit en phase active de travail (P1 à P7). Tarage extracteur : 0%.",
          action_needed: false
        };
      }
    } else if (latest.dep_delta_avg === null || latest.dep_delta_avg === undefined) {
      q = {
        status: "stabilizing",
        quality_score: null,
        quality_label: `Stabilisation P${pwr}`,
        quality_color: "sky",
        target_depression_pa: Math.round((2.0 + (pwr * 0.25)) * 10) / 10,
        depression_pa: dep,
        dep_delta: Math.round((dep - (2.0 + (pwr * 0.25))) * 10) / 10,
        dep_delta_avg: null,
        exhaust_rpm: rpm,
        current_trim: curTrim,
        suggested_trim: curTrim,
        explanation: `Consigne P${pwr} en cours de stabilisation aéraulique. Le lissage sur 30 minutes requiert une consigne de puissance et une ventilation stables avant d'évaluer le tirage.`,
        action_needed: false
      };
    } else {
      const targetDep = Math.round((2.0 + (pwr * 0.25)) * 10) / 10;
      const instantDelta = Math.round((dep - targetDep) * 10) / 10;
      const delta = Math.round(Number(latest.dep_delta_avg) * 10) / 10;
      if (delta < -1.0 || dep < 1.2) {
        const suggested = Math.min(5, curTrim + (delta < -2.0 ? 2 : 1));
        q = {
          status: "active",
          quality_score: Math.max(50, Math.round(100 - Math.abs(delta) * 20.0)),
          quality_label: "Débit d'Air Insuffisant (Pertes de charge)",
          quality_color: "rose",
          target_depression_pa: targetDep,
          depression_pa: dep,
          dep_delta: instantDelta,
          dep_delta_avg: delta,
          exhaust_rpm: rpm,
          current_trim: curTrim,
          suggested_trim: suggested,
          explanation: `Dépression comburant inférieure à la consigne (${delta >= 0 ? "+" + delta : delta} Pa vs cible ${targetDep} Pa, moyenne 30 min). Mesure capteur d'entrée à ${dep.toFixed(1)} Pa (seuil d'alerte AL11 à 1,0 Pa). Conseil : reporter le tarage extracteur à ${suggested > 0 ? "+" + suggested : suggested}% sur la radiocommande (MENU ▸ PROGRAMMATION UTILISATEUR ▸ MÉLANGE G/A).`,
          action_needed: suggested !== curTrim
        };
      } else if (delta > 1.5 || dep > 6.0) {
        const suggested = Math.max(-5, curTrim - (delta > 2.5 ? 2 : 1));
        q = {
          status: "active",
          quality_score: Math.max(55, Math.round(100 - Math.abs(delta) * 15.0)),
          quality_label: "Débit d'Air Élevé (Sur-tirage dynamique)",
          quality_color: "amber",
          target_depression_pa: targetDep,
          depression_pa: dep,
          dep_delta: instantDelta,
          dep_delta_avg: delta,
          exhaust_rpm: rpm,
          current_trim: curTrim,
          suggested_trim: suggested,
          explanation: `Dépression comburant élevée (${delta >= 0 ? "+" + delta : delta} Pa vs cible ${targetDep} Pa, moyenne 30 min). Sur-tirage ou vent fort évacuant trop vite les calories. Conseil : réduire l'extracteur à ${suggested > 0 ? "+" + suggested : suggested}% sur la radiocommande (MENU ▸ PROGRAMMATION UTILISATEUR ▸ MÉLANGE G/A).`,
          action_needed: suggested !== curTrim
        };
      } else {
        q = {
          status: "active",
          quality_score: Math.min(100, Math.round(98 - Math.abs(delta) * 10.0)),
          quality_label: "Aéraulique Optimale (Débit Conforme)",
          quality_color: "emerald",
          target_depression_pa: targetDep,
          depression_pa: dep,
          dep_delta: instantDelta,
          dep_delta_avg: delta,
          exhaust_rpm: rpm,
          current_trim: curTrim,
          suggested_trim: curTrim,
          explanation: `Dépression dynamique et débit comburant parfaitement équilibrés (${dep.toFixed(1)} Pa, écart moyen de ${delta >= 0 ? "+" + delta : delta} Pa en P${pwr} sur 30 min stables). Conforme aux spécifications Nobis. Aucun ajustement requis.`,
          action_needed: false
        };
      }
    }
  }

  currentExhaustAiDiagnosis = q;

  // Mise à jour du badge d'état
  const badgeEl = document.getElementById("exhaust-quality-badge");
  if (badgeEl) {
    badgeEl.textContent = q.quality_label;
    if (q.quality_color === "emerald") {
      badgeEl.className = "text-[10px] font-semibold px-2 py-0.5 rounded-full bg-emerald-950/80 text-emerald-400 border border-emerald-700/50";
    } else if (q.quality_color === "amber") {
      badgeEl.className = "text-[10px] font-semibold px-2 py-0.5 rounded-full bg-amber-950/80 text-amber-400 border border-amber-700/50";
    } else if (q.quality_color === "rose") {
      badgeEl.className = "text-[10px] font-semibold px-2 py-0.5 rounded-full bg-rose-950/80 text-rose-400 border border-rose-700/50";
    } else if (q.quality_color === "sky") {
      badgeEl.className = "text-[10px] font-semibold px-2 py-0.5 rounded-full bg-sky-950/80 text-sky-400 border border-sky-700/50";
    } else {
      badgeEl.className = "text-[10px] font-semibold px-2 py-0.5 rounded-full bg-gray-800 text-gray-300 border border-gray-700";
    }
  }

  // Métriques dépression
  const depEl = document.getElementById("exhaust-dep-val");
  if (depEl) {
    if ((q.status === "active" || q.status === "stabilizing") && q.target_depression_pa !== null && q.target_depression_pa !== undefined) {
      depEl.textContent = `${Number(latest.depression_pa || 0).toFixed(1)} / ${Number(q.target_depression_pa).toFixed(1)}`;
    } else {
      depEl.textContent = `${Number(latest.depression_pa || 0).toFixed(1)} / --`;
    }
  }

  const deltaEl = document.getElementById("exhaust-delta-val");
  if (deltaEl) {
    if (q.status === "stabilizing") {
      deltaEl.textContent = q.stable_minutes ? `${q.stable_minutes}/30m` : "En cours";
      deltaEl.className = "font-bold text-sky-400 text-xs";
    } else if (q.status === "active" && q.dep_delta_avg !== null && q.dep_delta_avg !== undefined) {
      const dVal = q.dep_delta_avg;
      deltaEl.textContent = `${dVal >= 0 ? "+" : ""}${Number(dVal).toFixed(1)} Pa`;
      deltaEl.className = "font-bold text-xs " + (q.quality_color === "emerald" ? "text-emerald-400" : (q.quality_color === "amber" ? "text-amber-400" : "text-rose-400"));
    } else {
      deltaEl.textContent = "--";
      deltaEl.className = "font-bold text-gray-400 text-xs";
    }
  }

  const curTrimEl = document.getElementById("exhaust-cur-trim");
  if (curTrimEl) {
    const curVal = q.current_trim ?? 0;
    curTrimEl.textContent = curVal > 0 ? `+${curVal}` : `${curVal}`;
  }

  // Diagnostic explicatif IA
  const expEl = document.getElementById("exhaust-ai-explanation");
  if (expEl) {
    expEl.textContent = q.explanation;
  }

  // Proposition & Action de tarage IA
  const propEl = document.getElementById("exhaust-ai-trim-proposal");
  const applyBtn = document.getElementById("exhaust-ai-apply-btn");
  if (propEl) {
    if (q.action_needed) {
      const sign = q.suggested_trim > 0 ? "+" : "";
      propEl.textContent = `Tarage recommandé : ${sign}${q.suggested_trim}% (Actuel : ${q.current_trim}%)`;
      if (applyBtn) {
        applyBtn.classList.remove("hidden");
        applyBtn.textContent = `Appliquer (${sign}${q.suggested_trim}%)`;
      }
    } else if (q.status === "stabilizing") {
      propEl.textContent = `Stabilisation aéraulique en cours (${q.stable_minutes || 0}/30 min sans variation)`;
      if (applyBtn) {
        applyBtn.classList.add("hidden");
      }
    } else {
      propEl.textContent = q.status === "active" ? "Tarage optimal (aucun ajustement requis)" : "Tarage en attente de combustion active";
      if (applyBtn) {
        applyBtn.classList.add("hidden");
      }
    }
  }
}

function applyAiExhaustTrim() {
  if (!currentExhaustAiDiagnosis || !currentExhaustAiDiagnosis.action_needed) {
    showToast("Aucun ajustement de tarage extracteur requis actuellement.");
    return;
  }
  const targetVal = Number(currentExhaustAiDiagnosis.suggested_trim);
  const sign = targetVal > 0 ? "+" : "";
  const activeMode = latestData.comm_mode || (currentSettings && currentSettings.comm_mode) || "agua_cloud";
  const isAgua = (activeMode === "agua_cloud");
  const remoteNote = isAgua
    ? `\n\n⚠️ IMPORTANT (Mode Cloud Agua) :\nLes offsets 70 et 71 ne figurent pas dans la table Cloud Agua IOT. L'application enregistre cette valeur en mémoire locale, mais vous devez la valider sur votre radiocommande :\nMENU ▸ PROGRAMMATION UTILISATEUR ▸ MÉLANGE G/A.`
    : "";

  openActionModal({
    title: "✨ Tarage Extracteur Fumées Optimisé par IA",
    message: `Appliquer la recommandation IA sur la vitesse de l'extracteur de fumées ?\n\n• Nouveau tarage recommandé : ${sign}${targetVal} %\n• Tarage actuel : ${currentExhaustAiDiagnosis.current_trim} %\n\nMotif : ${currentExhaustAiDiagnosis.explanation}${remoteNote}`,
    confirmText: isAgua ? `Mémoriser (${sign}${targetVal}%)` : `Appliquer (${sign}${targetVal}%)`,
    onConfirm: async () => {
      await sendCommand("trim_exhaust_pct", targetVal);
      showToast(`Tarage extracteur fumées ${sign}${targetVal}% enregistré.${isAgua ? " Validez sur votre radiocommande (MÉLANGE G/A)." : ""}`);
      await refreshLatestOnly();
    }
  });
}

function promptResetPelletBatch() {
  openActionModal({
    title: "🔄 RAZ Changement de Marque de Granulés",
    message: "Avant de confirmer la remise à zéro :\n" +
             "1. Aspirez le fond de la trémie (35 kg), le brasero et derrière le coupe-flamme (poêle froid et débranché).\n" +
             "2. Sur la télécommande Nobis, remettez MENU ▸ MÉLANGE G/A sur PELLET = 00 et PRISE D'AIR = 00.\n\n" +
             "Indiquez le nom ou la certification du nouveau lot de granulés (ENplus A1 / DINplus) :",
    showInput: true,
    defaultValue: "Lot DINplus / ENplus A1 - " + new Date().toLocaleDateString("fr-FR"),
    confirmText: "Lancer RAZ (0% & 180 min)",
    onConfirm: async (brandName) => {
      try {
        showToast("Remise à zéro de l'évaluation Mélange P/A en cours...");
        const res = await fetch("/api/pellet-batch/raz", {
          method: "POST",
          headers: getInstallerHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({ pellet_brand: brandName })
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          showToast("Erreur RAZ : " + (err.detail || res.statusText), true);
          return;
        }
        await loadDashboard();
        showToast(`RAZ effectué pour « ${brandName} » : évaluation de 180 min démarrée !`);
      } catch (e) {
        showToast("Erreur réseau RAZ : " + e.message, true);
      }
    }
  });
}

function resetStoveAlarm() {
  const st = Number(latestData.status_code ?? 0);
  const tf = Number(latestData.temp_fumes ?? 0);
  const rpm = Number(latestData.exhaust_rpm ?? 0);
  const alarm = Number(latestData.alarm_code ?? 0);
  const isPelletOpen = Boolean(latestData.popup_pellet_open || alarm === 16);
  const isDoorOpen = Boolean(latestData.popup_door_open || alarm === 15);

  // CAS 1 : Sécurité mécanique - Trappe granulés ouverte
  if (isPelletOpen) {
    openActionModal({
      title: "🔒 Sécurité Mécanique : Trappe Granulés Ouverte",
      htmlContent: `
        <div class="space-y-3 text-xs leading-relaxed">
          <div class="p-2.5 rounded-lg bg-amber-950/50 border border-amber-600/60 text-amber-200">
            <strong>⚠️ Contacteur de sécurité réservoir ouvert :</strong> La trappe du réservoir à granulés est ouverte ou son capteur n'est pas engagé.
          </div>
          <div class="text-gray-300 space-y-1.5 text-[11px]">
            <p>• Cette sécurité matérielle <strong>coupe immédiatement le moteur de la vis sans fin</strong> pour prévenir tout risque mécanique.</p>
            <p>• <span class="text-rose-400 font-bold">Cette sécurité ne s'acquitte pas à distance ni via un bouton logiciel.</span></p>
            <p>• <strong>Action requise :</strong> Refermez simplement et fermement la trappe du réservoir à granulés sur le poêle.</p>
            <p class="text-emerald-400">Dès fermeture physique, le contacteur se réenclenche et l'alerte s'efface automatiquement.</p>
          </div>
        </div>
      `,
      confirmText: "Compris, je referme la trappe",
      showCancel: false,
      onConfirm: () => {}
    });
    return;
  }

  // CAS 2 : Sécurité mécanique - Porte foyer ouverte
  if (isDoorOpen) {
    openActionModal({
      title: "🚪 Sécurité Mécanique : Porte Foyer Ouverte",
      htmlContent: `
        <div class="space-y-3 text-xs leading-relaxed">
          <div class="p-2.5 rounded-lg bg-amber-950/50 border border-amber-600/60 text-amber-200">
            <strong>⚠️ Contacteur de porte ouvert :</strong> La porte vitrée du foyer de combustion est ouverte.
          </div>
          <div class="text-gray-300 space-y-1.5 text-[11px]">
            <p>• Cette sécurité matérielle coupe la vis sans fin et interdit l'allumage.</p>
            <p>• <span class="text-rose-400 font-bold">Elle ne s'acquitte pas à distance.</span></p>
            <p>• <strong>Action requise :</strong> Refermez physiquement la porte du foyer et verrouillez la poignée.</p>
          </div>
        </div>
      `,
      confirmText: "Compris, je ferme la porte",
      showCancel: false,
      onConfirm: () => {}
    });
    return;
  }

  // CAS 3 : Alarme AL03 - PELLET FINI (Réservoir vide / Vis désamorcée)
  if (alarm === 3) {
    const isCooling = (st === 10 && tf > 65);
    openActionModal({
      title: "🛡️ Procédure Constructeur : Alarme AL03 (Pellet Fini)",
      htmlContent: `
        <div class="space-y-3 text-xs leading-relaxed">
          <div class="p-2.5 rounded-lg bg-rose-950/50 border border-rose-600/60 text-rose-200">
            <strong>⚠️ L'alarme AL03 ne s'acquitte JAMAIS à distance</strong>, même poêle froid (sécurité matérielle constructeur Nobis & norme EN 14785).
          </div>

          ${isCooling ? `
          <div class="p-2.5 rounded-lg bg-amber-950/40 border border-amber-600/50 text-amber-200">
            <strong>⏳ Phase de refroidissement actif en cours :</strong>
            <div class="mt-1 flex items-center justify-between text-[11px] tabular-nums">
              <span>Température fumées : <b>${tf}°C</b></span>
              <span>Extracteur fumées : <b>${rpm} tr/min</b></span>
            </div>
            <p class="mt-1 text-[11px] text-gray-300">La carte électronique verrouille tout acquittement tant que les fumées ne sont pas descendues sous ~65°C.</p>
          </div>
          ` : `
          <div class="p-2 rounded-lg bg-emerald-950/40 border border-emerald-600/50 text-emerald-300 text-[11px]">
            ❄️ <strong>Poêle refroidi (${tf}°C) :</strong> Le réarmement physique sur la télécommande est possible.
          </div>
          `}

          <div class="bg-gray-900/80 p-3 rounded-lg border border-gray-800 space-y-2">
            <div class="font-bold text-orange-400 text-[11px] uppercase tracking-wide">Procédure obligatoire sur le poêle Nobis :</div>
            <ol class="list-decimal pl-4 space-y-1.5 text-gray-200 text-[11px]">
              <li><strong>Remplir la trémie :</strong> Verser les granulés dans le réservoir et refermer la trappe (l'alerte trappe s'efface automatiquement).</li>
              <li><strong>Acquitter l'alarme :</strong> Sur la <strong>télécommande physique Nobis</strong>, maintenir la touche <strong>Marche/Arrêt (P3)</strong> enfoncée pendant <strong>3 à 5 secondes</strong> jusqu'au signal sonore d'acquittement.</li>
              <li><strong>Recharger la vis sans fin :</strong> En cas de panne sèche, la vis sans fin est totalement vide. Il faut lancer la fonction de <strong>chargement initial de la vis</strong> via le menu de la télécommande pour amener les pellets au brasier avant tout allumage (évite un nouvel échec d'allumage).</li>
              <li><strong>Vérifier le brasier :</strong> Vider et nettoyer le creuset avant de relancer l'allumage.</li>
            </ol>
          </div>
        </div>
      `,
      confirmText: "Compris (Effectuer sur télécommande)",
      cancelText: "Envoyer OFF bus (170)",
      showCancel: true,
      onConfirm: () => {},
      onCancel: async () => {
        await sendCommand("status_code", 170);
      }
    });
    return;
  }

  // CAS 4 : Autre alarme ou sécurité Nobis
  const alarmDesc = (latestData.alarm_label && !latestData.alarm_label.startsWith("Aucune"))
    ? latestData.alarm_label
    : (alarm > 0 ? `AL${alarm < 10 ? '0' + alarm : alarm}` : (st === 11 ? "Mémoire Alarme" : "Alarme de sécurité"));
  const isCooling = (st === 10 && tf > 65);

  openActionModal({
    title: `🛡️ Procédure Constructeur : ${alarmDesc}`,
    htmlContent: `
      <div class="space-y-3 text-xs leading-relaxed">
        <div class="p-2.5 rounded-lg bg-rose-950/50 border border-rose-600/60 text-rose-200">
          <strong>⚠️ Alarme active : ${alarmDesc}</strong>
          <p class="mt-1 text-[11px] text-gray-300">Conformément aux normes constructeur Nobis, les alarmes de sécurité critiques ne s'acquittent pas à distance.</p>
        </div>

        ${isCooling ? `
        <div class="p-2.5 rounded-lg bg-amber-950/40 border border-amber-600/50 text-amber-200">
          <strong>⏳ Refroidissement sécurité en cours :</strong> Fumées à <b>${tf}°C</b> (${rpm} tr/min). Tout redémarrage est verrouillé par la carte mère jusqu'à refroidissement complet (< 65°C).
        </div>
        ` : `
        <div class="p-2 rounded-lg bg-emerald-950/40 border border-emerald-600/50 text-emerald-300 text-[11px]">
          ❄️ Poêle refroidi (${tf}°C). L'acquittement physique est possible.
        </div>
        `}

        <div class="bg-gray-900/80 p-3 rounded-lg border border-gray-800 space-y-1.5 text-[11px] text-gray-200">
          <div class="font-bold text-orange-400 uppercase tracking-wide">Procédure de déblocage :</div>
          <p>1. Attendre l'arrêt complet de la ventilation.</p>
          <p>2. Résoudre la cause physique du défaut.</p>
          <p>3. Effectuer un <strong>appui long (3 à 5 secondes)</strong> sur la touche <strong>Marche/Arrêt (P3)</strong> de la télécommande physique Nobis.</p>
          <p class="text-gray-400 text-[10px] italic">* Si alarme AL14 (sécurité thermique) : dévisser le bouchon à l'arrière du poêle pour réarmer le bouton poussoir mécanique bilame.</p>
        </div>
      </div>
    `,
    confirmText: "Compris (Effectuer sur télécommande)",
    cancelText: "Envoyer OFF bus (170)",
    showCancel: true,
    onConfirm: () => {},
    onCancel: async () => {
      await sendCommand("status_code", 170);
    }
  });
}

function stepControl(reg_key, delta, minV, maxV) {
  const cur = Number(getRegVal(reg_key, 19));
  const next = Math.max(minV, Math.min(maxV, cur + delta));
  if (reg_key === "temp_amb_setpoint") updateAmbSetpointPreview(next);
  if (reg_key === "duct_left_setpoint") updateDuctSetpointPreview("dl", next);
  if (reg_key === "duct_right_setpoint") updateDuctSetpointPreview("dr", next);
  sendCommand(reg_key, next);
}

function toggleAutoMode() {
  const cur = Number(getRegVal("fun_auto_set", latestData.fun_auto_set ?? 1));
  sendCommand("fun_auto_set", cur ? 0 : 1);
}

function toggleEcoMode() {
  const isEco = Number(getRegVal("standby_set", 0)) === 1;
  sendCommand("standby_set", isEco ? 0 : 1);
}

// =============================================================================
// GESTION DU MODE SILENCE ! (POPUP TEMPORISÉE, CONVECTION PURE & RESTAURATION)
// =============================================================================
let selectedSilenceHours = 2.0;
let lastSilenceRemaining = null;

function formatSilenceCountdown(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const hh = Math.floor(s / 3600);
  const mm = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  return `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
}

function updateSilenceButtonState() {
  updateModeRing();
}

// Met à jour l'anneau extérieur des 5 modes de fonctionnement (Silence, 🔥+💨 Auto, BOOST, 💨 Auto, ECO)
function updateModeRing() {
  const autoVal = Number(getRegVal("fun_auto_set", 0)) === 1;
  const isSilActive = Boolean(silenceModeState && silenceModeState.active);
  const remSec = silenceModeState ? Math.max(0, Math.round(silenceModeState.end_epoch - Date.now() / 1000)) : 0;
  const isEco = !isSilActive && (Number(getRegVal("standby_set", 0)) === 1);
  const pwfVal = Number(getRegVal("fun_pwf_set", latestData.fun_pwf_set ?? 0));
  const isPsAll = (Number(getRegVal("fan_front_speed", 0)) === 8 &&
                   Number(getRegVal("duct_left_speed", 0)) === 8 &&
                   Number(getRegVal("duct_right_speed", 0)) === 8);

  // 1. 🤫 Silence ! (Haut-Gauche)
  const silPath = document.getElementById("ctl-ring-silence-path");
  const silLabel = document.getElementById("ctl-ring-silence-label");
  if (silPath) {
    if (isSilActive && remSec > 0) {
      silPath.setAttribute("fill", "rgba(126, 34, 206, 0.92)");
      silPath.setAttribute("stroke", "#c084fc");
      silPath.setAttribute("stroke-width", "2");
      silPath.style.filter = "drop-shadow(0 0 10px rgba(168, 85, 247, 0.7))";
      if (silLabel) {
        silLabel.textContent = formatSilenceCountdown(remSec);
        silLabel.setAttribute("fill", "#f3e8ff");
        silLabel.setAttribute("font-family", "monospace");
      }
    } else {
      silPath.setAttribute("fill", "rgba(15, 23, 42, 0.88)");
      silPath.setAttribute("stroke", "rgba(71, 85, 105, 0.7)");
      silPath.setAttribute("stroke-width", "1.5");
      silPath.style.filter = "none";
      if (silLabel) {
        silLabel.textContent = "Silence !";
        silLabel.setAttribute("fill", "#e2e8f0");
        silLabel.removeAttribute("font-family");
      }
    }
  }

  // 2. 🔥+💨 Auto (Haut-Droite)
  const autoPath = document.getElementById("ctl-ring-auto-path");
  if (autoPath) {
    if (autoVal) {
      autoPath.setAttribute("fill", "rgba(16, 185, 129, 0.9)");
      autoPath.setAttribute("stroke", "#6ee7b7");
      autoPath.setAttribute("stroke-width", "2");
      autoPath.style.filter = "drop-shadow(0 0 10px rgba(16, 185, 129, 0.65))";
    } else {
      autoPath.setAttribute("fill", "rgba(15, 23, 42, 0.88)");
      autoPath.setAttribute("stroke", "rgba(71, 85, 105, 0.7)");
      autoPath.setAttribute("stroke-width", "1.5");
      autoPath.style.filter = "none";
    }
  }

  // 3. ⚡ BOOST (Bas-Droite)
  const boostPath = document.getElementById("ctl-ring-boost-path");
  const boostIcon = document.getElementById("ctl-ring-boost-icon");
  const boostBadge = document.getElementById("ctl-ring-boost-badge");
  if (boostPath) {
    if (pwfVal === 1) {
      // BOOST Continu
      boostPath.setAttribute("fill", "rgba(234, 88, 12, 0.92)");
      boostPath.setAttribute("stroke", "#fdba74");
      boostPath.setAttribute("stroke-width", "2");
      boostPath.style.filter = "drop-shadow(0 0 10px rgba(234, 88, 12, 0.65))";
      if (boostIcon) boostIcon.textContent = "⚡";
      if (boostBadge) {
        boostBadge.textContent = "CONTINU";
        boostBadge.setAttribute("fill", "#ffedd5");
      }
    } else if (pwfVal === 2) {
      // BOOST Chrono
      boostPath.setAttribute("fill", "rgba(2, 132, 199, 0.92)");
      boostPath.setAttribute("stroke", "#7dd3fc");
      boostPath.setAttribute("stroke-width", "2");
      boostPath.style.filter = "drop-shadow(0 0 10px rgba(2, 132, 199, 0.65))";
      if (boostIcon) boostIcon.textContent = "⏱️";
      if (boostBadge) {
        boostBadge.textContent = "CHRONO";
        boostBadge.setAttribute("fill", "#e0f2fe");
      }
    } else {
      // BOOST OFF
      boostPath.setAttribute("fill", "rgba(15, 23, 42, 0.88)");
      boostPath.setAttribute("stroke", "rgba(71, 85, 105, 0.7)");
      boostPath.setAttribute("stroke-width", "1.5");
      boostPath.style.filter = "none";
      if (boostIcon) boostIcon.textContent = "⚡";
      if (boostBadge) {
        boostBadge.textContent = "OFF";
        boostBadge.setAttribute("fill", "#94a3b8");
      }
    }
  }

  // 4. 💨 Auto (Mode PS Global, Bas / 6h)
  const fanAutoPath = document.getElementById("ctl-ring-fanauto-path");
  if (fanAutoPath) {
    if (isPsAll) {
      fanAutoPath.setAttribute("fill", "rgba(8, 145, 178, 0.92)");
      fanAutoPath.setAttribute("stroke", "#67e8f9");
      fanAutoPath.setAttribute("stroke-width", "2");
      fanAutoPath.style.filter = "drop-shadow(0 0 10px rgba(8, 145, 178, 0.65))";
    } else {
      fanAutoPath.setAttribute("fill", "rgba(8, 47, 73, 0.65)");
      fanAutoPath.setAttribute("stroke", "rgba(14, 116, 144, 0.75)");
      fanAutoPath.setAttribute("stroke-width", "1.5");
      fanAutoPath.style.filter = "none";
    }
  }

  // 5. 💤 ECO (Bas-Gauche)
  const ecoPath = document.getElementById("ctl-ring-eco-path");
  if (ecoPath) {
    if (isEco) {
      ecoPath.setAttribute("fill", "rgba(16, 185, 129, 0.9)");
      ecoPath.setAttribute("stroke", "#6ee7b7");
      ecoPath.setAttribute("stroke-width", "2");
      ecoPath.style.filter = "drop-shadow(0 0 10px rgba(16, 185, 129, 0.65))";
    } else {
      ecoPath.setAttribute("fill", "rgba(15, 23, 42, 0.88)");
      ecoPath.setAttribute("stroke", "rgba(71, 85, 105, 0.7)");
      ecoPath.setAttribute("stroke-width", "1.5");
      ecoPath.style.filter = "none";
    }
  }
}

function updateSilenceCountdownDisplay() {
  if (!silenceModeState) return;
  if (silenceModeState.active) {
    const remSec = Math.max(0, Math.round(silenceModeState.end_epoch - Date.now() / 1000));
    silenceModeState.remaining_seconds = remSec;
    updateSilenceButtonState();

    if (remSec <= 0 && lastSilenceRemaining > 0) {
      silenceModeState.active = false;
      updateSilenceButtonState();
      showToast("Compte à rebours Silence ! terminé : valeurs initiales restaurées.");
      if (typeof refreshLatestOnly === "function") refreshLatestOnly();
    }
    lastSilenceRemaining = remSec;
  } else {
    lastSilenceRemaining = 0;
  }
}

function openSilenceModal() {
  const modal = document.getElementById("silence-modal");
  if (!modal) return;
  setSilenceDurationPreset(2);
  modal.classList.remove("hidden");
}

function closeSilenceModal() {
  const modal = document.getElementById("silence-modal");
  if (modal) modal.classList.add("hidden");
}

function setSilenceDurationPreset(hours) {
  selectedSilenceHours = Number(hours);
  const inp = document.getElementById("silence-hours-input");
  if (inp) inp.value = hours;

  document.querySelectorAll(".sil-preset-btn").forEach((btn, idx) => {
    const btnHours = [1, 2, 3, 4][idx];
    if (btnHours === hours) {
      btn.className = "sil-preset-btn py-2 px-2 text-xs font-bold rounded-lg border border-purple-500 bg-purple-900/50 text-purple-200 shadow hover:border-purple-400 transition";
    } else {
      btn.className = "sil-preset-btn py-2 px-2 text-xs font-bold rounded-lg border border-gray-800 bg-gray-900 text-gray-300 hover:border-purple-600 transition";
    }
  });

  const confirmBtn = document.getElementById("silence-modal-confirm-btn");
  if (confirmBtn) {
    confirmBtn.innerHTML = `<span>Activer Silence ! (${hours}H)</span>`;
  }
}

function handleSilenceDurationInput() {
  const inp = document.getElementById("silence-hours-input");
  if (!inp) return;
  let val = parseFloat(inp.value);
  if (isNaN(val) || val <= 0) val = 1;
  selectedSilenceHours = val;

  document.querySelectorAll(".sil-preset-btn").forEach((btn, idx) => {
    const btnHours = [1, 2, 3, 4][idx];
    if (btnHours === val) {
      btn.className = "sil-preset-btn py-2 px-2 text-xs font-bold rounded-lg border border-purple-500 bg-purple-900/50 text-purple-200 shadow hover:border-purple-400 transition";
    } else {
      btn.className = "sil-preset-btn py-2 px-2 text-xs font-bold rounded-lg border border-gray-800 bg-gray-900 text-gray-300 hover:border-purple-600 transition";
    }
  });

  const confirmBtn = document.getElementById("silence-modal-confirm-btn");
  if (confirmBtn) {
    confirmBtn.innerHTML = `<span>Activer Silence ! (${val}H)</span>`;
  }
}

async function confirmActivateSilence() {
  closeSilenceModal();
  const hours = selectedSilenceHours || 2.0;
  try {
    showToast(`Activation du mode Silence ! pour ${hours}H en cours...`);
    const res = await fetch("/api/silence/activate", {
      method: "POST",
      headers: getInstallerHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ duration_hours: hours })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      showToast("Erreur activation Silence ! : " + (err.detail || res.statusText), true);
      return;
    }
    const data = await res.json();
    if (data.silence_mode) {
      silenceModeState = data.silence_mode;
    } else {
      silenceModeState = {
        active: true,
        duration_hours: hours,
        end_epoch: Date.now() / 1000 + hours * 3600,
        remaining_seconds: hours * 3600
      };
    }
    updateSilenceButtonState();
    if (typeof refreshLatestOnly === "function") await refreshLatestOnly();
    if (typeof loadAudit === "function") await loadAudit();
    showToast(`Mode Silence ! actif (${hours}H) : P1, ventilation=0, auto/eco/boost coupés.`);
  } catch (e) {
    showToast("Erreur réseau activation Silence !: " + e.message, true);
  }
}

async function deactivateSilenceMode(overrideCommands = null) {
  try {
    showToast(overrideCommands ? "Désactivation du mode Silence ! et application de la consigne..." : "Restauration des valeurs initiales en cours...");
    const payload = overrideCommands ? { override_commands: overrideCommands } : {};
    const res = await fetch("/api/silence/deactivate", {
      method: "POST",
      headers: getInstallerHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload)
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      showToast("Erreur désactivation Silence ! : " + (err.detail || res.statusText), true);
      return;
    }
    const data = await res.json();
    silenceModeState = data.silence_mode || { active: false, remaining_seconds: 0, end_epoch: 0 };
    updateSilenceButtonState();
    if (typeof refreshLatestOnly === "function") await refreshLatestOnly();
    if (typeof loadAudit === "function") await loadAudit();
    showToast(overrideCommands ? "Mode Silence ! désactivé : nouvelle consigne appliquée." : "Mode Silence ! désactivé : valeurs d'origine restaurées.");
  } catch (e) {
    showToast("Erreur réseau désactivation Silence !: " + e.message, true);
  }
}

function handleSilenceButtonClick() {
  if (silenceModeState && silenceModeState.active) {
    const remMins = Math.ceil((silenceModeState.remaining_seconds || 0) / 60);
    openActionModal({
      title: "🤫 Mode Silence ! en cours",
      message: `Le mode Silence ! est actuellement actif (il reste environ ${remMins} min).\n\nSouhaitez-vous l'arrêter maintenant et restaurer immédiatement les réglages initiaux (Puissance, Ventilation, modes Auto/ECO/BOOST) ?`,
      confirmText: "Arrêter et Restaurer",
      onConfirm: () => deactivateSilenceMode()
    });
  } else {
    openSilenceModal();
  }
}

function toggleSilenceMode() {
  handleSilenceButtonClick();
}


function cycleBoostMode() {
  const cur = Number(getRegVal("fun_pwf_set", latestData.fun_pwf_set ?? 0));
  const next = (cur + 1) % 3;
  sendCommand("fun_pwf_set", next);
}

function toggleChronoWeek() {
  const cur = Number(getRegVal("chrono_week_enable_set", latestData.chrono_week_enable ?? 0));
  sendCommand("chrono_week_enable_set", cur ? 0 : 1);
}

function confirmPower(turnOn) {
  openActionModal({
    title: turnOn ? "🔥 Allumage du Poêle" : "🛑 Extinction du Poêle",
    message: turnOn
      ? "Confirmer l'ALLUMAGE du poêle Nobis A10 C Light Style Coax (Code 85 sur offset 232) ?"
      : "Confirmer l'EXTINCTION du poêle (Code 170 sur offset 232) ?",
    confirmText: turnOn ? "Allumer (85)" : "Éteindre (170)",
    onConfirm: () => sendCommand("status_code", turnOn ? 85 : 170)
  });
}

// Pictogrammes SVG vectoriels pour le bouton central (ON, OFF, RESET)
const FLOWER_CENTER_PICTOGRAMS = {
  ON: `<svg class="w-5 h-5 sm:w-6 sm:h-6 drop-shadow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path d="M12 2v10" />
    <path d="M18.4 6.6a9 9 0 1 1-12.77.04" />
  </svg>`,
  OFF: `<svg class="w-5 h-5 sm:w-6 sm:h-6 drop-shadow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <circle cx="12" cy="12" r="9" stroke-opacity="0.9" />
    <rect x="9" y="9" width="6" height="6" rx="1" fill="currentColor" stroke="none" />
  </svg>`,
  RESET: `<svg class="w-5 h-5 sm:w-6 sm:h-6 drop-shadow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path d="M3 12a9 9 0 1 0 3-6.7L3 8" />
    <path d="M3 3v5h5" />
    <path d="M12 8v4" />
    <circle cx="12" cy="15.5" r="1" fill="currentColor" stroke="none" />
  </svg>`
};

let defaultFlowerFooterState = { label: "", code: 0 };
let hoveredFlowerPetal = 0;
let flowerPreviewResetTimer = null;

// Colorise dynamiquement le bandeau sous la couronne selon la phase active ou survolée
function applyFlowerBannerTheme(phaseCode) {
  const banner = document.getElementById("flower-status-banner");
  const meta = getPhaseMeta(phaseCode);
  if (!banner || !meta) return;

  const isOff = (Number(phaseCode) === 0);
  const bgAlphaStart = isOff ? 0.22 : 0.28;
  const bgAlphaEnd = isOff ? 0.10 : 0.12;
  const borderAlpha = isOff ? 0.55 : 0.75;

  banner.style.background = `linear-gradient(135deg, rgba(${meta.rgb}, ${bgAlphaStart}) 0%, rgba(15, 23, 42, 0.92) 65%, rgba(${meta.rgb}, ${bgAlphaEnd}) 100%)`;
  banner.style.borderColor = `rgba(${meta.rgb}, ${borderAlpha})`;
  banner.style.borderLeftColor = meta.hex;
  banner.style.boxShadow = isOff
    ? "inset 0 1px 2px rgba(0,0,0,0.4)"
    : `0 0 16px rgba(${meta.rgb}, 0.22), inset 0 1px 2px rgba(255,255,255,0.06)`;
}

function previewFlowerPetal(phaseCode, isExplicitAction = false) {
  if (flowerPreviewResetTimer) {
    clearTimeout(flowerPreviewResetTimer);
    flowerPreviewResetTimer = null;
  }
  hoveredFlowerPetal = Number(phaseCode) || 0;
  const meta = getPhaseMeta(phaseCode);
  const stateEl = document.getElementById("hdr-state");
  if (stateEl && meta) {
    stateEl.textContent = meta.label;
  }
  applyFlowerBannerTheme(phaseCode);
  const st = Number(latestData.status_code ?? 0);
  const alarm = Number(latestData.alarm_code ?? 0);
  const blackout = Number(latestData.blackout_popup ?? 0);
  const isAlarm = (st === 10 || st === 11 || alarm > 0 || blackout > 0);
  renderLifecycleFlower(st, isAlarm);

  const isTouchDevice = ('ontouchstart' in window) || (navigator.maxTouchPoints > 0) || (window.matchMedia && window.matchMedia('(pointer: coarse)').matches);

  // Sur smartphone / tactile ou sur clic explicite : désélection automatique après 3 secondes
  if (isExplicitAction || isTouchDevice) {
    flowerPreviewResetTimer = setTimeout(() => {
      restoreFlowerPetalPreview();
    }, 3000);
  } else {
    // Survol souris desktop : filet de sécurité
    flowerPreviewResetTimer = setTimeout(() => {
      const anyHovered = document.querySelector("#lifecycle-flower-petals .flower-petal-group:hover");
      if (!anyHovered) {
        restoreFlowerPetalPreview();
      }
    }, 2500);
  }
}

function scheduleFlowerPetalRestore(delayMs = 900) {
  if (flowerPreviewResetTimer) {
    clearTimeout(flowerPreviewResetTimer);
  }
  flowerPreviewResetTimer = setTimeout(() => {
    restoreFlowerPetalPreview();
  }, delayMs);
}

function restoreFlowerPetalPreview() {
  if (flowerPreviewResetTimer) {
    clearTimeout(flowerPreviewResetTimer);
    flowerPreviewResetTimer = null;
  }
  hoveredFlowerPetal = 0;
  if (document.activeElement && typeof document.activeElement.blur === "function") {
    document.activeElement.blur();
  }
  const stateEl = document.getElementById("hdr-state");
  if (stateEl) {
    stateEl.textContent = latestData.status_label || "--";
  }
  const st = Number(latestData.status_code ?? 0);
  const alarm = Number(latestData.alarm_code ?? 0);
  const blackout = Number(latestData.blackout_popup ?? 0);
  const isAlarm = (st === 10 || st === 11 || alarm > 0 || blackout > 0);
  const bannerThemeCode = isAlarm ? (st === 11 ? 11 : 10) : st;
  applyFlowerBannerTheme(defaultFlowerFooterState.code ?? bannerThemeCode);
  renderLifecycleFlower(st, isAlarm);
}

function polarToXY(r, angleDeg) {
  const rad = (angleDeg * Math.PI) / 180;
  return [
    Number((r * Math.sin(rad)).toFixed(2)),
    Number((-r * Math.cos(rad)).toFixed(2))
  ];
}

function buildChevronPath(theta1, theta2, rIn, rMid, rOut, tipDeltaDeg) {
  const [x1, y1] = polarToXY(rIn, theta1);
  const [x2, y2] = polarToXY(rMid, theta1 + tipDeltaDeg);
  const [x3, y3] = polarToXY(rOut, theta1);
  const [x4, y4] = polarToXY(rOut, theta2);
  const [x5, y5] = polarToXY(rMid, theta2 + tipDeltaDeg);
  const [x6, y6] = polarToXY(rIn, theta2);
  return `M ${x1} ${y1} L ${x2} ${y2} L ${x3} ${y3} A ${rOut} ${rOut} 0 0 1 ${x4} ${y4} L ${x5} ${y5} L ${x6} ${y6} A ${rIn} ${rIn} 0 0 0 ${x1} ${y1} Z`;
}

// Génère et met à jour la couronne des 11 chevrons (#1 à #11) sans détruire les nœuds DOM au survol
function renderLifecycleFlower(st, isAlarm) {
  const petalsGroup = document.getElementById("lifecycle-flower-petals");
  if (!petalsGroup) return;

  const liveActiveCode = isAlarm ? (st === 11 ? 11 : 10) : (st >= 1 && st <= 11 ? st : 0);
  const totalPetals = 11;
  const stepDeg = 360 / totalPetals;

  // Création initiale unique des 11 groupes SVG si pas encore présents
  if (petalsGroup.children.length !== totalPetals) {
    let initHtml = "";
    for (let p = 1; p <= totalPetals; p++) {
      const meta = getPhaseMeta(p);
      initHtml += `
        <g class="flower-petal-group"
           data-petal="${p}"
           style="--petal-glow: ${meta.hex};"
           onmouseenter="previewFlowerPetal(${p})"
           onmouseleave="scheduleFlowerPetalRestore(900)"
           ontouchend="previewFlowerPetal(${p}, true)"
           onclick="previewFlowerPetal(${p}, true)">
          <title>${escapeHtml(meta.label)} : ${escapeHtml(meta.desc)}</title>
          <path class="flower-petal-path" stroke-linejoin="round" />
          <text text-anchor="middle" dominant-baseline="central" class="pointer-events-none select-none">${meta.icon}</text>
        </g>
      `;
    }
    petalsGroup.innerHTML = initHtml;
  }

  for (let p = 1; p <= totalPetals; p++) {
    const meta = getPhaseMeta(p);
    const angleDeg = (p - 1) * stepDeg;
    const isLiveActive = (p === liveActiveCode);
    const isHovered = (p === hoveredFlowerPetal);
    const isHighlighted = isLiveActive || isHovered;

    const fillStyle = isHighlighted
      ? `rgba(${meta.rgb}, 0.92)`
      : `rgba(${meta.rgb}, 0.18)`;
    const strokeStyle = isHighlighted
      ? "#ffffff"
      : `rgba(${meta.rgb}, 0.58)`;
    const strokeW = isHighlighted ? "2.2" : "1.3";

    const halfSpan = (stepDeg - 2.6) / 2;
    const t1 = angleDeg - halfSpan;
    const t2 = angleDeg + halfSpan;
    const rIn = isHighlighted ? 37 : 38;
    const rOut = isHighlighted ? 97 : 94;
    const rMid = (rIn + rOut) / 2;
    const chevronD = buildChevronPath(t1, t2, rIn, rMid, rOut, 4.2);
    const [cx, cy] = polarToXY(rMid, angleDeg + 2.2);

    const gEl = petalsGroup.children[p - 1];
    if (!gEl) continue;
    gEl.setAttribute("class", `flower-petal-group ${isLiveActive ? "flower-petal-active" : ""}`);

    const pathEl = gEl.querySelector("path");
    if (pathEl) {
      pathEl.setAttribute("d", chevronD);
      pathEl.setAttribute("fill", fillStyle);
      pathEl.setAttribute("stroke", strokeStyle);
      pathEl.setAttribute("stroke-width", strokeW);
    }

    const textEl = gEl.querySelector("text");
    if (textEl) {
      textEl.setAttribute("x", cx);
      textEl.setAttribute("y", cy);
      textEl.setAttribute("font-size", isHighlighted ? "15" : "13");
      textEl.setAttribute("opacity", isHighlighted ? "1" : "0.88");
    }
  }
}

// Met à jour dynamiquement la fleur (11 pétales) et le bouton central (ON, OFF ou RESET)
function updateDynamicPowerButton() {
  const btn = document.getElementById("ctl-dynamic-power-btn");
  const iconEl = document.getElementById("ctl-dynamic-power-icon");
  const actionTagEl = document.getElementById("ctl-dynamic-power-action-tag");
  const stateEl = document.getElementById("ctl-dynamic-power-state");

  if (!btn) return;

  const st = Number(latestData.status_code ?? 0);
  const alarm = Number(latestData.alarm_code ?? 0);
  const blackout = Number(latestData.blackout_popup ?? 0);
  const isAlarm = (st === 10 || st === 11 || alarm > 0 || blackout > 0);
  const stateLabel = latestData.status_label || (st === 0 ? "OFF (Éteint)" : `État #${st}`);

  if (stateEl) stateEl.textContent = stateLabel;

  renderLifecycleFlower(st, isAlarm);
  updateModeRing();

  const baseBtnClasses = "absolute w-[54px] h-[54px] sm:w-[62px] sm:h-[62px] rounded-full font-bold text-xs shadow-2xl flex flex-col items-center justify-center gap-0.5 transition-all duration-200 cursor-pointer hover:scale-105 active:scale-95 text-white border-2 z-10 ";

  if (isAlarm) {
    if (iconEl) iconEl.innerHTML = FLOWER_CENTER_PICTOGRAMS.RESET;
    if (actionTagEl) actionTagEl.textContent = "RESET";
    btn.className = baseBtnClasses + "bg-gradient-to-br from-rose-700 via-rose-600 to-red-600 hover:from-rose-600 hover:to-red-500 border-rose-300/80 shadow-[0_0_20px_rgba(244,63,94,0.65)] animate-pulse";
    btn.title = "Notice Nobis : Alarme de sécurité active. Cliquez au centre pour afficher la procédure de réarmement constructeur (télécommande P3 ou contacteur physique).";
  } else if (st === 0) {
    // Poêle Éteint -> Action Allumage (ON = 85)
    if (iconEl) iconEl.innerHTML = FLOWER_CENTER_PICTOGRAMS.ON;
    if (actionTagEl) actionTagEl.textContent = "ON";
    btn.className = baseBtnClasses + "bg-gradient-to-br from-emerald-600 to-emerald-500 hover:from-emerald-500 hover:to-emerald-400 border-emerald-300/70 shadow-[0_0_18px_rgba(16,185,129,0.45)]";
    btn.title = "Notice Nobis : Poêle à l'arrêt (#0 OFF). Cliquez au centre pour lancer la séquence d'allumage (Code 85 sur offset 232).";
  } else if (st >= 1 && st <= 4) {
    // Phases de démarrage/allumage (1=Check, 2=Préchauffage, 3=Pellet, 4=Flamme) -> Action OFF (170)
    if (iconEl) iconEl.innerHTML = FLOWER_CENTER_PICTOGRAMS.OFF;
    if (actionTagEl) actionTagEl.textContent = "OFF";
    btn.className = baseBtnClasses + "bg-gradient-to-br from-amber-600 to-rose-600 hover:from-amber-500 hover:to-rose-500 border-amber-300/70 shadow-[0_0_18px_rgba(245,158,11,0.5)]";
    btn.title = `Notice Nobis : Poêle en phase d'allumage (${stateLabel}). Cliquez au centre pour annuler et éteindre (Code 170 sur offset 232).`;
  } else if (st === 7) {
    // Extinction / Refroidissement -> Action OFF (170)
    if (iconEl) iconEl.innerHTML = FLOWER_CENTER_PICTOGRAMS.OFF;
    if (actionTagEl) actionTagEl.textContent = "OFF";
    btn.className = baseBtnClasses + "bg-gradient-to-br from-sky-700 to-slate-800 hover:from-sky-600 hover:to-slate-700 border-sky-300/60 shadow-[0_0_16px_rgba(14,165,233,0.45)]";
    btn.title = "Notice Nobis : Le poêle refroidit son foyer et évacue les fumées résiduelles. Cliquez pour forcer l'ordre OFF (170).";
  } else if (st === 8 || st === 9) {
    // ECO STOP / STAND BY -> Action OFF (170)
    if (iconEl) iconEl.innerHTML = FLOWER_CENTER_PICTOGRAMS.OFF;
    if (actionTagEl) actionTagEl.textContent = "OFF";
    btn.className = baseBtnClasses + "bg-gradient-to-br from-cyan-600 to-indigo-700 hover:from-cyan-500 hover:to-indigo-600 border-cyan-300/70 shadow-[0_0_18px_rgba(6,182,212,0.45)]";
    btn.title = `Notice Nobis : Poêle en veille thermique (${stateLabel}). Cliquez au centre pour éteindre complètement (Code 170 sur offset 232).`;
  } else if (st === -1) {
    // Déconnecté
    if (iconEl) iconEl.innerHTML = FLOWER_CENTER_PICTOGRAMS.OFF;
    if (actionTagEl) actionTagEl.textContent = "OFF";
    btn.className = baseBtnClasses + "bg-gray-800/95 hover:bg-gray-700 border-gray-600 text-gray-300";
    btn.title = "Communication indisponible avec le poêle.";
  } else {
    // En fonctionnement (TRAVAIL 5, NETTOYAGE BRASIER 6, etc.) -> Action OFF (170)
    if (iconEl) iconEl.innerHTML = FLOWER_CENTER_PICTOGRAMS.OFF;
    if (actionTagEl) actionTagEl.textContent = "OFF";
    btn.className = baseBtnClasses + "bg-gradient-to-br from-red-600 to-rose-600 hover:from-red-500 hover:to-rose-500 border-red-300/70 shadow-[0_0_18px_rgba(239,68,68,0.5)]";
    btn.title = `Notice Nobis : Poêle en fonctionnement (${stateLabel}). Cliquez au centre pour éteindre le poêle (Code 170 sur offset 232).`;
  }

  const bannerThemeCode = isAlarm ? (st === 11 ? 11 : 10) : st;
  applyFlowerBannerTheme(hoveredFlowerPetal || bannerThemeCode);

  defaultFlowerFooterState = {
    label: stateLabel,
    code: bannerThemeCode
  };
}

// Actionneur contextuel unique : déclenche l'action appropriée (Allumage, Extinction ou Reset)
function handleDynamicPowerAction() {
  const st = Number(latestData.status_code ?? 0);
  const alarm = Number(latestData.alarm_code ?? 0);
  const blackout = Number(latestData.blackout_popup ?? 0);
  const isAlarm = (st === 10 || st === 11 || alarm > 0 || blackout > 0);

  if (isAlarm) {
    resetStoveAlarm();
    return;
  }

  if (st === 0) {
    openActionModal({
      title: "🔥 Allumage du Poêle",
      message: "Confirmer l'ALLUMAGE du poêle Nobis A10 C Light Style Coax (Code 85 sur offset 232) ?",
      confirmText: "Allumer (85)",
      onConfirm: () => sendCommand("status_code", 85)
    });
    return;
  }

  if (st >= 1 && st <= 4) {
    const label = latestData.status_label || "Allumage en cours";
    openActionModal({
      title: "🛑 Arrêt de la séquence d'allumage",
      message: `Le poêle est actuellement en phase de démarrage (${label}).\nConfirmer l'interruption et l'extinction (Code 170 sur offset 232) ?`,
      confirmText: "Éteindre (170)",
      onConfirm: () => sendCommand("status_code", 170)
    });
    return;
  }

  if (st === 7) {
    openActionModal({
      title: "⏳ Poêle en cours d'extinction",
      message: "Le poêle effectue déjà son cycle de refroidissement et d'évacuation (ARRÊT).\nSouhaitez-vous forcer un nouvel ordre d'extinction (Code 170 sur offset 232) ?",
      confirmText: "Forcer extinction (170)",
      onConfirm: () => sendCommand("status_code", 170)
    });
    return;
  }

  if (st === 8 || st === 9) {
    const label = latestData.status_label || "Veille Stand-by";
    openActionModal({
      title: "🛑 Sortie de veille et extinction",
      message: `Le poêle est actuellement en mode veille (${label}).\nConfirmer l'extinction complète (Code 170 sur offset 232) ?`,
      confirmText: "Éteindre (170)",
      onConfirm: () => sendCommand("status_code", 170)
    });
    return;
  }

  if (st === -1) {
    openActionModal({
      title: "⚠️ Poêle Hors Ligne",
      message: "La communication avec le poêle est indisponible. Tenter d'envoyer l'ordre d'extinction (Code 170) ?",
      confirmText: "Envoyer OFF (170)",
      onConfirm: () => sendCommand("status_code", 170)
    });
    return;
  }

  // Par défaut : état de chauffe (TRAVAIL, NETTOYAGE BRASIER...)
  confirmPower(false);
}

async function sendRawOffset() {
  const offsetVal = document.getElementById("raw-offset").value;
  const valStr = document.getElementById("raw-value").value;
  if (offsetVal === "" || valStr === "") {
    showToast("Veuillez renseigner un Offset et une Valeur.", true);
    return;
  }
  const offset = Number(offsetVal);
  const value = Number(valStr);
  const res = await fetch("/api/command", {
    method: "POST",
    headers: getInstallerHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ reg_key: `RAW_${offset}`, offset, value })
  });
  if (res.ok) {
    showToast(`Offset brut #${offset} = ${value} écrit !`);
  } else {
    const err = await res.json().catch(() => ({}));
    showToast("Erreur offset brut : " + (err.detail || res.statusText), true);
  }
  await refreshLatestOnly();
  loadAudit();
}

async function loadAudit() {
  const res = await fetch("/api/audit");
  if (!res.ok) return;
  const rows = await res.json();
  const tbody = document.getElementById("audit-table-body");
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td class="py-2 text-gray-400">${escapeHtml(r.timestamp)}</td>
      <td><strong class="text-white">${escapeHtml(r.label)}</strong> <span class="text-gray-500">(#${Number(r.offset)})</span></td>
      <td class="font-mono text-amber-400">${Number(r.old_value)}</td>
      <td class="font-mono text-emerald-400">${Number(r.new_value)}</td>
      <td class="uppercase text-cyan-400 font-mono">${escapeHtml(r.comm_mode)}</td>
      <td><span class="px-2 py-0.5 rounded ${r.status === 'SUCCESS' ? 'bg-emerald-950 text-emerald-300' : 'bg-rose-950 text-rose-300'}">${escapeHtml(r.status)}</span></td>
      <td>
        <button onclick="rollbackAudit(${Number(r.id)}, ${Number(r.old_value)})" class="px-2.5 py-1 rounded bg-amber-700 hover:bg-amber-600 text-white text-xs">
          ↩ Rollback (${Number(r.old_value)})
        </button>
      </td>
    </tr>
  `).join("");
}

function rollbackAudit(id, oldVal) {
  openActionModal({
    title: "↩ Rollback Registre",
    message: `Restaurer immédiatement l'ancienne valeur (${oldVal}) dans le registre ?`,
    confirmText: "Restaurer",
    onConfirm: async () => {
      await fetch(`/api/rollback/${id}`, { method: "POST" });
      await refreshLatestOnly();
      await loadAudit();
      showToast("Rollback effectué !");
    }
  });
}

function setRange(r) {
  currentRange = r;
  document.querySelectorAll("#timeline-btns button").forEach(b => {
    const active = b.getAttribute("data-range") === r;
    b.className = active
      ? "px-2.5 sm:px-3 py-1 text-xs rounded bg-orange-600 text-white font-semibold"
      : "px-2.5 sm:px-3 py-1 text-xs rounded text-gray-300";
  });
  loadDashboard();
}

function exportCSV() {
  window.location.href = `/api/export-csv?range_key=${currentRange}`;
}

function exportLogsCSV() {
  window.location.href = `/api/export-logs-csv?severity=${currentLogSeverity}`;
}

async function pollNow() {
  const btn = document.getElementById("btn-poll-now");
  if (btn) btn.textContent = "⏳ Lecture...";
  updateConnectionIndicator("connecting", "Actualisation...");
  try {
    await fetch("/api/poll-now", { method: "POST" });
    await refreshLatestOnly();
    showToast("Données du poêle actualisées en direct !");
  } catch (e) {
    updateConnectionIndicator("disconnected", "Échec actualisation");
  } finally {
    if (btn) btn.textContent = "⟳ Actualiser";
  }
}

async function saveSettings() {
  const mode = document.getElementById("cfg-mode").value;
  const payload = {
    comm_mode: mode,
    poll_interval_s: document.getElementById("cfg-interval").value,
    weather_city: document.getElementById("cfg-city").value,
    weather_lat: document.getElementById("cfg-lat").value,
    weather_lon: document.getElementById("cfg-lon").value,
    pellet_bag_price_eur: document.getElementById("cfg-bag-price") ? document.getElementById("cfg-bag-price").value : "5.80",
    pellet_bags_stock: document.getElementById("cfg-bags-stock") ? document.getElementById("cfg-bags-stock").value : "40",
    webhook_url: document.getElementById("cfg-webhook") ? document.getElementById("cfg-webhook").value : "",
    matter_enabled: document.getElementById("cfg-matter-enabled") ? document.getElementById("cfg-matter-enabled").value : "1",
    matter_device_name: document.getElementById("cfg-matter-name") ? document.getElementById("cfg-matter-name").value : "Poêle Nobis A10",
    matter_port: document.getElementById("cfg-matter-port") ? document.getElementById("cfg-matter-port").value : "5540"
  };
  if (mode === "agua_cloud") {
    payload.agua_email = document.getElementById("cfg-email").value;
    payload.agua_password = document.getElementById("cfg-pass").value;
  } else if (mode === "local_gateway") {
    payload.local_gateway_url = document.getElementById("cfg-gw").value;
  }
  const res = await fetch("/api/settings", {
    method: "POST",
    headers: getInstallerHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload)
  });
  const j = await res.json();
  closeConfigModal();
  await loadDashboard();
  showToast("Configuration enregistrée ! Base active : " + (j.active_db || ""));
}

async function testConnection() {
  const diag = document.getElementById("cfg-diag");
  diag.classList.remove("hidden");
  diag.textContent = "Test de communication en cours...";
  const mode = document.getElementById("cfg-mode").value;
  const payload = { comm_mode: mode };
  if (mode === "agua_cloud") {
    payload.agua_email = document.getElementById("cfg-email").value;
    payload.agua_password = document.getElementById("cfg-pass").value;
  } else if (mode === "local_gateway") {
    payload.local_gateway_url = document.getElementById("cfg-gw").value;
  }
  const res = await fetch("/api/test-connection", {
    method: "POST",
    headers: getInstallerHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload)
  });
  const j = await res.json();
  diag.textContent = JSON.stringify(j, null, 2);
}

async function testWebhook() {
  const webhookInp = document.getElementById("cfg-webhook");
  const url = webhookInp ? webhookInp.value.trim() : "";
  if (!url) {
    showToast("Renseignez une URL Webhook / ntfy.sh avant de tester.", true);
    return;
  }
  // Sauvegarde préalable de l'URL webhook
  await fetch("/api/settings", {
    method: "POST",
    headers: getInstallerHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ webhook_url: url })
  });
  const res = await fetch("/api/test-webhook", {
    method: "POST",
    headers: getInstallerHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ webhook_url: url })
  });
  const j = await res.json().catch(() => ({}));
  if (res.ok && (j.status === "ok" || j.ok)) {
    showToast(j.message || "Notification de test envoyée avec succès !");
  } else {
    showToast(j.detail || "Échec de l'envoi Webhook (vérifiez l'URL).", true);
  }
}

async function reportClientError(source, message, severity = "ERROR") {
  try {
    await fetch("/api/logs/client-error", {
      method: "POST",
      headers: getInstallerHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ severity, source, message })
    });
    const logsTab = document.getElementById("tab-logs");
    if (logsTab && !logsTab.classList.contains("hidden")) {
      loadLogs(currentLogSeverity);
    }
  } catch (e) { }
}

window.addEventListener("error", (e) => {
  reportClientError("JS_ERROR", `${e.message} (${e.filename}:${e.lineno})`);
});
window.addEventListener("unhandledrejection", (e) => {
  reportClientError("PROMISE", e.reason?.message || String(e.reason));
});

function scrollToMobileCard(index) {
  const container = document.getElementById("tab-control-cards");
  if (!container) return;
  const cards = container.querySelectorAll(":scope > .card");
  if (cards && cards[index]) {
    cards[index].scrollIntoView({ behavior: "smooth", block: "start" });
    updateMobileCardIndicator(index);
  }
}

function updateMobileCardIndicator(index) {
  [0, 1, 2].forEach(i => {
    const btn = document.getElementById("mob-dot-" + i);
    if (btn) {
      if (i === index) {
        btn.className = "px-2.5 py-0.5 rounded-full bg-orange-600 text-white font-bold transition-all shadow-sm";
      } else {
        btn.className = "px-2.5 py-0.5 rounded-full text-gray-400 hover:text-white transition-all";
      }
    }
  });
}

function initMobileCardObserver() {
  const container = document.getElementById("tab-control-cards");
  if (!container) return;
  const cards = container.querySelectorAll(":scope > .card");
  if (!cards.length) return;

  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting && entry.intersectionRatio >= 0.5) {
        const idx = Array.from(cards).indexOf(entry.target);
        if (idx !== -1) {
          updateMobileCardIndicator(idx);
        }
      }
    });
  }, {
    root: container,
    threshold: 0.5
  });

  cards.forEach(c => observer.observe(c));
}

async function loadLogs(sev) {
  currentLogSeverity = sev || "ALL";

  // Highlight active filter buttons
  ["all", "info", "command", "warning", "error"].forEach(s => {
    const btn = document.getElementById("log-btn-" + s);
    if (btn) {
      if (s.toUpperCase() === currentLogSeverity.toUpperCase()) {
        btn.classList.add("active");
        btn.style.outline = "2px solid #f97316";
      } else {
        btn.classList.remove("active");
        btn.style.outline = "none";
      }
    }
  });

  const res = await fetch(`/api/logs?severity=${currentLogSeverity}`);
  if (!res.ok) return;
  const logs = await res.json();
  const box = document.getElementById("logs-container");
  if (!box) return;

  if (!logs || logs.length === 0) {
    box.innerHTML = `<div class="py-12 text-center text-gray-500">
      <span class="text-3xl block mb-2">📜</span>
      Aucun événement enregistré avec la sévérité <b>${escapeHtml(currentLogSeverity)}</b>.
    </div>`;
    return;
  }

  box.innerHTML = logs.map(l => {
    const s = (l.severity || "INFO").toUpperCase();
    let sevBadge = "text-sky-400";
    let bgBadge = "bg-sky-950/70 border-sky-800/80";
    if (s === "ERROR") {
      sevBadge = "text-rose-400 font-black";
      bgBadge = "bg-rose-950/80 border-rose-700/80";
    } else if (s === "WARNING" || s === "WARN") {
      sevBadge = "text-amber-400 font-bold";
      bgBadge = "bg-amber-950/80 border-amber-700/80";
    } else if (s === "COMMAND") {
      sevBadge = "text-emerald-400 font-bold";
      bgBadge = "bg-emerald-950/80 border-emerald-700/80";
    }

    return `
      <div class="flex items-start gap-2.5 border-b border-gray-900/80 py-1.5 hover:bg-gray-900/40 px-1 rounded transition text-xs font-mono">
        <span class="text-gray-500 tabular-nums shrink-0 text-[11px] mt-0.5">${escapeHtml(l.timestamp)}</span>
        <span class="px-1.5 py-0.5 rounded text-[10px] border ${bgBadge} ${sevBadge} shrink-0 w-20 text-center">[${escapeHtml(s)}]</span>
        <span class="text-gray-400 shrink-0 w-28 text-[11px] font-semibold truncate" title="${escapeHtml(l.source)}">${escapeHtml(l.source)}</span>
        <span class="text-gray-200 flex-1 break-words leading-relaxed">${escapeHtml(l.message)}</span>
      </div>
    `;
  }).join("");
}

// =============================================================================
// GESTION DU PONT THERMOSTAT MATTER (GOOGLE HOME / NEST HUB MAX)
// =============================================================================
let lastMatterPairingCodeRaw = "";

async function openMatterModal() {
  const modal = document.getElementById("matter-modal");
  if (modal) modal.classList.remove("hidden");
  await refreshMatterStatus(false);
}

function closeMatterModal() {
  const modal = document.getElementById("matter-modal");
  if (modal) modal.classList.add("hidden");
}

async function refreshMatterStatus(showFeedback = false) {
  try {
    const res = await fetch("/api/matter/status");
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    renderMatterStatus(data);
    if (showFeedback) {
      showToast("État du pont Matter actualisé !");
    }
  } catch (err) {
    const infoEl = document.getElementById("matter-fabrics-info");
    if (infoEl) {
      infoEl.textContent = "Erreur de lecture du pont Matter : " + (err.message || err);
    }
  }
}

function renderMatterStatus(data) {
  const dot = document.getElementById("hdr-matter-dot");
  const badge = document.getElementById("matter-status-badge");
  const qrBox = document.getElementById("matter-qr-box");
  const codeEl = document.getElementById("matter-manual-code");
  const infoEl = document.getElementById("matter-fabrics-info");
  const modeEl = document.getElementById("matter-live-mode");
  const ambEl = document.getElementById("matter-live-amb");
  const spEl = document.getElementById("matter-live-sp");

  const isRunning = Boolean(data && data.running);
  const isCommissioned = Boolean(data && data.commissioned);
  const fabricsCount = Number(data?.fabricsCount || 0);

  if (dot) {
    if (!isRunning) {
      dot.className = "w-2 h-2 rounded-full bg-rose-400";
    } else if (isCommissioned) {
      dot.className = "w-2 h-2 rounded-full bg-emerald-400 shadow-[0_0_8px_#34d399]";
    } else {
      dot.className = "w-2 h-2 rounded-full bg-cyan-400 animate-pulse";
    }
  }

  if (badge) {
    if (!isRunning) {
      badge.textContent = "Hors ligne";
      badge.className = "text-[10px] px-2 py-0.5 rounded-full bg-rose-950 text-rose-300 border border-rose-700 font-semibold";
    } else if (isCommissioned) {
      badge.textContent = `Appairé (${fabricsCount} hub${fabricsCount > 1 ? "s" : ""})`;
      badge.className = "text-[10px] px-2 py-0.5 rounded-full bg-emerald-950 text-emerald-300 border border-emerald-700 font-semibold";
    } else {
      badge.textContent = `Prêt à appairer (UDP ${data.matterPort || 5540})`;
      badge.className = "text-[10px] px-2 py-0.5 rounded-full bg-cyan-950 text-cyan-300 border border-cyan-700 font-semibold";
    }
  }

  const ls = data?.liveState || {};
  if (modeEl) {
    const isHeat = ls.systemMode === "Heat";
    modeEl.textContent = isHeat ? "🔥 Chauffage (ON)" : "⚫ Arrêt (OFF)";
    modeEl.className = "text-sm font-bold mt-0.5 " + (isHeat ? "text-emerald-400" : "text-gray-400");
  }
  if (ambEl) {
    ambEl.textContent = ls.localTemperatureC !== undefined ? `${Number(ls.localTemperatureC).toFixed(1)} °C` : "-- °C";
  }
  if (spEl) {
    spEl.textContent = ls.setpointC !== undefined ? `${Number(ls.setpointC).toFixed(0)} °C` : "-- °C";
  }

  lastMatterPairingCodeRaw = String(data?.manualPairingCode || "");
  if (codeEl) {
    codeEl.textContent = data?.manualPairingCodeFormatted || lastMatterPairingCodeRaw || "---- --- ----";
  }

  if (qrBox) {
    if (data?.qrSvg) {
      qrBox.innerHTML = data.qrSvg;
      const svgEl = qrBox.querySelector("svg");
      if (svgEl) {
        svgEl.style.width = "100%";
        svgEl.style.height = "100%";
      }
    } else if (isCommissioned) {
      qrBox.innerHTML = `<div class="text-center p-2 space-y-1"><div class="text-3xl">✅</div><div class="text-xs font-bold text-slate-800">Appareil appairé</div><div class="text-[10px] text-slate-600">Actif sur votre Nest Hub Max</div></div>`;
    } else {
      qrBox.innerHTML = `<span class="text-xs text-rose-600 font-semibold text-center px-2">${escapeHtml(data?.error || "Pont Matter indisponible")}</span>`;
    }
  }

  if (infoEl) {
    if (!isRunning) {
      infoEl.innerHTML = `⚠️ <strong>Pont Matter arrêté :</strong> ${escapeHtml(data?.error || "Vérifiez le service Node.js.")}`;
    } else if (isCommissioned) {
      const fabNames = (data.fabrics || []).map(f => escapeHtml(f.label || `Fabric #${f.fabricIndex}`)).join(", ");
      infoEl.innerHTML = `✅ <strong>Connecté en local avec votre écosystème Matter :</strong> ${fabNames || "Google Home / Nest Hub Max"}. Les commandes vocales et l'écran Thermostat sont actifs.`;
    } else {
      infoEl.innerHTML = `📡 <strong>En attente d'appairage sur le réseau local (mDNS / UDP ${data.matterPort || 5540}) :</strong> Scannez le QR Code ci-dessus dans l'application <strong>Google Home</strong> pour relier le poêle à votre <strong>Nest Hub Max</strong>.`;
    }
  }
}

async function copyMatterPairingCode() {
  if (!lastMatterPairingCodeRaw) {
    showToast("Aucun code d'appairage disponible.", true);
    return;
  }
  try {
    await navigator.clipboard.writeText(lastMatterPairingCodeRaw);
    showToast("Code Matter copié : " + lastMatterPairingCodeRaw);
  } catch {
    showToast("Code Matter : " + lastMatterPairingCodeRaw);
  }
}

function confirmResetMatterPairing() {
  openActionModal({
    title: "Réinitialiser l'appairage Matter ?",
    message: "Cette action dissociera le poêle de Google Home / Nest Hub Max et générera un nouveau QR Code d'appairage vierge.",
    confirmText: "Réinitialiser",
    onConfirm: async () => {
      try {
        showToast("Réinitialisation du pont Matter en cours...");
        const res = await fetch("/api/matter/factory-reset", { method: "POST" });
        const data = await res.json();
        renderMatterStatus(data);
        showToast("Nouveau QR Code Matter généré !");
      } catch (err) {
        showToast("Erreur lors de la réinitialisation Matter", true);
      }
    }
  });
}

window.addEventListener("DOMContentLoaded", () => {
  initThreePaneCockpit();
  switchTab("tab-control"); // Pilotage affiché par défaut à gauche
  updateDynamicPowerButton();
  loadDashboard();
  initSSEStream();
  refreshMatterStatus(false);
  initMobileCardObserver();
  // Filet de sécurité léger toutes les 60s si l'onglet navigateur a été mis en veille
  setInterval(refreshLatestOnly, 60000);
  // Rafraîchissement précis 1 Hz du compte à rebours Mode Silence ! dans le bouton
  setInterval(updateSilenceCountdownDisplay, 1000);
});

