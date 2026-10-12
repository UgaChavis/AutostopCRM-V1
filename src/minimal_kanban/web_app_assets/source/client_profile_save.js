    async function clientMutationRead(path) {
      const controller = new AbortController();
      const timeoutId = window.setTimeout(() => controller.abort(), 45000);
      try {
        return await api(path, { signal: controller.signal });
      } finally {
        window.clearTimeout(timeoutId);
      }
    }

    function clientProfilePayloadMatches(client, payload) {
      return Boolean(client) && Object.entries(payload).every(([key, value]) => {
        if (Array.isArray(value)) {
          // Readback reflects native phone deduplication; the submitted body stays unchanged.
          const seen = new Set();
          const expected = key === 'phones' ? value.filter((phone) => {
            const digits = String(phone).replace(/\D+/g, '');
            const identity = digits.length >= 10 ? '7' + digits.slice(-10) : digits || String(phone).toLowerCase();
            if (seen.has(identity)) return false;
            seen.add(identity);
            return true;
          }) : value;
          return JSON.stringify(client[key]) === JSON.stringify(expected);
        }
        return String(client[key] || '').trim() === String(value || '').trim();
      });
    }

    async function saveClientProfile() {
      updateClientSaveButtonState();
      if (els.clientSaveButton?.disabled) return;
      const payload = readClientFormPayload();
      const context = beginClientMutation({ allowNew: true });
      if (!context) return;
      const ownerKey = JSON.stringify([state.viewerStateGeneration, state.operatorSessionToken, context.clientId, state.clientsProfileRequestSeq || 0]);
      let checkpoint = state.clientProfileSaveCheckpoint;
      if (!checkpoint || checkpoint.ownerKey !== ownerKey) {
        checkpoint = { ownerKey, clientId: context.clientId, uncertain: false, payload };
        state.clientProfileSaveCheckpoint = checkpoint;
      }
      const wasExisting = Boolean(context.clientId);
      try {
        let data;
        if (checkpoint.uncertain) {
          if (!checkpoint.clientId) {
            await clientMutationRead('/api/search_clients?query=' + encodeURIComponent(checkpoint.payload.display_name || checkpoint.payload.phone || '') + '&limit=6');
            throw new Error('РЕЗУЛЬТАТ СОЗДАНИЯ КЛИЕНТА НЕ ПОДТВЕРЖДЁН. ПРОВЕРЬТЕ СПИСОК КЛИЕНТОВ. ПОВТОРНОЕ СОЗДАНИЕ ОСТАНОВЛЕНО; ЧЕРНОВИК СОХРАНЁН.');
          }
          data = await clientMutationRead('/api/get_client?client_id=' + encodeURIComponent(checkpoint.clientId));
          if (!context.isCurrent()) return;
          if (!clientProfilePayloadMatches(data?.client, checkpoint.payload)) {
            throw new Error('СОХРАНЕНИЕ КЛИЕНТА НЕ ПОДТВЕРЖДЕНО ПРИ ЧТЕНИИ. ЧЕРНОВИК ОСТАЛСЯ В ФОРМЕ.');
          }
          checkpoint.uncertain = false;
          if (JSON.stringify(checkpoint.payload) !== JSON.stringify(payload)) {
            throw new Error('ПРЕДЫДУЩЕЕ СОХРАНЕНИЕ ПОДТВЕРЖДЕНО. ТЕКУЩИЙ ЧЕРНОВИК ОСТАЛСЯ В ФОРМЕ; СОХРАНИТЕ ЕГО ОТДЕЛЬНО.');
          }
        } else {
          checkpoint.payload = payload;
          try {
            data = await cardPersistWrite(context.clientId ? '/api/update_client' : '/api/create_client', context.clientId
              ? { client_id: context.clientId, ...payload } : payload);
          } catch (error) {
            if (!context.isCurrent()) return;
            if (cardPersistErrorIsDefinite(error)) throw error;
            checkpoint.uncertain = true;
            if (!checkpoint.clientId) {
              await clientMutationRead('/api/search_clients?query=' + encodeURIComponent(payload.display_name || payload.phone || '') + '&limit=6');
              throw new Error('РЕЗУЛЬТАТ СОЗДАНИЯ КЛИЕНТА НЕ ПОДТВЕРЖДЁН. ПРОВЕРЬТЕ СПИСОК КЛИЕНТОВ. ПОВТОРНОЕ СОЗДАНИЕ ОСТАНОВЛЕНО; ЧЕРНОВИК СОХРАНЁН.');
            }
            data = await clientMutationRead('/api/get_client?client_id=' + encodeURIComponent(checkpoint.clientId));
            if (!context.isCurrent()) return;
            if (!clientProfilePayloadMatches(data?.client, payload)) {
              throw new Error('СОХРАНЕНИЕ КЛИЕНТА НЕ ПОДТВЕРЖДЕНО ПРИ ЧТЕНИИ. ЧЕРНОВИК ОСТАЛСЯ В ФОРМЕ.');
            }
            checkpoint.uncertain = false;
          }
        }
        if (!context.isCurrent()) return;
        const savedClientId = String(data?.client?.id || context.clientId || '').trim();
        if (!savedClientId) { checkpoint.uncertain = true; throw new Error('СЕРВЕР НЕ ПОДТВЕРДИЛ ID КЛИЕНТА. ЧЕРНОВИК ОСТАЛСЯ В ФОРМЕ.'); }
        checkpoint.clientId = savedClientId;
        state.clientsActiveId = savedClientId;
        setStatus(wasExisting ? 'КЛИЕНТ СОХРАНЕН.' : 'КЛИЕНТ СОЗДАН.', false);
        await loadClients({ openModal: false });
        if (context.ownsRequest() && String(state.clientsActiveId || '').trim() === savedClientId) await selectClient(savedClientId);
      } catch (error) {
        if (context.isCurrent()) setStatus(error.message, true);
      } finally {
        finishClientMutation(context);
      }
    }
