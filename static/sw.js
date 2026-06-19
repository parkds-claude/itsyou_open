// 최소 서비스워커 — PWA 설치 가능성(installability) 충족용.
// 캐싱하지 않고 기본 네트워크 처리에 위임한다(키오스크는 항상 localhost 라 오프라인 캐시 불필요).
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => { /* respondWith 호출하지 않음 → 브라우저 기본 처리 */ });
