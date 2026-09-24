"""Embedded styles for the printing interface."""

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
