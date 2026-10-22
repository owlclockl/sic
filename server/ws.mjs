/**
 * Минимальный WebSocket-сервер по RFC 6455 без внешних зависимостей.
 * Нужен, чтобы «онлайн-режим» Reveal Atlas работал на любом Node.js
 * без установки пакетов из интернета.
 */
import { createHash } from 'node:crypto';
import { EventEmitter } from 'node:events';

export const WS_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11';
export const MAX_PAYLOAD_BYTES = 8 * 1024 * 1024;

const OP_CONT = 0x0;
const OP_TEXT = 0x1;
const OP_BINARY = 0x2;
const OP_CLOSE = 0x8;
const OP_PING = 0x9;
const OP_PONG = 0xa;

/** Ответ на заголовок Sec-WebSocket-Key клиента. */
export function computeAcceptKey(key) {
  return createHash('sha1').update(String(key) + WS_GUID).digest('base64');
}

/** Одно подключение: разбор входящих кадров и отправка сообщений. */
export class WsConnection extends EventEmitter {
  constructor(socket) {
    super();
    this.socket = socket;
    this.buffer = Buffer.alloc(0);
    this.fragments = [];
    this.fragmentOpcode = 0;
    this.closed = false;
    this.closedByPeer = false;
    this.alive = true;
    this.remoteAddress = socket.remoteAddress ?? '';
    /** Произвольные данные приложения (например, игрок). */
    this.data = null;

    socket.setNoDelay(true);
    try {
      socket.setKeepAlive(true, 30000);
    } catch {
      /* не все сокеты поддерживают keep-alive */
    }

    socket.on('data', (chunk) => this._onData(chunk));
    socket.on('error', () => this.destroy());
    socket.on('close', () => this._onSocketClose());
    socket.on('end', () => this._onSocketClose());
  }

  _onSocketClose() {
    if (this.closed) return;
    this.closed = true;
    this.emit('close');
  }

  _onData(chunk) {
    if (this.closed) return;
    this.buffer = this.buffer.length === 0 ? chunk : Buffer.concat([this.buffer, chunk]);
    try {
      this._parse();
    } catch (err) {
      this.close(1002, 'protocol error');
      this.emit('error', err);
    }
  }

  _parse() {
    while (!this.closed) {
      const buf = this.buffer;
      if (buf.length < 2) return;
      const b0 = buf[0];
      const b1 = buf[1];
      const fin = (b0 & 0x80) !== 0;
      const opcode = b0 & 0x0f;
      const masked = (b1 & 0x80) !== 0;
      let length = b1 & 0x7f;
      let offset = 2;

      if (length === 126) {
        if (buf.length < offset + 2) return;
        length = buf.readUInt16BE(offset);
        offset += 2;
      } else if (length === 127) {
        if (buf.length < offset + 8) return;
        const big = buf.readBigUInt64BE(offset);
        if (big > BigInt(MAX_PAYLOAD_BYTES)) {
          this.close(1009, 'message too big');
          return;
        }
        length = Number(big);
        offset += 8;
      }

      const maskOffset = offset;
      if (masked) {
        if (buf.length < offset + 4) return;
        offset += 4;
      }
      if (length > MAX_PAYLOAD_BYTES || buf.length < offset + length) return;

      let payload = buf.subarray(offset, offset + length);
      if (masked) {
        const mask = buf.subarray(maskOffset, maskOffset + 4);
        const plain = Buffer.allocUnsafe(length);
        for (let i = 0; i < length; i++) plain[i] = payload[i] ^ mask[i & 3];
        payload = plain;
      }

      this.buffer = buf.subarray(offset + length);
      this._handleFrame(fin, opcode, payload);
    }
  }

  _handleFrame(fin, opcode, payload) {
    if (opcode === OP_PING) {
      this._sendFrame(OP_PONG, payload);
      return;
    }
    if (opcode === OP_PONG) {
      this.alive = true;
      this.emit('pong', payload);
      return;
    }
    if (opcode === OP_CLOSE) {
      this.closedByPeer = true;
      let code = 1005;
      let reason = '';
      if (payload.length >= 2) {
        code = payload.readUInt16BE(0);
        reason = payload.subarray(2).toString('utf8');
      }
      try {
        this._sendFrame(OP_CLOSE, payload.subarray(0, Math.min(2, payload.length)));
      } catch {
        /* сокет уже мёртв */
      }
      this.closed = true;
      this.emit('closed', code, reason);
      this.emit('close');
      this.socket.end();
      return;
    }

    // Управляющие кадры не могут быть фрагментированы и длиннее 125 байт.
    if (opcode >= 0x8) {
      this.close(1002, 'bad control frame');
      return;
    }

    if (opcode === OP_CONT) {
      if (this.fragments.length === 0) {
        this.close(1002, 'unexpected continuation');
        return;
      }
    } else {
      if (this.fragments.length > 0) {
        this.close(1002, 'expected continuation');
        return;
      }
      this.fragmentOpcode = opcode;
    }

    this.fragments.push(payload);
    if (!fin) return;

    const full = this.fragments.length === 1 ? this.fragments[0] : Buffer.concat(this.fragments);
    const type = this.fragmentOpcode;
    this.fragments = [];
    this.fragmentOpcode = 0;
    this.alive = true;
    if (type === OP_TEXT) this.emit('message', full.toString('utf8'), 'text');
    else if (type === OP_BINARY) this.emit('message', full, 'binary');
    else this.close(1002, 'bad opcode');
  }

