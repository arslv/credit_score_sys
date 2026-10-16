import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { Badge } from "../components/Badge";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  Modal,
  PageHeader,
  SearchInput,
  Segmented,
  Toolbar,
  matches,
} from "../components/ui";
import { liquidityLabel, reasonCodeKindLabel } from "../lib/labels";

type Tab = "goods" | "blacklist" | "reasons";

// T23: три справочника — вкладками на одной странице, у каждого поиск,
// фильтр и добавление/правка в модальном окне (раньше — формы-строки под
// таблицами и все три таблицы подряд).
export function ReferencesPage() {
  const [tab, setTab] = useState<Tab>("goods");
  return (
    <section>
      <PageHeader
        title="Справочники"
        description="Данные, на которые опираются проверки: категории товаров (шариат и ликвидность), чёрный список и коды причин решений."
      />
      <Segmented
        label="Справочник"
        value={tab}
        onChange={setTab}
        options={[
          { value: "goods", label: "Категории товаров" },
          { value: "blacklist", label: "Чёрный список" },
          { value: "reasons", label: "Причины" },
        ]}
      />
      {tab === "goods" && <GoodsCategories />}
      {tab === "blacklist" && <BlacklistSection />}
      {tab === "reasons" && <ReasonCodes />}
    </section>
  );
}

interface Category {
  id: string;
  code: string;
  name: string;
  liquidity: string;
  sharia_allowed: boolean;
  active: boolean;
}

const NEW_CATEGORY = { code: "", name: "", liquidity: "high", sharia_allowed: true, active: true };

function GoodsCategories() {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [sharia, setSharia] = useState("");
  const [editing, setEditing] = useState<Category | "new" | null>(null);

  const query = useQuery({
    queryKey: ["goods-categories"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/references/goods-categories", {
        params: { query: { page: 1, page_size: 100 } },
      });
      if (error) throw error;
      return data;
    },
  });

  const rows = (query.data?.items ?? []).filter(
    (c) =>
      matches(search, c.code, c.name) &&
      (sharia === "" || (sharia === "allowed" ? c.sharia_allowed : !c.sharia_allowed)),
  );

  return (
    <>
      <Toolbar>
        <SearchInput value={search} onChange={setSearch} placeholder="Код или название" />
        <select className="select" aria-label="Шариат" value={sharia} onChange={(e) => setSharia(e.target.value)}>
          <option value="">Шариат: все</option>
          <option value="allowed">Разрешено</option>
          <option value="forbidden">Запрещено</option>
        </select>
        <span className="toolbar__spacer" />
        <button className="btn btn-primary" onClick={() => setEditing("new")}>
          Добавить категорию
        </button>
      </Toolbar>
      {query.isLoading && <LoadingState />}
      {query.isError && (
        <ErrorState message="Не удалось загрузить категории товаров." onRetry={() => void query.refetch()} />
      )}
      {query.data && rows.length === 0 && (
        <EmptyState
          title={query.data.items.length === 0 ? "Категорий пока нет" : "Ничего не нашлось"}
          action={
            query.data.items.length === 0 ? (
              <button className="btn btn-primary" onClick={() => setEditing("new")}>
                Добавить категорию
              </button>
            ) : undefined
          }
        />
      )}
      {rows.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Название</th>
              <th>Код</th>
              <th>Ликвидность</th>
              <th>Шариат</th>
              <th>Статус</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.id}>
                <td>{c.name}</td>
                <td className="muted">{c.code}</td>
                <td>{liquidityLabel(c.liquidity)}</td>
                <td>{c.sharia_allowed ? "разрешено" : "запрещено"}</td>
                <td>
                  <Badge value={c.active ? "employee_active" : "employee_inactive"} label={c.active ? "активна" : "выключена"} />
                </td>
                <td className="cell-actions">
                  <button className="btn btn-small" onClick={() => setEditing(c)}>
                    Изменить
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {editing && (
        <CategoryModal
          category={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onDone={() => {
            setEditing(null);
            void queryClient.invalidateQueries({ queryKey: ["goods-categories"] });
          }}
        />
      )}
    </>
  );
}

