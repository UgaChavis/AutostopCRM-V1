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
      ) {
        request = state.employeesReferencePromise.promise;
      } else {
        request = api('/api/list_employees?month=' + encodeURIComponent(month))
          .finally(() => {
            if (state.employeesReferencePromise?.promise === request) {
              state.employeesReferencePromise = null;
            }
          });
        state.employeesReferencePromise = { month, viewerStateGeneration, promise: request };
      }
      const data = await request;
      if (
        viewerStateGeneration !== state.viewerStateGeneration
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
