import { loadState, normalizeProject, saveState, type Project, type StoredState } from './projects';

export interface DesktopEnv {
  token: string;
  dataDir: string;
  portable: boolean;
}

declare global {
  interface Window {
    __REVEAL_ATLAS_DESKTOP__?: DesktopEnv;
  }
}

const IDB_NAME = 'reveal-atlas-db';
const IDB_STORE = 'kv';
const IDB_KEY = 'state-v1';
const BLOB_PREFIX = '@blob:';

let hydratedState: StoredState | null = null;
let initialized = false;
let idbAvailable = false;
let idbPromise: Promise<IDBDatabase> | null = null;

/** Кэш уже загруженных на локальный сервер картинок (blobKey -> dataUrl). */
const uploadedBlobs = new Map<string, string>();

let pendingState: StoredState | null = null;
let savingInFlight = false;
let flushProvider: (() => StoredState | null) | null = null;
let sessionEventSource: EventSource | null = null;

export function getDesktopEnv(): DesktopEnv | null {
  if (typeof window === 'undefined') return null;
  const env = window.__REVEAL_ATLAS_DESKTOP__;
  if (!env || typeof env.token !== 'string' || !env.token) return null;
  return env;
}

export function isDesktopApp(): boolean {
  return getDesktopEnv() !== null;
}

export function getInitialStoredState(): StoredState | null {
  if (hydratedState) return hydratedState;
  return loadState();
}

export function registerFlushProvider(provider: (() => StoredState | null) | null): void {
  flushProvider = provider;
}

function safeBlobKey(id: string): string {
  const cleaned = id.replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 64);
  return cleaned || 'world';
}

function parseHydratedProjects(rawParsed: unknown): StoredState | null {
  const parsed = rawParsed as { version?: unknown; activeId?: unknown; projects?: unknown[] } | null;
  if (!parsed || !Array.isArray(parsed.projects)) return null;
  const projects = parsed.projects.map(normalizeProject).filter((p): p is Project => p !== null);
  if (projects.length === 0) return null;
  const activeId =
    typeof parsed.activeId === 'string' && projects.some((p) => p.id === parsed.activeId)
      ? parsed.activeId
      : projects[0].id;
  return { version: 1, activeId, projects };
}

function openIdb(): Promise<IDBDatabase> {
  if (idbPromise) return idbPromise;
  idbPromise = new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      reject(new Error('IndexedDB unavailable'));
      return;
    }
    const req = indexedDB.open(IDB_NAME, 1);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(IDB_STORE)) {
        db.createObjectStore(IDB_STORE);
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error('IndexedDB open error'));
  });
  return idbPromise;
}

async function idbLoad(): Promise<StoredState | null> {
  const db = await openIdb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(IDB_STORE, 'readonly');
    const store = tx.objectStore(IDB_STORE);
    const req = store.get(IDB_KEY);
    req.onsuccess = () => resolve(parseHydratedProjects(req.result));
    req.onerror = () => reject(req.error ?? new Error('IndexedDB read error'));
  });
}

async function idbSave(state: StoredState): Promise<void> {
  const db = await openIdb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(IDB_STORE, 'readwrite');
    const store = tx.objectStore(IDB_STORE);
    store.put(state, IDB_KEY);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error ?? new Error('IndexedDB write error'));
    tx.onabort = () => reject(tx.error ?? new Error('IndexedDB abort'));
  });
}

async function desktopFetch(path: string, init?: RequestInit): Promise<Response> {
  const env = getDesktopEnv();
  if (!env) throw new Error('Desktop runtime unavailable');
  const headers = new Headers(init?.headers);
  headers.set('X-Atlas-Token', env.token);
  return fetch(path, { ...init, headers });
}

