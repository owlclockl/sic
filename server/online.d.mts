import type { IncomingMessage, Server, ServerResponse } from 'node:http';

export interface OnlineOptions {
  /** Куда складывать комнаты и картинки. */
  dataDir: string;
  /** Папка со собранным приложением (dist) — для автономного сервера. */
  staticRoot?: string | null;
}

export interface RoomState {
  code: string;
  name: string;
  cols: number;
  rows: number;
  grid: Record<string, unknown>;
  image: { url: string; version: number; name: string; width: number; height: number; mime: string } | null;
  revealed: { cols: number; rows: number; bits: string };
  players: Array<{ id: string; name: string; color: string; cursor: { col: number; row: number } | null; host: boolean }>;
  hostId: string | null;
  progress: number;
  createdAt: number;
  chat: Array<{ id: string; name: string; color: string; text: string; ts: number }>;
}

export interface OnlineInstance {
  store: unknown;
  ws: unknown;
  handleRequest: (req: IncomingMessage, res: ServerResponse) => Promise<void>;
  handleApi: (req: IncomingMessage, res: ServerResponse, url: URL) => Promise<boolean>;
  startHeartbeat: () => void;
  stop: () => void;
}

export function createOnline(options: OnlineOptions): OnlineInstance;

/** Подключает онлайн-режим к уже существующему http.Server (автономный сервер). */
export function attachOnlineServer(httpServer: Server, options?: OnlineOptions): OnlineInstance;

/** Встраивает онлайн-режим в dev-сервер Vite (общий порт с приложением). */
export function attachToViteServer(viteServer: ViteDevServer, options?: OnlineOptions): OnlineInstance;

interface ViteDevServer {
  middlewares: {
    use: (handler: (req: IncomingMessage, res: ServerResponse, next: (err?: unknown) => void) => void) => void;
  };
  httpServer?: unknown;
}
