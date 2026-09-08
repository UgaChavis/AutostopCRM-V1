    // @include inventory_reference.js

    function inventoryItemId(item) {
      return String(item?.id || '').trim();
    }

    function inventoryItemById(itemId) {
      const normalizedId = String(itemId || '').trim();
      if (!normalizedId) return null;
      return (Array.isArray(state.inventoryItems) ? state.inventoryItems : [])
        .find((item) => inventoryItemId(item) === normalizedId) || null;
    }

    function activeInventoryItem() {
      return inventoryItemById(state.inventoryActiveId);
    }

    function inventoryDecimalText(value, fallback = '0') {
      const normalized = String(value ?? '').trim().replace(',', '.');
      if (!normalized) return fallback;
      const parsed = Number(normalized);
      if (!Number.isFinite(parsed)) return fallback;
      if (!String(normalized).includes('.')) return String(normalized);
      return String(normalized).replace(/0+$/, '').replace(/\.$/, '') || '0';
    }

    function inventoryDisplayQuantity(item) {
      return inventoryDecimalText(item?.quantity, '0') + ' ' + (item?.unit || 'шт');
    }

    function inventoryItemMeta(item) {
      const parts = [];
      const catalog = String(item?.catalog_number || '').trim();
      if (catalog) parts.push(catalog);
      parts.push(inventoryDisplayQuantity(item));
      parts.push('закуп ' + repairOrderFormatRubles(item?.cost_price ?? 0));
      parts.push('прод ' + repairOrderFormatRubles(item?.sale_price ?? 0));
      return parts.join(' · ');
    }

    function inventorySearchMatches(item, query) {
      const needle = String(query || '').trim().toLowerCase();
      if (!needle) return true;
      return [
        item?.name,
        item?.catalog_number,
        item?.id,
      ].some((value) => String(value || '').toLowerCase().includes(needle));
    }

    function inventoryItemQuantityNumber(item) {
      const parsed = repairOrderParseNumber(item?.quantity);
      return parsed === null ? 0 : parsed;
    }

    function inventoryFilteredItems() {
      const query = String(state.inventoryQuery || '').trim();
      const stockFilter = String(state.inventoryStockFilter || 'all');
      return (Array.isArray(state.inventoryItems) ? state.inventoryItems : [])
        .filter((item) => inventorySearchMatches(item, query))
        .filter((item) => {
          const quantity = inventoryItemQuantityNumber(item);
          if (stockFilter === 'in_stock') return quantity > 0;
          if (stockFilter === 'zero') return quantity <= 0;
          return true;
        });
    }

    function inventoryUpdatedText(item) {
      const value = item?.updated_at || item?.created_at || '';
      return value ? formatDate(value) : '-';
    }

    function inventoryTableRowHtml(item) {
      const itemId = inventoryItemId(item);
      const activeClass = itemId === String(state.inventoryActiveId || '').trim() ? ' is-active' : '';
      const zeroClass = inventoryItemQuantityNumber(item) <= 0 ? ' is-zero' : '';
      return '<tr class="inventory-table__row' + activeClass + zeroClass + '" data-inventory-item-id="' + escapeHtml(itemId) + '">'
        + '<td><button class="inventory-table__select" type="button" data-inventory-item-id="' + escapeHtml(itemId) + '">' + escapeHtml(item?.name || 'Позиция без названия') + '</button></td>'
        + '<td class="inventory-table__mono">' + escapeHtml(item?.catalog_number || '-') + '</td>'
        + '<td class="inventory-table__number">' + escapeHtml(inventoryDisplayQuantity(item)) + '</td>'
        + '<td class="inventory-table__number">' + escapeHtml(repairOrderFormatRubles(item?.cost_price ?? 0)) + '</td>'
        + '<td class="inventory-table__number">' + escapeHtml(repairOrderFormatRubles(item?.sale_price ?? 0)) + '</td>'
        + '<td class="inventory-table__mono">' + escapeHtml(inventoryUpdatedText(item)) + '</td>'
      + '</tr>';
    }

    function inventoryMovementKindText(kind) {
      const normalized = String(kind || '').trim();
      if (normalized === 'incoming') return 'ПРИХОД';
      if (normalized === 'write_off') return 'СПИСАНИЕ';
      if (normalized === 'return') return 'ВОЗВРАТ';
      if (normalized === 'adjustment') return 'КОРР.';
      return normalized || '-';
    }

    function inventoryMovementItemName(movement) {
      const item = inventoryItemById(movement?.item_id);
      return item?.name || movement?.item_name || movement?.item_id || '-';
    }

    function inventoryMovementCardText(movement) {
      const orderNumber = String(movement?.repair_order_number || '').trim();
      if (orderNumber) return 'ЗН ' + orderNumber;
      const cardId = String(movement?.card_id || '').trim();
      return cardId ? ('КАРТА ' + cardId.slice(0, 8)) : '-';
    }

    function inventoryMovementPriceText(movement) {
      const kind = String(movement?.kind || '');
      const value = kind === 'incoming' ? movement?.cost_price : movement?.sale_price;
      return repairOrderFormatRubles(value ?? 0);
    }

    function inventoryMovementQuantityText(movement) {
      const quantity = inventoryDecimalText(movement?.quantity, '0') + ' ' + (movement?.unit || 'шт');
      const delta = inventoryDecimalText(movement?.quantity_delta, '');
      return delta && delta !== '0' ? quantity + ' (' + delta + ')' : quantity;
    }

    function inventoryMovementRowHtml(movement) {
      return '<tr>'
        + '<td class="inventory-table__mono">' + escapeHtml(formatDate(movement?.created_at || '')) + '</td>'
        + '<td>' + escapeHtml(inventoryMovementKindText(movement?.kind)) + '</td>'
        + '<td>' + escapeHtml(inventoryMovementItemName(movement)) + '</td>'
        + '<td class="inventory-table__number">' + escapeHtml(inventoryMovementQuantityText(movement)) + '</td>'
        + '<td class="inventory-table__number">' + escapeHtml(inventoryMovementPriceText(movement)) + '</td>'
        + '<td class="inventory-table__mono">' + escapeHtml(inventoryMovementCardText(movement)) + '</td>'
        + '<td class="inventory-table__mono">' + escapeHtml(movement?.actor_name || '-') + '</td>'
      + '</tr>';
    }

    function renderInventoryWorkspace() {
      const isMovements = state.inventoryView === 'movements';
      if (els.inventoryPositionsTab) {
        els.inventoryPositionsTab.classList.toggle('is-active', !isMovements);
        els.inventoryPositionsTab.setAttribute('aria-selected', isMovements ? 'false' : 'true');
      }
      if (els.inventoryMovementsTab) {
        els.inventoryMovementsTab.classList.toggle('is-active', isMovements);
        els.inventoryMovementsTab.setAttribute('aria-selected', isMovements ? 'true' : 'false');
      }
      if (els.inventoryPositionsPanel) els.inventoryPositionsPanel.hidden = isMovements;
      if (els.inventoryMovementsPanel) els.inventoryMovementsPanel.hidden = !isMovements;
      if (els.inventoryStockFilter && els.inventoryStockFilter.value !== state.inventoryStockFilter) {
        els.inventoryStockFilter.value = state.inventoryStockFilter;
      }
    }

    function renderInventoryQuickState() {
      if (!els.inventoryQuickState) return;
      const item = activeInventoryItem();
      const refs = inventoryFormRefs({ mobile: false });
      let stateName = 'dirty';
      let text = item ? 'ИЗМЕНЕНО' : 'НОВАЯ ПОЗИЦИЯ';
      if (item?.id) {
        const dirty = [
          [refs.name?.value, item.name || ''],
          [refs.catalog?.value, item.catalog_number || ''],
          [refs.unit?.value, item.unit || 'шт'],
          [refs.costPrice?.value, inventoryDecimalText(item.cost_price, '0')],
          [refs.salePrice?.value, inventoryDecimalText(item.sale_price, '0')],
        ].some(([left, right]) => String(left ?? '').trim() !== String(right ?? '').trim());
        if (!dirty && inventoryItemQuantityNumber(item) <= 0) {
          stateName = 'empty';
          text = 'НЕТ ОСТАТКА';
        } else if (!dirty) {
          stateName = 'saved';
          text = 'СОХРАНЕНО · ' + inventoryDisplayQuantity(item);
        }
      }
      els.inventoryQuickState.dataset.state = stateName;
      els.inventoryQuickState.textContent = text;
    }

    function renderInventoryMovements() {
      if (!els.inventoryMovementsBody) return;
      const movements = Array.isArray(state.inventoryMovements) ? state.inventoryMovements.slice().reverse() : [];
      if (state.inventoryMovementsLoading && !movements.length) {
        els.inventoryMovementsBody.innerHTML = '<tr><td colspan="7" class="cashboxes-empty">ЗАГРУЖАЮ ДВИЖЕНИЯ...</td></tr>';
        return;
      }
      els.inventoryMovementsBody.innerHTML = movements.length
        ? movements.map((movement) => inventoryMovementRowHtml(movement)).join('')
        : '<tr><td colspan="7" class="cashboxes-empty">ДВИЖЕНИЙ ПОКА НЕТ.</td></tr>';
    }

    function renderMobileInventoryMovements() {
      if (!els.mobileInventoryRecentMovements) return;
      const item = activeInventoryItem();
      if (!item?.id) {
        els.mobileInventoryRecentMovements.innerHTML = '<div class="mobile-kicker">ДВИЖЕНИЯ</div><div class="mobile-empty">ВЫБЕРИТЕ ПОЗИЦИЮ.</div>';
        return;
      }
      const itemId = inventoryItemId(item);
      const movements = (Array.isArray(state.inventoryMovements) ? state.inventoryMovements : [])
        .filter((movement) => String(movement?.item_id || '').trim() === itemId)
        .slice()
        .reverse()
        .slice(0, 5);
      if (!state.inventoryMovementsLoaded && !movements.length) {
        els.mobileInventoryRecentMovements.innerHTML = '<div class="mobile-kicker">ДВИЖЕНИЯ</div><div class="mobile-empty">ДВИЖЕНИЯ ЗАГРУЖАЮТСЯ...</div>';
        return;
      }
      els.mobileInventoryRecentMovements.innerHTML = '<div class="mobile-kicker">ПОСЛЕДНИЕ ДВИЖЕНИЯ</div>'
        + (movements.length
          ? movements.map((movement) => (
            '<div class="mobile-inventory-movement">'
              + '<span>' + escapeHtml(formatDate(movement?.created_at || '') + ' · ' + inventoryMovementKindText(movement?.kind)) + '</span>'
              + '<strong>' + escapeHtml(inventoryMovementQuantityText(movement)) + '</strong>'
            + '</div>'
          )).join('')
          : '<div class="mobile-empty">ДВИЖЕНИЙ ПО ЭТОЙ ПОЗИЦИИ НЕТ.</div>');
    }

    function inventoryStatus(text, isError = false) {
      const statusText = String(text || '').trim();
      if (els.inventoryStatusLine) {
        els.inventoryStatusLine.textContent = statusText;
        els.inventoryStatusLine.dataset.tone = isError ? 'error' : 'normal';
      }
      if (els.repairOrderInventoryStatus) {
        els.repairOrderInventoryStatus.textContent = statusText;
        els.repairOrderInventoryStatus.dataset.tone = isError ? 'error' : 'normal';
      }
      if (els.mobileInventoryStatusLine) {
        els.mobileInventoryStatusLine.textContent = statusText;
        els.mobileInventoryStatusLine.dataset.tone = isError ? 'error' : 'normal';
      }
    }

    function inventoryRowHtml(item, { mobile = false, panel = false } = {}) {
      const itemId = inventoryItemId(item);
      const activeId = panel ? state.repairOrderInventorySelectedId : state.inventoryActiveId;
      const activeClass = itemId === String(activeId || '').trim() ? ' is-active' : '';
      const className = panel ? 'repair-order-inventory-result' : (mobile ? 'inventory-row mobile-inventory-row' : 'inventory-row');
      const attr = panel ? 'data-repair-order-inventory-item-id' : (mobile ? 'data-mobile-inventory-item-id' : 'data-inventory-item-id');
      return '<button class="' + className + activeClass + '" type="button" ' + attr + '="' + escapeHtml(itemId) + '">'
        + '<div class="inventory-row__top">'
          + '<span class="inventory-row__name">' + escapeHtml(item?.name || 'Позиция без названия') + '</span>'
          + '<strong class="inventory-row__qty">' + escapeHtml(inventoryDisplayQuantity(item)) + '</strong>'
        + '</div>'
        + '<div class="inventory-row__meta">' + escapeHtml(inventoryItemMeta(item)) + '</div>'
      + '</button>';
    }

    function renderInventoryItems() {
      const items = inventoryFilteredItems();
      if (els.inventorySearchInput && els.inventorySearchInput.value !== state.inventoryQuery) {
        els.inventorySearchInput.value = state.inventoryQuery;
      }
      renderInventoryWorkspace();
      if (els.inventoryTableBody) {
        els.inventoryTableBody.innerHTML = !state.inventoryLoaded && !items.length
          ? '<tr><td colspan="6" class="cashboxes-empty">ЗАГРУЖАЮ СКЛАД...</td></tr>'
          : (items.length
            ? items.map((item) => inventoryTableRowHtml(item)).join('')
            : '<tr><td colspan="6" class="cashboxes-empty">ПОЗИЦИЙ ПОКА НЕТ.</td></tr>');
      } else if (els.inventoryItemsList) {
        els.inventoryItemsList.innerHTML = !state.inventoryLoaded && !items.length
          ? '<div class="cashboxes-empty">ЗАГРУЖАЮ СКЛАД...</div>'
          : (items.length
            ? items.map((item) => inventoryRowHtml(item)).join('')
            : '<div class="cashboxes-empty">ПОЗИЦИЙ ПОКА НЕТ.</div>');
      }
      renderInventoryMovements();
      renderMobileInventory();
      renderRepairOrderInventoryPanel();
    }

    function inventoryFormRefs({ mobile = state.mobileLite && state.mobileView === 'inventory' } = {}) {
      return mobile ? {
        name: els.mobileInventoryNameInput,
        catalog: els.mobileInventoryCatalogInput,
        unit: els.mobileInventoryUnitSelect,
        quantity: els.mobileInventoryQuantityInput,
        replenishQuantity: els.mobileInventoryReplenishQuantityInput,
        costPrice: els.mobileInventoryCostPriceInput,
        salePrice: els.mobileInventorySalePriceInput,
        saveButton: els.mobileInventorySaveButton,
        replenishButton: els.mobileInventoryReplenishButton,
      } : {
        name: els.inventoryNameInput,
        catalog: els.inventoryCatalogInput,
        unit: els.inventoryUnitSelect,
        quantity: els.inventoryQuantityInput,
        replenishQuantity: els.inventoryReplenishQuantityInput,
        costPrice: els.inventoryCostPriceInput,
        salePrice: els.inventorySalePriceInput,
        saveButton: els.inventorySaveButton,
        replenishButton: els.inventoryReplenishButton,
      };
    }

    function syncInventoryForm(refs, item) {
      if (!refs?.name) return;
      const hasItem = Boolean(item?.id);
      refs.name.value = hasItem ? String(item.name || '') : '';
      if (refs.catalog) refs.catalog.value = hasItem ? String(item.catalog_number || '') : '';
      if (refs.unit) refs.unit.value = hasItem ? String(item.unit || 'шт') : 'шт';
      if (refs.quantity) {
        refs.quantity.value = hasItem ? inventoryDecimalText(item.quantity, '0') : '0';
        refs.quantity.readOnly = hasItem;
        refs.quantity.title = hasItem ? 'Остаток меняется через пополнение или списание.' : '';
      }
      if (refs.replenishQuantity) refs.replenishQuantity.value = '';
      if (refs.costPrice) refs.costPrice.value = hasItem ? inventoryDecimalText(item.cost_price, '0') : '0';
      if (refs.salePrice) refs.salePrice.value = hasItem ? inventoryDecimalText(item.sale_price, '0') : '0';
      if (refs.saveButton) refs.saveButton.disabled = state.inventorySaving;
      if (refs.replenishButton) refs.replenishButton.disabled = state.inventorySaving || !hasItem;
    }

    function renderInventoryForm() {
      const item = activeInventoryItem();
      if (els.inventoryDetailTitle) {
        els.inventoryDetailTitle.textContent = item ? 'ПОЗИЦИЯ СКЛАДА' : 'НОВАЯ ПОЗИЦИЯ';
      }
      syncInventoryForm(inventoryFormRefs({ mobile: false }), item);
      syncInventoryForm(inventoryFormRefs({ mobile: true }), item);
      renderInventoryQuickState();
    }

    function renderInventory() {
      renderInventoryItems();
      renderInventoryForm();
      renderInventoryMovements();
      renderMobileInventoryMovements();
    }

    function upsertInventoryItem(item) {
      if (!item?.id) return;
      const items = Array.isArray(state.inventoryItems) ? state.inventoryItems.slice() : [];
      const index = items.findIndex((entry) => inventoryItemId(entry) === inventoryItemId(item));
      if (index >= 0) items[index] = item;
      else items.unshift(item);
      state.inventoryItems = items;
      state.inventoryLoaded = true;
    }

    async function loadInventoryItems(openModal = false, { query = null, prepared = null } = {}) {
      const context = prepared || inventoryAsyncContext('items');
      try {
        if (!state.inventoryLoaded) renderInventoryItems();
        const data = await (prepared?.promise || readInventoryItems(query));
        if (!context.isCurrent()) return null;
        state.inventoryItems = Array.isArray(data?.items) ? data.items : [];
        state.inventoryLoaded = true;
        if (state.inventoryActiveId && !inventoryItemById(state.inventoryActiveId)) {
          state.inventoryActiveId = '';
        }
        if (!state.inventoryActiveId && state.inventoryItems.length) {
          state.inventoryActiveId = inventoryItemId(state.inventoryItems[0]);
        }
        if (state.repairOrderInventorySelectedId && !inventoryItemById(state.repairOrderInventorySelectedId)) {
          state.repairOrderInventorySelectedId = '';
        }
        if (!state.repairOrderInventorySelectedId && state.inventoryItems.length) {
          state.repairOrderInventorySelectedId = inventoryItemId(state.inventoryItems[0]);
        }
        renderInventory();
        maybeOpenModal(els.inventoryModal, openModal);
        return data;
      } catch (error) {
        if (!context.isCurrent()) return null;
        state.inventoryLoaded = false;
        renderInventory();
        maybeOpenModal(els.inventoryModal, openModal);
        inventoryStatus(error.message, true);
        setStatus(error.message, true);
        return null;
      }
    }

    async function loadInventoryMovements({ force = false } = {}) {
      if (state.inventoryMovementsLoading) return null;
      if (state.inventoryMovementsLoaded && !force) {
        renderInventoryMovements();
        renderMobileInventoryMovements();
        return { movements: state.inventoryMovements };
      }
      const context = inventoryAsyncContext('movements');
      state.inventoryMovementsLoading = true;
      renderInventoryMovements();
      renderMobileInventoryMovements();
      try {
        const data = await api('/api/list_inventory_movements?limit=200');
        if (!context.isCurrent()) return null;
        state.inventoryMovements = Array.isArray(data?.movements) ? data.movements : [];
        state.inventoryMovementsLoaded = true;
        renderInventoryMovements();
        renderMobileInventoryMovements();
        return data;
      } catch (error) {
        if (!context.isCurrent()) return null;
        state.inventoryMovementsLoaded = false;
        inventoryStatus(error.message, true);
        setStatus(error.message, true);
        renderInventoryMovements();
        renderMobileInventoryMovements();
        return null;
      } finally {
        if (context.owns()) {
          state.inventoryMovementsLoading = false;
          renderInventoryMovements();
          renderMobileInventoryMovements();
        }
      }
    }

    function invalidateInventoryMovements() {
      inventoryAsyncContext('movements');
      state.inventoryMovementsLoading = false;
      state.inventoryMovementsLoaded = false;
      if (state.inventoryView === 'movements' || (state.mobileLite && state.mobileView === 'inventory')) {
        loadInventoryMovements({ force: true });
      } else {
        renderInventoryMovements();
        renderMobileInventoryMovements();
      }
    }

    function openInventoryModal(prepared = null) {
      if (prepared?.promise && !prepared.isCurrent()) return;
      maybeOpenModal(els.inventoryModal, true);
      renderInventory();
      const loading = loadInventoryItems(false, { prepared: prepared?.promise ? prepared : null });
      if (state.inventoryView === 'movements') loadInventoryMovements();
      return loading;
    }

    function selectInventoryItem(itemId) {
      const normalizedId = String(itemId || '').trim();
      state.inventoryActiveId = normalizedId;
      if (normalizedId) state.repairOrderInventorySelectedId = normalizedId;
      renderInventory();
      if (state.mobileLite && state.mobileView === 'inventory') loadInventoryMovements();
    }

    function resetInventoryForm() {
      state.inventoryActiveId = '';
      renderInventory();
      inventoryFormRefs().name?.focus({ preventScroll: true });
    }

    function setInventoryView(view) {
      state.inventoryView = view === 'movements' ? 'movements' : 'positions';
      renderInventory();
      if (state.inventoryView === 'movements') loadInventoryMovements();
    }

    function handleInventoryStockFilterChange() {
      state.inventoryStockFilter = String(els.inventoryStockFilter?.value || 'all');
      renderInventoryItems();
    }

    function handleInventoryFormInput() {
      renderInventoryQuickState();
    }

    function inventoryPayloadFromForm() {
      const refs = inventoryFormRefs();
      const itemId = String(state.inventoryActiveId || '').trim();
      const payload = {
        name: String(refs.name?.value || '').trim(),
        catalog_number: String(refs.catalog?.value || '').trim(),
        unit: String(refs.unit?.value || 'шт').trim() || 'шт',
        cost_price: String(refs.costPrice?.value || '0').trim(),
        sale_price: String(refs.salePrice?.value || '0').trim(),
        actor_name: state.actor,
        source: 'ui',
      };
      if (itemId) payload.item_id = itemId;
      else payload.quantity = String(refs.quantity?.value || '0').trim();
      return payload;
    }

    async function saveInventoryItem() {
      if (state.inventorySaving) return;
      const payload = inventoryPayloadFromForm();
      if (!payload.name) {
        inventoryStatus('УКАЖИТЕ НАЗВАНИЕ ПОЗИЦИИ.', true);
        return;
      }
      let activeId = String(state.inventoryActiveId || '');
      const context = inventoryAsyncContext('save', () => String(state.inventoryActiveId || '') === activeId);
      state.inventorySaving = true;
      renderInventoryForm();
      try {
        const data = await api('/api/save_inventory_item', { method: 'POST', body: payload });
        if (!context.isCurrent()) return;
        if (data?.item) {
          upsertInventoryItem(data.item);
          state.inventoryActiveId = inventoryItemId(data.item);
          state.repairOrderInventorySelectedId = inventoryItemId(data.item);
          activeId = state.inventoryActiveId;
        }
        if (data?.movement) invalidateInventoryMovements();
        await loadInventoryItems(false, { query: state.inventoryQuery });
        if (!context.isCurrent()) return;
        inventoryStatus(data?.meta?.created ? 'ПОЗИЦИЯ ДОБАВЛЕНА.' : 'ПОЗИЦИЯ СОХРАНЕНА.', false);
      } catch (error) {
        if (!context.isCurrent()) return;
        inventoryStatus(error.message, true);
        setStatus(error.message, true);
      } finally {
        if (context.owns()) {
          state.inventorySaving = false;
          renderInventory();
        }
      }
    }

    async function replenishInventoryItem() {
      if (state.inventorySaving) return;
      const item = activeInventoryItem();
      if (!item?.id) return inventoryStatus('ВЫБЕРИТЕ ПОЗИЦИЮ ДЛЯ ПОПОЛНЕНИЯ.', true);
      const refs = inventoryFormRefs();
      const quantity = String(refs.replenishQuantity?.value || '').trim();
      const parsed = repairOrderParseNumber(quantity);
      if (parsed === null || parsed <= 0) {
        refs.replenishQuantity?.focus({ preventScroll: true });
        return inventoryStatus('УКАЖИТЕ КОЛИЧЕСТВО БОЛЬШЕ НУЛЯ.', true);
      }
      const context = inventoryAsyncContext('save', () => String(state.inventoryActiveId || '') === String(item.id));
      state.inventorySaving = true;
      renderInventoryForm();
      try {
        const data = await api('/api/replenish_inventory_item', {
          method: 'POST',
          body: {
            item_id: item.id,
            quantity,
            cost_price: String(refs.costPrice?.value || '').trim(),
            sale_price: String(refs.salePrice?.value || '').trim(),
            actor_name: state.actor,
            source: 'ui',
          },
        });
        if (!context.isCurrent()) return;
        if (data?.item) {
          upsertInventoryItem(data.item);
          state.inventoryActiveId = inventoryItemId(data.item);
          state.repairOrderInventorySelectedId = inventoryItemId(data.item);
        }
        invalidateInventoryMovements();
        await loadInventoryItems(false, { query: state.inventoryQuery });
        if (!context.isCurrent()) return;
        inventoryStatus('ОСТАТОК ПОПОЛНЕН.', false);
      } catch (error) {
        if (!context.isCurrent()) return;
        inventoryStatus(error.message, true);
        setStatus(error.message, true);
      } finally {
        if (context.owns()) {
          state.inventorySaving = false;
          renderInventory();
        }
      }
    }

    function handleInventorySearchInput() {
      state.inventoryQuery = String(els.inventorySearchInput?.value || '').trim();
      inventoryAsyncContext('items');
      const context = inventoryAsyncContext('search');
      if (state.inventorySearchTimer) window.clearTimeout(state.inventorySearchTimer);
      state.inventorySearchTimer = window.setTimeout(() => {
        if (!context.isCurrent()) return;
        state.inventorySearchTimer = null;
        loadInventoryItems(false, { query: state.inventoryQuery });
      }, 250);
    }

    function handleInventoryItemsClick(event) {
      const button = event.target instanceof HTMLElement ? event.target.closest('[data-inventory-item-id]') : null;
      if (!button || !els.inventoryItemsList?.contains(button)) return;
      selectInventoryItem(button.getAttribute('data-inventory-item-id'));
    }

    function renderMobileInventory() {
      if (!els.mobileInventoryItemsList) return;
      const items = (Array.isArray(state.inventoryItems) ? state.inventoryItems : [])
        .filter((item) => inventorySearchMatches(item, state.inventoryQuery));
      if (els.mobileInventorySearchInput && els.mobileInventorySearchInput.value !== state.inventoryQuery) {
        els.mobileInventorySearchInput.value = state.inventoryQuery;
      }
      els.mobileInventoryItemsList.innerHTML = !state.inventoryLoaded && !items.length
        ? '<div class="mobile-empty">СКЛАД ЗАГРУЖАЕТСЯ...</div>'
        : (items.length
          ? items.map((item) => inventoryRowHtml(item, { mobile: true })).join('')
          : '<div class="mobile-empty">ПОЗИЦИЙ ПОКА НЕТ.</div>');
      renderInventoryForm();
      renderMobileInventoryMovements();
    }

    function handleMobileInventorySearchInput() {
      state.inventoryQuery = String(els.mobileInventorySearchInput?.value || '').trim();
      inventoryAsyncContext('items');
      const context = inventoryAsyncContext('search');
      if (state.mobileInventorySearchTimer) window.clearTimeout(state.mobileInventorySearchTimer);
      state.mobileInventorySearchTimer = window.setTimeout(() => {
        if (!context.isCurrent()) return;
        state.mobileInventorySearchTimer = null;
        loadInventoryItems(false, { query: state.inventoryQuery });
      }, 250);
    }

    function handleMobileInventoryItemsClick(event) {
      const button = event.target instanceof HTMLElement ? event.target.closest('[data-mobile-inventory-item-id]') : null;
      if (!button || !els.mobileInventoryItemsList?.contains(button)) return;
      selectInventoryItem(button.getAttribute('data-mobile-inventory-item-id'));
    }

    function repairOrderMaterialRowElements() {
      return Array.from(els.repairOrderMaterialsBody?.querySelectorAll('tr[data-repair-order-row="materials"]') || []);
    }

    function repairOrderInventoryRememberedRowIndex() {
      const parsed = finiteNumber(state.repairOrderInventoryRowIndex, -1);
      const rows = repairOrderMaterialRowElements();
      return Number.isInteger(parsed) && parsed >= 0 && parsed < rows.length ? parsed : -1;
    }

    function rememberRepairOrderInventoryRow(event) {
      const row = event?.target instanceof HTMLElement ? event.target.closest('tr[data-repair-order-row="materials"]') : null;
      if (!row || !els.repairOrderMaterialsBody?.contains(row)) return;
      const rows = repairOrderMaterialRowElements();
      const index = rows.indexOf(row);
      if (index < 0) return;
      state.repairOrderInventoryRowIndex = String(index);
      const rowData = readRepairOrderRowElement(row);
      if (rowData.inventory_item_id) state.repairOrderInventorySelectedId = rowData.inventory_item_id;
      renderRepairOrderInventoryPanel();
    }

    function repairOrderInventorySelectedRow() {
      const rows = repairOrderMaterialRowElements();
      if (!rows.length) return null;
      const rememberedIndex = repairOrderInventoryRememberedRowIndex();
      if (rememberedIndex >= 0) return rows[rememberedIndex];
      const activeRow = document.activeElement instanceof HTMLElement
        ? document.activeElement.closest('tr[data-repair-order-row="materials"]')
        : null;
      if (activeRow && els.repairOrderMaterialsBody?.contains(activeRow)) return activeRow;
      const selectedItemId = String(state.repairOrderInventorySelectedId || '').trim();
      if (selectedItemId) {
        const linked = rows.find((row) => {
          const rowData = readRepairOrderRowElement(row);
          return rowData.inventory_item_id === selectedItemId && rowData.inventory_movement_id;
        });
        if (linked) return linked;
      }
      if (rows.length === 1 && !repairOrderRowHasAnyData(readRepairOrderRowElement(rows[0]))) return rows[0];
      return null;
    }

    function repairOrderInventorySelectedMovementId() {
      const selectedRow = repairOrderInventorySelectedRow();
      const selectedData = selectedRow ? readRepairOrderRowElement(selectedRow) : null;
      if (selectedData?.inventory_movement_id) return selectedData.inventory_movement_id;
      const selectedItemId = String(state.repairOrderInventorySelectedId || '').trim();
      if (!selectedItemId) return '';
      const linked = repairOrderMaterialRowElements().find((row) => {
        const rowData = readRepairOrderRowElement(row);
        return rowData.inventory_item_id === selectedItemId && rowData.inventory_movement_id;
      });
      return linked ? readRepairOrderRowElement(linked).inventory_movement_id : '';
    }

    function repairOrderInventoryTargetRowIndex() {
      const rows = repairOrderMaterialRowElements();
      const rememberedIndex = repairOrderInventoryRememberedRowIndex();
      if (rememberedIndex >= 0) return rememberedIndex;
      const selectedRow = repairOrderInventorySelectedRow();
      if (selectedRow) {
        const index = rows.indexOf(selectedRow);
        if (index >= 0) return index;
      }
      if (rows.length === 1 && !repairOrderRowHasAnyData(readRepairOrderRowElement(rows[0]))) return 0;
      return rows.length;
    }

    function selectedRepairOrderInventoryItem() {
      return inventoryItemById(state.repairOrderInventorySelectedId) || activeInventoryItem();
    }

    function repairOrderInventoryQuantity() {
      const raw = String(els.repairOrderInventoryQuantityInput?.value || '').trim();
      const parsed = repairOrderParseNumber(raw);
      return parsed !== null && parsed > 0 ? { raw, parsed } : null;
    }

    function repairOrderInventoryResults() {
      const query = String(state.repairOrderInventoryQuery || '').trim();
      return (Array.isArray(state.inventoryItems) ? state.inventoryItems : [])
        .filter((item) => inventorySearchMatches(item, query))
        .slice(0, 80);
    }

    function repairOrderInventoryRowFromItem(item, quantity) {
      const salePrice = repairOrderParseNumber(item?.sale_price ?? 0) ?? 0;
      return normalizeRepairOrderRow({
        name: item?.name || '',
        catalog_number: item?.catalog_number || '',
        quantity: quantity.raw,
        cost_price: item?.cost_price || '0',
        price: item?.sale_price || '0',
        total: repairOrderNumberToRaw(repairOrderRoundMoney(quantity.parsed * salePrice)),
        inventory_item_id: '',
        inventory_movement_id: '',
        inventory_unit: '',
      });
    }

    function setRepairOrderMaterialRow(rowData, targetIndex) {
      const rows = readRepairOrderRows('materials');
      let index = finiteNumber(targetIndex, rows.length);
      if (!Number.isInteger(index) || index < 0) index = rows.length;
      if (index > rows.length) index = rows.length;
      if (index === rows.length) rows.push(rowData);
      else rows[index] = normalizeRepairOrderRow({ ...rows[index], ...rowData });
      renderRepairOrderRows('materials', rows);
      state.repairOrderInventoryRowIndex = String(Math.max(0, Math.min(index, rows.length - 1)));
    }

    function fillRepairOrderInventoryRow() {
      const item = selectedRepairOrderInventoryItem();
      if (!item?.id) return inventoryStatus('ВЫБЕРИТЕ ПОЗИЦИЮ СКЛАДА.', true);
      const quantity = repairOrderInventoryQuantity();
      if (!quantity) {
        els.repairOrderInventoryQuantityInput?.focus({ preventScroll: true });
        return inventoryStatus('УКАЖИТЕ КОЛИЧЕСТВО БОЛЬШЕ НУЛЯ.', true);
      }
      setRepairOrderMaterialRow(repairOrderInventoryRowFromItem(item, quantity), repairOrderInventoryTargetRowIndex());
      syncRepairOrderTotals();
      renderRepairOrderInventoryPanel();
      inventoryStatus('СТРОКА МАТЕРИАЛА ЗАПОЛНЕНА БЕЗ СПИСАНИЯ.', false);
    }

    async function mutateInventoryMaterial(path, payload, message) {
      if (state.inventoryMaterialSaving) return;
      const cardContext = captureCardEditingContext();
      let expectedCardId = String(state.editingId || '');
      let selectedId = state.repairOrderInventorySelectedId;
      let selectedRow = state.repairOrderInventoryRowIndex;
      const context = inventoryAsyncContext('material', () => cardContext()
        && String(state.editingId || '') === expectedCardId
        && state.repairOrderInventorySelectedId === selectedId && state.repairOrderInventoryRowIndex === selectedRow);
      const actorName = state.actor;
      state.inventoryMaterialSaving = true;
      renderRepairOrderInventoryPanel();
      try {
        const cardId = await requireRepairOrderCardId();
        if (!cardId || !context.owns() || !cardContext() || (expectedCardId && cardId !== expectedCardId)) return;
        expectedCardId = String(cardId);
        if (!context.isCurrent()) return;
        const data = await api(path, {
          method: 'POST',
          body: { ...payload, card_id: cardId, actor_name: actorName, source: 'ui' },
        });
        if (!context.isCurrent()) return;
        if (data?.item) {
          upsertInventoryItem(data.item);
          state.inventoryActiveId = inventoryItemId(data.item);
          state.repairOrderInventorySelectedId = inventoryItemId(data.item);
        }
        if (Object.prototype.hasOwnProperty.call(payload, 'row_index')) {
          state.repairOrderInventoryRowIndex = String(data?.meta?.row_index ?? payload.row_index);
        }
        if (data?.card || data?.repair_order) {
          const updatedCard = repairOrderResponseCard(data, data?.repair_order || readRepairOrderFromForm());
          applyRepairOrderCardUpdate(updatedCard, data?.repair_order || {});
        }
        selectedId = state.repairOrderInventorySelectedId;
        selectedRow = state.repairOrderInventoryRowIndex;
        if (data?.card || data?.repair_order) {
          await refreshRepairOrdersListAfterMutation();
          if (!context.isCurrent()) return;
        }
        await loadInventoryItems(false, { query: state.repairOrderInventoryQuery || state.inventoryQuery });
        if (!context.isCurrent()) return;
        invalidateInventoryMovements();
        inventoryStatus(message, false);
        setStatus(message, false);
      } catch (error) {
        if (!context.isCurrent()) return;
        inventoryStatus(error.message, true);
        setStatus(error.message, true);
      } finally {
        if (context.owns()) {
          state.inventoryMaterialSaving = false;
          if (cardContext()) renderRepairOrderInventoryPanel();
        }
      }
    }

    async function writeOffInventoryItem() {
      const item = selectedRepairOrderInventoryItem();
      if (!item?.id) return inventoryStatus('ВЫБЕРИТЕ ПОЗИЦИЮ СКЛАДА.', true);
      const quantity = repairOrderInventoryQuantity();
      if (!quantity) {
        els.repairOrderInventoryQuantityInput?.focus({ preventScroll: true });
        return inventoryStatus('УКАЖИТЕ КОЛИЧЕСТВО БОЛЬШЕ НУЛЯ.', true);
      }
      const available = inventoryItemQuantityNumber(item);
      if (quantity.parsed > available) {
        els.repairOrderInventoryQuantityInput?.focus({ preventScroll: true });
        renderRepairOrderInventoryPanel();
        return inventoryStatus('НЕЛЬЗЯ СПИСАТЬ БОЛЬШЕ ОСТАТКА: ' + inventoryDisplayQuantity(item) + '.', true);
      }
      return mutateInventoryMaterial('/api/write_off_inventory_item', {
        item_id: item.id, quantity: quantity.raw, row_index: repairOrderInventoryTargetRowIndex(),
      }, 'МАТЕРИАЛ СПИСАН СО СКЛАДА.');
    }

    async function returnInventoryMovement() {
      const movementId = repairOrderInventorySelectedMovementId();
      if (!movementId) return inventoryStatus('В СТРОКЕ НЕТ СКЛАДСКОГО СПИСАНИЯ.', true);
      return mutateInventoryMaterial('/api/return_inventory_movement', {
        movement_id: movementId,
      }, 'СПИСАНИЕ ВОЗВРАЩЕНО НА СКЛАД.');
    }

    function renderRepairOrderInventoryPanel() {
      const panel = els.repairOrderInventoryPanel;
      const isOpen = Boolean(state.repairOrderInventoryOpen);
      const materialsCard = els.repairOrderMaterialsBody?.closest('.repair-order-table-card');
      materialsCard?.classList.toggle('is-inventory-open', isOpen);
      if (els.repairOrderInventoryToggleButton) {
        els.repairOrderInventoryToggleButton.classList.toggle('is-active', isOpen);
        els.repairOrderInventoryToggleButton.setAttribute('aria-pressed', isOpen ? 'true' : 'false');
      }
      if (!panel) return;
      panel.hidden = !isOpen;
      if (!isOpen) return;
      if (els.repairOrderInventorySearchInput && els.repairOrderInventorySearchInput.value !== state.repairOrderInventoryQuery) {
        els.repairOrderInventorySearchInput.value = state.repairOrderInventoryQuery;
      }
      const items = repairOrderInventoryResults();
      if (els.repairOrderInventoryResults) {
        els.repairOrderInventoryResults.innerHTML = !state.inventoryLoaded && !items.length
          ? '<div class="cashboxes-empty">ЗАГРУЖАЮ СКЛАД...</div>'
          : (items.length
            ? items.map((item) => inventoryRowHtml(item, { panel: true })).join('')
            : '<div class="cashboxes-empty">НИЧЕГО НЕ НАЙДЕНО.</div>');
      }
      const selected = selectedRepairOrderInventoryItem();
      if (els.repairOrderInventorySelected) {
        els.repairOrderInventorySelected.innerHTML = selected
          ? '<div class="inventory-row__top"><span class="inventory-row__name">' + escapeHtml(selected.name || 'Позиция') + '</span><strong class="inventory-row__qty">' + escapeHtml(inventoryDisplayQuantity(selected)) + '</strong></div>'
            + '<div class="inventory-row__meta">' + escapeHtml(inventoryItemMeta(selected)) + '</div>'
          : '<div class="cashboxes-empty">ВЫБЕРИТЕ ПОЗИЦИЮ.</div>';
      }
      if (els.repairOrderInventoryQuantityInput && selected && !String(els.repairOrderInventoryQuantityInput.value || '').trim()) {
        els.repairOrderInventoryQuantityInput.value = '1';
      }
      const hasQuantity = Boolean(repairOrderInventoryQuantity());
      const requestedQuantity = repairOrderInventoryQuantity();
      if (els.repairOrderInventoryStock) {
        const stockText = selected ? ('ОСТАТОК: ' + inventoryDisplayQuantity(selected)) : 'ОСТАТОК: НЕ ВЫБРАНО';
        const overLimit = selected && requestedQuantity && requestedQuantity.parsed > inventoryItemQuantityNumber(selected);
        els.repairOrderInventoryStock.textContent = overLimit
          ? stockText + ' · НЕДОСТАТОЧНО ДЛЯ СПИСАНИЯ'
          : stockText;
        els.repairOrderInventoryStock.dataset.tone = overLimit ? 'error' : 'normal';
      }
      const hasMovement = Boolean(repairOrderInventorySelectedMovementId());
      if (els.repairOrderInventoryFillButton) els.repairOrderInventoryFillButton.disabled = !selected || !hasQuantity;
      if (els.repairOrderInventoryIssueButton) els.repairOrderInventoryIssueButton.disabled = Boolean(state.inventoryMaterialSaving) || !selected || !hasQuantity;
      if (els.repairOrderInventoryReturnButton) els.repairOrderInventoryReturnButton.disabled = Boolean(state.inventoryMaterialSaving) || !hasMovement;
    }

    function toggleRepairOrderInventoryPanel() {
      state.repairOrderInventoryOpen = !state.repairOrderInventoryOpen;
      renderRepairOrderInventoryPanel();
      if (state.repairOrderInventoryOpen) {
        if (!state.inventoryLoaded) loadInventoryItems(false, { query: state.repairOrderInventoryQuery });
        const isCurrent = captureCardEditingContext();
        window.setTimeout(() => {
          if (isCurrent() && state.repairOrderInventoryOpen) els.repairOrderInventorySearchInput?.focus({ preventScroll: true });
        }, 0);
      }
    }

    function selectRepairOrderInventoryItem(itemId) {
      const normalizedId = String(itemId || '').trim();
      state.repairOrderInventorySelectedId = normalizedId;
      if (normalizedId) state.inventoryActiveId = normalizedId;
      if (els.repairOrderInventoryQuantityInput && !String(els.repairOrderInventoryQuantityInput.value || '').trim()) {
        els.repairOrderInventoryQuantityInput.value = '1';
      }
      renderRepairOrderInventoryPanel();
    }

    function handleRepairOrderInventorySearchInput() {
      state.repairOrderInventoryQuery = String(els.repairOrderInventorySearchInput?.value || '').trim();
      inventoryAsyncContext('items');
      const context = inventoryAsyncContext('search', captureCardEditingContext());
      renderRepairOrderInventoryPanel();
      if (state.inventorySearchTimer) window.clearTimeout(state.inventorySearchTimer);
      state.inventorySearchTimer = window.setTimeout(() => {
        if (!context.isCurrent()) return;
        state.inventorySearchTimer = null;
        loadInventoryItems(false, { query: state.repairOrderInventoryQuery });
      }, 250);
    }

    function handleRepairOrderInventoryResultsClick(event) {
      const button = event.target instanceof HTMLElement ? event.target.closest('[data-repair-order-inventory-item-id]') : null;
      if (!button || !els.repairOrderInventoryResults?.contains(button)) return;
      selectRepairOrderInventoryItem(button.getAttribute('data-repair-order-inventory-item-id'));
    }
