"""Поиск в списках заявок и клиентов (решение владельца 16.11.2026): строка
поиска делится на слова, каждое слово должно найтись хотя бы в одном поле
(ФИО, телефон, номер заявки, номер в CRM…) — «иванов 79» найдёт Иванова с
телефоном на 79. Регистр не важен, часть слова — достаточно."""

from sqlalchemy import ColumnElement, and_, false, or_, true

_MAX_TOKENS = 5


def search_tokens(q: str | None) -> list[str]:
    """Слова запроса, без пустых; не больше пяти — длинная строка не
    превращается в тяжёлый запрос."""
    if not q:
        return []
    return [token for token in q.split() if token][:_MAX_TOKENS]


def like_pattern(token: str) -> str:
    """%token% для ILIKE; % и _ из ввода — буквальные символы, не шаблон."""
    escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def all_tokens_match(tokens: list[str], per_token) -> ColumnElement[bool]:
    """И по словам, ИЛИ по полям: per_token(token) возвращает условие «слово
    нашлось хотя бы в одном поле»."""
    if not tokens:
        return true()
    return and_(*(per_token(token) for token in tokens))


def any_of(*conditions) -> ColumnElement[bool]:
    present = [c for c in conditions if c is not None]
    return or_(*present) if present else false()
