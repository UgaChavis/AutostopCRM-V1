    const employeeMoneyCheckpoints = new Map();
    let employeeMoneyControlHold = null;

    function releaseEmployeeMoneyControls() {
      if (!employeeMoneyControlHold) return;
      employeeMoneyControlHold.controls.forEach((saved, control) => {
        if (control !== els.employeeSalaryResetButton) control.disabled = saved.disabled;
        if (saved.value !== undefined) control.value = saved.value;
        if (saved.checked !== undefined) control.checked = saved.checked;
      });
      employeeMoneyControlHold.inert.forEach((inert, container) => { container.inert = inert; });
      employeeMoneyControlHold = null;
    }

    function lockEmployeeMoneyControls(owner) {
      if (employeeMoneyControlHold?.owner !== owner) {
        releaseEmployeeMoneyControls();
        employeeMoneyControlHold = { owner, controls: new Map(), inert: new Map() };
      }
      const hold = employeeMoneyControlHold;
      for (const container of [els.employeesModal, els.employeeSalaryModal, els.mobileEmployeesPanel]) {
        for (const control of container?.querySelectorAll?.('button, input, select, textarea') || []) {
          const field = ['INPUT', 'SELECT', 'TEXTAREA'].includes(control.tagName);
          if (!hold.controls.has(control)) hold.controls.set(control, {
            disabled: control.disabled,
            value: field ? control.value : undefined,
            checked: field && typeof control.checked === 'boolean' ? control.checked : undefined,
          });
          const saved = hold.controls.get(control);
          if (saved.value !== undefined) control.value = saved.value;
          if (saved.checked !== undefined) control.checked = saved.checked;
          control.disabled = true;
        }
      }
      for (const container of [els.employeesList, els.mobileEmployeesList]) {
        if (!container) continue;
        if (!hold.inert.has(container)) hold.inert.set(container, Boolean(container.inert));
        container.inert = true;
      }
    }

    function employeeMoneyGuardedBody(path, body) {
      const employee = (state.employees || []).find((item) => item.id === body.employee_id);
      const cashbox = (state.cashboxes || []).find((item) => item.id === body.cashbox_id);
      return { ...body, expected_employee_updated_at: employee?.updated_at,
        ...(path === '/api/create_employee_salary_transaction' ? { expected_cashbox_updated_at: cashbox?.updated_at } : {}) };
    }

    async function readEmployeeMoneyRetryGuard(checkpoint, isCurrent) {
      if (!checkpoint.body.cashbox_id || !checkpoint.body.expected_cashbox_updated_at) return false;
      const data = await api('/api/get_cashbox?cashbox_id=' + encodeURIComponent(checkpoint.body.cashbox_id) + '&compact=true&transaction_limit=1');
      return isCurrent() && data?.cashbox?.updated_at === checkpoint.body.expected_cashbox_updated_at;
    }

    async function runEmployeeMoneyMutation(path, body, entityField, { receipt, reload, onSuccess } = {}) {
      if (state.employeeSalaryResetPending) return false;
      const isCurrent = employeeAsyncContext('employeeMoneyMutationOperation', entityField, { singleFlight: true });
      if (!isCurrent) return false;
      const key = JSON.stringify([state.viewerStateGeneration, state.operatorSessionToken, isCurrent.month, path, body]);
      let checkpoint = employeeMoneyCheckpoints.get(key);
      const unresolved = [...employeeMoneyCheckpoints.values()].find((item) => item.session === state.operatorSessionToken
        && item.body.employee_id === body.employee_id && item.uncertain && !(item === checkpoint && item.retrySafe));
      if (!checkpoint) checkpoint = { session: state.operatorSessionToken, body: employeeMoneyGuardedBody(path, body), confirmed: null, uncertain: false };
      syncEmployeeMoneyMutationControls();
      let completed = false;
      try {
        if (unresolved) throw new Error('РЕЗУЛЬТАТ ПРЕДЫДУЩЕЙ ОПЕРАЦИИ НЕ ПОДТВЕРЖДЁН. ПОВТОР ОСТАНОВЛЕН; СВЕРЬТЕ ЖУРНАЛ СОТРУДНИКА.');
        if (!checkpoint.confirmed) {
          employeeMoneyCheckpoints.set(key, checkpoint);
          const previouslyUncertain = checkpoint.uncertain;
          checkpoint.retrySafe = false;
          const controller = new AbortController();
          const timeout = globalThis.setTimeout(() => controller.abort(), 90000);
          try {
            const data = await api(path, { method: 'POST', body: checkpoint.body, signal: controller.signal });
            if (!isCurrent()) return false;
            if (!receipt?.(data)) throw new Error('СЕРВЕР НЕ ПОДТВЕРДИЛ РЕЗУЛЬТАТ ОПЕРАЦИИ.');
            checkpoint.confirmed = data;
            checkpoint.uncertain = false;
          } catch (error) {
            if (previouslyUncertain || !(error?.status >= 400 && error.status < 500 && error.code !== 'invalid_json')) checkpoint.uncertain = true;
            else employeeMoneyCheckpoints.delete(key);
            throw error;
          } finally {
            globalThis.clearTimeout(timeout);
          }
        }
        if (!isCurrent()) return false;
        if (!await reload(isCurrent)) throw new Error('ОПЕРАЦИЯ ЗАПИСАНА, НО ЭКРАН НЕ ОБНОВИЛСЯ. ПОВТОРИТЕ ОБНОВЛЕНИЕ.');
        if (!isCurrent()) return false;
        employeeMoneyCheckpoints.delete(key);
        completed = true;
      } catch (error) {
        if (!isCurrent()) return false;
        if (checkpoint.uncertain || unresolved || error?.status === 409) {
          try {
            if (error?.status === 409) {
              state.cashboxesLoaded = false;
              await ensureEmployeeSalaryCashboxes(isCurrent);
            }
            if (await reload(isCurrent) && checkpoint.uncertain && isCurrent()) {
              checkpoint.retrySafe = await readEmployeeMoneyRetryGuard(checkpoint, isCurrent);
            }
          } catch (_) {}
        }
        if (isCurrent()) setStatus(String(error?.message || 'НЕ УДАЛОСЬ ВЫПОЛНИТЬ ОПЕРАЦИЮ.')
          + (checkpoint.uncertain || unresolved ? checkpoint.retrySafe
            ? ' ДАННЫЕ ОБНОВЛЕНЫ. МОЖНО ПОВТОРИТЬ ПОПЫТКУ.'
            : ' НЕ ПОВТОРЯЙТЕ ЗАПИСЬ ДО ПОДТВЕРЖДЕНИЯ РЕЗУЛЬТАТА.' : ''), true);
      } finally {
        if (isCurrent.release()) {
          syncEmployeeMoneyMutationControls();
          if (isCurrent.contextIsCurrent()) renderEmployeeSalaryModal();
        }
      }
      if (completed && isCurrent.contextIsCurrent()) onSuccess?.(checkpoint.confirmed);
      return completed;
    }

    async function refreshSalaryMoneyUi(employeeId, isCurrent) {
      const sheet = await loadEmployeeSalarySheet(employeeId, { openModal: false });
      if (!isCurrent() || !sheet) return false;
      state.employeesLoadedMonth = '';
      if (!await refreshEmployeePayroll(isCurrent)) return false;
      renderEmployeesWorkspace();
      await refreshCashboxesAfterMoneyMutation({ deferDetail: true });
      return isCurrent();
    }

    async function refreshShiftMoneyUi(employeeId, isCurrent) {
      if (!await refreshEmployeePayroll(isCurrent)) return false;
      renderEmployeesWorkspace();
      if (String(state.activeEmployeeSalaryId || '') === employeeId) {
        const sheet = await loadEmployeeSalarySheet(employeeId, { openModal: false });
        if (!isCurrent() || !sheet) return false;
      }
      return isCurrent();
    }
