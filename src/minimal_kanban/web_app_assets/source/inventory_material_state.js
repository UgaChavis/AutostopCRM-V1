    const inventoryMaterialCheckpoints = new Map();
    let inventoryMaterialHold = null;

    function releaseInventoryMaterialControls() {
      if (!inventoryMaterialHold) return;
      inventoryMaterialHold.controls.forEach((disabled, control) => { control.disabled = disabled; });
      inventoryMaterialHold.inert.forEach((inert, root) => { root.inert = inert; });
      inventoryMaterialHold = null;
    }

    function lockInventoryMaterialControls(owner = inventoryMaterialHold?.owner) {
      if (!owner) return;
      if (inventoryMaterialHold?.owner !== owner) {
        releaseInventoryMaterialControls();
        inventoryMaterialHold = { owner, controls: new Map(), inert: new Map() };
      }
      const hold = inventoryMaterialHold;
      for (const root of [els.cardModal, els.repairOrderModal, els.repairOrderPaymentsModal]) {
        for (const control of root?.querySelectorAll?.('input, textarea, select, button') || []) {
          if (!hold.controls.has(control)) hold.controls.set(control, control.disabled);
          control.disabled = true;
        }
      }
      const root = els.repairOrderInventoryResults;
      if (root) {
        if (!hold.inert.has(root)) hold.inert.set(root, Boolean(root.inert));
        root.inert = true;
      }
    }

    function captureInventoryMaterialDraft() {
      const fields = [...(els.repairOrderModal?.querySelectorAll?.('input[id], textarea[id], select[id]') || [])]
        .filter((field) => !els.repairOrderInventoryPanel?.contains(field));
      return {
        fields: fields.map((field) => [field, field.value, field.checked]),
        works: [...(els.repairOrderWorksBody?.children || [])],
        materials: [...(els.repairOrderMaterialsBody?.children || [])],
        before: state.activeCard?.repair_order?.materials || [],
        tags: state.repairOrderTags,
        tagColor: state.repairOrderTagColor,
      };
    }

    function applyInventoryMaterialCard(data, draft, payload) {
      if (!data?.card && !data?.repair_order) return;
      const updatedCard = repairOrderResponseCard(data, data?.repair_order || readRepairOrderFromForm());
      applyRepairOrderCardUpdate(updatedCard, data?.repair_order || {});
      draft.fields.forEach(([field, value, checked]) => { field.value = value; field.checked = checked; });
      state.repairOrderTags = draft.tags;
      state.repairOrderTagColor = draft.tagColor;
      renderRepairOrderTags();
      els.repairOrderWorksBody?.replaceChildren(...draft.works);
      const index = Number(data?.meta?.row_index ?? payload.row_index);
      const returnedRow = els.repairOrderMaterialsBody?.children[index];
      const rows = draft.materials.slice();
      const linkedIndex = payload.movement_id ? rows.findIndex((row) =>
        row.querySelector('[data-repair-order-row-field="inventory_movement_id"]')?.value === payload.movement_id) : -1;
      const draftIndex = payload.movement_id ? (linkedIndex >= 0 ? linkedIndex : index) : Number(payload.row_index ?? index);
      if (returnedRow && Number.isInteger(index) && index >= 0) {
        const original = rows[draftIndex];
        const previous = draft.before.find((row) => row.id === original?.dataset.repairOrderRowId);
        const current = updatedCard?.repair_order?.materials?.[index];
        for (const field of returnedRow.querySelectorAll('[data-repair-order-cell]')) {
          const key = field.dataset.repairOrderCell;
          if (previous && current && (key !== 'quantity' || payload.movement_id)
            && String(previous[key] ?? '') === String(current[key] ?? '')) {
            const raw = original?.querySelector('[data-repair-order-cell="' + key + '"]');
            if (raw) field.value = raw.value;
          }
        }
        rows[draftIndex] = returnedRow;
        els.repairOrderMaterialsBody?.replaceChildren(...rows.filter(Boolean));
      }
      syncRepairOrderTotals();
      syncRepairOrderSaveDirtyState();
      return draftIndex;
    }

    function inventoryMaterialKey(path, body) {
      return JSON.stringify([state.viewerStateGeneration, state.operatorSessionToken, path,
        body.card_id, body.item_id || body.movement_id, body.row_index ?? '', body.quantity ?? '']);
    }

    function inventoryMaterialHasCheckpoint(path, payload) {
      return inventoryMaterialCheckpoints.has(inventoryMaterialKey(path, { ...payload, card_id: String(state.editingId || '') }));
    }

    function pendingInventoryMaterialReturn() {
      const rowIndex = repairOrderInventoryTargetRowIndex();
      return [...inventoryMaterialCheckpoints.values()].find((checkpoint) =>
        checkpoint.path === '/api/return_inventory_movement'
        && checkpoint.owner === state.operatorSessionToken
        && checkpoint.body.card_id === String(state.editingId || '')
        && checkpoint.confirmed?.item?.id === state.repairOrderInventorySelectedId
        && (checkpoint.uiRowIndex ?? checkpoint.confirmed?.meta?.row_index) === rowIndex);
    }

    async function refreshInventoryMaterialItems(context) {
      const pending = loadInventoryItems(false, { query: state.repairOrderInventoryQuery || state.inventoryQuery });
      const token = state.inventoryRequests?.items;
      let timeout;
      const deadline = new Promise((_resolve, reject) => {
        timeout = globalThis.setTimeout(() => {
          if (context.isCurrent() && state.inventoryRequests?.items === token) state.inventoryRequests.items = null;
          reject(new Error('СКЛАД НЕ ОТВЕТИЛ НА ОБНОВЛЕНИЕ. ОПЕРАЦИЯ УЖЕ ЗАПИСАНА; ПОВТОРИТЕ ЧТЕНИЕ.'));
        }, 20000);
      });
      try {
        return await Promise.race([pending, deadline]);
      } finally {
        globalThis.clearTimeout(timeout);
      }
    }

    async function readInventoryMaterialResult(checkpoint, context) {
      const body = checkpoint.body;
      const [cardData, movementsData] = await Promise.all([
        api('/api/get_card?card_id=' + encodeURIComponent(body.card_id)),
        api('/api/list_inventory_movements?card_id=' + encodeURIComponent(body.card_id) + '&limit=500'),
      ]);
      if (!context.isCurrent()) return null;
      if (cardData?.card?.id !== body.card_id || !Array.isArray(movementsData?.movements)) {
        throw new Error('НЕ УДАЛОСЬ ПОДТВЕРДИТЬ ДВИЖЕНИЕ СКЛАДА.');
      }
      const movements = movementsData.movements;
      const materials = cardData.card.repair_order?.materials || [];
      let movement;
      if (checkpoint.confirmed?.movement?.id) {
        movement = movements.find((item) => item.id === checkpoint.confirmed.movement.id);
      } else if (checkpoint.path === '/api/write_off_inventory_item') {
        const nativeIndex = body.row_index;
        const row = materials[nativeIndex];
        const beforeId = checkpoint.before?.repair_order?.materials?.[nativeIndex]?.inventory_movement_id;
        if (row?.inventory_movement_id && row.inventory_movement_id !== beforeId) {
          movement = movements.find((item) => item.id === row.inventory_movement_id && item.kind === 'write_off'
            && item.item_id === body.item_id && item.card_id === body.card_id
            && item.repair_order_row_index === nativeIndex
            && repairOrderParseNumber(item.quantity) === repairOrderParseNumber(body.quantity));
        }
      } else {
        movement = movements.find((item) => item.kind === 'return' && item.related_movement_id === body.movement_id);
      }
      const itemId = movement?.item_id || body.item_id
        || movements.find((item) => item.id === body.movement_id)?.item_id;
      const itemData = itemId ? await api('/api/get_inventory_item?item_id=' + encodeURIComponent(itemId)) : null;
      if (!context.isCurrent()) return null;
      if (itemData?.item?.id !== itemId) throw new Error('НЕ УДАЛОСЬ ОБНОВИТЬ ОСТАТОК СКЛАДА.');
      const rowIndex = movement?.repair_order_row_index ?? body.row_index;
      const result = { ...cardData, item: itemData.item, movement,
        repair_order: cardData.card.repair_order, meta: { row_index: rowIndex } };
      if (movement) {
        checkpoint.confirmed = result;
        checkpoint.uncertain = false;
        checkpoint.retrySafe = false;
      } else {
        checkpoint.retrySafe = Boolean(body.expected_card_updated_at
          && body.expected_card_updated_at === cardData.card.updated_at
          && (checkpoint.path !== '/api/write_off_inventory_item' || (body.expected_updated_at
            && body.expected_updated_at === itemData.item.updated_at)));
      }
      return result;
    }

    async function runInventoryMaterialMutation(path, payload, message) {
      if (state.inventoryMaterialSaving || state.repairOrderMutationRequest
        || state.cardSaveInFlight || state.repairOrderLoading) return;
      const cardContext = captureCardEditingContext();
      let cardId = String(state.editingId || '');
      let expected = state.activeCard?.id === cardId ? state.activeCard.updated_at : undefined;
      let selectedId = state.repairOrderInventorySelectedId;
      let selectedRow = state.repairOrderInventoryRowIndex;
      const context = inventoryAsyncContext('material', () => cardContext()
        && String(state.editingId || '') === cardId
        && state.repairOrderInventorySelectedId === selectedId && state.repairOrderInventoryRowIndex === selectedRow);
      const actorName = state.actor;
      const draft = captureInventoryMaterialDraft();
      let checkpoint = null;
      let checkpointKey = '';
      state.inventoryMaterialSaving = true;
      inventoryAsyncContext('items');
      lockInventoryMaterialControls(context);
      renderRepairOrderInventoryPanel();
      try {
        const preparedId = await requireRepairOrderCardId();
        if (!preparedId || !context.owns() || !cardContext() || (cardId && preparedId !== cardId)) return;
        if (!cardId && state.activeCard?.id === preparedId) expected = state.activeCard.updated_at;
        cardId = String(preparedId);
        if (!context.isCurrent()) return;
        const body = { ...payload, card_id: cardId, expected_card_updated_at: expected || undefined,
          actor_name: actorName, source: 'ui' };
        const key = inventoryMaterialKey(path, body);
        checkpointKey = key;
        checkpoint = inventoryMaterialCheckpoints.get(key);
        const unresolved = [...inventoryMaterialCheckpoints.values()].find((item) => item.uncertain
          && item.owner === state.operatorSessionToken && item.body.card_id === cardId && item !== checkpoint);
        if (unresolved) throw new Error('РЕЗУЛЬТАТ ПРЕДЫДУЩЕЙ СКЛАДСКОЙ ОПЕРАЦИИ НЕ ПОДТВЕРЖДЁН. СВЕРЬТЕ ДВИЖЕНИЯ ПЕРЕД ПОВТОРОМ.');
        if (!checkpoint) {
          checkpoint = { owner: state.operatorSessionToken, path, body,
            before: state.activeCard, confirmed: null, uncertain: false, retrySafe: false };
        }
        let data = null;
        if (checkpoint.confirmed || checkpoint.uncertain) {
          data = await readInventoryMaterialResult(checkpoint, context);
          if (!context.isCurrent()) return;
          if (!checkpoint.confirmed && !checkpoint.retrySafe) {
            throw new Error('РЕЗУЛЬТАТ СКЛАДСКОЙ ОПЕРАЦИИ НЕ ОПРЕДЕЛЁН. ПОВТОРНАЯ ЗАПИСЬ ОСТАНОВЛЕНА.');
          }
        }
        if (!checkpoint.confirmed) {
          inventoryMaterialCheckpoints.set(key, checkpoint);
          const previouslyUncertain = checkpoint.uncertain;
          checkpoint.retrySafe = false;
          const controller = new AbortController();
          const timeout = globalThis.setTimeout(() => controller.abort(), 90000);
          try {
            data = await api(path, { method: 'POST', body: checkpoint.body, signal: controller.signal });
            if (!context.isCurrent()) return;
            if (!data?.movement?.id || !data?.item?.id || data?.card?.id !== cardId
              || data.movement.card_id !== cardId
              || (checkpoint.body.item_id && data.item.id !== checkpoint.body.item_id)
              || (checkpoint.body.movement_id && data.movement.related_movement_id !== checkpoint.body.movement_id)) {
              throw new Error('СЕРВЕР НЕ ПОДТВЕРДИЛ ДВИЖЕНИЕ СКЛАДА.');
            }
            checkpoint.confirmed = data;
            checkpoint.uncertain = false;
          } catch (error) {
            if (previouslyUncertain || !(error?.status >= 400 && error.status < 500 && error.code !== 'invalid_json')) {
              checkpoint.uncertain = true;
            } else inventoryMaterialCheckpoints.delete(key);
            throw error;
          } finally {
            globalThis.clearTimeout(timeout);
          }
        }
        data = data || checkpoint.confirmed;
        if (!context.isCurrent()) return;
        upsertInventoryItem(data.item);
        state.inventoryActiveId = inventoryItemId(data.item);
        state.repairOrderInventorySelectedId = inventoryItemId(data.item);
        selectedId = state.repairOrderInventorySelectedId;
        const rowIndex = applyInventoryMaterialCard(data, draft, payload);
        state.repairOrderInventoryRowIndex = String(rowIndex ?? data?.meta?.row_index ?? payload.row_index ?? selectedRow);
        selectedRow = state.repairOrderInventoryRowIndex;
        checkpoint.uiRowIndex = Number(selectedRow);
        lockInventoryMaterialControls(context);
        await refreshRepairOrdersListAfterMutation();
        if (!context.isCurrent()) return;
        if (!await refreshInventoryMaterialItems(context)) {
          throw new Error('ОПЕРАЦИЯ ЗАПИСАНА, НО СКЛАД НЕ ОБНОВИЛСЯ. ПОВТОРИТЕ ОБНОВЛЕНИЕ.');
        }
        if (!context.isCurrent()) return;
        invalidateInventoryMovements();
        inventoryMaterialCheckpoints.delete(key);
        inventoryStatus(message, false);
        setStatus(message, false);
      } catch (error) {
        if (!context.isCurrent()) return;
        if (checkpoint?.uncertain) {
          try {
            const data = await readInventoryMaterialResult(checkpoint, context);
            if (!context.isCurrent()) return;
            if (data?.item) upsertInventoryItem(data.item);
            if (checkpoint.confirmed) {
              state.inventoryActiveId = inventoryItemId(data.item);
              state.repairOrderInventorySelectedId = inventoryItemId(data.item);
              selectedId = state.repairOrderInventorySelectedId;
              const rowIndex = applyInventoryMaterialCard(data, draft, payload);
              state.repairOrderInventoryRowIndex = String(rowIndex ?? data?.meta?.row_index ?? selectedRow);
              selectedRow = state.repairOrderInventoryRowIndex;
              checkpoint.uiRowIndex = Number(selectedRow);
              lockInventoryMaterialControls(context);
              inventoryStatus(message + ' РЕЗУЛЬТАТ ПОДТВЕРЖДЁН ЧТЕНИЕМ.', false);
              setStatus(message + ' РЕЗУЛЬТАТ ПОДТВЕРЖДЁН ЧТЕНИЕМ.', false);
              invalidateInventoryMovements();
              inventoryMaterialCheckpoints.delete(checkpointKey);
              return;
            }
          } catch (_) {}
        }
        if (context.isCurrent()) {
          inventoryStatus(error.message, true);
          setStatus(error.message, true);
        }
      } finally {
        if (context.owns()) {
          releaseInventoryMaterialControls();
          state.inventoryMaterialSaving = false;
          if (cardContext()) {
            syncRepairOrderEditingState(state.activeCard?.repair_order || {});
            renderRepairOrderInventoryPanel();
          }
        }
      }
    }
