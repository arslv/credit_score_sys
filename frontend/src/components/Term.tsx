import type { ReactNode } from "react";

// T17, шаг 3: подсказка у термина (иконка «?», текст по hover/focus) —
// «Понятность везде» из брифинга. Общий компонент, не копия разметки на
// каждое место использования.
export function Term({ children, tip }: { children: ReactNode; tip: string }) {
  return (
    <span className="term" tabIndex={0}>
      {children}
      <span className="q">?</span>
      <span className="tip">{tip}</span>
    </span>
  );
}