async function loadDesktopState(): Promise<StoredState | null> {
  const res = await desktopFetch('/api/state', { method: 'GET', cache: 'no-store' });
  if (res.status === 204 || res.status === 404) return null;
  if (!res.ok) return null;
  const raw = (await res.json()) as {
    version?: unknown;
    activeId?: unknown;
    projects?: Array<Record<string, unknown>>;
  };
  if (!raw || !Array.isArray(raw.projects)) return null;

  await Promise.all(
    raw.projects.map(async (proj) => {
      if (!proj || typeof proj !== 'object') return;
      const img = proj.image as { dataUrl?: unknown } | undefined;
      if (!img || typeof img.dataUrl !== 'string') return;
      if (img.dataUrl.startsWith(BLOB_PREFIX)) {
        const key = safeBlobKey(img.dataUrl.slice(BLOB_PREFIX.length));
        try {
          const blobRes = await desktopFetch(`/api/blob/${encodeURIComponent(key)}`, {
            method: 'GET',
            cache: 'no-store',
          });
          if (blobRes.ok) {
            const dataUrl = await blobRes.text();
            if (dataUrl.startsWith('data:image/')) {
              img.dataUrl = dataUrl;
              uploadedBlobs.set(key, dataUrl);
            }
          }
        } catch {
          /* повреждённый/удалённый блоб отфильтруется в normalizeProject */
        }
      } else if (img.dataUrl.startsWith('data:image/') && typeof proj.id === 'string') {
        uploadedBlobs.set(safeBlobKey(proj.id), img.dataUrl);
      }
    }),
  );

  return parseHydratedProjects(raw);
}

interface CompactJson {
  body: string;
  /** false — в состоянии есть мир, картинка которого ещё не на сервере. */
  safe: boolean;
}

function buildCompactDesktopJson(state: StoredState, allowInlineFallback: boolean): CompactJson {
  let safe = true;
  const compactProjects = state.projects.map((p) => {
    const key = safeBlobKey(p.id);
    const alreadyUploaded = uploadedBlobs.get(key) === p.image.dataUrl;
    let dataUrl: string;
    if (alreadyUploaded) {
      dataUrl = `${BLOB_PREFIX}${key}`;
    } else if (allowInlineFallback && p.image.dataUrl.length < 45000) {
      dataUrl = p.image.dataUrl;
    } else {
      // Ссылаться на блоб, которого нет на сервере, нельзя: после перезапуска
      // мир бы просто исчез. Такую состояние не отправляем.
      dataUrl = `${BLOB_PREFIX}${key}`;
      safe = false;
    }
    return {
      ...p,
      image: {
        ...p.image,
        dataUrl,
      },
    };
  });
  return {
    body: JSON.stringify({
      version: 1,
      activeId: state.activeId,
      projects: compactProjects,
    }),
    safe,
  };
}

