    function setCashboxNoteInvalid(isInvalid) {
      if (!els.cashboxNoteInput) return;
      els.cashboxNoteInput.classList.toggle('is-invalid', Boolean(isInvalid));
      if (isInvalid) els.cashboxNoteInput.setAttribute('aria-invalid', 'true');
      else els.cashboxNoteInput.removeAttribute('aria-invalid');
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
      return runCashboxMoneyMutation('/api/create_cash_transaction', {
        cashbox_id: cashbox.id,
        direction: normalizedDirection,
        amount,
        note,
        actor_name: state.actor,
        source: 'ui',
      }, {
        receipt: (data) => data?.transaction?.id,
        onSuccess: () => {
          els.cashboxAmountInput.value = '';
          els.cashboxNoteInput.value = '';
          setStatus(direction === 'expense' ? 'СПИСАНИЕ СОХРАНЕНО.' : 'ПОСТУПЛЕНИЕ СОХРАНЕНО.', false);
        },
      });
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
      if (state.cashboxMoneyRequest) return false;
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
      if (state.cashboxMoneyRequest) return;
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
      setCashboxCancelFeedback('Отменяю операцию…');
      return runCashboxMoneyMutation('/api/cancel_cash_transaction', {
        cashbox_id: cashbox.id,
        transaction_id: transaction.id,
        reason,
        actor_name: state.actor,
        source: 'ui',
      }, {
        onError: (message) => setCashboxCancelFeedback(message, true),
        receipt: (data) => data?.meta?.cancelled && data?.cancellation_transaction?.id,
        verifyRead: (detail) => (detail?.transactions || []).some((item) => item.id === transaction.id && item.transaction_kind === 'cashbox_cancelled'),
        onSuccess: () => {
          closeCashboxCancelPopover();
          setStatus('ОПЕРАЦИЯ ОТМЕНЕНА.', false);
        },
      });
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
