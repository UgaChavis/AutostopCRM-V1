from __future__ import annotations

from typing import Any


async def _wait_modal_open(page: Any, selector: str) -> None:
    await page.wait_for_selector(f"{selector}.is-open")
    await page.wait_for_function(
        "(selector) => !document.querySelector(selector)?.hidden", arg=selector
    )


async def _wait_modal_closed(page: Any, selector: str) -> None:
    await page.wait_for_function(
        "(selector) => !document.querySelector(selector)?.classList.contains('is-open')",
        arg=selector,
    )


async def _is_modal_open(page: Any, selector: str) -> bool:
    return bool(
        await page.evaluate(
            "(selector) => document.querySelector(selector)?.classList.contains('is-open') === true",
            selector,
        )
    )


async def _close_card_modal_if_open(page: Any) -> bool:
    if not await _is_modal_open(page, "#cardModal"):
        return False
    await page.click('[data-close="card"]')
    await _wait_modal_closed(page, "#cardModal")
    return True


def _api_data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


async def _wait_clients_search_ready(
    page: Any, *, client_id: str, mobile: bool = False, query: str = ""
) -> None:
    if mobile:
        await page.wait_for_timeout(250)
        await page.wait_for_function(
            """(expectedQuery) => {
              const normalizedQuery = String(expectedQuery || '').trim().toLowerCase();
              const inputValue = String(document.querySelector('#mobileClientsSearchInput')?.value || '').trim().toLowerCase();
              const meta = document.querySelector('#mobileClientsMeta')?.textContent || '';
              const rows = Array.from(document.querySelectorAll('#mobileClientsList [data-mobile-client-id]'));
              return (
                rows.length > 0 &&
                inputValue === normalizedQuery &&
                !meta.includes('ЗАГРУЗКА') &&
                (!normalizedQuery || rows.some((row) => row.textContent.toLowerCase().includes(normalizedQuery)))
              );
            }""",
            arg=query,
        )
        return
    await page.wait_for_timeout(250)
    await page.wait_for_function(
        """([clientId, expectedQuery]) => {
          const normalizedQuery = String(expectedQuery || '').trim().toLowerCase();
          const inputValue = String(document.querySelector('#clientsSearchInput')?.value || '').trim().toLowerCase();
          const meta = document.querySelector('#clientsMeta')?.textContent || '';
          const row = document.querySelector('[data-client-id="' + clientId + '"]');
          const rowText = String(row?.textContent || '').toLowerCase();
          return Boolean(
            row &&
            inputValue === normalizedQuery &&
            !meta.includes('ПОИСК ПО ВСЕМ КЛИЕНТАМ') &&
            !meta.includes('ЗАГРУЗКА КРАТКОГО СПИСКА') &&
            (!normalizedQuery || (meta.includes('НАЙДЕНО') && rowText.includes(normalizedQuery)))
          );
        }""",
        arg=[client_id, query],
    )
