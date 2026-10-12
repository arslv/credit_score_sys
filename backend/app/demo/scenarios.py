"""План демо-заявок (T23): кто, когда, с каким товаром и какой кредитной
историей. Чистые функции — без БД и сети, детерминированно по seed.

План задаёт ВХОДНЫЕ данные (анкету, паспорт, отчёт БКИ, действия оператора);
решение принимает настоящий конвейер. Вид сценария (kind) — то, на что
данные рассчитаны: тест сида сверяет, что конвейер пришёл именно туда.

Пороги ниже (ПДН 55/60%, балл 450/700/720, сумма от 15 000) — не логика
решения, а цели генератора: числа подобраны с запасом относительно
config/rulesets/dev.yaml и демо-версий правил (runner.RULESET_HISTORY), чтобы
исход не зависел от округления."""

import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from app.demo.people import SANCTIONS_FILLER_NAMES, SANCTIONS_ONLY_NAMES, PeopleFactory, Person
from app.sources.bki import BkiContract, BkiInquiry, BkiReport

DEMO_EXTERNAL_ID_PREFIX = "DEMO-"
DAYS = 60

# --- виды сценариев и сколько их на 150 заявок ------------------------------------

APPROVED = "approved"
APPROVED_ALT_TARIFF = "approved_alt_tariff"  # заявлен «Выгодный» без 20% взноса -> предложен «Базовый»
APPROVED_GUARANTOR = "approved_guarantor"  # серая зона (ФССП поручителя) -> проверили -> повторное «Рассмотреть»
APPROVED_FSSP_LATER = "approved_fssp_later"  # одобрена, потом повторная проверка ФССП нашла долг
GRAY_FSSP = "gray_fssp_not_checked"
GRAY_FSSP_LATER = "gray_fssp_found_after"  # серая зона без ФССП, проверили после решения — долг
GRAY_GUARANTOR = "gray_guarantor_not_checked"
GRAY_PTI = "gray_pti"
GRAY_BKI_OFF = "gray_bki_not_configured"
GRAY_SCORE = "gray_score"
GRAY_SCORE_OVERRIDE = "gray_score_partner_override"  # оператор заказчика сменил статус сам — 409
REJ_A_SANCTIONS = "rejected_a_sanctions"
REJ_A_BLACKLIST = "rejected_a_blacklist"
REJ_A_PASSPORT = "rejected_a_passport_invalid"
REJ_B_PTI = "rejected_b_pti"
REJ_B_DECLARED_PTI = "rejected_b_declared_pti"
REJ_B_SCORE = "rejected_b_score"
REJ_B_LIMITS = "rejected_b_limits"
WAITING = "waiting_review"  # стадия А пройдена, звонок ещё не сделан
OCR_FAILED = "ocr_failed"

KIND_COUNTS: dict[str, int] = {
    APPROVED: 54, APPROVED_ALT_TARIFF: 3, APPROVED_GUARANTOR: 3, APPROVED_FSSP_LATER: 1,
    GRAY_FSSP: 8, GRAY_FSSP_LATER: 2, GRAY_GUARANTOR: 2, GRAY_PTI: 6, GRAY_BKI_OFF: 6, GRAY_SCORE: 12,
    GRAY_SCORE_OVERRIDE: 3,
    REJ_A_SANCTIONS: 4, REJ_A_BLACKLIST: 5, REJ_A_PASSPORT: 5,
    REJ_B_PTI: 7, REJ_B_DECLARED_PTI: 2, REJ_B_SCORE: 12, REJ_B_LIMITS: 5,
    WAITING: 7, OCR_FAILED: 3,
}

