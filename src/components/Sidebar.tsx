import { useRef } from 'react';
import type { Project } from '../lib/projects';

interface SidebarProps {
  projects: Project[];
  activeId: string | null;
  progressById: Record<string, number>;
  onSelect: (id: string) => void;
  onNew: (file: File) => void;
  onImport: (file: File) => void;
  onExport: (id: string) => void;
  onDelete: (id: string) => void;
  onClose?: () => void;
}

export function Sidebar({
  projects,
  activeId,
  progressById,
  onSelect,
  onNew,
  onImport,
  onExport,
  onDelete,
  onClose,
}: SidebarProps) {
  const newInputRef = useRef<HTMLInputElement | null>(null);
  const importInputRef = useRef<HTMLInputElement | null>(null);

  return (
    <aside className="sidebar">
      <div className="sidebar-head">
        <span className="sidebar-title">Миры</span>
        <span className="badge">{projects.length}</span>
        {onClose && (
          <button className="icon-btn sidebar-close" onClick={onClose} aria-label="Скрыть миры">
            ✕
          </button>
        )}
      </div>

      <div className="sidebar-actions">
        <button className="btn btn-primary" onClick={() => newInputRef.current?.click()}>
          + Новый мир
        </button>
        <button className="btn btn-ghost" onClick={() => importInputRef.current?.click()} title="Загрузить проект из JSON">
          ↓ Импорт
        </button>
      </div>

      <input
        ref={newInputRef}
        type="file"
        accept="image/*"
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onNew(file);
          e.target.value = '';
        }}
      />
      <input
        ref={importInputRef}
        type="file"
        accept="application/json,.json"
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onImport(file);
          e.target.value = '';
        }}
      />

      <ul className="project-list">
        {projects.map((project) => {
          const open = progressById[project.id] ?? 0;
          return (
            <li key={project.id}>
              <div
                className={`project-card${project.id === activeId ? ' is-active' : ''}`}
                role="button"
                tabIndex={0}
                onClick={() => onSelect(project.id)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    onSelect(project.id);
                  }
                }}
              >
                <div className="project-thumb">
                  <img src={project.image.dataUrl} alt="" draggable={false} />
                  <span className="project-thumb-fill" style={{ height: `${Math.round(open * 100)}%` }} />
                </div>
                <div className="project-meta">
                  <span className="project-name" title={project.name}>{project.name}</span>
                  <span className="project-sub">
                    {project.image.width}×{project.image.height} · {Math.round(open * 100)}%
                  </span>
                </div>
                <div className="project-tools">
                  <button
                    className="icon-btn"
                    title="Экспортировать проект"
                    onClick={(e) => {
                      e.stopPropagation();
                      onExport(project.id);
                    }}
                  >
                    ⤓
                  </button>
                  <button
                    className="icon-btn icon-btn-danger"
                    title="Удалить проект"
                    onClick={(e) => {
                      e.stopPropagation();
                      if (confirm(`Удалить «${project.name}»? Прогресс будет потерян.`)) onDelete(project.id);
                    }}
                  >
                    ✕
                  </button>
                </div>
              </div>
            </li>
          );
        })}
        {projects.length === 0 && <li className="empty-note">Пока нет миров. Загрузите карту или начните с демо.</li>}
      </ul>

      <div className="sidebar-foot">
        <span>Всё хранится локально в браузере</span>
      </div>
    </aside>
  );
}
