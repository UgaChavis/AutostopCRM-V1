# 015. Восстановить MCP и browser performance smoke

Приоритет: P0
Статус: выполняется; browser и CRM Gateway v2 контракты приняты 2026-08-30;
реальный sibling Manager acceptance ожидает отдельной синхронизации checkout
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

## Ход выполнения

- Первый safety-подсрез удаляет `--allow-write-workflows`: browser perf теперь
  целиком требует process-owned `--local-temp-server` и отказывает до первого
  сетевого запроса для любого remote URL. Это необходимо не только для явных
  `save_card`/`move_card`: обычное открытие карточки вызывает write-side effects
  `open_card` и `mark_card_seen`.
- Characterization фиксирует три границы: remote runtime запрещён, local flag без
  фактического runtime-владельца недостаточен, а синтетический Stage-1 с
  `--skip-browser` остаётся рабочим.
- Второй safety-подсрез удаляет мёртвый remote card-discovery HTTP-контур и
  `--base-url`, remote operator credentials и недостижимые ветви логина.
  Финальный JSON теперь отбрасывает полные target/base URL, credentials, record
  IDs, state path, payload/DOM text и сырые console/page errors; failed requests
  сохраняют только method, фиксированный route и нормализованный network code.
  Redacting parser не повторяет значения устаревших/ошибочных CLI-аргументов в
  stderr. Ошибки state benchmark превращаются в безопасный failed row без
  traceback. MCP surface/preflight и success-only browser login остаются
  следующими отдельными подсрезами.
- Третий browser-contract подсрез сохраняет process-owned `browser_url` отдельно
  от безопасного origin, скрывает runtime и URL с bearer из `repr`/JSON и
  использует temp runtime для защищённого print-route. Success-only login вынесен
  в общий support helper: browser smoke по-прежнему сначала проверяет негативный
  login regression, а performance workflow выполняет только успешный путь.
  MCP surface/preflight остаётся следующим отдельным подсрезом.
- Четвёртый MCP-contract подсрез заменяет устаревшие raw-вызовы на exact
  production Gateway v2: публичный surface обязан содержать ровно 24
  `PERMANENT_AGENT_GATEWAY_TOOL_NAMES`, иначе probe останавливается до первого
  tool call. Измеряются только `agent_bootstrap`, `agent_board_digest`,
  `agent_entity_context` и, исключительно на process-owned временном runtime,
  bounded `agent_board_workflow(manager_board_scan, mode=dry_run)`.
- `agent_envelope_v2` теперь разбирается fail-closed без `model_dump`/text
  fallback; application, protocol, transport и malformed-result ошибки
  сводятся к фиксированным кодам без payload/record ID. Полнота строк и число
  итераций проверяются с учётом local/remote режима, поэтому пустой, неполный
  или ошибочно skipped отчёт не может завершиться green. Размер `tools/list`
  учитывает имена, описания и input schemas, но сами descriptors в JSON не
  публикуются.
- Реальный CRM HTTP/MCP transport проверен совместимым fake-Manager fixture:
  exact 24-tool surface, пять сценариев, очистка runtime/environment lease и
  безопасный отчёт проходят. Отдельный mismatch-прогон доказывает, что anyio
  `ExceptionGroup` разворачивается в фиксированный preflight error только после
  выхода из transport contexts. Это тест CRM transport/оркестрации, а не
  доказательство совместимости установленного sibling Manager. Hosted и
  локальный CI компилируют `perf_mcp.py` одним parity-проверяемым release gate,
  а полный unittest исполняет оба transport-сценария.
- Фактический локальный checkout
  `C:\Users\9860606\Desktop\AutostopManager` по-прежнему возвращает ровно
  `autostop_manager_incompatible` с process exit `2` до запуска API/MCP и не
  изменяется автоматически. Синхронизация/проверка реального Manager и отдельный
  pinned integration gate остаются межрепозиторным follow-up.

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
