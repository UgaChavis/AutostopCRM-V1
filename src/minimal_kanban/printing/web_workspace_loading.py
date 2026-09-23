"""Loading lifecycle shared by cold and warm repair-order print openings."""

PRINTING_WORKSPACE_LOADING_SCRIPT = r"""
    async function loadRepairOrderPrintWorkspace({ openModal = false, preserveSelection = false, prepared = null } = {}) {
      if (prepared && !prepared.isCurrent()) return null;
      repairOrderPrintState.isWorkspaceLoading = true;
      invalidatePrintWorkspaceContext();
      currentPrintLoadShell = prepared?.shell || null;
      if (currentPrintLoadShell) currentPrintLoadShell.onClose = () => {
        invalidatePrintWorkspaceContext();
        cancelPendingRepairOrderPrintPreview();
        repairOrderPrintState.isWorkspaceLoading = false;
      };
      repairOrderPrintState.previewByDocument = {};
      repairOrderPrintState.pageIndexByDocument = {};
      repairOrderPrintState.mode = 'card';
      syncRepairOrderPrintMode();
      let operation = capturePrintOperation('', { card: false });
      const cardId = await operation.wait(requireRepairOrderCardId());
      if (!cardId || cardId !== completionActActiveCardId()) {
        prepared?.shell?.close();
        repairOrderPrintState.isWorkspaceLoading = false;
        return null;
      }
      if (prepared?.acceptCard && !prepared.acceptCard(cardId)) return null;
      operation = capturePrintOperation();
      if (prepared && (prepared.cardId !== cardId || !prepared.isCurrent())) return null;
      const data = prepared?.promise
        ? await operation.wait(prepared.promise)
        : await operation.request('/api/get_repair_order_print_workspace', {
          method: 'POST',
          body: { card_id: cardId, source: 'ui', repair_order: readRepairOrderFromForm() },
        });
      if (prepared && !prepared.isCurrent()) return null;
      repairOrderPrintState.isWorkspaceLoading = false;
      prepared?.shell?.documentsReady();
      applyRepairOrderPrintWorkspace(data, { preserveSelection });
      if (openModal) printEls.modal.classList.add('is-open');
      await operation.wait(refreshRepairOrderPrintPreview({}, { throwOnError: true }));
      if (!prepared || prepared.isCurrent()) prepared?.shell?.ready();
      return data;
    }
"""
