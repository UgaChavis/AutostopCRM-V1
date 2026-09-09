    const boardModuleRecords = new Map();

    function registerBoardModule(name, factory) {
      const record = boardModuleRecords.get(name);
      if (!record || typeof factory !== 'function' || record.factory) return;
      record.factory = factory;
    }
    window.registerBoardModule = registerBoardModule;

    function ensureBoardModule(name) {
      const path = BOARD_MODULE_MANIFEST[name];
      if (!path) return Promise.reject(new Error('Неизвестный модуль: ' + name));
      let record = boardModuleRecords.get(name);
      if (!record) {
        record = { exports: null, factory: null, loading: null, invocations: new Map() };
        boardModuleRecords.set(name, record);
      }
      if (record.exports) return Promise.resolve(record.exports);
      if (record.initializationError) return Promise.reject(record.initializationError);
      if (record.loading) return record.loading;
      record.loading = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = path;
        script.async = true;
        script.onload = () => {
          try {
            if (!record.factory) throw new Error('Модуль не зарегистрирован: ' + name);
            record.exports = record.factory({
              state, els,
              shared: typeof buildBoardModuleSharedContext === 'function'
                ? buildBoardModuleSharedContext(name)
                : {},
              api: async (...args) => {
                const generation = state.viewerStateGeneration;
                try {
                  const result = await api(...args);
                  if (generation !== state.viewerStateGeneration) throw new Error('');
                  return result;
                } catch (error) {
                  if (generation !== state.viewerStateGeneration) throw new Error('');
                  throw error;
                }
              },
              setStatus: (message, ...args) => { if (message) setStatus(message, ...args); },
            });
            resolve(record.exports);
          } catch (error) {
            record.initializationError = error;
            reject(error);
          } finally {
            script.remove();
          }
        };
        script.onerror = () => {
          script.remove();
          reject(new Error('Не удалось загрузить модуль. Повторите открытие.'));
        };
        document.head.appendChild(script);
      }).finally(() => { record.loading = null; });
      return record.loading;
    }

    function prepareRepairOrderPrintWorkspaceData() {
      const cardId = String(state.editingId || '').trim();
      if (!cardId) return null;
      const viewer = state.viewerStateGeneration;
      const session = state.operatorSessionToken;
      const editing = state.cardEditingGeneration || 0;
      const hydration = state.cardHydrationSeq || 0;
      const isCurrent = () => viewer === state.viewerStateGeneration
        && session === state.operatorSessionToken
        && editing === (state.cardEditingGeneration || 0)
        && hydration === (state.cardHydrationSeq || 0)
        && cardId === String(state.editingId || '').trim()
        && cardId === String(state.activeCard?.id || state.editingId || '').trim();
      let promise = null;
      try {
        promise = Promise.resolve(api('/api/get_repair_order_print_workspace', {
          method: 'POST',
          body: {
            card_id: cardId,
            source: 'ui',
            repair_order: readRepairOrderFromForm(),
          },
        }));
      } catch (error) {
        promise = Promise.reject(error);
      }
      return { cardId, isCurrent, promise };
    }

    function invokeBoardModule(name, method, args, passive = false) {
      const mobilePanelOpen = [
        'openMobileArchivePanel', 'openMobileEmployeesPanel', 'openMobileSharedFilesPanel',
      ].includes(method);
      const mobilePanelIntent = mobilePanelOpen ? claimMobileMorePanelIntent() : null;
      const record = boardModuleRecords.get(name);
      if (record?.exports && (!record.invocations.size || passive)) return record.exports[method](...args);
      if (passive) return true;
      const generation = state.viewerStateGeneration;
      const session = state.operatorSessionToken;
      const cardId = state.editingId;
      const cardEditingGeneration = state.cardEditingGeneration || 0;
      const cardHydrationSeq = state.cardHydrationSeq || 0;
      const printingOpen = name === 'printing' && method === 'printRepairOrderDraft';
      const payrollOpen = name === 'payroll' && method === 'openEmployeesModal';
      const month = payrollOpen ? (els.employeesMonthInput?.value || state.payrollMonth || currentPayrollMonthValue()) : '';
      const guardedAuxiliaryOpen = name === 'auxiliary' && [
        'openDisplayDashboardMessageEditor', 'openMobileArchivePanel',
        'openMobileSharedFilesPanel', 'openSharedFilesModal',
      ].includes(method);
      const settingsParent = method === 'openDisplayDashboardMessageEditor'
        ? (state.modalStack || []).find((entry) => entry?.key === 'settings')
        : null;
      const mobileView = method.startsWith('openMobile') ? state.mobileView : null;
      const pending = ensureBoardModule(name);
      const currentRecord = boardModuleRecords.get(name);
      const openIntent = guardedAuxiliaryOpen ? {} : null;
      if (openIntent) currentRecord.openIntent = openIntent;
      const intentIsCurrent = () => (!openIntent || currentRecord.openIntent === openIntent)
        && (!settingsParent || (state.modalStack || []).includes(settingsParent))
        && (mobileView === null || state.mobileView === mobileView)
        && (!mobilePanelIntent || state.mobileMorePanelIntent === mobilePanelIntent);
      let argumentKey = '';
      try { argumentKey = JSON.stringify(args); } catch (_) { /* Event objects can contain cycles. */ }
      const invocationKey = JSON.stringify([method, generation, session, cardId, month,
        payrollOpen ? state.employeesCashboxesAccessRevision : null,
        printingOpen ? cardEditingGeneration : null,
        printingOpen ? cardHydrationSeq : null,
        argumentKey]);
      const previous = currentRecord.invocations.get(invocationKey);
      if (previous && (!previous.isCurrent || previous.isCurrent())) return previous;
      if (record?.exports) return record.exports[method](...args);
      let prepared = null;
      if (payrollOpen && operatorCanViewEmployees()) {
        prepared = prepareEmployeesWorkspaceData(month, pending, { useEmbeddedPayrollReport: true });
      }
      if (name === 'inventory' && method === 'openInventoryModal') {
        prepared = prepareInventoryModal();
      }
      if (printingOpen && cardId) {
        prepared = prepareRepairOrderPrintWorkspaceData();
      }
      // A script failure or invalidated viewer may leave the prepared read without a consumer.
      prepared?.promise.catch(() => {});
      const invocation = pending.then((module) => {
        if (generation !== state.viewerStateGeneration || session !== state.operatorSessionToken
          || !intentIsCurrent()) return false;
        if (prepared && !prepared.isCurrent()) return false;
        if (name === 'printing' && (
          cardId !== state.editingId
          || cardEditingGeneration !== (state.cardEditingGeneration || 0)
          || cardHydrationSeq !== (state.cardHydrationSeq || 0)
        )) return false;
        return module[method](...(prepared ? [prepared] : args));
      }).catch((error) => {
        if (generation === state.viewerStateGeneration && session === state.operatorSessionToken
          && intentIsCurrent()
          && (!prepared || prepared.isCurrent())) {
          prepared?.onLoadError?.(error);
          setStatus(error.message, true);
        }
        return false;
      }).finally(() => {
        if (currentRecord.invocations.get(invocationKey) === invocation) currentRecord.invocations.delete(invocationKey);
        if (currentRecord.openIntent === openIntent) currentRecord.openIntent = null;
      });
      invocation.isCurrent = () => intentIsCurrent() && (!prepared || prepared.isCurrent());
      currentRecord.invocations.set(invocationKey, invocation);
      return invocation;
    }

    function resetBoardModules() {
      boardModuleRecords.forEach((record) => record.exports?.resetViewer?.());
    }
