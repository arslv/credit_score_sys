# Источник — fms_unit.csv

Справочник «код подразделения -> название» для проверки `passport_department_code_flag`
(T08, `app.engine.department_codes`, `app.engine.passport.check_department_code_region`).

| | |
| --- | --- |
| Составитель | DaData.ru — https://dadata.ru/suggestions/outward/fms_unit/ |
| Репозиторий | https://github.com/hflabs/fms-unit |
| Коммит | `7d2d9a2c196147c45e9c87975e212ade8a342f82` |
| Дата данных | 2026-09-22 (`fms_unit.csv-metadata.json`, поле `dc:issued`) |
| Дата загрузки в этот репозиторий | 2026-09-24 |
| Лицензия | Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0) — https://creativecommons.org/licenses/by-sa/4.0/ |
| Формат | CSV: `code` (код подразделения, `RRR-NNN`, где `RRR` — регион), `name` (название в творительном падеже) |
| Объём | 16584 строки, 7509 уникальных кодов, 92 уникальных двузначных региональных префикса |

Лицензия CC BY-SA 4.0 требует указания авторства при использовании данных — см. таблицу выше
и `docs/registries.md`.
