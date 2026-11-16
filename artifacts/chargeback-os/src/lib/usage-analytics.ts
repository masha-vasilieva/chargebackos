const ALLOWED_EVENTS = new Set(['setup_started']);
type AnalyticsPayload = Record<string, unknown>;
declare global {
  interface Window {
    umami?: { track: (buildPayload: (props: AnalyticsPayload) => AnalyticsPayload) => unknown };
  }
}

/** Sends a fixed-schema event and replaces the default URL with the public homepage. */
export function trackUsageEvent(name: string): boolean {
  if (!ALLOWED_EVENTS.has(name) || typeof window === 'undefined' || !window.umami?.track) return false;
  try {
    const result = window.umami.track((props) => ({
      website: props.website,
      hostname: props.hostname,
      url: '/',
      title: 'Chargeback OS',
      referrer: '',
      name,
    }));
    if (result && typeof (result as Promise<unknown>).then === 'function') {
      void (result as Promise<unknown>).catch(() => {});
    }
    return true;
  } catch {
    return false;
  }
}
