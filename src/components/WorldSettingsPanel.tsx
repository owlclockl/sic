import type { ProceduralWorld, WorldBiome } from '../lib/proceduralWorld';
import type { Project } from '../lib/projects';

interface Props {
  project: Project;
  world: ProceduralWorld;
  onRename: (name: string) => void;
  onWorldChange: (world: ProceduralWorld) => void;
  onExport: () => void;
  onDelete: () => void;
  onClose: () => void;
}

const BIOMES: Array<{ value: WorldBiome; label: string; emoji: string }> = [
  { value: 'temperate', label: 'Зелёный', emoji: '🌲' },
  { value: 'desert', label: 'Пустыня', emoji: '🏜️' },
  { value: 'frozen', label: 'Снега', emoji: '❄️' },
];

export function WorldSettingsPanel({ project, world, onRename, onWorldChange, onExport, onDelete, onClose }: Props) {
  return (
    <aside className="settings">
      <div className="settings-head">
        <span className="sidebar-title">Генератор мира</span>
        <button className="icon-btn" onClick={onClose} aria-label="Скрыть настройки">✕</button>
      </div>
      <section className="settings-section">
        <label className="field-label" htmlFor="world-name">Название мира</label>
        <input id="world-name" className="text-input" value={project.name} onChange={(e) => onRename(e.target.value)} maxLength={60} />
        <p className="hint">Мир бесконечен: новые чанки создаются, когда вы исследуете карту.</p>
      </section>
      <section className="settings-section">
        <div className="section-title">Тип местности</div>
        <div className="world-biomes">
          {BIOMES.map((biome) => (
            <button key={biome.value} className={`world-biome${world.biome === biome.value ? ' is-active' : ''}`} onClick={() => onWorldChange({ ...world, biome: biome.value })}>
              <span>{biome.emoji}</span>{biome.label}
            </button>
          ))}
        </div>
      </section>
      <section className="settings-section">
        <label className="field-label" htmlFor="world-seed">Ключ генерации (seed)</label>
        <div className="seed-row">
          <input id="world-seed" className="text-input" type="number" value={world.seed} onChange={(e) => {
            const value = Number(e.target.value);
            if (Number.isFinite(value)) onWorldChange({ ...world, seed: Math.trunc(value) });
          }} />
          <button className="btn btn-ghost seed-randomize" onClick={() => onWorldChange({ ...world, seed: Math.floor(Math.random() * 2_000_000_000) + 1 })} title="Случайный seed">⟳</button>
        </div>
        <p className="hint">Одинаковый ключ всегда даёт одну и ту же карту. Скопируйте его, чтобы вернуться к миру.</p>
      </section>
      <section className="settings-section world-facts">
        <div className="section-title">Процедурная карта</div>
        <div className="world-fact"><span>Размер</span><strong>∞ · без границ</strong></div>
        <div className="world-fact"><span>Генерация</span><strong>по координатам, локально</strong></div>
        <div className="world-fact"><span>Сохранение</span><strong>в этом проекте</strong></div>
      </section>
      <div className="world-settings-actions">
        <button className="btn btn-ghost" onClick={onExport}>⤓ Экспорт мира</button>
        <button className="btn btn-danger" onClick={onDelete}>Удалить мир</button>
      </div>
    </aside>
  );
}