EXPECTED_STATUS: dict[str, str] = {
    APPROVED: "approved", APPROVED_ALT_TARIFF: "approved", APPROVED_GUARANTOR: "approved",
    APPROVED_FSSP_LATER: "approved",
    GRAY_FSSP: "gray_zone", GRAY_FSSP_LATER: "gray_zone", GRAY_GUARANTOR: "gray_zone", GRAY_PTI: "gray_zone",
    GRAY_BKI_OFF: "gray_zone", GRAY_SCORE: "gray_zone", GRAY_SCORE_OVERRIDE: "gray_zone",
    REJ_A_SANCTIONS: "rejected_a", REJ_A_BLACKLIST: "rejected_a", REJ_A_PASSPORT: "rejected_a",
    REJ_B_PTI: "rejected_b", REJ_B_DECLARED_PTI: "rejected_b", REJ_B_SCORE: "rejected_b",
    REJ_B_LIMITS: "rejected_b",
    WAITING: "to_operator", OCR_FAILED: "to_operator",
}

_STAGE_A_ONLY = {REJ_A_SANCTIONS, REJ_A_BLACKLIST, REJ_A_PASSPORT, WAITING, OCR_FAILED}
# Действия «через несколько дней после решения» — только у заявок старше этого.
_LATE_ACTION_MIN_AGE_DAYS = 6

# --- товары ------------------------------------------------------------------------


@dataclass(frozen=True)
class Goods:
    category_code: str
    name: str
    price_min: int
    price_max: int


# auto_parts и phones — не из исходного справочника: сид заводит эти категории
# через API справочников (runner.DEMO_GOODS_CATEGORIES), как сделал бы админ.
GOODS = (
    Goods("household_appliances", "Холодильник Atlant ХМ-4624", 38000, 62000),
    Goods("household_appliances", "Стиральная машина LG F2J3HS0W", 32000, 54000),
    Goods("household_appliances", "Посудомоечная машина Bosch SMS4", 42000, 68000),
    Goods("household_appliances", "Кондиционер Haier HSU-12", 36000, 79000),
    Goods("household_appliances", "Пылесос Dyson V12", 45000, 59000),
    Goods("electronics", "Ноутбук Lenovo IdeaPad 5", 62000, 108000),
    Goods("electronics", "Телевизор Samsung 55\" QLED", 54000, 92000),
    Goods("electronics", "Игровая приставка Sony PlayStation 5", 52000, 64000),
    Goods("phones", "Смартфон Samsung Galaxy A55", 31000, 44000),
    Goods("phones", "Смартфон Apple iPhone 15, 128 ГБ", 76000, 94000),
    Goods("phones", "Смартфон Xiaomi Redmi Note 13", 19000, 27000),
    Goods("furniture", "Диван угловой «Мадрид»", 46000, 118000),
    Goods("furniture", "Кухонный гарнитур на заказ", 140000, 340000),
    Goods("furniture", "Спальный гарнитур «Верона»", 160000, 395000),
    Goods("furniture", "Шкаф-купе 2,4 м", 42000, 88000),
    Goods("auto_parts", "Комплект зимних шин R17", 46000, 82000),
    Goods("auto_parts", "Двигатель контрактный ВАЗ 21126", 92000, 148000),
    Goods("auto_parts", "КПП Hyundai Solaris (контрактная)", 72000, 118000),
    Goods("auto_parts", "Тормозные диски и колодки, комплект", 15500, 29000),
)
_CHEAP_GOODS = (
    Goods("auto_parts", "Аккумулятор Varta Blue 74 Ач", 16000, 19000),
    Goods("phones", "Смартфон Xiaomi Redmi Note 13", 17000, 19500),
    Goods("household_appliances", "Микроволновая печь Samsung", 15500, 18500),
)

_TERMS = (3, 6, 9, 10, 12, 12, 12, 18, 18, 24)
_EMPLOYMENT = ("employed",) * 7 + ("self_employed",) * 2 + ("individual_entrepreneur", "pensioner")

# --- план ----------------------------------------------------------------------------


@dataclass
class Deal:
    goods: Goods
    price: Decimal
    down_payment: Decimal
    term_months: int
    tariff: str
    monthly_payment: Decimal  # как его посчитает fake_partner: (цена − взнос) / срок

    @property
    def financed(self) -> Decimal:
        return self.price - self.down_payment