  _sendFrame(opcode, payload) {
    if (this.closed || this.socket.destroyed) return false;
    const length = payload.length;
    let headerSize;
    if (length < 126) headerSize = 2;
    else if (length < 65536) headerSize = 4;
    else headerSize = 10;
    const frame = Buffer.allocUnsafe(headerSize + length);
    frame[0] = 0x80 | opcode;
    if (length < 126) {
      frame[1] = length;
    } else if (length < 65536) {
      frame[1] = 126;
      frame.writeUInt16BE(length, 2);
    } else {
      frame[1] = 127;
      frame.writeBigUInt64BE(BigInt(length), 2);
    }
    payload.copy(frame, headerSize);
    try {
      return this.socket.write(frame);
    } catch {
      this.destroy();
      return false;
    }
  }

  /** Отправляет текст (объекты сериализуются в JSON). */
  send(data) {
    if (typeof data === 'string') return this._sendFrame(OP_TEXT, Buffer.from(data, 'utf8'));
    return this._sendFrame(OP_TEXT, Buffer.from(JSON.stringify(data), 'utf8'));
  }

  ping(payload = Buffer.alloc(0)) {
    return this._sendFrame(OP_PING, payload);
  }

  close(code = 1000, reason = '') {
    if (this.closed) return;
    const reasonBuf = Buffer.from(String(reason).slice(0, 120), 'utf8');
    const payload = Buffer.allocUnsafe(2 + reasonBuf.length);
    payload.writeUInt16BE(code, 0);
    reasonBuf.copy(payload, 2);
    try {
      this._sendFrame(OP_CLOSE, payload);
    } catch {
      /* ignore */
    }
    this.closed = true;
    this.socket.end();
    setTimeout(() => this.destroy(), 1000).unref();
  }

  destroy() {
    if (this.closed) {
      try {
        this.socket.destroy();
      } catch {
        /* ignore */
      }
      return;
    }
    this.closed = true;
    try {
      this.socket.destroy();
    } catch {
      /* ignore */
    }
    this.emit('close');
  }
}

/** Принимает WebSocket-подключения на уже существующем http.Server. */
export class WsServer extends EventEmitter {
  constructor(options = {}) {
    super();
    this.path = options.path ?? null;
    this.connections = new Set();
    this._heartbeat = null;
    this._interval = options.heartbeatMs ?? 25000;
  }

  /** Вешается на событие 'upgrade' http.Server. */
  handleUpgrade(req, socket, head) {
    const upgrade = String(req.headers.upgrade ?? '').toLowerCase();
    const key = req.headers['sec-websocket-key'];
    if (upgrade !== 'websocket' || !key || String(req.headers['sec-websocket-version'] ?? '') !== '13') {
      socket.destroy();
      return;
    }
    if (this.path) {
      const path = String(req.url ?? '/').split('?')[0];
      if (path !== this.path) {
        socket.destroy();
        return;
      }
    }

    socket.write(
      `HTTP/1.1 101 Switching Protocols\r\n` +
        `Upgrade: websocket\r\n` +
        `Connection: Upgrade\r\n` +
        `Sec-WebSocket-Accept: ${computeAcceptKey(key)}\r\n\r\n`,
    );

    const conn = new WsConnection(socket);
    this.connections.add(conn);
    conn.on('close', () => this.connections.delete(conn));
    if (head && head.length > 0) conn._onData(head);
    this.emit('connection', conn, req);
  }

  /** Пингует всех и отключает молчащие подключения. */
  startHeartbeat(intervalMs = this._interval, timeoutMs = intervalMs * 2.5) {
    this.stopHeartbeat();
    this._heartbeat = setInterval(() => {
      for (const conn of [...this.connections]) {
        if (!conn.alive) {
          conn.close(1001, 'timeout');
          continue;
        }
        conn.alive = false;
        conn.ping();
      }
      void timeoutMs;
    }, intervalMs);
    if (this._heartbeat.unref) this._heartbeat.unref();
  }

  stopHeartbeat() {
    if (this._heartbeat) clearInterval(this._heartbeat);
    this._heartbeat = null;
  }

  broadcast(data, except) {
    const text = typeof data === 'string' ? data : JSON.stringify(data);
    for (const conn of [...this.connections]) {
      if (except && conn === except) continue;
      conn.send(text);
    }
  }

  closeAll(code = 1001, reason = 'server shutdown') {
    for (const conn of [...this.connections]) conn.close(code, reason);
  }
}
