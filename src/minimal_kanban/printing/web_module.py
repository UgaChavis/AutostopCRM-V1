PRINTING_WEB_MODULE_STYLE = r"""
    .dialog--repair-order-print {
      width: min(1860px, calc(100% - 18px));
      max-width: none;
      height: min(96vh, 1160px);
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
    }
    .dialog--print-template-editor {
      width: min(1880px, calc(100% - 18px));
      max-width: none;
      height: min(96vh, 1160px);
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
    }
    .repair-order-print-layout {
      min-height: 0;
      display: grid;
      grid-template-columns: 320px minmax(0, 1fr) 340px;
      gap: 14px;
      padding: 14px;
      background: rgba(0, 0, 0, 0.08);
    }
    .repair-order-print-panel,
    .print-template-editor__panel,
    .print-template-editor__editor,
    .print-template-editor__preview {
      min-height: 0;
      border: 1px solid rgba(116, 128, 111, 0.42);
      background:
        linear-gradient(180deg, rgba(255,255,255,0.03), transparent 18%),
        rgba(30, 37, 32, 0.96);
      border-radius: 16px;
      padding: 12px;
      display: flex;
      flex-direction: column;
      gap: 10px;
      overflow: hidden;
    }
    .repair-order-print-panel__title,
    .print-template-editor__title {
      font-family: var(--mono);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: var(--text-soft);
    }
    .repair-order-print-documents { display: flex; flex-direction: column; gap: 8px; min-height: 0; overflow: auto; padding-right: 4px; }
    .repair-order-print-doc {
      border: 1px solid rgba(116, 128, 111, 0.34);
      border-radius: 14px;
      padding: 10px 12px;
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      gap: 10px;
      cursor: pointer;
      background: rgba(255, 255, 255, 0.02);
      transition: border-color .15s ease, background .15s ease, transform .15s ease;
    }
    .repair-order-print-doc:hover { border-color: rgba(167, 178, 132, 0.56); background: rgba(167, 178, 132, 0.08); transform: translateY(-1px); }
    .repair-order-print-doc.is-active { border-color: rgba(167, 178, 132, 0.74); background: rgba(167, 178, 132, 0.14); box-shadow: 0 0 0 1px rgba(167, 178, 132, 0.18) inset; }
    .repair-order-print-doc__meta { min-width: 0; display: flex; flex-direction: column; gap: 4px; }
    .repair-order-print-doc__title { font-size: 13px; font-weight: 700; color: var(--text); }
    .repair-order-print-doc__description,
    .repair-order-print-doc__template,
    .repair-order-print-preview__meta,
    .repair-order-print-preview__warnings,
    .print-template-editor__meta { color: var(--text-soft); font-size: 12px; line-height: 1.4; }
    .repair-order-print-preview-wrap {
      min-height: 0;
      border: 1px solid rgba(116, 128, 111, 0.34);
      border-radius: 16px;
      background: linear-gradient(180deg, rgba(255,255,255,0.02), rgba(0,0,0,0.08)), #1a211c;
      padding: 16px;
      overflow: auto;
      position: relative;
    }
    .repair-order-print-preview-stage { min-width: 0; width: 100%; min-height: 0; display: flex; justify-content: center; align-items: flex-start; padding: 4px 0 40px; }
    .repair-order-print-preview-frame,
    .print-template-editor__preview-frame {
      width: 920px;
      height: 1180px;
      border: 0;
      border-radius: 12px;
      background: #ffffff;
      box-shadow: 0 14px 34px rgba(0, 0, 0, 0.26);
      transform-origin: top center;
      transition: transform .12s ease, width .12s ease;
    }
    .repair-order-print-toolbar { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; }
    .repair-order-print-toolbar__group { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .repair-order-print-toolbar__label { font-size: 12px; color: var(--text-soft); text-transform: uppercase; letter-spacing: 0.08em; }
    .repair-order-print-toolbar .btn { min-height: 34px; padding-inline: 10px; }
    .repair-order-print-settings { min-height: 0; overflow: auto; padding-right: 4px; display: flex; flex-direction: column; gap: 10px; }
    .repair-order-print-settings .field { gap: 5px; }
    .repair-order-print-settings input,
    .repair-order-print-settings select,
    .print-template-editor__editor textarea { background: rgba(14, 18, 15, 0.76); }
    .repair-order-print-settings__row { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
    .repair-order-print-settings__section { display: flex; flex-direction: column; gap: 8px; padding: 10px; border-radius: 14px; border: 1px solid rgba(116, 128, 111, 0.3); background: rgba(255, 255, 255, 0.02); }
    .repair-order-print-settings__section-title { font-size: 12px; text-transform: uppercase; letter-spacing: 0.12em; color: var(--text-soft); font-family: var(--mono); }
    .repair-order-print-footer,
    .print-template-editor__footer { display: flex; justify-content: space-between; gap: 12px; align-items: center; flex-wrap: wrap; }
    .repair-order-print-footer__actions,
    .print-template-editor__actions { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
    .print-template-editor { min-height: 0; display: grid; grid-template-columns: 320px minmax(0, 1fr) minmax(520px, 0.95fr); gap: 14px; padding: 14px; background: rgba(0, 0, 0, 0.08); }
    .print-template-editor__list { min-height: 0; overflow: auto; display: flex; flex-direction: column; gap: 8px; padding-right: 4px; }
    .print-template-editor__item { border: 1px solid rgba(116, 128, 111, 0.34); border-radius: 14px; padding: 10px 12px; cursor: pointer; display: flex; flex-direction: column; gap: 4px; background: rgba(255,255,255,0.02); }
    .print-template-editor__item.is-active { border-color: rgba(167, 178, 132, 0.74); background: rgba(167, 178, 132, 0.14); }
    .print-template-editor__item-title { font-size: 13px; font-weight: 700; color: var(--text); }
    .print-template-editor__editor textarea { min-height: 0; flex: 1; resize: none; font-family: var(--mono); font-size: 12px; line-height: 1.5; }
    .print-template-editor__toolbar { display: flex; gap: 8px; flex-wrap: wrap; }
    .print-template-editor__preview-wrap { min-height: 0; overflow: auto; display: flex; justify-content: center; align-items: flex-start; padding: 4px 0 32px; background: linear-gradient(180deg, rgba(255,255,255,0.02), rgba(0,0,0,0.08)), #1a211c; border: 1px solid rgba(116, 128, 111, 0.34); border-radius: 16px; }
    .repair-order-print-empty { border: 1px dashed rgba(167, 178, 132, 0.36); border-radius: 16px; padding: 28px 18px; text-align: center; color: var(--text-soft); background: rgba(255,255,255,0.02); }
    @media (max-width: 1500px) {
      .repair-order-print-layout { grid-template-columns: 290px minmax(0, 1fr); grid-template-areas: "docs preview" "settings settings"; }
      .repair-order-print-layout > .repair-order-print-panel:first-child { grid-area: docs; }
      .repair-order-print-layout > .repair-order-print-panel:nth-child(2) { grid-area: preview; }
      .repair-order-print-layout > .repair-order-print-panel:nth-child(3) { grid-area: settings; }
      .print-template-editor { grid-template-columns: 300px minmax(0, 1fr); grid-template-areas: "list editor" "preview preview"; }
      .print-template-editor__panel { grid-area: list; }
      .print-template-editor__editor { grid-area: editor; }
      .print-template-editor__preview { grid-area: preview; }
    }
    @media (max-width: 1100px) {
      .dialog--repair-order-print,
      .dialog--print-template-editor { width: min(100%, calc(100% - 12px)); height: min(100vh, 100%); }
      .repair-order-print-layout,
      .print-template-editor { grid-template-columns: 1fr; grid-template-areas: none; }
      .repair-order-print-settings__row { grid-template-columns: 1fr; }
      .repair-order-print-preview-frame,
      .print-template-editor__preview-frame { width: 760px; }
    }
"""


