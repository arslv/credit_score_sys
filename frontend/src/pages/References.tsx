import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { liquidityLabel, reasonCodeKindLabel } from "../lib/labels";

export function ReferencesPage() {
  return (
    <section>
      <h1>Справочники</h1>
      <GoodsCategories />
      <ReasonCodes />
      <BlacklistSection />
    </section>
  );
}

function GoodsCategories() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ code: "", name: "", liquidity: "high", sharia_allowed: true });

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

  const createMutation = useMutation({
    mutationFn: async () => {
      const { error } = await api.POST("/api/references/goods-categories", { body: { ...form, active: true } });
      if (error) throw error;
    },
    onSuccess: () => {
      setForm({ code: "", name: "", liquidity: "high", sharia_allowed: true });
      void queryClient.invalidateQueries({ queryKey: ["goods-categories"] });
    },
  });

  const toggleActive = useMutation({
    mutationFn: async (input: { id: string; code: string; name: string; liquidity: string; sharia_allowed: boolean; active: boolean }) => {
      const { error } = await api.PATCH("/api/references/goods-categories/{category_id}", {
        params: { path: { category_id: input.id } },
        body: {
          code: input.code, name: input.name, liquidity: input.liquidity,
          sharia_allowed: input.sharia_allowed, active: !input.active,
        },
      });
      if (error) throw error;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["goods-categories"] }),
  });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    createMutation.mutate();
  }

  return (
    <>
      <h2>Категории товаров</h2>
      {query.isLoading && <p className="muted">Загрузка…</p>}
      {query.isError && <p role="alert" className="alert">Не удалось загрузить категории товаров.</p>}
      {query.data && (
        <table className="table">
          <thead>
            <tr>
              <th>Код</th>
              <th>Название</th>
              <th>Ликвидность</th>
              <th>Шариат</th>
              <th>Активна</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {query.data.items.map((c) => (
              <tr key={c.id}>
                <td>{c.code}</td>
                <td>{c.name}</td>
                <td className="muted">{liquidityLabel(c.liquidity)}</td>
                <td>{c.sharia_allowed ? "да" : "нет"}</td>
                <td>
                  <span className={`badge ${c.active ? "badge-success" : "badge-neutral"}`}>
                    {c.active ? "активна" : "выключена"}
                  </span>
                </td>
                <td>
                  <button className="btn btn-small" onClick={() => toggleActive.mutate(c)}>
                    {c.active ? "выключить" : "включить"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <form onSubmit={handleSubmit} className="card">
        <div className="toolbar">
          <label>
            Код
            <input value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} required />
          </label>
          <label>
            Название
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
          </label>
          <label>
            Ликвидность
            <select value={form.liquidity} onChange={(e) => setForm({ ...form, liquidity: e.target.value })}>
              <option value="high">{liquidityLabel("high")}</option>
              <option value="medium">{liquidityLabel("medium")}</option>
              <option value="low">{liquidityLabel("low")}</option>
            </select>
          </label>
          <label>
            <input
              type="checkbox"
              checked={form.sharia_allowed}
              onChange={(e) => setForm({ ...form, sharia_allowed: e.target.checked })}
            />
            шариат разрешён
          </label>
          <button type="submit" className="btn btn-primary btn-small" disabled={createMutation.isPending}>
            Добавить
          </button>
        </div>
        {createMutation.isError && <p role="alert" className="alert">Не удалось добавить категорию.</p>}
      </form>
    </>
  );
}

function ReasonCodes() {
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

  return (
    <>
      <h2>Причины</h2>
      <p className="muted">Только просмотр — коды заполняются миграцией.</p>
      {query.isLoading && <p className="muted">Загрузка…</p>}
      {query.isError && <p role="alert" className="alert">Не удалось загрузить причины.</p>}
      {query.data && (
        <table className="table">
          <thead>
            <tr>
              <th>Код</th>
              <th>Вид</th>
              <th>Название</th>
              <th>Активен</th>
            </tr>
          </thead>
          <tbody>
            {query.data.items.map((r) => (
              <tr key={r.code}>
                <td>{r.code}</td>
                <td className="muted">{reasonCodeKindLabel(r.kind)}</td>
                <td>{r.title}</td>
                <td>{r.active ? "да" : "нет"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

function BlacklistSection() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ last_name: "", first_name: "", reason: "" });

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

  const createMutation = useMutation({
    mutationFn: async () => {
      const { error } = await api.POST("/api/references/blacklist", {
        body: { last_name: form.last_name || null, first_name: form.first_name || null, reason: form.reason },
      });
      if (error) throw error;
    },
    onSuccess: () => {
      setForm({ last_name: "", first_name: "", reason: "" });
      void queryClient.invalidateQueries({ queryKey: ["blacklist"] });
    },
  });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    createMutation.mutate();
  }

  return (
    <>
      <h2>Чёрный список</h2>
      {query.isLoading && <p className="muted">Загрузка…</p>}
      {query.isError && <p role="alert" className="alert">Не удалось загрузить чёрный список.</p>}
      {query.data && (
        <table className="table">
          <thead>
            <tr>
              <th>Фамилия</th>
              <th>Имя</th>
              <th>Причина</th>
              <th>Активна</th>
            </tr>
          </thead>
          <tbody>
            {query.data.items.map((b) => (
              <tr key={b.id}>
                <td>{b.last_name ?? "—"}</td>
                <td>{b.first_name ?? "—"}</td>
                <td>{b.reason}</td>
                <td>{b.active ? "да" : "нет"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <form onSubmit={handleSubmit} className="card">
        <div className="toolbar">
          <label>
            Фамилия
            <input value={form.last_name} onChange={(e) => setForm({ ...form, last_name: e.target.value })} />
          </label>
          <label>
            Имя
            <input value={form.first_name} onChange={(e) => setForm({ ...form, first_name: e.target.value })} />
          </label>
          <label>
            Причина
            <input value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} required />
          </label>
          <button type="submit" className="btn btn-primary btn-small" disabled={createMutation.isPending}>
            Добавить
          </button>
        </div>
        {createMutation.isError && <p role="alert" className="alert">Не удалось добавить запись.</p>}
      </form>
    </>
  );
}
