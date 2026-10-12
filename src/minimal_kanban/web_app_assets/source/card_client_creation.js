    // @include card_client_controls.js

    async function reconcileCreatedClient(checkpoint, context) {
      if (!checkpoint.clientId) {
        await clientMutationRead('/api/search_clients?query=' + encodeURIComponent(checkpoint.payload.display_name || checkpoint.payload.phone || '') + '&limit=6');
        throw new Error('РЕЗУЛЬТАТ СОЗДАНИЯ КЛИЕНТА НЕ ПОДТВЕРЖДЁН. ПРОВЕРЬТЕ СПИСОК КЛИЕНТОВ; ПОВТОРНОЕ СОЗДАНИЕ ОСТАНОВЛЕНО, ЧЕРНОВИК ОСТАЛСЯ В ОКНЕ.');
      }
      if (checkpoint.uncertain === 'link') {
        const data = await clientMutationRead('/api/get_card?card_id=' + encodeURIComponent(context.cardId));
        if (!context.isCurrent()) return null;
        if (data?.card?.client_id !== checkpoint.clientId || (checkpoint.createVehicle && !data.card.client_vehicle_id)) {
          throw new Error('КЛИЕНТ СОЗДАН, НО ПРИВЯЗКА НЕ ПОДТВЕРЖДЕНА ПРИ ЧТЕНИИ. ПОВТОР ЗАПИСИ ОСТАНОВЛЕН.');
        }
        applyCardClientLinkResult(data.card);
        checkpoint.vehicleId = data.card.client_vehicle_id || '';
        checkpoint.linked = true;
      } else {
        const data = await clientMutationRead('/api/get_client?client_id=' + encodeURIComponent(checkpoint.clientId));
        if (!context.isCurrent()) return null;
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
      const request = normalizedCardClientCreatePayload(payload);
      if (!String(request.display_name || '').trim() && !String(request.phone || '').trim()) {
        setStatus('УКАЖИТЕ ИМЯ ИЛИ ТЕЛЕФОН КЛИЕНТА.', true);
        finishCardScopedRequest(parent);
        return null;
      }
      const ownerKey = JSON.stringify([state.viewerStateGeneration, state.operatorSessionToken, state.cardEditingGeneration || 0, context.cardId, creationGeneration]);
      let checkpoint = state.cardClientCreateCheckpoint;
      if (!checkpoint || checkpoint.ownerKey !== ownerKey) {
        checkpoint = { ownerKey, clientId: '', client: null, vehicleId: '', payload: request, key: '', uncertain: '', createVehicle: false, linked: false };
        state.cardClientCreateCheckpoint = checkpoint;
      }
      try {
        if (checkpoint.uncertain) await reconcileCreatedClient(checkpoint, context);
        if (!context.isCurrent()) return null;
        const key = JSON.stringify(request);
        if (!checkpoint.clientId || checkpoint.key !== key) {
          checkpoint.payload = request;
          try {
            const data = await cardPersistWrite(checkpoint.clientId ? '/api/update_client' : '/api/create_client', checkpoint.clientId
              ? { client_id: checkpoint.clientId, ...request } : request);
            if (!context.isCurrent()) return null;
            const client = data?.client;
            if (!client?.id) throw new Error('СЕРВЕР НЕ ПОДТВЕРДИЛ ID КЛИЕНТА.');
            checkpoint.client = client;
            checkpoint.clientId = client.id;
            checkpoint.key = key;
          } catch (error) {
            if (!context.isCurrent()) return null;
            if (cardPersistErrorIsDefinite(error)) throw error;
            checkpoint.uncertain = 'client';
            await reconcileCreatedClient(checkpoint, context);
          }
        }
        if (!context.isCurrent()) return null;
        const shouldCreateVehicle = Boolean(createVehicleFromCard && profileHasVehicleIdentity(sourceProfile));
        if (context.cardId && !checkpoint.linked) {
          checkpoint.createVehicle = shouldCreateVehicle;
          try {
            const linked = await linkActiveCardToClient(checkpoint.clientId, { createVehicleFromCard: shouldCreateVehicle, context });
            if (!context.isCurrent()) return null;
            checkpoint.vehicleId = linked?.card?.client_vehicle_id || '';
            checkpoint.linked = true;
          } catch (error) {
            if (!context.isCurrent()) return null;
            if (cardPersistErrorIsDefinite(error)) throw new Error('КЛИЕНТ СОЗДАН, НО ПРИВЯЗКА НЕ ЗАВЕРШЕНА. ' + error.message);
            checkpoint.uncertain = 'link';
            await reconcileCreatedClient(checkpoint, context);
          }
        } else if (!context.cardId && shouldCreateVehicle && !checkpoint.vehicleId) {
          try {
            const upserted = await cardPersistWrite('/api/upsert_client_vehicle', { client_id: checkpoint.clientId, vehicle: vehiclePayloadFromProfile(sourceProfile), sync_linked_cards: true });
            if (!context.isCurrent()) return null;
            checkpoint.vehicleId = upserted?.vehicle?.id || '';
          } catch (error) {
            if (!context.isCurrent()) return null;
            if (cardPersistErrorIsDefinite(error)) throw new Error('КЛИЕНТ СОЗДАН, НО АВТО НЕ ДОБАВЛЕНО. ' + error.message);
            checkpoint.uncertain = 'vehicle';
            await reconcileCreatedClient(checkpoint, context);
          }
        }
        if (!context.isCurrent()) return null;
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
