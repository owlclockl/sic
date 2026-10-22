/**
 * Онлайн-режим Reveal Atlas: HTTP API + WebSocket-протокол.
 *
 * HTTP:
 *   GET  /api/health              — состояние сервера
 *   GET  /api/rooms               — список комнат (лобби)
 *   POST /api/rooms               — создать комнату (загружает картинку мира)
 *   GET  /api/room/:code/image    — картинка мира комнаты
 *   GET  /api/room/:code/state    — состояние комнаты (отладка)
 *   POST /api/room/:code/image    — заменить картинку (только хост)
 *
 * WebSocket (/ws), сообщения — JSON:
 *   → join {code, player:{id,name}}     ← welcome {you, room}
 *   → leave                             ← players
 *   → list                              ← rooms
 *   → paint {cells:[[col,row,value]]}   ← paint {cells, by}   (остальным)
 *   → fill {value}                      ← fill {value, by}
 *   → grid {config}                     ← grid {config, by}
 *   → cursor {col,row}                  ← cursor {id,col,row}
 *   → chat {text}                       ← chat {message}
 *   → name {name}                       ← name {name, by}     (хост)
 *   → ping {ts}                         ← pong {ts}
 */
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { WsServer } from './ws.mjs';
import { RoomStore, normalizeCode } from './rooms.mjs';

const MAX_BODY_BYTES = 64 * 1024 * 1024;
const MAX_JSON_BODY_BYTES = 1024 * 1024;
const RATE_WINDOW_MS = 5000;
const RATE_MAX_MESSAGES = 240;
const CHAT_MIN_INTERVAL_MS = 400;
const CURSOR_MIN_INTERVAL_MS = 40;

const MIME_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.avif': 'image/avif',
  '.gif': 'image/gif',
  '.bmp': 'image/bmp',
  '.ico': 'image/x-icon',
  '.txt': 'text/plain; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
};

/**
 * CORS нужен, чтобы приложение могло лежать отдельно от сервера: например,
 * открытый с Google Диска или с чужого адреса HTML подключается к серверу,
 * поднятому где угодно (свой ПК, VPS, Colab).
 */
const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, X-Atlas-Token',
  'Access-Control-Max-Age': '86400',
};

function sendJson(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    'Content-Length': Buffer.byteLength(body),
    ...CORS_HEADERS,
  });
  res.end(body);
}

function readBody(req, limitBytes) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > limitBytes) {
        reject(new Error('Тело запроса слишком большое.'));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', () => reject(new Error('Ошибка чтения запроса.')));
  });
}

async function readJsonBody(req, limitBytes = MAX_JSON_BODY_BYTES) {
  const raw = await readBody(req, limitBytes);
  if (raw.length === 0) return {};
  try {
    return JSON.parse(raw.toString('utf8'));
  } catch {
    throw new Error('Некорректный JSON в теле запроса.');
  }
}

/** Раздаёт статику из dist/ с SPA-фолбэком. */
function serveStatic(root, req, res, url) {
  if (!root || !fs.existsSync(root)) return false;
  let pathname = decodeURIComponent(url.pathname);
  if (pathname.endsWith('/')) pathname += 'index.html';
  const target = path.normalize(path.join(root, pathname));
  if (!target.startsWith(path.normalize(root))) {
    res.writeHead(403).end('Forbidden');
    return true;
  }
  let filePath = target;
  if (!fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
    // Клиентский роутинг: любой GET отдаём index.html.
    if (req.method === 'GET' && (req.headers.accept ?? '').includes('text/html')) {
      filePath = path.join(root, 'index.html');
    } else {
      return false;
    }
  }
  const ext = path.extname(filePath).toLowerCase();
  const type = MIME_TYPES[ext] ?? 'application/octet-stream';
  const stat = fs.statSync(filePath);
  const etag = `W/"${stat.size}-${Number(stat.mtimeMs).toString(36)}"`;
  if (req.headers['if-none-match'] === etag) {
    res.writeHead(304, { ETag: etag });
    res.end();
    return true;
  }
  const immutable = filePath.includes(`${path.sep}assets${path.sep}`);
  res.writeHead(200, {
    'Content-Type': type,
    'Content-Length': stat.size,
    ETag: etag,
    'Cache-Control': immutable ? 'public, max-age=31536000, immutable' : 'no-cache',
  });
  if (req.method === 'HEAD') {
    res.end();
    return true;
  }
  fs.createReadStream(filePath).pipe(res);
  return true;
}

