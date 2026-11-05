// Service Worker pour Cockpit Nobis A10 (PWA)
const CACHE_NAME = 'nobis-cockpit-v1';
const PRECACHE_ASSETS = [
  '/',
  '/manifest.json',
  '/static/css/styles.css',
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

  // Stratégie Réseau d'abord avec repli sur le cache pour les fichiers statiques et pages
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
        // En cas de perte de connexion réseau, servir la version en cache si disponible
        return caches.match(event.request);
      })
  );
});

