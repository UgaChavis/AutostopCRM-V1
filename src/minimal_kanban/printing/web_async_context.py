"""Browser print operations retain their initiating viewer and editor context."""

PRINTING_ASYNC_CONTEXT_SCRIPT = r"""
    let printWorkspaceGeneration = 0;
    let printTemplateGeneration = 0;
    let printInspectionGeneration = 0;
    const printOperationTokens = new Map();
    const printFrameCancellations = new Set();
    let printJobOwner = null;

    function invalidatePrintWorkspaceContext() {
      printWorkspaceGeneration += 1;
      printOperationTokens.clear();
      printFrameCancellations.forEach((cancel) => cancel());
      printJobOwner = null;
      repairOrderPrintState.isPrintRunning = false;
      if (typeof syncRepairOrderPrintPrinterState === 'function') syncRepairOrderPrintPrinterState();
    }

    function invalidatePrintTemplateContext() {
      printTemplateGeneration += 1;
    }

    function invalidatePrintInspectionContext() {
      printInspectionGeneration += 1;
    }

    function scheduleCurrentPrintOperation(callback, delay, options = {}) {
      const operation = capturePrintOperation('', options);
      return window.setTimeout(() => { if (operation.current()) callback(); }, delay);
    }

    function resetPrintAsyncContext() {
      invalidatePrintWorkspaceContext();
      invalidatePrintTemplateContext();
    }

    function capturePrintOperation(scope = '', { card = true, workspace = true, mode = true, template = false, inspection = false, selection = false } = {}) {
      const viewer = state.viewerStateGeneration;
      const workspaceGeneration = printWorkspaceGeneration;
      const templateGeneration = printTemplateGeneration;
      const inspectionGeneration = printInspectionGeneration;
      const cardId = String(state.editingId || state.activeCard?.id || '');
      const printMode = repairOrderPrintState.mode;
      const selectedIds = JSON.stringify(repairOrderPrintState.selectedDocumentIds || []);
      const activeDocumentId = repairOrderPrintState.activeDocumentId;
      const token = {};
      if (scope) printOperationTokens.set(scope, token);
      const current = () => viewer === state.viewerStateGeneration
        && (!workspace || workspaceGeneration === printWorkspaceGeneration)
        && (!card || cardId === String(state.editingId || state.activeCard?.id || ''))
        && (!mode || printMode === repairOrderPrintState.mode)
        && (!template || templateGeneration === printTemplateGeneration)
        && (!inspection || inspectionGeneration === printInspectionGeneration)
        && (!selection || (selectedIds === JSON.stringify(repairOrderPrintState.selectedDocumentIds || [])
          && activeDocumentId === repairOrderPrintState.activeDocumentId))
        && (!scope || printOperationTokens.get(scope) === token);
      const check = () => {
        if (current()) return;
        const error = new Error('');
        error.code = 'stale_print_operation';
        throw error;
      };
      return {
        current, check, viewerCurrent: () => viewer === state.viewerStateGeneration,
        async wait(pending) {
          try { const value = await pending; check(); return value; }
          catch (error) { check(); throw error; }
        },
        request(...args) { check(); return this.wait(api(...args)); },
      };
    }
"""

