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
  inputType = "text",
  defaultValue = "",
  confirmText = "Confirmer",
  cancelText = "Annuler",
  showCancel = true,
  onConfirm,
  onCancel
}) {
  const titleEl = document.getElementById("action-modal-title");
  if (titleEl) {
    titleEl.innerHTML = title || "Confirmation";
  }
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
    inp.type = inputType || "text";
    inp.value = defaultValue;
  } else {
    inpWrap.classList.add("hidden");
    inp.type = "text";
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

const showActionModal = openActionModal;

// Mode de communication actif (Cloud / Passerelle / Simulation)
let currentCommMode = "agua_cloud";
let isSettingsFormDirty = false;

function getActiveCommMode() {
  return (typeof connectionState !== "undefined" && connectionState && connectionState.commStatus && connectionState.commStatus.comm_mode) ||
    (typeof latestData !== "undefined" && latestData && latestData.comm_mode) ||
    (typeof currentSettings !== "undefined" && currentSettings && currentSettings.comm_mode) ||
    currentCommMode ||
    "agua_cloud";
}

// =============================================================================
// GESTION DU RE-DESIGN DE L'ONGLET PARAMÈTRES, TARAGES & OBSERVABILITÉ
// =============================================================================
let currentInstallerSubTab = "settings";

function switchInstallerSubTab(subTabId) {
  currentInstallerSubTab = subTabId || "settings";
  const validTabs = ["settings", "tuning", "expert", "logs"];
  validTabs.forEach(t => {
    const pane = document.getElementById("subtab-pane-" + t);
    const btn = document.getElementById("subtab-btn-" + t);
    const isTarget = (t === currentInstallerSubTab);
    if (pane) {
      pane.classList.toggle("hidden", !isTarget);
    }
    if (btn) {
      if (isTarget) {
        btn.classList.add("subtab-active", "bg-orange-600", "text-white", "shadow-md");
        btn.classList.remove("text-gray-400", "hover:bg-gray-900");
      } else {
        btn.classList.remove("subtab-active", "bg-orange-600", "text-white", "shadow-md");
        btn.classList.add("text-gray-400", "hover:bg-gray-900");
      }
    }
  });

  if (currentInstallerSubTab === "expert") {
    checkInstallerAuthStatus();
    renderEepromTable();
    loadSnapshots();
    renderHardwareMasks();
  } else if (currentInstallerSubTab === "logs") {
    loadLogs(currentLogSeverity);
    loadAudit();
  } else if (currentInstallerSubTab === "tuning") {
    const trimPelletInp = document.getElementById("inst-trim-pellet");
    if (trimPelletInp && document.activeElement !== trimPelletInp) {
      trimPelletInp.value = getRegVal("trim_pellet_pct", 0);
    }
    const trimExhaustInp = document.getElementById("inst-trim-exhaust");
    if (trimExhaustInp && document.activeElement !== trimExhaustInp) {
      trimExhaustInp.value = getRegVal("trim_exhaust_pct", 0);
    }
  } else if (currentInstallerSubTab === "settings") {
    updateConfigModalFields();
  }
}

function switchLogView(viewName) {
  const sysView = document.getElementById("logs-view-system");
  const auditView = document.getElementById("logs-view-audit");
  const sysBtn = document.getElementById("logview-btn-system");
  const auditBtn = document.getElementById("logview-btn-audit");

  if (viewName === "audit") {
    if (sysView) sysView.classList.add("hidden");
    if (auditView) auditView.classList.remove("hidden");
    if (sysBtn) {
      sysBtn.className = "px-3.5 py-1.5 rounded-lg font-semibold transition text-gray-400 hover:text-white hover:bg-gray-900 cursor-pointer flex items-center gap-1.5";
    }
    if (auditBtn) {
      auditBtn.className = "px-3.5 py-1.5 rounded-lg font-bold transition bg-orange-600 text-white shadow-sm cursor-pointer flex items-center gap-1.5";
    }
    loadAudit();
  } else {
    if (sysView) sysView.classList.remove("hidden");
    if (auditView) auditView.classList.add("hidden");
    if (sysBtn) {
      sysBtn.className = "px-3.5 py-1.5 rounded-lg font-bold transition bg-orange-600 text-white shadow-sm cursor-pointer flex items-center gap-1.5";
    }
    if (auditBtn) {
      auditBtn.className = "px-3.5 py-1.5 rounded-lg font-semibold transition text-gray-400 hover:text-white hover:bg-gray-900 cursor-pointer flex items-center gap-1.5";
    }
    loadLogs(currentLogSeverity);
  }
}

// Gestion de la Configuration avec champs dynamiques selon le protocole choisi
function openConfigModal(highlightFieldId = null) {
  switchTab("tab-installer");
  switchInstallerSubTab("settings");
  updateConfigModalFields();
  const modal = document.getElementById("config-modal");
  if (modal) modal.classList.add("hidden");

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
    }, 150);
  }
}

function closeConfigModal() {
  const modal = document.getElementById("config-modal");
  if (modal) modal.classList.add("hidden");
}

function updateConfigModalFields() {
  const modeEl = document.getElementById("cfg-mode");
  if (!modeEl) return;
  const mode = modeEl.value;
  const aguaFields = document.getElementById("cfg-fields-agua");
  const gwFields = document.getElementById("cfg-fields-gateway");
  const simFields = document.getElementById("cfg-fields-sim");
  if (aguaFields) aguaFields.classList.toggle("hidden", mode !== "agua_cloud");
  if (gwFields) gwFields.classList.toggle("hidden", mode !== "local_gateway");
  if (simFields) simFields.classList.toggle("hidden", mode !== "simulation");
}

function toggleMobileSeriesDrawer() {
  const drawer = document.getElementById("series-filter-drawer");
  const icon = document.getElementById("mob-series-drawer-icon");
  const isHidden = drawer.classList.contains("hidden");
  drawer.classList.toggle("hidden", !isHidden);
  icon.textContent = isHidden ? "▲" : "▼";
}

function switchTab(tabId) {
  if (tabId === "tab-logs") {
    tabId = "tab-installer";
  }

  // Verrouillage du scroll vertical sur l'écran Pilotage sur smartphone (seul le swipe gauche/droite est actif)
  if (tabId === "tab-control" && window.innerWidth < 768) {
    document.documentElement.classList.add("overflow-y-hidden");
    document.body.classList.add("overflow-y-hidden");
  } else {
    document.documentElement.classList.remove("overflow-y-hidden");
    document.body.classList.remove("overflow-y-hidden");
  }

  // Remet l'ascenseur tout en haut de page lors du changement d'onglet
  window.scrollTo({ top: 0, left: 0, behavior: "instant" });
  document.documentElement.scrollTop = 0;
  document.body.scrollTop = 0;
  const mainEl = document.querySelector("main");
  if (mainEl) mainEl.scrollTop = 0;

  ["tab-control", "tab-pellets", "tab-chrono", "tab-dashboard", "tab-installer"].forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      const isTarget = (id === tabId);
      el.classList.toggle("hidden", !isTarget);
      el.style.display = isTarget ? "" : "none";
    }
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
    switchInstallerSubTab(currentInstallerSubTab || "settings");
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

let currentOpenProgramModal = null;
let programDraft = null; // { p, initial: {...}, current: {...} }

function openProgramModal(p) {
  const pNum = Number(p);
  currentOpenProgramModal = pNum;

  const initial = {
    startSlot: Number(getRegVal(`chrono_p${pNum}_start_set`, latestData[`chrono_p${pNum}_start`] ?? 144)),
    stopSlot: Number(getRegVal(`chrono_p${pNum}_stop_set`, latestData[`chrono_p${pNum}_stop`] ?? 144)),
    mask: Number(getRegVal(`chrono_p${pNum}_days_mask`, latestData[`chrono_p${pNum}_days`] ?? 0)),
    power: Number(getRegVal(`chrono_p${pNum}_power_set`, latestData[`chrono_p${pNum}_power`] ?? 7)),
    temperature: Number(getRegVal(`chrono_p${pNum}_temperature_set`, latestData[`chrono_p${pNum}_temp`] ?? 20)),
    fanFront: Number(getRegVal(`chrono_p${pNum}_fan_front_set`, 0)),
    tCanL: Number(getRegVal(`chrono_p${pNum}_tcanl_set`, 20)),
    vCanL: Number(getRegVal(`chrono_p${pNum}_canl_set`, 0)),
    tCanR: Number(getRegVal(`chrono_p${pNum}_tcanr_set`, 20)),
    vCanR: Number(getRegVal(`chrono_p${pNum}_canr_set`, 0))
  };

  programDraft = {
    p: pNum,
    initial: { ...initial },
    current: { ...initial }
  };

  renderProgramModalContent(currentOpenProgramModal);
  const modal = document.getElementById("program-modal");
  if (modal) modal.classList.remove("hidden");
}

function forceCloseProgramModal() {
  currentOpenProgramModal = null;
  programDraft = null;
  const modal = document.getElementById("program-modal");
  if (modal) modal.classList.add("hidden");
}

function closeProgramModal() {
  if (!programDraft || programDraft.p !== currentOpenProgramModal) {
    forceCloseProgramModal();
    return;
  }

  const p = programDraft.p;
  const changes = getProgramChangesSummary(p, programDraft.initial, programDraft.current);

  if (changes.length === 0) {
    forceCloseProgramModal();
    return;
  }

  showProgramConfirmationModal(p, changes);
}

function timeToSlot(timeStr) {
  if (!timeStr) return 144;
  const parts = timeStr.split(":");
  if (parts.length < 2) return 144;
  const h = parseInt(parts[0], 10);
  const m = parseInt(parts[1], 10);
  if (isNaN(h) || isNaN(m)) return 144;
  // Tranches de 10 min (0..143)
  return Math.min(143, Math.max(0, h * 6 + Math.round(m / 10)));
}

function updateProgramDraftTime(type, timeStr) {
  if (!programDraft) return;
  const slot = timeToSlot(timeStr);
  if (type === "start") {
    programDraft.current.startSlot = slot;
    if (programDraft.current.stopSlot >= 144 || programDraft.current.stopSlot === slot) {
      const defStop = (slot + 12) % 144;
      let savedStop = Number(localStorage.getItem(`nobis_p${programDraft.p}_last_stop`));
      if (isNaN(savedStop) || savedStop >= 144 || savedStop === slot) savedStop = defStop;
      programDraft.current.stopSlot = savedStop;
    }
  } else if (type === "stop") {
    programDraft.current.stopSlot = slot;
    if (programDraft.current.startSlot >= 144 || programDraft.current.startSlot === slot) {
      const defStart = (slot - 12 + 144) % 144;
      let savedStart = Number(localStorage.getItem(`nobis_p${programDraft.p}_last_start`));
      if (isNaN(savedStart) || savedStart >= 144 || savedStart === slot) savedStart = defStart;
      programDraft.current.startSlot = savedStart;
    }
  }

  if (programDraft.current.mask === 0) {
    programDraft.current.mask = 127;
  }

  renderProgramModalContent(programDraft.p);
}

function updateProgramDraft(field, value) {
  if (!programDraft) return;
  programDraft.current[field] = Number(value);
  renderProgramModalContent(programDraft.p);
}

function getProgramChangesSummary(p, initial, current) {
  const changes = [];
  const dayNames = [
    { bit: 1, name: "Lun" }, { bit: 2, name: "Mar" }, { bit: 4, name: "Mer" },
    { bit: 8, name: "Jeu" }, { bit: 16, name: "Ven" }, { bit: 32, name: "Sam" },
    { bit: 64, name: "Dim" }
  ];

  const formatDays = (mask) => {
    if (mask === 127) return "Tous les jours";
    if (mask === 0) return "Aucun jour";
    return dayNames.filter(d => (mask & d.bit) !== 0).map(d => d.name).join(", ");
  };

  const formatFan = (v) => {
    const n = Number(v);
    return n === 8 ? "AUTO" : (n === 0 ? "OFF" : String(n));
  };

  // 1. Plage horaire
  if (initial.startSlot !== current.startSlot || initial.stopSlot !== current.stopSlot) {
    const oldTime = (initial.startSlot < 144 && initial.stopSlot < 144 && initial.startSlot !== initial.stopSlot)
      ? `${slotToTime(initial.startSlot)} - ${slotToTime(initial.stopSlot)}`
      : "Inactif";
    const newTime = (current.startSlot < 144 && current.stopSlot < 144 && current.startSlot !== current.stopSlot)
      ? `${slotToTime(current.startSlot)} - ${slotToTime(current.stopSlot)}`
      : "Inactif";

    changes.push({
      icon: "⏰",
      label: "Plage horaire",
      oldText: oldTime,
      newText: newTime,
      commands: [
        { reg_key: `chrono_p${p}_start_set`, value: current.startSlot },
        { reg_key: `chrono_p${p}_stop_set`, value: current.stopSlot }
      ]
    });
  }

  // 2. Puissance
  if (initial.power !== current.power) {
    changes.push({
      icon: "🔥",
      label: "Puissance",
      oldText: `${initial.power}`,
      newText: `${current.power}`,
      commands: [
        { reg_key: `chrono_p${p}_power_set`, value: current.power }
      ]
    });
  }

  // 3. Température Salon
  if (initial.temperature !== current.temperature) {
    changes.push({
      icon: "🌡️",
      label: "Température",
      oldText: `${initial.temperature}°C`,
      newText: `${current.temperature}°C`,
      commands: [
        { reg_key: `chrono_p${p}_temperature_set`, value: current.temperature }
      ]
    });
  }

  // 4. Ventilation Façade
  if (initial.fanFront !== current.fanFront) {
    changes.push({
      icon: "💨",
      label: "Ventilation façade",
      oldText: formatFan(initial.fanFront),
      newText: formatFan(current.fanFront),
      commands: [
        { reg_key: `chrono_p${p}_fan_front_set`, value: current.fanFront }
      ]
    });
  }

  // 5. Canalisation Gauche
  if (initial.tCanL !== current.tCanL) {
    changes.push({
      icon: "⬅️🌡️",
      label: "T° Consigne Gauche",
      oldText: `${initial.tCanL}°C`,
      newText: `${current.tCanL}°C`,
      commands: [
        { reg_key: `chrono_p${p}_tcanl_set`, value: current.tCanL }
      ]
    });
  }
  if (initial.vCanL !== current.vCanL) {
    changes.push({
      icon: "⬅️💨",
      label: "Soufflerie Gauche",
      oldText: formatFan(initial.vCanL),
      newText: formatFan(current.vCanL),
      commands: [
        { reg_key: `chrono_p${p}_canl_set`, value: current.vCanL }
      ]
    });
  }

  // 6. Canalisation Droite
  if (initial.tCanR !== current.tCanR) {
    changes.push({
      icon: "➡️🌡️",
      label: "T° Consigne Droite",
      oldText: `${initial.tCanR}°C`,
      newText: `${current.tCanR}°C`,
      commands: [
        { reg_key: `chrono_p${p}_tcanr_set`, value: current.tCanR }
      ]
    });
  }
  if (initial.vCanR !== current.vCanR) {
    changes.push({
      icon: "➡️💨",
      label: "Soufflerie Droite",
      oldText: formatFan(initial.vCanR),
      newText: formatFan(current.vCanR),
      commands: [
        { reg_key: `chrono_p${p}_canr_set`, value: current.vCanR }
      ]
    });
  }

  // 7. Jours d'activation
  if (initial.mask !== current.mask) {
    changes.push({
      icon: "📅",
      label: "Jours d'activation",
      oldText: formatDays(initial.mask),
      newText: formatDays(current.mask),
      commands: [
        { reg_key: `chrono_p${p}_days_mask`, value: current.mask }
      ]
    });
  }

  return changes;
}

function showProgramConfirmationModal(p, changes) {
  const rowsHtml = changes.map(c => `
    <div class="flex items-center justify-between py-2 px-3 rounded-lg bg-gray-900 border border-gray-800 text-xs">
      <div class="flex items-center gap-2 text-gray-200 font-medium">
        <span class="text-sm">${c.icon}</span>
        <span>${c.label}</span>
      </div>
      <div class="flex items-center gap-1.5 font-mono text-xs">
        <span class="text-gray-400 line-through">${c.oldText}</span>
        <span class="text-orange-400 font-bold">➔</span>
        <span class="text-emerald-400 font-bold">${c.newText}</span>
      </div>
    </div>
  `).join("");

  const htmlContent = `
    <div class="space-y-3">
      <p class="text-xs text-gray-300 leading-relaxed">
        Vous avez modifié la configuration du <strong>Programme ${p}</strong>.
        Souhaitez-vous transmettre ces <strong>${changes.length} modification${changes.length > 1 ? "s" : ""}</strong> au poêle ?
      </p>
      <div class="space-y-1.5 max-h-60 overflow-y-auto pr-0.5">
        ${rowsHtml}
      </div>
    </div>
  `;

  openActionModal({
    title: `⚙️ Confirmation — Programme ${p}`,
    htmlContent,
    confirmText: "Appliquer au poêle",
    cancelText: "Abandonner",
    showCancel: true,
    onConfirm: async () => {
      await applyProgramChanges(p, changes);
    },
    onCancel: () => {
      forceCloseProgramModal();
      showToast("Modifications abandonnées");
    }
  });
}

async function applyProgramChanges(p, changes) {
  const toSend = [];
  changes.forEach(c => {
    c.commands.forEach(cmd => {
      toSend.push({ reg_key: cmd.reg_key, value: Number(cmd.value) });
    });
  });

  if (toSend.length === 0) return;

  if (programDraft && programDraft.current) {
    if (programDraft.current.startSlot < 144) localStorage.setItem(`nobis_p${p}_last_start`, programDraft.current.startSlot);
    if (programDraft.current.stopSlot < 144) localStorage.setItem(`nobis_p${p}_last_stop`, programDraft.current.stopSlot);
    if (programDraft.current.mask > 0) localStorage.setItem(`nobis_p${p}_last_mask`, programDraft.current.mask);
  }

  forceCloseProgramModal();

  if (silenceModeState && silenceModeState.active && toSend.some(c => SILENCE_OVERRIDABLE_KEYS[c.reg_key])) {
    await deactivateSilenceMode(toSend);
    return;
  }

  showToast(`Envoi de ${toSend.length} réglage(s) au poêle...`);

  try {
    if (toSend.length === 1) {
      await sendCommand(toSend[0].reg_key, toSend[0].value);
    } else {
      const res = await fetch("/api/command-batch", {
        method: "POST",
        headers: getInstallerHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ commands: toSend })
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showToast("Erreur batch : " + (err.detail || res.statusText), true);
        reportClientError("PROGRAMME", `Échec batch programme P${p} : ${err.detail || res.statusText}`);
      } else {
        showToast(`Programme ${p} mis à jour avec succès (${toSend.length} réglages) !`);
      }
      await refreshLatestOnly();
    }
  } catch (err) {
    showToast("Erreur réseau : " + err.message, true);
    reportClientError("RESEAU", `Erreur réseau maj programme P${p} : ${err.message}`);
  }
}

