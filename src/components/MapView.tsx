import { useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState, forwardRef } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import type { Grid } from '../lib/grid';
import { clamp, isInsideWorld } from '../lib/grid';
import { isRevealed, type Revealed } from '../lib/bits';
import type { ProjectImage } from '../lib/projects';

export type Tool = 'reveal' | 'hide' | 'pan';

export interface MapViewProps {
  projectId: string;
  image: ProjectImage;
  grid: Grid;
  revealed: Revealed;
  brushSize: number;
  tool: Tool;
  showGrid: boolean;
  onPaint: (cells: Array<[number, number]>, value: boolean) => void;
  onStrokeStart: () => void;
  onStrokeEnd: () => void;
  onScaleChange?: (scale: number) => void;
}

export interface MapHandle {
  fit: () => void;
  zoomBy: (factor: number) => void;
}

interface View {
  scale: number;
  tx: number;
  ty: number;
}

interface Viewport {
  w: number;
  h: number;
}

const FOG_TOP = '#111a2b';
const FOG_BOTTOM = '#0a1120';
const ACCENT = '#7ee0c3';

let fogTile: HTMLCanvasElement | null = null;

function getFogPattern(ctx: CanvasRenderingContext2D): CanvasPattern | null {
  if (fogTile) return ctx.createPattern(fogTile, 'repeat');
  const tile = document.createElement('canvas');
  tile.width = 96;
  tile.height = 96;
  const tctx = tile.getContext('2d');
  if (!tctx) return null;
  const grad = tctx.createLinearGradient(0, 0, 0, 96);
  grad.addColorStop(0, FOG_TOP);
  grad.addColorStop(1, FOG_BOTTOM);
  tctx.fillStyle = grad;
  tctx.fillRect(0, 0, 96, 96);
  tctx.fillStyle = 'rgba(255, 255, 255, 0.035)';
  for (let i = 0; i < 46; i++) {
    const x = Math.random() * 96;
    const y = Math.random() * 96;
    const r = Math.random() * 1.6 + 0.3;
    tctx.beginPath();
    tctx.arc(x, y, r, 0, Math.PI * 2);
    tctx.fill();
  }
  fogTile = tile;
  return ctx.createPattern(tile, 'repeat');
}

