import { useEffect, useMemo, useRef, useState } from 'react';
import type { OnlineIdentity, OnlineRoomInfo, OnlineRoomState, OnlineStatus } from '../lib/online';

export type ChatEntry =
  | { kind: 'chat'; id: string; name: string; color: string; text: string; ts: number }
  | { kind: 'system'; id: string; text: string; ts: number };

interface OnlinePanelProps {
  status: OnlineStatus;
  serverUrl: string;
  playerName: string;
  room: OnlineRoomState | null;
  you: OnlineIdentity | null;
  rooms: OnlineRoomInfo[];
  chat: ChatEntry[];
  unread: number;
  error: string | null;
  busy: boolean;
  onCreateRoom: () => void;
  onJoinRoom: (code: string) => void;
  onLeaveRoom: () => void;
  onRefreshRooms: () => void;
  onSendChat: (text: string) => void;
  onServerUrlChange: (url: string) => void;
  onPlayerNameChange: (name: string) => void;
  onCopyInvite: () => void;
  onClose: () => void;
}

/**
 * Приложение запущено как нативное (APK), а не в браузере: тогда origin
 * приложения — https://localhost, и онлайн-сервер надо указать вручную.
 */
function isNativeApp(): boolean {
  if (typeof window === 'undefined') return false;
  const cap = (window as unknown as {
    Capacitor?: { isNativePlatform?: () => boolean; getPlatform?: () => string };
  }).Capacitor;
  try {
    if (typeof cap?.isNativePlatform === 'function' && cap.isNativePlatform()) return true;
    const platform = typeof cap?.getPlatform === 'function' ? cap.getPlatform() : '';
    return platform === 'android' || platform === 'ios';
  } catch {
    return false;
  }
}

const STATUS_TEXT: Record<OnlineStatus, string> = {
  idle: 'Не в сети',
  connecting: 'Подключение…',
  online: 'На связи',
  reconnecting: 'Переподключение…',
  error: 'Ошибка связи',
};

function formatTime(ts: number): string {
  try {
    return new Date(ts).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '';
  }
}

