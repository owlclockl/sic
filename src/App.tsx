import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { DragEvent as ReactDragEvent } from 'react';
import { MapView, type MapHandle, type Tool } from './components/MapView';
import { Sidebar } from './components/Sidebar';
import { SettingsPanel } from './components/SettingsPanel';
import {
  DEFAULT_GRID,
  computeGrid,
  type Grid,
  type GridConfig,
} from './lib/grid';
import { countRevealed, emptyRevealed, remapRevealed, resizeRevealed, type Revealed } from './lib/bits';
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

type Toast = { kind: 'info' | 'error'; text: string } | null;

interface Snapshot {
  revealed: Revealed;
  grid: Grid;
}

const DEMO_GRID: GridConfig = { ...DEFAULT_GRID, tileSize: 64 };
const BRUSH_SIZES = [1, 2, 3, 5];
const MOBILE_HINT_KEY = 'reveal-atlas/mobile-hint';

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
    (which: 'worlds' | 'settings') => {
      if (which === 'worlds') {
        const next = !sidebarOpen;
        setSidebarOpen(next);
        if (next) setSettingsOpen(false);
      } else {
        const next = !settingsOpen;
        setSettingsOpen(next);
        if (next) setSidebarOpen(false);
      }
    },
    [sidebarOpen, settingsOpen],
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
    setRevealed({ cols: grid.cols, rows: grid.rows, bits });
  }, [grid]);

  const handlePaint = useCallback((cells: Array<[number, number]>, value: boolean) => {
    if (mobileHint) dismissMobileHint();
    setRevealed((prev) => {
      let changed = false;
      let bits: Uint8Array | null = null;
      for (const [col, row] of cells) {
        if (col < 0 || row < 0 || col >= prev.cols || row >= prev.rows) continue;
        const idx = row * prev.cols + col;
        if (prev.bits[idx] === (value ? 1 : 0)) continue;
        if (!bits) bits = prev.bits.slice();
        bits[idx] = value ? 1 : 0;
        changed = true;
      }
      return changed && bits ? { cols: prev.cols, rows: prev.rows, bits } : prev;
    });
  }, [mobileHint, dismissMobileHint]);

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
    },
    [pushUndo],
  );

  const updateConfig = useCallback(
    (patch: Partial<GridConfig>) => {
      const next: GridConfig = { ...gridConfig, ...patch };
      setGridConfig(next);
      const nextGrid = computeGrid(image.width, image.height, next);
      setRevealed((prev) => remapRevealed(prev, grid, nextGrid));
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
      } catch (err) {
        showToast('error', err instanceof Error ? err.message : 'Не удалось заменить картинку.');
      } finally {
        setBusy(false);
      }
    },
    [activeProject, gridConfig, showToast],
  );

  const renameProject = useCallback(
    (name: string) => {
      if (!activeId) return;
      setProjects((prev) => prev.map((p) => (p.id === activeId ? { ...p, name, updatedAt: Date.now() } : p)));
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
      if (typing) return;
      if (e.key === '1') setTool('reveal');
      else if (e.key === '2') setTool('hide');
      else if (e.key === '3') setTool('pan');
      else if (e.key === 'g') setShowGrid((v) => !v);
      else if (e.key === 'f') mapRef.current?.fit();
      else if (e.key === '[') setBrushSize((v) => Math.max(1, v - 1));
      else if (e.key === ']') setBrushSize((v) => Math.min(9, v + 1));
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
        let open = 0;
        for (let i = 0; i < project.revealed.bits.length; i++) {
          const code = project.revealed.bits.charCodeAt(i);
          const idx = code >= 65 && code <= 90 ? code - 65
            : code >= 97 && code <= 122 ? code - 71
              : code >= 48 && code <= 57 ? code + 4
                : code === 43 ? 62 : code === 47 ? 63 : 0;
          open += idx.toString(2).split('1').length - 1;
        }
        const capped = Math.min(open, cells);
        map[project.id] = cells ? capped / cells : 0;
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
          className={`icon-btn settings-toggle${settingsOpen ? ' is-active' : ''}`}
          onClick={() => toggleDrawer('settings')}
          aria-label="Настройки"
          title="Настройки сетки и мира"
        >
          ⚙
        </button>
      </header>

      <div className="layout">
        {(sidebarOpen || settingsOpen) && (
          <div
            className="scrim"
            onClick={() => {
              setSidebarOpen(false);
              setSettingsOpen(false);
            }}
          />
        )}

        <div className={sidebarOpen ? 'sidebar-shell is-open' : 'sidebar-shell'}>
          <Sidebar
            projects={projects}
            activeId={activeId}
            progressById={progressById}
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
          />

          <div className="hud">
            <div className="hud-progress">
              <div className="hud-bar">
                <div className="hud-bar-fill" style={{ width: `${progress * 100}%` }} />
              </div>
              <span className="hud-percent">{(progress * 100).toFixed(progress > 0 && progress < 0.1 ? 1 : 0)}%</span>
            </div>
            <div className="hud-meta">
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
