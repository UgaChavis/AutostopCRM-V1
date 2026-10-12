    async function refreshClientAfterVehicleMutation(context) {
      const draft = readClientFormPayload();
      const previous = JSON.parse(state.clientsDraftBaseline || '{}');
      const edited = Object.fromEntries(Object.entries(draft).filter(([key, value]) =>
        JSON.stringify(value) !== JSON.stringify(previous[key])));
      const data = await clientMutationRead('/api/get_client?client_id=' + encodeURIComponent(context.clientId) + '&order_limit=30');
      if (!context.isCurrent()) return false;
      state.clientVehicleEditor = null;
      renderClientProfile(data);
      const baseline = state.clientsDraftBaseline;
      fillClientForm({ ...readClientFormPayload(), ...edited });
      state.clientsDraftBaseline = baseline;
      updateClientSaveButtonState();
      renderClientsList();
      return true;
    }

    async function clientVehicleWrite(path, body, context) {
      const ownerKey = JSON.stringify([state.viewerStateGeneration, state.operatorSessionToken,
        context.clientId, state.clientsProfileRequestSeq || 0, path, body.client_vehicle_id || '__new__']);
      let checkpoint = state.clientVehicleMutationCheckpoint;
      if (!checkpoint || checkpoint.ownerKey !== ownerKey) {
        checkpoint = { ownerKey, body, uncertain: false };
        state.clientVehicleMutationCheckpoint = checkpoint;
      }
      try {
        if (!checkpoint.uncertain) {
          checkpoint.body = body;
          const data = await cardPersistWrite(path, body);
          if (path.endsWith('upsert_client_vehicle') && !data?.vehicle?.id) throw new Error('СЕРВЕР НЕ ПОДТВЕРДИЛ ID АВТОМОБИЛЯ.');
          return data;
        }
      } catch (error) {
        if (!context.isCurrent() || cardPersistErrorIsDefinite(error)) throw error;
        checkpoint.uncertain = true;
      }
      const data = await clientMutationRead('/api/get_client?client_id=' + encodeURIComponent(context.clientId) + '&order_limit=30');
      if (!context.isCurrent()) return null;
      const vehicleId = checkpoint.body.client_vehicle_id;
      const vehicle = (data?.vehicles || []).find((item) => item.id === vehicleId);
      const deleting = path.endsWith('delete_client_vehicle');
      const expected = checkpoint.body.vehicle || {};
      const comparable = (key, value) => key === 'vin' ? String(value || '').trim().toUpperCase()
        : key === 'license_plate' ? String(value || '').replace(/\s+/g, '').toUpperCase() : String(value || '').trim();
      const confirmed = vehicleId && (deleting ? !vehicle : vehicle && Object.entries(expected)
        .filter(([, value]) => String(value || '').trim())
        .every(([key, value]) => comparable(key, vehicle[key]) === comparable(key, value)));
      if (!confirmed) throw new Error('РЕЗУЛЬТАТ ЗАПИСИ АВТОМОБИЛЯ НЕ ПОДТВЕРЖДЁН. ПРОВЕРЬТЕ ПРОФИЛЬ КЛИЕНТА. ПОВТОР ОСТАНОВЛЕН; ЧЕРНОВИК СОХРАНЁН.');
      checkpoint.uncertain = false;
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
      if (currentVehicle?.id) body.client_vehicle_id = currentVehicle.id;
      const context = beginClientMutation();
      if (!context) return;
      body.client_id = context.clientId;
      try {
        await clientVehicleWrite('/api/upsert_client_vehicle', body, context);
        if (!context.isCurrent()) return;
        if (await refreshClientAfterVehicleMutation(context)) {
          setStatus('АВТОМОБИЛЬ КЛИЕНТА СОХРАНЁН.', false);
        }
      } catch (error) {
        if (context.isCurrent()) setStatus(error.message, true);
      } finally {
        finishClientMutation(context);
      }
    }

    async function deleteClientVehicle(key) {
      if (!state.clientsActiveId) return;
      const vehicle = findActiveClientVehicle(key);
      if (!vehicle?.id) {
        setStatus('ЭТА ЗАПИСЬ ПРИШЛА ИЗ СВЯЗАННОЙ КАРТОЧКИ. СНАЧАЛА ОТКРОЙТЕ РЕДАКТИРОВАНИЕ И СОХРАНИТЕ ЕЁ В ПРОФИЛЬ КЛИЕНТА.', true);
        return;
      }
      const title = String(vehicle.vehicle || vehicle.vin || vehicle.license_plate || 'автомобиль').trim();
      if (!window.confirm('Удалить автомобиль "' + title + '" из профиля клиента? Связанные карточки останутся, но связь с этим автомобилем будет снята.')) return;
      const context = beginClientMutation();
      if (!context) return;
      try {
        await clientVehicleWrite('/api/delete_client_vehicle', {
          client_id: context.clientId,
          client_vehicle_id: vehicle.id,
          unlink_cards: true,
        }, context);
        if (!context.isCurrent()) return;
        if (await refreshClientAfterVehicleMutation(context)) {
          setStatus('АВТОМОБИЛЬ КЛИЕНТА УДАЛЁН.', false);
        }
      } catch (error) {
        if (context.isCurrent()) setStatus(error.message, true);
      } finally {
        finishClientMutation(context);
      }
    }