export function OnlinePanel({
  status,
  serverUrl,
  playerName,
  room,
  you,
  rooms,
  chat,
  unread,
  error,
  busy,
  onCreateRoom,
  onJoinRoom,
  onLeaveRoom,
  onRefreshRooms,
  onSendChat,
  onServerUrlChange,
  onPlayerNameChange,
  onCopyInvite,
  onClose,
}: OnlinePanelProps) {
  const [code, setCode] = useState('');
  const [draft, setDraft] = useState('');
  const chatEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ block: 'end' });
  }, [chat.length]);

  const inviteLink = useMemo(() => {
    if (!room) return '';
    if (typeof window === 'undefined') return '';
    const url = new URL(window.location.href);
    url.search = '';
    url.hash = '';
    url.searchParams.set('room', room.code);
    return url.toString();
  }, [room]);

  const inRoom = Boolean(room);

  return (
    <aside className="settings online">
      <div className="settings-head">
        <span className="sidebar-title">Онлайн</span>
        {inRoom && unread > 0 && <span className="badge">{unread}</span>}
        <button className="icon-btn" onClick={onClose} aria-label="Скрыть онлайн-панель">
          ✕
        </button>
      </div>

      <section className="settings-section">
        <div className={`online-status is-${status}`}>
          <span className="online-dot" aria-hidden />
          <span className="online-status-text">{inRoom ? `Комната ${room?.code}` : STATUS_TEXT[status]}</span>
          {room && <span className="online-players">{room.players.length} игр.</span>}
        </div>
        <p className="hint">
          {inRoom
            ? 'Мир открывается сообща: каждый мазок сразу виден всем в комнате.'
            : 'Создайте комнату и отправьте другу код или ссылку — прогресс будет общим.'}
        </p>
        {error && <p className="hint online-error">{error}</p>}
      </section>

      <section className="settings-section">
        <div className="section-title">Игрок</div>
        <input
          className="text-input"
          value={playerName}
          maxLength={24}
          placeholder="Как вас зовут"
          onChange={(e) => onPlayerNameChange(e.target.value)}
        />
        <label className="field-label" htmlFor="online-server">
          Сервер
        </label>
        <input
          id="online-server"
          className="text-input"
          value={serverUrl}
          placeholder="http://localhost:8080"
          spellCheck={false}
          onChange={(e) => onServerUrlChange(e.target.value)}
        />
        <p className="hint">
          {isNativeApp()
            ? 'Это приложение для Android: localhost здесь ни к чему не подключится. Впишите адрес сервера (например, https://atlas.trycloudflare.com), иначе доступна только одиночная игра.'
            : 'По умолчанию — тот же адрес, с которым открыто приложение. Свой сервер: <code>npm run online</code>.'}
        </p>
      </section>

      {!inRoom && (
        <section className="settings-section">
          <div className="section-title">Комната</div>
          <button className="btn btn-primary btn-block" onClick={onCreateRoom} disabled={busy}>
            {busy ? 'Создаём…' : '+ Создать комнату из этого мира'}
          </button>
          <div className="online-join">
            <input
              className="text-input"
              value={code}
              placeholder="код комнаты"
              maxLength={12}
              spellCheck={false}
              onChange={(e) => setCode(e.target.value.toUpperCase())}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && code.trim()) onJoinRoom(code.trim());
              }}
            />
            <button className="btn btn-ghost" onClick={() => onJoinRoom(code.trim())} disabled={!code.trim()}>
              Войти
            </button>
          </div>
          <div className="online-rooms-head">
            <span className="field-label">Открытые комнаты</span>
            <button className="btn btn-ghost online-refresh" onClick={onRefreshRooms} disabled={busy}>
              Обновить
            </button>
          </div>
          <ul className="online-rooms">
            {rooms.length === 0 && <li className="empty-note">Пока никого. Создайте первую комнату!</li>}
            {rooms.map((item) => (
              <li key={item.code}>
                <button className="online-room" onClick={() => onJoinRoom(item.code)}>
                  <span className="online-room-code">{item.code}</span>
                  <span className="online-room-name" title={item.name}>
                    {item.name}
                  </span>
                  <span className="online-room-meta">
                    {item.players} игр. · {Math.round(item.progress * 100)}%
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {inRoom && room && (
        <>
          <section className="settings-section">
            <div className="section-title">Приглашение</div>
            <div className="online-code">{room.code}</div>
            <button className="btn btn-ghost btn-block" onClick={onCopyInvite}>
              Скопировать ссылку-приглашение
            </button>
            {inviteLink && <p className="hint online-link">{inviteLink}</p>}
            <button className="btn btn-danger btn-block" onClick={onLeaveRoom}>
              Выйти из комнаты
            </button>
          </section>

          <section className="settings-section">
            <div className="section-title">Игроки ({room.players.length})</div>
            <ul className="online-players-list">
              {room.players.map((player) => (
                <li key={player.id}>
                  <span className="online-player-dot" style={{ background: player.color }} aria-hidden />
                  <span className="online-player-name">{player.name || 'Игрок'}</span>
                  {player.host && <span className="badge">хост</span>}
                  {you?.id === player.id && <span className="badge">вы</span>}
                </li>
              ))}
            </ul>
            <div className="progress-line">
              <div className="progress-track">
                <div className="progress-fill" style={{ width: `${Math.min(100, room.progress * 100)}%` }} />
              </div>
              <span className="progress-value">{Math.round(room.progress * 100)}%</span>
            </div>
          </section>

          <section className="settings-section online-chat-section">
            <div className="section-title">Чат комнаты</div>
            <div className="online-chat">
              {chat.length === 0 && <div className="empty-note">Пока тихо. Напишите первым!</div>}
              {chat.map((entry) =>
                entry.kind === 'system' ? (
                  <div key={entry.id} className="online-chat-system">
                    {entry.text}
                  </div>
                ) : (
                  <div key={entry.id} className="online-chat-message">
                    <span className="online-chat-author" style={{ color: entry.color }}>
                      {entry.name}
                    </span>
                    <span className="online-chat-text">{entry.text}</span>
                    <span className="online-chat-time">{formatTime(entry.ts)}</span>
                  </div>
                ),
              )}
              <div ref={chatEndRef} />
            </div>
            <div className="online-join">
              <input
                className="text-input"
                value={draft}
                placeholder="Сообщение"
                maxLength={300}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && draft.trim()) {
                    onSendChat(draft.trim());
                    setDraft('');
                  }
                }}
              />
              <button
                className="btn btn-ghost"
                onClick={() => {
                  if (!draft.trim()) return;
                  onSendChat(draft.trim());
                  setDraft('');
                }}
                disabled={!draft.trim()}
              >
                Отправить
              </button>
            </div>
          </section>
        </>
      )}
    </aside>
  );
}
