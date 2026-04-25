# AutoStop Telegram AI: русская инструкция запуска и проверки

Эта инструкция нужна для нового агента или оператора, который открывает репозиторий с другого компьютера и должен понять, как запущен Telegram AI Board Manager.

## Текущая рабочая точка

- branch: `autostopcrm-v1`
- latest verified Telegram AI checkpoint: `fa3f574`
- production repo: `/opt/autostopcrm`
- CRM: `https://crm.autostopcrm.ru`
- MCP: `https://crm.autostopcrm.ru/mcp`
- Docker services: `autostopcrm`, `autostopcrm-telegram-ai`
- local full suite at checkpoint: `431/431 OK`
- MCP tool count at checkpoint: `50`

## Что делает Telegram AI

Worker `autostopcrm-telegram-ai`:

- получает сообщения из Telegram через long polling;
- принимает текст, голос и фото;
- авторизует владельца по `AUTOSTOP_TELEGRAM_OWNER_IDS`;
- вызывает OpenAI для CRM-планирования, transcription, vision и web-search;
- работает с CRM только через локальный API `http://autostopcrm:41731`;
- пишет изменения через разрешённые tools;
- проверяет запись read-after-write;
- пишет audit в `/root/.minimal-kanban/telegram_ai/audit.jsonl`;
- хранит краткую память диалога в `/root/.minimal-kanban/telegram_ai/conversation.jsonl`;
- отвечает одним финальным сообщением на основе фактических tool results.

## Обязательные env-переменные

Production env обычно подключается как `/run/telegram-ai.env` внутри контейнера.

```env
AUTOSTOP_TELEGRAM_AI_ENABLED=1
AUTOSTOP_TELEGRAM_BOT_TOKEN=...
AUTOSTOP_TELEGRAM_OWNER_IDS=123456789
OPENAI_API_KEY=...
AUTOSTOP_CRM_API_BASE_URL=http://autostopcrm:41731
AUTOSTOP_AI_MODEL=gpt-5.4-mini
AUTOSTOP_AI_STRONG_MODEL=gpt-5.4
AUTOSTOP_AI_REASONING_EFFORT=medium
AUTOSTOP_AI_STRONG_REASONING_EFFORT=high
AUTOSTOP_AI_WEB_SEARCH_ENABLED=1
AUTOSTOP_AI_AUDIT_ENABLED=1
```

Если нет bot token, owner IDs, OpenAI key или `AUTOSTOP_TELEGRAM_AI_ENABLED=1`, worker остаётся safe-disabled и не раскрывает данные CRM.

## Модели и web-search

Текущая стабильная схема:

- обычные CRM-команды: `gpt-5.4-mini`;
- сложные CRM-команды: `gpt-5.4` + `high` reasoning;
- прямой internet-search: `gpt-5.4-mini`, low search context, одна повторная попытка.

Причина: live-проверки показали, что strong-model web-search может уходить в timeout или `429 Too Many Requests`. Поэтому web-search специально оставлен быстрым и стабильным.

## Команды для пользователя в Telegram

Базовые:

```text
/start
/status
Кратко по доске
Что сейчас горит?
Что ты сделал сегодня?
Откати последнее действие
```

Запись в CRM:

```text
Создай карточку: Telegram AI test, проверить связь, сегодня до 18:00.
Перенеси Камри в работу.
Добавь в заказ-наряд Камри замену масла и фильтра.
```

Интернет:

```text
Найди в интернете официальный сайт Toyota и дай ссылку.
Найди в интернете артикул воздушного фильтра для Toyota Land Cruiser Prado J150 2010 дизель 3.0. Ответь кратко с источниками.
```

Голос и фото:

```text
Голосом: Создай карточку Камри, диагностика стука спереди, завтра утром.
Фото с подписью: Прикрепи это фото к карточке Camry.
```

## Деплой

```bash
cd /opt/autostopcrm
git fetch origin --prune
./deploy.sh
docker compose ps
docker compose logs --tail=100 autostopcrm-telegram-ai
```

`deploy.sh` сам синхронизирует checkout с `origin/autostopcrm-v1`, если не задан другой branch override.

## Проверка production

Из локального PowerShell:

```powershell
ssh -i C:\Users\User\.ssh\codex_autostopcrm root@vps26457.mnogoweb.in "cd /opt/autostopcrm && git status --short --branch && git rev-parse --short HEAD && git rev-parse --short origin/autostopcrm-v1 && docker compose ps"
```

Live connector:

```powershell
ssh -i C:\Users\User\.ssh\codex_autostopcrm root@vps26457.mnogoweb.in "cd /opt/autostopcrm && docker compose exec -T autostopcrm python scripts/check_live_connector.py --strict --site-url https://crm.autostopcrm.ru --expect-https --local-api-url http://127.0.0.1:41731 --mcp-url https://crm.autostopcrm.ru/mcp --operator-username admin --operator-password admin --expect-admin"
```

Web-search smoke inside container:

```bash
docker compose exec -T autostopcrm-telegram-ai sh -lc 'set -a; . /run/telegram-ai.env; cd /app; PYTHONPATH=/app/src python - <<'"'"'PY'"'"'
from minimal_kanban.telegram_ai.config import load_config
from minimal_kanban.telegram_ai.openai_client import TelegramAIOpenAIClient
client = TelegramAIOpenAIClient(load_config())
print(client.internet_search(command_text="Найди в интернете официальный сайт Toyota и ответь одной строкой с источником.", role="owner")[:800])
PY'
```

## Что делать дальше

Следующая большая задача: composed parts-search flow.

Целевой сценарий:

```text
Пользователь в Telegram:
Зайди в карточку Prado, возьми VIN и найди в интернете воздушный фильтр.

Agent:
1. search/get card context
2. extract VIN/vehicle facts
3. perform internet search
4. return parts/OEM/sources in Telegram
5. optionally write compact note back to CRM
```

Не надо возвращаться к старому AutostopAI/VIN worker как к основе продукта. Новый AI-контур живёт в CRM repo: `main_telegram_ai.py` и `src/minimal_kanban/telegram_ai/`.
