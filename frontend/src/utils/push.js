export async function ensureServiceWorker() {
	if (!('serviceWorker' in navigator)) return null;
	try {
		const reg = await navigator.serviceWorker.register('/sw.js');
		return reg;
	} catch (e) {
		return null;
	}
}

function urlBase64ToUint8Array(base64String) {
	const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
	const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
	const rawData = atob(base64);
	const outputArray = new Uint8Array(rawData.length);
	for (let i = 0; i < rawData.length; ++i) {
		outputArray[i] = rawData.charCodeAt(i);
	}
	return outputArray;
}

export async function registerPush({ calendarNotifyMinutes } = {}) {
	if (!('Notification' in window)) throw new Error('Notifications not supported');
	const perm = await Notification.requestPermission();
	if (perm !== 'granted') throw new Error('Permission denied');
	const reg = await ensureServiceWorker();
	if (!reg) throw new Error('No service worker');
	const keyRes = await fetch('/api/push/public-key');
	const { publicKey, enabled } = await keyRes.json();
	if (!enabled || !publicKey) throw new Error('Push not configured on server');
	const sub = await reg.pushManager.subscribe({
		userVisibleOnly: true,
		applicationServerKey: urlBase64ToUint8Array(publicKey),
	});
	await fetch('/api/push/subscribe', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ subscription: sub.toJSON(), enabled: true, calendar_notify_minutes: calendarNotifyMinutes }),
	});
	return true;
}

export async function unregisterPush() {
	const reg = await navigator.serviceWorker.getRegistration();
	if (reg) {
		const sub = await reg.pushManager.getSubscription();
		if (sub) await sub.unsubscribe();
	}
	await fetch('/api/push/unsubscribe', { method: 'POST' });
}



