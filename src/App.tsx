import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { DragEvent as ReactDragEvent } from 'react';
import { MapView, type MapHandle, type RemoteCursor, type Tool } from './components/MapView';
import { Sidebar } from './components/Sidebar';
import { SettingsPanel } from './components/SettingsPanel';
import { OnlinePanel, type ChatEntry } from './components/OnlinePanel';
import {
  DEFAULT_GRID,
  computeGrid,
  type Grid,
  type GridConfig,
} from './lib/grid';
import {
  applyCells,
  applyCellsToEncoded,
  countBitsInEncoded,
  countRevealed,
  emptyRevealed,
  fillEncoded,
  remapRevealed,
  resizeRevealed,
  type Revealed,
} from './lib/bits';
import {
  createProject,
  downloadJson,
  newId,
  normalizeProject,
  projectRevealed,
  serializeRevealed,
  type Project,
  type ProjectImage,
} from './lib/projects';
import {
  createDesktopShortcut,
  getInitialStoredState,
  openDesktopDataFolder,
  persistState,
  registerFlushProvider,
} from './lib/storage';
import { readImage, recommendedMaxSide, shrinkImage } from './lib/image';
import { createDemoWorld } from './lib/demoWorld';
import {
  defaultServerUrl,
  fetchImageAsDataUrl,
  loadLastRoom,
  loadPlayerIdentity,
  loadServerUrl,
  OnlineClient,
  saveLastRoom,
  savePlayerIdentity,
  saveServerUrl,
  normalizeServerUrl,
  type OnlineEvent,
  type OnlineIdentity,
  type OnlineRoomInfo,
  type OnlineRoomState,
  type OnlineStatus,
} from './lib/online';

type Toast = { kind: 'info' | 'error'; text: string } | null;

interface Snapshot {
  revealed: Revealed;
  grid: Grid;
}

const DEMO_GRID: GridConfig = { ...DEFAULT_GRID, tileSize: 64 };
const BRUSH_SIZES = [1, 2, 3, 5];
const MOBILE_HINT_KEY = 'reveal-atlas/mobile-hint';

interface OnlineState {
  status: OnlineStatus;
  serverUrl: string;
  playerName: string;
  playerId: string;
  room: OnlineRoomState | null;
  you: OnlineIdentity | null;
  rooms: OnlineRoomInfo[];
  chat: ChatEntry[];
  unread: number;
  error: string | null;
  busy: boolean;
}

function roomProjectIdOf(code: string): string {
  return `room-${code}`;
}

/** Абсолютный адрес картинки комнаты относительно выбранного сервера. */
function absoluteUrl(serverUrl: string, path: string): string {
  try {
    return new URL(path, normalizeServerUrl(serverUrl) || defaultServerUrl()).toString();
  } catch {
    return path;
  }
}

function chatFromRoom(room: OnlineRoomState): ChatEntry[] {
  return (room.chat ?? []).map((message) => ({ kind: 'chat', ...message }));
}

let demoImageCache: ProjectImage | null = null;
function demoImage(): ProjectImage {
  if (demoImageCache) return demoImageCache;
  const world = createDemoWorld();
  demoImageCache = { name: 'Демо-мир', width: world.width, height: world.height, dataUrl: world.dataUrl };
  return demoImageCache;
}

function initialState(): { projects: Project[]; activeId: string | null } {
  const stored = getInitialStoredState();
  if (stored && stored.projects.length > 0) {
    return { projects: stored.projects, activeId: stored.activeId ?? stored.projects[0].id };
  }
  const project = createProject('Демо-мир', demoImage(), DEMO_GRID);
  return { projects: [project], activeId: project.id };
}

