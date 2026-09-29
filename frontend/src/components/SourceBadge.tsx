import { sourceMeta } from "../lib/labels";

// Небольшая цветная «плашка-лого» вместо голого кода источника (mvd/fns/...)
// в списке проверок — не настоящий логотип (легальные вопросы, нет реальных
// ассетов бренда под рукой), но узнаваемый цветной значок + подпись.
export function SourceBadge({ source }: { source: string }) {
  const meta = sourceMeta(source);
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: 28,
          height: 20,
          borderRadius: 5,
          background: meta.color,
          color: "#fff",
          fontSize: 10,
          fontWeight: 700,
          letterSpacing: "0.02em",
          flexShrink: 0,
        }}
      >
        {meta.abbr}
      </span>
      {meta.label}
    </span>
  );
}
