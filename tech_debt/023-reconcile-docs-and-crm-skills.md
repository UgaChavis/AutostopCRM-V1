# 023. Синхронизировать canonical repo docs и их audit

Приоритет: P0
Статус: completed locally 2026-08-29; hosted CI финального среза после publish
Риск изменения продукта: низкий; operational contract

## Проблема

Repo docs audit зелёный, но ручная проверка нашла drift:

- `MCP_GUIDE.md` говорит о 43 CRM workflow operations, хотя task 009 и current
  attestation требуют 46;
- backlog хранит 175 capability actions и 100 writes, current parity — 176 и
  101/101;
- `AGENTS.md` и connector note повторяют детали, уже канонические в runbook и
  MCP guide.

Семь canonical docs содержат 1 956 строк. Длинный runbook и API/MCP contract
docs сокращать механически нельзя: основная безопасная цель — agent/connector
дублирование. User-level skills находятся вне этого Git-репозитория и вынесены
в отдельную задачу 024.

## Итерационные срезы

1. Отдельным commit исправить exact contract counts и добавить machine-readable
   docs regression, связанный с attestation/parity source, а не ручную
   константу в двух местах.
2. Отдельным commit добавить в `docs_audit --include-skills` явный повторяемый
   `--skill-path` (или exact CRM profile). Stale-pattern rules должны работать
   только по четырём переданным `autostopcrm-*` каталогам и не сканировать
   посторонние user skills.
3. Отдельным commit уплотнить `AGENTS.md` и `CHATGPT_CONNECTOR_SETUP.md`,
   сохранив ссылки и semantic safety checklist: no-deploy, production evidence,
   finance stop-line, canonical doc routing и compatibility names.
4. Свернуть завершённые task 000/002/004/006/007 в краткую таблицу README;
   task-файлы удалять только после проверки ratchet owner references.

## Результат

- Exact operation count выводится из attestation source, а scoped skill audit
  проверяет только явно переданные CRM skills.
- `AGENTS.md` и connector note сокращены с 266 до 177 строк с сохранением
  semantic safety contracts; срезы опубликованы как `db78e09`, `8e1edfd` и
  `88216ea`, hosted CI зелёный.
- Пять завершённых task-журналов сведены в таблицу README после проверки: все
  36 активных ratchet mappings имеют других существующих owners, прямых ссылок
  вне README нет.
- Tasks 015 и оставшиеся срезы 016 независимы и не блокируют docs-контракт.

## Приёмка

- docs, attestation и parity показывают согласованные current counts;
- scoped audit fixtures доказывают stale-pattern detection только в явно
  переданных CRM skill paths и отсутствие cross-project false positives;
- `AGENTS.md` плюс connector note проходят semantic checklist, уменьшаются
  относительно pre-slice baseline, а итоговый exact total фиксируется как cap;
- repo docs audit, localization и related unit tests проходят;
- каждый numbered slice — отдельный green repo commit с hosted CI; production не
  меняется.

## Не входит

- обновление project memory без отдельного прямого запроса;
- изменение файлов в `C:\Users\9860606\.codex\skills` — это задача 024;
- перенос runbook/API/MCP деталей в skills;
- удаление compatibility names или server recovery contract.
