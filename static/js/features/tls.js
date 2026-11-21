// =============================================================================
// FEATURE HTTPS / TLS — CERTIFICATS AUTO-SIGNÉS & LET'S ENCRYPT DNS-01
// =============================================================================

let _tlsProvidersCatalog = [];
let _tlsLastStatus = null;

function _setTlsFeedback(kind, message, targetUrl = "") {
  const box = document.getElementById("tls-feedback");
  if (!box) return;
  if (!message) {
    box.classList.add("hidden");
    box.textContent = "";
    return;
  }
  box.className = "rounded-xl p-3 text-xs border space-y-1.5";
  if (kind === "error") {
    box.classList.add("bg-rose-950/60", "border-rose-700/70", "text-rose-200");
  } else if (kind === "success") {
    box.classList.add("bg-emerald-950/60", "border-emerald-700/70", "text-emerald-200");
  } else {
    box.classList.add("bg-sky-950/60", "border-sky-700/70", "text-sky-200");
  }
  box.textContent = "";
  const p = document.createElement("div");
  p.textContent = message;
  box.appendChild(p);
  if (targetUrl) {
    const link = document.createElement("a");
    link.href = targetUrl;
    link.textContent = `Ouvrir ${targetUrl} →`;
    link.className = "inline-block font-mono font-bold underline text-white hover:text-emerald-300";
    box.appendChild(link);
  }
  box.classList.remove("hidden");
}

function onTlsProviderChange() {
  const sel = document.getElementById("tls-dns-provider");
  const tokInput = document.getElementById("tls-dns-token");
  const helpEl = document.getElementById("tls-provider-help");
  if (!sel) return;
  const prov = _tlsProvidersCatalog.find(p => p.id === sel.value);
  if (prov) {
    if (tokInput && (!tokInput.value || tokInput.value === "********")) {
      tokInput.placeholder = prov.token_placeholder || "Token API DNS...";
    }
    if (helpEl) {
      helpEl.textContent = prov.help || "";
    }
  }
}

