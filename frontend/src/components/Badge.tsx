const SUCCESS = new Set(["approve", "approved", "ok", "employee_active"]);
const DANGER = new Set(["reject", "rejected_a", "rejected_b", "source_unavailable", "error", "hit", "employee_inactive"]);
// T20: янтарный — только серая зона; «передано оператору» — обычный ход
// процесса (нейтральная точка), как и в списке заявок.
const WARNING = new Set(["manual_review", "gray_zone", "ocr_failed", "no_data"]);

function variantFor(value: string): string {
  if (SUCCESS.has(value)) return "badge-success";
  if (DANGER.has(value)) return "badge-danger";
  if (WARNING.has(value)) return "badge-warning";
  return "badge-neutral";
}

export function Badge({ value, label }: { value: string | null | undefined; label?: string }) {
  if (!value) return <span className="muted">—</span>;
  return <span className={`badge ${variantFor(value)}`}>{label ?? value}</span>;
}
