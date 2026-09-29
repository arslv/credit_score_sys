const SUCCESS = new Set(["approve", "approved", "ok"]);
const DANGER = new Set(["reject", "rejected_a", "rejected_b", "source_unavailable", "error", "hit"]);
const WARNING = new Set(["manual_review", "gray_zone", "to_operator", "ocr_failed", "no_data"]);

function variantFor(value: string): string {
  if (SUCCESS.has(value)) return "badge-success";
  if (DANGER.has(value)) return "badge-danger";
  if (WARNING.has(value)) return "badge-warning";
  return "badge-neutral";
}

export function Badge({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="muted">—</span>;
  return <span className={`badge ${variantFor(value)}`}>{value}</span>;
}
