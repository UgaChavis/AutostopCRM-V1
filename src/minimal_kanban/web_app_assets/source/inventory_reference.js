    function inventoryAsyncContext(key, scope = () => true) {
      const requests = state.inventoryRequests || (state.inventoryRequests = {});
      const token = {};
      const generation = state.viewerStateGeneration;
      const session = state.operatorSessionToken;
      requests[key] = token;
      const owns = () => state.inventoryRequests === requests && requests[key] === token
        && state.viewerStateGeneration === generation && state.operatorSessionToken === session;
      return { owns, isCurrent: () => owns() && scope() };
    }

    function readInventoryItems(query = null) {
      const requestedQuery = query === null ? String(state.inventoryQuery || '').trim() : String(query || '').trim();
      return requestedQuery
        ? api('/api/search_inventory_items', {
          method: 'POST', body: { query: requestedQuery, limit: 200 },
        })
        : api('/api/list_inventory_items?limit=200');
    }

    function prepareInventoryModal() {
      maybeOpenModal(els.inventoryModal, true);
      const entry = state.modalStack.find((item) => item.key === 'inventory');
      const workspace = els.inventoryModal.querySelector('.inventory-workspace');
      const status = els.inventoryStatusLine;
      const loadingText = 'ЗАГРУЖАЮ СКЛАД...';
      workspace.inert = true;
      if (status) status.textContent = loadingText;
      const modal = inventoryAsyncContext('modal', () => state.modalStack.includes(entry)
        && els.inventoryModal.classList.contains('is-open'));
      return {
        ...inventoryAsyncContext('items', modal.isCurrent),
        promise: readInventoryItems(),
        finish: () => {
          if (!modal.isCurrent()) return;
          workspace.inert = false;
          if (status?.textContent === loadingText) status.textContent = '';
        },
        onLoadError: (error) => {
          if (!modal.isCurrent() || !status) return;
          status.textContent = error.message;
          status.dataset.tone = 'error';
        },
      };
    }