PRINTING_WEB_MODULE_HTML = r"""
  <div class="modal" id="repairOrderPrintModal">
    <div class="dialog dialog--repair-order-print" role="dialog" aria-modal="true" aria-labelledby="repairOrderPrintTitle">
      <div class="dialog__head">
        <div><div class="dialog__title-prefix">ПЕЧАТЬ</div><h2 class="dialog__title" id="repairOrderPrintTitle">Документы автосервиса</h2></div>
        <button class="btn btn--ghost" id="repairOrderPrintCloseX" type="button">ЗАКРЫТЬ</button>
      </div>
      <div class="dialog__body repair-order-print-layout">
        <section class="repair-order-print-panel">
          <div class="repair-order-print-panel__title">Документы</div>
          <div class="repair-order-print-preview__meta" id="repairOrderPrintDocumentsMeta">Выберите документы для печати.</div>
          <div class="repair-order-print-documents" id="repairOrderPrintDocuments"></div>
        </section>
        <section class="repair-order-print-panel">
          <div class="repair-order-print-toolbar">
            <div class="repair-order-print-toolbar__group"><span class="repair-order-print-toolbar__label" id="repairOrderPrintActiveLabel">Предпросмотр</span><span class="repair-order-print-preview__meta" id="repairOrderPrintPageMeta">Страница 1 / 1</span></div>
            <div class="repair-order-print-toolbar__group">
              <button class="btn btn--ghost" id="repairOrderPrintFitWidthButton" type="button">FIT</button>
              <button class="btn btn--ghost" id="repairOrderPrintActualSizeButton" type="button">100%</button>
              <button class="btn btn--ghost" id="repairOrderPrintZoomOutButton" type="button">-</button>
              <button class="btn btn--ghost" id="repairOrderPrintZoomInButton" type="button">+</button>
              <button class="btn btn--ghost" id="repairOrderPrintPrevPageButton" type="button">←</button>
              <button class="btn btn--ghost" id="repairOrderPrintNextPageButton" type="button">→</button>
            </div>
          </div>
          <div class="repair-order-print-preview__warnings" id="repairOrderPrintWarnings"></div>
          <div class="repair-order-print-preview-wrap" id="repairOrderPrintPreviewWrap">
            <div class="repair-order-print-preview-stage"><iframe class="repair-order-print-preview-frame" id="repairOrderPrintPreviewFrame" title="Предпросмотр документа"></iframe></div>
          </div>
        </section>
        <section class="repair-order-print-panel">
          <div class="repair-order-print-panel__title">Настройки</div>
          <div class="repair-order-print-settings" id="repairOrderPrintSettings">
            <section class="repair-order-print-settings__section">
              <div class="repair-order-print-settings__section-title">Шаблон активного документа</div>
              <div class="field field--compact"><label for="repairOrderPrintTemplateSelect">Шаблон</label><select id="repairOrderPrintTemplateSelect"></select></div>
            </section>
            <section class="repair-order-print-settings__section">
              <div class="repair-order-print-settings__section-title">Печать</div>
              <div class="field field--compact"><label for="repairOrderPrintPrinterSelect">Принтер</label><select id="repairOrderPrintPrinterSelect"></select></div>
              <div class="repair-order-print-settings__row">
                <div class="field field--compact"><label for="repairOrderPrintCopies">Копии</label><input id="repairOrderPrintCopies" type="number" min="1" max="20" step="1" value="1"></div>
                <div class="field field--compact"><label for="repairOrderPrintPaperSize">Формат</label><select id="repairOrderPrintPaperSize"><option value="A4">A4</option><option value="A5">A5</option><option value="LETTER">Letter</option></select></div>
              </div>
              <div class="field field--compact"><label for="repairOrderPrintOrientation">Ориентация</label><select id="repairOrderPrintOrientation"><option value="portrait">Портретная</option><option value="landscape">Альбомная</option></select></div>
            </section>
            <section class="repair-order-print-settings__section">
              <div class="repair-order-print-settings__section-title">Реквизиты сервиса</div>
              <div class="field field--compact"><label for="printProfileCompanyName">Компания</label><input id="printProfileCompanyName" type="text" maxlength="120"></div>
              <div class="field field--compact"><label for="printProfileLegalName">Юр. лицо</label><input id="printProfileLegalName" type="text" maxlength="160"></div>
              <div class="field field--compact"><label for="printProfileAddress">Адрес</label><input id="printProfileAddress" type="text" maxlength="240"></div>
              <div class="repair-order-print-settings__row"><div class="field field--compact"><label for="printProfilePhone">Телефон</label><input id="printProfilePhone" type="text" maxlength="80"></div><div class="field field--compact"><label for="printProfileEmail">Email</label><input id="printProfileEmail" type="text" maxlength="120"></div></div>
              <div class="repair-order-print-settings__row"><div class="field field--compact"><label for="printProfileInn">ИНН</label><input id="printProfileInn" type="text" maxlength="32"></div><div class="field field--compact"><label for="printProfileKpp">КПП</label><input id="printProfileKpp" type="text" maxlength="32"></div></div>
              <div class="field field--compact"><label for="printProfileOgrn">ОГРН</label><input id="printProfileOgrn" type="text" maxlength="32"></div>
              <div class="repair-order-print-settings__row"><div class="field field--compact"><label for="printProfileBankName">Банк</label><input id="printProfileBankName" type="text" maxlength="160"></div><div class="field field--compact"><label for="printProfileBik">БИК</label><input id="printProfileBik" type="text" maxlength="32"></div></div>
              <div class="repair-order-print-settings__row"><div class="field field--compact"><label for="printProfileSettlementAccount">Р/с</label><input id="printProfileSettlementAccount" type="text" maxlength="64"></div><div class="field field--compact"><label for="printProfileCorrespondentAccount">К/с</label><input id="printProfileCorrespondentAccount" type="text" maxlength="64"></div></div>
              <div class="field field--compact"><label for="printProfileTaxLabel">Налоговый режим</label><input id="printProfileTaxLabel" type="text" maxlength="48"></div>
              <button class="btn btn--ghost" id="repairOrderPrintSaveSettingsButton" type="button">СОХРАНИТЬ НАСТРОЙКИ</button>
            </section>
          </div>
        </section>
      </div>
      <div class="dialog__foot repair-order-print-footer">
        <div class="repair-order-print-preview__meta" id="repairOrderPrintFooterMeta">PDF генерируется из шаблона и текущих данных заказ-наряда.</div>
        <div class="repair-order-print-footer__actions"><button class="btn btn--ghost" id="repairOrderPrintTemplateEditorButton" type="button">ШАБЛОНЫ</button><button class="btn btn--ghost" id="repairOrderPrintExportButton" type="button">PDF</button><button class="btn" id="repairOrderPrintRunButton" type="button">ПЕЧАТЬ</button></div>
      </div>
    </div>
  </div>

  <div class="modal" id="printTemplateEditorModal">
    <div class="dialog dialog--print-template-editor" role="dialog" aria-modal="true" aria-labelledby="printTemplateEditorTitle">
      <div class="dialog__head">
        <div><div class="dialog__title-prefix">ШАБЛОНЫ</div><h2 class="dialog__title" id="printTemplateEditorTitle">Редактор печатных шаблонов</h2></div>
        <button class="btn btn--ghost" id="printTemplateEditorCloseX" type="button">ЗАКРЫТЬ</button>
      </div>
      <div class="dialog__body print-template-editor">
        <aside class="print-template-editor__panel">
          <div class="print-template-editor__title">Тип документа</div>
          <div class="field field--compact"><label for="printTemplateDocumentType">Документ</label><select id="printTemplateDocumentType"></select></div>
          <div class="print-template-editor__toolbar">
            <button class="btn btn--ghost" id="printTemplateNewButton" type="button">НОВЫЙ</button>
            <button class="btn btn--ghost" id="printTemplateDuplicateButton" type="button">ДУБЛЬ</button>
            <button class="btn btn--ghost" id="printTemplateDeleteButton" type="button">УДАЛИТЬ</button>
            <button class="btn btn--ghost" id="printTemplateUploadButton" type="button">ЗАГРУЗИТЬ</button>
            <input id="printTemplateUploadInput" type="file" accept=".html,.htm,.txt,.tpl" hidden>
          </div>
          <div class="print-template-editor__meta" id="printTemplateListMeta">Выберите шаблон или создайте новый.</div>
          <div class="print-template-editor__list" id="printTemplateList"></div>
        </aside>
        <section class="print-template-editor__editor">
          <div class="print-template-editor__title">Редактор</div>
          <div class="field field--compact"><label for="printTemplateName">Название</label><input id="printTemplateName" type="text" maxlength="120"></div>
          <div class="print-template-editor__meta" id="printTemplateEditorMeta">Поддерживаются placeholders вроде {{client.name_display}} и секции {{#works}}...{{/works}}.</div>
          <textarea id="printTemplateContent" spellcheck="false"></textarea>
        </section>
        <section class="print-template-editor__preview">
          <div class="print-template-editor__title">Предпросмотр</div>
          <div class="print-template-editor__meta" id="printTemplatePreviewMeta">Предпросмотр использует текущий заказ-наряд как тестовые данные.</div>
          <div class="print-template-editor__preview-wrap"><iframe class="print-template-editor__preview-frame" id="printTemplatePreviewFrame" title="Предпросмотр шаблона"></iframe></div>
        </section>
      </div>
      <div class="dialog__foot print-template-editor__footer">
        <div class="print-template-editor__meta" id="printTemplateFooterMeta">Встроенные шаблоны можно дублировать и делать шаблоном по умолчанию.</div>
        <div class="print-template-editor__actions"><button class="btn btn--ghost" id="printTemplateSetDefaultButton" type="button">ПО УМОЛЧАНИЮ</button><button class="btn btn--ghost" id="printTemplatePreviewButton" type="button">ПРЕДПРОСМОТР</button><button class="btn" id="printTemplateSaveButton" type="button">СОХРАНИТЬ</button></div>
      </div>
    </div>
  </div>
"""


