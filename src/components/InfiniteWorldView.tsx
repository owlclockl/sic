import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent, WheelEvent as ReactWheelEvent } from 'react';
import { drawWorld, type ProceduralWorld } from '../lib/proceduralWorld';
import type { MapHandle } from './MapView';

interface Props {
  world: ProceduralWorld;
  onScaleChange?: (scale: number) => void;
}
interface Camera { x: number; y: number; tileSize: number }

export const InfiniteWorldView = forwardRef<MapHandle, Props>(function InfiniteWorldView({ world, onScaleChange }, ref) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const cameraRef = useRef<Camera>({ x: -18, y: -12, tileSize: 32 });
  const [camera, setCamera] = useState(cameraRef.current);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [pointer, setPointer] = useState<{ x: number; y: number } | null>(null);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const gesture = useRef<
    | { mode: 'pan'; id: number; x: number; y: number; cameraX: number; cameraY: number }
    | { mode: 'pinch'; distance: number; tileSize: number; worldX: number; worldY: number }
    | null
  >(null);

  const setCameraState = useCallback((next: Camera) => {
    cameraRef.current = next;
    setCamera(next);
  }, []);

  const zoomAt = useCallback((screenX: number, screenY: number, factor: number) => {
    const old = cameraRef.current;
    const tileSize = Math.min(96, Math.max(6, old.tileSize * factor));
    const worldX = old.x + screenX / old.tileSize;
    const worldY = old.y + screenY / old.tileSize;
    setCameraState({ x: worldX - screenX / tileSize, y: worldY - screenY / tileSize, tileSize });
  }, [setCameraState]);

  useImperativeHandle(ref, () => ({
    fit: () => setCameraState({ x: -18, y: -12, tileSize: 32 }),
    zoomBy: (factor) => zoomAt((size.width || 0) / 2, (size.height || 0) / 2, factor),
  }), [setCameraState, size.width, size.height, zoomAt]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const observer = new ResizeObserver(([entry]) => {
      setSize({ width: Math.round(entry.contentRect.width), height: Math.round(entry.contentRect.height) });
    });
    observer.observe(host);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !size.width || !size.height) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.round(size.width * dpr);
    canvas.height = Math.round(size.height * dpr);
    canvas.style.width = `${size.width}px`;
    canvas.style.height = `${size.height}px`;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.imageSmoothingEnabled = false;
    drawWorld(ctx, size.width, size.height, world, camera.x, camera.y, camera.tileSize);
    onScaleChange?.(camera.tileSize / 32);
  }, [world.seed, world.biome, size.width, size.height, camera, onScaleChange]);

  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    const rect = hostRef.current?.getBoundingClientRect();
    const points = [...pointers.current.values()];
    if (points.length >= 2 && rect) {
      const [a, b] = points;
      const midX = (a.x + b.x) / 2 - rect.left;
      const midY = (a.y + b.y) / 2 - rect.top;
      const dx = a.x - b.x; const dy = a.y - b.y;
      gesture.current = {
        mode: 'pinch', distance: Math.hypot(dx, dy) || 1, tileSize: cameraRef.current.tileSize,
        worldX: cameraRef.current.x + midX / cameraRef.current.tileSize,
        worldY: cameraRef.current.y + midY / cameraRef.current.tileSize,
      };
    } else {
      gesture.current = { mode: 'pan', id: event.pointerId, x: event.clientX, y: event.clientY, cameraX: cameraRef.current.x, cameraY: cameraRef.current.y };
    }
  };
  const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const rect = hostRef.current?.getBoundingClientRect();
    if (pointers.current.has(event.pointerId)) pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    const currentCamera = cameraRef.current;
    if (rect) setPointer({ x: currentCamera.x + (event.clientX - rect.left) / currentCamera.tileSize, y: currentCamera.y + (event.clientY - rect.top) / currentCamera.tileSize });
    const active = gesture.current;
    if (!active) return;
    if (active.mode === 'pinch') {
      const points = [...pointers.current.values()];
      if (points.length < 2 || !rect) return;
      const [a, b] = points;
      const midX = (a.x + b.x) / 2 - rect.left;
      const midY = (a.y + b.y) / 2 - rect.top;
      const scale = Math.min(96, Math.max(6, active.tileSize * Math.hypot(a.x - b.x, a.y - b.y) / active.distance));
      setCameraState({ x: active.worldX - midX / scale, y: active.worldY - midY / scale, tileSize: scale });
      return;
    }
    if (active.id !== event.pointerId) return;
    setCameraState({ ...cameraRef.current, x: active.cameraX - (event.clientX - active.x) / cameraRef.current.tileSize, y: active.cameraY - (event.clientY - active.y) / cameraRef.current.tileSize });
  };
  const handlePointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    pointers.current.delete(event.pointerId);
    const remaining = [...pointers.current.entries()][0];
    if (gesture.current?.mode === 'pinch' && remaining) {
      gesture.current = { mode: 'pan', id: remaining[0], x: remaining[1].x, y: remaining[1].y, cameraX: cameraRef.current.x, cameraY: cameraRef.current.y };
    } else if (gesture.current?.mode === 'pan' && gesture.current.id === event.pointerId) {
      gesture.current = null;
    }
  };
  const handleWheel = (event: ReactWheelEvent<HTMLDivElement>) => {
    event.preventDefault();
    const rect = hostRef.current?.getBoundingClientRect();
    if (!rect) return;
    zoomAt(event.clientX - rect.left, event.clientY - rect.top, event.deltaY < 0 ? 1.14 : 1 / 1.14);
  };

  return (
    <div ref={hostRef} className="infinite-world-view" onPointerDown={handlePointerDown} onPointerMove={handlePointerMove} onPointerUp={handlePointerUp} onPointerCancel={handlePointerUp} onPointerLeave={() => setPointer(null)} onWheel={handleWheel}>
      <canvas ref={canvasRef} className="infinite-world-canvas" />
      <div className="world-coordinate" aria-live="polite">
        <span className="world-live-dot" /> Бесконечный мир <span>·</span> {world.biome === 'desert' ? 'Пески' : world.biome === 'frozen' ? 'Ледяной' : 'Лесной'} биом <span>·</span> seed {world.seed}
        {pointer && <><span>·</span> X {Math.floor(pointer.x)} / Y {Math.floor(pointer.y)}</>}
      </div>
      <div className="world-infinite-badge">∞ · генерация чанков на лету</div>
    </div>
  );
});
