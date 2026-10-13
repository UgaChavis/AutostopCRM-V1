    const operatorUserDeleteCheckpoints = new Map();

    async function persistOperatorUserSave(payload, context) {
      if (!context.viewer.isCurrent()) return;
      const data = await cardFilesApi('/api/save_operator_user', {
        method: 'POST',
        body: payload,
      });
      if (!context.viewer.isCurrent()) return;
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
      if (!context.isCurrent()) return;
      const name = String(username || '').trim().toUpperCase();
      const key = JSON.stringify([state.viewerStateGeneration, state.operatorSessionToken, name]);
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

    async function persistOperatorEmployeeBinding(payload, context) {
      if (!context.viewer.isCurrent()) return;
      const data = await cardFilesApi('/api/set_operator_user_employee', {
        method: 'POST',
        body: payload,
      });
      if (!context.viewer.isCurrent()) return;
      const isCurrentIntent = context.isCurrent();
      applyOperatorUserSummary(data?.user, { preserveBindingDraft: !isCurrentIntent });
      if (!isCurrentIntent) return;
      setStatus(data?.meta?.bound ? 'СОТРУДНИК ПРИВЯЗАН.' : 'СОТРУДНИК ОТВЯЗАН.', false);
      closeOperatorEmployeeBinding();
      await refreshOperatorAdminSurfaces({
        openAdminModal: false,
        refreshProfile: String(state.actor || '').trim().toUpperCase() === payload.username,
        viewerContext: context.viewer,
      });
    }
