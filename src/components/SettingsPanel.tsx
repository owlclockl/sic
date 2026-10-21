import { useRef } from 'react';
import {
  MAX_COLUMNS,
  MAX_TILE,
  MIN_COLUMNS,
  MIN_TILE,
  type Grid,
  type GridConfig,
} from '../lib/grid';
import type { Project } from '../lib/projects';

interface SettingsPanelProps {
  project: Project;
  progress: number;
  grid: Grid;
  config: GridConfig;
  onConfigChange: (patch: Partial<GridConfig>) => void;
  onResetGrid: () => void;
  onReplaceImage: (file: File) => void;
  onRename: (name: string) => void;
  onRevealAll: () => void;
  onHideAll: () => void;
  onExport: () => void;
  onDelete: () => void;
  onClose: () => void;
}

const TILE_PRESETS = [16, 24, 32, 48, 64, 96, 128];
const COLUMN_PRESETS = [12, 16, 24, 32, 48, 64];

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(value < 0.1 ? 1 : 0)}%`;
}

export function SettingsPanel({
  project,
  progress,
  grid,
  config,
  onConfigChange,
  onResetGrid,
  onReplaceImage,
  onRename,
  onRevealAll,
  onHideAll,
  onExport,
  onDelete,
  onClose,
}: SettingsPanelProps) {
  const fileRef = useRef<HTMLInputElement | null>(null);
  const total = grid.cols * grid.rows;
  const share = grid.tileW / grid.imageW;

  return (
    <aside className="settings">
      <div className="settings-head">
        <span className="sidebar-title">Настройки</span>
        <button className="icon-btn" onClick={onClose} aria-label="Скрыть настройки">✕</button>
      </div>

      <section className="settings-section">
        <label className="field-label" htmlFor="world-name">Название мира</label>
        <input
          id="world-name"
          className="text-input"
          value={project.name}
          onChange={(e) => onRename(e.target.value)}
          maxLength={60}
        />
        <p className="hint">
          {project.image.width}×{project.image.height} px · {project.image.name}
        </p>
      </section>

      <section className="settings-section">
        <div className="section-title">Размер квадратиков</div>

        <div className="segmented" role="tablist" aria-label="Способ задания сетки">
          <button
            role="tab"
            aria-selected={config.mode === 'tile'}
            className={config.mode === 'tile' ? 'is-active' : ''}
            onClick={() => onConfigChange({ mode: 'tile' })}
          >
            В пикселях
          </button>
          <button
            role="tab"
            aria-selected={config.mode === 'columns'}
            className={config.mode === 'columns' ? 'is-active' : ''}
            onClick={() => onConfigChange({ mode: 'columns', columns: grid.cols })}
          >
            По колонкам
          </button>
        </div>

        {config.mode === 'tile' ? (
          <>
            <div className="range-row">
              <input
                type="range"
                min={MIN_TILE}
                max={MAX_TILE}
                step={1}
                value={Math.round(config.tileSize)}
                onChange={(e) => onConfigChange({ mode: 'tile', tileSize: Number(e.target.value) })}
              />
              <div className="number-box">
                <input
                  type="number"
                  min={MIN_TILE}
                  max={MAX_TILE}
                  value={Math.round(config.tileSize)}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    if (Number.isFinite(v)) onConfigChange({ mode: 'tile', tileSize: v });
                  }}
                />
                <span>px</span>
              </div>
            </div>
            <div className="chip-row">
              {TILE_PRESETS.map((size) => (
                <button
                  key={size}
                  className={`chip${Math.round(config.tileSize) === size ? ' is-active' : ''}`}
                  onClick={() => onConfigChange({ mode: 'tile', tileSize: size })}
                >
                  {size}
                </button>
              ))}
            </div>
            <p className="hint">
              Квадрат {Math.round(grid.tileW)} px исходника — это{' '}
              {(share * 100).toFixed(share < 0.01 ? 1 : 0)}% ширины мира.
            </p>
          </>
        ) : (
          <>
            <div className="range-row">
              <input
                type="range"
                min={MIN_COLUMNS}
                max={MAX_COLUMNS}
                step={1}
                value={Math.round(config.columns)}
                onChange={(e) => onConfigChange({ mode: 'columns', columns: Number(e.target.value) })}
              />
              <div className="number-box">
                <input
                  type="number"
                  min={MIN_COLUMNS}
                  max={MAX_COLUMNS}
                  value={Math.round(config.columns)}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    if (Number.isFinite(v)) onConfigChange({ mode: 'columns', columns: v });
                  }}
                />
                <span>кол.</span>
              </div>
            </div>
            <div className="chip-row">
              {COLUMN_PRESETS.map((size) => (
                <button
                  key={size}
                  className={`chip${Math.round(config.columns) === size ? ' is-active' : ''}`}
                  onClick={() => onConfigChange({ mode: 'columns', columns: size })}
                >
                  {size}
                </button>
              ))}
            </div>
            <p className="hint">
              Сетка подстраивается под любое разрешение: клетка всегда одна и та же доля карты.
            </p>
          </>
        )}

        <div className="grid-summary">
          <div>
            <span className="summary-value">{grid.cols} × {grid.rows}</span>
            <span className="summary-label">клеток</span>
          </div>
          <div>
            <span className="summary-value">{total.toLocaleString('ru-RU')}</span>
            <span className="summary-label">всего</span>
          </div>
          <div>
            <span className="summary-value">{Math.round(grid.tileW * 10) / 10} px</span>
            <span className="summary-label">сторона</span>
          </div>
        </div>
      </section>

      <section className="settings-section">
        <div className="section-title">Выравнивание сетки</div>
        <div className="offset-row">
          <span className="offset-label">Сдвиг X</span>
          <input
            type="range"
            min={0}
            max={Math.max(1, Math.round(grid.tileW) - 1)}
            value={config.offsetX}
            onChange={(e) => onConfigChange({ offsetX: Number(e.target.value) })}
          />
          <span className="offset-value">{Math.round(config.offsetX)}</span>
        </div>
        <div className="offset-row">
          <span className="offset-label">Сдвиг Y</span>
          <input
            type="range"
            min={0}
            max={Math.max(1, Math.round(grid.tileH) - 1)}
            value={config.offsetY}
            onChange={(e) => onConfigChange({ offsetY: Number(e.target.value) })}
          />
          <span className="offset-value">{Math.round(config.offsetY)}</span>
        </div>
        <button className="btn btn-ghost btn-block" onClick={onResetGrid}>Сбросить сетку</button>
        <p className="hint">
          Сдвиг помогает совместить сетку с границами регионов на карте.
        </p>
      </section>

      <section className="settings-section">
        <div className="section-title">Прогресс</div>
        <div className="progress-line">
          <div className="progress-track">
            <div className="progress-fill" style={{ width: `${Math.min(100, progress * 100)}%` }} />
          </div>
          <span className="progress-value">{formatPercent(progress)}</span>
        </div>
        <div className="button-grid">
          <button className="btn btn-ghost" onClick={onRevealAll}>Открыть всё</button>
          <button className="btn btn-ghost" onClick={onHideAll}>Скрыть всё</button>
        </div>
        <p className="hint">
          Изменение размера сетки сохраняет уже открытые клетки — прогресс не сбрасывается.
        </p>
      </section>

      <section className="settings-section">
        <div className="section-title">Картинка мира</div>
        <button className="btn btn-ghost btn-block" onClick={() => fileRef.current?.click()}>
          Заменить изображение
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) onReplaceImage(file);
            e.target.value = '';
          }}
        />
        <button className="btn btn-ghost btn-block" onClick={onExport}>Экспортировать проект (JSON)</button>
        <button className="btn btn-danger btn-block" onClick={onDelete}>Удалить мир</button>
        <p className="hint">
          Замена картинки сбрасывает прогресс, но сохраняет размер сетки.
        </p>
      </section>
    </aside>
  );
}

