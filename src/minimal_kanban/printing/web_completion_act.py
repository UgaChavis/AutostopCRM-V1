"""JavaScript editor fragment for the embedded completion-act UI."""

PRINTING_COMPLETION_ACT_SCRIPT = r"""    function blankCompletionActParty() {
      return {
        legal_name: '',
        address: '',
        inn: '',
        kpp: '',
        ogrn: '',
        bank_name: '',
        bik: '',
        settlement_account: '',
        correspondent_account: '',
        signer_position: '',
        signer_name: '',
      };
    }

    function blankCompletionActForm() {
      return {
        document_number: '',
        document_date: '',
        basis: '',
        performer: blankCompletionActParty(),
        customer: blankCompletionActParty(),
        items: [],
        acceptance_text: '',
      };
    }

    function cloneCompletionActValue(value) {
      return JSON.parse(JSON.stringify(value ?? null));
    }

    function normalizeCompletionActParty(value) {
      const source = value && typeof value === 'object' ? value : {};
      const blank = blankCompletionActParty();
      Object.keys(blank).forEach((key) => { blank[key] = String(source[key] ?? '').trim(); });
      return blank;
    }

    function normalizeCompletionActItems(value) {
      return (Array.isArray(value) ? value : []).map((item) => {
        const source = item && typeof item === 'object' ? item : {};
        return {
          id: String(source.id ?? '').trim(),
          section: String(source.section ?? '').trim() === 'materials' ? 'materials' : (String(source.section ?? '').trim() === 'manual' ? 'manual' : 'works'),
          name: String(source.name ?? '').trim(),
          unit: String(source.unit ?? '').trim(),
          quantity: String(source.quantity ?? '').trim(),
          price: String(source.price ?? '').trim(),
        };
      });
    }

    function normalizeCompletionActForm(value) {
      const source = value && typeof value === 'object' ? value : {};
      return {
        document_number: String(source.document_number ?? '').trim(),
        document_date: String(source.document_date ?? '').trim(),
        basis: String(source.basis ?? '').trim(),
        performer: normalizeCompletionActParty(source.performer),
        customer: normalizeCompletionActParty(source.customer),
        items: normalizeCompletionActItems(source.items),
        acceptance_text: String(source.acceptance_text ?? '').trim(),
      };
    }

    function completionActReadPath(value, path) {
      return String(path || '').split('.').reduce((current, key) => {
        if (current === null || current === undefined) return undefined;
        return current[key];
      }, value);
    }

    function completionActSetPath(value, path, nextValue) {
      const keys = String(path || '').split('.').filter(Boolean);
      let target = value;
      keys.forEach((key, index) => {
        if (index === keys.length - 1) target[key] = nextValue;
        else {
          if (!target[key] || typeof target[key] !== 'object') target[key] = {};
          target = target[key];
        }
      });
    }

    function completionActDefaultSource(path) {
      if (String(path).startsWith('performer.')) return 'settings';
      if (String(path).startsWith('customer.')) return 'client';
      return 'repair_order';
    }

    function completionActSourceLabel(source) {
      const normalized = String(source || '').toLowerCase();
      if (['manual', 'draft', 'override', 'user'].includes(normalized)) return 'вручную';
      if (['client', 'customer'].includes(normalized)) return 'из клиента';
      if (['settings', 'service_profile', 'profile', 'performer'].includes(normalized)) return 'из настроек';
      if (normalized === 'system') return 'стандарт';
      if (normalized === 'empty') return 'не заполнено';
      return 'из ЗН';
    }

    function renderCompletionActSourceBadges() {
      printEls.completionActForm?.querySelectorAll('[data-completion-act-source]').forEach((badge) => {
        const path = badge.getAttribute('data-completion-act-source') || '';
        const rawSource = completionActReadPath(repairOrderPrintState.completionAct.sources, path) || completionActDefaultSource(path);
        const label = completionActSourceLabel(rawSource);
        const freshValue = completionActReadPath(repairOrderPrintState.completionAct.freshForm, path);
        const currentValue = completionActReadPath(repairOrderPrintState.completionAct.form, path);
        const changed = label === 'вручную' && String(freshValue ?? '') !== String(currentValue ?? '');
        badge.classList.toggle('is-manual', label === 'вручную');
        badge.textContent = changed && String(freshValue ?? '').trim()
          ? (label + ' · CRM: ' + String(freshValue).trim().slice(0, 28))
          : label;
        badge.title = changed ? ('Текущее значение CRM: ' + String(freshValue ?? '')) : '';
      });
    }

    function completionActItemsHaveManualSource() {
      const sourceItems = repairOrderPrintState.completionAct.sources?.items;
      if (!Array.isArray(sourceItems)) return false;
      return sourceItems.some((item) => {
        const values = item && typeof item === 'object' ? Object.values(item) : [item];
        return values.some((source) => completionActSourceLabel(source) === 'вручную');
      });
    }

    function completionActItemsDiffer(currentItems, freshItems) {
      return JSON.stringify(normalizeCompletionActItems(currentItems)) !== JSON.stringify(normalizeCompletionActItems(freshItems));
    }

    function completionActFreshItemSectionLabel(section) {
      if (section === 'materials') return 'Материал';
      if (section === 'manual') return 'Другое';
      return 'Работа';
    }

    function renderCompletionActFreshItems() {
      const currentItems = normalizeCompletionActItems(repairOrderPrintState.completionAct.form?.items);
      const freshItems = normalizeCompletionActItems(repairOrderPrintState.completionAct.freshForm?.items);
      const visible = Boolean(
        repairOrderPrintState.completionAct.draft?.is_stale
        && completionActItemsHaveManualSource()
        && completionActItemsDiffer(currentItems, freshItems)
      );
      if (printEls.completionActFreshItems) printEls.completionActFreshItems.hidden = !visible;
      if (!visible) {
        if (printEls.completionActFreshItemsCount) printEls.completionActFreshItemsCount.textContent = '';
        if (printEls.completionActFreshItemsList) printEls.completionActFreshItemsList.innerHTML = '';
        return;
      }
      if (printEls.completionActFreshItemsCount) printEls.completionActFreshItemsCount.textContent = 'Строк в CRM: ' + String(freshItems.length);
      if (!printEls.completionActFreshItemsList) return;
      printEls.completionActFreshItemsList.innerHTML = freshItems.length ? freshItems.map((item) => (
        '<div class="completion-act-fresh-item" data-completion-act-fresh-item role="listitem">'
        + '<div class="completion-act-fresh-item__section">' + escapeHtml(completionActFreshItemSectionLabel(item.section)) + '</div>'
        + '<div class="completion-act-fresh-item__name"><strong>Наименование:</strong> ' + escapeHtml(item.name || '—') + '</div>'
        + '<div class="completion-act-fresh-item__meta">Количество: ' + escapeHtml(item.quantity || '—')
        + ' · Ед.: ' + escapeHtml(item.unit || '—')
        + ' · Цена: ' + escapeHtml(item.price || '—') + '</div>'
        + '</div>'
      )).join('') : '<div class="completion-act-fresh-item" data-completion-act-fresh-item role="listitem"><div class="completion-act-fresh-item__name">В свежем заказ-наряде строк нет.</div></div>';
    }

    function renderCompletionActTotals(totals = null) {
      const supplied = totals && typeof totals === 'object' ? totals : null;
      if (supplied) {
        if (printEls.completionActBaseTotal) printEls.completionActBaseTotal.textContent = supplied.base_display || '—';
        if (printEls.completionActVatTotal) printEls.completionActVatTotal.textContent = supplied.vat_display || '—';
        if (printEls.completionActGrossTotal) printEls.completionActGrossTotal.textContent = supplied.gross_display || '—';
        return;
      }
      if (printEls.completionActBaseTotal) printEls.completionActBaseTotal.textContent = 'Пересчёт…';
      if (printEls.completionActVatTotal) printEls.completionActVatTotal.textContent = 'Пересчёт…';
      if (printEls.completionActGrossTotal) printEls.completionActGrossTotal.textContent = 'Пересчёт…';
    }

    function completionActComputedItem(item, index) {
      const computedItems = Array.isArray(repairOrderPrintState.completionAct.computedItems) ? repairOrderPrintState.completionAct.computedItems : [];
      return computedItems.find((computed) => item.id && computed.id === item.id)
        || computedItems.find((computed) => printFiniteNumber(computed.index, 0) === index + 1)
        || null;
    }

    function renderCompletionActItems(items) {
      if (!printEls.completionActItemRows) return;
      const normalized = normalizeCompletionActItems(items);
      if (printEls.completionActAddItemButton) {
        printEls.completionActAddItemButton.disabled = normalized.length >= COMPLETION_ACT_MAX_ITEMS;
      }
      printEls.completionActItemRows.innerHTML = normalized.length ? normalized.map((item, index) => {
        const rowNumber = index + 1;
        const computed = completionActComputedItem(item, index);
        const lineTotal = computed?.sum_without_vat_display || 'Пересчёт…';
        const itemSource = completionActReadPath(repairOrderPrintState.completionAct.sources, 'items.' + String(index) + '.name') || 'repair_order';
        return '<div class="completion-act-item" data-completion-act-item data-completion-act-item-id="' + escapeHtml(item.id) + '">' +
          '<div class="field field--compact"><label>Раздел</label><select data-completion-act-item-field="section"><option value="works"' + (item.section === 'works' ? ' selected' : '') + '>Работа</option><option value="materials"' + (item.section === 'materials' ? ' selected' : '') + '>Материал</option><option value="manual"' + (item.section === 'manual' ? ' selected' : '') + '>Другое</option></select></div>' +
          '<div class="field field--compact"><label>Наименование <span class="completion-act-source-badge' + (completionActSourceLabel(itemSource) === 'вручную' ? ' is-manual' : '') + '">' + escapeHtml(completionActSourceLabel(itemSource)) + '</span></label><input data-completion-act-item-field="name" type="text" maxlength="320" value="' + escapeHtml(item.name) + '"></div>' +
          '<div class="field field--compact"><label>Количество</label><input data-completion-act-item-field="quantity" inputmode="decimal" type="text" maxlength="32" value="' + escapeHtml(item.quantity) + '"></div>' +
          '<div class="field field--compact"><label>Ед.</label><input data-completion-act-item-field="unit" type="text" maxlength="24" value="' + escapeHtml(item.unit) + '"></div>' +
          '<div class="field field--compact"><label>Цена без НДС</label><input data-completion-act-item-field="price" inputmode="decimal" type="text" maxlength="32" value="' + escapeHtml(item.price) + '"></div>' +
          '<div class="field field--compact"><label>Сумма</label><output class="completion-act-item__total">' + escapeHtml(lineTotal) + '</output></div>' +
          '<div class="completion-act-item__actions"><button class="btn btn--ghost" data-completion-act-item-action="up" type="button" aria-label="Переместить строку ' + String(rowNumber) + ' выше" title="Переместить строку ' + String(rowNumber) + ' выше">↑</button><button class="btn btn--ghost" data-completion-act-item-action="down" type="button" aria-label="Переместить строку ' + String(rowNumber) + ' ниже" title="Переместить строку ' + String(rowNumber) + ' ниже">↓</button><button class="btn btn--ghost" data-completion-act-item-action="duplicate" type="button" aria-label="Дублировать строку ' + String(rowNumber) + '" title="Дублировать строку ' + String(rowNumber) + '">⧉</button><button class="btn btn--ghost" data-completion-act-item-action="remove" type="button" aria-label="Удалить строку ' + String(rowNumber) + '" title="Удалить строку ' + String(rowNumber) + '">×</button></div>' +
        '</div>';
      }).join('') : '<div class="repair-order-print-empty">Строк пока нет. Добавьте работу или материал.</div>';
    }

    function renderCompletionActComputedValues(preview) {
      repairOrderPrintState.completionAct.computedItems = Array.isArray(preview?.computed_items) ? cloneCompletionActValue(preview.computed_items) : [];
      repairOrderPrintState.completionAct.totals = preview?.computed_totals && typeof preview.computed_totals === 'object' ? cloneCompletionActValue(preview.computed_totals) : {};
      const rows = Array.from(printEls.completionActItemRows?.querySelectorAll('[data-completion-act-item]') || []);
      const formItems = readCompletionActItemsFromInputs();
      rows.forEach((row, index) => {
        const output = row.querySelector('.completion-act-item__total');
        const computed = completionActComputedItem(formItems[index] || {}, index);
        if (output) output.textContent = computed?.sum_without_vat_display || '—';
      });
      renderCompletionActTotals(repairOrderPrintState.completionAct.totals);
    }

    function readCompletionActItemsFromInputs() {
      if (!printEls.completionActItemRows) return [];
      return Array.from(printEls.completionActItemRows.querySelectorAll('[data-completion-act-item]')).map((row) => ({
        id: String(row.getAttribute('data-completion-act-item-id') || '').trim(),
        section: ['works', 'materials', 'manual'].includes(row.querySelector('[data-completion-act-item-field="section"]')?.value) ? row.querySelector('[data-completion-act-item-field="section"]')?.value : 'works',
        name: String(row.querySelector('[data-completion-act-item-field="name"]')?.value || '').trim(),
        unit: String(row.querySelector('[data-completion-act-item-field="unit"]')?.value || '').trim(),
        quantity: String(row.querySelector('[data-completion-act-item-field="quantity"]')?.value || '').trim(),
        price: String(row.querySelector('[data-completion-act-item-field="price"]')?.value || '').trim(),
      }));
    }

    function applyCompletionActFormToInputs(form) {
      const normalized = normalizeCompletionActForm(form);
      repairOrderPrintState.completionAct.form = cloneCompletionActValue(normalized);
      printEls.completionActForm?.querySelectorAll('[data-completion-act-field]').forEach((field) => {
        const rawValue = String(completionActReadPath(normalized, field.getAttribute('data-completion-act-field') || '') ?? '');
        field.value = field.getAttribute('type') === 'date' && /^\d{4}-\d{2}-\d{2}/.test(rawValue) ? rawValue.slice(0, 10) : rawValue;
      });
      renderCompletionActItems(normalized.items);
      renderCompletionActSourceBadges();
      renderCompletionActFreshItems();
      renderCompletionActTotals(repairOrderPrintState.completionAct.totals);
    }

    function readCompletionActFormFromInputs() {
      const form = blankCompletionActForm();
      printEls.completionActForm?.querySelectorAll('[data-completion-act-field]').forEach((field) => {
        completionActSetPath(form, field.getAttribute('data-completion-act-field') || '', String(field.value || '').trim());
      });
      form.items = readCompletionActItemsFromInputs();
      return normalizeCompletionActForm(form);
    }

    function completionActCurrentForm() {
      if (printEls.completionActModal?.classList.contains('is-open')) {
        const session = completionActEditorSessionSnapshot();
        if (!completionActEditorSessionIsCurrent(session)) return null;
        return readCompletionActFormFromInputs();
      }
      return repairOrderPrintState.completionAct.form ? normalizeCompletionActForm(repairOrderPrintState.completionAct.form) : null;
    }

    function completionActRequestOverrides() {
      if (!printEls.completionActModal?.classList.contains('is-open')) return {};
      const form = completionActCurrentForm();
      return form ? { completion_act: form } : {};
    }

    function completionActActiveCardId() {
      return String(state.editingId || state.activeCard?.id || '').trim();
    }

    function completionActEditorSessionSnapshot() {
      const current = repairOrderPrintState.completionAct;
      const cardId = String(current.cardId || '').trim();
      if (!cardId) return null;
      return { generation: current.generation, cardId, viewerGeneration: state.viewerStateGeneration, workspaceGeneration: printWorkspaceGeneration };
    }

    function resetCompletionActEditorSessionData() {
      const current = repairOrderPrintState.completionAct;
      current.form = null;
      current.savedForm = null;
      current.freshForm = null;
      current.sources = {};
      current.draft = { exists: false, version: 0, is_stale: false };
      current.totals = {};
      current.computedItems = [];
      current.warnings = [];
      current.missingFields = [];
      current.dirty = false;
      current.preview = null;
      current.pageIndex = 0;
      delete repairOrderPrintState.previewByDocument.completion_act;
      [
        printEls.completionActSaveButton,
        printEls.completionActResetButton,
        printEls.completionActExportButton,
        printEls.completionActPrintButton,
      ].forEach((button) => { if (button) button.disabled = false; });
      applyCompletionActFormToInputs(blankCompletionActForm());
      renderCompletionActStaleWarning();
      renderCompletionActLiveWarnings();
      renderCompletionActPreview();
    }

    function beginCompletionActEditorSession(cardId, returnFocus = null) {
      const current = repairOrderPrintState.completionAct;
      cancelPendingCompletionActPreview();
      current.generation += 1;
      current.previewToken += 1;
      current.cardId = String(cardId || '').trim();
      current.editRevision = 0;
      current.returnFocus = returnFocus instanceof HTMLElement ? returnFocus : null;
      resetCompletionActEditorSessionData();
      return completionActEditorSessionSnapshot();
    }

    function invalidateCompletionActEditorSession({ hideModal = false } = {}) {
      const current = repairOrderPrintState.completionAct;
      cancelPendingCompletionActPreview();
      current.generation += 1;
      current.previewToken += 1;
      current.cardId = '';
      current.editRevision = 0;
      resetCompletionActEditorSessionData();
      if (hideModal) printEls.completionActModal?.classList.remove('is-open');
    }

    function completionActEditorSessionIsCurrent(session, { requireModal = true } = {}) {
      const current = repairOrderPrintState.completionAct;
      const activeCardId = completionActActiveCardId();
      if (current.cardId && current.cardId !== activeCardId) {
        invalidateCompletionActEditorSession({ hideModal: true });
        return false;
      }
      return Boolean(
        session &&
        session.viewerGeneration === state.viewerStateGeneration &&
        session.workspaceGeneration === printWorkspaceGeneration &&
        session.generation === current.generation &&
        session.cardId === current.cardId &&
        session.cardId === activeCardId &&
        (!requireModal || printEls.completionActModal?.classList.contains('is-open'))
      );
    }

    function invalidateCompletionActEditorOnCardChange() {
      const currentCardId = String(repairOrderPrintState.completionAct.cardId || '').trim();
      if (!currentCardId || currentCardId === completionActActiveCardId()) return false;
      invalidateCompletionActEditorSession({ hideModal: true });
      return true;
    }

    function completionActEndpointPayload(extra = {}, cardId = repairOrderPrintState.completionAct.cardId) {
      return {
        source: 'ui',
        ...extra,
        card_id: String(cardId || '').trim(),
      };
    }

    function completionActIdempotencyKey(action) {
      const suffix = globalThis.crypto?.randomUUID ? globalThis.crypto.randomUUID() : (Date.now().toString(36) + '-' + Math.random().toString(36).slice(2));
      return 'completion-act-ui-' + action + '-' + suffix;
    }

    function renderCompletionActStaleWarning() {
      const stale = Boolean(repairOrderPrintState.completionAct.draft?.is_stale);
      if (!printEls.completionActStaleWarning) return;
      printEls.completionActStaleWarning.hidden = !stale;
      printEls.completionActStaleWarning.textContent = stale
        ? 'Данные CRM изменились после сохранения черновика. Ручные значения сохранены; рядом показаны свежие значения CRM.'
        : '';
    }

    function renderCompletionActLiveWarnings(warnings = [], missingFields = []) {
      const messages = (Array.isArray(warnings) ? warnings : [])
        .map((item) => String(item || '').trim())
        .filter(Boolean);
      const missing = (Array.isArray(missingFields) ? missingFields : [])
        .map((item) => String(item || '').trim())
        .filter(Boolean);
      if (missing.length) messages.push('Не заполнены поля: ' + missing.join(', ') + '.');
      const uniqueMessages = Array.from(new Set(messages));
      repairOrderPrintState.completionAct.warnings = uniqueMessages;
      repairOrderPrintState.completionAct.missingFields = missing;
      if (!printEls.completionActLiveWarnings) return;
      printEls.completionActLiveWarnings.hidden = !uniqueMessages.length;
      printEls.completionActLiveWarnings.textContent = uniqueMessages.join(' · ');
    }

    function applyCompletionActResponse(data, { session = null, preserveCurrentEdits = false, savedForm = null } = {}) {
      if (session && !completionActEditorSessionIsCurrent(session)) return false;
      const responseForm = normalizeCompletionActForm(data?.form || blankCompletionActForm());
      const currentForm = preserveCurrentEdits ? readCompletionActFormFromInputs() : null;
      repairOrderPrintState.completionAct.savedForm = cloneCompletionActValue(savedForm || responseForm);
      repairOrderPrintState.completionAct.freshForm = normalizeCompletionActForm(data?.fresh_form || responseForm);
      repairOrderPrintState.completionAct.sources = data?.sources && typeof data.sources === 'object' ? cloneCompletionActValue(data.sources) : {};
      const legacyDraftExists = Boolean(data?.draft?.exists);
      const draftState = ['absent', 'active', 'reset_tombstone'].includes(data?.draft?.state)
        ? data.draft.state
        : (legacyDraftExists ? 'active' : 'absent');
      repairOrderPrintState.completionAct.draft = {
        exists: legacyDraftExists,
        state: draftState,
        version: Math.max(0, printFiniteNumber(data?.draft?.version, 0)),
        revision: Math.max(0, printFiniteNumber(data?.draft?.revision, data?.draft?.version || 0)),
        last_operation: String(data?.draft?.last_operation || ''),
        updated_at: String(data?.draft?.updated_at || ''),
        filled_by: String(data?.draft?.filled_by || ''),
        source_fingerprint: String(data?.draft?.source_fingerprint || ''),
        current_source_fingerprint: String(data?.draft?.current_source_fingerprint || ''),
        is_stale: Boolean(data?.draft?.is_stale),
      };
      repairOrderPrintState.completionAct.totals = preserveCurrentEdits
        ? {}
        : (data?.totals && typeof data.totals === 'object' ? cloneCompletionActValue(data.totals) : {});
      repairOrderPrintState.completionAct.computedItems = [];
      repairOrderPrintState.completionAct.dirty = preserveCurrentEdits;
      if (preserveCurrentEdits) {
        repairOrderPrintState.completionAct.form = cloneCompletionActValue(currentForm);
      } else {
        repairOrderPrintState.completionAct.editRevision = 0;
        applyCompletionActFormToInputs(responseForm);
      }
      const draft = repairOrderPrintState.completionAct.draft;
      if (printEls.completionActMeta) {
        const parts = draft.state === 'active'
          ? ['Сохранён черновик · версия ' + String(draft.revision)]
          : draft.state === 'reset_tombstone'
            ? ['Черновик сброшен · используются актуальные данные CRM']
            : ['Используются актуальные данные CRM'];
        if (draft.state === 'active' && draft.filled_by) parts.push('автор ' + draft.filled_by);
        if (draft.state === 'active' && draft.updated_at) parts.push('обновлён ' + draft.updated_at);
        printEls.completionActMeta.textContent = parts.join(' · ');
      }
      renderCompletionActStaleWarning();
      renderCompletionActLiveWarnings(data?.warnings, data?.missing_fields);
      if (printEls.completionActFooterMeta) {
        printEls.completionActFooterMeta.textContent = preserveCurrentEdits
          ? 'Черновик сохранён, но в редакторе есть более новые несохранённые изменения.'
          : 'Ручные изменения относятся только к печатной версии и не меняют заказ-наряд.';
      }
      return true;
    }

    function setCompletionActSection(section) {
      const normalized = ['document', 'performer', 'customer', 'items', 'terms'].includes(section) ? section : 'document';
      repairOrderPrintState.completionAct.activeSection = normalized;
      printEls.completionActModal?.querySelectorAll('[data-completion-act-section]').forEach((button) => {
        const active = button.getAttribute('data-completion-act-section') === normalized;
        button.classList.toggle('is-active', active);
        button.setAttribute('aria-pressed', active ? 'true' : 'false');
      });
      printEls.completionActModal?.querySelectorAll('[data-completion-act-panel]').forEach((panel) => {
        panel.hidden = panel.getAttribute('data-completion-act-panel') !== normalized;
      });
    }

    function setCompletionActMobileView(view) {
      const normalized = view === 'preview' ? 'preview' : 'data';
      if (printEls.completionActLayout) printEls.completionActLayout.dataset.mobileView = normalized;
      const dataActive = normalized === 'data';
      printEls.completionActMobileDataButton?.classList.toggle('is-active', dataActive);
      printEls.completionActMobilePreviewButton?.classList.toggle('is-active', !dataActive);
      printEls.completionActMobileDataButton?.setAttribute('aria-pressed', dataActive ? 'true' : 'false');
      printEls.completionActMobilePreviewButton?.setAttribute('aria-pressed', dataActive ? 'false' : 'true');
      if (!dataActive) scheduleCurrentPrintOperation(applyCompletionActPreviewScale, 0);
    }

    function markCompletionActDirty(path = '') {
      const session = completionActEditorSessionSnapshot();
      if (!completionActEditorSessionIsCurrent(session)) return;
      repairOrderPrintState.completionAct.form = readCompletionActFormFromInputs();
      repairOrderPrintState.completionAct.dirty = true;
      repairOrderPrintState.completionAct.editRevision += 1;
      repairOrderPrintState.completionAct.totals = {};
      repairOrderPrintState.completionAct.computedItems = [];
      if (path) {
        printEls.completionActForm?.querySelectorAll('[data-completion-act-source]').forEach((badge) => {
          if (badge.getAttribute('data-completion-act-source') !== path) return;
          badge.textContent = 'вручную';
          badge.classList.add('is-manual');
        });
      }
      if (path === 'items') renderCompletionActFreshItems();
      if (printEls.completionActFooterMeta) printEls.completionActFooterMeta.textContent = 'Есть несохранённые изменения. PDF и печать используют текущие значения.';
      printEls.completionActItemRows?.querySelectorAll('.completion-act-item__total').forEach((output) => { output.textContent = 'Пересчёт…'; });
      renderCompletionActTotals();
      scheduleCompletionActPreview();
    }

    function applyCompletionActPreviewScale() {
      if (!printEls.completionActPreviewWrap || !printEls.completionActPreviewStage || !printEls.completionActPreviewFrame) return;
      const width = 920;
      const height = 1180;
      const available = Math.max(320, printEls.completionActPreviewWrap.clientWidth - 28);
      const scale = Math.max(.3, Math.min(1, available / width));
      printEls.completionActPreviewStage.style.width = String(width * scale) + 'px';
      printEls.completionActPreviewStage.style.height = String(height * scale) + 'px';
      printEls.completionActPreviewFrame.style.transform = 'scale(' + String(scale) + ')';
    }

    function renderCompletionActPreview() {
      const preview = repairOrderPrintState.completionAct.preview;
      const pages = Array.isArray(preview?.pages) ? preview.pages : [];
      const maxIndex = Math.max(0, pages.length - 1);
      const pageIndex = Math.max(0, Math.min(maxIndex, printFiniteNumber(repairOrderPrintState.completionAct.pageIndex, 0)));
      repairOrderPrintState.completionAct.pageIndex = pageIndex;
      const page = pages[pageIndex] || null;
      if (printEls.completionActPreviewFrame) printEls.completionActPreviewFrame.srcdoc = page?.html || '<!doctype html><html lang="ru"><body style="font-family: Segoe UI, sans-serif; padding: 32px; color: #444">Подготовка предпросмотра...</body></html>';
      if (printEls.completionActPreviewMeta) printEls.completionActPreviewMeta.textContent = 'Страница ' + String(pageIndex + 1) + ' / ' + String(Math.max(1, pages.length));
      if (printEls.completionActPrevPageButton) printEls.completionActPrevPageButton.disabled = !pages.length || pageIndex <= 0;
      if (printEls.completionActNextPageButton) printEls.completionActNextPageButton.disabled = !pages.length || pageIndex >= maxIndex;
      scheduleCurrentPrintOperation(applyCompletionActPreviewScale, 0);
    }

    async function refreshCompletionActPreview(options = {}) {
      const operation = capturePrintOperation();
      const session = options?.session || completionActEditorSessionSnapshot();
      if (!repairOrderPrintState.workspace || !completionActEditorSessionIsCurrent(session)) return null;
      const token = ++repairOrderPrintState.completionAct.previewToken;
      const form = readCompletionActFormFromInputs();
      repairOrderPrintState.completionAct.form = cloneCompletionActValue(form);
      try {
        const data = await operation.request('/api/preview_repair_order_print_documents', {
          method: 'POST',
          body: repairOrderPrintRequestPayload({
            card_id: session.cardId,
            selected_document_ids: ['completion_act'],
            active_document_id: 'completion_act',
            document_overrides: { ...readRegulatedPrintOverridesFromInputs(), completion_act: form },
          }),
        });
        if (
          token !== repairOrderPrintState.completionAct.previewToken ||
          !completionActEditorSessionIsCurrent(session)
        ) {
          if (options?.requireCurrent) throw new Error('Предпросмотр акта изменился во время подготовки. Повторите действие.');
          return null;
        }
        const preview = (data?.documents || []).find((item) => item.id === 'completion_act') || data?.documents?.[0] || null;
        if (!preview && options?.requireCurrent) throw new Error('Не удалось получить свежий предпросмотр акта.');
        repairOrderPrintState.completionAct.preview = preview;
        if (preview) repairOrderPrintState.previewByDocument.completion_act = preview;
        renderCompletionActLiveWarnings(preview?.warnings, preview?.missing_fields);
        renderCompletionActComputedValues(preview);
        renderCompletionActPreview();
        renderRepairOrderPrintPreview();
        return preview;
      } catch (error) {
        if (!operation.current() || error?.code === 'stale_print_operation') return;
        if (!completionActEditorSessionIsCurrent(session)) return null;
        if (token !== repairOrderPrintState.completionAct.previewToken && !options?.requireCurrent) return null;
        if (printEls.completionActFooterMeta) printEls.completionActFooterMeta.textContent = error.message || 'Не удалось построить предпросмотр.';
        return Promise.reject(error);
      }
    }

    function cancelPendingCompletionActPreview() {
      if (completionActPreviewTimer) window.clearTimeout(completionActPreviewTimer);
      completionActPreviewTimer = null;
    }

    function scheduleCompletionActPreview() {
      if (completionActPreviewTimer) window.clearTimeout(completionActPreviewTimer);
      completionActPreviewTimer = scheduleCurrentPrintOperation(() => {
        completionActPreviewTimer = null;
        refreshCompletionActPreview().catch(() => {});
      }, 320);
    }

    async function openCompletionActEditor(returnFocus = null) {
      let operation = capturePrintOperation('', { card: false });
      const cardId = await operation.wait(requireRepairOrderCardId());
      if (!cardId || cardId !== completionActActiveCardId()) return;
      operation = capturePrintOperation();
      const session = beginCompletionActEditorSession(cardId, returnFocus);
      if (!session) return;
      repairOrderPrintState.selectedDocumentIds = ['completion_act'];
      repairOrderPrintState.activeDocumentId = 'completion_act';
      renderRepairOrderPrintDocuments();
      renderRepairOrderPrintTemplateSelect();
      setCompletionActSection('document');
      setCompletionActMobileView('data');
      printEls.completionActModal?.classList.add('is-open');
      if (printEls.completionActMeta) printEls.completionActMeta.textContent = 'Загрузка данных из заказ-наряда...';
      try {
        const data = await operation.request('/api/get_completion_act_form', {
          method: 'POST',
          body: completionActEndpointPayload({}, session.cardId),
        });
        if (!completionActEditorSessionIsCurrent(session)) return;
        if (!applyCompletionActResponse(data, { session })) return;
        await operation.wait(refreshCompletionActPreview({ session }));
        if (!completionActEditorSessionIsCurrent(session)) return;
        printEls.completionActForm?.querySelector('[data-completion-act-field]')?.focus();
      } catch (error) {
        if (!operation.current() || error?.code === 'stale_print_operation') return;
        if (!completionActEditorSessionIsCurrent(session)) return;
        if (printEls.completionActMeta) printEls.completionActMeta.textContent = error.message || 'Не удалось загрузить акт.';
        setStatus(error.message, true);
      }
    }

    function closeCompletionActEditor() {
      const operation = capturePrintOperation();
      const session = completionActEditorSessionSnapshot();
      if (!completionActEditorSessionIsCurrent(session)) return true;
      if (repairOrderPrintState.completionAct.dirty && !window.confirm('Закрыть редактор без сохранения? Несохранённые изменения будут отменены.')) return false;
      const returnFocus = repairOrderPrintState.completionAct.returnFocus;
      invalidateCompletionActEditorSession({ hideModal: true });
      const restoreEditorTriggerFocus = () => {
        if (!operation.current()) return;
        const target = returnFocus?.isConnected
          ? returnFocus
          : printEls.documents?.querySelector('[data-completion-act-editor-open]');
        target?.focus();
      };
      refreshRepairOrderPrintPreview({ selected_document_ids: ['completion_act'], active_document_id: 'completion_act' })
        .catch(() => {})
        .finally(() => scheduleCurrentPrintOperation(restoreEditorTriggerFocus, 0));
      scheduleCurrentPrintOperation(restoreEditorTriggerFocus, 0);
      return true;
    }

    async function saveCompletionActDraft() {
      const operation = capturePrintOperation();
      const session = completionActEditorSessionSnapshot();
      if (!completionActEditorSessionIsCurrent(session)) return;
      if (printEls.completionActSaveButton) printEls.completionActSaveButton.disabled = true;
      const form = readCompletionActFormFromInputs();
      const editRevision = repairOrderPrintState.completionAct.editRevision;
      try {
        const data = await operation.request('/api/save_completion_act_form', {
          method: 'POST',
          body: completionActEndpointPayload(
            {
              form,
              expected_version: repairOrderPrintState.completionAct.draft.version || 0,
              expected_source_fingerprint: repairOrderPrintState.completionAct.draft.current_source_fingerprint || '',
              idempotency_key: completionActIdempotencyKey('save'),
              actor_name: 'ui',
            },
            session.cardId
          ),
        });
        if (!completionActEditorSessionIsCurrent(session)) return;
        const hasNewerEdits =
          repairOrderPrintState.completionAct.editRevision !== editRevision ||
          JSON.stringify(readCompletionActFormFromInputs()) !== JSON.stringify(form);
        if (!applyCompletionActResponse(data, {
          session,
          preserveCurrentEdits: hasNewerEdits,
          savedForm: form,
        })) return;
        await operation.wait(refreshCompletionActPreview({ session }));
        if (!completionActEditorSessionIsCurrent(session)) return;
        setStatus(
          hasNewerEdits
            ? 'Черновик сохранён; более новые изменения остаются несохранёнными.'
            : 'Черновик акта сохранён.',
          false
        );
      } catch (error) {
        if (!operation.current() || error?.code === 'stale_print_operation') return;
        if (!completionActEditorSessionIsCurrent(session)) return;
        if (printEls.completionActFooterMeta) printEls.completionActFooterMeta.textContent = error.message || 'Не удалось сохранить черновик.';
        setStatus(error.message, true);
      } finally {
        if (!operation.current()) return;
        if (completionActEditorSessionIsCurrent(session) && printEls.completionActSaveButton) {
          printEls.completionActSaveButton.disabled = false;
        }
      }
    }

    async function resetCompletionActDraft() {
      const operation = capturePrintOperation();
      if (!window.confirm('Сбросить ручные изменения акта и заново взять данные из CRM?')) return;
      const session = completionActEditorSessionSnapshot();
      if (!completionActEditorSessionIsCurrent(session)) return;
      if (printEls.completionActResetButton) printEls.completionActResetButton.disabled = true;
      const editRevision = repairOrderPrintState.completionAct.editRevision;
      try {
        const data = await operation.request('/api/reset_completion_act_form', {
          method: 'POST',
          body: completionActEndpointPayload(
            {
              expected_version: repairOrderPrintState.completionAct.draft.version || 0,
              expected_source_fingerprint: repairOrderPrintState.completionAct.draft.current_source_fingerprint || '',
              idempotency_key: completionActIdempotencyKey('reset'),
            },
            session.cardId
          ),
        });
        if (!completionActEditorSessionIsCurrent(session)) return;
        const hasNewerEdits = repairOrderPrintState.completionAct.editRevision !== editRevision;
        if (!applyCompletionActResponse(data, {
          session,
          preserveCurrentEdits: hasNewerEdits,
        })) return;
        await operation.wait(refreshCompletionActPreview({ session }));
        if (!completionActEditorSessionIsCurrent(session)) return;
        setStatus(
          hasNewerEdits
            ? 'Черновик сброшен; более новые изменения остаются несохранёнными.'
            : 'Акт сброшен к актуальным данным CRM.',
          false
        );
      } catch (error) {
        if (!operation.current() || error?.code === 'stale_print_operation') return;
        if (!completionActEditorSessionIsCurrent(session)) return;
        if (printEls.completionActFooterMeta) printEls.completionActFooterMeta.textContent = error.message || 'Не удалось сбросить черновик.';
        setStatus(error.message, true);
      } finally {
        if (!operation.current()) return;
        if (completionActEditorSessionIsCurrent(session) && printEls.completionActResetButton) {
          printEls.completionActResetButton.disabled = false;
        }
      }
    }

    async function exportCompletionActPdf() {
      const operation = capturePrintOperation();
      const session = completionActEditorSessionSnapshot();
      if (!completionActEditorSessionIsCurrent(session)) return;
      if (printEls.completionActExportButton) printEls.completionActExportButton.disabled = true;
      try {
        cancelPendingCompletionActPreview();
        const form = readCompletionActFormFromInputs();
        const editRevision = repairOrderPrintState.completionAct.editRevision;
        const preview = await operation.wait(refreshCompletionActPreview({ requireCurrent: true, session }));
        if (!preview) throw new Error('Не удалось обновить акт перед созданием PDF.');
        if (repairOrderPrintState.completionAct.editRevision !== editRevision) {
          throw new Error('Данные акта изменились во время подготовки PDF. Повторите действие.');
        }
        const data = await operation.request('/api/export_repair_order_print_pdf', {
          method: 'POST',
          body: repairOrderPrintRequestPayload({
            card_id: session.cardId,
            selected_document_ids: ['completion_act'],
            active_document_id: 'completion_act',
            document_overrides: { ...readRegulatedPrintOverridesFromInputs(), completion_act: form },
          }),
        });
        if (
          !completionActEditorSessionIsCurrent(session) ||
          repairOrderPrintState.completionAct.editRevision !== editRevision
        ) return;
        triggerBlobDownload(base64ToBlob(data?.content_base64 || '', 'application/pdf'), data?.file_name || 'completion-act.pdf');
        setStatus('PDF акта подготовлен.', false);
      } catch (error) {
        if (!operation.current() || error?.code === 'stale_print_operation') return;
        if (!completionActEditorSessionIsCurrent(session)) return;
        if (printEls.completionActFooterMeta) printEls.completionActFooterMeta.textContent = error.message || 'Не удалось подготовить PDF.';
        setStatus(error.message, true);
      } finally {
        if (!operation.current()) return;
        if (completionActEditorSessionIsCurrent(session) && printEls.completionActExportButton) {
          printEls.completionActExportButton.disabled = false;
        }
      }
    }

    async function printCompletionAct() {
      const operation = capturePrintOperation();
      const session = completionActEditorSessionSnapshot();
      if (!completionActEditorSessionIsCurrent(session)) return;
      if (printEls.completionActPrintButton) printEls.completionActPrintButton.disabled = true;
      try {
        cancelPendingCompletionActPreview();
        const editRevision = repairOrderPrintState.completionAct.editRevision;
        const preview = await operation.wait(refreshCompletionActPreview({ requireCurrent: true, session }));
        if (!preview) throw new Error('Не удалось обновить акт перед печатью.');
        if (!completionActEditorSessionIsCurrent(session)) return;
        if (repairOrderPrintState.completionAct.editRevision !== editRevision) {
          throw new Error('Данные акта изменились во время подготовки печати. Повторите действие.');
        }
        await operation.wait(runCompletionActBrowserPrint(preview));
        setStatus('Открыто системное окно печати акта.', false);
      } catch (error) {
        if (!operation.current() || error?.code === 'stale_print_operation') return;
        if (!completionActEditorSessionIsCurrent(session)) return;
        if (printEls.completionActFooterMeta) printEls.completionActFooterMeta.textContent = error.message || 'Не удалось открыть печать.';
        setStatus(error.message, true);
      } finally {
        if (!operation.current()) return;
        if (completionActEditorSessionIsCurrent(session) && printEls.completionActPrintButton) {
          printEls.completionActPrintButton.disabled = false;
        }
      }
    }

    function handleCompletionActFormInput(event) {
      const target = event.target;
      if (!(target instanceof HTMLInputElement) && !(target instanceof HTMLTextAreaElement) && !(target instanceof HTMLSelectElement)) return;
      const itemRow = target.closest('[data-completion-act-item]');
      if (itemRow) {
        const output = itemRow.querySelector('.completion-act-item__total');
        if (output) output.textContent = 'Пересчёт…';
        const badge = itemRow.querySelector('.completion-act-source-badge');
        if (badge) {
          badge.textContent = 'вручную';
          badge.classList.add('is-manual');
        }
      }
      const path = target.getAttribute('data-completion-act-field') || (itemRow ? 'items' : '');
      markCompletionActDirty(path);
    }

    function handleCompletionActItemsClick(event) {
      const session = completionActEditorSessionSnapshot();
      if (!completionActEditorSessionIsCurrent(session)) return;
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const actionButton = target.closest('[data-completion-act-item-action]');
      if (!actionButton) return;
      const row = actionButton.closest('[data-completion-act-item]');
      if (!row) return;
      const rows = readCompletionActItemsFromInputs();
      const index = Array.from(printEls.completionActItemRows?.querySelectorAll('[data-completion-act-item]') || []).indexOf(row);
      if (index < 0) return;
      const action = actionButton.getAttribute('data-completion-act-item-action') || '';
      if (action === 'remove') rows.splice(index, 1);
      else if (action === 'duplicate') {
        if (rows.length >= COMPLETION_ACT_MAX_ITEMS) {
          const message = 'В одном акте допускается не более 300 строк.';
          if (printEls.completionActFooterMeta) printEls.completionActFooterMeta.textContent = message;
          setStatus(message, true);
          return;
        }
        rows.splice(index + 1, 0, { ...rows[index], id: '' });
      }
      else if (action === 'up' && index > 0) [rows[index - 1], rows[index]] = [rows[index], rows[index - 1]];
      else if (action === 'down' && index < rows.length - 1) [rows[index], rows[index + 1]] = [rows[index + 1], rows[index]];
      else return;
      renderCompletionActItems(rows);
      markCompletionActDirty('items');
    }

    function appendCompletionActItem() {
      const session = completionActEditorSessionSnapshot();
      if (!completionActEditorSessionIsCurrent(session)) return;
      const rows = readCompletionActItemsFromInputs();
      if (rows.length >= COMPLETION_ACT_MAX_ITEMS) {
        const message = 'В одном акте допускается не более 300 строк.';
        if (printEls.completionActFooterMeta) printEls.completionActFooterMeta.textContent = message;
        if (printEls.completionActAddItemButton) printEls.completionActAddItemButton.disabled = true;
        setStatus(message, true);
        return;
      }
      rows.push({ id: '', section: 'works', name: '', unit: 'ч', quantity: '1', price: '' });
      renderCompletionActItems(rows);
      markCompletionActDirty('items');
      printEls.completionActItemRows?.querySelector('[data-completion-act-item]:last-child [data-completion-act-item-field="name"]')?.focus();
    }
"""
