// Плоское представление вложенного JSON анкеты/снимка для табличного вывода
// и посписочного сравнения соседних снимков стадии Б (подсветка правок
// оператора между запусками — ТЗ, «Анкета»).
export function flatten(value: unknown, prefix = ""): Record<string, string> {
  const result: Record<string, string> = {};
  if (value === null || value === undefined) return result;

  if (typeof value !== "object") {
    result[prefix || "value"] = String(value);
    return result;
  }

  if (Array.isArray(value)) {
    result[prefix || "value"] = JSON.stringify(value);
    return result;
  }

  for (const [key, nested] of Object.entries(value as Record<string, unknown>)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (nested !== null && typeof nested === "object" && !Array.isArray(nested)) {
      Object.assign(result, flatten(nested, path));
    } else if (nested === null || nested === undefined) {
      result[path] = "—";
    } else if (Array.isArray(nested)) {
      result[path] = JSON.stringify(nested);
    } else {
      result[path] = String(nested);
    }
  }
  return result;
}