function CategoryModal({
  category,
  onClose,
  onDone,
}: {
  category: Category | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const [form, setForm] = useState(category ?? NEW_CATEGORY);

  const saveMutation = useMutation({
    mutationFn: async () => {
      const body = {
        code: form.code, name: form.name, liquidity: form.liquidity,
        sharia_allowed: form.sharia_allowed, active: form.active,
      };
      const { error } = category
        ? await api.PATCH("/api/references/goods-categories/{category_id}", {
            params: { path: { category_id: category.id } },
            body,
          })
        : await api.POST("/api/references/goods-categories", { body });
      if (error) throw error;
    },
    onSuccess: onDone,
  });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    saveMutation.mutate();
  }

  return (
    <Modal
      title={category ? category.name : "Новая категория товаров"}
      open
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Отмена
          </button>
          <button type="submit" form="category-form" className="btn btn-primary" disabled={saveMutation.isPending}>
            {category ? "Сохранить" : "Добавить"}
          </button>
        </>
      }
    >
      <form id="category-form" onSubmit={handleSubmit} className="form-grid">
        <label className="field field--wide">
          Название
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
        </label>
        <label className="field">
          Код
          <input value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} required />
          <span className="field-hint">Как приходит из системы заказчика.</span>
        </label>
        <label className="field">
          Ликвидность
          <select value={form.liquidity} onChange={(e) => setForm({ ...form, liquidity: e.target.value })}>
            <option value="high">{liquidityLabel("high")}</option>
            <option value="medium">{liquidityLabel("medium")}</option>
            <option value="low">{liquidityLabel("low")}</option>
          </select>
        </label>
        <label className="field field--check field--wide">
          <input
            type="checkbox"
            checked={form.sharia_allowed}
            onChange={(e) => setForm({ ...form, sharia_allowed: e.target.checked })}
          />
          Разрешено по шариату
        </label>
        <label className="field field--check field--wide">
          <input type="checkbox" checked={form.active} onChange={(e) => setForm({ ...form, active: e.target.checked })} />
          Категория активна
        </label>
        {saveMutation.isError && (
          <div className="field--wide">
            <ErrorState message="Не удалось сохранить категорию (код или название уже заняты?)." />
          </div>
        )}
      </form>
    </Modal>
  );
}

