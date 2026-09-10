    function employeeAsyncContext(key, entityField = '', { singleFlight = false } = {}) {
      if (singleFlight && state[key]) return null;
      const token = {};
      const viewer = state.viewerStateGeneration;
      const session = state.operatorSessionToken;
      const access = state.employeesCashboxesAccessRevision;
      const month = state.payrollMonth || currentPayrollMonthValue();
      let employee = entityField ? state[entityField] : '';
      let salaryView = state.employeeSalaryViewGeneration;
      state[key] = token;
      const owns = () => state[key] === token
        && viewer === state.viewerStateGeneration && session === state.operatorSessionToken
        && access === state.employeesCashboxesAccessRevision
        && (!entityField || employee === state[entityField])
        && (entityField !== 'activeEmployeeSalaryId' || salaryView === state.employeeSalaryViewGeneration);
      const isCurrent = () => owns() && month === (state.payrollMonth || currentPayrollMonthValue());
      isCurrent.owns = owns;
      isCurrent.month = month;
      isCurrent.rebindEntity = () => {
        employee = entityField ? state[entityField] : '';
        salaryView = state.employeeSalaryViewGeneration;
        return isCurrent;
      };
      isCurrent.release = () => {
        if (state[key] !== token) return false;
        state[key] = null;
        return true;
      };
      return isCurrent;
    }

    function syncEmployeeMoneyMutationControls() {
      const pending = Boolean(state.employeeMoneyMutationOperation);
      for (const key of [
        'employeeSalaryActionConfirmButton',
        'employeeSalaryAdvanceConfirmButton',
        'employeeShiftAccrualConfirmButton',
      ]) {
        if (els[key]) els[key].disabled = pending;
      }
    }

    function syncEmployeeEditMutationControls() {
      const pending = Boolean(state.employeeEditOperation);
      if (els.employeeSaveButton) els.employeeSaveButton.disabled = pending;
      if (els.employeeDeleteButton) {
        els.employeeDeleteButton.disabled = pending || !selectedEmployeeRecord();
      }
    }

    async function refreshEmployeePayroll(isCurrent) {
      if (!isCurrent()) return false;
      state.employeesReferencePromise = null;
      const employees = await loadEmployeesReference({ month: isCurrent.month, apply: false });
      if (!isCurrent()) return false;
      const report = await loadPayrollReport({ month: isCurrent.month, apply: false });
      if (!isCurrent()) return false;
      applyEmployeesReferenceData(employees, isCurrent.month);
      state.payrollReport = report;
      state.payrollReportMonth = isCurrent.month;
      return true;
    }

    const EMPLOYEE_INCENTIVE_DEFINITIONS = [
      {
        kind: 'base_salary',
        inputKey: 'employeeBaseSalaryInput',
        label: 'Оклад',
        shortLabel: 'Оклад',
        hint: 'Фиксированная сумма за неделю. Начисляется по пятницам в 20:00.',
        valueLabel: 'Сумма',
        placeholder: '0',
        defaultValue: '0',
        inactiveValue: '',
        activeModes: ['salary_only', 'salary_plus_percent'],
      },
      {
        kind: 'work_percent',
        inputKey: 'employeeWorkPercentInput',
        label: 'Выплата с работ',
        shortLabel: '% с работ',
        hint: 'Процент от закрытых работ сотрудника.',
        valueLabel: 'Процент',
        placeholder: '0',
        defaultValue: '0',
        inactiveValue: '',
        activeModes: ['percent_only', 'salary_plus_percent'],
      },
      {
        kind: 'material_percent',
        inputKey: 'employeeMaterialPercentInput',
        label: 'Материалы и запчасти',
        shortLabel: '% с материалов',
        hint: 'Процент от прибыли продажи материалов и запчастей.',
        valueLabel: 'Процент',
        placeholder: '10',
        defaultValue: '10',
        inactiveValue: '0',
        activeModes: [],
      },
      {
        kind: 'repair_order_percent',
        inputKey: 'employeeRepairOrderPercentInput',
        label: 'Заказ-наряды',
        shortLabel: '% от наличной стоимости ЗН',
        hint: 'Процент от стоимости заказ-наряда за наличный расчёт: работы плюс материалы без безналичной надбавки, независимо от способа оплаты.',
        valueLabel: 'Процент',
        placeholder: '0',
        defaultValue: '0',
        inactiveValue: '0',
        activeModes: [],
      },
    ];

    function employeeSalaryModeLabel(mode) {
      if (mode === 'none') return 'БЕЗ НАЧИСЛЕНИЙ';
      if (mode === 'salary_only') return 'ОКЛАД';
      if (mode === 'percent_only') return '% ОТ РАБОТ';
      return 'ОКЛАД + %';
    }

    function normalizeEmployeeComparableText(value) {
      return String(value ?? '').trim();
    }

    function normalizeEmployeeComparableNumber(value) {
      const parsed = repairOrderParseNumber(value);
      return parsed === null ? '' : repairOrderNumberToRaw(parsed);
    }

    function employeeCurrentPayrollTerm(employee) {
      if (!employee || typeof employee !== 'object') return {};
      if (employee.current_payroll_term && typeof employee.current_payroll_term === 'object') {
        return { ...employee, ...employee.current_payroll_term };
      }
      const now = Date.now();
      const terms = (Array.isArray(employee.payroll_terms) ? employee.payroll_terms : [])
        .map((term) => ({
          term,
          startsAt: Date.parse(String(term?.effective_from || '')),
          endsAt: term?.effective_to ? Date.parse(String(term.effective_to)) : Number.POSITIVE_INFINITY,
        }))
        .filter((item) => Number.isFinite(item.startsAt))
        .sort((left, right) => left.startsAt - right.startsAt);
      const current = terms.slice().reverse().find(
        (item) => item.startsAt <= now && now < item.endsAt
      )?.term;
      return current ? { ...employee, ...current } : employee;
    }

    function employeePayrollFormulaLabel(employee) {
      const current = employeeCurrentPayrollTerm(employee);
      const parts = [];
      const baseSalary = normalizeEmployeeComparableNumber(current.base_salary);
      const workPercent = normalizeEmployeeComparableNumber(current.work_percent);
      const materialPercent = normalizeEmployeeComparableNumber(current.material_percent);
      const repairOrderPercent = normalizeEmployeeComparableNumber(current.repair_order_percent);
      if (baseSalary && baseSalary !== '0') parts.push('Оклад ' + baseSalary + ' ₽/нед.');
      if (workPercent && workPercent !== '0') parts.push(workPercent + '% с работ');
      if (materialPercent && materialPercent !== '0') parts.push(materialPercent + '% с прибыли материалов');
      if (repairOrderPercent && repairOrderPercent !== '0') parts.push(repairOrderPercent + '% от стоимости заказ-наряда за наличный расчёт');
      return parts.length ? parts.join(' + ') : 'Без начислений';
    }

    function employeeSalaryModeFromIncentives(flags) {
      const hasBase = Boolean(flags?.base_salary);
      const hasWork = Boolean(flags?.work_percent);
      if (hasBase && hasWork) return 'salary_plus_percent';
      if (hasBase) return 'salary_only';
      if (hasWork) return 'percent_only';
      return 'none';
    }

    function employeeIncentiveDefinition(kind) {
      return EMPLOYEE_INCENTIVE_DEFINITIONS.find((item) => item.kind === kind) || null;
    }

    function employeeIncentiveInput(kind) {
      const definition = employeeIncentiveDefinition(kind);
      const inputKey = definition?.inputKey || '';
      return inputKey ? els[inputKey] : null;
    }

    function employeeIncentiveFlagsFromValues(mode, baseSalary, workPercent, materialPercent, repairOrderPercent) {
      const normalizedMode = normalizeEmployeeComparableText(mode || 'percent_only');
      const rawValues = {
        base_salary: baseSalary,
        work_percent: workPercent,
        material_percent: materialPercent,
        repair_order_percent: repairOrderPercent,
      };
      return EMPLOYEE_INCENTIVE_DEFINITIONS.reduce((flags, item) => {
        const normalizedValue = normalizeEmployeeComparableNumber(rawValues[item.kind]);
        const activeByValue = Boolean(normalizedValue && normalizedValue !== '0');
        const activeByMode = Array.isArray(item.activeModes) && item.activeModes.includes(normalizedMode);
        flags[item.kind] = activeByMode || activeByValue;
        return flags;
      }, {});
    }

    function employeeIncentiveSummaryLabel(employee) {
      const current = employeeCurrentPayrollTerm(employee);
      const flags = employeeIncentiveFlagsFromValues(
        current?.salary_mode,
        current?.base_salary,
        current?.work_percent,
        current?.material_percent,
        current?.repair_order_percent,
      );
      return Object.values(flags).some(Boolean)
        ? employeePayrollFormulaLabel(current)
        : 'БЕЗ НАЧИСЛЕНИЙ';
    }

    function currentEmployeeIncentiveFlags() {
      return employeeIncentiveFlagsFromValues(
        els.employeeSalaryModeInput?.value,
        els.employeeBaseSalaryInput?.value,
        els.employeeWorkPercentInput?.value,
        els.employeeMaterialPercentInput?.value,
        els.employeeRepairOrderPercentInput?.value,
      );
    }

    function employeeIncentiveFieldValue(kind) {
      return String(employeeIncentiveInput(kind)?.value || '');
    }

    function setEmployeeIncentiveFieldValue(kind, value) {
      const input = employeeIncentiveInput(kind);
      if (input) input.value = String(value ?? '');
    }

    function syncEmployeeSalaryModeFromIncentives(flags = currentEmployeeIncentiveFlags()) {
      if (els.employeeSalaryModeInput) {
        els.employeeSalaryModeInput.value = employeeSalaryModeFromIncentives(flags);
      }
    }

    function setEmployeeIncentiveActive(kind, active) {
      const definition = employeeIncentiveDefinition(kind);
      if (!definition) return;
      const flags = currentEmployeeIncentiveFlags();
      const wasActive = Boolean(flags[kind]);
      flags[kind] = Boolean(active);
      if (active && (!wasActive || !employeeIncentiveFieldValue(kind))) {
        setEmployeeIncentiveFieldValue(kind, definition.defaultValue ?? '0');
      }
      if (!active) {
        setEmployeeIncentiveFieldValue(kind, definition.inactiveValue ?? '');
      }
      syncEmployeeSalaryModeFromIncentives(flags);
      renderEmployeeIncentives();
      renderEmployeeProfileMeta();
      const nextInput = els.employeeIncentivesList?.querySelector('[data-employee-incentive-value="' + kind + '"]');
      if (active && nextInput instanceof HTMLInputElement) {
        setTimeout(() => nextInput.focus(), 0);
      }
    }

    function renderEmployeeIncentives() {
      if (!els.employeeIncentivesList || !els.employeeIncentiveAddChoices) return;
      const flags = currentEmployeeIncentiveFlags();
      const activeDefinitions = EMPLOYEE_INCENTIVE_DEFINITIONS.filter((item) => Boolean(flags[item.kind]));
      const inactiveDefinitions = EMPLOYEE_INCENTIVE_DEFINITIONS.filter((item) => !flags[item.kind]);
      els.employeeIncentiveAddChoices.innerHTML = inactiveDefinitions.length
        ? inactiveDefinitions.map((item) => (
          '<button class="btn btn--ghost" type="button" data-employee-incentive-add="' + escapeHtml(item.kind) + '">+ ' + escapeHtml(item.shortLabel) + '</button>'
        )).join('')
        : '<span class="employees-row__summary-label">ВСЁ ДОБАВЛЕНО</span>';
      if (!activeDefinitions.length) {
        els.employeeIncentivesList.innerHTML = '<div class="employees-incentives__empty">Начисления не добавлены. Добавьте оклад, процент с работ или процент с материалов.</div>';
        return;
      }
      els.employeeIncentivesList.innerHTML = activeDefinitions.map((item) => {
        const value = employeeIncentiveFieldValue(item.kind);
        return '<div class="employees-incentive-row" data-employee-incentive-row="' + escapeHtml(item.kind) + '">'
          + '<div class="employees-incentive-row__main">'
            + '<div class="employees-incentive-row__title">' + escapeHtml(item.label) + '</div>'
            + '<div class="employees-incentive-row__hint">' + escapeHtml(item.hint) + '</div>'
          + '</div>'
          + '<label class="employees-incentive-row__value"><span>' + escapeHtml(item.valueLabel) + '</span><input type="text" inputmode="decimal" maxlength="40" placeholder="' + escapeHtml(item.placeholder) + '" value="' + escapeHtml(value) + '" data-employee-incentive-value="' + escapeHtml(item.kind) + '"></label>'
          + '<button class="btn btn--ghost employees-incentive-row__remove" type="button" data-employee-incentive-remove="' + escapeHtml(item.kind) + '">УДАЛИТЬ</button>'
          + '</div>';
      }).join('');
    }

    function renderEmployeeShiftAccrualDialog() {
      const employee = selectedEmployeeRecord();
      const isOpen = Boolean(state.employeeShiftAccrualOpen && employee && !state.employeeCreateMode);
      if (els.employeeShiftAccrualDialog) els.employeeShiftAccrualDialog.hidden = !isOpen;
      if (els.employeeShiftAccrualButton) {
        els.employeeShiftAccrualButton.disabled = !employee || state.employeeCreateMode || employeeFormHasUnsavedChanges();
      }
      if (!isOpen) return;
      if (els.employeeShiftAccrualAmountInput && !String(els.employeeShiftAccrualAmountInput.value || '').trim()) {
        els.employeeShiftAccrualAmountInput.value = state.employeeShiftAccrualDraft || '';
      }
    }

    function openEmployeeShiftAccrualDialog() {
      const employee = selectedEmployeeRecord();
      if (!employee || state.employeeCreateMode) {
        setStatus('СНАЧАЛА ВЫБЕРИТЕ СОТРУДНИКА.', true);
        return;
      }
      if (employeeFormHasUnsavedChanges()) {
        setStatus('СНАЧАЛА СОХРАНИТЕ ИЗМЕНЕНИЯ СОТРУДНИКА.', true);
        return;
      }
      state.employeeShiftAccrualOpen = true;
      state.employeeShiftAccrualDraft = '';
      const isCurrent = employeeAsyncContext('employeeShiftAccrualDialogRequest', 'activeEmployeeId');
      syncEmployeeMoneyMutationControls();
      if (els.employeeShiftAccrualAmountInput) els.employeeShiftAccrualAmountInput.value = '';
      renderEmployeeShiftAccrualDialog();
      if (els.employeeShiftAccrualAmountInput) {
        setTimeout(() => { if (isCurrent()) els.employeeShiftAccrualAmountInput.focus(); }, 0);
      }
    }

    function closeEmployeeShiftAccrualDialog() {
      state.employeeShiftAccrualDialogRequest = null;
      state.employeeShiftAccrualOpen = false;
      state.employeeShiftAccrualDraft = '';
      if (els.employeeShiftAccrualAmountInput) els.employeeShiftAccrualAmountInput.value = '';
      renderEmployeeShiftAccrualDialog();
    }

    function employeeComparableSnapshot(employee = null) {
      const current = employeeCurrentPayrollTerm(employee);
      return {
        name: normalizeEmployeeComparableText(employee?.name),
        position: normalizeEmployeeComparableText(employee?.position),
        salary_mode: normalizeEmployeeComparableText(current?.salary_mode || 'percent_only'),
        base_salary: normalizeEmployeeComparableNumber(current?.base_salary),
        work_percent: normalizeEmployeeComparableNumber(current?.work_percent),
        material_percent: normalizeEmployeeComparableNumber(current?.material_percent),
        repair_order_percent: normalizeEmployeeComparableNumber(current?.repair_order_percent),
        is_active: employee ? Boolean(employee.is_active) : true,
      };
    }

    function employeeCombinedNameFromForm() {
      return [els.employeeNameInput?.value, els.employeeMiddleNameInput?.value]
        .map((part) => String(part || '').trim())
        .filter(Boolean)
        .join(' ');
    }

    function employeeFormSnapshot() {
      return {
        name: normalizeEmployeeComparableText(employeeCombinedNameFromForm()),
        position: normalizeEmployeeComparableText(els.employeePositionInput?.value),
        salary_mode: normalizeEmployeeComparableText(els.employeeSalaryModeInput?.value || 'percent_only'),
        base_salary: normalizeEmployeeComparableNumber(els.employeeBaseSalaryInput?.value),
        work_percent: normalizeEmployeeComparableNumber(els.employeeWorkPercentInput?.value),
        material_percent: normalizeEmployeeComparableNumber(els.employeeMaterialPercentInput?.value),
        repair_order_percent: normalizeEmployeeComparableNumber(els.employeeRepairOrderPercentInput?.value),
        is_active: Boolean(selectedEmployeeRecord()?.is_active ?? true),
      };
    }

    function employeeFormHasUnsavedChanges() {
      const baseline = state.employeeFormBaseline || employeeComparableSnapshot(selectedEmployeeRecord());
      return JSON.stringify(employeeFormSnapshot()) !== JSON.stringify(baseline);
    }

    function employeeRowAriaLabel(employee, summaryValue) {
      return [
        String(employee?.name || 'Без имени'),
        String(employee?.position || 'без должности'),
        'начисления ' + employeeIncentiveSummaryLabel(employee),
        'к выплате ' + String(summaryValue || '0'),
      ].filter(Boolean).join('. ');
    }

    function employeeSalaryReconciliationDateInputValue(date) {
      if (!(date instanceof Date) || Number.isNaN(date.getTime())) return '';
      return [
        String(date.getFullYear()).padStart(4, '0'),
        String(date.getMonth() + 1).padStart(2, '0'),
        String(date.getDate()).padStart(2, '0'),
      ].join('-');
    }

    function employeeSalaryReconciliationTodayInputValue() {
      return employeeSalaryReconciliationDateInputValue(new Date());
    }

    function employeeSalaryReconciliationDefaultDateFrom(daysValue) {
      const rawDaysValue = daysValue === undefined || daysValue === null || daysValue === '' ? '30' : daysValue;
      const days = Number.parseInt(String(rawDaysValue), 10);
      const safeDays = Number.isFinite(days) && days > 0 ? Math.min(days, 366) : 30;
      const date = new Date();
      date.setDate(date.getDate() - safeDays);
      return employeeSalaryReconciliationDateInputValue(date);
    }

    function employeeSalaryReconciliationNormalizedDays({ strict = false } = {}) {
      const raw = String(els.employeeSalaryReconciliationDaysInput?.value || state.employeeSalaryReconciliationDays || '').trim();
      if (!raw) {
        if (strict) setStatus('УКАЖИТЕ КОЛИЧЕСТВО ДНЕЙ ДЛЯ АКТА.', true);
        return strict ? null : 30;
      }
      const parsed = Number.parseInt(raw, 10);
      if (!Number.isFinite(parsed) || String(parsed) !== raw.replace(/^\+/, '')) {
        if (strict) setStatus('КОЛИЧЕСТВО ДНЕЙ ДОЛЖНО БЫТЬ ЦЕЛЫМ ЧИСЛОМ.', true);
        return strict ? null : 30;
      }
      if (parsed < 1 || parsed > 366) {
        if (strict) setStatus('ПЕРИОД АКТА ДОЛЖЕН БЫТЬ ОТ 1 ДО 366 ДНЕЙ.', true);
        return strict ? null : Math.min(Math.max(parsed, 1), 366);
      }
      return parsed;
    }

    function employeeSalaryReconciliationQueryParams(employeeId, { strict = false } = {}) {
      const requestedId = String(employeeId || '').trim();
      if (!requestedId) return null;
      const params = new URLSearchParams();
      params.set('employee_id', requestedId);
      const mode = String(state.employeeSalaryReconciliationPeriodMode || 'days');
      if (mode === 'dates') {
        const dateFrom = String(els.employeeSalaryReconciliationDateFromInput?.value || state.employeeSalaryReconciliationDateFrom || '').trim();
        const dateTo = String(els.employeeSalaryReconciliationDateToInput?.value || state.employeeSalaryReconciliationDateTo || '').trim();
        if (!dateFrom || !dateTo) {
          if (strict) setStatus('УКАЖИТЕ ДАТЫ НАЧАЛА И ОКОНЧАНИЯ АКТА.', true);
          return strict ? null : params;
        }
        if (dateFrom > dateTo) {
          if (strict) setStatus('ДАТА НАЧАЛА АКТА НЕ МОЖЕТ БЫТЬ ПОЗЖЕ ДАТЫ ОКОНЧАНИЯ.', true);
          return strict ? null : params;
        }
        params.set('date_from', dateFrom);
        params.set('date_to', dateTo);
      } else {
        const days = employeeSalaryReconciliationNormalizedDays({ strict });
        if (days === null) return null;
        params.set('days', String(days));
      }
      return params;
    }

    function employeeSalaryReconciliationApiPath(employeeId, options = {}) {
      const params = employeeSalaryReconciliationQueryParams(employeeId, options);
      if (!params) return '';
      return '/api/get_employee_salary_reconciliation?' + params.toString();
    }

    function syncEmployeeSalaryReconciliationPeriodUi() {
      const mode = state.employeeSalaryReconciliationPeriodMode === 'dates' ? 'dates' : 'days';
      state.employeeSalaryReconciliationPeriodMode = mode;
      const days = employeeSalaryReconciliationNormalizedDays();
      state.employeeSalaryReconciliationDays = String(days);
      if (els.employeeSalaryReconciliationPeriodMode) {
        els.employeeSalaryReconciliationPeriodMode.value = mode;
      }
      if (els.employeeSalaryReconciliationDaysInput) {
        els.employeeSalaryReconciliationDaysInput.value = state.employeeSalaryReconciliationDays;
      }
      if (!state.employeeSalaryReconciliationDateTo) {
        state.employeeSalaryReconciliationDateTo = employeeSalaryReconciliationTodayInputValue();
      }
      if (!state.employeeSalaryReconciliationDateFrom) {
        state.employeeSalaryReconciliationDateFrom = employeeSalaryReconciliationDefaultDateFrom(days);
      }
      if (state.employeeSalaryReconciliationDateFrom > state.employeeSalaryReconciliationDateTo) {
        state.employeeSalaryReconciliationDateTo = state.employeeSalaryReconciliationDateFrom;
      }
      if (els.employeeSalaryReconciliationDateFromInput) {
        els.employeeSalaryReconciliationDateFromInput.value = state.employeeSalaryReconciliationDateFrom;
      }
      if (els.employeeSalaryReconciliationDateToInput) {
        els.employeeSalaryReconciliationDateToInput.value = state.employeeSalaryReconciliationDateTo;
      }
      const dateMode = mode === 'dates';
      if (els.employeeSalaryReconciliationDaysField) {
        els.employeeSalaryReconciliationDaysField.hidden = dateMode;
      }
      if (els.employeeSalaryReconciliationDateFromField) {
        els.employeeSalaryReconciliationDateFromField.hidden = !dateMode;
      }
      if (els.employeeSalaryReconciliationDateToField) {
        els.employeeSalaryReconciliationDateToField.hidden = !dateMode;
      }
    }

    function handleEmployeeSalaryReconciliationPeriodChange() {
      state.employeeSalaryReconciliationPeriodMode = els.employeeSalaryReconciliationPeriodMode?.value === 'dates' ? 'dates' : 'days';
      state.employeeSalaryReconciliationDays = String(employeeSalaryReconciliationNormalizedDays() ?? 30);
      state.employeeSalaryReconciliationDateFrom = String(els.employeeSalaryReconciliationDateFromInput?.value || state.employeeSalaryReconciliationDateFrom || '').trim();
      state.employeeSalaryReconciliationDateTo = String(els.employeeSalaryReconciliationDateToInput?.value || state.employeeSalaryReconciliationDateTo || '').trim();
      syncEmployeeSalaryReconciliationPeriodUi();
    }

    function confirmDiscardEmployeeChanges() {
      if (!employeeFormHasUnsavedChanges()) return true;
      return window.confirm('Несохранённые изменения сотрудника будут потеряны. Продолжить?');
    }

    function filteredEmployeesList() {
      const employees = Array.isArray(state.employees) ? state.employees : [];
      return employees
        .filter((employee) => {
          if (!employee) return false;
          return Boolean(employee.is_active);
        })
        .slice()
        .sort((left, right) => String(left?.name || '').localeCompare(String(right?.name || ''), 'ru'));
    }

    function syncEmployeesReadOnlyWorkspaceUi() {
      const canManageEmployees = operatorCanAccessEmployeesCashboxes();
      const readOnly = operatorHasEmployeesReadOnlyAccess();
      if (els.employeesCreateButton) {
        els.employeesCreateButton.hidden = !canManageEmployees;
        els.employeesCreateButton.disabled = !canManageEmployees;
      }
      if (els.employeesProfilePanel) els.employeesProfilePanel.hidden = !canManageEmployees;
      if (els.employeesReportPanel) els.employeesReportPanel.hidden = !canManageEmployees;
      if (els.employeesReadOnlyNotice) els.employeesReadOnlyNotice.hidden = !readOnly;
    }

    function payrollSummaryMap() {
      const rows = Array.isArray(state.payrollReport?.summary) ? state.payrollReport.summary : [];
      return rows.reduce((map, row) => {
        map.set(String(row.employee_id || ''), row);
        return map;
      }, new Map());
    }

    function renderEmployeeProfileMeta() {
      if (!els.employeesMeta) return;
      const selectedEmployee = selectedEmployeeRecord();
      const currentTerm = employeeCurrentPayrollTerm(selectedEmployee);
      const mode = String(els.employeeSalaryModeInput?.value || currentTerm?.salary_mode || 'salary_plus_percent').trim();
      const parts = [];
      if (selectedEmployee) {
        parts.push(selectedEmployee.is_active ? 'АКТИВЕН' : 'ВЫКЛ');
      } else {
        parts.push('НОВЫЙ СОТРУДНИК');
      }
      const flags = currentEmployeeIncentiveFlags();
      const incentiveLabels = EMPLOYEE_INCENTIVE_DEFINITIONS
        .filter((item) => Boolean(flags[item.kind]))
        .map((item) => item.shortLabel);
      parts.push(incentiveLabels.length ? incentiveLabels.join(' + ') : employeeSalaryModeLabel(mode));
      if (currentTerm?.effective_from) {
        parts.push('УСЛОВИЯ С ' + formatDateTime(currentTerm.effective_from));
      }
      if (employeeFormHasUnsavedChanges()) parts.push('ИЗМЕНЕНО');
      els.employeesMeta.textContent = parts.join(' · ');
    }

    function syncEmployeesReportPanelUi() {
      const detailsOpen = Boolean(state.employeesReportDetailsOpen && String(state.activeEmployeeId || '').trim());
      if (els.employeesReportShell) {
        els.employeesReportShell.dataset.detailsOpen = detailsOpen ? 'true' : 'false';
      }
      els.employeesDetailsPanel?.classList.toggle('is-collapsed', !detailsOpen);
      if (els.employeesDetailsPanel) {
        els.employeesDetailsPanel.dataset.reportState = detailsOpen ? 'open' : 'collapsed';
      }
    }

    function syncEmployeeSalaryModeUi() {
      syncEmployeeSalaryModeFromIncentives();
      renderEmployeeIncentives();
      renderEmployeeProfileMeta();
    }

    function fillEmployeeForm(employee) {
      const current = employee ? employeeCurrentPayrollTerm(employee) : null;
      if (els.employeesCardMode) {
        els.employeesCardMode.textContent = current ? String(current.name || 'СОТРУДНИК').toUpperCase() : 'НОВЫЙ СОТРУДНИК';
      }
      els.employeeNameInput.value = current?.name || '';
      if (els.employeeMiddleNameInput) els.employeeMiddleNameInput.value = '';
      els.employeePositionInput.value = current?.position || '';
      els.employeeSalaryModeInput.value = current?.salary_mode || 'percent_only';
      els.employeeBaseSalaryInput.value = current?.base_salary || '';
      els.employeeWorkPercentInput.value = current?.work_percent || '';
      els.employeeMaterialPercentInput.value = current?.material_percent || '';
      els.employeeRepairOrderPercentInput.value = current?.repair_order_percent || '';
      if (els.employeeDeleteButton) {
        els.employeeDeleteButton.disabled = !current;
      }
      state.employeeFormBaseline = employeeComparableSnapshot(current);
      syncEmployeeSalaryModeUi();
      syncEmployeeEditMutationControls();
    }

    function readEmployeeFormPayload() {
      const selectedEmployee = selectedEmployeeRecord();
      const payload = {
        create_mode: Boolean(state.employeeCreateMode),
        employee_id: state.employeeCreateMode ? '' : (state.activeEmployeeId || ''),
        name: employeeCombinedNameFromForm(),
        position: els.employeePositionInput.value,
        salary_mode: els.employeeSalaryModeInput.value,
        base_salary: els.employeeBaseSalaryInput.value,
        work_percent: els.employeeWorkPercentInput.value,
        material_percent: els.employeeMaterialPercentInput.value,
        repair_order_percent: els.employeeRepairOrderPercentInput.value,
        is_active: selectedEmployee ? Boolean(selectedEmployee.is_active) : true,
        actor_name: state.actor,
        source: 'ui',
      };
      return payload;
    }

    function renderEmployeesList() {
      const employees = Array.isArray(state.employees) ? state.employees : [];
      const visibleEmployees = filteredEmployeesList();
      const readOnly = operatorHasEmployeesReadOnlyAccess();
      const summaryMap = payrollSummaryMap();
      if (!els.employeesList) return;
      if (!employees.length) {
        els.employeesList.innerHTML = '<div class="cashboxes-empty">Нет сотрудников.</div>';
        return;
      }
      if (!visibleEmployees.length) {
        els.employeesList.innerHTML = '<div class="cashboxes-empty">Ничего не найдено.</div>';
        return;
      }
      els.employeesList.innerHTML = visibleEmployees.map((employee) => {
        const isActive = !state.employeeCreateMode && employee.id === state.activeEmployeeId;
        const summary = summaryMap.get(String(employee.id || ''));
        const summaryLabel = 'К ВЫПЛАТЕ';
        const summaryValue = String(employee.balance_total ?? summary?.balance_total ?? summary?.total_salary ?? '0');
        const rowLabel = readOnly
          ? [employee.name || 'Сотрудник', employee.position || 'Без должности'].join(' · ')
          : employeeRowAriaLabel(employee, summaryValue);
        const readOnlyContent = '<div class="employees-row__formula">ТОЛЬКО ПРОСМОТР</div>';
        const fullAccessContent = '<div class="employees-row__formula">' + escapeHtml(employeePayrollFormulaLabel(employee)) + '</div>'
          + '<div class="employees-row__summary"><span class="employees-row__summary-label">' + escapeHtml(summaryLabel) + '</span><strong>' + escapeHtml(summaryValue) + '</strong></div>';
        const actions = readOnly
          ? ''
          : '<div class="employees-row__actions">'
            + '<button class="btn btn--ghost employees-row__salary" type="button" data-employee-salary="' + escapeHtml(employee.id) + '">ЗАРПЛАТА</button>'
            + '<button class="btn btn--ghost employees-row__report" type="button" data-employee-report="' + escapeHtml(employee.id) + '" title="ВЫБРАТЬ ПЕРИОД И ОТКРЫТЬ ПЕЧАТНЫЙ АКТ СВЕРКИ ЗАРПЛАТЫ">ОТЧЕТ</button>'
          + '</div>';
        return '<div class="employees-row' + (isActive ? ' is-active' : '') + '">'
          + '<button class="employees-row__body" type="button" data-employee-id="' + escapeHtml(employee.id) + '" aria-label="Сотрудник ' + escapeHtml(rowLabel) + '" title="' + escapeHtml(rowLabel) + '">'
            + '<div class="employees-row__top"><div class="employees-row__title">' + escapeHtml(employee.name) + '</div></div>'
            + '<div class="employees-row__meta">' + escapeHtml(employee.position || 'Без должности') + '</div>'
            + (readOnly ? readOnlyContent : fullAccessContent)
          + '</button>'
          + actions
          + '</div>';
      }).join('');
    }

    function renderEmployeesDetails() {
      const selectedId = state.employeesReportDetailsOpen ? String(state.activeEmployeeId || '').trim() : '';
      const selectedEmployee = selectedId ? selectedEmployeeRecord() : null;
      const rows = Array.isArray(state.payrollReport?.detail_rows) ? state.payrollReport.detail_rows : [];
      const visibleRows = selectedId ? rows.filter((item) => String(item.employee_id || '').trim() === selectedId) : [];
      if (els.employeesReportMeta) {
        els.employeesReportMeta.textContent = selectedEmployee
          ? ('Детализация: ' + String(selectedEmployee.name || 'СОТРУДНИК').toUpperCase())
          : 'Выберите сотрудника слева, чтобы открыть детализацию.';
      }
      if (els.employeesDetailsMeta) {
        els.employeesDetailsMeta.textContent = selectedEmployee
          ? ('Выбран ' + String(selectedEmployee.name || 'сотрудник') + ' · ' + employeeIncentiveSummaryLabel(selectedEmployee))
          : 'Детализация появится после выбора сотрудника.';
      }
      if (!selectedId) {
        els.employeesDetailTable.innerHTML = '<tr><td colspan="9">Выберите сотрудника слева, чтобы увидеть его наряды.</td></tr>';
        return;
      }
      if (!visibleRows.length) {
        els.employeesDetailTable.innerHTML = '<tr><td colspan="9">Строк начисления нет.</td></tr>';
        return;
      }
      els.employeesDetailTable.innerHTML = visibleRows.map((row) => {
        const rowType = String(row.row_type || '').trim();
        const isMaterial = rowType === 'material';
        const isBaseSalary = rowType === 'base_salary';
        const isShiftAccrual = rowType === 'shift_accrual';
        const isRepairOrderAccrual = rowType === 'repair_order_accrual' || rowType === 'repair_order_accrual_reversal';
        const positionName = isRepairOrderAccrual ? (row.material_name || '% от стоимости ЗН за наличный расчёт') : ((isBaseSalary || isShiftAccrual) ? (row.material_name || (isBaseSalary ? 'Недельный оклад' : 'Выплата за смены за текущую неделю')) : (isMaterial ? (row.material_name || '-') : ((row.works_count || '0') + ' раб.')));
        const saleTotal = isRepairOrderAccrual ? (row.base_amount || row.work_total || '0') : ((isBaseSalary || isShiftAccrual) ? '-' : (isMaterial ? (row.material_total || '0') : (row.work_total || '0')));
        const costTotal = (isBaseSalary || isShiftAccrual || isRepairOrderAccrual) ? '-' : (isMaterial ? (row.material_cost_total || '0') : '-');
        const profitTotal = (isBaseSalary || isShiftAccrual || isRepairOrderAccrual) ? '-' : (isMaterial ? (row.material_profit || '0') : '-');
        return '<tr data-card-id="' + escapeHtml(row.card_id || '') + '" data-open-repair-order="' + (row.repair_order_number ? '1' : '') + '">' +
          '<td>' + escapeHtml(row.closed_at || '-') + '</td>' +
          '<td>' + escapeHtml(row.repair_order_number || '-') + '</td>' +
          '<td>' + escapeHtml(row.vehicle || '-') + '</td>' +
          '<td>' + escapeHtml(row.type_label || (isMaterial ? 'Материал' : 'Работа')) + '</td>' +
          '<td>' + escapeHtml(positionName) + '</td>' +
          '<td class="is-num">' + escapeHtml(saleTotal) + '</td>' +
          '<td class="is-num">' + escapeHtml(costTotal) + '</td>' +
          '<td class="is-num">' + escapeHtml(profitTotal) + '</td>' +
          '<td class="is-num">' + escapeHtml(row.salary_amount || '0') + '</td>' +
        '</tr>';
      }).join('');
    }

    function selectedEmployeeSalaryRecord() {
      return (Array.isArray(state.employees) ? state.employees : []).find((item) => item.id === state.activeEmployeeSalaryId) || null;
    }

    function employeeSalaryActionLabel(kind) {
      return String(kind || '') === 'salary_advance' ? 'АВАНС' : 'ВЫПЛАТА ЗАРПЛАТЫ';
    }

    function preferredEmployeeSalaryCashboxId() {
      const items = Array.isArray(state.cashboxes) ? state.cashboxes : [];
      const current = String(state.employeeSalaryCashboxId || state.activeCashboxId || '').trim();
      if (current && items.some((item) => String(item?.id || '').trim() === current)) return current;
      const cashMatch = items.find((item) => {
        const name = String(item?.name || '').trim().toLowerCase();
        return name === 'наличный' || name.includes('налич') || name.includes('cash');
      });
      return String((cashMatch || items[0] || {})?.id || '').trim();
    }

    function renderEmployeeSalaryCashboxOptions(selectEl = els.employeeSalaryCashboxSelect) {
      if (!(selectEl instanceof HTMLElement)) return;
      const items = (Array.isArray(state.cashboxes) ? state.cashboxes : []).slice().sort((left, right) => {
        const orderDiff = finiteNumber(left?.order) - finiteNumber(right?.order);
        if (orderDiff) return orderDiff;
        return String(left?.name || '').localeCompare(String(right?.name || ''), 'ru', { sensitivity: 'base' });
      });
      const selectedId = preferredEmployeeSalaryCashboxId();
      selectEl.innerHTML = ['<option value="">ВЫБЕРИ КАССУ</option>'].concat(items.map((item) => {
        const itemId = String(item?.id || '').trim();
        const selected = itemId && itemId === selectedId ? ' selected' : '';
        return '<option value="' + escapeHtml(itemId) + '"' + selected + '>' + escapeHtml(item?.name || 'Касса') + '</option>';
      })).join('');
      if (selectedId) {
        selectEl.value = selectedId;
        state.employeeSalaryCashboxId = selectedId;
      }
    }

    async function ensureEmployeeSalaryCashboxes(isCurrent = employeeAsyncContext('employeeSalaryCashboxesRequest')) {
      if (state.cashboxesLoaded && Array.isArray(state.cashboxes) && state.cashboxes.length) {
        renderEmployeeSalaryCashboxOptions();
        return;
      }
      const data = await api('/api/list_cashboxes?limit=200');
      if (!isCurrent()) return;
      state.cashboxes = Array.isArray(data?.cashboxes) ? data.cashboxes : [];
      state.cashboxesLoaded = true;
      state.cashboxesReferencesOnly = Boolean(data?.meta?.references_only);
      renderEmployeeSalaryCashboxOptions();
    }

    function renderEmployeeSalaryActionDialog() {
      if (!els.employeeSalaryActionDialog || !els.employeeSalaryActionTitle || !els.employeeSalaryActionConfirmButton) return;
      const isOpen = String(state.employeeSalaryActionKind || '').trim() === 'salary_payout';
      els.employeeSalaryActionDialog.hidden = !isOpen;
      if (!isOpen) return;
      els.employeeSalaryActionTitle.textContent = employeeSalaryActionLabel('salary_payout');
      els.employeeSalaryActionConfirmButton.textContent = 'ВЫПЛАТИТЬ';
      if (els.employeeSalaryAmountInput && !String(els.employeeSalaryAmountInput.value || '').trim()) {
        els.employeeSalaryAmountInput.value = state.employeeSalaryActionDraft || '';
      }
      renderEmployeeSalaryCashboxOptions(els.employeeSalaryCashboxSelect);
    }

    function renderEmployeeSalaryAdvanceDialog() {
      if (!els.employeeSalaryAdvanceDialog || !els.employeeSalaryAdvanceTitle || !els.employeeSalaryAdvanceConfirmButton) return;
      const isOpen = Boolean(state.employeeSalaryAdvanceOpen);
      els.employeeSalaryAdvanceDialog.hidden = !isOpen;
      if (!isOpen) return;
      els.employeeSalaryAdvanceTitle.textContent = 'АВАНС';
      els.employeeSalaryAdvanceConfirmButton.textContent = 'ВЫДАТЬ АВАНС';
      if (els.employeeSalaryAdvanceAmountInput && !String(els.employeeSalaryAdvanceAmountInput.value || '').trim()) {
        els.employeeSalaryAdvanceAmountInput.value = state.employeeSalaryAdvanceDraft || '';
      }
      if (els.employeeSalaryAdvanceCommentInput && !String(els.employeeSalaryAdvanceCommentInput.value || '').trim()) {
        els.employeeSalaryAdvanceCommentInput.value = state.employeeSalaryAdvanceNoteDraft || '';
      }
      renderEmployeeSalaryCashboxOptions(els.employeeSalaryAdvanceCashboxSelect);
    }

    function renderEmployeeSalaryModal() {
      const employee = selectedEmployeeSalaryRecord();
      const sheet = state.employeeSalarySheet;
      if (!els.employeeSalaryModal) return;
      if (els.employeeSalaryTitle) {
        els.employeeSalaryTitle.textContent = employee ? String(employee.name || 'СОТРУДНИК').toUpperCase() : 'СОТРУДНИК';
      }
      if (els.employeeSalaryBalance) {
        els.employeeSalaryBalance.textContent = String(sheet?.balance_display || sheet?.balance_total || '0');
      }
      if (els.employeeSalaryResetButton) {
        const canResetBalance = operatorCanResetSalaryBalance();
        const balanceMinor = Number(sheet?.balance_minor);
        els.employeeSalaryResetButton.classList.toggle('hidden', !canResetBalance);
        els.employeeSalaryResetButton.disabled = Boolean(
          state.employeeSalaryResetPending
          || !sheet
          || !Number.isSafeInteger(balanceMinor)
          || balanceMinor === 0
        );
        els.employeeSalaryResetButton.textContent = state.employeeSalaryResetPending
          ? 'ОБНУЛЕНИЕ...'
          : 'ОБНУЛИТЬ БАЛАНС';
      }
      if (els.employeeSalaryJournalMeta) {
        const periods = finiteNonNegativeNumber(sheet?.period_months, 6);
        const rows = finiteNonNegativeNumber(sheet?.journal_total);
        els.employeeSalaryJournalMeta.textContent = 'ПЕРИОД ' + periods + ' МЕС. · СТРОК ' + rows;
      }
      if (els.employeeSalarySummary) {
        const summaryItems = [
          { label: 'НАЧИСЛЕНО', value: sheet?.accrued_total_display || sheet?.accrued_total || '0' },
          { label: 'ВЫПЛАЧЕНО', value: sheet?.payout_total_display || '0' },
          { label: 'АВАНС', value: sheet?.advance_total_display || '0' },
          { label: 'БАЛАНС', value: sheet?.balance_display || sheet?.balance_total || '0', accent: true },
        ];
        els.employeeSalarySummary.innerHTML = summaryItems.map((item) => {
          const accentClass = item.accent ? ' employees-kpi--accent' : '';
          return '<div class="employees-kpi' + accentClass + '"><div class="employees-kpi__label">' + escapeHtml(item.label) + '</div><div class="employees-kpi__value">' + escapeHtml(item.value) + '</div></div>';
        }).join('');
      }
      if (els.employeeSalaryJournalTable) {
        const rows = Array.isArray(sheet?.journal_rows) ? sheet.journal_rows : [];
        if (!rows.length) {
          els.employeeSalaryJournalTable.innerHTML = '<tr><td colspan="7">За выбранный период движений нет.</td></tr>';
        } else {
          els.employeeSalaryJournalTable.innerHTML = rows.map((row) => {
            return '<tr>'
              + '<td>' + escapeHtml(row.created_at || '-') + '</td>'
              + '<td>' + escapeHtml(row.kind_label || '-') + '</td>'
              + '<td>' + escapeHtml(row.repair_order_number || row.source_label || '-') + '</td>'
              + '<td>' + escapeHtml(row.vehicle || '-') + '</td>'
              + '<td>' + escapeHtml(row.work_name || '-') + '</td>'
              + '<td>' + escapeHtml(row.note || '-') + '</td>'
              + '<td class="is-num">' + escapeHtml(row.amount_display || '0') + '</td>'
              + '</tr>';
          }).join('');
        }
      }
      renderEmployeeSalaryActionDialog();
      renderEmployeeSalaryAdvanceDialog();
      syncEmployeeMoneyMutationControls();
    }

    async function loadEmployeeSalarySheet(employeeId, { openModal = false } = {}) {
      const requestedId = String(employeeId || '').trim();
      if (!requestedId) return null;
      if (String(state.activeEmployeeSalaryId || '').trim() !== requestedId) {
        state.employeeSalaryViewGeneration = (state.employeeSalaryViewGeneration || 0) + 1;
        state.employeeSalaryResetPending = false;
        closeEmployeeSalaryDialog();
      }
      state.activeEmployeeSalaryId = requestedId;
      const isCurrent = employeeAsyncContext('employeeSalarySheetRequest', 'activeEmployeeSalaryId');
      try {
        const data = await api('/api/get_employee_salary_ledger?employee_id=' + encodeURIComponent(requestedId) + '&months=6');
        if (!isCurrent()) return null;
        state.employeeSalarySheet = data || null;
        renderEmployeeSalaryModal();
        maybeOpenModal(els.employeeSalaryModal, openModal);
        return data;
      } catch (error) {
        if (isCurrent()) throw error;
        return null;
      }
    }

    async function openEmployeeSalaryDialog(kind) {
      if (String(kind || '').trim() === 'salary_advance') {
        await openEmployeeSalaryAdvanceDialog();
        return;
      }
      state.employeeSalaryViewGeneration = (state.employeeSalaryViewGeneration || 0) + 1;
      state.employeeSalaryResetPending = false;
      state.employeeSalaryAdvanceOpen = false;
      state.employeeSalaryAdvanceDraft = '';
      state.employeeSalaryAdvanceNoteDraft = '';
      if (els.employeeSalaryAdvanceAmountInput) els.employeeSalaryAdvanceAmountInput.value = '';
      if (els.employeeSalaryAdvanceCommentInput) els.employeeSalaryAdvanceCommentInput.value = '';
      state.employeeSalaryActionKind = String(kind || '').trim();
      state.employeeSalaryActionDraft = '';
      if (els.employeeSalaryAmountInput) els.employeeSalaryAmountInput.value = '';
      const isCurrent = employeeAsyncContext('employeeSalaryDialogRequest', 'activeEmployeeSalaryId');
      syncEmployeeMoneyMutationControls();
      try {
        await ensureEmployeeSalaryCashboxes(isCurrent);
      } catch (error) {
        if (isCurrent()) setStatus(error.message, true);
      }
      if (!isCurrent()) return;
      renderEmployeeSalaryModal();
      if (els.employeeSalaryAmountInput) setTimeout(() => { if (isCurrent()) els.employeeSalaryAmountInput.focus(); }, 0);
    }

    async function openEmployeeSalaryAdvanceDialog() {
      state.employeeSalaryViewGeneration = (state.employeeSalaryViewGeneration || 0) + 1;
      state.employeeSalaryResetPending = false;
      state.employeeSalaryActionKind = '';
      state.employeeSalaryActionDraft = '';
      if (els.employeeSalaryAmountInput) els.employeeSalaryAmountInput.value = '';
      state.employeeSalaryAdvanceOpen = true;
      state.employeeSalaryAdvanceDraft = '';
      state.employeeSalaryAdvanceNoteDraft = '';
      if (els.employeeSalaryAdvanceAmountInput) els.employeeSalaryAdvanceAmountInput.value = '';
      if (els.employeeSalaryAdvanceCommentInput) els.employeeSalaryAdvanceCommentInput.value = '';
      const isCurrent = employeeAsyncContext('employeeSalaryDialogRequest', 'activeEmployeeSalaryId');
      syncEmployeeMoneyMutationControls();
      try {
        await ensureEmployeeSalaryCashboxes(isCurrent);
      } catch (error) {
        if (isCurrent()) setStatus(error.message, true);
      }
      if (!isCurrent()) return;
      renderEmployeeSalaryModal();
      if (els.employeeSalaryAdvanceAmountInput) {
        setTimeout(() => { if (isCurrent()) els.employeeSalaryAdvanceAmountInput.focus(); }, 0);
      }
    }

    function closeEmployeeSalaryDialog() {
      state.employeeSalaryDialogRequest = null;
      state.employeeSalaryActionKind = '';
      state.employeeSalaryActionDraft = '';
      if (els.employeeSalaryAmountInput) els.employeeSalaryAmountInput.value = '';
      state.employeeSalaryAdvanceOpen = false;
      state.employeeSalaryAdvanceDraft = '';
      state.employeeSalaryAdvanceNoteDraft = '';
      if (els.employeeSalaryAdvanceAmountInput) els.employeeSalaryAdvanceAmountInput.value = '';
      if (els.employeeSalaryAdvanceCommentInput) els.employeeSalaryAdvanceCommentInput.value = '';
      renderEmployeeSalaryModal();
    }

    function closeEmployeeSalaryAdvanceDialog() {
      state.employeeSalaryDialogRequest = null;
      state.employeeSalaryAdvanceOpen = false;
      state.employeeSalaryAdvanceDraft = '';
      state.employeeSalaryAdvanceNoteDraft = '';
      if (els.employeeSalaryAdvanceAmountInput) els.employeeSalaryAdvanceAmountInput.value = '';
      if (els.employeeSalaryAdvanceCommentInput) els.employeeSalaryAdvanceCommentInput.value = '';
      renderEmployeeSalaryModal();
    }

    function closeEmployeeSalaryModal() {
      state.employeeSalaryViewGeneration = (state.employeeSalaryViewGeneration || 0) + 1;
      state.employeeSalaryResetPending = false;
      popModal('employeeSalary');
      state.activeEmployeeSalaryId = '';
      state.employeeSalarySheet = null;
      closeEmployeeSalaryDialog();
    }

    function selectedEmployeeSalaryReconciliationReportRecord() {
      return (Array.isArray(state.employees) ? state.employees : []).find((item) => item.id === state.activeEmployeeSalaryReconciliationReportId) || null;
    }

    function renderEmployeeSalaryReconciliationPeriodDialog() {
      const employee = selectedEmployeeSalaryReconciliationReportRecord();
      if (els.employeeSalaryReconciliationPeriodTitle) {
        els.employeeSalaryReconciliationPeriodTitle.textContent = employee ? String(employee.name || 'СОТРУДНИК').toUpperCase() : 'СОТРУДНИК';
      }
      syncEmployeeSalaryReconciliationPeriodUi();
    }

    function openEmployeeSalaryReconciliationPeriodDialog(employeeId) {
      if (!requireEmployeesCashboxesAccess()) return;
      const requestedId = String(employeeId || '').trim();
      if (!requestedId) return;
      state.activeEmployeeSalaryReconciliationReportId = requestedId;
      renderEmployeeSalaryReconciliationPeriodDialog();
      pushModal('employee-salary-reconciliation-period', els.employeeSalaryReconciliationPeriodModal);
      const focusTarget = state.employeeSalaryReconciliationPeriodMode === 'dates'
        ? els.employeeSalaryReconciliationDateFromInput
        : els.employeeSalaryReconciliationDaysInput;
      if (focusTarget instanceof HTMLElement) {
        window.setTimeout(() => focusTarget.focus({ preventScroll: true }), 0);
      }
    }

    function closeEmployeeSalaryReconciliationPeriodDialog() {
      popModal('employee-salary-reconciliation-period');
      state.activeEmployeeSalaryReconciliationReportId = '';
      renderEmployeeSalaryReconciliationPeriodDialog();
    }

    async function openSelectedEmployeeSalaryReconciliationPrint(event) {
      if (event?.preventDefault) event.preventDefault();
      const requestedId = String(state.activeEmployeeSalaryReconciliationReportId || '').trim();
      if (!requestedId) {
        setStatus('СНАЧАЛА ВЫБЕРИТЕ СОТРУДНИКА.', true);
        return;
      }
      const opened = await openEmployeeSalaryReport(requestedId);
      if (opened) closeEmployeeSalaryReconciliationPeriodDialog();
    }

    async function loadEmployeeSalaryReconciliation(employeeId) {
      const requestedId = String(employeeId || '').trim();
      if (!requestedId) return null;
      const path = employeeSalaryReconciliationApiPath(requestedId, { strict: true });
      if (!path) return null;
      return await api(path);
    }

    function employeeSalaryReconciliationText(value, fallback = '-') {
      const text = String(value ?? '').trim();
      return text ? text : fallback;
    }

    function employeeSalaryReconciliationVehicleHtml(row) {
      const vehicle = employeeSalaryReconciliationText(row?.vehicle, '');
      const plate = employeeSalaryReconciliationText(row?.license_plate, '');
      if (vehicle && plate) {
        return escapeHtml(vehicle) + '<br><span class="muted">госномер: ' + escapeHtml(plate) + '</span>';
      }
      return escapeHtml(vehicle || plate || '-');
    }

    function employeeSalaryReconciliationEmptyText(report) {
      const label = employeeSalaryReconciliationText(report?.period?.label, '');
      return label ? ('За период ' + label + ' движений нет.') : 'За выбранный период движений нет.';
    }

    function employeeSalaryReconciliationRowsHtml(report) {
      const rows = Array.isArray(report?.rows) ? report.rows : [];
      if (!rows.length) {
        return '<tr><td colspan="11" class="empty">' + escapeHtml(employeeSalaryReconciliationEmptyText(report)) + '</td></tr>';
      }
      return rows.map((row) => {
        return '<tr>'
          + '<td class="is-num">' + escapeHtml(row.number || '') + '</td>'
          + '<td>' + escapeHtml(employeeSalaryReconciliationText(row.date)) + '</td>'
          + '<td>' + escapeHtml(employeeSalaryReconciliationText(row.kind_label)) + '</td>'
          + '<td>' + escapeHtml(employeeSalaryReconciliationText(row.repair_order_number)) + '</td>'
          + '<td>' + employeeSalaryReconciliationVehicleHtml(row) + '</td>'
          + '<td>' + escapeHtml(employeeSalaryReconciliationText(row.item)) + '</td>'
          + '<td>' + escapeHtml(employeeSalaryReconciliationText(row.calculation_base)) + '</td>'
          + '<td>' + escapeHtml(employeeSalaryReconciliationText(row.scheme)) + '</td>'
          + '<td class="money">' + escapeHtml(employeeSalaryReconciliationText(row.accrued_display, '')) + '</td>'
          + '<td class="money">' + escapeHtml(employeeSalaryReconciliationText(row.payment_display, '')) + '</td>'
          + '<td>' + escapeHtml(employeeSalaryReconciliationText(row.note, '')) + '</td>'
          + '</tr>';
      }).join('');
    }

    function employeeSalaryReconciliationTotalsHtml(report) {
      const totals = report?.totals || {};
      const items = [
        ['Всего начислено', totals.accrued_total_display || totals.accrued_total || '0'],
        ['Выплачено', totals.payout_total_display || totals.payout_total || '0'],
        ['Авансы', totals.advance_total_display || totals.advance_total || '0'],
        ['Корректировка баланса', totals.adjustment_total_display || totals.adjustment_total || '0'],
        ['Итог к выплате', totals.amount_due_total_display || totals.amount_due_total || '0'],
      ];
      return items.map((item) => {
        return '<div class="summary-item"><span>' + escapeHtml(item[0]) + '</span><strong>' + escapeHtml(item[1]) + '</strong></div>';
      }).join('');
    }

    function employeeSalaryReconciliationPrintDate(value) {
      const raw = employeeSalaryReconciliationText(value, '');
      if (!raw) return '';
      try {
        const date = value instanceof Date ? value : new Date(raw);
        if (Number.isNaN(date.getTime())) return raw;
        const dd = String(date.getDate()).padStart(2, '0');
        const mm = String(date.getMonth() + 1).padStart(2, '0');
        return dd + '.' + mm + '.' + date.getFullYear();
      } catch {
        return raw;
      }
    }

    function buildEmployeeSalaryReconciliationPrintHtml(report) {
      const employee = report?.employee || {};
      const period = report?.period || {};
      const title = 'Акт сверки зарплаты';
      const generatedAt = employeeSalaryReconciliationPrintDate(period.generated_at);
      return '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        + '<title>' + escapeHtml(title) + '</title>'
        + '<style>'
        + '@page { size: A4 landscape; margin: 12mm; }'
        + 'body { margin: 0; color: #111; background: #fff; font: 12px/1.35 "Segoe UI", Arial, sans-serif; }'
        + '.toolbar { position: sticky; top: 0; display: flex; justify-content: flex-end; gap: 8px; padding: 10px 0; background: #fff; border-bottom: 1px solid #ddd; margin-bottom: 18px; }'
        + '.print-button { border: 1px solid #111; background: #111; color: #fff; padding: 8px 14px; cursor: pointer; font-weight: 700; letter-spacing: .04em; }'
        + 'h1 { margin: 0 0 10px; font-size: 22px; line-height: 1.15; }'
        + '.meta { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px 18px; margin-bottom: 14px; }'
        + '.meta div, .summary-item { border: 1px solid #d4d4d4; padding: 7px 8px; }'
        + '.meta span, .summary-item span { display: block; color: #555; font-size: 10px; text-transform: uppercase; }'
        + '.meta strong, .summary-item strong { display: block; margin-top: 2px; font-size: 13px; }'
        + '.summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin: 10px 0 16px; }'
        + 'table { width: 100%; border-collapse: collapse; table-layout: fixed; }'
        + 'th, td { border: 1px solid #c9c9c9; padding: 5px 6px; vertical-align: top; word-break: break-word; }'
        + 'th { background: #efefef; text-align: left; font-size: 10px; text-transform: uppercase; }'
        + '.is-num, .money { text-align: right; white-space: nowrap; }'
        + '.muted { color: #555; }'
        + '.empty { text-align: center; padding: 18px; color: #555; }'
        + '.signatures { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 24px; margin-top: 28px; }'
        + '.signature { border-top: 1px solid #111; padding-top: 6px; min-height: 34px; }'
        + '@media print { .toolbar { display: none; } body { font-size: 11px; } th, td { padding: 4px 5px; } }'
        + '</style></head><body>'
        + '<div class="toolbar"><button class="print-button" type="button" onclick="window.print()">ПЕЧАТЬ</button></div>'
        + '<main>'
        + '<h1>' + escapeHtml(title) + '</h1>'
        + '<section class="meta">'
        + '<div><span>Сотрудник</span><strong>' + escapeHtml(employeeSalaryReconciliationText(employee.name, 'Сотрудник')) + '</strong></div>'
        + '<div><span>Должность</span><strong>' + escapeHtml(employeeSalaryReconciliationText(employee.position, 'Не указана')) + '</strong></div>'
        + '<div><span>Период</span><strong>' + escapeHtml(employeeSalaryReconciliationText(period.label, 'Последние 30 дней')) + '</strong></div>'
        + '</section>'
        + '<section class="summary">' + employeeSalaryReconciliationTotalsHtml(report) + '</section>'
        + '<table><thead><tr>'
        + '<th style="width:34px;">№</th><th style="width:84px;">Дата</th><th style="width:76px;">Движение</th><th style="width:58px;">ЗН</th>'
        + '<th style="width:130px;">Авто / госномер</th><th>Работа / позиция</th><th style="width:120px;">База расчета</th>'
        + '<th style="width:105px;">Схема</th><th style="width:92px;">Начислено</th><th style="width:98px;">Выплата / аванс</th><th>Примечание</th>'
        + '</tr></thead><tbody>' + employeeSalaryReconciliationRowsHtml(report) + '</tbody></table>'
        + '<section class="signatures">'
        + '<div class="signature">Бухгалтер</div>'
        + '<div class="signature">Сотрудник</div>'
        + '<div class="signature">Дата' + (generatedAt ? ': ' + escapeHtml(generatedAt) : '') + '</div>'
        + '</section>'
        + '</main></body></html>';
    }

    function createEmployeeSalaryReconciliationPrintWindow() {
      const printWindow = window.open('', '_blank', 'width=1200,height=800');
      if (!printWindow) {
        setStatus('БРАУЗЕР ЗАБЛОКИРОВАЛ ПЕЧАТНОЕ ОКНО.', true);
        return null;
      }
      printWindow.document.open();
      printWindow.document.write(
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        + '<title>Акт сверки зарплаты</title>'
        + '<style>body{margin:32px;color:#111;background:#fff;font:14px/1.45 "Segoe UI",Arial,sans-serif;}</style>'
        + '</head><body><h1>Загрузка акта сверки зарплаты...</h1></body></html>'
      );
      printWindow.document.close();
      printWindow.focus();
      return printWindow;
    }

    function openEmployeeSalaryReconciliationPrint(report, printWindow = null) {
      const html = buildEmployeeSalaryReconciliationPrintHtml(report || {});
      const targetWindow = printWindow || createEmployeeSalaryReconciliationPrintWindow();
      if (!targetWindow) {
        setStatus('БРАУЗЕР ЗАБЛОКИРОВАЛ ПЕЧАТНОЕ ОКНО.', true);
        return false;
      }
      targetWindow.document.open();
      targetWindow.document.write(html);
      targetWindow.document.close();
      targetWindow.focus();
      return true;
    }

    async function openEmployeeSalaryModal(employeeId) {
      if (!requireEmployeesCashboxesAccess()) return;
      const requestedId = String(employeeId || '').trim();
      if (!requestedId) return;
      if (!confirmDiscardEmployeeChanges()) return;
      try {
        await loadEmployeeSalarySheet(requestedId, { openModal: true });
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function openEmployeeSalaryReport(employeeId) {
      const requestedId = String(employeeId || '').trim();
      if (!requestedId) return false;
      if (!employeeSalaryReconciliationApiPath(requestedId, { strict: true })) return false;
      const printWindow = createEmployeeSalaryReconciliationPrintWindow();
      if (!printWindow) return false;
      try {
        const report = await loadEmployeeSalaryReconciliation(requestedId);
        if (openEmployeeSalaryReconciliationPrint(report, printWindow)) {
          setStatus('АКТ СВЕРКИ ЗАРПЛАТЫ ОТКРЫТ.', false);
          return true;
        }
      } catch (error) {
        try {
          printWindow.close();
        } catch (_) {
        }
        setStatus(error.message, true);
      }
      return false;
    }

    function createEmployeeSalaryResetIdempotencyKey() {
      if (typeof window.crypto?.randomUUID === 'function') {
        return 'salary-balance-reset-' + window.crypto.randomUUID();
      }
      return 'salary-balance-reset-' + Date.now() + '-' + Math.random().toString(16).slice(2);
    }

    function employeeSalaryResetIntent(employeeId, balanceMinor, balanceRevision) {
      const existing = state.employeeSalaryResetIntent;
      if (
        existing
        && existing.employeeId === employeeId
        && existing.balanceMinor === balanceMinor
        && existing.balanceRevision === balanceRevision
      ) return existing;
      const intent = {
        employeeId,
        balanceMinor,
        balanceRevision,
        idempotencyKey: createEmployeeSalaryResetIdempotencyKey(),
      };
      state.employeeSalaryResetIntent = intent;
      return intent;
    }

    async function handleEmployeeSalaryReset() {
      if (state.employeeSalaryResetPending) return;
      if (!operatorCanResetSalaryBalance()) {
        setStatus('НЕТ ПРАВА НА ОБНУЛЕНИЕ ЗАРПЛАТНОГО БАЛАНСА.', true);
        return;
      }
      const employeeId = String(state.activeEmployeeSalaryId || '').trim();
      const employee = selectedEmployeeSalaryRecord();
      const sheet = state.employeeSalarySheet;
      const balanceMinor = Number(sheet?.balance_minor);
      const balanceRevision = String(sheet?.balance_revision || '').trim();
      if (!employeeId || !employee || !Number.isSafeInteger(balanceMinor) || !balanceRevision) {
        setStatus('ОБНОВИТЕ ЗАРПЛАТНЫЙ ЛИСТ И ПОВТОРИТЕ.', true);
        return;
      }
      if (balanceMinor === 0) {
        state.employeeSalaryResetIntent = null;
        setStatus('БАЛАНС УЖЕ РАВЕН НУЛЮ.', false);
        return;
      }

      state.employeeSalaryResetPending = true;
      renderEmployeeSalaryModal();
      const employeeName = String(employee.name || 'СОТРУДНИК');
      const balanceDisplay = String(sheet?.balance_display || sheet?.balance_total || balanceMinor);
      const confirmed = window.confirm(
        'Обнулить зарплатный баланс сотрудника «' + employeeName + '»?\n\n'
        + 'Текущий баланс: ' + balanceDisplay + '.\n'
        + 'Будет записана некассовая корректировка «ОБНУЛЕНИЕ БАЛАНСА». История выплат сохранится.'
      );
      if (!confirmed) {
        state.employeeSalaryResetPending = false;
        renderEmployeeSalaryModal();
        return;
      }

      const intent = employeeSalaryResetIntent(employeeId, balanceMinor, balanceRevision);
      const isCurrent = employeeAsyncContext('employeeSalaryResetOperation', 'activeEmployeeSalaryId');
      try {
        const data = await api('/api/reset_employee_salary_balance', {
          method: 'POST',
          body: {
            employee_id: employeeId,
            expected_balance_minor: balanceMinor,
            expected_balance_revision: balanceRevision,
            idempotency_key: intent.idempotencyKey,
            source: 'ui',
          },
        });
        if (!isCurrent()) return;
        state.employeeSalaryResetIntent = null;
        state.employeeSalarySheet = data?.ledger || null;
        renderEmployeeSalaryModal();
        state.employeesLoadedMonth = '';
        if (!await refreshEmployeePayroll(isCurrent)) return;
        renderEmployeesWorkspace();
        setStatus(data?.meta?.replayed ? 'ОБНУЛЕНИЕ УЖЕ БЫЛО ПРИМЕНЕНО.' : 'БАЛАНС ОБНУЛЁН.', false);
      } catch (error) {
        if (!isCurrent()) return;
        if (
          error?.code === 'salary_balance_reset_conflict'
          || error?.code === 'salary_balance_reset_idempotency_conflict'
        ) {
          state.employeeSalaryResetIntent = null;
          try {
            const sheet = await loadEmployeeSalarySheet(employeeId, { openModal: true });
            if (!isCurrent() || !sheet) return;
            setStatus(
              error?.code === 'salary_balance_reset_conflict'
                ? 'БАЛАНС ИЗМЕНИЛСЯ. ПРОВЕРЬТЕ НОВУЮ СУММУ И ПОДТВЕРДИТЕ ЕЩЁ РАЗ.'
                : 'КЛЮЧ ЗАПРОСА УЖЕ ИСПОЛЬЗОВАН. ПРОВЕРЬТЕ СУММУ И ПОДТВЕРДИТЕ ЕЩЁ РАЗ.',
              true,
            );
          } catch (refreshError) {
            if (isCurrent()) setStatus(refreshError.message, true);
          }
        } else {
          setStatus(error.message, true);
        }
      } finally {
        if (isCurrent.owns()) {
          state.employeeSalaryResetPending = false;
          renderEmployeeSalaryModal();
        }
      }
    }

    async function handleEmployeeSalaryActionConfirm() {
      const employeeId = String(state.activeEmployeeSalaryId || '').trim();
      const kind = String(state.employeeSalaryActionKind || '').trim();
      const amount = String(els.employeeSalaryAmountInput?.value || '').trim();
      if (!employeeId || !kind) {
        setStatus('СНАЧАЛА ВЫБЕРИТЕ СОТРУДНИКА.', true);
        return;
      }
      if (!amount) {
        setStatus('УКАЖИТЕ СУММУ.', true);
        return;
      }
      const cashboxId = String(els.employeeSalaryCashboxSelect?.value || state.employeeSalaryCashboxId || '').trim();
      if (!cashboxId) {
        setStatus('ВЫБЕРИТЕ КАССУ ДЛЯ СПИСАНИЯ.', true);
        els.employeeSalaryCashboxSelect?.focus();
        return;
      }
      const isCurrent = employeeAsyncContext(
        'employeeMoneyMutationOperation',
        'activeEmployeeSalaryId',
        { singleFlight: true },
      );
      if (!isCurrent) return;
      syncEmployeeMoneyMutationControls();
      try {
        if (els.employeeSalaryActionConfirmButton) els.employeeSalaryActionConfirmButton.disabled = true;
        await api('/api/create_employee_salary_transaction', {
          method: 'POST',
          body: {
            employee_id: employeeId,
            transaction_kind: kind,
            amount,
            cashbox_id: cashboxId,
            actor_name: state.actor,
            source: 'ui',
          },
        });
        if (!isCurrent()) return;
        state.employeeSalaryCashboxId = cashboxId;
        state.employeeSalaryActionDraft = '';
        closeEmployeeSalaryDialog();
        const sheet = await loadEmployeeSalarySheet(employeeId, { openModal: true });
        if (!isCurrent() || !sheet) return;
        state.employeesLoadedMonth = '';
        if (!await refreshEmployeePayroll(isCurrent)) return;
        renderEmployeesWorkspace();
        await refreshCashboxesAfterMoneyMutation({ deferDetail: true });
        if (!isCurrent()) return;
        setStatus(kind === 'salary_advance' ? 'АВАНС ВЫДАН.' : 'ЗАРПЛАТА ВЫПЛАЧЕНА.', false);
      } catch (error) {
        if (isCurrent()) setStatus(error.message, true);
      } finally {
        if (isCurrent.release()) syncEmployeeMoneyMutationControls();
      }
    }

    async function handleEmployeeSalaryAdvanceConfirm() {
      const employeeId = String(state.activeEmployeeSalaryId || '').trim();
      const amount = String(els.employeeSalaryAdvanceAmountInput?.value || '').trim();
      const comment = String(els.employeeSalaryAdvanceCommentInput?.value || '').trim();
      if (!employeeId) {
        setStatus('СНАЧАЛА ВЫБЕРИТЕ СОТРУДНИКА.', true);
        return;
      }
      if (!amount) {
        setStatus('УКАЖИТЕ СУММУ.', true);
        return;
      }
      const cashboxId = String(els.employeeSalaryAdvanceCashboxSelect?.value || state.employeeSalaryCashboxId || '').trim();
      if (!cashboxId) {
        setStatus('ВЫБЕРИТЕ КАССУ ДЛЯ СПИСАНИЯ.', true);
        els.employeeSalaryAdvanceCashboxSelect?.focus();
        return;
      }
      const isCurrent = employeeAsyncContext(
        'employeeMoneyMutationOperation',
        'activeEmployeeSalaryId',
        { singleFlight: true },
      );
      if (!isCurrent) return;
      syncEmployeeMoneyMutationControls();
      try {
        if (els.employeeSalaryAdvanceConfirmButton) els.employeeSalaryAdvanceConfirmButton.disabled = true;
        await api('/api/create_employee_salary_transaction', {
          method: 'POST',
          body: {
            employee_id: employeeId,
            transaction_kind: 'salary_advance',
            amount,
            cashbox_id: cashboxId,
            note: comment ? ('Аванс: ' + comment) : '',
            actor_name: state.actor,
            source: 'ui',
          },
        });
        if (!isCurrent()) return;
        state.employeeSalaryCashboxId = cashboxId;
        state.employeeSalaryAdvanceDraft = '';
        state.employeeSalaryAdvanceNoteDraft = '';
        closeEmployeeSalaryAdvanceDialog();
        const sheet = await loadEmployeeSalarySheet(employeeId, { openModal: true });
        if (!isCurrent() || !sheet) return;
        state.employeesLoadedMonth = '';
        if (!await refreshEmployeePayroll(isCurrent)) return;
        renderEmployeesWorkspace();
        await refreshCashboxesAfterMoneyMutation({ deferDetail: true });
        if (!isCurrent()) return;
        setStatus('АВАНС ВЫДАН.', false);
      } catch (error) {
        if (isCurrent()) setStatus(error.message, true);
      } finally {
        if (isCurrent.release()) syncEmployeeMoneyMutationControls();
      }
    }

    async function handleEmployeeShiftAccrualConfirm() {
      const employeeId = String(state.activeEmployeeId || '').trim();
      const amount = String(els.employeeShiftAccrualAmountInput?.value || '').trim();
      if (!employeeId || state.employeeCreateMode) {
        setStatus('СНАЧАЛА ВЫБЕРИТЕ СОТРУДНИКА.', true);
        return;
      }
      if (employeeFormHasUnsavedChanges()) {
        setStatus('СНАЧАЛА СОХРАНИТЕ ИЗМЕНЕНИЯ СОТРУДНИКА.', true);
        return;
      }
      if (!amount) {
        setStatus('УКАЖИТЕ СУММУ.', true);
        els.employeeShiftAccrualAmountInput?.focus();
        return;
      }
      const isCurrent = employeeAsyncContext(
        'employeeMoneyMutationOperation',
        'activeEmployeeId',
        { singleFlight: true },
      );
      if (!isCurrent) return;
      syncEmployeeMoneyMutationControls();
      try {
        if (els.employeeShiftAccrualConfirmButton) els.employeeShiftAccrualConfirmButton.disabled = true;
        await api('/api/create_employee_shift_accrual', {
          method: 'POST',
          body: {
            employee_id: employeeId,
            amount,
            note: 'Выплата за смены за текущую неделю',
            actor_name: state.actor,
            source: 'ui',
          },
        });
        if (!isCurrent()) return;
        closeEmployeeShiftAccrualDialog();
        state.employeesLoadedMonth = '';
        state.employeesReferencePromise = null;
        const employees = await loadEmployeesReference({ month: isCurrent.month, apply: false });
        if (!isCurrent()) return;
        applyEmployeesReferenceData(employees, isCurrent.month);
        renderEmployeesWorkspace();
        try {
          const report = await loadPayrollReport({ month: isCurrent.month, apply: false });
          if (!isCurrent()) return;
          state.payrollReport = report;
          state.payrollReportMonth = isCurrent.month;
          renderEmployeesWorkspace();
        } catch (reportError) {
          if (!isCurrent()) return;
          setStatus(reportError.message, true);
        }
        if (String(state.activeEmployeeSalaryId || '') === employeeId) {
          const sheet = await loadEmployeeSalarySheet(employeeId, { openModal: true });
          if (!isCurrent() || !sheet) return;
        }
        setStatus('ВЫПЛАТА ЗА СМЕНЫ НАЧИСЛЕНА.', false);
      } catch (error) {
        if (isCurrent()) setStatus(error.message, true);
      } finally {
        if (isCurrent.release()) syncEmployeeMoneyMutationControls();
      }
    }

    function renderEmployeesWorkspace() {
      syncEmployeesReadOnlyWorkspaceUi();
      if (operatorHasEmployeesReadOnlyAccess()) {
        state.employeeCreateMode = false;
        state.employeesReportDetailsOpen = false;
        state.employeeFormBaseline = null;
        state.employeeShiftAccrualOpen = false;
        state.employeeShiftAccrualDraft = '';
        renderEmployeesList();
        return;
      }
      const employees = Array.isArray(state.employees) ? state.employees : [];
      if (!state.employeeCreateMode && !state.activeEmployeeId && employees.length) {
        state.activeEmployeeId = employees[0].id;
      }
      if (state.activeEmployeeId && !employees.some((item) => item.id === state.activeEmployeeId)) {
        state.activeEmployeeId = employees[0]?.id || '';
      }
      if (!employees.length) {
        state.activeEmployeeId = '';
        state.employeeCreateMode = true;
      }
      if (els.employeesMonthInput) {
        els.employeesMonthInput.value = state.payrollMonth || currentPayrollMonthValue();
      }
      syncEmployeeSalaryReconciliationPeriodUi();
      fillEmployeeForm(state.employeeCreateMode ? null : selectedEmployeeRecord());
      renderEmployeesList();
      renderEmployeesDetails();
      syncEmployeesReportPanelUi();
      renderEmployeeShiftAccrualDialog();
    }

    // @include employees_reference.js

    function refreshRepairOrderEmployeeSelects() {
      if (!els.repairOrderModal?.classList.contains('is-open')) return;
      renderRepairOrderRows('works', readRepairOrderRows('works'));
    }

    async function loadEmployeesWorkspace(openModal = false, prepared = null) {
      const month = els.employeesMonthInput?.value || state.payrollMonth || currentPayrollMonthValue();
      const loading = prepared || prepareEmployeesWorkspaceData(month);
      const loadResult = await loading.promise;
      if (!loadResult.applied || !loading.isCurrent()) return loadResult;
      renderEmployeesWorkspace();
      refreshRepairOrderEmployeeSelects();
      if (openModal) {
        pushModal('employees', els.employeesModal);
        els.employeesModal.scrollTop = 0;
        const dialog = els.employeesModal.querySelector('.dialog');
        if (dialog instanceof HTMLElement) {
          dialog.scrollTop = 0;
          dialog.scrollLeft = 0;
        }
        const closeButton = els.employeesModal.querySelector('[data-close="employees"]');
        if (closeButton instanceof HTMLElement) {
          closeButton.focus({ preventScroll: true });
        }
      }
      return loadResult;
    }

    async function addEmployeeFromForm() {
      if (!requireEmployeesCashboxesAccess()) return;
      if (state.employeeCreateMode && employeeFormHasUnsavedChanges()) {
        await saveEmployee();
        return;
      }
      if (!confirmDiscardEmployeeChanges()) return;
      state.employeeCreateMode = true;
      state.activeEmployeeId = '';
      state.employeesReportDetailsOpen = false;
      state.employeeShiftAccrualOpen = false;
      state.employeeShiftAccrualDraft = '';
      renderEmployeesWorkspace();
      setStatus('ЗАПОЛНИТЕ НОВОГО СОТРУДНИКА И НАЖМИТЕ ДОБАВИТЬ.', false);
      if (els.employeeNameInput) {
        setTimeout(() => els.employeeNameInput.focus(), 0);
      }
    }

    function openEmployeesModal(prepared = null) {
      if (prepared?.promise && !prepared.isCurrent()) return;
      if (!requireEmployeesViewAccess()) return;
      ensureEmployeesUi();
      hydrateEmployeesUiRefs();
      bindEmployeesUiEvents();
      state.employeesReportDetailsOpen = false;
      if (els.employeesMonthInput && !els.employeesMonthInput.value) {
        els.employeesMonthInput.value = state.payrollMonth || currentPayrollMonthValue();
      }
      if (document.activeElement instanceof HTMLElement) {
        document.activeElement.blur();
      }
      els.employeesModal.scrollTop = 0;
      const dialog = els.employeesModal.querySelector('.dialog');
      if (dialog instanceof HTMLElement) {
        dialog.scrollTop = 0;
        dialog.scrollLeft = 0;
      }
      return loadEmployeesWorkspace(true, prepared?.promise ? prepared : null)
        .catch((error) => setStatus(error.message, true));
    }

    async function saveEmployee() {
      if (!requireEmployeesCashboxesAccess()) return;
      const employeeName = employeeCombinedNameFromForm();
      if (!employeeName) {
        if (els.employeeNameInput) els.employeeNameInput.focus();
        setStatus('УКАЖИ ИМЯ СОТРУДНИКА.', true);
        return;
      }
      const isCurrent = employeeAsyncContext(
        'employeeEditOperation',
        'activeEmployeeId',
        { singleFlight: true },
      );
      if (!isCurrent) return;
      syncEmployeeEditMutationControls();
      try {
        const data = await api('/api/save_employee', { method: 'POST', body: readEmployeeFormPayload() });
        if (!isCurrent()) return;
        state.employees = Array.isArray(data?.employees) ? data.employees : [];
        state.employeesLoadedMonth = isCurrent.month;
        state.employeeCreateMode = false;
        state.activeEmployeeId = data?.employee?.id || state.activeEmployeeId;
        isCurrent.rebindEntity();
        const report = await loadPayrollReport({ month: isCurrent.month, apply: false });
        if (!isCurrent()) return;
        state.payrollReport = report;
        state.payrollReportMonth = isCurrent.month;
        if (String(state.activeEmployeeSalaryId || '') === String(data?.employee?.id || '')) {
          const sheet = await loadEmployeeSalarySheet(state.activeEmployeeSalaryId, { openModal: true });
          if (!isCurrent() || !sheet) return;
        }
        renderEmployeesWorkspace();
        refreshRepairOrderEmployeeSelects();
        setStatus(data?.created ? 'СОТРУДНИК ДОБАВЛЕН.' : 'СОТРУДНИК СОХРАНЕН.', false);
      } catch (error) {
        if (isCurrent()) setStatus(error.message, true);
      } finally {
        if (isCurrent.release()) syncEmployeeEditMutationControls();
      }
    }

    async function deleteEmployee() {
      if (!requireEmployeesCashboxesAccess()) return;
      const employee = selectedEmployeeRecord();
      if (!employee) {
        setStatus('ВЫБЕРИ СОТРУДНИКА ДЛЯ УДАЛЕНИЯ.', true);
        return;
      }
      if (!confirmDiscardEmployeeChanges()) return;
      if (!window.confirm('Удалить сотрудника "' + String(employee.name || 'Сотрудник') + '"?')) return;
      const isCurrent = employeeAsyncContext(
        'employeeEditOperation',
        'activeEmployeeId',
        { singleFlight: true },
      );
      if (!isCurrent) return;
      syncEmployeeEditMutationControls();
      try {
        const data = await api('/api/delete_employee', {
          method: 'POST',
          body: {
            employee_id: employee.id,
            actor_name: state.actor,
            source: 'ui',
          },
        });
        if (!isCurrent()) return;
        state.employees = Array.isArray(data?.employees) ? data.employees : [];
        state.employeesLoadedMonth = state.payrollMonth || currentPayrollMonthValue();
        if (String(state.activeEmployeeId || '') === String(employee.id || '')) {
          state.activeEmployeeId = '';
        }
        state.employeesReportDetailsOpen = false;
        state.employeeShiftAccrualOpen = false;
        state.employeeShiftAccrualDraft = '';
        state.employeeCreateMode = !state.employees.length;
        isCurrent.rebindEntity();
        const report = await loadPayrollReport({ month: isCurrent.month, apply: false });
        if (!isCurrent()) return;
        state.payrollReport = report;
        state.payrollReportMonth = isCurrent.month;
        if (String(state.activeEmployeeSalaryId || '') === String(employee.id || '')) {
          state.activeEmployeeSalaryId = '';
          state.employeeSalarySheet = null;
          closeEmployeeSalaryModal();
        }
        renderEmployeesWorkspace();
        refreshRepairOrderEmployeeSelects();
        setStatus('СОТРУДНИК УДАЛЕН.', false);
      } catch (error) {
        if (isCurrent()) setStatus(error.message, true);
      } finally {
        if (isCurrent.release()) syncEmployeeEditMutationControls();
      }
    }

    async function handleEmployeesMonthChange() {
      if (!confirmDiscardEmployeeChanges()) {
        if (els.employeesMonthInput) {
          els.employeesMonthInput.value = state.payrollMonth || currentPayrollMonthValue();
        }
        return;
      }
      try {
        await loadEmployeesWorkspace(false);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    function handleEmployeesListClick(event) {
      if (operatorHasEmployeesReadOnlyAccess()) return;
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const salaryButton = target.closest('[data-employee-salary]');
      if (salaryButton instanceof HTMLElement) {
        const employeeId = String(salaryButton.dataset.employeeSalary || '').trim();
        if (!employeeId) return;
        openEmployeeSalaryModal(employeeId);
        return;
      }
      const reportButton = target.closest('[data-employee-report]');
      if (reportButton instanceof HTMLElement) {
        event.preventDefault();
        const employeeId = String(reportButton.dataset.employeeReport || '').trim();
        if (!employeeId) return;
        openEmployeeSalaryReconciliationPeriodDialog(employeeId);
        return;
      }
      const row = target.closest('[data-employee-id]');
      if (!(row instanceof HTMLElement)) return;
      const nextEmployeeId = String(row.dataset.employeeId || '').trim();
      if (!nextEmployeeId) return;
      if (!confirmDiscardEmployeeChanges()) return;
      state.employeeCreateMode = false;
      state.activeEmployeeId = nextEmployeeId;
      state.employeesReportDetailsOpen = true;
      state.employeeShiftAccrualOpen = false;
      state.employeeShiftAccrualDraft = '';
      renderEmployeesWorkspace();
    }

    function handleEmployeesModalFormInput(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      if (target.closest('#employeeSalaryReconciliationPeriod')) return;
      if (target === els.employeeShiftAccrualAmountInput) {
        state.employeeShiftAccrualDraft = String(els.employeeShiftAccrualAmountInput.value || '').trim();
        return;
      }
      const incentiveInput = target.closest('[data-employee-incentive-value]');
      if (incentiveInput instanceof HTMLInputElement) {
        const kind = String(incentiveInput.dataset.employeeIncentiveValue || '').trim();
        setEmployeeIncentiveFieldValue(kind, incentiveInput.value);
        syncEmployeeSalaryModeFromIncentives();
        renderEmployeeProfileMeta();
        renderEmployeeShiftAccrualDialog();
        return;
      }
      if (target === els.employeeSalaryModeInput) {
        syncEmployeeSalaryModeUi();
        renderEmployeeShiftAccrualDialog();
        return;
      }
      if (
        target === els.employeeNameInput
        || target === els.employeeMiddleNameInput
        || target === els.employeePositionInput
        || target === els.employeeBaseSalaryInput
        || target === els.employeeWorkPercentInput
        || target === els.employeeMaterialPercentInput
        || target === els.employeeRepairOrderPercentInput
      ) {
        renderEmployeeProfileMeta();
        renderEmployeeShiftAccrualDialog();
      }
    }

    function handleEmployeesModalKeydown(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      if (target === els.employeeShiftAccrualAmountInput && event.key === 'Enter') {
        event.preventDefault();
        handleEmployeeShiftAccrualConfirm();
      }
    }

    function handleEmployeeSalaryModalInput(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      if (target === els.employeeSalaryAmountInput) {
        state.employeeSalaryActionDraft = String(els.employeeSalaryAmountInput.value || '').trim();
        return;
      }
      if (target === els.employeeSalaryAdvanceAmountInput) {
        state.employeeSalaryAdvanceDraft = String(els.employeeSalaryAdvanceAmountInput.value || '').trim();
        return;
      }
      if (target === els.employeeSalaryAdvanceCommentInput) {
        state.employeeSalaryAdvanceNoteDraft = String(els.employeeSalaryAdvanceCommentInput.value || '').trim();
        return;
      }
      if (target === els.employeeSalaryCashboxSelect) {
        state.employeeSalaryCashboxId = String(els.employeeSalaryCashboxSelect.value || '').trim();
        return;
      }
      if (target === els.employeeSalaryAdvanceCashboxSelect) {
        state.employeeSalaryCashboxId = String(els.employeeSalaryAdvanceCashboxSelect.value || '').trim();
      }
    }

    function handleEmployeeSalaryModalKeydown(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      if (target === els.employeeSalaryAmountInput && event.key === 'Enter') {
        event.preventDefault();
        handleEmployeeSalaryActionConfirm();
        return;
      }
      if (
        (target === els.employeeSalaryAdvanceAmountInput || target === els.employeeSalaryAdvanceCommentInput)
        && event.key === 'Enter'
      ) {
        event.preventDefault();
        handleEmployeeSalaryAdvanceConfirm();
      }
    }

    function handleEmployeeSalaryActionButtonsClick(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      if (target === els.employeeSalaryResetButton) {
        handleEmployeeSalaryReset();
        return;
      }
      if (target === els.employeeSalaryPayoutButton) {
        openEmployeeSalaryDialog('salary_payout');
        return;
      }
      if (target === els.employeeSalaryAdvanceButton) {
        openEmployeeSalaryAdvanceDialog();
        return;
      }
      if (target === els.employeeSalaryActionCancelButton) {
        closeEmployeeSalaryDialog();
        return;
      }
      if (target === els.employeeSalaryActionConfirmButton) {
        handleEmployeeSalaryActionConfirm();
        return;
      }
      if (target === els.employeeSalaryAdvanceCancelButton) {
        closeEmployeeSalaryAdvanceDialog();
        return;
      }
      if (target === els.employeeSalaryAdvanceConfirmButton) {
        handleEmployeeSalaryAdvanceConfirm();
      }
    }

    async function handleEmployeesDetailClick(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const row = target.closest('[data-card-id]');
      if (!(row instanceof HTMLElement)) return;
      const cardId = String(row.dataset.cardId || '').trim();
      if (!cardId) return;
      if (!confirmDiscardEmployeeChanges()) return;
      try {
        const shouldOpenRepairOrder = String(row.dataset.openRepairOrder || '') === '1';
        if (shouldOpenRepairOrder) {
          await openRepairOrderCard(cardId, { parentLayer: 'employees' });
        } else {
          await openCardWorkspace(cardId, { openCardModalEl: true });
        }
      } catch (error) {
        setStatus(error.message, true);
      }
    }
