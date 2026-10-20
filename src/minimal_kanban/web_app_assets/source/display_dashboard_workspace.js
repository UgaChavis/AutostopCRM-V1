    let displayDashboardControls = null;
    let displayDashboardMessageWrite = null;

    function holdDisplayDashboardControls() {
      const controls = Array.from(els.displayDashboardMessageModal?.querySelectorAll('button, input, select') || []);
      controls.push(...(els.boardSettingsModal?.querySelectorAll('[data-close="settings"]') || []));
      const disabled = controls.map((control) => [control, control.disabled]);
      const editor = els.displayDashboardMessageEditor;
      const editable = editor?.getAttribute('contenteditable');
      const readOnly = editor?.getAttribute('aria-readonly');
      controls.forEach((control) => { control.disabled = true; });
      editor?.setAttribute('contenteditable', 'false');
      editor?.setAttribute('aria-readonly', 'true');
      editor?.setAttribute('aria-busy', 'true');
      displayDashboardControls = () => {
        disabled.forEach(([control, value]) => { control.disabled = value; });
        if (editor) {
          if (editable === null) editor.removeAttribute('contenteditable');
          else editor.setAttribute('contenteditable', editable);
          if (readOnly === null) editor.removeAttribute('aria-readonly');
          else editor.setAttribute('aria-readonly', readOnly);
          editor.removeAttribute('aria-busy');
        }
      };
    }

    function releaseDisplayDashboardControls() {
      displayDashboardControls?.();
      displayDashboardControls = null;
    }

    function dashboardWriteErrorIsDefinite(error) {
      return error?.status >= 400 && error.status < 500 && error.code !== 'invalid_json';
    }

    function normalizedDisplayDashboardMessage(value) {
      const source = value && typeof value === 'object' ? value : {};
      return {
        schema_version: String(source.schema_version || 'display_dashboard_message.v1'),
        body_html: String(source.body_html || ''),
        image_file_ids: Array.isArray(source.image_file_ids)
          ? Array.from(new Set(source.image_file_ids.map((item) => String(item || '').trim()).filter(Boolean))).slice(0, DISPLAY_DASHBOARD_MAX_IMAGES)
          : [],
        updated_at: String(source.updated_at || ''),
        updated_by: String(source.updated_by || ''),
        revision: String(source.revision || ''),
      };
    }

    function clearDisplayDashboardImageDrafts() {
      state.displayDashboardPendingImages.forEach((item) => {
        if (item?.url) URL.revokeObjectURL(item.url);
      });
      state.displayDashboardPendingImages = [];
      state.displayDashboardExistingImageUrls.forEach((url) => URL.revokeObjectURL(url));
      state.displayDashboardExistingImageUrls.clear();
      state.displayDashboardExistingImageIds = [];
      if (els.displayDashboardImageInput) els.displayDashboardImageInput.value = '';
      if (els.displayDashboardMessageImages) els.displayDashboardMessageImages.innerHTML = '';
    }

    function closeDisplayDashboardMessageEditor() {
      if (state.displayDashboardMessageSaving) return false;
      state.displayDashboardContextGeneration = (state.displayDashboardContextGeneration || 0) + 1;
      state.displayDashboardOpenRequest = null;
      state.displayDashboardSaveRequest = null;
      state.displayDashboardMessageSaving = false;
      displayDashboardMessageWrite = null;
      clearDisplayDashboardImageDrafts();
      state.displayDashboardSelectionRange = null;
      if (els.displayDashboardEmojiPalette) els.displayDashboardEmojiPalette.hidden = true;
      if (els.displayDashboardEmojiButton) els.displayDashboardEmojiButton.setAttribute('aria-expanded', 'false');
      popModal('display-dashboard-message');
      return true;
    }

    function appendDisplayDashboardImagePreview({ url, key, existing }) {
      if (!url || !els.displayDashboardMessageImages) return;
      const card = document.createElement('div');
      card.className = 'display-dashboard-editor__image';
      const image = document.createElement('img');
      image.src = url;
      image.alt = 'Фото к сообщению';
      const removeButton = document.createElement('button');
      removeButton.type = 'button';
      removeButton.textContent = '×';
      removeButton.title = 'Убрать фото';
      removeButton.setAttribute('aria-label', 'Убрать фото');
      removeButton.dataset.dashboardImageRemove = key;
      removeButton.dataset.dashboardImageExisting = existing ? 'true' : 'false';
      removeButton.disabled = Boolean(state.displayDashboardMessageSaving);
      card.append(image, removeButton);
      els.displayDashboardMessageImages.appendChild(card);
    }

    function renderDisplayDashboardImageDrafts() {
      if (!els.displayDashboardMessageImages) return;
      els.displayDashboardMessageImages.innerHTML = '';
      state.displayDashboardExistingImageIds.forEach((fileId) => {
        const url = state.displayDashboardExistingImageUrls.get(fileId);
        if (url) appendDisplayDashboardImagePreview({ url, key: fileId, existing: true });
      });
      state.displayDashboardPendingImages.forEach((item) => {
        appendDisplayDashboardImagePreview({ url: item.url, key: item.key, existing: false });
      });
      const total = state.displayDashboardExistingImageIds.length + state.displayDashboardPendingImages.length;
      if (els.displayDashboardMessageMeta && state.displayDashboardMessage) {
        const updatedAt = formatDate(state.displayDashboardMessage.updated_at);
        els.displayDashboardMessageMeta.textContent = [
          updatedAt ? ('ОБНОВЛЕНО ' + updatedAt) : 'НОВОЕ СООБЩЕНИЕ',
          state.displayDashboardMessage.updated_by,
          'ФОТО: ' + total + '/' + DISPLAY_DASHBOARD_MAX_IMAGES,
        ].filter(Boolean).join(' · ');
      }
    }

    async function loadDisplayDashboardExistingImages(imageIds, revision, context) {
      const headers = {};
      if (context?.apiToken) headers.Authorization = 'Bearer ' + context.apiToken;
      if (context?.operatorSessionToken) headers['X-Operator-Session'] = context.operatorSessionToken;
      const loaded = await Promise.all(imageIds.map(async (fileId) => {
        try {
          const response = await fetch(
            '/api/shared_file?file_id=' + encodeURIComponent(fileId) + '&disposition=inline',
            { headers, cache: 'no-store' },
          );
          if (!response.ok) return null;
          const blob = await response.blob();
          if (!String(blob.type || '').toLowerCase().startsWith('image/')) return null;
          return { fileId, url: URL.createObjectURL(blob) };
        } catch (_error) {
          return null;
        }
      }));
      if (
        !context?.isCurrent()
        || !isModalOpen('display-dashboard-message')
        || state.displayDashboardMessage?.revision !== revision
      ) {
        loaded.filter(Boolean).forEach((item) => URL.revokeObjectURL(item.url));
        return;
      }
      loaded.filter(Boolean).forEach((item) => {
        state.displayDashboardExistingImageUrls.set(item.fileId, item.url);
      });
      renderDisplayDashboardImageDrafts();
    }

    async function openDisplayDashboardMessageEditor() {
      if (state.displayDashboardMessageSaving) return;
      if (!requireOperatorSession()) return;
      const settingsParent = (state.modalStack || []).find((entry) => entry?.key === 'settings');
      if (!settingsParent) return;
      const request = {};
      const viewerContext = captureViewerRequestContext();
      const contextGeneration = (state.displayDashboardContextGeneration || 0) + 1;
      state.displayDashboardContextGeneration = contextGeneration;
      const context = {
        ...viewerContext,
        isCurrent: () => viewerContext.isCurrent()
          && state.displayDashboardContextGeneration === contextGeneration,
      };
      const isCurrent = () => state.displayDashboardOpenRequest === request && context.isCurrent();
      const ownsOpenIntent = () => isCurrent() && (state.modalStack || []).includes(settingsParent);
      state.displayDashboardOpenRequest = request;
      try {
        const data = await api('/api/get_display_dashboard');
        if (!ownsOpenIntent()) return;
        const message = normalizedDisplayDashboardMessage(data?.message_board);
        displayDashboardMessageWrite = null;
        clearDisplayDashboardImageDrafts();
        state.displayDashboardMessage = message;
        state.displayDashboardExistingImageIds = message.image_file_ids.slice();
        if (els.displayDashboardMessageEditor) {
          els.displayDashboardMessageEditor.innerHTML = message.body_html;
        }
        if (els.displayDashboardMessageSaveButton) els.displayDashboardMessageSaveButton.disabled = false;
        state.displayDashboardSelectionRange = null;
        if (els.displayDashboardEmojiPalette) els.displayDashboardEmojiPalette.hidden = true;
        if (els.displayDashboardEmojiButton) els.displayDashboardEmojiButton.setAttribute('aria-expanded', 'false');
        pushModal('display-dashboard-message', els.displayDashboardMessageModal, { parentKey: 'settings' });
        renderDisplayDashboardImageDrafts();
        void loadDisplayDashboardExistingImages(message.image_file_ids, message.revision, context);
        requestAnimationFrame(() => els.displayDashboardMessageEditor?.focus({ preventScroll: true }));
      } catch (error) {
        if (ownsOpenIntent()) setStatus(error.message, true);
      } finally {
        if (state.displayDashboardOpenRequest === request) state.displayDashboardOpenRequest = null;
      }
    }

    function rememberDisplayDashboardSelection() {
      const selection = window.getSelection();
      if (!selection?.rangeCount || !els.displayDashboardMessageEditor) return;
      const range = selection.getRangeAt(0);
      const container = range.commonAncestorContainer;
      if (!els.displayDashboardMessageEditor.contains(
        container.nodeType === Node.ELEMENT_NODE ? container : container.parentElement,
      )) return;
      state.displayDashboardSelectionRange = range.cloneRange();
    }

    function restoreDisplayDashboardSelection() {
      const range = state.displayDashboardSelectionRange;
      if (!(range instanceof Range)) return;
      const selection = window.getSelection();
      selection?.removeAllRanges();
      selection?.addRange(range);
    }

    function applyDisplayDashboardFormat(command, value = null) {
      if (state.displayDashboardMessageSaving) return;
      if (!els.displayDashboardMessageEditor) return;
      els.displayDashboardMessageEditor.focus({ preventScroll: true });
      restoreDisplayDashboardSelection();
      document.execCommand(command, false, value);
      rememberDisplayDashboardSelection();
    }

    function handleDisplayDashboardToolbarClick(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const formatButton = target.closest('[data-dashboard-format]');
      if (formatButton instanceof HTMLElement) {
        applyDisplayDashboardFormat(String(formatButton.dataset.dashboardFormat || ''));
        return;
      }
      const emojiButton = target.closest('[data-dashboard-emoji]');
      if (emojiButton instanceof HTMLElement) {
        applyDisplayDashboardFormat('insertText', String(emojiButton.dataset.dashboardEmoji || ''));
      }
    }

    function handleDisplayDashboardFontSize() {
      applyDisplayDashboardFormat('fontSize', String(els.displayDashboardFontSize?.value || '3'));
    }

    function toggleDisplayDashboardEmojiPalette() {
      if (state.displayDashboardMessageSaving) return;
      if (!els.displayDashboardEmojiPalette) return;
      els.displayDashboardEmojiPalette.hidden = !els.displayDashboardEmojiPalette.hidden;
      els.displayDashboardEmojiButton?.setAttribute(
        'aria-expanded',
        els.displayDashboardEmojiPalette.hidden ? 'false' : 'true',
      );
    }

    function addDisplayDashboardImages(files) {
      if (state.displayDashboardMessageSaving) return;
      const selected = Array.from(files || []).filter((file) =>
        String(file?.type || '').toLowerCase().startsWith('image/')
      );
      if (!selected.length) {
        setStatus('ВЫБЕРИТЕ ИЗОБРАЖЕНИЕ JPG, PNG, WEBP ИЛИ GIF.', true);
        return;
      }
      const available = Math.max(
        0,
        DISPLAY_DASHBOARD_MAX_IMAGES
          - state.displayDashboardExistingImageIds.length
          - state.displayDashboardPendingImages.length,
      );
      if (selected.length > available) {
        setStatus('НА ДОСКЕ МОЖЕТ БЫТЬ НЕ БОЛЬШЕ 8 ФОТО.', true);
      }
      selected.slice(0, available).forEach((file) => {
        if (file.size > SHARED_FILE_UPLOAD_MAX_SIZE_BYTES) {
          setStatus('ФОТО СЛИШКОМ БОЛЬШОЕ. ЛИМИТ: 25 МБ.', true);
          return;
        }
        state.displayDashboardPendingImages.push({
          key: crypto.randomUUID(),
          file,
          url: URL.createObjectURL(file),
        });
      });
      if (els.displayDashboardImageInput) els.displayDashboardImageInput.value = '';
      renderDisplayDashboardImageDrafts();
    }

    function removeDisplayDashboardImage(event) {
      if (state.displayDashboardMessageSaving) return;
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const button = target.closest('[data-dashboard-image-remove]');
      if (!(button instanceof HTMLElement)) return;
      const key = String(button.dataset.dashboardImageRemove || '');
      if (button.dataset.dashboardImageExisting === 'true') {
        state.displayDashboardExistingImageIds = state.displayDashboardExistingImageIds
          .filter((fileId) => fileId !== key);
        const url = state.displayDashboardExistingImageUrls.get(key);
        if (url) URL.revokeObjectURL(url);
        state.displayDashboardExistingImageUrls.delete(key);
      } else {
        const removed = state.displayDashboardPendingImages.find((item) => item.key === key);
        if (removed?.url) URL.revokeObjectURL(removed.url);
        state.displayDashboardPendingImages = state.displayDashboardPendingImages
          .filter((item) => item.key !== key);
      }
      renderDisplayDashboardImageDrafts();
    }

    async function uploadDisplayDashboardImage(item, index, context) {
      if (!context.isCurrent()) return '';
      if (item.uploadedId) return item.uploadedId;
      if (item.uncertain) throw new Error('РЕЗУЛЬТАТ ЗАГРУЗКИ ФОТО НЕ ПОДТВЕРЖДЁН. ПОВТОРНАЯ ОТПРАВКА ОСТАНОВЛЕНА; СВЕРЬТЕ СПИСОК ФАЙЛОВ.');
      const file = item.file;
      const buffer = await file.arrayBuffer();
      if (!context.isCurrent()) return '';
      const fileName = String(file.name || ('dashboard-image-' + (index + 1) + '.jpg')).slice(0, 240);
      let uploaded;
      try {
        uploaded = await cardFilesApi('/api/upload_shared_file', {
          method: 'POST',
          body: {
            actor_name: context.actorName,
            source: 'ui',
            file_name: fileName,
            mime_type: String(file.type || 'image/jpeg'),
            content_base64: arrayBufferToBase64(buffer),
            x: 0,
            y: 0,
          },
        });
      } catch (error) {
        if (context.isCurrent() && !dashboardWriteErrorIsDefinite(error)) item.uncertain = true;
        throw error;
      }
      if (!context.isCurrent()) return '';
      const fileId = String(uploaded?.file?.id || '').trim();
      if (!fileId) {
        item.uncertain = true;
        throw new Error('СЕРВЕР НЕ ПОДТВЕРДИЛ ЗАГРУЗКУ ИЗОБРАЖЕНИЯ.');
      }
      item.uploadedId = fileId;
      return fileId;
    }

    async function reconcileDisplayDashboardMessage(context) {
      const checkpoint = displayDashboardMessageWrite;
      if (!checkpoint || !context.isCurrent()) return false;
      const data = await api('/api/get_display_dashboard');
      if (!context.isCurrent()) return false;
      const message = normalizedDisplayDashboardMessage(data?.message_board);
      if (!message.revision) throw new Error('СЕРВЕР НЕ ПОДТВЕРДИЛ СООБЩЕНИЕ НА ДОСКЕ.');
      if (message.body_html === checkpoint.body.body_html
        && JSON.stringify(message.image_file_ids) === JSON.stringify(checkpoint.body.image_file_ids)) {
        state.displayDashboardMessage = message;
        if (state.snapshot?.settings) state.snapshot.settings.display_dashboard_message = message;
        displayDashboardMessageWrite = null;
        return true;
      }
      if (message.revision === checkpoint.expectedRevision) {
        displayDashboardMessageWrite = null;
        return false;
      }
      throw new Error('СООБЩЕНИЕ НА ДОСКЕ ИЗМЕНИЛОСЬ. ЧЕРНОВИК СОХРАНЁН; СВЕРЬТЕ РЕЗУЛЬТАТ ПЕРЕД ПОВТОРОМ.');
    }

    async function saveDisplayDashboardMessage() {
      if (state.displayDashboardMessageSaving || !state.displayDashboardMessage) return;
      const request = {};
      const viewerContext = captureViewerRequestContext();
      const contextGeneration = state.displayDashboardContextGeneration || 0;
      const expectedRevision = String(state.displayDashboardMessage.revision || '');
      const pendingImages = state.displayDashboardPendingImages
        .filter((item) => item?.file);
      const existingImageIds = state.displayDashboardExistingImageIds.slice();
      const bodyHtml = String(els.displayDashboardMessageEditor?.innerHTML || '');
      const isCurrent = () => state.displayDashboardSaveRequest === request
        && viewerContext.isCurrent()
        && state.displayDashboardContextGeneration === contextGeneration
        && isModalOpen('display-dashboard-message');
      const context = { ...viewerContext, request, expectedRevision, isCurrent };
      state.displayDashboardSaveRequest = request;
      state.displayDashboardMessageSaving = true;
      holdDisplayDashboardControls();
      let completed = false;
      let messageAttempted = false;
      try {
        if (displayDashboardMessageWrite && await reconcileDisplayDashboardMessage(context)) {
          const currentIds = existingImageIds.concat(pendingImages.map((item) => item.uploadedId || ''));
          if (state.displayDashboardMessage.body_html === bodyHtml
            && JSON.stringify(state.displayDashboardMessage.image_file_ids) === JSON.stringify(currentIds)) {
            completed = true;
            return;
          }
          context.expectedRevision = state.displayDashboardMessage.revision;
        }
        if (!isCurrent()) return;
        const uploadedIds = [];
        for (let index = 0; index < pendingImages.length; index += 1) {
          if (!isCurrent()) return;
          const fileId = await uploadDisplayDashboardImage(pendingImages[index], index, context);
          if (!isCurrent()) return;
          if (fileId) uploadedIds.push(fileId);
        }
        const imageFileIds = existingImageIds.concat(uploadedIds);
        displayDashboardMessageWrite = { expectedRevision: context.expectedRevision, body: { body_html: bodyHtml, image_file_ids: imageFileIds } };
        messageAttempted = true;
        const data = await cardFilesApi('/api/update_board_settings', {
          method: 'POST',
          body: {
            actor_name: context.actorName,
            source: 'ui',
            expected_revision: context.expectedRevision,
            display_dashboard_message: displayDashboardMessageWrite.body,
          },
        });
        if (!isCurrent()) return;
        const message = normalizedDisplayDashboardMessage(
          data?.meta?.display_dashboard_message || data?.settings?.display_dashboard_message,
        );
        if (!message.revision) throw new Error('СЕРВЕР НЕ ПОДТВЕРДИЛ СОХРАНЕНИЕ СООБЩЕНИЯ.');
        state.displayDashboardMessage = message;
        if (state.snapshot && data?.settings) state.snapshot.settings = data.settings;
        displayDashboardMessageWrite = null;
        completed = true;
      } catch (error) {
        if (!isCurrent()) return;
        if (displayDashboardMessageWrite) {
          if (messageAttempted && dashboardWriteErrorIsDefinite(error)) displayDashboardMessageWrite = null;
          else {
            try {
              completed = await reconcileDisplayDashboardMessage(context);
            } catch (_) {}
          }
        }
        if (!completed && isCurrent()) setStatus(error.message, true);
      } finally {
        if (state.displayDashboardSaveRequest === request) {
          const ownsEditor = isCurrent();
          state.displayDashboardSaveRequest = null;
          state.displayDashboardMessageSaving = false;
          releaseDisplayDashboardControls();
          renderDisplayDashboardImageDrafts();
          if (completed && ownsEditor) {
            closeDisplayDashboardMessageEditor();
            setStatus('ДОСКА МЕХАНИКОВ ОБНОВЛЕНА.', false);
          }
        }
      }
    }
