"""JavaScript fragments for the embedded print-template editor."""

PRINTING_TEMPLATE_EDITOR_HELPERS_SCRIPT = r"""    function buildPrintTemplateVisualEditorHtml(content) {
      return '<!doctype html><html><head><meta charset="utf-8"><style>'
        + 'html,body{margin:0;padding:0;background:#fff;color:#111;font:14px/1.55 Segoe UI,Arial,sans-serif;}'
        + 'body{padding:28px;}'
        + '[contenteditable=\"true\"]{min-height:980px;outline:none;}'
        + 'table{border-collapse:collapse;width:100%;}'
        + 'td,th{border:1px solid #d6d6d6;padding:6px 8px;vertical-align:top;}'
        + '.token{display:inline-block;padding:2px 6px;border-radius:999px;background:#eef3e8;border:1px solid #cad6be;font:12px/1.2 Consolas,monospace;color:#35412f;white-space:nowrap;}'
        + '</style></head><body><div id=\"editor\" contenteditable=\"true\"></div></body></html>';
    }

    function buildPrintTemplateEditorFallbackHtml(title, message, detail = '') {
      return '<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">'
        + '<style>body{margin:0;padding:32px;font-family:Segoe UI,Arial,sans-serif;background:#fff;color:#1b1b1b;}'
        + '.wrap{max-width:760px;margin:0 auto;}'
        + '.title{font-size:22px;font-weight:700;line-height:1.15;margin:0 0 10px;}'
        + '.message{font-size:14px;line-height:1.55;margin:0 0 12px;color:#444;}'
        + '.detail{white-space:pre-wrap;padding:12px 14px;border:1px solid #d7d7d7;border-radius:12px;background:#f7f7f7;font:12px/1.5 Consolas,monospace;color:#2f2f2f;}</style>'
        + '</head><body><div class=\"wrap\"><div class=\"title\">' + escapeHtml(title || 'Предпросмотр') + '</div>'
        + '<p class=\"message\">' + escapeHtml(message || 'Не удалось построить предпросмотр.') + '</p>'
        + (detail ? '<div class=\"detail\">' + escapeHtml(detail) + '</div>' : '')
        + '</div></body></html>';
    }

    function renderPrintTemplateVisualEditor(content) {
      if (!printEls.templateVisualEditorFrame) return;
      printEls.templateVisualEditorFrame.srcdoc = buildPrintTemplateVisualEditorHtml(content);
    }

    function schedulePrintTemplatePreview() {
      if (printTemplatePreviewTimer) {
        window.clearTimeout(printTemplatePreviewTimer);
      }
      printTemplatePreviewTimer = scheduleCurrentPrintOperation(() => {
        printTemplatePreviewTimer = null;
        previewCurrentPrintTemplate();
      }, 220, { template: true });
    }

    function syncPrintTemplateSourceFromVisualEditor() {
      const doc = printEls.templateVisualEditorFrame?.contentDocument;
      const editor = doc?.getElementById('editor');
      if (!editor || !printEls.templateContent) return;
      printEls.templateContent.value = editor.innerHTML;
      printEls.templateContent.dataset.dirty = '0';
    }

    function loadPrintTemplateEditorContent(content) {
      if (printEls.templateContent) {
        printEls.templateContent.value = String(content || '');
        printEls.templateContent.dataset.dirty = '0';
      }
      renderPrintTemplateVisualEditor(content);
      schedulePrintTemplatePreview();
    }

    function readPrintTemplateEditorContent() {
      // Visual edits already update the source; a reloading iframe can still contain old HTML.
      return printEls.templateContent?.value || '';
    }

    function handlePrintTemplateVisualEditorLoad() {
      const doc = printEls.templateVisualEditorFrame?.contentDocument;
      const editor = doc?.getElementById('editor');
      if (!editor) return;
      editor.innerHTML = printEls.templateContent?.value || '';
      editor.addEventListener('input', () => {
        if (printEls.templateContent) {
          printEls.templateContent.value = editor.innerHTML;
          printEls.templateContent.dataset.dirty = '0';
        }
        schedulePrintTemplatePreview();
      });
    }

    function renderPrintTemplateTokenOptions() {
      if (!printEls.templateTokenSelect) return;
      printEls.templateTokenSelect.innerHTML = PRINT_TEMPLATE_EDITOR_TOKENS
        .map((item) => '<option value="' + escapeHtml(item.value) + '">' + escapeHtml(item.label) + '</option>')
        .join('');
    }

    function execPrintTemplateEditorCommand(command, value) {
      const doc = printEls.templateVisualEditorFrame?.contentDocument;
      const win = printEls.templateVisualEditorFrame?.contentWindow;
      if (!doc || !win) return;
      const editor = doc.getElementById('editor');
      if (!editor) return;
      win.focus();
      editor.focus();
      doc.execCommand(command, false, value || null);
      syncPrintTemplateSourceFromVisualEditor();
    }

    function insertPrintTemplateToken() {
      const value = printEls.templateTokenSelect?.value || '';
      if (!value) return;
      execPrintTemplateEditorCommand('insertHTML', '<span class="token">' + escapeHtml(value) + '</span>');
      const doc = printEls.templateVisualEditorFrame?.contentDocument;
      const editor = doc?.getElementById('editor');
      if (editor) {
        editor.innerHTML = editor.innerHTML.replace(/<span class=\"token\">([^<]+)<\/span>/g, '$1');
      }
      syncPrintTemplateSourceFromVisualEditor();
    }

"""

