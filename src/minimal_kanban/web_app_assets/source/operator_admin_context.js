function operatorEmployeeById(employeeId) {
	const id = String(employeeId || '').trim();
	if (!id) return null;
	return (Array.isArray(state.employees) ? state.employees : []).find((item) => String(item?.id || '').trim() === id) || null;
}

function operatorEmployeeBoundUsername(employeeId, exceptUsername = '') {
	const id = String(employeeId || '').trim();
	const userKey = String(exceptUsername || '').trim().toUpperCase();
	if (!id) return '';
	const user = (Array.isArray(state.operatorUsers) ? state.operatorUsers : []).find((item) => {
		const username = String(item?.username || '').trim().toUpperCase();
		return username && username !== userKey && String(item?.employee_id || '').trim() === id;
	});
	return user?.username || '';
}

function operatorUserEmployeeLabel(user) {
	const employeeId = String(user?.employee_id || '').trim();
	if (!employeeId) return 'СОТРУДНИК: НЕ ПРИВЯЗАН';
	const employee = operatorEmployeeById(employeeId);
	if (!employee) return 'СОТРУДНИК: НЕ НАЙДЕН';
	return 'СОТРУДНИК: ' + (employee.name || 'Сотрудник') + (employee.is_active ? '' : ' (ВЫКЛЮЧЕН)');
}

function operatorUserEmployeeOptionsHtml(selectedId = '', username = '') {
	const selected = String(selectedId || '').trim();
	const userKey = String(username || '').trim().toUpperCase();
	const employees = Array.isArray(state.employees) ? state.employees : [];
	const rendered = new Set();
	const options = ['<option value="">НЕ ПРИВЯЗАН</option>'];
	employees.forEach((employee) => {
		const employeeId = String(employee?.id || '').trim();
		if (!employeeId || rendered.has(employeeId) || !employee?.is_active) return;
		rendered.add(employeeId);
		const boundUsername = operatorEmployeeBoundUsername(employeeId, userKey);
		const disabled = boundUsername ? ' disabled' : '';
		const suffix = boundUsername ? (' (' + boundUsername + ')') : '';
		options.push(
			'<option value="' + escapeHtml(employeeId) + '"' + (employeeId === selected ? ' selected' : '') + disabled + '>' + escapeHtml((employee.name || 'Сотрудник') + suffix) + '</option>'
		);
	});
	if (selected && !rendered.has(selected)) {
		const employee = operatorEmployeeById(selected);
		const label = employee ? ((employee.name || 'Сотрудник') + ' (ВЫКЛЮЧЕН)') : 'СОТРУДНИК НЕ НАЙДЕН';
		options.push('<option value="' + escapeHtml(selected) + '" selected disabled>' + escapeHtml(label) + '</option>');
	}
	return options.join('');
}

function applyOperatorUserSummary(user, { preserveBindingDraft = false } = {}) {
	const username = String(user?.username || '').trim().toUpperCase();
	if (!username) return false;
	const draft = preserveBindingDraft && state.operatorEmployeeBindingUser
		? {
				generation: state.operatorEmployeeBindingIntentGeneration,
				username: String(state.operatorEmployeeBindingUser || '').trim().toUpperCase(),
				employeeId: String(els.operatorUserEmployeeSelect?.value || '').trim(),
			}
		: null;
	const users = [...(Array.isArray(state.operatorUsers) ? state.operatorUsers : [])];
	const index = users.findIndex(
		(item) => String(item?.username || '').trim().toUpperCase() === username,
	);
	const currentRev = String(users[index]?.updated_at || '').trim();
	const incomingRev = String(user.updated_at || '').trim();
	if (currentRev && incomingRev && currentRev >= incomingRev) return false;
	if (index >= 0) users[index] = user;
	else users.push(user);
	renderOperatorUsers({ users });
	if (
		draft
		&& state.operatorEmployeeBindingIntentGeneration === draft.generation
		&& String(state.operatorEmployeeBindingUser || '').trim().toUpperCase() === draft.username
		&& els.operatorUserEmployeeSelect
	) els.operatorUserEmployeeSelect.value = draft.employeeId;
	return true;
}

function syncOperatorEmployeeBindingMutationControls() {
	const pending = Boolean(state.operatorEmployeeBindingMutationRequest);
	if (els.operatorUserEmployeeSelect) els.operatorUserEmployeeSelect.disabled = pending;
	if (els.operatorUserEmployeeSaveButton) els.operatorUserEmployeeSaveButton.disabled = pending;
	if (els.operatorUserEmployeeClearButton) els.operatorUserEmployeeClearButton.disabled = pending;
}

