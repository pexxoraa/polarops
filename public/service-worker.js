const CACHE='polarops-shell-v29-route-form-visibility';
const SHELL=['/','/vendor/leaflet/leaflet.css','/vendor/leaflet/leaflet.js','/static/app.css','/static/reference-ui.css?v=15','/static/app.js?v=22','/static/ops-features.js?v=3','/media/antarctica-nasa.jpg','/media/arctic-nasa.jpg','/manifest.webmanifest'];

self.addEventListener('install',e=>e.waitUntil(
  caches.open(CACHE).then(c=>c.addAll(SHELL)).then(()=>self.skipWaiting())
));

self.addEventListener('activate',e=>e.waitUntil(Promise.all([
  caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE&&k.startsWith('polarops-shell-')).map(k=>caches.delete(k)))),
  self.clients.claim()
])));

self.addEventListener('fetch',e=>{
  const u=new URL(e.request.url);

  // API/WebSocket traffic must always go directly to the live backend.
  if(u.origin===self.location.origin && (u.pathname.startsWith('/api/')||u.pathname.startsWith('/ws/'))) return;

  // Do not proxy third-party map tiles through the service worker. Let the
  // browser use the provider's own HTTP cache and connection pooling.
  if(u.origin!==self.location.origin) return;

  if(e.request.mode==='navigate'){
    e.respondWith(
      fetch(e.request).then(res=>{
        const copy=res.clone();
        caches.open(CACHE).then(c=>c.put('/',copy));
        return res;
      }).catch(()=>caches.match('/'))
    );
    return;
  }

  // Static assets are versioned. Cache-first makes repeat loads effectively
  // instant while a new service-worker version replaces old cached assets.
  e.respondWith(
    caches.match(e.request).then(hit=>hit || fetch(e.request).then(res=>{
      const copy=res.clone();
      caches.open(CACHE).then(c=>c.put(e.request,copy));
      return res;
    }))
  );
});
