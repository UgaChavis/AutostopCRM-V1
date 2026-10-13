    const inventoryWriteCheckpoints = new Map();
    let inventoryEditHold = null;
    let inventoryFormDraft = null;
    const inventoryDraftInputs = new WeakSet();

    function bindInventoryDraftInputs() {
      const mobile = inventoryFormRefs({ mobile: true });
      const fields = [els.inventoryQuantityInput, els.inventoryReplenishQuantityInput,
        ...['name', 'catalog', 'unit', 'quantity', 'replenishQuantity', 'costPrice', 'salePrice'].map((key) => mobile[key])];
      fields.forEach((field) => {
        if (!field?.addEventListener || inventoryDraftInputs.has(field)) return;
        inventoryDraftInputs.add(field);
        field.addEventListener('input', handleInventoryFormInput);
        field.addEventListener('change', handleInventoryFormInput);
      });
    }

    function inventoryDraftValues(refs = inventoryFormRefs()) {
      return Object.fromEntries(['name', 'catalog', 'unit', 'quantity', 'replenishQuantity', 'costPrice', 'salePrice']
        .map((key) => [key, String(refs[key]?.value ?? '')]));
    }

    function inventoryDraftIsCurrent() {
      return inventoryFormDraft && inventoryFormDraft.viewer === state.viewerStateGeneration
        && inventoryFormDraft.session === state.operatorSessionToken
        && inventoryFormDraft.itemId === String(state.inventoryActiveId || '');
    }

    function rememberInventoryDraft() {
      inventoryFormDraft = { viewer: state.viewerStateGeneration, session: state.operatorSessionToken,
        itemId: String(state.inventoryActiveId || ''), values: inventoryDraftValues() };
    }

    function releaseInventoryEditControls() {
      if (!inventoryEditHold) return;
      inventoryEditHold.controls.forEach((disabled, control) => { control.disabled = disabled; });
      inventoryEditHold.inert.forEach((inert, container) => { container.inert = inert; });
      inventoryEditHold = null;
    }

    function lockInventoryEditControls(owner) {
      if (inventoryEditHold?.owner !== owner) {
        releaseInventoryEditControls();
        inventoryEditHold = { owner, controls: new Map(), inert: new Map() };
      }
      const hold = inventoryEditHold;
      for (const container of [els.inventoryModal, els.mobileAppShell?.querySelector?.('[data-mobile-panel="inventory"]')]) {
        for (const control of container?.querySelectorAll?.('button, input, select, textarea') || []) {
          if (!hold.controls.has(control)) hold.controls.set(control, control.disabled);
          control.disabled = true;
        }
      }
      for (const container of [els.inventoryItemsList, els.mobileInventoryItemsList]) {
        if (!container) continue;
        if (!hold.inert.has(container)) hold.inert.set(container, Boolean(container.inert));
        container.inert = true;
      }
    }

    function inventoryWriteIntent(path, payload) {
      const intent = { ...payload };
      delete intent.expected_updated_at;
      delete intent.item_id;
      // Initial quantity belongs only to creation; the confirmed ID binds its retry.
      if (path === '/api/save_inventory_item') delete intent.quantity;
      return JSON.stringify(intent);
    }

    function inventoryWriteCheckpoint(path, payload) {
      const itemId = String(payload.item_id || '');
      const intent = inventoryWriteIntent(path, payload);
      const sameViewer = (entry) => entry.viewer === state.viewerStateGeneration
        && entry.session === state.operatorSessionToken;
      const entries = [...inventoryWriteCheckpoints.values()].filter(sameViewer);
      const checkpoint = entries.find((entry) => entry.path === path && entry.intent === intent
        && (String(entry.body.item_id || '') === itemId || entry.confirmed?.item?.id === itemId));
      const unresolved = entries.find((entry) => entry.uncertain
        && (entry.body.item_id ? String(entry.body.item_id) === itemId : !itemId)
        && entry !== checkpoint);
      return { checkpoint, unresolved, intent };
    }

    function resetInventoryEditor() {
      releaseInventoryMaterialControls();
      inventoryMaterialCheckpoints.clear();
      releaseInventoryEditControls();
      inventoryWriteCheckpoints.clear();
      inventoryFormDraft = null;
    }

    async function runInventoryEditorWrite(path, payload) {
      let activeId = String(state.inventoryActiveId || '');
      const context = inventoryAsyncContext('save', () => String(state.inventoryActiveId || '') === activeId);
      const match = inventoryWriteCheckpoint(path, payload);
      const checkpoint = match.checkpoint || { viewer: state.viewerStateGeneration,
        session: state.operatorSessionToken, path, intent: match.intent, body: payload,
        confirmed: null, uncertain: false, retrySafe: false };
      rememberInventoryDraft();
      state.inventorySaving = true;
      lockInventoryEditControls(context);
      let completed = false;
      try {
        if (match.unresolved || (checkpoint.uncertain && !checkpoint.retrySafe)) {
          throw new Error('РЕЗУЛЬТАТ ПРЕДЫДУЩЕЙ ЗАПИСИ НЕ ПОДТВЕРЖДЁН. ПОВТОР ОСТАНОВЛЕН; ОБНОВИТЕ СКЛАД И СВЕРЬТЕ ПОЗИЦИЮ.');
        }
        if (!checkpoint.confirmed) {
          inventoryWriteCheckpoints.set(checkpoint, checkpoint);
          const previouslyUncertain = checkpoint.uncertain;
          checkpoint.retrySafe = false;
          const controller = new AbortController();
          const timeout = globalThis.setTimeout(() => controller.abort(), 90000);
          try {
            const data = await api(path, { method: 'POST', body: checkpoint.body, signal: controller.signal });
            if (!context.isCurrent()) return;
            if (!data?.item?.id) throw new Error('СЕРВЕР НЕ ПОДТВЕРДИЛ ЗАПИСЬ ПОЗИЦИИ.');
            checkpoint.confirmed = data;
            checkpoint.uncertain = false;
          } catch (error) {
            if (previouslyUncertain || !(error?.status >= 400 && error.status < 500 && error.code !== 'invalid_json')) {
              checkpoint.uncertain = true;
            } else {
              inventoryWriteCheckpoints.delete(checkpoint);
            }
            throw error;
          } finally {
            globalThis.clearTimeout(timeout);
          }
        }
        if (!context.isCurrent()) return;
        const data = checkpoint.confirmed;
        upsertInventoryItem(data.item);
        activeId = inventoryItemId(data.item);
        state.inventoryActiveId = activeId;
        state.repairOrderInventorySelectedId = activeId;
        inventoryFormDraft.itemId = activeId;
        if (data.movement || path === '/api/replenish_inventory_item') invalidateInventoryMovements();
        if (!await loadInventoryItems(false, { query: state.inventoryQuery })) {
          throw new Error('ПОЗИЦИЯ ЗАПИСАНА, НО СПИСОК НЕ ОБНОВИЛСЯ. ПОВТОРИТЕ ОБНОВЛЕНИЕ.');
        }
        if (!context.isCurrent()) return;
        inventoryWriteCheckpoints.delete(checkpoint);
        completed = true;
        inventoryStatus(path === '/api/replenish_inventory_item' ? 'ОСТАТОК ПОПОЛНЕН.'
          : (data.meta?.created ? 'ПОЗИЦИЯ ДОБАВЛЕНА.' : 'ПОЗИЦИЯ СОХРАНЕНА.'), false);
      } catch (error) {
        if (!context.isCurrent()) return;
        if (checkpoint.uncertain || match.unresolved || error?.status === 409) {
          try {
            const targetId = checkpoint.body.item_id;
            const fresh = targetId
              ? await api('/api/get_inventory_item?item_id=' + encodeURIComponent(targetId))
              : await readInventoryItems('');
            if (!context.isCurrent()) return;
            if (fresh?.item) {
              upsertInventoryItem(fresh.item);
              checkpoint.retrySafe = Boolean(checkpoint.uncertain && checkpoint.body.expected_updated_at
                && fresh.item.updated_at === checkpoint.body.expected_updated_at);
            } else if (Array.isArray(fresh?.items)) {
              state.inventoryItems = fresh.items;
              state.inventoryLoaded = true;
            }
          } catch (_) { /* Keep the unresolved write and draft if readback also fails. */ }
        }
        if (!context.isCurrent()) return;
        inventoryStatus(error.message, true);
        setStatus(error.message, true);
      } finally {
        if (context.owns()) {
          state.inventorySaving = false;
          releaseInventoryEditControls();
          if (completed) inventoryFormDraft = null;
          renderInventory();
        }
      }
    }
