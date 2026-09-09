    function sharedFileById(fileId) {
      const normalizedId = String(fileId || '').trim();
      return (state.sharedFiles || []).find((item) => item.id === normalizedId) || null;
    }

    function activeSharedFile() {
      return sharedFileById(state.sharedFilesActiveId);
    }

    function beginSharedFilesMutation({ fileId = '', clipboardId = '' } = {}) {
      if (state.sharedFilesMutationRequest) return null;
      const request = {};
      state.sharedFilesMutationRequest = request;
      const viewerStateGeneration = state.viewerStateGeneration;
      const operatorSessionToken = state.operatorSessionToken;
      const normalizedFileId = String(fileId || '').trim();
      const normalizedClipboardId = String(clipboardId || '').trim();
      let requestOwnedActiveId = String(state.sharedFilesActiveId || '').trim();
      const ownsRequest = () => state.sharedFilesMutationRequest === request
        && state.viewerStateGeneration === viewerStateGeneration
        && state.operatorSessionToken === operatorSessionToken;
      return {
        request,
        actorName: state.actor,
        fileId: normalizedFileId,
        clipboardId: normalizedClipboardId,
        ownsRequest,
        selectCreatedFile: (createdFileId) => {
          const normalizedCreatedFileId = String(createdFileId || '').trim();
          if (!normalizedCreatedFileId || !ownsRequest()) return false;
          if (String(state.sharedFilesActiveId || '').trim() !== requestOwnedActiveId) return false;
          state.sharedFilesActiveId = normalizedCreatedFileId;
          requestOwnedActiveId = normalizedCreatedFileId;
          return true;
        },
        isCurrent: () => ownsRequest()
          && (!normalizedFileId || String(state.sharedFilesActiveId || '').trim() === normalizedFileId)
          && (!normalizedClipboardId || String(state.sharedFilesClipboardId || '').trim() === normalizedClipboardId),
      };
    }

    function finishSharedFilesMutation(context) {
      if (!context?.ownsRequest()) return false;
      state.sharedFilesMutationRequest = null;
      updateSharedFilesActions();
      return true;
    }

    function sharedFileKindLabel(file) {
      const extension = String(file?.extension || '').replace('.', '').toUpperCase();
      if (!extension) return 'FILE';
      if (['JPG', 'JPEG', 'PNG', 'WEBP', 'GIF'].includes(extension)) return 'IMG';
      if (extension.length > 5) return extension.slice(0, 5);
      return extension;
    }

    function sharedFileDownloadUrl(file, { inline = false } = {}) {
      if (!file?.id) return '#';
      const path = '/api/shared_file?file_id=' + encodeURIComponent(file.id) + (inline ? '&disposition=inline' : '');
      return withAccessToken(path);
    }

    function sharedFileMetaParts(file) {
      return [
        sharedFileKindLabel(file),
        formatBytes(file?.size_bytes ?? 0),
        file?.updated_at ? formatDate(file.updated_at) : '',
      ].filter(Boolean);
    }

    function updateSharedFilesActions() {
      const busy = Boolean(state.sharedFilesMutationRequest);
      const hasActive = Boolean(activeSharedFile()) && !busy;
      [
        els.sharedFilesOpenButton,
        els.sharedFilesDownloadButton,
        els.sharedFilesRenameButton,
        els.sharedFilesCopyButton,
        els.sharedFilesDeleteButton,
      ].forEach((button) => {
        if (button) button.disabled = !hasActive;
      });
      if (els.sharedFilesPasteButton) {
        els.sharedFilesPasteButton.disabled = busy || !state.sharedFilesClipboardId;
      }
      const crmPasteButton = els.sharedFilesContextMenu?.querySelector('[data-shared-files-menu-action="paste-crm"]');
      if (crmPasteButton instanceof HTMLButtonElement) {
        crmPasteButton.disabled = busy || !state.sharedFilesClipboardId;
      }
    }

    function renderSharedFiles() {
      const files = Array.isArray(state.sharedFiles) ? state.sharedFiles : [];
      if (!els.sharedFilesDesktop) return;
      const laidOutFiles = sharedFilesLayout(files);
      if (!files.length) {
        els.sharedFilesDesktop.innerHTML = '<div class="shared-files-empty">ФАЙЛОВ ПОКА НЕТ.</div>';
      } else {
        els.sharedFilesDesktop.innerHTML = laidOutFiles.map((file) => {
          const x = finiteNonNegativeNumber(file.x);
          const y = finiteNonNegativeNumber(file.y);
          const activeClass = file.id === state.sharedFilesActiveId ? ' is-active' : '';
          const name = String(file.original_name || 'Файл');
          const metaParts = sharedFileMetaParts(file);
          const metaText = metaParts.join(' · ');
          return '<button class="shared-file-icon' + activeClass + '" type="button" data-shared-file-id="' + escapeHtml(file.id) + '" style="left:' + x + 'px; top:' + y + 'px" title="' + escapeHtml(name) + '" aria-label="Файл ' + escapeHtml(name + (metaText ? '. ' + metaText : '')) + '">'
            + '<span class="shared-file-icon__glyph">' + escapeHtml(sharedFileKindLabel(file)) + '</span>'
            + '<span class="shared-file-icon__name">' + escapeHtml(name) + '</span>'
            + '<span class="shared-file-icon__meta">' + metaParts.map((item) => '<span class="shared-file-icon__meta-chip">' + escapeHtml(item) + '</span>').join('') + '</span>'
            + '</button>';
        }).join('');
      }
      const storage = state.sharedFilesStorage || {};
      if (els.sharedFilesMeta) {
        els.sharedFilesMeta.textContent = formatBytes(storage.used_bytes ?? 0) + ' / ' + formatBytes(storage.limit_bytes ?? 0) + ' · ' + files.length + ' ФАЙЛ.';
      }
      updateSharedFilesActions();
    }

    function updateSharedFilesSelection() {
      if (!els.sharedFilesDesktop) return;
      const activeId = String(state.sharedFilesActiveId || '').trim();
      for (const icon of els.sharedFilesDesktop.querySelectorAll('[data-shared-file-id]')) {
        if (!(icon instanceof HTMLElement)) continue;
        icon.classList.toggle('is-active', String(icon.dataset.sharedFileId || '') === activeId);
      }
    }

    async function loadSharedFiles({ openModal = false } = {}) {
      const requestSeq = (state.sharedFilesRequestSeq || 0) + 1;
      state.sharedFilesRequestSeq = requestSeq;
      const viewerStateGeneration = state.viewerStateGeneration;
      const operatorSessionToken = state.operatorSessionToken;
      const isCurrent = () => state.sharedFilesRequestSeq === requestSeq
        && state.viewerStateGeneration === viewerStateGeneration
        && state.operatorSessionToken === operatorSessionToken;
      try {
        const data = await api('/api/list_shared_files');
        if (!isCurrent()) return null;
        state.sharedFiles = Array.isArray(data?.files) ? data.files : [];
        state.sharedFilesStorage = data?.storage || null;
        if (state.sharedFilesActiveId && !sharedFileById(state.sharedFilesActiveId)) {
          state.sharedFilesActiveId = '';
        }
        renderSharedFiles();
        maybeOpenModal(els.sharedFilesModal, openModal);
        return data;
      } catch (error) {
        if (!isCurrent()) return null;
        if (els.sharedFilesMeta) els.sharedFilesMeta.textContent = error.message;
        maybeOpenModal(els.sharedFilesModal, openModal);
        setStatus(error.message, true);
        return null;
      }
    }

    async function openSharedFilesModal() {
      await loadSharedFiles({ openModal: true });
      els.sharedFilesDesktop?.focus?.({ preventScroll: true });
    }

    function selectSharedFile(fileId) {
      state.sharedFilesActiveId = String(fileId || '').trim();
      updateSharedFilesSelection();
      updateSharedFilesActions();
    }

    function normalizeSharedFilesDropPoint(point) {
      const x = finiteNumber(point?.x, NaN);
      const y = finiteNumber(point?.y, NaN);
      if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
      return { x: Math.max(0, Math.round(x)), y: Math.max(0, Math.round(y)) };
    }

    function sharedFilesDropPointFromEvent(event) {
      if (!els.sharedFilesDesktop) return null;
      const rect = els.sharedFilesDesktop.getBoundingClientRect();
      return normalizeSharedFilesDropPoint({
        x: event.clientX - rect.left + els.sharedFilesDesktop.scrollLeft,
        y: event.clientY - rect.top + els.sharedFilesDesktop.scrollTop,
      });
    }

    function sharedFilesUploadPoint(index, baseIndex, dropPoint) {
      const point = normalizeSharedFilesDropPoint(dropPoint);
      if (!point) {
        return sharedFilesGridPointFromSlot(baseIndex + index);
      }
      return sharedFilesGridPointFromSlot(sharedFilesGridSlotFromPoint(point.x, point.y) + index);
    }

    function sharedFilesGridSlotFromPoint(x, y) {
      const col = Math.max(0, Math.round((finiteNumber(x) - 24) / 116));
      const row = Math.max(0, Math.round((finiteNumber(y) - 24) / 126));
      return row * 7 + col;
    }

    function sharedFilesGridPointFromSlot(slot) {
      const index = Math.max(0, Math.floor(finiteNumber(slot)));
      return {
        x: 24 + (index % 7) * 116,
        y: 24 + Math.floor(index / 7) * 126,
      };
    }

    function sharedFilesSnapPointToGrid(x, y) {
      return sharedFilesGridPointFromSlot(sharedFilesGridSlotFromPoint(x, y));
    }

    function sharedFilesLayout(files) {
      const orderedFiles = Array.from(files || []).sort((left, right) => {
        const leftSlot = sharedFilesStoredSlot(left);
        const rightSlot = sharedFilesStoredSlot(right);
        if (leftSlot !== rightSlot) return leftSlot - rightSlot;
        const leftTime = sharedFilesStableTime(left);
        const rightTime = sharedFilesStableTime(right);
        if (leftTime !== rightTime) return leftTime - rightTime;
        return String(left?.id || '').localeCompare(String(right?.id || ''));
      });
      const occupied = new Set();
      return orderedFiles.map((file, index) => {
        const storedX = finiteNumber(file?.x, NaN);
        const storedY = finiteNumber(file?.y, NaN);
        const hasStoredPosition = Number.isFinite(storedX) && Number.isFinite(storedY);
        let slot = hasStoredPosition ? sharedFilesGridSlotFromPoint(file.x, file.y) : index;
        while (occupied.has(slot)) slot += 1;
        occupied.add(slot);
        const point = sharedFilesGridPointFromSlot(slot);
        return {
          ...file,
          x: point.x,
          y: point.y,
          shared_files_layout_slot: slot,
        };
      });
    }

    function sharedFilesStoredSlot(file) {
      if (!file) return Number.MAX_SAFE_INTEGER;
      const x = finiteNumber(file.x, NaN);
      const y = finiteNumber(file.y, NaN);
      if (!Number.isFinite(x) || !Number.isFinite(y)) return Number.MAX_SAFE_INTEGER;
      return sharedFilesGridSlotFromPoint(x, y);
    }

    function sharedFilesStableTime(file) {
      const createdAt = Date.parse(String(file?.created_at || ''));
      if (Number.isFinite(createdAt)) return createdAt;
      const updatedAt = Date.parse(String(file?.updated_at || ''));
      if (Number.isFinite(updatedAt)) return updatedAt;
      return 0;
    }

    function sharedFilesClipboardFileName(mimeType, index = 0) {
      const normalizedMime = normalizeAttachmentMimeType(mimeType);
      const extension = ATTACHMENT_MIME_TO_EXTENSION[normalizedMime] || '.bin';
      const prefix = normalizedMime.startsWith('image/') ? 'clipboard-image' : 'clipboard-file';
      return clipboardAttachmentName(index > 0 ? prefix + '-' + (index + 1) : prefix, extension);
    }

    function sharedFilesFileNameForUpload(file, index) {
      const fileName = String(file?.name || '').trim();
      return fileName || sharedFilesClipboardFileName(file?.type || '', index);
    }

    function sharedFilesClipboardTypeLooksFile(mimeType) {
      const normalizedMime = normalizeAttachmentMimeType(mimeType);
      if (!normalizedMime) return false;
      return !normalizedMime.startsWith('text/');
    }

    async function readSharedFilesClipboardFiles(isCurrent = () => true) {
      if (!navigator.clipboard?.read) {
        throw new Error('БРАУЗЕР НЕ ДАЁТ ПРОЧИТАТЬ ФАЙЛ ИЗ БУФЕРА. ИСПОЛЬЗУЙТЕ CTRL+V ИЛИ ПЕРЕТАСКИВАНИЕ.');
      }
      const items = await navigator.clipboard.read();
      if (!isCurrent()) return [];
      const files = [];
      for (const item of items || []) {
        const types = Array.from(item.types || []);
        for (const type of types) {
          if (!sharedFilesClipboardTypeLooksFile(type)) continue;
          const blob = await item.getType(type);
          if (!isCurrent()) return [];
          if (!blob || !blob.size) continue;
          const mimeType = normalizeAttachmentMimeType(blob.type || type) || 'application/octet-stream';
          files.push(new File([blob], sharedFilesClipboardFileName(mimeType, files.length), { type: mimeType, lastModified: Date.now() }));
          break;
        }
      }
      return files;
    }

    function filesFromSharedFilesPasteEvent(event) {
      const clipboardData = event?.clipboardData;
      const directFiles = Array.from(clipboardData?.files || []).filter(Boolean);
      if (directFiles.length) return directFiles;
      const files = [];
      Array.from(clipboardData?.items || []).forEach((item) => {
        if (item.kind !== 'file') return;
        const file = item.getAsFile();
        if (file) files.push(file);
      });
      return files;
    }

    function sharedFilesClipboardFallbackAllowed(error) {
      const code = String(error?.code || '').trim();
      return code === 'clipboard_empty' || code === 'clipboard_unavailable';
    }

    async function pasteSharedFilesFromLocalClipboard(dropPoint, context) {
      const point = normalizeSharedFilesDropPoint(dropPoint);
      const pasted = await api('/api/paste_shared_files_from_clipboard', {
        method: 'POST',
        body: {
          actor_name: context.actorName,
          source: 'ui',
          x: point?.x ?? 24,
          y: point?.y ?? 24,
        },
      });
      if (!context.isCurrent()) return null;
      const pastedFiles = Array.isArray(pasted?.files) ? pasted.files : [];
      if (!pastedFiles.length) return false;
      context.selectCreatedFile(pastedFiles[pastedFiles.length - 1]?.id);
      await loadSharedFiles();
      if (!context.isCurrent()) return null;
      setStatus(pastedFiles.length > 1 ? 'ФАЙЛЫ ВСТАВЛЕНЫ ИЗ БУФЕРА.' : 'ФАЙЛ ВСТАВЛЕН ИЗ БУФЕРА.', false);
      return true;
    }

    async function pasteSharedFilesFromSystemClipboard() {
      const dropPoint = state.sharedFilesContextPoint || null;
      const context = beginSharedFilesMutation();
      if (!context) return;
      try {
        let localClipboardError = null;
        try {
          if (await pasteSharedFilesFromLocalClipboard(dropPoint, context)) {
            if (!context.isCurrent()) return;
            hideSharedFilesContextMenu();
            return;
          }
        } catch (error) {
          if (!context.isCurrent()) return;
          localClipboardError = error;
          if (!sharedFilesClipboardFallbackAllowed(error)) {
            const reconciled = Boolean(await loadSharedFiles());
            if (context.isCurrent()) setStatus(
              (error.message || 'НЕ УДАЛОСЬ ВСТАВИТЬ ФАЙЛ ИЗ БУФЕРА.')
                + (reconciled
                  ? ' СПИСОК ОБНОВЛЕН; ПРОВЕРЬТЕ РЕЗУЛЬТАТ ПЕРЕД ПОВТОРОМ.'
                  : ' РЕЗУЛЬТАТ НЕ ОПРЕДЕЛЕН. НЕ ПОВТОРЯЙТЕ ОПЕРАЦИЮ ДО ОБНОВЛЕНИЯ СПИСКА.'),
              true,
            );
            return;
          }
        }
        try {
          if (!context.isCurrent()) return;
          const files = await readSharedFilesClipboardFiles(context.isCurrent);
          if (!context.isCurrent()) return;
          if (!files.length) {
            setStatus(localClipboardError?.message || 'В БУФЕРЕ НЕТ ФАЙЛА ДЛЯ ВСТАВКИ. ИСПОЛЬЗУЙТЕ CTRL+V ИЛИ ПЕРЕТАСКИВАНИЕ.', true);
            return;
          }
          await uploadSharedFiles(files, { dropPoint, mutationContext: context });
          if (!context.isCurrent()) return;
          hideSharedFilesContextMenu();
        } catch (error) {
          if (context.isCurrent()) setStatus(localClipboardError?.message || error.message || 'НЕ УДАЛОСЬ ВСТАВИТЬ ФАЙЛ ИЗ БУФЕРА.', true);
        }
      } finally {
        finishSharedFilesMutation(context);
      }
    }

    async function handleSharedFilesPaste(event) {
      if (!els.sharedFilesModal?.classList.contains('is-open')) return;
      const files = filesFromSharedFilesPasteEvent(event);
      if (!files.length) return;
      event.preventDefault();
      event.stopPropagation();
      await uploadSharedFiles(files, { dropPoint: state.sharedFilesContextPoint || null });
      hideSharedFilesContextMenu();
    }

    function hideSharedFilesContextMenu() {
      if (els.sharedFilesContextMenu) {
        els.sharedFilesContextMenu.hidden = true;
        els.sharedFilesContextMenu.style.left = '';
        els.sharedFilesContextMenu.style.top = '';
      }
      state.sharedFilesContextPoint = null;
    }

    function positionSharedFilesContextMenu(clientX, clientY) {
      if (!els.sharedFilesContextMenu) return;
      const padding = 8;
      els.sharedFilesContextMenu.style.left = Math.max(padding, Math.round(clientX)) + 'px';
      els.sharedFilesContextMenu.style.top = Math.max(padding, Math.round(clientY)) + 'px';
      window.requestAnimationFrame(() => {
        const rect = els.sharedFilesContextMenu.getBoundingClientRect();
        const maxLeft = Math.max(padding, window.innerWidth - rect.width - padding);
        const maxTop = Math.max(padding, window.innerHeight - rect.height - padding);
        els.sharedFilesContextMenu.style.left = Math.min(Math.max(padding, Math.round(clientX)), maxLeft) + 'px';
        els.sharedFilesContextMenu.style.top = Math.min(Math.max(padding, Math.round(clientY)), maxTop) + 'px';
      });
    }

    function handleSharedFilesContextMenu(event) {
      if (!els.sharedFilesDesktop?.contains(event.target)) return;
      event.preventDefault();
      event.stopPropagation();
      const icon = event.target?.closest?.('[data-shared-file-id]');
      if (icon instanceof HTMLElement) selectSharedFile(icon.dataset.sharedFileId);
      state.sharedFilesContextPoint = sharedFilesDropPointFromEvent(event);
      updateSharedFilesActions();
      if (els.sharedFilesContextMenu) {
        els.sharedFilesContextMenu.hidden = false;
        positionSharedFilesContextMenu(event.clientX, event.clientY);
      }
      els.sharedFilesDesktop?.focus?.({ preventScroll: true });
    }

    async function handleSharedFilesContextMenuClick(event) {
      const button = event.target?.closest?.('[data-shared-files-menu-action]');
      if (!(button instanceof HTMLButtonElement) || button.disabled) return;
      event.preventDefault();
      const action = String(button.dataset.sharedFilesMenuAction || '').trim();
      if (action === 'paste-clipboard') {
        await pasteSharedFilesFromSystemClipboard();
        return;
      }
      if (action === 'paste-crm') {
        await pasteSharedFile({ dropPoint: state.sharedFilesContextPoint || null });
        hideSharedFilesContextMenu();
        return;
      }
      if (action === 'upload') {
        hideSharedFilesContextMenu();
        els.sharedFilesInput?.click();
      }
    }

    function handleSharedFilesDocumentClick(event) {
      if (!els.sharedFilesContextMenu || els.sharedFilesContextMenu.hidden) return;
      if (els.sharedFilesContextMenu.contains(event.target)) return;
      hideSharedFilesContextMenu();
    }

    function handleSharedFilesGlobalKeydown(event) {
      if (event.key === 'Escape') hideSharedFilesContextMenu();
    }

    function handleSharedFilesDragOver(event) {
      const types = Array.from(event.dataTransfer?.types || []);
      if (!types.includes('Files')) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = 'copy';
      els.sharedFilesDesktop?.classList.add('is-drop-target');
      els.sharedFilesDesktop?.focus?.({ preventScroll: true });
    }

    function handleSharedFilesDragLeave(event) {
      if (!els.sharedFilesDesktop) return;
      if (event.relatedTarget instanceof Node && els.sharedFilesDesktop.contains(event.relatedTarget)) return;
      els.sharedFilesDesktop.classList.remove('is-drop-target');
    }

    async function handleSharedFilesDrop(event) {
      const files = Array.from(event.dataTransfer?.files || []).filter(Boolean);
      els.sharedFilesDesktop?.classList.remove('is-drop-target');
      if (!files.length) return;
      event.preventDefault();
      event.stopPropagation();
      hideSharedFilesContextMenu();
      await uploadSharedFiles(files, { dropPoint: sharedFilesDropPointFromEvent(event) });
    }

    async function uploadSharedFiles(files, { dropPoint = null, mutationContext = null } = {}) {
      const selectedFiles = Array.from(files || []).filter(Boolean);
      if (!selectedFiles.length) return null;
      const context = mutationContext || beginSharedFilesMutation();
      if (!context) return null;
      const ownsContext = Boolean(mutationContext);
      const isCurrent = context.isCurrent;
      let attemptedWrites = 0;
      let completedWrites = 0;
      try {
        const baseIndex = (state.sharedFiles || []).length;
        const normalizedDropPoint = normalizeSharedFilesDropPoint(dropPoint);
        for (let index = 0; index < selectedFiles.length; index += 1) {
          if (!isCurrent()) return null;
          const file = selectedFiles[index];
          if (file.size > SHARED_FILE_UPLOAD_MAX_SIZE_BYTES) {
            throw new Error('ФАЙЛ СЛИШКОМ БОЛЬШОЙ. ЛИМИТ: 25 МБ.');
          }
          const buffer = await file.arrayBuffer();
          if (!isCurrent()) return null;
          const fileName = sharedFilesFileNameForUpload(file, index);
          const extension = attachmentExtension(fileName);
          const mimeType = normalizeAttachmentMimeType(file.type) || attachmentMimeTypeFromExtension(extension) || 'application/octet-stream';
          const point = sharedFilesUploadPoint(index, baseIndex, normalizedDropPoint);
          attemptedWrites += 1;
          const uploaded = await api('/api/upload_shared_file', {
            method: 'POST',
            body: {
              actor_name: context.actorName,
              source: 'ui',
              file_name: fileName,
              mime_type: mimeType,
              content_base64: arrayBufferToBase64(buffer),
              x: point.x,
              y: point.y,
            },
          });
          if (!isCurrent()) return null;
          completedWrites += 1;
          context.selectCreatedFile(uploaded?.file?.id);
        }
        await loadSharedFiles();
        if (!isCurrent()) return null;
        setStatus(selectedFiles.length > 1 ? 'ФАЙЛЫ ЗАГРУЖЕНЫ.' : 'ФАЙЛ ЗАГРУЖЕН.', false);
        return completedWrites;
      } catch (error) {
        if (!isCurrent()) return null;
        const reconciled = attemptedWrites ? Boolean(await loadSharedFiles()) : false;
        if (!isCurrent()) return null;
        const progress = completedWrites ? (' СОХРАНЕНО: ' + completedWrites + ' ИЗ ' + selectedFiles.length + '.') : '';
        const retryWarning = attemptedWrites
          ? (reconciled
            ? ' СПИСОК ОБНОВЛЕН; ПОВТОРЯЙТЕ ТОЛЬКО ОТСУТСТВУЮЩИЕ ФАЙЛЫ.'
            : ' ПРОВЕРЬТЕ СПИСОК ПЕРЕД ПОВТОРНОЙ ЗАГРУЗКОЙ.')
          : '';
        setStatus(error.message + progress + retryWarning, true);
        return null;
      } finally {
        if (!ownsContext && finishSharedFilesMutation(context)) {
          if (els.sharedFilesInput) els.sharedFilesInput.value = '';
        }
      }
    }

    function openActiveSharedFile() {
      const file = activeSharedFile();
      if (!file) return;
      window.open(sharedFileDownloadUrl(file, { inline: true }), '_blank', 'noopener');
    }

    async function downloadActiveSharedFile() {
      const file = activeSharedFile();
      if (!file) return;
      try {
        await downloadAttachment(sharedFileDownloadUrl(file));
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function renameActiveSharedFile() {
      const file = activeSharedFile();
      if (!file) return;
      const nextName = window.prompt('Новое имя файла', file.original_name || '');
      if (nextName === null) return;
      const normalized = String(nextName || '').trim();
      if (!normalized) return;
      const context = beginSharedFilesMutation({ fileId: file.id });
      if (!context) return;
      try {
        const data = await api('/api/rename_shared_file', {
          method: 'POST',
          body: { file_id: file.id, file_name: normalized, actor_name: context.actorName, source: 'ui' },
        });
        if (!context.ownsRequest()) return;
        context.selectCreatedFile(data?.file?.id);
        await loadSharedFiles();
      } catch (error) {
        if (!context.ownsRequest()) return;
        const reconciled = Boolean(await loadSharedFiles());
        if (!context.ownsRequest()) return;
        const confirmed = reconciled && String(sharedFileById(file.id)?.original_name || '').trim() === normalized;
        setStatus(confirmed
          ? 'ФАЙЛ ПЕРЕИМЕНОВАН; РЕЗУЛЬТАТ ПОДТВЕРЖДЕН ПОВТОРНЫМ ЧТЕНИЕМ.'
          : error.message + (reconciled ? '' : ' ПРОВЕРЬТЕ СПИСОК ПЕРЕД ПОВТОРОМ.'), !confirmed);
      } finally {
        finishSharedFilesMutation(context);
      }
    }

    async function copyActiveSharedFile() {
      const file = activeSharedFile();
      if (!file) return;
      const context = beginSharedFilesMutation({ fileId: file.id });
      if (!context) return;
      try {
        const data = await api('/api/copy_shared_file', {
          method: 'POST',
          body: { file_id: file.id, actor_name: context.actorName, source: 'ui' },
        });
        if (!context.ownsRequest()) return;
        state.sharedFilesClipboardId = data?.clipboard?.source_id || file.id;
        updateSharedFilesActions();
        setStatus('ФАЙЛ СКОПИРОВАН.', false);
      } catch (error) {
        if (context.ownsRequest()) setStatus(error.message, true);
      } finally {
        finishSharedFilesMutation(context);
      }
    }

    async function pasteSharedFile({ dropPoint = null } = {}) {
      if (!state.sharedFilesClipboardId) return;
      const clipboardId = String(state.sharedFilesClipboardId || '').trim();
      const source = sharedFileById(clipboardId);
      const point = normalizeSharedFilesDropPoint(dropPoint);
      const basePoint = point
        ? point
        : {
            x: Math.max(24, finiteNumber(source?.x, 24) + 32),
            y: Math.max(24, finiteNumber(source?.y, 24) + 32),
          };
      const snappedPoint = sharedFilesSnapPointToGrid(basePoint.x, basePoint.y);
      const context = beginSharedFilesMutation({ clipboardId });
      if (!context) return;
      try {
        const data = await api('/api/paste_shared_file', {
          method: 'POST',
          body: {
            source_id: clipboardId,
            x: snappedPoint.x,
            y: snappedPoint.y,
            actor_name: context.actorName,
            source: 'ui',
          },
        });
        if (!context.ownsRequest()) return;
        context.selectCreatedFile(data?.file?.id);
        await loadSharedFiles();
      } catch (error) {
        if (!context.ownsRequest()) return;
        const reconciled = Boolean(await loadSharedFiles());
        if (context.ownsRequest()) setStatus(
          error.message + (reconciled ? ' СПИСОК ОБНОВЛЕН; ПРОВЕРЬТЕ РЕЗУЛЬТАТ ПЕРЕД ПОВТОРОМ.' : ' ПРОВЕРЬТЕ СПИСОК ПЕРЕД ПОВТОРОМ.'),
          true,
        );
      } finally {
        finishSharedFilesMutation(context);
      }
    }

    async function deleteActiveSharedFile() {
      const file = activeSharedFile();
      if (!file) return;
      if (!window.confirm('Удалить файл "' + String(file.original_name || '').trim() + '"?')) return;
      const context = beginSharedFilesMutation({ fileId: file.id });
      if (!context) return;
      try {
        await api('/api/delete_shared_file', {
          method: 'POST',
          body: { file_id: file.id, actor_name: context.actorName, source: 'ui' },
        });
        if (!context.ownsRequest()) return;
        if (state.sharedFilesClipboardId === file.id) state.sharedFilesClipboardId = '';
        if (state.sharedFilesActiveId === file.id) state.sharedFilesActiveId = '';
        await loadSharedFiles();
      } catch (error) {
        if (!context.ownsRequest()) return;
        const reconciled = Boolean(await loadSharedFiles());
        if (!context.ownsRequest()) return;
        const confirmed = reconciled && !sharedFileById(file.id);
        if (confirmed) {
          if (state.sharedFilesClipboardId === file.id) state.sharedFilesClipboardId = '';
          if (state.sharedFilesActiveId === file.id) state.sharedFilesActiveId = '';
        }
        setStatus(confirmed
          ? 'ФАЙЛ УДАЛЕН; РЕЗУЛЬТАТ ПОДТВЕРЖДЕН ПОВТОРНЫМ ЧТЕНИЕМ.'
          : error.message + (reconciled ? '' : ' ПРОВЕРЬТЕ СПИСОК ПЕРЕД ПОВТОРОМ.'), !confirmed);
      } finally {
        finishSharedFilesMutation(context);
      }
    }

    function beginSharedFileDrag(event) {
      const icon = event.target?.closest?.('[data-shared-file-id]');
      if (!(icon instanceof HTMLElement) || event.button !== 0) return;
      const fileId = String(icon.dataset.sharedFileId || '').trim();
      if (!fileId) return;
      hideSharedFilesContextMenu();
      selectSharedFile(fileId);
      const desktopRect = els.sharedFilesDesktop.getBoundingClientRect();
      const iconRect = icon.getBoundingClientRect();
      state.sharedFilesDrag = {
        fileId,
        pointerId: event.pointerId,
        offsetX: event.clientX - iconRect.left,
        offsetY: event.clientY - iconRect.top,
        moved: false,
      };
      icon.classList.add('is-dragging');
      icon.setPointerCapture?.(event.pointerId);
      event.preventDefault();
      void desktopRect;
    }

    function moveSharedFileDrag(event) {
      const drag = state.sharedFilesDrag;
      if (!drag || drag.pointerId !== event.pointerId) return;
      const icon = els.sharedFilesDesktop?.querySelector('[data-shared-file-id="' + CSS.escape(drag.fileId) + '"]');
      if (!(icon instanceof HTMLElement)) return;
      const desktopRect = els.sharedFilesDesktop.getBoundingClientRect();
      const nextX = Math.max(
        0,
        Math.round(event.clientX - desktopRect.left + els.sharedFilesDesktop.scrollLeft - drag.offsetX),
      );
      const nextY = Math.max(
        0,
        Math.round(event.clientY - desktopRect.top + els.sharedFilesDesktop.scrollTop - drag.offsetY),
      );
      const snappedPoint = sharedFilesSnapPointToGrid(nextX, nextY);
      icon.style.left = snappedPoint.x + 'px';
      icon.style.top = snappedPoint.y + 'px';
      icon.dataset.dragX = String(snappedPoint.x);
      icon.dataset.dragY = String(snappedPoint.y);
      drag.moved = true;
      event.preventDefault();
    }

    async function finishSharedFileDrag(event) {
      const drag = state.sharedFilesDrag;
      if (!drag || drag.pointerId !== event.pointerId) return;
      state.sharedFilesDrag = null;
      const icon = els.sharedFilesDesktop?.querySelector('[data-shared-file-id="' + CSS.escape(drag.fileId) + '"]');
      if (icon instanceof HTMLElement) icon.classList.remove('is-dragging');
      if (!(icon instanceof HTMLElement) || !drag.moved) return;
      const x = finiteNonNegativeNumber(icon.dataset.dragX);
      const y = finiteNonNegativeNumber(icon.dataset.dragY);
      const context = beginSharedFilesMutation({ fileId: drag.fileId });
      if (!context) {
        renderSharedFiles();
        return;
      }
      try {
        const data = await api('/api/update_shared_file_position', {
          method: 'POST',
          body: { file_id: drag.fileId, x, y, actor_name: context.actorName, source: 'ui' },
        });
        if (!context.ownsRequest()) return;
        if (data?.file?.id) {
          const file = sharedFileById(data.file.id);
          if (file) {
            file.x = data.file.x;
            file.y = data.file.y;
          }
        }
      } catch (error) {
        if (!context.ownsRequest()) return;
        const reconciled = Boolean(await loadSharedFiles());
        if (context.ownsRequest()) setStatus(
          error.message + (reconciled ? ' ПОЗИЦИЯ ОБНОВЛЕНА ИЗ ХРАНИЛИЩА.' : ' ОБНОВИТЕ СПИСОК ПЕРЕД ПОВТОРОМ.'),
          true,
        );
      } finally {
        finishSharedFilesMutation(context);
      }
    }

    function handleSharedFilesDesktopClick(event) {
      els.sharedFilesDesktop?.focus?.({ preventScroll: true });
      hideSharedFilesContextMenu();
      const icon = event.target?.closest?.('[data-shared-file-id]');
      if (icon instanceof HTMLElement) selectSharedFile(icon.dataset.sharedFileId);
    }

    function handleSharedFilesDesktopDoubleClick(event) {
      hideSharedFilesContextMenu();
      const icon = event.target?.closest?.('[data-shared-file-id]');
      if (!(icon instanceof HTMLElement)) return;
      selectSharedFile(icon.dataset.sharedFileId);
      openActiveSharedFile();
    }
