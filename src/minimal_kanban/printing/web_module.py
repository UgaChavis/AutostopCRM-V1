PRINTING_WEB_MODULE_STYLE = r"""
    #repairOrderPrintModal {
      z-index: 16;
    }
    #printTemplateEditorModal {
      z-index: 17;
    }
    #inspectionSheetFormModal {
      z-index: 18;
    }
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
      grid-template-columns: clamp(224px, 16vw, 280px) minmax(0, 1fr) 340px;
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
    .repair-order-print-documents {
      display: flex;
      flex-direction: column;
      gap: 6px;
      flex: 1 1 auto;
      min-height: 0;
      overflow: auto;
      padding: 2px 2px 0 0;
    }
    .repair-order-print-doc {
      appearance: none;
      border: 1px solid rgba(116, 128, 111, 0.28);
      border-radius: 10px;
      padding: 8px 10px;
      min-height: 40px;
      display: flex;
      gap: 8px;
      align-items: center;
      cursor: pointer;
      background: rgba(255, 255, 255, 0.02);
      color: inherit;
      text-align: left;
      width: 100%;
      transition: background .15s ease, transform .15s ease, box-shadow .15s ease;
    }
    .repair-order-print-doc:hover { background: rgba(167, 178, 132, 0.08); transform: translateX(1px); }
    .repair-order-print-doc.is-active { background: rgba(167, 178, 132, 0.14); border-color: rgba(211, 220, 164, 0.7); box-shadow: inset 0 0 0 1px rgba(211, 220, 164, 0.16); }
    .repair-order-print-doc__meta { min-width: 0; display: flex; flex: 1 1 auto; align-items: center; gap: 8px; }
    .repair-order-print-doc__title {
      font-size: 12px;
      font-weight: 700;
      line-height: 1.25;
      color: var(--text);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      flex: 1 1 auto;
    }
    .repair-order-print-doc__state {
      flex: 0 0 auto;
      width: 4px;
      height: 20px;
      border-radius: 999px;
      background: rgba(167, 178, 132, 0.28);
    }
    .repair-order-print-doc.is-active .repair-order-print-doc__state { background: rgba(211, 220, 164, 0.96); }
    .repair-order-print-docs-footer {
      display: flex;
      flex-direction: column;
      gap: 6px;
      margin-top: auto;
      padding-top: 8px;
      border-top: 1px solid rgba(116, 128, 111, 0.22);
    }
    .repair-order-print-docs-count { font-size: 11px; color: var(--text-soft); letter-spacing: 0.04em; }
    .repair-order-print-docs-action { min-height: 28px; padding-inline: 10px; font-size: 10px; width: 100%; letter-spacing: 0.08em; }
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
    .print-template-editor__editor select,
    .print-template-editor__editor input,
    .print-template-editor__source textarea { background: rgba(14, 18, 15, 0.76); }
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
    .print-template-editor__toolbar { display: flex; gap: 8px; flex-wrap: wrap; }
    .print-template-editor__editor { gap: 12px; }
    .print-template-editor__editor-toolbar { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .print-template-editor__editor-toolbar .btn { min-height: 34px; padding-inline: 10px; }
    .print-template-editor__editor-toolbar select { min-width: 220px; }
    .print-template-editor__surface-wrap { min-height: 0; flex: 1; overflow: auto; border: 1px solid rgba(116, 128, 111, 0.34); border-radius: 16px; background: linear-gradient(180deg, rgba(255,255,255,0.03), rgba(0,0,0,0.04)), #d8ddd7; padding: 18px; display: flex; justify-content: center; align-items: flex-start; }
    .print-template-editor__surface-frame { width: 920px; min-height: 1040px; height: 1040px; border: 0; border-radius: 10px; background: #ffffff; box-shadow: 0 14px 34px rgba(0, 0, 0, 0.18); }
    .print-template-editor__source { border: 1px solid rgba(116, 128, 111, 0.28); border-radius: 14px; background: rgba(255,255,255,0.02); padding: 10px 12px; }
    .print-template-editor__source summary { cursor: pointer; list-style: none; font-size: 12px; text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-soft); font-family: var(--mono); }
    .print-template-editor__source summary::-webkit-details-marker { display: none; }
    .print-template-editor__source textarea { width: 100%; min-height: 220px; margin-top: 10px; resize: vertical; font-family: var(--mono); font-size: 12px; line-height: 1.5; }
    .print-template-editor__preview-wrap { min-height: 0; overflow: auto; display: flex; justify-content: center; align-items: flex-start; padding: 4px 0 32px; background: linear-gradient(180deg, rgba(255,255,255,0.02), rgba(0,0,0,0.08)), #1a211c; border: 1px solid rgba(116, 128, 111, 0.34); border-radius: 16px; }
    .repair-order-print-empty { border: 1px dashed rgba(167, 178, 132, 0.36); border-radius: 16px; padding: 28px 18px; text-align: center; color: var(--text-soft); background: rgba(255,255,255,0.02); }
    .dialog--inspection-sheet-form {
      width: min(1080px, calc(100% - 18px));
      max-width: none;
      height: min(92vh, 980px);
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
    }
    .inspection-sheet-form {
      min-height: 0;
      padding: 14px;
      display: flex;
      flex-direction: column;
      gap: 12px;
      background: rgba(0, 0, 0, 0.08);
    }
    .inspection-sheet-form__surface {
      min-height: 0;
      border: 1px solid rgba(116, 128, 111, 0.42);
      background:
        linear-gradient(180deg, rgba(255,255,255,0.03), transparent 18%),
        rgba(30, 37, 32, 0.96);
      border-radius: 16px;
      padding: 14px;
      display: flex;
      flex-direction: column;
      gap: 12px;
      overflow: auto;
    }
    .inspection-sheet-form__hint { color: var(--text-soft); font-size: 12px; line-height: 1.45; }
    .inspection-sheet-form__grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
    .inspection-sheet-form__row { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .inspection-sheet-form__field .field { gap: 5px; }
    .inspection-sheet-form textarea {
      min-height: 94px;
      resize: vertical;
      background: rgba(14, 18, 15, 0.76);
      line-height: 1.45;
    }
    .inspection-sheet-table-editor { border: 1px solid rgba(116, 128, 111, 0.42); border-radius: 12px; padding: 10px; background: rgba(255,255,255,0.02); }
    .inspection-sheet-table-editor__head { display: flex; justify-content: space-between; align-items: center; gap: 10px; margin-bottom: 8px; }
    .inspection-sheet-table-editor__title { color: var(--text-soft); font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; }
    .inspection-sheet-table-editor__rows { display: flex; flex-direction: column; gap: 8px; }
    .inspection-sheet-table-editor__row { display: grid; grid-template-columns: minmax(0, 1fr) 92px 36px; gap: 8px; align-items: end; }
    .inspection-sheet-table-editor__row .field { gap: 4px; }
    .inspection-sheet-table-editor__remove { min-width: 36px; padding-inline: 0; }
    .inspection-sheet-form__field--wide { grid-column: 1 / -1; }
    .inspection-sheet-form__footer {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    .inspection-sheet-form__actions { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
    @media (max-width: 1100px) {
      .dialog--inspection-sheet-form { width: min(100%, calc(100% - 12px)); height: min(100vh, 100%); }
      .inspection-sheet-form__grid,
      .inspection-sheet-form__row { grid-template-columns: 1fr; }
    }
    @media (max-width: 1500px) {
      .repair-order-print-layout { grid-template-columns: clamp(200px, 16vw, 240px) minmax(0, 1fr); grid-template-areas: "docs preview" "settings settings"; }
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
      .repair-order-print-documents { max-height: 240px; }
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
          <div class="repair-order-print-documents" id="repairOrderPrintDocuments" role="tablist" aria-orientation="vertical"></div>
          <div class="repair-order-print-docs-footer">
            <div class="repair-order-print-docs-count" id="repairOrderPrintDocumentsCount">0 документов</div>
            <button class="btn btn--ghost repair-order-print-docs-action" id="repairOrderPrintDocumentsAction" type="button">ЗАПОЛНИТЬ ВЕДОМОСТЬ</button>
          </div>
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
              <div class="repair-order-print-settings__row"><div class="field field--compact"><label for="printProfilePhone">Телефон</label><input id="printProfilePhone" type="text" maxlength="80"></div><div class="field field--compact"><label for="printProfileReceptionPhone">Телефон ресепшена</label><input id="printProfileReceptionPhone" type="text" maxlength="80"></div></div>
              <div class="field field--compact"><label for="printProfileEmail">Email</label><input id="printProfileEmail" type="text" maxlength="120"></div>
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
          <div class="print-template-editor__editor-toolbar">
            <button class="btn btn--ghost" data-print-template-command="formatBlock" data-print-template-value="h1" type="button">H1</button>
            <button class="btn btn--ghost" data-print-template-command="formatBlock" data-print-template-value="h2" type="button">H2</button>
            <button class="btn btn--ghost" data-print-template-command="formatBlock" data-print-template-value="p" type="button">P</button>
            <button class="btn btn--ghost" data-print-template-command="bold" type="button">B</button>
            <button class="btn btn--ghost" data-print-template-command="insertUnorderedList" type="button">LIST</button>
            <select id="printTemplateTokenSelect"></select>
            <button class="btn btn--ghost" id="printTemplateInsertTokenButton" type="button">ВСТАВИТЬ ПОЛЕ</button>
          </div>
          <div class="print-template-editor__surface-wrap"><iframe class="print-template-editor__surface-frame" id="printTemplateVisualEditorFrame" title="Визуальный редактор шаблона"></iframe></div>
          <details class="print-template-editor__source" id="printTemplateSourceSection"><summary>Исходный шаблон</summary><textarea id="printTemplateContent" spellcheck="false"></textarea></details>
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

  <div class="modal" id="inspectionSheetFormModal">
    <div class="dialog dialog--inspection-sheet-form" role="dialog" aria-modal="true" aria-labelledby="inspectionSheetFormTitle">
      <div class="dialog__head">
        <div><div class="dialog__title-prefix">ВЕДОМОСТЬ</div><h2 class="dialog__title" id="inspectionSheetFormTitle">Заполнение дефектовочной ведомости</h2></div>
        <button class="btn btn--ghost" id="inspectionSheetFormCloseX" type="button">ЗАКРЫТЬ</button>
      </div>
      <div class="dialog__body inspection-sheet-form">
        <div class="inspection-sheet-form__surface">
          <div class="inspection-sheet-form__hint" id="inspectionSheetFormMeta">Заполните поля вручную или используйте автозаполнение по данным карточки.</div>
          <div class="inspection-sheet-form__grid">
            <div class="inspection-sheet-form__field"><div class="field field--compact"><label for="inspectionSheetClient">Клиент</label><input id="inspectionSheetClient" type="text" maxlength="200"></div></div>
            <div class="inspection-sheet-form__field"><div class="field field--compact"><label for="inspectionSheetVehicle">Автомобиль</label><input id="inspectionSheetVehicle" type="text" maxlength="200"></div></div>
            <div class="inspection-sheet-form__field"><div class="field field--compact"><label for="inspectionSheetVinPlate">VIN / госномер</label><input id="inspectionSheetVinPlate" type="text" maxlength="200"></div></div>
          </div>
          <div class="inspection-sheet-form__field inspection-sheet-form__field--wide"><div class="field field--compact"><label for="inspectionSheetComplaint">С чем приехал клиент</label><textarea id="inspectionSheetComplaint"></textarea></div></div>
          <div class="inspection-sheet-form__field inspection-sheet-form__field--wide"><div class="field field--compact"><label for="inspectionSheetFindings">Что выявлено</label><textarea id="inspectionSheetFindings"></textarea></div></div>
          <div class="inspection-sheet-form__field inspection-sheet-form__field--wide"><div class="field field--compact"><label for="inspectionSheetRecommendations">Рекомендации</label><textarea id="inspectionSheetRecommendations"></textarea></div></div>
          <div class="inspection-sheet-form__row">
            <div class="inspection-sheet-form__field"><div class="field field--compact"><label for="inspectionSheetPlannedWorks">Планируемые работы</label><textarea id="inspectionSheetPlannedWorks"></textarea></div></div>
            <div class="inspection-sheet-form__field"><div class="field field--compact"><label for="inspectionSheetPlannedMaterials">Планируемые материалы</label><textarea id="inspectionSheetPlannedMaterials"></textarea></div></div>
          </div>
          <div class="inspection-sheet-form__row">
            <div class="inspection-sheet-table-editor">
              <div class="inspection-sheet-table-editor__head">
                <div class="inspection-sheet-table-editor__title">Необходимые работы</div>
                <button class="btn btn--ghost" id="inspectionSheetAddWorkRowButton" type="button">+ строка</button>
              </div>
              <div class="inspection-sheet-table-editor__rows" id="inspectionSheetWorkRows"></div>
            </div>
            <div class="inspection-sheet-table-editor">
              <div class="inspection-sheet-table-editor__head">
                <div class="inspection-sheet-table-editor__title">Необходимые запчасти</div>
                <button class="btn btn--ghost" id="inspectionSheetAddMaterialRowButton" type="button">+ строка</button>
              </div>
              <div class="inspection-sheet-table-editor__rows" id="inspectionSheetMaterialRows"></div>
            </div>
          </div>
          <div class="inspection-sheet-form__field inspection-sheet-form__field--wide"><div class="field field--compact"><label for="inspectionSheetMasterComment">Комментарий мастера</label><textarea id="inspectionSheetMasterComment"></textarea></div></div>
        </div>
      </div>
      <div class="dialog__foot inspection-sheet-form__footer">
        <div class="inspection-sheet-form__hint" id="inspectionSheetFormFooterMeta">После применения предпросмотр и печать будут использовать заполненную ведомость.</div>
        <div class="inspection-sheet-form__actions">
          <button class="btn btn--ghost" id="inspectionSheetFormAutofillButton" type="button">АВТОЗАПОЛНЕНИЕ</button>
          <button class="btn btn--ghost" id="inspectionSheetFormSaveButton" type="button">СОХРАНИТЬ</button>
          <button class="btn" id="inspectionSheetFormApplyButton" type="button">ПРИМЕНИТЬ</button>
        </div>
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
      isPrintRunning: false,
      zoomMode: 'fit',
      zoom: 1,
      inspectionSheetForm: null,
      templateEditor: { documentType: 'repair_order', templateId: '' },
    };
    let printTemplatePreviewTimer = null;

    const printEls = {
      modal: document.getElementById('repairOrderPrintModal'),
      closeX: document.getElementById('repairOrderPrintCloseX'),
      documentsMeta: document.getElementById('repairOrderPrintDocumentsMeta'),
      documents: document.getElementById('repairOrderPrintDocuments'),
      documentsCount: document.getElementById('repairOrderPrintDocumentsCount'),
      documentsAction: document.getElementById('repairOrderPrintDocumentsAction'),
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
      inspectionSheetModal: document.getElementById('inspectionSheetFormModal'),
      inspectionSheetCloseX: document.getElementById('inspectionSheetFormCloseX'),
      inspectionSheetMeta: document.getElementById('inspectionSheetFormMeta'),
      inspectionSheetFooterMeta: document.getElementById('inspectionSheetFormFooterMeta'),
      inspectionSheetClient: document.getElementById('inspectionSheetClient'),
      inspectionSheetVehicle: document.getElementById('inspectionSheetVehicle'),
      inspectionSheetVinPlate: document.getElementById('inspectionSheetVinPlate'),
      inspectionSheetComplaint: document.getElementById('inspectionSheetComplaint'),
      inspectionSheetFindings: document.getElementById('inspectionSheetFindings'),
      inspectionSheetRecommendations: document.getElementById('inspectionSheetRecommendations'),
      inspectionSheetPlannedWorks: document.getElementById('inspectionSheetPlannedWorks'),
      inspectionSheetPlannedMaterials: document.getElementById('inspectionSheetPlannedMaterials'),
      inspectionSheetWorkRows: document.getElementById('inspectionSheetWorkRows'),
      inspectionSheetMaterialRows: document.getElementById('inspectionSheetMaterialRows'),
      inspectionSheetAddWorkRowButton: document.getElementById('inspectionSheetAddWorkRowButton'),
      inspectionSheetAddMaterialRowButton: document.getElementById('inspectionSheetAddMaterialRowButton'),
      inspectionSheetMasterComment: document.getElementById('inspectionSheetMasterComment'),
      inspectionSheetAutofillButton: document.getElementById('inspectionSheetFormAutofillButton'),
      inspectionSheetSaveButton: document.getElementById('inspectionSheetFormSaveButton'),
      inspectionSheetApplyButton: document.getElementById('inspectionSheetFormApplyButton'),
      profileCompanyName: document.getElementById('printProfileCompanyName'),
      profileLegalName: document.getElementById('printProfileLegalName'),
      profileAddress: document.getElementById('printProfileAddress'),
      profilePhone: document.getElementById('printProfilePhone'),
      profileReceptionPhone: document.getElementById('printProfileReceptionPhone'),
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
      templateSourceSection: document.getElementById('printTemplateSourceSection'),
      templateContent: document.getElementById('printTemplateContent'),
      templateVisualEditorFrame: document.getElementById('printTemplateVisualEditorFrame'),
      templateTokenSelect: document.getElementById('printTemplateTokenSelect'),
      templateInsertTokenButton: document.getElementById('printTemplateInsertTokenButton'),
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
      const first = Array.isArray(value)
        ? value.map((item) => String(item || '').trim()).find((item) => item && validIds.has(item))
        : '';
      return first ? [first] : (validIds.has('repair_order') ? ['repair_order'] : docs.slice(0, 1).map((item) => item.id));
    }

    const PRINT_TEMPLATE_EDITOR_TOKENS = [
      { label: 'Клиент', value: '{{client.name_display}}' },
      { label: 'Телефон', value: '{{client.phone_display}}' },
      { label: 'Автомобиль', value: '{{vehicle.display_name}}' },
      { label: 'VIN', value: '{{vehicle.vin_display}}' },
      { label: 'Госномер', value: '{{vehicle.license_plate_display}}' },
      { label: 'Пробег', value: '{{vehicle.mileage_display}}' },
      { label: 'Причина обращения', value: '{{document.reason}}' },
      { label: 'Описание', value: '{{document.description}}' },
      { label: 'Информация для клиента', value: '{{document.client_summary}}' },
      { label: 'Работы', value: '{{#works}}<tr><td>{{name}}</td><td>{{quantity_display}}</td><td>{{price_display}}</td><td>{{total_display}}</td></tr>{{/works}}' },
      { label: 'Материалы', value: '{{#materials}}<tr><td>{{name}}</td><td>{{quantity_display}}</td><td>{{price_display}}</td><td>{{total_display}}</td></tr>{{/materials}}' },
      { label: 'Итого', value: '{{totals.grand_total_display}}' },
      { label: 'Компания', value: '{{service.company_name}}' },
      { label: 'Адрес сервиса', value: '{{service.address}}' },
      { label: 'Телефон ресепшена', value: '{{service.reception_phone}}' },
    ];

    function buildPrintTemplateVisualEditorHtml(content) {
      return '<!doctype html><html><head><meta charset="utf-8"><style>'
        + 'html,body{margin:0;padding:0;background:#fff;color:#111;font:14px/1.55 Segoe UI,Arial,sans-serif;}'
        + 'body{padding:28px;}'
        + '[contenteditable=\"true\"]{min-height:980px;outline:none;}'
        + 'table{border-collapse:collapse;width:100%;}'
        + 'td,th{border:1px solid #d6d6d6;padding:6px 8px;vertical-align:top;}'
        + '.token{display:inline-block;padding:2px 6px;border-radius:999px;background:#eef3e8;border:1px solid #cad6be;font:12px/1.2 Consolas,monospace;color:#35412f;white-space:nowrap;}'
        + '</style></head><body><div id=\"editor\" contenteditable=\"true\"></div><script>'
        + 'const editor=document.getElementById(\"editor\");'
        + 'editor.innerHTML=' + JSON.stringify(String(content || '')) + ';'
        + 'document.addEventListener(\"selectionchange\",()=>{window.parent?.postMessage({type:\"print-template-selection-change\"},\"*\");});'
        + '<\/script></body></html>';
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
      printTemplatePreviewTimer = window.setTimeout(() => {
        printTemplatePreviewTimer = null;
        previewCurrentPrintTemplate();
      }, 220);
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
      syncPrintTemplateSourceFromVisualEditor();
      return printEls.templateContent?.value || '';
    }

    function handlePrintTemplateVisualEditorLoad() {
      const doc = printEls.templateVisualEditorFrame?.contentDocument;
      const editor = doc?.getElementById('editor');
      if (!editor) return;
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
          reception_phone: printEls.profileReceptionPhone?.value || '',
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

    function blankInspectionSheetForm() {
      return {
        client: '',
        vehicle: '',
        vin_or_plate: '',
        complaint_summary: '',
        findings: '',
        recommendations: '',
        planned_works: '',
        planned_materials: '',
        planned_work_rows: [],
        planned_material_rows: [],
        master_comment: '',
        updated_at: '',
        filled_by: '',
        source: 'manual',
      };
    }

    function normalizeInspectionSheetTableRows(value) {
      const items = Array.isArray(value) ? value : [];
      const rows = [];
      items.forEach((item) => {
        const row = item && typeof item === 'object' ? item : { name: String(item || ''), quantity: '' };
        const name = String(row.name || '').trim();
        const quantity = String(row.quantity || '').trim();
        if (!name && !quantity) return;
        rows.push({ name, quantity });
      });
      return rows.slice(0, 80);
    }

    function normalizeInspectionSheetForm(value) {
      const input = value && typeof value === 'object' ? value : {};
      return {
        client: String(input.client || '').trim(),
        vehicle: String(input.vehicle || '').trim(),
        vin_or_plate: String(input.vin_or_plate || '').trim(),
        complaint_summary: String(input.complaint_summary || '').trim(),
        findings: String(input.findings || '').trim(),
        recommendations: String(input.recommendations || '').trim(),
        planned_works: String(input.planned_works || '').trim(),
        planned_materials: String(input.planned_materials || '').trim(),
        planned_work_rows: normalizeInspectionSheetTableRows(input.planned_work_rows),
        planned_material_rows: normalizeInspectionSheetTableRows(input.planned_material_rows),
        master_comment: String(input.master_comment || '').trim(),
        updated_at: String(input.updated_at || '').trim(),
        filled_by: String(input.filled_by || '').trim(),
        source: String(input.source || 'manual').trim() || 'manual',
      };
    }

    function inspectionSheetTableElements(kind) {
      return kind === 'works'
        ? { rows: printEls.inspectionSheetWorkRows, field: 'planned_work_rows' }
        : { rows: printEls.inspectionSheetMaterialRows, field: 'planned_material_rows' };
    }

    function renderInspectionSheetTableRows(kind, rows) {
      const target = inspectionSheetTableElements(kind).rows;
      if (!target) return;
      const normalized = normalizeInspectionSheetTableRows(rows);
      if (!normalized.length) {
        target.innerHTML = '';
        return;
      }
      target.innerHTML = normalized.map((row, index) => (
        '<div class="inspection-sheet-table-editor__row" data-inspection-row-kind="' + escapeHtml(kind) + '" data-inspection-row-index="' + String(index) + '">' +
          '<div class="field field--compact"><label>Наименование</label><input data-inspection-row-name type="text" maxlength="240" value="' + escapeHtml(row.name) + '"></div>' +
          '<div class="field field--compact"><label>Кол-во</label><input data-inspection-row-quantity type="text" maxlength="40" value="' + escapeHtml(row.quantity) + '"></div>' +
          '<button class="btn btn--ghost inspection-sheet-table-editor__remove" data-inspection-row-remove type="button">−</button>' +
        '</div>'
      )).join('');
    }

    function readInspectionSheetTableRows(kind) {
      const target = inspectionSheetTableElements(kind).rows;
      if (!target) return [];
      const rows = [];
      target.querySelectorAll('[data-inspection-row-kind]').forEach((rowElement) => {
        const name = String(rowElement.querySelector('[data-inspection-row-name]')?.value || '').trim();
        const quantity = String(rowElement.querySelector('[data-inspection-row-quantity]')?.value || '').trim();
        if (!name && !quantity) return;
        rows.push({ name, quantity });
      });
      return normalizeInspectionSheetTableRows(rows);
    }

    function appendInspectionSheetTableRow(kind) {
      const currentRows = readInspectionSheetTableRows(kind);
      currentRows.push({ name: '', quantity: '' });
      renderInspectionSheetTableRows(kind, currentRows);
    }

    function removeInspectionSheetTableRow(kind, index) {
      const currentRows = readInspectionSheetTableRows(kind);
      if (index < 0 || index >= currentRows.length) return;
      currentRows.splice(index, 1);
      renderInspectionSheetTableRows(kind, currentRows);
    }

    function applyInspectionSheetFormToInputs(form) {
      const normalized = normalizeInspectionSheetForm(form);
      repairOrderPrintState.inspectionSheetForm = normalized;
      if (printEls.inspectionSheetClient) printEls.inspectionSheetClient.value = normalized.client;
      if (printEls.inspectionSheetVehicle) printEls.inspectionSheetVehicle.value = normalized.vehicle;
      if (printEls.inspectionSheetVinPlate) printEls.inspectionSheetVinPlate.value = normalized.vin_or_plate;
      if (printEls.inspectionSheetComplaint) printEls.inspectionSheetComplaint.value = normalized.complaint_summary;
      if (printEls.inspectionSheetFindings) printEls.inspectionSheetFindings.value = normalized.findings;
      if (printEls.inspectionSheetRecommendations) printEls.inspectionSheetRecommendations.value = normalized.recommendations;
      if (printEls.inspectionSheetPlannedWorks) printEls.inspectionSheetPlannedWorks.value = normalized.planned_works;
      if (printEls.inspectionSheetPlannedMaterials) printEls.inspectionSheetPlannedMaterials.value = normalized.planned_materials;
      renderInspectionSheetTableRows('works', normalized.planned_work_rows);
      renderInspectionSheetTableRows('materials', normalized.planned_material_rows);
      if (printEls.inspectionSheetMasterComment) printEls.inspectionSheetMasterComment.value = normalized.master_comment;
      if (printEls.inspectionSheetMeta) {
        const metaBits = [];
        if (normalized.source) metaBits.push('Источник: ' + normalized.source);
        if (normalized.filled_by) metaBits.push('Заполнил: ' + normalized.filled_by);
        if (normalized.updated_at) metaBits.push('Обновлено: ' + normalized.updated_at);
        printEls.inspectionSheetMeta.textContent = metaBits.length
          ? metaBits.join(' · ')
          : 'Заполните поля вручную или используйте автозаполнение по данным текущей карточки.';
      }
    }

    function readInspectionSheetFormFromInputs() {
      return normalizeInspectionSheetForm({
        client: printEls.inspectionSheetClient?.value || '',
        vehicle: printEls.inspectionSheetVehicle?.value || '',
        vin_or_plate: printEls.inspectionSheetVinPlate?.value || '',
        complaint_summary: printEls.inspectionSheetComplaint?.value || '',
        findings: printEls.inspectionSheetFindings?.value || '',
        recommendations: printEls.inspectionSheetRecommendations?.value || '',
        planned_works: printEls.inspectionSheetPlannedWorks?.value || '',
        planned_materials: printEls.inspectionSheetPlannedMaterials?.value || '',
        planned_work_rows: readInspectionSheetTableRows('works'),
        planned_material_rows: readInspectionSheetTableRows('materials'),
        master_comment: printEls.inspectionSheetMasterComment?.value || '',
        updated_at: repairOrderPrintState.inspectionSheetForm?.updated_at || '',
        filled_by: repairOrderPrintState.inspectionSheetForm?.filled_by || '',
        source: repairOrderPrintState.inspectionSheetForm?.source || 'manual',
      });
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
      if (printEls.profileReceptionPhone) printEls.profileReceptionPhone.value = profile.reception_phone || '';
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
      const activeDoc = repairOrderPrintDocumentMap()[repairOrderPrintActiveDocument()] || docs[0] || null;
      printEls.documents.innerHTML = docs.length ? docs.map((item) => {
        const isActive = item.id === repairOrderPrintActiveDocument();
        const activeClass = isActive ? ' is-active' : '';
        return '<button class="repair-order-print-doc' + activeClass + '" data-print-document="' + escapeHtml(item.id) + '" type="button" role="tab" aria-selected="' + (isActive ? 'true' : 'false') + '">' +
          '<div class="repair-order-print-doc__meta">' +
            '<div class="repair-order-print-doc__state" aria-hidden="true"></div>' +
            '<div class="repair-order-print-doc__title">' + escapeHtml(item.label) + '</div>' +
          '</div>' +
        '</button>';
      }).join('') : '<div class="repair-order-print-empty">Документы для печати пока недоступны.</div>';
      printEls.documentsMeta.textContent = docs.length ? 'Выберите документ.' : 'Документы для печати отсутствуют.';
      if (printEls.documentsCount) printEls.documentsCount.textContent = docs.length ? (String(docs.length) + ' документов') : '0 документов';
      if (printEls.documentsAction) {
        const canFill = Boolean(activeDoc?.supports_form_fill);
        printEls.documentsAction.style.display = canFill ? '' : 'none';
        printEls.documentsAction.textContent = canFill ? 'ЗАПОЛНИТЬ ВЕДОМОСТЬ' : 'НЕДОСТУПНО';
      }
    }

    function handleRepairOrderPrintDocumentsActionClick() {
      openInspectionSheetForm();
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
        printEls.printButton.disabled = !repairOrderPrintWorkspaceDocuments().length;
        printEls.printButton.title = hasPrinters
          ? 'Откроет системное окно печати браузера. При сбое будет использован выбранный серверный принтер.'
          : 'Откроет системное окно печати браузера. Серверные принтеры не найдены.';
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

    async function loadInspectionSheetForm() {
      const data = await api('/api/get_inspection_sheet_form', {
        method: 'POST',
        body: repairOrderPrintRequestPayload({
          selected_document_ids: ['inspection_sheet'],
          active_document_id: 'inspection_sheet',
        }),
      });
      applyInspectionSheetFormToInputs(data?.form || blankInspectionSheetForm());
      return data;
    }

    async function openInspectionSheetForm() {
      repairOrderPrintState.selectedDocumentIds = ['inspection_sheet'];
      repairOrderPrintState.activeDocumentId = 'inspection_sheet';
      renderRepairOrderPrintDocuments();
      renderRepairOrderPrintTemplateSelect();
      if (!repairOrderPrintState.previewByDocument?.inspection_sheet) {
        await refreshRepairOrderPrintPreview({
          selected_document_ids: ['inspection_sheet'],
          active_document_id: 'inspection_sheet',
        });
      } else {
        renderRepairOrderPrintPreview();
      }
      await loadInspectionSheetForm();
      if (printEls.inspectionSheetFooterMeta) {
        printEls.inspectionSheetFooterMeta.textContent = 'После применения предпросмотр и печать будут использовать заполненную ведомость.';
      }
      printEls.inspectionSheetModal?.classList.add('is-open');
    }

    function closeInspectionSheetForm() {
      printEls.inspectionSheetModal?.classList.remove('is-open');
    }

    async function saveInspectionSheetFormDraft({ closeAfter = false } = {}) {
      const data = await api('/api/save_inspection_sheet_form', {
        method: 'POST',
        body: repairOrderPrintRequestPayload({
          selected_document_ids: ['inspection_sheet'],
          active_document_id: 'inspection_sheet',
          form_source: 'manual',
          form_data: readInspectionSheetFormFromInputs(),
        }),
      });
      applyInspectionSheetFormToInputs(data?.form || blankInspectionSheetForm());
      await refreshRepairOrderPrintPreview({
        selected_document_ids: ['inspection_sheet'],
        active_document_id: 'inspection_sheet',
      });
      if (printEls.inspectionSheetFooterMeta) {
        printEls.inspectionSheetFooterMeta.textContent = 'Ведомость сохранена и применена к предпросмотру.';
      }
      if (closeAfter) closeInspectionSheetForm();
      setStatus('Ведомость сохранена.', false);
      return data;
    }

    async function autofillInspectionSheetFormDraft() {
      if (printEls.inspectionSheetAutofillButton) printEls.inspectionSheetAutofillButton.disabled = true;
      try {
        const data = await api('/api/autofill_inspection_sheet_form', {
          method: 'POST',
          body: repairOrderPrintRequestPayload({
            selected_document_ids: ['inspection_sheet'],
            active_document_id: 'inspection_sheet',
          }),
        });
        applyInspectionSheetFormToInputs(data?.form || blankInspectionSheetForm());
        await refreshRepairOrderPrintPreview({
          selected_document_ids: ['inspection_sheet'],
          active_document_id: 'inspection_sheet',
        });
        const notes = Array.isArray(data?.autofill?.confidence_notes) ? data.autofill.confidence_notes : [];
        if (printEls.inspectionSheetFooterMeta) {
          printEls.inspectionSheetFooterMeta.textContent = notes.length
            ? ('Автозаполнение завершено. ' + notes.slice(0, 2).join(' · '))
            : 'Автозаполнение завершено.';
        }
        setStatus('Ведомость автозаполнена.', false);
      } catch (error) {
        if (printEls.inspectionSheetFooterMeta) {
          printEls.inspectionSheetFooterMeta.textContent = error.message || 'Не удалось автозаполнить ведомость.';
        }
        setStatus(error.message, true);
      } finally {
        if (printEls.inspectionSheetAutofillButton) printEls.inspectionSheetAutofillButton.disabled = false;
      }
    }
"""


