self.addEventListener('install', (event) => {
	self.skipWaiting();
});

self.addEventListener('activate', (event) => {
	self.clients.claim();
});

self.addEventListener('push', (event) => {
	let data = {};
	try {
		if (event.data) data = event.data.json();
	} catch (e) {}
	const title = data.title || 'Float notification';
	const body = data.body || '';
	const options = {
		body,
		data: data.data || {},
		icon: '/floatgpt.png',
		badge: '/floatgpt.png',
	};
	event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
	event.notification.close();
	const url = (event.notification.data && event.notification.data.action_url) || '/';
	event.waitUntil(
		self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientsArr) => {
			for (const client of clientsArr) {
				if ('focus' in client) {
					client.navigate(url);
					return client.focus();
				}
			}
			if (self.clients.openWindow) {
				return self.clients.openWindow(url);
			}
		})
	);
});



