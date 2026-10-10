import { useState } from "react";

// T20: заголовок вердикта с единственной на экране подсказкой «?». По клику
// (не по наведению) под заголовком раскрывается объяснение ИМЕННО этого
// решения своими словами (lib/explain.ts), а не общий текст из словаря:
// определения терминов живут в Справке.
export function VerdictTitle({ title, help }: { title: string; help: string | null }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <h1 className="verdict-title">
        {title}
        {help && (
          <button
            type="button"
            className="help-toggle"
            aria-expanded={open}
            aria-label="Почему такое решение — объяснение"
            onClick={() => setOpen((value) => !value)}
          >
            ?
          </button>
        )}
      </h1>
      {help && open && (
        <p className="help-text" role="note">
          {help}
        </p>
      )}
    </>
  );
}
