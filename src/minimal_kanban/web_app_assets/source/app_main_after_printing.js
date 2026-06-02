
    function openFilePickerFromDropzone() {
      if (!requireSavedCardForFiles()) return;
      els.fileInput.click();
    }

    function handleFileDropzoneKeydown(event) {
      const isPasteShortcut = (event.ctrlKey || event.metaKey) && String(event.key || '').toLowerCase() === 'v';
      if (isPasteShortcut) return;
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        openFilePickerFromDropzone();
        return;
      }
      if (event.key.length === 1 || event.key === 'Backspace' || event.key === 'Delete') {
        event.preventDefault();
      }
    }

    function handleFileDropzoneBeforeInput(event) {
      if (event.inputType && event.inputType.startsWith('insert')) event.preventDefault();
    }

    function handleFileDropzoneInput() {
      els.fileDropzone.textContent = '';
    }

    function handleFileDropzoneDragEnter(event) {
      if (!event.dataTransfer) return;
      event.preventDefault();
      if (!state.editingId) return;
      els.fileDropzone.classList.add('is-active');
    }

    function handleFileDropzoneDragOver(event) {
      if (!event.dataTransfer) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = state.editingId ? 'copy' : 'none';
      if (!state.editingId) return;
      els.fileDropzone.classList.add('is-active');
    }

    function handleFileDropzoneDragLeave(event) {
      if (!(event.target instanceof HTMLElement)) return;
      if (event.target !== els.fileDropzone) return;
      els.fileDropzone.classList.remove('is-active');
    }

    async function handleFileDropzoneDrop(event) {
      if (!event.dataTransfer) return;
      event.preventDefault();
      if (!requireSavedCardForFiles()) {
        els.fileDropzone.classList.remove('is-active');
        return;
      }
      await uploadProvidedFiles(event.dataTransfer.files);
    }

    async function handleFileDropzonePaste(event) {
      event.preventDefault();
      if (!requireSavedCardForFiles()) return;
      const files = collectClipboardAttachmentFiles(event);
      if (!files.length) {
        setStatus('В БУФЕРЕ НЕТ ФАЙЛА ИЛИ ТЕКСТА ДЛЯ ВЛОЖЕНИЯ.', true);
        return;
      }
      await uploadProvidedFiles(files);
    }

    function handleCardSeenPointerOver(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const card = target.closest('.card');
      if (!card) return;
      const hasUnreadMarker = card.dataset.unread === 'true';
      const hasUpdatedMarker = card.dataset.updatedUnseen === 'true';
      if (!hasUnreadMarker && !hasUpdatedMarker) return;
      const relatedTarget = event.relatedTarget;
      if (relatedTarget instanceof HTMLElement && card.contains(relatedTarget)) return;
      scheduleCardSeen(card.dataset.cardId);
    }

    function handleCardSeenPointerOut(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const card = target.closest('.card');
      if (!card) return;
      const relatedTarget = event.relatedTarget;
      if (relatedTarget instanceof HTMLElement && card.contains(relatedTarget)) return;
      clearUnreadHoverTimer(card.dataset.cardId);
    }

    function handleBoardCardDragStart(event) {
      if (state.mobileLite) return;
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const card = target.closest('.card');
      if (!card) return;
      state.boardDragCardId = card.dataset.cardId || '';
      card.classList.add('is-dragging');
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('text/plain', state.boardDragCardId);
      }
    }

    function handleBoardColumnDragStart(event) {
      if (state.mobileLite) return;
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      if (target.closest('.card')) return;
      if (target.closest('button, input, textarea, select, a, label')) return;
      const column = target.closest('.column');
      if (!(column instanceof HTMLElement)) return;
      state.boardDragColumnId = column.dataset.columnId || '';
      column.classList.add('is-column-dragging');
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('application/x-kanban-column', state.boardDragColumnId);
      }
    }

    function handleBoardCardDragOver(event) {
      if (state.mobileLite) return;
      const draggedCardId = state.boardDragCardId || event.dataTransfer?.getData('text/plain') || '';
      if (!draggedCardId) return;
      event.preventDefault();
      updateBoardDragAutoScroll(event.clientX, event.clientY);
      const rawTarget = event.target;
      const target = rawTarget instanceof Element
        ? rawTarget
        : (rawTarget instanceof Node ? rawTarget.parentElement : null);
      if (!(target instanceof Element)) return;
      const column = target.closest('.column');
      if (!column) {
        clearCardDropState();
        return;
      }
      const beforeCardId = resolveDropBeforeCardId(column, event.clientY, draggedCardId);
      updateCardDropState(column, beforeCardId);
    }

    function handleBoardColumnDragOver(event) {
      if (state.mobileLite) return;
      const draggedColumnId = state.boardDragColumnId || event.dataTransfer?.getData('application/x-kanban-column') || '';
      if (!draggedColumnId) return;
      event.preventDefault();
      updateBoardDragAutoScroll(event.clientX, event.clientY);
      const target = event.target instanceof Element
        ? event.target
        : (event.target instanceof Node ? event.target.parentElement : null);
      if (!(target instanceof Element)) return;
      const column = target.closest('.column');
      if (!(column instanceof HTMLElement)) {
        clearColumnDropState();
        return;
      }
      const hoveredColumnId = String(column.dataset.columnId || '').trim();
      if (!hoveredColumnId || hoveredColumnId === draggedColumnId) {
        clearColumnDropState();
        return;
      }
      state.boardDropBeforeColumnId = resolveDropBeforeColumnId(column, event.clientX, draggedColumnId);
      document.querySelectorAll('.column.is-column-drop-target').forEach((item) => item.classList.remove('is-column-drop-target'));
      column.classList.add('is-column-drop-target');
    }

    function handleBoardCardDragLeave(event) {
      if (state.mobileLite) return;
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const column = target.closest('.column');
      if (!column) return;
      const relatedTarget = event.relatedTarget;
      if (relatedTarget instanceof HTMLElement && column.contains(relatedTarget)) return;
      clearCardDropState();
    }

    function handleBoardColumnDragLeave(event) {
      if (state.mobileLite) return;
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const column = target.closest('.column');
      if (!column) return;
      const relatedTarget = event.relatedTarget;
      if (relatedTarget instanceof HTMLElement && column.contains(relatedTarget)) return;
      clearColumnDropState();
    }

    async function handleBoardCardDrop(event) {
      if (state.mobileLite) return;
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const column = target.closest('.column');
      if (!column) return;
      event.preventDefault();
      const cardId = state.boardDragCardId || event.dataTransfer?.getData('text/plain') || '';
      const columnId = state.boardDropColumnId || column.dataset.columnId || '';
      const beforeCardId = state.boardDropBeforeCardId || '';
      if (cardId && columnId) {
        await moveCard(cardId, columnId, beforeCardId);
      } else {
        finishCardDrag();
      }
    }

    async function handleBoardColumnDrop(event) {
      if (state.mobileLite) return;
      const draggedColumnId = state.boardDragColumnId || event.dataTransfer?.getData('application/x-kanban-column') || '';
      if (!draggedColumnId) return;
      const target = event.target;
      if (!(target instanceof HTMLElement)) {
        finishColumnDrag();
        return;
      }
      const column = target.closest('.column');
      if (!(column instanceof HTMLElement)) {
        finishColumnDrag();
        return;
      }
      event.preventDefault();
      const hoveredColumnId = String(column.dataset.columnId || '').trim();
      const beforeColumnId = state.boardDropBeforeColumnId || resolveDropBeforeColumnId(column, event.clientX, draggedColumnId);
      if (hoveredColumnId && hoveredColumnId !== draggedColumnId) {
        await moveColumn(draggedColumnId, beforeColumnId);
      } else {
        finishColumnDrag();
      }
    }

    document.addEventListener('click', async (event) => {
      const rawTarget = event.target;
      const target = rawTarget instanceof Element
        ? rawTarget
        : (rawTarget instanceof Node ? rawTarget.parentElement : null);
      if (!(target instanceof Element)) return;
      const closeTrigger = target.closest('[data-close]');
      if (closeTrigger instanceof HTMLElement) {
        event.preventDefault();
        event.stopPropagation();
        closeNamedModal(closeTrigger.dataset.close);
        return;
      }
      const tabTrigger = target.closest('[data-tab]');
      if (tabTrigger instanceof HTMLElement) setTab(tabTrigger.dataset.tab);
      const cardJournalLoadMoreTrigger = target.closest('[data-card-journal-load-more]');
      if (cardJournalLoadMoreTrigger instanceof HTMLElement) {
        event.preventDefault();
        event.stopPropagation();
        handleCardJournalLoadMore(cardJournalLoadMoreTrigger);
        return;
      }
      const linkClientTarget = target.closest('[data-link-client]');
      if (linkClientTarget instanceof HTMLElement) {
        event.preventDefault();
        event.stopPropagation();
        await linkActiveCardToClient(linkClientTarget.dataset.linkClient);
        return;
      }
      const closeClientSuggestionsTarget = target.closest('[data-close-client-suggestions]');
      if (closeClientSuggestionsTarget instanceof HTMLElement) {
        event.preventDefault();
        event.stopPropagation();
        hideClientSuggestions();
        return;
      }
      const selectClientVehicleTarget = target.closest('[data-select-client-vehicle]');
      if (selectClientVehicleTarget instanceof HTMLElement) {
        event.preventDefault();
        event.stopPropagation();
        await chooseClientSuggestion(
          selectClientVehicleTarget.dataset.clientId,
          selectClientVehicleTarget.dataset.selectClientVehicle,
        );
        return;
      }
      const selectClientNewVehicleTarget = target.closest('[data-select-client-new-vehicle]');
      if (selectClientNewVehicleTarget instanceof HTMLElement) {
        event.preventDefault();
        event.stopPropagation();
        await chooseClientSuggestion(selectClientNewVehicleTarget.dataset.selectClientNewVehicle, '', { createNewVehicle: true });
        return;
      }
      const loadClientVehiclesTarget = target.closest('[data-load-client-vehicles]');
      if (loadClientVehiclesTarget instanceof HTMLElement) {
        event.preventDefault();
        event.stopPropagation();
        await loadClientSuggestionVehicles(loadClientVehiclesTarget.dataset.loadClientVehicles);
        return;
      }
      const selectClientSuggestionTarget = target.closest('[data-client-suggestion], [data-select-client-suggestion]');
      if (selectClientSuggestionTarget instanceof HTMLElement) {
        event.preventDefault();
        event.stopPropagation();
        await chooseClientSuggestion(
          selectClientSuggestionTarget.dataset.clientSuggestion
            || selectClientSuggestionTarget.dataset.selectClientSuggestion,
        );
        return;
      }
      const openCardClientCreateTarget = target.closest('[data-open-card-client-create]');
      if (openCardClientCreateTarget instanceof HTMLElement) {
        event.preventDefault();
        event.stopPropagation();
        openCardClientCreateModal();
        return;
      }
      const saveCardClientCreateTarget = target.closest('[data-card-client-create-save]');
      if (saveCardClientCreateTarget instanceof HTMLElement) {
        event.preventDefault();
        event.stopPropagation();
        await saveCardClientFromPopup();
        return;
      }
      const addCardClientPhoneTarget = target.closest('[data-card-client-phone-add]');
      if (addCardClientPhoneTarget instanceof HTMLElement) {
        event.preventDefault();
        event.stopPropagation();
        addCardClientCreatePhoneField();
        return;
      }
      const removeCardClientPhoneTarget = target.closest('[data-card-client-phone-remove]');
      if (removeCardClientPhoneTarget instanceof HTMLElement) {
        event.preventDefault();
        event.stopPropagation();
        removeCardClientCreatePhoneField(removeCardClientPhoneTarget.dataset.cardClientPhoneRemove);
        return;
      }
      const createClientSuggestionTarget = target.closest('[data-client-suggest-create]');
      if (createClientSuggestionTarget instanceof HTMLElement) {
        event.preventDefault();
        event.stopPropagation();
        await createClientFromCardSuggestion();
        return;
      }
      const addVehiclePhoneTarget = target.closest('[data-vehicle-phone-add]');
      if (addVehiclePhoneTarget instanceof HTMLElement) {
        event.preventDefault();
        event.stopPropagation();
        addVehicleCustomerPhoneField();
        return;
      }
      const removeVehiclePhoneTarget = target.closest('[data-vehicle-phone-remove]');
      if (removeVehiclePhoneTarget instanceof HTMLElement) {
        event.preventDefault();
        event.stopPropagation();
        removeVehicleCustomerPhoneField(removeVehiclePhoneTarget.dataset.vehiclePhoneRemove);
        return;
      }
      if (els.clientMatchPanel?.classList.contains('is-visible') && !target.closest('#clientMatchPanel') && !target.closest('[data-vehicle-field-input="customer_name"]') && !target.closest('[data-vehicle-field-input="customer_phone"]') && !target.closest('[data-vehicle-field-input="vin"]') && !target.closest('[data-vehicle-field-input="registration_plate"]')) {
        hideClientSuggestions();
      }
      const openRepairOrderModalTarget = target.closest('[data-open-repair-order-modal]');
      if (openRepairOrderModalTarget) {
        event.preventDefault();
        event.stopPropagation();
        openRepairOrderModal();
        return;
      }
      const openRepairOrderCardTarget = target.closest('[data-open-repair-order-card]');
      if (openRepairOrderCardTarget) {
        await openRepairOrderCard(openRepairOrderCardTarget.dataset.openRepairOrderCard, {
          parentLayer: repairOrderParentLayerFromTrigger(openRepairOrderCardTarget),
        });
        return;
      }
      const addRepairOrderRowButton = target.closest('[data-add-repair-order-row]');
      if (addRepairOrderRowButton) {
        await addRepairOrderRow(addRepairOrderRowButton.dataset.addRepairOrderRow);
        return;
      }
      const workSalaryGearButton = target.closest('[data-repair-order-work-salary-gear]');
      if (workSalaryGearButton) {
        event.preventDefault();
        event.stopPropagation();
        openRepairOrderWorkSalaryPopover(workSalaryGearButton);
        return;
      }
      const workSalaryApplyButton = target.closest('[data-repair-order-work-salary-apply]');
      if (workSalaryApplyButton) {
        event.preventDefault();
        event.stopPropagation();
        applyRepairOrderWorkSalaryPopover();
        return;
      }
      const workSalaryResetButton = target.closest('[data-repair-order-work-salary-reset]');
      if (workSalaryResetButton) {
        event.preventDefault();
        event.stopPropagation();
        resetRepairOrderWorkSalaryOverride();
        return;
      }
      if (els.repairOrderWorkSalaryPopover?.classList.contains('is-open') && !target.closest('#repairOrderWorkSalaryPopover')) {
        closeRepairOrderWorkSalaryPopover();
      }
      const removeRepairOrderRowButton = target.closest('[data-remove-repair-order-row]');
      if (removeRepairOrderRowButton) {
        removeRepairOrderRow(
          removeRepairOrderRowButton.dataset.removeRepairOrderRow,
          Number(removeRepairOrderRowButton.dataset.rowIndex || '-1')
        );
        return;
      }
      const renameColumnButton = target.closest('[data-rename-column]');
      if (renameColumnButton) {
        await renameColumnFromButton(renameColumnButton);
        return;
      }
      const deleteColumnButton = target.closest('[data-delete-column]');
      if (deleteColumnButton) {
        await deleteColumnFromButton(deleteColumnButton);
        return;
      }
      const createInTrigger = target.closest('[data-create-in]');
      if (createInTrigger instanceof HTMLElement) openNewCardInColumn(createInTrigger.dataset.createIn);
      if (await handleAuxiliaryBoardClick(target, event)) return;
      if (await handleCardWorkspaceClick(target)) return;
    });

    document.addEventListener('pointerover', handleCardSeenPointerOver);
    document.addEventListener('pointerout', handleCardSeenPointerOut);
    document.addEventListener('dragstart', handleBoardColumnDragStart);
    document.addEventListener('dragstart', handleBoardCardDragStart);
    document.addEventListener('dragover', handleBoardColumnDragOver);
    document.addEventListener('dragover', handleBoardCardDragOver);
    document.addEventListener('dragleave', handleBoardColumnDragLeave);
    document.addEventListener('dragleave', handleBoardCardDragLeave);
    document.addEventListener('drop', handleBoardColumnDrop);
    document.addEventListener('drop', handleBoardCardDrop);
    document.addEventListener('dragend', finishBoardDrag);
    els.boardScroll.addEventListener('pointerdown', beginBoardPan);
    els.boardScroll.addEventListener('pointermove', moveBoardPan);
    els.boardScroll.addEventListener('pointerup', endBoardPan);
    els.boardScroll.addEventListener('pointercancel', endBoardPan);
    els.boardScroll.addEventListener('lostpointercapture', endBoardPan);
    window.addEventListener('resize', () => { adjustBoardBounds(); });
    document.addEventListener('pointerdown', beginStickyDrag);
    document.addEventListener('pointermove', moveStickyDrag);
    document.addEventListener('pointerup', endStickyDrag);
    document.addEventListener('pointercancel', endStickyDrag);

    /* Legacy pre-session operator listeners removed.
      const actor = els.identityInput.value.trim().toUpperCase();
      if (!actor) return setStatus('НУЖНО УКАЗАТЬ ИМЯ ОПЕРАТОРА.', true);
      state.actor = actor;
      sessionStorage.setItem('legacy-operator-unused', actor);
      ensureActor();
    });
    */
    function remountElement(key) {
      const element = els[key];
      if (!element) return null;
      const clone = element.cloneNode(true);
      element.replaceWith(clone);
      els[key] = clone;
      return clone;
    }

    remountElement('identitySave');
    remountElement('operatorButton');
    remountElement('operatorLogoutButton');
    remountElement('operatorAdminButton');
    remountElement('adminSaveUserButton');
    remountElement('operatorUserEmployeeSaveButton');
    remountElement('operatorUserEmployeeClearButton');
    remountElement('operatorUserEmployeeCancelButton');
    remountElement('operatorActivityExportButton');
    remountElement('sharedFilesButton');
    remountElement('sharedFilesUploadButton');
    remountElement('sharedFilesOpenButton');
    remountElement('sharedFilesDownloadButton');
    remountElement('sharedFilesRenameButton');
    remountElement('sharedFilesCopyButton');
    remountElement('sharedFilesPasteButton');
    remountElement('sharedFilesDeleteButton');
    remountElement('sharedFilesContextMenu');
    remountElement('cashboxesButton');
    remountElement('employeesButton');
    remountElement('cashboxCreateButton');
    remountElement('cashboxJournalButton');
    remountElement('cashboxJournalLedgerButton');
    remountElement('cashboxJournalStatsButton');
    remountElement('cashboxJournalDownloadButton');
    remountElement('cashboxDeleteButton');
    remountElement('cashboxCancelLastButton');
    remountElement('cashboxIncomeButton');
    remountElement('cashboxTransferButton');
    remountElement('cashboxExpenseButton');
    configureOperatorIdentityUi();

    els.identitySave.addEventListener('click', loginOperator);
    els.identityInput.addEventListener('keydown', handleIdentityInputKeydown);
    els.identityInput.addEventListener('input', handleIdentityCredentialInput);
    if (els.identityPassword) {
      els.identityPassword.addEventListener('keydown', handleIdentityPasswordKeydown);
      els.identityPassword.addEventListener('input', handleIdentityCredentialInput);
    }
    window.__AUTOSTOP_UI_BOUND__ = true;
    els.operatorButton.addEventListener('click', openOperatorWorkspace);
    els.operatorLogoutButton.addEventListener('click', logoutOperator);
    els.operatorAdminButton.addEventListener('click', openOperatorAdminModal);
    els.operatorAdminTabs?.addEventListener('click', handleOperatorAdminTabsClick);
    els.adminSaveUserButton.addEventListener('click', saveOperatorUser);
    els.adminUsersList.addEventListener('click', handleAdminUsersListClick);
    els.operatorUserEmployeeSaveButton?.addEventListener('click', () => saveOperatorEmployeeBinding());
    els.operatorUserEmployeeClearButton?.addEventListener('click', () => saveOperatorEmployeeBinding(''));
    els.operatorUserEmployeeCancelButton?.addEventListener('click', closeOperatorEmployeeBinding);
    els.operatorActivityDays?.addEventListener('change', handleOperatorActivityFilterChange);
    els.operatorActivityUserFilter?.addEventListener('change', handleOperatorActivityFilterChange);
    els.operatorActivityModuleFilter?.addEventListener('change', handleOperatorActivityFilterChange);
    els.operatorActivityActionFilter?.addEventListener('change', handleOperatorActivityFilterChange);
    els.operatorActivitySearchInput?.addEventListener('input', handleOperatorActivityFilterChange);
    els.operatorActivityExportButton?.addEventListener('click', exportOperatorActivity);
    els.operatorActivityTable?.addEventListener('click', handleOperatorActivityTableClick);
    els.operatorActivityTable?.addEventListener('keydown', handleOperatorActivityTableKeydown);
    window.addEventListener('resize', updateOperatorActivityScrollHint);

    els.boardSettingsButton.addEventListener('click', openBoardSettings);
    els.archiveButton.addEventListener('click', openArchiveModal);
    els.archiveSearchInput?.addEventListener('input', () => {
      state.archiveQuery = String(els.archiveSearchInput?.value || '').trim();
      renderArchive();
    });
    els.repairOrdersButton.addEventListener('click', openRepairOrdersModal);
    els.sharedFilesButton.addEventListener('click', openSharedFilesModal);
    els.cardAgentButton.addEventListener('click', runFullCardEnrichment);
    els.cashboxesButton.addEventListener('click', openCashboxesModal);
    els.employeesButton.addEventListener('click', openEmployeesModal);
    els.repairOrdersOpenTab.addEventListener('click', () => setRepairOrdersFilter('open'));
    els.repairOrdersReadyTab.addEventListener('click', () => setRepairOrdersFilter('ready'));
    els.repairOrdersClosedTab.addEventListener('click', () => setRepairOrdersFilter('closed'));
    els.repairOrdersSearchInput.addEventListener('input', handleRepairOrdersSearchInput);
    els.repairOrdersTableHead.addEventListener('click', handleRepairOrdersSearchFieldClick);
    els.repairOrdersSortBy.addEventListener('change', handleRepairOrdersSortChange);
    els.repairOrdersSortDir.addEventListener('change', handleRepairOrdersSortChange);
    els.boardSearchInput.addEventListener('input', scheduleBoardSearch);
    els.boardSearchInput.addEventListener('focus', openBoardSearchOnFocus);
    els.boardSearchInput.addEventListener('click', openBoardSearchOnFocus);
    els.boardSearchInput.addEventListener('keydown', handleBoardSearchKeydown);
    els.boardSearchClearButton.addEventListener('click', () => {
      clearBoardSearchState();
      els.boardSearchInput.focus();
    });
    els.boardSearchResults.addEventListener('click', handleBoardSearchResultsClick);
    document.addEventListener('click', handleBoardSearchDocumentClick);
    document.addEventListener('keydown', handleModalStackKeydown);
    els.cashboxCreateButton.addEventListener('click', createCashbox);
    els.cashboxJournalButton.addEventListener('click', openCashJournalModal);
    if (els.cashboxJournalLedgerButton) {
      els.cashboxJournalLedgerButton.addEventListener('click', handleCashJournalModeClick);
      els.cashboxJournalLedgerButton.addEventListener('keydown', handleCashJournalModeKeydown);
    }
    if (els.cashboxJournalStatsButton) {
      els.cashboxJournalStatsButton.addEventListener('click', handleCashJournalModeClick);
      els.cashboxJournalStatsButton.addEventListener('keydown', handleCashJournalModeKeydown);
    }
    els.cashboxJournalText.addEventListener('input', handleCashJournalFilterInput);
    els.cashboxJournalText.addEventListener('change', handleCashJournalFilterInput);
    els.cashboxJournalText.addEventListener('click', handleCashJournalResetClick);
    els.cashboxJournalText.addEventListener('click', handleCashJournalBalancesToggle);
    els.cashboxJournalText.addEventListener('click', handleCashJournalPeriodClick);
    els.cashboxJournalText.addEventListener('click', handleCashJournalStatsPeriodClick);
    els.cashboxJournalText.addEventListener('click', handleCashJournalLoadMoreClick);
    els.cashboxJournalDownloadButton.addEventListener('click', downloadCashJournal);
    els.cashboxDeleteButton.addEventListener('click', deleteActiveCashbox);
    els.sharedFilesUploadButton.addEventListener('click', () => els.sharedFilesInput.click());
    els.sharedFilesInput.addEventListener('change', () => uploadSharedFiles(els.sharedFilesInput.files));
    els.sharedFilesOpenButton.addEventListener('click', openActiveSharedFile);
    els.sharedFilesDownloadButton.addEventListener('click', downloadActiveSharedFile);
    els.sharedFilesRenameButton.addEventListener('click', renameActiveSharedFile);
    els.sharedFilesCopyButton.addEventListener('click', copyActiveSharedFile);
    els.sharedFilesPasteButton.addEventListener('click', () => pasteSharedFile());
    els.sharedFilesDeleteButton.addEventListener('click', deleteActiveSharedFile);
    els.sharedFilesDesktop.addEventListener('click', handleSharedFilesDesktopClick);
    els.sharedFilesDesktop.addEventListener('dblclick', handleSharedFilesDesktopDoubleClick);
    els.sharedFilesDesktop.addEventListener('contextmenu', handleSharedFilesContextMenu);
    els.sharedFilesDesktop.addEventListener('paste', handleSharedFilesPaste);
    els.sharedFilesDesktop.addEventListener('dragover', handleSharedFilesDragOver);
    els.sharedFilesDesktop.addEventListener('dragleave', handleSharedFilesDragLeave);
    els.sharedFilesDesktop.addEventListener('drop', handleSharedFilesDrop);
    els.sharedFilesDesktop.addEventListener('pointerdown', beginSharedFileDrag);
    els.sharedFilesDesktop.addEventListener('pointermove', moveSharedFileDrag);
    els.sharedFilesDesktop.addEventListener('pointerup', finishSharedFileDrag);
    els.sharedFilesDesktop.addEventListener('pointercancel', finishSharedFileDrag);
    els.sharedFilesContextMenu.addEventListener('click', handleSharedFilesContextMenuClick);
    document.addEventListener('click', handleSharedFilesDocumentClick);
    document.addEventListener('keydown', handleSharedFilesGlobalKeydown);
    if (els.cashboxCancelLastButton) {
      els.cashboxCancelLastButton.addEventListener('click', cancelLastCashboxTransaction);
    }
    els.cashboxIncomeButton.addEventListener('click', () => createCashboxTransaction('income'));
    els.cashboxTransferButton.addEventListener('click', createCashboxTransfer);
    els.cashboxExpenseButton.addEventListener('click', () => createCashboxTransaction('expense'));
    els.cashboxTransferTargets.addEventListener('click', handleCashboxTransferTargetsClick);
    els.cashboxTransferConfirmButton.addEventListener('click', submitCashboxTransfer);
    els.cashboxTransferAmountInput.addEventListener('input', handleCashboxTransferAmountInput);
    els.cashboxTransferNoteInput.addEventListener('input', handleCashboxTransferNoteInput);
    els.cashboxNoteInput.addEventListener('input', handleCashboxNoteInput);
    els.cashboxesList.addEventListener('click', handleCashboxesListClick);
    els.cashboxTransactions.addEventListener('click', handleCashboxTransactionsClick);
    els.cashboxesList.addEventListener('keydown', handleCashboxesListKeydown);
    els.cashboxesList.addEventListener('dragstart', handleCashboxesListDragStart);
    els.cashboxesList.addEventListener('dragover', handleCashboxesListDragOver);
    els.cashboxesList.addEventListener('drop', handleCashboxesListDrop);
    els.cashboxesList.addEventListener('dragend', handleCashboxesListDragEnd);
    els.cashboxAmountInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        createCashboxTransaction('income');
      }
    });
    els.gptWallBoardTab.addEventListener('click', () => setGptWallView('board_content'));
    els.gptWallEventsTab.addEventListener('click', () => setGptWallView('event_log'));
    els.gptWallRefresh.addEventListener('click', refreshGptWallView);
    els.boardScaleInput.addEventListener('input', handleBoardScaleInput);
    els.boardScaleInput.addEventListener('change', persistBoardScaleChange);
    els.boardScaleReset.addEventListener('click', resetBoardScaleToDefault);
    els.columnButton.addEventListener('click', createColumnFromTopbar);
    els.cardButton.addEventListener('click', openDefaultNewCard);
    els.signalDaysIncrementButton.addEventListener('click', () => adjustSignalPart('days', 1));
    els.signalDaysDecrementButton.addEventListener('click', () => adjustSignalPart('days', -1));
    els.signalHoursIncrementButton.addEventListener('click', () => adjustSignalPart('hours', 1));
    els.signalHoursDecrementButton.addEventListener('click', () => adjustSignalPart('hours', -1));
    [els.signalDays, els.signalHours].forEach((input) => {
      input.addEventListener('input', renderSignalPreview);
      input.addEventListener('change', renderSignalPreview);
    });
    els.tagAddButton.addEventListener('click', addDraftTag);
    els.tagInput.addEventListener('keydown', handleTagInputKeydown);
    configureVehicleAutofillUi();
    els.cardDescriptionEditor.addEventListener('beforeinput', handleDescriptionBeforeInput);
    els.cardDescriptionEditor.addEventListener('input', handleCardDescriptionInput);
    els.cardDescriptionEditor.addEventListener('keydown', handleDescriptionKeyboardShortcut);
    els.cardDescriptionEditor.addEventListener('paste', handleDescriptionPaste);
    els.cardDescriptionToolbar.addEventListener('mousedown', handleDescriptionToolbarMouseDown);
    els.cardDescriptionToolbar.addEventListener('click', handleDescriptionFormatClick);
    els.vehicleAutofillButton.addEventListener('click', autofillVehicleProfile);
    els.repairOrderAddWorkRowButton.addEventListener('click', (event) => {
      addRepairOrderRowFromButton('works', event).catch((error) => setStatus(error.message, true));
    });
    els.repairOrderAddMaterialRowButton.addEventListener('click', (event) => {
      addRepairOrderRowFromButton('materials', event).catch((error) => setStatus(error.message, true));
    });
    els.repairOrderModal.addEventListener('input', handleRepairOrderModalInput);
    els.repairOrderModal.addEventListener('change', handleRepairOrderModalInput);
    els.repairOrderWorkSalaryPopover?.addEventListener('input', syncRepairOrderWorkSalaryPopoverPreview);
    els.repairOrderWorkSalaryPopover?.addEventListener('change', syncRepairOrderWorkSalaryPopoverPreview);
    els.repairOrderTagAddButton.addEventListener('click', addRepairOrderTag);
    els.repairOrderTagInput.addEventListener('keydown', handleRepairOrderTagInputKeydown);
    els.repairOrderButton.addEventListener('click', openRepairOrderModal);
    els.repairOrderAutofillButton.addEventListener('click', autofillRepairOrder);
    els.repairOrderCloseButton.addEventListener('click', toggleRepairOrderStatus);
    els.repairOrderSaveButton.addEventListener('click', saveRepairOrderDraft);
    els.repairOrderPaymentsButton.addEventListener('click', openRepairOrderPaymentsModal);
    els.repairOrderPrintButton.addEventListener('click', printRepairOrderDraft);
    els.saveCardButton.addEventListener('click', saveCard);
    els.saveStickyButton.addEventListener('click', saveSticky);
    els.archiveAction.addEventListener('click', archiveActiveCard);
    els.restoreAction.addEventListener('click', restoreActiveCard);
    els.uploadButton.addEventListener('click', uploadFiles);
    els.fileInput.addEventListener('change', () => uploadProvidedFiles(els.fileInput.files));
    els.fileDropzone.addEventListener('click', openFilePickerFromDropzone);
    els.fileDropzone.addEventListener('keydown', handleFileDropzoneKeydown);
    els.fileDropzone.addEventListener('beforeinput', handleFileDropzoneBeforeInput);
    els.fileDropzone.addEventListener('input', handleFileDropzoneInput);
    els.fileDropzone.addEventListener('dragenter', handleFileDropzoneDragEnter);
    els.fileDropzone.addEventListener('dragover', handleFileDropzoneDragOver);
    els.fileDropzone.addEventListener('dragleave', handleFileDropzoneDragLeave);
    els.fileDropzone.addEventListener('drop', handleFileDropzoneDrop);
    els.fileDropzone.addEventListener('paste', handleFileDropzonePaste);
    document.addEventListener('keydown', handleFilePreviewKeydown);
    document.addEventListener('error', handleAttachmentThumbnailError, true);
    els.repairOrdersList.addEventListener('click', handleRepairOrdersListClick);
    els.repairOrdersList.addEventListener('keydown', handleRepairOrdersListKeydown);
    els.stickyModal.addEventListener('click', handleStickyModalOverlayClick);
    els.repairOrderModal.addEventListener('click', handleRepairOrderModalOverlayClick);
    els.operatorProfileModal.addEventListener('click', handleOperatorProfileModalOverlayClick);
    els.operatorAdminModal.addEventListener('click', handleOperatorAdminModalOverlayClick);

    const CARD_VEHICLE_FIELD_LABEL = 'Марка / модель';
    const CARD_TITLE_FIELD_LABEL = 'Краткая суть';
    const CARD_TITLE_REQUIRED_MESSAGE = 'УКАЖИ КРАТКУЮ СУТЬ КАРТОЧКИ.';

    function configureCardFieldSemantics() {
      const vehicleLabel = document.querySelector('label[for="cardVehicle"]');
      if (vehicleLabel) vehicleLabel.textContent = 'МАРКА / МОДЕЛЬ';
      if (els.cardVehicle) {
        els.cardVehicle.placeholder = 'Nissan Teana J32';
        els.cardVehicle.title = 'Указывай только марку и модель автомобиля.';
      }
      const titleLabel = document.querySelector('label[for="cardTitle"]');
      if (titleLabel) titleLabel.textContent = 'КРАТКАЯ СУТЬ';
      if (els.cardTitle) {
        els.cardTitle.placeholder = 'Краткая суть проблемы, задачи или результата';
        els.cardTitle.title = 'Указывай только краткую суть карточки, без марки и модели.';
      }
    }

    function buildVehicleAutofillRawText() {
      const parts = [];
      const vehicle = String(els.cardVehicle.value || '').trim();
      const title = String(els.cardTitle.value || '').trim();
      const description = getCardDescriptionValue();
      if (vehicle) parts.push(CARD_VEHICLE_FIELD_LABEL + ': ' + vehicle);
      if (title) parts.push(CARD_TITLE_FIELD_LABEL + ': ' + title);
      if (description) parts.push('Описание:\n' + description);
      return parts.join('\n\n').trim();
    }

    function buildCardHeadingHtml(card) {
      const vehicle = String(card?.vehicle || '').trim();
      const title = String(card?.title || '').trim();
      if (vehicle && title) {
        return '<div class="card__heading"><div class="card__vehicle">' + escapeHtml(vehicle) + '</div><div class="card__title">' + escapeHtml(title) + '</div></div>';
      }
      if (vehicle) return '<div class="card__vehicle">' + escapeHtml(vehicle) + '</div>';
      return '<div class="card__title">' + escapeHtml(title) + '</div>';
    }

    function boardCardDescription(card) {
      return stripDescriptionFormatting(card?.board_summary || card?.description_preview || card?.description || 'Описание не указано');
    }

    function cardUnreadBadgeHtml(card) {
      if (card?.is_unread) {
        return '<div class="card__unread-badge" title="Не прочитано" aria-label="Не прочитано">NEW</div>';
      }
      if (card?.has_unseen_update) {
        return '<div class="card__updated-badge" title="Обновлено" aria-label="Обновлено">ОБНОВЛЕНО</div>';
      }
      return '';
    }

    function renderBoardCardHtml(card) {
      const normalizedTags = normalizeDraftTags(card.tag_items || card.tags || []);
      const previewTags = normalizedTags.slice(0, CARD_TAG_LIMIT);
      const extraTags = normalizedTags.length - previewTags.length;
      const tagsHtml = previewTags.length
        ? previewTags.map((tag) => '<span class="tag" data-tag-color="' + escapeHtml(tag.color) + '"><span class="tag__dot"></span>' + escapeHtml(tag.label) + '</span>').join('') + (extraTags > 0 ? '<span class="tag">+' + extraTags + '</span>' : '')
        : '<span class="tag tag--muted">БЕЗ МЕТОК</span>';
      const headingHtml = buildCardHeadingHtml(card);
      const badgeHtml = cardUnreadBadgeHtml(card);
      const heatStyle = '--deadline-heat-border:' + escapeHtml(card.deadline_heat_border_color || 'rgba(83, 191, 122, 0.34)') + ';--deadline-heat-ring:' + escapeHtml(card.deadline_heat_ring_color || 'rgba(83, 191, 122, 0.08)') + ';--deadline-heat-glow:' + escapeHtml(card.deadline_heat_glow_color || 'rgba(83, 191, 122, 0.04)') + ';';
      return '<article class="card" style="' + heatStyle + '" draggable="true" data-card-id="' + escapeHtml(card.id) + '" data-indicator="' + escapeHtml(card.indicator) + '" data-status="' + escapeHtml(card.status) + '" data-blink="' + (card.is_blinking ? "true" : "false") + '" data-unread="' + (card.is_unread ? 'true' : 'false') + '" data-updated-unseen="' + (card.has_unseen_update ? 'true' : 'false') + '" data-deadline-bucket="' + escapeHtml(card.deadline_progress_bucket ?? 0) + '" data-deadline-step="' + escapeHtml(card.deadline_progress_step_percent ?? 0) + '">' + badgeHtml + headingHtml + '<div class="card__desc">' + escapeHtml(boardCardDescription(card)) + '</div><div class="card__footer"><div class="card__signal"><span class="card__signal-label"><span class="lamp" data-indicator="' + escapeHtml(card.indicator) + '"></span></span><span class="card__signal-value">' + durationToMarkup(card.remaining_seconds, false) + '</span></div><div class="card__tags">' + tagsHtml + '</div></div></article>';
    }

    function refreshVehiclePanel() {
      const profile = cloneVehicleProfile(state.vehicleProfileDraft || emptyVehicleProfile());
      const summaryLines = [];
      els.vehiclePanelSummary.textContent = summaryLines.join('\n');
      els.vehiclePanelSummary.style.display = summaryLines.length ? '' : 'none';

      const vinInput = getVehicleFieldInput('vin');
      if (vinInput) vinInput.classList.toggle('vehicle-suspect', vinLooksSuspicious(profile.vin));

      if (!state.vehicleAutofillResult) renderVehicleAutofillStatus(defaultVehicleStatusText(profile), Boolean(profile?.warnings?.length || vinLooksSuspicious(profile.vin)));
    }

    async function saveCard() {
      if (state.cardSaveInFlight) return;
      if (state.editingId && !state.activeCardIsFull) return setStatus('ДОЖДИТЕСЬ ЗАГРУЗКИ КАРТОЧКИ.', true);
      const payload = currentCardPayload();
      if (!payload.title) return setStatus(CARD_TITLE_REQUIRED_MESSAGE, true);
      clearCardOpenSideEffectTimer();
      state.cardSaveInFlight = true;
      if (els.saveCardButton) els.saveCardButton.disabled = true;
      return perfMeasureAsync('saveCard', async () => {
        try {
          const data = await persistCardPayload(payload);
          if (data?.card) applySavedCardLocalPatch(data.card);
          closeCardModal({ force: true });
          setStatus('КАРТОЧКА СОХРАНЕНА.', false);
        } catch (error) {
          setStatus(error.message, true);
        } finally {
          state.cardSaveInFlight = false;
          if (els.saveCardButton) els.saveCardButton.disabled = false;
        }
      });
    }

    configureCardFieldSemantics();
    consumeUrlAccessToken();
    configureOperatorIdentityUi();
    bindClientsUiEvents();
    renderVehicleProfileFields();
    applyVehicleProfileToForm(emptyVehicleProfile());
    refreshRepairOrderEntry(null);
    renderCashboxDetail();
    bindDirectCardModalCloseButtons();
    mountStatusLine();
    applyMobileLiteMode(detectMobileLiteMode());
    window.addEventListener('resize', syncMobileLiteMode);
    bootstrapOperatorSession();
    refreshSnapshot(true);
    document.addEventListener('visibilitychange', handleSnapshotVisibilityChange);
    startSnapshotPolling();
  </script>
</body>
</html>
