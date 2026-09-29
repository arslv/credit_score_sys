interface BarItem {
  label: string;
  value: number;
  displayValue?: string;
}

// Простой CSS-бар-чарт — без внешней библиотеки (нет нужды тянуть тяжёлую
// зависимость ради одной полоски на блок дашборда).
export function BarList({ items, emptyText }: { items: BarItem[]; emptyText: string }) {
  if (items.length === 0) return <p className="muted">{emptyText}</p>;
  const max = Math.max(...items.map((i) => i.value), 1);
  return (
    <div>
      {items.map((item) => (
        <div className="bar-row" key={item.label}>
          <span>{item.label}</span>
          <span className="bar-track">
            <span className="bar-fill" style={{ width: `${(item.value / max) * 100}%` }} />
          </span>
          <span className="muted">{item.displayValue ?? item.value}</span>
        </div>
      ))}
    </div>
  );
}
