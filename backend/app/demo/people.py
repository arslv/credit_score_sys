"""Вымышленные заявители и поручители демо-сида (T23).

ФИО собираются случайно из частых дагестанских и русских имён/фамилий —
это не реальные люди: комбинация, дата рождения, паспорт, телефон и адрес
придуманы и независимы друг от друга. Телефоны — в диапазоне +7 999 000-xx-xx,
адреса — на вымышленных улицах («ул. Демонстрационная» и т.п.).

Паспорта проходят штатные проверки стадии А (формат, срок по возрасту 20/45,
год бланка, код подразделения из справочника и его регион = первые две цифры
серии — app/engine/passport.py), чтобы отказ/одобрение определялись
сценарием, а не случайной ошибкой в данных."""

import random
from dataclasses import dataclass
from datetime import date, timedelta

# --- имена -----------------------------------------------------------------------

_DAG_SURNAMES = (
    "Магомедов", "Абдуллаев", "Гаджиев", "Алиев", "Рамазанов", "Исмаилов", "Курбанов", "Омаров",
    "Гасанов", "Халилов", "Муртазалиев", "Шахбанов", "Ибрагимов", "Нурмагомедов", "Сулейманов",
    "Абакаров", "Мусаев", "Джамалов", "Хизриев", "Темирханов",
)
_DAG_MALE = ("Магомед", "Шамиль", "Арсен", "Руслан", "Ахмед", "Рашид", "Камиль", "Мурад", "Тимур", "Ислам",
             "Гасан", "Расул", "Заур", "Эльдар")
_DAG_FEMALE = ("Патимат", "Айшат", "Мадина", "Зарема", "Хадижат", "Зайнаб", "Аминат", "Сабина", "Марьям",
               "Джамиля")
_DAG_FATHERS = ("Магомед", "Ахмед", "Гаджи", "Расул", "Шамиль", "Абдулла", "Омар", "Курбан", "Алибек",
                "Исмаил", "Гусейн", "Рамазан")

_RU_SURNAMES = (
    "Смирнов", "Кузнецов", "Попов", "Васильев", "Петров", "Соколов", "Михайлов", "Новиков", "Фёдоров",
    "Морозов", "Волков", "Лебедев", "Козлов", "Степанов", "Никитин", "Орлов",
)
_RU_MALE = ("Алексей", "Дмитрий", "Сергей", "Андрей", "Иван", "Николай", "Павел", "Михаил", "Евгений",
            "Константин")
_RU_FEMALE = ("Анна", "Елена", "Ольга", "Наталья", "Татьяна", "Мария", "Ирина", "Светлана", "Юлия",
              "Екатерина")
_RU_FATHERS = ("Александр", "Владимир", "Сергей", "Николай", "Виктор", "Юрий", "Андрей", "Игорь", "Олег",
               "Борис")

# Только для совпадений с перечнем Росфинмониторинга: имена, которых нет ни у
# одного другого демо-заявителя, — поиск по перечню нечёткий (pg_trgm, порог
# 0.7), общие имя+отчество дали бы ложные совпадения у посторонних заявок.
SANCTIONS_ONLY_NAMES = (
    ("Зухайраев", "Абакар", "Гусейнович", "male"),
    ("Тагирбеков", "Вадим", "Ринатович", "male"),
    ("Ферзалиев", "Ринат", "Эдуардович", "male"),
    ("Курамагомедова", "Луиза", "Хасбулатовна", "female"),
)
# Строки-«наполнитель» перечня — тоже вне общих пулов имён.
SANCTIONS_FILLER_NAMES = (
    "Абдулвагабов Юнус Кадиевич", "Бекмурзаев Тамерлан Ильясович", "Вагапов Ильдар Ренатович",
    "Галиаскаров Наиль Фаридович", "Дербишев Анзор Хамзатович", "Ержанов Бауржан Серикович",
    "Жамбулатов Аслан Тимурович", "Зайнуллин Марсель Рустемович", "Исрапилов Хусейн Абуевич",
    "Каримуллин Ринат Ильгизович", "Латыпов Айдар Фанисович", "Мамедкеримов Эльчин Ровшанович",
    "Насибуллин Рамиль Габдулович", "Оздоев Магомед-Башир Иссаевич", "Пашаев Тофик Ариф оглы",
    "Рахимзянов Ильнур Маратович", "Сапаров Дауд Мерденович", "Тлисов Казбек Муратович",
)


