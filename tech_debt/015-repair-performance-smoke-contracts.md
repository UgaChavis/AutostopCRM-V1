# 015. Восстановить MCP и browser performance smoke

Приоритет: P0
Статус: готово к выполнению
Риск изменения продукта: низкий; меняется проверочный контур

## Проблема

Два документированных локальных performance gate не проверяют текущий продукт:

- `scripts/perf_mcp.py --local-temp-server --iterations 3` сначала падает на
  старом sibling checkout AutostopManager, а с текущим Manager — вызывает
  устаревшие raw-инструменты `get_card`, `list_columns`, `bootstrap_context` и
  завершается `AttributeError` внутри `_first_move_target`;
- `scripts/perf_workflows.py --local-temp-server` повторяемо зависает при
  логине: perf импортирует приватный smoke helper, который специально сначала
  имитирует неправильный пароль, и ждёт закрытия `#identityModal` 30 секунд.

CI выполняет HTTP probe и synthetic Stage-1 с `--skip-browser`, поэтому оба
дефекта остаются зелёными в hosted workflow.

В снимке окружения 2026-08-29 локальный
`C:\Users\9860606\Desktop\AutostopManager` отставал от
`origin/AutostopManager` на 146 commits. Это не постоянный контракт, а
датированный environment drift: preflight должен сообщать фактическое состояние
явно, а не маскировать несовместимость `TypeError`.

## Итерационные срезы

1. Удалить `--allow-live-writes` и characterization-тестом зафиксировать:
   remote URL всегда read-only, любые `update`/`move` допустимы только с
   `--local-temp-server`, а отчёт не содержит record IDs или полных payloads.
2. Добавить явный MCP surface/preflight и безопасный разбор
   `structuredContent`; отсутствие ожидаемого инструмента или Manager contract
   должно давать короткий диагностический результат без `TaskGroup`/`NoneType`.
3. Зафиксировать один режим измерения — production Gateway v2. Измерять
   `agent_bootstrap`, `agent_board_digest`, exact context и только безопасный
   temp-state workflow. Не auto-detect raw/public surface.
4. Вынести success-only operator login в `browser_smoke_support.py`. Негативный
   login regression оставить в browser smoke; perf должен измерять только
   успешный путь и проверять authenticated state.
5. Добавить по одному bounded local-temp сценарию MCP и browser perf в CI.
   Полные профили остаются release/manual gates.

Каждый срез — отдельный commit с characterization test до изменения.

## Приёмка

- обе команды завершаются успешно на временных данных;
- remote-mode не имеет write-флага или write-кода; mutation paths требуют
  созданный текущим процессом local-temp server;
- MCP report измеряет текущие публичные Gateway-инструменты и не раскрывает
  bearer, record IDs, board data или полные payloads;
- stale/missing Manager даёт точный preflight error либо явно выбранный
  CRM-only режим, но не traceback из регистратора;
- browser perf не выполняет искусственный failed-login перед измерением;
- unit tests исполняют минимум surface contract и один реальный local-temp path;
- full unittest, 13 coverage floors, 24-tool Gateway contract, core browser
  smoke и Stage-1 performance gate остаются зелёными.

## Не входит

- production benchmarks с записями; они технически запрещены, а не оставлены
  опциональным флагом;
- изменение бизнес-логики, Gateway surface или release credentials;
- автоматическое обновление sibling checkout или deploy.
