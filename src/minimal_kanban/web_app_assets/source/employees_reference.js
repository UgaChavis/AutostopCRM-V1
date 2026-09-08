    function applyEmployeesReferenceData(data, month) {
      state.employees = Array.isArray(data?.employees) ? data.employees : [];
      state.employeesLoadedMonth = month;
      if (!state.employeeCreateMode && !state.activeEmployeeId && state.employees.length) {
        state.activeEmployeeId = state.employees[0].id;
      }
      if (!state.employees.length) {
        state.employeeCreateMode = true;
      }
    }

    async function loadEmployeesReference({ month: requestedMonth = '', apply = true } = {}) {
      const viewerStateGeneration = state.viewerStateGeneration;
      const session = state.operatorSessionToken;
      const accessRevision = state.employeesCashboxesAccessRevision;
      const month = String(requestedMonth || state.payrollMonth || currentPayrollMonthValue()).trim();
      if (state.employeesLoadedMonth === month && Array.isArray(state.employees)) {
        return { employees: state.employees, meta: { cached: true, month } };
      }
      let request = null;
      if (
        state.employeesReferencePromise
        && state.employeesReferencePromise.month === month
        && state.employeesReferencePromise.viewerStateGeneration === viewerStateGeneration
        && state.employeesReferencePromise.session === session
        && state.employeesReferencePromise.accessRevision === accessRevision
      ) {
        request = state.employeesReferencePromise.promise;
      } else {
        request = api('/api/list_employees?month=' + encodeURIComponent(month))
          .finally(() => {
            if (state.employeesReferencePromise?.promise === request) {
              state.employeesReferencePromise = null;
            }
          });
        state.employeesReferencePromise = { month, viewerStateGeneration, session, accessRevision, promise: request };
      }
      const data = await request;
      if (
        viewerStateGeneration !== state.viewerStateGeneration
        || session !== state.operatorSessionToken
        || accessRevision !== state.employeesCashboxesAccessRevision
      ) return data;
      const activeMonth = state.payrollMonth || currentPayrollMonthValue();
      if (apply && month === activeMonth) applyEmployeesReferenceData(data, month);
      return data;
    }

    async function loadPayrollReport({ month: requestedMonth = '', apply = true } = {}) {
      const viewerStateGeneration = state.viewerStateGeneration;
      const month = String(requestedMonth || state.payrollMonth || currentPayrollMonthValue()).trim();
      const report = await api('/api/get_payroll_report?month=' + encodeURIComponent(month));
      if (viewerStateGeneration !== state.viewerStateGeneration) return report;
      const activeMonth = state.payrollMonth || currentPayrollMonthValue();
      if (apply && month === activeMonth) {
        state.payrollReport = report;
        state.payrollReportMonth = month;
      }
      return report;
    }

    function prepareEmployeesWorkspaceData(month, moduleReady) {
      const viewer = state.viewerStateGeneration;
      const session = state.operatorSessionToken;
      const access = state.employeesCashboxesAccessRevision;
      const requestedMonth = String(month || state.payrollMonth || currentPayrollMonthValue()).trim();
      state.payrollMonth = requestedMonth;
      const generation = ++state.employeesWorkspaceLoadGeneration;
      const isCurrent = () => viewer === state.viewerStateGeneration && session === state.operatorSessionToken
        && access === state.employeesCashboxesAccessRevision
        && generation === state.employeesWorkspaceLoadGeneration && requestedMonth === state.payrollMonth;
      const result = { applied: false, generation, month: requestedMonth };
      const canManage = operatorCanAccessEmployeesCashboxes();
      const promise = Promise.all([
        loadEmployeesReference({ month: requestedMonth, apply: false }),
        canManage ? loadPayrollReport({ month: requestedMonth, apply: false }) : null,
        moduleReady,
      ]).then(([employeesData, payrollReport]) => {
        if (!isCurrent()) return result;
        applyEmployeesReferenceData(employeesData, requestedMonth);
        state.payrollReport = payrollReport;
        state.payrollReportMonth = canManage ? requestedMonth : '';
        return { ...result, applied: true };
      }, (error) => {
        if (!isCurrent()) return result;
        throw error;
      });
      return { isCurrent, promise };
    }

    async function loadEmployeesWorkspaceData(month) {
      return prepareEmployeesWorkspaceData(month).promise;
    }
