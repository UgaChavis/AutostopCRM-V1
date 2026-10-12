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
      return error?.status >= 400 && error.status < 500 && error.code !== 'invalid_json';
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
      const link = checkpoint.linkBody;
      const matches = checkpoint.uncertain === 'link'
        ? card?.client_id === link.client_id && (!link.client_vehicle_id || card.client_vehicle_id === link.client_vehicle_id)
          && (!link.create_vehicle_from_card || Boolean(card.client_vehicle_id && card.client_vehicle_id !== checkpoint.beforeVehicleId))
        : cardPersistPayloadMatches(card, checkpoint.payload);
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
      if (link) checkpoint.linkBody = null;
      checkpoint.data = { ...data, meta: { ...data?.meta, changed } };
      adoptPersistedCard(checkpoint.data, checkpoint.payload);
      return checkpoint.data;
    }

    async function persistCardPayload(payload) {
      const isCurrent = captureCardSaveWorkspace();
      const clientId = state.pendingCardClientId;
      const clientVehicleId = state.pendingCardClientVehicleId || '';
      const createVehicle = Boolean(state.pendingCreateClientVehicleFromCard);
      let checkpoint = state.cardPersistCheckpoint;
      if (checkpoint?.uncertain) {
        await reconcileCardPersist(checkpoint, isCurrent);
        if (!isCurrent()) return null;
      }
      const key = cardPersistPayloadKey(payload);
      if (!checkpoint?.data || checkpoint.key !== key) {
        const cardId = state.editingId || '';
        checkpoint = { cardId, key, payload, data: null, uncertain: '', linkBody: null };
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
      if (cardId && clientId && data.card.client_id !== clientId) {
        checkpoint.linkBody = {
          card_id: cardId, client_id: clientId, client_vehicle_id: clientVehicleId,
          create_vehicle_from_card: createVehicle, sync_vehicle_fields: true,
          sync_fields: true, overwrite_card_fields: false,
        };
        checkpoint.beforeVehicleId = data.card.client_vehicle_id || '';
        try {
          const linked = await cardPersistWrite('/api/link_card_to_client', checkpoint.linkBody);
          if (!isCurrent()) return null;
          data = { ...linked, meta: { ...linked?.meta, changed: data?.meta?.changed === true || linked?.meta?.changed === true } };
          checkpoint.data = data;
          checkpoint.linkBody = null;
          adoptPersistedCard(data, payload);
        } catch (error) {
          if (!isCurrent()) return null;
          if (!cardPersistErrorIsDefinite(error)) {
            checkpoint.uncertain = 'link';
            return reconcileCardPersist(checkpoint, isCurrent);
          }
          throw new Error('КАРТОЧКА СОХРАНЕНА, НО ПРИВЯЗКА КЛИЕНТА НЕ ЗАВЕРШЕНА. ' + error.message);
        }
      }
      return data;
    }