export default function App() {
  const [{ projects: initialProjects, activeId: initialActiveId }] = useState(initialState);
  const [projects, setProjects] = useState<Project[]>(initialProjects);
  const [activeId, setActiveId] = useState<string | null>(initialActiveId);
  const [revealed, setRevealed] = useState<Revealed>(() => {
    const active = initialProjects.find((p) => p.id === initialActiveId) ?? initialProjects[0];
    if (!active) return emptyRevealed(1, 1);
    const g = computeGrid(active.image.width, active.image.height, active.grid);
    return projectRevealed(active, g.cols, g.rows);
  });
  const [gridConfig, setGridConfig] = useState<GridConfig>(() => {
    const active = initialProjects.find((p) => p.id === initialActiveId) ?? initialProjects[0];
    return active ? active.grid : DEFAULT_GRID;
  });
  const [brushSize, setBrushSize] = useState<number>(() => {
    const active = initialProjects.find((p) => p.id === initialActiveId) ?? initialProjects[0];
    return active?.brushSize ?? 1;
  });
  const [tool, setTool] = useState<Tool>('reveal');
  const [showGrid, setShowGrid] = useState(true);
  const [settingsOpen, setSettingsOpen] = useState(() => window.innerWidth > 1180);
  const [sidebarOpen, setSidebarOpen] = useState(() => window.innerWidth > 980);
  const [scale, setScale] = useState(1);
  const [toast, setToast] = useState<Toast>(null);
  const [dragOver, setDragOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [mobileHint, setMobileHint] = useState(() => {
    if (typeof window === 'undefined' || window.innerWidth > 980) return false;
    try {
      return localStorage.getItem(MOBILE_HINT_KEY) !== 'off';
    } catch {
      return true;
    }
  });

  const mapRef = useRef<MapHandle | null>(null);
  const undoRef = useRef<Snapshot[]>([]);
  const [undoCount, setUndoCount] = useState(0);

  const activeProject = useMemo(
    () => projects.find((p) => p.id === activeId) ?? null,
    [projects, activeId],
  );

  const image = activeProject?.image ?? demoImage();
  const grid = useMemo(() => computeGrid(image.width, image.height, gridConfig), [image, gridConfig]);
  const totalCells = grid.cols * grid.rows;
  const revealedCount = useMemo(() => countRevealed(revealed), [revealed]);
  const progress = totalCells > 0 ? Math.min(1, revealedCount / totalCells) : 0;

  const latestRef = useRef({ activeId, gridConfig, brushSize, revealed, projects });
  latestRef.current = { activeId, gridConfig, brushSize, revealed, projects };
  const dragDepthRef = useRef(0);

  /* ─── онлайн-режим ─── */

  const initialIdentity = useRef(loadPlayerIdentity());
  const [onlineOpen, setOnlineOpen] = useState(false);
  const [online, setOnline] = useState<OnlineState>(() => ({
    status: 'idle',
    serverUrl: loadServerUrl() || defaultServerUrl(),
    playerName: initialIdentity.current.name,
    playerId: initialIdentity.current.id,
    room: null,
    you: null,
    rooms: [],
    chat: [],
    unread: 0,
    error: null,
    busy: false,
  }));
  const [remoteCursors, setRemoteCursors] = useState<RemoteCursor[]>([]);

  const clientRef = useRef<OnlineClient | null>(null);
  /** projectId комнаты → адрес картинки, из которого получен текущий dataUrl. */
  const roomImageUrls = useRef(new Map<string, string>());
  const roomProjectId = online.room ? roomProjectIdOf(online.room.code) : null;
  const roomProjectIdRef = useRef<string | null>(null);
  roomProjectIdRef.current = roomProjectId;
  const roomActive = Boolean(roomProjectId && activeProject?.id === roomProjectId);
  const roomActiveRef = useRef(false);
  roomActiveRef.current = roomActive;
  const onlineOpenRef = useRef(onlineOpen);
  onlineOpenRef.current = onlineOpen;

  useEffect(() => {
    document.title = activeProject ? `${activeProject.name} — Reveal Atlas` : 'Reveal Atlas';
  }, [activeProject]);

  useEffect(() => {
    registerFlushProvider(() => {
      const { activeId: id, gridConfig: cfg, brushSize: brush, revealed: rev, projects: list } = latestRef.current;
      const merged = id
        ? list.map((p) =>
            p.id === id
              ? {
                  ...p,
                  grid: cfg,
                  brushSize: brush,
                  revealed: serializeRevealed(rev.cols, rev.rows, rev.bits),
                  updatedAt: Date.now(),
                }
              : p,
          )
        : list;
      return { version: 1, activeId: id, projects: merged };
    });
    return () => registerFlushProvider(null);
  }, []);

  useEffect(() => {
    const preventFileNav = (e: DragEvent) => {
      if (e.dataTransfer?.types?.includes('Files')) {
        e.preventDefault();
      }
    };
    window.addEventListener('dragover', preventFileNav);
    window.addEventListener('drop', preventFileNav);
    return () => {
      window.removeEventListener('dragover', preventFileNav);
      window.removeEventListener('drop', preventFileNav);
    };
  }, []);

  const showToast = useCallback((kind: 'info' | 'error', text: string) => {
    setToast({ kind, text });
  }, []);

  const toggleDrawer = useCallback(
    (which: 'worlds' | 'settings' | 'online') => {
      if (which === 'worlds') {
        const next = !sidebarOpen;
        setSidebarOpen(next);
        if (next) {
          setSettingsOpen(false);
          setOnlineOpen(false);
        }
      } else if (which === 'online') {
        const next = !onlineOpen;
        setOnlineOpen(next);
        if (next) {
          setSettingsOpen(false);
          setSidebarOpen(false);
          setOnline((prev) => ({ ...prev, unread: 0 }));
        }
      } else {
        const next = !settingsOpen;
        setSettingsOpen(next);
        if (next) {
          setSidebarOpen(false);
          setOnlineOpen(false);
        }
      }
    },
    [sidebarOpen, settingsOpen, onlineOpen],
  );

  const cycleBrush = useCallback(() => {
    setBrushSize((current) => {
      const idx = BRUSH_SIZES.indexOf(current);
      return BRUSH_SIZES[(idx + 1) % BRUSH_SIZES.length];
    });
  }, []);

  const dismissMobileHint = useCallback(() => {
    setMobileHint(false);
    try {
      localStorage.setItem(MOBILE_HINT_KEY, 'off');
    } catch {
      /* приватный режим — просто скрываем подсказку */
    }
  }, []);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), toast.kind === 'error' ? 6000 : 3500);
    return () => clearTimeout(t);
  }, [toast]);

  // Сохраняем правки активного проекта в список (с задержкой, чтобы не дёргать рендер).
  useEffect(() => {
    if (!activeId) return;
    const timeout = setTimeout(() => {
      const { gridConfig: cfg, brushSize: brush, revealed: rev } = latestRef.current;
      setProjects((prev) =>
        prev.map((p) =>
          p.id === activeId
            ? {
                ...p,
                grid: cfg,
                brushSize: brush,
                revealed: serializeRevealed(rev.cols, rev.rows, rev.bits),
                updatedAt: Date.now(),
              }
            : p,
        ),
      );
    }, 400);
    return () => clearTimeout(timeout);
  }, [activeId, gridConfig, brushSize, revealed]);

  const quotaWarned = useRef(false);
  useEffect(() => {
    let cancelled = false;
    void persistState({ version: 1, activeId, projects }).then((result) => {
      if (cancelled) return;
      if (result === 'quota' && !quotaWarned.current) {
        quotaWarned.current = true;
        showToast('error', 'Не хватает места в хранилище браузера. Экспортируйте проект в JSON или запустите RevealAtlas.cmd.');
      }
    });
    return () => {
      cancelled = true;
    };
  }, [projects, activeId, showToast]);

  const commitActive = useCallback(() => {
    const { activeId: id, gridConfig: cfg, brushSize: brush, revealed: rev } = latestRef.current;
    if (!id) return;
    setProjects((prev) =>
      prev.map((p) =>
        p.id === id
          ? { ...p, grid: cfg, brushSize: brush, revealed: serializeRevealed(rev.cols, rev.rows, rev.bits), updatedAt: Date.now() }
          : p,
      ),
    );
  }, []);

  const loadProjectIntoEditor = useCallback((project: Project) => {
    const nextGrid = computeGrid(project.image.width, project.image.height, project.grid);
    setActiveId(project.id);
    setGridConfig(project.grid);
    setBrushSize(project.brushSize || 1);
    setRevealed(projectRevealed(project, nextGrid.cols, nextGrid.rows));
    undoRef.current = [];
    setUndoCount(0);
  }, []);

  const handleSelectProject = useCallback(
    (id: string) => {
      if (id === activeId) {
        setSidebarOpen(false);
        return;
      }
      commitActive();
      const target = projects.find((p) => p.id === id);
      if (!target) return;
      loadProjectIntoEditor(target);
      setSidebarOpen(false);
    },
    [activeId, commitActive, projects, loadProjectIntoEditor],
  );

  // Подгоняем маску, если поменялись размеры сетки.
  useEffect(() => {
    setRevealed((prev) => resizeRevealed(prev, grid.cols, grid.rows));
  }, [grid.cols, grid.rows]);

  const pushUndo = useCallback(() => {
    undoRef.current = [
      ...undoRef.current.slice(-39),
      { revealed: { cols: revealed.cols, rows: revealed.rows, bits: revealed.bits.slice() }, grid },
    ];
    setUndoCount(undoRef.current.length);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revealed.cols, revealed.rows, revealed.bits, grid]);

  const handleUndo = useCallback(() => {
    const snapshot = undoRef.current.pop();
    setUndoCount(undoRef.current.length);
    if (!snapshot) return;
    const gridOfMask = { ...snapshot.grid, cols: snapshot.revealed.cols, rows: snapshot.revealed.rows };
    const remapped = remapRevealed(snapshot.revealed, gridOfMask, grid);
    const bits = remapped.bits.length === grid.cols * grid.rows
      ? remapped.bits
      : resizeRevealed(snapshot.revealed, grid.cols, grid.rows).bits;
    const restored = { cols: grid.cols, rows: grid.rows, bits };
    const before = latestRef.current.revealed;
    setRevealed(restored);
    if (roomActiveRef.current) {
      // Возвращаем всем ровно те клетки, которые изменились.
      const diff: Array<[number, number, number]> = [];
      const total = Math.min(before.bits.length, restored.bits.length);
      for (let i = 0; i < total; i++) {
        if (before.bits[i] === restored.bits[i]) continue;
        diff.push([i % restored.cols, Math.floor(i / restored.cols), restored.bits[i]]);
      }
      if (diff.length > 0) clientRef.current?.sendPaint(diff);
    }
  }, [grid]);

  const handlePaint = useCallback(
    (cells: Array<[number, number]>, value: boolean) => {
      if (mobileHint) dismissMobileHint();
      const prev = latestRef.current.revealed;
      const changed: Array<[number, number, number]> = [];
      for (const [col, row] of cells) {
        if (col < 0 || row < 0 || col >= prev.cols || row >= prev.rows) continue;
        const idx = row * prev.cols + col;
        if (prev.bits[idx] === (value ? 1 : 0)) continue;
        changed.push([col, row, value ? 1 : 0]);
      }
      if (changed.length === 0) return;
      setRevealed((current) => applyCells(current, changed));
      // В комнате мазок сразу уходит на сервер и появляется у остальных.
      if (roomActiveRef.current) clientRef.current?.sendPaint(changed);
    },
    [mobileHint, dismissMobileHint],
  );

  const handleStrokeStart = useCallback(() => {
    pushUndo();
  }, [pushUndo]);

  const handleStrokeEnd = useCallback(() => {}, []);

  const fillAll = useCallback(
    (value: boolean) => {
      pushUndo();
      setRevealed((prev) => {
        const bits = new Uint8Array(prev.bits.length);
        if (value) bits.fill(1);
        return { cols: prev.cols, rows: prev.rows, bits };
      });
      if (roomActiveRef.current) clientRef.current?.sendFill(value);
    },
    [pushUndo],
  );

  const updateConfig = useCallback(
    (patch: Partial<GridConfig>) => {
      const next: GridConfig = { ...gridConfig, ...patch };
      setGridConfig(next);
      const nextGrid = computeGrid(image.width, image.height, next);
      setRevealed((prev) => remapRevealed(prev, grid, nextGrid));
      if (roomActiveRef.current) clientRef.current?.sendGrid(next);
    },
    [gridConfig, grid, image.width, image.height],
  );

  const resetGrid = useCallback(() => {
    updateConfig({ mode: 'tile', tileSize: DEFAULT_GRID.tileSize, offsetX: 0, offsetY: 0 });
  }, [updateConfig]);

  const addProject = useCallback(
    async (file: File) => {
      setBusy(true);
      try {
        const loaded = await readImage(file);
        const small = await shrinkImage(loaded, recommendedMaxSide(), 0.88);
        const project = createProject(small.name || 'Новый мир', {
          name: small.name,
          width: small.width,
          height: small.height,
          dataUrl: small.dataUrl,
        });
        // Первый проект в списке задаёт удобный «средний» размер клетки.
        const cfg: GridConfig = { ...DEMO_GRID, tileSize: Math.max(32, Math.round(small.width / 24)) };
        project.grid = cfg;
        commitActive();
        setProjects((prev) => [project, ...prev]);
        loadProjectIntoEditor(project);
        showToast('info', `Мир «${project.name}» загружен: ${small.width}×${small.height} px`);
      } catch (err) {
        showToast('error', err instanceof Error ? err.message : 'Не удалось загрузить картинку.');
      } finally {
        setBusy(false);
      }
    },
    [commitActive, loadProjectIntoEditor, showToast],
  );

  const replaceImage = useCallback(
    async (file: File) => {
      if (!activeProject) return;
      setBusy(true);
      try {
        const loaded = await readImage(file);
        const small = await shrinkImage(loaded, recommendedMaxSide(), 0.88);
        const nextGrid = computeGrid(small.width, small.height, gridConfig);
        setProjects((prev) =>
          prev.map((p) =>
            p.id === activeProject.id
              ? {
                  ...p,
                  image: { name: small.name, width: small.width, height: small.height, dataUrl: small.dataUrl },
                  revealed: serializeRevealed(nextGrid.cols, nextGrid.rows, new Uint8Array(nextGrid.cols * nextGrid.rows)),
                  grid: gridConfig,
                  updatedAt: Date.now(),
                }
              : p,
          ),
        );
        setRevealed(emptyRevealed(nextGrid.cols, nextGrid.rows));
        undoRef.current = [];
        setUndoCount(0);
        showToast('info', `Карта заменена: ${small.width}×${small.height} px, прогресс сброшен.`);
        if (roomActiveRef.current && online.room) {
          // Новая картинка уходит на сервер и появляется у всех в комнате.
          void clientRef.current
            ?.replaceRoomImage(online.room.code, {
              dataUrl: small.dataUrl,
              name: small.name,
              width: small.width,
              height: small.height,
            })
            .catch((err: unknown) => {
              showToast('error', err instanceof Error ? err.message : 'Не удалось загрузить картинку на сервер.');
            });
        }
      } catch (err) {
        showToast('error', err instanceof Error ? err.message : 'Не удалось заменить картинку.');
      } finally {
        setBusy(false);
      }
    },
    [activeProject, gridConfig, online.room, showToast],
  );

  const renameProject = useCallback(
    (name: string) => {
      if (!activeId) return;
      setProjects((prev) => prev.map((p) => (p.id === activeId ? { ...p, name, updatedAt: Date.now() } : p)));
      if (roomActiveRef.current) clientRef.current?.sendWorldName(name);
    },
    [activeId],
  );

  const deleteProject = useCallback(
    (id: string) => {
      const remaining = projects.filter((p) => p.id !== id);
      if (id === activeId) {
        if (remaining.length > 0) {
          loadProjectIntoEditor(remaining[0]);
          setProjects(remaining);
        } else {
          const demo = createProject('Демо-мир', demoImage(), DEMO_GRID);
          setProjects([demo]);
          loadProjectIntoEditor(demo);
        }
      } else {
        setProjects(remaining);
      }
      showToast('info', 'Мир удалён.');
    },
    [projects, activeId, loadProjectIntoEditor, showToast],
  );

  const exportProject = useCallback(
    (id: string) => {
      const source = projects.find((p) => p.id === id);
      if (!source) return;
      let project = source;
      if (id === activeId) {
        project = {
          ...source,
          grid: gridConfig,
          brushSize,
          revealed: serializeRevealed(revealed.cols, revealed.rows, revealed.bits),
        };
      }
      const safeName = project.name.replace(/[^\p{L}\p{N}_-]+/gu, '_') || 'world';
      downloadJson(`reveal-atlas-${safeName}.json`, { version: 1, projects: [project] });
      showToast('info', 'Файл проекта скачан.');
    },
    [projects, activeId, gridConfig, brushSize, revealed, showToast],
  );

  const importProject = useCallback(
    async (file: File) => {
      try {
        const text = await file.text();
        const parsed = JSON.parse(text) as { projects?: Project[] } | Project;
        const list = Array.isArray((parsed as { projects?: Project[] }).projects)
          ? (parsed as { projects: Project[] }).projects
          : [parsed as Project];
        const valid = list.map(normalizeProject).filter((p): p is Project => p !== null);
        if (valid.length === 0) throw new Error('В файле нет ни одного проекта Reveal Atlas.');
        const prepared = valid.map((p) => ({ ...p, id: newId() }));
        commitActive();
        setProjects((prev) => [...prepared, ...prev]);
        loadProjectIntoEditor(prepared[0]);
        showToast('info', `Импортировано миров: ${prepared.length}`);
      } catch (err) {
        showToast('error', err instanceof Error ? err.message : 'Не удалось прочитать JSON.');
      }
    },
    [commitActive, loadProjectIntoEditor, showToast],
  );

  const handleDrop = useCallback(
    (e: ReactDragEvent) => {
      e.preventDefault();
      dragDepthRef.current = 0;
      setDragOver(false);
      const file = e.dataTransfer.files?.[0];
      if (!file) return;
      if (file.type === 'application/json' || /\.json$/i.test(file.name)) {
        void importProject(file);
      } else {
        void addProject(file);
      }
    },
    [addProject, importProject],
  );

  const handleOpenDataFolder = useCallback(async () => {
    const ok = await openDesktopDataFolder();
    if (!ok) showToast('error', 'Не удалось открыть папку сохранений.');
  }, [showToast]);

  const handleCreateShortcut = useCallback(async () => {
    const res = await createDesktopShortcut();
    if (res.ok) {
      showToast('info', 'Ярлык «Reveal Atlas» создан на Рабочем столе.');
    } else {
      showToast('error', 'Не удалось создать ярлык на Рабочем столе.');
    }
  }, [showToast]);

  /* ─── действия онлайн-режима ─── */

  const setRoomInviteUrl = useCallback((code: string | null) => {
    if (typeof window === 'undefined') return;
    try {
      const url = new URL(window.location.href);
      if (code) url.searchParams.set('room', code);
      else url.searchParams.delete('room');
      window.history.replaceState(null, '', url.toString());
    } catch {
      /* History недоступен — просто не показываем ссылку */
    }
  }, []);

  const patchRoomProject = useCallback((projectId: string, patch: Partial<Project>) => {
    setProjects((prev) => prev.map((p) => (p.id === projectId ? { ...p, ...patch, updatedAt: Date.now() } : p)));
  }, []);

  const loadRoomImage = useCallback(
    (projectId: string, image: NonNullable<OnlineRoomState['image']>) => {
      const absolute = absoluteUrl(online.serverUrl, image.url);
      void fetchImageAsDataUrl(absolute)
        .then((dataUrl) => {
          roomImageUrls.current.set(projectId, absolute);
          patchRoomProject(projectId, {
            image: { name: image.name, width: image.width, height: image.height, dataUrl },
          });
        })
        .catch((err: unknown) => {
          showToast('error', err instanceof Error ? err.message : 'Не удалось загрузить картинку комнаты.');
        });
    },
    [online.serverUrl, patchRoomProject, showToast],
  );

  const dropRoomProject = useCallback(() => {
    const projectId = roomProjectIdRef.current;
    setOnline((prev) => ({ ...prev, room: null, you: null, chat: [], unread: 0, status: 'idle' }));
    setRemoteCursors([]);
    setRoomInviteUrl(null);
    if (!projectId) return;
    const remaining = latestRef.current.projects.filter((p) => p.id !== projectId);
    const fallback = remaining[0] ?? createProject('Демо-мир', demoImage(), DEMO_GRID);
    setProjects(remaining.length > 0 ? remaining : [fallback]);
    if (latestRef.current.activeId === projectId) loadProjectIntoEditor(fallback);
  }, [loadProjectIntoEditor, setRoomInviteUrl]);

  const handleOnlineEvent = useCallback(
    (event: OnlineEvent) => {
      switch (event.type) {
        case 'status': {
          setOnline((prev) => ({
            ...prev,
            status: event.status,
            error: event.status === 'error' ? event.detail ?? prev.error : prev.error,
          }));
          return;
        }

        case 'welcome': {
          const room = event.room;
          const projectId = roomProjectIdOf(room.code);
          const existing = latestRef.current.projects.find((p) => p.id === projectId);
          let dataUrl = existing?.image.dataUrl ?? '';
          if (room.image) {
            const absolute = absoluteUrl(online.serverUrl, room.image.url);
            const known = roomImageUrls.current.get(projectId);
            if (known !== absolute || !dataUrl) {
              dataUrl = absolute;
              loadRoomImage(projectId, room.image);
            }
          }
          const project: Project = existing
            ? {
                ...existing,
                name: room.name,
                grid: room.grid,
                revealed: room.revealed,
                image: {
                  name: room.image?.name ?? existing.image.name,
                  width: room.image?.width ?? existing.image.width,
                  height: room.image?.height ?? existing.image.height,
                  dataUrl,
                },
              }
            : {
                id: projectId,
                name: room.name,
                createdAt: room.createdAt,
                updatedAt: Date.now(),
                image: {
                  name: room.image?.name ?? 'Комната',
                  width: room.image?.width ?? 0,
                  height: room.image?.height ?? 0,
                  dataUrl,
                },
                grid: room.grid,
                revealed: room.revealed,
                brushSize: 1,
              };
          setProjects((prev) =>
            prev.some((p) => p.id === projectId) ? prev.map((p) => (p.id === projectId ? project : p)) : [project, ...prev],
          );
          setOnline((prev) => ({
            ...prev,
            room,
            you: event.you,
            chat: chatFromRoom(room),
            unread: 0,
            error: null,
            status: 'online',
          }));
          loadProjectIntoEditor(project);
          return;
        }

        case 'players': {
          const ids = new Set(event.players.map((p) => p.id));
          setRemoteCursors((prev) => prev.filter((cursor) => ids.has(cursor.id)));
          setOnline((prev) => (prev.room ? { ...prev, room: { ...prev.room, players: event.players } } : prev));
          return;
        }

        case 'paint': {
          const projectId = roomProjectIdRef.current;
          if (!projectId) return;
          // Функциональная форма: несколько мазков подряд не перезатирают друг друга.
          setProjects((prev) =>
            prev.map((p) =>
              p.id === projectId ? { ...p, revealed: applyCellsToEncoded(p.revealed, event.cells), updatedAt: Date.now() } : p,
            ),
          );
          if (roomActiveRef.current) setRevealed((prev) => applyCells(prev, event.cells));
          return;
        }

        case 'fill': {
          const projectId = roomProjectIdRef.current;
          if (!projectId) return;
          setProjects((prev) =>
            prev.map((p) => (p.id === projectId ? { ...p, revealed: fillEncoded(p.revealed, event.value), updatedAt: Date.now() } : p)),
          );
          if (roomActiveRef.current) {
            setRevealed((prev) => {
              const bits = new Uint8Array(prev.bits.length);
              if (event.value) bits.fill(1);
              return { cols: prev.cols, rows: prev.rows, bits };
            });
          }
          return;
        }

        case 'grid': {
          const projectId = roomProjectIdRef.current;
          if (!projectId) return;
          patchRoomProject(projectId, { grid: event.config });
          if (roomActiveRef.current) {
            const from = computeGrid(image.width, image.height, latestRef.current.gridConfig);
            const to = computeGrid(image.width, image.height, event.config);
            setGridConfig(event.config);
            setRevealed((prev) => remapRevealed(prev, from, to));
          }
          return;
        }

        case 'cursor': {
          const player = online.room?.players.find((p) => p.id === event.id);
          if (!player) return;
          setRemoteCursors((prev) => {
            const rest = prev.filter((cursor) => cursor.id !== event.id);
            return [...rest, { id: player.id, name: player.name, color: player.color, col: event.col, row: event.row }];
          });
          return;
        }

        case 'chat': {
          setOnline((prev) => ({
            ...prev,
            chat: [...prev.chat.slice(-59), { kind: 'chat', ...event.message }],
            unread: onlineOpenRef.current ? 0 : prev.unread + 1,
          }));
          return;
        }

        case 'system': {
          setOnline((prev) => ({
            ...prev,
            chat: [...prev.chat.slice(-59), { kind: 'system', id: `s${Date.now()}${Math.random()}`, text: event.text, ts: Date.now() }],
          }));
          return;
        }

        case 'image': {
          const projectId = roomProjectIdRef.current;
          if (!projectId || !event.image) return;
          const absolute = absoluteUrl(online.serverUrl, event.image.url);
          roomImageUrls.current.set(projectId, absolute);
          patchRoomProject(projectId, {
            image: { name: event.image.name, width: event.image.width, height: event.image.height, dataUrl: absolute },
          });
          loadRoomImage(projectId, event.image);
          return;
        }

        case 'name': {
          const projectId = roomProjectIdRef.current;
          if (projectId) patchRoomProject(projectId, { name: event.name });
          setOnline((prev) => (prev.room ? { ...prev, room: { ...prev.room, name: event.name } } : prev));
          return;
        }

        case 'rooms': {
          setOnline((prev) => ({ ...prev, rooms: event.rooms, busy: false }));
          return;
        }

        case 'error': {
          setOnline((prev) => ({ ...prev, error: event.message, busy: false }));
          showToast('error', event.message);
          return;
        }

        case 'left': {
          dropRoomProject();
          showToast('info', 'Сервер закрыл комнату.');
          return;
        }

        default:
          return;
      }
    },
    [image.height, image.width, loadProjectIntoEditor, loadRoomImage, online.room, online.serverUrl, patchRoomProject, showToast],
  );

  // Обработчик событий обновляется каждый рендер, а клиент создаётся один раз.
  const onlineHandlerRef = useRef<(event: OnlineEvent) => void>(() => {});
  onlineHandlerRef.current = handleOnlineEvent;

  useEffect(() => {
    const identity = loadPlayerIdentity();
    const client = new OnlineClient({
      serverUrl: loadServerUrl() || defaultServerUrl(),
      identity,
      onEvent: (event) => onlineHandlerRef.current(event),
    });
    clientRef.current = client;

    // Ссылка-приглашение: ?room=CODE сразу подключает к комнате.
    let invite = '';
    try {
      invite = (new URLSearchParams(window.location.search).get('room') ?? '').toUpperCase().replace(/[^A-Z0-9]/g, '');
    } catch {
      invite = loadLastRoom();
    }
    if (invite) {
      setOnlineOpen(true);
      setOnline((prev) => ({ ...prev, status: 'connecting' }));
      window.setTimeout(() => client.joinRoom(invite), 0);
    }

    return () => {
      client.disconnect();
      clientRef.current = null;
    };
  }, []);

  const handleCursorMove = useCallback((cell: { col: number; row: number }) => {
    if (!roomActiveRef.current) return;
    clientRef.current?.sendCursor(cell.col, cell.row);
  }, []);

  const createRoom = useCallback(async () => {
    const client = clientRef.current;
    const project = activeProject;
    if (!client) return;
    if (!project) {
      showToast('error', 'Сначала откройте мир, из которого делать комнату.');
      return;
    }
    setOnline((prev) => ({ ...prev, busy: true, error: null }));
    try {
      const { code, imageUrl } = await client.createRoom({
        name: project.name,
        image: {
          dataUrl: project.image.dataUrl,
          name: project.image.name,
          width: project.image.width,
          height: project.image.height,
        },
        grid: gridConfig,
        revealed: serializeRevealed(revealed.cols, revealed.rows, revealed.bits),
      });
      roomImageUrls.current.set(roomProjectIdOf(code), imageUrl);
      setOnline((prev) => ({ ...prev, busy: false }));
      setRoomInviteUrl(code);
      setOnlineOpen(true);
      client.joinRoom(code);
      void client.listRooms().then((rooms) => setOnline((prev) => ({ ...prev, rooms }))).catch(() => {});
      showToast('info', `Комната ${code} создана — отправьте другу ссылку или код.`);
    } catch (err) {
      setOnline((prev) => ({ ...prev, busy: false }));
      showToast('error', err instanceof Error ? err.message : 'Не удалось создать комнату.');
    }
  }, [activeProject, gridConfig, revealed, setRoomInviteUrl, showToast]);

  const joinRoom = useCallback(
    (code: string) => {
      const client = clientRef.current;
      if (!client) return;
      const clean = code.toUpperCase().replace(/[^A-Z0-9]/g, '');
      if (!clean) {
        showToast('error', 'Введите код комнаты.');
        return;
      }
      setOnline((prev) => ({ ...prev, error: null }));
      setOnlineOpen(true);
      setRoomInviteUrl(clean);
      const previous = roomProjectIdRef.current;
      if (previous && previous !== roomProjectIdOf(clean)) {
        const remaining = latestRef.current.projects.filter((p) => p.id !== previous);
        setProjects(remaining.length > 0 ? remaining : [createProject('Демо-мир', demoImage(), DEMO_GRID)]);
      }
      client.joinRoom(clean);
    },
    [setRoomInviteUrl, showToast],
  );

  const leaveRoom = useCallback(() => {
    clientRef.current?.leaveRoom();
    saveLastRoom('');
    dropRoomProject();
    showToast('info', 'Вы вышли из комнаты. Мир остался у вас копией.');
  }, [dropRoomProject, showToast]);

  const refreshRooms = useCallback(async () => {
    const client = clientRef.current;
    if (!client) return;
    setOnline((prev) => ({ ...prev, busy: true, error: null }));
    const health = await client.health();
    if (!health.ok) {
      setOnline((prev) => ({ ...prev, busy: false, error: health.error ?? 'Сервер недоступен.' }));
      return;
    }
    try {
      const rooms = await client.listRooms();
      setOnline((prev) => ({ ...prev, rooms, busy: false, error: null }));
    } catch (err) {
      setOnline((prev) => ({ ...prev, busy: false, error: err instanceof Error ? err.message : 'Не удалось получить список комнат.' }));
    }
  }, []);

  const sendChat = useCallback((text: string) => {
    clientRef.current?.sendChat(text);
  }, []);

  const copyInvite = useCallback(async () => {
    if (!online.room) return;
    let link = '';
    try {
      const url = new URL(window.location.href);
      url.searchParams.set('room', online.room.code);
      link = url.toString();
    } catch {
      link = online.room.code;
    }
    try {
      await navigator.clipboard.writeText(link);
      showToast('info', 'Ссылка-приглашение скопирована.');
    } catch {
      showToast('info', `Код комнаты: ${online.room.code}`);
    }
  }, [online.room, showToast]);

  const handlePlayerName = useCallback((name: string) => {
    setOnline((prev) => ({ ...prev, playerName: name }));
    savePlayerIdentity({ id: loadPlayerIdentity().id, name });
    clientRef.current?.setIdentity({ name });
  }, []);

  const handleServerUrl = useCallback((url: string) => {
    const normalized = normalizeServerUrl(url);
    setOnline((prev) => ({ ...prev, serverUrl: normalized, error: null }));
    saveServerUrl(normalized);
    const client = clientRef.current;
    if (!client) return;
    client.setServerUrl(normalized);
    if (online.room) {
      // Переподключаемся к комнате уже на новом сервере.
      client.joinRoom(online.room.code);
    }
  }, [online.room]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const typing = target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName);
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
        if (!typing) {
          e.preventDefault();
          handleUndo();
        }
        return;
      }
      if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === '1') setTool('reveal');
      else if (e.key === '2') setTool('hide');
      else if (e.key === '3') setTool('pan');
      else if (e.key === 'g') setShowGrid((v) => !v);
      else if (e.key === 'f') mapRef.current?.fit();
      else if (e.key === '[' || e.key === ']') {
        // Кисть перебирается по тем же размерам, что и кнопки на панели.
        const step = e.key === ']' ? 1 : -1;
        setBrushSize((current) => {
          const index = BRUSH_SIZES.indexOf(current);
          const from = index === -1 ? 0 : index;
          const next = (from + step + BRUSH_SIZES.length) % BRUSH_SIZES.length;
          return BRUSH_SIZES[next];
        });
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [handleUndo]);

  const progressById = useMemo(() => {
    const map: Record<string, number> = {};
    for (const project of projects) {
      if (project.id === activeId) {
        map[project.id] = progress;
      } else {
        const cells = project.revealed.cols * project.revealed.rows;
        const open = Math.min(countBitsInEncoded(project.revealed.bits), cells);
        map[project.id] = cells ? open / cells : 0;
      }
    }
    return map;
  }, [projects, activeId, progress]);

  const toolHint =
    tool === 'reveal'
      ? 'Тяните по карте, чтобы открывать клетки. ПКМ стирает.'
      : tool === 'hide'
        ? 'Тяните по карте, чтобы снова спрятать клетки.'
        : 'Тяните, чтобы перемещаться по карте.';

  return (
    <div
      className="app"
      onDragEnter={(e) => {
        if (!e.dataTransfer?.types?.includes('Files')) return;
        e.preventDefault();
        dragDepthRef.current += 1;
        setDragOver(true);
      }}
      onDragOver={(e) => {
        if (!e.dataTransfer?.types?.includes('Files')) return;
        e.preventDefault();
      }}
      onDragLeave={(e) => {
        if (!e.dataTransfer?.types?.includes('Files')) return;
        dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
        if (dragDepthRef.current === 0) setDragOver(false);
      }}
      onDrop={handleDrop}
    >
      <header className="topbar">
        <button
          className="icon-btn mobile-only"
          onClick={() => toggleDrawer('worlds')}
          aria-label="Миры"
          aria-expanded={sidebarOpen}
        >
          ☰
        </button>
        <div className="brand">
          <span className="brand-mark" aria-hidden>◍</span>
          <div className="brand-text">
            <span className="brand-name">Reveal Atlas</span>
            <span className="brand-sub">{activeProject?.name ?? 'Мир'}</span>
          </div>
        </div>

        <div className="toolbar">
          <div className="tool-group" role="group" aria-label="Инструмент">
            <button
              className={`tool-btn${tool === 'reveal' ? ' is-active' : ''}`}
              onClick={() => setTool('reveal')}
              title="Открывать клетки (1)"
            >
              ✦ <span>Открыть</span>
            </button>
            <button
              className={`tool-btn${tool === 'hide' ? ' is-active' : ''}`}
              onClick={() => setTool('hide')}
              title="Скрывать клетки (2)"
            >
              ◒ <span>Скрыть</span>
            </button>
            <button
              className={`tool-btn${tool === 'pan' ? ' is-active' : ''}`}
              onClick={() => setTool('pan')}
              title="Перемещение (3)"
            >
              ✥ <span>Рука</span>
            </button>
          </div>

          <div className="tool-group brush-group" role="group" aria-label="Размер кисти">
            {[1, 2, 3, 5].map((size) => (
              <button
                key={size}
                className={`size-btn${brushSize === size ? ' is-active' : ''}`}
                onClick={() => setBrushSize(size)}
                title={`Кисть ${size}×${size} клеток`}
              >
                {size}
              </button>
            ))}
          </div>

          <div className="tool-group">
            <button
              className={`tool-btn${showGrid ? ' is-active' : ''}`}
              onClick={() => setShowGrid((v) => !v)}
              title="Показать сетку (G)"
            >
              ▦ <span>Сетка</span>
            </button>
            <button
              className="tool-btn"
              onClick={handleUndo}
              disabled={undoCount === 0}
              title="Отменить (Ctrl+Z)"
            >
              ↶ <span>Отменить</span>
            </button>
          </div>

          <div className="tool-group zoom-group">
            <button className="tool-btn" onClick={() => mapRef.current?.zoomBy(1 / 1.3)} title="Уменьшить">−</button>
            <span className="zoom-value">{Math.round(scale * 100)}%</span>
            <button className="tool-btn" onClick={() => mapRef.current?.zoomBy(1.3)} title="Увеличить">+</button>
            <button className="tool-btn" onClick={() => mapRef.current?.fit()} title="Вписать мир (F)">⤢</button>
          </div>
        </div>

        <button
          className={`tool-btn online-btn${online.room ? ' is-active' : ''}${online.unread > 0 ? ' has-unread' : ''}`}
          onClick={() => toggleDrawer('online')}
          aria-label="Онлайн-режим"
          title="Онлайн-режим: общий мир с друзьями"
          aria-expanded={onlineOpen}
        >
          <span className={`online-dot is-${online.status}`} aria-hidden />
          <span>{online.room ? `Онлайн · ${online.room.code}` : 'Онлайн'}</span>
          {online.unread > 0 && <span className="online-unread">{online.unread}</span>}
        </button>

        <button
          className={`icon-btn settings-toggle${settingsOpen ? ' is-active' : ''}`}
          onClick={() => toggleDrawer('settings')}
          aria-label="Настройки"
          title="Настройки сетки и мира"
        >
          ⚙
        </button>
      </header>

      <div className="layout">
        {(sidebarOpen || settingsOpen || onlineOpen) && (
          <div
            className="scrim"
            onClick={() => {
              setSidebarOpen(false);
              setSettingsOpen(false);
              setOnlineOpen(false);
            }}
          />
        )}

        <div className={sidebarOpen ? 'sidebar-shell is-open' : 'sidebar-shell'}>
          <Sidebar
            projects={projects}
            activeId={activeId}
            progressById={progressById}
            onlineRoomId={roomProjectId}
            onSelect={handleSelectProject}
            onNew={addProject}
            onImport={importProject}
            onExport={exportProject}
            onDelete={deleteProject}
            onClose={() => setSidebarOpen(false)}
          />
        </div>

        <main
          className={`map-area${dragOver ? ' is-drag-over' : ''}`}
          onPointerDown={() => {
            if (window.innerWidth <= 980) {
              setSidebarOpen(false);
              setSettingsOpen(false);
            }
          }}
        >
          <MapView
            ref={mapRef}
            projectId={activeProject?.id ?? 'none'}
            image={image}
            grid={grid}
            revealed={revealed}
            brushSize={brushSize}
            tool={tool}
            showGrid={showGrid}
            onPaint={handlePaint}
            onStrokeStart={handleStrokeStart}
            onStrokeEnd={handleStrokeEnd}
            onScaleChange={setScale}
            remoteCursors={roomActive ? remoteCursors : []}
            onCursor={handleCursorMove}
          />

          <div className="hud">
            <div className="hud-progress">
              <div className="hud-bar">
                <div className="hud-bar-fill" style={{ width: `${progress * 100}%` }} />
              </div>
              <span className="hud-percent">{(progress * 100).toFixed(progress > 0 && progress < 0.1 ? 1 : 0)}%</span>
            </div>
            <div className="hud-meta">
              {online.room && (
                <span className="hud-online" title="Идёт общая игра через сервер">
                  ОНЛАЙН · {online.room.code} · {online.room.players.length} игр.
                </span>
              )}
              <span>{revealedCount.toLocaleString('ru-RU')} / {totalCells.toLocaleString('ru-RU')} клеток</span>
              <span className="hud-sep">·</span>
              <span>сетка {grid.cols}×{grid.rows}</span>
              <span className="hud-sep">·</span>
              <span>{Math.round(grid.tileW)} px</span>
            </div>
            <div className="hud-hint">{toolHint} Колесо — зум, Shift+перетаскивание — панорама.</div>
          </div>

          {dragOver && (
            <div className="drop-zone">
              <div className="drop-zone-inner">
                <strong>Отпустите файл</strong>
                <span>Картинка создаст новый мир, JSON — импортирует проект</span>
              </div>
            </div>
          )}

          {busy && (
            <div className="busy">
              <span className="spinner" /> Загружаем картинку…
            </div>
          )}
        </main>

        <div className={onlineOpen ? 'settings-shell online-shell is-open' : 'settings-shell online-shell'}>
          <OnlinePanel
            status={online.status}
            serverUrl={online.serverUrl}
            playerName={online.playerName}
            room={online.room}
            you={online.you}
            rooms={online.rooms}
            chat={online.chat}
            unread={online.unread}
            error={online.error}
            busy={online.busy}
            onCreateRoom={() => void createRoom()}
            onJoinRoom={joinRoom}
            onLeaveRoom={leaveRoom}
            onRefreshRooms={() => void refreshRooms()}
            onSendChat={sendChat}
            onServerUrlChange={handleServerUrl}
            onPlayerNameChange={handlePlayerName}
            onCopyInvite={() => void copyInvite()}
            onClose={() => setOnlineOpen(false)}
          />
        </div>

        <div className={settingsOpen ? 'settings-shell is-open' : 'settings-shell'}>
          {activeProject && (
            <SettingsPanel
              project={activeProject}
              progress={progress}
              grid={grid}
              config={gridConfig}
              onConfigChange={updateConfig}
              onResetGrid={resetGrid}
              onReplaceImage={replaceImage}
              onRename={renameProject}
              onRevealAll={() => fillAll(true)}
              onHideAll={() => fillAll(false)}
              onExport={() => exportProject(activeProject.id)}
              onDelete={() => {
                if (confirm(`Удалить «${activeProject.name}»? Прогресс будет потерян.`)) deleteProject(activeProject.id);
              }}
              onOpenDataFolder={handleOpenDataFolder}
              onCreateShortcut={handleCreateShortcut}
              onClose={() => setSettingsOpen(false)}
            />
          )}
        </div>

        <nav className="dock" aria-label="Панель инструментов">
          {mobileHint && (
            <div className="mobile-hint">
              <div>
                <strong>Как управлять</strong>
                Один палец — открывать клетки, два — зум и панорама.
                «Рука» — перемещение по карте.
              </div>
              <button onClick={dismissMobileHint}>Понятно</button>
            </div>
          )}

          <div className="dock-progress">
            <div className="dock-bar">
              <div className="dock-bar-fill" style={{ width: `${progress * 100}%` }} />
            </div>
            <span className="dock-percent">
              {(progress * 100).toFixed(progress > 0 && progress < 0.1 ? 1 : 0)}%
            </span>
            <span className="dock-count">
              {revealedCount.toLocaleString('ru-RU')} / {totalCells.toLocaleString('ru-RU')}
            </span>
          </div>

          <div className="dock-tools" role="group" aria-label="Инструмент">
            <button
              className={tool === 'reveal' ? 'is-active' : ''}
              onClick={() => setTool('reveal')}
              aria-pressed={tool === 'reveal'}
            >
              <b aria-hidden>✦</b>
              <span>Открыть</span>
            </button>
            <button
              className={tool === 'hide' ? 'is-active' : ''}
              onClick={() => setTool('hide')}
              aria-pressed={tool === 'hide'}
            >
              <b aria-hidden>◒</b>
              <span>Скрыть</span>
            </button>
            <button
              className={tool === 'pan' ? 'is-active' : ''}
              onClick={() => setTool('pan')}
              aria-pressed={tool === 'pan'}
            >
              <b aria-hidden>✥</b>
              <span>Рука</span>
            </button>
          </div>

          <div className="dock-actions">
            <button onClick={cycleBrush} title="Размер кисти">
              <b>{brushSize}×{brushSize}</b>
              <span>Кисть</span>
            </button>
            <button onClick={handleUndo} disabled={undoCount === 0} title="Отменить мазок">
              <b aria-hidden>↶</b>
              <span>Отмена</span>
            </button>
            <button
              className={showGrid ? 'is-active' : ''}
              onClick={() => setShowGrid((v) => !v)}
              aria-pressed={showGrid}
              title="Показать сетку"
            >
              <b aria-hidden>▦</b>
              <span>Сетка</span>
            </button>
            <button onClick={() => mapRef.current?.fit()} title="Вписать мир">
              <b aria-hidden>⤢</b>
              <span>Вписать</span>
            </button>
            <button
              className={onlineOpen ? 'is-active' : ''}
              onClick={() => toggleDrawer('online')}
              aria-pressed={onlineOpen}
              title="Онлайн-режим"
            >
              <b aria-hidden>⬢</b>
              <span>Онлайн{online.unread > 0 ? ` · ${online.unread}` : ''}</span>
            </button>
            <button
              className={settingsOpen ? 'is-active' : ''}
              onClick={() => toggleDrawer('settings')}
              aria-pressed={settingsOpen}
              title="Настройки"
            >
              <b aria-hidden>⚙</b>
              <span>Настройки</span>
            </button>
          </div>
        </nav>
      </div>

      {toast && <div className={`toast toast-${toast.kind}`}>{toast.text}</div>}
    </div>
  );
}