PRINTING_BROWSER_LIFECYCLE_SCRIPT = r"""
    function runBrowserPrintHtml(printableHtml, operation = capturePrintOperation()) {
      return new Promise((resolve, reject) => {
        const frame = document.createElement('iframe');
        let settled = false;
        let printStarted = false;
        const timers = new Set();
        const later = (callback, delay) => {
          const timer = window.setTimeout(() => { timers.delete(timer); callback(); }, delay);
          timers.add(timer);
        };
        const cleanup = () => {
          printFrameCancellations.delete(cancel);
          timers.forEach((timer) => window.clearTimeout(timer));
          timers.clear();
          frame.onload = null;
          frame.remove();
        };
        const fail = (error) => { if (!settled) { settled = true; cleanup(); reject(error); } };
        const cancel = () => { try { operation.check(); } catch (error) { fail(error); } };
        const finish = () => {
          if (settled) return;
          settled = true;
          printFrameCancellations.delete(cancel);
          timers.forEach((timer) => window.clearTimeout(timer));
          timers.clear();
          frame.onload = null;
          window.setTimeout(() => frame.remove(), 1500);
          resolve();
        };
        frame.style.cssText = 'position:fixed;right:-12000px;bottom:0;width:1px;height:1px;border:0';
        frame.setAttribute('aria-hidden', 'true');
        frame.setAttribute('sandbox', 'allow-same-origin allow-modals');
        frame.onload = () => {
          if (printStarted || settled) return;
          printStarted = true;
          frame.onload = null;
          later(() => {
            try {
              operation.check();
              const win = frame.contentWindow;
              if (!win) throw new Error('Не удалось открыть системное окно печати.');
              win.onafterprint = finish;
              win.focus();
              win.print();
              later(finish, 1200);
            } catch (error) { fail(error); }
          }, 120);
        };
        try {
          operation.check();
          printFrameCancellations.add(cancel);
          document.body.appendChild(frame);
          const doc = frame.contentDocument;
          if (!doc) throw new Error('Не удалось подготовить документ для печати.');
          doc.open(); doc.write(printableHtml); doc.close();
        } catch (error) { fail(error); }
      });
    }
    function runRepairOrderBrowserPrint() {
      const selectedIds = repairOrderPrintSelectedIds();
      if (selectedIds.length === 1 && selectedIds[0] === 'completion_act') {
        const preview = repairOrderPrintState.previewByDocument?.completion_act;
        const pages = Array.isArray(preview?.pages) ? preview.pages : [];
        return runBrowserPrintHtml(completionActCombinedHtml(pages));
      }
      return runBrowserPrintHtml(repairOrderPrintCombinedHtml());
    }
"""

PRINTING_JOB_SCRIPT = r"""
    async function runRepairOrderPrintJob() {
      const operation = capturePrintOperation('', { selection: true });
      if (repairOrderPrintState.isPrintRunning) return;
      printJobOwner = operation;
      repairOrderPrintState.isPrintRunning = true;
      if (printEls.printButton) printEls.printButton.disabled = true;
      try {
        cancelPendingRepairOrderPrintPreview();
        const refreshed = await operation.wait(refreshRepairOrderPrintPreview({}, { throwOnError: true }));
        const selectedIds = repairOrderPrintSelectedIds();
        if (!refreshed || selectedIds.some((documentId) => !repairOrderPrintState.previewByDocument?.[documentId])) {
          throw new Error('Не удалось обновить документ перед печатью.');
        }
        await operation.wait(runRepairOrderBrowserPrint());
        setStatus('Открыто системное окно печати браузера.', false);
      } catch (error) {
        if (!operation.current() || error?.code === 'stale_print_operation') return;
        try {
          const printerName = printEls.printerSelect?.value || '';
          if (!printerName) throw error;
          const fallback = await operation.request('/api/print_repair_order_documents', {
            method: 'POST',
            body: repairOrderPrintRequestPayload({ printer_name: printerName }),
          });
          setStatus('Браузерная печать не открылась. Документ отправлен на серверный принтер: ' + (fallback?.printer_name || printerName) + '.', false);
        } catch (fallbackError) {
          if (!operation.current() || fallbackError?.code === 'stale_print_operation') return;
          setStatus((fallbackError && fallbackError.message) || (error && error.message) || 'Не удалось запустить печать.', true);
        }
      } finally {
        if (printJobOwner === operation && operation.viewerCurrent()) {
          printJobOwner = null;
          repairOrderPrintState.isPrintRunning = false;
          syncRepairOrderPrintPrinterState();
        }
      }
    }
"""
