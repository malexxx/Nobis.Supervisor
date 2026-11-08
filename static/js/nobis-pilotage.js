const NobisUI = (() => {
  const FAN_STEPS = ['OFF', '1', '2', '3', '4', '5', '6', '7', 'PS'];
  const STORAGE_KEY = 'nobis_layout_mode';
  let debounceTimer = null;
  let onCommandCallback = null;

  const state = {
    // Uniquement 'h-focus' ou 'v-cockpit'
    layout: localStorage.getItem(STORAGE_KEY) === 'v-cockpit' ? 'v-cockpit' : 'h-focus',
    activeZone: 'salon',
    power: { real: 1, set: 8 },
    zones: {
      salon: { id: 'salon', name: '🏠 Salon',     color: '#10b981', tempReal: 19, tempSet: 21, fanReal: 0, fanSet: 0 },
      sx:    { id: 'sx',    name: '⬅️ Canal. SX', color: '#38bdf8', tempReal: 0,  tempSet: 11, fanReal: 0, fanSet: 0 },
      dx:    { id: 'dx',    name: '➡️ Canal. DX', color: '#60a5fa', tempReal: 0,  tempSet: 10, fanReal: 0, fanSet: 0 }
    }
  };

  function formatTemp(t) {
    if (t <= 6) return '❄️ H-Gel';
    if (t >= 31) return 'MAX 🔥';
    return t + ' °C';
  }

  // Envoi différé au poêle (évite de saturer l'API pendant un glissement au doigt)
  function emitCommand(payload) {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      debounceTimer = null;
      if (typeof onCommandCallback === 'function') {
        onCommandCallback(payload);
      }
    }, 350);
  }

  function setLayoutMode(mode) {
    if (mode !== 'h-focus' && mode !== 'v-cockpit') return;
    state.layout = mode;
    localStorage.setItem(STORAGE_KEY, mode);
    syncConfigButtons();
    render();
  }

  function syncConfigButtons() {
    document.querySelectorAll('[data-nb-mode]').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.nbMode === state.layout);
    });
  }

  function selectZone(zId) {
    if (!state.zones[zId]) return;
    state.activeZone = zId;
    render();
  }

  function setZoneTemp(zId, val) {
    if (!state.zones[zId]) return;
    const clamped = Math.max(6, Math.min(31, Math.round(val)));
    if (state.zones[zId].tempSet === clamped) return;
    state.zones[zId].tempSet = clamped;
    render();
    emitCommand({ type: 'temp', zone: zId, value: clamped });
  }

  function setZoneFan(zId, val) {
    if (!state.zones[zId]) return;
    const clamped = Math.max(0, Math.min(8, parseInt(val, 10)));
    if (state.zones[zId].fanSet === clamped) return;
    state.zones[zId].fanSet = clamped;
    render();
    emitCommand({ type: 'fan', zone: zId, value: clamped, label: FAN_STEPS[clamped] });
  }

  function setPower(val) {
    const clamped = Math.max(0, Math.min(8, parseInt(val, 10)));
    if (state.power.set === clamped) return;
    state.power.set = clamped;
    render();
    emitCommand({ type: 'power', value: clamped });
  }

  function buildHeaderTabsHTML() {
    return `
      <div class="nb-legend">
        <div class="nb-leg-i"><span class="nb-swatch nb-sw-real"></span> État Réel</div>
        <div class="nb-leg-i"><span class="nb-swatch nb-sw-mod"></span> Modulation Auto</div>
        <div class="nb-leg-i"><span class="nb-swatch nb-sw-set"></span> Consigne</div>
      </div>
      <div class="nb-zone-tabs">
        ${['salon', 'sx', 'dx'].map(k => {
          const z = state.zones[k];
          const tCible = z.tempSet <= 6 ? '❄️' : (z.tempSet >= 31 ? 'MAX' : z.tempSet + '°');
          return `
            <div class="nb-z-tab ${state.activeZone === k ? 'active' : ''}" data-z="${k}" onclick="NobisUI.selectZone('${k}')">
              <div class="nb-z-title">${z.name}</div>
              <div class="nb-z-sub"><strong>${z.tempReal}°</strong>→${tCible} | ${FAN_STEPS[z.fanSet]}</div>
            </div>
          `;
        }).join('')}
      </div>
    `;
  }

  // Rendu Mode 1 : Horizontal Focus
  function buildHorizontalFocusHTML() {
    const z = state.zones[state.activeZone];
    const realPct = Math.max(0, Math.min(100, ((z.tempReal - 6) / 25) * 100));
    const setPct  = Math.max(0, Math.min(100, ((z.tempSet - 6) / 25) * 100));
    const modLeft = Math.min(realPct, setPct);
    const modW    = Math.abs(setPct - realPct);

    let fanCells = '';
    for (let i = 0; i <= 8; i++) {
      const isReal = i === z.fanReal;
      const isSet  = i === z.fanSet;
      const isMod  = (i > z.fanReal && i <= z.fanSet) || (i < z.fanReal && i >= z.fanSet);
      const cls = ['nb-seg-cell', isReal ? 'real' : (isMod ? 'mod' : ''), isSet ? 'set' : ''].join(' ');
      const tag = isReal && isSet ? 'R=C' : (isReal ? 'RÉEL' : (isSet ? 'CIBLE' : ''));
      fanCells += `<div class="${cls}"><span>${FAN_STEPS[i]}</span>${tag ? `<span class="nb-seg-tag">${tag}</span>` : ''}</div>`;
    }

    let pwrCells = '';
    for (let i = 0; i <= 8; i++) {
      const isReal = i === state.power.real;
      const isSet  = i === state.power.set;
      const isMod  = (i > state.power.real && i <= state.power.set) || (i < state.power.real && i >= state.power.set);
      const cls = ['nb-seg-cell', isReal ? 'real' : (isMod ? 'mod' : ''), isSet ? 'set' : ''].join(' ');
      const tag = isReal && isSet ? '🔥=🎯' : (isReal ? 'RÉEL' : (isSet ? 'CIBLE' : ''));
      pwrCells += `<div class="${cls}"><span>${i}</span>${tag ? `<span class="nb-seg-tag">${tag}</span>` : ''}</div>`;
    }

    return `
      ${buildHeaderTabsHTML()}
      <!-- Carte Zone Active (Température + Ventilation) -->
      <section class="nb-h-card" style="--accent:${z.color}">
        <div class="nb-h-head">
          <div>
            <div class="nb-h-title">${z.name}</div>
            <div class="nb-h-sub">
              Réel : <strong>${z.tempReal} °C</strong> • Ventil. réelle : <strong style="color:${z.color}">${FAN_STEPS[z.fanReal]}</strong>
            </div>
          </div>
          <div class="nb-stepper">
            <button type="button" class="nb-btn-pm" onclick="NobisUI.setZoneTemp('${z.id}', ${z.tempSet - 1})">−</button>
            <div class="nb-step-val"><small>Consigne</small>${formatTemp(z.tempSet)}</div>
            <button type="button" class="nb-btn-pm" onclick="NobisUI.setZoneTemp('${z.id}', ${z.tempSet + 1})">+</button>
          </div>
        </div>

        <div class="nb-temp-row">
          <button type="button" class="nb-bound-btn frost ${z.tempSet <= 6 ? 'active' : ''}" onclick="NobisUI.setZoneTemp('${z.id}', 6)">❄️ H-Gel</button>
          <div class="nb-h-temp-track" data-h-temp="${z.id}">
            <div class="nb-h-temp-rail">
              <div class="nb-h-temp-real" style="width:${realPct}%"></div>
              <div class="nb-h-temp-mod" style="left:${modLeft}%; width:${modW}%"></div>
              <div class="nb-h-temp-thumb" style="left:${setPct}%"></div>
            </div>
          </div>
          <button type="button" class="nb-bound-btn max ${z.tempSet >= 31 ? 'active' : ''}" onclick="NobisUI.setZoneTemp('${z.id}', 31)">MAX 🔥</button>
        </div>

        <div>
          <div class="nb-seg-header">
            <span>💨 Ventilation (Réel : <strong>${FAN_STEPS[z.fanReal]}</strong>)</span>
            <span>Consigne : <strong>${FAN_STEPS[z.fanSet]}</strong></span>
          </div>
          <div class="nb-h-seg-bar" data-h-fan="${z.id}">${fanCells}</div>
        </div>
      </section>

      <!-- Carte Puissance (0 à 8) -->
      <section class="nb-h-card" style="--accent:var(--nb-power)">
        <div class="nb-h-head">
          <div>
            <div class="nb-h-title">🔥 Puissance (0 à 8)</div>
            <div class="nb-h-sub">
              État réel : <strong style="color:var(--nb-power)">P${state.power.real}</strong> • Consigne Auto : <strong>P${state.power.set}</strong>
            </div>
          </div>
          <div class="nb-stepper">
            <button type="button" class="nb-btn-pm" onclick="NobisUI.setPower(${state.power.set - 1})">−</button>
            <div class="nb-step-val" style="color:var(--nb-power)"><small>Consigne</small>P${state.power.set}</div>
            <button type="button" class="nb-btn-pm" onclick="NobisUI.setPower(${state.power.set + 1})">+</button>
          </div>
        </div>
        <div class="nb-h-seg-bar" data-h-power="true">${pwrCells}</div>
      </section>
    `;
  }

  // Rendu Mode 2 : Cockpit Vertical (3 colonnes)
  function buildVerticalCockpitHTML() {
    const z = state.zones[state.activeZone];
    const realPct = Math.max(0, Math.min(100, ((z.tempReal - 6) / 25) * 100));
    const setPct  = Math.max(0, Math.min(100, ((z.tempSet - 6) / 25) * 100));

    let vFanCells = '';
    for (let i = 8; i >= 0; i--) {
      const isReal = i === z.fanReal;
      const isSet  = i === z.fanSet;
      const isMod  = (i > z.fanReal && i <= z.fanSet) || (i < z.fanReal && i >= z.fanSet);
      const cls = ['nb-seg-cell', isReal ? 'real' : (isMod ? 'mod' : ''), isSet ? 'set' : ''].join(' ');
      vFanCells += `<div class="${cls}">${FAN_STEPS[i]}</div>`;
    }

    let vPwrCells = '';
    for (let i = 8; i >= 0; i--) {
      const isReal = i === state.power.real;
      const isSet  = i === state.power.set;
      const isMod  = (i > state.power.real && i <= state.power.set) || (i < state.power.real && i >= state.power.set);
      const cls = ['nb-seg-cell', isReal ? 'real' : (isMod ? 'mod' : ''), isSet ? 'set' : ''].join(' ');
      vPwrCells += `<div class="${cls}">${i}</div>`;
    }

    return `
      ${buildHeaderTabsHTML()}
      <div class="nb-v-grid">
        <div class="nb-v-col" style="--accent:${z.color}">
          <div class="nb-v-title">${z.name}</div>
          <div class="nb-v-real">Réel : <strong>${z.tempReal}°C</strong></div>
          <div class="nb-v-stepper">
            <button type="button" class="nb-v-btn" onclick="NobisUI.setZoneTemp('${z.id}', ${z.tempSet - 1})">−</button>
            <span class="nb-v-val">${formatTemp(z.tempSet)}</span>
            <button type="button" class="nb-v-btn" onclick="NobisUI.setZoneTemp('${z.id}', ${z.tempSet + 1})">+</button>
          </div>
          <div class="nb-v-temp-wrap">
            <button type="button" class="nb-bound-btn max ${z.tempSet >= 31 ? 'active' : ''}" onclick="NobisUI.setZoneTemp('${z.id}', 31)">MAX 🔥</button>
            <div class="nb-v-temp-track" data-v-temp="${z.id}">
              <div class="nb-v-temp-real" style="height:${realPct}%"></div>
              <div class="nb-v-temp-thumb" style="bottom:${setPct}%"></div>
            </div>
            <button type="button" class="nb-bound-btn frost ${z.tempSet <= 6 ? 'active' : ''}" onclick="NobisUI.setZoneTemp('${z.id}', 6)">❄️ H-Gel</button>
          </div>
        </div>

        <div class="nb-v-col" style="--accent:${z.color}">
          <div class="nb-v-title">💨 Ventil.</div>
          <div class="nb-v-real">Réel : <strong>${FAN_STEPS[z.fanReal]}</strong></div>
          <div class="nb-v-stepper">
            <button type="button" class="nb-v-btn" onclick="NobisUI.setZoneFan('${z.id}', ${z.fanSet - 1})">−</button>
            <span class="nb-v-val">${FAN_STEPS[z.fanSet]}</span>
            <button type="button" class="nb-v-btn" onclick="NobisUI.setZoneFan('${z.id}', ${z.fanSet + 1})">+</button>
          </div>
          <div class="nb-v-stack" data-v-fan="${z.id}">${vFanCells}</div>
        </div>

        <div class="nb-v-col" style="--accent:var(--nb-power)">
          <div class="nb-v-title">🔥 Puissance</div>
          <div class="nb-v-real">Réel : <strong style="color:var(--nb-power)">P${state.power.real}</strong></div>
          <div class="nb-v-stepper">
            <button type="button" class="nb-v-btn" onclick="NobisUI.setPower(${state.power.set - 1})">−</button>
            <span class="nb-v-val">P${state.power.set}</span>
            <button type="button" class="nb-v-btn" onclick="NobisUI.setPower(${state.power.set + 1})">+</button>
          </div>
          <div class="nb-v-stack" data-v-power="true">${vPwrCells}</div>
        </div>
      </div>
    `;
  }

  function bindGestures() {
    const bindDrag = (selector, calcFn) => {
      document.querySelectorAll(selector).forEach(el => {
        const move = (e) => calcFn(el, e);
        el.onpointerdown = (e) => {
          try { el.setPointerCapture(e.pointerId); } catch (_) {}
          move(e);
          el.onpointermove = move;
        };
        el.onpointerup = () => { el.onpointermove = null; };
        el.onpointercancel = () => { el.onpointermove = null; };
      });
    };

    bindDrag('[data-h-temp]', (el, e) => {
      const r = el.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
      setZoneTemp(el.dataset.hTemp, 6 + ratio * 25);
    });

    bindDrag('[data-h-fan]', (el, e) => {
      const r = el.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(0.999, (e.clientX - r.left) / r.width));
      setZoneFan(el.dataset.hFan, Math.floor(ratio * 9));
    });

    bindDrag('[data-h-power]', (el, e) => {
      const r = el.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(0.999, (e.clientX - r.left) / r.width));
      setPower(Math.floor(ratio * 9));
    });

    bindDrag('[data-v-temp]', (el, e) => {
      const r = el.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (r.bottom - e.clientY) / r.height));
      setZoneTemp(el.dataset.vTemp, 6 + ratio * 25);
    });

    bindDrag('[data-v-fan]', (el, e) => {
      const r = el.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(0.999, (r.bottom - e.clientY) / r.height));
      setZoneFan(el.dataset.vFan, Math.floor(ratio * 9));
    });

    bindDrag('[data-v-power]', (el, e) => {
      const r = el.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(0.999, (r.bottom - e.clientY) / r.height));
      setPower(Math.floor(ratio * 9));
    });
  }

  function render() {
    const root = document.getElementById('nobis-pilotage-root');
    if (!root) return;
    root.innerHTML = state.layout === 'v-cockpit'
      ? buildVerticalCockpitHTML()
      : buildHorizontalFocusHTML();
    bindGestures();
  }

  // Mise à jour depuis votre API / WebSocket de télémétrie
  function updateTelemetry(data) {
    if (!data) return;
    if (data.power) {
      if (data.power.real !== undefined) state.power.real = Math.max(0, Math.min(8, Math.round(Number(data.power.real))));
      if (data.power.set !== undefined && !debounceTimer) {
        state.power.set = Math.max(0, Math.min(8, Math.round(Number(data.power.set))));
      }
    }
    if (data.zones) {
      ['salon', 'sx', 'dx'].forEach(k => {
        const zIn = data.zones[k];
        if (!zIn) return;
        const z = state.zones[k];
        if (zIn.tempReal !== undefined) z.tempReal = Math.round(Number(zIn.tempReal) * 10) / 10;
        if (zIn.fanReal !== undefined) z.fanReal = Math.max(0, Math.min(8, Math.round(Number(zIn.fanReal))));
        if (!debounceTimer) {
          if (zIn.tempSet !== undefined) z.tempSet = Math.max(6, Math.min(31, Math.round(Number(zIn.tempSet))));
          if (zIn.fanSet !== undefined) z.fanSet = Math.max(0, Math.min(8, Math.round(Number(zIn.fanSet))));
        }
      });
    }
    render();
  }

  function init(options = {}) {
    onCommandCallback = options.onCommand || null;
    syncConfigButtons();
    render();
  }

  return {
    init,
    updateTelemetry,
    setLayoutMode,
    syncConfigButtons,
    selectZone,
    setZoneTemp,
    setZoneFan,
    setPower
  };
})();

if (typeof window !== 'undefined') {
  window.NobisUI = NobisUI;
}

