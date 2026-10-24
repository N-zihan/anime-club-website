// 缓存的版本号——每次更新缓存时修改这个版本号
const CACHE_VERSION = 'v4';
const CACHE_STATIC = `anime-club-static-${CACHE_VERSION}`;
const OFFLINE_URL = '/offline';

// 预缓存静态资源 + 离线兜底页
const STATIC_URLS = [
    '/static/css/style.css',
    '/static/images/icon-192.png',
    '/static/images/icon-512.png',
    '/static/images/kanban.png',
    '/static/favicon.ico',
    OFFLINE_URL,
];

// 安装 —— 预缓存静态资源，并立即跳过等待
self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_STATIC).then((cache) => cache.addAll(STATIC_URLS))
    );
    self.skipWaiting();
});

// 激活 —— 清理旧版本缓存，立即接管页面
self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((names) => {
            return Promise.all(
                names.map((name) => {
                    if (name.startsWith('anime-club-') && name !== CACHE_STATIC) {
                        return caches.delete(name);
                    }
                })
            );
        }).then(() => self.clients.claim())
    );
});

function offlineFallback() {
    return caches.match(OFFLINE_URL).then((cached) => {
        if (cached) return cached;
        return new Response(
            '<!doctype html><meta charset="utf-8"><title>离线</title>' +
            '<p style="font-family:sans-serif;padding:40px;text-align:center;">网络开小差了，请稍后重试。</p>',
            { headers: { 'Content-Type': 'text/html; charset=utf-8' } }
        );
    });
}

// 拦截请求：
// 1) 页面导航：网络优先，断网回落到离线兜底页
// 2) 静态资源：**网络优先**，成功后写回缓存；断网才用缓存
//    （旧版是缓存优先，导致部署后浏览器一直用旧 JS/CSS：投票页时钟不走的锅就是它）
self.addEventListener('fetch', (event) => {
    const { request } = event;

    // 只处理 GET
    if (request.method !== 'GET') return;

    const url = new URL(request.url);

    // 只处理同源
    if (url.origin !== self.location.origin) return;

    // 页面导航（含浏览器地址栏 / 链接跳转）
    if (request.mode === 'navigate') {
        event.respondWith(
            fetch(request).catch(() => offlineFallback())
        );
        return;
    }

    // 只有静态资源走 SW；其他一律放过，让浏览器自己处理
    if (!url.pathname.startsWith('/static/') && url.pathname !== '/favicon.ico') {
        return;
    }

    event.respondWith(
        fetch(request, { cache: 'no-cache' }).then((response) => {
            if (response.ok) {
                const clone = response.clone();
                caches.open(CACHE_STATIC).then((cache) => cache.put(request, clone));
            }
            return response;
        }).catch(() => caches.match(request).then((cached) => {
            if (cached) return cached;
            return new Response('', { status: 504, statusText: 'offline' });
        }))
    );
});