// Rendu du contenu complet d'un programme dans la Popup modale
function renderProgramModalContent(p) {
  const modalContainer = document.getElementById("program-modal-container");
  if (!modalContainer) return;

  const cur = (programDraft && programDraft.p === p) ? programDraft.current : null;

  const startSlot = cur ? cur.startSlot : Number(getRegVal(`chrono_p${p}_start_set`, latestData[`chrono_p${p}_start`] ?? 144));
  const stopSlot = cur ? cur.stopSlot : Number(getRegVal(`chrono_p${p}_stop_set`, latestData[`chrono_p${p}_stop`] ?? 144));
  const mask = cur ? cur.mask : Number(getRegVal(`chrono_p${p}_days_mask`, latestData[`chrono_p${p}_days`] ?? 0));
  const pwr = cur ? cur.power : Number(getRegVal(`chrono_p${p}_power_set`, latestData[`chrono_p${p}_power`] ?? 7));
  const temp = cur ? cur.temperature : Number(getRegVal(`chrono_p${p}_temperature_set`, latestData[`chrono_p${p}_temp`] ?? 20));

  const fanFront = cur ? cur.fanFront : Number(getRegVal(`chrono_p${p}_fan_front_set`, 0));
  const tCanL = cur ? cur.tCanL : Number(getRegVal(`chrono_p${p}_tcanl_set`, 20));
  const vCanL = cur ? cur.vCanL : Number(getRegVal(`chrono_p${p}_canl_set`, 0));
  const tCanR = cur ? cur.tCanR : Number(getRegVal(`chrono_p${p}_tcanr_set`, 20));
  const vCanR = cur ? cur.vCanR : Number(getRegVal(`chrono_p${p}_canr_set`, 0));

  const dayLabels = [
    { bit: 1, name: "Lun", full: "Lundi" },
    { bit: 2, name: "Mar", full: "Mardi" },
    { bit: 4, name: "Mer", full: "Mercredi" },
    { bit: 8, name: "Jeu", full: "Jeudi" },
    { bit: 16, name: "Ven", full: "Vendredi" },
    { bit: 32, name: "Sam", full: "Samedi" },
    { bit: 64, name: "Dim", full: "Dimanche" }
  ];

  const progTheme = {
    1: {
      border: "border-emerald-600/70",
      badge: "bg-emerald-500 text-gray-950",
      text: "text-emerald-400",
      dayActive: "bg-emerald-600 text-white font-bold border-emerald-500 shadow-sm"
    },
    2: {
      border: "border-orange-600/70",
      badge: "bg-orange-500 text-gray-950",
      text: "text-orange-400",
      dayActive: "bg-orange-600 text-white font-bold border-orange-500 shadow-sm"
    },
    3: {
      border: "border-cyan-600/70",
      badge: "bg-cyan-500 text-gray-950",
      text: "text-cyan-400",
      dayActive: "bg-cyan-600 text-white font-bold border-cyan-500 shadow-sm"
    },
    4: {
      border: "border-purple-600/70",
      badge: "bg-purple-500 text-white",
      text: "text-purple-400",
      dayActive: "bg-purple-600 text-white font-bold border-purple-500 shadow-sm"
    }
  }[p];

  const isConfigured = (startSlot < 144 && stopSlot < 144 && startSlot !== stopSlot);

  if (startSlot < 144) localStorage.setItem(`nobis_p${p}_last_start`, startSlot);
  if (stopSlot < 144) localStorage.setItem(`nobis_p${p}_last_stop`, stopSlot);
  if (mask > 0) localStorage.setItem(`nobis_p${p}_last_mask`, mask);

  const defStart = p === 1 ? 48 : (p === 2 ? 126 : 42);
  const defStop = p === 1 ? 63 : (p === 2 ? 138 : 54);
  let savedStart = Number(localStorage.getItem(`nobis_p${p}_last_start`));
  let savedStop = Number(localStorage.getItem(`nobis_p${p}_last_stop`));
  if (isNaN(savedStart) || savedStart >= 144) savedStart = defStart;
  if (isNaN(savedStop) || savedStop >= 144 || savedStop === savedStart) savedStop = defStop;

  const startTimeVal = startSlot < 144 ? slotToTime(startSlot) : slotToTime(savedStart);
  const stopTimeVal = stopSlot < 144 ? slotToTime(stopSlot) : slotToTime(savedStop);

  // Choix de la puissance : uniquement des chiffres (1..7)
  let powerOptions = "";
  for (let lvl = 1; lvl <= 7; lvl++) {
    powerOptions += `<option value="${lvl}" ${pwr === lvl ? 'selected' : ''}>${lvl}</option>`;
  }

  // Choix de la T° : uniquement la température, sans MAX ni Hors-gel
  let tempOptions = "";
  for (let t = 7; t <= 41; t++) {
    tempOptions += `<option value="${t}" ${temp === t ? 'selected' : ''}>${t}°C</option>`;
  }

  let tCanLOptions = "";
  let tCanROptions = "";
  for (let t = 7; t <= 41; t++) {
    tCanLOptions += `<option value="${t}" ${tCanL === t ? 'selected' : ''}>${t}°C</option>`;
    tCanROptions += `<option value="${t}" ${tCanR === t ? 'selected' : ''}>${t}°C</option>`;
  }

  // Choix de la ventilation : uniquement des chiffres ou OFF/AUTO
  const fanDuctOptions = (curVal) => [
    { val: 0, label: "OFF" },
    { val: 1, label: "1" },
    { val: 2, label: "2" },
    { val: 3, label: "3" },
    { val: 4, label: "4" },
    { val: 5, label: "5" },
    { val: 6, label: "6" },
    { val: 7, label: "7" },
    { val: 8, label: "AUTO" }
  ].map(o => `<option value="${o.val}" ${curVal === o.val ? 'selected' : ''}>${o.label}</option>`).join("");

  const fanFrontOptions = [
    { val: 0, label: "OFF" },
    { val: 1, label: "1" },
    { val: 2, label: "2" },
    { val: 3, label: "3" },
    { val: 4, label: "4" },
    { val: 5, label: "5" },
    { val: 6, label: "6" },
    { val: 7, label: "7" },
    { val: 8, label: "AUTO" }
  ].map(o => `<option value="${o.val}" ${fanFront === o.val ? 'selected' : ''}>${o.label}</option>`).join("");

  // Jours d'activation
  const dayBtns = dayLabels.map(d => {
    const isOn = (mask & d.bit) !== 0;
    const nextMask = isOn ? (mask & ~d.bit) : (mask | d.bit);
    return `<button type="button" onclick="updateProgramDraft('mask', ${nextMask})"
      class="min-h-[34px] py-1 px-1.5 rounded-lg text-xs font-bold transition-all border cursor-pointer ${isOn ? progTheme.dayActive : 'bg-gray-800 text-gray-400 hover:text-white hover:bg-gray-700 border-gray-700'}"
      title="${d.full}">
      ${d.name}
    </button>`;
  }).join("");

  modalContainer.className = `bg-gray-900 border ${progTheme.border} rounded-2xl max-w-lg w-full max-h-[90vh] flex flex-col shadow-2xl overflow-hidden my-auto`;

  modalContainer.innerHTML = `
    <!-- En-tête de la Popup : Titre "Configuration" sans sous-titre -->
    <div class="p-4 bg-gray-950/90 border-b border-gray-800 flex items-center justify-between">
      <div class="flex items-center gap-2.5">
        <span class="px-2.5 py-1 rounded-md text-xs font-black ${progTheme.badge} shadow-sm">P${p}</span>
        <h3 class="text-base font-bold text-white">Configuration</h3>
      </div>
      <button type="button" onclick="closeProgramModal()"
        class="w-8 h-8 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 hover:text-white font-bold flex items-center justify-center transition cursor-pointer"
        title="Fermer">
        ✕
      </button>
    </div>

    <!-- Contenu du Programme (Scrollable) -->
    <div class="p-4 sm:p-5 overflow-y-auto space-y-4 text-xs tabular-nums flex-1 pb-6">
      <!-- Bulle fusionnée : Plages Horaires & Consignes de Chauffe (sans titres) -->
      <div class="bg-gray-950/70 p-3.5 rounded-xl border border-gray-800 space-y-3.5">
        <!-- 1. Plage Horaire (horloge smartphone native) -->
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="text-[11px] text-gray-400 block mb-1">Début</label>
            <input type="time" step="600"
              style="color-scheme: dark"
              value="${startTimeVal}"
              onchange="updateProgramDraftTime('start', this.value)"
              class="w-full bg-gray-800 border ${isConfigured ? 'border-gray-700 text-white' : 'border-gray-800 text-gray-400'} rounded-lg px-2.5 py-1.5 font-mono text-xs focus:outline-none focus:border-orange-500 cursor-pointer">
          </div>
          <div>
            <label class="text-[11px] text-gray-400 block mb-1">Fin</label>
            <input type="time" step="600"
              style="color-scheme: dark"
              value="${stopTimeVal}"
              onchange="updateProgramDraftTime('stop', this.value)"
              class="w-full bg-gray-800 border ${isConfigured ? 'border-gray-700 text-white' : 'border-gray-800 text-gray-400'} rounded-lg px-2.5 py-1.5 font-mono text-xs focus:outline-none focus:border-orange-500 cursor-pointer">
          </div>
        </div>

        <!-- 2. Consignes de Chauffe (Flamme, T°, Ventilation) avec pictogrammes -->
        <div class="pt-3 border-t border-gray-800/80">
          <div class="grid grid-cols-3 gap-2.5">
            <div>
              <label class="text-base block mb-1 text-center" title="Puissance de chauffe (1 à 7)">🔥</label>
              <select onchange="updateProgramDraft('power', this.value)"
                class="w-full bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-white text-xs text-center focus:outline-none focus:border-orange-500 cursor-pointer">
                ${powerOptions}
              </select>
            </div>
            <div>
              <label class="text-base block mb-1 text-center" title="Température Salon (7 à 41°C)">🌡️</label>
              <select onchange="updateProgramDraft('temperature', this.value)"
                class="w-full bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-white text-xs text-center focus:outline-none focus:border-orange-500 cursor-pointer">
                ${tempOptions}
              </select>
            </div>
            <div>
              <label class="text-base block mb-1 text-center" title="Ventilation façade (OFF, 1..7, AUTO)">💨</label>
              <select onchange="updateProgramDraft('fanFront', this.value)"
                class="w-full bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-white text-xs text-center focus:outline-none focus:border-orange-500 cursor-pointer">
                ${fanFrontOptions}
              </select>
            </div>
          </div>
        </div>
      </div>

      <!-- Canalisations d'air chaud Gauche & Droite (sans SX/DX, sans Cons/Vit) -->
      <div class="bg-gray-950/70 p-3.5 rounded-xl border border-gray-800 space-y-2.5">
        <span class="font-bold text-gray-200 text-xs block">💨 Canalisations d'Air Chaud</span>
        <div class="grid grid-cols-2 gap-3">
          <!-- Canalisation Gauche -->
          <div class="bg-gray-900/90 p-2.5 rounded-lg border border-gray-800 space-y-2">
            <div class="text-[11px] text-cyan-400 font-semibold flex items-center justify-between">
              <span>⬅️ Gauche</span>
            </div>
            <div class="space-y-1.5">
              <div>
                <label class="text-[10px] text-gray-400 block mb-0.5">T° Consigne</label>
                <select onchange="updateProgramDraft('tCanL', this.value)"
                  class="w-full bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-white text-xs text-center focus:outline-none focus:border-cyan-500 cursor-pointer">
                  ${tCanLOptions}
                </select>
              </div>
              <div>
                <label class="text-[10px] text-gray-400 block mb-0.5">Vitesse Soufflerie</label>
                <select onchange="updateProgramDraft('vCanL', this.value)"
                  class="w-full bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-white text-xs text-center focus:outline-none focus:border-cyan-500 cursor-pointer">
                  ${fanDuctOptions(vCanL)}
                </select>
              </div>
            </div>
          </div>

          <!-- Canalisation Droite -->
          <div class="bg-gray-900/90 p-2.5 rounded-lg border border-gray-800 space-y-2">
            <div class="text-[11px] text-sky-400 font-semibold flex items-center justify-between">
              <span>➡️ Droite</span>
            </div>
            <div class="space-y-1.5">
              <div>
                <label class="text-[10px] text-gray-400 block mb-0.5">T° Consigne</label>
                <select onchange="updateProgramDraft('tCanR', this.value)"
                  class="w-full bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-white text-xs text-center focus:outline-none focus:border-sky-500 cursor-pointer">
                  ${tCanROptions}
                </select>
              </div>
              <div>
                <label class="text-[10px] text-gray-400 block mb-0.5">Vitesse Soufflerie</label>
                <select onchange="updateProgramDraft('vCanR', this.value)"
                  class="w-full bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-white text-xs text-center focus:outline-none focus:border-sky-500 cursor-pointer">
                  ${fanDuctOptions(vCanR)}
                </select>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Jours actifs de la semaine -->
      <div class="bg-gray-950/70 p-3.5 rounded-xl border border-gray-800 space-y-2">
        <span class="font-bold text-gray-200 text-xs block">📅 Jours d'Activation</span>
        <div class="grid grid-cols-7 gap-1.5 pt-1">
          ${dayBtns}
        </div>
      </div>

      <!-- Bouton Enregistrer / Valider pour sortir de la bulle -->
      <div class="pt-1">
        <button type="button" onclick="closeProgramModal()"
          class="w-full py-2.5 px-4 rounded-xl font-bold text-xs bg-orange-600 hover:bg-orange-500 text-white shadow-lg transition cursor-pointer flex items-center justify-center gap-2">
          <span>Enregistrer les modifications</span>
        </button>
      </div>
    </div>
  `;
}

// Rendu de la Frise Gantt 24h et des 4 boutons Programmes Chrono P1..P4
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

  // Badges colorés pour le libellé P1..P4 dans la frise
  const progTrackBadges = {
    1: "bg-emerald-950/80 border-emerald-500/70 text-emerald-300 hover:border-emerald-400",
    2: "bg-orange-950/80 border-orange-500/70 text-orange-300 hover:border-orange-400",
    3: "bg-cyan-950/80 border-cyan-500/70 text-cyan-300 hover:border-cyan-400",
    4: "bg-purple-950/80 border-purple-500/70 text-purple-300 hover:border-purple-400"
  };

  // Styles des 4 boutons de cartes programmes
  const progBorderColors = {
    1: "border-emerald-500/50 hover:border-emerald-400 bg-gradient-to-br from-emerald-950/60 to-gray-900/90 hover:from-emerald-900/60 hover:to-gray-800/90 shadow-emerald-950/30",
    2: "border-orange-500/50 hover:border-orange-400 bg-gradient-to-br from-orange-950/60 to-gray-900/90 hover:from-orange-900/60 hover:to-gray-800/90 shadow-orange-950/30",
    3: "border-cyan-500/50 hover:border-cyan-400 bg-gradient-to-br from-cyan-950/60 to-gray-900/90 hover:from-cyan-900/60 hover:to-gray-800/90 shadow-cyan-950/30",
    4: "border-purple-500/50 hover:border-purple-400 bg-gradient-to-br from-purple-950/60 to-gray-900/90 hover:from-purple-900/60 hover:to-gray-800/90 shadow-purple-950/30"
  };
  const progBadgeStyles = {
    1: "bg-emerald-500 text-gray-950 font-black",
    2: "bg-orange-500 text-gray-950 font-black",
    3: "bg-cyan-500 text-gray-950 font-black",
    4: "bg-purple-500 text-white font-black"
  };
  const progTextColors = {
    1: "text-emerald-300",
    2: "text-orange-300",
    3: "text-cyan-300",
    4: "text-purple-300"
  };
  const progPillColors = {
    1: "bg-emerald-950/80 text-emerald-300 border-emerald-700/60",
    2: "bg-orange-950/80 text-orange-300 border-orange-700/60",
    3: "bg-cyan-950/80 text-cyan-300 border-cyan-700/60",
    4: "bg-purple-950/80 text-purple-300 border-purple-700/60"
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

  // Rendu du sélecteur de jour dans l'en-tête de la frise (étendu dynamiquement, texte tronqué)
  const daySelector = document.getElementById("chrono-day-selector");
  if (daySelector) {
    daySelector.innerHTML = GANTT_DAYS.map(d => {
      const isSel = (d.bit === selectedDayObj.bit);
      return `<button type="button" onclick="setGanttDay(${d.bit})"
        class="min-h-[30px] sm:min-h-[32px] px-1 py-1 rounded-md text-xs font-semibold flex items-center justify-center transition-all cursor-pointer min-w-0 w-full overflow-hidden ${isSel
          ? 'bg-orange-600 text-white font-bold shadow-md shadow-orange-950/50 border border-orange-500'
          : 'bg-gray-900 text-gray-400 hover:text-white hover:bg-gray-800 border border-gray-800'
        }"
        title="${d.full}">
        <span class="truncate block w-full text-center">${d.full}</span>
      </button>`;
    }).join("");
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

  let buttonsHtml = "";
  let ganttHtml = "";

  for (let p = 1; p <= 4; p++) {
    const startSlot = Number(getRegVal(`chrono_p${p}_start_set`, latestData[`chrono_p${p}_start`] ?? 144));
    const stopSlot = Number(getRegVal(`chrono_p${p}_stop_set`, latestData[`chrono_p${p}_stop`] ?? 144));
    const mask = Number(getRegVal(`chrono_p${p}_days_mask`, latestData[`chrono_p${p}_days`] ?? 0));
    const pwr = Number(getRegVal(`chrono_p${p}_power_set`, latestData[`chrono_p${p}_power`] ?? 7));
    const temp = Number(getRegVal(`chrono_p${p}_temperature_set`, latestData[`chrono_p${p}_temp`] ?? 20));

    const fanFront = Number(getRegVal(`chrono_p${p}_fan_front_set`, 0));
    const tCanL = Number(getRegVal(`chrono_p${p}_tcanl_set`, latestData[`chrono_p${p}_tcanl`] ?? latestData[`chrono_p${p}_temp`] ?? 20));
    const vCanL = Number(getRegVal(`chrono_p${p}_canl_set`, latestData[`chrono_p${p}_canl`] ?? 0));
    const tCanR = Number(getRegVal(`chrono_p${p}_tcanr_set`, latestData[`chrono_p${p}_tcanr`] ?? latestData[`chrono_p${p}_temp`] ?? 20));
    const vCanR = Number(getRegVal(`chrono_p${p}_canr_set`, latestData[`chrono_p${p}_canr`] ?? 0));

    const isDayActive = (mask & selectedDayObj.bit) !== 0;
    const wasPrevDayActive = (mask & prevDayObj.bit) !== 0;

    // Calcul du segment Gantt filtré sur le jour sélectionné (sur 144 pas de 10 min) sans contenu textuel intérieur
    let barSegments = "";
    if (startSlot < 144 && stopSlot < 144 && startSlot !== stopSlot) {
      if (stopSlot > startSlot) {
        if (isDayActive) {
          const leftPct = ((startSlot / 144) * 100).toFixed(2);
          const widthPct = (((stopSlot - startSlot) / 144) * 100).toFixed(2);
          barSegments = `<div style="left:${leftPct}%;width:${widthPct}%" class="absolute inset-y-0.5 rounded border ${progColors[p]} shadow-sm cursor-pointer" title="P${p}: ${slotToTime(startSlot)}-${slotToTime(stopSlot)} (${temp}°C / P${pwr})" onclick="openProgramModal(${p})"></div>`;
        }
      } else {
        // Créneau chevauchant minuit (ex: 22h00 -> 06h00)
        // 1. Matinée (00h00 -> stopSlot), suite du démarrage de la veille
        if (wasPrevDayActive) {
          const wMorning = ((stopSlot / 144) * 100).toFixed(2);
          barSegments += `<div style="left:0%;width:${wMorning}%" class="absolute inset-y-0.5 rounded-r border ${progColors[p]} shadow-sm cursor-pointer" title="P${p}: 00h-${slotToTime(stopSlot)} (${temp}°C) - Suite veille (${prevDayObj.name})" onclick="openProgramModal(${p})"></div>`;
        }
        // 2. Soirée (startSlot -> 24h00), démarrage du créneau aujourd'hui
        if (isDayActive) {
          const lEvening = ((startSlot / 144) * 100).toFixed(2);
          const wEvening = (((144 - startSlot) / 144) * 100).toFixed(2);
          barSegments += `<div style="left:${lEvening}%;width:${wEvening}%" class="absolute inset-y-0.5 rounded-l border ${progColors[p]} shadow-sm cursor-pointer" title="P${p}: ${slotToTime(startSlot)}-24h (${temp}°C / P${pwr})" onclick="openProgramModal(${p})"></div>`;
        }
      }
    }

    // Libellé P1..P4 coloré dans la frise
    ganttHtml += `
      <div class="flex items-center gap-2">
        <button type="button" onclick="openProgramModal(${p})"
          class="w-8 text-center py-0.5 rounded text-[11px] font-extrabold border cursor-pointer hover:brightness-125 transition shadow-sm ${progTrackBadges[p]}"
          title="Ouvrir le Programme P${p}">P${p}</button>
        <div class="relative flex-1 h-5 bg-gray-900 rounded border border-gray-800 overflow-hidden">
          ${barSegments}
        </div>
      </div>
    `;

    // Calcul du résumé pour le bouton de carte programme compacte (grille 2x2 sans titre, avec canalisations)
    const isConfigured = (startSlot < 144 && stopSlot < 144 && startSlot !== stopSlot);
    const activeDays = dayLabels.filter(d => (mask & d.bit) !== 0).map(d => d.name);
    let activeDaysSummary = "Aucun jour";
    if (activeDays.length === 7) activeDaysSummary = "Tous les jours";
    else if (activeDays.length > 0) activeDaysSummary = activeDays.join(", ");

    const fanDisplay = fanFront === 8 ? "AUTO" : (fanFront === 0 ? "OFF" : String(fanFront));
    const fanLDisplay = vCanL === 8 ? "AUTO" : (vCanL === 0 ? "OFF" : String(vCanL));
    const fanRDisplay = vCanR === 8 ? "AUTO" : (vCanR === 0 ? "OFF" : String(vCanR));

    buttonsHtml += `
      <button type="button" onclick="openProgramModal(${p})"
        class="w-full text-left p-2.5 sm:p-3 rounded-xl border transition-all duration-200 cursor-pointer shadow-md hover:shadow-lg hover:scale-[1.01] flex flex-col justify-between gap-1.5 sm:gap-2 ${progBorderColors[p]} group">
        <div class="flex items-center justify-between gap-1">
          <span class="px-2 py-0.5 rounded text-[11px] sm:text-xs font-black ${progBadgeStyles[p]} shadow-sm">P${p}</span>
        </div>

        <div class="space-y-1 text-[11px] sm:text-xs">
          ${isConfigured ? `
            <div class="flex items-center gap-2 text-gray-200 font-medium truncate">
              <span class="flex items-center gap-1" title="Consigne T° Salon">🌡️ ${temp}°C</span>
              <span class="flex items-center gap-1 text-amber-300" title="Puissance de chauffe (1..7)">🔥 ${pwr}</span>
              <span class="flex items-center gap-1 text-emerald-300" title="Ventilation Façade">💨 ${fanDisplay}</span>
            </div>

            <div class="grid grid-cols-2 gap-1 text-[10px] sm:text-[11px] pt-0.5 border-t border-gray-800/60">
              <span class="text-cyan-400 truncate flex items-center gap-1" title="Canalisation Gauche : ${tCanL}°C, Vitesse ${fanLDisplay}">
                <span>⬅️</span>
                <span>${tCanL}°C</span>
                <span class="text-cyan-300 font-mono">💨${fanLDisplay}</span>
              </span>
              <span class="text-sky-400 truncate flex items-center gap-1" title="Canalisation Droite : ${tCanR}°C, Vitesse ${fanRDisplay}">
                <span>➡️</span>
                <span>${tCanR}°C</span>
                <span class="text-sky-300 font-mono">💨${fanRDisplay}</span>
              </span>
            </div>
          ` : `
            <div class="text-gray-500 italic py-0.5 flex items-center gap-1.5">
              <span>⏹️</span><span>Programme inactif</span>
            </div>
          `}

          <div class="text-[10px] sm:text-[11px] text-gray-400 truncate pt-0.5 flex items-center gap-1">
            <span>📅</span>
            <span class="truncate">${activeDaysSummary}</span>
          </div>
        </div>
      </button>
    `;
  }

  if (ganttTracks) ganttTracks.innerHTML = ganttHtml;
  container.innerHTML = buttonsHtml;

  // Rafraîchir la modale ouverte sans perturber la saisie si l'utilisateur est en train d'écrire
  if (currentOpenProgramModal !== null) {
    const activeEl = document.activeElement;
    const isEditing = activeEl && activeEl.closest && activeEl.closest("#program-modal") && (activeEl.tagName === "INPUT" || activeEl.tagName === "SELECT");
    if (!isEditing) {
      renderProgramModalContent(currentOpenProgramModal);
    }
  }
}

// Écouteur d'échappement pour fermer la popup
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && currentOpenProgramModal !== null) {
    closeProgramModal();
  }
});

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

// États de connexion dissociés :
// 1) Conteneur (Navigateur <-> Conteneur Nobis.Supervisor / Serveur Web & Flux SSE)
// 2) Cloud Nobis (Conteneur <-> Cloud Agua IOT / Poêle physique Micronova)
const connectionState = {
  isServerConnected: true,
  isSSEConnected: false,
  containerStatus: "connected",
  cloudStatus: "connected",
  commStatus: null,
  containerLatencyMs: null,
};

