    function setCashboxNoteInvalid(isInvalid) {
      if (!els.cashboxNoteInput) return;
      els.cashboxNoteInput.classList.toggle('is-invalid', Boolean(isInvalid));
      if (isInvalid) els.cashboxNoteInput.setAttribute('aria-invalid', 'true');
      else els.cashboxNoteInput.removeAttribute('aria-invalid');
    }

    function cashboxExpenseNoteIsValid(note) {
      return String(note || '').trim().length >= CASHBOX_EXPENSE_NOTE_MIN_LENGTH;
    }

    function handleCashboxNoteInput() {
      if (cashboxExpenseNoteIsValid(els.cashboxNoteInput?.value || '')) {
        setCashboxNoteInvalid(false);
      }
    }

    async function createCashboxTransaction(direction) {
      const cashbox = state.activeCashbox?.cashbox || null;
      if (!cashbox?.id) {
        setStatus('СНАЧАЛА ВЫБЕРИТЕ КАССУ.', true);
        return;
      }
      const amount = String(els.cashboxAmountInput.value || '').trim();
      if (!amount) {
        setStatus('УКАЖИТЕ СУММУ.', true);
        return;
      }
      const normalizedDirection = direction === 'expense' ? 'expense' : 'income';
      const note = String(els.cashboxNoteInput.value || '').trim();
      if (normalizedDirection === 'expense' && !cashboxExpenseNoteIsValid(note)) {
        setCashboxNoteInvalid(true);
        els.cashboxNoteInput.focus();
        setStatus('ДЛЯ СПИСАНИЯ УКАЖИТЕ КОММЕНТАРИЙ НЕ КОРОЧЕ 10 СИМВОЛОВ.', true);
        return;
      }
      setCashboxNoteInvalid(false);
      try {
        els.cashboxIncomeButton.disabled = true;
        els.cashboxExpenseButton.disabled = true;
        await api('/api/create_cash_transaction', {
          method: 'POST',
          body: {
            cashbox_id: cashbox.id,
            direction: normalizedDirection,
            amount,
            note,
            actor_name: state.actor,
            source: 'ui',
          },
        });
        els.cashboxAmountInput.value = '';
        els.cashboxNoteInput.value = '';
        await refreshCashboxesAfterMoneyMutation({ openModal: true, deferDetail: false });
        setStatus(direction === 'expense' ? 'СПИСАНИЕ СОХРАНЕНО.' : 'ПОСТУПЛЕНИЕ СОХРАНЕНО.', false);
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        els.cashboxIncomeButton.disabled = false;
        els.cashboxExpenseButton.disabled = false;
      }
    }

    function setCashboxCancelReasonInvalid(isInvalid) {
      if (!els.cashboxCancelReasonInput) return;
      els.cashboxCancelReasonInput.classList.toggle('is-invalid', Boolean(isInvalid));
      if (isInvalid) els.cashboxCancelReasonInput.setAttribute('aria-invalid', 'true');
      else els.cashboxCancelReasonInput.removeAttribute('aria-invalid');
    }

    function setCashboxCancelFeedback(message = '', isError = false) {
      if (!els.cashboxCancelFeedback) return;
      const text = String(message || '').trim();
      els.cashboxCancelFeedback.textContent = text;
      els.cashboxCancelFeedback.hidden = !text;
      els.cashboxCancelFeedback.dataset.tone = isError ? 'error' : 'normal';
    }

    function closeCashboxCancelPopover() {
      state.cashboxCancelTransactionId = '';
      if (els.cashboxCancelPopover) els.cashboxCancelPopover.hidden = true;
      if (els.cashboxCancelReasonInput) {
        els.cashboxCancelReasonInput.value = '';
        setCashboxCancelReasonInvalid(false);
      }
      setCashboxCancelFeedback();
      if (els.cashboxCancelConfirmButton) els.cashboxCancelConfirmButton.disabled = false;
    }

    function openCashboxCancelPopover(transactionId) {
      const transaction = cashboxTransactionById(transactionId);
      if (!transaction || !cashboxTransactionCanBeCancelled(transaction)) {
        setStatus('ЭТУ ОПЕРАЦИЮ НЕЛЬЗЯ ОТМЕНИТЬ.', true);
        return;
      }
      state.cashboxCancelTransactionId = String(transaction.id || '').trim();
      const amount = cashboxFormatMinorAmount(transaction.amount_minor ?? 0).replace(/^-/, '');
      const note = String(transaction.note || '').trim() || 'Без комментария';
      if (els.cashboxCancelMeta) {
        els.cashboxCancelMeta.textContent = note + ' · ' + amount;
      }
      if (els.cashboxCancelReasonInput) {
        els.cashboxCancelReasonInput.value = '';
        setCashboxCancelReasonInvalid(false);
      }
      setCashboxCancelFeedback();
      if (els.cashboxCancelPopover) els.cashboxCancelPopover.hidden = false;
      window.setTimeout(() => {
        els.cashboxCancelReasonInput?.focus();
      }, 0);
    }

    async function submitCashboxTransactionCancellation() {
      const cashbox = state.activeCashbox?.cashbox || null;
      const transaction = cashboxTransactionById(state.cashboxCancelTransactionId);
      if (!cashbox?.id || !transaction?.id) {
        setStatus('ВЫБЕРИТЕ ОПЕРАЦИЮ ДЛЯ ОТМЕНЫ.', true);
        return;
      }
      const reason = String(els.cashboxCancelReasonInput?.value || '').trim();
      if (reason.length < CASHBOX_CANCEL_REASON_MIN_LENGTH) {
        setCashboxCancelReasonInvalid(true);
        els.cashboxCancelReasonInput?.focus();
        setCashboxCancelFeedback('Причина отмены должна быть не короче 10 символов.', true);
        setStatus('ПРИЧИНА ОТМЕНЫ ДОЛЖНА БЫТЬ НЕ КОРОЧЕ 10 СИМВОЛОВ.', true);
        return;
      }
      try {
        if (els.cashboxCancelConfirmButton) els.cashboxCancelConfirmButton.disabled = true;
        setCashboxCancelFeedback('Отменяю операцию…');
        await api('/api/cancel_cash_transaction', {
          method: 'POST',
          body: {
            cashbox_id: cashbox.id,
            transaction_id: transaction.id,
            reason,
            actor_name: state.actor,
            source: 'ui',
          },
        });
        closeCashboxCancelPopover();
        await refreshCashboxesAfterMoneyMutation({ openModal: true, deferDetail: false });
        setStatus('ОПЕРАЦИЯ ОТМЕНЕНА.', false);
      } catch (error) {
        const message = String(error?.message || 'НЕ УДАЛОСЬ ОТМЕНИТЬ ОПЕРАЦИЮ.');
        setCashboxCancelFeedback(message, true);
        setStatus(message, true);
      } finally {
        if (els.cashboxCancelConfirmButton) els.cashboxCancelConfirmButton.disabled = false;
      }
    }

    async function handleCashboxTransactionsClick(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const cancelButton = target.closest('[data-cashbox-transaction-cancel]');
      if (cancelButton instanceof HTMLButtonElement) {
        event.preventDefault();
        openCashboxCancelPopover(cancelButton.getAttribute('data-cashbox-transaction-cancel'));
        return;
      }
      const button = target.closest('[data-cashbox-transactions-load-more]');
      if (!(button instanceof HTMLButtonElement)) return;
      event.preventDefault();
      button.disabled = true;
      try {
        await loadMoreCashboxTransactions();
      } finally {
        button.disabled = false;
      }
    }