async function saveDesktopStateNow(state: StoredState): Promise<'ok' | 'error'> {
  try {
    const activeKeys = new Set<string>();
    for (const p of state.projects) {
      const key = safeBlobKey(p.id);
      activeKeys.add(key);
      if (uploadedBlobs.get(key) !== p.image.dataUrl) {
        const putBlob = await desktopFetch(`/api/blob/${encodeURIComponent(key)}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'text/plain; charset=utf-8' },
          body: p.image.dataUrl,
        });
        if (!putBlob.ok) return 'error';
        uploadedBlobs.set(key, p.image.dataUrl);
      }
    }

    const compact = buildCompactDesktopJson(state, false);
    const putState = await desktopFetch('/api/state', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: compact.body,
    });
    if (!putState.ok) return 'error';

    for (const existingKey of [...uploadedBlobs.keys()]) {
      if (!activeKeys.has(existingKey)) {
        uploadedBlobs.delete(existingKey);
        void desktopFetch(`/api/blob/${encodeURIComponent(existingKey)}`, { method: 'DELETE' }).catch(() => {});
      }
    }

    return 'ok';
  } catch {
    return 'error';
  }
}

function setupDesktopLifecycle(env: DesktopEnv): void {
  try {
    sessionEventSource = new EventSource(`/api/session?t=${encodeURIComponent(env.token)}`);
  } catch {
    sessionEventSource = null;
  }

  const flushAndNotify = (closing: boolean) => {
    try {
      const latest = flushProvider ? flushProvider() : pendingState;
      if (latest && latest.projects.length > 0) {
        const compact = buildCompactDesktopJson(latest, true);
        // Не отправляем состояние со ссылками на незагруженные картинки —
        // иначе после перезапуска эти миры пропадут. Обычное сохранение
        // (persistState) догрузит блобы и запишет всё как надо.
        if (compact.safe && compact.body.length < 60000) {
          void fetch('/api/state', {
            method: 'PUT',
            headers: {
              'Content-Type': 'application/json; charset=utf-8',
              'X-Atlas-Token': env.token,
            },
            body: compact.body,
            keepalive: true,
          }).catch(() => {});
        }
      }
    } catch {
      /* игнорируем ошибки при закрытии окна */
    }

    if (closing) {
      try {
        if (sessionEventSource) {
          sessionEventSource.close();
          sessionEventSource = null;
        }
      } catch {
        /* ignore */
      }
      try {
        if (navigator.sendBeacon) {
          navigator.sendBeacon(`/api/bye?t=${encodeURIComponent(env.token)}`, '1');
        }
      } catch {
        /* ignore */
      }
    }
  };

  window.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') {
      flushAndNotify(false);
    }
  });

  window.addEventListener('pagehide', () => {
    flushAndNotify(true);
  });
}

export async function initStorage(): Promise<void> {
  if (initialized) return;
  initialized = true;

  const env = getDesktopEnv();
  if (env) {
    setupDesktopLifecycle(env);
    try {
      const desktopLoaded = await loadDesktopState();
      if (desktopLoaded) {
        hydratedState = desktopLoaded;
        return;
      }
      // Миграция из localStorage, если ранее что-то было открыто на этом же порту.
      const legacy = loadState();
      if (legacy) {
        hydratedState = legacy;
        void saveDesktopStateNow(legacy);
      }
    } catch {
      hydratedState = loadState();
    }
    return;
  }

  try {
    const fromIdb = await idbLoad();
    idbAvailable = true;
    if (fromIdb) {
      hydratedState = fromIdb;
      return;
    }
    const fromLs = loadState();
    if (fromLs) {
      hydratedState = fromLs;
      await idbSave(fromLs).catch(() => {});
    }
  } catch {
    idbAvailable = false;
    hydratedState = loadState();
  }
}

export async function persistState(state: StoredState): Promise<'ok' | 'quota' | 'error'> {
  pendingState = state;
  hydratedState = state;

  if (isDesktopApp()) {
    if (savingInFlight) return 'ok';
    savingInFlight = true;
    let lastResult: 'ok' | 'error' = 'ok';
    try {
      while (pendingState) {
        const snapshot = pendingState;
        pendingState = null;
        lastResult = await saveDesktopStateNow(snapshot);
      }
    } finally {
      savingInFlight = false;
    }
    return lastResult;
  }

  if (idbAvailable) {
    try {
      await idbSave(state);
      return 'ok';
    } catch (err) {
      const quota =
        err instanceof DOMException &&
        (err.name === 'QuotaExceededError' || err.name === 'NS_ERROR_DOM_QUOTA_REACHED');
      if (quota) return 'quota';
    }
  }

  return saveState(state);
}

export async function openDesktopDataFolder(): Promise<boolean> {
  if (!isDesktopApp()) return false;
  try {
    const res = await desktopFetch('/api/open-data-folder', { method: 'POST' });
    return res.ok;
  } catch {
    return false;
  }
}

export async function createDesktopShortcut(): Promise<{ ok: boolean; path?: string }> {
  if (!isDesktopApp()) return { ok: false };
  try {
    const res = await desktopFetch('/api/create-shortcut', { method: 'POST' });
    if (!res.ok) return { ok: false };
    const data = (await res.json()) as { ok?: boolean; shortcutPath?: string };
    return { ok: Boolean(data.ok), path: data.shortcutPath };
  } catch {
    return { ok: false };
  }
}