function ReasonCodes() {
  const [search, setSearch] = useState("");
  const query = useQuery({
    queryKey: ["reason-codes"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/references/reason-codes", {
        params: { query: { page: 1, page_size: 100 } },
      });
      if (error) throw error;
      return data;
    },
  });
  const rows = (query.data?.items ?? []).filter((r) => matches(search, r.code, r.title));

  return (
    <>
      <Toolbar>
        <SearchInput value={search} onChange={setSearch} placeholder="Код или название" />
        <span className="toolbar__spacer toolbar__count">Только просмотр — коды заполняются миграцией.</span>
      </Toolbar>
      {query.isLoading && <LoadingState />}
      {query.isError && <ErrorState message="Не удалось загрузить причины." onRetry={() => void query.refetch()} />}
      {query.data && rows.length === 0 && <EmptyState title="Ничего не нашлось" />}
      {rows.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Название</th>
              <th>Вид</th>
              <th>Код</th>
              <th>Статус</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.code}>
                <td>{r.title}</td>
                <td>{reasonCodeKindLabel(r.kind)}</td>
                <td className="muted">{r.code}</td>
                <td>
                  <Badge value={r.active ? "employee_active" : "employee_inactive"} label={r.active ? "активен" : "выключен"} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

const NEW_BLACKLIST = {
  last_name: "", first_name: "", middle_name: "", birth_date: "",
  passport_series: "", passport_number: "", phone: "", reason: "",
};

function BlacklistSection() {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);

  const query = useQuery({
    queryKey: ["blacklist"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/references/blacklist", {
        params: { query: { page: 1, page_size: 100 } },
      });
      if (error) throw error;
      return data;
    },
  });
  const rows = (query.data?.items ?? []).filter((b) => matches(search, b.last_name, b.first_name, b.reason));

  return (
    <>
      <Toolbar>
        <SearchInput value={search} onChange={setSearch} placeholder="Фамилия, имя или причина" />
        <span className="toolbar__spacer" />
        <button className="btn" onClick={() => setImporting(true)}>
          Загрузить из файла
        </button>
        <button className="btn btn-primary" onClick={() => setCreating(true)}>
          Добавить в список
        </button>
      </Toolbar>
      {query.isLoading && <LoadingState />}
      {query.isError && (
        <ErrorState message="Не удалось загрузить чёрный список." onRetry={() => void query.refetch()} />
      )}
      {query.data && rows.length === 0 && (
        <EmptyState
          title={query.data.items.length === 0 ? "Чёрный список пуст" : "Ничего не нашлось"}
          hint={
            query.data.items.length === 0
              ? "Совпадение по паспорту или телефону со строкой списка — стоп-фактор стадии А."
              : undefined
          }
          action={
            query.data.items.length === 0 ? (
              <button className="btn btn-primary" onClick={() => setCreating(true)}>
                Добавить в список
              </button>
            ) : undefined
          }
        />
      )}
      {rows.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Фамилия и имя</th>
              <th>Причина</th>
              <th>Статус</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((b) => (
              <tr key={b.id}>
                <td>{[b.last_name, b.first_name].filter(Boolean).join(" ") || "—"}</td>
                <td>{b.reason}</td>
                <td>
                  <Badge value={b.active ? "employee_active" : "employee_inactive"} label={b.active ? "действует" : "выключен"} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <BlacklistImportModal
        open={importing}
        onClose={() => setImporting(false)}
        onImported={() => void queryClient.invalidateQueries({ queryKey: ["blacklist"] })}
      />
      <BlacklistModal
        open={creating}
        onClose={() => setCreating(false)}
        onDone={() => {
          setCreating(false);
          void queryClient.invalidateQueries({ queryKey: ["blacklist"] });
        }}
      />
    </>
  );
}

// T23: загрузка чёрного списка файлом. Формат предварительный (уточняется с
// заказчиком) — app/importers/blacklist.py: CSV, первая строка — названия колонок.
const BLACKLIST_COLUMNS = [
  "last_name", "first_name", "middle_name", "birth_date", "passport_series", "passport_number", "phone", "reason",
];

function downloadBlacklistTemplate() {
  const csv = `${BLACKLIST_COLUMNS.join(";")}\nИванов;Иван;Иванович;01.02.1985;8201;123456;+79990000000;Причина\n`;
  const url = URL.createObjectURL(new Blob(["\ufeff", csv], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = "blacklist-template.csv";
  link.click();
  URL.revokeObjectURL(url);
}

function BlacklistImportModal({
  open,
  onClose,
  onImported,
}: {
  open: boolean;
  onClose: () => void;
  onImported: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ imported: number; skipped: number } | null>(null);

  function close() {
    setFile(null);
    setError(null);
    setResult(null);
    onClose();
  }

  async function upload() {
    if (!file) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const response = await fetch("/api/references/blacklist/import", {
        method: "POST",
        body: form,
        headers: { "X-Requested-With": "XMLHttpRequest" },
      });
      const body = await response.json().catch(() => ({ detail: response.statusText }));
      if (!response.ok) {
        setError(typeof body.detail === "string" ? body.detail : "не удалось загрузить файл");
        return;
      }
      setResult(body as { imported: number; skipped: number });
      onImported();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Загрузить чёрный список из файла"
      open={open}
      onClose={close}
      footer={
        <>
          <button className="btn" onClick={close}>
            {result ? "Готово" : "Отмена"}
          </button>
          {!result && (
            <button className="btn btn-primary" disabled={!file || busy} onClick={() => void upload()}>
              {busy ? "Загружаю…" : "Загрузить"}
            </button>
          )}
        </>
      }
    >
      <p className="muted">
        Файл CSV, первая строка — названия колонок: {BLACKLIST_COLUMNS.join(", ")}. Обязательна только reason
        (причина). Дата рождения — ДД.ММ.ГГГГ или ГГГГ-ММ-ДД. Строки, которые уже есть в списке, пропускаются.
        Если в файле есть ошибка, не загружается ничего.
      </p>
      <p>
        <button type="button" className="btn-link" onClick={downloadBlacklistTemplate}>
          Скачать шаблон
        </button>
      </p>
      <label className="field">
        Файл
        <input
          type="file"
          accept=".csv,text/csv"
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null);
            setError(null);
            setResult(null);
          }}
        />
      </label>
      {error && (
        <p className="manual-check__error" role="alert">
          {error}
        </p>
      )}
      {result && (
        <p role="status">
          Добавлено: <b>{result.imported}</b>
          {result.skipped > 0 && <> · уже были в списке: {result.skipped}</>}
        </p>
      )}
    </Modal>
  );
}

function BlacklistModal({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const [form, setForm] = useState(NEW_BLACKLIST);
  const set = (key: keyof typeof NEW_BLACKLIST) => (e: { target: { value: string } }) =>
    setForm({ ...form, [key]: e.target.value });

  const createMutation = useMutation({
    mutationFn: async () => {
      const orNull = (v: string) => v.trim() || null;
      const { error } = await api.POST("/api/references/blacklist", {
        body: {
          last_name: orNull(form.last_name), first_name: orNull(form.first_name),
          middle_name: orNull(form.middle_name), birth_date: orNull(form.birth_date),
          passport_series: orNull(form.passport_series), passport_number: orNull(form.passport_number),
          phone: orNull(form.phone), reason: form.reason,
        },
      });
      if (error) throw error;
    },
    onSuccess: () => {
      setForm(NEW_BLACKLIST);
      onDone();
    },
  });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    createMutation.mutate();
  }

  return (
    <Modal
      title="Добавить в чёрный список"
      open={open}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Отмена
          </button>
          <button type="submit" form="blacklist-form" className="btn btn-primary" disabled={createMutation.isPending}>
            Добавить
          </button>
        </>
      }
    >
      <form id="blacklist-form" onSubmit={handleSubmit} className="form-grid">
        <label className="field">
          Фамилия
          <input value={form.last_name} onChange={set("last_name")} />
        </label>
        <label className="field">
          Имя
          <input value={form.first_name} onChange={set("first_name")} />
        </label>
        <label className="field">
          Отчество
          <input value={form.middle_name} onChange={set("middle_name")} />
        </label>
        <label className="field">
          Дата рождения
          <input type="date" value={form.birth_date} onChange={set("birth_date")} />
        </label>
        <label className="field">
          Серия паспорта
          <input value={form.passport_series} onChange={set("passport_series")} maxLength={4} inputMode="numeric" />
        </label>
        <label className="field">
          Номер паспорта
          <input value={form.passport_number} onChange={set("passport_number")} maxLength={6} inputMode="numeric" />
        </label>
        <label className="field field--wide">
          Телефон
          <input value={form.phone} onChange={set("phone")} placeholder="+7…" />
          <span className="field-hint">Совпадение ищется по паспорту или телефону — нужно хотя бы одно из двух.</span>
        </label>
        <label className="field field--wide">
          Причина
          <input value={form.reason} onChange={set("reason")} required />
        </label>
        {createMutation.isError && (
          <div className="field--wide">
            <ErrorState message="Не удалось добавить запись." />
          </div>
        )}
      </form>
    </Modal>
  );
}
