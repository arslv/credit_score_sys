// Поиск места в тексте YAML по пути из справочника полей («bki.cache_days»,
// «tariffs.*.markup_pct», «matrix[].when», «checks.<источник>.status»).
// Сначала — по вложенности ключей; если такого ключа в конфиге нет (поля
// заявки вроде features.pti — это не ключи YAML, а значения field:), — по
// строке «field: …», затем по имени последнего ключа. null — в тексте нет.

export interface YamlSpot {
  start: number;
  end: number;
  line: number;
}

const KEY_RE = /^([\w-]+)\s*:/;

function isWildcard(segment: string): boolean {
  return segment === "*" || segment.startsWith("<");
}

export function locateInYaml(text: string, rawPath: string): YamlSpot | null {
  const lines = text.split("\n");
  const offsets: number[] = [];
  let acc = 0;
  for (const line of lines) {
    offsets.push(acc);
    acc += line.length + 1;
  }
  const spot = (line: number): YamlSpot => ({ start: offsets[line]!, end: offsets[line]! + lines[line]!.length, line });

  // «limits.amount_min / amount_max» → первый ключ; «[]» у списков не нужен.
  const path = rawPath.split(" / ")[0]!.trim().replace(/\[\]/g, "");
  const segments = path.split(".").filter(Boolean);

  // 1. По вложенности: каждый следующий ключ — глубже предыдущего и внутри его блока.
  let from = 0;
  let parentIndent = -1;
  let found = -1;
  for (const [depth, segment] of segments.entries()) {
    found = -1;
    for (let i = from; i < lines.length; i++) {
      const trimmed = lines[i]!.trimStart();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const listItem = trimmed.startsWith("- ");
      const indent = lines[i]!.length - trimmed.length + (listItem ? 2 : 0);
      if (indent <= parentIndent) break; // вышли из блока родителя
      if (depth === 0 && indent !== 0) continue; // первый ключ — верхнего уровня
      const key = (listItem ? trimmed.slice(2) : trimmed).match(KEY_RE)?.[1];
      if (key && (isWildcard(segment) || key === segment)) {
        found = i;
        parentIndent = indent;
        from = i + 1;
        break;
      }
    }
    if (found < 0) break;
  }
  if (found >= 0) return spot(found);

  // 2. Поле заявки в правиле: «field: checks.» для «checks.<источник>.status».
  const wildcardAt = segments.findIndex(isWildcard);
  const fieldPrefix = (wildcardAt >= 0 ? segments.slice(0, wildcardAt) : segments).join(".");
  if (fieldPrefix) {
    const index = lines.findIndex((line) => line.includes(`field: ${fieldPrefix}`));
    if (index >= 0) return spot(index);
  }

  // 3. Хотя бы ключ с таким именем где-нибудь (например, «id» — первое правило).
  const last = segments[segments.length - 1];
  if (last && !isWildcard(last)) {
    const keyLine = new RegExp(`^\s*(- )?${last.replace(/[^\w-]/g, "")}\s*:`);
    const index = lines.findIndex((line) => keyLine.test(line));
    if (index >= 0) return spot(index);
  }
  return null;
}
