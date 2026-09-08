    function mobileEmployeeSummaryMap() {
      return payrollSummaryMap();
    }

    function mobileEmployeeBalanceValue(employee, summary) {
      return String(employee?.balance_total ?? summary?.balance_total ?? summary?.total_salary ?? '0');
    }

    function mobileEmployeeMoneyText(value) {
      const raw = String(value ?? '').trim();
      return raw ? repairOrderFormatRubles(raw) : repairOrderFormatRubles(0);
    }

    function mobileEmployeeDetailRows(employeeId) {
      const normalizedId = String(employeeId || '').trim();
      if (!normalizedId) return [];
      return (Array.isArray(state.payrollReport?.detail_rows) ? state.payrollReport.detail_rows : [])
        .filter((row) => String(row?.employee_id || '').trim() === normalizedId);
    }

    function mobileEmployeeAccrualTitle(row) {
      const type = String(row?.type_label || row?.row_type || 'Начисление').trim();
      const number = String(row?.repair_order_number || '').trim();
      const vehicle = String(row?.vehicle || '').trim();
      return [
        type,
        number ? ('№ ' + number) : '',
        vehicle,
      ].filter(Boolean).join(' · ') || 'Начисление';
    }

    function mobileEmployeeAccrualMeta(row) {
      const parts = [];
      if (row?.closed_at) parts.push(formatDate(row.closed_at));
      const worksCount = finiteNonNegativeNumber(row?.works_count);
      const materialsCount = finiteNonNegativeNumber(row?.materials_count);
      if (worksCount > 0) parts.push('РАБОТ: ' + String(worksCount));
      if (String(row?.work_total || '').trim() && String(row?.work_total || '0') !== '0') {
        parts.push('РАБОТЫ ' + mobileEmployeeMoneyText(row.work_total));
      }
      if (materialsCount > 0) parts.push('МАТ.: ' + String(materialsCount));
      if (String(row?.material_profit || '').trim() && String(row?.material_profit || '0') !== '0') {
        parts.push('ПРИБЫЛЬ ' + mobileEmployeeMoneyText(row.material_profit));
      }
      return parts.join(' · ') || 'ДЕТАЛЕЙ НЕТ';
    }

    function renderMobileEmployeesList() {
      if (!els.mobileEmployeesList) return;
      const employees = filteredEmployeesList();
      const readOnly = operatorHasEmployeesReadOnlyAccess();
      const summaryMap = mobileEmployeeSummaryMap();
      if (state.mobileEmployeesLoading && !employees.length) {
        els.mobileEmployeesList.innerHTML = '<div class="mobile-employee-detail__empty">ЗАГРУЗКА СОТРУДНИКОВ...</div>';
        return;
      }
      if (!employees.length) {
        els.mobileEmployeesList.innerHTML = '<div class="mobile-employee-detail__empty">СОТРУДНИКОВ ПОКА НЕТ.</div>';
        return;
      }
      if (readOnly) {
        els.mobileEmployeesList.innerHTML = employees.map((employee) => {
          const isActive = String(employee.id || '') === String(state.activeEmployeeId || '');
          return '<button class="mobile-employee-row' + (isActive ? ' is-active' : '') + '" type="button" data-mobile-employee-id="' + escapeHtml(employee.id || '') + '">'
            + '<div class="mobile-employee-row__top">'
              + '<div class="mobile-employee-row__name">' + escapeHtml(employee.name || 'Сотрудник') + '</div>'
            + '</div>'
            + '<div class="mobile-employee-row__meta">' + escapeHtml(employee.position || 'Без должности') + '</div>'
            + '<div class="mobile-employee-row__meta">ТОЛЬКО ПРОСМОТР</div>'
          + '</button>';
        }).join('');
        return;
      }
      els.mobileEmployeesList.innerHTML = employees.map((employee) => {
        const summary = summaryMap.get(String(employee.id || ''));
        const balance = mobileEmployeeBalanceValue(employee, summary);
        const isActive = String(employee.id || '') === String(state.activeEmployeeId || '');
        return '<button class="mobile-employee-row' + (isActive ? ' is-active' : '') + '" type="button" data-mobile-employee-id="' + escapeHtml(employee.id || '') + '">'
          + '<div class="mobile-employee-row__top">'
            + '<div class="mobile-employee-row__name">' + escapeHtml(employee.name || 'Сотрудник') + '</div>'
            + '<div class="mobile-employee-row__balance">' + escapeHtml(mobileEmployeeMoneyText(balance)) + '</div>'
          + '</div>'
          + '<div class="mobile-employee-row__meta">' + escapeHtml(employee.position || 'Без должности') + '</div>'
          + '<div class="mobile-employee-row__meta">' + escapeHtml(employeeIncentiveSummaryLabel(employee)) + '</div>'
        + '</button>';
      }).join('');
    }

    function renderMobileEmployeeDetail() {
      if (!els.mobileEmployeeDetail) return;
      const employees = filteredEmployeesList();
      let employee = selectedEmployeeRecord();
      if (!employee && employees.length) {
        employee = employees[0];
        state.activeEmployeeId = employee.id || '';
      }
      if (state.mobileEmployeesLoading && !employee) {
        els.mobileEmployeeDetail.innerHTML = '<div class="mobile-employee-detail__empty">ЗАГРУЗКА НАЧИСЛЕНИЙ...</div>';
        return;
      }
      if (!employee) {
        els.mobileEmployeeDetail.innerHTML = '<div class="mobile-employee-detail__empty">ВЫБЕРИТЕ СОТРУДНИКА, ЧТОБЫ УВИДЕТЬ НАЧИСЛЕНИЯ.</div>';
        return;
      }
      if (operatorHasEmployeesReadOnlyAccess()) {
        els.mobileEmployeeDetail.innerHTML = '<div class="mobile-employee-detail__head">'
          + '<div>'
            + '<div class="mobile-employee-detail__name">' + escapeHtml(employee.name || 'Сотрудник') + '</div>'
            + '<div class="mobile-employee-detail__meta">' + escapeHtml(employee.position || 'Без должности') + '</div>'
          + '</div>'
        + '</div>'
        + '<section class="mobile-employee-detail__section"><h4>ТОЛЬКО ПРОСМОТР</h4><div class="mobile-employee-detail__empty">Зарплаты, начисления, отчёты и изменение данных недоступны.</div></section>';
        return;
      }
      const summary = mobileEmployeeSummaryMap().get(String(employee.id || '')) || {};
      const balance = mobileEmployeeBalanceValue(employee, summary);
      const details = mobileEmployeeDetailRows(employee.id);
      const detailsHtml = details.length
        ? details.slice(0, 5).map((row) => {
          return '<div class="mobile-employee-accrual">'
            + '<strong>' + escapeHtml(mobileEmployeeAccrualTitle(row)) + '</strong>'
            + '<span>' + escapeHtml(mobileEmployeeAccrualMeta(row)) + '</span>'
            + '<span>НАЧИСЛЕНО ' + escapeHtml(mobileEmployeeMoneyText(row?.salary_amount ?? 0)) + '</span>'
          + '</div>';
        }).join('')
        : '<div class="mobile-employee-detail__empty">НАЧИСЛЕНИЙ ЗА МЕСЯЦ ПОКА НЕТ.</div>';
      els.mobileEmployeeDetail.innerHTML = '<div class="mobile-employee-detail__head">'
          + '<div>'
            + '<div class="mobile-employee-detail__name">' + escapeHtml(employee.name || 'Сотрудник') + '</div>'
            + '<div class="mobile-employee-detail__meta">' + escapeHtml(employee.position || 'Без должности') + ' · ' + escapeHtml(employeeIncentiveSummaryLabel(employee)) + '</div>'
          + '</div>'
          + '<div class="mobile-employee-detail__balance">' + escapeHtml(mobileEmployeeMoneyText(balance)) + '</div>'
        + '</div>'
        + '<div class="mobile-employee-kpis">'
          + '<div class="mobile-employee-kpi"><span>К выплате</span><strong>' + escapeHtml(mobileEmployeeMoneyText(balance)) + '</strong></div>'
          + '<div class="mobile-employee-kpi"><span>Начислено</span><strong>' + escapeHtml(mobileEmployeeMoneyText(summary.total_salary ?? summary.accrued_total ?? 0)) + '</strong></div>'
          + '<div class="mobile-employee-kpi"><span>Работы</span><strong>' + escapeHtml(String(finiteNonNegativeNumber(summary.works_count))) + ' / ' + escapeHtml(mobileEmployeeMoneyText(summary.work_accrued_total ?? 0)) + '</strong></div>'
          + '<div class="mobile-employee-kpi"><span>Материалы</span><strong>' + escapeHtml(String(finiteNonNegativeNumber(summary.materials_count))) + ' / ' + escapeHtml(mobileEmployeeMoneyText(summary.materials_accrued_total ?? 0)) + '</strong></div>'
        + '</div>'
        + '<section class="mobile-employee-detail__section"><h4>Последние начисления</h4>' + detailsHtml + '</section>';
    }

    function renderMobileEmployeesPanel() {
      const isOpen = state.mobileMorePanel === 'employees';
      if (els.mobileEmployeesPanel) els.mobileEmployeesPanel.hidden = !isOpen;
      syncMobileMorePanelChrome();
      if (!isOpen) return;
      const employees = filteredEmployeesList();
      if (!state.activeEmployeeId && employees.length) {
        state.activeEmployeeId = employees[0].id || '';
      }
      if (els.mobileEmployeesMeta) {
        const month = state.payrollMonth || currentPayrollMonthValue();
        if (state.mobileEmployeesLoading) {
          els.mobileEmployeesMeta.textContent = 'ЗАГРУЗКА...';
        } else if (operatorHasEmployeesReadOnlyAccess()) {
          els.mobileEmployeesMeta.textContent = 'ТОЛЬКО ПРОСМОТР · АКТИВНЫХ: ' + String(employees.length);
        } else if (employees.length) {
          els.mobileEmployeesMeta.textContent = 'МЕСЯЦ: ' + month + ' · АКТИВНЫХ: ' + String(employees.length);
        } else {
          els.mobileEmployeesMeta.textContent = 'СОТРУДНИКОВ ПОКА НЕТ';
        }
      }
      renderMobileEmployeesList();
      renderMobileEmployeeDetail();
    }

    async function loadMobileEmployees({ force = false } = {}) {
      const month = state.payrollMonth || currentPayrollMonthValue();
      const needsPayrollData = operatorCanAccessEmployeesCashboxes();
      if (!force && state.employeesLoadedMonth === month && Array.isArray(state.employees) && (!needsPayrollData || state.payrollReportMonth === month)) {
        renderMobileEmployeesPanel();
        return;
      }
      state.payrollMonth = month;
      state.mobileEmployeesLoading = true;
      renderMobileEmployeesPanel();
      try {
        await loadEmployeesWorkspaceData(month);
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        state.mobileEmployeesLoading = false;
        renderMobileEmployeesPanel();
      }
    }

    function openMobileEmployeesPanel() {
      if (!requireEmployeesViewAccess()) return;
      state.mobileMorePanel = 'employees';
      renderMobileMore();
      loadMobileEmployees();
    }

    function handleMobileEmployeesClick(event) {
      const button = event.target instanceof HTMLElement ? event.target.closest('[data-mobile-employee-id]') : null;
      if (!button || !els.mobileEmployeesPanel?.contains(button)) return;
      const employeeId = String(button.getAttribute('data-mobile-employee-id') || '').trim();
      if (!employeeId) return;
      event.preventDefault();
      state.activeEmployeeId = employeeId;
      state.employeeCreateMode = false;
      state.employeesReportDetailsOpen = true;
      renderMobileEmployeesPanel();
    }
