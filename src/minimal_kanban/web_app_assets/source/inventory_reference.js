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
