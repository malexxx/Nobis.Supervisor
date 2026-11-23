// =============================================================================
// NOBIS A10 C LIGHT STYLE COAX — POINT D'ENTRÉE BOOTSTRAP SPA (DOMContentLoaded)
// =============================================================================

window.addEventListener("DOMContentLoaded", () => {
  try { initThreePaneCockpit(); } catch (e) { console.error("[Init] ThreePaneCockpit:", e); }
  try { initTriArcInteractions(); } catch (e) { console.error("[Init] TriArcInteractions:", e); }
  try { renderTriArcCockpit(); } catch (e) { console.error("[Init] RenderTriArc:", e); }
  try { switchTab("tab-control"); } catch (e) { console.error("[Init] SwitchTab:", e); }
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
  try { if (typeof loadStorageStatus === "function") loadStorageStatus(); } catch (e) { console.error("[Init] StorageStatus:", e); }
  try { if (typeof loadUpdateStatus === "function") loadUpdateStatus(); } catch (e) { console.error("[Init] UpdateStatus:", e); }
  // Filet de sécurité adaptatif (60s) : inactif en arrière-plan ou lorsque le flux SSE est frais (< 150s)
  setInterval(() => {
    if (document.hidden) return;
    const sseFresh = Boolean(
      connectionState &&
      connectionState.isSSEConnected &&
      connectionState.lastMessageAt > 0 &&
      (Date.now() - connectionState.lastMessageAt) < 150000
    );
    if (!sseFresh) {
      refreshLatestOnly();
    }
  }, 60000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      const stale = !connectionState || !connectionState.lastMessageAt || (Date.now() - connectionState.lastMessageAt) > 30000;
      if (stale) refreshLatestOnly();
    }
  });
  // Rafraîchissement ciblé 1 Hz du compte à rebours Mode Silence ! (sans rebuild SVG complet)
  setInterval(() => {
    if (!document.hidden) updateSilenceCountdownDisplay();
  }, 1000);
});

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js", { scope: "/" })
      .then((reg) => console.log("[PWA] Service Worker actif, scope:", reg.scope))
      .catch((err) => console.warn("[PWA] Enregistrement SW ignoré (contexte non sécurisé ou unsupported):", err));
  });
}
