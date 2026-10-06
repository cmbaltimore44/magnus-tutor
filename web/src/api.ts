// Small API client: JSON helpers, a streaming SSE reader for POSTs, and the
// shared /api/events stream (timer, solver, jobs, settings).

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function handle<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let msg = r.statusText;
    try {
      const j = await r.json();
      msg = j.detail || j.error || msg;
    } catch {
      /* not JSON */
    }
    throw new ApiError(r.status, msg);
  }
  return r.json() as Promise<T>;
}

export const api = {
  get: <T = any>(path: string) => fetch(`/api${path}`).then((r) => handle<T>(r)),
  post: <T = any>(path: string, body?: unknown) =>
    fetch(`/api${path}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) }).then((r) => handle<T>(r)),
  patch: <T = any>(path: string, body: unknown) =>
    fetch(`/api${path}`, { method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }).then((r) => handle<T>(r)),
  put: <T = any>(path: string, body: unknown) =>
    fetch(`/api${path}`, { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }).then((r) => handle<T>(r)),
  del: <T = any>(path: string) => fetch(`/api${path}`, { method: 'DELETE' }).then((r) => handle<T>(r)),
  upload: async (file: Blob, name = 'image.png'): Promise<string> => {
    const fd = new FormData();
    fd.append('file', file, name);
    const r = await handle<{ id: string }>(await fetch('/api/uploads', { method: 'POST', body: fd }));
    return r.id;
  },
};

export type StreamEvent = { type: string; [k: string]: any };

/** POST and read a text/event-stream response, calling onEvent per event. */
export async function postStream(path: string, body: unknown, onEvent: (ev: StreamEvent) => void, signal?: AbortSignal): Promise<void> {
  const r = await fetch(`/api${path}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body), signal });
  if (!r.ok || !r.body) {
    let msg = r.statusText;
    try {
      msg = (await r.json()).detail || msg;
    } catch {
      /* ignore */
    }
    throw new ApiError(r.status, msg);
  }
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const raw = buf.slice(0, i);
      buf = buf.slice(i + 2);
      let data = '';
      for (const line of raw.split('\n')) {
        if (line.startsWith('data:')) data += line.slice(5).trim();
      }
      if (data) {
        try {
          onEvent(JSON.parse(data));
        } catch {
          /* partial or non-JSON line */
        }
      }
    }
  }
}

type Listener = (data: any) => void;
const listeners = new Map<string, Set<Listener>>();
let source: EventSource | null = null;
let connected = false;
const connListeners = new Set<(c: boolean) => void>();

function ensureSource() {
  if (source) return;
  source = new EventSource('/api/events');
  source.onopen = () => {
    connected = true;
    connListeners.forEach((f) => f(true));
  };
  source.onerror = () => {
    connected = false;
    connListeners.forEach((f) => f(false));
  };
  for (const kind of ['timer', 'solver', 'job', 'settings', 'resources', 'hello']) {
    source.addEventListener(kind, (e) => {
      const data = JSON.parse((e as MessageEvent).data);
      listeners.get(kind)?.forEach((f) => f(data));
    });
  }
}

export function onEvent(kind: string, f: Listener): () => void {
  ensureSource();
  if (!listeners.has(kind)) listeners.set(kind, new Set());
  listeners.get(kind)!.add(f);
  return () => listeners.get(kind)?.delete(f);
}

export function onConnection(f: (c: boolean) => void): () => void {
  ensureSource();
  connListeners.add(f);
  f(connected);
  return () => connListeners.delete(f);
}
