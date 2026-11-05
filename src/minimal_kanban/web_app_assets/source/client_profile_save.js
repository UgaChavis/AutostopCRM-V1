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
          const expected = key === 'phones' && !value.some((phone) => /[^\x00-\x7f]/.test(String(phone))) ? value.filter((phone) => {
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

    function clientProfileHasRevision(client) {
      const version = client?.updated_at;
      return typeof version === 'string' && Boolean(version) && version === version.trim();
    }

    function clientProfileDuplicateIdentity(payload) {
      const text = (key) => String(payload?.[key] || '').trim();
      const name = payload?.client_type === 'person'
        ? [text('last_name'), text('first_name'), text('middle_name')].filter(Boolean).join(' ') || text('display_name')
        : text('short_name') || text('legal_name') || text('display_name');
      return JSON.stringify([name, payload?.phones || []]);
    }

    async function saveClientProfile() {
      updateClientSaveButtonState();
      if (els.clientSaveButton?.disabled) return;
      if (protectClientDraft(true)) return;
      const payload = readClientFormPayload();
      const context = beginClientMutation({ allowNew: true });
      if (!context) return;
      const ownerKey = JSON.stringify([state.viewerStateGeneration, state.operatorSessionToken, context.clientId]);
      const effectKey = JSON.stringify([context.clientId, context.clientId ? null : payload]);
      const previous = state.clientProfileSaveCheckpoint;
      const duplicateCreate = !context.clientId && previous?.ownerKey === ownerKey
        && previous.profile === state.clientsDesktopProfile && previous.receipt?.meta?.created === false
        && (previous.effect || clientProfileDuplicateIdentity(previous.payload) === clientProfileDuplicateIdentity(payload));
      let checkpoint = duplicateCreate ? previous : state.clientProfileUncertainEffects.get(effectKey) || previous;
      const confirmedCreate = !context.clientId && checkpoint?.receipt && checkpoint.effect && checkpoint.ownerKey === ownerKey;
      if (!duplicateCreate && !confirmedCreate && (!checkpoint || checkpoint.effectKey !== effectKey || (!checkpoint.uncertain && (checkpoint.ownerKey !== ownerKey || checkpoint.profile !== state.clientsDesktopProfile)))) {
        checkpoint = { ownerKey, effectKey, profile: state.clientsDesktopProfile, clientId: context.clientId,
          expectedUpdatedAt: context.clientId ? state.clientsDraftRevision?.version : null,
          uncertain: false, payload };
      }
      state.clientProfileSaveCheckpoint = checkpoint;
      const wasExisting = Boolean(context.clientId);
      try {
        let data;
        if (!wasExisting && checkpoint.effect) {
          beginCardClientCreate(checkpoint.ownerKey === ownerKey ? checkpoint.effect : null);
        }
        if (duplicateCreate) {
          if (checkpoint.effect && !finishCardClientCreate(checkpoint.effect)) throw new Error('НЕ УДАЛОСЬ СНЯТЬ ЗАЩИТУ СОЗДАНИЯ КЛИЕНТА. ПОВТОР ОСТАНОВЛЕН.');
          checkpoint.effect = null;
          checkpoint.uncertain = false;
          state.clientProfileUncertainEffects.delete(checkpoint.effectKey);
          setStatus('НАЙДЕН СУЩЕСТВУЮЩИЙ КЛИЕНТ. ВВЕДЁННЫЕ ДАННЫЕ НЕ СОХРАНЕНЫ; ПРОВЕРЬТЕ ПРОФИЛЬ.', true);
          return;
        }
        if (confirmedCreate) {
          if (!finishCardClientCreate(checkpoint.effect)) throw new Error('НЕ УДАЛОСЬ СНЯТЬ ЗАЩИТУ СОЗДАНИЯ КЛИЕНТА. ПОВТОР ОСТАНОВЛЕН.');
          checkpoint.effect = null;
          checkpoint.uncertain = false;
          state.clientProfileUncertainEffects.delete(checkpoint.effectKey);
          if (JSON.stringify(checkpoint.payload) !== JSON.stringify(payload)) {
            state.clientProfileSaveCheckpoint = null;
            setStatus(`ПРЕДЫДУЩИЙ КЛИЕНТ ${checkpoint.receipt.meta.created ? 'СОЗДАН' : 'НАЙДЕН'}. ТЕКУЩИЙ ЧЕРНОВИК СОХРАНЁН; ПРОВЕРЬТЕ СПИСОК КЛИЕНТОВ.`, false);
            return;
          }
          data = checkpoint.receipt;
        } else if (checkpoint.uncertain) {
          if (!checkpoint.clientId) {
            await clientMutationRead('/api/search_clients?query=' + encodeURIComponent(checkpoint.payload.display_name || checkpoint.payload.phone || '') + '&limit=6');
            throw new Error('РЕЗУЛЬТАТ СОЗДАНИЯ КЛИЕНТА НЕ ПОДТВЕРЖДЁН. ПРОВЕРЬТЕ СПИСОК КЛИЕНТОВ. ПОВТОРНОЕ СОЗДАНИЕ ОСТАНОВЛЕНО; ЧЕРНОВИК СОХРАНЁН.');
          }
          data = await clientMutationRead('/api/get_client?client_id=' + encodeURIComponent(checkpoint.clientId));
          if (!context.isCurrent()) return;
          if (!clientProfileReadValid(data, checkpoint.clientId) || !clientProfileHasRevision(data.client)
              || !clientProfilePayloadMatches(data.client, checkpoint.payload)) {
            throw new Error('СОХРАНЕНИЕ КЛИЕНТА НЕ ПОДТВЕРЖДЕНО ПРИ ЧТЕНИИ. ЧЕРНОВИК ОСТАЛСЯ В ФОРМЕ.');
          }
          const hasNewDraft = JSON.stringify(checkpoint.payload) !== JSON.stringify(payload);
          if (hasNewDraft && !state.clientsDraftRevision?.conflict) {
            checkpoint.expectedUpdatedAt = data.client.updated_at;
            state.clientsDraftRevision = { version: data.client.updated_at, conflict: false };
          }
          checkpoint.uncertain = false;
          state.clientProfileUncertainEffects.delete(effectKey);
          if (hasNewDraft) {
            throw new Error('ПРЕДЫДУЩЕЕ СОХРАНЕНИЕ ПОДТВЕРЖДЕНО. ТЕКУЩИЙ ЧЕРНОВИК ОСТАЛСЯ В ФОРМЕ; СОХРАНИТЕ ЕГО ОТДЕЛЬНО.');
          }
        } else {
          checkpoint.payload = payload;
          if (context.clientId && (typeof checkpoint.expectedUpdatedAt !== 'string' || !checkpoint.expectedUpdatedAt
              || checkpoint.expectedUpdatedAt !== checkpoint.expectedUpdatedAt.trim())) {
            throw new Error('ВЕРСИЯ КЛИЕНТА НЕ ПОДТВЕРЖДЕНА. ЧЕРНОВИК СОХРАНЁН В ФОРМЕ.');
          }
          if (!wasExisting) checkpoint.effect = beginCardClientCreate();
          try {
            data = await cardPersistWrite(context.clientId ? '/api/update_client' : '/api/create_client', context.clientId
              ? { client_id: context.clientId, ...payload, expected_updated_at: checkpoint.expectedUpdatedAt } : payload);
          } catch (error) {
            if (!context.isCurrent()) return;
            if (!wasExisting) {
              if (cardClientCreateRejected(error) && finishCardClientCreate(checkpoint.effect)) {
                checkpoint.effect = null;
                throw error;
              }
              checkpoint.uncertain = true;
              state.clientProfileUncertainEffects.set(effectKey, checkpoint);
              throw new Error('РЕЗУЛЬТАТ СОЗДАНИЯ КЛИЕНТА НЕ ПОДТВЕРЖДЁН. ПРОВЕРЬТЕ СПИСОК КЛИЕНТОВ. ПОВТОРНОЕ СОЗДАНИЕ ОСТАНОВЛЕНО; ЧЕРНОВИК СОХРАНЁН.');
            }
            if (cardPersistErrorIsDefinite(error)) throw error;
            checkpoint.uncertain = true;
            state.clientProfileUncertainEffects.set(effectKey, checkpoint);
            if (!checkpoint.clientId) {
              await clientMutationRead('/api/search_clients?query=' + encodeURIComponent(payload.display_name || payload.phone || '') + '&limit=6');
              throw new Error('РЕЗУЛЬТАТ СОЗДАНИЯ КЛИЕНТА НЕ ПОДТВЕРЖДЁН. ПРОВЕРЬТЕ СПИСОК КЛИЕНТОВ. ПОВТОРНОЕ СОЗДАНИЕ ОСТАНОВЛЕНО; ЧЕРНОВИК СОХРАНЁН.');
            }
            data = await clientMutationRead('/api/get_client?client_id=' + encodeURIComponent(checkpoint.clientId));
            if (!context.isCurrent()) return;
            if (!clientProfileReadValid(data, checkpoint.clientId) || !clientProfileHasRevision(data.client)
                || !clientProfilePayloadMatches(data.client, payload)) {
              throw new Error('СОХРАНЕНИЕ КЛИЕНТА НЕ ПОДТВЕРЖДЕНО ПРИ ЧТЕНИИ. ЧЕРНОВИК ОСТАЛСЯ В ФОРМЕ.');
            }
            checkpoint.uncertain = false;
            state.clientProfileUncertainEffects.delete(effectKey);
          }
        }
        if (!context.isCurrent()) return;
        const savedClientId = typeof data?.client?.id === 'string' ? data.client.id : '';
        if (!savedClientId || savedClientId !== savedClientId.trim() || (context.clientId && savedClientId !== context.clientId)
            || (!wasExisting && data?.meta?.created === false && (data.meta.duplicate !== true || data.meta.duplicate_of !== savedClientId))
            || (!wasExisting && (!clientProfileHasRevision(data.client) || (!checkpoint.clientId && typeof data.meta?.created !== 'boolean')
              || !Object.entries(payload).every(([key, value]) => Array.isArray(value) ? Array.isArray(data.client[key]) : typeof data.client[key] === 'string')))) { checkpoint.uncertain = true; state.clientProfileUncertainEffects.set(effectKey, checkpoint); throw new Error('СЕРВЕР НЕ ПОДТВЕРДИЛ КЛИЕНТА. ЧЕРНОВИК ОСТАЛСЯ В ФОРМЕ.'); }
        checkpoint.clientId = savedClientId;
        if (!wasExisting && checkpoint.effect) {
          checkpoint.receipt = data;
          checkpoint.uncertain = true;
          state.clientProfileUncertainEffects.set(effectKey, checkpoint);
          if (!finishCardClientCreate(checkpoint.effect)) throw new Error('НЕ УДАЛОСЬ СНЯТЬ ЗАЩИТУ СОЗДАНИЯ КЛИЕНТА. ПОВТОР ОСТАНОВЛЕН.');
          checkpoint.effect = null;
          checkpoint.uncertain = false;
          state.clientProfileUncertainEffects.delete(effectKey);
        }
        if (!wasExisting && data?.meta?.created === false) {
          setStatus('НАЙДЕН СУЩЕСТВУЮЩИЙ КЛИЕНТ. ВВЕДЁННЫЕ ДАННЫЕ НЕ СОХРАНЕНЫ; ПРОВЕРЬТЕ ПРОФИЛЬ.', true);
          return;
        }
        state.clientsActiveId = savedClientId;
        setStatus(wasExisting ? 'КЛИЕНТ СОХРАНЕН.' : data.meta.created ? 'КЛИЕНТ СОЗДАН.' : 'КЛИЕНТ НАЙДЕН.', false);
        await loadClients({ openModal: false });
        if (context.ownsRequest() && String(state.clientsActiveId || '').trim() === savedClientId) await selectClient(savedClientId);
      } catch (error) {
        if (context.isCurrent()) setStatus(error.message, true);
      } finally {
        finishClientMutation(context);
      }
    }