def _patronymic(father: str, gender: str) -> str:
    irregular = {
        "Гаджи": ("Гаджиевич", "Гаджиевна"), "Абдулла": ("Абдуллаевич", "Абдуллаевна"),
        "Никола": ("Николаевич", "Николаевна"), "Николай": ("Николаевич", "Николаевна"),
        "Сергей": ("Сергеевич", "Сергеевна"), "Андрей": ("Андреевич", "Андреевна"),
        "Юрий": ("Юрьевич", "Юрьевна"), "Игорь": ("Игоревич", "Игоревна"),
        "Алибек": ("Алибекович", "Алибековна"),
    }
    if father in irregular:
        male, female = irregular[father]
    else:
        male, female = f"{father}ович", f"{father}овна"
    return male if gender == "male" else female


def _feminine_surname(surname: str) -> str:
    if surname.endswith(("ов", "ев", "ёв", "ин")):
        return surname + "а"
    return surname


# --- регионы и паспорта ------------------------------------------------------------


@dataclass(frozen=True)
class Region:
    code: str  # две цифры: регион серии паспорта = первые цифры кода подразделения
    fssp_region: str  # код региона в config/fssp_regions.yaml
    department_codes: tuple[str, ...]  # реально есть в config/reference/fms_unit.csv
    issued_by: str
    city: str
    streets: tuple[str, ...]


REGIONS = {
    "dagestan": Region(
        "05", "05", ("050-001", "050-002", "050-003", "050-004", "050-005"),
        "МВД по Республике Дагестан", "Республика Дагестан, г. Махачкала",
        ("ул. Демонстрационная", "ул. Учебная", "пр-т Образцовый", "ул. Тренировочная"),
    ),
    "moscow": Region(
        "77", "77", ("770-001", "770-002", "770-003", "770-004"),
        "ГУ МВД России по г. Москве", "г. Москва",
        ("ул. Демонстрационная", "ул. Макетная", "Примерный пер."),
    ),
    "spb": Region(
        "78", "78", ("780-001", "780-002", "780-003"),
        "ГУ МВД России по г. Санкт-Петербургу", "г. Санкт-Петербург",
        ("ул. Учебная", "наб. Образцовая"),
    ),
    "stavropol": Region(
        "26", "26", ("260-001", "260-002", "260-003"),
        "ГУ МВД России по Ставропольскому краю", "Ставропольский край, г. Ставрополь",
        ("ул. Тренировочная", "ул. Макетная"),
    ),
}


@dataclass(frozen=True)
class Person:
    last_name: str
    first_name: str
    middle_name: str
    gender: str
    birth_date: date
    phone: str
    passport_series: str
    passport_number: str
    issue_date: date
    issued_by: str
    department_code: str
    registration_address: str
    birth_place: str
    fssp_region: str

    @property
    def full_name(self) -> str:
        return f"{self.last_name} {self.first_name} {self.middle_name}"

    def ocr_fields(self) -> dict[str, str]:
        """Формат overrides для build_ocr_result (app/ocr/fake_provider.py)."""
        return {
            "last_name": self.last_name, "first_name": self.first_name, "middle_name": self.middle_name,
            "birth_date": self.birth_date.isoformat(), "birth_place": self.birth_place,
            "gender": self.gender, "passport_series": self.passport_series,
            "passport_number": self.passport_number, "issue_date": self.issue_date.isoformat(),
            "issued_by": self.issued_by, "department_code": self.department_code,
            "registration_address": self.registration_address,
        }

    def guarantor_payload(self) -> dict[str, str]:
        """Формат guarantor в теле /review (app/api/partner_webhooks.py::GuarantorIn)."""
        return {
            "last_name": self.last_name, "first_name": self.first_name, "middle_name": self.middle_name,
            "birth_date": self.birth_date.isoformat(), "passport_series": self.passport_series,
            "passport_number": self.passport_number, "issue_date": self.issue_date.isoformat(),
            "registration_address": self.registration_address, "phone": self.phone,
        }