@dataclass
class DemoApplication:
    index: int
    external_id: str
    kind: str
    applicant: Person
    submitted_at: datetime
    ocr_low_confidence: bool = False
    # стадия Б (None — до /review дело не дошло)
    deal: Deal | None = None
    employment_type: str = "employed"
    declared_income: Decimal = Decimal("0")
    declared_obligations: Decimal = Decimal("0")
    guarantor: Person | None = None
    bki_configured: bool = True
    bki_report: BkiReport | None = None
    # действия оператора и заказчика
    fssp_applicant_before_review: bool = False
    fssp_guarantor_then_rereview: bool = False
    fssp_debt_after_decision: bool = False
    partner_override_status: str | None = None
    compliance_verdict: str | None = None  # для совпадений с перечнем
    # время шагов (заполняет _schedule)
    stage_a_at: datetime | None = None
    fssp_at: datetime | None = None
    review_at: datetime | None = None
    guarantor_fssp_at: datetime | None = None
    rereview_at: datetime | None = None
    late_fssp_at: datetime | None = None
    compliance_at: datetime | None = None
    contract_issue_date: date | None = None  # для одобренных — дата договора (portfolio.py)
    notes: list[str] = field(default_factory=list)

    @property
    def expected_status(self) -> str:
        return EXPECTED_STATUS[self.kind]


@dataclass
class DemoPlan:
    now: datetime
    applications: list[DemoApplication]
    sanctions_rows: list[dict]  # строки CSV перечня (registries/parsers.py)
    invalid_passports: list[tuple[str, str]]
    blacklist_rows: list[dict]  # тела POST /api/references/blacklist


