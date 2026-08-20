    async function loadCashJournalData({ includeMarkdown = false } = {}) {
      const journalPath = includeMarkdown
        ? '/api/get_cash_journal?months=3&limit=5000&include_markdown=true'
        : '/api/get_cash_journal?months=3&limit=5000&include_markdown=false&compact_groups=true';
      const [journalData, cashboxesData] = await Promise.all([
        api(journalPath),
        api('/api/list_cashboxes?limit=200'),
      ]);
      return {
        ...(journalData || {}),
        cashboxes: Array.isArray(cashboxesData?.cashboxes) ? cashboxesData.cashboxes : [],
      };
    }

    async function loadCashJournalText() {
      const data = await loadCashJournalData({ includeMarkdown: true });
      return String(data?.markdown || data?.text || 'ЗА ВЫБРАННЫЙ ПЕРИОД ДВИЖЕНИЙ НЕТ.');
    }

    function renderCashJournalLoading() {
      return '<div class="cashbox-journal-loading">'
        + '<div class="cashbox-journal-loading__title">ЗАГРУЖАЮ ЖУРНАЛ...</div>'
        + '<div class="cashbox-journal-loading__hint">На наполненной базе это может занять несколько секунд. Фильтры появятся после загрузки операций.</div>'
        + '</div>';
    }

    function cashJournalDefaultFilters() {
      return { query: '', cashbox: '', type: 'all', period: 'all', periodKind: '', periodKey: '', periodLabel: '' };
    }

    function cashJournalFilters() {
      const current = state.cashboxJournalFilters || {};
      const defaults = cashJournalDefaultFilters();
      const type = ['all', 'income', 'expense', 'transfer'].indexOf(current.type) >= 0 ? current.type : defaults.type;
      const period = ['all', '30', '7', 'today'].indexOf(current.period) >= 0 ? current.period : defaults.period;
      const periodKind = ['day', 'week', 'month'].indexOf(current.periodKind) >= 0 ? String(current.periodKind) : '';
      const periodKey = periodKind ? String(current.periodKey || '').trim() : '';
      return {
        query: String(current.query || ''),
        cashbox: String(current.cashbox || ''),
        type,
        period,
        periodKind: periodKey ? periodKind : '',
        periodKey,
        periodLabel: periodKey ? String(current.periodLabel || current.periodKey || '') : '',
      };
    }

    function cashJournalStatHtml(label, value, sign = 'positive') {
      return '<div class="cashbox-journal-stat">'
        + '<div class="cashbox-journal-stat__label">' + escapeHtml(label) + '</div>'
        + '<div class="cashbox-journal-stat__value" data-balance-sign="' + escapeHtml(sign) + '">' + escapeHtml(value) + '</div>'
        + '</div>';
    }

    function cashJournalSingleTransferDisplay(incomeMinor, expenseMinor) {
      const transferMinor = Math.max(Math.abs(finiteNumber(incomeMinor)), Math.abs(finiteNumber(expenseMinor)));
      return cashboxFormatMinorAmount(transferMinor);
    }

    function cashJournalTransferSummaryText(transferMinor, count) {
      return cashboxFormatMinorAmount(transferMinor) + ' · ' + String(finiteNumber(count)) + ' оп.';
    }

    function cashJournalSignedAmountDisplay(amountMinor) {
      const amount = finiteNumber(amountMinor);
      return (amount > 0 ? '+' : '') + cashboxFormatMinorAmount(amount);
    }

    function cashJournalVisibleSource(sourceLabel) {
      const source = String(sourceLabel || '').trim();
      if (!source) return '';
      const normalized = source.toLowerCase();
      if (['api', 'ручное', 'система'].indexOf(normalized) >= 0) return '';
      return source;
    }

    function cashJournalCleanOperationNote(note) {
      const text = String(note || '').trim();
      const cleaned = text.replace(/^(?:поступление|списание|приход|расход)\s*:\s*/i, '').trim();
      return cleaned || 'Без комментария';
    }

    function cashJournalDisplayTypeLabel(direction) {
      if (direction === 'transfer') return 'Перевод';
      return direction === 'expense' ? 'Расход' : 'Приход';
    }

    function cashJournalLinkFlags(item) {
      const status = String(item?.link_status || '').trim();
      const flags = [];
      if (status === 'linked_legacy') flags.push('legacy');
      if (status === 'payment_without_order') flags.push('нет связи с оплатой');
      if (status === 'legacy_without_payment') flags.push('legacy');
      if (status === 'legacy_without_payment') flags.push('проверить связь');
      if (item?.stored_note && item.stored_note !== item.note) flags.push('исправлен display-note');
      return flags;
    }

    function cashJournalOperationTagsHtml(tags) {
      const items = (Array.isArray(tags) ? tags : [])
        .map((tag) => String(tag || '').trim())
        .filter(Boolean);
      if (!items.length) return '';
      return '<div class="cashbox-journal-operation-tags">'
        + items.map((tag) => '<span class="cashbox-journal-operation-tag">' + escapeHtml(tag) + '</span>').join('')
        + '</div>';
    }

    function cashJournalEntryNoteText(item) {
      const direction = item?.direction === 'expense' ? 'expense' : 'income';
      if (item?.repair_order_number) {
        const parts = ['ЗН №' + String(item.repair_order_number)];
        if (item?.repair_order_vehicle) parts.push(String(item.repair_order_vehicle));
        return parts.join(' · ');
      }
      return cashJournalCleanOperationNote(item?.note || (direction === 'expense' ? 'Расход' : 'Приход'));
    }

    function cashJournalTransferNote(item) {
      const raw = String(item?.note || '').trim();
      return raw
        .replace(/^перемещение\s+(?:в|из)\s+[^:]+(?::\s*)?/i, '')
        .trim();
    }

    function cashJournalTextKey(value) {
      return String(value || '').trim().toLowerCase().replace(/\s+/g, ' ');
    }

    function cashJournalLegacyTransferPeerName(item) {
      const note = String(item?.note || '').trim();
      const direction = item?.direction === 'expense' ? 'expense' : 'income';
      const pattern = direction === 'expense'
        ? /^перемещение\s+в\s+(.+?)(?::|$)/i
        : /^перемещение\s+из\s+(.+?)(?::|$)/i;
      const match = note.match(pattern);
      return match ? String(match[1] || '').trim() : '';
    }

    function cashJournalLegacyTransferActorKey(item) {
      return cashJournalTextKey(item?.actor_label || item?.actor_name || '');
    }

    function cashJournalLegacyTransferPairMatches(item, candidate) {
      if (item?.source_label !== 'перемещение' || candidate?.source_label !== 'перемещение') return false;
      if (candidate?.direction === item?.direction) return false;
      const sameDate = cashJournalEntryDateKey(candidate) === cashJournalEntryDateKey(item);
      const sameTime = String(candidate?.time_short || '') === String(item?.time_short || '');
      const amountMinor = finiteNumber(item?.amount_minor);
      const sameAmount = finiteNumber(candidate?.amount_minor) === amountMinor;
      if (!sameDate || !sameTime || !sameAmount || !amountMinor) return false;
      const itemActor = cashJournalLegacyTransferActorKey(item);
      const candidateActor = cashJournalLegacyTransferActorKey(candidate);
      if (itemActor && candidateActor && itemActor !== candidateActor) return false;
      const source = item?.direction === 'expense' ? item : candidate;
      const target = item?.direction === 'income' ? item : candidate;
      const sourceCashbox = cashJournalTextKey(source?.cashbox_name || '');
      const targetCashbox = cashJournalTextKey(target?.cashbox_name || '');
      const sourcePeer = cashJournalTextKey(cashJournalLegacyTransferPeerName(source));
      const targetPeer = cashJournalTextKey(cashJournalLegacyTransferPeerName(target));
      if (sourcePeer && sourcePeer !== targetCashbox) return false;
      if (targetPeer && targetPeer !== sourceCashbox) return false;
      return Boolean(sourcePeer || targetPeer);
    }

    function cashJournalFindTransferPair(item, entries, usedIds) {
      const itemId = String(item?.id || '');
      if (item?.source_label !== 'перемещение' || usedIds.has(itemId)) return null;
      const relatedId = String(item?.related_transaction_id || '');
      const transferGroupId = String(item?.transfer_group_id || '');
      for (const candidate of entries) {
        const candidateId = String(candidate?.id || '');
        if (!candidateId || candidateId === itemId || usedIds.has(candidateId)) continue;
        if (candidate?.source_label !== 'перемещение') continue;
        const sameRelated = relatedId && candidateId === relatedId;
        const sameGroup = transferGroupId && String(candidate?.transfer_group_id || '') === transferGroupId;
        if (!sameRelated && !sameGroup) continue;
        if (candidate?.direction === item?.direction) continue;
        const source = item?.direction === 'expense' ? item : candidate;
        const target = item?.direction === 'income' ? item : candidate;
        return { source, target };
      }
      const legacyCandidates = entries.filter((candidate) => {
        const candidateId = String(candidate?.id || '');
        if (!candidateId || candidateId === itemId || usedIds.has(candidateId)) return false;
        return cashJournalLegacyTransferPairMatches(item, candidate);
      });
      if (legacyCandidates.length === 1) {
        const candidate = legacyCandidates[0];
        const source = item?.direction === 'expense' ? item : candidate;
        const target = item?.direction === 'income' ? item : candidate;
        return { source, target };
      }
      return null;
    }

    function cashJournalDisplayRows(entries) {
      const sourceEntries = Array.isArray(entries) ? entries : [];
      const rows = [];
      const usedIds = new Set();
      sourceEntries.forEach((item) => {
        const itemId = String(item?.id || '');
        if (itemId && usedIds.has(itemId)) return;
        const transferPair = cashJournalFindTransferPair(item, sourceEntries, usedIds);
        if (transferPair) {
          if (transferPair.source?.id) usedIds.add(String(transferPair.source.id));
          if (transferPair.target?.id) usedIds.add(String(transferPair.target.id));
          rows.push({ kind: 'transfer', ...transferPair });
          return;
        }
        if (itemId) usedIds.add(itemId);
        rows.push({ kind: 'operation', item });
      });
      return rows;
    }

    function cashJournalFlatEntries(data) {
      if (Array.isArray(data?.entries) && data.entries.length) return data.entries;
      const days = Array.isArray(data?.days) ? data.days : [];
      const entries = [];
      days.forEach((day) => {
        if (Array.isArray(day?.entries)) entries.push(...day.entries);
      });
      return entries;
    }

    function cashJournalRowItems(row) {
      if (row?.kind === 'transfer') return [row.source, row.target].filter(Boolean);
      return row?.item ? [row.item] : [];
    }

    function cashJournalEntryDateKey(item) {
      const date = String(item?.business_date || item?.date || '').trim();
      if (date) return date.slice(0, 10);
      return String(item?.created_at || '').slice(0, 10);
    }

    function cashJournalDateFromKey(key) {
      const parts = String(key || '').split('-').map((part) => finiteNumber(part, NaN));
      if (parts.length < 3 || parts.some((part) => Number.isNaN(part))) return null;
      return new Date(parts[0], parts[1] - 1, parts[2]);
    }

    function cashJournalDateKey(date) {
      if (!(date instanceof Date) || Number.isNaN(date.getTime())) return '';
      const year = String(date.getFullYear()).padStart(4, '0');
      const month = String(date.getMonth() + 1).padStart(2, '0');
      const day = String(date.getDate()).padStart(2, '0');
      return year + '-' + month + '-' + day;
    }

    function cashJournalPeriodAnchor(data) {
      const entries = cashJournalFlatEntries(data);
      let anchor = null;
      entries.forEach((item) => {
        const date = cashJournalDateFromKey(cashJournalEntryDateKey(item));
        if (date && (!anchor || date > anchor)) anchor = date;
      });
      return anchor || new Date();
    }

    function cashJournalEntryMatchesPeriod(item, period, anchor) {
      if (period === 'all') return true;
      const date = cashJournalDateFromKey(cashJournalEntryDateKey(item));
      if (!date) return false;
      const anchorDay = new Date(anchor.getFullYear(), anchor.getMonth(), anchor.getDate());
      if (period === 'today') return cashJournalDateKey(date) === cashJournalDateKey(anchorDay);
      const days = period === '7' ? 7 : 30;
      const start = new Date(anchorDay);
      start.setDate(start.getDate() - (days - 1));
      return date >= start && date <= anchorDay;
    }

    function cashJournalEntryMatchesExactPeriod(item, periodKind, periodKey) {
      const key = String(periodKey || '').trim();
      if (!key) return true;
      if (periodKind === 'day') return cashJournalEntryDateKey(item) === key;
      if (periodKind === 'week') return String(item?.week_key || '') === key;
      if (periodKind === 'month') return String(item?.month_key || '') === key;
      return true;
    }

    function cashJournalSearchTextForItem(item) {
      return [
        item?.time_short,
        item?.cashbox_name,
        item?.direction_label,
        item?.signed_amount_display,
        item?.amount_display,
        item?.note,
        item?.source_label,
        item?.link_status,
        item?.repair_order_number,
        item?.repair_order_vehicle,
        item?.repair_order_card_id,
        item?.repair_order_payment_id,
        item?.actor_label,
        item?.actor_name,
        item?.short_id,
        item?.transaction_kind,
      ].map((part) => String(part || '').toLowerCase()).join(' ');
    }

    function cashJournalRowMatchesFilters(row, filters, anchor) {
      const items = cashJournalRowItems(row);
      if (!items.length) return false;
      if (filters.type !== 'all') {
        const rowType = row?.kind === 'transfer' ? 'transfer' : String(items[0]?.direction || '');
        if (rowType !== filters.type) return false;
      }
      if (filters.cashbox && !items.some((item) => String(item?.cashbox_id || '') === filters.cashbox)) {
        return false;
      }
      if (filters.periodKind && filters.periodKey) {
        if (!items.some((item) => cashJournalEntryMatchesExactPeriod(item, filters.periodKind, filters.periodKey))) {
          return false;
        }
      } else if (!items.some((item) => cashJournalEntryMatchesPeriod(item, filters.period, anchor))) {
        return false;
      }
      const query = String(filters.query || '').trim().toLowerCase();
      if (query && !items.map(cashJournalSearchTextForItem).join(' ').includes(query)) {
        return false;
      }
      return true;
    }

    function cashJournalFilteredEntries(data) {
      const entries = cashJournalFlatEntries(data);
      const filters = cashJournalFilters();
      const anchor = cashJournalPeriodAnchor(data);
      const rows = cashJournalDisplayRows(entries);
      const result = [];
      const usedIds = new Set();
      rows.forEach((row) => {
        if (!cashJournalRowMatchesFilters(row, filters, anchor)) return;
        cashJournalRowItems(row).forEach((item) => {
          const id = String(item?.id || '');
          if (id && usedIds.has(id)) return;
          if (id) usedIds.add(id);
          result.push(item);
        });
      });
      return result;
    }

    function cashJournalSummarizeEntries(entries, base = {}) {
      let externalIncomeMinor = 0;
      let externalExpenseMinor = 0;
      let transferIncomeMinor = 0;
      let transferExpenseMinor = 0;
      (Array.isArray(entries) ? entries : []).forEach((item) => {
        const amount = Math.abs(finiteNumber(item?.amount_minor));
        const isTransfer = item?.source_label === 'перемещение';
        if (item?.direction === 'expense') {
          if (isTransfer) transferExpenseMinor += amount;
          else externalExpenseMinor += amount;
        } else {
          if (isTransfer) transferIncomeMinor += amount;
          else externalIncomeMinor += amount;
        }
      });
      const balanceMinor = externalIncomeMinor - externalExpenseMinor;
      const rowsCount = cashJournalDisplayRows(entries).length;
      return {
        ...base,
        entries,
        count: rowsCount,
        income_minor: externalIncomeMinor + transferIncomeMinor,
        expense_minor: externalExpenseMinor + transferExpenseMinor,
        balance_minor: balanceMinor,
        external_income_minor: externalIncomeMinor,
        external_expense_minor: externalExpenseMinor,
        transfer_income_minor: transferIncomeMinor,
        transfer_expense_minor: transferExpenseMinor,
        income_display: cashboxFormatMinorAmount(externalIncomeMinor + transferIncomeMinor),
        expense_display: cashboxFormatMinorAmount(externalExpenseMinor + transferExpenseMinor),
        balance_display: cashJournalSignedAmountDisplay(balanceMinor),
        external_income_display: cashboxFormatMinorAmount(externalIncomeMinor),
        external_expense_display: cashboxFormatMinorAmount(externalExpenseMinor),
        transfer_income_display: cashboxFormatMinorAmount(transferIncomeMinor),
        transfer_expense_display: cashboxFormatMinorAmount(transferExpenseMinor),
      };
    }

    function cashJournalRebuildDays(data, entries) {
      const baseByDate = new Map();
      (Array.isArray(data?.days) ? data.days : []).forEach((day) => {
        baseByDate.set(String(day?.date || day?.key || ''), day);
      });
      const groups = new Map();
      (Array.isArray(entries) ? entries : []).forEach((item) => {
        const key = cashJournalEntryDateKey(item);
        if (!key) return;
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(item);
      });
      return Array.from(groups.entries()).map(([key, dayEntries]) => {
        const base = baseByDate.get(key) || { key, date: key, label: key };
        return cashJournalSummarizeEntries(dayEntries, { ...base, key, date: base.date || key });
      });
    }

    function cashJournalDayCompactSummaryHtml(day) {
      const balanceMinor = finiteNumber(day?.balance_minor);
      const operationCount = finiteNumber(day?.count, cashJournalDisplayRows(day?.entries || []).length);
      const balanceText = String(day?.balance_display || cashJournalSignedAmountDisplay(balanceMinor));
      const sign = balanceMinor < 0 ? 'negative' : 'positive';
      return '<span data-balance-sign="' + escapeHtml(sign) + '">' + escapeHtml(balanceText) + ' · ' + escapeHtml(String(operationCount)) + ' оп.</span>';
    }

    function renderCashJournalEntry(item) {
      const direction = item?.direction === 'expense' ? 'expense' : 'income';
      const source = cashJournalVisibleSource(item?.source_label);
      const visualDirection = source === 'перемещение' ? 'transfer' : direction;
      const typeLabel = cashJournalDisplayTypeLabel(visualDirection);
      const note = cashJournalEntryNoteText(item);
      const amount = String(item?.signed_amount_display || ((direction === 'expense' ? '-' : '+') + cashboxFormatMinorAmount(item?.amount_minor ?? 0).replace(/^-/, '')));
      const actor = String(item?.actor_label || item?.actor_name || '').trim();
      const actorText = actor && actor !== 'СИСТЕМА' ? actor : '';
      const flags = cashJournalLinkFlags(item);
      const tags = source ? [source].concat(flags) : flags;
      const rowLabel = [item?.time_short || '--:--', item?.cashbox_name || 'Касса', typeLabel, note, amount, actorText].filter(Boolean).join('. ');
      return '<div class="cashbox-journal-operation-row" data-direction="' + escapeHtml(visualDirection) + '" role="listitem" aria-label="Операция кассы ' + escapeHtml(rowLabel) + '">'
        + '<div class="cashbox-journal-operation-row__time">' + escapeHtml(item?.time_short || '--:--') + '</div>'
        + '<div class="cashbox-journal-operation-row__cashbox">' + escapeHtml(item?.cashbox_name || 'Касса') + '</div>'
        + '<div class="cashbox-journal-operation-row__type">' + escapeHtml(typeLabel) + '</div>'
        + '<div class="cashbox-journal-operation-row__body">'
        + '<div class="cashbox-journal-operation-row__note" title="' + escapeHtml(note) + '">' + escapeHtml(note) + '</div>'
        + cashJournalOperationTagsHtml(tags)
        + '</div>'
        + '<div class="cashbox-journal-operation-row__actor">' + escapeHtml(actorText) + '</div>'
        + '<div class="cashbox-journal-operation-row__amount" data-direction="' + escapeHtml(visualDirection) + '">' + escapeHtml(amount) + '</div>'
        + '</div>';
    }

    function renderCashJournalTransferEntry(source, target) {
      const amount = cashboxFormatMinorAmount(source?.amount_minor ?? target?.amount_minor ?? 0);
      const note = cashJournalTransferNote(source) || cashJournalTransferNote(target);
      const actor = String(source?.actor_label || source?.actor_name || target?.actor_label || target?.actor_name || '').trim();
      const actorText = actor && actor !== 'СИСТЕМА' ? actor : '';
      const cashboxPath = String(source?.cashbox_name || 'Касса') + ' → ' + String(target?.cashbox_name || 'Касса');
      const rowLabel = [source?.time_short || target?.time_short || '--:--', cashboxPath, 'Перемещение', note, amount, actorText].filter(Boolean).join('. ');
      return '<div class="cashbox-journal-operation-row cashbox-journal-operation-row--transfer" data-direction="transfer" role="listitem" aria-label="Операция кассы ' + escapeHtml(rowLabel) + '">'
        + '<div class="cashbox-journal-operation-row__time">' + escapeHtml(source?.time_short || target?.time_short || '--:--') + '</div>'
        + '<div class="cashbox-journal-operation-row__cashbox">' + escapeHtml(cashboxPath) + '</div>'
        + '<div class="cashbox-journal-operation-row__type">' + escapeHtml(cashJournalDisplayTypeLabel('transfer')) + '</div>'
        + '<div class="cashbox-journal-operation-row__body">'
        + '<div class="cashbox-journal-operation-row__note" title="' + escapeHtml(note || 'Перемещение') + '">Перемещение</div>'
        + (note ? '<div class="cashbox-journal-operation-row__source">' + escapeHtml(note) + '</div>' : '')
        + '</div>'
        + '<div class="cashbox-journal-operation-row__actor">' + escapeHtml(actorText) + '</div>'
        + '<div class="cashbox-journal-operation-row__amount" data-direction="transfer">' + escapeHtml(amount) + '</div>'
        + '</div>';
    }

    function cashJournalOperationHeaderHtml() {
      return '<div class="cashbox-journal-operation-head" aria-hidden="true">'
        + '<span>Время</span>'
        + '<span>Касса</span>'
        + '<span>Тип</span>'
        + '<span>Операция</span>'
        + '<span>Оператор</span>'
        + '<span>Сумма</span>'
        + '</div>';
    }

    function renderCashJournalRow(row) {
      if (row?.kind === 'transfer') return renderCashJournalTransferEntry(row.source, row.target);
      return renderCashJournalEntry(row?.item || {});
    }

    function renderCashJournalDay(day) {
      const entries = Array.isArray(day?.entries) ? day.entries : [];
      const rows = cashJournalDisplayRows(entries);
      return '<section class="cashbox-journal-day">'
        + '<div class="cashbox-journal-day-divider">'
        + '<div class="cashbox-journal-day-divider__title">' + escapeHtml(day?.label || day?.date || 'День') + '</div>'
        + '<div class="cashbox-journal-day-divider__meta" data-cash-journal-compact-day="' + escapeHtml(day?.key || day?.date || '') + '">' + cashJournalDayCompactSummaryHtml(day || {}) + '</div>'
        + '</div>'
        + '<div class="cashbox-journal-entries">'
        + (rows.length ? rows.map(renderCashJournalRow).join('') : '<div class="cashbox-journal-empty">Операций за день нет.</div>')
        + '</div>'
        + '</section>';
    }

    function cashJournalStatsRowHtml(item, periodKind = '') {
      const balanceMinor = finiteNumber(item?.balance_minor);
      const transferMinor = Math.max(Math.abs(finiteNumber(item?.transfer_income_minor)), Math.abs(finiteNumber(item?.transfer_expense_minor)));
      const labelText = String(item?.label || item?.key || 'Период');
      const incomeText = String(item?.external_income_display || cashboxFormatMinorAmount(item?.external_income_minor ?? 0));
      const expenseText = String(item?.external_expense_display || cashboxFormatMinorAmount(item?.external_expense_minor ?? 0));
      const balanceText = String(item?.balance_display || cashboxFormatMinorAmount(balanceMinor));
      const transferText = cashJournalTransferSummaryText(transferMinor, item?.count ?? 0);
      const periodKey = String(item?.key || item?.date || item?.week_key || item?.month_key || '').trim();
      const ariaText = labelText + ': приход ' + incomeText + ', расход ' + expenseText + ', итог ' + balanceText + ', перемещения ' + transferText;
      return '<button class="cashbox-journal-stats-row" type="button" data-cash-journal-period-kind="' + escapeHtml(periodKind) + '" data-cash-journal-period-key="' + escapeHtml(periodKey) + '" data-cash-journal-period-label="' + escapeHtml(labelText) + '" aria-label="' + escapeHtml(ariaText) + '">'
        + '<div class="cashbox-journal-stats-row__label">' + escapeHtml(labelText) + '</div>'
        + '<div class="cashbox-journal-stats-row__value">' + escapeHtml(incomeText) + '</div>'
        + '<div class="cashbox-journal-stats-row__value" data-balance-sign="negative">' + escapeHtml(expenseText) + '</div>'
        + '<div class="cashbox-journal-stats-row__value" data-balance-sign="' + escapeHtml(balanceMinor < 0 ? 'negative' : 'positive') + '">' + escapeHtml(balanceText) + '</div>'
        + '<div class="cashbox-journal-stats-row__meta" title="' + escapeHtml(transferText) + '">' + escapeHtml(transferText) + '</div>'
        + '</button>';
    }

    function cashJournalStatsHeaderHtml() {
      return '<div class="cashbox-journal-stats-head" aria-hidden="true">'
        + '<span>Период</span>'
        + '<span>Приход</span>'
        + '<span>Расход</span>'
        + '<span>Итог</span>'
        + '<span>Перемещения</span>'
        + '</div>';
    }

    function renderCashJournalStatsSection(title, items, periodKind) {
      const hasRows = Array.isArray(items) && items.length;
      const rows = hasRows
        ? cashJournalStatsHeaderHtml() + items.map((item) => cashJournalStatsRowHtml(item, periodKind)).join('')
        : '<div class="cashbox-journal-empty">Данных пока нет.</div>';
      return '<section class="cashbox-journal-stats-section">'
        + '<div class="cashbox-journal-stats-section__head">' + escapeHtml(title) + '</div>'
        + '<div class="cashbox-journal-stats-table">' + rows + '</div>'
        + '</section>';
    }

    function renderCashJournalStats(data) {
      const totals = data?.totals || {};
      const meta = data?.meta || {};
      const shownText = String(totals?.count ?? meta?.returned ?? 0) + ' из ' + String(meta?.total ?? totals?.count ?? 0);
      const balanceMinor = finiteNumber(totals?.balance_minor);
      const transferMinor = Math.max(Math.abs(finiteNumber(totals?.transfer_income_minor)), Math.abs(finiteNumber(totals?.transfer_expense_minor)));
      const summaryHtml = '<div class="cashbox-journal-summary">'
        + cashJournalStatHtml('Период', 'последние ' + String(finiteNonNegativeNumber(meta?.months, 3)) + ' мес.')
        + cashJournalStatHtml('Операции', shownText)
        + cashJournalStatHtml('Поступления', String(totals?.external_income_display || cashboxFormatMinorAmount(totals?.external_income_minor ?? 0)))
        + cashJournalStatHtml('Списания', String(totals?.external_expense_display || cashboxFormatMinorAmount(totals?.external_expense_minor ?? 0)), 'negative')
        + cashJournalStatHtml('Итог периода', String(totals?.balance_display || cashboxFormatMinorAmount(balanceMinor)), balanceMinor < 0 ? 'negative' : 'positive')
        + cashJournalStatHtml('Перемещения', cashJournalTransferSummaryText(transferMinor, totals?.count ?? meta?.returned ?? 0))
        + '</div>';
      return '<div class="cashbox-journal-view cashbox-journal-view--stats">'
        + summaryHtml
        + renderCashJournalStatsSection('По месяцам', data?.months || [], 'month')
        + renderCashJournalStatsSection('По неделям', data?.weeks || [], 'week')
        + renderCashJournalStatsSection('По дням', data?.days || [], 'day')
        + '</div>';
    }

    function cashJournalVisibleRowLimit() {
      const limit = finiteNonNegativeNumber(state.cashboxJournalVisibleRowLimit);
      return Math.max(CASH_JOURNAL_RENDER_BATCH_SIZE, Number.isFinite(limit) ? limit : CASH_JOURNAL_RENDER_BATCH_SIZE);
    }

    function cashJournalRowsToEntries(rows) {
      const entries = [];
      const usedIds = new Set();
      (Array.isArray(rows) ? rows : []).forEach((row) => {
        cashJournalRowItems(row).forEach((item) => {
          const id = String(item?.id || '');
          if (id && usedIds.has(id)) return;
          if (id) usedIds.add(id);
          entries.push(item);
        });
      });
      return entries;
    }

    function renderCashJournalLoadMore(renderedRowCount, filteredRowCount) {
      if (renderedRowCount >= filteredRowCount) return '';
      return '<button class="cashbox-journal-load-more" type="button" data-cash-journal-load-more>'
        + escapeHtml(cashJournalLoadMoreText(renderedRowCount, filteredRowCount))
        + '</button>';
    }

    function cashJournalLoadMoreText(renderedRowCount, filteredRowCount) {
      const remaining = Math.max(0, finiteNonNegativeNumber(filteredRowCount) - finiteNonNegativeNumber(renderedRowCount));
      return 'Показать еще ' + String(Math.min(CASH_JOURNAL_RENDER_BATCH_SIZE, remaining));
    }

    function cashJournalLedgerParts(data) {
      const totalRowCount = cashJournalDisplayRows(cashJournalFlatEntries(data)).length;
      const filteredEntries = cashJournalFilteredEntries(data);
      const filteredRows = cashJournalDisplayRows(filteredEntries);
      const rowLimit = cashJournalVisibleRowLimit();
      const renderedRows = filteredRows.slice(0, rowLimit);
      const renderedEntries = cashJournalRowsToEntries(renderedRows);
      const days = cashJournalRebuildDays(data, renderedEntries);
      const meta = data?.meta || {};
      const visibleRowCount = filteredRows.length;
      const renderedRowCount = renderedRows.length;
      const limitNotice = finiteNonNegativeNumber(meta?.total) > finiteNonNegativeNumber(meta?.returned)
        ? '<div class="cashbox-journal-status-line">Показана часть операций. Для полной выгрузки увеличьте лимит журнала.</div>'
        : '';
      const renderNotice = renderedRowCount < visibleRowCount
        ? '<div class="cashbox-journal-status-line">Показаны первые ' + escapeHtml(String(renderedRowCount)) + ' из ' + escapeHtml(String(visibleRowCount)) + ' операций. Фильтры и сводка считаются по загруженному журналу.</div>'
        : '';
      const headerHtml = renderedRowCount ? cashJournalOperationHeaderHtml() : '';
      const daysHtml = days.length
        ? days.map(renderCashJournalDay).join('')
        : '<div class="cashbox-journal-empty">По выбранным фильтрам движений нет.</div>';
      const loadMoreHtml = renderCashJournalLoadMore(renderedRowCount, visibleRowCount);
      return {
        visibleRowCount,
        renderedRowCount,
        totalRowCount,
        bodyHtml: headerHtml + daysHtml + loadMoreHtml + renderNotice + limitNotice,
      };
    }

    function renderCashJournalLedger(data) {
      const parts = cashJournalLedgerParts(data);
      return '<div class="cashbox-journal-view">'
        + '<div data-cash-journal-region="body">' + parts.bodyHtml + '</div>'
        + '</div>';
    }

    function renderCashJournal(data) {
      return state.cashboxJournalView === 'stats'
        ? renderCashJournalStats(data)
        : renderCashJournalLedger(data);
    }

    function refreshCashJournalView() {
      if (!state.cashboxJournalData) return;
      els.cashboxJournalText.innerHTML = renderCashJournal(state.cashboxJournalData);
    }

    function refreshCashJournalLedgerBody() {
      if (!state.cashboxJournalData || state.cashboxJournalView === 'stats') return false;
      const bodyRegion = els.cashboxJournalText.querySelector('[data-cash-journal-region="body"]');
      if (!(bodyRegion instanceof HTMLElement)) return false;
      const parts = cashJournalLedgerParts(state.cashboxJournalData);
      bodyRegion.innerHTML = parts.bodyHtml;
      return true;
    }

    function applyCashJournalStatsPeriodFilter(periodKind, periodKey, periodLabel) {
      const nextFilters = cashJournalFilters();
      nextFilters.period = 'all';
      nextFilters.periodKind = String(periodKind || '');
      nextFilters.periodKey = String(periodKey || '');
      nextFilters.periodLabel = String(periodLabel || periodKey || '');
      state.cashboxJournalFilters = nextFilters;
      state.cashboxJournalVisibleRowLimit = CASH_JOURNAL_RENDER_BATCH_SIZE;
      setCashJournalView('journal');
    }

    function handleCashJournalStatsPeriodClick(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const button = target.closest('[data-cash-journal-period-kind][data-cash-journal-period-key]');
      if (!(button instanceof HTMLElement)) return;
      const periodKind = String(button.dataset.cashJournalPeriodKind || '');
      const periodKey = String(button.dataset.cashJournalPeriodKey || '');
      if (!periodKind || !periodKey) return;
      event.preventDefault();
      applyCashJournalStatsPeriodFilter(periodKind, periodKey, String(button.dataset.cashJournalPeriodLabel || periodKey));
    }

    function handleCashJournalLoadMoreClick(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const button = target.closest('[data-cash-journal-load-more]');
      if (!(button instanceof HTMLElement)) return;
      event.preventDefault();
      state.cashboxJournalVisibleRowLimit = cashJournalVisibleRowLimit() + CASH_JOURNAL_RENDER_BATCH_SIZE;
      if (!refreshCashJournalLedgerBody()) refreshCashJournalView();
    }

    function syncCashJournalModeButtons() {
      const statsOpen = state.cashboxJournalView === 'stats';
      if (els.cashboxJournalLedgerButton) {
        els.cashboxJournalLedgerButton.classList.toggle('is-active', !statsOpen);
        els.cashboxJournalLedgerButton.setAttribute('aria-pressed', statsOpen ? 'false' : 'true');
      }
      if (els.cashboxJournalStatsButton) {
        els.cashboxJournalStatsButton.classList.toggle('is-active', statsOpen);
        els.cashboxJournalStatsButton.setAttribute('aria-pressed', statsOpen ? 'true' : 'false');
      }
    }

    function setCashJournalView(view) {
      state.cashboxJournalView = view === 'stats' ? 'stats' : 'journal';
      syncCashJournalModeButtons();
      refreshCashJournalView();
    }

    function handleCashJournalModeClick(event) {
      const target = event.currentTarget;
      if (!(target instanceof HTMLElement)) return;
      setCashJournalView(String(target.dataset.cashJournalView || 'journal'));
    }

    function handleCashJournalModeKeydown(event) {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      event.currentTarget?.click?.();
    }

    async function openCashJournalModal() {
      state.cashboxJournalFilters = cashJournalDefaultFilters();
      state.cashboxJournalVisibleRowLimit = CASH_JOURNAL_RENDER_BATCH_SIZE;
      syncCashJournalModeButtons();
      els.cashboxJournalText.innerHTML = renderCashJournalLoading();
      maybeOpenModal(els.cashboxJournalModal, true);
      try {
        const data = await loadCashJournalData();
        state.cashboxJournalData = data;
        els.cashboxJournalText.innerHTML = renderCashJournal(data);
      } catch (error) {
        els.cashboxJournalText.innerHTML = '<div class="cashbox-journal-empty">' + escapeHtml(String(error?.message || 'НЕ УДАЛОСЬ ЗАГРУЗИТЬ ЖУРНАЛ.')) + '</div>';
        setStatus(String(error?.message || 'НЕ УДАЛОСЬ ЗАГРУЗИТЬ ЖУРНАЛ.'), true);
      }
    }

    function closeCashJournalModal() {
      popModal('cashbox-journal');
    }

    async function downloadCashJournal() {
      try {
        const data = await loadCashJournalData({ includeMarkdown: true });
        const text = String(data?.markdown || data?.text || 'ЗА ВЫБРАННЫЙ ПЕРИОД ДВИЖЕНИЙ НЕТ.');
        const blob = new Blob([text.trim() + '\n'], { type: 'text/markdown;charset=utf-8' });
        const fileName = 'cash-journal-' + new Date().toISOString().slice(0, 10) + '.md';
        triggerBlobDownload(blob, fileName);
        setStatus('ЖУРНАЛ СКАЧАН.', false);
      } catch (error) {
        setStatus(String(error?.message || 'НЕ УДАЛОСЬ СКАЧАТЬ ЖУРНАЛ.'), true);
      }
    }
