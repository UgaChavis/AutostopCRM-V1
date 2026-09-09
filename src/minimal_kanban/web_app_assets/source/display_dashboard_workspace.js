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
      state.displayDashboardContextGeneration = (state.displayDashboardContextGeneration || 0) + 1;
      state.displayDashboardOpenRequest = null;
      state.displayDashboardSaveRequest = null;
      state.displayDashboardMessageSaving = false;
      clearDisplayDashboardImageDrafts();
      state.displayDashboardSelectionRange = null;
      if (els.displayDashboardEmojiPalette) els.displayDashboardEmojiPalette.hidden = true;
      if (els.displayDashboardEmojiButton) els.displayDashboardEmojiButton.setAttribute('aria-expanded', 'false');
      popModal('display-dashboard-message');
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
      if (!els.displayDashboardEmojiPalette) return;
      els.displayDashboardEmojiPalette.hidden = !els.displayDashboardEmojiPalette.hidden;
      els.displayDashboardEmojiButton?.setAttribute(
        'aria-expanded',
        els.displayDashboardEmojiPalette.hidden ? 'false' : 'true',
      );
    }

    function addDisplayDashboardImages(files) {
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

    async function uploadDisplayDashboardImage(file, index, context) {
      if (!context.isCurrent()) return '';
      const buffer = await file.arrayBuffer();
      if (!context.isCurrent()) return '';
      const fileName = String(file.name || ('dashboard-image-' + (index + 1) + '.jpg')).slice(0, 240);
      const uploaded = await api('/api/upload_shared_file', {
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
      if (!context.isCurrent()) return '';
      const fileId = String(uploaded?.file?.id || '').trim();
      if (!fileId) throw new Error('НЕ УДАЛОСЬ СОХРАНИТЬ ИЗОБРАЖЕНИЕ.');
      return fileId;
    }

    async function saveDisplayDashboardMessage() {
      if (state.displayDashboardMessageSaving || !state.displayDashboardMessage) return;
      const request = {};
      const viewerContext = captureViewerRequestContext();
      const contextGeneration = state.displayDashboardContextGeneration || 0;
      const expectedRevision = String(state.displayDashboardMessage.revision || '');
      const pendingImages = state.displayDashboardPendingImages
        .map((item) => ({ file: item?.file }))
        .filter((item) => item.file);
      const existingImageIds = state.displayDashboardExistingImageIds.slice();
      const bodyHtml = String(els.displayDashboardMessageEditor?.innerHTML || '');
      const isCurrent = () => state.displayDashboardSaveRequest === request
        && viewerContext.isCurrent()
        && state.displayDashboardContextGeneration === contextGeneration
        && isModalOpen('display-dashboard-message')
        && String(state.displayDashboardMessage?.revision || '') === expectedRevision;
      const context = { ...viewerContext, request, expectedRevision, isCurrent };
      state.displayDashboardSaveRequest = request;
      state.displayDashboardMessageSaving = true;
      if (els.displayDashboardMessageSaveButton) els.displayDashboardMessageSaveButton.disabled = true;
      try {
        const uploadedIds = [];
        for (let index = 0; index < pendingImages.length; index += 1) {
          if (!isCurrent()) return;
          const fileId = await uploadDisplayDashboardImage(pendingImages[index].file, index, context);
          if (!isCurrent()) return;
          if (fileId) uploadedIds.push(fileId);
        }
        const imageFileIds = existingImageIds.concat(uploadedIds);
        const data = await api('/api/update_board_settings', {
          method: 'POST',
          body: {
            actor_name: context.actorName,
            source: 'ui',
            expected_revision: context.expectedRevision,
            display_dashboard_message: {
              body_html: bodyHtml,
              image_file_ids: imageFileIds,
            },
          },
        });
        if (!isCurrent()) return;
        state.displayDashboardMessage = normalizedDisplayDashboardMessage(
          data?.meta?.display_dashboard_message || data?.settings?.display_dashboard_message,
        );
        if (state.snapshot && data?.settings) state.snapshot.settings = data.settings;
        closeDisplayDashboardMessageEditor();
        setStatus('ДОСКА МЕХАНИКОВ ОБНОВЛЕНА.', false);
      } catch (error) {
        if (isCurrent()) setStatus(error.message, true);
      } finally {
        if (state.displayDashboardSaveRequest === request) {
          state.displayDashboardSaveRequest = null;
          state.displayDashboardMessageSaving = false;
          if (els.displayDashboardMessageSaveButton) els.displayDashboardMessageSaveButton.disabled = false;
        }
      }
    }