def _add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 февраля
        return d.replace(year=d.year + years, day=28)


class PeopleFactory:
    """Уникальные ФИО, паспорта и телефоны на весь прогон."""

    def __init__(self, rng: random.Random, *, today: date) -> None:
        self._rng = rng
        self._today = today
        self._names: set[tuple[str, str, str]] = set()
        self._passports: set[tuple[str, str]] = set()
        self._phone_counter = 0

    def _unique_name(self, gender: str) -> tuple[str, str, str]:
        rng = self._rng
        for _ in range(1000):
            if rng.random() < 0.7:
                surname = rng.choice(_DAG_SURNAMES)
                first = rng.choice(_DAG_MALE if gender == "male" else _DAG_FEMALE)
                father = rng.choice(_DAG_FATHERS)
            else:
                surname = rng.choice(_RU_SURNAMES)
                first = rng.choice(_RU_MALE if gender == "male" else _RU_FEMALE)
                father = rng.choice(_RU_FATHERS)
            if gender == "female":
                surname = _feminine_surname(surname)
            name = (surname, first, _patronymic(father, gender))
            if name not in self._names:
                self._names.add(name)
                return name
        raise RuntimeError("не удалось подобрать уникальное ФИО")

    def _phone(self) -> str:
        self._phone_counter += 1
        n = self._phone_counter
        return f"+7999000{n // 100:02d}{n % 100:02d}"

    def make(
        self, *, as_of: date, region_key: str | None = None, name: tuple[str, str, str, str] | None = None,
        age_range: tuple[int, int] = (22, 62),
    ) -> Person:
        rng = self._rng
        if name is not None:
            last_name, first_name, middle_name, gender = name
            self._names.add((last_name, first_name, middle_name))
        else:
            gender = "male" if rng.random() < 0.62 else "female"
            last_name, first_name, middle_name = self._unique_name(gender)

        if region_key is None:
            region_key = rng.choices(("dagestan", "moscow", "spb", "stavropol"), weights=(70, 14, 5, 11))[0]
        region = REGIONS[region_key]

        age = rng.randint(*age_range)
        birth_date = as_of - timedelta(days=age * 365 + rng.randint(30, 330))
        # Паспорт выдан при последней плановой замене (20 или 45 лет), через
        # 3 недели – 4 месяца после дня рождения — срок действия не истёк на
        # as_of. Порог считается по точному возрасту; если замена была совсем
        # недавно (меньше месяца назад), человек «старше» на полгода — иначе
        # дата выдачи не помещается между заменой и as_of.
        while True:
            exact_age = as_of.year - birth_date.year - ((as_of.month, as_of.day) < (birth_date.month, birth_date.day))
            threshold_birthday = _add_years(birth_date, 45 if exact_age >= 45 else 20)
            if (as_of - threshold_birthday).days >= 30:
                break
            birth_date -= timedelta(days=180)
        latest_offset = min(120, (as_of - threshold_birthday).days - 1)
        issue_date = threshold_birthday + timedelta(days=rng.randint(20, latest_offset))
        blank_year = max(issue_date.year - rng.randint(0, 2), 1997)

        while True:
            series = f"{region.code}{blank_year % 100:02d}"
            number = f"{rng.randint(100000, 999999)}"
            if (series, number) not in self._passports:
                self._passports.add((series, number))
                break

        house, flat = rng.randint(1, 120), rng.randint(1, 240)
        return Person(
            last_name=last_name, first_name=first_name, middle_name=middle_name, gender=gender,
            birth_date=birth_date, phone=self._phone(), passport_series=series, passport_number=number,
            issue_date=issue_date, issued_by=region.issued_by,
            department_code=rng.choice(region.department_codes),
            registration_address=f"{region.city}, {rng.choice(region.streets)}, д. {house}, кв. {flat}",
            birth_place=region.city.split(", ")[-1], fssp_region=region.fssp_region,
        )

    def reserve_passport(self, series: str, number: str) -> None:
        self._passports.add((series, number))
