    // @include card_save_controls.js

    function cardPersistPayloadKey(payload) {
      const { expected_updated_at, deadline, ...content } = payload;
      return JSON.stringify(content);
    }

    function cardPersistPayloadMatches(card, payload) {
      if (!card) return false;
      return ['title', 'vehicle', 'description', 'column'].every((key) => String(card[key] || '') === String(payload[key] || ''))
        && JSON.stringify(normalizeDraftTags(card.tag_items || card.tags || [])) === JSON.stringify(normalizeDraftTags(payload.tags || []))
        && Object.entries(payload.vehicle_profile || {}).every(([key, value]) =>
          JSON.stringify(card.vehicle_profile?.[key]) === JSON.stringify(value));
    }

    function cardPersistErrorIsDefinite(error) {
      return error?.status >= 400 && error.status < 500 && ![408, 429].includes(error.status) && error.code !== 'invalid_json';
    }

    async function cardPersistWrite(path, body) {
      const controller = new AbortController();
      const timeoutId = window.setTimeout(() => controller.abort(), 45000);
      try {
        return await api(path, { method: 'POST', body, signal: controller.signal });
      } finally {
        window.clearTimeout(timeoutId);
      }
    }

    const cardLinkEffects = new Set();

    function requireCardLinkSettled(cardId, isCurrent = null) {
      if (isCurrent && !isCurrent()) return '';
      const key = 'autostop:card-link:' + JSON.stringify(cardId);
      try {
        if (cardLinkEffects.has(key) || window.sessionStorage.getItem(key) !== null) {
          throw Object.assign(new Error('РЕЗУЛЬТАТ ПРИВЯЗКИ НЕ ПОДТВЕРЖДЁН. ПРОВЕРЬТЕ КАРТОЧКУ; ПОВТОР ОСТАНОВЛЕН, ЧЕРНОВИК СОХРАНЁН.'), { linkUncertain: true });
        }
      } catch (error) {
        if (!error?.linkUncertain) error = Object.assign(new Error('НЕВОЗМОЖНО ПРОВЕРИТЬ СОСТОЯНИЕ ПРИВЯЗКИ. ЗАПИСЬ ОСТАНОВЛЕНА, ЧЕРНОВИК СОХРАНЁН.'), { localValidation: true });
        if (isCurrent) { if (isCurrent()) setStatus(error.message, true); return ''; }
        throw error;
      }
      return key;
    }

    async function cardLinkWrite(body, beforeVehicleId, isCurrent) {
      if (!isCurrent()) return null;
      const key = requireCardLinkSettled(body.card_id);
      const blocked = () => Object.assign(new Error('РЕЗУЛЬТАТ ПРИВЯЗКИ НЕ ПОДТВЕРЖДЁН. ПРОВЕРЬТЕ КАРТОЧКУ; ПОВТОР ОСТАНОВЛЕН, ЧЕРНОВИК СОХРАНЁН.'), { linkUncertain: true });
      try {
        cardLinkEffects.add(key);
        window.sessionStorage.setItem(key, '1');
        if (window.sessionStorage.getItem(key) !== '1') throw new Error('storage');
      } catch (error) {
        if (error?.linkUncertain) throw error;
        cardLinkEffects.delete(key); // No POST has started; a partially written storage marker stays intact.
        throw Object.assign(new Error('НЕВОЗМОЖНО СОХРАНИТЬ ЗАЩИТУ ПРИВЯЗКИ. ЗАПИСЬ ОСТАНОВЛЕНА.'), { localValidation: true });
      }
      const release = () => {
        try {
          window.sessionStorage.removeItem(key);
          if (window.sessionStorage.getItem(key) !== null) return false;
          cardLinkEffects.delete(key);
          return true;
        } catch (_) {
          try { window.sessionStorage.setItem(key, '1'); } catch (_) { /* Keep the in-memory block. */ }
          return false;
        }
      };
      try {
        const data = await cardPersistWrite('/api/link_card_to_client', body);
        const card = data?.card, version = card?.updated_at, vehicleId = card?.client_vehicle_id;
        if (!mobileCardHasRequiredFields(card) || card.id !== body.card_id || card.client_id !== body.client_id
            || typeof version !== 'string' || !version || version !== version.trim() || version.length > 80
            || typeof vehicleId !== 'string' || vehicleId !== vehicleId.trim()
            || (body.client_vehicle_id && vehicleId !== body.client_vehicle_id)
            || (body.create_vehicle_from_card && (!vehicleId || vehicleId === beforeVehicleId))) throw blocked();
        if (!release()) throw blocked();
        return data;
      } catch (error) {
        if (error?.status >= 400 && error.status < 500 && ![408, 429].includes(error.status)
            && ['validation_error', 'card_update_conflict', 'client_update_conflict', 'client_ambiguous', 'client_vehicle_ambiguous', 'client_vehicle_conflict', 'client_vehicle_id_conflict', 'client_vehicle_reference_conflict', 'not_found'].includes(error.code)
            && release()) throw error;
        throw blocked();
      }
    }

    function adoptPersistedCard(data, payload) {
      const card = data?.card;
      if (!card?.id) return;
      state.boardMutationGeneration += 1;
      const full = data?.meta?.response_mode !== 'compact';
      const activeCard = full ? card : {
        ...(state.activeCard || {}), ...card, description: payload.description,
        tags: (payload.tags || []).map((tag) => tag.label), tag_items: payload.tags || [],
        vehicle_profile: payload.vehicle_profile,
      };
      state.editingId = card.id;
      state.activeCardIsFull = true;
      applySavedCardLocalPatch(card, { cardIsFull: full, payload });
      // Board projection uses a description preview; the editor retains its full
      // confirmed content and revision even when that projection is replaced.
      state.activeCard = activeCard;
      rememberCardModalCleanState(payload);
    }

    async function reconcileCardPersist(checkpoint, isCurrent) {
      if (!checkpoint.cardId) {
        // create_card has no idempotency/receipt contract. Read the board but
        // never guess an ID from a matching title or repeat an uncertain create.
        await api('/api/get_board_snapshot?compact=1&include_archive=0');
        throw new Error('РЕЗУЛЬТАТ СОЗДАНИЯ КАРТОЧКИ НЕ ПОДТВЕРЖДЁН. ПРОВЕРЬТЕ ДОСКУ ПЕРЕД ПОВТОРНЫМ СОЗДАНИЕМ. ЧЕРНОВИК СОХРАНЁН В ОКНЕ.');
      }
      const data = await api('/api/get_card?card_id=' + encodeURIComponent(checkpoint.cardId));
      if (!isCurrent()) return null;
      const card = data?.card;
      if (checkpoint.uncertain === 'link') throw new Error('ПРИВЯЗКА НЕ ПОДТВЕРЖДЕНА. ПРОВЕРЬТЕ КАРТОЧКУ; ПОВТОР ОСТАНОВЛЕН, ЧЕРНОВИК СОХРАНЁН.');
      const matches = cardPersistPayloadMatches(card, checkpoint.payload);
      if (!matches) {
        // A guarded update can be retried only after this read confirms the old
        // version. A changed version retains the draft and native CAS conflict.
        if (checkpoint.uncertain === 'card' && card?.updated_at === checkpoint.payload.expected_updated_at) {
          checkpoint.uncertain = '';
        }
        throw new Error('СОХРАНЕНИЕ НЕ ПОДТВЕРЖДЕНО ПРИ ПРОВЕРКЕ КАРТОЧКИ. ЧЕРНОВИК ОСТАЛСЯ В ОКНЕ.');
      }
      const changed = checkpoint.data?.meta?.changed === true || Boolean(card?.updated_at
        && card.updated_at !== (checkpoint.data?.card?.updated_at || checkpoint.payload.expected_updated_at));
      checkpoint.uncertain = '';
      checkpoint.data = { ...data, meta: { ...data?.meta, changed } };
      adoptPersistedCard(checkpoint.data, checkpoint.payload);
      return checkpoint.data;
    }

    async function persistCardPayload(payload) {
      const isCurrent = captureCardSaveWorkspace();
      if (state.editingId) requireCardLinkSettled(state.editingId);
      const clientId = state.pendingCardClientId;
      const clientVehicleId = state.pendingCardClientVehicleId || '';
      const createVehicle = Boolean(state.pendingCreateClientVehicleFromCard);
      let checkpoint = state.cardPersistCheckpoint;
      const key = cardPersistPayloadKey(payload);
      const retryCreatedLink = checkpoint?.created && checkpoint.cardId === state.editingId && checkpoint.data && checkpoint.key === key
        && checkpoint.linkBody?.client_id === clientId && checkpoint.linkBody?.client_vehicle_id === clientVehicleId
        && checkpoint.linkBody?.create_vehicle_from_card === createVehicle;
      if (state.editingId) {
        const active = state.activeCard;
        if (active?.id !== state.editingId || typeof active.client_id !== 'string' || typeof active.client_vehicle_id !== 'string'
            || (!retryCreatedLink && (active.client_id !== (clientId || '')
            || active.client_vehicle_id !== clientVehicleId || createVehicle))) {
          throw new Error('КЛИЕНТ ИЛИ АВТОМОБИЛЬ НЕ ПОДТВЕРЖДЕНЫ В КАРТОЧКЕ. ПРОВЕРЬТЕ СВЯЗЬ; ЧЕРНОВИК СОХРАНЁН.');
        }
      }
      if (checkpoint?.uncertain) {
        await reconcileCardPersist(checkpoint, isCurrent);
        if (!isCurrent()) return null;
      }
      if (!checkpoint?.data || checkpoint.key !== key) {
        const cardId = state.editingId || '';
        checkpoint = { cardId, key, payload, data: null, uncertain: '', linkBody: null, created: !cardId };
        state.cardPersistCheckpoint = checkpoint;
        let data;
        try {
          data = await cardPersistWrite(cardId ? '/api/update_card' : '/api/create_card', cardId
            ? { card_id: cardId, ...payload, response_mode: 'compact' } : payload);
        } catch (error) {
          if (!isCurrent()) return null;
          if (cardPersistErrorIsDefinite(error)) throw error;
          checkpoint.uncertain = 'card';
          await reconcileCardPersist(checkpoint, isCurrent);
          data = checkpoint.data;
        }
        if (!isCurrent()) return null;
        if (!data?.card?.id) {
          checkpoint.uncertain = 'card';
          throw new Error('СЕРВЕР НЕ ПОДТВЕРДИЛ ID КАРТОЧКИ. ЧЕРНОВИК ОСТАЛСЯ В ОКНЕ.');
        }
        checkpoint.cardId = data.card.id;
        checkpoint.data = data;
        adoptPersistedCard(data, payload);
      }
      let data = checkpoint.data;
      const cardId = checkpoint.cardId;
      const persistedClientId = data.card.client_id ?? state.activeCard?.client_id ?? '';
      if (cardId && clientId && persistedClientId !== clientId) {
        const version = data.card.updated_at;
        if (typeof version !== 'string' || !version || version !== version.trim() || version.length > 80) {
          throw new Error('ВЕРСИЯ СОХРАНЁННОЙ КАРТОЧКИ НЕ ПОДТВЕРЖДЕНА. ПРИВЯЗКА НЕ ВЫПОЛНЕНА.');
        }
        checkpoint.linkBody = {
          card_id: cardId, client_id: clientId, client_vehicle_id: clientVehicleId,
          expected_card_updated_at: version,
          create_vehicle_from_card: createVehicle, sync_vehicle_fields: true,
          sync_fields: true, overwrite_card_fields: false,
        };
        checkpoint.beforeVehicleId = data.card.client_vehicle_id || '';
        try {
          const linked = await cardLinkWrite(checkpoint.linkBody, checkpoint.beforeVehicleId, isCurrent);
          if (!isCurrent()) return null;
          data = { ...linked, meta: { ...linked?.meta, changed: data?.meta?.changed === true || linked?.meta?.changed === true } };
          checkpoint.data = data;
          checkpoint.linkBody = null;
          adoptPersistedCard(data, payload);
        } catch (error) {
          if (!isCurrent()) return null;
          if (error?.localValidation) throw error;
          if (error?.linkUncertain || !cardPersistErrorIsDefinite(error)) {
            checkpoint.uncertain = 'link';
            throw error;
          }
          throw new Error('КАРТОЧКА СОХРАНЕНА, НО ПРИВЯЗКА КЛИЕНТА НЕ ЗАВЕРШЕНА. ' + error.message);
        }
      }
      return data;
    }
