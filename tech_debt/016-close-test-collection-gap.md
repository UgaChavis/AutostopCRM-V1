# 016. Закрыть пробел test collection и локального quality gate

Приоритет: P0
Статус: выполнено 2026-08-30
Риск изменения продукта: низкий; меняются tests/tooling

## Проблема

Статически в `tests/` находятся 2 038 тестов, но `python -m unittest discover`
исполняет 2 035. Не собираются три pytest-style функции:

- две в `tests/test_agent_card_editing_contract.py`;
- одна в `tests/test_description_web_contract.py`.

Локально случайно установлен pytest, но он не закреплён в manifests и не
используется CI. Добавлять второй runner ради трёх функций не требуется.

Дополнительно локальные entrypoints расходятся с hosted CI:

- `run_checks.ps1` — только changed-file Ruff, JS syntax и code health;
- `run_quality_pass.ps1` — unit/build gate без coverage, docs, browser perf и
  parity;
- CI — один Ubuntu/Python 3.12 job; локально проверка выполнялась на Python
  3.13.12, Windows job отсутствует.

## Итерационные срезы

1. Преобразовать три функции в два `unittest.TestCase`, сохранив смысл
   assertions. На неизменном source SHA suite должен вырасти ровно на три теста
   при тех же 34 platform skip. Это первый commit волны 0.
2. Добавить collection guard: module-level `test_*` запрещены, кроме
   attestation script, где набор функций обязан точно совпадать с `_CASE_NAMES`.
3. Отдельным commit убрать два текущих `F841` в `test_service.py` и сузить
   file-wide suppression, не выполняя массовый test-lint cleanup.
4. Отдельным commit сделать один документированный локальный профиль,
   зеркалящий обязательные
   non-production CI gates и переиспользующий существующие команды. Не
   дублировать orchestration во втором большом script.
5. Решение о коротком Windows/Python 3.12 hosted job для
   collection/compile/focused contracts оформить отдельным follow-up после
   измерения; не смешивать CI-matrix change с исправлением collection.

## Выполнено

- Три pytest-style функции преобразованы в собираемые `unittest.TestCase`, а
  guard запрещает новые module-level `test_*` вне точного attestation registry.
- Срезы collection/guard опубликованы commit `2f8b5d7`; hosted workflow
  `33248712521` зелёный.
- Из `tests/test_service.py` удалён устаревший file-wide `F841` suppression и
  две неиспользуемые привязки без удаления самих side-effect вызовов. Commit
  `ec51902`; hosted workflow `33264017412` зелёный.
- `scripts/run_checks.ps1 -Profile ci` последовательно зеркалит 25 обязательных
  non-container gates hosted quality job: Ruff, docs, обе coverage-сессии и
  ratchet, code health/localization/JS, capability/change-feed parity, core
  browser smoke, compile и два performance gate. Coverage и pycache изолированы
  в уникальном временном каталоге с проверяемой очисткой и восстановлением env.
- Первый полный Windows/Python 3.13.12 прогон нового профиля прошёл: 2 103 теста,
  34 ожидаемых platform skip, runtime branch coverage 79.74%, все 13 critical
  coverage floors, 396-file code-health audit, parity gaps=0, core browser smoke
  и production-scale synthetic performance thresholds.
- Hosted-only отличия перечислены явно: Ubuntu/Python 3.12, production Compose
  config и `docker-runtime-assets`. Live/full-browser блоки остаются только
  ручными `workflow_dispatch` inputs и выключены по умолчанию; локальный профиль
  не читает `.env`, не обращается к production и не запускает deploy.
- Отдельный Windows/Python 3.12 hosted job сейчас не добавлен: он дублировал бы
  полный локальный Windows gate. Вернуться к нему нужно только при повторяемом
  Windows-only дефекте, который не воспроизводится этим профилем.

## Приёмка

- на одном source SHA `unittest discover` исполняет baseline + 3 теста;
  непреднамеренные module-level tests ломают guard;
- pytest не добавлен в зависимости;
- full suite, coverage audit, Ruff, docs, parity и browser core проходят;
- локальная команда и hosted workflow перечисляют один и тот же обязательный
  набор gates либо явно документируют platform-only исключения;
- coverage и compile выполняются последовательно или с изолированным pycache,
  чтобы Windows atomic `.pyc` replace не давал ложную concurrency-ошибку.

## Не входит

- переход с unittest на pytest;
- дробление всех крупных test modules одним изменением;
- исправление всех 84 strict-complexity сигналов в tests.
