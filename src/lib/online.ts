/**
 * Клиент онлайн-режима: настоящее сетевое подключение по WebSocket.
 *
 * Клиент сам переподключается при обрыве связи, буферизует мазки,
 * сделанные офлайн, и складывает их в одно сообщение при восстановлении.
 */
import type { GridConfig } from './grid';

export type OnlineStatus = 'idle' | 'connecting' | 'online' | 'reconnecting' | 'error';

export type Cell = [number, number, number];

export interface OnlinePlayer {
  id: string;
  name: string;
  color: string;
  cursor: { col: number; row: number } | null;
  host: boolean;
}

export interface OnlineRoomInfo {
  code: string;
  name: string;
  players: number;
  progress: number;
  cols: number;
  rows: number;
  createdAt: number;
  hasImage: boolean;
}

export interface OnlineChatMessage {
  id: string;
  name: string;
  color: string;
  text: string;
  ts: number;
}

export interface OnlineRoomImage {
  url: string;
  version: number;
  name: string;
  width: number;
  height: number;
  mime: string;
}

export interface OnlineRoomState {
  code: string;
  name: string;
  cols: number;
  rows: number;
  grid: GridConfig;
  revealed: { cols: number; rows: number; bits: string };
  image: OnlineRoomImage | null;
  players: OnlinePlayer[];
  hostId: string | null;
  progress: number;
  createdAt: number;
  chat: OnlineChatMessage[];
}

export interface OnlineIdentity {
  id: string;
  name: string;
  color: string;
  host: boolean;
}

export type OnlineEvent =
  | { type: 'status'; status: OnlineStatus; detail?: string }
  | { type: 'welcome'; you: OnlineIdentity; room: OnlineRoomState }
  | { type: 'players'; players: OnlinePlayer[] }
  | { type: 'paint'; cells: Cell[]; by: { id: string; name: string; color: string } }
  | { type: 'fill'; value: boolean; by: { id: string; name: string; color: string } }
  | { type: 'grid'; config: GridConfig; by: { id: string; name: string; color: string } }
  | { type: 'cursor'; id: string; col: number; row: number }
  | { type: 'chat'; message: OnlineChatMessage }
  | { type: 'system'; text: string }
  | { type: 'image'; image: OnlineRoomImage; by: string }
  | { type: 'name'; name: string; by: string }
  | { type: 'rooms'; rooms: OnlineRoomInfo[] }
  | { type: 'error'; code: string; message: string }
  | { type: 'left' };

export interface OnlineClientHandlers {
  onEvent: (event: OnlineEvent) => void;
}

const PLAYER_KEY = 'reveal-atlas/online-player';
const SERVER_KEY = 'reveal-atlas/online-server';
const ROOM_KEY = 'reveal-atlas/online-room';
const RECONNECT_BASE_MS = 600;
const RECONNECT_MAX_MS = 8000;
const CURSOR_INTERVAL_MS = 60;

function randomId(): string {
  return `p-${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36).slice(-4)}`;
}

export function loadPlayerIdentity(): { id: string; name: string } {
  try {
    const raw = localStorage.getItem(PLAYER_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as { id?: unknown; name?: unknown };
      if (typeof parsed.id === 'string' && parsed.id) {
        return { id: parsed.id, name: typeof parsed.name === 'string' ? parsed.name : '' };
      }
    }
  } catch {
    /* приватный режим */
  }
  return { id: randomId(), name: '' };
}

export function savePlayerIdentity(identity: { id: string; name: string }): void {
  try {
    localStorage.setItem(PLAYER_KEY, JSON.stringify(identity));
  } catch {
    /* приватный режим */
  }
}

export function loadServerUrl(): string {
  try {
    return localStorage.getItem(SERVER_KEY) ?? '';
  } catch {
    return '';
  }
}

export function saveServerUrl(url: string): void {
  try {
    localStorage.setItem(SERVER_KEY, url);
  } catch {
    /* приватный режим */
  }
}

export function loadLastRoom(): string {
  try {
    return localStorage.getItem(ROOM_KEY) ?? '';
  } catch {
    return '';
  }
}