function renderTlsStatus(data) {
  if (!data) return;
  _tlsLastStatus = data;
  if (Array.isArray(data.providers) && data.providers.length > 0) {
    _tlsProvidersCatalog = data.providers;
    const sel = document.getElementById("tls-dns-provider");
    if (sel) {
      const currentVal = data.https_dns_provider || sel.value || "duckdns";
      sel.innerHTML = "";
      data.providers.forEach(p => {
        const opt = document.createElement("option");
        opt.value = p.id;
        opt.textContent = p.label;
        sel.appendChild(opt);
      });
      sel.value = currentVal;
    }
  }

  const modeLabels = {
    selfsigned: "🛡️ HTTPS Auto-signé (Local)",
    letsencrypt: "🔐 HTTPS Let's Encrypt (Officiel)",
    http: "🔓 HTTP (Sans TLS)"
  };
  const activeMode = data.active_mode || "http";
  const badge = document.getElementById("tls-active-mode-badge");
  if (badge) {
    badge.textContent = modeLabels[activeMode] || activeMode;
    badge.className = "font-semibold text-xs block mt-0.5 " + (
      activeMode === "letsencrypt" ? "text-emerald-400" :
      activeMode === "selfsigned" ? "text-teal-400" : "text-amber-400"
    );
  }

  const cert = data.active_cert;
  const subjEl = document.getElementById("tls-cert-subject");
  const expEl = document.getElementById("tls-cert-expiry");
  if (cert && cert.exists) {
    if (subjEl) {
      const cnMatch = (cert.subject || "").match(/CN\s*=\s*([^,/]+)/i);
      subjEl.textContent = cnMatch ? cnMatch[1].trim() : (data.https_domain || cert.subject || "Certificat actif");
      subjEl.title = (cert.sans && cert.sans.length) ? cert.sans.join(", ") : (cert.subject || "");
    }
    if (expEl) {
      const days = cert.days_remaining;
      if (days !== null && days !== undefined) {
        expEl.textContent = days > 0 ? `Valide (${days} j restants)` : "Expiré";
        expEl.className = "font-mono text-xs block mt-0.5 " + (days <= 15 ? "text-rose-400" : days <= 30 ? "text-amber-400" : "text-sky-400");
      } else {
        expEl.textContent = cert.not_after || "Actif";
      }
    }
  } else {
    if (subjEl) subjEl.textContent = "Aucun certificat actif";
    if (expEl) expEl.textContent = "--";
  }

  const sumEl = document.getElementById("acc-sum-tls");
  if (sumEl) {
    const shortMode = activeMode === "letsencrypt" ? `Let's Encrypt (${data.https_domain || "actif"})` :
      activeMode === "selfsigned" ? "HTTPS Auto-signé" : "HTTP (sans TLS)";
    const daysTxt = (cert && cert.days_remaining != null) ? ` • ${cert.days_remaining}j` : "";
    sumEl.textContent = `${shortMode}${daysTxt}`;
  }

  const sanIpsEl = document.getElementById("tls-san-ips");
  if (sanIpsEl && !sanIpsEl.matches(":focus")) sanIpsEl.value = data.https_san_ips || "";
  const sanDnsEl = document.getElementById("tls-san-dns");
  if (sanDnsEl && !sanDnsEl.matches(":focus")) sanDnsEl.value = data.https_san_dns || "";
  const domEl = document.getElementById("tls-domain");
  if (domEl && !domEl.matches(":focus")) domEl.value = data.https_domain || "";
  const emailEl = document.getElementById("tls-email");
  if (emailEl && !emailEl.matches(":focus")) emailEl.value = data.https_email || "";
  const tokEl = document.getElementById("tls-dns-token");
  if (tokEl && !tokEl.matches(":focus")) {
    tokEl.value = data.has_dns_token ? "********" : "";
  }

  const renewBtn = document.getElementById("btn-tls-renew");
  if (renewBtn) {
    renewBtn.classList.toggle("hidden", !(data.https_domain && data.has_dns_token));
  }
  const dlBtn = document.getElementById("btn-tls-download-crt");
  if (dlBtn) {
    const hasAnyCert = Boolean((data.active_cert && data.active_cert.exists) || (data.selfsigned_cert && data.selfsigned_cert.exists));
    dlBtn.classList.toggle("opacity-50", !hasAnyCert);
    dlBtn.classList.toggle("pointer-events-none", !hasAnyCert);
  }

  onTlsProviderChange();
}

async function loadTlsStatus() {
  try {
    const res = await fetch("/api/tls/status", { cache: "no-store" });
    if (!res.ok) return;
    const data = await res.json();
    renderTlsStatus(data);
  } catch (_) {
    // Ignore transient network errors
  }
}

function _buildTargetProtocolUrl(protocol, customHost = "") {
  const host = customHost || window.location.hostname;
  const port = window.location.port ? `:${window.location.port}` : "";
  return `${protocol}//${host}${port}/`;
}

