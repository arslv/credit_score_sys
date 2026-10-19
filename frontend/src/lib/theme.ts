// Тема оформления: светлая, тёмная или как в системе (по умолчанию).
// Выбор — удобство одного сотрудника в его браузере, поэтому localStorage;
// если он недоступен (приватный режим), просто остаётся «как в системе».
export type ThemeChoice = "auto" | "light" | "dark";

const KEY = "dialog.theme";

export function readTheme(): ThemeChoice {
  try {
    const value = localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : "auto";
  } catch {
    return "auto";
  }
}

// CSS (index.css) уже умеет: :root[data-theme="dark"|"light"] перебивает
// системную тему, без атрибута — prefers-color-scheme.
export function applyTheme(choice: ThemeChoice): void {
  const root = document.documentElement;
  if (choice === "auto") delete root.dataset.theme;
  else root.dataset.theme = choice;
}

export function saveTheme(choice: ThemeChoice): void {
  try {
    if (choice === "auto") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, choice);
  } catch {
    // Хранилище недоступно — тема действует до перезагрузки страницы.
  }
  applyTheme(choice);
}