_PRINTING_SCRIPT_PART1 = r"""
    const repairOrderPrintState = {
      workspace: null,
      selectedDocumentIds: ['repair_order'],
      activeDocumentId: 'repair_order',
      selectedTemplateIds: {},
      previewByDocument: {},
      pageIndexByDocument: {},
      previewToken: 0,
      zoomMode: 'fit',
      zoom: 1,
      templateEditor: { documentType: 'repair_order', templateId: '' },
    };

    const printEls = {
      modal: document.getElementById('repairOrderPrintModal'),
      closeX: document.getElementById('repairOrderPrintCloseX'),
      documentsMeta: document.getElementById('repairOrderPrintDocumentsMeta'),
      documents: document.getElementById('repairOrderPrintDocuments'),
      activeLabel: document.getElementById('repairOrderPrintActiveLabel'),
      pageMeta: document.getElementById('repairOrderPrintPageMeta'),
      warnings: document.getElementById('repairOrderPrintWarnings'),
      previewWrap: document.getElementById('repairOrderPrintPreviewWrap'),
      previewFrame: document.getElementById('repairOrderPrintPreviewFrame'),
      fitWidthButton: document.getElementById('repairOrderPrintFitWidthButton'),
      actualSizeButton: document.getElementById('repairOrderPrintActualSizeButton'),
      zoomOutButton: document.getElementById('repairOrderPrintZoomOutButton'),
      zoomInButton: document.getElementById('repairOrderPrintZoomInButton'),
      prevPageButton: document.getElementById('repairOrderPrintPrevPageButton'),
      nextPageButton: document.getElementById('repairOrderPrintNextPageButton'),
      templateSelect: document.getElementById('repairOrderPrintTemplateSelect'),
      printerSelect: document.getElementById('repairOrderPrintPrinterSelect'),
      copies: document.getElementById('repairOrderPrintCopies'),
      paperSize: document.getElementById('repairOrderPrintPaperSize'),
      orientation: document.getElementById('repairOrderPrintOrientation'),
      saveSettingsButton: document.getElementById('repairOrderPrintSaveSettingsButton'),
      exportButton: document.getElementById('repairOrderPrintExportButton'),
      printButton: document.getElementById('repairOrderPrintRunButton'),
      templateEditorButton: document.getElementById('repairOrderPrintTemplateEditorButton'),
      footerMeta: document.getElementById('repairOrderPrintFooterMeta'),
      profileCompanyName: document.getElementById('printProfileCompanyName'),
      profileLegalName: document.getElementById('printProfileLegalName'),
      profileAddress: document.getElementById('printProfileAddress'),
      profilePhone: document.getElementById('printProfilePhone'),
      profileEmail: document.getElementById('printProfileEmail'),
      profileInn: document.getElementById('printProfileInn'),
      profileKpp: document.getElementById('printProfileKpp'),
      profileOgrn: document.getElementById('printProfileOgrn'),
      profileBankName: document.getElementById('printProfileBankName'),
      profileBik: document.getElementById('printProfileBik'),
      profileSettlementAccount: document.getElementById('printProfileSettlementAccount'),
      profileCorrespondentAccount: document.getElementById('printProfileCorrespondentAccount'),
      profileTaxLabel: document.getElementById('printProfileTaxLabel'),
      templateModal: document.getElementById('printTemplateEditorModal'),
      templateCloseX: document.getElementById('printTemplateEditorCloseX'),
      templateDocumentType: document.getElementById('printTemplateDocumentType'),
      templateListMeta: document.getElementById('printTemplateListMeta'),
      templateList: document.getElementById('printTemplateList'),
      templateName: document.getElementById('printTemplateName'),
      templateContent: document.getElementById('printTemplateContent'),
      templateEditorMeta: document.getElementById('printTemplateEditorMeta'),
      templatePreviewMeta: document.getElementById('printTemplatePreviewMeta'),
      templatePreviewFrame: document.getElementById('printTemplatePreviewFrame'),
      templateFooterMeta: document.getElementById('printTemplateFooterMeta'),
      templateNewButton: document.getElementById('printTemplateNewButton'),
      templateDuplicateButton: document.getElementById('printTemplateDuplicateButton'),
      templateDeleteButton: document.getElementById('printTemplateDeleteButton'),
      templateUploadButton: document.getElementById('printTemplateUploadButton'),
      templateUploadInput: document.getElementById('printTemplateUploadInput'),
      templateSetDefaultButton: document.getElementById('printTemplateSetDefaultButton'),
      templatePreviewButton: document.getElementById('printTemplatePreviewButton'),
      templateSaveButton: document.getElementById('printTemplateSaveButton'),
    };

    function repairOrderPrintWorkspaceDocuments() {
      return Array.isArray(repairOrderPrintState.workspace?.documents) ? repairOrderPrintState.workspace.documents : [];
    }

    function repairOrderPrintDocumentMap() {
      return Object.fromEntries(repairOrderPrintWorkspaceDocuments().map((item) => [item.id, item]));
    }

    function normalizeRepairOrderPrintSelectedIds(value) {
      const docs = repairOrderPrintWorkspaceDocuments();
      const validIds = new Set(docs.map((item) => item.id));
      const normalized = Array.isArray(value)
        ? value.map((item) => String(item || '').trim()).filter((item, index, arr) => item && validIds.has(item) && arr.indexOf(item) === index)
        : [];
      return normalized.length ? normalized : (validIds.has('repair_order') ? ['repair_order'] : docs.slice(0, 1).map((item) => item.id));
    }

    function repairOrderPrintSelectedIds() {
      repairOrderPrintState.selectedDocumentIds = normalizeRepairOrderPrintSelectedIds(repairOrderPrintState.selectedDocumentIds);
      return repairOrderPrintState.selectedDocumentIds.slice();
    }

    function repairOrderPrintActiveDocument() {
      const selectedIds = repairOrderPrintSelectedIds();
      if (!selectedIds.includes(repairOrderPrintState.activeDocumentId)) {
        repairOrderPrintState.activeDocumentId = selectedIds[0] || 'repair_order';
      }
      return repairOrderPrintState.activeDocumentId;
    }

    function repairOrderPrintTemplatesFor(documentType) {
      const templates = repairOrderPrintState.workspace?.templates?.[documentType];
      return Array.isArray(templates) ? templates : [];
    }

    function repairOrderPrintSelectedTemplateId(documentType) {
      const templates = repairOrderPrintTemplatesFor(documentType);
      const candidate = String(repairOrderPrintState.selectedTemplateIds?.[documentType] || '').trim();
      if (candidate && templates.some((item) => item.id === candidate)) return candidate;
      const defaultTemplate = templates.find((item) => item.is_default) || templates[0] || null;
      const nextId = defaultTemplate?.id || '';
      repairOrderPrintState.selectedTemplateIds[documentType] = nextId;
      return nextId;
    }

    function repairOrderPrintCurrentPreview() {
      return repairOrderPrintState.previewByDocument?.[repairOrderPrintActiveDocument()] || null;
    }

    function repairOrderPrintCurrentPageIndex() {
      const activeId = repairOrderPrintActiveDocument();
      const preview = repairOrderPrintCurrentPreview();
      const pageCount = Math.max(1, Number(preview?.page_count || preview?.pages?.length || 1));
      const current = Number(repairOrderPrintState.pageIndexByDocument?.[activeId] || 0);
      return Math.max(0, Math.min(pageCount - 1, current));
    }

    function repairOrderPrintSetPageIndex(index) {
      repairOrderPrintState.pageIndexByDocument[repairOrderPrintActiveDocument()] = index;
      renderRepairOrderPrintPreview();
    }

    function repairOrderPrintSettingsPayload() {
      return {
        default_printer: printEls.printerSelect?.value || '',
        copies: Number(printEls.copies?.value || 1) || 1,
        paper_size: printEls.paperSize?.value || 'A4',
        orientation: printEls.orientation?.value || 'portrait',
        service_profile: {
          company_name: printEls.profileCompanyName?.value || '',
          legal_name: printEls.profileLegalName?.value || '',
          address: printEls.profileAddress?.value || '',
          phone: printEls.profilePhone?.value || '',
          email: printEls.profileEmail?.value || '',
          inn: printEls.profileInn?.value || '',
          kpp: printEls.profileKpp?.value || '',
          ogrn: printEls.profileOgrn?.value || '',
          bank_name: printEls.profileBankName?.value || '',
          bik: printEls.profileBik?.value || '',
          settlement_account: printEls.profileSettlementAccount?.value || '',
          correspondent_account: printEls.profileCorrespondentAccount?.value || '',
          tax_label: printEls.profileTaxLabel?.value || '',
        },
      };
    }

    function repairOrderPrintRequestPayload(extra = {}) {
      return {
        card_id: state.editingId || state.activeCard?.id || '',
        source: 'ui',
        repair_order: readRepairOrderFromForm(),
        selected_document_ids: repairOrderPrintSelectedIds(),
        active_document_id: repairOrderPrintActiveDocument(),
        selected_template_ids: { ...repairOrderPrintState.selectedTemplateIds },
        print_settings: repairOrderPrintSettingsPayload(),
        ...extra,
      };
    }
"""


