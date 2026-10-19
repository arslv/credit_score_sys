import { useState } from "react";
import { Monitor, Moon, Sun, type LucideIcon } from "lucide-react";
import { readTheme, saveTheme, type ThemeChoice } from "../lib/theme";

const OPTIONS: { value: ThemeChoice; label: string; Icon: LucideIcon }[] = [
  { value: "light", label: "Светлая", Icon: Sun },
  { value: "dark", label: "Тёмная", Icon: Moon },
  { value: "auto", label: "Как в системе", Icon: Monitor },
];

// Переключатель темы внизу меню: светлая / тёмная / как в системе.
export function ThemeSwitch() {
  const [choice, setChoice] = useState<ThemeChoice>(readTheme);
  return (
    <div className="theme-switch" role="radiogroup" aria-label="Тема оформления">
      <span className="theme-switch__label">Тема</span>
      {OPTIONS.map(({ value, label, Icon }) => (
        <button
          key={value}
          type="button"
          role="radio"
          aria-checked={choice === value}
          aria-label={label}
          title={label}
          className={choice === value ? "theme-switch__btn theme-switch__btn--on" : "theme-switch__btn"}
          onClick={() => {
            saveTheme(value);
            setChoice(value);
          }}
        >
          <Icon size={15} strokeWidth={1.75} />
        </button>
      ))}
    </div>
  );
}
