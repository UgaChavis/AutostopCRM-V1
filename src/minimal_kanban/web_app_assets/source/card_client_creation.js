    function vehiclePayloadFromProfile(profile) {
      return {
        vehicle: profile.display_name || vehicleDisplayFromProfile(profile) || '',
        brand: profile.make_display || '',
        model: profile.model_display || '',
        year: profile.production_year ? String(profile.production_year) : '',
        mileage: profile.mileage ? String(profile.mileage) : '',
        vin: profile.vin || '',
        license_plate: profile.registration_plate || '',
        body_number: profile.body_number || '',
        chassis_number: profile.chassis_number || '',
        engine_code: profile.engine_code || '',
        engine_model: profile.engine_model || '',
        gearbox_type: profile.gearbox_type || '',
        gearbox_model: profile.gearbox_model || '',
        drivetrain: profile.drivetrain || '',
      };
    }

    function normalizedCardClientCreatePayload(payload) {
      const source = payload && typeof payload === 'object' ? payload : {};
      const allowedTypes = new Set(['person', 'ip', 'ooo', 'company']);
      const clientType = allowedTypes.has(String(source.client_type || '').trim()) ? String(source.client_type || '').trim() : 'person';
      const phones = normalizePhoneList([
        source.phone,
        ...(Array.isArray(source.phones) ? source.phones : []),
      ]);
      const phone = phones[0] || String(source.phone || '').trim();
      const displayName = String(source.display_name || source.short_name || source.legal_name || '').trim();
      const request = {
        ...source,
        client_type: clientType,
        display_name: displayName || phone,
        phone,
        phones,
      };
      if (clientType !== 'person' && request.display_name) {
        if (!String(request.short_name || '').trim()) request.short_name = request.display_name;
        if (!String(request.legal_name || '').trim()) request.legal_name = request.display_name;
      }
      return request;
    }

    function applyCardClientLinkResult(card) {
      const previous = state.activeCard || {};
      const draft = currentCardPayload();
      const oldProfile = cloneVehicleProfile(previous.vehicle_profile);
      const draftProfile = cloneVehicleProfile(draft.vehicle_profile);
      const editedProfile = Object.keys(draftProfile).filter((key) => JSON.stringify(draftProfile[key]) !== JSON.stringify(oldProfile[key]));
      const edited = ['title', 'vehicle', 'description'].filter((key) => String(draft[key] || '') !== String(previous[key] || ''));
      const editedTags = JSON.stringify(normalizeDraftTags(draft.tags)) !== JSON.stringify(normalizeDraftTags(previous.tag_items || previous.tags));
      applyCardModalState(card);
      for (const key of edited) {
        if (key === 'description') setCardDescriptionValue(draft.description);
        else if (key === 'title') els.cardTitle.value = draft.title;
        else els.cardVehicle.value = draft.vehicle;
      }
      if (editedProfile.length) {
        const profile = cloneVehicleProfile(card.vehicle_profile);
        editedProfile.forEach((key) => { profile[key] = draftProfile[key]; });
        applyVehicleProfileToForm(profile);
      }
      if (editedTags) { state.draftTags = normalizeDraftTags(draft.tags); renderColorTags(); }
      state.boardMutationGeneration += 1;
      applySavedCardLocalPatch(card);
      state.activeCard = card;
      syncCardSaveDirtyState();
    }

    const cardClientCreatePendingKey = 'autostop:card-client-create-pending';
    let cardClientCreateOwner = null;

    function beginCardClientCreate(previous = null) {
      let stored;
      try { stored = window.sessionStorage.getItem(cardClientCreatePendingKey); }
      catch (_) { throw new Error('НЕВОЗМОЖНО ПРОВЕРИТЬ СОЗДАНИЕ КЛИЕНТА. ПОВТОР ОСТАНОВЛЕН.'); }
      if (previous && previous === cardClientCreateOwner && stored === '1') return previous;
      if (cardClientCreateOwner || stored !== null) throw new Error('СОЗДАНИЕ КЛИЕНТА НЕ ПОДТВЕРЖДЕНО. ПРОВЕРЬТЕ СПИСОК КЛИЕНТОВ; ПОВТОР ОСТАНОВЛЕН.');
      const effect = {};
      try {
        window.sessionStorage.setItem(cardClientCreatePendingKey, '1');
        if (window.sessionStorage.getItem(cardClientCreatePendingKey) !== '1') throw new Error('storage');
      } catch (_) { throw new Error('НЕВОЗМОЖНО СОХРАНИТЬ ЗАЩИТУ СОЗДАНИЯ КЛИЕНТА.'); }
      cardClientCreateOwner = effect;
      return effect;
    }

    function finishCardClientCreate(effect) {
      if (!effect || cardClientCreateOwner !== effect) return false;
      try {
        window.sessionStorage.removeItem(cardClientCreatePendingKey);
        if (window.sessionStorage.getItem(cardClientCreatePendingKey) !== null) return false;
      } catch (_) {
        try { window.sessionStorage.setItem(cardClientCreatePendingKey, '1'); } catch (_) { /* Retain the in-memory block. */ }
        return false;
      }
      cardClientCreateOwner = null;
      return true;
    }

    function cardClientCreateRejected(error) {
      return (error?.status === 400 && error.code === 'validation_error')
        || (error?.status === 409 && error.code === 'state_write_conflict');
    }

    async function linkActiveCardToClient(clientId, { clientVehicleId = '', createVehicleFromCard = false, context = null } = {}) {
      if (!context?.isCurrent() || !context.cardId || !clientId) return null;
      try {
        const version = context.expectedUpdatedAt;
        if (!version || version !== version.trim() || version.length > 80) throw Object.assign(new Error('НЕТ ВЕРСИИ КАРТОЧКИ.'), { localValidation: true, linkNotStarted: true });
        const body = {
          card_id: context.cardId, expected_card_updated_at: version,
          client_id: clientId, client_vehicle_id: clientVehicleId || '',
          create_vehicle_from_card: Boolean(createVehicleFromCard),
          sync_vehicle_fields: true, sync_fields: true, overwrite_card_fields: false,
        };
        const data = await cardLinkWrite(body, state.activeCard?.client_vehicle_id || '', context.isCurrent);
        if (!context.isCurrent()) return null;
        if (!data?.card) throw Object.assign(new Error('ПРИВЯЗКА НЕ ПОДТВЕРЖДЕНА. ПРОВЕРЬТЕ КАРТОЧКУ; ПОВТОР ОСТАНОВЛЕН.'), { linkUncertain: true });
        applyCardClientLinkResult(data.card);
        hideClientSuggestions();
        setStatus('КАРТОЧКА СВЯЗАНА С КЛИЕНТОМ.', false);
        await refreshSnapshot(true);
        return context.isCurrent() ? data : null;
      } catch (error) {
        if (!context.isCurrent()) return null;
        setStatus(error.message, true);
        throw error;
      }
    }

    async function reconcileCreatedClient(checkpoint, context) {
      if (!checkpoint.clientId) {
        await clientMutationRead('/api/search_clients?query=' + encodeURIComponent(checkpoint.payload.display_name || checkpoint.payload.phone || '') + '&limit=6');
        throw new Error('РЕЗУЛЬТАТ СОЗДАНИЯ КЛИЕНТА НЕ ПОДТВЕРЖДЁН. ПРОВЕРЬТЕ СПИСОК КЛИЕНТОВ; ПОВТОРНОЕ СОЗДАНИЕ ОСТАНОВЛЕНО, ЧЕРНОВИК ОСТАЛСЯ В ОКНЕ.');
      }
      if (checkpoint.uncertain === 'link') {
        throw new Error('КЛИЕНТ СОЗДАН, НО ПРИВЯЗКА НЕ ПОДТВЕРЖДЕНА. ПРОВЕРЬТЕ КАРТОЧКУ; ПОВТОР ОСТАНОВЛЕН.');
      } else {
        const data = await clientMutationRead('/api/get_client?client_id=' + encodeURIComponent(checkpoint.clientId));
        if (!context.isCurrent()) return null;
        if (!clientProfileReadValid(data, checkpoint.clientId)) throw new Error('ПРОФИЛЬ КЛИЕНТА НЕ ПОДТВЕРЖДЁН. ПОВТОР ЗАПИСИ ОСТАНОВЛЕН.');
        if (checkpoint.uncertain === 'client') {
          if (!clientProfilePayloadMatches(data?.client, checkpoint.payload)) {
            throw new Error('СОХРАНЕНИЕ КЛИЕНТА НЕ ПОДТВЕРЖДЕНО. ЧЕРНОВИК ОСТАЛСЯ В ОКНЕ.');
          }
          checkpoint.client = data.client;
          checkpoint.key = JSON.stringify(checkpoint.payload);
        } else {
          // The vehicle's receipt is unknown; a matching VIN/plate is not ownership.
          throw new Error('КЛИЕНТ СОЗДАН, НО РЕЗУЛЬТАТ ДОБАВЛЕНИЯ АВТО НЕ ПОДТВЕРЖДЁН. ПРОВЕРЬТЕ ПРОФИЛЬ КЛИЕНТА ПЕРЕД ПОВТОРОМ.');
        }
      }
      checkpoint.uncertain = '';
      return checkpoint;
    }

    async function createClientForCard(profile, payload, { createVehicleFromCard = true, creationGeneration = null, isCurrent: requestGuard = null } = {}) {
      if (requestGuard && !requestGuard()) return null;
      const parent = beginCardScopedRequest('cardClientMutationRequest', { allowNew: true });
      if (!parent) return null;
      const context = { ...parent, writeRequest: cardPersistWrite,
        isCurrent: () => parent.isCurrent() && (!requestGuard || requestGuard()) };
      const sourceProfile = cloneVehicleProfile(profile && typeof profile === 'object' ? profile : readVehicleProfileForm());
      const shouldCreateVehicle = Boolean(createVehicleFromCard && profileHasVehicleIdentity(sourceProfile));
      const request = normalizedCardClientCreatePayload(payload);
      const ownerKey = JSON.stringify([state.viewerStateGeneration, state.operatorSessionToken, state.cardEditingGeneration || 0, context.cardId, creationGeneration]);
      let checkpoint = state.cardClientCreateCheckpoint;
      try {
        if (checkpoint?.effect && checkpoint.ownerKey !== ownerKey) throw new Error('ПРЕДЫДУЩЕЕ СОЗДАНИЕ КЛИЕНТА НЕ ЗАВЕРШЕНО. ПРОВЕРЬТЕ ПРОФИЛЬ; ПОВТОР ОСТАНОВЛЕН.');
        if (checkpoint?.ownerKey === ownerKey && checkpoint.receipt?.meta?.created === false) {
          if (checkpoint.effect) {
            beginCardClientCreate(checkpoint.effect);
            if (!finishCardClientCreate(checkpoint.effect)) throw new Error('НЕ УДАЛОСЬ СНЯТЬ ЗАЩИТУ СОЗДАНИЯ КЛИЕНТА. ПОВТОР ОСТАНОВЛЕН.');
            checkpoint.effect = null;
            setStatus('НАЙДЕН СУЩЕСТВУЮЩИЙ КЛИЕНТ. ВВЕДЁННЫЕ ДАННЫЕ НЕ СОХРАНЕНЫ; ПРОВЕРЬТЕ ПРОФИЛЬ.', true);
            return null;
          }
          if (clientProfileDuplicateIdentity(checkpoint.payload) === clientProfileDuplicateIdentity(request)) {
            setStatus('НАЙДЕН СУЩЕСТВУЮЩИЙ КЛИЕНТ. ВВЕДЁННЫЕ ДАННЫЕ НЕ СОХРАНЕНЫ; ПРОВЕРЬТЕ ПРОФИЛЬ.', true);
            return null;
          }
          checkpoint = null;
        }
        if ((context.cardId || shouldCreateVehicle) && (!cardVehicleNumbersCanSave() || !cardVehicleTextCanSave())) return null;
        if (!context.cardId && shouldCreateVehicle && !cardMileageCanSave(sourceProfile)) return null;
        if (!String(request.display_name || '').trim() && !String(request.phone || '').trim()) {
          setStatus('УКАЖИТЕ ИМЯ ИЛИ ТЕЛЕФОН КЛИЕНТА.', true);
          return null;
        }
        if (!checkpoint || checkpoint.ownerKey !== ownerKey) {
          checkpoint = { ownerKey, clientId: '', client: null, vehicleId: '', payload: request, key: '', uncertain: '', createVehicle: false, linked: false };
          state.cardClientCreateCheckpoint = checkpoint;
        }
        if (context.cardId) requireCardLinkSettled(context.cardId);
        if (checkpoint.effect) beginCardClientCreate(checkpoint.effect);
        if (checkpoint.uncertain) await reconcileCreatedClient(checkpoint, context);
        if (!context.isCurrent()) return null;
        const key = JSON.stringify(request);
        if (!checkpoint.clientId || checkpoint.key !== key) {
          const version = checkpoint.client?.updated_at;
          if (checkpoint.clientId && (checkpoint.client?.id !== checkpoint.clientId || typeof version !== 'string' || !version || version !== version.trim())) {
            throw new Error('ВЕРСИЯ КЛИЕНТА НЕ ПОДТВЕРЖДЕНА. ПРОВЕРЬТЕ ПРОФИЛЬ; ЧЕРНОВИК СОХРАНЁН.');
          }
          checkpoint.payload = request;
          const creatingClient = !checkpoint.clientId;
          if (creatingClient) checkpoint.effect = beginCardClientCreate(checkpoint.effect);
          try {
            const data = await cardPersistWrite(checkpoint.clientId ? '/api/update_client' : '/api/create_client', checkpoint.clientId
              ? { client_id: checkpoint.clientId, ...request, expected_updated_at: version } : request);
            if (!context.isCurrent()) return null;
            const client = data?.client;
            if (typeof client?.id !== 'string' || !client.id.trim() || client.id !== client.id.trim()
                || (checkpoint.clientId && client.id !== checkpoint.clientId)
                || (creatingClient && (typeof data?.meta?.created !== 'boolean'
                  || (data.meta.created === false && (data.meta.duplicate !== true || data.meta.duplicate_of !== client.id
                    || !clientProfileHasRevision(client) || !Object.entries(request).every(([key, value]) => Array.isArray(value) ? Array.isArray(client[key]) : typeof client[key] === 'string')))))) throw new Error('СЕРВЕР НЕ ПОДТВЕРДИЛ КЛИЕНТА.');
            checkpoint.client = client;
            checkpoint.clientId = client.id;
            checkpoint.key = key;
            if (creatingClient) { checkpoint.clientCreated = data.meta.created; checkpoint.receipt = data; }
          } catch (error) {
            if (!context.isCurrent()) return null;
            if (creatingClient && cardClientCreateRejected(error) && finishCardClientCreate(checkpoint.effect)) checkpoint.effect = null;
            else if (creatingClient) { checkpoint.uncertain = 'client'; throw error; }
            if (cardPersistErrorIsDefinite(error)) throw error;
            checkpoint.uncertain = 'client';
            await reconcileCreatedClient(checkpoint, context);
          }
        }
        if (!context.isCurrent()) return null;
        if (checkpoint.receipt?.meta?.created === false) {
          if (!finishCardClientCreate(checkpoint.effect)) throw new Error('НЕ УДАЛОСЬ СНЯТЬ ЗАЩИТУ СОЗДАНИЯ КЛИЕНТА. ПОВТОР ОСТАНОВЛЕН.');
          checkpoint.effect = null;
          setStatus('НАЙДЕН СУЩЕСТВУЮЩИЙ КЛИЕНТ. ВВЕДЁННЫЕ ДАННЫЕ НЕ СОХРАНЕНЫ; ПРОВЕРЬТЕ ПРОФИЛЬ.', true);
          return null;
        }
        if (context.cardId && !checkpoint.linked) {
          checkpoint.createVehicle = shouldCreateVehicle;
          checkpoint.linkRejected = false;
          try {
            const linked = await linkActiveCardToClient(checkpoint.clientId, { createVehicleFromCard: shouldCreateVehicle, context });
            if (!context.isCurrent()) return null;
            checkpoint.vehicleId = linked?.card?.client_vehicle_id || '';
            checkpoint.linked = true;
          } catch (error) {
            if (!context.isCurrent()) return null;
            checkpoint.linkRejected = Boolean(!error?.linkUncertain && (error?.localValidation && error?.linkNotStarted || error?.status >= 400 && error.status < 500
              && ![408, 429].includes(error.status)
              && ['validation_error', 'card_update_conflict', 'client_update_conflict', 'client_ambiguous', 'client_vehicle_ambiguous', 'client_vehicle_conflict', 'client_vehicle_id_conflict', 'client_vehicle_reference_conflict', 'not_found'].includes(error.code)));
            if (error?.localValidation || cardPersistErrorIsDefinite(error)) throw new Error('КЛИЕНТ СОЗДАН, НО ПРИВЯЗКА НЕ ЗАВЕРШЕНА. ' + error.message);
            checkpoint.uncertain = 'link';
            throw error;
          }
        } else if (!context.cardId && shouldCreateVehicle && !checkpoint.vehicleId) {
          try {
            const upserted = await cardPersistWrite('/api/upsert_client_vehicle', { client_id: checkpoint.clientId, vehicle: vehiclePayloadFromProfile(sourceProfile), sync_linked_cards: true });
            if (!context.isCurrent()) return null;
            const vehicleId = upserted?.vehicle?.id;
            if (upserted?.client?.id !== checkpoint.clientId || typeof vehicleId !== 'string' || !vehicleId || vehicleId !== vehicleId.trim()) throw new Error('СЕРВЕР НЕ ПОДТВЕРДИЛ КЛИЕНТА И АВТОМОБИЛЬ.');
            checkpoint.vehicleId = vehicleId;
          } catch (error) {
            if (!context.isCurrent()) return null;
            if (cardPersistErrorIsDefinite(error)) throw new Error('КЛИЕНТ СОЗДАН, НО АВТО НЕ ДОБАВЛЕНО. ' + error.message);
            checkpoint.uncertain = 'vehicle';
            await reconcileCreatedClient(checkpoint, context);
          }
        }
        if (!context.isCurrent()) return null;
        if (checkpoint.effect && !finishCardClientCreate(checkpoint.effect)) throw new Error('НЕ УДАЛОСЬ СНЯТЬ ЗАЩИТУ СОЗДАНИЯ КЛИЕНТА. ПОВТОР ОСТАНОВЛЕН.');
        checkpoint.effect = null;
        state.pendingCardClientId = checkpoint.clientId;
        state.pendingCardClientVehicleId = checkpoint.vehicleId;
        state.pendingCreateClientVehicleFromCard = false;
        hideClientSuggestions();
        return { client: checkpoint.client, clientId: checkpoint.clientId, clientVehicleId: checkpoint.vehicleId, context };
      } catch (error) {
        if (!context.isCurrent()) return null;
        throw error;
      } finally {
        finishCardScopedRequest(parent);
      }
    }