// Met à jour la couleur du rectangle global autour des 2 pictos (vert uniquement si les 2 pictos sont verts)
function updateStatusPillBorder() {
  const pillEl = document.getElementById("hdr-status-pill");
  if (!pillEl) return;
  const dockerVer = (typeof currentSettings !== "undefined" && currentSettings && currentSettings.docker_version) || "v2.1.2";
  const cState = connectionState.containerStatus;
  const mState = connectionState.cloudStatus;

  if (cState === "connected" && mState === "connected") {
    pillEl.className = "flex items-center gap-1.5 px-2.5 py-1 sm:py-1.5 rounded-xl border bg-emerald-950/60 border-emerald-500/70 shadow-[0_0_10px_rgba(16,185,129,0.25)] cursor-pointer transition-all duration-200 hover:brightness-110";
    pillEl.title = `Conteneur (${dockerVer}) & Liaison actifs • Cliquer pour ouvrir les réglages`;
  } else if (cState === "disconnected" || mState === "disconnected") {
    pillEl.className = "flex items-center gap-1.5 px-2.5 py-1 sm:py-1.5 rounded-xl border bg-rose-950/75 border-rose-500/80 shadow-[0_0_12px_rgba(244,63,94,0.35)] animate-pulse cursor-pointer transition-all duration-200 hover:brightness-110";
    pillEl.title = `Anomalie de connexion (${dockerVer}) • Cliquer pour ouvrir les réglages`;
  } else {
    pillEl.className = "flex items-center gap-1.5 px-2.5 py-1 sm:py-1.5 rounded-xl border bg-amber-950/70 border-amber-500/80 shadow-[0_0_12px_rgba(245,158,11,0.35)] cursor-pointer transition-all duration-200 hover:brightness-110";
    pillEl.title = `Mode dégradé / simulation ou synchronisation (${dockerVer}) • Cliquer pour ouvrir les réglages`;
  }
}

// Met à jour l'indicateur visuel de connexion au conteneur Docker Nobis.Supervisor (picto Docker)
function updateContainerConnectionIndicator(overrideStatus, message) {
  const verEl = document.getElementById("hdr-container-version");
  const cfgDiagContainer = document.getElementById("cfg-diag-container");

  const dockerVer = (typeof currentSettings !== "undefined" && currentSettings && currentSettings.docker_version) || "v2.1.2";
  const effectiveStatus = overrideStatus || (connectionState.isServerConnected ? "connected" : "disconnected");
  connectionState.containerStatus = effectiveStatus;

  // Cas 1 : Conteneur ou réseau injoignable -> Picto Docker Rouge
  if (effectiveStatus === "disconnected" || !connectionState.isServerConnected) {
    connectionState.containerStatus = "disconnected";
    if (verEl) {
      verEl.className = "flex items-center justify-center w-4 h-4 sm:w-[18px] sm:h-[18px] text-rose-400 animate-pulse";
      verEl.title = `Conteneur Nobis.Supervisor (${dockerVer}) injoignable`;
    }
    if (cfgDiagContainer) {
      cfgDiagContainer.className = "text-[11px] font-semibold text-rose-400 flex items-center gap-1.5";
      cfgDiagContainer.innerHTML = `<span class="w-2 h-2 rounded-full bg-rose-500 animate-ping"></span>${message || "Injoignable"}`;
    }
    updateStatusPillBorder();
    return;
  }

  // Cas 2 : Reconnexion / négociation en cours -> Picto Docker Orange
  if (effectiveStatus === "connecting") {
    if (verEl) {
      verEl.className = "flex items-center justify-center w-4 h-4 sm:w-[18px] sm:h-[18px] text-amber-400 animate-pulse";
      verEl.title = `Connexion au conteneur Nobis.Supervisor (${dockerVer}) en cours...`;
    }
    if (cfgDiagContainer) {
      cfgDiagContainer.className = "text-[11px] font-semibold text-sky-400 flex items-center gap-1.5";
      cfgDiagContainer.innerHTML = `<span class="w-2 h-2 rounded-full bg-sky-400 animate-pulse"></span>Connexion...`;
    }
    updateStatusPillBorder();
    return;
  }

  // Cas 3 : Conteneur connecté et opérationnel -> Picto Docker Vert
  const sseTxt = connectionState.isSSEConnected ? " • Flux SSE actif" : " • HTTP actif";
  const latTxt = connectionState.containerLatencyMs ? ` • Ping ${connectionState.containerLatencyMs}ms` : "";
  if (verEl) {
    verEl.className = "flex items-center justify-center w-4 h-4 sm:w-[18px] sm:h-[18px] text-emerald-400";
    verEl.title = `Conteneur Nobis.Supervisor (${dockerVer}) en ligne${sseTxt}${latTxt}`;
  }
  if (cfgDiagContainer) {
    cfgDiagContainer.className = "text-[11px] font-semibold text-emerald-400 flex items-center gap-1.5";
    cfgDiagContainer.innerHTML = `<span class="w-2 h-2 rounded-full bg-emerald-400 shadow-[0_0_6px_#34d399]"></span>En ligne (${connectionState.isSSEConnected ? 'SSE actif' : 'HTTP'})`;
  }
  updateStatusPillBorder();
}

const MODE_CONN_ICONS = {
  agua_cloud: `<svg viewBox="0 0 24 24" class="w-4 h-4 sm:w-[18px] sm:h-[18px]" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z" /></svg>`,
  local_gateway: `<svg viewBox="0 0 24 24" class="w-4 h-4 sm:w-[18px] sm:h-[18px]" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="6" width="12" height="12" rx="2" /><rect x="9" y="9" width="6" height="6" /><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4" /></svg>`,
  simulation: `<svg viewBox="0 0 24 24" class="w-4 h-4 sm:w-[18px] sm:h-[18px]" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 3h6M10 3v5.5L5.2 17.6A2 2 0 0 0 7 20.5h10a2 2 0 0 0 1.8-2.9L14 8.5V3" /><path d="M7.5 15h9" /></svg>`,
};

// Met à jour le pictogramme unique de mode (Cloud / ESP / Simulation) colorisé vert/orange/rouge selon la connexion
function updateCloudConnectionIndicator(overrideStatus, message) {
  const modeBtn = document.getElementById("hdr-mode-conn-btn");
  const modeIcon = document.getElementById("hdr-mode-conn-icon");
  const cfgDiagCloud = document.getElementById("cfg-diag-cloud");

  const st = Number(latestData.status_code ?? 0);
  const activeMode = (connectionState.commStatus && connectionState.commStatus.comm_mode) ||
    latestData.comm_mode ||
    (currentSettings && currentSettings.comm_mode) ||
    "agua_cloud";
  currentCommMode = activeMode;

  let modeName = "Cloud Nobis";
  if (activeMode === "local_gateway") {
    modeName = "ESP / Passerelle UART";
  } else if (activeMode === "simulation") {
    modeName = "Simulation";
  }

  if (modeIcon) {
    modeIcon.innerHTML = MODE_CONN_ICONS[activeMode] || MODE_CONN_ICONS.agua_cloud;
  }

  // Cas 1 : Si le conteneur lui-même est inaccessible -> Pictogramme Rouge
  if (!connectionState.isServerConnected) {
    connectionState.cloudStatus = "disconnected";
    if (modeBtn) {
      modeBtn.className = "flex items-center justify-center text-rose-400 animate-pulse hover:scale-110 transition-transform cursor-pointer";
      modeBtn.title = `Mode ${modeName} • Liaison indisponible (conteneur déconnecté)`;
    }
    if (cfgDiagCloud) {
      cfgDiagCloud.className = "text-[11px] font-semibold text-gray-400 flex items-center gap-1.5";
      cfgDiagCloud.innerHTML = `<span class="w-2 h-2 rounded-full bg-gray-500"></span>Inaccessible`;
    }
    updateStatusPillBorder();
    return;
  }

  // Cas 2 : Reconnexion / Actualisation en cours -> Pictogramme Orange
  if (overrideStatus === "connecting") {
    connectionState.cloudStatus = "connecting";
    if (modeBtn) {
      modeBtn.className = "flex items-center justify-center text-amber-400 animate-pulse hover:scale-110 transition-transform cursor-pointer";
      modeBtn.title = `Mode ${modeName} • Synchronisation en cours...`;
    }
    if (cfgDiagCloud) {
      cfgDiagCloud.className = "text-[11px] font-semibold text-sky-400 flex items-center gap-1.5";
      cfgDiagCloud.innerHTML = `<span class="w-2 h-2 rounded-full bg-sky-400 animate-pulse"></span>Synchronisation...`;
    }
    updateStatusPillBorder();
    return;
  }

  const commStatus = connectionState.commStatus;
  const isCloudOffline = overrideStatus === "disconnected" ||
    (commStatus && commStatus.connected === false) ||
    (st === -1);

  // Cas 3 : Conteneur connecté, mais communication perdue -> Pictogramme Rouge
  if (isCloudOffline) {
    connectionState.cloudStatus = "disconnected";
    const reason = (commStatus && commStatus.last_error) || "Rupture de communication ou poêle éteint";
    if (modeBtn) {
      modeBtn.className = "flex items-center justify-center text-rose-400 animate-pulse hover:scale-110 transition-transform cursor-pointer";
      modeBtn.title = `Mode ${modeName} • Perte de communication (${reason}) • Cliquer pour actualiser`;
    }
    if (cfgDiagCloud) {
      cfgDiagCloud.className = "text-[11px] font-semibold text-rose-400 flex items-center gap-1.5";
      cfgDiagCloud.innerHTML = `<span class="w-2 h-2 rounded-full bg-rose-400 animate-pulse"></span>Hors ligne`;
    }
    updateStatusPillBorder();
    return;
  }

  // Cas 4 : Mode Simulation actif (virtuel) -> Pictogramme Orange
  if (activeMode === "simulation") {
    connectionState.cloudStatus = "simulation";
    if (modeBtn) {
      modeBtn.className = "flex items-center justify-center text-amber-400 hover:scale-110 transition-transform cursor-pointer";
      modeBtn.title = "Mode Simulation actif (données virtuelles) • Cliquer pour actualiser";
    }
    if (cfgDiagCloud) {
      cfgDiagCloud.className = "text-[11px] font-semibold text-amber-400 flex items-center gap-1.5";
      cfgDiagCloud.innerHTML = `<span class="w-2 h-2 rounded-full bg-amber-400"></span>Simulation (${activeMode})`;
    }
    updateStatusPillBorder();
    return;
  }

  // Cas 5 : Tout est connecté et synchronisé -> Pictogramme Vert
  connectionState.cloudStatus = "connected";
  const lastTime = (commStatus && commStatus.last_sync_time) || latestData.timestamp || "";
  const lastSyncStr = lastTime ? ` • Synchro : ${lastTime.slice(11, 19)}` : "";

  if (modeBtn) {
    modeBtn.className = "flex items-center justify-center text-emerald-400 hover:scale-110 transition-transform cursor-pointer";
    modeBtn.title = `Mode ${modeName} • Liaison active${lastSyncStr} • Cliquer pour forcer l'actualisation`;
  }
  if (cfgDiagCloud) {
    cfgDiagCloud.className = "text-[11px] font-semibold text-emerald-400 flex items-center gap-1.5";
    cfgDiagCloud.innerHTML = `<span class="w-2 h-2 rounded-full bg-emerald-400 shadow-[0_0_6px_#34d399]"></span>Connecté (${activeMode})`;
  }
  updateStatusPillBorder();
}

// Met à jour les deux indicateurs dissociés (Conteneur & Cloud Nobis)
function updateConnectionIndicator(overrideStatus, message) {
  updateContainerConnectionIndicator(overrideStatus, message);
  updateCloudConnectionIndicator(overrideStatus, message);
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
      if (dot) dot.className = "w-7 h-7 sm:w-8 sm:h-8 rounded-full flex items-center justify-center text-sm sm:text-base bg-orange-600 text-white shadow-[0_0_12px_#ea580c] flex-shrink-0 cursor-pointer border border-orange-400";
      if (kpiDot) kpiDot.className = "w-2.5 h-2.5 rounded-full bg-orange-500 animate-pulse shrink-0";
      if (stateEl) stateEl.className = "text-sm sm:text-base font-bold text-orange-400 leading-tight truncate";
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
  if (data.comm_status) {
    connectionState.commStatus = data.comm_status;
  }
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
  currentCommMode = activeMode;
  const activeDbFile = data.active_db_file || `nobis_a10_${activeMode}.db`;

  const dbEl = document.getElementById("hdr-db");
  if (dbEl) dbEl.textContent = activeDbFile;
  const dockerVersion = data.docker_version || (currentSettings && currentSettings.docker_version) || "v2.1.2";
  const cfgDockerVerEl = document.getElementById("cfg-docker-version");
  if (cfgDockerVerEl) cfgDockerVerEl.textContent = dockerVersion;
  const hdrDockerBadgeEl = document.getElementById("hdr-docker-version-badge");
  if (hdrDockerBadgeEl) hdrDockerBadgeEl.textContent = `🐳 Docker ${dockerVersion}`;
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
  if (typeof renderWeatherModalData === "function") {
    renderWeatherModalData();
  }

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

  // Mise à jour de l'autonomie au centre du Header principal
  const hdrHopperEl = document.getElementById("hdr-pellet-hopper");
  const hdrRunwayEl = document.getElementById("hdr-pellet-runway");
  if (hdrHopperEl) hdrHopperEl.textContent = `${Math.round(hopPct)}%`;
  if (hdrRunwayEl) {
    if (hoursLeft < 24) {
      hdrRunwayEl.textContent = `~${hoursLeft}h (${kgRemaining.toFixed(1)}kg)`;
    } else {
      const d = (hoursLeft / 24.0).toFixed(1);
      hdrRunwayEl.textContent = `~${d}j (${kgRemaining.toFixed(1)}kg)`;
    }
  }

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
    const sbEnabled = (stCode === 8 || stCode === 9 || Number(latestData.standby_enabled ?? 0) === 1);

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
    const isEcoActive = isStandbyPhase || (!isSilActive && (Number(latestData.standby_enabled ?? 0) === 1));
    climBadge.textContent = isStandbyPhase ? (stCode === 8 ? "ACTIF (8 = ECO STOP)" : "ACTIF (9 = STAND BY)") : (isEcoActive ? "STAND-BY ARMÉ" : "VEILLE INACTIVE");
    climBadge.className = `min-h-[36px] px-3 py-1.5 rounded-lg font-semibold text-xs flex items-center border ${isStandbyPhase || isEcoActive ? "bg-cyan-950/80 border-cyan-500 text-cyan-300 shadow" : "bg-gray-900 border-gray-700 text-gray-400"
      }`;
  }

  updateModeRing();
  if (typeof renderTriArcCockpit === "function") {
    renderTriArcCockpit();
  }

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

  // Synchronisation des réglages système (ne pas écraser si l'utilisateur est en train d'éditer ou a modifié le formulaire)
  const isEditingSettings = document.activeElement && document.activeElement.id && document.activeElement.id.startsWith("cfg-");
  if (currentSettings && !isEditingSettings && !isSettingsFormDirty) {
    const modeEl = document.getElementById("cfg-mode");
    if (modeEl) modeEl.value = currentSettings.comm_mode || "agua_cloud";
    const intEl = document.getElementById("cfg-interval");
    if (intEl) intEl.value = currentSettings.poll_interval_s || "120";
    const cityEl = document.getElementById("cfg-city");
    if (cityEl) cityEl.value = currentSettings.weather_city || "";
    const latEl = document.getElementById("cfg-lat");
    if (latEl) latEl.value = currentSettings.weather_lat || "";
    const lonEl = document.getElementById("cfg-lon");
    if (lonEl) lonEl.value = currentSettings.weather_lon || "";
    const emailEl = document.getElementById("cfg-email");
    if (emailEl) emailEl.value = currentSettings.agua_email || "";
    const gwEl = document.getElementById("cfg-gw");
    if (gwEl) gwEl.value = currentSettings.local_gateway_url || "";
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
  let data;
  try {
    const res = await fetch(`/api/dashboard?range_key=${currentRange}`);
    if (!res.ok) {
      connectionState.isServerConnected = false;
      updateConnectionIndicator("disconnected", "Erreur HTTP");
      return;
    }
    data = await res.json();
    connectionState.isServerConnected = true;
  } catch (e) {
    connectionState.isServerConnected = false;
    updateConnectionIndicator("disconnected", "Réseau KO");
    return;
  }

  try {
    applyDashboardState(data, true);
  } catch (e) {
    console.error("[Dashboard] Erreur application état UI:", e);
  }
}

