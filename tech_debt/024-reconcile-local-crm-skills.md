# 024. Актуализировать локальные CRM skills отдельным recoverable срезом

Приоритет: P0
Статус: готово после 023
Риск изменения продукта: низкий; user-level agent instructions вне repo

## Проблема

Четыре локальных skill-каталога находятся в
`C:\Users\9860606\.codex\skills`, а не в Git-репозитории AutostopCRM:

- `autostopcrm-maintain`;
- `autostopcrm-code-maintain`;
- `autostopcrm-optimize`;
- `autostopcrm-ui-optimize`.

В снимке 2026-08-29 они содержат около 561 Markdown-строки, удалённый
`AUTOSTOP_DEPLOY_BRANCH`, ссылку на отсутствующий `telegram_ai/`, старые версии
зависимостей и destructive `git reset --hard` recovery. Часть workflows
подразумевает server sync/deploy или запись memory без отдельной команды
владельца. Hosted CI репозитория эти файлы не видит и не может подтвердить.

## Итерационные срезы

1. После repo-задачи 023 перечитать все четыре `SKILL.md` и требуемые references,
   зафиксировать hashes/line totals и создать timestamped recoverable backup с
   manifest вне самих skill-каталогов. Credentials в backup/report не включать.
2. Отдельными skill-by-skill patches убрать deploy/access/version копии,
   destructive reset, stale paths и безусловные server/memory mutations.
   Оставить короткую маршрутизацию к canonical repo docs и guardrails.
3. После каждого patch выполнить scoped audit из 023 только для изменённого
   каталога, проверить diff относительно backup и перечитать итоговый файл.
4. После четырёх patches выполнить общий scoped audit, зафиксировать новые exact
   hashes/line totals и recovery path в отчёте. Не выдавать это за GitHub CI.

Каждый каталог — отдельный local slice. Repo commit, push или deploy в этой
задаче не выполняются.

## Приёмка

- четыре CRM skills не содержат credentials, hard reset, auto-deploy,
  auto-server-sync, безусловной memory write или копий server access procedure;
- все referenced repo paths и dependency names проверены по current checkout;
- сохранены semantic boundaries: отдельная команда на deploy, production
  evidence перед legacy deletion, finance stop-line и compatibility names;
- scoped docs audit проходит только по четырём CRM skills без ошибок из чужих
  user-level skills;
- каждый изменённый skill короче своего pre-slice baseline; итоговые exact caps,
  diff summary и проверенный recovery path записаны в отчёте;
- production, GitHub и project memory не меняются.

## Не входит

- обновление project memory без отдельного прямого запроса;
- перенос runbook/API/MCP деталей в skills;
- удаление backup до завершения goal и приёмки итогового отчёта;
- server sync, deploy или изменение финансовых данных.