_PRINTING_SCRIPT_PART3 = r"""
    function base64ToBlob(base64, mimeType = 'application/octet-stream') {
      const binary = atob(String(base64 || ''));
      const bytes = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
      return new Blob([bytes], { type: mimeType });
    }

    function repairOrderPrintCombinedHtml() {
      const docs = repairOrderPrintWorkspaceDocuments();
      const pages = docs.flatMap((item) => {
        const preview = repairOrderPrintState.previewByDocument?.[item.id];
        return Array.isArray(preview?.pages) ? preview.pages : [];
      });
      const body = pages.length
        ? pages.map((page) => page.html || '').join('<div style="page-break-after:always"></div>')
        : '<!doctype html><html lang="ru"><body style="font-family: Segoe UI, sans-serif; padding: 32px; color: #444">Нет данных для печати.</body></html>';
      return body.includes('<!doctype html')
        ? body
        : '<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>AutoStop CRM Print</title></head><body>' + body + '</body></html>';
    }

    function runRepairOrderBrowserPrint() {
      const printableHtml = repairOrderPrintCombinedHtml();
      return new Promise((resolve, reject) => {
        const frame = document.createElement('iframe');
        let settled = false;
        let printStarted = false;
        const cleanup = () => window.setTimeout(() => frame.remove(), 1500);
        frame.style.position = 'fixed';
        frame.style.right = '-12000px';
        frame.style.bottom = '0';
        frame.style.width = '1px';
        frame.style.height = '1px';
        frame.style.border = '0';
        frame.setAttribute('aria-hidden', 'true');
        frame.onload = () => {
          if (printStarted) return;
          printStarted = true;
          frame.onload = null;
          window.setTimeout(() => {
            try {
              const win = frame.contentWindow;
              if (!win) throw new Error('Не удалось открыть системное окно печати.');
              win.onafterprint = () => {
                if (settled) return;
                settled = true;
                cleanup();
                resolve();
              };
              win.focus();
              win.print();
              window.setTimeout(() => {
                if (settled) return;
                settled = true;
                cleanup();
                resolve();
              }, 1200);
            } catch (error) {
              if (settled) return;
              settled = true;
              cleanup();
              reject(error);
            }
          }, 120);
        };
        document.body.appendChild(frame);
        const doc = frame.contentDocument;
        if (!doc) {
          cleanup();
          reject(new Error('Не удалось подготовить документ для печати.'));
          return;
        }
        doc.open();
        doc.write(printableHtml);
        doc.close();
      });
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
      if (repairOrderPrintState.isPrintRunning) return;
      repairOrderPrintState.isPrintRunning = true;
      if (printEls.printButton) printEls.printButton.disabled = true;
      try {
        await runRepairOrderBrowserPrint();
        setStatus('Открыто системное окно печати браузера.', false);
      } catch (error) {
        try {
          const printerName = printEls.printerSelect?.value || '';
          if (!printerName) throw error;
          const fallback = await api('/api/print_repair_order_documents', {
            method: 'POST',
            body: repairOrderPrintRequestPayload({ printer_name: printerName }),
          });
          setStatus('Браузерная печать не открылась. Документ отправлен на серверный принтер: ' + (fallback?.printer_name || printerName) + '.', false);
        } catch (fallbackError) {
          setStatus((fallbackError && fallbackError.message) || (error && error.message) || 'Не удалось запустить печать.', true);
        }
      } finally {
        repairOrderPrintState.isPrintRunning = false;
        syncRepairOrderPrintPrinterState();
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

    function handleRepairOrderPrintDocumentsClick(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const card = target.closest('[data-print-document]');
      if (!card) return;
      const documentId = card.dataset.printDocument || 'repair_order';
      const needsSelection = repairOrderPrintActiveDocument() !== documentId;
      repairOrderPrintState.selectedDocumentIds = [documentId];
      repairOrderPrintState.activeDocumentId = documentId;
      renderRepairOrderPrintDocuments();
      renderRepairOrderPrintTemplateSelect();
      if (needsSelection || !repairOrderPrintState.previewByDocument?.[documentId]) refreshRepairOrderPrintPreview();
      else renderRepairOrderPrintPreview();
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
      repairOrderPrintState.templateEditor.documentType = repairOrderPrintActiveDocument() || 'repair_order';
      repairOrderPrintState.templateEditor.templateId = repairOrderPrintSelectedTemplateId(repairOrderPrintState.templateEditor.documentType);
      renderPrintTemplateDocumentTypeOptions();
      renderPrintTemplateList();
      printEls.templateModal.classList.add('is-open');
    }

    function closePrintTemplateEditor() {
      printEls.templateModal.classList.remove('is-open');
    }

    function selectPrintTemplateRecord(templateId) {
      repairOrderPrintState.templateEditor.templateId = templateId;
      renderPrintTemplateList();
    }

    async function previewCurrentPrintTemplate() {
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
        const data = await api('/api/preview_repair_order_print_documents', {
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
          printEls.templatePreviewMeta.textContent = 'Страниц в предпросмотре: ' + Math.max(1, documentPreview.page_count || documentPreview.pages?.length || 1);
          return;
        }
        printEls.templatePreviewFrame.srcdoc = buildPrintTemplateEditorFallbackHtml(
          'Предпросмотр шаблона',
          'Сервер не вернул HTML для предпросмотра.',
        );
        printEls.templatePreviewMeta.textContent = 'Предпросмотр недоступен.';
      } catch (error) {
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
      const documentType = repairOrderPrintState.templateEditor.documentType || 'repair_order';
      const current = repairOrderPrintCurrentTemplateRecord();
      const saveTargetId = current && !current.is_builtin ? current.id : '';
      try {
        const data = await api('/api/save_print_template', {
          method: 'POST',
          body: { source: 'ui', document_type: documentType, template_id: saveTargetId, name: printEls.templateName?.value || '', content: readPrintTemplateEditorContent() },
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
      loadPrintTemplateEditorContent('');
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
        loadPrintTemplateEditorContent(await file.text());
        repairOrderPrintState.templateEditor.templateId = '';
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

    function handleInspectionSheetFormOverlayClick(event) {
      if (event.target === printEls.inspectionSheetModal) closeInspectionSheetForm();
    }

    function handleInspectionSheetTableRowsClick(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const removeButton = target.closest('[data-inspection-row-remove]');
      if (!removeButton) return;
      const row = removeButton.closest('[data-inspection-row-kind]');
      if (!row) return;
      const kind = row.getAttribute('data-inspection-row-kind') || '';
      const index = Number(row.getAttribute('data-inspection-row-index') || '-1');
      if (!kind || Number.isNaN(index)) return;
      removeInspectionSheetTableRow(kind, index);
    }

    printRepairOrderDraft = function() { return openRepairOrderPrintWorkspace(); };

    if (printEls.documents) printEls.documents.addEventListener('click', handleRepairOrderPrintDocumentsClick);
    if (printEls.documentsAction) printEls.documentsAction.addEventListener('click', handleRepairOrderPrintDocumentsActionClick);
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
    if (printEls.inspectionSheetCloseX) printEls.inspectionSheetCloseX.addEventListener('click', closeInspectionSheetForm);
    if (printEls.inspectionSheetModal) printEls.inspectionSheetModal.addEventListener('click', handleInspectionSheetFormOverlayClick);
    if (printEls.inspectionSheetWorkRows) printEls.inspectionSheetWorkRows.addEventListener('click', handleInspectionSheetTableRowsClick);
    if (printEls.inspectionSheetMaterialRows) printEls.inspectionSheetMaterialRows.addEventListener('click', handleInspectionSheetTableRowsClick);
    if (printEls.inspectionSheetAddWorkRowButton) printEls.inspectionSheetAddWorkRowButton.addEventListener('click', () => { appendInspectionSheetTableRow('works'); });
    if (printEls.inspectionSheetAddMaterialRowButton) printEls.inspectionSheetAddMaterialRowButton.addEventListener('click', () => { appendInspectionSheetTableRow('materials'); });
    if (printEls.inspectionSheetSaveButton) printEls.inspectionSheetSaveButton.addEventListener('click', () => { saveInspectionSheetFormDraft(); });
    if (printEls.inspectionSheetApplyButton) printEls.inspectionSheetApplyButton.addEventListener('click', () => { saveInspectionSheetFormDraft({ closeAfter: true }); });
    if (printEls.inspectionSheetAutofillButton) printEls.inspectionSheetAutofillButton.addEventListener('click', autofillInspectionSheetFormDraft);
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
    if (printEls.templateVisualEditorFrame) printEls.templateVisualEditorFrame.addEventListener('load', handlePrintTemplateVisualEditorLoad);
    if (printEls.templateInsertTokenButton) printEls.templateInsertTokenButton.addEventListener('click', insertPrintTemplateToken);
    if (printEls.templateContent) printEls.templateContent.addEventListener('input', () => { printEls.templateContent.dataset.dirty = '1'; });
    if (printEls.templateContent) printEls.templateContent.addEventListener('blur', () => {
      if (printEls.templateContent.dataset.dirty === '1') {
        renderPrintTemplateVisualEditor(printEls.templateContent.value || '');
        printEls.templateContent.dataset.dirty = '0';
        schedulePrintTemplatePreview();
      }
    });
    if (printEls.templateContent) printEls.templateContent.addEventListener('input', schedulePrintTemplatePreview);
    document.addEventListener('click', (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const commandButton = target.closest('[data-print-template-command]');
      if (!commandButton) return;
      execPrintTemplateEditorCommand(commandButton.dataset.printTemplateCommand || '', commandButton.dataset.printTemplateValue || '');
    });
"""


PRINTING_WEB_MODULE_SCRIPT = (
    _PRINTING_SCRIPT_PART1 + _PRINTING_SCRIPT_PART2 + _PRINTING_SCRIPT_PART3
)