async function refreshLatestOnly() {
  let data;
  try {
    const res = await fetch("/api/latest");
    if (!res.ok) {
      connectionState.isServerConnected = false;
      updateConnectionIndicator("disconnected", "Erreur HTTP");
      return;
    }
    data = await res.json();
    connectionState.isServerConnected = true;
  } catch (e) {
    connectionState.isServerConnected = false;
    updateConnectionIndicator("disconnected", "Réseau KO");
    return;
  }

  try {
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
    console.error("[Dashboard] Erreur mise à jour télémétrie:", e);
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
      if (msg.comm_status) {
        connectionState.commStatus = msg.comm_status;
      }
      updateConnectionIndicator();
      if (msg.event === "telemetry" || msg.type === "telemetry_update" || msg.type === "register_update") {
        refreshLatestOnly();
      }
    } catch (e) { }
  };
  sseSource.onerror = () => {
    connectionState.isSSEConnected = false;
    try { sseSource.close(); } catch (e) { }
    updateContainerConnectionIndicator("connecting", "Reconnexion SSE...");
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
let currentMemoryFilter = "ALL";

function setMemoryFilter(type) {
  currentMemoryFilter = type;
  document.querySelectorAll(".mem-tab-btn").forEach(btn => {
    btn.classList.remove("active", "bg-gray-800", "text-white");
  });
  const activeBtn = document.getElementById("mem-filter-" + type.toLowerCase());
  if (activeBtn) {
    activeBtn.classList.add("active", "bg-gray-800", "text-white");
  }
  const searchInp = document.getElementById("catalog-search");
  filterCatalogTable(searchInp ? searchInp.value : "");
}

function toggleCatalogCollapse() {
  const wrap = document.getElementById("catalog-table-wrap");
  const btn = document.getElementById("btn-toggle-catalog");
  if (!wrap) return;
  isCatalogCollapsed = !isCatalogCollapsed;
  if (isCatalogCollapsed) {
    wrap.classList.remove("max-h-[380px]");
    wrap.classList.add("max-h-[850px]");
    if (btn) btn.innerHTML = "↕️ Réduire";
  } else {
    wrap.classList.remove("max-h-[850px]");
    wrap.classList.add("max-h-[380px]");
    if (btn) btn.innerHTML = "↕️ Déplier";
  }
}

function filterCatalogTable(query) {
  const q = (query || "").toLowerCase().trim();
  const rows = document.querySelectorAll("#eeprom-table-body tr");
  let visibleCount = 0;
  rows.forEach(tr => {
    const text = tr.textContent.toLowerCase();
    const rowMemType = tr.dataset.memType || "";
    const typeMatch = (currentMemoryFilter === "ALL") || (rowMemType === currentMemoryFilter);
    const searchMatch = !q || text.includes(q);
    const match = typeMatch && searchMatch;
    tr.style.display = match ? "" : "none";
    if (match) visibleCount++;
  });
  const countEl = document.getElementById("catalog-reg-count");
  if (countEl) {
    countEl.textContent = (q || currentMemoryFilter !== "ALL")
      ? `${visibleCount} / ${rows.length} affichés`
      : `${rows.length} adresses`;
  }
}

function renderEepromTable() {
  const tbody = document.getElementById("eeprom-table-body");
  if (!tbody) return;
  if (document.activeElement && tbody.contains(document.activeElement)) return;
  tbody.innerHTML = "";

  let rawObj = {};
  try {
    rawObj = JSON.parse(latestData.raw_buffer_json || "{}");
  } catch (e) { }

  const combinedMap = new Map();

  // 1. Registres du catalogue officiel (EEPROM et commandes RAM)
  Object.values(registersCatalog).forEach(r => {
    const offNum = Number(r.offset);
    const memType = r.mem_type || (offNum >= 32768 ? "EEPROM" : "RAM");
    const liveVal = (rawObj[offNum] !== undefined) ? rawObj[offNum] : (r.current_val ?? "--");
    combinedMap.set(offNum, {
      offset: offNum,
      mem_type: memType,
      reg_key: r.reg_key,
      label: r.label,
      category: r.category || "CONTROL",
      unit: r.unit || "",
      min_val: r.min_val,
      max_val: r.max_val,
      step: r.step || 1,
      current_val: liveVal,
      is_writable: true
    });
  });

  // 2. Télémétrie brute des 76 offsets (buffer RAM)
  Object.keys(rawObj).forEach(k => {
    const offNum = Number(k);
    if (!combinedMap.has(offNum)) {
      const memType = (offNum >= 32768) ? "EEPROM" : "RAM";
      const rawDesc = OFFSET_NAMES[offNum] || `Offset #${offNum}`;
      let keyName = `offset_${offNum}`;
      let unit = "";
      if (OFFSET_NAMES[offNum]) {
        const parts = OFFSET_NAMES[offNum].split(" ");
        keyName = parts[0];
        if (parts.length > 1 && parts[1].startsWith("(")) {
          unit = parts.slice(1).join(" ").replace(/[()]/g, "");
        }
      }
      let cat = "TÉLÉMÉTRIE";
      if ([0, 2, 103].includes(offNum)) cat = "SONDES T°";
      else if ([8, 34, 40, 115].includes(offNum)) cat = "ÉTAT / SÉCURITÉ";
      else if ([16, 31, 32, 47].includes(offNum)) cat = "VENTILATEURS";
      else if (offNum === 37) cat = "DÉPRESSION";
      else if ([105, 107].includes(offNum)) cat = "MASQUES USINE";
      else if (offNum === 260) cat = "MAINTENANCE";

      combinedMap.set(offNum, {
        offset: offNum,
        mem_type: memType,
        reg_key: keyName,
        label: rawDesc,
        category: cat,
        unit: unit,
        min_val: null,
        max_val: null,
        step: 1,
        current_val: rawObj[k],
        is_writable: false
      });
    }
  });

  // Si rawObj est encore vide, peupler quand même avec les offsets connus
  if (Object.keys(rawObj).length === 0) {
    Object.keys(OFFSET_NAMES).forEach(k => {
      const offNum = Number(k);
      if (!combinedMap.has(offNum)) {
        const memType = (offNum >= 32768) ? "EEPROM" : "RAM";
        combinedMap.set(offNum, {
          offset: offNum,
          mem_type: memType,
          reg_key: OFFSET_NAMES[offNum].split(" ")[0],
          label: OFFSET_NAMES[offNum],
          category: offNum >= 32768 ? "EEPROM" : "TÉLÉMÉTRIE",
          unit: "",
          min_val: null,
          max_val: null,
          step: 1,
          current_val: "--",
          is_writable: false
        });
      }
    });
  }

  const items = Array.from(combinedMap.values()).sort((a, b) => a.offset - b.offset);

  // Mise à jour des compteurs
  const ramCount = items.filter(i => i.mem_type === "RAM").length;
  const eepromCount = items.filter(i => i.mem_type === "EEPROM").length;
  const totalCount = items.length;

  const cAll = document.getElementById("count-mem-all");
  if (cAll) cAll.textContent = totalCount;
  const cRam = document.getElementById("count-mem-ram");
  if (cRam) cRam.textContent = ramCount;
  const cEep = document.getElementById("count-mem-eeprom");
  if (cEep) cEep.textContent = eepromCount;
  const cBadge = document.getElementById("catalog-reg-count");
  if (cBadge) cBadge.textContent = `${totalCount} adresses`;
  const rawBufEl = document.getElementById("raw-buf-count");
  if (rawBufEl) rawBufEl.textContent = Object.keys(rawObj).length || ramCount;

  items.forEach(item => {
    const tr = document.createElement("tr");
    tr.className = "hover:bg-gray-800/40 transition border-b border-gray-800/40";
    tr.dataset.memType = item.mem_type;

    const hexStr = "0x" + item.offset.toString(16).toUpperCase().padStart(2, "0");
    const typeBadge = (item.mem_type === "RAM")
      ? `<span class="px-1.5 py-0.5 rounded font-mono font-bold text-[10px] bg-emerald-950 text-emerald-300 border border-emerald-800/80">🟢 RAM</span>`
      : `<span class="px-1.5 py-0.5 rounded font-mono font-bold text-[10px] bg-purple-950 text-purple-300 border border-purple-800/80">🟣 EEPROM</span>`;

    const rangeStr = (item.min_val !== null && item.max_val !== null)
      ? `[${Number(item.min_val)}..${Number(item.max_val)}] ${escapeHtml(item.unit || "")}`
      : (item.unit ? escapeHtml(item.unit) : `<span class="text-gray-500">-</span>`);

    let valDisplay = item.current_val;
    if (typeof valDisplay === "number") {
      valDisplay = Number.isInteger(valDisplay) ? valDisplay : valDisplay.toFixed(1);
    }

    const editVal = (typeof item.current_val === "number") ? item.current_val : (Number(item.default_val) || 0);

    const actionHtml = item.is_writable
      ? `<div class="flex items-center justify-end gap-1.5">
          <input id="inp-${escapeHtml(item.reg_key)}" type="number" step="${Number(item.step || 1)}" min="${Number(item.min_val ?? 0)}" max="${Number(item.max_val ?? 65535)}" value="${Number(editVal)}"
            class="inst-input bg-gray-800 border border-gray-700 rounded px-1.5 py-0.5 w-16 sm:w-20 text-white tabular-nums text-xs focus:border-orange-500 focus:outline-none" />
          <button data-reg="${escapeHtml(item.reg_key)}" onclick="sendCommand(this.dataset.reg, document.getElementById('inp-' + this.dataset.reg).value)"
            class="inst-input px-2.5 py-0.5 rounded bg-orange-600 hover:bg-orange-500 text-white text-[11px] font-semibold cursor-pointer transition shadow">Écrire</button>
        </div>`
      : `<div class="text-right"><span class="text-[10px] text-gray-500 font-mono italic">👁️ Direct (RO)</span></div>`;

    tr.innerHTML = `
      <td class="py-1.5 px-2.5 font-mono text-cyan-300 font-bold whitespace-nowrap">
        <span>#${item.offset}</span>
        <span class="text-[9px] text-gray-500 font-normal ml-1">${hexStr}</span>
      </td>
      <td class="py-1.5 px-2 whitespace-nowrap">${typeBadge}</td>
      <td class="py-1.5 px-2 font-mono text-orange-400 font-semibold whitespace-nowrap">${escapeHtml(item.reg_key)}</td>
      <td class="py-1.5 px-2 whitespace-nowrap"><span class="px-2 py-0.5 rounded bg-gray-800 text-gray-300 text-[10px] font-medium">${escapeHtml(item.category)}</span></td>
      <td class="py-1.5 px-2 text-gray-200 max-w-[200px] sm:max-w-[280px] truncate" title="${escapeHtml(item.label)}">${escapeHtml(item.label)}</td>
      <td class="py-1.5 px-2 text-gray-400 text-[10px] whitespace-nowrap">${rangeStr}</td>
      <td class="py-1.5 px-2 font-bold text-white tabular-nums whitespace-nowrap">${escapeHtml(valDisplay)}</td>
      <td class="py-1.5 px-2.5 whitespace-nowrap">${actionHtml}</td>
    `;
    tbody.appendChild(tr);
  });

  const searchInp = document.getElementById("catalog-search");
  filterCatalogTable(searchInp ? searchInp.value : "");
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

// =============================================================================
// AUTHENTIFICATION SÉCURISÉE MENU INSTALLATEUR (CLOUD AGUA PASSWORD)
// =============================================================================

async function checkInstallerAuthStatus(forceRefresh = false) {
  const lockScreen = document.getElementById("installer-lock-screen");
  const unlockedContent = document.getElementById("installer-unlocked-content");
  const noPwdAlert = document.getElementById("installer-no-pwd-alert");
  const errBox = document.getElementById("installer-auth-error");
  const authPill = document.getElementById("installer-auth-pill");
  const authPillText = document.getElementById("installer-auth-pill-text");
  const lockIcon = document.getElementById("subtab-expert-lock-icon");

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
      if (authPill) authPill.className = "text-[10px] font-semibold px-2 py-0.5 rounded-full bg-gray-800 text-gray-400 border border-gray-700 flex items-center gap-1";
      if (authPillText) authPillText.textContent = "Expert Verrouillé";
      if (lockIcon) lockIcon.textContent = "🔒";
      return;
    }
    const data = await res.json();

    if (data.authenticated) {
      lockScreen.classList.add("hidden");
      unlockedContent.classList.remove("hidden");
      if (authPill) authPill.className = "text-[10px] font-semibold px-2 py-0.5 rounded-full bg-emerald-950 text-emerald-300 border border-emerald-600 flex items-center gap-1";
      if (authPillText) authPillText.textContent = "Expert Déverrouillé";
      if (lockIcon) lockIcon.textContent = "🔓";
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
      if (authPill) authPill.className = "text-[10px] font-semibold px-2 py-0.5 rounded-full bg-gray-800 text-gray-400 border border-gray-700 flex items-center gap-1";
      if (authPillText) authPillText.textContent = "Expert Verrouillé";
      if (lockIcon) lockIcon.textContent = "🔒";

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
  "fun_pwf_set": "le mode BOOST",
  "temp_amb_setpoint": "la consigne de température ambiante",
  "duct_left_setpoint": "la consigne canalisation gauche",
  "duct_right_setpoint": "la consigne canalisation droite",
  "chrono_week_enable_set": "la programmation horaire (Chrono)",
};

async function sendCommand(reg_key, value, forceOverride = false) {
  if (!forceOverride && silenceModeState && silenceModeState.active && SILENCE_OVERRIDABLE_KEYS[reg_key]) {
    let targetLabel = "";
    if (reg_key === "power_level") {
      targetLabel = `la puissance de combustion sur P${value}`;
    } else if (reg_key.includes("speed")) {
      const spdText = Number(value) === 0 ? "OFF" : (Number(value) === 8 ? "PS Auto" : `vitesse ${value}`);
      targetLabel = `${SILENCE_OVERRIDABLE_KEYS[reg_key]} sur ${spdText}`;
    } else if (reg_key.includes("setpoint")) {
      targetLabel = `${SILENCE_OVERRIDABLE_KEYS[reg_key]} sur ${value} °C`;
    } else if (reg_key === "chrono_week_enable_set") {
      targetLabel = Number(value) === 1 ? "l'activation du Chrono hebdomadaire" : "la désactivation du Chrono hebdomadaire";
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
      message: `Le mode Silence ! intelligent est actuellement actif (temporisé).\n\nPour appliquer manuellement ${targetLabel}, le mode Silence ! doit être désactivé.\n\nConfirmez-vous la désactivation du mode Silence ! et l'application de cette consigne ?`,
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
  const installerTab = document.getElementById("tab-installer");
  if (installerTab && !installerTab.classList.contains("hidden")) loadAudit();
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
      onConfirm: () => { }
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
      onConfirm: () => { }
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
      onConfirm: () => { },
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
    onConfirm: () => { },
    onCancel: async () => {
      await sendCommand("status_code", 170);
    }
  });
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
  if (typeof renderTriArcCockpit === "function") {
    renderTriArcCockpit();
  }
}

// Met à jour l'anneau extérieur des 5 modes de fonctionnement (Silence, 🔥+💨 Auto, BOOST, 💨 Auto, ECO)
function updateModeRing() {
  const autoVal = Number(getRegVal("fun_auto_set", 0)) === 1;
  const isSilActive = Boolean(silenceModeState && silenceModeState.active);
  const remSec = silenceModeState ? Math.max(0, Math.round(silenceModeState.end_epoch - Date.now() / 1000)) : 0;
  const stCode = Number(latestData.status_code ?? 0);
  const isEco = !isSilActive && (stCode === 8 || stCode === 9 || Number(latestData.standby_enabled ?? 0) === 1);
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
    const isStoveOn = data.silence_mode && typeof data.silence_mode.is_stove_on === "boolean" ? data.silence_mode.is_stove_on : (Number(latestData.status_code ?? 0) !== 0);
    if (isStoveOn) {
      showToast(`Mode Silence ! intelligent actif (${hours}H) : Boost/Éco/Chrono coupés, Auto total actif, consignes en hors gel 7°C (P0 & Ventil 0 sans extinction).`);
    } else {
      showToast(`Mode Silence ! intelligent actif (${hours}H) : poêle éteint, consignes passées en hors gel (7°C).`);
    }
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
    showToast(overrideCommands ? "Mode Silence ! désactivé : nouvelle consigne appliquée." : "Mode Silence ! désactivé : consignes et réglages d'origine restaurés.");
  } catch (e) {
    showToast("Erreur réseau désactivation Silence !: " + e.message, true);
  }
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

function svgDjuDeltaDegreeIcon(cls = "w-4 h-4 text-orange-400 inline-block align-middle shrink-0") {
  return `<svg viewBox="0 0 24 24" class="${cls}" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polygon points="9.5,6.5 17,20 2,20" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linejoin="round" /><circle cx="18.5" cy="6" r="2.2" fill="none" stroke="currentColor" stroke-width="2" /></svg>`;
}

function showDjuInfoModal(event) {
  if (event) {
    if (typeof event.stopPropagation === "function") event.stopPropagation();
    if (typeof event.preventDefault === "function") event.preventDefault();
  }
  showActionModal({
    title: `<span class="flex items-center gap-2">${svgDjuDeltaDegreeIcon("w-4 h-4 text-orange-400")}<span>Degré Jour Unifié (DJU) — Qu'est-ce que c'est ?</span></span>`,
    htmlContent: `
      <div class="space-y-3 text-xs text-gray-200">
        <p class="leading-relaxed">
          Le <b class="text-orange-400">DJU (Degré Jour Unifié)</b> est un indicateur thermique et météorologique standardisé qui mesure la <b>rigueur du froid extérieur</b> par rapport au besoin de chauffage du logement.
        </p>

        <div class="bg-gray-900/90 border border-orange-500/30 rounded-xl p-3 space-y-2">
          <div class="text-orange-300 font-semibold text-xs flex items-center gap-1.5">
            <span>🌡️</span><span>Méthode de calcul (Base 19°C)</span>
          </div>
          <p class="text-gray-300 text-[11px] leading-relaxed">
            Il mesure l'écart entre la température extérieure moyenne d'une journée et la consigne de confort intérieur (fixée à <b>19 °C</b>) :
          </p>
          <div class="bg-black/50 rounded-lg p-2 text-center font-mono text-orange-200 text-xs border border-orange-900/40">
            DJU = 19°C − Température extérieure moyenne
          </div>
          <ul class="text-[11px] text-gray-400 space-y-1 list-disc list-inside">
            <li>Si T°ext = 7°C ➔ 19 − 7 = <b class="text-orange-300">12 DJU</b> nécessaires.</li>
            <li>Si T°ext &ge; 19°C ➔ <b class="text-emerald-400">0 DJU</b> (aucun besoin de chauffage).</li>
          </ul>
        </div>

        <div class="bg-gray-900/90 border border-sky-800/40 rounded-xl p-3 space-y-1.5">
          <div class="text-sky-300 font-semibold text-xs flex items-center gap-1.5">
            ${svgDjuDeltaDegreeIcon("w-3.5 h-3.5 text-sky-400")}<span>Son rôle dans Nobis.Supervisor</span>
          </div>
          <ul class="text-[11px] text-gray-300 space-y-2 leading-relaxed">
            <li class="flex items-start gap-1.5">
              <span class="text-orange-400 font-bold">•</span>
              <span><b>Consommation prédictive :</b> Chaque DJU correspond à environ <b>~0,78 kg de granulés</b> brûlés pour maintenir la maison à température.</span>
            </li>
            <li class="flex items-start gap-1.5">
              <span class="text-emerald-400 font-bold">•</span>
              <span><b>Autonomie du stock (14 jours) :</b> En croisant les DJU prévus par Open-Meteo avec votre stock de sacs, Nobis anticipe le jour exact d'épuisement des pellets.</span>
            </li>
          </ul>
        </div>
      </div>
    `,
    confirmText: "Compris",
    showCancel: false,
    onConfirm: () => { }
  });
}

function openWeatherModal() {
  const modal = document.getElementById("weather-modal");
  if (!modal) return;
  renderWeatherModalData();
  modal.classList.remove("hidden");
}

function closeWeatherModal() {
  const modal = document.getElementById("weather-modal");
  if (modal) modal.classList.add("hidden");
}

function renderWeatherModalData() {
  const city = (currentSettings && currentSettings.weather_city) || "Bazoches-les-Gallerandes";
  const lat = (currentSettings && currentSettings.weather_lat) || "48.1636";
  const lon = (currentSettings && currentSettings.weather_lon) || "2.0436";

  const wDetails = latestData.weather_details || {};
  const tempExt = latestData.weather_temp_ext ?? wDetails.weather_temp_ext;
  const humExt = latestData.weather_humidity ?? wDetails.weather_humidity;
  const windExt = latestData.weather_wind_kmh ?? wDetails.weather_wind_kmh;

  const tNum = tempExt !== undefined && tempExt !== null ? Number(tempExt) : null;
  const djuNow = tNum !== null && !isNaN(tNum) ? Math.max(0, 19.0 - tNum) : null;
  const estKg = djuNow !== null ? Math.max(3.5, djuNow * 0.78) : null;

  const cityEl = document.getElementById("w-modal-city");
  if (cityEl) cityEl.textContent = city;
  const coordsEl = document.getElementById("w-modal-coords");
  if (coordsEl) coordsEl.textContent = `(${lat}, ${lon})`;

  const tempEl = document.getElementById("w-modal-temp");
  if (tempEl) tempEl.textContent = tNum !== null && !isNaN(tNum) ? tNum.toFixed(1) : "--";
  const humEl = document.getElementById("w-modal-hum");
  if (humEl) humEl.textContent = humExt !== undefined && humExt !== null ? Number(humExt).toFixed(0) : "--";
  const windEl = document.getElementById("w-modal-wind");
  if (windEl) windEl.textContent = windExt !== undefined && windExt !== null ? Number(windExt).toFixed(1) : "--";

  const djuEl = document.getElementById("w-modal-dju");
  if (djuEl) djuEl.textContent = djuNow !== null ? djuNow.toFixed(1) : "--";
  const estKgEl = document.getElementById("w-modal-est-kg");
  if (estKgEl) estKgEl.textContent = estKg !== null ? estKg.toFixed(1) : "--";

  const updatedEl = document.getElementById("w-modal-updated");
  if (updatedEl) {
    const ts = latestData.timestamp || "";
    updatedEl.textContent = ts ? `(${ts})` : "";
  }

  const listEl = document.getElementById("w-modal-forecast-list");
  if (!listEl) return;

  const means = Array.isArray(wDetails.forecast_daily_means) ? wDetails.forecast_daily_means : [];
  const mins = Array.isArray(wDetails.forecast_daily_mins) ? wDetails.forecast_daily_mins : [];
  const maxs = Array.isArray(wDetails.forecast_daily_maxs) ? wDetails.forecast_daily_maxs : [];
  const dates = Array.isArray(wDetails.forecast_daily_dates) ? wDetails.forecast_daily_dates : [];

  if (means.length === 0) {
    listEl.innerHTML = `<div class="py-2 text-gray-500 text-center text-[11px]">Aucune prévision journalière en cache.</div>`;
    return;
  }

  const dayNames = ["Dim", "Lun", "Mar", "Mer", "Jeu", "Ven", "Sam"];
  const now = new Date();
  listEl.innerHTML = means.map((mVal, idx) => {
    const dObj = dates[idx] ? new Date(dates[idx] + "T12:00:00") : new Date(now.getTime() + idx * 86400000);
    const dayLbl = idx === 0
      ? "Aujourd'hui"
      : `${dayNames[dObj.getDay()]} ${String(dObj.getDate()).padStart(2, "0")}/${String(dObj.getMonth() + 1).padStart(2, "0")}`;
    const meanT = Number(mVal);
    const minT = mins[idx] !== undefined ? Number(mins[idx]) : (meanT - 3.2);
    const maxT = maxs[idx] !== undefined ? Number(maxs[idx]) : (meanT + 3.2);
    const djuDay = Math.max(0, 19.0 - meanT);
    const kgDay = Math.max(3.5, djuDay * 0.78);

    return `
      <div class="py-1.5 flex items-center justify-between gap-2 tabular-nums">
        <span class="font-medium ${idx === 0 ? 'text-sky-300 font-bold' : 'text-gray-300'} w-24 truncate">${dayLbl}</span>
        <span class="text-[11px] text-gray-400">
          <b class="text-cyan-400">${minT.toFixed(1)}°</b> / <b class="text-white">${meanT.toFixed(1)}°</b> / <b class="text-amber-400">${maxT.toFixed(1)}°</b>
        </span>
        <span class="text-[11px] text-orange-300 font-semibold text-right">Δ° ${djuDay.toFixed(1)} (~${kgDay.toFixed(1)}kg)</span>
      </div>
    `;
  }).join("");
}

function openLifecycleFlowerModal() {
  const modal = document.getElementById("lifecycle-flower-modal");
  if (!modal) return;
  modal.classList.remove("hidden");
  updateDynamicPowerButton();
  restoreFlowerPetalPreview();
}

function closeLifecycleFlowerModal() {
  const modal = document.getElementById("lifecycle-flower-modal");
  if (modal) modal.classList.add("hidden");
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
  const descEl = document.getElementById("flower-status-desc");
  if (descEl && meta) {
    descEl.textContent = meta.desc || "";
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
  const st = Number(latestData.status_code ?? 0);
  const alarm = Number(latestData.alarm_code ?? 0);
  const blackout = Number(latestData.blackout_popup ?? 0);
  const isAlarm = (st === 10 || st === 11 || alarm > 0 || blackout > 0);
  const bannerThemeCode = isAlarm ? (st === 11 ? 11 : 10) : st;
  const curMeta = getPhaseMeta(bannerThemeCode);
  const stateEl = document.getElementById("hdr-state");
  if (stateEl) {
    stateEl.textContent = latestData.status_label || (curMeta ? curMeta.label : "--");
  }
  const descEl = document.getElementById("flower-status-desc");
  if (descEl) {
    descEl.textContent = curMeta ? (curMeta.desc || "") : "";
  }
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
  if (typeof renderTriArcCockpit === "function") {
    renderTriArcCockpit();
  }

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
  const res = await fetch("/api/audit", { cache: "no-store" });
  if (!res.ok) return;
  const rows = await res.json();
  const tbody = document.getElementById("audit-table-body");
  if (!tbody) return;
  if (!rows || rows.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="py-8 text-center text-gray-500 text-xs">🛡️ Aucune écriture de registre enregistrée pour le mode actif.</td></tr>`;
    return;
  }
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
  updateCloudConnectionIndicator("connecting", "Sync Cloud...");
  try {
    const res = await fetch("/api/poll-now", { method: "POST" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (data.comm_status) {
      connectionState.commStatus = data.comm_status;
    }
    await refreshLatestOnly();
    showToast("☁️ Données Cloud Nobis actualisées en direct !");
  } catch (e) {
    updateCloudConnectionIndicator("disconnected", "Échec Cloud");
    showToast("⚠️ Échec d'interrogation du Cloud Nobis");
  } finally {
    if (btn) btn.textContent = "⟳ Actualiser";
    updateCloudConnectionIndicator();
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
  const j = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 401 || res.status === 403) {
      openActionModal({
        title: "🔐 Authentification requise",
        message: "Saisissez votre mot de passe Agua Cloud (Installateur) pour enregistrer la nouvelle liaison :",
        showInput: true,
        inputType: "password",
        confirmText: "Déverrouiller & Enregistrer",
        cancelText: "Annuler",
        onConfirm: async (pwd) => {
          if (!pwd) {
            showToast("Mot de passe requis pour modifier ce paramètre.", true);
            return;
          }
          try {
            const authRes = await fetch("/api/installer/verify", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ password: pwd })
            });
            const authData = await authRes.json().catch(() => ({}));
            if (!authRes.ok || !authData.token) {
              showToast(authData.detail || "Mot de passe incorrect.", true);
              return;
            }
            installerAuthToken = authData.token;
            sessionStorage.setItem("nobis_installer_token", installerAuthToken);
            if (typeof checkInstallerAuthStatus === "function") {
              checkInstallerAuthStatus();
            }
            await saveSettings();
          } catch (e) {
            showToast("Erreur réseau lors de l'authentification : " + e.message, true);
          }
        }
      });
    } else {
      showToast("Erreur : " + (j.detail || res.statusText || "Échec de l'enregistrement"), true);
    }
    return;
  }
  isSettingsFormDirty = false;
  if (j.settings) {
    currentSettings = j.settings;
  }
  if (connectionState && connectionState.commStatus) {
    connectionState.commStatus.comm_mode = mode;
  }
  currentCommMode = mode;
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

let lastLoadedLogs = [];

function renderLogsList(logs) {
  const box = document.getElementById("logs-container");
  if (!box) return;

  if (!logs || logs.length === 0) {
    box.innerHTML = `<div class="py-12 text-center text-gray-500">
      <span class="text-3xl block mb-2">📜</span>
      Aucun événement ne correspond à ce filtre.
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
        <span class="text-gray-500 tabular-nums shrink-0 text-[11px] mt-0.5">${escapeHtml(l.timestamp || "")}</span>
        <span class="px-1.5 py-0.5 rounded text-[10px] border ${bgBadge} ${sevBadge} shrink-0 w-20 text-center">[${escapeHtml(s)}]</span>
        <span class="text-gray-400 shrink-0 w-28 text-[11px] font-semibold truncate" title="${escapeHtml(l.source || "")}">${escapeHtml(l.source || "")}</span>
        <span class="text-gray-200 flex-1 break-words leading-relaxed">${escapeHtml(l.message || "")}</span>
      </div>
    `;
  }).join("");
}

function filterLogsClientSide(query) {
  const q = (query || "").trim().toLowerCase();
  if (!q) {
    renderLogsList(lastLoadedLogs);
    return;
  }
  const filtered = (lastLoadedLogs || []).filter(l => {
    const msg = (l.message || "").toLowerCase();
    const src = (l.source || "").toLowerCase();
    const sev = (l.severity || "").toLowerCase();
    const ts = (l.timestamp || "").toLowerCase();
    return msg.includes(q) || src.includes(q) || sev.includes(q) || ts.includes(q);
  });
  renderLogsList(filtered);
}

function copyLogsToClipboard() {
  if (!lastLoadedLogs || lastLoadedLogs.length === 0) {
    showToast("Aucun log à copier.", true);
    return;
  }
  const searchInp = document.getElementById("log-search-input");
  const q = searchInp ? searchInp.value.trim().toLowerCase() : "";
  const logsToCopy = q ? lastLoadedLogs.filter(l => {
    const msg = (l.message || "").toLowerCase();
    const src = (l.source || "").toLowerCase();
    return msg.includes(q) || src.includes(q);
  }) : lastLoadedLogs;

  const text = logsToCopy.map(l => `${l.timestamp || ""} [${(l.severity || "INFO").toUpperCase()}] [${l.source || ""}] ${l.message || ""}`).join("\n");
  navigator.clipboard.writeText(text).then(() => {
    showToast(`📋 ${logsToCopy.length} logs copiés dans le presse-papier !`);
  }).catch(() => {
    showToast("Impossible de copier dans le presse-papier", true);
  });
}

async function loadLogs(sev) {
  currentLogSeverity = sev || "ALL";

  // Highlight active filter buttons
  ["all", "info", "command", "warning", "error", "container"].forEach(s => {
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

  const res = await fetch(`/api/logs?severity=${encodeURIComponent(currentLogSeverity)}`, { cache: "no-store" });
  if (!res.ok) return;
  const logs = await res.json();
  lastLoadedLogs = Array.isArray(logs) ? logs : [];

  const searchInp = document.getElementById("log-search-input");
  if (searchInp && searchInp.value.trim()) {
    filterLogsClientSide(searchInp.value);
  } else {
    renderLogsList(lastLoadedLogs);
  }
}

async function restartBackend() {
  if (!confirm("Voulez-vous vraiment redémarrer le serveur backend Nobis.Supervisor ?")) {
    return;
  }
  try {
    const res = await fetch("/api/system/restart", {
      method: "POST",
      headers: getInstallerHeaders()
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 403) {
      showToast("🔒 Authentification installateur requise : déverrouillez l'onglet Expert & Registres.", true);
      return;
    }
    if (!res.ok) {
      showToast(data.detail || "Échec du redémarrage du backend.", true);
      return;
    }
    showToast(data.message || "🔄 Redémarrage du backend en cours...");
    const startedAt = Date.now();
    const waitReboot = async () => {
      await new Promise(r => setTimeout(r, 1500));
      while (Date.now() - startedAt < 30000) {
        try {
          const hr = await fetch("/health", { cache: "no-store" });
          if (hr.ok) {
            showToast("✅ Backend redémarré et opérationnel !");
            if (typeof loadDashboard === "function") await loadDashboard();
            return;
          }
        } catch (_) {
          // Serveur en cours de redémarrage
        }
        await new Promise(r => setTimeout(r, 1500));
      }
      showToast("⚠️ Le backend met du temps à répondre, rechargez la page.", true);
    };
    waitReboot();
  } catch (err) {
    showToast("Erreur réseau lors de la demande de redémarrage.", true);
  }
}

async function checkWatchtowerUpdate() {
  try {
    showToast("⬆️ Vérification de mise à jour Watchtower en cours...");
    const res = await fetch("/api/system/check-update", {
      method: "POST",
      headers: getInstallerHeaders()
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 403) {
      showToast("🔒 Authentification installateur requise : déverrouillez l'onglet Expert & Registres.", true);
      return;
    }
    if (!res.ok) {
      showToast(data.detail || "Watchtower injoignable ou erreur de mise à jour.", true);
      return;
    }
    if (data.status === "unavailable") {
      showToast(`ℹ️ ${data.message}`);
      return;
    }
    showToast(data.message || "✅ Vérification de mise à jour Watchtower déclenchée !");
  } catch (err) {
    showToast("Erreur réseau lors de l'appel à Watchtower.", true);
  }
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
  const matterBtn = document.getElementById("hdr-matter-btn");
  const dot = document.getElementById("hdr-matter-dot");
  const cfgDot = document.getElementById("cfg-matter-status-dot");
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

  if (cfgDot) {
    if (!isRunning) {
      cfgDot.className = "w-2.5 h-2.5 rounded-full bg-rose-500 shadow-[0_0_6px_#f43f5e] inline-block";
      cfgDot.title = "Pont Matter hors ligne";
    } else if (isCommissioned) {
      cfgDot.className = "w-2.5 h-2.5 rounded-full bg-emerald-400 shadow-[0_0_8px_#34d399] inline-block";
      cfgDot.title = `Pont Matter actif et appairé (${fabricsCount} hub${fabricsCount > 1 ? "s" : ""})`;
    } else {
      cfgDot.className = "w-2.5 h-2.5 rounded-full bg-cyan-400 animate-pulse shadow-[0_0_8px_#38bdf8] inline-block";
      cfgDot.title = "Pont Matter prêt à appairer";
    }
  }

  if (matterBtn) {
    if (!isRunning) {
      matterBtn.className = "w-8 h-8 sm:w-9 sm:h-9 rounded-full flex items-center justify-center border bg-rose-600/85 border-rose-400 text-white shadow-[0_0_10px_rgba(244,63,94,0.45)] flex-shrink-0 cursor-pointer hover:bg-rose-500 transition-all";
      matterBtn.title = "Pont Matter hors ligne • Cliquer pour configurer";
    } else {
      matterBtn.className = "w-8 h-8 sm:w-9 sm:h-9 rounded-full flex items-center justify-center border bg-emerald-600/85 border-emerald-400 text-white shadow-[0_0_10px_rgba(16,185,129,0.45)] flex-shrink-0 cursor-pointer hover:bg-emerald-500 transition-all";
      matterBtn.title = isCommissioned
        ? `Pont Matter actif et appairé (${fabricsCount} hub${fabricsCount > 1 ? "s" : ""}) • Cliquer pour afficher`
        : "Pont Matter en ligne (prêt à appairer) • Cliquer pour afficher le QR Code";
    }
  }
  if (dot) dot.className = "hidden";

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
        const res = await fetch("/api/matter/factory-reset", { method: "POST", headers: getInstallerHeaders() });
        const data = await res.json();
        renderMatterStatus(data);
        showToast("Nouveau QR Code Matter généré !");
      } catch (err) {
        showToast("Erreur lors de la réinitialisation Matter", true);
      }
    }
  });
}

// =============================================================================
// COCKPIT TRI-ARC V10 (ONGLET PILOTAGE : SX ⇄ POÊLE ⇄ DX, JAUGES & BULLES)
// =============================================================================
let triArcZone = "AMB"; // "SX" | "AMB" | "DX"
let triArcExpandedMode = null; // null | "silence" | "auto" | "boost"
let triArcDragging = null; // { zone, arcType, value }
let triArcOptimisticValues = {}; // { key: { val, expires } }
let triArcPendingChanges = {}; // { reg_key: { reg_key, label, oldVal, newVal, fmtFn } }
let triArcBatchTimer = null;

const TRIARC_REG_META = {
  temp_amb_setpoint: { label: "🌡️ Consigne T° Poêle ( Principal )", fmt: fmtTriArcTempSp },
  duct_left_setpoint: { label: "🌡️ Consigne T° Soufflerie SX", fmt: fmtTriArcTempSp },
  duct_right_setpoint: { label: "🌡️ Consigne T° Soufflerie DX", fmt: fmtTriArcTempSp },
  fan_front_speed: { label: "🌀 Ventilation Poêle ( Frontale )", fmt: fmtTriArcFan },
  duct_left_speed: { label: "🌀 Ventilation Soufflerie SX", fmt: fmtTriArcFan },
  duct_right_speed: { label: "🌀 Ventilation Soufflerie DX", fmt: fmtTriArcFan },
  power_level: { label: "🔥 Puissance de combustion", fmt: (v) => `P${v}` },
  fun_pwf_set: { label: "🚀 Mode Boost (Puissance)", fmt: (v) => (Number(v) === 1 ? "🚀 ON" : (Number(v) === 2 ? "⏱️ PROG" : "OFF")) },
  fun_auto_set: { label: "🔥+🌀 Mode Automatique", fmt: (v) => (Number(v) === 1 ? "ACTIF" : "OFF") },
  standby_set: { label: "💤 Mode Éco (Stand-by)", fmt: (v) => (Number(v) === 1 ? "ACTIF" : "OFF") }
};

function setTriArcOptimistic(key, val, durationMs = 30000) {
  triArcOptimisticValues[key] = { val: Number(val), expires: Date.now() + durationMs };
}

function clearTriArcOptimistic(keys) {
  (keys || Object.keys(triArcOptimisticValues)).forEach(k => {
    delete triArcOptimisticValues[k];
  });
}

function getTriArcLiveOrOpt(key, fallbackVal) {
  const opt = triArcOptimisticValues[key];
  if (opt && Date.now() < opt.expires) {
    return opt.val;
  }
  return Number(fallbackVal);
}

function getTriArcRawBaseline(reg_key) {
  if (reg_key === "fun_pwf_set") return Number(getRegVal("fun_pwf_set", latestData.fun_pwf_set ?? 0));
  if (reg_key === "fun_auto_set") return Number(getRegVal("fun_auto_set", latestData.fun_auto_set ?? 0));
  if (reg_key === "standby_set") return Number(getRegVal("standby_set", latestData.standby_set ?? 0));
  if (reg_key === "power_level") return Number(getRegVal("power_level", latestData.power_level ?? 4));
  return Number(getRegVal(reg_key, 0));
}

function queueTriArcChanges(changesList) {
  if (!Array.isArray(changesList) || changesList.length === 0) return;

  changesList.forEach(({ reg_key, value }) => {
    const newVal = Number(value);
    const existing = triArcPendingChanges[reg_key];
    const oldVal = existing ? existing.oldVal : getTriArcRawBaseline(reg_key);
    const meta = TRIARC_REG_META[reg_key] || { label: reg_key, fmt: (v) => String(v) };

    setTriArcOptimistic(reg_key, newVal, 30000);

    if (newVal === oldVal) {
      delete triArcPendingChanges[reg_key];
      delete triArcOptimisticValues[reg_key];
    } else {
      triArcPendingChanges[reg_key] = {
        reg_key,
        label: meta.label,
        oldVal,
        newVal,
        fmt: meta.fmt
      };
    }
  });

  renderTriArcCockpit();

  if (triArcBatchTimer) {
    clearTimeout(triArcBatchTimer);
    triArcBatchTimer = null;
  }

  if (Object.keys(triArcPendingChanges).length > 0) {
    triArcBatchTimer = setTimeout(() => {
      triArcBatchTimer = null;
      promptTriArcBatchValidation();
    }, 1000);
  }
}

function promptTriArcBatchValidation() {
  if (triArcDragging) {
    triArcBatchTimer = setTimeout(promptTriArcBatchValidation, 600);
    return;
  }
  const items = Object.values(triArcPendingChanges);
  if (items.length === 0) return;

  const rowsHtml = items.map(item => `
    <div class="flex items-center justify-between gap-2 bg-slate-950/90 border border-slate-800 rounded-xl px-3 py-2 text-xs">
      <span class="font-semibold text-slate-200">${escapeHtml(item.label)}</span>
      <span class="font-mono font-bold whitespace-nowrap">
        <span class="text-slate-400">${escapeHtml(item.fmt(item.oldVal))}</span>
        <span class="text-orange-400 mx-1">➔</span>
        <span class="text-emerald-400">${escapeHtml(item.fmt(item.newVal))}</span>
      </span>
    </div>
  `).join("");

  const htmlContent = `
    <div class="space-y-2.5">
      <p class="text-xs text-slate-300">Confirmez-vous l'application ${items.length > 1 ? `de ces <strong>${items.length} modifications</strong>` : "de cette modification"} sur le poêle ?</p>
      <div class="space-y-1.5 max-h-60 overflow-y-auto pr-0.5">${rowsHtml}</div>
    </div>
  `;

  openActionModal({
    title: `✅ Validation (${items.length} réglage${items.length > 1 ? "s" : ""})`,
    htmlContent,
    confirmText: "Valider et Appliquer",
    cancelText: "Annuler",
    onConfirm: async () => {
      const toSend = Object.values(triArcPendingChanges).map(it => ({
        reg_key: it.reg_key,
        value: Number(it.newVal)
      }));
      triArcPendingChanges = {};
      if (toSend.length === 0) return;

      toSend.forEach(c => setTriArcOptimistic(c.reg_key, c.value, 6000));

      if (silenceModeState && silenceModeState.active && toSend.some(c => SILENCE_OVERRIDABLE_KEYS[c.reg_key])) {
        await deactivateSilenceMode(toSend);
        return;
      }

      if (toSend.length === 1) {
        await sendCommand(toSend[0].reg_key, toSend[0].value);
      } else {
        showToast(`Application groupée de ${toSend.length} réglages...`);
        try {
          const res = await fetch("/api/command-batch", {
            method: "POST",
            headers: getInstallerHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ commands: toSend })
          });
          if (res.ok) {
            showToast(`${toSend.length} réglages appliqués avec succès !`);
          } else {
            const err = await res.json().catch(() => ({}));
            showToast("Erreur batch : " + (err.detail || res.statusText), true);
          }
        } catch (e) {
          showToast("Erreur réseau : " + e.message, true);
        }
        await refreshLatestOnly();
      }
    },
    onCancel: () => {
      const keys = Object.keys(triArcPendingChanges);
      triArcPendingChanges = {};
      clearTriArcOptimistic(keys);
      renderTriArcCockpit();
      showToast("Modifications annulées.");
    }
  });
}

function nobisStovePaths() {
  return `
    <path fill-rule="evenodd" d="M 43 4 H 57 Q 59 4 59 6 V 19 Q 64 19 66 22 L 67.5 27 Q 69 31 73 31 Q 81 31 84 35 Q 86 39 86 48 V 104 V 112 Q 86 114 83 114 H 74 Q 71.5 114 70 111 L 66 105 H 34 L 30 111 Q 28.5 114 26 114 H 17 Q 14 114 14 112 V 104 V 48 Q 14 39 16 35 Q 19 31 27 31 Q 31 31 32.5 27 L 34 22 Q 36 19 41 19 V 6 Q 41 4 43 4 Z M 29 42 H 71 Q 73 42 73 44 V 78 Q 73 80 71 80 H 29 Q 27 80 27 78 V 44 Q 27 42 29 42 Z M 25 88 H 75 Q 77 88 77 90.5 Q 77 93 75 93 H 25 Q 23 93 23 90.5 Q 23 88 25 88 Z M 33 80 C 32 72 34 65 37 60 C 40 66 43 68 45 67 C 46 58 48 45 52 44 C 55 52 56 61 58 63 C 60 59 63 54 65 56 C 68 62 69 71 67 80 Z" />
  `;
}

function svgNobisStoveIcon(cls = "w-5 h-5 inline-block", color = "currentColor") {
  return `<svg viewBox="10 0 80 120" class="${cls}" fill="${color}">${nobisStovePaths()}</svg>`;
}

function formatPhaseTwoLines(phaseLabel, meta) {
  if (!phaseLabel) return { line1: "ÉTAT", line2: "Inconnu" };
  const raw = String(phaseLabel).trim();
  const parenMatch = raw.match(/^([^(]+)\s*\(([^)]+)\)$/);
  if (parenMatch) {
    return {
      line1: parenMatch[1].trim(),
      line2: parenMatch[2].trim()
    };
  }
  const parts = raw.split(/\s+/);
  if (parts.length >= 2) {
    return {
      line1: parts[0],
      line2: parts.slice(1).join(" ")
    };
  }
  return {
    line1: raw,
    line2: meta ? (meta.short || "") : ""
  };
}

function svgPropellerFanIcon(cls = "w-4 h-4 inline-block", color = "currentColor") {
  return `<svg viewBox="0 0 24 24" class="${cls}" fill="${color}">
    <circle cx="12" cy="12" r="2.3"/>
    <path d="M12 9.2c-1.6-2.5-1.2-6.2 1.8-6.7 2.4-.4 4.1 1.8 3.1 4.3-.7 1.7-2.4 2.3-4.9 2.4z"/>
    <path d="M14.4 13.4c2.9.2 5.9 2.4 4.9 5.2-.9 2.3-3.6 2.7-5.3.6-1.1-1.4-.8-3.2.4-5.8z"/>
    <path d="M9.6 13.4c-1.3 2.6-2.6 4.5-4.7 3.8-2.3-.7-2.6-3.5-.6-5.2 1.5-1.3 3.4-1 5.3 1.4z"/>
  </svg>`;
}

function fmtTriArcTempSp(val) {
  const v = Math.round(Number(val));
  if (v <= 7) return "❄️";
  if (v >= 41) return "MAX";
  return `${v}°`;
}

function fmtTriArcFan(val) {
  const v = Math.round(Number(val));
  if (v <= 0) return "OFF";
  if (v >= 8) return "AUTO";
  return String(v);
}

// Échelle non-linéaire dilatée autour de 19° (avec 19° à l'apogée 270°)
function triArcTempToAngle(t) {
  const num = Number(t);
  const val = isNaN(num) ? 19 : num;
  const v = Math.max(7, Math.min(41, val));
  if (v <= 17) {
    return 135 + ((v - 7) / 10) * 75; // 7°->135°, 17°->210°
  } else if (v <= 19) {
    return 210 + ((v - 17) / 2) * 60; // 17°->210°, 19°->270° (apogée)
  } else if (v <= 23) {
    return 270 + ((v - 19) / 4) * 70; // 19°->270°, 23°->340°
  } else {
    return 340 + ((v - 23) / 18) * 65; // 23°->340°, 41°->405°
  }
}

function triArcAngleToTemp(angleDeg) {
  const a = Math.max(135, Math.min(405, Number(angleDeg)));
  if (a <= 210) {
    return Math.round(7 + ((a - 135) / 75) * 10);
  } else if (a <= 270) {
    return Math.round(17 + ((a - 210) / 60) * 2);
  } else if (a <= 340) {
    return Math.round(19 + ((a - 270) / 70) * 4);
  } else {
    const raw = 23 + ((a - 340) / 65) * 18;
    return raw >= 40.5 ? 41 : Math.round(raw);
  }
}

function triArcPtOnCircle(cx, cy, r, deg) {
  const rad = (deg * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}

function triArcPath(cx, cy, r, startDeg, endDeg, ccw = false) {
  if (Math.abs(endDeg - startDeg) < 0.3) return "";
  const s = triArcPtOnCircle(cx, cy, r, startDeg);
  const e = triArcPtOnCircle(cx, cy, r, endDeg);
  const diff = ccw ? (startDeg - endDeg) : (endDeg - startDeg);
  const largeArc = Math.abs(diff) > 180 ? 1 : 0;
  const sweep = ccw ? 0 : 1;
  return `M ${s.x.toFixed(1)} ${s.y.toFixed(1)} A ${r} ${r} 0 ${largeArc} ${sweep} ${e.x.toFixed(1)} ${e.y.toFixed(1)}`;
}

function goToTriArcZone(z) {
  triArcZone = z;
  triArcExpandedMode = null;
  renderTriArcCockpit();
}

function closeTriArcOptionBubble() {
  triArcExpandedMode = null;
  renderTriArcCockpit();
}

function toggleTriArcOptionBubble(modeName) {
  triArcExpandedMode = (triArcExpandedMode === modeName) ? null : modeName;
  renderTriArcCockpit();
}

function getTriArcTelemetryState() {
  const st = Number(latestData.status_code ?? 0);
  const alarm = Number(latestData.alarm_code ?? 0);
  const blackout = Number(latestData.blackout_popup ?? 0);
  const isAlarm = (st === 10 || st === 11 || alarm > 0 || blackout > 0);
  const meta = typeof getPhaseMeta === "function" ? getPhaseMeta(isAlarm ? (st === 11 ? 11 : 10) : st) : null;
  const phaseLabelRaw = latestData.status_label || (meta ? meta.label : (st === 0 ? "Éteint" : `Phase #${st}`));
  const phaseIcon = meta ? meta.icon : (st === 0 ? "⚫" : "🔥");
  const phaseColor = meta ? meta.hex : (st === 5 ? "#ea580c" : "#94a3b8");

  const realAmb = Number(latestData.temp_ambient ?? 20.0).toFixed(1);
  const realSx = Number(latestData.duct_left_temp ?? 19.5).toFixed(1);
  const realDx = Number(latestData.duct_right_temp ?? 19.0).toFixed(1);

  let spAmb = getTriArcLiveOrOpt("temp_amb_setpoint", getRegVal("temp_amb_setpoint", 20));
  let spSx = getTriArcLiveOrOpt("duct_left_setpoint", getRegVal("duct_left_setpoint", 20));
  let spDx = getTriArcLiveOrOpt("duct_right_setpoint", getRegVal("duct_right_setpoint", 20));

  let fanAmb = getTriArcLiveOrOpt("fan_front_speed", getRegVal("fan_front_speed", 3));
  let fanSx = getTriArcLiveOrOpt("duct_left_speed", getRegVal("duct_left_speed", 0));
  let fanDx = getTriArcLiveOrOpt("duct_right_speed", getRegVal("duct_right_speed", 0));

  const realFanAmb = getActualFanButtonVal(latestData.vent_front_actual, fanAmb);
  const realFanSx = getActualFanButtonVal(latestData.duct_left_actual, fanSx);
  const realFanDx = getActualFanButtonVal(latestData.duct_right_actual, fanDx);

  const pwfVal = getTriArcLiveOrOpt("fun_pwf_set", getRegVal("fun_pwf_set", latestData.fun_pwf_set ?? 0));
  const pwrSetVal = getTriArcLiveOrOpt("power_level", getRegVal("power_level", 4));
  // L'activation du mode Boost (POWERFUL) ne passe pas la consigne de puissance à 8 (celle-ci reste celle choisie par l'utilisateur)
  let pwrSp = pwrSetVal;
  const realPwr = Number(latestData.power_actual ?? pwrSp);

  // Surcharge en direct pendant le glisser tactile
  if (triArcDragging) {
    if (triArcDragging.arcType === "temp") {
      if (triArcDragging.zone === "AMB") spAmb = triArcDragging.value;
      else if (triArcDragging.zone === "SX") spSx = triArcDragging.value;
      else if (triArcDragging.zone === "DX") spDx = triArcDragging.value;
    } else if (triArcDragging.arcType === "fan") {
      if (triArcDragging.zone === "AMB") fanAmb = triArcDragging.value;
      else if (triArcDragging.zone === "SX") fanSx = triArcDragging.value;
      else if (triArcDragging.zone === "DX") fanDx = triArcDragging.value;
    } else if (triArcDragging.arcType === "pwr") {
      pwrSp = triArcDragging.value;
    }
  }

  const autoVal = getTriArcLiveOrOpt("fun_auto_set", getRegVal("fun_auto_set", latestData.fun_auto_set ?? 0));
  const isPsAll = (fanAmb === 8 && fanSx === 8 && fanDx === 8);
  const autoIdx = autoVal === 1 ? 2 : (isPsAll ? 1 : 0);

  const isSilActive = Boolean(silenceModeState && silenceModeState.active);
  const remSec = silenceModeState ? Math.max(0, Math.round(silenceModeState.end_epoch - Date.now() / 1000)) : 0;
  const isEco = !isSilActive && (st === 8 || st === 9 || (getActiveCommMode() === "simulation" && getTriArcLiveOrOpt("standby_set", getRegVal("standby_set", 0)) === 1));

  return {
    st, isAlarm, phaseIcon, phaseLabel: phaseLabelRaw, phaseColor,
    realAmb, spAmb, fanAmb, realFanAmb,
    realSx, spSx, fanSx, realFanSx,
    realDx, spDx, fanDx, realFanDx,
    pwrSp, realPwr, pwfVal, autoIdx, isSilActive, remSec, isEco
  };
}

function renderTriArcTopHeader(zone, T) {
  // Chevron SVG Gauche (pointe à gauche ◄) avec contours doux et arrondis
  const svgChevronLeftBg = (borderColor, fillColor = "#0b1222") => `
    <svg class="absolute inset-0 w-full h-full pointer-events-none" preserveAspectRatio="none" viewBox="0 0 100 50">
      <path d="M 22 2 L 88 2 Q 97 2 97 10 L 97 40 Q 97 48 88 48 L 22 48 Q 14 48 9.5 41.5 L 3.5 28.5 Q 1.5 25 3.5 21.5 L 9.5 8.5 Q 14 2 22 2 Z" fill="${fillColor}" fill-opacity="0.94" stroke="${borderColor}" stroke-width="2" stroke-linejoin="round" />
    </svg>
  `;

  // Chevron SVG Droite (pointe à droite ►) avec contours doux et arrondis
  const svgChevronRightBg = (borderColor, fillColor = "#0b1222") => `
    <svg class="absolute inset-0 w-full h-full pointer-events-none" preserveAspectRatio="none" viewBox="0 0 100 50">
      <path d="M 12 2 Q 3 2 3 10 L 3 40 Q 3 48 12 48 L 78 48 Q 86 48 90.5 41.5 L 96.5 28.5 Q 98.5 25 96.5 21.5 L 90.5 8.5 Q 86 2 78 2 Z" fill="${fillColor}" fill-opacity="0.94" stroke="${borderColor}" stroke-width="2" stroke-linejoin="round" />
    </svg>
  `;

  // Écran AMB : Bouton SX (Chevron gauche ◄, hauteur fixe h-14)
  const chevronSX = `
    <button type="button" onclick="goToTriArcZone('SX')"
      class="flex-1 h-14 relative py-1 pl-3.5 pr-2 flex items-center justify-between text-left transition hover:brightness-125 shadow cursor-pointer group">
      ${svgChevronLeftBg("#06b6d4")}
      <div class="relative z-10 flex items-center font-black text-cyan-300 text-xs my-auto pl-1">
        <span>SX</span>
      </div>
      <div class="relative z-10 flex flex-col items-end gap-0.5 tabular-nums">
        <span class="flex items-center gap-1 text-cyan-300 font-black text-[11px]">
          ${svgPropellerFanIcon("w-3.5 h-3.5", "#22d3ee")}
          <span>${fmtTriArcFan(T.fanSx)}</span>
        </span>
        <span class="text-[11px] font-black text-white pt-0.5 border-t border-slate-700/80">
          ${T.realSx}° <span class="text-cyan-400">→</span> <span class="text-cyan-200">${fmtTriArcTempSp(T.spSx)}</span>
        </span>
      </div>
    </button>
  `;

  // Écran AMB : Bouton DX (Chevron droite ►, hauteur fixe h-14)
  const chevronDX = `
    <button type="button" onclick="goToTriArcZone('DX')"
      class="flex-1 h-14 relative py-1 pl-2 pr-3.5 flex items-center justify-between text-right transition hover:brightness-125 shadow cursor-pointer group">
      ${svgChevronRightBg("#0284c7")}
      <div class="relative z-10 flex flex-col items-start gap-0.5 tabular-nums">
        <span class="flex items-center gap-1 text-sky-300 font-black text-[11px]">
          ${svgPropellerFanIcon("w-3.5 h-3.5", "#38bdf8")}
          <span>${fmtTriArcFan(T.fanDx)}</span>
        </span>
        <span class="text-[11px] font-black text-white pt-0.5 border-t border-slate-700/80">
          ${T.realDx}° <span class="text-sky-400">→</span> <span class="text-sky-200">${fmtTriArcTempSp(T.spDx)}</span>
        </span>
      </div>
      <div class="relative z-10 flex items-center font-black text-sky-300 text-xs my-auto pr-1">
        <span>DX</span>
      </div>
    </button>
  `;

  // Écran SX : Le bouton SX devient un rectangle élargi (hauteur fixe h-14)
  const expandedRectSX = `
    <div class="flex-1 min-w-0 h-14 py-1.5 px-3 sm:px-3.5 rounded-2xl bg-cyan-950/70 border-2 border-cyan-400 flex items-center justify-between text-left transition shadow box-border">
      <div class="flex items-center gap-1 font-bold text-cyan-300 text-xs my-auto truncate">
        <span>Canal. SX</span>
      </div>
      <div class="flex flex-col items-end gap-0.5 tabular-nums shrink-0">
        <span class="flex items-center gap-1 text-cyan-300 font-black text-xs">
          ${svgPropellerFanIcon("w-3.5 h-3.5", "#22d3ee")}
          <span>${fmtTriArcFan(T.fanSx)}</span>
        </span>
        <span class="text-xs font-black text-white whitespace-nowrap pt-0.5 border-t border-slate-700/80">
          ${T.realSx}° <span class="text-cyan-400">→</span> <span class="text-cyan-200">${fmtTriArcTempSp(T.spSx)}</span>
        </span>
      </div>
    </div>
  `;

  // Écran DX : Le bouton DX devient un rectangle élargi (hauteur fixe h-14)
  const expandedRectDX = `
    <div class="flex-1 min-w-0 h-14 py-1.5 px-3 sm:px-3.5 rounded-2xl bg-sky-950/70 border-2 border-sky-400 flex items-center justify-between text-right transition shadow box-border">
      <div class="flex flex-col items-start gap-0.5 tabular-nums shrink-0">
        <span class="flex items-center gap-1 text-sky-300 font-black text-xs">
          ${svgPropellerFanIcon("w-3.5 h-3.5", "#38bdf8")}
          <span>${fmtTriArcFan(T.fanDx)}</span>
        </span>
        <span class="text-xs font-black text-white whitespace-nowrap pt-0.5 border-t border-slate-700/80">
          ${T.realDx}° <span class="text-sky-400">→</span> <span class="text-sky-200">${fmtTriArcTempSp(T.spDx)}</span>
        </span>
      </div>
      <div class="flex items-center gap-1 font-bold text-sky-300 text-xs my-auto truncate">
        <span>Canal. DX</span>
      </div>
    </div>
  `;

  // Bouton retour Poêle (Colorisé selon l'état du poêle, élargi pour garder T° et consigne sur une seule ligne)
  const chevronStoveReturn = (direction) => {
    const isReturnFromSx = (direction === "right");
    return `
      <button type="button" onclick="goToTriArcZone('AMB')"
        class="flex-1 min-w-0 h-14 relative py-1 ${isReturnFromSx ? 'pl-2.5 pr-4' : 'pl-4 pr-2.5'} flex items-center justify-between transition hover:brightness-125 shadow cursor-pointer group">
        ${isReturnFromSx ? svgChevronRightBg(T.phaseColor, "#0b1222") : svgChevronLeftBg(T.phaseColor, "#0b1222")}
        ${isReturnFromSx ? `
          <div class="relative z-10 flex flex-col items-start gap-0.5 tabular-nums min-w-0 pr-1">
            <span class="flex items-center gap-1 font-black text-[11px]" style="color:${T.phaseColor}">
              ${svgPropellerFanIcon("w-3 h-3", T.phaseColor)}
              <span>${fmtTriArcFan(T.fanAmb)}</span>
            </span>
            <span class="text-xs sm:text-[13px] font-black text-white whitespace-nowrap pt-0.5 border-t border-slate-700/80">
              ${T.realAmb}° <span style="color:${T.phaseColor}">→</span> <span style="color:${T.phaseColor}">${fmtTriArcTempSp(T.spAmb)}</span>
            </span>
          </div>
          <div class="relative z-10 flex items-center justify-center my-auto pr-1 shrink-0" title="Retour Poêle">
            ${svgNobisStoveIcon("w-6 h-6", T.phaseColor)}
          </div>
        ` : `
          <div class="relative z-10 flex items-center justify-center my-auto pl-1 shrink-0" title="Retour Poêle">
            ${svgNobisStoveIcon("w-6 h-6", T.phaseColor)}
          </div>
          <div class="relative z-10 flex flex-col items-end gap-0.5 tabular-nums min-w-0 pl-1">
            <span class="flex items-center gap-1 font-black text-[11px]" style="color:${T.phaseColor}">
              ${svgPropellerFanIcon("w-3 h-3", T.phaseColor)}
              <span>${fmtTriArcFan(T.fanAmb)}</span>
            </span>
            <span class="text-xs sm:text-[13px] font-black text-white whitespace-nowrap pt-0.5 border-t border-slate-700/80">
              ${T.realAmb}° <span style="color:${T.phaseColor}">→</span> <span style="color:${T.phaseColor}">${fmtTriArcTempSp(T.spAmb)}</span>
            </span>
          </div>
        `}
      </button>
    `;
  };

  let pwrBtnCls = "bg-orange-600/90 border-orange-400 text-white shadow-[0_0_14px_rgba(234,88,12,0.5)]";
  if (T.isAlarm) {
    pwrBtnCls = "bg-rose-700 border-rose-300 text-white shadow-[0_0_16px_rgba(244,63,94,0.65)] animate-pulse";
  } else if (T.st === 0) {
    pwrBtnCls = "bg-rose-950/90 border-rose-500 text-rose-200";
  }

  const centerPowerBtn = `
    <button type="button" onclick="handleDynamicPowerAction()"
      title="Bouton Marche / Arrêt Poêle"
      class="h-14 w-14 aspect-square rounded-2xl border-2 flex items-center justify-center shrink-0 transition shadow-lg cursor-pointer ${pwrBtnCls}">
      <svg viewBox="0 0 24 24" class="w-6 h-6 drop-shadow" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 2v10" />
        <path d="M18.4 6.6a9 9 0 1 1-12.77.04" />
      </svg>
    </button>
  `;

  if (zone === "AMB") {
    return `<div class="flex items-stretch justify-between gap-2">${chevronSX}${centerPowerBtn}${chevronDX}</div>`;
  } else if (zone === "SX") {
    return `<div class="flex items-stretch justify-between gap-2">${expandedRectSX}${chevronStoveReturn("right")}</div>`;
  } else {
    return `<div class="flex items-stretch justify-between gap-2">${chevronStoveReturn("left")}${expandedRectDX}</div>`;
  }
}

function renderTriArcSvgForZone(zone, T) {
  const isDuct = (zone === "SX" || zone === "DX");
  const realT = zone === "AMB" ? T.realAmb : (zone === "SX" ? T.realSx : T.realDx);
  const spT = zone === "AMB" ? T.spAmb : (zone === "SX" ? T.spSx : T.spDx);
  const fanSp = zone === "AMB" ? T.fanAmb : (zone === "SX" ? T.fanSx : T.fanDx);
  const fanReal = zone === "AMB" ? T.realFanAmb : (zone === "SX" ? T.realFanSx : T.realFanDx);
  const pwrSp = T.pwrSp;
  const pwrReal = T.realPwr;

  const CX = 160, CY = 128;
  const R_TEMP = 74;
  const R_OUTER = 112;

  const angTempSp = triArcTempToAngle(spT);
  const angTempReal = triArcTempToAngle(realT);
  const posTempSp = triArcPtOnCircle(CX, CY, R_TEMP, angTempSp);
  const posTempReal = triArcPtOnCircle(CX, CY, R_TEMP, angTempReal);
  const trackTemp = triArcPath(CX, CY, R_TEMP, 135, 405, false);
  const fillTemp = triArcPath(CX, CY, R_TEMP, 135, angTempSp, false);

  const fanGaugeVal = Math.max(0, Math.min(8, Number(fanReal > 0 ? fanReal : fanSp)));
  const angFanSp = 142 + (Math.max(0, Math.min(8, fanSp)) / 8) * 76;
  const angFanReal = 142 + (Math.max(0, Math.min(8, fanReal)) / 8) * 76;
  const angFanGauge = 142 + (fanGaugeVal / 8) * 76;
  const posFanSp = triArcPtOnCircle(CX, CY, R_OUTER, angFanSp);
  const posFanReal = triArcPtOnCircle(CX, CY, R_OUTER, angFanReal);
  const trackFan = triArcPath(CX, CY, R_OUTER, 142, 218, false);
  const fillFan = triArcPath(CX, CY, R_OUTER, 142, angFanGauge, false);

  const pwrGaugeVal = Math.max(0, Math.min(8, Number(pwrReal > 0 ? pwrReal : pwrSp)));
  const angPwrSp = 38 - (Math.max(0, Math.min(8, pwrSp)) / 8) * 76;
  const angPwrReal = 38 - (Math.max(0, Math.min(8, pwrReal)) / 8) * 76;
  const angPwrGauge = 38 - (pwrGaugeVal / 8) * 76;
  const posPwrSp = triArcPtOnCircle(CX, CY, R_OUTER, angPwrSp);
  const posPwrReal = triArcPtOnCircle(CX, CY, R_OUTER, angPwrReal);
  const trackPwr = triArcPath(CX, CY, R_OUTER, 38, -38, true);
  const fillPwr = triArcPath(CX, CY, R_OUTER, 38, angPwrGauge, true);

  let fanTicks = "";
  for (let i = 0; i <= 8; i++) {
    const a = 142 + (i / 8) * 76;
    const pLbl = triArcPtOnCircle(CX, CY, R_OUTER + 18, a);
    const lbl = i === 0 ? "OFF" : (i === 8 ? "AUTO" : String(i));
    const isAct = (i === Math.round(fanGaugeVal)) || (i === Math.round(fanSp));
    fanTicks += `<text x="${pLbl.x.toFixed(1)}" y="${(pLbl.y + 3).toFixed(1)}" text-anchor="middle" font-size="${i === 0 || i === 8 ? '8.5' : '9'}" font-weight="${isAct ? '900' : '600'}" fill="${isAct ? '#38bdf8' : '#64748b'}">${lbl}</text>`;
  }

  let pwrTicks = "";
  for (let i = 0; i <= 8; i++) {
    const a = 38 - (i / 8) * 76;
    const pLbl = triArcPtOnCircle(CX, CY, R_OUTER + 20, a);
    const lbl = i === 0 ? "0 Eco" : String(i);
    const isAct = !isDuct && ((i === Math.round(pwrGaugeVal)) || (i === Math.round(pwrSp)));
    pwrTicks += `<text x="${pLbl.x.toFixed(1)}" y="${(pLbl.y + 3).toFixed(1)}" text-anchor="middle" font-size="${i === 0 ? '8.5' : '9'}" font-weight="${isAct ? '900' : '600'}" fill="${isAct ? '#ea580c' : '#64748b'}">${lbl}</text>`;
  }

  const bgAnimClass = T.isAlarm ? "triarc-bg-alarm" : (T.st === 0 ? "" : "triarc-bg-breath");
  const phaseLines = formatPhaseTwoLines(T.phaseLabel, typeof getPhaseMeta === "function" ? getPhaseMeta(T.st) : null);

  return `
    <svg id="tri-arc-svg-${zone}" data-zone="${zone}" viewBox="14 20 292 198" class="tri-arc-svg w-full mx-auto overflow-visible">
      <defs>
        <linearGradient id="gradTemp-${zone}" x1="0%" y1="100%" x2="100%" y2="0%">
          <stop offset="0%" stop-color="#38bdf8" />
          <stop offset="45%" stop-color="#f59e0b" />
          <stop offset="75%" stop-color="#ea580c" />
          <stop offset="100%" stop-color="#e11d48" />
        </linearGradient>
        <linearGradient id="gradFan-${zone}" x1="0%" y1="100%" x2="0%" y2="0%">
          <stop offset="0%" stop-color="#0284c7" />
          <stop offset="50%" stop-color="#38bdf8" />
          <stop offset="100%" stop-color="#fde047" />
        </linearGradient>
        <linearGradient id="gradPwr-${zone}" x1="0%" y1="100%" x2="0%" y2="0%">
          <stop offset="0%" stop-color="#38bdf8" />
          <stop offset="50%" stop-color="#f97316" />
          <stop offset="100%" stop-color="#ea580c" />
        </linearGradient>
        <radialGradient id="gradPhaseBg-${zone}" cx="50%" cy="50%" r="60%">
          <stop offset="0%" stop-color="${T.phaseColor}" stop-opacity="${T.st === 0 ? '0.12' : '0.42'}" />
          <stop offset="65%" stop-color="${T.phaseColor}" stop-opacity="${T.st === 0 ? '0.04' : '0.16'}" />
          <stop offset="100%" stop-color="#0b101d" stop-opacity="0.95" />
        </radialGradient>
      </defs>

      <!-- Fond du cadran central + Animation de la phase du poêle -->
      <circle cx="${CX}" cy="${CY}" r="68" fill="#0b101d" stroke="#1e293b" stroke-width="1" />
      <circle cx="${CX}" cy="${CY}" r="66" fill="url(#gradPhaseBg-${zone})" class="${bgAnimClass} pointer-events-none" />
      ${T.st !== 0 ? `<circle cx="${CX}" cy="${CY}" r="54" fill="none" stroke="${T.phaseColor}" stroke-width="1.5" class="triarc-bg-ripple pointer-events-none" />` : ""}

      <!-- LOGO EN ARRIÈRE-PLAN : POÊLE SUR AMB, VENTILATEUR EN FILIGRANE GRISÉ SUR SX & DX -->
      ${zone === "AMB" ? `
        <g transform="translate(${CX - 28}, ${CY - 38}) scale(0.56)" fill="${T.phaseColor}" opacity="0.18" class="pointer-events-none">
          ${nobisStovePaths()}
        </g>
      ` : `
        <g transform="translate(${CX - 50.4}, ${CY - 50.4}) scale(4.2)" fill="#94a3b8" opacity="0.14" class="pointer-events-none">
          <circle cx="12" cy="12" r="2.3"/>
          <path d="M12 9.2c-1.6-2.5-1.2-6.2 1.8-6.7 2.4-.4 4.1 1.8 3.1 4.3-.7 1.7-2.4 2.3-4.9 2.4z"/>
          <path d="M14.4 13.4c2.9.2 5.9 2.4 4.9 5.2-.9 2.3-3.6 2.7-5.3.6-1.1-1.4-.8-3.2.4-5.8z"/>
          <path d="M9.6 13.4c-1.3 2.6-2.6 4.5-4.7 3.8-2.3-.7-2.6-3.5-.6-5.2 1.5-1.3 3.4-1 5.3 1.4z"/>
        </g>
      `}

      <!-- 1. CERCLE CENTRAL TRONQUÉ : TEMPÉRATURE (SANS MARQUES 17°/23°) -->
      <path d="${trackTemp}" fill="none" stroke="#1e293b" stroke-width="8" stroke-linecap="round" />
      ${fillTemp ? `<path d="${fillTemp}" fill="none" stroke="url(#gradTemp-${zone})" stroke-width="8" stroke-linecap="round" />` : ""}
      <path d="${trackTemp}" fill="none" stroke="transparent" stroke-width="28" class="arc-hit" data-arc="temp" />

      <!-- Uniquement ❄️, 19° et MAX (19° à l'apogée 270°) -->
      <text x="105" y="202" text-anchor="middle" font-size="11" fill="#38bdf8" font-weight="bold">❄️</text>
      <text x="${CX}" y="46" text-anchor="middle" font-size="9.5" fill="#f97316" font-weight="bold">19°</text>
      <text x="215" y="202" text-anchor="middle" font-size="9" fill="#fb7185" font-weight="bold">MAX</text>

      <!-- MARQUEUR DU RÉEL SUR L'ARC TEMPÉRATURE -->
      <g transform="translate(${posTempReal.x}, ${posTempReal.y})">
        <circle cx="0" cy="0" r="5.5" fill="#090d16" stroke="#38bdf8" stroke-width="2.2" />
        <circle cx="0" cy="0" r="2" fill="#38bdf8" />
      </g>

      <!-- CURSEUR PICTOGRAMME TEMPÉRATURE (🌡️) -->
      <g transform="translate(${posTempSp.x}, ${posTempSp.y})" class="pointer-events-none">
        <circle cx="0" cy="0" r="11" fill="#0f172a" stroke="#ea580c" stroke-width="2.2" />
        <text x="0" y="1" text-anchor="middle" dominant-baseline="central" font-size="11.5">🌡️</text>
      </g>

      <!-- LIBELLÉ DISCRET DU VOLET / ZONE (CANALISATION SX / DX, SANS SALON) -->
      <text x="${CX}" y="79" text-anchor="middle" font-size="8.5" font-weight="800" fill="${zone === "SX" ? "#22d3ee" : (zone === "DX" ? "#38bdf8" : "#94a3b8")}" letter-spacing="0.5">${zone === "AMB" ? "" : (zone === "SX" ? "CANAL. SX" : "CANAL. DX")}</text>

      <!-- T° RÉELLE REDESCENDUE DANS LE CADRAN CENTRAL + FLÈCHE → CONSIGNE -->
      <text x="${CX - 13}" y="134" text-anchor="middle" font-size="34" font-weight="900" fill="#ffffff">${realT}°</text>
      <text x="${CX + 28}" y="124" text-anchor="start" font-size="12" font-weight="bold" fill="#fb923c">→ ${fmtTriArcTempSp(spT)}</text>

      <!-- BULLE D'ÉTAT DU POÊLE (UNIQUEMENT SUR L'ÉCRAN CENTRAL AMB, DISPARAÎT SUR SX & DX) -->
      ${zone === "AMB" ? `
        <g onclick="openLifecycleFlowerModal()" class="triarc-phase-hit cursor-pointer" role="button" aria-label="Cycle de vie du poêle">
          <rect x="${CX - 44}" y="148" width="88" height="30" rx="9" fill="#0f172a" fill-opacity="0.88" stroke="${T.phaseColor}" stroke-opacity="0.75" stroke-width="1.2" />
          <text x="${CX}" y="159" text-anchor="middle" font-size="8.5" font-weight="900" fill="${T.phaseColor}">${T.phaseIcon} ${escapeHtml(phaseLines.line1)}</text>
          <text x="${CX}" y="171" text-anchor="middle" font-size="7.5" font-weight="700" fill="#cbd5e1">${escapeHtml(phaseLines.line2)}</text>
        </g>
      ` : ""}

      <!-- 2. ARC GAUCHE : VENTILATION (OFF ➔ 1..7 ➔ AUTO) -->
      ${(() => {
      const isFanAuto = (T.autoIdx === 1 || T.autoIdx === 2 || fanSp === 8);
      return `
          <g class="${isFanAuto ? 'pointer-events-none opacity-60' : ''}">
            <path d="${trackFan}" fill="none" stroke="#1e293b" stroke-width="9" stroke-linecap="round" />
            ${fillFan ? `<path d="${fillFan}" fill="none" stroke="url(#gradFan-${zone})" stroke-width="9" stroke-linecap="round" />` : ""}
            ${!isFanAuto ? `<path d="${trackFan}" fill="none" stroke="transparent" stroke-width="28" class="arc-hit cursor-pointer" data-arc="fan" />` : ""}
            ${fanTicks}

            <g transform="translate(${posFanReal.x}, ${posFanReal.y})">
              <circle cx="0" cy="0" r="5.5" fill="#090d16" stroke="#38bdf8" stroke-width="2.2" />
              <circle cx="0" cy="0" r="2" fill="#38bdf8" />
            </g>
            ${!isFanAuto ? `
            <g transform="translate(${posFanSp.x}, ${posFanSp.y})" class="pointer-events-none">
              <circle cx="0" cy="0" r="11" fill="#0f172a" stroke="#38bdf8" stroke-width="2.2" />
              <g transform="translate(-7.5, -7.5) scale(0.625)" fill="#38bdf8">
                <circle cx="12" cy="12" r="2.3"/>
                <path d="M12 9.2c-1.6-2.5-1.2-6.2 1.8-6.7 2.4-.4 4.1 1.8 3.1 4.3-.7 1.7-2.4 2.3-4.9 2.4z"/>
                <path d="M14.4 13.4c2.9.2 5.9 2.4 4.9 5.2-.9 2.3-3.6 2.7-5.3.6-1.1-1.4-.8-3.2.4-5.8z"/>
                <path d="M9.6 13.4c-1.3 2.6-2.6 4.5-4.7 3.8-2.3-.7-2.6-3.5-.6-5.2 1.5-1.3 3.4-1 5.3 1.4z"/>
              </g>
            </g>
            ` : ""}
          </g>
        `;
    })()}

      <!-- 3. ARC DROIT : PUISSANCE (0 Eco ➔ 1..7 ➔ 8, GRISÉ SUR SX/DX, NON CLIQUABLE SI AUTO COMPLET) -->
      ${(() => {
      const isPwrAuto = (T.autoIdx === 2);
      const isDisabled = isDuct || isPwrAuto;
      return `
          <g opacity="${isDuct ? '0.22' : (isPwrAuto ? '0.60' : '1')}" class="${isDisabled ? 'pointer-events-none' : ''}">
            <path d="${trackPwr}" fill="none" stroke="#1e293b" stroke-width="9" stroke-linecap="round" />
            ${fillPwr ? `<path d="${fillPwr}" fill="none" stroke="${isDuct ? '#475569' : `url(#gradPwr-${zone})`}" stroke-width="9" stroke-linecap="round" />` : ""}
            ${!isDisabled ? `<path d="${trackPwr}" fill="none" stroke="transparent" stroke-width="28" class="arc-hit cursor-pointer" data-arc="pwr" />` : ""}
            ${pwrTicks}

            <g transform="translate(${posPwrReal.x}, ${posPwrReal.y})">
              <circle cx="0" cy="0" r="5.5" fill="#090d16" stroke="${isDuct ? '#64748b' : '#ea580c'}" stroke-width="2.2" />
              <circle cx="0" cy="0" r="2" fill="${isDuct ? '#64748b' : '#ea580c'}" />
            </g>
            ${!isPwrAuto ? `
            <g transform="translate(${posPwrSp.x}, ${posPwrSp.y})" class="pointer-events-none">
              <circle cx="0" cy="0" r="11" fill="#0f172a" stroke="${isDuct ? '#64748b' : '#ea580c'}" stroke-width="2.2" />
              <text x="0" y="1" text-anchor="middle" dominant-baseline="central" font-size="11.5">🔥</text>
            </g>
            ` : ""}
          </g>
        `;
    })()}
    </svg>
  `;
}

function renderTriArcDailyChronoFrise(zone = "AMB") {
  const now = new Date();
  const todayJsDay = now.getDay();
  const nowMin = now.getHours() * 60 + now.getMinutes();

  const isChronoEnabled = Boolean(Number(getRegVal("chrono_week_enable_set", latestData.chrono_week_enable ?? 0)));

  // Couleurs vives pour les 4 programmes (1: Vert, 2: Ambre, 3: Cyan, 4: Violet)
  const progColors = isChronoEnabled ? {
    1: "bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.7)]",
    2: "bg-amber-400 shadow-[0_0_6px_rgba(251,191,36,0.7)]",
    3: "bg-cyan-400 shadow-[0_0_6px_rgba(34,211,238,0.7)]",
    4: "bg-purple-400 shadow-[0_0_6px_rgba(192,132,252,0.7)]"
  } : {
    1: "bg-slate-600",
    2: "bg-slate-600",
    3: "bg-slate-600",
    4: "bg-slate-600"
  };

  const fmtChronoFan = (v) => {
    const n = Number(v);
    if (n <= 0) return "OFF";
    if (n >= 8) return "Auto";
    return String(n);
  };

  // Fenêtre glissante de 24h centrée sur Maintenant (Now - 12h à Now + 12h)
  const HALF_WINDOW = 720; // 12 heures en minutes

  // Segments par programme (1 à 4) sur 4 pistes distinctes pour éviter toute superposition
  const tracksSegments = { 1: "", 2: "", 3: "", 4: "" };

  for (let p = 1; p <= 4; p++) {
    const startSlot = Number(getRegVal(`chrono_p${p}_start_set`, latestData[`chrono_p${p}_start`] ?? 144));
    const stopSlot = Number(getRegVal(`chrono_p${p}_stop_set`, latestData[`chrono_p${p}_stop`] ?? 144));
    const mask = Number(getRegVal(`chrono_p${p}_days_mask`, latestData[`chrono_p${p}_days`] ?? 0));

    if (startSlot >= 144 || stopSlot >= 144 || startSlot === stopSlot) continue;

    let temp = 19;
    let subLabel = "";
    if (zone === "SX") {
      temp = Number(getRegVal(`chrono_p${p}_tcanl_set`, latestData[`chrono_p${p}_tcanl`] ?? latestData[`chrono_p${p}_temp`] ?? 20));
      const ventSx = Number(getRegVal(`chrono_p${p}_canl_set`, latestData[`chrono_p${p}_canl`] ?? 0));
      subLabel = `V${fmtChronoFan(ventSx)}`;
    } else if (zone === "DX") {
      temp = Number(getRegVal(`chrono_p${p}_tcanr_set`, latestData[`chrono_p${p}_tcanr`] ?? latestData[`chrono_p${p}_temp`] ?? 20));
      const ventDx = Number(getRegVal(`chrono_p${p}_canr_set`, latestData[`chrono_p${p}_canr`] ?? 0));
      subLabel = `V${fmtChronoFan(ventDx)}`;
    } else {
      temp = Number(getRegVal(`chrono_p${p}_temperature_set`, latestData[`chrono_p${p}_temp`] ?? 19));
      const pwr = Number(getRegVal(`chrono_p${p}_power_set`, latestData[`chrono_p${p}_power`] ?? 4));
      const ventAmb = Number(getRegVal(`chrono_p${p}_fan_front_set`, latestData[`chrono_p${p}_fan_front`] ?? 0));
      subLabel = `P${pwr} · V${fmtChronoFan(ventAmb)}`;
    }

    // Parcourir hier (-1), aujourd'hui (0) et demain (+1)
    [-1, 0, 1].forEach(dOffset => {
      const targetJsDay = (todayJsDay + dOffset + 7) % 7;
      const dayObj = GANTT_DAYS.find(d => d.jsDay === targetJsDay) || GANTT_DAYS[0];
      const isDayActive = (mask & dayObj.bit) !== 0;
      if (!isDayActive) return;

      const dayBaseMin = dOffset * 1440;
      const subRanges = [];

      if (stopSlot > startSlot) {
        subRanges.push([dayBaseMin + startSlot * 10, dayBaseMin + stopSlot * 10]);
      } else {
        // Traverse minuit
        subRanges.push([dayBaseMin + startSlot * 10, dayBaseMin + 1440]);
        subRanges.push([dayBaseMin + 1440, dayBaseMin + 1440 + stopSlot * 10]);
      }

      subRanges.forEach(([t1, t2]) => {
        const relStart = t1 - nowMin;
        const relStop = t2 - nowMin;
        const cStart = Math.max(-HALF_WINDOW, relStart);
        const cStop = Math.min(HALF_WINDOW, relStop);

        if (cStop > cStart) {
          const leftPct = (50 + (cStart / HALF_WINDOW) * 50).toFixed(2);
          const widthPct = (((cStop - cStart) / HALF_WINDOW) * 50).toFixed(2);
          tracksSegments[p] += `
            <div style="left:${leftPct}%;width:${widthPct}%"
              class="absolute inset-y-0 rounded-full ${progColors[p]}"
              title="Programme ${p} (${zone}) : ${temp}°C | ${subLabel} ${!isChronoEnabled ? '(Chrono désactivé)' : ''}">
            </div>
          `;
        }
      });
    });
  }

  // Graduations d'heures dynamiques glissantes toutes les 3 heures
  let ticksHtml = "";
  [-1, 0, 1].forEach(dOffset => {
    for (let h = 0; h < 24; h += 3) {
      const tMin = dOffset * 1440 + h * 60;
      const rel = tMin - nowMin;
      if (rel >= -HALF_WINDOW + 25 && rel <= HALF_WINDOW - 25) {
        if (Math.abs(rel) > 40) {
          const xPct = (50 + (rel / HALF_WINDOW) * 50).toFixed(1);
          ticksHtml += `<span style="left:${xPct}%;" class="absolute -translate-x-1/2">${String(h).padStart(2, "0")}h</span>`;
        }
      }
    }
  });

  return `
    <button type="button" onclick="switchTab('tab-chrono')"
      title="Ouvrir l'onglet Programmes (🕒 Chrono)"
      class="w-full text-left bg-slate-900/90 hover:bg-slate-800/90 border border-slate-800/90 hover:border-orange-500/60 rounded-2xl px-3 py-1 space-y-1 shadow-md transition cursor-pointer select-none ${!isChronoEnabled ? 'opacity-60' : ''}">
      <!-- 4 pistes distinctes superposées (P1 à P4) : traits de couleur sans superposition -->
      <div class="relative w-full h-8 bg-slate-950 rounded-xl border border-slate-800 overflow-hidden flex flex-col justify-between py-1.5 px-1">
        <div class="relative w-full h-1 bg-slate-900/60 rounded-full overflow-hidden">
          ${tracksSegments[1]}
        </div>
        <div class="relative w-full h-1 bg-slate-900/60 rounded-full overflow-hidden">
          ${tracksSegments[2]}
        </div>
        <div class="relative w-full h-1 bg-slate-900/60 rounded-full overflow-hidden">
          ${tracksSegments[3]}
        </div>
        <div class="relative w-full h-1 bg-slate-900/60 rounded-full overflow-hidden">
          ${tracksSegments[4]}
        </div>
        <!-- Trait rouge fixe au centre (50%) symbolisant Maintenant -->
        <div style="left:50%;" class="absolute inset-y-0 w-0.5 bg-rose-500 z-10 shadow-[0_0_10px_#f43f5e] -translate-x-1/2 pointer-events-none"></div>
      </div>
      <!-- Échelle horaire glissante -->
      <div class="relative w-full h-3.5 text-[9.5px] text-slate-500 font-mono leading-none">
        ${ticksHtml}
      </div>
    </button>
  `;
}

function renderTriArcBottomModes(T) {
  const silActive = T.isSilActive && T.remSec > 0;
  const silTimerTxt = silActive
    ? `${Math.floor(T.remSec / 3600)}h${String(Math.floor((T.remSec % 3600) / 60)).padStart(2, "0")}`
    : "";

  let autoBtnCls = "bg-slate-900/90 border-slate-700 text-slate-400 hover:border-cyan-400";
  let autoBadge = "";
  if (T.autoIdx === 1) {
    autoBtnCls = "bg-cyan-950/80 border-cyan-400 text-cyan-200 shadow-[0_0_12px_rgba(6,182,212,0.35)]";
    autoBadge = `<span class="text-[9.5px] font-black uppercase text-cyan-300 mt-0.5">Ventil</span>`;
  } else if (T.autoIdx === 2) {
    autoBtnCls = "bg-orange-950/80 border-orange-400 text-orange-200 shadow-[0_0_12px_rgba(249,115,22,0.35)]";
    autoBadge = `<span class="text-[9.5px] font-black uppercase text-orange-300 mt-0.5">Auto</span>`;
  }

  return `
    <div class="grid grid-cols-4 gap-2 pt-1 pb-1">
      <!-- 1. Silence (🤫) -->
      <button type="button" onclick="toggleTriArcOptionBubble('silence')"
        title="Mode Silence (Timer)"
        class="h-14 sm:h-15 min-h-[54px] rounded-2xl border-2 flex flex-col items-center justify-center transition cursor-pointer ${silActive ? 'bg-purple-950/75 border-purple-400 text-white shadow-[0_0_12px_rgba(168,85,247,0.35)]' : 'bg-slate-900/90 border-slate-700 text-slate-400 hover:border-purple-400'}">
        <span class="text-2xl leading-none">🤫</span>
        ${silActive ? `<span class="text-[10px] font-black mt-0.5 tabular-nums text-purple-200">${silTimerTxt}</span>` : ""}
      </button>

      <!-- 2. Auto (Cliquable avec modal/popup 3 positions : OFF, Ventil seule, Auto complet) -->
      <button type="button" onclick="toggleTriArcOptionBubble('auto')"
        title="Mode Auto (OFF / Ventilation / Auto complet)"
        class="h-14 sm:h-15 min-h-[54px] rounded-2xl border-2 flex flex-col items-center justify-center transition cursor-pointer ${autoBtnCls}">
        <span class="text-lg leading-none flex items-center justify-center gap-0.5">
          <span>🔥</span><span class="text-base font-bold">+</span>${svgPropellerFanIcon("w-4 h-4", "currentColor")}
        </span>
        ${autoBadge}
      </button>

      <!-- 3. Boost (🚀 : désactivé=fusée colorée, actif=fusée allumée avec halo, programmé=fusée allumée + horloge) -->
      ${(() => {
      let boostBtnCls = "bg-slate-900/90 border-slate-700 text-slate-300 hover:border-rose-400";
      let boostIcon = `<span class="text-2xl leading-none">🚀</span>`;
      if (T.pwfVal === 1) {
        boostBtnCls = "bg-rose-950/80 border-rose-400 text-rose-200 shadow-[0_0_14px_rgba(244,63,94,0.45)]";
        boostIcon = `<span class="text-2xl leading-none drop-shadow-[0_0_8px_rgba(244,63,94,0.8)]">🚀</span>`;
      } else if (T.pwfVal === 2) {
        boostBtnCls = "bg-amber-950/80 border-amber-400 text-amber-200 shadow-[0_0_14px_rgba(245,158,11,0.45)]";
        boostIcon = `
            <div class="relative inline-flex items-center justify-center">
              <span class="text-2xl leading-none drop-shadow-[0_0_8px_rgba(245,158,11,0.8)]">🚀</span>
              <span class="absolute -bottom-1 -right-1.5 text-xs leading-none">⏱️</span>
            </div>
          `;
      }
      return `
          <button type="button" onclick="toggleTriArcOptionBubble('boost')"
            title="Mode Boost / POWERFUL (1=Actif, 2=Programmé)"
            class="h-14 sm:h-15 min-h-[54px] rounded-2xl border-2 flex flex-col items-center justify-center transition cursor-pointer ${boostBtnCls}">
            ${boostIcon}
          </button>
        `;
    })()}

      <!-- 4. Eco / Stand-by (💤 : CONFORT MODE Nobis) -->
      <button type="button" onclick="toggleTriArcOptionBubble('eco')"
        title="Mode Éco / Confort Mode (Stand-by)"
        class="h-14 sm:h-15 min-h-[54px] rounded-2xl border-2 flex flex-col items-center justify-center transition cursor-pointer ${T.isEco ? 'bg-emerald-950/75 border-emerald-400 text-emerald-200 shadow-[0_0_12px_rgba(16,185,129,0.35)]' : 'bg-slate-900/90 border-slate-700 text-slate-300 hover:border-emerald-400'}">
        <span class="text-2xl leading-none">💤</span>
      </button>
    </div>
  `;
}

function setTriArcEcoOption(val) {
  closeTriArcOptionBubble();
  if (getActiveCommMode() === "agua_cloud") {
    showToast("Le Cloud Agua IOT ne permet pas de modifier Comfort Clima à distance. Utilisez la radiocommande Nobis (MENU ▸ UTILISATEUR ▸ COMFORT CLIMA).", "warning");
    return;
  }
  queueTriArcChanges([{ reg_key: "standby_set", value: Number(val) }]);
}

function setTriArcAutoOption(idx) {
  closeTriArcOptionBubble();
  if (idx === 0) {
    // Désactivation complète du mode Auto : fun_auto_set = 0 et réinitialisation des vitesses de ventilateurs si elles étaient en Auto (8)
    const changes = [{ reg_key: "fun_auto_set", value: 0 }];
    const curFanAmb = Number(getTriArcLiveOrOpt("fan_front_speed", getRegVal("fan_front_speed", 3)));
    const curFanSx = Number(getTriArcLiveOrOpt("duct_left_speed", getRegVal("duct_left_speed", 0)));
    const curFanDx = Number(getTriArcLiveOrOpt("duct_right_speed", getRegVal("duct_right_speed", 0)));
    if (curFanAmb >= 8) changes.push({ reg_key: "fan_front_speed", value: 3 });
    if (curFanSx >= 8) changes.push({ reg_key: "duct_left_speed", value: 0 });
    if (curFanDx >= 8) changes.push({ reg_key: "duct_right_speed", value: 0 });
    queueTriArcChanges(changes);
  } else if (idx === 1) {
    queueTriArcChanges([
      { reg_key: "fun_auto_set", value: 0 },
      { reg_key: "fan_front_speed", value: 8 },
      { reg_key: "duct_left_speed", value: 8 },
      { reg_key: "duct_right_speed", value: 8 }
    ]);
  } else if (idx === 2) {
    queueTriArcChanges([
      { reg_key: "fun_auto_set", value: 1 },
      { reg_key: "fan_front_speed", value: 8 },
      { reg_key: "duct_left_speed", value: 8 },
      { reg_key: "duct_right_speed", value: 8 }
    ]);
  }
}

async function setTriArcBoostOption(val) {
  closeTriArcOptionBubble();
  const v = Number(val);
  // Le mode Boost désactive le mode silence s'il est actif
  if (v > 0 && silenceModeState && silenceModeState.active) {
    await deactivateSilenceMode();
  }
  // Ne désactive pas les fonctions auto
  queueTriArcChanges([{ reg_key: "fun_pwf_set", value: v }]);
}

async function setTriArcSilenceHours(hours) {
  closeTriArcOptionBubble();
  if (hours <= 0) {
    await deactivateSilenceMode();
  } else {
    selectedSilenceHours = Number(hours);
    await confirmActivateSilence();
  }
}

function renderTriArcFloatingOptionBubble(T) {
  if (!triArcExpandedMode) return "";

  if (triArcExpandedMode === "auto") {
    return `
      <div class="bg-slate-900 border-2 border-cyan-400 rounded-3xl p-3.5 space-y-2.5 shadow-[0_10px_40px_rgba(0,0,0,0.9)]">
        <div class="flex items-center justify-between text-xs border-b border-slate-800 pb-2">
          <span class="font-bold text-cyan-300 flex items-center gap-1.5">🔥+${svgPropellerFanIcon("w-4 h-4", "#22d3ee")} Mode Auto (3 positions)</span>
          <button type="button" onclick="closeTriArcOptionBubble()" class="w-6 h-6 rounded-full bg-slate-800 text-slate-300 font-bold cursor-pointer">✕</button>
        </div>
        <div class="grid grid-cols-3 gap-2 text-xs font-bold">
          <button type="button" onclick="setTriArcAutoOption(0)" class="py-2.5 rounded-2xl border cursor-pointer ${T.autoIdx === 0 ? 'bg-slate-700 border-white text-white' : 'bg-slate-950 border-slate-800 text-slate-400'}">OFF</button>
          <button type="button" onclick="setTriArcAutoOption(1)" class="py-2.5 rounded-2xl border flex items-center justify-center gap-1 cursor-pointer ${T.autoIdx === 1 ? 'bg-cyan-600 border-cyan-300 text-white' : 'bg-slate-950 border-slate-800 text-slate-300'}">${svgPropellerFanIcon("w-4 h-4")}<span>Ventil</span></button>
          <button type="button" onclick="setTriArcAutoOption(2)" class="py-2.5 rounded-2xl border flex items-center justify-center gap-1 cursor-pointer ${T.autoIdx === 2 ? 'bg-orange-600 border-orange-300 text-white' : 'bg-slate-950 border-slate-800 text-slate-300'}"><span>🔥+</span>${svgPropellerFanIcon("w-4 h-4")}</button>
        </div>
        <div class="space-y-1.5 text-[11px] leading-tight text-slate-300 bg-slate-950/70 border border-slate-800 rounded-xl p-2.5">
          <p class="font-semibold text-cyan-300">Fonctionnement du mode Auto</p>
          <ul class="space-y-1.5 list-disc list-inside text-slate-300">
            <li><strong>OFF</strong> : Manuel intégral. Vous réglez la ventilation et la puissance de chauffe à votre convenance.</li>
            <li><strong>Ventil (${svgPropellerFanIcon("w-3.5 h-3.5 inline text-cyan-400")})</strong> : Ventilation modulée automatiquement selon l'écart entre la température ambiante et la consigne pour un confort silencieux.</li>
            <li><strong>Auto Total (🔥+Ventil)</strong> : Régulation 100% autonome conjointe de la flamme et de la ventilation par le poêle Nobis selon votre température cible.</li>
          </ul>
        </div>
      </div>
    `;
  }

  if (triArcExpandedMode === "boost") {
    return `
      <div class="bg-slate-900 border-2 border-rose-400 rounded-3xl p-3.5 space-y-2.5 shadow-[0_10px_40px_rgba(0,0,0,0.9)]">
        <div class="flex items-center justify-between text-xs border-b border-slate-800 pb-2">
          <span class="font-bold text-rose-300 flex items-center gap-1.5"><span class="text-base">🚀</span> Mode Boost (POWERFUL Nobis)</span>
          <button type="button" onclick="closeTriArcOptionBubble()" class="w-6 h-6 rounded-full bg-slate-800 text-slate-300 font-bold cursor-pointer">✕</button>
        </div>
        <div class="grid grid-cols-3 gap-2 text-xs font-bold">
          <button type="button" onclick="setTriArcBoostOption(0)" class="py-2.5 rounded-2xl border cursor-pointer ${T.pwfVal === 0 ? 'bg-slate-700 border-white text-white' : 'bg-slate-950 border-slate-800 text-slate-400'}">OFF</button>
          <button type="button" onclick="setTriArcBoostOption(1)" class="py-2.5 rounded-2xl border flex items-center justify-center gap-1 cursor-pointer ${T.pwfVal === 1 ? 'bg-rose-600 border-rose-300 text-white' : 'bg-slate-950 border-slate-800 text-slate-300'}"><span class="text-sm">🚀</span><span>ACTIF</span></button>
          <button type="button" onclick="setTriArcBoostOption(2)" class="py-2.5 rounded-2xl border flex items-center justify-center gap-1 cursor-pointer ${T.pwfVal === 2 ? 'bg-amber-600 border-amber-300 text-white' : 'bg-slate-950 border-slate-800 text-slate-300'}"><span class="text-sm">⏱️</span><span>PROG</span></button>
        </div>
        <div class="space-y-1.5 text-[11px] leading-tight text-slate-300 bg-slate-950/70 border border-slate-800 rounded-xl p-2.5">
          <p class="font-semibold text-rose-300">Fonctionnement du mode Powerful</p>
          <p>Dans l'environnement Nobis, cette fonction s'appelle <strong>POWERFUL</strong>. Elle enclenche temporairement la ventilation et la puissance de chauffe à leur niveau maximum pour réchauffer très rapidement la pièce, tout en conservant votre consigne habituelle de puissance (qui ne passe pas à 8).</p>
        </div>
        <div class="text-[11px] leading-tight text-amber-300/90 bg-amber-950/40 border border-amber-500/30 rounded-xl p-2 flex items-start gap-1.5">
          <span class="text-sm leading-none shrink-0">⚠️</span>
          <div>
            <p class="font-bold text-amber-200 mb-0.5">Chemin de programmation sur la télécommande :</p>
            <p><strong>Menu &gt; Réglages / Set Poêle &gt; Powerful &gt; Programmation horaire</strong></p>
            <p class="mt-1 text-[10px] text-amber-300/80">La programmation horaire ne peut pas être effectuée depuis l'application : elle doit obligatoirement être configurée depuis la télécommande physique. Merci Nobis !</p>
          </div>
        </div>
      </div>
    `;
  }

  if (triArcExpandedMode === "eco") {
    const isStandbyPhase = (T.st === 8 || T.st === 9);
    const phaseBadgeHtml = T.st === 8
      ? `<div class="py-2.5 px-3 rounded-2xl bg-cyan-950/80 border border-cyan-500 text-cyan-300 text-xs font-bold flex items-center justify-center gap-2 shadow">
          <span>⏳</span><span>PHASE ACTIVE : 8 = ECO STOP</span>
         </div>`
      : (T.st === 9
        ? `<div class="py-2.5 px-3 rounded-2xl bg-cyan-950/80 border border-cyan-500 text-cyan-300 text-xs font-bold flex items-center justify-center gap-2 shadow">
            <span>🌙</span><span>PHASE ACTIVE : 9 = STAND BY</span>
           </div>`
        : `<div class="py-2.5 px-3 rounded-2xl bg-slate-950 border border-slate-700 text-slate-300 text-xs font-bold flex items-center justify-center gap-2">
            <span class="text-slate-400">💤</span><span>ÉTAT POÊLE : VEILLE INACTIVE (${escapeHtml(T.phaseLabel)})</span>
           </div>`);

    const simControlsHtml = getActiveCommMode() === "simulation"
      ? `
        <div class="grid grid-cols-2 gap-2 text-xs font-bold pt-1">
          <button type="button" onclick="setTriArcEcoOption(0)" class="py-2.5 rounded-2xl border cursor-pointer ${!T.isEco ? 'bg-slate-700 border-white text-white' : 'bg-slate-950 border-slate-800 text-slate-400'}">OFF (Simulation)</button>
          <button type="button" onclick="setTriArcEcoOption(1)" class="py-2.5 rounded-2xl border flex items-center justify-center gap-1.5 cursor-pointer ${T.isEco ? 'bg-emerald-600 border-emerald-300 text-white' : 'bg-slate-950 border-slate-800 text-slate-300'}"><span class="text-sm">💤</span><span>ACTIF (Simulation)</span></button>
        </div>
      `
      : "";

    return `
      <div class="bg-slate-900 border-2 border-emerald-400 rounded-3xl p-3.5 space-y-2.5 shadow-[0_10px_40px_rgba(0,0,0,0.9)]">
        <div class="flex items-center justify-between text-xs border-b border-slate-800 pb-2">
          <span class="font-bold text-emerald-300 flex items-center gap-1.5"><span class="text-base">💤</span> Mode Éco (CONFORT CLIMA Nobis)</span>
          <button type="button" onclick="closeTriArcOptionBubble()" class="w-6 h-6 rounded-full bg-slate-800 text-slate-300 font-bold cursor-pointer">✕</button>
        </div>

        ${phaseBadgeHtml}
        ${simControlsHtml}

        <div class="space-y-1.5 text-[11px] leading-tight text-slate-300 bg-slate-950/70 border border-slate-800 rounded-xl p-2.5">
          <p class="font-semibold text-emerald-300">Fonctionnement Confort Clima (Eco-Stop)</p>
          <p>Le mode Éco correspond au <strong>Confort Clima</strong> de Nobis. Lorsque la température ambiante mesurée dépasse votre consigne cible, le poêle passe automatiquement en extinction thermique (<strong>ECO STOP</strong>) pour ne pas surchauffer et économiser les granulés. Dès que la température redescend sous la consigne (moins le delta configuré), le poêle redémarre tout seul (<strong>STAND BY</strong>).</p>
        </div>

        <div class="text-[11px] leading-tight text-amber-300/90 bg-amber-950/40 border border-amber-500/30 rounded-xl p-2.5 flex items-start gap-2">
          <span class="text-base leading-none shrink-0">ℹ️</span>
          <div>
            <p class="font-bold text-amber-200 mb-1">Activation et réglages sur la radiocommande Nobis :</p>
            <p class="text-white font-mono text-[11px] bg-black/40 px-2 py-1 rounded border border-amber-500/20 mb-1.5">
              MENU ▸ UTILISATEUR ▸ COMFORT CLIMA (ON / OFF)
            </p>
            <p class="text-[10px] text-amber-200/90 leading-normal">
              L'activation/désactivation de Comfort Clima ainsi que ses réglages (temporisation d'extinction et écart thermique Delta T°) se configurent <strong>exclusivement sur la radiocommande physique Nobis</strong>. Le protocole Cloud Agua IOT ne dispose d'aucun registre de commande à distance pour cette fonction. L'application supervise fidèlement l'état et détecte automatiquement vos phases réelles <strong>ECO STOP (8)</strong> et <strong>STAND BY (9)</strong>.
            </p>
          </div>
        </div>
      </div>
    `;
  }

  if (triArcExpandedMode === "silence") {
    const silActive = T.isSilActive && T.remSec > 0;
    const remStr = silActive ? formatSilenceCountdown(T.remSec) : "OFF";
    return `
      <div class="bg-slate-900 border-2 border-purple-400 rounded-3xl p-3.5 space-y-2.5 shadow-[0_10px_40px_rgba(0,0,0,0.9)]">
        <div class="flex items-center justify-between text-xs border-b border-slate-800 pb-2">
          <span class="font-bold text-purple-300">🤫 Timer Silence (${remStr})</span>
          <button type="button" onclick="closeTriArcOptionBubble()" class="w-6 h-6 rounded-full bg-slate-800 text-slate-300 font-bold cursor-pointer">✕</button>
        </div>
        <div class="grid grid-cols-5 gap-1.5 text-xs font-bold">
          <button type="button" onclick="setTriArcSilenceHours(0)" class="py-2.5 rounded-2xl border cursor-pointer ${!silActive ? 'bg-slate-700 border-white text-white' : 'bg-slate-950 border-slate-800 text-slate-400'}">OFF</button>
          <button type="button" onclick="setTriArcSilenceHours(1)" class="py-2.5 rounded-2xl border cursor-pointer bg-slate-950 border-slate-800 hover:border-purple-400 text-purple-200">1h</button>
          <button type="button" onclick="setTriArcSilenceHours(2)" class="py-2.5 rounded-2xl border cursor-pointer bg-purple-700/80 border-purple-300 text-white">2h</button>
          <button type="button" onclick="setTriArcSilenceHours(3)" class="py-2.5 rounded-2xl border cursor-pointer bg-slate-950 border-slate-800 hover:border-purple-400 text-purple-200">3h</button>
          <button type="button" onclick="setTriArcSilenceHours(4)" class="py-2.5 rounded-2xl border cursor-pointer bg-slate-950 border-slate-800 hover:border-purple-400 text-purple-200">4h</button>
        </div>
        <div class="space-y-1.5 text-[11px] leading-tight text-slate-300 bg-slate-950/70 border border-slate-800 rounded-xl p-2.5">
          <p class="font-semibold text-purple-300">Fonctionnement intelligent du mode Silence</p>
          <p>• <strong>Si poêle éteint :</strong> toutes les consignes passent immédiatement en <strong>hors gel (7°C)</strong>.</p>
          <p>• <strong>Si poêle allumé :</strong> désactivation du <strong>Boost</strong>, du <strong>Mode Éco</strong> et de la <strong>programmation (Chrono)</strong>, activation du <strong>Mode Auto total</strong> et passage des consignes en <strong>hors gel (7°C)</strong>. Le poêle bascule ainsi automatiquement en puissance minimale <strong>P0</strong> et ventilation <strong>Ventil 0</strong> (convection 100% silencieuse) <strong>sans s'éteindre</strong>.</p>
          <p class="text-slate-400">À la fin du décompte programmé ou lors d'une modification manuelle, les consignes et réglages d'origine sont automatiquement restaurés.</p>
        </div>
      </div>
    `;
  }
  return "";
}

function renderTriArcCockpit() {
  const track = document.getElementById("triarc-carousel-track");
  if (!track) return;

  const T = getTriArcTelemetryState();
  const targetZones = triArcDragging ? [triArcDragging.zone] : ["SX", "AMB", "DX"];

  targetZones.forEach(z => {
    const slideEl = document.getElementById(`triarc-slide-${z}`);
    if (slideEl) {
      slideEl.innerHTML = `
        <div class="shrink-0 w-full">
          ${renderTriArcTopHeader(z, T)}
        </div>
        <div class="flex-1 min-h-0 flex flex-col justify-center items-center gap-1 sm:gap-2 my-auto w-full">
          <div class="w-full flex-1 min-h-0 max-h-[300px] sm:max-h-[350px] flex items-center justify-center">
            ${renderTriArcSvgForZone(z, T)}
          </div>
          <div class="w-full shrink-0">
            ${renderTriArcDailyChronoFrise(z)}
          </div>
        </div>
        <div class="shrink-0 w-full pb-0.5">
          ${renderTriArcBottomModes(T)}
        </div>
      `;
    }
  });

  const overlay = document.getElementById("triarc-floating-overlay");
  const bubble = document.getElementById("triarc-floating-bubble");
  if (overlay && bubble) {
    if (triArcExpandedMode) {
      overlay.classList.remove("hidden");
      bubble.innerHTML = renderTriArcFloatingOptionBubble(T);
    } else {
      overlay.classList.add("hidden");
      bubble.innerHTML = "";
    }
  }

  const offset = triArcZone === "SX" ? "0%" : (triArcZone === "AMB" ? "-100%" : "-200%");
  track.style.transform = `translateX(${offset})`;

  const dSx = document.getElementById("triarc-dot-sx");
  const dAmb = document.getElementById("triarc-dot-amb");
  const dDx = document.getElementById("triarc-dot-dx");
  if (dSx && dAmb && dDx) {
    dSx.className = triArcZone === "SX" ? "w-4 h-2 rounded-full bg-cyan-400 transition-all cursor-pointer" : "w-2 h-2 rounded-full bg-slate-700 transition-all cursor-pointer";
    dAmb.className = triArcZone === "AMB" ? "w-4 h-2 rounded-full bg-orange-500 transition-all cursor-pointer" : "w-2 h-2 rounded-full bg-slate-700 transition-all cursor-pointer";
    dDx.className = triArcZone === "DX" ? "w-4 h-2 rounded-full bg-sky-400 transition-all cursor-pointer" : "w-2 h-2 rounded-full bg-slate-700 transition-all cursor-pointer";
  }
}

function updateTriArcValueFromPoint(svgEl, arcType, clientX, clientY) {
  const rect = svgEl.getBoundingClientRect();
  const x = 14 + (clientX - rect.left) * (292 / rect.width);
  const y = 20 + (clientY - rect.top) * (198 / rect.height);
  const dx = x - 160;
  const dy = y - 128;
  let deg = (Math.atan2(dy, dx) * 180) / Math.PI; // -180..180

  const zone = svgEl.dataset.zone || triArcZone;
  const T = getTriArcTelemetryState();
  const fanSp = zone === "AMB" ? T.fanAmb : (zone === "SX" ? T.fanSx : T.fanDx);

  if (arcType === "temp") {
    let a = deg < 0 ? deg + 360 : deg;
    if (a < 90) a += 360;
    a = Math.max(135, Math.min(405, a));
    return triArcAngleToTemp(a);
  } else if (arcType === "fan") {
    // Si la ventilation est en mode Auto, l'arc n'est pas cliquable/modifiable
    const isFanAuto = (T.autoIdx === 1 || T.autoIdx === 2 || fanSp === 8);
    if (isFanAuto) return null;

    let a = deg < 0 ? deg + 360 : deg;
    a = Math.max(142, Math.min(218, a));
    return Math.round(((a - 142) / 76) * 8);
  } else if (arcType === "pwr") {
    // Si la puissance est en mode Auto complet (2) ou s'il s'agit d'un écran canalisation, l'arc n'est pas cliquable
    if (zone === "SX" || zone === "DX" || T.autoIdx === 2) return null;

    let a = Math.max(-38, Math.min(38, deg));
    const rawVal = Math.round(((38 - a) / 76) * 8);
    // Le niveau 8 (Boost) n'est pas sélectionnable avec le curseur (plage consigne 0..7)
    return Math.min(7, Math.max(0, rawVal));
  }
  return null;
}

function commitTriArcDrag(dragState) {
  if (!dragState) return;
  const { zone, arcType, value } = dragState;
  const v = Number(value);

  if (arcType === "temp") {
    const key = zone === "AMB" ? "temp_amb_setpoint" : (zone === "SX" ? "duct_left_setpoint" : "duct_right_setpoint");
    queueTriArcChanges([{ reg_key: key, value: v }]);
  } else if (arcType === "fan") {
    const key = zone === "AMB" ? "fan_front_speed" : (zone === "SX" ? "duct_left_speed" : "duct_right_speed");
    const changes = [{ reg_key: key, value: v }];
    queueTriArcChanges(changes);
  } else if (arcType === "pwr" && zone === "AMB") {
    if (v === 8) {
      queueTriArcChanges([{ reg_key: "fun_pwf_set", value: 1 }]);
    } else if (v === 0) {
      renderTriArcCockpit();
      showToast("P0 (Éco) est un ralenti automatique enclenché lorsque la température atteint la consigne.");
    } else {
      const changes = [{ reg_key: "power_level", value: v }];
      const pwfCur = Number(getTriArcLiveOrOpt("fun_pwf_set", getRegVal("fun_pwf_set", latestData.fun_pwf_set ?? 0)));
      if (pwfCur > 0) {
        changes.unshift({ reg_key: "fun_pwf_set", value: 0 });
      }
      // Ne désactive pas les fonctions auto lors du changement de puissance
      queueTriArcChanges(changes);
    }
  }
}

function initTriArcInteractions() {
  const vp = document.getElementById("triarc-carousel-viewport");
  if (!vp) return;

  // Gestion du glisser sur les arcs (consigne température, ventilation, puissance)
  window.addEventListener("pointerdown", (e) => {
    const hit = e.target.closest ? e.target.closest(".arc-hit") : null;
    if (!hit) return;
    const svg = hit.closest("svg");
    if (!svg) return;
    e.preventDefault();
    const arcType = hit.dataset.arc;
    const zone = svg.dataset.zone || triArcZone;
    const val = updateTriArcValueFromPoint(svg, arcType, e.clientX, e.clientY);
    if (val !== null) {
      triArcDragging = { zone, arcType, value: val };
      renderTriArcCockpit();
    }
  });

  window.addEventListener("pointermove", (e) => {
    if (!triArcDragging) return;
    const svg = document.getElementById(`tri-arc-svg-${triArcDragging.zone}`);
    if (!svg) return;
    const val = updateTriArcValueFromPoint(svg, triArcDragging.arcType, e.clientX, e.clientY);
    if (val !== null && val !== triArcDragging.value) {
      triArcDragging.value = val;
      renderTriArcCockpit();
    }
  });

  window.addEventListener("pointerup", () => {
    if (!triArcDragging) return;
    const finished = { ...triArcDragging };
    triArcDragging = null;
    commitTriArcDrag(finished);
  });
}

window.addEventListener("DOMContentLoaded", () => {
  try { initThreePaneCockpit(); } catch (e) { console.error("[Init] ThreePaneCockpit:", e); }
  try { initTriArcInteractions(); } catch (e) { console.error("[Init] TriArcInteractions:", e); }
  try { renderTriArcCockpit(); } catch (e) { console.error("[Init] RenderTriArc:", e); }
  try { switchTab("tab-control"); } catch (e) { console.error("[Init] SwitchTab:", e); }
  try { updateDynamicPowerButton(); } catch (e) { console.error("[Init] DynamicPowerButton:", e); }
  const settingsPane = document.getElementById("subtab-pane-settings");
  if (settingsPane) {
    const markSettingsDirty = (ev) => {
      if (ev.target && ev.target.id && ev.target.id.startsWith("cfg-")) {
        isSettingsFormDirty = true;
      }
    };
    settingsPane.addEventListener("input", markSettingsDirty);
    settingsPane.addEventListener("change", markSettingsDirty);
  }
  loadDashboard();
  initSSEStream();
  try { refreshMatterStatus(false); } catch (e) { console.error("[Init] MatterStatus:", e); }
  // Filet de sécurité léger toutes les 60s si l'onglet navigateur a été mis en veille
  setInterval(refreshLatestOnly, 60000);
  // Rafraîchissement précis 1 Hz du compte à rebours Mode Silence ! dans le bouton
  setInterval(updateSilenceCountdownDisplay, 1000);
});
