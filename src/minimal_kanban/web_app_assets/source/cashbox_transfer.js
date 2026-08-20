    function closeCashboxTransferModal() {
      popModal('cashbox-transfer');
      state.cashboxTransferDraft = {
        sourceId: '',
        targetId: '',
        amount: '',
        note: '',
      };
      if (els.cashboxTransferAmountInput) els.cashboxTransferAmountInput.value = '';
      if (els.cashboxTransferNoteInput) els.cashboxTransferNoteInput.value = '';
    }

    async function createCashboxTransfer() {
      const sourceCashbox = state.activeCashbox?.cashbox || null;
      if (!sourceCashbox?.id) {
        setStatus('СНАЧАЛА ВЫБЕРИТЕ КАССУ.', true);
        return;
      }
      const availableCashboxes = (Array.isArray(state.cashboxes) ? state.cashboxes : []).filter((item) => item.id !== sourceCashbox.id);
      if (!availableCashboxes.length) {
        setStatus('НЕТ ДРУГОЙ КАССЫ ДЛЯ ПЕРЕМЕЩЕНИЯ.', true);
        return;
      }
      state.cashboxTransferDraft = {
        sourceId: sourceCashbox.id,
        targetId: availableCashboxes[0]?.id || '',
        amount: '',
        note: '',
      };
      if (els.cashboxTransferAmountInput) els.cashboxTransferAmountInput.value = '';
      if (els.cashboxTransferNoteInput) els.cashboxTransferNoteInput.value = '';
      renderCashboxTransferModal();
      maybeOpenModal(els.cashboxTransferModal, true);
    }

    function cashboxTransferAmountMinor() {
      const parsed = repairOrderParseNumber(els.cashboxTransferAmountInput?.value ?? state.cashboxTransferDraft?.amount ?? '');
      if (parsed === null || parsed <= 0) return null;
      return Math.round(parsed * 100);
    }

    function renderCashboxTransferBalanceRow(label, amountMinor) {
      const sign = cashboxBalanceSign(amountMinor);
      return '<div class="cashbox-transfer-preview__row">'
        + '<span>' + escapeHtml(label) + '</span>'
        + '<strong class="cashbox-transfer-preview__amount" data-balance-sign="' + escapeHtml(sign) + '">' + escapeHtml(cashboxFormatMinorAmount(amountMinor)) + '</strong>'
        + '</div>';
    }

    function renderCashboxTransferPreview(sourceCashbox, targetCashbox) {
      if (!els.cashboxTransferPreview) return;
      const amountMinor = cashboxTransferAmountMinor();
      if (!sourceCashbox?.id || !targetCashbox?.id) {
        els.cashboxTransferPreview.innerHTML = '<div class="cashbox-transfer-preview__label">Выберите кассу для перемещения.</div>';
        return;
      }
      if (amountMinor === null) {
        els.cashboxTransferPreview.innerHTML = '<div class="cashbox-transfer-preview__label">Введите сумму, чтобы увидеть баланс до и после.</div>';
        return;
      }
      const sourceBefore = cashboxBalanceMinor(sourceCashbox);
      const targetBefore = cashboxBalanceMinor(targetCashbox);
      const sourceAfter = sourceBefore - amountMinor;
      const targetAfter = targetBefore + amountMinor;
      els.cashboxTransferPreview.innerHTML = '<div class="cashbox-transfer-preview__label">'
        + escapeHtml(String(sourceCashbox.name || 'Касса') + ' → ' + String(targetCashbox.name || 'Касса'))
        + '</div>'
        + '<div class="cashbox-transfer-preview__rows">'
        + renderCashboxTransferBalanceRow('Откуда сейчас', sourceBefore)
        + renderCashboxTransferBalanceRow('Откуда после', sourceAfter)
        + renderCashboxTransferBalanceRow('Куда сейчас', targetBefore)
        + renderCashboxTransferBalanceRow('Куда после', targetAfter)
        + '</div>';
    }

    function renderCashboxTransferModal() {
      const sourceId = String(state.cashboxTransferDraft?.sourceId || state.activeCashbox?.cashbox?.id || '').trim();
      const sourceCashbox = (Array.isArray(state.cashboxes) ? state.cashboxes : []).find((item) => item.id === sourceId) || state.activeCashbox?.cashbox || null;
      const availableCashboxes = (Array.isArray(state.cashboxes) ? state.cashboxes : []).filter((item) => item.id !== sourceId);
      els.cashboxTransferSourceName.textContent = sourceCashbox?.name || 'КАССА НЕ ВЫБРАНА';
      if (els.cashboxTransferSourceBalance) {
        els.cashboxTransferSourceBalance.textContent = sourceCashbox ? ('Баланс: ' + cashboxBalanceDisplay(sourceCashbox)) : '';
      }
      els.cashboxTransferTargets.innerHTML = availableCashboxes.length ? availableCashboxes.map((item) => {
        const activeClass = item.id === state.cashboxTransferDraft.targetId ? ' is-active' : '';
        return '<button class="cashbox-transfer-target' + activeClass + '" type="button" data-cashbox-transfer-target="' + escapeHtml(item.id) + '">'
          + '<div class="cashbox-transfer-target__name">' + escapeHtml(item.name || '—') + '</div>'
          + '<div class="cashbox-transfer-target__balance">' + escapeHtml(cashboxBalanceDisplay(item)) + '</div>'
          + '</button>';
      }).join('') : '<div class="cashboxes-empty">НЕТ ДРУГИХ КАСС.</div>';
      const selectedTarget = availableCashboxes.find((item) => item.id === state.cashboxTransferDraft.targetId) || availableCashboxes[0] || null;
      if (selectedTarget && selectedTarget.id !== state.cashboxTransferDraft.targetId) {
        state.cashboxTransferDraft.targetId = selectedTarget.id;
      }
      if (els.cashboxTransferConfirmButton) {
        const hasAmount = cashboxTransferAmountMinor() !== null;
        els.cashboxTransferConfirmButton.disabled = !selectedTarget || !sourceCashbox || !hasAmount;
      }
      renderCashboxTransferPreview(sourceCashbox, selectedTarget);
    }

    function setCashboxTransferTarget(cashboxId) {
      const requestedId = String(cashboxId || '').trim();
      const sourceId = String(state.cashboxTransferDraft?.sourceId || '').trim();
      if (!requestedId || requestedId === sourceId) return;
      state.cashboxTransferDraft.targetId = requestedId;
      renderCashboxTransferModal();
    }

    async function submitCashboxTransfer() {
      const sourceCashbox = (Array.isArray(state.cashboxes) ? state.cashboxes : []).find((item) => item.id === state.cashboxTransferDraft.sourceId) || null;
      const targetCashbox = (Array.isArray(state.cashboxes) ? state.cashboxes : []).find((item) => item.id === state.cashboxTransferDraft.targetId) || null;
      if (!sourceCashbox?.id) {
        setStatus('СНАЧАЛА ВЫБЕРИТЕ КАССУ.', true);
        return;
      }
      if (!targetCashbox?.id || targetCashbox.id === sourceCashbox.id) {
        setStatus('УКАЖИТЕ КАССУ ДЛЯ ПЕРЕМЕЩЕНИЯ.', true);
        return;
      }
      const amount = String(els.cashboxTransferAmountInput?.value || '').trim();
      if (!amount) {
        setStatus('УКАЖИТЕ СУММУ.', true);
        return;
      }
      try {
        els.cashboxTransferConfirmButton.disabled = true;
        els.cashboxTransferButton.disabled = true;
        await api('/api/create_cashbox_transfer', {
          method: 'POST',
          body: {
            from_cashbox_id: sourceCashbox.id,
            to_cashbox_id: targetCashbox.id,
            amount,
            note: String(els.cashboxTransferNoteInput?.value || '').trim(),
            actor_name: state.actor,
            source: 'ui',
          },
        });
        if (els.cashboxTransferAmountInput) els.cashboxTransferAmountInput.value = '';
        if (els.cashboxTransferNoteInput) els.cashboxTransferNoteInput.value = '';
        closeCashboxTransferModal();
        await refreshCashboxesAfterMoneyMutation({ openModal: true, deferDetail: false });
        setStatus('ПЕРЕМЕЩЕНИЕ СОХРАНЕНО.', false);
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        els.cashboxTransferConfirmButton.disabled = false;
        els.cashboxTransferButton.disabled = false;
      }
    }

    function handleCashboxTransferAmountInput() {
      state.cashboxTransferDraft.amount = String(els.cashboxTransferAmountInput?.value || '').trim();
      renderCashboxTransferModal();
    }

    function handleCashboxTransferNoteInput() {
      state.cashboxTransferDraft.note = String(els.cashboxTransferNoteInput?.value || '');
    }

    function handleCashboxTransferTargetsClick(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const button = target.closest('[data-cashbox-transfer-target]');
      if (!button) return;
      setCashboxTransferTarget(button.getAttribute('data-cashbox-transfer-target'));
    }
