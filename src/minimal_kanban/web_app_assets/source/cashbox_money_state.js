    const cashboxMoneyCheckpoints = new Map();

    function cashboxMoneyWriteBody(path, body) {
      const cashbox = state.activeCashbox?.cashbox;
      if (path === '/api/create_cashbox_transfer') {
        const source = (state.cashboxes || []).find((item) => item.id === body.from_cashbox_id);
        const target = (state.cashboxes || []).find((item) => item.id === body.to_cashbox_id);
        return { ...body, expected_from_updated_at: source?.updated_at, expected_to_updated_at: target?.updated_at };
      }
      if (path === '/api/cancel_cash_transaction') {
        return { ...body, expected_cashbox_updated_at: cashbox?.updated_at };
      }
      return { ...body, expected_updated_at: cashbox?.updated_at };
    }

    function cashboxMoneyRetryIsGuarded(checkpoint) {
      const body = checkpoint.body;
      const revisions = body.from_cashbox_id
        ? [[body.from_cashbox_id, body.expected_from_updated_at], [body.to_cashbox_id, body.expected_to_updated_at]]
        : [[body.cashbox_id, body.expected_updated_at || body.expected_cashbox_updated_at]];
      return revisions.every(([id, expected]) => expected
        && (state.cashboxes || []).find((cashbox) => cashbox.id === id)?.updated_at === expected);
    }

    async function runCashboxMoneyMutation(path, body, { receipt, verifyRead, onSuccess, onError } = {}) {
      if (state.cashboxMoneyRequest) return false;
      const viewer = state.viewerStateGeneration;
      const session = state.operatorSessionToken;
      const mobile = Boolean(state.mobileLite);
      const parent = (state.modalStack || []).find((entry) => entry?.key === 'cashboxes');
      const original = state.activeCashbox;
      const cashboxId = String(body.cashbox_id || body.from_cashbox_id || '');
      const cashboxIds = [cashboxId, body.to_cashbox_id].filter(Boolean);
      const key = JSON.stringify([viewer, session, path, body]);
      const ownsViewer = () => state.viewerStateGeneration === viewer && state.operatorSessionToken === session;
      const ownsUi = () => ownsViewer()
        && (mobile ? state.mobileView === 'cashboxes' : !parent || (state.modalStack || []).includes(parent))
        && String(state.activeCashbox?.cashbox?.id || state.activeCashboxId || '') === cashboxId;
      const request = beginCashboxMoneyRequest();
      if (!request) return false;
      let checkpoint = cashboxMoneyCheckpoints.get(key);
      const unresolved = [...cashboxMoneyCheckpoints.values()].find((item) => item.owner === session
        && item.cashboxIds.some((id) => cashboxIds.includes(id)) && item.uncertain
        && !(item === checkpoint && item.retrySafe));
      if (!checkpoint) {
        checkpoint = { owner: session, cashboxIds, body: cashboxMoneyWriteBody(path, body), confirmed: null, uncertain: false };
      }
      const reload = async () => {
        const data = await refreshCashboxesAfterMoneyMutation({ openModal: false, deferDetail: false });
        if (!data && ownsViewer() && state.cashboxMoneyRequest === request && !state.activeCashbox) {
          state.activeCashbox = original;
          state.activeCashboxId = cashboxId;
        }
        return data;
      };
      let completed = false;
      try {
        if (unresolved) {
          throw new Error('РЕЗУЛЬТАТ ПРЕДЫДУЩЕЙ ОПЕРАЦИИ НЕ ПОДТВЕРЖДЁН. ПОВТОР ОСТАНОВЛЕН; СВЕРЬТЕ ДВИЖЕНИЯ ПО КАССЕ.');
        }
        if (!checkpoint.confirmed) {
          cashboxMoneyCheckpoints.set(key, checkpoint);
          const previouslyUncertain = checkpoint.uncertain;
          checkpoint.retrySafe = false;
          const controller = new AbortController();
          const timeout = globalThis.setTimeout(() => controller.abort(), 90000);
          try {
            const data = await api(path, { method: 'POST', body: checkpoint.body, signal: controller.signal });
            if (!ownsViewer() || state.cashboxMoneyRequest !== request) return false;
            if (!receipt?.(data)) throw new Error('СЕРВЕР НЕ ПОДТВЕРДИЛ РЕЗУЛЬТАТ ОПЕРАЦИИ.');
            checkpoint.confirmed = data;
            checkpoint.uncertain = false;
            abortCashboxesLoad();
          } catch (error) {
            if (previouslyUncertain || !(error?.status >= 400 && error.status < 500 && error.code !== 'invalid_json')) checkpoint.uncertain = true;
            else cashboxMoneyCheckpoints.delete(key);
            throw error;
          } finally {
            globalThis.clearTimeout(timeout);
          }
        }
        if (!ownsUi()) return false;
        if (!await reload()) throw new Error('ОПЕРАЦИЯ ЗАПИСАНА, НО ЭКРАН НЕ ОБНОВИЛСЯ. ПОВТОРИТЕ ОБНОВЛЕНИЕ.');
        if (!ownsUi()) return false;
        cashboxMoneyCheckpoints.delete(key);
        completed = true;
      } catch (error) {
        if (!ownsViewer() || state.cashboxMoneyRequest !== request) return false;
        if (checkpoint.uncertain || unresolved || error?.status === 409) {
          try {
            if (await reload() && checkpoint.uncertain && ownsUi()) {
              if (verifyRead?.(state.activeCashbox)) {
                cashboxMoneyCheckpoints.delete(key);
                completed = true;
              } else {
                checkpoint.retrySafe = cashboxMoneyRetryIsGuarded(checkpoint);
              }
            }
          } catch (_) {}
        }
        if (!completed && ownsViewer() && state.cashboxMoneyRequest === request) {
          const uncertain = (checkpoint.uncertain || Boolean(unresolved)) && !checkpoint.retrySafe;
          const message = String(error?.message || 'НЕ УДАЛОСЬ ВЫПОЛНИТЬ ОПЕРАЦИЮ.')
            + (uncertain ? ' НЕ ПОВТОРЯЙТЕ ЗАПИСЬ ДО ПОДТВЕРЖДЕНИЯ РЕЗУЛЬТАТА.' : checkpoint.retrySafe ? ' СПИСОК ОБНОВЛЕН. МОЖНО ПОВТОРИТЬ ПОПЫТКУ.' : '');
          setStatus(message, true);
          if (ownsUi()) onError?.(message);
        }
      } finally {
        request.release();
      }
      if (completed && ownsUi()) onSuccess?.(checkpoint.confirmed);
      return completed;
    }

    async function submitMobileCashboxAction() {
      if (!requireEmployeesCashboxesAccess()) return;
      const action = String(state.mobileCashboxAction || '');
      const cashbox = state.activeCashbox?.cashbox
        || (Array.isArray(state.cashboxes) ? state.cashboxes.find((item) => item.id === state.activeCashboxId) : null)
        || null;
      if (!cashbox?.id) return setStatus('СНАЧАЛА ВЫБЕРИТЕ КАССУ.', true);
      const amount = String(els.mobileCashboxAmountInput?.value || '').trim();
      if (!amount || repairOrderParseNumber(amount) === null || repairOrderParseNumber(amount) <= 0) {
        return setStatus('УКАЖИТЕ СУММУ.', true);
      }
      const note = String(els.mobileCashboxNoteInput?.value || '').trim();
      if (action === 'expense' && !cashboxExpenseNoteIsValid(note)) {
        els.mobileCashboxNoteInput?.focus({ preventScroll: true });
        return setStatus('ДЛЯ СПИСАНИЯ УКАЖИТЕ КОММЕНТАРИЙ НЕ КОРОЧЕ 10 СИМВОЛОВ.', true);
      }
      let path = '/api/create_cash_transaction';
      let body = { cashbox_id: cashbox.id, direction: action === 'expense' ? 'expense' : 'income', amount, note, actor_name: state.actor, source: 'ui' };
      if (action === 'transfer') {
        const targetId = String(els.mobileCashboxTargetSelect?.value || '').trim();
        if (!targetId || targetId === cashbox.id) return setStatus('УКАЖИТЕ КАССУ ДЛЯ ПЕРЕМЕЩЕНИЯ.', true);
        path = '/api/create_cashbox_transfer';
        body = { from_cashbox_id: cashbox.id, to_cashbox_id: targetId, amount, note, actor_name: state.actor, source: 'ui' };
      }
      return runCashboxMoneyMutation(path, body, {
        receipt: (data) => action === 'transfer' ? data?.source_transaction?.id && data?.target_transaction?.id : data?.transaction?.id,
        onSuccess: () => {
          if (els.mobileCashboxAmountInput) els.mobileCashboxAmountInput.value = '';
          if (els.mobileCashboxNoteInput) els.mobileCashboxNoteInput.value = '';
          state.mobileCashboxAction = '';
          renderMobileCashboxes();
          setStatus(action === 'transfer' ? 'ПЕРЕМЕЩЕНИЕ СОХРАНЕНО.' : action === 'expense' ? 'СПИСАНИЕ СОХРАНЕНО.' : 'ПОСТУПЛЕНИЕ СОХРАНЕНО.', false);
        },
      });
    }
