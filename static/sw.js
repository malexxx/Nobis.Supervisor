// Service Worker pour Cockpit Nobis A10 (PWA)
const CACHE_NAME = 'nobis-cockpit-v2.3.0';
const PRECACHE_ASSETS = [
  '/',
  '/manifest.json',
  '/static/css/tailwind.generated.css',
  '/static/css/styles.css',
  '/static/css/calibration_wizard.css',
  '/static/vendor/chart.umd.min.js',
  '/static/js/state.js',
  '/static/js/calibration_wizard_ui.js',
  '/static/js/charts/series_utils.js',
  '/static/js/charts/cockpit_charts.js',
  '/static/js/charts/security_pane.js',
  '/static/js/core/ui_core.js',
  '/static/js/features/stove_control.js',
  '/static/js/features/silence.js',
  '/static/js/features/chrono.js',
  '/static/js/features/pellets_ai.js',
  '/static/js/features/weather_flower.js',
  '/static/js/features/installer.js',
  '/static/js/features/settings.js',
  '/static/js/features/tls.js',
  '/static/js/features/matter.js',
  '/static/js/features/triarc_controller.js',
  '/static/js/features/triarc_render.js',
  '/static/js/core/sync.js',
  '/static/js/app.js',
  '/static/icon-192.png',
  '/static/icon-512.png',
  '/static/icon-maskable-192.png',
  '/static/icon-maskable-512.png',
  '/static/icon.svg',
  '/static/apple-touch-icon.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(PRECACHE_ASSETS).catch((err) => {
        console.warn('[SW] Pré-cache partiel:', err);
      });
    }).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cacheName) => {
          if (cacheName !== CACHE_NAME) {
            console.log('[SW] Nettoyage ancien cache:', cacheName);
            return caches.delete(cacheName);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Ne JAMAIS intercepter ni mettre en cache les requêtes dynamiques : SSE (/api/events) et API REST (/api/*)
  if (url.pathname.startsWith('/api/') || event.request.method !== 'GET') {
    return;
  }

  // Stratégie Cache-First pour les assets statiques versionnés (/static/*) : chargement quasi instantané
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(event.request, { ignoreSearch: true }).then((cached) => {
        if (cached) return cached;
        return fetch(event.request).then((response) => {
          if (response && response.status === 200 && response.type === 'basic') {
            const responseToCache = response.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(event.request, responseToCache);
            });
          }
          return response;
        });
      })
    );
    return;
  }

  // Stratégie Réseau d'abord avec repli sur le cache pour la page SPA (/) et manifest.json
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        // Mettre à jour le cache dynamiquement pour les requêtes GET réussies
        if (response && response.status === 200 && response.type === 'basic') {
          const responseToCache = response.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, responseToCache);
          });
        }
        return response;
      })
      .catch(() => {
        // En cas de perte de connexion réseau, servir la version en cache (ignoreSearch tolère ?v=...)
        return caches.match(event.request, { ignoreSearch: true });
      })
  );
});
