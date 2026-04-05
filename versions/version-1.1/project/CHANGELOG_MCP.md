# Changelog MCP / GPT Integration

## Что добавлено

- Нормализована схема `settings.json` по блокам:
  - `general`
  - `local_api`
  - `mcp`
  - `openai`
  - `auth`
  - `diagnostics`
- Добавлен централизованный расчёт:
  - `runtime_local_api_url`
  - `local_api_health_url`
  - `local_mcp_url`
  - `derived_public_mcp_url`
  - `derived_tunnel_mcp_url`
  - `effective_local_api_url`
  - `effective_mcp_url`
- Добавлено управление MCP runtime из UI:
  - запуск;
  - остановка;
  - индикация текущего состояния.
- Добавлена генерация bearer token для локального API и MCP.
- Добавлен экспорт:
  - карточки подключения;
  - снимка настроек интеграции.
- Добавлен helper-файл `connection_card.py`.

## Что исправлено

- Убран рассинхрон между старой формой настроек и новой моделью конфигурации.
- Секреты теперь скрыты по умолчанию, но доступны для show/hide и copy.
- Диагностика сохраняет статусы и времена последней проверки в `settings.json`.
- MCP runtime теперь можно запускать прямо из окна настроек, без ручного старта PowerShell.
- Итоговый MCP URL считается строго по приоритету override -> public -> tunnel -> local.
- Валидация и подсказки в Settings стали явными и инженерно читаемыми.

## Что автоматизировано

- Создание `settings.json` при первом запуске.
- Fallback на defaults при битом config-файле.
- Подстановка дефолтных host, port, path и OpenAI-параметров.
- Пересчёт derived URL в реальном времени.
- Export connection card без секретов по умолчанию.
- Автосинхронизация `local_api_bearer_token` и `mcp_bearer_token` между секциями конфигурации.

## Что осталось ограничением

- Секреты пока хранятся без системного secure storage.
- Полноценного OAuth provider пока нет.
- Изменение параметров уже запущенного локального API требует перезапуска приложения.
- Изменение параметров уже запущенного MCP runtime требует его остановки и повторного запуска.
- В MCP-интеграционных тестах библиотека по-прежнему оставляет шумные shutdown warnings, но functional pass зелёный.
