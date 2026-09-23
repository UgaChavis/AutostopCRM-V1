    function invalidateEmployeeNamesReference() {
      state.employeeNamesRevision = (state.employeeNamesRevision || 0) + 1;
      state.employeeNamesPromise = null;
      state.employeeNamesLoadedMonth = '';
      state.employeeNames = null;
    }

    function invalidateEmployeesReference() {
      state.employeesReferenceRevision = (state.employeesReferenceRevision || 0) + 1;
      state.employeesReferencePromise = null;
      state.employeesLoadedMonth = '';
      invalidateEmployeeNamesReference();
    }

    function applyEmployeesReferenceData(data, month) {
      invalidateEmployeeNamesReference();
      state.employees = Array.isArray(data?.employees) ? data.employees : [];
      state.employeesLoadedMonth = month;
      if (!state.employeeCreateMode && !state.activeEmployeeId && state.employees.length) {
        state.activeEmployeeId = state.employees[0].id;
      }
      if (!state.employees.length) {
        state.employeeCreateMode = true;
      }
    }

    async function loadEmployeesReference({ month: requestedMonth = '', apply = true, force = false, referencesOnly = false } = {}) {
      const viewerStateGeneration = state.viewerStateGeneration;
      const session = state.operatorSessionToken;
      const accessRevision = state.employeesCashboxesAccessRevision;
      const referenceRevision = state.employeesReferenceRevision || 0;
      const namesRevision = referencesOnly ? (state.employeeNamesRevision || 0) : null;
      const month = String(requestedMonth || state.payrollMonth || currentPayrollMonthValue()).trim();
      const loadedMonthKey = referencesOnly ? 'employeeNamesLoadedMonth' : 'employeesLoadedMonth';
      const employeesKey = referencesOnly ? 'employeeNames' : 'employees';
      const promiseKey = referencesOnly ? 'employeeNamesPromise' : 'employeesReferencePromise';
      if (!force && state[loadedMonthKey] === month && Array.isArray(state[employeesKey])) {
        return { employees: state[employeesKey], meta: { cached: true, month, references_only: referencesOnly } };
      }
      let request = null;
      const pending = state[promiseKey];
      if (
        pending
        && pending.month === month
        && pending.viewerStateGeneration === viewerStateGeneration
        && pending.session === session
        && pending.accessRevision === accessRevision
        && pending.referenceRevision === referenceRevision
        && pending.namesRevision === namesRevision
      ) {
        request = pending.promise;
      } else {
        request = api('/api/list_employees?month=' + encodeURIComponent(month) + (referencesOnly ? '&references_only=true' : ''))
          .finally(() => {
            if (state[promiseKey]?.promise === request) {
              state[promiseKey] = null;
            }
          });
        state[promiseKey] = { month, viewerStateGeneration, session, accessRevision, referenceRevision, namesRevision, promise: request };
      }
      const data = await request;
      if (
        viewerStateGeneration !== state.viewerStateGeneration
        || session !== state.operatorSessionToken
        || accessRevision !== state.employeesCashboxesAccessRevision
        || referenceRevision !== (state.employeesReferenceRevision || 0)
        || (referencesOnly && namesRevision !== (state.employeeNamesRevision || 0))
      ) return data;
      const activeMonth = state.payrollMonth || currentPayrollMonthValue();
      if (apply && month === activeMonth) {
        if (referencesOnly) {
          state.employeeNames = Array.isArray(data?.employees) ? data.employees : [];
          state.employeeNamesLoadedMonth = month;
        } else applyEmployeesReferenceData(data, month);
      }
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

    function payrollReportFromEmployeesData(data, requestedMonth) {
      if (
        data?.meta?.references_only
        || String(data?.month || '') !== requestedMonth
        || !data?.summary
        || typeof data.summary !== 'object'
        || !Array.isArray(data?.detail_rows)
      ) return null;
      return {
        month: requestedMonth,
        summary: data?.summary,
        detail_rows: data?.detail_rows,
      };
    }

    function prepareEmployeesWorkspaceData(
      month,
      moduleReady,
      { useEmbeddedPayrollReport = false } = {},
    ) {
      const viewer = state.viewerStateGeneration;
      const session = state.operatorSessionToken;
      const access = state.employeesCashboxesAccessRevision;
      const reference = state.employeesReferenceRevision || 0;
      const requestedMonth = String(month || state.payrollMonth || currentPayrollMonthValue()).trim();
      state.payrollMonth = requestedMonth;
      const generation = ++state.employeesWorkspaceLoadGeneration;
      const isCurrent = () => viewer === state.viewerStateGeneration && session === state.operatorSessionToken
        && access === state.employeesCashboxesAccessRevision
        && reference === (state.employeesReferenceRevision || 0)
        && generation === state.employeesWorkspaceLoadGeneration && requestedMonth === state.payrollMonth;
      const result = { applied: false, generation, month: requestedMonth };
      const canView = operatorCanViewEmployees();
      const employeesRequest = loadEmployeesReference({
        month: requestedMonth,
        apply: false,
        force: canView && useEmbeddedPayrollReport,
      });
      const payrollRequest = canView
        ? (useEmbeddedPayrollReport
          ? employeesRequest.then((data) => {
            const embeddedReport = payrollReportFromEmployeesData(data, requestedMonth);
            if (embeddedReport || !isCurrent()) return embeddedReport;
            return loadPayrollReport({ month: requestedMonth, apply: false });
          })
          : loadPayrollReport({ month: requestedMonth, apply: false }))
        : null;
      const promise = Promise.all([
        employeesRequest,
        payrollRequest,
        moduleReady,
      ]).then(([employeesData, payrollReport]) => {
        if (!isCurrent()) return result;
        applyEmployeesReferenceData(employeesData, requestedMonth);
        state.payrollReport = payrollReport;
        state.payrollReportMonth = canView ? requestedMonth : '';
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
