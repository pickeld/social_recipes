const CACHE_NAME = 'social-recipes-v5';
const STATIC_CACHE_NAME = 'social-recipes-static-v5';
const CDN_CACHE_NAME = 'social-recipes-cdn-v5';

// Static assets to cache on install
const STATIC_ASSETS = [
  '/static/css/style.css',
  '/static/js/main.js',
  '/static/manifest.json',
  '/static/icons/icon-192x192.png',
  '/static/icons/icon-512x512.png'
];

// CDN assets to pre-cache (these need special CORS handling)
const CDN_ASSETS = [
  'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css',
  'https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.6.1/socket.io.min.js'
];

// Install event - cache static resources
self.addEventListener('install', (event) => {
  console.log('[SW] Installing service worker...');
  event.waitUntil(
    Promise.all([
      // Cache local static assets
      caches.open(STATIC_CACHE_NAME)
        .then((cache) => {
          console.log('[SW] Caching static assets');
          return cache.addAll(STATIC_ASSETS);
        }),
      // Pre-cache CDN assets with no-cors mode
      caches.open(CDN_CACHE_NAME)
        .then((cache) => {
          console.log('[SW] Pre-caching CDN assets');
          return Promise.all(
            CDN_ASSETS.map(url =>
              fetch(url, { mode: 'cors', credentials: 'omit' })
                .then(response => {
                  if (response.ok) {
                    return cache.put(url, response);
                  }
                })
                .catch(err => console.warn('[SW] Failed to cache CDN asset:', url, err))
            )
          );
        })
    ])
    .then(() => {
      console.log('[SW] All assets cached successfully');
      return self.skipWaiting();
    })
    .catch((error) => {
      console.error('[SW] Cache install failed:', error);
    })
  );
});

// Activate event - clean up old caches and take control
self.addEventListener('activate', (event) => {
  console.log('[SW] Activating service worker...');
  const currentCaches = [CACHE_NAME, STATIC_CACHE_NAME, CDN_CACHE_NAME];
  event.waitUntil(
    Promise.all([
      caches.keys().then((cacheNames) => {
        return Promise.all(
          cacheNames.map((cacheName) => {
            if (!currentCaches.includes(cacheName)) {
              console.log('[SW] Deleting old cache:', cacheName);
              return caches.delete(cacheName);
            }
          })
        );
      }),
      self.clients.claim()
    ]).then(() => {
      console.log('[SW] Service worker activated and controlling');
    })
  );
});

// Fetch event handler
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  
  // Skip chrome-extension, moz-extension, and other non-http(s) requests
  if (!url.protocol.startsWith('http')) {
    return;
  }
  
  // Handle share target POST requests specially
  if (event.request.method === 'POST' && url.pathname === '/share') {
    console.log('[SW] Handling share target POST request');
    event.respondWith(handleShareTarget(event.request));
    return;
  }
  
  // Skip non-GET requests (except share target handled above)
  if (event.request.method !== 'GET') {
    return;
  }
  
  // Handle navigation requests (HTML pages) - network-first
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          if (response.ok) {
            const responseClone = response.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(event.request, responseClone);
            });
          }
          return response;
        })
        .catch(() => {
          return caches.match(event.request).then((cachedResponse) => {
            if (cachedResponse) {
              return cachedResponse;
            }
            return caches.match('/');
          });
        })
    );
    return;
  }
  
  // Handle static assets - cache-first
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(event.request).then((cachedResponse) => {
        if (cachedResponse) {
          return cachedResponse;
        }
        return fetch(event.request).then((response) => {
          if (response.ok) {
            const responseClone = response.clone();
            caches.open(STATIC_CACHE_NAME).then((cache) => {
              cache.put(event.request, responseClone);
            });
          }
          return response;
        });
      })
    );
    return;
  }
  
  // Handle CDN resources (external origins) - cache-first with proper CORS handling
  if (url.origin !== self.location.origin) {
    // Check if this is a known CDN resource
    const isCDN = url.hostname.includes('cdnjs.cloudflare.com') ||
                  url.hostname.includes('cdn.') ||
                  url.hostname.includes('fonts.googleapis.com') ||
                  url.hostname.includes('fonts.gstatic.com');
    
    if (isCDN) {
      event.respondWith(
        caches.match(event.request).then((cachedResponse) => {
          if (cachedResponse) {
            return cachedResponse;
          }
          // Use credentials: 'omit' to avoid CORS issues with CDNs
          return fetch(event.request.url, {
            mode: 'cors',
            credentials: 'omit'
          }).then((response) => {
            if (response.ok) {
              const responseClone = response.clone();
              caches.open(CDN_CACHE_NAME).then((cache) => {
                cache.put(event.request.url, responseClone);
              });
            }
            return response;
          }).catch((error) => {
            console.warn('[SW] CDN fetch failed:', url.href, error);
            // Return empty response for non-critical CDN resources
            return new Response('', { status: 503 });
          });
        })
      );
      return;
    }
    
    // Skip other external requests (analytics, etc.) - don't intercept
    return;
  }
  
  // Default: network-first for API and other requests
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        return response;
      })
      .catch(() => {
        return caches.match(event.request);
      })
  );
});

// Handle share target POST requests
async function handleShareTarget(request) {
  console.log('[SW] Processing share target data');
  
  try {
    // Get form data from the POST request
    const formData = await request.formData();
    const title = formData.get('title') || '';
    const text = formData.get('text') || '';
    const url = formData.get('url') || '';
    
    console.log('[SW] Share data received:', { title, text, url });
    
    // Extract URL from shared content
    // Apps like TikTok/Instagram often share URL in the "text" field
    let sharedUrl = url;
    
    if (!sharedUrl && text) {
      // Try to extract URL from text using regex
      const urlRegex = /(https?:\/\/[^\s]+)/gi;
      const matches = text.match(urlRegex);
      if (matches && matches.length > 0) {
        sharedUrl = matches[0];
        console.log('[SW] Extracted URL from text:', sharedUrl);
      } else {
        // Use entire text as potential URL
        sharedUrl = text;
      }
    }
    
    if (!sharedUrl && title) {
      // Last resort: check title for URL
      const urlRegex = /(https?:\/\/[^\s]+)/gi;
      const matches = title.match(urlRegex);
      if (matches && matches.length > 0) {
        sharedUrl = matches[0];
      }
    }
    
    // Redirect to the main page with the shared URL as a query parameter
    const redirectUrl = new URL('/', self.location.origin);
    if (sharedUrl) {
      redirectUrl.searchParams.set('shared_url', sharedUrl);
    }
    if (text && text !== sharedUrl) {
      redirectUrl.searchParams.set('shared_text', text);
    }
    
    console.log('[SW] Redirecting to:', redirectUrl.toString());
    
    // Return a redirect response
    return Response.redirect(redirectUrl.toString(), 303);
    
  } catch (error) {
    console.error('[SW] Error handling share target:', error);
    // On error, just redirect to home page
    return Response.redirect('/', 303);
  }
}

// Handle messages from clients
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});