def _money(value: float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _round_to(value: float, step: int) -> Decimal:
    return Decimal(int(round(value / step) * step))


def _clipped_normal(rng: random.Random, mean: float, sd: float, lo: float, hi: float) -> int:
    for _ in range(100):
        value = rng.gauss(mean, sd)
        if lo <= value <= hi:
            return int(round(value))
    return int(round(min(max(mean, lo), hi)))


def _score_for(kind: str, rng: random.Random, financed: Decimal) -> int:
    if kind in (REJ_B_SCORE,):
        return _clipped_normal(rng, 405, 30, 320, 444)
    if kind in (GRAY_SCORE, GRAY_SCORE_OVERRIDE):
        return _clipped_normal(rng, 610, 55, 458, 694)
    if kind in (GRAY_PTI, REJ_B_PTI, GRAY_BKI_OFF, REJ_B_DECLARED_PTI, REJ_B_LIMITS):
        return _clipped_normal(rng, 690, 70, 470, 830)
    # одобрение (и всё, что одобрилось бы, не будь ФССП/поручителя): выше 720 —
    # самого строгого порога одобрения среди демо-версий правил; при сумме
    # выше 200 000 матрица поднимает порог до 780.
    floor = 792 if financed > 200000 else 726
    return _clipped_normal(rng, max(floor + 30, 765), 32, floor, 845)


def _pick_deal(rng: random.Random, kind: str) -> Deal:
    if kind == REJ_B_LIMITS:
        goods = rng.choice(_CHEAP_GOODS)
        price = _round_to(rng.uniform(goods.price_min, goods.price_max), 100)
        # Сумма рассрочки ниже минимальной (15 000): большой первый взнос.
        down = price - _round_to(rng.uniform(9500, 14500), 100)
        term = rng.choice((3, 6))
        tariff = "standard"
    else:
        goods = rng.choice(GOODS)
        price = _round_to(rng.uniform(goods.price_min, goods.price_max), 500)
        down_share = rng.choice((0, 0, 0, 0.1, 0.1, 0.15, 0.2, 0.25, 0.3))
        down = Decimal(math.ceil(float(price) * down_share / 500) * 500)
        if price - down < 15000:
            down, down_share = Decimal(0), 0
        # Платёж не больше ~45 000 в месяц — иначе при доходе до 200 000 не
        # получить нужный ПДН: дорогая мебель — на длинный срок.
        min_term = math.ceil(float(price - down) / 45000)
        term = rng.choice([t for t in _TERMS if t >= min_term] or [24])
        tariff = "advantageous" if down_share >= 0.2 else "standard"
        if kind == APPROVED_ALT_TARIFF:
            # «Выгодный» требует взнос от 20% — заявлен с 10%, лимиты предложат «Базовый».
            tariff = "advantageous"
            down = _round_to(float(price) * 0.1, 500)
    monthly = ((price - down) / Decimal(term)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return Deal(goods=goods, price=price, down_payment=down, term_months=term, tariff=tariff,
                monthly_payment=monthly)


def _pti_target(kind: str, rng: random.Random) -> tuple[float, float]:
    """Диапазон целевого ПДН по КИ (активные кредиты + платёж по рассрочке)."""
    if kind == GRAY_PTI:
        return 0.565, 0.595
    if kind == REJ_B_PTI:
        return 0.64, 0.88
    return 0.12, 0.5


def _income_and_obligations(
    rng: random.Random, kind: str, monthly: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    """(заявленный доход, заявленные платежи по кредитам, платежи по активным кредитам в КИ)."""
    pmt = float(monthly)
    lo, hi = _pti_target(kind, rng)
    if kind == REJ_B_DECLARED_PTI:
        income = _round_to(rng.uniform(max(30000, pmt / 0.5), max(42000, pmt / 0.35)), 1000)
        declared = _round_to(float(income) * rng.uniform(0.68, 0.85) - pmt, 500)
        return income, max(declared, Decimal(0)), declared
    # Доход 30–200 тыс., но не меньше, чем нужно, чтобы платёж по рассрочке
    # сам по себе оставлял место под целевой ПДН.
    # Доля платежа по рассрочке в доходе — с запасом ниже цели, чтобы
    # оставалось место под кредиты из КИ; для отказа по ПДН из КИ — не выше
    # 35%, иначе уже ЗАЯВЛЕННЫЙ ПДН перешёл бы 60% и отказ был бы до БКИ.
    max_share = {GRAY_PTI: 0.5, REJ_B_PTI: 0.35}.get(kind, 0.42)
    min_income = max(30000.0, pmt / max_share)
    income_value = min(200000.0, rng.uniform(min_income, max(min_income * 1.8, 60000)))
    income = _round_to(income_value, 1000)
    target = rng.uniform(lo, hi)
    active = max(0.0, target * float(income) - pmt)
    if kind == REJ_B_PTI:
        # Заявил меньше, чем в КИ: заявленный ПДН проходит, фактический — нет.
        declared = _round_to(active * rng.uniform(0.1, 0.35), 500)
    elif kind == GRAY_PTI:
        declared = _round_to(active * rng.uniform(0.7, 0.92), 500)
    else:
        declared = _round_to(active * rng.uniform(0.8, 1.05), 500)
    return income, declared, _round_to(active, 100)


def _bki_report(
    rng: random.Random, kind: str, *, active_total: Decimal, score: int, as_of: date,
) -> BkiReport:
    contracts: list[BkiContract] = []
    remaining = float(active_total)
    n_active = 0 if remaining < 1000 else rng.choice((1, 1, 2, 2, 3))
    subjects = ("Потребительский кредит", "Кредитная карта", "Кредит наличными", "Автокредит", "Микрозайм")
    for i in range(n_active):
        share = remaining if i == n_active - 1 else remaining * rng.uniform(0.3, 0.6)
        remaining -= share
        opened = as_of - timedelta(days=rng.randint(90, 1400))
        overdue = rng.choice((0, 0, 0, 3, 7)) if kind != REJ_B_SCORE else rng.choice((12, 35, 64, 95))
        contracts.append(BkiContract(
            opened_at=opened, closed_at=None, status="active",
            monthly_payment=str(_money(share)), balance=str(_money(share * rng.uniform(4, 18))),
            overdue_days_max=overdue, subject=rng.choice(subjects),
        ))
    for _ in range(rng.choice((0, 1, 1, 2, 3))):
        opened = as_of - timedelta(days=rng.randint(700, 3000))
        closed = opened + timedelta(days=rng.randint(180, 720))
        overdue = rng.choice((0, 0, 0, 0, 5)) if kind != REJ_B_SCORE else rng.choice((0, 30, 91))
        contracts.append(BkiContract(
            opened_at=opened, closed_at=min(closed, as_of - timedelta(days=30)), status="closed",
            monthly_payment=str(_money(rng.uniform(2500, 15000))), balance="0",
            overdue_days_max=overdue, subject=rng.choice(subjects),
        ))
    inquiries = [
        BkiInquiry(requested_at=as_of - timedelta(days=rng.randint(5, 360)),
                   requester=rng.choice(("Банк А", "Банк Б", "МФО В", "Банк Г")))
        for _ in range(rng.choice((0, 1, 1, 2, 3, 5)))
    ]
    return BkiReport(contracts=contracts, inquiries=inquiries, score=score)


def _slot_times(rng: random.Random, now: datetime, n: int) -> list[datetime]:
    """n моментов подачи равномерно по последним 60 дням: 2–3 заявки в день,
    рабочее время 09:00–21:00 МСК (06:00–18:00 UTC), строго раньше now."""
    today = now.date()
    latest = now - timedelta(hours=3)
    moments = []
    for k in range(n):
        day = today - timedelta(days=DAYS - 1 - (k * DAYS) // n)
        start = datetime.combine(day, time(6, 0), tzinfo=timezone.utc)
        moment = start + timedelta(minutes=rng.randint(0, 12 * 60 - 1))
        if moment > latest:
            moment = latest - timedelta(minutes=rng.randint(10, 90))
        moments.append(moment)
    return sorted(moments)


def _place_kinds(rng: random.Random, slots: list[datetime], now: datetime) -> list[str]:
    """Вид на каждый слот. Ограничения по времени:
    - WAITING — последние 4 дня (звонка ещё не было), OCR_FAILED — последние 7;
    - REJ_B_PTI — после включения отказа по ПДН из КИ (последние 13 дней,
      демо-версия правил v4, см. runner.RULESET_HISTORY), раньше это была бы
      серая зона;
    - действия «через пару дней после решения» — у заявок старше 6 дней."""
    kinds = [k for k, c in KIND_COUNTS.items() for _ in range(c)]
    if len(kinds) != len(slots):
        raise ValueError(f"план на {len(kinds)} заявок, слотов {len(slots)}")
    age_days = [(now - s).total_seconds() / 86400 for s in slots]
    assigned: list[str | None] = [None] * len(slots)

    def take(kind: str, eligible) -> None:
        candidates = [i for i in range(len(slots)) if assigned[i] is None and eligible(age_days[i])]
        count = KIND_COUNTS[kind]
        if len(candidates) < count:
            raise ValueError(f"не хватает слотов для {kind}")
        for i in rng.sample(candidates, count):
            assigned[i] = kind

    take(WAITING, lambda age: age < 4)
    take(OCR_FAILED, lambda age: age < 7)
    take(REJ_B_PTI, lambda age: 0.5 < age < 13)
    for late_kind in (APPROVED_FSSP_LATER, GRAY_FSSP_LATER, APPROVED_GUARANTOR):
        take(late_kind, lambda age: age > _LATE_ACTION_MIN_AGE_DAYS)

    rest = [k for k in kinds if k not in (WAITING, OCR_FAILED, REJ_B_PTI, APPROVED_FSSP_LATER,
                                          GRAY_FSSP_LATER, APPROVED_GUARANTOR)]
    rng.shuffle(rest)
    # Свежие слоты (младше полусуток) — только то, что решается в тот же день.
    free = [i for i in range(len(slots)) if assigned[i] is None]
    for i, kind in zip(free, rest):
        assigned[i] = kind
    return [k for k in assigned if k is not None]


def build_plan(*, now: datetime, seed: int = 2026) -> DemoPlan:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    rng = random.Random(seed)
    people = PeopleFactory(rng, today=now.date())

    slots = _slot_times(rng, now, sum(KIND_COUNTS.values()))
    kinds = _place_kinds(rng, slots, now)

    applications: list[DemoApplication] = []
    sanctions_rows: list[dict] = []
    invalid_passports: list[tuple[str, str]] = []
    blacklist_rows: list[dict] = []
    sanction_names = list(SANCTIONS_ONLY_NAMES)

    for index, (submitted_at, kind) in enumerate(zip(slots, kinds), start=1):
        as_of = submitted_at.date()
        if kind == REJ_A_SANCTIONS:
            last, first, middle, gender = sanction_names.pop()
            applicant = people.make(as_of=as_of, name=(last, first, middle, gender))
        else:
            applicant = people.make(as_of=as_of)

        app = DemoApplication(
            index=index, external_id=f"{DEMO_EXTERNAL_ID_PREFIX}{index:04d}", kind=kind,
            applicant=applicant, submitted_at=submitted_at,
        )

        if kind == REJ_A_SANCTIONS:
            # Одна запись перечня без даты рождения — совпадение только по ФИО,
            # комплаенс признаёт её ложной (однофамилец); остальные — с датой.
            no_birth_date = not any(r["birth_date"] == "" for r in sanctions_rows)
            sanctions_rows.append({
                "list_type": "te" if not no_birth_date else "mvk", "full_name": applicant.full_name,
                "birth_date": "" if no_birth_date else applicant.birth_date.isoformat(),
            })
            app.compliance_verdict = "false_positive" if no_birth_date else (
                "confirmed" if len(sanctions_rows) == 2 else None
            )
        elif kind == REJ_A_BLACKLIST:
            by_phone = len(blacklist_rows) % 2 == 1
            blacklist_rows.append({
                "last_name": applicant.last_name, "first_name": applicant.first_name,
                "middle_name": applicant.middle_name, "birth_date": applicant.birth_date.isoformat(),
                **({"phone": applicant.phone} if by_phone else {
                    "passport_series": applicant.passport_series, "passport_number": applicant.passport_number,
                }),
                "reason": rng.choice((
                    "Мошенничество с документами при прошлой покупке",
                    "Неоднократный невозврат товара по прошлой рассрочке",
                    "Предоставил чужой паспорт (решение службы безопасности)",
                )),
            })
        elif kind == REJ_A_PASSPORT:
            invalid_passports.append((applicant.passport_series, applicant.passport_number))
        elif kind == OCR_FAILED:
            app.ocr_low_confidence = True

        if kind not in _STAGE_A_ONLY:
            _fill_stage_b(app, rng, people)

        _schedule(app, rng, now)
        applications.append(app)

    # Наполнитель перечня и реестра недействительных паспортов — чтобы это
    # были похожие на настоящие реестры, а не список из четырёх «наших» строк.
    for i, name in enumerate(SANCTIONS_FILLER_NAMES):
        sanctions_rows.append({
            "list_type": ("te", "fromu", "mvk", "un")[i % 4], "full_name": name,
            "birth_date": date(1952 + i % 20, 1 + i % 12, 1 + i % 27).isoformat(),
        })
    filler_rng = random.Random(seed + 1)
    used = {(a.applicant.passport_series, a.applicant.passport_number) for a in applications}
    while len(invalid_passports) < 400:
        series = f"{filler_rng.choice(('05', '77', '78', '26', '07', '20', '61'))}{filler_rng.randint(0, 24):02d}"
        number = f"{filler_rng.randint(100000, 999999)}"
        if (series, number) not in used:
            used.add((series, number))
            invalid_passports.append((series, number))

    return DemoPlan(
        now=now, applications=applications, sanctions_rows=sanctions_rows,
        invalid_passports=invalid_passports, blacklist_rows=blacklist_rows,
    )


def _fill_stage_b(app: DemoApplication, rng: random.Random, people: PeopleFactory) -> None:
    kind = app.kind
    deal = _pick_deal(rng, kind)
    app.deal = deal
    app.employment_type = rng.choice(_EMPLOYMENT)
    income, declared, active_total = _income_and_obligations(rng, kind, deal.monthly_payment)
    app.declared_income = income
    app.declared_obligations = declared
    score = _score_for(kind, rng, deal.financed)
    app.bki_report = _bki_report(rng, kind, active_total=active_total, score=score, as_of=app.submitted_at.date())
    app.bki_configured = kind != GRAY_BKI_OFF

    # ФССП по заявителю до «Рассмотреть» — у всех, кроме тех, где серая зона
    # именно из-за непроверенного ФССП, и части прочих (оператор не всегда
    # успевает до звонка — на одобрение это не влияет, только у тех, кто
    # одобрился бы).
    would_approve = kind in (APPROVED, APPROVED_ALT_TARIFF, APPROVED_GUARANTOR, APPROVED_FSSP_LATER,
                             GRAY_GUARANTOR)
    if would_approve:
        app.fssp_applicant_before_review = True
    elif kind in (GRAY_FSSP, GRAY_FSSP_LATER):
        app.fssp_applicant_before_review = False
    else:
        app.fssp_applicant_before_review = rng.random() < 0.6

    if kind in (APPROVED_GUARANTOR, GRAY_GUARANTOR):
        app.guarantor = people.make(as_of=app.submitted_at.date(), age_range=(28, 60))
        app.fssp_guarantor_then_rereview = kind == APPROVED_GUARANTOR
    if kind in (APPROVED_FSSP_LATER, GRAY_FSSP_LATER):
        app.fssp_debt_after_decision = True
    if kind == GRAY_SCORE_OVERRIDE:
        app.partner_override_status = rng.choice(("approved", "rejected", "approved"))


def _schedule(app: DemoApplication, rng: random.Random, now: datetime) -> None:
    latest = now - timedelta(minutes=30)

    def clamp(moment: datetime) -> datetime:
        return min(moment, latest)

    t = app.submitted_at
    app.stage_a_at = t + timedelta(seconds=rng.randint(15, 80))
    if app.kind == REJ_A_SANCTIONS and app.compliance_verdict:
        app.compliance_at = clamp(t + timedelta(hours=rng.randint(20, 50)))
    if app.deal is None:
        return
    if app.fssp_applicant_before_review:
        app.fssp_at = clamp(app.stage_a_at + timedelta(minutes=rng.randint(15, 110)))
    review_base = app.fssp_at or app.stage_a_at
    app.review_at = clamp(review_base + timedelta(minutes=rng.randint(30, 240)))
    if app.fssp_at and app.review_at <= app.fssp_at:
        app.review_at = app.fssp_at + timedelta(minutes=5)
    if app.fssp_guarantor_then_rereview:
        app.guarantor_fssp_at = app.review_at + timedelta(minutes=rng.randint(40, 150))
        app.rereview_at = app.guarantor_fssp_at + timedelta(minutes=rng.randint(10, 45))
    if app.fssp_debt_after_decision:
        app.late_fssp_at = (app.rereview_at or app.review_at) + timedelta(hours=rng.randint(26, 80))
    if app.expected_status == "approved":
        decided = app.rereview_at or app.review_at
        app.contract_issue_date = (decided + timedelta(days=rng.choice((0, 0, 1, 1, 2, 3)))).date()

    for moment in (app.review_at, app.guarantor_fssp_at, app.rereview_at, app.late_fssp_at):
        if moment is not None and moment >= latest + timedelta(minutes=1):
            raise ValueError(f"{app.external_id}: шаг позже текущего момента ({moment.isoformat()})")


def demo_summary(plan: DemoPlan) -> dict[str, int]:
    """Сколько заявок какого итогового статуса ожидается — для отчёта сида."""
    summary: dict[str, int] = {}
    for app in plan.applications:
        summary[app.expected_status] = summary.get(app.expected_status, 0) + 1
    return summary
