// T23: поиск по словам для локальной фильтрации (справка, справочники,
// сотрудники, комплаенс). Раньше — подстрока целиком: «стоп ф» не находил
// «Стоп-фактор» (пробел против дефиса), порядок слов имел значение.
//
// Правила:
// - регистр, «ё»/«е», дефисы и знаки препинания не важны;
// - запрос делится на слова, порядок слов не важен, найтись должны ВСЕ;
// - слово запроса совпадает с НАЧАЛОМ слова текста («ф» → «фактор»), а от 3
//   символов — и с частью слова («ктор» → «фактор»).
//
// Нормализация сохраняет длину строки (каждый символ → ровно один), поэтому
// позиции совпадений в нормализованном тексте совпадают с исходным — по ним
// строится подсветка.

const MIN_INFIX = 3;

export function normalizeForSearch(text: string): string {
  return text.toLowerCase().replace(/ё/g, "е").replace(/[^\p{L}\p{N}]/gu, " ");
}

export function searchTokens(query: string): string[] {
  return normalizeForSearch(query).split(" ").filter(Boolean);
}

function isWordStart(text: string, index: number): boolean {
  return index === 0 || text[index - 1] === " ";
}

/** Все позиции, где слово запроса совпадает (по правилам выше). */
function tokenPositions(normalized: string, token: string): number[] {
  const positions: number[] = [];
  let from = 0;
  for (;;) {
    const index = normalized.indexOf(token, from);
    if (index < 0) break;
    if (token.length >= MIN_INFIX || isWordStart(normalized, index)) positions.push(index);
    from = index + 1;
  }
  return positions;
}

export function matches(query: string, ...values: (string | null | undefined)[]): boolean {
  const tokens = searchTokens(query);
  if (tokens.length === 0) return true;
  const haystack = normalizeForSearch(values.filter((v): v is string => Boolean(v)).join(" "));
  return tokens.every((token) => tokenPositions(haystack, token).length > 0);
}

/** Диапазоны [начало, конец) для подсветки в исходном тексте — слитые, без пересечений. */
export function highlightRanges(text: string, query: string): [number, number][] {
  const tokens = searchTokens(query);
  if (tokens.length === 0) return [];
  const normalized = normalizeForSearch(text);
  const ranges = tokens
    .flatMap((token) => tokenPositions(normalized, token).map((start) => [start, start + token.length] as [number, number]))
    .sort((a, b) => a[0] - b[0]);
  const merged: [number, number][] = [];
  for (const range of ranges) {
    const last = merged[merged.length - 1];
    if (last && range[0] <= last[1]) last[1] = Math.max(last[1], range[1]);
    else merged.push([...range]);
  }
  return merged;
}
