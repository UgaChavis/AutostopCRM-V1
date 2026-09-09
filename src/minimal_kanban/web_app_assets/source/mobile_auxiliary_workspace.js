    function renderMobileArchiveRows(cards) {
      return cards.map((card) => {
        const heading = cardHeading(card);
        const compactDescription = stripDescriptionFormatting(card?.description || card?.description_preview || 'Описание не указано').replace(/\s+/g, ' ').trim();
        const summary = compactDescription.length > 160 ? compactDescription.slice(0, 157) + '...' : compactDescription;
        const metaParts = [
          card?.updated_at ? ('АРХИВ: ' + formatDate(card.updated_at)) : '',
          card?.column ? ('КОЛОНКА: ' + columnLabelById(card.column)) : '',
        ].filter(Boolean);
        return '<article class="mobile-archive-row" data-mobile-archive-card="' + escapeHtml(card?.id || '') + '">'
          + '<div class="mobile-archive-row__top">'
            + '<div class="mobile-archive-row__title">' + escapeHtml(heading) + '</div>'
          + '</div>'
          + '<div class="mobile-archive-row__summary">' + escapeHtml(summary || 'Описание не указано') + '</div>'
          + '<div class="mobile-archive-row__meta">' + escapeHtml(metaParts.join(' · ') || 'АРХИВНАЯ КАРТОЧКА') + '</div>'
          + '<div class="mobile-archive-row__actions">'
            + '<button class="mobile-action mobile-action--primary" type="button" data-mobile-archive-restore="' + escapeHtml(card?.id || '') + '">ВЕРНУТЬ</button>'
          + '</div>'
        + '</article>';
      }).join('');
    }

    function renderMobileArchivePanel() {
      const isOpen = state.mobileMorePanel === 'archive';
      if (els.mobileArchivePanel) els.mobileArchivePanel.hidden = !isOpen;
      syncMobileMorePanelChrome();
      if (!isOpen) return;
      if (els.mobileArchiveSearchInput && els.mobileArchiveSearchInput.value !== String(state.archiveQuery || '')) {
        els.mobileArchiveSearchInput.value = String(state.archiveQuery || '');
      }
      const total = archivedCardsTotal();
      const cards = filteredArchiveCards();
      if (els.mobileArchiveMeta) {
        if (state.mobileArchiveLoading || state.archiveLoading) {
          els.mobileArchiveMeta.textContent = 'ЗАГРУЗКА...';
        } else if (String(state.archiveQuery || '').trim()) {
          els.mobileArchiveMeta.textContent = 'НАЙДЕНО: ' + String(cards.length) + ' ИЗ ' + String(total);
        } else {
          els.mobileArchiveMeta.textContent = cards.length ? ('ПОКАЗАНО: ' + String(cards.length) + ' ИЗ ' + String(total)) : 'АРХИВНЫХ КАРТОЧЕК ПОКА НЕТ';
        }
      }
      if (!els.mobileArchiveList) return;
      if (state.mobileArchiveLoading && !cards.length) {
        els.mobileArchiveList.innerHTML = '<div class="mobile-archive-empty">ЗАГРУЗКА АРХИВА...</div>';
      } else if (!cards.length) {
        els.mobileArchiveList.innerHTML = '<div class="mobile-archive-empty">ПО ДАННОМУ ПОИСКУ НИЧЕГО НЕ НАЙДЕНО.</div>';
      } else {
        els.mobileArchiveList.innerHTML = renderMobileArchiveRows(cards);
      }
    }

    async function loadMobileArchive({ force = false } = {}) {
      if (!force && state.archiveLoaded) {
        renderMobileArchivePanel();
        return;
      }
      const viewerStateGeneration = state.viewerStateGeneration;
      const operatorSessionToken = state.operatorSessionToken;
      const isCurrent = () => state.viewerStateGeneration === viewerStateGeneration
        && state.operatorSessionToken === operatorSessionToken;
      state.mobileArchiveLoading = true;
      renderMobileArchivePanel();
      try {
        await loadArchive(false, { force });
      } finally {
        if (isCurrent()) {
          state.mobileArchiveLoading = false;
          renderMobileArchivePanel();
          renderMobileMoreModules();
        }
      }
    }

    function openMobileArchivePanel() {
      state.mobileMorePanel = 'archive';
      renderMobileMore();
      loadMobileArchive();
    }

    function handleMobileArchiveInput() {
      state.archiveQuery = String(els.mobileArchiveSearchInput?.value || '').trim();
      window.clearTimeout(state.mobileArchiveSearchTimer);
      state.mobileArchiveSearchTimer = window.setTimeout(() => renderMobileArchivePanel(), 80);
      renderMobileArchivePanel();
    }

    async function restoreMobileArchiveCard(cardId) {
      const normalizedId = String(cardId || '').trim();
      if (!normalizedId || state.mobileArchiveRestoreRequest) return;
      const request = {};
      state.mobileArchiveRestoreRequest = request;
      const viewerStateGeneration = state.viewerStateGeneration;
      const operatorSessionToken = state.operatorSessionToken;
      const actorName = state.actor;
      const isCurrent = () => state.mobileArchiveRestoreRequest === request
        && state.viewerStateGeneration === viewerStateGeneration
        && state.operatorSessionToken === operatorSessionToken;
      state.mobileArchiveLoading = true;
      renderMobileArchivePanel();
      try {
        const data = await api('/api/restore_card', {
          method: 'POST',
          body: { card_id: normalizedId, actor_name: actorName, source: 'ui' },
        });
        if (!isCurrent()) return null;
        const restoredId = String(data?.card?.id || normalizedId).trim();
        const patched = data?.card ? applyArchivedCardPatch(data.card) : false;
        if (!patched) {
          state.archiveCards = (Array.isArray(state.archiveCards) ? state.archiveCards : []).filter((card) => String(card?.id || '') !== normalizedId);
          await refreshSnapshot(true);
          if (!isCurrent()) return null;
        }
        await loadArchive(false, { force: true });
        if (!isCurrent()) return null;
        state.mobileArchiveLoading = false;
        renderMobileArchivePanel();
        renderMobileMoreModules();
        setStatus('КАРТОЧКА ВОССТАНОВЛЕНА.', false);
        if (restoredId) {
          state.mobileMorePanel = '';
          setMobileView('board');
          await openMobileCardDetail(restoredId);
        }
        return data;
      } catch (error) {
        if (isCurrent()) setStatus(error.message, true);
        return null;
      } finally {
        if (state.mobileArchiveRestoreRequest === request
            && state.viewerStateGeneration === viewerStateGeneration
            && state.operatorSessionToken === operatorSessionToken) {
          state.mobileArchiveRestoreRequest = null;
          state.mobileArchiveLoading = false;
          renderMobileArchivePanel();
          renderMobileMoreModules();
        }
      }
    }

    function handleMobileArchiveClick(event) {
      const button = event.target instanceof HTMLElement ? event.target.closest('[data-mobile-archive-restore]') : null;
      if (!button || !els.mobileArchivePanel?.contains(button)) return;
      event.preventDefault();
      restoreMobileArchiveCard(button.getAttribute('data-mobile-archive-restore'));
    }

    function mobileSharedFilesMetaText(files) {
      const storage = state.sharedFilesStorage || {};
      const total = Array.isArray(files) ? files.length : 0;
      if (storage.limit_bytes) {
        return formatBytes(storage.used_bytes ?? 0) + ' / ' + formatBytes(storage.limit_bytes ?? 0) + ' · ' + total + ' ФАЙЛ.';
      }
      return total ? (total + ' ФАЙЛ.') : 'ФАЙЛОВ ПОКА НЕТ';
    }

    function renderMobileSharedFilesRows(files) {
      return files.map((file) => {
        const fileId = String(file?.id || '').trim();
        const isActive = fileId === String(state.sharedFilesActiveId || '');
        const isRenaming = fileId === String(state.mobileSharedFileRenamingId || '');
        const metaText = sharedFileMetaParts(file).join(' · ') || 'ФАЙЛ';
        return '<article class="mobile-shared-file-row' + (isActive ? ' is-active' : '') + '" data-mobile-shared-file-id="' + escapeHtml(fileId) + '">'
          + '<div class="mobile-shared-file-row__top">'
            + '<div class="mobile-shared-file-row__name">' + escapeHtml(file?.original_name || 'Файл') + '</div>'
            + '<div class="mobile-shared-file-row__kind">' + escapeHtml(sharedFileKindLabel(file)) + '</div>'
          + '</div>'
          + '<div class="mobile-shared-file-row__meta">' + escapeHtml(metaText) + '</div>'
          + '<div class="mobile-shared-file-row__actions">'
            + '<button class="mobile-action mobile-action--ghost" type="button" data-mobile-shared-file-action="open" data-mobile-shared-file-id="' + escapeHtml(fileId) + '">ОТКРЫТЬ</button>'
            + '<button class="mobile-action mobile-action--ghost" type="button" data-mobile-shared-file-action="download" data-mobile-shared-file-id="' + escapeHtml(fileId) + '">СКАЧАТЬ</button>'
            + '<button class="mobile-action mobile-action--ghost" type="button" data-mobile-shared-file-action="rename" data-mobile-shared-file-id="' + escapeHtml(fileId) + '">' + (isRenaming ? '...' : 'ПЕРЕИМ.') + '</button>'
            + '<button class="mobile-action mobile-action--expense" type="button" data-mobile-shared-file-action="delete" data-mobile-shared-file-id="' + escapeHtml(fileId) + '">УДАЛИТЬ</button>'
          + '</div>'
        + '</article>';
      }).join('');
    }

    function renderMobileSharedFilesPanel() {
      const isOpen = state.mobileMorePanel === 'files';
      if (els.mobileSharedFilesPanel) els.mobileSharedFilesPanel.hidden = !isOpen;
      syncMobileMorePanelChrome();
      if (!isOpen) return;
      const files = Array.isArray(state.sharedFiles) ? state.sharedFiles : [];
      if (els.mobileSharedFilesUploadButton) {
        els.mobileSharedFilesUploadButton.disabled = Boolean(state.mobileSharedFilesLoading);
        els.mobileSharedFilesUploadButton.textContent = state.mobileSharedFilesLoading ? '...' : 'ЗАГРУЗИТЬ';
      }
      if (els.mobileSharedFilesMeta) {
        els.mobileSharedFilesMeta.textContent = state.mobileSharedFilesLoading ? 'ЗАГРУЗКА...' : mobileSharedFilesMetaText(files);
      }
      if (!els.mobileSharedFilesList) return;
      if (state.mobileSharedFilesLoading && !files.length) {
        els.mobileSharedFilesList.innerHTML = '<div class="mobile-shared-file-empty">ЗАГРУЗКА ФАЙЛОВ...</div>';
      } else if (!files.length) {
        els.mobileSharedFilesList.innerHTML = '<div class="mobile-shared-file-empty">ОБЩИХ ФАЙЛОВ ПОКА НЕТ.</div>';
      } else {
        els.mobileSharedFilesList.innerHTML = renderMobileSharedFilesRows(files);
      }
    }

    async function loadMobileSharedFiles({ force = false } = {}) {
      void force;
      const request = {};
      state.mobileSharedFilesRequest = request;
      const viewerStateGeneration = state.viewerStateGeneration;
      const operatorSessionToken = state.operatorSessionToken;
      const isCurrent = () => state.mobileSharedFilesRequest === request
        && state.viewerStateGeneration === viewerStateGeneration
        && state.operatorSessionToken === operatorSessionToken;
      state.mobileSharedFilesLoading = true;
      renderMobileSharedFilesPanel();
      try {
        await loadSharedFiles({ openModal: false });
      } finally {
        if (isCurrent()) {
          state.mobileSharedFilesRequest = null;
          state.mobileSharedFilesLoading = false;
          renderMobileSharedFilesPanel();
          renderMobileMoreModules();
        }
      }
    }

    function openMobileSharedFilesPanel() {
      state.mobileMorePanel = 'files';
      renderMobileMore();
      loadMobileSharedFiles();
    }

    function selectMobileSharedFile(fileId) {
      const normalizedId = String(fileId || '').trim();
      if (!normalizedId) return null;
      selectSharedFile(normalizedId);
      renderMobileSharedFilesPanel();
      return sharedFileById(normalizedId);
    }

    function openMobileSharedFile(fileId) {
      const file = selectMobileSharedFile(fileId);
      if (!file) return;
      window.open(sharedFileDownloadUrl(file, { inline: true }), '_blank', 'noopener');
    }

    async function downloadMobileSharedFile(fileId) {
      const file = selectMobileSharedFile(fileId);
      if (!file) return;
      await downloadActiveSharedFile();
      renderMobileSharedFilesPanel();
    }

    async function renameMobileSharedFile(fileId) {
      const file = selectMobileSharedFile(fileId);
      if (!file) return;
      const viewerStateGeneration = state.viewerStateGeneration;
      const operatorSessionToken = state.operatorSessionToken;
      const isCurrent = () => state.viewerStateGeneration === viewerStateGeneration
        && state.operatorSessionToken === operatorSessionToken
        && state.mobileSharedFileRenamingId === file.id;
      state.mobileSharedFileRenamingId = file.id;
      renderMobileSharedFilesPanel();
      try {
        await renameActiveSharedFile();
      } finally {
        if (isCurrent()) {
          state.mobileSharedFileRenamingId = '';
          renderMobileSharedFilesPanel();
          renderMobileMoreModules();
        }
      }
    }

    async function deleteMobileSharedFile(fileId) {
      const file = selectMobileSharedFile(fileId);
      if (!file) return;
      const viewerStateGeneration = state.viewerStateGeneration;
      const operatorSessionToken = state.operatorSessionToken;
      try {
        await deleteActiveSharedFile();
      } finally {
        if (state.viewerStateGeneration === viewerStateGeneration
            && state.operatorSessionToken === operatorSessionToken) {
          renderMobileSharedFilesPanel();
          renderMobileMoreModules();
        }
      }
    }

    async function uploadMobileSharedFiles() {
      const files = Array.from(els.mobileSharedFilesInput?.files || []).filter(Boolean);
      if (!files.length || state.mobileSharedFilesRequest) return;
      const request = {};
      state.mobileSharedFilesRequest = request;
      const viewerStateGeneration = state.viewerStateGeneration;
      const operatorSessionToken = state.operatorSessionToken;
      const isCurrent = () => state.mobileSharedFilesRequest === request
        && state.viewerStateGeneration === viewerStateGeneration
        && state.operatorSessionToken === operatorSessionToken;
      state.mobileSharedFilesLoading = true;
      renderMobileSharedFilesPanel();
      try {
        await uploadSharedFiles(files, { dropPoint: null });
      } finally {
        if (isCurrent()) {
          state.mobileSharedFilesRequest = null;
          if (els.mobileSharedFilesInput) els.mobileSharedFilesInput.value = '';
          state.mobileSharedFilesLoading = false;
          renderMobileSharedFilesPanel();
          renderMobileMoreModules();
        }
      }
    }

    function handleMobileSharedFilesClick(event) {
      const button = event.target instanceof HTMLElement ? event.target.closest('[data-mobile-shared-file-action]') : null;
      if (!button || !els.mobileSharedFilesPanel?.contains(button)) return;
      event.preventDefault();
      const action = String(button.getAttribute('data-mobile-shared-file-action') || '').trim();
      const fileId = String(button.getAttribute('data-mobile-shared-file-id') || button.closest('[data-mobile-shared-file-id]')?.getAttribute('data-mobile-shared-file-id') || '').trim();
      if (action === 'open') return openMobileSharedFile(fileId);
      if (action === 'download') return downloadMobileSharedFile(fileId);
      if (action === 'rename') return renameMobileSharedFile(fileId);
      if (action === 'delete') return deleteMobileSharedFile(fileId);
      return null;
    }
