"""Render the authenticated CRM OAuth consent form."""

from __future__ import annotations

import html

from .oauth_provider import OAUTH_CONSENT_PATH


def _oauth_consent_page(
    request_id: str,
    *,
    client_name: str,
    scopes: list[str],
    error: str = "",
    action_url: str = OAUTH_CONSENT_PATH,
) -> str:
    safe_request_id = html.escape(str(request_id or ""), quote=True)
    safe_client_name = html.escape(str(client_name or "Codex / ChatGPT")[:120])
    safe_scopes = html.escape(", ".join(scopes))
    safe_action = html.escape(action_url, quote=True)
    error_block = (
        '<p class="error" role="alert">Не удалось подтвердить вход администратора.</p>'
        if error
        else ""
    )
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Подключение AutoStop CRM</title>
  <style>
    body {{ font: 16px/1.45 system-ui, sans-serif; margin: 0; background: #f4f6f8; color: #17202a; }}
    main {{ max-width: 440px; margin: 8vh auto; background: white; padding: 28px; border-radius: 14px; box-shadow: 0 12px 40px #18223022; }}
    h1 {{ font-size: 24px; margin: 0 0 10px; }}
    p {{ color: #52606d; }}
    label {{ display: block; margin: 16px 0 6px; font-weight: 650; }}
    input {{ box-sizing: border-box; width: 100%; padding: 11px 12px; border: 1px solid #b7c2cc; border-radius: 8px; font: inherit; }}
    button {{ width: 100%; margin-top: 22px; padding: 12px; border: 0; border-radius: 8px; background: #1261a0; color: white; font: inherit; font-weight: 700; cursor: pointer; }}
    .meta {{ font-size: 14px; }} .error {{ color: #b42318; font-weight: 650; }}
  </style>
</head>
<body><main>
  <h1>Подключить AutoStop CRM</h1>
  <p>Клиент <strong>{safe_client_name}</strong> запрашивает доступ к текущей CRM.</p>
  <p class="meta">Разрешения: {safe_scopes}</p>
  {error_block}
  <form method="post" action="{safe_action}" autocomplete="on">
    <input type="hidden" name="request_id" value="{safe_request_id}">
    <label for="username">Логин администратора CRM</label>
    <input id="username" name="username" type="text" autocomplete="username" required maxlength="120">
    <label for="password">Пароль</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required maxlength="512">
    <button type="submit">Подтвердить подключение</button>
  </form>
</main></body></html>"""
