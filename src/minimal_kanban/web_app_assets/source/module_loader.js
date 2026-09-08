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

    function invokeBoardModule(name, method, args, passive = false) {
      const record = boardModuleRecords.get(name);
      if (record?.exports && (!record.invocations.size || passive)) return record.exports[method](...args);
      if (passive) return true;
      const generation = state.viewerStateGeneration;
      const session = state.operatorSessionToken;
      const cardId = state.editingId;
      const payrollOpen = name === 'payroll' && method === 'openEmployeesModal';
      const month = payrollOpen ? (els.employeesMonthInput?.value || state.payrollMonth || currentPayrollMonthValue()) : '';
      const pending = ensureBoardModule(name);
      const currentRecord = boardModuleRecords.get(name);
      let argumentKey = '';
      try { argumentKey = JSON.stringify(args); } catch (_) { /* Event objects can contain cycles. */ }
      const invocationKey = JSON.stringify([method, generation, session, cardId, month,
        payrollOpen ? state.employeesCashboxesAccessRevision : null, argumentKey]);
      if (currentRecord.invocations.has(invocationKey)) return currentRecord.invocations.get(invocationKey);
      if (record?.exports) return record.exports[method](...args);
      let prepared = null;
      if (payrollOpen && operatorCanViewEmployees()) prepared = prepareEmployeesWorkspaceData(month, pending);
      if (name === 'inventory' && method === 'openInventoryModal') {
        prepared = { ...inventoryAsyncContext('items'), promise: readInventoryItems() };
      }
      // A script failure or invalidated viewer may leave the prepared read without a consumer.
      prepared?.promise.catch(() => {});
      const invocation = pending.then((module) => {
        if (generation !== state.viewerStateGeneration || session !== state.operatorSessionToken) return false;
        if (prepared && !prepared.isCurrent()) return false;
        if (name === 'printing' && cardId !== state.editingId) return false;
        return module[method](...(prepared ? [prepared] : args));
      }).catch((error) => {
        if (generation === state.viewerStateGeneration && session === state.operatorSessionToken
          && (!prepared || prepared.isCurrent())) setStatus(error.message, true);
        return false;
      }).finally(() => { currentRecord.invocations.delete(invocationKey); });
      currentRecord.invocations.set(invocationKey, invocation);
      return invocation;
    }

    function resetBoardModules() {
      boardModuleRecords.forEach((record) => record.exports?.resetViewer?.());
    }
