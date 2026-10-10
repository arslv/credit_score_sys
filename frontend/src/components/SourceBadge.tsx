import { sourceMeta } from "../lib/labels";

// T16: без логотипов (легальный риск чужого бренд-ассета). T20: и без цветных
// плашек — один акцентный цвет на весь интерфейс, источник — просто подпись.
export function SourceBadge({ source }: { source: string }) {
  return <span className="source-name">{sourceMeta(source).label}</span>;
}
