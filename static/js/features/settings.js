// =============================================================================
// FEATURE PARAMÈTRES — ACCORDÉONS, CANALISATIONS, LOGS & ADMINISTRATION SYSTÈME
// =============================================================================

let currentInstallerSubTab = null;
let openSettingsAccId = null;

function toggleSettingsAccordion(accId, forceOpen = false) {
  const targetBody = document.getElementById("acc-body-" + accId);
  if (!targetBody) return;

  const isAlreadyOpen = !targetBody.classList.contains("hidden");
  if (isAlreadyOpen && !forceOpen) {
    targetBody.classList.add("hidden");
    const arrow = document.getElementById("acc-arrow-" + accId);
    if (arrow) arrow.classList.remove("rotate-180");
    openSettingsAccId = null;
    return;
  }

  // Réduit tous les autres blocs (un seul bloc déplié à la fois)
  document.querySelectorAll(".settings-acc-body").forEach((el) => el.classList.add("hidden"));
  document.querySelectorAll(".settings-acc-arrow").forEach((el) => el.classList.remove("rotate-180"));

  targetBody.classList.remove("hidden");
  const arrow = document.getElementById("acc-arrow-" + accId);
  if (arrow) arrow.classList.add("rotate-180");
  openSettingsAccId = accId;
  currentInstallerSubTab = accId;

  if (accId === "expert") {
    checkInstallerAuthStatus();
    renderEepromTable();
    loadSnapshots();
    renderHardwareMasks();
  } else if (accId === "logs") {
    loadLogs(currentLogSeverity);
    loadAudit();
  } else if (accId === "tuning") {
    const trimPelletInp = document.getElementById("inst-trim-pellet");
    if (trimPelletInp && document.activeElement !== trimPelletInp) {
      trimPelletInp.value = getRegVal("trim_pellet_pct", 0);
    }
    const trimExhaustInp = document.getElementById("inst-trim-exhaust");
    if (trimExhaustInp && document.activeElement !== trimExhaustInp) {
      trimExhaustInp.value = getRegVal("trim_exhaust_pct", 0);
    }
  } else if (accId === "conn") {
    updateConfigModalFields();
  } else if (accId === "zones") {
    updateDuctChevronPreview();
  }
}

function filterSettingsAccordions(query) {
  const q = String(query || "").trim().toLowerCase();
  const clearBtn = document.getElementById("settings-acc-search-clear");
  if (clearBtn) clearBtn.classList.toggle("hidden", !q);

  const items = Array.from(document.querySelectorAll(".settings-acc-item"));
  let visibleCount = 0;
  let firstVisibleId = null;

  items.forEach((item) => {
    const kw = (item.dataset.keywords || "").toLowerCase();
    const txt = (item.textContent || "").toLowerCase();
    const match = !q || kw.includes(q) || txt.includes(q);
    item.classList.toggle("hidden", !match);
    if (match) {
      visibleCount++;
      if (!firstVisibleId) firstVisibleId = item.dataset.accId;
    }
  });

  const emptyEl = document.getElementById("settings-acc-empty");
  if (emptyEl) emptyEl.classList.toggle("hidden", visibleCount > 0);

  if (q && firstVisibleId) {
    toggleSettingsAccordion(firstVisibleId, true);
  } else if (!q) {
    if (openSettingsAccId) {
      toggleSettingsAccordion(openSettingsAccId, true);
    } else {
      document.querySelectorAll(".settings-acc-body").forEach((el) => el.classList.add("hidden"));
      document.querySelectorAll(".settings-acc-arrow").forEach((el) => el.classList.remove("rotate-180"));
    }
  }
}

function updateDuctChevronPreview() {
  const leftInp = document.getElementById("cfg-duct-left-name");
  const rightInp = document.getElementById("cfg-duct-right-name");
  const sx = (leftInp && leftInp.value.trim()) ? leftInp.value.trim() : getDuctName("SX");
  const dx = (rightInp && rightInp.value.trim()) ? rightInp.value.trim() : getDuctName("DX");

  const prevSx = document.getElementById("preview-chevron-sx-name");
  const prevDx = document.getElementById("preview-chevron-dx-name");
  if (prevSx) prevSx.textContent = sx;
  if (prevDx) prevDx.textContent = dx;

  const sumZones = document.getElementById("acc-sum-zones");
  if (sumZones) sumZones.textContent = `${sx} • ${dx}`;

  // Hydrate les valeurs télémétriques des chevrons simulés avec les données réelles du poêle
  const realSx = latestData && latestData.duct_left_temp != null ? Number(latestData.duct_left_temp).toFixed(1) : "17.0";
  const spSx = getRegVal("duct_left_setpoint", 20);
  const fanSx = getRegVal("duct_left_speed", 8);
  const realDx = latestData && latestData.duct_right_temp != null ? Number(latestData.duct_right_temp).toFixed(1) : "17.0";
  const spDx = getRegVal("duct_right_setpoint", 20);
  const fanDx = getRegVal("duct_right_speed", 8);

  const fmtFan = (v) => (Number(v) === 0 ? "OFF" : Number(v) >= 8 ? "AUTO" : `V${v}`);
  const fmtSp = (v) => (Number(v) >= 41 ? "MAX" : `${v}°`);

  const sxFanEl = document.getElementById("preview-chevron-sx-fan");
  const sxTempEl = document.getElementById("preview-chevron-sx-temp");
  const dxFanEl = document.getElementById("preview-chevron-dx-fan");
  const dxTempEl = document.getElementById("preview-chevron-dx-temp");

  if (sxFanEl) sxFanEl.textContent = fmtFan(fanSx);
  if (sxTempEl) sxTempEl.innerHTML = `${escapeHtml(realSx)}° <span class="text-cyan-400">→</span> <span class="text-cyan-200">${escapeHtml(fmtSp(spSx))}</span>`;
  if (dxFanEl) dxFanEl.textContent = fmtFan(fanDx);
  if (dxTempEl) dxTempEl.innerHTML = `${escapeHtml(realDx)}° <span class="text-sky-400">→</span> <span class="text-sky-200">${escapeHtml(fmtSp(spDx))}</span>`;
}