export const MapView = forwardRef<MapHandle, MapViewProps>(function MapView(props, ref) {
  const {
    projectId,
    image,
    grid,
    revealed,
    brushSize,
    tool,
    showGrid,
    onPaint,
    onStrokeStart,
    onStrokeEnd,
    onScaleChange,
  } = props;

  const containerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [viewport, setViewport] = useState<Viewport>({ w: 0, h: 0 });
  const [view, setView] = useState<View>({ scale: 1, tx: 0, ty: 0 });
  const [hover, setHover] = useState<{ col: number; row: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [painting, setPainting] = useState(false);

  const viewRef = useRef(view);
  viewRef.current = view;
  const limitsRef = useRef({ min: 0.05, max: 12 });
  const pointersRef = useRef(new Map<number, { x: number; y: number }>());
  const gestureRef = useRef<{
    mode: 'pan' | 'paint' | 'pinch';
    paintValue: boolean;
    lastCell: { col: number; row: number } | null;
    startView: View;
    startPointer: { x: number; y: number };
    pinchDistance?: number;
    pinchScale?: number;
    pinchMid?: { x: number; y: number };
    moved: boolean;
    touch: boolean;
    started: boolean;
    startCell: { col: number; row: number } | null;
  } | null>(null);
  const lastPointerTypeRef = useRef<string>('mouse');

  const fitScale = useMemo(() => {
    if (!viewport.w || !viewport.h) return 1;
    const pad = 0.94;
    return Math.min((viewport.w * pad) / grid.imageW, (viewport.h * pad) / grid.imageH);
  }, [viewport, grid.imageW, grid.imageH]);

  const clampView = useCallback((next: View): View => {
    const { min, max } = limitsRef.current;
    const scale = clamp(next.scale, min, max);
    const w = grid.imageW * scale;
    const h = grid.imageH * scale;
    // Не даём утащить мир за пределы окна: оставляем хотя бы часть на экране.
    const marginX = Math.min(viewport.w * 0.5, w * 0.5);
    const marginY = Math.min(viewport.h * 0.5, h * 0.5);
    const tx = clamp(next.tx, -w + marginX, viewport.w - marginX);
    const ty = clamp(next.ty, -h + marginY, viewport.h - marginY);
    return { scale, tx, ty };
  }, [grid.imageW, grid.imageH, viewport.w, viewport.h]);

  const doFit = useCallback(() => {
    if (!viewport.w || !viewport.h) return;
    const pad = 0.94;
    const scale = Math.min((viewport.w * pad) / grid.imageW, (viewport.h * pad) / grid.imageH);
    setView({
      scale,
      tx: (viewport.w - grid.imageW * scale) / 2,
      ty: (viewport.h - grid.imageH * scale) / 2,
    });
  }, [viewport, grid.imageW, grid.imageH]);

  useImperativeHandle(ref, () => ({
    fit: doFit,
    zoomBy: (factor: number) => {
      setView((prev) =>
        clampView({
          scale: prev.scale * factor,
          tx: viewport.w / 2 - (viewport.w / 2 - prev.tx) * factor,
          ty: viewport.h / 2 - (viewport.h / 2 - prev.ty) * factor,
        }),
      );
    },
  }), [doFit, clampView, viewport.w, viewport.h]);

  // Границы масштаба зависят от «вписанного» масштаба.
  useEffect(() => {
    if (!fitScale) return;
    limitsRef.current = { min: fitScale * 0.6, max: Math.max(fitScale * 24, 6) };
  }, [fitScale]);

  // Следим за размером окна карты.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const rect = entries[0].contentRect;
      setViewport({ w: Math.round(rect.width), h: Math.round(rect.height) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Вписываем мир при смене проекта и при первом известном размере окна.
  const fittedRef = useRef<string>('');
  useEffect(() => {
    if (!viewport.w || !viewport.h) return;
    const key = `${projectId}:${viewport.w}x${viewport.h}`;
    if (fittedRef.current === key) return;
    const isNewProject = !fittedRef.current.startsWith(`${projectId}:`);
    if (isNewProject || !fittedRef.current) {
      fittedRef.current = key;
      doFit();
    }
  }, [projectId, viewport.w, viewport.h, doFit]);

  // Масштаб наружу — для индикатора в шапке.
  useEffect(() => {
    onScaleChange?.(view.scale);
  }, [view.scale, onScaleChange]);

  const zoomAt = useCallback((px: number, py: number, nextScale: number) => {
    setView((prev) => {
      const scale = clamp(nextScale, limitsRef.current.min, limitsRef.current.max);
      const k = scale / prev.scale;
      return clampView({
        scale,
        tx: px - (px - prev.tx) * k,
        ty: py - (py - prev.ty) * k,
      });
    });
  }, [clampView]);

  // Зум колесом: слушатель не passive, иначе браузер скроллит страницу.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const px = e.clientX - rect.left;
      const py = e.clientY - rect.top;
      const base = e.deltaMode === 1 ? 18 : 1;
      const factor = Math.exp(-e.deltaY * base * 0.0016);
      const current = viewRef.current.scale;
      zoomAt(px, py, current * factor);
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, [zoomAt]);

  const localPoint = (clientX: number, clientY: number) => {
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    return { x: clientX - rect.left, y: clientY - rect.top };
  };

  const cellFromScreen = (px: number, py: number, clampToWorld: boolean) => {
    const v = viewRef.current;
    const wx = (px - v.tx) / v.scale;
    const wy = (py - v.ty) / v.scale;
    if (!clampToWorld && !isInsideWorld(grid, wx, wy)) return null;
    const col = clamp(Math.floor((wx - grid.originX) / grid.tileW), 0, grid.cols - 1);
    const row = clamp(Math.floor((wy - grid.originY) / grid.tileH), 0, grid.rows - 1);
    return { col, row };
  };

  const stampCells = (center: { col: number; row: number }, size: number): Array<[number, number]> => {
    const n = Math.max(1, Math.min(9, Math.round(size)));
    const back = Math.floor((n - 1) / 2);
    const cells: Array<[number, number]> = [];
    for (let dr = 0; dr < n; dr++) {
      for (let dc = 0; dc < n; dc++) {
        const col = center.col - back + dc;
        const row = center.row - back + dr;
        if (col >= 0 && row >= 0 && col < grid.cols && row < grid.rows) cells.push([col, row]);
      }
    }
    return cells;
  };

  const beginStroke = () => {
    const g = gestureRef.current;
    if (!g || g.started) return;
    g.started = true;
    onStrokeStart();
  };

  const paintAt = (px: number, py: number) => {
    const g = gestureRef.current;
    if (!g || g.mode !== 'paint') return;
    const cell = cellFromScreen(px, py, false);
    if (!cell) return;
    if (g.lastCell && g.lastCell.col === cell.col && g.lastCell.row === cell.row) return;
    beginStroke();
    g.lastCell = cell;
    onPaint(stampCells(cell, brushSize), g.paintValue);
  };

  const handlePointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button === 2) e.preventDefault();
    const isTouch = e.pointerType === 'touch';
    lastPointerTypeRef.current = e.pointerType || 'mouse';
    const point = localPoint(e.clientX, e.clientY);
    pointersRef.current.set(e.pointerId, point);
    e.currentTarget.setPointerCapture(e.pointerId);

    if (pointersRef.current.size === 2) {
      // Второй палец отменяет начатый мазок и превращает жест в зум.
      if (gestureRef.current?.mode === 'paint' && gestureRef.current.started) onStrokeEnd();
      const pts = [...pointersRef.current.values()];
      const dx = pts[0].x - pts[1].x;
      const dy = pts[0].y - pts[1].y;
      gestureRef.current = {
        mode: 'pinch',
        paintValue: true,
        lastCell: null,
        startView: viewRef.current,
        startPointer: point,
        pinchDistance: Math.hypot(dx, dy) || 1,
        pinchScale: viewRef.current.scale,
        pinchMid: { x: (pts[0].x + pts[1].x) / 2, y: (pts[0].y + pts[1].y) / 2 },
        moved: false,
        touch: isTouch,
        started: false,
        startCell: null,
      };
      setPainting(false);
      setDragging(false);
      return;
    }

    const panMode = tool === 'pan' || e.button === 1 || e.shiftKey;
    if (panMode) {
      gestureRef.current = {
        mode: 'pan',
        paintValue: true,
        lastCell: null,
        startView: viewRef.current,
        startPointer: point,
        moved: false,
        touch: isTouch,
        started: false,
        startCell: null,
      };
      setDragging(true);
      return;
    }

    const paintValue = tool === 'hide' ? false : e.button === 2 ? false : true;
    gestureRef.current = {
      mode: 'paint',
      paintValue,
      lastCell: null,
      startView: viewRef.current,
      startPointer: point,
      moved: false,
      touch: isTouch,
      started: false,
      startCell: cellFromScreen(point.x, point.y, false),
    };
    setPainting(true);
    // На тач-экране не закрашиваем сразу: вдруг это начало пинча.
    // Одиночное касание открывает клетку на отпускании, движение — рисует.
    if (!isTouch) paintAt(point.x, point.y);
  };

  const handlePointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const point = localPoint(e.clientX, e.clientY);
    if (pointersRef.current.has(e.pointerId)) pointersRef.current.set(e.pointerId, point);
    const g = gestureRef.current;

    if (!g) {
      setHover(cellFromScreen(point.x, point.y, true));
      return;
    }

    if (g.mode === 'pinch') {
      const pts = [...pointersRef.current.values()];
      if (pts.length < 2 || !g.pinchDistance || !g.pinchScale) return;
      const dx = pts[0].x - pts[1].x;
      const dy = pts[0].y - pts[1].y;
      const dist = Math.hypot(dx, dy) || 1;
      const factor = dist / g.pinchDistance;
      const mid = { x: (pts[0].x + pts[1].x) / 2, y: (pts[0].y + pts[1].y) / 2 };
      const target = g.pinchScale * factor;
      setView(() => {
        const scale = clamp(target, limitsRef.current.min, limitsRef.current.max);
        const worldX = (g.pinchMid!.x - g.startView.tx) / g.startView.scale;
        const worldY = (g.pinchMid!.y - g.startView.ty) / g.startView.scale;
        return clampView({
          scale,
          tx: mid.x - worldX * scale,
          ty: mid.y - worldY * scale,
        });
      });
      return;
    }

    if (g.mode === 'pan') {
      const dx = point.x - g.startPointer.x;
      const dy = point.y - g.startPointer.y;
      if (Math.abs(dx) > 2 || Math.abs(dy) > 2) g.moved = true;
      setView(clampView({
        scale: g.startView.scale,
        tx: g.startView.tx + dx,
        ty: g.startView.ty + dy,
      }));
      return;
    }

    setHover(cellFromScreen(point.x, point.y, true));

    if (g.touch && !g.moved) {
      const dx = point.x - g.startPointer.x;
      const dy = point.y - g.startPointer.y;
      if (Math.hypot(dx, dy) < 4) return;
      g.moved = true;
    }
    paintAt(point.x, point.y);
  };

  const endGesture = (e: ReactPointerEvent<HTMLDivElement>) => {
    pointersRef.current.delete(e.pointerId);
    const g = gestureRef.current;
    if (!g) {
      const point = localPoint(e.clientX, e.clientY);
      setHover(cellFromScreen(point.x, point.y, true));
      return;
    }
    if (g.mode === 'pinch' && pointersRef.current.size === 1) {
      // Палец остался один — продолжаем как панорамирование.
      const point = localPoint(e.clientX, e.clientY);
      gestureRef.current = {
        mode: 'pan',
        paintValue: true,
        lastCell: null,
        startView: viewRef.current,
        startPointer: point,
        moved: false,
        touch: true,
        started: false,
        startCell: null,
      };
      return;
    }
    if (g.mode === 'paint') {
      // Тап без движения — открыть (или скрыть) одну клетку.
      if (g.touch && !g.moved && g.startCell && !g.started) {
        beginStroke();
        onPaint(stampCells(g.startCell, brushSize), g.paintValue);
      }
      if (g.started) onStrokeEnd();
      setPainting(false);
    }
    if (g.mode === 'pan') setDragging(false);
    if (pointersRef.current.size === 0) gestureRef.current = null;
  };

  // Отрисовка слоя тумана и сетки.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = viewport.w;
    const h = viewport.h;
    if (!w || !h) return;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const { scale, tx, ty } = view;
    const imgW = grid.imageW * scale;
    const imgH = grid.imageH * scale;

    ctx.save();
    ctx.beginPath();
    ctx.rect(tx, ty, imgW, imgH);
    ctx.clip();

    const tileW = grid.tileW * scale;
    const tileH = grid.tileH * scale;

    // Видимый диапазон клеток.
    const col0 = clamp(Math.floor((-tx / scale - grid.originX) / grid.tileW), 0, grid.cols - 1);
    const col1 = clamp(Math.ceil(((w - tx) / scale - grid.originX) / grid.tileW), 0, grid.cols - 1);
    const row0 = clamp(Math.floor((-ty / scale - grid.originY) / grid.tileH), 0, grid.rows - 1);
    const row1 = clamp(Math.ceil(((h - ty) / scale - grid.originY) / grid.tileH), 0, grid.rows - 1);

    const pattern = getFogPattern(ctx);
    ctx.fillStyle = pattern ?? FOG_TOP;

    // Скрытые клетки заливаем «полосами» — так меньше вызовов fillRect.
    for (let row = row0; row <= row1; row++) {
      let runStart = -1;
      for (let col = col0; col <= col1 + 1; col++) {
        const hidden = col <= col1 && !isRevealed(revealed, col, row);
        if (hidden && runStart === -1) runStart = col;
        if (!hidden && runStart !== -1) {
          const x0 = tx + (grid.originX + runStart * grid.tileW) * scale;
          const x1 = tx + (grid.originX + col * grid.tileW) * scale;
          const y0 = ty + (grid.originY + row * grid.tileH) * scale;
          ctx.fillRect(x0, y0, x1 - x0 + 0.5, tileH + 0.5);
          runStart = -1;
        }
      }
    }

    // Лёгкая подсветка скрытых клеток по краям — объём у «тумана».
    if (tileW > 6) {
      ctx.strokeStyle = 'rgba(126, 224, 195, 0.05)';
      ctx.lineWidth = 1;
      for (let row = row0; row <= row1; row++) {
        for (let col = col0; col <= col1; col++) {
          if (isRevealed(revealed, col, row)) continue;
          const x = tx + (grid.originX + col * grid.tileW) * scale;
          const y = ty + (grid.originY + row * grid.tileH) * scale;
          ctx.strokeRect(x + 0.5, y + 0.5, tileW - 1, tileH - 1);
        }
      }
    }

    // Сетка поверх открытых участков.
    if (showGrid && tileW >= 5) {
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.16)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (let col = col0; col <= col1 + 1; col++) {
        const x = Math.round(tx + (grid.originX + col * grid.tileW) * scale) + 0.5;
        if (x < -1 || x > w + 1) continue;
        ctx.moveTo(x, Math.max(0, ty));
        ctx.lineTo(x, Math.min(h, ty + imgH));
      }
      for (let row = row0; row <= row1 + 1; row++) {
        const y = Math.round(ty + (grid.originY + row * grid.tileH) * scale) + 0.5;
        if (y < -1 || y > h + 1) continue;
        ctx.moveTo(Math.max(0, tx), y);
        ctx.lineTo(Math.min(w, tx + imgW), y);
      }
      ctx.stroke();
    }

    // Курсор-кисть.
    if (hover && !dragging && tool !== 'pan') {
      const n = Math.max(1, Math.min(9, Math.round(brushSize)));
      const back = Math.floor((n - 1) / 2);
      const c0 = clamp(hover.col - back, 0, grid.cols - 1);
      const r0 = clamp(hover.row - back, 0, grid.rows - 1);
      const c1 = clamp(hover.col - back + n - 1, 0, grid.cols - 1);
      const r1 = clamp(hover.row - back + n - 1, 0, grid.rows - 1);
      const x = tx + (grid.originX + c0 * grid.tileW) * scale;
      const y = ty + (grid.originY + r0 * grid.tileH) * scale;
      const wpx = (c1 - c0 + 1) * tileW;
      const hpx = (r1 - r0 + 1) * tileH;
      ctx.fillStyle = tool === 'hide' ? 'rgba(255, 120, 120, 0.18)' : `${ACCENT}2e`;
      ctx.fillRect(x, y, wpx, hpx);
      ctx.strokeStyle = tool === 'hide' ? 'rgba(255, 130, 130, 0.9)' : ACCENT;
      ctx.lineWidth = 1.5;
      ctx.strokeRect(x + 0.75, y + 0.75, wpx - 1.5, hpx - 1.5);
    }

    ctx.restore();

    // Рамка мира.
    ctx.strokeStyle = 'rgba(126, 224, 195, 0.35)';
    ctx.lineWidth = 1;
    ctx.strokeRect(tx + 0.5, ty + 0.5, imgW - 1, imgH - 1);
  }, [revealed, grid, view, viewport, hover, showGrid, tool, brushSize, dragging]);

  const cursor =
    tool === 'pan' ? (dragging ? 'grabbing' : 'grab')
      : painting ? 'crosshair'
        : tool === 'hide' ? 'crosshair'
          : 'crosshair';

  return (
    <div
      ref={containerRef}
      className={`map-viewport tool-${tool}`}
      style={{ cursor }}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={endGesture}
      onPointerCancel={endGesture}
      onPointerLeave={() => setHover(null)}
      onDoubleClick={(e) => {
        // На тач-экранах зум делается пинчем, двойной тап только мешает рисовать.
        if (lastPointerTypeRef.current === 'touch') return;
        const p = localPoint(e.clientX, e.clientY);
        zoomAt(p.x, p.y, viewRef.current.scale * 1.6);
      }}
      onContextMenu={(e) => e.preventDefault()}
    >
      <div
        className="map-stage"
        style={{
          transform: `translate3d(${view.tx}px, ${view.ty}px, 0)`,
          width: grid.imageW * view.scale,
          height: grid.imageH * view.scale,
        }}
      >
        <img
          className="map-image"
          src={image.dataUrl}
          alt={image.name}
          draggable={false}
          style={{ width: '100%', height: '100%' }}
        />
      </div>
      <canvas ref={canvasRef} className="map-canvas" style={{ width: viewport.w, height: viewport.h }} />
    </div>
  );
});