PRINTING_TEMPLATE_EDITOR_WORKFLOW_SCRIPT = r"""    function repairOrderPrintCurrentTemplateRecord() {
      const documentType = repairOrderPrintState.templateEditor.documentType || 'repair_order';
      const templateId = repairOrderPrintState.templateEditor.templateId || '';
      return repairOrderPrintTemplatesFor(documentType).find((item) => item.id === templateId) || null;
    }

    function renderPrintTemplateDocumentTypeOptions() {
      const docs = repairOrderPrintWorkspaceDocuments().filter((item) => !item.template_locked);
      const requested = repairOrderPrintState.templateEditor.documentType || repairOrderPrintActiveDocument() || 'repair_order';
      const current = docs.some((item) => item.id === requested) ? requested : (docs[0]?.id || 'repair_order');
      printEls.templateDocumentType.innerHTML = docs.map((item) => '<option value="' + escapeHtml(item.id) + '"' + (item.id === current ? ' selected' : '') + '>' + escapeHtml(item.label) + '</option>').join('');
      repairOrderPrintState.templateEditor.documentType = current;
      renderPrintTemplateTokenOptions();
    }

    function renderPrintTemplateList() {
      const documentType = repairOrderPrintState.templateEditor.documentType || 'repair_order';
      const templates = repairOrderPrintTemplatesFor(documentType);
      const activeTemplateId = repairOrderPrintState.templateEditor.templateId || repairOrderPrintSelectedTemplateId(documentType);
      repairOrderPrintState.templateEditor.templateId = activeTemplateId;
      printEls.templateList.innerHTML = templates.length
        ? templates.map((item) => '<div class="print-template-editor__item' + (item.id === activeTemplateId ? ' is-active' : '') + '" data-print-template-id="' + escapeHtml(item.id) + '"><div class="print-template-editor__item-title">' + escapeHtml(item.name) + '</div><div class="print-template-editor__meta">' + escapeHtml(item.source || 'custom') + (item.is_default ? ' · default' : '') + '</div></div>').join('')
        : '<div class="repair-order-print-empty">Шаблонов пока нет.</div>';
      printEls.templateListMeta.textContent = templates.length ? ('Шаблонов: ' + templates.length + '. Активный документ: ' + (repairOrderPrintDocumentMap()[documentType]?.label || documentType)) : 'Создайте первый шаблон для этого типа документа.';
      const current = repairOrderPrintCurrentTemplateRecord();
      printEls.templateName.value = current?.name || '';
      loadPrintTemplateEditorContent(current?.content || '');
      printEls.templateEditorMeta.textContent = current ? ('Источник: ' + (current.source || 'custom') + (current.is_builtin ? '. Встроенный шаблон можно сохранить как новый.' : '.')) : 'Новый шаблон можно сохранить как отдельную запись.';
      printEls.templateFooterMeta.textContent = current?.is_default ? 'Этот шаблон уже используется по умолчанию.' : 'Можно сделать текущий шаблон шаблоном по умолчанию.';
    }

    function openPrintTemplateEditor() {
      invalidatePrintTemplateContext();
      const activeDocument = repairOrderPrintDocumentMap()[repairOrderPrintActiveDocument()] || null;
      repairOrderPrintState.templateEditor.documentType = activeDocument?.template_locked ? 'repair_order' : (activeDocument?.id || 'repair_order');
      repairOrderPrintState.templateEditor.templateId = repairOrderPrintSelectedTemplateId(repairOrderPrintState.templateEditor.documentType);
      renderPrintTemplateDocumentTypeOptions();
      renderPrintTemplateList();
      printEls.templateModal.classList.add('is-open');
    }

    function closePrintTemplateEditor() {
      invalidatePrintTemplateContext();
      if (printTemplatePreviewTimer) window.clearTimeout(printTemplatePreviewTimer);
      printTemplatePreviewTimer = null;
      printEls.templateModal.classList.remove('is-open');
    }

    function selectPrintTemplateRecord(templateId) {
      invalidatePrintTemplateContext();
      repairOrderPrintState.templateEditor.templateId = templateId;
      renderPrintTemplateList();
    }

    async function previewCurrentPrintTemplate() {
      const operation = capturePrintOperation('previewCurrentPrintTemplate', { template: true });
      const documentType = repairOrderPrintState.templateEditor.documentType || 'repair_order';
      const draftContent = readPrintTemplateEditorContent();
      if (printEls.templatePreviewMeta) {
        printEls.templatePreviewMeta.textContent = 'Обновляем предпросмотр...';
      }
      if (printEls.templatePreviewFrame) {
        printEls.templatePreviewFrame.srcdoc = buildPrintTemplateEditorFallbackHtml(
          'Предпросмотр шаблона',
          'Построение предпросмотра...',
        );
      }
      try {
        const data = await operation.request('/api/preview_repair_order_print_documents', {
          method: 'POST',
          body: repairOrderPrintRequestPayload({
            selected_document_ids: [documentType],
            active_document_id: documentType,
            template_overrides: { [documentType]: draftContent },
          }),
        });
        const documentPreview = data?.documents?.[0] || null;
        const previewHtml = documentPreview?.pages?.[0]?.html || '';
        if (previewHtml) {
          printEls.templatePreviewFrame.srcdoc = previewHtml;
          printEls.templatePreviewMeta.textContent = 'Страниц в предпросмотре: ' + Math.max(1, printFiniteNumber(documentPreview.page_count ?? documentPreview.pages?.length ?? 1, 1));
          return;
        }
        printEls.templatePreviewFrame.srcdoc = buildPrintTemplateEditorFallbackHtml(
          'Предпросмотр шаблона',
          'Сервер не вернул HTML для предпросмотра.',
        );
        printEls.templatePreviewMeta.textContent = 'Предпросмотр недоступен.';
      } catch (error) {
        if (!operation.current() || error?.code === 'stale_print_operation') return;
        const message = error.message || 'Не удалось построить предпросмотр шаблона.';
        printEls.templatePreviewFrame.srcdoc = buildPrintTemplateEditorFallbackHtml(
          'Предпросмотр шаблона',
          'Не удалось построить предпросмотр шаблона.',
          message,
        );
        printEls.templatePreviewMeta.textContent = message;
      }
    }

    async function saveCurrentPrintTemplate() {
      const operation = capturePrintOperation('', { template: true });
      const documentType = repairOrderPrintState.templateEditor.documentType || 'repair_order';
      const current = repairOrderPrintCurrentTemplateRecord();
      const saveTargetId = current && !current.is_builtin ? current.id : '';
      try {
        const data = await operation.request('/api/save_print_template', {
          method: 'POST',
          body: { source: 'ui', document_type: documentType, template_id: saveTargetId, name: printEls.templateName?.value || '', content: readPrintTemplateEditorContent() },
        });
        repairOrderPrintState.workspace.templates[documentType] = data?.templates || [];
        repairOrderPrintState.templateEditor.templateId = data?.template?.id || '';
        repairOrderPrintState.selectedTemplateIds[documentType] = data?.template?.id || repairOrderPrintState.selectedTemplateIds[documentType];
        renderPrintTemplateList();
        renderRepairOrderPrintDocuments();
        renderRepairOrderPrintTemplateSelect();
        await operation.wait(refreshRepairOrderPrintPreview());
        setStatus('Шаблон сохранен.', false);
      } catch (error) {
        if (!operation.current() || error?.code === 'stale_print_operation') return;
        setStatus(error.message, true);
      }
    }

    async function duplicateCurrentPrintTemplate() {
      const operation = capturePrintOperation('', { template: true });
      const current = repairOrderPrintCurrentTemplateRecord();
      if (!current?.id) return;
      try {
        const data = await operation.request('/api/duplicate_print_template', { method: 'POST', body: { source: 'ui', template_id: current.id } });
        const documentType = current.document_type || repairOrderPrintState.templateEditor.documentType || 'repair_order';
        repairOrderPrintState.workspace.templates[documentType] = data?.templates || [];
        repairOrderPrintState.templateEditor.templateId = data?.template?.id || '';
        renderPrintTemplateList();
        setStatus('Шаблон продублирован.', false);
      } catch (error) {
        if (!operation.current() || error?.code === 'stale_print_operation') return;
        setStatus(error.message, true);
      }
    }

    async function deleteCurrentPrintTemplate() {
      const operation = capturePrintOperation('', { template: true });
      const current = repairOrderPrintCurrentTemplateRecord();
      if (!current?.id || current.is_builtin) {
        setStatus('Встроенный шаблон удалить нельзя.', true);
        return;
      }
      if (!window.confirm('Удалить выбранный шаблон?')) return;
      try {
        const data = await operation.request('/api/delete_print_template', { method: 'POST', body: { source: 'ui', template_id: current.id } });
        const documentType = data?.document_type || repairOrderPrintState.templateEditor.documentType || 'repair_order';
        repairOrderPrintState.workspace.templates[documentType] = data?.templates || [];
        repairOrderPrintState.templateEditor.templateId = repairOrderPrintTemplatesFor(documentType)[0]?.id || '';
        renderPrintTemplateList();
        renderRepairOrderPrintDocuments();
        renderRepairOrderPrintTemplateSelect();
        await operation.wait(refreshRepairOrderPrintPreview());
        setStatus('Шаблон удален.', false);
      } catch (error) {
        if (!operation.current() || error?.code === 'stale_print_operation') return;
        setStatus(error.message, true);
      }
    }

    async function setCurrentPrintTemplateDefault() {
      const operation = capturePrintOperation('', { template: true });
      const current = repairOrderPrintCurrentTemplateRecord();
      const documentType = repairOrderPrintState.templateEditor.documentType || 'repair_order';
      if (!current?.id) return;
      try {
        const data = await operation.request('/api/set_default_print_template', { method: 'POST', body: { source: 'ui', document_type: documentType, template_id: current.id } });
        repairOrderPrintState.workspace.templates[documentType] = data?.templates || [];
        repairOrderPrintState.selectedTemplateIds[documentType] = current.id;
        renderPrintTemplateList();
        renderRepairOrderPrintDocuments();
        renderRepairOrderPrintTemplateSelect();
        await operation.wait(refreshRepairOrderPrintPreview());
        setStatus('Шаблон по умолчанию обновлен.', false);
      } catch (error) {
        if (!operation.current() || error?.code === 'stale_print_operation') return;
        setStatus(error.message, true);
      }
    }

    function createNewPrintTemplateDraft() {
      invalidatePrintTemplateContext();
      repairOrderPrintState.templateEditor.templateId = '';
      printEls.templateName.value = '';
      loadPrintTemplateEditorContent('');
      printEls.templateEditorMeta.textContent = 'Новый шаблон будет сохранен как отдельная запись.';
      printEls.templateFooterMeta.textContent = 'Сохраните шаблон, затем при необходимости сделайте его шаблоном по умолчанию.';
      printEls.templateName.focus();
    }

    function handlePrintTemplateUpload() { printEls.templateUploadInput?.click(); }

    async function handlePrintTemplateUploadChange(event) {
      const operation = capturePrintOperation('templateUpload', { template: true });
      const input = event.target;
      if (!(input instanceof HTMLInputElement) || !input.files?.length) return;
      const file = input.files[0];
      try {
        if (file.size > PRINT_TEMPLATE_UPLOAD_MAX_SIZE_BYTES) {
          setStatus('Файл шаблона слишком большой. Максимум: 256 КБ.', true);
          return;
        }
        printEls.templateName.value = (file.name || 'uploaded-template').replace(/\.[^.]+$/, '');
        loadPrintTemplateEditorContent(await operation.wait(file.text()));
        repairOrderPrintState.templateEditor.templateId = '';
      } catch (_) {
        if (!operation.current() || _?.code === 'stale_print_operation') return;
        setStatus('Не удалось прочитать файл шаблона.', true);
      } finally {
        if (!operation.current()) return;
        input.value = '';
      }
    }

    function handlePrintTemplateListClick(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const item = target.closest('[data-print-template-id]');
      if (!item) return;
      selectPrintTemplateRecord(item.dataset.printTemplateId || '');
    }

    function handlePrintTemplateDocumentTypeChange() {
      invalidatePrintTemplateContext();
      repairOrderPrintState.templateEditor.documentType = printEls.templateDocumentType?.value || 'repair_order';
      repairOrderPrintState.templateEditor.templateId = repairOrderPrintSelectedTemplateId(repairOrderPrintState.templateEditor.documentType);
      renderPrintTemplateList();
      previewCurrentPrintTemplate();
    }

"""
