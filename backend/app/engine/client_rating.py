"""T23: рейтинг клиента — экспертная скоркарта заказчика («Рейтинг клиента.docx»).

Баллы по критериям (возраст, контакты, работа, прописка) складываются в одну
сумму; отдельно — причины, по которым заказчик запрашивает поручителя
автоматически. ПОКА ИНФОРМАЦИОННО: показывается в карточке заявки, на решение
не влияет — пороги суммы заказчик ещё не назвал. Все баллы, возрасты, списки
мест и категорий — в конфиге правил (client_rating), здесь только сравнение
(правило 2 CLAUDE.md). Чистая функция, без БД и сети.

Критерия нет данных (анкета «Рассмотреть» ещё не пришла, место работы не
указано, адрес не из списка) — пункт с points=None, в сумму не входит, но
виден в разбивке: сумма не выдаёт себя за полную."""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app.engine.schema import ClientRatingConfig


@dataclass
class RatingItem:
    criterion: str  # age | contacts | work | registration
    title: str
    value: str | None  # что именно учтено (возраст, место работы, район) — ПД, отдаётся не всем ролям
    label: str  # по какому правилу
    points: int | None  # None — нет данных, в сумму не входит


@dataclass
class GuarantorReason:
    reason: str
    satisfied: bool  # поручитель в анкете уже есть


@dataclass
class ClientRating:
    total: int
    items: list[RatingItem]
    guarantor_reasons: list[GuarantorReason] = field(default_factory=list)
    guarantor_present: bool = False
    not_yet_counted: list[str] = field(default_factory=list)
    affects_decision: bool = False


def _words(text: str) -> list[str]:
    """Нормализация как у поиска на фронте (lib/search.ts): регистр, ё/е,
    дефисы и знаки — разделители."""
    return re.sub(r"[^0-9a-zа-я]+", " ", text.lower().replace("ё", "е")).split()


def _contains_phrase(words: list[str], phrase: str, *, prefix: bool) -> bool:
    """Фраза из конфига — последовательность слов адреса/места работы.
    prefix=True — каждое слово фразы совпадает с НАЧАЛОМ слова («учител» →
    «учительница»); False — целым словом («кизляр» ≠ «кизлярский»)."""
    target = _words(phrase)
    if not target:
        return False
    for start in range(len(words) - len(target) + 1):
        window = words[start : start + len(target)]
        if all((w.startswith(t) if prefix else w == t) for w, t in zip(window, target)):
            return True
    return False


def _years(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} год"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} года"
    return f"{n} лет"


def _age(birth_date: date, as_of: date) -> int:
    return as_of.year - birth_date.year - ((as_of.month, as_of.day) < (birth_date.month, birth_date.day))