export function saveLastRoom(code: string): void {
  try {
    if (code) localStorage.setItem(ROOM_KEY, code);
    else localStorage.removeItem(ROOM_KEY);
  } catch {
    /* приватный режим */
  }
}

/** Приводит адрес сервера к виду http(s)://host:port без завершающего слэша. */
export function normalizeServerUrl(raw: string): string {
  let value = String(raw ?? '').trim();
  if (!value) return '';
  if (!/^https?:\/\//i.test(value)) value = `http://${value}`;
  return value.replace(/\/+$/, '');
}

export function serverWebSocketUrl(raw: string): string {
  const base = normalizeServerUrl(raw) || (typeof window !== 'undefined' ? window.location.origin : '');
  return base.replace(/^http/i, 'ws').replace(/\/+$/, '') + '/ws';
}

/** Адрес сервера по умолчанию — тот же origin, с которым открыто приложение. */
export function defaultServerUrl(): string {
  if (typeof window === 'undefined') return '';
  return normalizeServerUrl(window.location.origin);
}

export interface CreateRoomPayload {
  name: string;
  image: { dataUrl: string; name: string; width: number; height: number };
  grid: GridConfig;
  revealed: { cols: number; rows: number; bits: string };
}

export class OnlineClient {
  private ws: WebSocket | null = null;
  private handlers: OnlineClientHandlers;
  private serverUrl: string;
  private identity: { id: string; name: string };
  private roomCode: string | null = null;
  private intentionalClose = false;
  private reconnectAttempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private bufferedCells = new Map<string, Cell>();
  private bufferedFills: boolean[] = [];
  private lastCursorAt = 0;
  private lastPingAt = 0;
  private status: OnlineStatus = 'idle';

  constructor(options: { serverUrl?: string; identity?: { id: string; name: string } } & OnlineClientHandlers) {
    this.handlers = handlersSafe(options);
    this.serverUrl = normalizeServerUrl(options.serverUrl ?? '') || defaultServerUrl();
    this.identity = options.identity ?? { id: randomId(), name: '' };
  }

  getStatus(): OnlineStatus {
    return this.status;
  }

  getRoomCode(): string | null {
    return this.roomCode;
  }

  getServer(): string {
    return this.serverUrl;
  }

  setIdentity(patch: { id?: string; name?: string }): void {
    if (patch.id) this.identity.id = patch.id;
    if (typeof patch.name === 'string') this.identity.name = patch.name;
  }

  /** Меняет адрес сервера на лету (без пересоздания клиента). */
  setServerUrl(url: string): void {
    this.serverUrl = normalizeServerUrl(url) || defaultServerUrl();
  }

  private emit(event: OnlineEvent): void {
    this.handlers.onEvent(event);
  }

  private setStatus(status: OnlineStatus, detail?: string): void {
    if (this.status === status && !detail) return;
    this.status = status;
    this.emit({ type: 'status', status, detail });
  }

  private baseHttp(): string {
    return normalizeServerUrl(this.serverUrl) || defaultServerUrl();
  }

  /* ── соединение ── */

  connect(): void {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    this.intentionalClose = false;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.setStatus(this.roomCode ? 'reconnecting' : 'connecting');
    let socket: WebSocket;
    try {
      socket = new WebSocket(serverWebSocketUrl(this.serverUrl));
    } catch (err) {
      this.setStatus('error', err instanceof Error ? err.message : 'Не удалось открыть соединение.');
      this.scheduleReconnect();
      return;
    }
    this.ws = socket;

    socket.onopen = () => {
      this.reconnectAttempt = 0;
      this.setStatus('online');
      this.flushBuffer();
      if (this.roomCode) {
        this.send({ t: 'join', code: this.roomCode, player: { id: this.identity.id, name: this.identity.name } });
      }
    };

    socket.onmessage = (event) => {
      if (typeof event.data !== 'string') return;
      let msg: Record<string, unknown>;
      try {
        msg = JSON.parse(event.data) as Record<string, unknown>;
      } catch {
        return;
      }
      this.handleMessage(msg);
    };

    socket.onerror = () => {
      /* onclose всё равно сработает — там логика переподключения */
    };

    socket.onclose = () => {
      this.ws = null;
      if (this.intentionalClose) {
        this.setStatus('idle');
        return;
      }
      if (this.roomCode) this.scheduleReconnect();
      else this.setStatus('idle');
    };
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) return;
    this.setStatus('reconnecting');
    const delay = Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** this.reconnectAttempt);
    this.reconnectAttempt += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (this.intentionalClose) return;
      this.connect();
    }, delay);
  }

  disconnect(): void {
    this.intentionalClose = true;
    this.roomCode = null;
    this.bufferedCells.clear();
    this.bufferedFills = [];
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    const socket = this.ws;
    this.ws = null;
    if (socket) {
      try {
        socket.close(1000, 'client left');
      } catch {
        /* уже закрыт */
      }
    }
    this.setStatus('idle');
  }

  /* ── отправка ── */

  private send(message: Record<string, unknown>): boolean {
    const socket = this.ws;
    if (!socket || socket.readyState !== WebSocket.OPEN) return false;
    try {
      socket.send(JSON.stringify(message));
      return true;
    } catch {
      return false;
    }
  }

  /** Буферизует клетки, нарисованные без связи: при переподключении уйдут одним мазком. */
  private flushBuffer(): void {
    if (this.bufferedCells.size === 0 && this.bufferedFills.length === 0) return;
    const cells = [...this.bufferedCells.values()];
    this.bufferedCells.clear();
    const fills = this.bufferedFills;
    this.bufferedFills = [];
    for (const value of fills) this.send({ t: 'fill', value });
    if (cells.length > 0) this.send({ t: 'paint', cells });
  }

  sendPaint(cells: Cell[]): void {
    if (cells.length === 0) return;
    if (!this.send({ t: 'paint', cells })) {
      for (const cell of cells) this.bufferedCells.set(`${cell[0]}:${cell[1]}`, cell);
    }
  }

  sendFill(value: boolean): void {
    if (!this.send({ t: 'fill', value })) this.bufferedFills.push(value);
  }

  sendGrid(config: GridConfig): void {
    this.send({ t: 'grid', config });
  }

  sendCursor(col: number, row: number): void {
    const now = Date.now();
    if (now - this.lastCursorAt < CURSOR_INTERVAL_MS) return;
    this.lastCursorAt = now;
    this.send({ t: 'cursor', col, row });
  }

  sendChat(text: string): void {
    const clean = text.trim().slice(0, 300);
    if (!clean) return;
    this.send({ t: 'chat', text: clean });
  }

  sendWorldName(name: string): void {
    this.send({ t: 'name', name: name.trim().slice(0, 60) });
  }

  requestRooms(): void {
    this.send({ t: 'list' });
  }

  ping(): void {
    this.lastPingAt = Date.now();
    this.send({ t: 'ping', ts: this.lastPingAt });
  }

  /* ── команды уровня комнаты ── */

  joinRoom(code: string): void {
    const clean = code.toUpperCase().replace(/[^A-Z0-9]/g, '');
    if (!clean) return;
    this.roomCode = clean;
    saveLastRoom(clean);
    this.connect();
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.send({ t: 'join', code: clean, player: { id: this.identity.id, name: this.identity.name } });
    }
  }

  leaveRoom(): void {
    this.send({ t: 'leave' });
    this.roomCode = null;
    saveLastRoom('');
    this.disconnect();
  }

  /* ── приём ── */

  private handleMessage(msg: Record<string, unknown>): void {
    const type = String(msg.t ?? '');
    switch (type) {
      case 'welcome': {
        const room = msg.room as OnlineRoomState | undefined;
        if (room) this.emit({ type: 'welcome', you: msg.you as OnlineIdentity, room });
        return;
      }
      case 'players':
        this.emit({ type: 'players', players: (msg.players as OnlinePlayer[]) ?? [] });
        return;
      case 'paint':
        this.emit({ type: 'paint', cells: (msg.cells as Cell[]) ?? [], by: msg.by as { id: string; name: string; color: string } });
        return;
      case 'fill':
        this.emit({ type: 'fill', value: Boolean(msg.value), by: msg.by as { id: string; name: string; color: string } });
        return;
      case 'grid':
        this.emit({ type: 'grid', config: msg.config as GridConfig, by: msg.by as { id: string; name: string; color: string } });
        return;
      case 'cursor':
        this.emit({ type: 'cursor', id: String(msg.id ?? ''), col: Number(msg.col), row: Number(msg.row) });
        return;
      case 'chat':
        this.emit({ type: 'chat', message: msg.message as OnlineChatMessage });
        return;
      case 'system':
        this.emit({ type: 'system', text: String(msg.text ?? '') });
        return;
      case 'image':
        this.emit({ type: 'image', image: msg.image as OnlineRoomImage, by: String(msg.by ?? '') });
        return;
      case 'name':
        this.emit({ type: 'name', name: String(msg.name ?? ''), by: String(msg.by ?? '') });
        return;
      case 'rooms':
        this.emit({ type: 'rooms', rooms: (msg.rooms as OnlineRoomInfo[]) ?? [] });
        return;
      case 'error':
        this.emit({ type: 'error', code: String(msg.code ?? ''), message: String(msg.message ?? '') });
        return;
      case 'pong':
        return;
      case 'left':
        this.roomCode = null;
        saveLastRoom('');
        this.emit({ type: 'left' });
        return;
      default:
        return;
    }
  }

  /* ── HTTP ── */

  async createRoom(payload: CreateRoomPayload): Promise<{ code: string; imageUrl: string }> {
    const res = await fetch(`${this.baseHttp()}/api/rooms`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: JSON.stringify(payload),
    });
    const data = (await res.json().catch(() => null)) as { ok?: boolean; code?: string; imageUrl?: string; error?: string } | null;
    if (!res.ok || !data?.ok || !data.code) {
      throw new Error(data?.error ?? `Сервер ответил ${res.status}.`);
    }
    return { code: data.code, imageUrl: data.imageUrl ?? '' };
  }

  async listRooms(): Promise<OnlineRoomInfo[]> {
    const res = await fetch(`${this.baseHttp()}/api/rooms`, { cache: 'no-store' });
    if (!res.ok) throw new Error(`Сервер ответил ${res.status}.`);
    const data = (await res.json().catch(() => null)) as { rooms?: OnlineRoomInfo[] } | null;
    return data?.rooms ?? [];
  }

  async replaceRoomImage(code: string, image: { dataUrl: string; name: string; width: number; height: number }): Promise<OnlineRoomImage> {
    const res = await fetch(`${this.baseHttp()}/api/room/${encodeURIComponent(code)}/image`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: JSON.stringify({ ...image, playerId: this.identity.id }),
    });
    const data = (await res.json().catch(() => null)) as { ok?: boolean; image?: OnlineRoomImage; error?: string } | null;
    if (!res.ok || !data?.ok || !data.image) {
      throw new Error(data?.error ?? `Сервер ответил ${res.status}.`);
    }
    return data.image;
  }

  async health(): Promise<{ ok: boolean; rooms?: number; players?: number; error?: string }> {
    try {
      const res = await fetch(`${this.baseHttp()}/api/health`, { cache: 'no-store' });
      if (!res.ok) return { ok: false, error: `Сервер ответил ${res.status}.` };
      const data = (await res.json().catch(() => null)) as { rooms?: number; players?: number } | null;
      return { ok: true, rooms: data?.rooms ?? 0, players: data?.players ?? 0 };
    } catch (err) {
      return { ok: false, error: err instanceof Error ? err.message : 'Сервер недоступен.' };
    }
  }
}

/** Защита от «вызвали onEvent до того, как подписались». */
function handlersSafe(options: OnlineClientHandlers): OnlineClientHandlers {
  if (typeof options.onEvent !== 'function') {
    return { onEvent: () => {} };
  }
  return options;
}

/** Читает картинку комнаты как data:URL — тогда её можно сохранять и показывать как обычно. */
export async function fetchImageAsDataUrl(url: string): Promise<string> {
  const res = await fetch(url, { cache: 'no-store' });
  if (!res.ok) throw new Error(`Картинка комнаты недоступна (${res.status}).`);
  const blob = await res.blob();
  return await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('Не удалось прочитать картинку комнаты.'));
    reader.onload = () => resolve(String(reader.result ?? ''));
    reader.readAsDataURL(blob);
  });
}