async function applySelfSignedTls() {
  const sanIps = (document.getElementById("tls-san-ips")?.value || "").trim();
  const sanDns = (document.getElementById("tls-san-dns")?.value || "").trim();
  const btn = document.getElementById("btn-tls-selfsigned");
  if (btn) btn.disabled = true;
  _setTlsFeedback("info", "Génération du certificat auto-signé et redémarrage en HTTPS...");
  try {
    const res = await fetch("/api/tls/mode", {
      method: "POST",
      headers: getInstallerHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        mode: "selfsigned",
        san_ips: sanIps,
        san_dns: sanDns,
        regenerate: true,
        restart: true
      })
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 403) {
      _setTlsFeedback("error", "🔒 Authentification installateur requise : déverrouillez l'onglet Expert & Registres.");
      showToast("🔒 Authentification installateur requise.", true);
      return;
    }
    if (!res.ok) {
      _setTlsFeedback("error", data.detail || "Échec d'activation du mode HTTPS auto-signé.");
      showToast(data.detail || "Échec HTTPS auto-signé.", true);
      return;
    }
    const targetUrl = _buildTargetProtocolUrl("https:");
    _setTlsFeedback("success", `✅ ${data.message || "Mode HTTPS auto-signé activé."} Le conteneur redémarre (3-5 s).`, targetUrl);
    showToast("✅ HTTPS auto-signé activé, redémarrage en cours...");
  } catch (_) {
    _setTlsFeedback("error", "Erreur réseau lors de la demande HTTPS auto-signé.");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function issueLetsEncryptTls() {
  const provider = (document.getElementById("tls-dns-provider")?.value || "duckdns").trim();
  const domain = (document.getElementById("tls-domain")?.value || "").trim();
  const token = (document.getElementById("tls-dns-token")?.value || "").trim();
  const email = (document.getElementById("tls-email")?.value || "").trim();

  if (!domain) {
    _setTlsFeedback("error", "Veuillez saisir un nom de domaine valide (ex: mon-poele.duckdns.org).");
    return;
  }
  const btn = document.getElementById("btn-tls-letsencrypt");
  if (btn) btn.disabled = true;
  _setTlsFeedback("info", `⏳ Validation DNS-01 Let's Encrypt en cours pour ${domain} (${provider})... Cela peut prendre 30 à 90 secondes.`);
  try {
    const res = await fetch("/api/tls/letsencrypt/issue", {
      method: "POST",
      headers: getInstallerHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        domain,
        dns_provider: provider,
        dns_token: token,
        email,
        force: false,
        restart: true
      })
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 403) {
      _setTlsFeedback("error", "🔒 Authentification installateur requise : déverrouillez l'onglet Expert & Registres.");
      showToast("🔒 Authentification installateur requise.", true);
      return;
    }
    if (!res.ok) {
      _setTlsFeedback("error", data.detail || "Échec de la validation Let's Encrypt. Le mode précédent reste actif.");
      showToast("Échec validation Let's Encrypt.", true);
      return;
    }
    const targetUrl = _buildTargetProtocolUrl("https:", data.domain || domain);
    _setTlsFeedback("success", `✅ ${data.message || "Certificat Let's Encrypt activé !"} Le conteneur redémarre (3-5 s).`, targetUrl);
    showToast("✅ Certificat Let's Encrypt activé !");
  } catch (_) {
    _setTlsFeedback("error", "Erreur réseau pendant la validation Let's Encrypt.");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function renewLetsEncryptTls() {
  const btn = document.getElementById("btn-tls-renew");
  if (btn) btn.disabled = true;
  _setTlsFeedback("info", "♻️ Renouvellement Let's Encrypt en cours...");
  try {
    const res = await fetch("/api/tls/letsencrypt/renew", {
      method: "POST",
      headers: getInstallerHeaders()
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 403) {
      _setTlsFeedback("error", "🔒 Authentification installateur requise.");
      return;
    }
    if (!res.ok) {
      _setTlsFeedback("error", data.detail || "Échec du renouvellement Let's Encrypt.");
      return;
    }
    const targetUrl = _buildTargetProtocolUrl("https:", data.domain || "");
    _setTlsFeedback("success", `✅ ${data.message || "Certificat renouvelé !"} Redémarrage en cours...`, targetUrl);
    showToast("✅ Certificat Let's Encrypt renouvelé !");
  } catch (_) {
    _setTlsFeedback("error", "Erreur réseau lors du renouvellement.");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function switchTlsToHttp() {
  if (!confirm("Repasser le conteneur en HTTP simple (sans TLS) ? Utile uniquement derrière un reverse proxy externe.")) {
    return;
  }
  const btn = document.getElementById("btn-tls-http");
  if (btn) btn.disabled = true;
  _setTlsFeedback("info", "Bascule en mode HTTP et redémarrage du backend...");
  try {
    const res = await fetch("/api/tls/mode", {
      method: "POST",
      headers: getInstallerHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ mode: "http", restart: true })
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 403) {
      _setTlsFeedback("error", "🔒 Authentification installateur requise : déverrouillez l'onglet Expert & Registres.");
      return;
    }
    if (!res.ok) {
      _setTlsFeedback("error", data.detail || "Échec du passage en HTTP.");
      return;
    }
    const targetUrl = _buildTargetProtocolUrl("http:");
    _setTlsFeedback("success", "✅ Mode HTTP enregistré. Le conteneur redémarre (3-5 s).", targetUrl);
    showToast("🔓 Retour en HTTP déclenché...");
  } catch (_) {
    _setTlsFeedback("error", "Erreur réseau lors du passage en HTTP.");
  } finally {
    if (btn) btn.disabled = false;
  }
}
