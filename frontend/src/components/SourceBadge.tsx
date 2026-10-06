import { hexToRgba, sourceMeta } from "../lib/labels";

// T16: без логотипов (легальный риск использовать чужой бренд-ассет без
// разрешения, ваше явное указание) — текстовая плашка: прозрачный фон
// (8–12% альфы цвета источника), тонкая рамка и текст в этом же цвете.
// Цвет сам по себе — не единственный носитель смысла (подпись всегда рядом
// текстом), поэтому плашка одинаково читаема в светлой и тёмной теме.
export function SourceBadge({ source }: { source: string }) {
  const meta = sourceMeta(source);
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        padding: "2px 8px",
        borderRadius: 5,
        background: hexToRgba(meta.color, 0.1),
        border: `1px solid ${hexToRgba(meta.color, 0.4)}`,
        color: meta.color,
        fontSize: 12,
        fontWeight: 600,
        whiteSpace: "nowrap",
      }}
    >
      {meta.label}
    </span>
  );
}