function syncOperatorUserSaveControl() {
	if (els.adminSaveUserButton) {
		els.adminSaveUserButton.disabled = Boolean(state.operatorUserSaveRequest);
	}
}

function claimOperatorBindingIntent() {
	state.operatorEmployeeBindingIntentGeneration = (state.operatorEmployeeBindingIntentGeneration || 0) + 1;
	return state.operatorEmployeeBindingIntentGeneration;
}

function claimOperatorUserEditorIntent() {
	claimOperatorBindingIntent();
	state.operatorUserEditorIntentGeneration = (state.operatorUserEditorIntentGeneration || 0) + 1;
	return state.operatorUserEditorIntentGeneration;
}

function operatorUserEditorDraftSignature() {
	return JSON.stringify({
		username: String(els.adminUserLogin?.value || ''),
		password: String(els.adminUserPassword?.value || ''),
		permissionEditor: String(state.operatorPermissionEditorUsername || ''),
		employeesCashboxes: Boolean(els.adminUserEmployeesCashboxesAccess?.checked),
		employeesRead: Boolean(els.adminUserEmployeesReadAccess?.checked),
		salaryBalanceReset: Boolean(els.adminUserSalaryBalanceReset?.checked),
	});
}

function clearOperatorUserEditor({ invalidate = true } = {}) {
	if (invalidate) claimOperatorUserEditorIntent();
	state.operatorPermissionEditorUsername = '';
	if (els.adminUserLogin) els.adminUserLogin.value = '';
	if (els.adminUserPassword) els.adminUserPassword.value = '';
	if (els.adminUserSalaryBalanceReset) els.adminUserSalaryBalanceReset.checked = false;
	if (els.adminUserEmployeesCashboxesAccess) els.adminUserEmployeesCashboxesAccess.checked = false;
	if (els.adminUserEmployeesReadAccess) els.adminUserEmployeesReadAccess.checked = false;
	syncOperatorAdminSalaryResetPermission();
	syncOperatorUserSaveControl();
}

function resetOperatorAdminViewerState() {
	claimOperatorBindingIntent();
	state.operatorUserEditorIntentGeneration = (state.operatorUserEditorIntentGeneration || 0) + 1;
	state.operatorEmployeeBindingMutationRequest = null;
	state.operatorUserSaveRequest = null;
	state.operatorUsers = [];
	state.operatorEmployeeBindingUser = '';
	clearOperatorUserEditor({ invalidate: false });
	if (els.operatorUserEmployeeSelect) els.operatorUserEmployeeSelect.value = '';
	syncOperatorEmployeeBindingMutationControls();
}

function beginOperatorEmployeeBindingMutation(username) {
	if (state.operatorEmployeeBindingMutationRequest) return null;
	const request = {};
	const viewer = captureViewerRequestContext();
	const intent = state.operatorEmployeeBindingIntentGeneration || 0;
	const userKey = String(username || '').trim().toUpperCase();
	state.operatorEmployeeBindingMutationRequest = request;
	return {
		viewer,
		ownsRequest: () => state.operatorEmployeeBindingMutationRequest === request,
		isCurrent: () => state.operatorEmployeeBindingMutationRequest === request
			&& viewer.isCurrent()
			&& state.operatorEmployeeBindingIntentGeneration === intent
			&& String(state.operatorEmployeeBindingUser || '').trim().toUpperCase() === userKey,
	};
}

function finishOperatorEmployeeBindingMutation(context) {
	if (!context?.ownsRequest()) return false;
	state.operatorEmployeeBindingMutationRequest = null;
	syncOperatorEmployeeBindingMutationControls();
	return true;
}

function beginOperatorUserSaveMutation() {
	if (state.operatorUserSaveRequest) return null;
	const request = {};
	const viewer = captureViewerRequestContext();
	const intent = state.operatorUserEditorIntentGeneration || 0;
	const draftKey = operatorUserEditorDraftSignature();
	state.operatorUserSaveRequest = request;
	return {
		viewer,
		ownsRequest: () => state.operatorUserSaveRequest === request,
		isCurrent: () => state.operatorUserSaveRequest === request
			&& viewer.isCurrent()
			&& state.operatorUserEditorIntentGeneration === intent
			&& operatorUserEditorDraftSignature() === draftKey,
	};
}

function finishOperatorUserSaveMutation(context) {
	if (!context?.ownsRequest()) return false;
	state.operatorUserSaveRequest = null;
	syncOperatorUserSaveControl();
	return true;
}
