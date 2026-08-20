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
    #completionActEditorModal {
      z-index: 19;
    }
    body.is-mobile-lite #repairOrderPrintModal {
      z-index: 68;
    }
    body.is-mobile-lite #printTemplateEditorModal {
      z-index: 69;
    }
    body.is-mobile-lite #inspectionSheetFormModal {
      z-index: 70;
    }
    body.is-mobile-lite #completionActEditorModal {
      z-index: 71;
    }
    .dialog--repair-order-print {
      width: min(1718px, calc(100% - 18px));
      max-width: none;
      height: min(96vh, 1030px);
      max-height: min(96vh, 1030px);
      overflow: hidden;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
    }
    .dialog--print-template-editor {
      width: min(1880px, calc(100% - 18px));
      max-width: none;
      height: min(96vh, 1160px);
      overflow: hidden;
      display: grid;
      grid-template-rows: auto auto minmax(0, 1fr) auto;
    }
    .repair-order-print-layout {
      min-height: 0;
      display: grid;
      grid-template-columns: 220px minmax(0, 1400px);
      justify-content: center;
      gap: 14px;
      padding: 14px;
      background: rgba(0, 0, 0, 0.08);
    }
    .dialog--repair-order-print > .repair-order-print-layout {
      overflow: hidden;
    }
    .repair-order-print-layout > .repair-order-print-panel:first-child {
      align-items: center;
    }
    .repair-order-print-layout > .repair-order-print-panel:first-child > * {
      width: 100%;
    }
    .repair-order-print-layout > .repair-order-print-panel:first-child .repair-order-print-panel__title,
    .repair-order-print-layout > .repair-order-print-panel:first-child .repair-order-print-preview__meta,
    .repair-order-print-layout > .repair-order-print-panel:first-child .repair-order-print-docs-count {
      text-align: center;
    }
    .repair-order-print-panel.repair-order-print-panel--settings {
      display: none;
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
    .repair-order-print-layout > .repair-order-print-panel:nth-child(2) {
      width: 100%;
      max-width: 1400px;
      height: 100%;
      max-height: 850px;
      align-self: center;
      justify-self: center;
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
    .repair-order-print-doc-row { display: grid; grid-template-columns: minmax(0, 1fr) 44px; gap: 6px; align-items: stretch; }
    .repair-order-print-doc-row .repair-order-print-doc { min-width: 0; }
    .repair-order-print-doc__editor {
      appearance: none;
      width: 44px;
      min-width: 44px;
      min-height: 44px;
      padding: 0;
      border: 1px solid rgba(116, 128, 111, 0.36);
      border-radius: 10px;
      background: rgba(255, 255, 255, 0.03);
      color: var(--text);
      cursor: pointer;
      font-size: 18px;
      line-height: 1;
    }
    .repair-order-print-doc__editor:hover,
    .repair-order-print-doc__editor:focus-visible { border-color: rgba(211, 220, 164, 0.82); background: rgba(167, 178, 132, 0.14); outline: none; }
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
      padding: 12px;
      overflow: auto;
      position: relative;
    }
    .repair-order-print-preview-stage {
      min-width: 0;
      width: 920px;
      height: 1180px;
      min-height: 0;
      position: relative;
      margin: 0 auto 28px;
    }
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
    .repair-order-print-preview-frame {
      position: absolute;
      top: 0;
      left: 0;
      transform-origin: top left;
    }
    .repair-order-print-toolbar { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; }
    .repair-order-print-toolbar__group { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .repair-order-print-toolbar__label { font-size: 12px; color: var(--text-soft); text-transform: uppercase; letter-spacing: 0.08em; }
    .repair-order-print-toolbar .btn { min-height: 34px; padding-inline: 10px; }
    .repair-order-print-settings { min-height: 0; overflow: auto; padding-right: 4px; display: flex; flex-direction: column; gap: 10px; }
    .repair-order-print-settings .field { gap: 5px; }
    .repair-order-print-settings input,
    .repair-order-print-settings select,
    .repair-order-print-settings textarea,
    .print-template-editor__editor select,
    .print-template-editor__editor input,
    .print-template-editor__source textarea { background: rgba(14, 18, 15, 0.76); }
    .repair-order-print-settings__row { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
    .repair-order-print-settings__section { display: flex; flex-direction: column; gap: 8px; padding: 10px; border-radius: 14px; border: 1px solid rgba(116, 128, 111, 0.3); background: rgba(255, 255, 255, 0.02); }
    .repair-order-print-settings__section-title { font-size: 12px; text-transform: uppercase; letter-spacing: 0.12em; color: var(--text-soft); font-family: var(--mono); }
    .manual-print-document-form { display: flex; flex-direction: column; gap: 8px; }
    .manual-print-document-form[hidden] { display: none; }
    .manual-print-document-form textarea { min-height: 82px; resize: vertical; line-height: 1.45; }
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
      overflow: hidden;
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
    .dialog--completion-act-editor {
      width: min(1880px, calc(100% - 18px));
      max-width: none;
      height: min(96vh, 1160px);
      overflow: hidden;
      display: grid;
      grid-template-rows: auto auto minmax(0, 1fr) auto;
    }
    .completion-act-editor {
      min-height: 0;
      display: grid;
      grid-template-columns: minmax(520px, 0.9fr) minmax(620px, 1.1fr);
      gap: 14px;
      padding: 14px;
      background: rgba(0, 0, 0, 0.08);
    }
    .completion-act-editor__pane {
      min-height: 0;
      border: 1px solid rgba(116, 128, 111, 0.42);
      border-radius: 16px;
      background: rgba(30, 37, 32, 0.96);
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    .completion-act-editor__pane-head { padding: 12px 14px; border-bottom: 1px solid rgba(116, 128, 111, 0.28); display: flex; flex-direction: column; gap: 8px; }
    .completion-act-editor__pane-title { font-family: var(--mono); font-size: 12px; text-transform: uppercase; letter-spacing: .12em; color: var(--text-soft); }
    .completion-act-editor__meta { color: var(--text-soft); font-size: 12px; line-height: 1.45; }
    .completion-act-editor__warning { margin: 0 14px; padding: 10px 12px; border: 1px solid rgba(217, 164, 65, .52); border-radius: 10px; background: rgba(217, 164, 65, .12); color: #f1d591; font-size: 12px; line-height: 1.45; }
    .completion-act-editor__warning[hidden] { display: none; }
    .completion-act-editor__warning--live { border-color: rgba(116, 128, 111, .52); background: rgba(116, 128, 111, .12); color: var(--text-soft); }
    .completion-act-editor__tabs { display: flex; gap: 6px; overflow-x: auto; padding-bottom: 2px; }
    .completion-act-editor__tab { min-height: 36px; flex: 0 0 auto; padding-inline: 10px; font-size: 10px; }
    .completion-act-editor__tab.is-active { border-color: rgba(211, 220, 164, .8); background: rgba(167, 178, 132, .16); }
    .completion-act-editor__form { min-height: 0; overflow: auto; padding: 14px; }
    .completion-act-editor__section { display: flex; flex-direction: column; gap: 12px; }
    .completion-act-editor__section[hidden] { display: none; }
    .completion-act-editor__section-title { font-size: 13px; font-weight: 700; color: var(--text); }
    .completion-act-editor__grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .completion-act-editor__field--wide { grid-column: 1 / -1; }
    .completion-act-editor__field .field { gap: 5px; }
    .completion-act-editor__field label { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
    .completion-act-editor__field input,
    .completion-act-editor__field textarea,
    .completion-act-items input,
    .completion-act-items select { background: rgba(14, 18, 15, 0.76); }
    .completion-act-editor__field textarea { min-height: 112px; resize: vertical; line-height: 1.45; }
    .completion-act-source-badge { flex: 0 0 auto; padding: 2px 6px; border: 1px solid rgba(116, 128, 111, .38); border-radius: 999px; color: var(--text-soft); font-size: 9px; font-weight: 600; letter-spacing: .04em; text-transform: uppercase; }
    .completion-act-source-badge.is-manual { border-color: rgba(211, 220, 164, .56); color: #d3dca4; background: rgba(167, 178, 132, .1); }
    .completion-act-items { display: flex; flex-direction: column; gap: 8px; }
    .completion-act-items__toolbar { display: flex; justify-content: space-between; gap: 8px; align-items: center; flex-wrap: wrap; }
    .completion-act-items__rows { display: flex; flex-direction: column; gap: 8px; }
    .completion-act-item {
      display: grid;
      grid-template-columns: 86px minmax(180px, 1fr) 74px 64px 104px 112px 144px;
      gap: 6px;
      align-items: end;
      padding: 8px;
      border: 1px solid rgba(116, 128, 111, .34);
      border-radius: 12px;
      background: rgba(255,255,255,.02);
    }
    .completion-act-item .field { gap: 4px; min-width: 0; }
    .completion-act-item label { font-size: 10px; color: var(--text-soft); }
    .completion-act-item__total { min-height: 38px; display: flex; align-items: center; padding: 8px 9px; border: 1px solid rgba(116, 128, 111, .25); border-radius: 8px; background: rgba(255,255,255,.025); color: var(--text-soft); font-family: var(--mono); font-size: 12px; }
    .completion-act-item__actions { display: grid; grid-template-columns: repeat(4, 32px); gap: 4px; }
    .completion-act-item__actions .btn { min-width: 32px; min-height: 38px; padding: 0; }
    .completion-act-fresh-items { display: flex; flex-direction: column; gap: 8px; padding: 11px; border: 1px solid rgba(217, 164, 65, .48); border-radius: 12px; background: rgba(217, 164, 65, .08); }
    .completion-act-fresh-items[hidden] { display: none; }
    .completion-act-fresh-items__head { display: flex; justify-content: space-between; align-items: baseline; gap: 10px; flex-wrap: wrap; }
    .completion-act-fresh-items__title { color: #f1d591; font-size: 12px; font-weight: 700; }
    .completion-act-fresh-items__count { color: var(--text-soft); font-size: 11px; }
    .completion-act-fresh-items__list { display: flex; flex-direction: column; gap: 6px; }
    .completion-act-fresh-item { display: grid; grid-template-columns: 82px minmax(0, 1fr); gap: 4px 10px; padding: 7px 8px; border: 1px solid rgba(116, 128, 111, .3); border-radius: 9px; background: rgba(14, 18, 15, .48); }
    .completion-act-fresh-item__section { grid-row: 1 / span 2; color: #d3dca4; font-family: var(--mono); font-size: 10px; text-transform: uppercase; }
    .completion-act-fresh-item__name { min-width: 0; color: var(--text); font-size: 12px; overflow-wrap: anywhere; }
    .completion-act-fresh-item__meta { color: var(--text-soft); font-size: 11px; overflow-wrap: anywhere; }
    .completion-act-totals { margin-top: 4px; margin-left: auto; width: min(100%, 360px); display: grid; grid-template-columns: 1fr auto; gap: 8px 16px; padding: 12px; border: 1px solid rgba(116, 128, 111, .36); border-radius: 12px; background: rgba(255,255,255,.025); }
    .completion-act-totals__label { color: var(--text-soft); }
    .completion-act-totals__value { text-align: right; font-family: var(--mono); color: var(--text); }
    .completion-act-totals__grand { font-weight: 700; color: #d3dca4; }
    .completion-act-editor__preview-toolbar { padding: 12px 14px; border-bottom: 1px solid rgba(116, 128, 111, .28); display: flex; justify-content: space-between; gap: 8px; align-items: center; }
    .completion-act-editor__preview-wrap { min-height: 0; flex: 1; overflow: auto; padding: 14px; background: #1a211c; display: flex; justify-content: center; align-items: flex-start; }
    .completion-act-editor__preview-stage { width: 760px; height: 1080px; position: relative; flex: 0 0 auto; }
    .completion-act-editor__preview-frame { position: absolute; inset: 0 auto auto 0; width: 920px; height: 1180px; border: 0; border-radius: 10px; background: #fff; box-shadow: 0 14px 34px rgba(0,0,0,.24); transform-origin: top left; }
    .completion-act-editor__mobile-toggle { display: none; gap: 6px; padding: 10px 14px 0; }
    .completion-act-editor__footer { display: flex; justify-content: space-between; gap: 12px; align-items: center; flex-wrap: wrap; }
    .completion-act-editor__actions { display: flex; justify-content: flex-end; gap: 8px; flex-wrap: wrap; }
    @media (max-width: 1800px) {
      .completion-act-item { grid-template-columns: 90px minmax(180px, 1fr) 100px; }
      .completion-act-item__actions { grid-column: 1 / -1; justify-content: end; }
    }
    @media (max-width: 1100px) {
      .dialog--inspection-sheet-form { width: min(100%, calc(100% - 12px)); height: min(100vh, 100%); }
      .inspection-sheet-form__grid,
      .inspection-sheet-form__row { grid-template-columns: 1fr; }
      .modal .dialog--completion-act-editor.dialog--fixed-actions { width: min(100%, calc(100% - 8px)); height: 100vh; grid-template-rows: auto auto minmax(0, 1fr) auto; }
      .completion-act-editor { grid-template-columns: 1fr; padding: 8px; }
      .completion-act-editor__mobile-toggle { display: flex; position: relative; z-index: 4; background: rgba(28, 36, 30, .98); }
      .completion-act-editor[data-mobile-view="data"] .completion-act-editor__pane--preview { display: none; }
      .completion-act-editor[data-mobile-view="preview"] .completion-act-editor__pane--form { display: none; }
      .completion-act-editor__pane { min-height: 0; }
      .completion-act-editor__grid { grid-template-columns: 1fr; }
      .completion-act-editor__field--wide { grid-column: auto; }
      .completion-act-item { grid-template-columns: 90px minmax(180px, 1fr) 78px; }
      .completion-act-item .field:nth-child(4),
      .completion-act-item .field:nth-child(5),
      .completion-act-item__total { grid-column: auto; }
    }
    @media (max-width: 620px) {
      .completion-act-item { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
      .completion-act-item .field:nth-child(2),
      .completion-act-item__actions { grid-column: 1 / -1; }
    }
    @media (max-width: 1500px) {
      .repair-order-print-layout { grid-template-columns: 220px minmax(0, 1fr); grid-template-areas: "docs preview"; }
      .repair-order-print-layout > .repair-order-print-panel:first-child { grid-area: docs; }
      .repair-order-print-layout > .repair-order-print-panel:nth-child(2) { grid-area: preview; }
      .print-template-editor { grid-template-columns: 300px minmax(0, 1fr); grid-template-areas: "list editor" "preview preview"; }
      .print-template-editor__panel { grid-area: list; }
      .print-template-editor__editor { grid-area: editor; }
      .print-template-editor__preview { grid-area: preview; }
    }
    @media (max-width: 1100px) {
      .dialog--repair-order-print,
      .dialog--print-template-editor { width: min(100%, calc(100% - 12px)); height: min(100vh, 100%); }
      .dialog--repair-order-print { max-height: min(100vh, 100%); }
      .repair-order-print-layout { grid-template-columns: 1fr; grid-template-areas: "docs" "preview"; grid-auto-rows: auto; align-content: start; overflow: auto; }
      .dialog--repair-order-print > .repair-order-print-layout { overflow: auto; }
      .repair-order-print-layout > .repair-order-print-panel:first-child { grid-area: docs; }
      .repair-order-print-layout > .repair-order-print-panel:nth-child(2) { grid-area: preview; }
      .repair-order-print-layout > .repair-order-print-panel { min-height: auto; overflow: visible; }
      .repair-order-print-layout > .repair-order-print-panel:nth-child(2) { min-height: 420px; height: auto; max-height: none; align-self: stretch; overflow: hidden; }
      .print-template-editor { grid-template-columns: 1fr; grid-template-areas: "list" "editor" "preview"; align-content: start; overflow: auto; }
      .repair-order-print-documents { max-height: 240px; }
      .repair-order-print-settings { overflow: visible; }
      .repair-order-print-settings__row { grid-template-columns: 1fr; }
      .repair-order-print-preview-wrap { max-height: min(70vh, 640px); }
      .repair-order-print-preview-frame,
      .print-template-editor__preview-frame { width: 760px; }
    }
"""


PRINTING_WEB_MODULE_HTML = r"""
  <div class="modal" id="repairOrderPrintModal">
    <div class="dialog dialog--repair-order-print dialog--fixed-actions" role="dialog" aria-modal="true" aria-labelledby="repairOrderPrintTitle">
      <div class="dialog__head dialog__floating-actions">
        <div><div class="dialog__title-prefix">ПЕЧАТЬ</div><h2 class="dialog__title" id="repairOrderPrintTitle">Документы автосервиса</h2></div>
        <button class="btn btn--ghost" id="repairOrderPrintCloseX" type="button">ЗАКРЫТЬ</button>
      </div>
      <div class="dialog__body dialog__body-scroll repair-order-print-layout">
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
            <div class="repair-order-print-preview-stage" id="repairOrderPrintPreviewStage"><iframe class="repair-order-print-preview-frame" id="repairOrderPrintPreviewFrame" title="Предпросмотр документа" sandbox="allow-same-origin"></iframe></div>
          </div>
        </section>
        <section class="repair-order-print-panel repair-order-print-panel--settings" hidden>
          <div class="repair-order-print-panel__title">Настройки</div>
          <div class="repair-order-print-settings" id="repairOrderPrintSettings">
            <section class="repair-order-print-settings__section">
              <div class="repair-order-print-settings__section-title">Режим</div>
              <div class="field field--compact"><label for="repairOrderPrintModeSelect">Источник данных</label><select id="repairOrderPrintModeSelect"><option value="card">Карточка CRM</option><option value="manual">Документ без карточки</option></select></div>
              <div class="manual-print-document-form" id="manualPrintDocumentForm" hidden>
                <div class="repair-order-print-settings__row">
                  <div class="field field--compact"><label for="manualPrintDocumentNumber">Номер</label><input id="manualPrintDocumentNumber" type="text" maxlength="40"></div>
                  <div class="field field--compact"><label for="manualPrintDocumentDate">Дата</label><input id="manualPrintDocumentDate" type="date"></div>
                </div>
                <div class="field field--compact"><label for="manualPrintTaxLabel">НДС</label><select id="manualPrintTaxLabel"><option value="НДС (5%)">НДС 5%</option><option value="Без НДС">Без НДС</option></select></div>
                <div class="field field--compact"><label for="manualPrintClientName">Клиент</label><input id="manualPrintClientName" type="text" maxlength="160"></div>
                <div class="repair-order-print-settings__row">
                  <div class="field field--compact"><label for="manualPrintClientPhone">Телефон</label><input id="manualPrintClientPhone" type="text" maxlength="80"></div>
                  <div class="field field--compact"><label for="manualPrintClientInn">ИНН</label><input id="manualPrintClientInn" type="text" maxlength="32"></div>
                </div>
                <div class="repair-order-print-settings__row">
                  <div class="field field--compact"><label for="manualPrintClientKpp">КПП</label><input id="manualPrintClientKpp" type="text" maxlength="32"></div>
                  <div class="field field--compact"><label for="manualPrintClientBik">БИК</label><input id="manualPrintClientBik" type="text" maxlength="32"></div>
                </div>
                <div class="field field--compact"><label for="manualPrintClientAddress">Адрес клиента</label><input id="manualPrintClientAddress" type="text" maxlength="240"></div>
                <div class="field field--compact"><label for="manualPrintClientBankName">Банк клиента</label><input id="manualPrintClientBankName" type="text" maxlength="160"></div>
                <div class="repair-order-print-settings__row">
                  <div class="field field--compact"><label for="manualPrintClientAccount">Р/с клиента</label><input id="manualPrintClientAccount" type="text" maxlength="64"></div>
                  <div class="field field--compact"><label for="manualPrintClientCorrAccount">К/с клиента</label><input id="manualPrintClientCorrAccount" type="text" maxlength="64"></div>
                </div>
                <div class="field field--compact"><label for="manualPrintVehicle">Автомобиль</label><input id="manualPrintVehicle" type="text" maxlength="160"></div>
                <div class="repair-order-print-settings__row">
                  <div class="field field--compact"><label for="manualPrintLicensePlate">Госномер</label><input id="manualPrintLicensePlate" type="text" maxlength="40"></div>
                  <div class="field field--compact"><label for="manualPrintVin">VIN</label><input id="manualPrintVin" type="text" maxlength="80"></div>
                </div>
                <div class="field field--compact"><label for="manualPrintMileage">Пробег</label><input id="manualPrintMileage" type="text" maxlength="40"></div>
                <div class="field field--compact"><label for="manualPrintWorks">Работы</label><textarea id="manualPrintWorks" spellcheck="false"></textarea></div>
                <div class="field field--compact"><label for="manualPrintMaterials">Материалы</label><textarea id="manualPrintMaterials" spellcheck="false"></textarea></div>
                <div class="field field--compact"><label for="manualPrintPayments">Оплаты</label><textarea id="manualPrintPayments" spellcheck="false"></textarea></div>
                <div class="field field--compact"><label for="manualPrintReason">Основание</label><textarea id="manualPrintReason" spellcheck="false"></textarea></div>
                <div class="field field--compact"><label for="manualPrintComment">Комментарий</label><textarea id="manualPrintComment" spellcheck="false"></textarea></div>
                <div class="field field--compact"><label for="manualPrintNote">Примечание мастера</label><textarea id="manualPrintNote" spellcheck="false"></textarea></div>
                <div class="field field--compact"><label for="manualPrintRequestText">Текстовый запрос агента</label><textarea id="manualPrintRequestText" spellcheck="false"></textarea></div>
              </div>
            </section>
            <section class="repair-order-print-settings__section" id="regulatedPrintOverridesForm">
              <div class="repair-order-print-settings__section-title">Счет-фактура / УПД</div>
              <div class="field field--compact"><label for="regulatedPrintBuyerName">Покупатель</label><input id="regulatedPrintBuyerName" type="text" maxlength="180"></div>
              <div class="repair-order-print-settings__row">
                <div class="field field--compact"><label for="regulatedPrintBuyerInn">ИНН покупателя</label><input id="regulatedPrintBuyerInn" type="text" maxlength="32"></div>
                <div class="field field--compact"><label for="regulatedPrintBuyerKpp">КПП покупателя</label><input id="regulatedPrintBuyerKpp" type="text" maxlength="32"></div>
              </div>
              <div class="field field--compact"><label for="regulatedPrintBuyerAddress">Адрес покупателя</label><input id="regulatedPrintBuyerAddress" type="text" maxlength="300"></div>
              <div class="field field--compact"><label for="regulatedPrintBasis">Основание передачи</label><textarea id="regulatedPrintBasis" spellcheck="false"></textarea></div>
              <div class="field field--compact"><label for="regulatedPrintTransportDetails">Транспортировка</label><textarea id="regulatedPrintTransportDetails" spellcheck="false"></textarea></div>
              <div class="repair-order-print-settings__row">
                <div class="field field--compact"><label for="regulatedPrintBuyerPosition">Должность покупателя</label><input id="regulatedPrintBuyerPosition" type="text" maxlength="120"></div>
                <div class="field field--compact"><label for="regulatedPrintBuyerSigner">Подписант покупателя</label><input id="regulatedPrintBuyerSigner" type="text" maxlength="120"></div>
              </div>
              <div class="repair-order-print-settings__row">
                <div class="field field--compact"><label for="regulatedPrintSellerPosition">Должность продавца</label><input id="regulatedPrintSellerPosition" type="text" maxlength="120"></div>
                <div class="field field--compact"><label for="regulatedPrintSellerSigner">Подписант продавца</label><input id="regulatedPrintSellerSigner" type="text" maxlength="120"></div>
              </div>
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
              <div class="repair-order-print-settings__row"><div class="field field--compact"><label for="printProfileSparePartsPhone">Телефон запчастей</label><input id="printProfileSparePartsPhone" type="text" maxlength="80"></div><div class="field field--compact"><label for="printProfileWebsite">Сайт</label><input id="printProfileWebsite" type="text" maxlength="120"></div></div>
              <div class="repair-order-print-settings__row"><div class="field field--compact"><label for="printProfileEmail">Email</label><input id="printProfileEmail" type="text" maxlength="120"></div><div class="field field--compact"><label for="printProfileWorkHours">Режим</label><input id="printProfileWorkHours" type="text" maxlength="160"></div></div>
              <div class="repair-order-print-settings__row"><div class="field field--compact"><label for="printProfileInn">ИНН</label><input id="printProfileInn" type="text" maxlength="32"></div><div class="field field--compact"><label for="printProfileKpp">КПП</label><input id="printProfileKpp" type="text" maxlength="32"></div></div>
              <div class="field field--compact"><label for="printProfileOgrn">ОГРН</label><input id="printProfileOgrn" type="text" maxlength="32"></div>
              <div class="repair-order-print-settings__row"><div class="field field--compact"><label for="printProfileBankName">Банк</label><input id="printProfileBankName" type="text" maxlength="160"></div><div class="field field--compact"><label for="printProfileBik">БИК</label><input id="printProfileBik" type="text" maxlength="32"></div></div>
              <div class="repair-order-print-settings__row"><div class="field field--compact"><label for="printProfileSettlementAccount">Р/с</label><input id="printProfileSettlementAccount" type="text" maxlength="64"></div><div class="field field--compact"><label for="printProfileCorrespondentAccount">К/с</label><input id="printProfileCorrespondentAccount" type="text" maxlength="64"></div></div>
              <div class="repair-order-print-settings__row"><div class="field field--compact"><label for="printProfileSignerPosition">Должность подписанта</label><input id="printProfileSignerPosition" type="text" maxlength="120"></div><div class="field field--compact"><label for="printProfileSignerName">ФИО подписанта</label><input id="printProfileSignerName" type="text" maxlength="160"></div></div>
              <div class="field field--compact"><label for="printProfileTaxLabel">Налоговый режим</label><input id="printProfileTaxLabel" type="text" maxlength="48"></div>
              <div class="field field--compact"><label for="printProfilePaymentPurpose">Назначение платежа</label><input id="printProfilePaymentPurpose" type="text" maxlength="240"></div>
              <button class="btn btn--ghost" id="repairOrderPrintSaveSettingsButton" type="button">СОХРАНИТЬ НАСТРОЙКИ</button>
            </section>
          </div>
        </section>
      </div>
      <div class="dialog__foot repair-order-print-footer dialog__floating-actions">
        <div class="repair-order-print-preview__meta" id="repairOrderPrintFooterMeta">PDF генерируется из шаблона и текущих данных заказ-наряда.</div>
        <div class="repair-order-print-footer__actions"><button class="btn btn--ghost" id="repairOrderPrintExportButton" type="button">PDF</button><button class="btn" id="repairOrderPrintRunButton" type="button">ПЕЧАТЬ</button></div>
      </div>
    </div>
  </div>

  <div class="modal" id="printTemplateEditorModal">
    <div class="dialog dialog--print-template-editor dialog--fixed-actions" role="dialog" aria-modal="true" aria-labelledby="printTemplateEditorTitle">
      <div class="dialog__head dialog__floating-actions">
        <div><div class="dialog__title-prefix">ШАБЛОНЫ</div><h2 class="dialog__title" id="printTemplateEditorTitle">Редактор печатных шаблонов</h2></div>
        <button class="btn btn--ghost" id="printTemplateEditorCloseX" type="button">ЗАКРЫТЬ</button>
      </div>
      <div class="dialog__body dialog__body-scroll print-template-editor">
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
          <div class="print-template-editor__surface-wrap"><iframe class="print-template-editor__surface-frame" id="printTemplateVisualEditorFrame" title="Визуальный редактор шаблона" sandbox="allow-same-origin"></iframe></div>
          <details class="print-template-editor__source" id="printTemplateSourceSection"><summary>Исходный шаблон</summary><textarea id="printTemplateContent" spellcheck="false"></textarea></details>
        </section>
        <section class="print-template-editor__preview">
          <div class="print-template-editor__title">Предпросмотр</div>
          <div class="print-template-editor__meta" id="printTemplatePreviewMeta">Предпросмотр использует текущий заказ-наряд как тестовые данные.</div>
          <div class="print-template-editor__preview-wrap"><iframe class="print-template-editor__preview-frame" id="printTemplatePreviewFrame" title="Предпросмотр шаблона" sandbox="allow-same-origin"></iframe></div>
        </section>
      </div>
      <div class="dialog__foot print-template-editor__footer dialog__floating-actions">
        <div class="print-template-editor__meta" id="printTemplateFooterMeta">Встроенные шаблоны можно дублировать и делать шаблоном по умолчанию.</div>
        <div class="print-template-editor__actions"><button class="btn btn--ghost" id="printTemplateSetDefaultButton" type="button">ПО УМОЛЧАНИЮ</button><button class="btn btn--ghost" id="printTemplatePreviewButton" type="button">ПРЕДПРОСМОТР</button><button class="btn" id="printTemplateSaveButton" type="button">СОХРАНИТЬ</button></div>
      </div>
    </div>
  </div>

  <div class="modal" id="inspectionSheetFormModal">
    <div class="dialog dialog--inspection-sheet-form dialog--fixed-actions" role="dialog" aria-modal="true" aria-labelledby="inspectionSheetFormTitle">
      <div class="dialog__head dialog__floating-actions">
        <div><div class="dialog__title-prefix">ВЕДОМОСТЬ</div><h2 class="dialog__title" id="inspectionSheetFormTitle">Заполнение дефектовочной ведомости</h2></div>
        <button class="btn btn--ghost" id="inspectionSheetFormCloseX" type="button">ЗАКРЫТЬ</button>
      </div>
      <div class="dialog__body dialog__body-scroll inspection-sheet-form">
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
      <div class="dialog__foot inspection-sheet-form__footer dialog__floating-actions">
        <div class="inspection-sheet-form__hint" id="inspectionSheetFormFooterMeta">После применения предпросмотр и печать будут использовать заполненную ведомость.</div>
        <div class="inspection-sheet-form__actions">
          <button class="btn btn--ghost" id="inspectionSheetFormAutofillButton" type="button">АВТОЗАПОЛНЕНИЕ</button>
          <button class="btn btn--ghost" id="inspectionSheetFormSaveButton" type="button">СОХРАНИТЬ</button>
          <button class="btn" id="inspectionSheetFormApplyButton" type="button">ПРИМЕНИТЬ</button>
        </div>
      </div>
    </div>
  </div>

  <div class="modal" id="completionActEditorModal">
    <div class="dialog dialog--completion-act-editor dialog--fixed-actions" role="dialog" aria-modal="true" aria-labelledby="completionActEditorTitle">
      <div class="dialog__head dialog__floating-actions">
        <div><div class="dialog__title-prefix">АКТ</div><h2 class="dialog__title" id="completionActEditorTitle">Редактор акта выполненных работ</h2></div>
        <button class="btn btn--ghost" id="completionActEditorCloseX" type="button">ЗАКРЫТЬ</button>
      </div>
      <div class="completion-act-editor__mobile-toggle" role="group" aria-label="Режим редактора">
        <button class="btn btn--ghost is-active" id="completionActMobileDataButton" type="button" aria-pressed="true">ДАННЫЕ</button>
        <button class="btn btn--ghost" id="completionActMobilePreviewButton" type="button" aria-pressed="false">ПРЕДПРОСМОТР</button>
      </div>
      <div class="dialog__body dialog__body-scroll completion-act-editor" id="completionActEditorLayout" data-mobile-view="data">
        <section class="completion-act-editor__pane completion-act-editor__pane--form">
          <div class="completion-act-editor__pane-head">
            <div class="completion-act-editor__pane-title">Данные документа</div>
            <div class="completion-act-editor__meta" id="completionActEditorMeta">Загрузка данных из заказ-наряда...</div>
            <nav class="completion-act-editor__tabs" aria-label="Разделы акта">
              <button class="btn btn--ghost completion-act-editor__tab is-active" data-completion-act-section="document" type="button" aria-pressed="true">ДОКУМЕНТ</button>
              <button class="btn btn--ghost completion-act-editor__tab" data-completion-act-section="performer" type="button" aria-pressed="false">ИСПОЛНИТЕЛЬ</button>
              <button class="btn btn--ghost completion-act-editor__tab" data-completion-act-section="customer" type="button" aria-pressed="false">ЗАКАЗЧИК</button>
              <button class="btn btn--ghost completion-act-editor__tab" data-completion-act-section="items" type="button" aria-pressed="false">РАБОТЫ И МАТЕРИАЛЫ</button>
              <button class="btn btn--ghost completion-act-editor__tab" data-completion-act-section="terms" type="button" aria-pressed="false">УСЛОВИЯ И ПОДПИСИ</button>
            </nav>
          </div>
          <div class="completion-act-editor__warning" id="completionActStaleWarning" role="status" hidden></div>
          <div class="completion-act-editor__warning completion-act-editor__warning--live" id="completionActLiveWarnings" role="status" aria-live="polite" aria-atomic="true" hidden></div>
          <form class="completion-act-editor__form" id="completionActEditorForm" autocomplete="off">
            <section class="completion-act-editor__section" data-completion-act-panel="document">
              <div class="completion-act-editor__section-title">Документ</div>
              <div class="completion-act-editor__grid">
                <div class="completion-act-editor__field"><div class="field field--compact"><label for="completionActDocumentNumber">Номер <span class="completion-act-source-badge" data-completion-act-source="document_number">из ЗН</span></label><input id="completionActDocumentNumber" data-completion-act-field="document_number" type="text" maxlength="80"></div></div>
                <div class="completion-act-editor__field"><div class="field field--compact"><label for="completionActDocumentDate">Дата <span class="completion-act-source-badge" data-completion-act-source="document_date">из ЗН</span></label><input id="completionActDocumentDate" data-completion-act-field="document_date" type="text" maxlength="64" placeholder="ДД.ММ.ГГГГ"></div></div>
                <div class="completion-act-editor__field completion-act-editor__field--wide"><div class="field field--compact"><label for="completionActBasis">Основание <span class="completion-act-source-badge" data-completion-act-source="basis">из ЗН</span></label><textarea id="completionActBasis" data-completion-act-field="basis" maxlength="500"></textarea></div></div>
              </div>
            </section>
            <section class="completion-act-editor__section" data-completion-act-panel="performer" hidden>
              <div class="completion-act-editor__section-title">Реквизиты исполнителя</div>
              <div class="completion-act-editor__grid" id="completionActPerformerFields">
                <div class="completion-act-editor__field completion-act-editor__field--wide"><div class="field field--compact"><label for="completionActPerformerLegalName">Наименование <span class="completion-act-source-badge" data-completion-act-source="performer.legal_name">из настроек</span></label><input id="completionActPerformerLegalName" data-completion-act-field="performer.legal_name" type="text" maxlength="240"></div></div>
                <div class="completion-act-editor__field completion-act-editor__field--wide"><div class="field field--compact"><label for="completionActPerformerAddress">Адрес <span class="completion-act-source-badge" data-completion-act-source="performer.address">из настроек</span></label><input id="completionActPerformerAddress" data-completion-act-field="performer.address" type="text" maxlength="320"></div></div>
                <div class="completion-act-editor__field"><div class="field field--compact"><label for="completionActPerformerInn">ИНН <span class="completion-act-source-badge" data-completion-act-source="performer.inn">из настроек</span></label><input id="completionActPerformerInn" data-completion-act-field="performer.inn" type="text" maxlength="32"></div></div>
                <div class="completion-act-editor__field"><div class="field field--compact"><label for="completionActPerformerKpp">КПП <span class="completion-act-source-badge" data-completion-act-source="performer.kpp">из настроек</span></label><input id="completionActPerformerKpp" data-completion-act-field="performer.kpp" type="text" maxlength="32"></div></div>
                <div class="completion-act-editor__field"><div class="field field--compact"><label for="completionActPerformerOgrn">ОГРН <span class="completion-act-source-badge" data-completion-act-source="performer.ogrn">из настроек</span></label><input id="completionActPerformerOgrn" data-completion-act-field="performer.ogrn" type="text" maxlength="32"></div></div>
                <div class="completion-act-editor__field"><div class="field field--compact"><label for="completionActPerformerBik">БИК <span class="completion-act-source-badge" data-completion-act-source="performer.bik">из настроек</span></label><input id="completionActPerformerBik" data-completion-act-field="performer.bik" type="text" maxlength="32"></div></div>
                <div class="completion-act-editor__field completion-act-editor__field--wide"><div class="field field--compact"><label for="completionActPerformerBankName">Банк <span class="completion-act-source-badge" data-completion-act-source="performer.bank_name">из настроек</span></label><input id="completionActPerformerBankName" data-completion-act-field="performer.bank_name" type="text" maxlength="240"></div></div>
                <div class="completion-act-editor__field"><div class="field field--compact"><label for="completionActPerformerSettlementAccount">Расчётный счёт <span class="completion-act-source-badge" data-completion-act-source="performer.settlement_account">из настроек</span></label><input id="completionActPerformerSettlementAccount" data-completion-act-field="performer.settlement_account" type="text" maxlength="64"></div></div>
                <div class="completion-act-editor__field"><div class="field field--compact"><label for="completionActPerformerCorrespondentAccount">Корр. счёт <span class="completion-act-source-badge" data-completion-act-source="performer.correspondent_account">из настроек</span></label><input id="completionActPerformerCorrespondentAccount" data-completion-act-field="performer.correspondent_account" type="text" maxlength="64"></div></div>
              </div>
            </section>
            <section class="completion-act-editor__section" data-completion-act-panel="customer" hidden>
              <div class="completion-act-editor__section-title">Реквизиты заказчика</div>
              <div class="completion-act-editor__grid" id="completionActCustomerFields">
                <div class="completion-act-editor__field completion-act-editor__field--wide"><div class="field field--compact"><label for="completionActCustomerLegalName">Наименование <span class="completion-act-source-badge" data-completion-act-source="customer.legal_name">из клиента</span></label><input id="completionActCustomerLegalName" data-completion-act-field="customer.legal_name" type="text" maxlength="240"></div></div>
                <div class="completion-act-editor__field completion-act-editor__field--wide"><div class="field field--compact"><label for="completionActCustomerAddress">Адрес <span class="completion-act-source-badge" data-completion-act-source="customer.address">из клиента</span></label><input id="completionActCustomerAddress" data-completion-act-field="customer.address" type="text" maxlength="320"></div></div>
                <div class="completion-act-editor__field"><div class="field field--compact"><label for="completionActCustomerInn">ИНН <span class="completion-act-source-badge" data-completion-act-source="customer.inn">из клиента</span></label><input id="completionActCustomerInn" data-completion-act-field="customer.inn" type="text" maxlength="32"></div></div>
                <div class="completion-act-editor__field"><div class="field field--compact"><label for="completionActCustomerKpp">КПП <span class="completion-act-source-badge" data-completion-act-source="customer.kpp">из клиента</span></label><input id="completionActCustomerKpp" data-completion-act-field="customer.kpp" type="text" maxlength="32"></div></div>
                <div class="completion-act-editor__field"><div class="field field--compact"><label for="completionActCustomerOgrn">ОГРН <span class="completion-act-source-badge" data-completion-act-source="customer.ogrn">из клиента</span></label><input id="completionActCustomerOgrn" data-completion-act-field="customer.ogrn" type="text" maxlength="32"></div></div>
                <div class="completion-act-editor__field"><div class="field field--compact"><label for="completionActCustomerBik">БИК <span class="completion-act-source-badge" data-completion-act-source="customer.bik">из клиента</span></label><input id="completionActCustomerBik" data-completion-act-field="customer.bik" type="text" maxlength="32"></div></div>
                <div class="completion-act-editor__field completion-act-editor__field--wide"><div class="field field--compact"><label for="completionActCustomerBankName">Банк <span class="completion-act-source-badge" data-completion-act-source="customer.bank_name">из клиента</span></label><input id="completionActCustomerBankName" data-completion-act-field="customer.bank_name" type="text" maxlength="240"></div></div>
                <div class="completion-act-editor__field"><div class="field field--compact"><label for="completionActCustomerSettlementAccount">Расчётный счёт <span class="completion-act-source-badge" data-completion-act-source="customer.settlement_account">из клиента</span></label><input id="completionActCustomerSettlementAccount" data-completion-act-field="customer.settlement_account" type="text" maxlength="64"></div></div>
                <div class="completion-act-editor__field"><div class="field field--compact"><label for="completionActCustomerCorrespondentAccount">Корр. счёт <span class="completion-act-source-badge" data-completion-act-source="customer.correspondent_account">из клиента</span></label><input id="completionActCustomerCorrespondentAccount" data-completion-act-field="customer.correspondent_account" type="text" maxlength="64"></div></div>
              </div>
            </section>
            <section class="completion-act-editor__section" data-completion-act-panel="items" hidden>
              <div class="completion-act-editor__section-title">Работы и материалы</div>
              <div class="completion-act-items">
                <div class="completion-act-items__toolbar"><div class="completion-act-editor__meta">Итоги рассчитываются автоматически. НДС всегда начисляется сверху по ставке 5%.</div><button class="btn btn--ghost" id="completionActAddItemButton" type="button">+ СТРОКА</button></div>
                <div class="completion-act-items__rows" id="completionActItemRows"></div>
                <aside class="completion-act-fresh-items" id="completionActFreshItems" role="region" aria-labelledby="completionActFreshItemsTitle" aria-live="polite" hidden>
                  <div class="completion-act-fresh-items__head"><div class="completion-act-fresh-items__title" id="completionActFreshItemsTitle">Свежие строки из заказ-наряда</div><div class="completion-act-fresh-items__count" id="completionActFreshItemsCount"></div></div>
                  <div class="completion-act-fresh-items__list" id="completionActFreshItemsList" role="list" aria-label="Свежие строки из заказ-наряда"></div>
                </aside>
                <div class="completion-act-totals" aria-live="polite">
                  <div class="completion-act-totals__label">Итого без НДС</div><output class="completion-act-totals__value" id="completionActBaseTotal">0,00 ₽</output>
                  <div class="completion-act-totals__label">НДС (5%)</div><output class="completion-act-totals__value" id="completionActVatTotal">0,00 ₽</output>
                  <div class="completion-act-totals__label completion-act-totals__grand">Всего</div><output class="completion-act-totals__value completion-act-totals__grand" id="completionActGrossTotal">0,00 ₽</output>
                </div>
              </div>
            </section>
            <section class="completion-act-editor__section" data-completion-act-panel="terms" hidden>
              <div class="completion-act-editor__section-title">Условия и подписи</div>
              <div class="completion-act-editor__grid">
                <div class="completion-act-editor__field completion-act-editor__field--wide"><div class="field field--compact"><label for="completionActAcceptanceText">Текст приёмки <span class="completion-act-source-badge" data-completion-act-source="acceptance_text">из ЗН</span></label><textarea id="completionActAcceptanceText" data-completion-act-field="acceptance_text" maxlength="1000"></textarea></div></div>
                <div class="completion-act-editor__field"><div class="field field--compact"><label for="completionActPerformerSignerPosition">Должность исполнителя <span class="completion-act-source-badge" data-completion-act-source="performer.signer_position">из настроек</span></label><input id="completionActPerformerSignerPosition" data-completion-act-field="performer.signer_position" type="text" maxlength="120"></div></div>
                <div class="completion-act-editor__field"><div class="field field--compact"><label for="completionActPerformerSignerName">ФИО исполнителя <span class="completion-act-source-badge" data-completion-act-source="performer.signer_name">из настроек</span></label><input id="completionActPerformerSignerName" data-completion-act-field="performer.signer_name" type="text" maxlength="160"></div></div>
                <div class="completion-act-editor__field"><div class="field field--compact"><label for="completionActCustomerSignerPosition">Должность заказчика <span class="completion-act-source-badge" data-completion-act-source="customer.signer_position">из клиента</span></label><input id="completionActCustomerSignerPosition" data-completion-act-field="customer.signer_position" type="text" maxlength="120"></div></div>
                <div class="completion-act-editor__field"><div class="field field--compact"><label for="completionActCustomerSignerName">ФИО заказчика <span class="completion-act-source-badge" data-completion-act-source="customer.signer_name">из клиента</span></label><input id="completionActCustomerSignerName" data-completion-act-field="customer.signer_name" type="text" maxlength="160"></div></div>
              </div>
            </section>
          </form>
        </section>
        <section class="completion-act-editor__pane completion-act-editor__pane--preview">
          <div class="completion-act-editor__preview-toolbar"><div><div class="completion-act-editor__pane-title">Предпросмотр A4</div><div class="completion-act-editor__meta" id="completionActPreviewMeta">Страница 1 / 1</div></div><div><button class="btn btn--ghost" id="completionActPrevPageButton" type="button" aria-label="Предыдущая страница">←</button> <button class="btn btn--ghost" id="completionActNextPageButton" type="button" aria-label="Следующая страница">→</button></div></div>
          <div class="completion-act-editor__preview-wrap" id="completionActPreviewWrap"><div class="completion-act-editor__preview-stage" id="completionActPreviewStage"><iframe class="completion-act-editor__preview-frame" id="completionActPreviewFrame" title="Предпросмотр акта выполненных работ" sandbox="allow-same-origin"></iframe></div></div>
        </section>
      </div>
      <div class="dialog__foot completion-act-editor__footer dialog__floating-actions">
        <div class="completion-act-editor__meta" id="completionActEditorFooterMeta">Ручные изменения относятся только к печатной версии и не меняют заказ-наряд.</div>
        <div class="completion-act-editor__actions"><button class="btn btn--ghost" id="completionActResetButton" type="button">СБРОСИТЬ К CRM</button><button class="btn btn--ghost" id="completionActExportButton" type="button">PDF</button><button class="btn btn--ghost" id="completionActPrintButton" type="button">ПЕЧАТЬ</button><button class="btn" id="completionActSaveButton" type="button">СОХРАНИТЬ ЧЕРНОВИК</button></div>
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
      mode: 'card',
      manualDocument: null,
      inspectionSheetForm: null,
      completionAct: {
        form: null,
        savedForm: null,
        freshForm: null,
        sources: {},
        draft: {
          exists: false,
          version: 0,
          source_fingerprint: '',
          current_source_fingerprint: '',
          is_stale: false,
        },
        totals: {},
        computedItems: [],
        warnings: [],
        missingFields: [],
        dirty: false,
        preview: null,
        previewToken: 0,
        pageIndex: 0,
        activeSection: 'document',
        returnFocus: null,
        generation: 0,
        cardId: '',
        editRevision: 0,
      },
      templateEditor: { documentType: 'repair_order', templateId: '' },
    };
    let printTemplatePreviewTimer = null;
    let manualPrintPreviewTimer = null;
    let regulatedPrintPreviewTimer = null;
    let completionActPreviewTimer = null;

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
      previewStage: document.getElementById('repairOrderPrintPreviewStage'),
      previewFrame: document.getElementById('repairOrderPrintPreviewFrame'),
      fitWidthButton: document.getElementById('repairOrderPrintFitWidthButton'),
      actualSizeButton: document.getElementById('repairOrderPrintActualSizeButton'),
      zoomOutButton: document.getElementById('repairOrderPrintZoomOutButton'),
      zoomInButton: document.getElementById('repairOrderPrintZoomInButton'),
      prevPageButton: document.getElementById('repairOrderPrintPrevPageButton'),
      nextPageButton: document.getElementById('repairOrderPrintNextPageButton'),
      templateSelect: document.getElementById('repairOrderPrintTemplateSelect'),
      modeSelect: document.getElementById('repairOrderPrintModeSelect'),
      manualForm: document.getElementById('manualPrintDocumentForm'),
      manualDocumentNumber: document.getElementById('manualPrintDocumentNumber'),
      manualDocumentDate: document.getElementById('manualPrintDocumentDate'),
      manualTaxLabel: document.getElementById('manualPrintTaxLabel'),
      manualClientName: document.getElementById('manualPrintClientName'),
      manualClientPhone: document.getElementById('manualPrintClientPhone'),
      manualClientInn: document.getElementById('manualPrintClientInn'),
      manualClientKpp: document.getElementById('manualPrintClientKpp'),
      manualClientAddress: document.getElementById('manualPrintClientAddress'),
      manualClientBankName: document.getElementById('manualPrintClientBankName'),
      manualClientBik: document.getElementById('manualPrintClientBik'),
      manualClientAccount: document.getElementById('manualPrintClientAccount'),
      manualClientCorrAccount: document.getElementById('manualPrintClientCorrAccount'),
      manualVehicle: document.getElementById('manualPrintVehicle'),
      manualLicensePlate: document.getElementById('manualPrintLicensePlate'),
      manualVin: document.getElementById('manualPrintVin'),
      manualMileage: document.getElementById('manualPrintMileage'),
      manualWorks: document.getElementById('manualPrintWorks'),
      manualMaterials: document.getElementById('manualPrintMaterials'),
      manualPayments: document.getElementById('manualPrintPayments'),
      manualReason: document.getElementById('manualPrintReason'),
      manualComment: document.getElementById('manualPrintComment'),
      manualNote: document.getElementById('manualPrintNote'),
      manualRequestText: document.getElementById('manualPrintRequestText'),
      regulatedOverridesForm: document.getElementById('regulatedPrintOverridesForm'),
      regulatedBuyerName: document.getElementById('regulatedPrintBuyerName'),
      regulatedBuyerInn: document.getElementById('regulatedPrintBuyerInn'),
      regulatedBuyerKpp: document.getElementById('regulatedPrintBuyerKpp'),
      regulatedBuyerAddress: document.getElementById('regulatedPrintBuyerAddress'),
      regulatedBasis: document.getElementById('regulatedPrintBasis'),
      regulatedTransportDetails: document.getElementById('regulatedPrintTransportDetails'),
      regulatedBuyerPosition: document.getElementById('regulatedPrintBuyerPosition'),
      regulatedBuyerSigner: document.getElementById('regulatedPrintBuyerSigner'),
      regulatedSellerPosition: document.getElementById('regulatedPrintSellerPosition'),
      regulatedSellerSigner: document.getElementById('regulatedPrintSellerSigner'),
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
      profileSparePartsPhone: document.getElementById('printProfileSparePartsPhone'),
      profileEmail: document.getElementById('printProfileEmail'),
      profileWebsite: document.getElementById('printProfileWebsite'),
      profileWorkHours: document.getElementById('printProfileWorkHours'),
      profileInn: document.getElementById('printProfileInn'),
      profileKpp: document.getElementById('printProfileKpp'),
      profileOgrn: document.getElementById('printProfileOgrn'),
      profileBankName: document.getElementById('printProfileBankName'),
      profileBik: document.getElementById('printProfileBik'),
      profileSettlementAccount: document.getElementById('printProfileSettlementAccount'),
      profileCorrespondentAccount: document.getElementById('printProfileCorrespondentAccount'),
      profileSignerPosition: document.getElementById('printProfileSignerPosition'),
      profileSignerName: document.getElementById('printProfileSignerName'),
      profileTaxLabel: document.getElementById('printProfileTaxLabel'),
      profilePaymentPurpose: document.getElementById('printProfilePaymentPurpose'),
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
      completionActModal: document.getElementById('completionActEditorModal'),
      completionActCloseX: document.getElementById('completionActEditorCloseX'),
      completionActLayout: document.getElementById('completionActEditorLayout'),
      completionActForm: document.getElementById('completionActEditorForm'),
      completionActMeta: document.getElementById('completionActEditorMeta'),
      completionActStaleWarning: document.getElementById('completionActStaleWarning'),
      completionActLiveWarnings: document.getElementById('completionActLiveWarnings'),
      completionActItemRows: document.getElementById('completionActItemRows'),
      completionActFreshItems: document.getElementById('completionActFreshItems'),
      completionActFreshItemsCount: document.getElementById('completionActFreshItemsCount'),
      completionActFreshItemsList: document.getElementById('completionActFreshItemsList'),
      completionActAddItemButton: document.getElementById('completionActAddItemButton'),
      completionActBaseTotal: document.getElementById('completionActBaseTotal'),
      completionActVatTotal: document.getElementById('completionActVatTotal'),
      completionActGrossTotal: document.getElementById('completionActGrossTotal'),
      completionActPreviewWrap: document.getElementById('completionActPreviewWrap'),
      completionActPreviewStage: document.getElementById('completionActPreviewStage'),
      completionActPreviewFrame: document.getElementById('completionActPreviewFrame'),
      completionActPreviewMeta: document.getElementById('completionActPreviewMeta'),
      completionActPrevPageButton: document.getElementById('completionActPrevPageButton'),
      completionActNextPageButton: document.getElementById('completionActNextPageButton'),
      completionActFooterMeta: document.getElementById('completionActEditorFooterMeta'),
      completionActResetButton: document.getElementById('completionActResetButton'),
      completionActExportButton: document.getElementById('completionActExportButton'),
      completionActPrintButton: document.getElementById('completionActPrintButton'),
      completionActSaveButton: document.getElementById('completionActSaveButton'),
      completionActMobileDataButton: document.getElementById('completionActMobileDataButton'),
      completionActMobilePreviewButton: document.getElementById('completionActMobilePreviewButton'),
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
      { label: 'Телефон запчастей', value: '{{service.spare_parts_phone}}' },
      { label: 'Сайт', value: '{{service.website}}' },
      { label: 'Банк', value: '{{service.bank_name}}' },
      { label: 'Расчетный счет', value: '{{service.settlement_account}}' },
      { label: 'Условия приема', value: '{{{repair_order.acceptance_terms_html}}}' },
    ];
    const PRINT_TEMPLATE_UPLOAD_MAX_SIZE_BYTES = 256 * 1024;
    const COMPLETION_ACT_MAX_ITEMS = 300;

    function printFiniteNumber(value, fallback = 0) {
      const numeric = Number(value);
      return Number.isFinite(numeric) ? numeric : fallback;
    }

    function buildPrintTemplateVisualEditorHtml(content) {
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
      const pageCount = Math.max(1, printFiniteNumber(preview?.page_count ?? preview?.pages?.length, 1));
      const current = printFiniteNumber(repairOrderPrintState.pageIndexByDocument?.[activeId]);
      return Math.max(0, Math.min(pageCount - 1, current));
    }

    function repairOrderPrintSetPageIndex(index) {
      repairOrderPrintState.pageIndexByDocument[repairOrderPrintActiveDocument()] = index;
      renderRepairOrderPrintPreview();
    }

    function repairOrderPrintResetPreviewScroll() {
      if (!printEls.previewFrame) return;
      try {
        printEls.previewFrame.contentWindow?.scrollTo(0, 0);
        const doc = printEls.previewFrame.contentDocument;
        if (doc?.documentElement) doc.documentElement.scrollTop = 0;
        if (doc?.body) doc.body.scrollTop = 0;
      } catch (error) {
        // Ignore cross-context scroll resets; srcdoc should still render correctly.
      }
    }

    function repairOrderPrintSettingsPayload() {
      return {
        default_printer: printEls.printerSelect?.value || '',
        copies: Math.max(1, Math.min(20, printFiniteNumber(printEls.copies?.value, 1))),
        paper_size: printEls.paperSize?.value || 'A4',
        orientation: printEls.orientation?.value || 'portrait',
        service_profile: {
          company_name: printEls.profileCompanyName?.value || '',
          legal_name: printEls.profileLegalName?.value || '',
          address: printEls.profileAddress?.value || '',
          phone: printEls.profilePhone?.value || '',
          reception_phone: printEls.profileReceptionPhone?.value || '',
          spare_parts_phone: printEls.profileSparePartsPhone?.value || '',
          email: printEls.profileEmail?.value || '',
          website: printEls.profileWebsite?.value || '',
          work_hours: printEls.profileWorkHours?.value || '',
          inn: printEls.profileInn?.value || '',
          kpp: printEls.profileKpp?.value || '',
          ogrn: printEls.profileOgrn?.value || '',
          bank_name: printEls.profileBankName?.value || '',
          bik: printEls.profileBik?.value || '',
          settlement_account: printEls.profileSettlementAccount?.value || '',
          correspondent_account: printEls.profileCorrespondentAccount?.value || '',
          signer_position: printEls.profileSignerPosition?.value || '',
          signer_name: printEls.profileSignerName?.value || '',
          tax_label: printEls.profileTaxLabel?.value || '',
          payment_purpose: printEls.profilePaymentPurpose?.value || '',
        },
      };
    }

    function repairOrderPrintIsManualMode() {
      return repairOrderPrintState.mode === 'manual';
    }

    function manualPrintLocalDateValue() {
      const date = new Date();
      const year = String(date.getFullYear()).padStart(4, '0');
      const month = String(date.getMonth() + 1).padStart(2, '0');
      const day = String(date.getDate()).padStart(2, '0');
      return year + '-' + month + '-' + day;
    }

    function blankManualPrintDocument() {
      return {
        document_number: '',
        document_date: manualPrintLocalDateValue(),
        tax_label: 'НДС (5%)',
        client: {
          display_name: '',
          phone: '',
          inn: '',
          kpp: '',
          legal_address: '',
          actual_address: '',
          bank_name: '',
          bik: '',
          checking_account: '',
          correspondent_account: '',
        },
        vehicle: { name: '', license_plate: '', vin: '', mileage: '' },
        works: [],
        materials: [],
        payments: [],
        reason: '',
        comment: '',
        note: '',
      };
    }

    function splitManualPrintRows(value) {
      return String(value || '')
        .replace(/\r\n/g, '\n')
        .replace(/\r/g, '\n')
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean);
    }

    function readManualPrintLineItems(value) {
      return splitManualPrintRows(value).map((line) => {
        const parts = line.split(/[|\t;]/).map((item) => item.trim());
        if (parts.length >= 3) {
          return { name: parts[0], quantity: parts[1] || '1', price: parts[2] || '', total: parts[3] || '' };
        }
        const match = line.match(/^(.*?)\s+(\d+(?:[,.]\d+)?)\s*(?:x|х|\*)\s*(\d+(?:[,.]\d+)?)$/i);
        if (match) {
          return { name: match[1].trim(), quantity: match[2].replace(',', '.'), price: match[3].replace(',', '.') };
        }
        return { name: line, quantity: '1', price: '', total: '' };
      });
    }

    function readManualPrintPayments(value) {
      return splitManualPrintRows(value).map((line) => {
        const parts = line.split(/[|\t;]/).map((item) => item.trim());
        return {
          amount: parts[0] || line,
          paid_at: parts[1] || '',
          payment_method: parts[2] || '',
          note: parts.slice(3).join(' · '),
        };
      });
    }

    function applyManualPrintDocumentToInputs(documentData = blankManualPrintDocument()) {
      const data = { ...blankManualPrintDocument(), ...(documentData || {}) };
      const client = { ...blankManualPrintDocument().client, ...(data.client || {}) };
      const vehicle = { ...blankManualPrintDocument().vehicle, ...(data.vehicle || {}) };
      if (printEls.manualDocumentNumber) printEls.manualDocumentNumber.value = data.document_number || '';
      if (printEls.manualDocumentDate) printEls.manualDocumentDate.value = data.document_date || '';
      if (printEls.manualTaxLabel) printEls.manualTaxLabel.value = data.tax_label || 'НДС (5%)';
      if (printEls.manualClientName) printEls.manualClientName.value = client.display_name || '';
      if (printEls.manualClientPhone) printEls.manualClientPhone.value = client.phone || '';
      if (printEls.manualClientInn) printEls.manualClientInn.value = client.inn || '';
      if (printEls.manualClientKpp) printEls.manualClientKpp.value = client.kpp || '';
      if (printEls.manualClientAddress) printEls.manualClientAddress.value = client.legal_address || client.actual_address || '';
      if (printEls.manualClientBankName) printEls.manualClientBankName.value = client.bank_name || '';
      if (printEls.manualClientBik) printEls.manualClientBik.value = client.bik || '';
      if (printEls.manualClientAccount) printEls.manualClientAccount.value = client.checking_account || '';
      if (printEls.manualClientCorrAccount) printEls.manualClientCorrAccount.value = client.correspondent_account || '';
      if (printEls.manualVehicle) printEls.manualVehicle.value = vehicle.name || '';
      if (printEls.manualLicensePlate) printEls.manualLicensePlate.value = vehicle.license_plate || '';
      if (printEls.manualVin) printEls.manualVin.value = vehicle.vin || '';
      if (printEls.manualMileage) printEls.manualMileage.value = vehicle.mileage || '';
      if (printEls.manualWorks) printEls.manualWorks.value = '';
      if (printEls.manualMaterials) printEls.manualMaterials.value = '';
      if (printEls.manualPayments) printEls.manualPayments.value = '';
      if (printEls.manualReason) printEls.manualReason.value = data.reason || '';
      if (printEls.manualComment) printEls.manualComment.value = data.comment || '';
      if (printEls.manualNote) printEls.manualNote.value = data.note || '';
      if (printEls.manualRequestText) printEls.manualRequestText.value = '';
    }

    function readManualPrintDocumentFromInputs() {
      const address = printEls.manualClientAddress?.value || '';
      return {
        document_number: printEls.manualDocumentNumber?.value || '',
        document_date: printEls.manualDocumentDate?.value || '',
        tax_label: printEls.manualTaxLabel?.value || '',
        client: {
          display_name: printEls.manualClientName?.value || '',
          phone: printEls.manualClientPhone?.value || '',
          inn: printEls.manualClientInn?.value || '',
          kpp: printEls.manualClientKpp?.value || '',
          legal_address: address,
          actual_address: address,
          bank_name: printEls.manualClientBankName?.value || '',
          bik: printEls.manualClientBik?.value || '',
          checking_account: printEls.manualClientAccount?.value || '',
          correspondent_account: printEls.manualClientCorrAccount?.value || '',
        },
        vehicle: {
          name: printEls.manualVehicle?.value || '',
          license_plate: printEls.manualLicensePlate?.value || '',
          vin: printEls.manualVin?.value || '',
          mileage: printEls.manualMileage?.value || '',
        },
        works: readManualPrintLineItems(printEls.manualWorks?.value || ''),
        materials: readManualPrintLineItems(printEls.manualMaterials?.value || ''),
        payments: readManualPrintPayments(printEls.manualPayments?.value || ''),
        reason: printEls.manualReason?.value || '',
        comment: printEls.manualComment?.value || '',
        note: printEls.manualNote?.value || '',
      };
    }

    function readRegulatedPrintOverridesFromInputs() {
      return {
        buyer_name: printEls.regulatedBuyerName?.value || '',
        buyer_inn: printEls.regulatedBuyerInn?.value || '',
        buyer_kpp: printEls.regulatedBuyerKpp?.value || '',
        buyer_address: printEls.regulatedBuyerAddress?.value || '',
        basis: printEls.regulatedBasis?.value || '',
        transport_details: printEls.regulatedTransportDetails?.value || '',
        buyer_position: printEls.regulatedBuyerPosition?.value || '',
        buyer_signer: printEls.regulatedBuyerSigner?.value || '',
        seller_position: printEls.regulatedSellerPosition?.value || '',
        seller_signer: printEls.regulatedSellerSigner?.value || '',
      };
    }

    function syncRepairOrderPrintMode() {
      const nextMode = repairOrderPrintState.mode === 'manual' ? 'manual' : 'card';
      repairOrderPrintState.mode = nextMode;
      if (printEls.modeSelect) printEls.modeSelect.value = nextMode;
      if (printEls.manualForm) printEls.manualForm.hidden = !repairOrderPrintIsManualMode();
      if (printEls.regulatedOverridesForm) printEls.regulatedOverridesForm.hidden = repairOrderPrintIsManualMode();
      if (printEls.footerMeta && !repairOrderPrintState.previewByDocument?.[repairOrderPrintActiveDocument()]) {
        printEls.footerMeta.textContent = repairOrderPrintIsManualMode()
          ? 'PDF генерируется из стандартного шаблона AutoStop без карточки CRM.'
          : 'PDF генерируется из шаблона и текущих данных заказ-наряда.';
      }
    }

    function repairOrderPrintRequestPayload(extra = {}) {
      return {
        card_id: repairOrderPrintIsManualMode() ? '' : (state.editingId || state.activeCard?.id || ''),
        source: 'ui',
        repair_order: repairOrderPrintIsManualMode() ? {} : readRepairOrderFromForm(),
        selected_document_ids: repairOrderPrintSelectedIds(),
        active_document_id: repairOrderPrintActiveDocument(),
        selected_template_ids: { ...repairOrderPrintState.selectedTemplateIds },
        print_settings: repairOrderPrintSettingsPayload(),
        document_overrides: {
          ...readRegulatedPrintOverridesFromInputs(),
          ...completionActRequestOverrides(),
        },
        ...(repairOrderPrintIsManualMode() ? {
          document_without_card: repairOrderPrintIsManualMode(),
          manual_document: readManualPrintDocumentFromInputs(),
          request_text: printEls.manualRequestText?.value || '',
        } : {
          document_without_card: repairOrderPrintIsManualMode(),
        }),
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
      invalidateCompletionActEditorOnCardChange();
      const defaultDocumentId = repairOrderPrintIsManualMode() ? 'invoice' : 'repair_order';
      const previousSelected = preserveSelection ? repairOrderPrintSelectedIds() : [defaultDocumentId];
      const previousActive = preserveSelection ? repairOrderPrintActiveDocument() : defaultDocumentId;
      const previousTemplates = preserveSelection ? { ...repairOrderPrintState.selectedTemplateIds } : {};
      repairOrderPrintState.workspace = data || { documents: [], templates: {}, settings: {}, printers: [] };
      const docs = repairOrderPrintWorkspaceDocuments();
      const selected = normalizeRepairOrderPrintSelectedIds(
        preserveSelection ? previousSelected : [defaultDocumentId]
      );
      repairOrderPrintState.selectedDocumentIds = selected;
      repairOrderPrintState.activeDocumentId = selected.includes(previousActive) ? previousActive : (selected[0] || docs[0]?.id || defaultDocumentId);
      repairOrderPrintState.selectedTemplateIds = {};
      docs.forEach((item) => {
        repairOrderPrintState.selectedTemplateIds[item.id] = previousTemplates[item.id] || item.selected_template_id || '';
      });
      const settings = repairOrderPrintState.workspace?.settings || {};
      const profile = settings.service_profile || {};
      if (printEls.printerSelect) {
        printEls.printerSelect.innerHTML = (data?.printers || []).map((printer) => '<option value="' + escapeHtml(printer.name) + '"' + (printer.is_default ? ' selected' : '') + '>' + escapeHtml(printer.label || printer.name) + '</option>').join('') || '<option value="">PDF export only</option>';
      }
      if (printEls.copies) printEls.copies.value = String(settings.copies ?? 1);
      if (printEls.paperSize) printEls.paperSize.value = settings.paper_size || 'A4';
      if (printEls.orientation) printEls.orientation.value = settings.orientation || 'portrait';
      if (printEls.profileCompanyName) printEls.profileCompanyName.value = profile.company_name || '';
      if (printEls.profileLegalName) printEls.profileLegalName.value = profile.legal_name || '';
      if (printEls.profileAddress) printEls.profileAddress.value = profile.address || '';
      if (printEls.profilePhone) printEls.profilePhone.value = profile.phone || '';
      if (printEls.profileReceptionPhone) printEls.profileReceptionPhone.value = profile.reception_phone || '';
      if (printEls.profileSparePartsPhone) printEls.profileSparePartsPhone.value = profile.spare_parts_phone || '';
      if (printEls.profileEmail) printEls.profileEmail.value = profile.email || '';
      if (printEls.profileWebsite) printEls.profileWebsite.value = profile.website || '';
      if (printEls.profileWorkHours) printEls.profileWorkHours.value = profile.work_hours || '';
      if (printEls.profileInn) printEls.profileInn.value = profile.inn || '';
      if (printEls.profileKpp) printEls.profileKpp.value = profile.kpp || '';
      if (printEls.profileOgrn) printEls.profileOgrn.value = profile.ogrn || '';
      if (printEls.profileBankName) printEls.profileBankName.value = profile.bank_name || '';
      if (printEls.profileBik) printEls.profileBik.value = profile.bik || '';
      if (printEls.profileSettlementAccount) printEls.profileSettlementAccount.value = profile.settlement_account || '';
      if (printEls.profileCorrespondentAccount) printEls.profileCorrespondentAccount.value = profile.correspondent_account || '';
      if (printEls.profileSignerPosition) printEls.profileSignerPosition.value = profile.signer_position || '';
      if (printEls.profileSignerName) printEls.profileSignerName.value = profile.signer_name || '';
      if (printEls.profileTaxLabel) printEls.profileTaxLabel.value = profile.tax_label || '';
      if (printEls.profilePaymentPurpose) printEls.profilePaymentPurpose.value = profile.payment_purpose || '';
      renderRepairOrderPrintDocuments();
      renderRepairOrderPrintTemplateSelect();
      renderPrintTemplateDocumentTypeOptions();
      syncRepairOrderPrintPrinterState();
      syncRepairOrderPrintMode();
    }

    function renderRepairOrderPrintDocuments() {
      const docs = repairOrderPrintWorkspaceDocuments();
      const activeDoc = repairOrderPrintDocumentMap()[repairOrderPrintActiveDocument()] || docs[0] || null;
      printEls.documents.innerHTML = docs.length ? docs.map((item) => {
        const isActive = item.id === repairOrderPrintActiveDocument();
        const activeClass = isActive ? ' is-active' : '';
        const selectButton = '<button class="repair-order-print-doc' + activeClass + '" data-print-document="' + escapeHtml(item.id) + '" type="button" role="tab" aria-selected="' + (isActive ? 'true' : 'false') + '">' +
          '<div class="repair-order-print-doc__meta"><div class="repair-order-print-doc__state" aria-hidden="true"></div><div class="repair-order-print-doc__title">' + escapeHtml(item.label) + '</div></div></button>';
        if (item.id !== 'completion_act' || repairOrderPrintState.mode !== 'card') return selectButton;
        return '<div class="repair-order-print-doc-row">' + selectButton +
          '<button class="repair-order-print-doc__editor" data-completion-act-editor-open type="button" title="Редактировать акт выполненных работ" aria-label="Редактировать акт выполненных работ">⚙</button></div>';
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
      const activeDocument = repairOrderPrintDocumentMap()[activeId] || null;
      const templates = repairOrderPrintTemplatesFor(activeId);
      const selectedTemplateId = repairOrderPrintSelectedTemplateId(activeId);
      const templateLocked = Boolean(activeDocument?.template_locked);
      if (printEls.templateEditorButton) {
        printEls.templateEditorButton.hidden = templateLocked;
        printEls.templateEditorButton.disabled = templateLocked;
      }
      if (!printEls.templateSelect) return;
      printEls.templateSelect.hidden = templateLocked;
      printEls.templateSelect.disabled = templateLocked;
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

    const REPAIR_ORDER_PRINT_PREVIEW_VIEWPORTS = {
      portrait: { width: 920, height: 1180 },
      landscape: { width: 1180, height: 860 },
    };

    function repairOrderPrintPreviewViewport() {
      const preview = repairOrderPrintCurrentPreview();
      const page = preview?.pages?.[repairOrderPrintCurrentPageIndex()] || null;
      const pageHtml = String(page?.html || '');
      return pageHtml.includes('regulated-page--landscape')
        ? REPAIR_ORDER_PRINT_PREVIEW_VIEWPORTS.landscape
        : REPAIR_ORDER_PRINT_PREVIEW_VIEWPORTS.portrait;
    }

    function repairOrderPrintScale() {
      const viewport = repairOrderPrintPreviewViewport();
      if (repairOrderPrintState.zoomMode === 'fit') {
        const availableWidth = Math.max(320, (printEls.previewWrap?.clientWidth ?? viewport.width) - 24);
        return Math.max(0.3, Math.min(1, availableWidth / viewport.width));
      }
      return Math.max(0.4, Math.min(2, printFiniteNumber(repairOrderPrintState.zoom, 1)));
    }

    function applyRepairOrderPrintZoom() {
      const viewport = repairOrderPrintPreviewViewport();
      const scale = repairOrderPrintScale();
      if (!printEls.previewFrame) return;
      if (printEls.previewStage) {
        printEls.previewStage.style.width = (viewport.width * scale) + 'px';
        printEls.previewStage.style.height = (viewport.height * scale) + 'px';
      }
      printEls.previewFrame.style.width = viewport.width + 'px';
      printEls.previewFrame.style.height = viewport.height + 'px';
      printEls.previewFrame.style.transform = 'scale(' + scale + ')';
    }

    function renderRepairOrderPrintPreview() {
      const docs = repairOrderPrintWorkspaceDocuments();
      const activeId = repairOrderPrintActiveDocument();
      const activeDoc = docs.find((item) => item.id === activeId) || null;
      const preview = repairOrderPrintCurrentPreview();
      const pageIndex = repairOrderPrintCurrentPageIndex();
      const page = preview?.pages?.[pageIndex] || null;
      printEls.activeLabel.textContent = activeDoc ? ('Предпросмотр · ' + activeDoc.label) : 'Предпросмотр';
      const previewPageCount = Math.max(1, printFiniteNumber(preview?.page_count ?? preview?.pages?.length ?? 1, 1));
      printEls.pageMeta.textContent = preview ? ('Страница ' + (pageIndex + 1) + ' / ' + previewPageCount) : 'Страница 1 / 1';
      printEls.prevPageButton.disabled = !preview || pageIndex <= 0;
      printEls.nextPageButton.disabled = !preview || pageIndex >= Math.max(0, previewPageCount - 1);
      const warnings = [];
      if (Array.isArray(preview?.warnings)) warnings.push(...preview.warnings);
      if (Array.isArray(preview?.missing_fields) && preview.missing_fields.length) warnings.push('Проверьте поля: ' + preview.missing_fields.join(', '));
      printEls.warnings.textContent = warnings.join(' · ');
      printEls.previewFrame.srcdoc = page?.html || '<!doctype html><html lang="ru"><body style="font-family: Segoe UI, sans-serif; padding: 32px; color: #444">Выберите документ для предпросмотра.</body></html>';
      window.setTimeout(repairOrderPrintResetPreviewScroll, 0);
      printEls.footerMeta.textContent = preview ? ('Документ: ' + (activeDoc?.label || activeId) + '. Страниц: ' + previewPageCount + '.') : 'PDF генерируется из шаблона и текущих данных заказ-наряда.';
      applyRepairOrderPrintZoom();
    }

    async function refreshRepairOrderPrintPreview(extra = {}, options = {}) {
      if (!repairOrderPrintState.workspace) return null;
      const requestToken = ++repairOrderPrintState.previewToken;
      try {
        const data = await api('/api/preview_repair_order_print_documents', {
          method: 'POST',
          body: repairOrderPrintRequestPayload(extra),
        });
        if (requestToken !== repairOrderPrintState.previewToken) return null;
        repairOrderPrintState.previewByDocument = Object.fromEntries((data?.documents || []).map((item) => [item.id, item]));
        renderRepairOrderPrintDocuments();
        renderRepairOrderPrintTemplateSelect();
        renderRepairOrderPrintPreview();
        return data;
      } catch (error) {
        if (requestToken !== repairOrderPrintState.previewToken) return null;
        setStatus(error.message, true);
        if (options?.throwOnError) throw error;
        return null;
      }
    }

    function cancelPendingRepairOrderPrintPreview() {
      if (manualPrintPreviewTimer) window.clearTimeout(manualPrintPreviewTimer);
      if (regulatedPrintPreviewTimer) window.clearTimeout(regulatedPrintPreviewTimer);
      manualPrintPreviewTimer = null;
      regulatedPrintPreviewTimer = null;
      cancelPendingCompletionActPreview();
    }

    async function loadRepairOrderPrintWorkspace({ openModal = false, preserveSelection = false } = {}) {
      repairOrderPrintState.mode = 'card';
      syncRepairOrderPrintMode();
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

    async function openManualDocumentPrintWorkspace() {
      try {
        repairOrderPrintState.mode = 'manual';
        if (!repairOrderPrintState.manualDocument) {
          repairOrderPrintState.manualDocument = blankManualPrintDocument();
          applyManualPrintDocumentToInputs(repairOrderPrintState.manualDocument);
        }
        syncRepairOrderPrintMode();
        const data = await api('/api/get_repair_order_print_workspace', {
          method: 'POST',
          body: repairOrderPrintRequestPayload({
            document_without_card: true,
            selected_document_ids: ['invoice'],
            active_document_id: 'invoice',
          }),
        });
        applyRepairOrderPrintWorkspace(data, { preserveSelection: Boolean(repairOrderPrintState.workspace) });
        repairOrderPrintState.selectedDocumentIds = normalizeRepairOrderPrintSelectedIds(['invoice']);
        repairOrderPrintState.activeDocumentId = 'invoice';
        renderRepairOrderPrintDocuments();
        renderRepairOrderPrintTemplateSelect();
        printEls.modal.classList.add('is-open');
        await refreshRepairOrderPrintPreview({
          document_without_card: true,
          selected_document_ids: repairOrderPrintState.selectedDocumentIds,
          active_document_id: repairOrderPrintState.activeDocumentId,
        });
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function openRepairOrderPrintWorkspace() {
      try {
        await loadRepairOrderPrintWorkspace({ openModal: true, preserveSelection: Boolean(repairOrderPrintState.workspace) });
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    function closeRepairOrderPrintWorkspace() {
      if (
        printEls.completionActModal?.classList.contains('is-open') &&
        !closeCompletionActEditor()
      ) return;
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
      if (printEls.inspectionSheetAutofillButton) {
        printEls.inspectionSheetAutofillButton.disabled = repairOrderPrintIsManualMode();
        printEls.inspectionSheetAutofillButton.title = repairOrderPrintIsManualMode()
          ? 'Автозаполнение доступно только для карточки CRM.'
          : '';
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

    function blankCompletionActParty() {
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
      return { generation: current.generation, cardId };
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
      repairOrderPrintState.completionAct.draft = {
        exists: Boolean(data?.draft?.exists),
        version: Math.max(0, printFiniteNumber(data?.draft?.version, 0)),
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
        const parts = [draft.exists ? ('Сохранён черновик · версия ' + String(draft.version)) : 'Используются актуальные данные CRM'];
        if (draft.updated_at) parts.push('обновлён ' + draft.updated_at);
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
      if (!dataActive) window.setTimeout(applyCompletionActPreviewScale, 0);
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
      window.setTimeout(applyCompletionActPreviewScale, 0);
    }

    async function refreshCompletionActPreview(options = {}) {
      const session = options?.session || completionActEditorSessionSnapshot();
      if (!repairOrderPrintState.workspace || !completionActEditorSessionIsCurrent(session)) return null;
      const token = ++repairOrderPrintState.completionAct.previewToken;
      const form = readCompletionActFormFromInputs();
      repairOrderPrintState.completionAct.form = cloneCompletionActValue(form);
      try {
        const data = await api('/api/preview_repair_order_print_documents', {
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
      completionActPreviewTimer = window.setTimeout(() => {
        completionActPreviewTimer = null;
        refreshCompletionActPreview().catch(() => {});
      }, 320);
    }

    async function openCompletionActEditor(returnFocus = null) {
      const cardId = await requireRepairOrderCardId();
      if (!cardId || cardId !== completionActActiveCardId()) return;
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
        const data = await api('/api/get_completion_act_form', {
          method: 'POST',
          body: completionActEndpointPayload({}, session.cardId),
        });
        if (!completionActEditorSessionIsCurrent(session)) return;
        if (!applyCompletionActResponse(data, { session })) return;
        await refreshCompletionActPreview({ session });
        if (!completionActEditorSessionIsCurrent(session)) return;
        printEls.completionActForm?.querySelector('[data-completion-act-field]')?.focus();
      } catch (error) {
        if (!completionActEditorSessionIsCurrent(session)) return;
        if (printEls.completionActMeta) printEls.completionActMeta.textContent = error.message || 'Не удалось загрузить акт.';
        setStatus(error.message, true);
      }
    }

    function closeCompletionActEditor() {
      const session = completionActEditorSessionSnapshot();
      if (!completionActEditorSessionIsCurrent(session)) return true;
      if (repairOrderPrintState.completionAct.dirty && !window.confirm('Закрыть редактор без сохранения? Несохранённые изменения будут отменены.')) return false;
      const returnFocus = repairOrderPrintState.completionAct.returnFocus;
      invalidateCompletionActEditorSession({ hideModal: true });
      const restoreEditorTriggerFocus = () => {
        const target = returnFocus?.isConnected
          ? returnFocus
          : printEls.documents?.querySelector('[data-completion-act-editor-open]');
        target?.focus();
      };
      refreshRepairOrderPrintPreview({ selected_document_ids: ['completion_act'], active_document_id: 'completion_act' })
        .catch(() => {})
        .finally(() => window.setTimeout(restoreEditorTriggerFocus, 0));
      window.setTimeout(restoreEditorTriggerFocus, 0);
      return true;
    }

    async function saveCompletionActDraft() {
      const session = completionActEditorSessionSnapshot();
      if (!completionActEditorSessionIsCurrent(session)) return;
      if (printEls.completionActSaveButton) printEls.completionActSaveButton.disabled = true;
      const form = readCompletionActFormFromInputs();
      const editRevision = repairOrderPrintState.completionAct.editRevision;
      try {
        const data = await api('/api/save_completion_act_form', {
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
        await refreshCompletionActPreview({ session });
        if (!completionActEditorSessionIsCurrent(session)) return;
        setStatus(
          hasNewerEdits
            ? 'Черновик сохранён; более новые изменения остаются несохранёнными.'
            : 'Черновик акта сохранён.',
          false
        );
      } catch (error) {
        if (!completionActEditorSessionIsCurrent(session)) return;
        if (printEls.completionActFooterMeta) printEls.completionActFooterMeta.textContent = error.message || 'Не удалось сохранить черновик.';
        setStatus(error.message, true);
      } finally {
        if (completionActEditorSessionIsCurrent(session) && printEls.completionActSaveButton) {
          printEls.completionActSaveButton.disabled = false;
        }
      }
    }

    async function resetCompletionActDraft() {
      if (!window.confirm('Сбросить ручные изменения акта и заново взять данные из CRM?')) return;
      const session = completionActEditorSessionSnapshot();
      if (!completionActEditorSessionIsCurrent(session)) return;
      if (printEls.completionActResetButton) printEls.completionActResetButton.disabled = true;
      const editRevision = repairOrderPrintState.completionAct.editRevision;
      try {
        const data = await api('/api/reset_completion_act_form', {
          method: 'POST',
          body: completionActEndpointPayload(
            {
              expected_version: repairOrderPrintState.completionAct.draft.version || 0,
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
        await refreshCompletionActPreview({ session });
        if (!completionActEditorSessionIsCurrent(session)) return;
        setStatus(
          hasNewerEdits
            ? 'Черновик сброшен; более новые изменения остаются несохранёнными.'
            : 'Акт сброшен к актуальным данным CRM.',
          false
        );
      } catch (error) {
        if (!completionActEditorSessionIsCurrent(session)) return;
        if (printEls.completionActFooterMeta) printEls.completionActFooterMeta.textContent = error.message || 'Не удалось сбросить черновик.';
        setStatus(error.message, true);
      } finally {
        if (completionActEditorSessionIsCurrent(session) && printEls.completionActResetButton) {
          printEls.completionActResetButton.disabled = false;
        }
      }
    }

    async function exportCompletionActPdf() {
      const session = completionActEditorSessionSnapshot();
      if (!completionActEditorSessionIsCurrent(session)) return;
      if (printEls.completionActExportButton) printEls.completionActExportButton.disabled = true;
      try {
        cancelPendingCompletionActPreview();
        const form = readCompletionActFormFromInputs();
        const editRevision = repairOrderPrintState.completionAct.editRevision;
        const preview = await refreshCompletionActPreview({ requireCurrent: true, session });
        if (!preview) throw new Error('Не удалось обновить акт перед созданием PDF.');
        if (repairOrderPrintState.completionAct.editRevision !== editRevision) {
          throw new Error('Данные акта изменились во время подготовки PDF. Повторите действие.');
        }
        const data = await api('/api/export_repair_order_print_pdf', {
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
        if (!completionActEditorSessionIsCurrent(session)) return;
        if (printEls.completionActFooterMeta) printEls.completionActFooterMeta.textContent = error.message || 'Не удалось подготовить PDF.';
        setStatus(error.message, true);
      } finally {
        if (completionActEditorSessionIsCurrent(session) && printEls.completionActExportButton) {
          printEls.completionActExportButton.disabled = false;
        }
      }
    }

    async function printCompletionAct() {
      const session = completionActEditorSessionSnapshot();
      if (!completionActEditorSessionIsCurrent(session)) return;
      if (printEls.completionActPrintButton) printEls.completionActPrintButton.disabled = true;
      try {
        cancelPendingCompletionActPreview();
        const editRevision = repairOrderPrintState.completionAct.editRevision;
        const preview = await refreshCompletionActPreview({ requireCurrent: true, session });
        if (!preview) throw new Error('Не удалось обновить акт перед печатью.');
        if (!completionActEditorSessionIsCurrent(session)) return;
        if (repairOrderPrintState.completionAct.editRevision !== editRevision) {
          throw new Error('Данные акта изменились во время подготовки печати. Повторите действие.');
        }
        await runCompletionActBrowserPrint(preview);
        setStatus('Открыто системное окно печати акта.', false);
      } catch (error) {
        if (!completionActEditorSessionIsCurrent(session)) return;
        if (printEls.completionActFooterMeta) printEls.completionActFooterMeta.textContent = error.message || 'Не удалось открыть печать.';
        setStatus(error.message, true);
      } finally {
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

    function runBrowserPrintHtml(printableHtml) {
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
        frame.setAttribute('sandbox', 'allow-same-origin allow-modals');
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

    function runRepairOrderBrowserPrint() {
      const selectedIds = repairOrderPrintSelectedIds();
      if (selectedIds.length === 1 && selectedIds[0] === 'completion_act') {
        const preview = repairOrderPrintState.previewByDocument?.completion_act;
        const pages = Array.isArray(preview?.pages) ? preview.pages : [];
        return runBrowserPrintHtml(completionActCombinedHtml(pages));
      }
      return runBrowserPrintHtml(repairOrderPrintCombinedHtml());
    }

    function completionActCombinedHtml(pages) {
      const parser = new DOMParser();
      const parsedPages = (Array.isArray(pages) ? pages : []).map((page) => {
        const documentNode = parser.parseFromString(String(page?.html || ''), 'text/html');
        const shell = documentNode.querySelector('.document-shell');
        const bodyFragment = shell?.outerHTML || documentNode.body?.innerHTML || '';
        const headFragments = Array.from(documentNode.head?.querySelectorAll('style, link[rel="stylesheet"]') || [])
          .map((node) => node.outerHTML)
          .filter(Boolean);
        return { bodyFragment, headFragments };
      }).filter((page) => page.bodyFragment.trim());
      const headFragments = Array.from(new Set(parsedPages.flatMap((page) => page.headFragments))).join('');
      const body = parsedPages.length
        ? parsedPages.map((page) => page.bodyFragment).join('')
        : '<main style="font-family: Segoe UI, sans-serif; padding: 32px; color: #444">Нет данных акта для печати.</main>';
      return '<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Акт выполненных работ</title>'
        + headFragments
        + '<style>.document-shell + .document-shell{break-before:page;page-break-before:always}</style>'
        + '</head><body>' + body + '</body></html>';
    }

    function runCompletionActBrowserPrint(preview = null) {
      const currentPreview = preview && typeof preview === 'object'
        ? preview
        : repairOrderPrintState.completionAct.preview;
      const pages = Array.isArray(currentPreview?.pages)
        ? currentPreview.pages
        : [];
      return runBrowserPrintHtml(completionActCombinedHtml(pages));
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
        cancelPendingRepairOrderPrintPreview();
        const refreshed = await refreshRepairOrderPrintPreview({}, { throwOnError: true });
        const selectedIds = repairOrderPrintSelectedIds();
        if (!refreshed || selectedIds.some((documentId) => !repairOrderPrintState.previewByDocument?.[documentId])) {
          throw new Error('Не удалось обновить документ перед печатью.');
        }
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
      const editorButton = target.closest('[data-completion-act-editor-open]');
      if (editorButton) {
        openCompletionActEditor(editorButton);
        return;
      }
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

    function scheduleManualPrintPreviewRefresh() {
      if (!repairOrderPrintIsManualMode() || !repairOrderPrintState.workspace) return;
      repairOrderPrintState.manualDocument = readManualPrintDocumentFromInputs();
      if (manualPrintPreviewTimer) {
        window.clearTimeout(manualPrintPreviewTimer);
      }
      manualPrintPreviewTimer = window.setTimeout(() => {
        manualPrintPreviewTimer = null;
        refreshRepairOrderPrintPreview();
      }, 320);
    }

    function scheduleRegulatedPrintPreviewRefresh() {
      if (repairOrderPrintIsManualMode() || !repairOrderPrintState.workspace) return;
      if (regulatedPrintPreviewTimer) {
        window.clearTimeout(regulatedPrintPreviewTimer);
      }
      regulatedPrintPreviewTimer = window.setTimeout(() => {
        regulatedPrintPreviewTimer = null;
        refreshRepairOrderPrintPreview();
      }, 320);
    }

    function handleRepairOrderPrintModeChange() {
      const nextMode = printEls.modeSelect?.value === 'manual' ? 'manual' : 'card';
      if (nextMode === 'manual') {
        openManualDocumentPrintWorkspace();
        return;
      }
      repairOrderPrintState.mode = 'card';
      syncRepairOrderPrintMode();
      loadRepairOrderPrintWorkspace({ openModal: true, preserveSelection: false }).catch((error) => {
        setStatus(error.message, true);
      });
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
      const docs = repairOrderPrintWorkspaceDocuments().filter((item) => item.id !== 'completion_act');
      const requested = repairOrderPrintState.templateEditor.documentType || repairOrderPrintActiveDocument() || 'repair_order';
      const current = requested === 'completion_act' ? (docs[0]?.id || 'repair_order') : requested;
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
      const activeDocument = repairOrderPrintActiveDocument() || 'repair_order';
      repairOrderPrintState.templateEditor.documentType = activeDocument === 'completion_act' ? 'repair_order' : activeDocument;
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
          printEls.templatePreviewMeta.textContent = 'Страниц в предпросмотре: ' + Math.max(1, printFiniteNumber(documentPreview.page_count ?? documentPreview.pages?.length ?? 1, 1));
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
        if (file.size > PRINT_TEMPLATE_UPLOAD_MAX_SIZE_BYTES) {
          setStatus('Файл шаблона слишком большой. Максимум: 256 КБ.', true);
          return;
        }
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

    function handleCompletionActModalClick(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      if (target === printEls.completionActModal) {
        closeCompletionActEditor();
        return;
      }
      const sectionButton = target.closest('[data-completion-act-section]');
      if (sectionButton) setCompletionActSection(sectionButton.getAttribute('data-completion-act-section') || 'document');
    }

    function handleCompletionActKeydown(event) {
      if (event.key !== 'Escape' || !printEls.completionActModal?.classList.contains('is-open')) return;
      event.preventDefault();
      event.stopPropagation();
      closeCompletionActEditor();
    }

    function handleCompletionActBeforeUnload(event) {
      if (!repairOrderPrintState.completionAct.dirty) return;
      event.preventDefault();
      event.returnValue = '';
    }

    function handleInspectionSheetTableRowsClick(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const removeButton = target.closest('[data-inspection-row-remove]');
      if (!removeButton) return;
      const row = removeButton.closest('[data-inspection-row-kind]');
      if (!row) return;
      const kind = row.getAttribute('data-inspection-row-kind') || '';
      const index = printFiniteNumber(row.getAttribute('data-inspection-row-index'), -1);
      if (!kind || index < 0) return;
      removeInspectionSheetTableRow(kind, index);
    }

    printRepairOrderDraft = function() { return openRepairOrderPrintWorkspace(); };

    if (printEls.documents) printEls.documents.addEventListener('click', handleRepairOrderPrintDocumentsClick);
    if (printEls.documentsAction) printEls.documentsAction.addEventListener('click', handleRepairOrderPrintDocumentsActionClick);
    if (printEls.templateSelect) printEls.templateSelect.addEventListener('change', handleRepairOrderPrintTemplateSelectChange);
    if (printEls.modeSelect) printEls.modeSelect.addEventListener('change', handleRepairOrderPrintModeChange);
    if (printEls.manualForm) printEls.manualForm.addEventListener('input', scheduleManualPrintPreviewRefresh);
    if (printEls.manualForm) printEls.manualForm.addEventListener('change', scheduleManualPrintPreviewRefresh);
    if (printEls.regulatedOverridesForm) printEls.regulatedOverridesForm.addEventListener('input', scheduleRegulatedPrintPreviewRefresh);
    if (printEls.regulatedOverridesForm) printEls.regulatedOverridesForm.addEventListener('change', scheduleRegulatedPrintPreviewRefresh);
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
    if (printEls.completionActCloseX) printEls.completionActCloseX.addEventListener('click', closeCompletionActEditor);
    if (printEls.completionActModal) printEls.completionActModal.addEventListener('click', handleCompletionActModalClick);
    if (printEls.completionActForm) printEls.completionActForm.addEventListener('submit', (event) => event.preventDefault());
    if (printEls.completionActForm) printEls.completionActForm.addEventListener('input', handleCompletionActFormInput);
    if (printEls.completionActForm) printEls.completionActForm.addEventListener('change', handleCompletionActFormInput);
    if (printEls.completionActItemRows) printEls.completionActItemRows.addEventListener('click', handleCompletionActItemsClick);
    if (printEls.completionActAddItemButton) printEls.completionActAddItemButton.addEventListener('click', appendCompletionActItem);
    if (printEls.completionActSaveButton) printEls.completionActSaveButton.addEventListener('click', saveCompletionActDraft);
    if (printEls.completionActResetButton) printEls.completionActResetButton.addEventListener('click', resetCompletionActDraft);
    if (printEls.completionActExportButton) printEls.completionActExportButton.addEventListener('click', exportCompletionActPdf);
    if (printEls.completionActPrintButton) printEls.completionActPrintButton.addEventListener('click', printCompletionAct);
    if (printEls.completionActPrevPageButton) printEls.completionActPrevPageButton.addEventListener('click', () => { repairOrderPrintState.completionAct.pageIndex -= 1; renderCompletionActPreview(); });
    if (printEls.completionActNextPageButton) printEls.completionActNextPageButton.addEventListener('click', () => { repairOrderPrintState.completionAct.pageIndex += 1; renderCompletionActPreview(); });
    if (printEls.completionActMobileDataButton) printEls.completionActMobileDataButton.addEventListener('click', () => setCompletionActMobileView('data'));
    if (printEls.completionActMobilePreviewButton) printEls.completionActMobilePreviewButton.addEventListener('click', () => setCompletionActMobileView('preview'));
    window.addEventListener('resize', applyCompletionActPreviewScale);
    window.addEventListener('beforeunload', handleCompletionActBeforeUnload);
    document.addEventListener('keydown', handleCompletionActKeydown, true);
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
