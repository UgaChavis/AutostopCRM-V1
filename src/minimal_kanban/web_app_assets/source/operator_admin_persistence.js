    const operatorUserDeleteCheckpoints = new Map();
    const operatorUserSaveCheckpoints = new Map();
    const operatorEmployeeBindingCheckpoints = new Map();
    let operatorAdminWriteViewerKey = '';

    function operatorAdminViewerIsCurrent(context) {
      return context.viewer.isCurrent()
        && String(context.viewer.apiToken || '') === String(state.apiToken || '');
    }

    function operatorAdminWriteKey(username, context) {
      const viewerKey = JSON.stringify([
        context.viewer.viewerStateGeneration,
        context.viewer.operatorSessionToken,
        context.viewer.apiToken,
      ]);
      if (operatorAdminWriteViewerKey !== viewerKey) {
        operatorUserDeleteCheckpoints.clear();
        operatorUserSaveCheckpoints.clear();
        operatorEmployeeBindingCheckpoints.clear();
        operatorAdminWriteViewerKey = viewerKey;
      }
      return String(username || '').trim().toUpperCase();
    }

    function guardOperatorAdminWrite(key, kind) {
      const maps = {
        save: operatorUserSaveCheckpoints,
        delete: operatorUserDeleteCheckpoints,
        binding: operatorEmployeeBindingCheckpoints,
      };
      if (Object.entries(maps).some(([name, checkpoints]) => name !== kind && checkpoints.has(key))) {
        throw new Error('ПРЕЖНЯЯ ОПЕРАЦИЯ С ЭТОЙ УЧЁТНОЙ ЗАПИСЬЮ НЕ ПОДТВЕРЖДЕНА. СНАЧАЛА СВЕРЬТЕ ЕЁ РЕЗУЛЬТАТ.');
      }
    }

    function operatorAdminWriteIsDefinite(error) {
      return error?.status >= 400 && error.status < 500 && error.code !== 'invalid_json';
    }

    function operatorAdminReceiptMatches(user, username) {
      return user && typeof user === 'object'
        && String(user.username || '').trim().toUpperCase() === String(username || '').trim().toUpperCase();
    }

    async function readOperatorAdminMutationUser(username, context) {
      if (!operatorAdminViewerIsCurrent(context) || !context.isCurrent()) return null;
      const requestSeq = (state.operatorAdminRequestSeq || 0) + 1;
      state.operatorAdminRequestSeq = requestSeq;
      const binding = {
        generation: state.operatorEmployeeBindingIntentGeneration,
        username: state.operatorEmployeeBindingUser,
        employeeId: String(els.operatorUserEmployeeSelect?.value || ''),
      };
      const data = await api('/api/list_operator_users');
      if (!operatorAdminViewerIsCurrent(context) || !context.isCurrent()
        || state.operatorAdminRequestSeq !== requestSeq) return null;
      if (!Array.isArray(data?.users)) throw new Error('НЕ УДАЛОСЬ СВЕРИТЬ СПИСОК ПОЛЬЗОВАТЕЛЕЙ.');
      renderOperatorUsers(data);
      if (state.operatorEmployeeBindingIntentGeneration === binding.generation
        && state.operatorEmployeeBindingUser === binding.username && els.operatorUserEmployeeSelect) {
        els.operatorUserEmployeeSelect.value = binding.employeeId;
      }
      return data.users.find((user) => operatorAdminReceiptMatches(user, username)) || null;
    }

    async function persistOperatorUserSave(payload, context) {
      if (!operatorAdminViewerIsCurrent(context) || !context.ownsRequest()) return;
      const key = operatorAdminWriteKey(payload.username, context);
      guardOperatorAdminWrite(key, 'save');
      const uncertain = 'РЕЗУЛЬТАТ СОХРАНЕНИЯ ПОЛЬЗОВАТЕЛЯ НЕ ПОДТВЕРЖДЁН. ПОВТОРНАЯ ЗАПИСЬ ОСТАНОВЛЕНА; ПРОВЕРЬТЕ ВХОД С НОВЫМ ПАРОЛЕМ.';
      if (operatorUserSaveCheckpoints.has(key)) {
        try { await readOperatorAdminMutationUser(payload.username, context); } catch (_) {}
        if (operatorAdminViewerIsCurrent(context)) throw new Error(uncertain);
        return;
      }
      operatorUserSaveCheckpoints.set(key, true);
      let data;
      try {
        data = await cardFilesApi('/api/save_operator_user', { method: 'POST', body: payload });
        if (!operatorAdminViewerIsCurrent(context)) return;
        if (!operatorAdminReceiptMatches(data?.user, payload.username)) {
          throw new Error('СЕРВЕР НЕ ПОДТВЕРДИЛ СОХРАНЕНИЕ ЭТОГО ПОЛЬЗОВАТЕЛЯ.');
        }
      } catch (error) {
        if (!operatorAdminViewerIsCurrent(context)) return;
        if (operatorAdminWriteIsDefinite(error)) {
          operatorUserSaveCheckpoints.delete(key);
          throw error;
        }
        try { await readOperatorAdminMutationUser(payload.username, context); } catch (_) {}
        if (operatorAdminViewerIsCurrent(context)) throw new Error(uncertain);
        return;
      }
      operatorUserSaveCheckpoints.delete(key);
      const isCurrentIntent = context.isCurrent();
      applyOperatorUserSummary(data?.user, { preserveBindingDraft: !isCurrentIntent });
      if (!isCurrentIntent) return;
      clearOperatorUserEditor();
      setStatus((data?.meta?.created ? 'Пользователь создан.' : 'Пользователь обновлён.') + ' ' + (data?.user?.username || ''), false);
      await refreshOperatorAdminSurfaces({
        openAdminModal: false,
        refreshProfile: true,
        viewerContext: context.viewer,
      });
    }

    async function readOperatorUserDeletion(username, context) {
      const data = await api('/api/list_operator_users');
      if (!context.isCurrent()) return null;
      if (!Array.isArray(data?.users)) throw new Error('НЕ УДАЛОСЬ ОБНОВИТЬ СПИСОК ПОЛЬЗОВАТЕЛЕЙ.');
      renderOperatorUsers(data);
      context.request.lock();
      return !data.users.some((user) => String(user.username || '').trim().toUpperCase() === username);
    }

    async function persistOperatorUserDelete(username, context) {
      const caller = context;
      context = { ...caller, isCurrent: () => caller.isCurrent() && operatorAdminViewerIsCurrent(caller) };
      if (!context.isCurrent()) return;
      const name = String(username || '').trim().toUpperCase();
      const key = operatorAdminWriteKey(name, context);
      guardOperatorAdminWrite(key, 'delete');
      let checkpoint = operatorUserDeleteCheckpoints.get(key);
      if (checkpoint) {
        const absent = await readOperatorUserDeletion(name, context);
        if (!context.isCurrent()) return;
        if (!absent) throw new Error('РЕЗУЛЬТАТ ПРЕЖНЕГО УДАЛЕНИЯ НЕ ОПРЕДЕЛЁН. ПОВТОРНАЯ ЗАПИСЬ ОСТАНОВЛЕНА; СВЕРЬТЕ УЧЁТНУЮ ЗАПИСЬ.');
        operatorUserDeleteCheckpoints.delete(key);
        setStatus('ПОЛЬЗОВАТЕЛЬ УДАЛЁН; ОТСУТСТВИЕ ПОДТВЕРЖДЕНО ЧТЕНИЕМ.', false);
        return;
      }
      checkpoint = { confirmed: false };
      operatorUserDeleteCheckpoints.set(key, checkpoint);
      try {
        const data = await cardFilesApi('/api/delete_operator_user', {
          method: 'POST', body: { username },
        });
        if (!context.isCurrent()) return;
        if (data?.deleted !== true || String(data.username || '').trim().toUpperCase() !== name) {
          throw new Error('СЕРВЕР НЕ ПОДТВЕРДИЛ УДАЛЕНИЕ ПОЛЬЗОВАТЕЛЯ.');
        }
        checkpoint.confirmed = true;
        const absent = await readOperatorUserDeletion(name, context);
        if (!context.isCurrent()) return;
        if (!absent) throw new Error('УЧЁТНАЯ ЗАПИСЬ С ТЕМ ЖЕ ЛОГИНОМ СНОВА ПРИСУТСТВУЕТ. ПОВТОРНОЕ УДАЛЕНИЕ ОСТАНОВЛЕНО.');
        operatorUserDeleteCheckpoints.delete(key);
        setStatus('ПОЛЬЗОВАТЕЛЬ УДАЛЁН.', false);
      } catch (error) {
        if (!context.isCurrent()) return;
        if (!checkpoint.confirmed && error?.status >= 400 && error.status < 500
          && error.status !== 404 && error.code !== 'invalid_json') {
          operatorUserDeleteCheckpoints.delete(key);
          throw error;
        }
        try {
          const absent = await readOperatorUserDeletion(name, context);
          if (!context.isCurrent()) return;
          if (absent) {
            operatorUserDeleteCheckpoints.delete(key);
            setStatus('ПОЛЬЗОВАТЕЛЬ УДАЛЁН; ОТСУТСТВИЕ ПОДТВЕРЖДЕНО ЧТЕНИЕМ.', false);
            return;
          }
        } catch (_) {}
        if (context.isCurrent()) throw new Error(checkpoint.confirmed
          ? 'УДАЛЕНИЕ ЗАПИСАНО. СВЕРЬТЕ АКТУАЛЬНЫЙ СПИСОК; ПОВТОРНАЯ ЗАПИСЬ ОСТАНОВЛЕНА.'
          : 'РЕЗУЛЬТАТ УДАЛЕНИЯ НЕ ОПРЕДЕЛЁН. СВЕРЬТЕ СПИСОК; ПОВТОРНАЯ ЗАПИСЬ ОСТАНОВЛЕНА.');
      }
    }

    async function applyOperatorEmployeeBindingResult(user, context, employeeId, recovered = false) {
      const isCurrentIntent = context.isCurrent();
      applyOperatorUserSummary(user, { preserveBindingDraft: !isCurrentIntent });
      if (!isCurrentIntent) return;
      setStatus(recovered ? 'ПРИВЯЗКА ПОДТВЕРЖДЕНА ЧТЕНИЕМ.'
        : employeeId ? 'СОТРУДНИК ПРИВЯЗАН.' : 'СОТРУДНИК ОТВЯЗАН.', false);
      closeOperatorEmployeeBinding();
      await refreshOperatorAdminSurfaces({
        openAdminModal: false,
        refreshProfile: String(state.actor || '').trim().toUpperCase() === String(user.username || '').trim().toUpperCase(),
        viewerContext: context.viewer,
      });
    }

    async function reconcileOperatorEmployeeBinding(payload, context, key, checkpoint) {
      let user;
      try { user = await readOperatorAdminMutationUser(payload.username, context); } catch (_) {}
      if (!operatorAdminViewerIsCurrent(context) || !context.isCurrent()) return;
      if (!user || !checkpoint.createdAt || user.created_at !== checkpoint.createdAt
        || String(user.employee_id || '') !== checkpoint.employeeId) {
        throw new Error('РЕЗУЛЬТАТ ПРИВЯЗКИ НЕ ПОДТВЕРЖДЁН. ПОВТОРНАЯ ЗАПИСЬ ОСТАНОВЛЕНА; СВЕРЬТЕ СОТРУДНИКА.');
      }
      operatorEmployeeBindingCheckpoints.delete(key);
      if (String(payload.employee_id || '') !== checkpoint.employeeId) {
        setStatus('ПРЕЖНЯЯ ПРИВЯЗКА ПОДТВЕРЖДЕНА. НОВЫЙ ВЫБОР СОХРАНЁН В ЧЕРНОВИКЕ И НЕ ОТПРАВЛЕН.', false);
        return;
      }
      await applyOperatorEmployeeBindingResult(user, context, checkpoint.employeeId, true);
    }

    async function persistOperatorEmployeeBinding(payload, context) {
      if (!operatorAdminViewerIsCurrent(context) || !context.ownsRequest()) return;
      const key = operatorAdminWriteKey(payload.username, context);
      guardOperatorAdminWrite(key, 'binding');
      let checkpoint = operatorEmployeeBindingCheckpoints.get(key);
      if (checkpoint) return reconcileOperatorEmployeeBinding(payload, context, key, checkpoint);
      const existing = (state.operatorUsers || []).find((user) => operatorAdminReceiptMatches(user, payload.username));
      checkpoint = { employeeId: String(payload.employee_id || ''), createdAt: String(existing?.created_at || '') };
      operatorEmployeeBindingCheckpoints.set(key, checkpoint);
      let data;
      try {
        data = await cardFilesApi('/api/set_operator_user_employee', { method: 'POST', body: payload });
        if (!operatorAdminViewerIsCurrent(context)) return;
        if (!operatorAdminReceiptMatches(data?.user, payload.username)
          || String(data.user.employee_id || '') !== checkpoint.employeeId) {
          throw new Error('СЕРВЕР НЕ ПОДТВЕРДИЛ НУЖНУЮ ПРИВЯЗКУ СОТРУДНИКА.');
        }
      } catch (error) {
        if (!operatorAdminViewerIsCurrent(context)) return;
        if (operatorAdminWriteIsDefinite(error)) {
          operatorEmployeeBindingCheckpoints.delete(key);
          throw error;
        }
        return reconcileOperatorEmployeeBinding(payload, context, key, checkpoint);
      }
      operatorEmployeeBindingCheckpoints.delete(key);
      await applyOperatorEmployeeBindingResult(data.user, context, checkpoint.employeeId);
    }