export function createOnline({ dataDir, staticRoot = null } = {}) {
  const store = new RoomStore({ dataDir });
  const ws = new WsServer({ path: '/ws' });
  const startedAt = Date.now();
  const sweepTimer = setInterval(() => store.sweepIdle(), 60 * 1000);
  if (sweepTimer.unref) sweepTimer.unref();

  /* ── помощники ── */

  function roomOf(conn) {
    if (!conn.data?.roomCode) return null;
    return store.get(conn.data.roomCode);
  }

  function broadcastToRoom(room, message, exceptConn) {
    if (!room) return;
    const text = JSON.stringify(message);
    for (const conn of [...ws.connections]) {
      if (conn === exceptConn) continue;
      if (conn.data?.roomCode !== room.code) continue;
      conn.send(text);
    }
  }

  function fail(conn, code, message) {
    conn.send({ t: 'error', code, message });
  }

  function touchPlayer(room, conn) {
    const player = room.players.get(conn.data.id);
    if (player) {
      player.name = conn.data.name;
    }
    return player ?? null;
  }

  function detach(conn, notify = true) {
    const room = roomOf(conn);
    if (!room || !conn.data?.id) return;
    const holder = room.conns?.get(conn.data.id);
    if (holder === conn) {
      room.conns.delete(conn.data.id);
      store.leave(room, conn.data.id);
      if (notify) broadcastToRoom(room, { t: 'players', players: room.playerList() });
    }
    conn.data.roomCode = null;
  }

  /* ── WebSocket ── */

  ws.on('connection', (conn) => {
    conn.data = { id: null, name: 'Игрок', roomCode: null, count: 0, windowStart: Date.now(), lastChat: 0, lastCursor: 0 };

    conn.on('message', (raw) => {
      let msg;
      try {
        msg = JSON.parse(typeof raw === 'string' ? raw : raw.toString('utf8'));
      } catch {
        return fail(conn, 'bad-json', 'Сообщение не в формате JSON.');
      }
      if (!msg || typeof msg.t !== 'string') return fail(conn, 'bad-message', 'Неизвестное сообщение.');

      const now = Date.now();
      if (now - conn.data.windowStart > RATE_WINDOW_MS) {
        conn.data.windowStart = now;
        conn.data.count = 0;
      }
      conn.data.count += 1;
      if (conn.data.count > RATE_MAX_MESSAGES) {
        conn.close(1008, 'too many messages');
        return;
      }

      try {
        handleMessage(conn, msg, now);
      } catch (err) {
        fail(conn, 'server-error', err instanceof Error ? err.message : 'Внутренняя ошибка сервера.');
      }
    });

    conn.on('close', () => detach(conn));
    conn.on('error', () => detach(conn, false));
  });

  function handleMessage(conn, msg, now) {
    switch (msg.t) {
      case 'join': {
        const code = normalizeCode(msg.code);
        if (!code) return fail(conn, 'bad-code', 'Некорректный код комнаты.');
        const player = {
          id: String(msg.player?.id ?? '').slice(0, 40),
          name: String(msg.player?.name ?? '').slice(0, 60),
        };
        if (!player.id) return fail(conn, 'bad-player', 'Клиент не сообщил свой идентификатор.');

        const previous = roomOf(conn);
        if (previous) detach(conn);

        const result = store.join(code, player);
        if (result.error) return fail(conn, 'join-failed', result.error);
        const room = result.room;
        if (!room.conns) room.conns = new Map();
        const stale = room.conns.get(player.id);
        if (stale && stale !== conn) {
          stale.close(4000, 'подключение заменено новым');
          room.conns.delete(player.id);
        }
        room.conns.set(player.id, conn);
        room.lastEmptyAt = Date.now();
        conn.data.id = player.id;
        conn.data.name = result.player.name;
        conn.data.roomCode = room.code;

        conn.send({ t: 'welcome', you: { id: result.player.id, name: result.player.name, color: result.player.color, host: room.hostId === result.player.id }, room: room.state() });
        broadcastToRoom(room, { t: 'players', players: room.playerList() }, conn);
        broadcastToRoom(room, { t: 'system', text: `${result.player.name} в комнате` }, conn);
        store.persist(room);
        return;
      }

      case 'leave': {
        const room = roomOf(conn);
        const name = conn.data?.name ?? 'Игрок';
        detach(conn);
        if (room) broadcastToRoom(room, { t: 'system', text: `${name} вышел из комнаты` });
        conn.send({ t: 'left' });
        return;
      }

      case 'list': {
        conn.send({ t: 'rooms', rooms: store.list() });
        return;
      }

      case 'paint': {
        const room = roomOf(conn);
        if (!room) return fail(conn, 'no-room', 'Вы не в комнате.');
        const player = touchPlayer(room, conn);
        const cells = Array.isArray(msg.cells) ? msg.cells : [];
        const changed = room.applyCells(cells);
        if (changed.length === 0) return;
        broadcastToRoom(room, { t: 'paint', cells: changed, by: { id: player?.id ?? conn.data.id, name: player?.name ?? conn.data.name, color: player?.color ?? '#7ee0c3' } }, conn);
        store.schedulePersist(room);
        return;
      }

      case 'fill': {
        const room = roomOf(conn);
        if (!room) return fail(conn, 'no-room', 'Вы не в комнате.');
        const player = touchPlayer(room, conn);
        const changed = room.applyFill(Boolean(msg.value));
        if (changed.length === 0) return;
        broadcastToRoom(room, { t: 'fill', value: Boolean(msg.value), by: { id: player?.id ?? conn.data.id, name: player?.name ?? conn.data.name, color: player?.color ?? '#7ee0c3' } }, conn);
        store.persist(room);
        return;
      }

      case 'grid': {
        const room = roomOf(conn);
        if (!room) return fail(conn, 'no-room', 'Вы не в комнате.');
        const player = touchPlayer(room, conn);
        if (!store.setGrid(room, msg.config)) return;
        broadcastToRoom(room, { t: 'grid', config: room.grid, by: { id: player?.id ?? conn.data.id, name: player?.name ?? conn.data.name, color: player?.color ?? '#7ee0c3' } }, conn);
        return;
      }

      case 'cursor': {
        const room = roomOf(conn);
        if (!room) return;
        if (now - conn.data.lastCursor < CURSOR_MIN_INTERVAL_MS) return;
        conn.data.lastCursor = now;
        const player = room.players.get(conn.data.id);
        if (!player) return;
        const col = Number(msg.col);
        const row = Number(msg.row);
        if (!Number.isFinite(col) || !Number.isFinite(row)) return;
        player.cursor = { col: Math.round(col), row: Math.round(row) };
        broadcastToRoom(room, { t: 'cursor', id: player.id, col: player.cursor.col, row: player.cursor.row }, conn);
        return;
      }

      case 'chat': {
        const room = roomOf(conn);
        if (!room) return fail(conn, 'no-room', 'Вы не в комнате.');
        if (now - conn.data.lastChat < CHAT_MIN_INTERVAL_MS) return;
        conn.data.lastChat = now;
        const player = touchPlayer(room, conn);
        const message = store.addChat(room, player ?? { name: conn.data.name }, msg.text);
        if (!message) return;
        broadcastToRoom(room, { t: 'chat', message });
        return;
      }

      case 'name': {
        const room = roomOf(conn);
        if (!room) return fail(conn, 'no-room', 'Вы не в комнате.');
        if (room.hostId !== conn.data.id) return fail(conn, 'not-host', 'Переименовывать мир может только хост.');
        const name = String(msg.name ?? '').replace(/[\u0000-\u001f<>]/g, '').trim().slice(0, 60);
        if (!name) return;
        room.name = name;
        room.updatedAt = Date.now();
        store.persist(room);
        broadcastToRoom(room, { t: 'name', name, by: conn.data.id });
        return;
      }

      case 'ping': {
        conn.send({ t: 'pong', ts: Number(msg.ts) || Date.now() });
        return;
      }

      default:
        fail(conn, 'unknown', `Неизвестная команда: ${msg.t}`);
    }
  }

  /* ── HTTP ── */

  async function handleApi(req, res, url) {
    const pathname = url.pathname;

    if (req.method === 'GET' && pathname === '/api/health') {
      const players = [...store.rooms.values()].reduce((sum, room) => sum + room.players.size, 0);
      sendJson(res, 200, { ok: true, service: 'reveal-atlas-online', rooms: store.rooms.size, players, uptime: Math.round((Date.now() - startedAt) / 1000) });
      return true;
    }

    if (req.method === 'GET' && pathname === '/api/rooms') {
      sendJson(res, 200, { rooms: store.list() });
      return true;
    }

    if (req.method === 'POST' && pathname === '/api/rooms') {
      let body;
      try {
        body = await readJsonBody(req, MAX_BODY_BYTES);
      } catch (err) {
        sendJson(res, 413, { ok: false, error: err.message });
        return true;
      }
      const result = store.create({
        name: body.name,
        image: body.image,
        grid: body.grid,
        revealed: body.revealed,
        player: body.player,
      });
      if (result.error) {
        sendJson(res, 400, { ok: false, error: result.error });
        return true;
      }
      sendJson(res, 200, {
        ok: true,
        code: result.room.code,
        imageUrl: `/api/room/${result.room.code}/image?v=${result.room.image?.version ?? 1}`,
        room: result.room.summary(),
      });
      return true;
    }

    const roomMatch = /^\/api\/room\/([A-Za-z0-9]{4,12})(\/image|\/state)?$/.exec(pathname);
    if (roomMatch) {
      const room = store.get(roomMatch[1]);
      if (!room) {
        sendJson(res, 404, { ok: false, error: 'Комната не найдена.' });
        return true;
      }
      const action = roomMatch[2] ?? '/state';

      if (action === '/image') {
        if (req.method === 'GET') {
          const file = store.imageFile(room);
          if (!file) {
            sendJson(res, 404, { ok: false, error: 'У комнаты нет картинки.' });
            return true;
          }
          const stat = fs.statSync(file);
          const etag = `W/"${stat.size}-${Number(stat.mtimeMs).toString(36)}"`;
          if (req.headers['if-none-match'] === etag) {
            res.writeHead(304, { ETag: etag });
            res.end();
            return true;
          }
          res.writeHead(200, {
            'Content-Type': room.image?.mime ?? 'application/octet-stream',
            'Content-Length': stat.size,
            ETag: etag,
            'Cache-Control': 'public, max-age=3600',
            ...CORS_HEADERS,
          });
          if (req.method === 'HEAD') {
            res.end();
            return true;
          }
          fs.createReadStream(file).pipe(res);
          return true;
        }

        if (req.method === 'POST') {
          let body;
          try {
            body = await readJsonBody(req, MAX_BODY_BYTES);
          } catch (err) {
            sendJson(res, 413, { ok: false, error: err.message });
            return true;
          }
          const playerId = String(body.playerId ?? '');
          if (room.hostId && playerId !== room.hostId) {
            sendJson(res, 403, { ok: false, error: 'Менять картинку может только хост.' });
            return true;
          }
          if (!store.setImage(room, { dataUrl: body.dataUrl, name: body.name, width: body.width, height: body.height })) {
            sendJson(res, 400, { ok: false, error: 'Не удалось прочитать картинку.' });
            return true;
          }
          const image = {
            url: `/api/room/${room.code}/image?v=${room.image.version}`,
            version: room.image.version,
            name: room.image.name,
            width: room.image.width,
            height: room.image.height,
            mime: room.image.mime,
          };
          broadcastToRoom(room, { t: 'image', image, by: playerId });
          sendJson(res, 200, { ok: true, image });
          return true;
        }
      }

      if (req.method === 'GET' && action === '/state') {
        sendJson(res, 200, { room: room.state() });
        return true;
      }
    }

    return false;
  }

  /** Полная обработка запроса: API, затем статика. */
  async function handleRequest(req, res) {
    const url = new URL(req.url ?? '/', `http://${req.headers.host ?? 'localhost'}`);
    try {
      if (url.pathname.startsWith('/api/')) {
        if (req.method === 'OPTIONS') {
          res.writeHead(204, CORS_HEADERS);
          res.end();
          return;
        }
        if (await handleApi(req, res, url)) return;
        sendJson(res, 404, { ok: false, error: 'Неизвестный метод API.' });
        return;
      }
      if (serveStatic(staticRoot, req, res, url)) return;
      sendJson(res, 404, { ok: false, error: 'Not found' });
    } catch (err) {
      if (!res.headersSent) sendJson(res, 500, { ok: false, error: err instanceof Error ? err.message : 'Ошибка сервера.' });
      else res.end();
    }
  }

  /** Обработчик только для /api/* — чтобы встроиться в middleware Vite. */
  async function middleware(req, res, next) {
    const url = new URL(req.url ?? '/', `http://${req.headers.host ?? 'localhost'}`);
    if (!url.pathname.startsWith('/api/')) {
      next();
      return;
    }
    try {
      if (req.method === 'OPTIONS') {
        res.writeHead(204, CORS_HEADERS);
        res.end();
        return;
      }
      if (await handleApi(req, res, url)) return;
      sendJson(res, 404, { ok: false, error: 'Неизвестный метод API.' });
    } catch (err) {
      if (typeof next === 'function') next(err);
      else if (!res.headersSent) sendJson(res, 500, { ok: false, error: 'Ошибка сервера.' });
    }
  }

  function startHeartbeat() {
    ws.startHeartbeat(25000);
  }

  function stop() {
    clearInterval(sweepTimer);
    ws.stopHeartbeat();
    store.flushAll();
    ws.closeAll(1001, 'сервер остановлен');
  }

  return { store, ws, handleRequest, handleApi, middleware, startHeartbeat, stop };
}

/** Подключает онлайн-режим к уже существующему http.Server (автономный сервер). */
export function attachOnlineServer(httpServer, options = {}) {
  const online = createOnline(options);
  httpServer.on('request', (req, res) => {
    void online.handleRequest(req, res);
  });
  httpServer.on('upgrade', (req, socket, head) => online.ws.handleUpgrade(req, socket, head));
  online.startHeartbeat();
  return online;
}

/**
 * Встраивает онлайн-режим в dev-сервер Vite: /api/* уходит в наш обработчик,
 * WebSocket слушает тот же порт, поэтому клиент работает без настроек.
 */
export function attachToViteServer(viteServer, options = {}) {
  const online = createOnline(options);
  viteServer.middlewares.use((req, res, next) => {
    void online.middleware(req, res, next);
  });
  viteServer.httpServer?.on('upgrade', (req, socket, head) => online.ws.handleUpgrade(req, socket, head));
  online.startHeartbeat();
  return online;
}
