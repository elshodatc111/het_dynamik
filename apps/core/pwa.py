"""Ilovani telefon va kompyuterga o‘rnatish (PWA): manifest, service worker, oflayn sahifa."""
import json

from django.http import HttpResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.views.decorators.cache import never_cache

from .context import _asset_version


def manifest(request):
    icons = [
        {"src": static("img/icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": static("img/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": static("img/icon-maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ]
    data = {
        "name": "Dinamika — Qashqadaryo HETK", "short_name": "Dinamika", "description": "Dinamika, monitoring, Customer va Location tizimi",
        "id": "/", "start_url": "/", "scope": "/", "display": "standalone", "orientation": "any", "lang": "uz",
        "background_color": "#f5f6fa", "theme_color": "#3b5bfd", "icons": icons,
        "shortcuts": [{"name": "Location xaritasi", "url": "/location/xarita/", "icons": icons[:1]},
                      {"name": "Yangi post", "url": "/postlar/yangi/", "icons": icons[:1]}],
    }
    resp = HttpResponse(json.dumps(data, ensure_ascii=False), content_type="application/manifest+json; charset=utf-8")
    resp["Cache-Control"] = "no-cache"
    return resp


SW = """/* Dinamika service worker — v%(v)s */
const V = 'dinamika-%(v)s';
const OFFLINE = '/offline/';
self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(V).then((c) => c.addAll([OFFLINE])).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== V).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', (e) => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== location.origin) return;
  if (url.pathname.startsWith('/static/')) {            // statik fayllar: avval keshdan, fonda yangilanadi
    e.respondWith(caches.open(V).then((c) => c.match(req).then((hit) => {
      const net = fetch(req).then((r) => { if (r.ok) c.put(req, r.clone()); return r; }).catch(() => hit);
      return hit || net;
    })));
    return;
  }
  if (req.mode === 'navigate') {                        // sahifalar: doim serverdan; internet yo‘q bo‘lsa — oflayn sahifa
    e.respondWith(fetch(req).catch(() => caches.match(OFFLINE)));
  }
});
"""


@never_cache
def service_worker(request):
    resp = HttpResponse(SW % {"v": _asset_version()}, content_type="application/javascript; charset=utf-8")
    resp["Service-Worker-Allowed"] = "/"
    return resp


def offline(request):
    return render(request, "offline.html")