_PRINTING_SCRIPT_PART2 = r"""
    function applyRepairOrderPrintWorkspace(data, { preserveSelection = false } = {}) {
      const previousSelected = preserveSelection ? repairOrderPrintSelectedIds() : ['repair_order'];
      const previousActive = preserveSelection ? repairOrderPrintActiveDocument() : 'repair_order';
      const previousTemplates = preserveSelection ? { ...repairOrderPrintState.selectedTemplateIds } : {};
      repairOrderPrintState.workspace = data || { documents: [], templates: {}, settings: {}, printers: [] };
      const docs = repairOrderPrintWorkspaceDocuments();
      const selected = normalizeRepairOrderPrintSelectedIds(preserveSelection ? previousSelected : ['repair_order']);
      repairOrderPrintState.selectedDocumentIds = selected;
      repairOrderPrintState.activeDocumentId = selected.includes(previousActive) ? previousActive : (selected[0] || docs[0]?.id || 'repair_order');
      repairOrderPrintState.selectedTemplateIds = {};
      docs.forEach((item) => {
        repairOrderPrintState.selectedTemplateIds[item.id] = previousTemplates[item.id] || item.selected_template_id || '';
      });
      const settings = repairOrderPrintState.workspace?.settings || {};
      const profile = settings.service_profile || {};
      if (printEls.printerSelect) {
        printEls.printerSelect.innerHTML = (data?.printers || []).map((printer) => '<option value="' + escapeHtml(printer.name) + '"' + (printer.is_default ? ' selected' : '') + '>' + escapeHtml(printer.label || printer.name) + '</option>').join('') || '<option value="">PDF export only</option>';
      }
      if (printEls.copies) printEls.copies.value = String(settings.copies || 1);
      if (printEls.paperSize) printEls.paperSize.value = settings.paper_size || 'A4';
      if (printEls.orientation) printEls.orientation.value = settings.orientation || 'portrait';
      if (printEls.profileCompanyName) printEls.profileCompanyName.value = profile.company_name || '';
      if (printEls.profileLegalName) printEls.profileLegalName.value = profile.legal_name || '';
      if (printEls.profileAddress) printEls.profileAddress.value = profile.address || '';
      if (printEls.profilePhone) printEls.profilePhone.value = profile.phone || '';
      if (printEls.profileEmail) printEls.profileEmail.value = profile.email || '';
      if (printEls.profileInn) printEls.profileInn.value = profile.inn || '';
      if (printEls.profileKpp) printEls.profileKpp.value = profile.kpp || '';
      if (printEls.profileOgrn) printEls.profileOgrn.value = profile.ogrn || '';
      if (printEls.profileBankName) printEls.profileBankName.value = profile.bank_name || '';
      if (printEls.profileBik) printEls.profileBik.value = profile.bik || '';
      if (printEls.profileSettlementAccount) printEls.profileSettlementAccount.value = profile.settlement_account || '';
      if (printEls.profileCorrespondentAccount) printEls.profileCorrespondentAccount.value = profile.correspondent_account || '';
      if (printEls.profileTaxLabel) printEls.profileTaxLabel.value = profile.tax_label || '';
      renderRepairOrderPrintDocuments();
      renderRepairOrderPrintTemplateSelect();
      renderPrintTemplateDocumentTypeOptions();
      syncRepairOrderPrintPrinterState();
    }

    function renderRepairOrderPrintDocuments() {
      const docs = repairOrderPrintWorkspaceDocuments();
      const selected = new Set(repairOrderPrintSelectedIds());
      printEls.documents.innerHTML = docs.length ? docs.map((item) => {
        const activeClass = item.id === repairOrderPrintActiveDocument() ? ' is-active' : '';
        const checked = selected.has(item.id) ? ' checked' : '';
        const templateId = repairOrderPrintSelectedTemplateId(item.id);
        const template = repairOrderPrintTemplatesFor(item.id).find((candidate) => candidate.id === templateId);
        return '<label class="repair-order-print-doc' + activeClass + '" data-print-document="' + escapeHtml(item.id) + '">' +
          '<input type="checkbox" data-print-document-toggle="' + escapeHtml(item.id) + '"' + checked + '>' +
          '<div class="repair-order-print-doc__meta">' +
            '<div class="repair-order-print-doc__title">' + escapeHtml(item.label) + '</div>' +
            '<div class="repair-order-print-doc__description">' + escapeHtml(item.description || '') + '</div>' +
            '<div class="repair-order-print-doc__template">Шаблон: ' + escapeHtml(template?.name || 'не выбран') + '</div>' +
          '</div>' +
        '</label>';
      }).join('') : '<div class="repair-order-print-empty">Документы для печати пока недоступны.</div>';
      printEls.documentsMeta.textContent = docs.length ? ('Выбрано документов: ' + repairOrderPrintSelectedIds().length) : 'Документы для печати отсутствуют.';
    }

    function renderRepairOrderPrintTemplateSelect() {
      const activeId = repairOrderPrintActiveDocument();
      const templates = repairOrderPrintTemplatesFor(activeId);
      const selectedTemplateId = repairOrderPrintSelectedTemplateId(activeId);
      printEls.templateSelect.innerHTML = templates.length
        ? templates.map((item) => '<option value="' + escapeHtml(item.id) + '"' + (item.id === selectedTemplateId ? ' selected' : '') + '>' + escapeHtml(item.name) + (item.is_default ? ' · default' : '') + '</option>').join('')
        : '<option value="">Шаблонов нет</option>';
    }

    function syncRepairOrderPrintPrinterState() {
      const printers = Array.isArray(repairOrderPrintState.workspace?.printers) ? repairOrderPrintState.workspace.printers : [];
      const hasPrinters = printers.length > 0;
      if (printEls.printerSelect) {
        printEls.printerSelect.disabled = !hasPrinters;
      }
      if (printEls.printButton) {
        printEls.printButton.disabled = !hasPrinters;
        printEls.printButton.title = hasPrinters ? '' : 'Принтер недоступен. Используйте PDF-экспорт.';
      }
    }

    function repairOrderPrintScale() {
      if (repairOrderPrintState.zoomMode === 'fit') {
        const availableWidth = Math.max(560, (printEls.previewWrap?.clientWidth || 920) - 48);
        return Math.max(0.55, Math.min(1.2, availableWidth / 920));
      }
      return Math.max(0.4, Math.min(2, Number(repairOrderPrintState.zoom || 1)));
    }

    function applyRepairOrderPrintZoom() {
      const scale = repairOrderPrintScale();
      if (!printEls.previewFrame) return;
      printEls.previewFrame.style.transform = 'scale(' + scale + ')';
      printEls.previewFrame.style.width = (920 / Math.max(scale, 0.001)) + 'px';
    }

    function renderRepairOrderPrintPreview() {
      const docs = repairOrderPrintWorkspaceDocuments();
      const activeId = repairOrderPrintActiveDocument();
      const activeDoc = docs.find((item) => item.id === activeId) || null;
      const preview = repairOrderPrintCurrentPreview();
      const pageIndex = repairOrderPrintCurrentPageIndex();
      const page = preview?.pages?.[pageIndex] || null;
      printEls.activeLabel.textContent = activeDoc ? ('Предпросмотр · ' + activeDoc.label) : 'Предпросмотр';
      printEls.pageMeta.textContent = preview ? ('Страница ' + (pageIndex + 1) + ' / ' + Math.max(1, preview.page_count || preview.pages?.length || 1)) : 'Страница 1 / 1';
      printEls.prevPageButton.disabled = !preview || pageIndex <= 0;
      printEls.nextPageButton.disabled = !preview || pageIndex >= Math.max(0, (preview.page_count || preview.pages?.length || 1) - 1);
      const warnings = [];
      if (Array.isArray(preview?.warnings)) warnings.push(...preview.warnings);
      if (Array.isArray(preview?.missing_fields) && preview.missing_fields.length) warnings.push('Проверьте поля: ' + preview.missing_fields.join(', '));
      printEls.warnings.textContent = warnings.join(' · ');
      printEls.previewFrame.srcdoc = page?.html || '<!doctype html><html lang="ru"><body style="font-family: Segoe UI, sans-serif; padding: 32px; color: #444">Выберите документ для предпросмотра.</body></html>';
      printEls.footerMeta.textContent = preview ? ('Документ: ' + (activeDoc?.label || activeId) + '. Страниц: ' + Math.max(1, preview.page_count || preview.pages?.length || 1) + '.') : 'PDF генерируется из шаблона и текущих данных заказ-наряда.';
      applyRepairOrderPrintZoom();
    }

    async function refreshRepairOrderPrintPreview(extra = {}) {
      if (!repairOrderPrintState.workspace) return;
      const requestToken = ++repairOrderPrintState.previewToken;
      try {
        const data = await api('/api/preview_repair_order_print_documents', {
          method: 'POST',
          body: repairOrderPrintRequestPayload(extra),
        });
        if (requestToken !== repairOrderPrintState.previewToken) return;
        repairOrderPrintState.previewByDocument = Object.fromEntries((data?.documents || []).map((item) => [item.id, item]));
        renderRepairOrderPrintDocuments();
        renderRepairOrderPrintTemplateSelect();
        renderRepairOrderPrintPreview();
      } catch (error) {
        if (requestToken !== repairOrderPrintState.previewToken) return;
        setStatus(error.message, true);
      }
    }

    async function loadRepairOrderPrintWorkspace({ openModal = false, preserveSelection = false } = {}) {
      const cardId = await requireRepairOrderCardId();
      if (!cardId) return null;
      const data = await api('/api/get_repair_order_print_workspace', {
        method: 'POST',
        body: {
          card_id: cardId,
          source: 'ui',
          repair_order: readRepairOrderFromForm(),
        },
      });
      applyRepairOrderPrintWorkspace(data, { preserveSelection });
      if (openModal) printEls.modal.classList.add('is-open');
      await refreshRepairOrderPrintPreview();
      return data;
    }

    async function openRepairOrderPrintWorkspace() {
      try {
        await loadRepairOrderPrintWorkspace({ openModal: true, preserveSelection: Boolean(repairOrderPrintState.workspace) });
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    function closeRepairOrderPrintWorkspace() {
      printEls.modal.classList.remove('is-open');
    }
"""