function resetDuctNamesDefault() {
  const leftInp = document.getElementById("cfg-duct-left-name");
  const rightInp = document.getElementById("cfg-duct-right-name");
  if (leftInp) leftInp.value = "SX";
  if (rightInp) rightInp.value = "DX";
  isSettingsFormDirty = true;
  updateDuctChevronPreview();
}

function locateWeatherFromBrowser() {
  if (!navigator.geolocation) {
    showToast("La géolocalisation n'est pas supportée par ce navigateur.", true);
    return;
  }
  const btn = document.getElementById("btn-geolocate-weather");
  const origHtml = btn ? btn.innerHTML : "";
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = "<span>⏳</span><span>Localisation...</span>";
  }

  const restoreBtn = () => {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = origHtml || "<span>📍</span><span>Me localiser</span>";
    }
  };

  navigator.geolocation.getCurrentPosition(
    async (pos) => {
      const lat = pos.coords.latitude;
      const lon = pos.coords.longitude;
      const latEl = document.getElementById("cfg-lat");
      const lonEl = document.getElementById("cfg-lon");
      const cityEl = document.getElementById("cfg-city");

      if (latEl) latEl.value = lat.toFixed(4);
      if (lonEl) lonEl.value = lon.toFixed(4);
      isSettingsFormDirty = true;

      let resolvedCity = "";
      try {
        const res = await fetch(`/api/reverse-geocode?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}`);
        if (res.ok) {
          const data = await res.json();
          if (data && data.city) {
            resolvedCity = data.city;
            if (cityEl) cityEl.value = resolvedCity;
          }
        }
      } catch (e) {
        // Repli silencieux : coordonnées GPS déjà renseignées
      }

      updateSettingsAccordionSummaries();
      restoreBtn();
      if (resolvedCity) {
        showToast(`📍 Position détectée : ${resolvedCity} — cliquez sur Enregistrer.`);
      } else {
        showToast(`📍 Coordonnées détectées (${lat.toFixed(4)}, ${lon.toFixed(4)}) — cliquez sur Enregistrer.`);
      }
    },
    (err) => {
      restoreBtn();
      let msg = "Impossible d'obtenir votre position.";
      if (err && err.code === 1) {
        msg = "Permission de géolocalisation refusée dans le navigateur (HTTPS ou localhost requis).";
      } else if (err && err.code === 2) {
        msg = "Position GPS indisponible pour le moment.";
      } else if (err && err.code === 3) {
        msg = "Délai d'attente dépassé pour la géolocalisation.";
      }
      showToast(msg, true);
    },
    { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 }
  );
}