def compute_client_rating(
    config: ClientRatingConfig,
    *,
    birth_date: date | None,
    as_of: date,
    registration_address: str | None,
    stage_b: dict[str, Any] | None,
    financed_amount: Decimal | None,
) -> ClientRating:
    """stage_b — секция stage_b последнего снимка анкеты «Рассмотреть»
    (external_snapshots.payload_raw["stage_b"]) или None, если её ещё не было."""
    stage_b = stage_b or {}
    guarantor_present = bool(stage_b.get("guarantor"))
    items: list[RatingItem] = []
    reasons: list[GuarantorReason] = []
    age = _age(birth_date, as_of) if birth_date else None

    # --- возраст --------------------------------------------------------------
    if age is None:
        items.append(RatingItem("age", "Возраст", None, "дата рождения не распознана", None))
    else:
        band = next((b for b in config.age_bands if b.min <= age <= b.max), None)
        if band is None:
            items.append(RatingItem("age", "Возраст", _years(age), "возраст вне шкалы", None))
        else:
            with_guarantor = guarantor_present and band.points_with_guarantor is not None
            points = band.points_with_guarantor if with_guarantor else band.points
            label = f"{band.min}–{band.max} лет" + (" (есть поручитель)" if with_guarantor else "")
            items.append(RatingItem("age", "Возраст", _years(age), label, points))
            if band.requires_guarantor:
                reasons.append(GuarantorReason(f"Возраст {_years(age)}", guarantor_present))

    # --- контакты -------------------------------------------------------------
    contacts = stage_b.get("contacts")
    if not stage_b:
        items.append(RatingItem("contacts", "Контакты", None, "появятся после «Рассмотреть»", None))
    elif not contacts:
        items.append(RatingItem("contacts", "Контакты", None, "контакты не переданы", None))
    else:
        relations = {str(c.get("relation") or "other") for c in contacts}
        value = ", ".join(
            sorted({config.contact_relations.get(r, r) for r in relations})
        )
        rule = next(
            (
                r for r in config.contact_rules
                if (r.age_min is None or (age is not None and age >= r.age_min))
                and (r.age_max is None or (age is not None and age <= r.age_max))
                and all(x in relations for x in r.has_all)
                and (not r.has_any or any(x in relations for x in r.has_any))
                and not any(x in relations for x in r.lacks)
                and (not r.only or relations <= set(r.only))
            ),
            None,
        )
        if rule is None:
            items.append(RatingItem("contacts", "Контакты", value, "без баллов", 0))
        else:
            items.append(RatingItem("contacts", "Контакты", value, rule.label, rule.points))

    # --- работа ---------------------------------------------------------------
    employment = stage_b.get("employment") or {}
    work_text = " ".join(str(employment.get(k) or "") for k in ("employer", "position")).strip()
    employment_type = employment.get("type")
    if not stage_b:
        items.append(RatingItem("work", "Работа", None, "появится после «Рассмотреть»", None))
    else:
        words = _words(work_text)
        category = next(
            (
                c for c in config.work_categories
                if any(_contains_phrase(words, k, prefix=True) for k in c.keywords)
                or (employment_type and employment_type in c.employment_types)
            ),
            None,
        )
        if category is not None:
            items.append(RatingItem("work", "Работа", work_text or None, category.label, category.points))
        elif work_text:
            items.append(RatingItem("work", "Работа", work_text, "нет в списке категорий", 0))
        else:
            items.append(RatingItem("work", "Работа", None, "место работы не указано", None))

    # --- прописка -------------------------------------------------------------
    if not registration_address:
        items.append(RatingItem("registration", "Прописка", None, "адрес не распознан", None))
    else:
        words = _words(registration_address)
        group = next(
            (g for g in config.registration_groups if any(_contains_phrase(words, p, prefix=False) for p in g.places)),
            None,
        )
        if group is None:
            items.append(RatingItem("registration", "Прописка", registration_address, "нет в списке районов", None))
        else:
            place = next(p for p in group.places if _contains_phrase(words, p, prefix=False))
            items.append(RatingItem("registration", "Прописка", place.capitalize(), group.label, group.points))
            if group.refused:
                reasons.append(GuarantorReason(f"Отказная прописка ({place.capitalize()})", guarantor_present))
            elif group.requires_guarantor:
                reasons.append(GuarantorReason(f"Прописка: {place.capitalize()}", guarantor_present))

    # --- поручитель по товару (телефоны дороже порога у молодых) --------------
    category_code = (stage_b.get("deal") or {}).get("goods_category_code")
    if age is not None and category_code and financed_amount is not None:
        for rule in config.guarantor_product_rules:
            if (
                category_code in rule.goods_category_codes
                and financed_amount >= rule.financed_amount_min
                and age <= rule.age_max
            ):
                reasons.append(GuarantorReason(rule.label, guarantor_present))

    total = sum(i.points for i in items if i.points is not None)
    return ClientRating(
        total=total, items=items, guarantor_reasons=reasons, guarantor_present=guarantor_present,
        not_yet_counted=list(config.not_yet_counted), affects_decision=config.affects_decision,
    )