_PRINTING_SCRIPT_PART3 = r"""
    function base64ToBlob(base64, mimeType = 'application/octet-stream') {
      const binary = atob(String(base64 || ''));
      const bytes = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
      return new Blob([bytes], { type: mimeType });
    }

    async function exportRepairOrderPrintPdf() {
      try {
        const data = await api('/api/export_repair_order_print_pdf', {
          method: 'POST',
          body: repairOrderPrintRequestPayload(),
        });
        triggerBlobDownload(base64ToBlob(data?.content_base64 || '', 'application/pdf'), data?.file_name || 'autostopcrm-print.pdf');
        setStatus('PDF подготовлен.', false);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function runRepairOrderPrintJob() {
      try {
        const data = await api('/api/print_repair_order_documents', {
          method: 'POST',
          body: repairOrderPrintRequestPayload({ printer_name: printEls.printerSelect?.value || '' }),
        });
        setStatus('Документы отправлены на принтер: ' + (data?.printer_name || 'неизвестно') + '.', false);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function saveRepairOrderPrintSettings() {
      try {
        const data = await api('/api/save_print_module_settings', {
          method: 'POST',
          body: { source: 'ui', print_settings: repairOrderPrintSettingsPayload() },
        });
        repairOrderPrintState.workspace = { ...(repairOrderPrintState.workspace || {}), settings: data?.settings || {}, printers: data?.printers || [] };
        if (Array.isArray(data?.printers)) {
          printEls.printerSelect.innerHTML = data.printers.map((printer) => '<option value="' + escapeHtml(printer.name) + '"' + (printer.is_default ? ' selected' : '') + '>' + escapeHtml(printer.label || printer.name) + '</option>').join('') || '<option value="">PDF export only</option>';
        }
        syncRepairOrderPrintPrinterState();
        setStatus('Настройки печати сохранены.', false);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    function handleRepairOrderPrintDocumentsChange(event) {
      const target = event.target;
      if (!(target instanceof HTMLInputElement)) return;
      const documentId = target.dataset.printDocumentToggle;
      if (!documentId) return;
      const next = new Set(repairOrderPrintSelectedIds());
      if (target.checked) next.add(documentId); else next.delete(documentId);
      repairOrderPrintState.selectedDocumentIds = Array.from(next);
      if (!repairOrderPrintState.selectedDocumentIds.length) repairOrderPrintState.selectedDocumentIds = [documentId];
      if (!repairOrderPrintState.selectedDocumentIds.includes(repairOrderPrintActiveDocument())) repairOrderPrintState.activeDocumentId = repairOrderPrintState.selectedDocumentIds[0];
      renderRepairOrderPrintDocuments();
      renderRepairOrderPrintTemplateSelect();
      refreshRepairOrderPrintPreview();
    }

    function handleRepairOrderPrintDocumentsClick(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const card = target.closest('[data-print-document]');
      if (!card) return;
      repairOrderPrintState.activeDocumentId = card.dataset.printDocument || 'repair_order';
      renderRepairOrderPrintDocuments();
      renderRepairOrderPrintTemplateSelect();
      renderRepairOrderPrintPreview();
    }

    function handleRepairOrderPrintTemplateSelectChange() {
      repairOrderPrintState.selectedTemplateIds[repairOrderPrintActiveDocument()] = printEls.templateSelect?.value || '';
      refreshRepairOrderPrintPreview();
    }

    function repairOrderPrintSetZoom(mode, zoomValue = repairOrderPrintState.zoom) {
      repairOrderPrintState.zoomMode = mode;
      repairOrderPrintState.zoom = zoomValue;
      applyRepairOrderPrintZoom();
    }

    function repairOrderPrintCurrentTemplateRecord() {
      const documentType = repairOrderPrintState.templateEditor.documentType || 'repair_order';
      const templateId = repairOrderPrintState.templateEditor.templateId || '';
      return repairOrderPrintTemplatesFor(documentType).find((item) => item.id === templateId) || null;
    }

    function renderPrintTemplateDocumentTypeOptions() {
      const docs = repairOrderPrintWorkspaceDocuments();
      const current = repairOrderPrintState.templateEditor.documentType || repairOrderPrintActiveDocument() || 'repair_order';
      printEls.templateDocumentType.innerHTML = docs.map((item) => '<option value="' + escapeHtml(item.id) + '"' + (item.id === current ? ' selected' : '') + '>' + escapeHtml(item.label) + '</option>').join('');
      repairOrderPrintState.templateEditor.documentType = current;
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
      printEls.templateContent.value = current?.content || '';
      printEls.templateEditorMeta.textContent = current ? ('Источник: ' + (current.source || 'custom') + (current.is_builtin ? '. Встроенный шаблон можно сохранить как новый.' : '.')) : 'Новый шаблон можно сохранить как отдельную запись.';
      printEls.templateFooterMeta.textContent = current?.is_default ? 'Этот шаблон уже используется по умолчанию.' : 'Можно сделать текущий шаблон шаблоном по умолчанию.';
    }

    function openPrintTemplateEditor() {
      repairOrderPrintState.templateEditor.documentType = repairOrderPrintActiveDocument() || 'repair_order';
      repairOrderPrintState.templateEditor.templateId = repairOrderPrintSelectedTemplateId(repairOrderPrintState.templateEditor.documentType);
      renderPrintTemplateDocumentTypeOptions();
      renderPrintTemplateList();
      printEls.templateModal.classList.add('is-open');
      previewCurrentPrintTemplate();
    }

    function closePrintTemplateEditor() {
      printEls.templateModal.classList.remove('is-open');
    }

    function selectPrintTemplateRecord(templateId) {
      repairOrderPrintState.templateEditor.templateId = templateId;
      renderPrintTemplateList();
      previewCurrentPrintTemplate();
    }

    async function previewCurrentPrintTemplate() {
      const documentType = repairOrderPrintState.templateEditor.documentType || 'repair_order';
      try {
        const data = await api('/api/preview_repair_order_print_documents', {
          method: 'POST',
          body: repairOrderPrintRequestPayload({
            selected_document_ids: [documentType],
            active_document_id: documentType,
            template_overrides: { [documentType]: printEls.templateContent?.value || '' },
          }),
        });
        const documentPreview = data?.documents?.[0] || null;
        printEls.templatePreviewFrame.srcdoc = documentPreview?.pages?.[0]?.html || '<!doctype html><html><body style="font-family:Segoe UI;padding:32px">Нет данных для предпросмотра.</body></html>';
        printEls.templatePreviewMeta.textContent = documentPreview ? ('Страниц в предпросмотре: ' + Math.max(1, documentPreview.page_count || documentPreview.pages?.length || 1)) : 'Предпросмотр недоступен.';
      } catch (error) {
        printEls.templatePreviewMeta.textContent = error.message || 'Не удалось построить предпросмотр шаблона.';
      }
    }

    async function saveCurrentPrintTemplate() {
      const documentType = repairOrderPrintState.templateEditor.documentType || 'repair_order';
      const current = repairOrderPrintCurrentTemplateRecord();
      const saveTargetId = current && !current.is_builtin ? current.id : '';
      try {
        const data = await api('/api/save_print_template', {
          method: 'POST',
          body: { source: 'ui', document_type: documentType, template_id: saveTargetId, name: printEls.templateName?.value || '', content: printEls.templateContent?.value || '' },
        });
        repairOrderPrintState.workspace.templates[documentType] = data?.templates || [];
        repairOrderPrintState.templateEditor.templateId = data?.template?.id || '';
        repairOrderPrintState.selectedTemplateIds[documentType] = data?.template?.id || repairOrderPrintState.selectedTemplateIds[documentType];
        renderPrintTemplateList();
        renderRepairOrderPrintDocuments();
        renderRepairOrderPrintTemplateSelect();
        await refreshRepairOrderPrintPreview();
        setStatus('Шаблон сохранен.', false);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function duplicateCurrentPrintTemplate() {
      const current = repairOrderPrintCurrentTemplateRecord();
      if (!current?.id) return;
      try {
        const data = await api('/api/duplicate_print_template', { method: 'POST', body: { source: 'ui', template_id: current.id } });
        const documentType = current.document_type || repairOrderPrintState.templateEditor.documentType || 'repair_order';
        repairOrderPrintState.workspace.templates[documentType] = data?.templates || [];
        repairOrderPrintState.templateEditor.templateId = data?.template?.id || '';
        renderPrintTemplateList();
        setStatus('Шаблон продублирован.', false);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function deleteCurrentPrintTemplate() {
      const current = repairOrderPrintCurrentTemplateRecord();
      if (!current?.id || current.is_builtin) {
        setStatus('Встроенный шаблон удалить нельзя.', true);
        return;
      }
      if (!window.confirm('Удалить выбранный шаблон?')) return;
      try {
        const data = await api('/api/delete_print_template', { method: 'POST', body: { source: 'ui', template_id: current.id } });
        const documentType = data?.document_type || repairOrderPrintState.templateEditor.documentType || 'repair_order';
        repairOrderPrintState.workspace.templates[documentType] = data?.templates || [];
        repairOrderPrintState.templateEditor.templateId = repairOrderPrintTemplatesFor(documentType)[0]?.id || '';
        renderPrintTemplateList();
        renderRepairOrderPrintDocuments();
        renderRepairOrderPrintTemplateSelect();
        await refreshRepairOrderPrintPreview();
        setStatus('Шаблон удален.', false);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function setCurrentPrintTemplateDefault() {
      const current = repairOrderPrintCurrentTemplateRecord();
      const documentType = repairOrderPrintState.templateEditor.documentType || 'repair_order';
      if (!current?.id) return;
      try {
        const data = await api('/api/set_default_print_template', { method: 'POST', body: { source: 'ui', document_type: documentType, template_id: current.id } });
        repairOrderPrintState.workspace.templates[documentType] = data?.templates || [];
        repairOrderPrintState.selectedTemplateIds[documentType] = current.id;
        renderPrintTemplateList();
        renderRepairOrderPrintDocuments();
        renderRepairOrderPrintTemplateSelect();
        await refreshRepairOrderPrintPreview();
        setStatus('Шаблон по умолчанию обновлен.', false);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    function createNewPrintTemplateDraft() {
      repairOrderPrintState.templateEditor.templateId = '';
      printEls.templateName.value = '';
      printEls.templateContent.value = '';
      printEls.templateEditorMeta.textContent = 'Новый шаблон будет сохранен как отдельная запись.';
      printEls.templateFooterMeta.textContent = 'Сохраните шаблон, затем при необходимости сделайте его шаблоном по умолчанию.';
      printEls.templateName.focus();
    }

    function handlePrintTemplateUpload() { printEls.templateUploadInput?.click(); }

    async function handlePrintTemplateUploadChange(event) {
      const input = event.target;
      if (!(input instanceof HTMLInputElement) || !input.files?.length) return;
      const file = input.files[0];
      try {
        printEls.templateName.value = (file.name || 'uploaded-template').replace(/\.[^.]+$/, '');
        printEls.templateContent.value = await file.text();
        repairOrderPrintState.templateEditor.templateId = '';
        previewCurrentPrintTemplate();
      } catch (_) {
        setStatus('Не удалось прочитать файл шаблона.', true);
      } finally {
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
      repairOrderPrintState.templateEditor.documentType = printEls.templateDocumentType?.value || 'repair_order';
      repairOrderPrintState.templateEditor.templateId = repairOrderPrintSelectedTemplateId(repairOrderPrintState.templateEditor.documentType);
      renderPrintTemplateList();
      previewCurrentPrintTemplate();
    }

    function handleRepairOrderPrintModalOverlayClick(event) {
      if (event.target === printEls.modal) closeRepairOrderPrintWorkspace();
    }

    function handlePrintTemplateEditorOverlayClick(event) {
      if (event.target === printEls.templateModal) closePrintTemplateEditor();
    }

    printRepairOrderDraft = function() { return openRepairOrderPrintWorkspace(); };

    if (printEls.documents) printEls.documents.addEventListener('change', handleRepairOrderPrintDocumentsChange);
    if (printEls.documents) printEls.documents.addEventListener('click', handleRepairOrderPrintDocumentsClick);
    if (printEls.templateSelect) printEls.templateSelect.addEventListener('change', handleRepairOrderPrintTemplateSelectChange);
    if (printEls.fitWidthButton) printEls.fitWidthButton.addEventListener('click', () => repairOrderPrintSetZoom('fit', 1));
    if (printEls.actualSizeButton) printEls.actualSizeButton.addEventListener('click', () => repairOrderPrintSetZoom('manual', 1));
    if (printEls.zoomInButton) printEls.zoomInButton.addEventListener('click', () => repairOrderPrintSetZoom('manual', repairOrderPrintScale() + 0.1));
    if (printEls.zoomOutButton) printEls.zoomOutButton.addEventListener('click', () => repairOrderPrintSetZoom('manual', repairOrderPrintScale() - 0.1));
    if (printEls.prevPageButton) printEls.prevPageButton.addEventListener('click', () => repairOrderPrintSetPageIndex(repairOrderPrintCurrentPageIndex() - 1));
    if (printEls.nextPageButton) printEls.nextPageButton.addEventListener('click', () => repairOrderPrintSetPageIndex(repairOrderPrintCurrentPageIndex() + 1));
    if (printEls.exportButton) printEls.exportButton.addEventListener('click', exportRepairOrderPrintPdf);
    if (printEls.printButton) printEls.printButton.addEventListener('click', runRepairOrderPrintJob);
    if (printEls.saveSettingsButton) printEls.saveSettingsButton.addEventListener('click', saveRepairOrderPrintSettings);
    if (printEls.templateEditorButton) printEls.templateEditorButton.addEventListener('click', openPrintTemplateEditor);
    if (printEls.closeX) printEls.closeX.addEventListener('click', closeRepairOrderPrintWorkspace);
    if (printEls.modal) printEls.modal.addEventListener('click', handleRepairOrderPrintModalOverlayClick);
    if (printEls.previewWrap) window.addEventListener('resize', applyRepairOrderPrintZoom);
    if (printEls.templateCloseX) printEls.templateCloseX.addEventListener('click', closePrintTemplateEditor);
    if (printEls.templateModal) printEls.templateModal.addEventListener('click', handlePrintTemplateEditorOverlayClick);
    if (printEls.templateDocumentType) printEls.templateDocumentType.addEventListener('change', handlePrintTemplateDocumentTypeChange);
    if (printEls.templateList) printEls.templateList.addEventListener('click', handlePrintTemplateListClick);
    if (printEls.templatePreviewButton) printEls.templatePreviewButton.addEventListener('click', previewCurrentPrintTemplate);
    if (printEls.templateSaveButton) printEls.templateSaveButton.addEventListener('click', saveCurrentPrintTemplate);
    if (printEls.templateDuplicateButton) printEls.templateDuplicateButton.addEventListener('click', duplicateCurrentPrintTemplate);
    if (printEls.templateDeleteButton) printEls.templateDeleteButton.addEventListener('click', deleteCurrentPrintTemplate);
    if (printEls.templateSetDefaultButton) printEls.templateSetDefaultButton.addEventListener('click', setCurrentPrintTemplateDefault);
    if (printEls.templateNewButton) printEls.templateNewButton.addEventListener('click', createNewPrintTemplateDraft);
    if (printEls.templateUploadButton) printEls.templateUploadButton.addEventListener('click', handlePrintTemplateUpload);
    if (printEls.templateUploadInput) printEls.templateUploadInput.addEventListener('change', handlePrintTemplateUploadChange);
"""


PRINTING_WEB_MODULE_SCRIPT = _PRINTING_SCRIPT_PART1 + _PRINTING_SCRIPT_PART2 + _PRINTING_SCRIPT_PART3
