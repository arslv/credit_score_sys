// T23: общие строительные блоки экранов — один заголовок страницы, одно
// пустое состояние, одна загрузка/ошибка, одно модальное окно. Раньше каждый
// экран собирал их сам (h1 + muted-абзац, «Загрузка…» разным текстом и
// местом) — отсюда разнобой. Все размеры — токенами index.css.
import { useEffect, useId, useRef, type ReactNode } from "react";
import { Search, X } from "lucide-react";

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div className="page-header__text">
        <h1>{title}</h1>
        {description && <p className="page-header__description">{description}</p>}
      </div>
      {actions && <div className="page-header__actions">{actions}</div>}
    </header>
  );
}

export function LoadingState({ label = "Загрузка…" }: { label?: string }) {
  return (
    <p className="state state--loading" role="status">
      <span className="spinner" aria-hidden="true" />
      {label}
    </p>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="state state--error" role="alert">
      <span>{message}</span>
      {onRetry && (
        <button type="button" className="btn btn-small" onClick={onRetry}>
          Повторить
        </button>
      )}
    </div>
  );
}

// Пустое состояние: одна фраза — что здесь будет, и не больше одной кнопки.
export function EmptyState({ title, hint, action }: { title: string; hint?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty-state">
      <p className="empty-state__title">{title}</p>
      {hint && <p className="empty-state__hint">{hint}</p>}
      {action && <div className="empty-state__action">{action}</div>}
    </div>
  );
}

export function SearchInput({
  value,
  onChange,
  placeholder = "Поиск",
  label,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  label?: string;
}) {
  return (
    <label className="search-input">
      <Search size={14} strokeWidth={2} aria-hidden="true" />
      <input
        type="search"
        value={value}
        placeholder={placeholder}
        aria-label={label ?? placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

export function Toolbar({ children }: { children: ReactNode }) {
  return <div className="toolbar">{children}</div>;
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { value: T; label: string; count?: number }[];
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div className="segmented" role="tablist" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="tab"
          aria-selected={option.value === value}
          className={option.value === value ? "segmented__item segmented__item--active" : "segmented__item"}
          onClick={() => onChange(option.value)}
        >
          {option.label}
          {option.count !== undefined && <span className="segmented__count">{option.count}</span>}
        </button>
      ))}
    </div>
  );
}

// Модальное окно для создания/редактирования: Esc и клик по фону закрывают,
// фокус — на первое поле.
export function Modal({
  title,
  open,
  onClose,
  children,
  footer,
}: {
  title: string;
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    dialogRef.current?.querySelector<HTMLElement>("input, select, textarea, button:not(.modal__close)")?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={dialogRef}>
        <div className="modal__header">
          <h2 id={titleId}>{title}</h2>
          <button type="button" className="modal__close" aria-label="Закрыть" onClick={onClose}>
            <X size={16} />
          </button>
        </div>
        <div className="modal__body">{children}</div>
        {footer && <div className="modal__footer">{footer}</div>}
      </div>
    </div>
  );
}

export function Pagination({
  page,
  pageSize,
  total,
  onPage,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (total <= pageSize) return null;
  return (
    <div className="pagination">
      <span>
        {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} из {total}
      </span>
      <button className="btn btn-small" disabled={page <= 1} onClick={() => onPage(page - 1)}>
        ← назад
      </button>
      <span>
        Страница {page} из {pages}
      </span>
      <button className="btn btn-small" disabled={page >= pages} onClick={() => onPage(page + 1)}>
        вперёд →
      </button>
    </div>
  );
}

// Поиск по строке без учёта регистра и «ё» — для локальной фильтрации
// небольших справочников (сотрудники, категории, чёрный список).
export function matches(query: string, ...values: (string | null | undefined)[]): boolean {
  const normalize = (s: string) => s.toLowerCase().replace(/ё/g, "е").trim();
  const q = normalize(query);
  if (!q) return true;
  return values.some((v) => v && normalize(v).includes(q));
}