function updateSettingsAccordionSummaries() {
  // 1. Zones SX / DX
  updateDuctChevronPreview();

  // 2. Liaison Poêle
  const modeEl = document.getElementById("cfg-mode");
  const intEl = document.getElementById("cfg-interval");
  const modeVal = modeEl ? modeEl.value : (currentSettings.comm_mode || "agua_cloud");
  const modeTxt = modeVal === "local_gateway" ? "🔌 UART ESP32" : (modeVal === "simulation" ? "🧪 Simulation" : "☁️ Agua Cloud");
  const intVal = intEl && intEl.value ? intEl.value : (currentSettings.poll_interval_s || "120");
  const sumConn = document.getElementById("acc-sum-conn");
  if (sumConn) sumConn.textContent = `${modeTxt} • ${intVal}s`;

  // 3. Matter & Webhook
  const mEnEl = document.getElementById("cfg-matter-enabled");
  const mPortEl = document.getElementById("cfg-matter-port");
  const whEl = document.getElementById("cfg-webhook");
  const mEn = mEnEl ? mEnEl.value === "1" : String(currentSettings.matter_enabled ?? "1") === "1";
  const mPort = (mPortEl && mPortEl.value) || currentSettings.matter_port || "5540";
  const hasWh = Boolean((whEl ? whEl.value : currentSettings.webhook_url || "").trim());
  const sumDom = document.getElementById("acc-sum-domotic");
  if (sumDom) sumDom.textContent = `${mEn ? `✅ Matter Actif (${mPort})` : "❌ Matter OFF"} • ${hasWh ? "🔔 Webhook actif" : "🔕 Sans webhook"}`;

  // 4. Granulés & Météo
  const bagsEl = document.getElementById("cfg-bags-stock");
  const priceEl = document.getElementById("cfg-bag-price");
  const cityEl = document.getElementById("cfg-city");
  const bags = (bagsEl && bagsEl.value !== "") ? bagsEl.value : (currentSettings.pellet_bags_stock ?? "40");
  const price = (priceEl && priceEl.value !== "") ? priceEl.value : (currentSettings.pellet_bag_price_eur || "4.80");
  const city = ((cityEl && cityEl.value) || currentSettings.weather_city || "--").split("(")[0].trim();
  const sumHome = document.getElementById("acc-sum-home");
  if (sumHome) sumHome.textContent = `${bags} sacs (${price} €) • ${city}`;

  // 5. Tarages IA
  const tPel = getRegVal("trim_pellet_pct", 0);
  const tExh = getRegVal("trim_exhaust_pct", 0);
  const sumTuning = document.getElementById("acc-sum-tuning");
  if (sumTuning) sumTuning.textContent = `Vis ${tPel}% • Fumées ${tExh}%`;

  // 6. Système
  const rtcEl = document.getElementById("inst-clock-val");
  const verEl = document.getElementById("cfg-docker-version");
  const rtc = rtcEl ? rtcEl.textContent.trim() : "--:--";
  const ver = verEl ? verEl.textContent.trim() : "v2.1.2";
  const sumSys = document.getElementById("acc-sum-system");
  if (sumSys) sumSys.textContent = `RTC ${rtc} • Docker ${ver}`;

  // 7. Expert
  const sumExp = document.getElementById("acc-sum-expert");
  if (sumExp) {
    sumExp.textContent = installerUnlocked
      ? "Session Expert active • Snapshots, RAM/EEPROM, Injecteur"
      : "Verrouillé • Snapshots, RAM/EEPROM, Injecteur";
  }
}

function applyDuctNamesAcrossUI() {
  syncSeriesDuctLabels();
  const sx = getDuctName("SX");
  const dx = getDuctName("DX");

  const dotSx = document.getElementById("triarc-dot-sx");
  const dotDx = document.getElementById("triarc-dot-dx");
  if (dotSx) dotSx.title = `Canalisation Gauche (${sx})`;
  if (dotDx) dotDx.title = `Canalisation Droite (${dx})`;

  const silDucts = document.getElementById("silence-modal-ducts-label");
  if (silDucts) silDucts.textContent = `Canalisations ${sx} & ${dx}`;
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

// Gestion de la Configuration avec ouverture automatique du bon accordéon si un champ précis est ciblé
function openConfigModal(highlightFieldId = null) {
  switchTab("tab-installer");

  if (highlightFieldId) {
    let targetAcc = "zones";
    if (["cfg-city", "cfg-lat", "cfg-lon", "cfg-bags-stock", "cfg-bag-price"].includes(highlightFieldId)) {
      targetAcc = "home";
    } else if (["cfg-pass", "cfg-email", "cfg-mode", "cfg-gw", "cfg-interval"].includes(highlightFieldId)) {
      targetAcc = "conn";
    } else if (["cfg-matter-enabled", "cfg-matter-name", "cfg-matter-port", "cfg-webhook"].includes(highlightFieldId)) {
      targetAcc = "domotic";
    } else if (["cfg-duct-left-name", "cfg-duct-right-name"].includes(highlightFieldId)) {
      targetAcc = "zones";
    }
    toggleSettingsAccordion(targetAcc, true);
  }

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
    matter_port: document.getElementById("cfg-matter-port") ? document.getElementById("cfg-matter-port").value : "5540",
    duct_left_name: document.getElementById("cfg-duct-left-name") ? (document.getElementById("cfg-duct-left-name").value.trim() || "SX") : "SX",
    duct_right_name: document.getElementById("cfg-duct-right-name") ? (document.getElementById("cfg-duct-right-name").value.trim() || "DX") : "DX"
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
  applyDuctNamesAcrossUI();
  updateSettingsAccordionSummaries();
  if (connectionState && connectionState.commStatus) {
    connectionState.commStatus.comm_mode = mode;
  }
  currentCommMode = mode;
  closeConfigModal();
  await loadDashboard();
  if (typeof renderTriArcCockpit === "function") renderTriArcCockpit();
  if (typeof renderChronoCards === "function") renderChronoCards();
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
