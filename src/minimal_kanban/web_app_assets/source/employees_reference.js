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

    async function loadEmployeesReference({ month: requestedMonth = '', apply = true, force = false } = {}) {
      const viewerStateGeneration = state.viewerStateGeneration;
      const session = state.operatorSessionToken;
      const accessRevision = state.employeesCashboxesAccessRevision;
      const month = String(requestedMonth || state.payrollMonth || currentPayrollMonthValue()).trim();
      if (!force && state.employeesLoadedMonth === month && Array.isArray(state.employees)) {
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
      const requestedMonth = String(month || state.payrollMonth || currentPayrollMonthValue()).trim();
      state.payrollMonth = requestedMonth;
      const generation = ++state.employeesWorkspaceLoadGeneration;
      const isCurrent = () => viewer === state.viewerStateGeneration && session === state.operatorSessionToken
        && access === state.employeesCashboxesAccessRevision
        && generation === state.employeesWorkspaceLoadGeneration && requestedMonth === state.payrollMonth;
      const result = { applied: false, generation, month: requestedMonth };
      const canManage = operatorCanAccessEmployeesCashboxes();
      const employeesRequest = loadEmployeesReference({
        month: requestedMonth,
        apply: false,
        force: canManage && useEmbeddedPayrollReport,
      });
      const payrollRequest = canManage
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
