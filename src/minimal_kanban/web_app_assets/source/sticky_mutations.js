    const stickyWriteCheckpoints = new Map();

    function forgetStickyWrite(checkpoint) {
      for (const [key, value] of stickyWriteCheckpoints) {
        if (value === checkpoint) stickyWriteCheckpoints.delete(key);
      }
    }

    function stickyWriteKey(stickyId) {
      return JSON.stringify([state.viewerStateGeneration, state.operatorSessionToken, stickyId || '']);
    }

    function stickyWriteReadMatches(checkpoint, stickies) {
      const body = checkpoint.body;
      const sticky = stickies.find((item) => item.id === body.sticky_id);
      if (checkpoint.path === '/api/delete_sticky') return !sticky;
      if (!sticky) return false;
      if (checkpoint.path === '/api/move_sticky') return sticky.x === body.x && sticky.y === body.y;
      if (checkpoint.path !== '/api/update_sticky') return false;
      const deadline = body.deadline || {};
      const total = Number(deadline.days || 0) * 86400 + Number(deadline.hours || 0) * 3600
        + Number(deadline.minutes || 0) * 60 + Number(deadline.seconds || 0);
      return sticky.text === body.text && sticky.deadline_total_seconds === total;
    }

    async function readStickyWriteResult(checkpoint, context) {
      const snapshot = await api('/api/get_board_snapshot?compact=1&include_archive=0');
      if (!context.isCurrent()) return null;
      if (!Array.isArray(snapshot?.stickies)) throw new Error('СЕРВЕР НЕ ПОДТВЕРДИЛ СПИСОК СТИКЕРОВ.');
      if (!stickyWriteReadMatches(checkpoint, snapshot.stickies)) {
        applyStickySnapshot(snapshot.stickies);
        throw new Error('РЕЗУЛЬТАТ ПРЕДЫДУЩЕЙ ЗАПИСИ СТИКЕРА НЕ ПОДТВЕРЖДЁН. ПОВТОР ОСТАНОВЛЕН; СВЕРЬТЕ ДОСКУ.');
      }
      const sticky = snapshot.stickies.find((item) => item.id === checkpoint.body.sticky_id);
      checkpoint.data = { sticky, stickies: snapshot.stickies, deleted: checkpoint.path === '/api/delete_sticky' };
      return checkpoint.data;
    }

    async function persistStickyMutation(path, body, context) {
      if (!context.isCurrent()) return null;
      const key = stickyWriteKey(body.sticky_id);
      const intent = JSON.stringify([path, body]);
      let checkpoint = stickyWriteCheckpoints.get(key);
      let reuseConfirmed = Boolean(checkpoint?.data);
      if (checkpoint?.data && context.request.editor
        && checkpoint.editorGeneration !== state.stickyEditorGeneration) {
        forgetStickyWrite(checkpoint);
        checkpoint = null;
        reuseConfirmed = false;
      }
      if (checkpoint && !checkpoint.data) {
        await readStickyWriteResult(checkpoint, context);
        if (!context.isCurrent()) return null;
      }
      if (checkpoint && intent !== checkpoint.intent && intent !== checkpoint.aliasIntent) {
        forgetStickyWrite(checkpoint);
        checkpoint = null;
        reuseConfirmed = false;
      }
      if (!checkpoint) {
        checkpoint = { path, body, intent, editorGeneration: state.stickyEditorGeneration, data: null };
        stickyWriteCheckpoints.set(key, checkpoint);
        try {
          const data = await cardFilesApi(path, { method: 'POST', body });
          if (!context.isCurrent()) return null;
          const expectedId = String(body.sticky_id || '');
          const resultId = String(data?.sticky?.id || '');
          const confirmed = path === '/api/delete_sticky'
            ? data?.deleted === true && String(data?.sticky_id || '') === expectedId
            : resultId && (!expectedId || expectedId === resultId);
          if (!confirmed) throw new Error('СЕРВЕР НЕ ПОДТВЕРДИЛ ЗАПИСЬ СТИКЕРА.');
          checkpoint.data = data;
        } catch (error) {
          if (!context.isCurrent()) return null;
          if (error?.status >= 400 && error.status < 500 && error.code !== 'invalid_json') {
            forgetStickyWrite(checkpoint);
            throw error;
          }
          await readStickyWriteResult(checkpoint, context);
          if (!context.isCurrent()) return null;
        }
      }
      const data = checkpoint.data;
      if (!data || !context.isCurrent()) return null;
      if (context.request.editor && data.sticky?.id) {
        state.stickyDraft = data.sticky;
        if (els.stickyModalTitle) els.stickyModalTitle.textContent = 'СТИКЕР / ' + String(data.sticky.id).slice(0, 8).toUpperCase();
        if (checkpoint.path === '/api/create_sticky') {
          const { text, deadline, actor_name, source } = checkpoint.body;
          checkpoint.aliasIntent = JSON.stringify(['/api/update_sticky', { sticky_id: data.sticky.id, text, deadline, actor_name, source }]);
          stickyWriteCheckpoints.set(stickyWriteKey(data.sticky.id), checkpoint);
        }
      }
      let stickies = data.stickies;
      if (reuseConfirmed) {
        const snapshot = await api('/api/get_board_snapshot?compact=1&include_archive=0');
        if (!context.isCurrent()) return null;
        if (!Array.isArray(snapshot?.stickies)) throw new Error('СЕРВЕР НЕ ПОДТВЕРДИЛ СПИСОК СТИКЕРОВ.');
        stickies = snapshot.stickies;
      }
      if (!Array.isArray(state.snapshot?.stickies)) {
        await refreshSnapshot(true);
        if (!context.isCurrent()) return null;
        stickies = state.snapshot?.stickies;
      }
      if (!Array.isArray(stickies) || !applyStickySnapshot(stickies)) {
        const snapshot = await api('/api/get_board_snapshot?compact=1&include_archive=0');
        if (!context.isCurrent()) return null;
        stickies = snapshot?.stickies;
        if (!Array.isArray(stickies) || !applyStickySnapshot(stickies)) {
          throw new Error('СТИКЕР ЗАПИСАН, НО ДОСКА НЕ ОБНОВИЛАСЬ. ПОВТОРИТЕ ОБНОВЛЕНИЕ.');
        }
      }
      forgetStickyWrite(checkpoint);
      return { ...data, stickies };
    }
