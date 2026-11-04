    function clientVehicleDeletionVerified(data, vehicleId) {
      return data.client.vehicles.every((item) => item && typeof item.id === 'string' && item.id && item.id === item.id.trim() && item.id !== vehicleId)
        && data.vehicles.every((item) => item && typeof item.id === 'string' && item.id === item.id.trim()
          && typeof item.card_id === 'string' && (item.id || item.card_id) && item.id !== vehicleId);
    }
    async function refreshClientAfterVehicleMutation(context, expectedVehicleId = '', deletedVehicleId = '') {
      const draft = readClientFormPayload();
      const previous = JSON.parse(state.clientsDraftBaseline || '{}');
      const revision = state.clientsDraftRevision;
      const pending = state.clientProfileUncertainEffects.get(JSON.stringify([context.clientId, null]))?.uncertain;
      const edited = Object.fromEntries(Object.entries(draft).filter(([key, value]) =>
        JSON.stringify(value) !== JSON.stringify(previous[key])));
      const data = await clientMutationRead('/api/get_client?client_id=' + encodeURIComponent(context.clientId) + '&order_limit=30');
      if (!context.isCurrent()) return false;
      if (!clientProfileReadValid(data, context.clientId)) throw new Error('ПРОФИЛЬ КЛИЕНТА НЕ ПОДТВЕРЖДЁН.');
      if (expectedVehicleId && (!data.client.vehicles.every((item) => item && typeof item.id === 'string' && item.id && item.id === item.id.trim())
          || data.client.vehicles.filter((item) => item.id === expectedVehicleId).length !== 1)) {
        throw new Error('СОХРАНЁННЫЙ АВТОМОБИЛЬ НЕ ПОДТВЕРЖДЁН В ПРОФИЛЕ КЛИЕНТА.');
      }
      if (deletedVehicleId && !clientVehicleDeletionVerified(data, deletedVehicleId)) throw new Error('УДАЛЕНИЕ АВТОМОБИЛЯ НЕ ПОДТВЕРЖДЕНО В ПРОФИЛЕ КЛИЕНТА.');
      state.clientVehicleEditor = null;
      renderClientProfile(data);
      const baseline = state.clientsDraftBaseline;
      const refreshed = JSON.parse(baseline);
      const keys = new Set(Object.keys(edited));
      for (const pair of [['phone', 'phones'], ['email', 'emails']]) {
        if (pair.some((key) => keys.has(key))) pair.forEach((key) => keys.add(key));
      }
      const conflict = Boolean(revision?.conflict) || Array.from(keys).some((key) =>
        JSON.stringify(previous[key]) !== JSON.stringify(refreshed[key]));
      fillClientForm({ ...readClientFormPayload(), ...edited });
      state.clientsDraftBaseline = pending ? JSON.stringify(previous) : baseline;
      if (conflict || pending) state.clientsDraftRevision = { version: revision?.version || '', conflict };
      updateClientSaveButtonState();
      renderClientsList();
      return true;
    }
    function clientVehicleMutationOwnerKey(path, body, context) {
      return JSON.stringify([state.viewerStateGeneration, state.operatorSessionToken,
        context.clientId, path, body.client_vehicle_id || '__new__']);
    }
    function requireClientVehicleMutationOwner(ownerKey) {
      const checkpoint = state.clientVehicleMutationCheckpoint;
      if (checkpoint && checkpoint.ownerKey !== ownerKey && (checkpoint.uncertain || checkpoint.confirmedResult)) {
        throw new Error('ПРЕДЫДУЩАЯ ОПЕРАЦИЯ С АВТОМОБИЛЕМ НЕ ПОДТВЕРЖДЕНА. СНАЧАЛА ПРОВЕРЬТЕ ЕЁ.');
      }
    }
    function prepareClientVehicleMutation(path, body, context) {
      const ownerKey = clientVehicleMutationOwnerKey(path, body, context);
      requireClientVehicleMutationOwner(ownerKey);
      const checkpoint = state.clientVehicleMutationCheckpoint;
      const version = checkpoint?.ownerKey === ownerKey && (checkpoint.uncertain || checkpoint.confirmedResult)
        ? checkpoint.body.expected_client_updated_at
        : path.endsWith('/upsert_client_vehicle') ? state.clientVehicleEditor?.expectedClientUpdatedAt
        : state.clientsDesktopProfile?.client?.updated_at;
      if (typeof version !== 'string' || !version || version !== version.trim()) {
        throw new Error('ВЕРСИЯ КЛИЕНТА НЕ ПОДТВЕРЖДЕНА. ПРОВЕРЬТЕ ПРОФИЛЬ; ИЗМЕНЕНИЯ АВТОМОБИЛЯ СОХРАНЕНЫ В ФОРМЕ.');
      }
      body.expected_client_updated_at = version;
      return ownerKey;
    }
    async function clientVehicleWrite(path, body, context, effect = null) {
      const ownerKey = clientVehicleMutationOwnerKey(path, body, context);
      requireClientVehicleMutationOwner(ownerKey);
      let checkpoint = state.clientVehicleMutationCheckpoint;
      if (!checkpoint || checkpoint.ownerKey !== ownerKey) {
        checkpoint = { ownerKey, body, uncertain: false };
        state.clientVehicleMutationCheckpoint = checkpoint;
      }
      if (effect) checkpoint.effect = effect;
      if (checkpoint.confirmedResult) {
        if (JSON.stringify(checkpoint.body) !== JSON.stringify(body)) throw new Error('ПРЕДЫДУЩАЯ ЗАПИСЬ АВТОМОБИЛЯ ПОДТВЕРЖДЕНА. СНАЧАЛА ОБНОВИТЕ ПРОФИЛЬ.');
        return checkpoint.confirmedResult;
      }
      try {
        if (!checkpoint.uncertain) {
          checkpoint.body = body;
          const data = await cardPersistWrite(path, body);
          const id = data?.vehicle?.id;
          if (data?.client?.id !== context.clientId || typeof id !== 'string' || !id || id !== id.trim()
              || (body.client_vehicle_id && id !== body.client_vehicle_id)
              || (path.endsWith('delete_client_vehicle') && data?.meta?.deleted !== true)) throw new Error('СЕРВЕР НЕ ПОДТВЕРДИЛ КЛИЕНТА И АВТОМОБИЛЬ.');
          checkpoint.confirmedResult = data;
          return data;
        }
      } catch (error) {
        if (!context.isCurrent() || clientVehicleErrorIsDefinite(error)) throw error;
        checkpoint.uncertain = true;
      }
      const data = await clientMutationRead('/api/get_client?client_id=' + encodeURIComponent(context.clientId) + '&order_limit=30');
      if (!context.isCurrent()) return null;
      if (!clientProfileReadValid(data, context.clientId)) throw new Error('ПРОФИЛЬ КЛИЕНТА НЕ ПОДТВЕРЖДЁН. ПОВТОР ЗАПИСИ ОСТАНОВЛЕН.');
      const vehicleId = checkpoint.body.client_vehicle_id;
      const deleting = path.endsWith('delete_client_vehicle');
      const storedVehicles = data.client.vehicles;
      const validStored = storedVehicles.every((item) => item && typeof item.id === 'string' && item.id && item.id === item.id.trim());
      const matches = validStored ? storedVehicles.filter((item) => item.id === vehicleId) : [];
      const vehicle = matches.length === 1 ? matches[0] : null;
      const expected = checkpoint.body.vehicle || {};
      const comparable = (key, value) => key === 'vin' ? String(value || '').trim().toUpperCase()
        : key === 'license_plate' ? String(value || '').replace(/\s+/g, '').toUpperCase() : String(value || '').trim();
      const confirmed = vehicleId && validStored && (deleting ? clientVehicleDeletionVerified(data, vehicleId) : vehicle && Object.entries(expected)
        .filter(([, value]) => String(value || '').trim())
        .every(([key, value]) => comparable(key, vehicle[key]) === comparable(key, value)));
      if (!confirmed) throw new Error('РЕЗУЛЬТАТ ЗАПИСИ АВТОМОБИЛЯ НЕ ПОДТВЕРЖДЁН. ПРОВЕРЬТЕ ПРОФИЛЬ КЛИЕНТА. ПОВТОР ОСТАНОВЛЕН; ЧЕРНОВИК СОХРАНЁН.');
      checkpoint.uncertain = false;
      checkpoint.confirmedResult = data;
      if (JSON.stringify(checkpoint.body) !== JSON.stringify(body)) throw new Error('ПРЕДЫДУЩАЯ ЗАПИСЬ ПОДТВЕРЖДЕНА. ТЕКУЩИЙ ЧЕРНОВИК ОСТАЛСЯ В ФОРМЕ; СОХРАНИТЕ ЕГО ОТДЕЛЬНО.');
      return data;
    }

    async function saveClientVehicleFromEditor(key) {
      if (!state.clientsActiveId) {
        setStatus('СНАЧАЛА ВЫБЕРИТЕ ИЛИ СОХРАНИТЕ КЛИЕНТА.', true);
        return;
      }
      const normalizedKey = String(key || '').trim();
      const currentVehicle = normalizedKey === '__new__' ? null : findActiveClientVehicle(normalizedKey);
      if (normalizedKey !== '__new__' && !currentVehicle) {
        setStatus('АВТОМОБИЛЬ НЕ НАЙДЕН В ПРОФИЛЕ. ПРОВЕРЬТЕ ДАННЫЕ; ЧЕРНОВИК СОХРАНЁН.', true);
        return;
      }
      const payload = readClientVehicleEditorPayload(normalizedKey);
      if (!payload.vehicle && !payload.vin && !payload.license_plate) {
        setStatus('ЗАПОЛНИТЕ МОДЕЛЬ, VIN ИЛИ ГОСНОМЕР АВТОМОБИЛЯ.', true);
        return;
      }
      const body = {
        client_id: state.clientsActiveId,
        vehicle: payload,
        sync_linked_cards: true,
      };
      if (currentVehicle?.id && !currentVehicle?.card_id) body.client_vehicle_id = currentVehicle.id;
      const context = beginClientMutation();
      if (!context) return;
      body.client_id = context.clientId;
      let effect = null, created = false;
      try {
        const ownerKey = prepareClientVehicleMutation('/api/upsert_client_vehicle', body, context);
        if (!currentVehicle || currentVehicle.card_id) {
          const checkpoint = state.clientVehicleMutationCheckpoint;
          effect = checkpoint?.ownerKey === ownerKey && checkpoint.effect && state.clientCandidateVehicleEffects.get(checkpoint.effect.key) === checkpoint.effect
            ? checkpoint.effect : beginCandidateVehicleCreation(context.clientId, currentVehicle);
        }
        const result = await clientVehicleWrite('/api/upsert_client_vehicle', body, context, effect);
        created = Boolean(result?.vehicle?.id);
        if (!context.isCurrent()) return;
        if (await refreshClientAfterVehicleMutation(context, result?.vehicle?.id || body.client_vehicle_id || '')) {
          if (state.clientVehicleMutationCheckpoint) state.clientVehicleMutationCheckpoint.confirmedResult = null;
          finishCandidateVehicleCreation(effect, { confirmed: true });
          setStatus(state.clientsDraftRevision?.conflict
            ? 'АВТОМОБИЛЬ СОХРАНЁН. ПРОФИЛЬ ИЗМЕНИЛСЯ; ПРОВЕРЬТЕ ЧЕРНОВИК.' : 'АВТОМОБИЛЬ КЛИЕНТА СОХРАНЁН.', Boolean(state.clientsDraftRevision?.conflict));
        }
      } catch (error) {
        if (!created && context.isCurrent()) {
          finishCandidateVehicleCreation(effect, { error });
          if (effect && state.clientCandidateVehicleEffects.get(effect.key) === effect
              && state.clientVehicleMutationCheckpoint?.effect === effect) state.clientVehicleMutationCheckpoint.uncertain = true;
        }
        if (context.isCurrent()) setStatus(error.message, true);
      } finally {
        finishClientMutation(context);
      }
    }

    async function deleteClientVehicle(key) {
      if (!state.clientsActiveId) return;
      const vehicle = findActiveClientVehicle(key);
      if (!vehicle?.id || vehicle?.card_id) {
        setStatus('ЭТА ЗАПИСЬ ПРИШЛА ИЗ СВЯЗАННОЙ КАРТОЧКИ. СНАЧАЛА ОТКРОЙТЕ РЕДАКТИРОВАНИЕ И СОХРАНИТЕ ЕЁ В ПРОФИЛЬ КЛИЕНТА.', true);
        return;
      }
      const title = String(vehicle.vehicle || vehicle.vin || vehicle.license_plate || 'автомобиль').trim();
      if (!window.confirm('Удалить автомобиль "' + title + '" из профиля клиента? Связанные карточки останутся, но связь с этим автомобилем будет снята.')) return;
      const context = beginClientMutation();
      if (!context) return;
      try {
        const body = {
          client_id: context.clientId,
          client_vehicle_id: vehicle.id,
          unlink_cards: true,
        };
        prepareClientVehicleMutation('/api/delete_client_vehicle', body, context);
        await clientVehicleWrite('/api/delete_client_vehicle', body, context);
        if (!context.isCurrent()) return;
        if (await refreshClientAfterVehicleMutation(context, '', vehicle.id)) {
          if (state.clientVehicleMutationCheckpoint) state.clientVehicleMutationCheckpoint.confirmedResult = null;
          setStatus(state.clientsDraftRevision?.conflict
            ? 'АВТОМОБИЛЬ УДАЛЁН. ПРОФИЛЬ ИЗМЕНИЛСЯ; ПРОВЕРЬТЕ ЧЕРНОВИК.' : 'АВТОМОБИЛЬ КЛИЕНТА УДАЛЁН.', Boolean(state.clientsDraftRevision?.conflict));
        }
      } catch (error) {
        if (context.isCurrent()) setStatus(error.message, true);
      } finally {
        finishClientMutation(context);
      }
    }

    async function ensureStableClientSuggestionVehicle(clientId, vehicle, context = null) {
      if (!vehicle || (String(vehicle?.id || '').trim() && !vehicle?.card_id)) return vehicle;
      if (context && !context.isCurrent()) return null;
      const effect = beginCandidateVehicleCreation(clientId, vehicle);
      try {
        const data = await cardPersistWrite('/api/upsert_client_vehicle', {
          client_id: clientId,
          vehicle: {
            vehicle: vehicle.vehicle || '',
            brand: vehicle.brand || '',
            model: vehicle.model || '',
            year: vehicle.year || '',
            mileage: vehicle.mileage || '',
            vin: vehicle.vin || '',
            license_plate: vehicle.license_plate || vehicle.registration_plate || '',
            body_number: vehicle.body_number || '',
            chassis_number: vehicle.chassis_number || '',
            engine_code: vehicle.engine_code || '',
            engine_model: vehicle.engine_model || '',
            gearbox_type: vehicle.gearbox_type || '',
            gearbox_model: vehicle.gearbox_model || '',
            drivetrain: vehicle.drivetrain || '',
            notes: vehicle.notes || '',
          },
        });
        if (context && !context.isCurrent()) return null;
        if (data?.client?.id !== clientId || typeof data?.vehicle?.id !== 'string'
            || !data.vehicle.id || data.vehicle.id !== data.vehicle.id.trim()) throw new Error('СЕРВЕР НЕ ПОДТВЕРДИЛ КЛИЕНТА И АВТОМОБИЛЬ.');
        const stableVehicle = { ...data.vehicle, card_id: '' };
        state.clientSuggestionProfiles = {
          ...(state.clientSuggestionProfiles || {}),
          [clientId]: {
            ...(state.clientSuggestionProfiles?.[clientId] || {}),
            vehicles: clientSuggestionVehicles({ id: clientId, vehicles_preview: [] }).map((item) => (
              clientSuggestionVehicleKey(item) === clientSuggestionVehicleKey(vehicle) ? { ...item, ...stableVehicle } : item
            )),
          },
        };
        state.clientSuggestions = state.clientSuggestions.map((item) => {
          if (String(item?.id || '') !== String(clientId)) return item;
          const preview = Array.isArray(item.vehicles_preview) ? item.vehicles_preview : [];
          return {
            ...item,
            vehicles_preview: preview.map((previewVehicle) => (
              clientSuggestionVehicleKey(previewVehicle) === clientSuggestionVehicleKey(vehicle) ? { ...previewVehicle, ...stableVehicle } : previewVehicle
            )),
          };
        });
        finishCandidateVehicleCreation(effect, { confirmed: true });
        return stableVehicle;
      } catch (error) {
        if (!context || context.isCurrent()) finishCandidateVehicleCreation(effect, { error });
        throw error;
      }
    }
