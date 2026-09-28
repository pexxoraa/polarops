const CACHE='polarops-shell-v7-login-bg-fix';
const SHELL=['/','/vendor/leaflet/leaflet.css','/vendor/leaflet/leaflet.js','/static/app.css','/static/reference-ui.css?v=4','/static/app.js?v=4','/media/antarctica-nasa.jpg','/media/arctic-nasa.jpg','/manifest.webmanifest'];
self.addEventListener('install',e=>e.waitUntil(caches.open(CACHE).then(c=>c.addAll(SHELL)).then(()=>self.skipWaiting())));
self.addEventListener('activate',e=>e.waitUntil(Promise.all([
  caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE&&k.startsWith('polarops-shell-')).map(k=>caches.delete(k)))),
  self.clients.claim()
])));
self.addEventListener('fetch',e=>{
  const u=new URL(e.request.url);
  if(u.pathname.startsWith('/api/')||u.pathname.startsWith('/ws/')) return;
  e.respondWith(fetch(e.request).then(res=>{const c=res.clone();caches.open(CACHE).then(x=>x.put(e.request,c));return res;}).catch(()=>caches.match(e.request)));
});
