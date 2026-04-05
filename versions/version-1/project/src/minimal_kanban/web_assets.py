BOARD_WEB_APP_HTML = "".join(
    [
        """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>КАНБАН</title>
  <style>
    :root {
      --bg: #171d18;
      --bg-panel: #222a24;
      --bg-panel-2: #2b342d;
      --card: #d0c6ab;
      --card-2: #bfb393;
      --card-text: #161812;
      --line: #5e685b;
      --line-soft: #3d463d;
      --accent: #a7b284;
      --accent-soft: rgba(167, 178, 132, 0.12);
      --warn: #d4af37;
      --danger: #cf5b4b;
      --ok: #72b66b;
      --text: #f2f0e6;
      --text-soft: #c8c6bb;
      --paper-line: #847f6e;
      --column-tint: rgba(39, 48, 42, 0.94);
      --column-head: rgba(92, 105, 83, 0.24);
      --column-edge: #717a6d;
      --column-empty: rgba(167, 178, 132, 0.06);
      --mono: "Cascadia Mono", "Consolas", "Courier New", monospace;
      --sans: "Segoe UI", "Tahoma", sans-serif;
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body {
      margin: 0;
      font-family: var(--sans);
      background:
        linear-gradient(180deg, rgba(255,255,255,0.02), transparent 40%),
        radial-gradient(circle at top right, rgba(165,177,127,0.08), transparent 32%),
        var(--bg);
      color: var(--text);
      overflow: hidden;
    }
    button, input, textarea, select { font: inherit; }
    .shell { display: grid; grid-template-rows: auto auto minmax(0, 1fr); height: 100%; min-height: 0; overflow: hidden; }
    .status-shell {
      min-height: 0;
      padding: 12px 12px 0;
      position: relative;
      z-index: 2;
    }
    .status-shell .message {
      display: inline-flex;
      align-items: center;
      width: max-content;
      max-width: 100%;
    }
    .topbar {
      border-bottom: 1px solid var(--line);
      background: rgba(0, 0, 0, 0.18);
      padding: 14px 18px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      position: relative;
      z-index: 3;
    }
    .topbar__left { display: flex; align-items: center; gap: 12px; min-width: 0; }
    .brand { display: flex; flex-direction: column; gap: 4px; }
    .brand__title {
      font-family: var(--mono);
      font-size: 20px;
      letter-spacing: 0.14em;
      font-weight: 700;
    }
    .brand__sub {
      color: var(--text-soft);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      font-family: var(--mono);
    }
    .topbar__actions {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    .btn, .pill {
      border: 1px solid var(--line);
      background: var(--bg-panel);
      color: var(--text);
      padding: 9px 12px;
      cursor: pointer;
      text-transform: uppercase;
      font-family: var(--mono);
      font-size: 12px;
      letter-spacing: 0.08em;
      text-decoration: none;
    }
    .btn:hover { background: var(--bg-panel-2); }
    .btn--accent { border-color: #8c976d; color: #edf0df; }
    .btn--danger { border-color: var(--danger); color: #ffd2c9; }
    .btn--ghost {
      color: var(--text-soft);
      border-color: rgba(255,255,255,0.14);
      background: rgba(0,0,0,0.12);
    }
    .btn--ghost:hover {
      color: var(--text);
      border-color: var(--line);
    }
    .gear-button {
      width: 48px;
      height: 48px;
      padding: 0 12px 12px;
      display: grid;
      place-items: center;
      flex: 0 0 auto;
      border-color: rgba(165, 176, 122, 0.85);
      background:
        linear-gradient(180deg, rgba(255,255,255,0.08), transparent 24%),
        rgba(18, 24, 19, 0.9);
      box-shadow:
        inset 0 1px 0 rgba(255,255,255,0.08),
        0 0 0 1px rgba(0,0,0,0.28);
    }
    .gear-button:hover { border-color: var(--accent); }
    .gear-button svg {
      width: 22px;
      height: 22px;
      stroke: currentColor;
      stroke-width: 1.8;
      fill: none;
    }
    .sticky-dock {
      position: fixed;
      left: 28px;
      bottom: 34px;
      z-index: 12;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .sticky-dock__button {
      border: 1px solid rgba(165, 176, 122, 0.75);
      background:
        linear-gradient(180deg, rgba(255,255,255,0.08), transparent 24%),
        rgba(18, 24, 19, 0.94);
      color: var(--text);
      width: 68px;
      height: 68px;
      padding: 0;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      box-shadow:
        inset 0 1px 0 rgba(255,255,255,0.08),
        0 0 0 1px rgba(0,0,0,0.2),
        0 10px 26px rgba(0,0,0,0.28);
    }
    .sticky-dock__button:hover { border-color: var(--accent); transform: translateY(-1px); }
    .sticky-dock__button svg {
      width: 34px;
      height: 34px;
      stroke: currentColor;
      stroke-width: 1.8;
      fill: rgba(214, 194, 117, 0.18);
    }
    .board-scroll {
      overflow: auto;
      padding: 0;
      position: relative;
      z-index: 0;
      min-height: 0;
      cursor: grab;
      overscroll-behavior: contain;
      overflow-anchor: none;
      scrollbar-gutter: stable both-edges;
      user-select: none;
      touch-action: none;
      isolation: isolate;
    }
    .board-scroll.is-panning { cursor: grabbing; }
    .board {
      --board-scale: 1;
      --board-pad-top: 12px;
      --board-pad-x: 18px;
      --board-pad-bottom: 220px;
      --board-gutter-top: 0px;
      --board-gutter-right: 320px;
      --board-gutter-bottom: 260px;
      --board-gutter-left: 0px;
      position: relative;
      display: flex;
      width: max-content;
      min-width: 100%;
      overflow-anchor: none;
      box-sizing: border-box;
      gap: calc(14px * var(--board-scale));
      align-items: flex-start;
      min-height: 100%;
      margin: var(--board-gutter-top) var(--board-gutter-right) var(--board-gutter-bottom) var(--board-gutter-left);
      padding: var(--board-pad-top) var(--board-pad-x) var(--board-pad-bottom);
    }
    .column {
      position: relative;
      z-index: 1;
      width: calc(360px * var(--board-scale));
      min-width: calc(360px * var(--board-scale));
      background:
        linear-gradient(180deg, var(--column-head) 0, var(--column-head) 54px, transparent 54px),
        linear-gradient(180deg, rgba(255,255,255,0.03), transparent 34%),
        var(--column-tint);
      border: 1px solid var(--column-edge);
      box-shadow:
        inset 0 0 0 1px rgba(0, 0, 0, 0.26),
        inset 0 1px 0 rgba(255, 255, 255, 0.04);
      padding: calc(12px * var(--board-scale));
      display: grid;
      grid-template-rows: auto 1fr auto;
      gap: calc(12px * var(--board-scale));
      isolation: isolate;
    }
    .column::before {
      content: "";
      position: absolute;
      inset: 0 auto 0 0;
      width: 4px;
      background: linear-gradient(180deg, rgba(255,255,255,0.14), rgba(0,0,0,0.12));
      opacity: 0.85;
      z-index: 0;
    }
    .column > * {
      position: relative;
      z-index: 1;
    }
    .column.is-drop-target { outline: 1px solid var(--accent); }
    .column__head { display: flex; justify-content: space-between; align-items: center; gap: calc(10px * var(--board-scale)); }
    .column__title {
      font-family: var(--mono);
      font-size: calc(14px * var(--board-scale));
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      text-shadow: 0 1px 0 rgba(0,0,0,0.38);
    }
    .column__count {
      min-width: calc(34px * var(--board-scale));
      text-align: center;
      padding: calc(5px * var(--board-scale)) calc(8px * var(--board-scale));
      border: 1px solid rgba(255,255,255,0.1);
      background: rgba(9, 12, 10, 0.22);
      font-family: var(--mono);
      font-size: calc(12px * var(--board-scale));
    }
    .column__cards { display: flex; flex-direction: column; gap: calc(8px * var(--board-scale)); min-height: calc(80px * var(--board-scale)); }
    .empty {
      border: 1px dashed rgba(255,255,255,0.1);
      background: var(--column-empty);
      padding: calc(14px * var(--board-scale));
      color: var(--text-soft);
      font-size: calc(12px * var(--board-scale));
      line-height: 1.4;
      min-height: calc(72px * var(--board-scale));
      display: grid;
      place-items: center;
      text-align: center;
    }
    .card {
      border: 1px solid var(--paper-line);
      background:
        linear-gradient(180deg, rgba(255,255,255,0.14), transparent 18%),
        linear-gradient(180deg, var(--card) 0, var(--card-2) 100%);
      color: var(--card-text);
      padding: calc(11px * var(--board-scale)) calc(11px * var(--board-scale)) calc(10px * var(--board-scale));
      min-height: calc(172px * var(--board-scale));
      display: grid;
      grid-template-rows: auto auto 1fr auto auto auto;
      gap: calc(6px * var(--board-scale));
      cursor: pointer;
      box-shadow:
        inset 0 4px 0 #8e9570,
        inset 0 0 0 1px rgba(255,255,255,0.08),
        0 1px 0 rgba(0,0,0,0.18);
    }
    .board-scroll.is-panning .card,
    .board-scroll.is-panning .sticky,
    .board-scroll.is-panning .btn,
    .board-scroll.is-panning .gear-button,
    .board-scroll.is-panning .tab-btn,
    .board-scroll.is-panning input,
    .board-scroll.is-panning textarea,
    .board-scroll.is-panning select {
      pointer-events: none;
    }
    .card[data-indicator="yellow"] {
      border-color: rgba(212, 175, 55, 0.88);
      box-shadow:
        inset 0 4px 0 #8e9570,
        inset 0 0 0 1px rgba(255,255,255,0.08),
        0 0 0 1px rgba(212, 175, 55, 0.16),
        0 0 14px rgba(212, 175, 55, 0.12);
    }
    .card[data-indicator="red"] {
      border-color: rgba(207, 91, 75, 0.92);
      box-shadow:
        inset 0 4px 0 #8e9570,
        inset 0 0 0 1px rgba(255,255,255,0.08),
        0 0 0 1px rgba(207, 91, 75, 0.24),
        0 0 18px rgba(207, 91, 75, 0.24);
    }
    @keyframes card-alert-blink {
      0%, 100% {
        border-color: rgba(207, 91, 75, 0.92);
        box-shadow:
          inset 0 4px 0 #8e9570,
          inset 0 0 0 1px rgba(255,255,255,0.08),
          0 0 0 1px rgba(207, 91, 75, 0.28),
          0 0 22px rgba(207, 91, 75, 0.28);
      }
      50% {
        border-color: rgba(207, 91, 75, 0.38);
        box-shadow:
          inset 0 4px 0 #8e9570,
          inset 0 0 0 1px rgba(255,255,255,0.08),
          0 0 0 1px rgba(207, 91, 75, 0.12),
          0 0 8px rgba(207, 91, 75, 0.08);
      }
    }
    @keyframes card-alert-expired-blink {
      0%, 100% {
        border-color: rgba(207, 91, 75, 1);
        box-shadow:
          inset 0 4px 0 #8e9570,
          inset 0 0 0 1px rgba(255,255,255,0.08),
          0 0 0 1px rgba(207, 91, 75, 0.42),
          0 0 28px rgba(207, 91, 75, 0.42),
          0 0 42px rgba(207, 91, 75, 0.24);
      }
      50% {
        border-color: rgba(207, 91, 75, 0.22);
        box-shadow:
          inset 0 4px 0 #8e9570,
          inset 0 0 0 1px rgba(255,255,255,0.08),
          0 0 0 1px rgba(207, 91, 75, 0.08),
          0 0 6px rgba(207, 91, 75, 0.05);
      }
    }
    .card[data-blink="true"][data-indicator="red"] {
      animation: card-alert-blink 1.3s steps(2, end) infinite;
    }
    .card[data-status="expired"][data-blink="true"][data-indicator="red"] {
      animation: card-alert-expired-blink 0.72s steps(2, end) infinite;
    }
    .sticky-layer {
      position: absolute;
      inset: 0;
      pointer-events: none;
      z-index: 3;
    }
    .sticky {
      position: absolute;
      pointer-events: auto;
      width: calc(216px * var(--board-scale));
      min-height: calc(128px * var(--board-scale));
      padding: calc(10px * var(--board-scale)) calc(10px * var(--board-scale)) calc(8px * var(--board-scale));
      display: grid;
      gap: calc(7px * var(--board-scale));
      border: 1px solid rgba(131, 121, 77, 0.72);
      background:
        linear-gradient(180deg, rgba(255,255,255,0.1), transparent 22%),
        linear-gradient(180deg, rgba(245, 227, 141, 0.96), rgba(208, 193, 112, 0.9));
      color: #18170f;
      box-shadow:
        inset 0 1px 0 rgba(255,255,255,0.2),
        0 1px 0 rgba(0,0,0,0.12);
      cursor: grab;
      opacity: 0.9;
    }
    .sticky[data-expired="true"] {
      opacity: 0.5;
    }
    .sticky.is-dragging {
      cursor: grabbing;
      box-shadow:
        inset 0 1px 0 rgba(255,255,255,0.22),
        0 6px 16px rgba(0,0,0,0.25);
    }
    .sticky__head {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      align-items: center;
      font-family: var(--mono);
      font-size: calc(10px * var(--board-scale));
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: rgba(24, 23, 15, 0.72);
    }
    .sticky__pin {
      display: inline-flex;
      align-items: center;
      gap: 4px;
    }
    .sticky__text {
      font-family: var(--mono);
      font-size: calc(12px * var(--board-scale));
      line-height: 1.3;
      white-space: pre-wrap;
      word-break: break-word;
      user-select: text;
    }
    .sticky__meta {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      align-items: center;
      font-family: var(--mono);
      font-size: calc(10px * var(--board-scale));
      letter-spacing: 0.05em;
      text-transform: uppercase;
      color: rgba(24, 23, 15, 0.7);
    }
    .sticky__close {
      border: 1px solid rgba(24, 23, 15, 0.28);
      background: rgba(255,255,255,0.18);
      color: #1b180f;
      width: calc(20px * var(--board-scale));
      height: calc(20px * var(--board-scale));
      padding: 0;
      display: grid;
      place-items: center;
      font-family: var(--mono);
      font-size: calc(10px * var(--board-scale));
      cursor: pointer;
    }
    .sticky__close:hover { background: rgba(255,255,255,0.32); }
    .sticky[data-opacity="low"] {
      filter: saturate(0.92);
    }
    .sticky[data-opacity="mid"] {
      filter: saturate(0.88);
    }
    .sticky[data-opacity="high"] {
      filter: saturate(0.84);
    }
    .card__heading {
      display: flex;
      align-items: baseline;
      gap: calc(5px * var(--board-scale));
      min-width: 0;
      font-family: var(--mono);
      text-transform: uppercase;
    }
    .card__vehicle {
      font-family: var(--mono);
      font-size: calc(13px * var(--board-scale));
      line-height: 1.12;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      color: #4f4b40;
      font-weight: 700;
      flex: 0 1 auto;
      min-width: 0;
      overflow: hidden;
      white-space: nowrap;
      text-overflow: ellipsis;
    }
    .card__slash {
      color: #615d51;
      font-size: calc(13px * var(--board-scale));
      line-height: 1;
      flex: 0 0 auto;
      transform: translateY(-0.03em);
    }
    .card__title {
      font-weight: 800;
      font-size: calc(13px * var(--board-scale));
      line-height: 1.12;
      text-transform: uppercase;
      font-family: var(--mono);
      flex: 1 1 auto;
      min-width: 0;
      overflow: hidden;
      white-space: nowrap;
      text-overflow: ellipsis;
    }
    .card__desc {
      font-size: calc(12px * var(--board-scale));
      line-height: 1.22;
      display: -webkit-box;
      -webkit-line-clamp: 5;
      -webkit-box-orient: vertical;
      overflow: hidden;
      white-space: pre-wrap;
    }
    .card__signal {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: calc(10px * var(--board-scale));
      font-family: var(--mono);
      font-size: calc(11px * var(--board-scale));
      text-transform: uppercase;
      border-top: 1px solid rgba(78, 73, 61, 0.26);
      padding-top: calc(8px * var(--board-scale));
    }
    .card__signal-label { display: inline-flex; align-items: center; gap: calc(7px * var(--board-scale)); color: #544f42; }
    .card__signal-value { font-weight: 700; letter-spacing: 0.04em; }
    .time-readout {
      display: inline-flex;
      align-items: baseline;
      gap: calc(4px * var(--board-scale));
      font-family: var(--mono);
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }
    .time-readout__group {
      display: inline-flex;
      align-items: baseline;
      gap: calc(1px * var(--board-scale));
    }
    .time-readout__num {
      font-size: 1em;
      line-height: 1;
    }
    .time-readout__unit {
      font-size: 0.62em;
      line-height: 1;
      opacity: 0.88;
      transform: translateY(-0.18em);
    }
    .lamp { width: calc(13px * var(--board-scale)); height: calc(13px * var(--board-scale)); border: 1px solid #283126; background: #6f786e; flex: 0 0 auto; }
    .lamp[data-indicator="green"] { background: var(--ok); }
    .lamp[data-indicator="yellow"] { background: var(--warn); }
    .lamp[data-indicator="red"] { background: var(--danger); }
    .card__tags, .tag-list {
      display: flex;
      flex-wrap: wrap;
      gap: calc(5px * var(--board-scale));
      align-content: flex-start;
      align-items: flex-start;
    }
    .tag {
      border: 1px solid #7d7a6c;
      padding: calc(2px * var(--board-scale)) calc(5px * var(--board-scale));
      font-family: var(--mono);
      font-size: calc(10px * var(--board-scale));
      letter-spacing: 0.05em;
      text-transform: uppercase;
      background: rgba(22,24,18,0.08);
      color: inherit;
    }
    .tag--muted, .tag-empty {
      color: var(--text-soft);
      border-style: dashed;
      background: transparent;
    }
    .meta-line {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: calc(10px * var(--board-scale));
      color: #4e493d;
      font-family: var(--mono);
      font-size: calc(11px * var(--board-scale));
      text-transform: uppercase;
    }
    .modal {
      position: fixed;
      inset: 0;
      display: none;
      align-items: center;
      justify-content: center;
      padding: 20px;
      background: rgba(9, 12, 10, 0.72);
      z-index: 10;
    }
    .modal.is-open { display: flex; }
    .dialog {
      width: min(980px, 100%);
      max-height: min(92vh, 900px);
      overflow: auto;
      background: var(--bg-panel);
      border: 1px solid var(--line);
      padding: 16px;
      display: grid;
      gap: 14px;
    }
    .dialog__head, .dialog__foot, .dialog__tabs {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }
""",
        """
    .dialog__title {
      font-family: var(--mono);
      text-transform: uppercase;
      letter-spacing: 0.14em;
      font-size: 14px;
      font-weight: 700;
    }
    .tab-btn {
      border: 1px solid var(--line-soft);
      padding: 8px 10px;
      background: transparent;
      color: var(--text-soft);
      cursor: pointer;
      text-transform: uppercase;
      font-family: var(--mono);
      font-size: 12px;
    }
    .tab-btn.is-active { color: var(--text); border-color: var(--accent); }
    .grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
    .overview-layout { display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(210px, 0.54fr); gap: 10px; align-items: start; }
    .stack { display: grid; gap: 12px; }
    .field { display: grid; gap: 6px; }
    .grid--overview { grid-template-columns: minmax(190px, 0.72fr) minmax(0, 1fr); gap: 10px; }
    .field label {
      font-family: var(--mono);
      font-size: 12px;
      text-transform: uppercase;
      color: var(--text-soft);
      letter-spacing: 0.08em;
    }
    .field--compact { gap: 4px; }
    .field--compact label {
      font-size: 11px;
      letter-spacing: 0.06em;
    }
    input[type="text"], textarea, select, input[type="number"] {
      width: 100%;
      border: 1px solid var(--line);
      background: #151c17;
      color: var(--text);
      padding: 9px 10px;
      resize: vertical;
      min-height: 38px;
    }
    input[type="range"] {
      width: 100%;
      min-height: auto;
      padding: 0;
      border: none;
      background: transparent;
      accent-color: var(--accent);
    }
    .field--compact input[type="text"],
    .field--compact select {
      min-height: 34px;
      padding: 6px 8px;
      font-size: 13px;
    }
    input[type="number"] { text-align: center; }
    textarea { min-height: 192px; }
    .panel-title {
      font-family: var(--mono);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: var(--text-soft);
    }
    .signal-panel { gap: 8px; padding: 8px; }
    .signal-preview {
      border: 1px solid var(--line-soft);
      background: rgba(0,0,0,0.22);
      padding: 6px 8px;
      font-family: var(--mono);
      font-size: 15px;
      line-height: 1.15;
      letter-spacing: 0.04em;
      color: var(--text);
    }
    .signal-preview .time-readout { gap: 8px; }
    .signal-preview .time-readout__unit { font-size: 0.58em; }
    .signal-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 5px;
    }
    .signal-cell {
      display: grid;
      gap: 3px;
    }
    .signal-cell span {
      font-family: var(--mono);
      font-size: 9px;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      color: var(--text-soft);
    }
    .signal-panel input[type="number"] {
      min-height: 30px;
      padding: 5px 6px;
      font-size: 13px;
    }
    .scale-control {
      display: grid;
      gap: 10px;
    }
    .scale-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }
    .scale-head__label {
      font-family: var(--mono);
      font-size: 12px;
      text-transform: uppercase;
      color: var(--text-soft);
      letter-spacing: 0.08em;
    }
    .scale-head__value {
      min-width: 72px;
      padding: 5px 8px;
      border: 1px solid rgba(167, 178, 132, 0.55);
      background:
        linear-gradient(180deg, rgba(255,255,255,0.06), transparent 36%),
        rgba(0, 0, 0, 0.22);
      color: var(--text);
      text-align: center;
      font-family: var(--mono);
      font-size: 13px;
      font-weight: 700;
      letter-spacing: 0.08em;
    }
    .scale-track {
      position: relative;
      padding: 4px 0;
    }
    .scale-track::before {
      content: "";
      position: absolute;
      inset: 50% 0 auto;
      height: 6px;
      transform: translateY(-50%);
      border: 1px solid var(--line-soft);
      background:
        linear-gradient(90deg, rgba(86, 97, 82, 0.9), rgba(167, 178, 132, 0.42));
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.05);
      pointer-events: none;
    }
    .scale-track input[type="range"] {
      position: relative;
      z-index: 1;
      appearance: none;
      -webkit-appearance: none;
      background: transparent;
      margin: 0;
    }
    .scale-track input[type="range"]::-webkit-slider-runnable-track {
      height: 14px;
      background: transparent;
      border: none;
    }
    .scale-track input[type="range"]::-webkit-slider-thumb {
      appearance: none;
      -webkit-appearance: none;
      width: 18px;
      height: 18px;
      margin-top: -2px;
      border: 1px solid #b7c08f;
      background:
        linear-gradient(180deg, rgba(255,255,255,0.18), transparent 35%),
        #30382e;
      box-shadow:
        0 0 0 1px rgba(0,0,0,0.28),
        inset 0 1px 0 rgba(255,255,255,0.08);
      cursor: pointer;
    }
    .scale-track input[type="range"]::-moz-range-track {
      height: 14px;
      background: transparent;
      border: none;
    }
    .scale-track input[type="range"]::-moz-range-thumb {
      width: 18px;
      height: 18px;
      border-radius: 0;
      border: 1px solid #b7c08f;
      background:
        linear-gradient(180deg, rgba(255,255,255,0.18), transparent 35%),
        #30382e;
      box-shadow:
        0 0 0 1px rgba(0,0,0,0.28),
        inset 0 1px 0 rgba(255,255,255,0.08);
      cursor: pointer;
    }
    .tag-entry {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 38px;
      gap: 6px;
      align-items: center;
    }
    .tag-entry input[type="text"] {
      min-height: 34px;
      padding: 6px 8px;
      font-family: var(--sans);
      font-size: 14px;
      letter-spacing: 0.02em;
    }
    .tag-entry .btn {
      min-width: 38px;
      min-height: 34px;
      padding: 0;
      display: grid;
      place-items: center;
    }
    .tag-suggestions {
      display: flex;
      flex-wrap: wrap;
      gap: 5px;
      align-content: flex-start;
    }
    .tag-list .tag {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 28px;
      padding: 4px 7px;
      font-family: var(--sans);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.02em;
    }
    .tag-list .tag--muted {
      justify-content: flex-start;
      min-width: 118px;
    }
    .tag-suggestion {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 28px;
      border: 1px solid var(--line-soft);
      background: rgba(0, 0, 0, 0.16);
      color: var(--text-soft);
      padding: 5px 8px;
      font-family: var(--sans);
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.02em;
      line-height: 1.05;
      cursor: pointer;
    }
    .tag-suggestion:hover,
    .tag-suggestion.is-active {
      color: var(--text);
      border-color: var(--accent);
      background: rgba(201, 180, 118, 0.08);
    }
    .tag-suggestion--danger {
      border-color: rgba(201, 84, 71, 0.48);
      color: #e8b0a7;
    }
    .tag-suggestion--danger:hover,
    .tag-suggestion--danger.is-active {
      border-color: #d15a4c;
      background: rgba(209, 90, 76, 0.12);
      color: #f0c1ba;
    }
    .compact-note {
      font-family: var(--mono);
      font-size: 11px;
      color: var(--text-soft);
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    .signal-panel .compact-note {
      font-size: 10px;
      letter-spacing: 0.03em;
    }
    .subpanel {
      border: 1px solid var(--line-soft);
      padding: 10px;
      display: grid;
      gap: 10px;
      background: rgba(0,0,0,0.14);
    }
    .field--tags {
      gap: 7px;
    }
    .file-row, .archive-row {
      border: 1px solid var(--line-soft);
      background: rgba(0,0,0,0.18);
      padding: 10px;
      display: grid;
      gap: 6px;
    }
    .log-row {
      border-bottom: 1px solid rgba(115, 126, 105, 0.24);
      padding: 6px 2px 7px;
      font-family: var(--mono);
      font-size: 12px;
      line-height: 1.35;
      color: var(--text);
      white-space: pre-wrap;
      word-break: break-word;
    }
    .log-row__meta, .archive-row__meta {
      color: var(--text-soft);
      font-size: 12px;
      font-family: var(--mono);
    }
    .log-row__meta { font-size: 11px; }
    .log-view {
      border: 1px solid var(--line-soft);
      background: rgba(0,0,0,0.12);
      padding: 10px 12px;
      display: grid;
      gap: 0;
    }
    .wall-meta {
      color: var(--text-soft);
      font-family: var(--mono);
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    .wall-view {
      border: 1px solid var(--line-soft);
      background: rgba(0,0,0,0.18);
      color: var(--text);
      padding: 12px;
      margin: 0;
      min-height: 420px;
      max-height: min(72vh, 760px);
      overflow: auto;
      font-family: var(--mono);
      font-size: 12px;
      line-height: 1.45;
      white-space: pre-wrap;
      user-select: text;
    }
    .hidden { display: none !important; }
    .message {
      padding: 10px 12px;
      border: 1px solid var(--line);
      background: rgba(0,0,0,0.18);
      font-size: 13px;
      margin: 0;
    }
    @media (max-width: 900px) {
      .grid { grid-template-columns: 1fr; }
      .overview-layout { grid-template-columns: 1fr; }
      .signal-grid { grid-template-columns: repeat(2, 1fr); }
      .column { width: 336px; min-width: 336px; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header class="topbar">
      <div class="topbar__left">
        <button class="gear-button" id="boardSettingsButton" title="НАСТРОЙКИ ДОСКИ" aria-label="НАСТРОЙКИ ДОСКИ">
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path d="M10.5 3.75h3l.47 2.12c.58.16 1.14.39 1.66.68l1.9-1.2 2.12 2.12-1.2 1.9c.29.52.52 1.08.68 1.66l2.12.47v3l-2.12.47c-.16.58-.39 1.14-.68 1.66l1.2 1.9-2.12 2.12-1.9-1.2c-.52.29-1.08.52-1.66.68l-.47 2.12h-3l-.47-2.12a6.9 6.9 0 0 1-1.66-.68l-1.9 1.2-2.12-2.12 1.2-1.9a6.9 6.9 0 0 1-.68-1.66l-2.12-.47v-3l2.12-.47c.16-.58.39-1.14.68-1.66l-1.2-1.9 2.12-2.12 1.9 1.2c.52-.29 1.08-.52 1.66-.68l.47-2.12Zm1.5 5.25a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5Z"/>
          </svg>
        </button>
        <div class="brand">
          <div class="brand__title">КАНБАН / ПУЛЬТ</div>
          <div class="brand__sub">МИНИМУМ ИНТЕРФЕЙСА · ПОЛНЫЙ ЖУРНАЛ · ХОСТ В СЕТИ</div>
        </div>
      </div>
      <div class="topbar__actions">
        <button class="btn" id="operatorButton">ОПЕРАТОР</button>
        <button class="btn" id="archiveButton">АРХИВ</button>
        <button class="btn btn--ghost" id="gptWallButton">СТЕНА</button>
        <button class="btn" id="columnButton">+ СТОЛБЕЦ</button>
        <button class="btn btn--accent" id="cardButton">+ КАРТОЧКА</button>
      </div>
    </header>
    <div class="board-scroll">
      <div class="message" id="statusLine">СОЕДИНЕНИЕ С ДОСКОЙ...</div>
      <div class="board" id="board"></div>
    </div>
  </div>

  <div class="sticky-dock">
    <button class="sticky-dock__button" id="stickyDockButton" type="button" aria-label="Новый стикер" title="Новый стикер">
      <svg viewBox="0 0 32 32" aria-hidden="true">
        <path d="M8 6h12l4 4v16H8z"></path>
        <path d="M20 6v6h6"></path>
        <path d="M12 16h8"></path>
        <path d="M12 21h6"></path>
      </svg>
    </button>
  </div>

  <div class="modal" id="identityModal">
    <div class="dialog" style="width:min(480px,100%)">
      <div class="dialog__title">КТО РАБОТАЕТ С ДОСКОЙ</div>
      <div class="field">
        <label for="identityInput">ИМЯ ОПЕРАТОРА</label>
        <input id="identityInput" type="text" maxlength="40" placeholder="Например: АНДРЕЙ">
      </div>
      <div class="dialog__foot">
        <div class="log-row__meta">Имя попадёт в журнал всех действий этой сессии.</div>
        <button class="btn btn--accent" id="identitySave">ПРИМЕНИТЬ</button>
      </div>
    </div>
  </div>

  <div class="modal" id="stickyModal">
    <div class="dialog" style="width:min(560px,100%)">
      <div class="dialog__head">
        <div class="dialog__title" id="stickyModalTitle">СТИКЕР</div>
        <button class="btn" data-close="sticky">ЗАКРЫТЬ</button>
      </div>
      <div class="field">
        <label for="stickyText">ТЕКСТ СТИКЕРА</label>
        <textarea id="stickyText" maxlength="1000" placeholder="Короткая заметка без лишнего шума."></textarea>
      </div>
      <div class="signal-grid">
        <div class="signal-cell">
          <span>ДНИ</span>
          <input id="stickyDays" type="number" min="0" max="365" value="0">
        </div>
        <div class="signal-cell">
          <span>ЧАСЫ</span>
          <input id="stickyHours" type="number" min="0" max="23" value="4">
        </div>
      </div>
      <div class="compact-note">Стикер появится на доске, после чего его можно двигать мышью.</div>
      <div class="dialog__foot">
        <button class="btn" data-close="sticky">ОТМЕНА</button>
        <button class="btn btn--accent" id="saveStickyButton">СОХРАНИТЬ</button>
      </div>
    </div>
  </div>

  <div class="modal" id="archiveModal">
    <div class="dialog" style="width:min(720px,100%)">
      <div class="dialog__head">
        <div class="dialog__title">АРХИВ / ПОСЛЕДНИЕ 10</div>
        <button class="btn" data-close="archive">ЗАКРЫТЬ</button>
      </div>
      <div id="archiveList"></div>
    </div>
  </div>

  <div class="modal" id="gptWallModal">
    <div class="dialog" style="width:min(1040px,100%)">
      <div class="dialog__head">
        <div class="dialog__title">СТЕНА / СВЯЗЬ С GPT</div>
        <div style="display:flex; gap:8px; flex-wrap:wrap;">
          <button class="btn btn--ghost" id="gptWallRefresh">ОБНОВИТЬ</button>
          <button class="btn" data-close="wall">ЗАКРЫТЬ</button>
        </div>
      </div>
      <div class="wall-meta" id="gptWallMeta">СЛУЖЕБНЫЙ СЛОЙ ЕЩЁ НЕ ЗАГРУЖЕН.</div>
      <pre class="wall-view" id="gptWallText">ЗАГРУЗКА...</pre>
    </div>
  </div>

  <div class="modal" id="boardSettingsModal">
    <div class="dialog" style="width:min(560px,100%)">
      <div class="dialog__head">
        <div class="dialog__title">НАСТРОЙКИ ДОСКИ</div>
        <button class="btn" data-close="settings">ЗАКРЫТЬ</button>
      </div>
      <div class="field">
        <div class="scale-control">
          <div class="scale-head">
            <label class="scale-head__label" for="boardScaleInput">МАСШТАБ</label>
            <span class="scale-head__value" id="boardScaleValue">100%</span>
          </div>
          <div class="scale-track">
            <input id="boardScaleInput" type="range" min="50" max="150" step="5" value="100">
          </div>
        </div>
      </div>
      <div class="dialog__foot">
        <button class="btn btn--ghost" id="boardScaleReset">СБРОСИТЬ НА 100%</button>
      </div>
    </div>
  </div>

  <div class="modal" id="cardModal">
    <div class="dialog">
      <div class="dialog__head">
        <div class="dialog__title" id="cardModalTitle">КАРТОЧКА</div>
        <button class="btn" data-close="card">ЗАКРЫТЬ</button>
      </div>
      <div class="dialog__tabs">
        <div>
          <button class="tab-btn is-active" data-tab="overview">ОБЗОР</button>
          <button class="tab-btn" data-tab="files">ФАЙЛЫ</button>
          <button class="tab-btn" data-tab="journal">ЖУРНАЛ</button>
        </div>
        <div class="log-row__meta" id="cardMetaLine"></div>
      </div>
      <section data-panel="overview">
        <div class="overview-layout">
          <div class="stack">
            <div class="grid grid--overview">
              <div class="field field--compact">
                <label for="cardVehicle">МАРКА / МОДЕЛЬ</label>
                <input id="cardVehicle" type="text" maxlength="60" placeholder="KIA RIO">
              </div>
              <div class="field field--compact">
                <label for="cardColumn">СТОЛБЕЦ</label>
                <select id="cardColumn"></select>
              </div>
            </div>
            <div class="field">
              <label for="cardTitle">ЗАГОЛОВОК</label>
              <input id="cardTitle" type="text" maxlength="120">
            </div>
            <div class="field">
              <label for="cardDescription">ОПИСАНИЕ</label>
        <textarea id="cardDescription" maxlength="20000"></textarea>
            </div>
          </div>
          <div class="stack">
            <div class="subpanel signal-panel">
              <div class="panel-title">СИГНАЛ / ОБРАТНЫЙ ОТСЧЁТ</div>
              <div class="signal-preview" id="signalPreview">01Д 00Ч</div>
              <div class="signal-grid">
                <label class="signal-cell"><span>ДН</span><input id="signalDays" type="number" min="0" max="365"></label>
                <label class="signal-cell"><span>ЧС</span><input id="signalHours" type="number" min="0" max="23"></label>
              </div>
            </div>
            <div class="subpanel">
              <div class="field field--tags">
                <label for="tagInput">МЕТКИ</label>
                <div class="tag-list" id="tagList"></div>
                <div class="tag-suggestions" id="tagSuggestions"></div>
                <div class="tag-entry">
                  <input id="tagInput" type="text" maxlength="24" placeholder="ЖДЁМ">
                  <button class="btn" id="tagAddButton">+</button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>
      <section data-panel="files" class="hidden">
        <div class="subpanel">
          <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
            <input id="fileInput" type="file" multiple>
            <button class="btn" id="uploadButton">ЗАГРУЗИТЬ</button>
          </div>
          <div id="fileList"></div>
        </div>
      </section>
      <section data-panel="journal" class="hidden">
        <div class="log-view" id="logList"></div>
      </section>
      <div class="dialog__foot">
        <div style="display:flex; gap:8px; flex-wrap:wrap;">
          <button class="btn btn--danger hidden" id="archiveAction">В АРХИВ</button>
          <button class="btn hidden" id="restoreAction">ВЕРНУТЬ ИЗ АРХИВА</button>
        </div>
        <div style="display:flex; gap:8px; flex-wrap:wrap;">
          <button class="btn" data-close="card">ОТМЕНА</button>
          <button class="btn btn--accent" id="saveCardButton">СОХРАНИТЬ</button>
        </div>
      </div>
    </div>
  </div>

  <script>
    const ACTOR_STORAGE_KEY = 'kanban-actor';
    const API_TOKEN_STORAGE_KEY = 'kanban-api-token';

    const state = {
      actor: localStorage.getItem(ACTOR_STORAGE_KEY) || '',
      apiToken: localStorage.getItem(API_TOKEN_STORAGE_KEY) || '',
      boardScale: 1,
      boardPan: {
        active: false,
        pointerId: null,
        startX: 0,
        startY: 0,
        scrollLeft: 0,
        scrollTop: 0,
        moved: false,
      },
      boardViewportPrimed: false,
      stickyDraft: null,
      stickyDrag: {
        active: false,
        pointerId: null,
        stickyId: null,
        startX: 0,
        startY: 0,
        startClientX: 0,
        startClientY: 0,
        moved: false,
      },
      snapshot: null,
      gptWall: null,
      activeCard: null,
      editingId: null,
      currentTab: 'overview',
      draftTags: [],
      pollHandle: null,
    };

    const COLUMN_TONES = [
      { tint: 'rgba(38, 47, 41, 0.95)', head: 'rgba(95, 109, 86, 0.22)', edge: '#70796c', empty: 'rgba(167, 178, 132, 0.06)' },
      { tint: 'rgba(35, 41, 45, 0.95)', head: 'rgba(108, 116, 132, 0.18)', edge: '#6f7880', empty: 'rgba(147, 161, 176, 0.06)' },
      { tint: 'rgba(45, 43, 37, 0.95)', head: 'rgba(129, 114, 84, 0.18)', edge: '#827763', empty: 'rgba(181, 156, 107, 0.06)' },
      { tint: 'rgba(40, 44, 39, 0.95)', head: 'rgba(110, 122, 96, 0.16)', edge: '#76806f', empty: 'rgba(160, 174, 135, 0.05)' },
    ];
    const SUGGESTED_TAGS = [
      { label: 'СРОЧНО', tone: 'danger' },
      { label: 'ГОРИТ СРОК' },
      { label: 'ЖДЁМ' },
      { label: 'СОГЛАСОВАТЬ' },
      { label: 'ЗАКАЗАТЬ' },
    ];

    const els = {
      boardScroll: document.querySelector('.board-scroll'),
      board: document.getElementById('board'),
      statusLine: document.getElementById('statusLine'),
      boardSettingsButton: document.getElementById('boardSettingsButton'),
      stickyDockButton: document.getElementById('stickyDockButton'),
      operatorButton: document.getElementById('operatorButton'),
      archiveButton: document.getElementById('archiveButton'),
      gptWallButton: document.getElementById('gptWallButton'),
      columnButton: document.getElementById('columnButton'),
      cardButton: document.getElementById('cardButton'),
      boardSettingsModal: document.getElementById('boardSettingsModal'),
      boardScaleInput: document.getElementById('boardScaleInput'),
      boardScaleValue: document.getElementById('boardScaleValue'),
      boardScaleReset: document.getElementById('boardScaleReset'),
      stickyModal: document.getElementById('stickyModal'),
      stickyModalTitle: document.getElementById('stickyModalTitle'),
      stickyText: document.getElementById('stickyText'),
      stickyDays: document.getElementById('stickyDays'),
      stickyHours: document.getElementById('stickyHours'),
      saveStickyButton: document.getElementById('saveStickyButton'),
      identityModal: document.getElementById('identityModal'),
      identityInput: document.getElementById('identityInput'),
      identitySave: document.getElementById('identitySave'),
      archiveModal: document.getElementById('archiveModal'),
      archiveList: document.getElementById('archiveList'),
      gptWallModal: document.getElementById('gptWallModal'),
      gptWallMeta: document.getElementById('gptWallMeta'),
      gptWallText: document.getElementById('gptWallText'),
      gptWallRefresh: document.getElementById('gptWallRefresh'),
""",
        """
      cardModal: document.getElementById('cardModal'),
      cardModalTitle: document.getElementById('cardModalTitle'),
      cardMetaLine: document.getElementById('cardMetaLine'),
      cardVehicle: document.getElementById('cardVehicle'),
      cardTitle: document.getElementById('cardTitle'),
      cardColumn: document.getElementById('cardColumn'),
      cardDescription: document.getElementById('cardDescription'),
      signalPreview: document.getElementById('signalPreview'),
      signalDays: document.getElementById('signalDays'),
      signalHours: document.getElementById('signalHours'),
      tagList: document.getElementById('tagList'),
      tagSuggestions: document.getElementById('tagSuggestions'),
      tagInput: document.getElementById('tagInput'),
      tagAddButton: document.getElementById('tagAddButton'),
      saveCardButton: document.getElementById('saveCardButton'),
      archiveAction: document.getElementById('archiveAction'),
      restoreAction: document.getElementById('restoreAction'),
      fileInput: document.getElementById('fileInput'),
      uploadButton: document.getElementById('uploadButton'),
      fileList: document.getElementById('fileList'),
      logList: document.getElementById('logList'),
    };

    const escapeHtml = (value) => String(value ?? '').replace(/[&<>"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[char]));

    async function api(path, options = {}) {
      const request = { method: options.method || 'GET', headers: {}, cache: 'no-store' };
      if (options.body) {
        request.method = options.method || 'POST';
        request.headers['Content-Type'] = 'application/json';
        request.body = JSON.stringify(options.body);
      }
      if (state.apiToken) request.headers['Authorization'] = 'Bearer ' + state.apiToken;
      let response;
      try {
        response = await fetch(path, request);
      } catch (_) {
        throw new Error('НЕТ СВЯЗИ С ДОСКОЙ. ПРОВЕРЬ СЕТЬ ИЛИ ПУБЛИЧНЫЙ АДРЕС.');
      }
      const rawText = await response.text();
      let payload = null;
      try {
        payload = rawText ? JSON.parse(rawText) : null;
      } catch (_) {
        payload = null;
      }
      if (response.status === 401 || payload?.error?.code === 'unauthorized') {
        throw new Error(accessDeniedMessage());
      }
      if (!payload.ok) throw new Error(payload.error?.message || 'Ошибка API');
      if (!response.ok) {
        throw new Error(payload?.error?.message || ('HTTP ' + response.status));
      }
      return payload.data;
    }

    function setApiToken(token, { persist = true } = {}) {
      const normalized = String(token || '').trim();
      state.apiToken = normalized;
      if (!persist) return normalized;
      if (normalized) localStorage.setItem(API_TOKEN_STORAGE_KEY, normalized);
      else localStorage.removeItem(API_TOKEN_STORAGE_KEY);
      return normalized;
    }

    function consumeUrlAccessToken() {
      try {
        const url = new URL(window.location.href);
        const token = url.searchParams.get('access_token');
        if (!token) return;
        setApiToken(token);
        url.searchParams.delete('access_token');
        const cleanQuery = url.searchParams.toString();
        const cleanUrl = url.pathname + (cleanQuery ? ('?' + cleanQuery) : '') + url.hash;
        window.history.replaceState({}, document.title, cleanUrl || '/');
      } catch (_) {
        return;
      }
    }

    function withAccessToken(path) {
      const url = new URL(path, window.location.origin);
      if (state.apiToken) url.searchParams.set('access_token', state.apiToken);
      return url.pathname + url.search + url.hash;
    }

    function accessDeniedMessage() {
      if (state.apiToken) {
        return 'ДОСТУП ОТКЛОНЁН. ТОКЕН УСТАРЕЛ ИЛИ НЕ ПОДХОДИТ ДЛЯ ЭТОЙ ДОСКИ.';
      }
      return 'ЭТА ДОСКА ЗАЩИЩЕНА. ОТКРОЙ ССЫЛКУ ДОСТУПА ИЗ ОКНА ХОСТА ИЛИ ПЕРЕДАЙ access_token В URL.';
    }

    function extractDownloadName(response, fallbackName) {
      const header = response.headers.get('Content-Disposition') || '';
      const utfName = header.match(/filename\\*=UTF-8''([^;]+)/i);
      if (utfName?.[1]) {
        try { return decodeURIComponent(utfName[1]); } catch (_) { return utfName[1]; }
      }
      const plainName = header.match(/filename=\"?([^\";]+)\"?/i);
      if (plainName?.[1]) return plainName[1];
      return fallbackName || 'attachment.bin';
    }

    async function downloadAttachment(url) {
      const headers = state.apiToken ? { Authorization: 'Bearer ' + state.apiToken } : {};
      let response;
      try {
        response = await fetch(url, { headers, cache: 'no-store' });
      } catch (_) {
        throw new Error('НЕ УДАЛОСЬ СКАЧАТЬ ФАЙЛ. ПРОВЕРЬ СЕТЬ И ДОСТУП К ДОСКЕ.');
      }
      if (response.status === 401) {
        throw new Error(accessDeniedMessage());
      }
      if (!response.ok) {
        throw new Error('ФАЙЛ НЕДОСТУПЕН: HTTP ' + response.status);
      }
      const blob = await response.blob();
      const downloadUrl = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = downloadUrl;
      link.download = extractDownloadName(response, 'attachment.bin');
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(downloadUrl), 1500);
    }

    function ensureActor() {
      if (state.actor) {
        els.operatorButton.textContent = 'ОПЕРАТОР: ' + state.actor;
        els.identityModal.classList.remove('is-open');
        return true;
      }
      els.identityInput.value = '';
      els.identityModal.classList.add('is-open');
      els.identityInput.focus();
      return false;
    }

    function setStatus(text, isError = false) {
      els.statusLine.textContent = text;
      els.statusLine.style.borderColor = isError ? 'var(--danger)' : 'var(--line)';
      els.statusLine.style.color = isError ? '#ffd1ca' : 'var(--text)';
    }

    function formatDate(value) {
      if (!value) return 'нет даты';
      try { return new Date(value).toLocaleString('ru-RU'); } catch { return value; }
    }

    function normalizeBoardScale(value) {
      const numeric = Number(value);
      if (!Number.isFinite(numeric)) return 1;
      return Math.min(1.5, Math.max(0.5, Math.round(numeric * 100) / 100));
    }

    function applyBoardScale(value, { syncInput = false } = {}) {
      const scale = normalizeBoardScale(value);
      state.boardScale = scale;
      els.board.style.setProperty('--board-scale', String(scale));
      adjustBoardBounds();
      const percent = Math.round(scale * 100);
      if (els.boardScaleValue) els.boardScaleValue.textContent = percent + '%';
      if (syncInput && els.boardScaleInput) els.boardScaleInput.value = String(percent);
      return scale;
    }

    function clampBoardScroll(left = els.boardScroll.scrollLeft, top = els.boardScroll.scrollTop) {
      const maxLeft = Math.max(0, els.boardScroll.scrollWidth - els.boardScroll.clientWidth);
      const maxTop = Math.max(0, els.boardScroll.scrollHeight - els.boardScroll.clientHeight);
      const rawLeft = Number(left);
      const rawTop = Number(top);
      const safeLeft = Number.isFinite(rawLeft) ? rawLeft : els.boardScroll.scrollLeft;
      const safeTop = Number.isFinite(rawTop) ? rawTop : els.boardScroll.scrollTop;
      const nextLeft = Math.min(maxLeft, Math.max(0, safeLeft));
      const nextTop = Math.min(maxTop, Math.max(0, safeTop));
      els.boardScroll.scrollLeft = nextLeft;
      els.boardScroll.scrollTop = nextTop;
      return { left: nextLeft, top: nextTop, maxLeft, maxTop };
    }

    function boardViewportAnchor() {
      return {
        x: els.boardScroll.clientWidth / 2,
        y: els.boardScroll.clientHeight / 2,
      };
    }

    function boardCanvasLayout() {
      const clientWidth = Math.max(els.boardScroll.clientWidth, 1);
      const clientHeight = Math.max(els.boardScroll.clientHeight, 1);
      return {
        left: 0,
        top: 0,
        right: Math.max(240, Math.round(clientWidth * 0.32)),
        bottom: Math.max(220, Math.round(clientHeight * 0.24)),
      };
    }

    function applyBoardCanvasLayout() {
      const layout = boardCanvasLayout();
      els.board.style.setProperty('--board-gutter-left', layout.left + 'px');
      els.board.style.setProperty('--board-gutter-right', layout.right + 'px');
      els.board.style.setProperty('--board-gutter-top', layout.top + 'px');
      els.board.style.setProperty('--board-gutter-bottom', layout.bottom + 'px');
      return layout;
    }

    function boardCanvasOffsets() {
      const styles = getComputedStyle(els.board);
      return {
        left: Number.parseFloat(styles.getPropertyValue('--board-gutter-left')) || 0,
        top: Number.parseFloat(styles.getPropertyValue('--board-gutter-top')) || 0,
      };
    }

    function zoomBoardTo(value, { syncInput = false, anchor = null } = {}) {
      const previous = state.boardScale || 1;
      const next = normalizeBoardScale(value);
      const targetAnchor = anchor || boardViewportAnchor();
      const anchorX = Math.max(0, Number(targetAnchor.x || 0));
      const anchorY = Math.max(0, Number(targetAnchor.y || 0));
      const previousLeft = els.boardScroll.scrollLeft;
      const previousTop = els.boardScroll.scrollTop;
      const previousOffsets = boardCanvasOffsets();
      const contentX = (previousLeft + anchorX - previousOffsets.left) / previous;
      const contentY = (previousTop + anchorY - previousOffsets.top) / previous;
      applyBoardScale(next, { syncInput });
      requestAnimationFrame(() => {
        const nextOffsets = boardCanvasOffsets();
        clampBoardScroll(
          nextOffsets.left + (contentX * next) - anchorX,
          nextOffsets.top + (contentY * next) - anchorY,
        );
      });
      return next;
    }

    function mountStatusLine() {
      if (!els.statusLine || !els.boardScroll || els.statusLine.parentElement !== els.boardScroll) return;
      const host = document.createElement('div');
      host.className = 'status-shell';
      els.boardScroll.parentElement.insertBefore(host, els.boardScroll);
      host.appendChild(els.statusLine);
    }

    function primeBoardViewport() {
      if (state.boardViewportPrimed) return;
      const layout = applyBoardCanvasLayout();
      const scrollWidth = els.boardScroll.scrollWidth;
      const clientWidth = els.boardScroll.clientWidth;
      const scrollHeight = els.boardScroll.scrollHeight;
      const clientHeight = els.boardScroll.clientHeight;
      if (scrollWidth > clientWidth || scrollHeight > clientHeight) {
        clampBoardScroll(
          Math.max(0, layout.left),
          Math.max(0, layout.top),
        );
      }
      state.boardViewportPrimed = true;
    }

    function beginBoardPan(event) {
      if (event.button !== 0) return;
      if (!(event.target instanceof HTMLElement)) return;
      if (event.target.closest('.card, .sticky, .sticky__close, .btn, .gear-button, .tab-btn, input, textarea, select, a, [contenteditable="true"]')) return;
      state.boardPan.active = true;
      state.boardPan.pointerId = event.pointerId;
      state.boardPan.startX = event.clientX;
      state.boardPan.startY = event.clientY;
      state.boardPan.scrollLeft = els.boardScroll.scrollLeft;
      state.boardPan.scrollTop = els.boardScroll.scrollTop;
      state.boardPan.moved = false;
      els.boardScroll.classList.add('is-panning');
      els.boardScroll.setPointerCapture(event.pointerId);
      event.preventDefault();
    }

    function moveBoardPan(event) {
      if (!state.boardPan.active || state.boardPan.pointerId !== event.pointerId) return;
      const dx = event.clientX - state.boardPan.startX;
      const dy = event.clientY - state.boardPan.startY;
      if (!state.boardPan.moved && Math.abs(dx) + Math.abs(dy) > 4) state.boardPan.moved = true;
      if (!state.boardPan.moved) return;
      clampBoardScroll(state.boardPan.scrollLeft - dx, state.boardPan.scrollTop - dy);
      event.preventDefault();
    }

    function endBoardPan(event) {
      if (!state.boardPan.active || state.boardPan.pointerId !== event.pointerId) return;
      state.boardPan.active = false;
      state.boardPan.pointerId = null;
      els.boardScroll.classList.remove('is-panning');
      try {
        if (els.boardScroll.hasPointerCapture(event.pointerId)) {
          els.boardScroll.releasePointerCapture(event.pointerId);
        }
      } catch (error) {
        // Pointer capture can disappear if the browser cancels the gesture.
      }
    }

    function secondsToParts(total) {
      const safe = Math.max(0, Number(total || 0));
      if (!safe) {
        return { days: 0, hours: 0, minutes: 0, seconds: 0 };
      }
      const roundedHours = Math.max(1, Math.ceil(safe / 3600));
      return {
        days: Math.floor(roundedHours / 24),
        hours: roundedHours % 24,
        minutes: 0,
        seconds: 0,
      };
    }

    function deadlineInput() {
      return {
        days: Number(els.signalDays.value || 0),
        hours: Number(els.signalHours.value || 0),
        minutes: 0,
        seconds: 0,
      };
    }

    function stickyDeadlineInput() {
      return {
        days: Number(els.stickyDays.value || 0),
        hours: Number(els.stickyHours.value || 0),
        minutes: 0,
        seconds: 0,
      };
    }

    function durationToShort(total) {
      const parts = secondsToParts(total);
      const hh = String(parts.hours).padStart(2, '0');
      if (parts.days > 0) return parts.days + 'Д ' + hh + 'Ч';
      return hh + 'Ч';
    }

    function durationToFull(total) {
      const parts = secondsToParts(total);
      return String(parts.days).padStart(2, '0') + 'Д ' + String(parts.hours).padStart(2, '0') + 'Ч';
    }

    function durationToMarkup(total, alwaysShowDays = false) {
      const parts = secondsToParts(total);
      const groups = [];
      if (alwaysShowDays || parts.days > 0) {
        groups.push('<span class="time-readout__group"><span class="time-readout__num">' + String(parts.days).padStart(2, '0') + '</span><span class="time-readout__unit">Д</span></span>');
      }
      groups.push('<span class="time-readout__group"><span class="time-readout__num">' + String(parts.hours).padStart(2, '0') + '</span><span class="time-readout__unit">Ч</span></span>');
      return '<span class="time-readout">' + groups.join('') + '</span>';
    }

    function cardHeading(card) {
      const vehicle = String(card?.vehicle || '').trim();
      const title = String(card?.title || '').trim();
      if (vehicle && title) return vehicle + ' / ' + title;
      return title || vehicle || 'Без названия';
    }

    function stickyPayload() {
      return {
        actor_name: state.actor,
        source: 'ui',
        text: els.stickyText.value.trim(),
        deadline: stickyDeadlineInput(),
      };
    }

    function stickyHeadingLabel(sticky) {
      return 'СТИКЕР / ' + String(sticky?.id || '').slice(0, 8).toUpperCase();
    }

    function stickyRenderPosition(value, scale = state.boardScale || 1) {
      return Math.max(0, Math.round(Number(value || 0) * scale));
    }

    function stickyDurationMarkup(sticky) {
      return durationToMarkup(sticky.remaining_seconds || 0, true);
    }

    function stickyToStyle(sticky) {
      const scale = state.boardScale || 1;
      const left = stickyRenderPosition(sticky.x, scale);
      const top = stickyRenderPosition(sticky.y, scale);
      const opacity = Number(sticky.opacity ?? 0.9);
      const toneClass = opacity <= 0.6 ? 'high' : (opacity <= 0.75 ? 'mid' : 'low');
      return { left, top, opacity, toneClass };
    }

    function getStickyComposerPlacement() {
      const scale = state.boardScale || 1;
      const left = (els.boardScroll.scrollLeft + Math.min(220, els.boardScroll.clientWidth * 0.22)) / scale;
      const top = (els.boardScroll.scrollTop + Math.min(140, els.boardScroll.clientHeight * 0.16)) / scale;
      return {
        x: Math.max(0, Math.round(left)),
        y: Math.max(0, Math.round(top)),
      };
    }

    function stickyHtml(sticky) {
      const style = stickyToStyle(sticky);
      const expired = sticky.is_expired ? 'true' : 'false';
      const tone = style.toneClass;
      return '<article class="sticky" data-sticky-id="' + escapeHtml(sticky.id) + '" data-expired="' + expired + '" data-opacity="' + tone + '" style="left:' + style.left + 'px; top:' + style.top + 'px; opacity:' + style.opacity + ';">' +
        '<div class="sticky__head"><span class="sticky__pin">СТИКЕР</span><button class="sticky__close" type="button" data-delete-sticky="' + escapeHtml(sticky.id) + '" title="Удалить">×</button></div>' +
        '<div class="sticky__text">' + escapeHtml(sticky.text || 'ЗАМЕТКА') + '</div>' +
        '<div class="sticky__meta"><span>' + stickyDurationMarkup(sticky) + '</span><span>TIME</span></div>' +
        '</article>';
    }

    function adjustBoardBounds() {
      const scale = state.boardScale || 1;
      applyBoardCanvasLayout();
      els.board.style.minWidth = '';
      els.board.style.minHeight = '';
      const naturalWidth = Math.max(els.board.scrollWidth, els.board.clientWidth);
      const naturalHeight = Math.max(els.board.scrollHeight, els.board.clientHeight);
      const stickyWidth = 240;
      const stickyHeight = 152;
      const viewportSlackX = Math.max(48, Math.round(els.boardScroll.clientWidth * 0.04));
      const viewportSlackY = Math.max(32, Math.round(els.boardScroll.clientHeight * 0.03));
      let maxRight = naturalWidth + viewportSlackX;
      let maxBottom = naturalHeight + viewportSlackY;
      (state.snapshot?.stickies || []).forEach((sticky) => {
        maxRight = Math.max(maxRight, Math.round((Number(sticky.x || 0) + stickyWidth + 24) * scale));
        maxBottom = Math.max(maxBottom, Math.round((Number(sticky.y || 0) + stickyHeight + 24) * scale));
      });
      els.board.style.minWidth = Math.max(naturalWidth, maxRight) + 'px';
      els.board.style.minHeight = Math.max(naturalHeight, maxBottom) + 'px';
      clampBoardScroll();
    }

    function renderStickies() {
      const layer = els.stickyLayer || document.getElementById('stickyLayer');
      if (!layer) return;
      els.stickyLayer = layer;
      const stickies = state.snapshot?.stickies || [];
      layer.innerHTML = stickies.length ? stickies.map(stickyHtml).join('') : '';
      adjustBoardBounds();
    }

    function renderSignalPreview() {
      const draft = deadlineInput();
      const total = (draft.days * 86400) + (draft.hours * 3600) + (draft.minutes * 60) + draft.seconds;
      els.signalPreview.innerHTML = durationToMarkup(total, true);
    }

    function addDraftTag() {
      const tag = els.tagInput.value.trim().toUpperCase();
      if (!tag) return;
      if (!state.draftTags.includes(tag)) state.draftTags.push(tag);
      els.tagInput.value = '';
      renderTags();
    }

    function addSuggestedTag(tag) {
      const value = String(tag || '').trim().toUpperCase();
      if (!value) return;
      if (!state.draftTags.includes(value)) state.draftTags.push(value);
      renderTags();
    }

    function arrayBufferToBase64(buffer) {
      const bytes = new Uint8Array(buffer);
      const chunkSize = 0x8000;
      let binary = '';
      for (let index = 0; index < bytes.length; index += chunkSize) {
        const chunk = bytes.subarray(index, index + chunkSize);
        binary += String.fromCharCode.apply(null, chunk);
      }
      return btoa(binary);
    }

    function currentCardPayload() {
      return {
        actor_name: state.actor,
        source: 'ui',
        vehicle: els.cardVehicle.value.trim(),
        title: els.cardTitle.value.trim(),
        description: els.cardDescription.value.trim(),
        column: els.cardColumn.value,
        tags: state.draftTags.slice(),
        deadline: deadlineInput(),
      };
    }

    function populateColumns(selectedId) {
      els.cardColumn.innerHTML = (state.snapshot?.columns || []).map((column) => {
        const selected = column.id === selectedId ? ' selected' : '';
        return '<option value="' + escapeHtml(column.id) + '"' + selected + '>' + escapeHtml(column.label) + '</option>';
      }).join('');
    }

    function columnLabelById(columnId) {
      const found = (state.snapshot?.columns || []).find((column) => column.id === columnId);
      return found?.label || String(columnId || '—');
    }

    function formatIndicatorLabel(value) {
      return ({ green: 'зелёный', yellow: 'жёлтый', red: 'красный' }[String(value || '').toLowerCase()] || String(value || '—'));
    }

    function formatSourceLabel(value) {
      return ({ ui: '', mcp: 'через GPT', api: 'через API', system: 'системой' }[String(value || '').toLowerCase()] || String(value || ''));
    }

    function formatBytes(value) {
      const size = Number(value || 0);
      if (size >= 1024 * 1024) return (size / (1024 * 1024)).toFixed(1) + ' МБ';
      if (size >= 1024) return Math.round(size / 1024) + ' КБ';
      return size + ' Б';
    }

    function describeValue(value, key = '') {
      if (Array.isArray(value)) return value.length ? value.join(', ') : '—';
      if (value === null || value === undefined || value === '') return '—';
      if (String(key).includes('column')) return columnLabelById(value);
      if (String(key).includes('indicator')) return formatIndicatorLabel(value);
      if (String(key).includes('size_bytes')) return formatBytes(value);
      if (String(key).includes('total_seconds')) return durationToFull(value);
      if (String(key).includes('timestamp')) return formatDate(value);
      return String(value).replace(/\\s+/g, ' ').trim();
    }

    function formatLogDetails(event) {
      const details = event.details || {};
      const lines = [];
      const push = (label, value, kind = '') => {
        lines.push(label + ' ' + describeValue(value, kind || label));
      };

      if (event.action === 'card_created') {
        if (details.vehicle) push('машина', details.vehicle);
        if (details.column) push('столбец', details.column);
        if (details.tags) push('метки', details.tags);
        if (details.deadline_total_seconds !== undefined) push('сигнал', details.deadline_total_seconds, 'deadline_total_seconds');
      } else if (event.action === 'card_moved') {
        if (details.before_column) push('из', details.before_column);
        if (details.after_column) push('в', details.after_column);
      } else if (event.action === 'card_archived' || event.action === 'card_restored') {
        if (details.column) push('столбец', details.column);
      } else if (event.action === 'vehicle_changed') {
        if (details.before !== undefined) push('было', details.before);
        if (details.after !== undefined) push('стало', details.after);
      } else if (event.action === 'title_changed' || event.action === 'description_changed') {
        if (details.before !== undefined) push('было', details.before);
        if (details.after !== undefined) push('стало', details.after);
      } else if (event.action === 'signal_changed') {
        if (details.before_total_seconds !== undefined) push('было', details.before_total_seconds, 'before_total_seconds');
        if (details.after_total_seconds !== undefined) push('стало', details.after_total_seconds, 'after_total_seconds');
      } else if (event.action === 'signal_indicator_changed') {
        if (details.before_indicator !== undefined) push('было', details.before_indicator, 'before_indicator');
        if (details.after_indicator !== undefined) push('стало', details.after_indicator, 'after_indicator');
        if (details.deadline_total_seconds !== undefined) push('сигнал', details.deadline_total_seconds, 'deadline_total_seconds');
      } else if (event.action === 'attachment_added' || event.action === 'attachment_removed') {
        if (details.file_name) push('файл', details.file_name);
        if (details.size_bytes !== undefined) push('размер', details.size_bytes, 'size_bytes');
      } else if (event.action === 'tag_added' || event.action === 'tag_removed') {
        if (details.tag) push('метка', details.tag);
      } else if (event.action === 'tags_changed') {
        if (details.before !== undefined) push('было', details.before);
        if (details.after !== undefined) push('стало', details.after);
      } else {
        Object.entries(details).forEach(([key, value]) => push(key.replace(/_/g, ' '), value));
      }

      return lines.join(' | ');
    }

    function renderTags() {
      els.tagList.innerHTML = state.draftTags.length
        ? state.draftTags.map((tag) => '<button class="tag" data-remove-tag="' + escapeHtml(tag) + '">' + escapeHtml(tag) + ' ×</button>').join('')
        : '<div class="tag tag--muted">МЕТОК НЕТ</div>';
      els.tagSuggestions.innerHTML = SUGGESTED_TAGS.map((tag) => {
        const active = state.draftTags.includes(tag.label);
        const toneClass = tag.tone === 'danger' ? ' tag-suggestion--danger' : '';
        const activeClass = active ? ' is-active' : '';
        return '<button class="tag-suggestion' + toneClass + activeClass + '" data-suggest-tag="' + escapeHtml(tag.label) + '">' + escapeHtml(tag.label) + '</button>';
      }).join('');
    }

    function renderFiles(card) {
      const attachments = (card?.attachments || []).filter((item) => !item.removed);
      els.fileList.innerHTML = attachments.length
        ? attachments.map((item) => '<div class="file-row"><div>' + escapeHtml(item.file_name) + '</div><div class="log-row__meta">' + escapeHtml(formatDate(item.created_at)) + ' · ' + Math.round(item.size_bytes / 1024) + ' КБ</div><div style="display:flex; gap:8px; flex-wrap:wrap;"><a class="btn" href="/api/attachment?card_id=' + encodeURIComponent(card.id) + '&attachment_id=' + encodeURIComponent(item.id) + '">СКАЧАТЬ</a><button class="btn btn--danger" data-remove-file="' + escapeHtml(item.id) + '">УДАЛИТЬ</button></div></div>').join('')
        : '<div class="log-row__meta">ФАЙЛОВ НЕТ.</div>';
    }

    function renderLogs(events) {
      els.logList.innerHTML = events.length
        ? events.map((event) => {
            const sourceLabel = formatSourceLabel(event.source);
            const details = formatLogDetails(event);
            const parts = [formatDate(event.timestamp), event.actor_name];
            if (sourceLabel) parts.push(sourceLabel);
            parts.push(event.message);
            if (details) parts.push(details);
            return '<div class="log-row">' + escapeHtml(parts.join(' | ')) + '</div>';
          }).join('')
        : '<div class="log-row__meta">ЗАПИСЕЙ НЕТ.</div>';
    }

    function renderArchive() {
      const cards = state.snapshot?.archive || [];
      els.archiveList.innerHTML = cards.length
        ? cards.map((card) => '<div class="archive-row"><div><strong>' + escapeHtml(cardHeading(card)) + '</strong></div><div>' + escapeHtml(card.description || 'Описание не указано') + '</div><div class="archive-row__meta">АРХИВ: ' + escapeHtml(formatDate(card.updated_at)) + '</div><div style="display:flex; gap:8px;"><button class="btn" data-restore-card="' + escapeHtml(card.id) + '">ВЕРНУТЬ</button></div></div>').join('')
        : '<div class="log-row__meta">АРХИВ ПУСТ.</div>';
    }

    function renderGptWall(data) {
      state.gptWall = data;
      const meta = data?.meta || {};
      els.gptWallMeta.textContent =
        'СОБРАНО: ' + formatDate(meta.generated_at) +
        ' | СТОЛБЦОВ: ' + (meta.columns ?? 0) +
        ' | АКТИВНЫХ: ' + (meta.active_cards ?? 0) +
        ' | АРХИВ: ' + (meta.archived_cards ?? 0) +
        ' | СТИКЕРОВ: ' + (meta.stickies ?? 0) +
        ' | СОБЫТИЙ: ' + (meta.events_total ?? 0);
      els.gptWallText.textContent = data?.text || 'ДАННЫХ НЕТ.';
    }

    async function loadGptWall(openModal = false) {
      try {
        const data = await api('/api/get_gpt_wall', { method: 'POST', body: { include_archived: true, event_limit: 100 } });
        renderGptWall(data);
        if (openModal) els.gptWallModal.classList.add('is-open');
      } catch (error) {
        els.gptWallMeta.textContent = 'ОШИБКА ЗАГРУЗКИ СЛОЯ GPT.';
        els.gptWallText.textContent = error.message;
        if (openModal) els.gptWallModal.classList.add('is-open');
        setStatus(error.message, true);
      }
    }

    function cardHtml(card) {
      const previewTags = (card.tags || []).slice(0, 3);
      const extraTags = (card.tags || []).length - previewTags.length;
      const tagsHtml = previewTags.length
        ? previewTags.map((tag) => '<span class="tag">' + escapeHtml(tag) + '</span>').join('') + (extraTags > 0 ? '<span class="tag">+' + extraTags + '</span>' : '')
        : '<span class="tag tag--muted">БЕЗ МЕТОК</span>';
      const headingHtml = card.vehicle
        ? '<div class="card__heading"><div class="card__vehicle">' + escapeHtml(card.vehicle) + '</div><span class="card__slash">/</span><div class="card__title">' + escapeHtml(card.title) + '</div></div>'
        : '<div class="card__title">' + escapeHtml(card.title) + '</div>';
      return '<article class="card" draggable="true" data-card-id="' + escapeHtml(card.id) + '" data-indicator="' + escapeHtml(card.indicator) + '" data-status="' + escapeHtml(card.status) + '" data-blink="' + (card.is_blinking ? 'true' : 'false') + '">' + headingHtml + '<div class="card__desc">' + escapeHtml(card.description || 'Описание не указано') + '</div><div class="card__signal"><span class="card__signal-label"><span class="lamp" data-indicator="' + escapeHtml(card.indicator) + '"></span><span>СИГН</span></span><span class="card__signal-value">' + durationToMarkup(card.remaining_seconds, false) + '</span></div><div class="card__tags">' + tagsHtml + '</div><div class="meta-line"><span>ФАЙЛЫ ' + escapeHtml(card.attachment_count) + '</span><span>ЖУРНАЛ ' + escapeHtml(card.events_count) + '</span></div></article>';
    }

    function renderBoard() {
      const snapshot = state.snapshot;
      if (!snapshot) return;
      const grouped = new Map();
      snapshot.columns.forEach((column) => grouped.set(column.id, []));
      snapshot.cards.forEach((card) => {
        if (!grouped.has(card.column)) grouped.set(card.column, []);
        grouped.get(card.column).push(card);
      });
      els.board.innerHTML = snapshot.columns.map((column, index) => {
        const cards = grouped.get(column.id) || [];
        const tone = COLUMN_TONES[index % COLUMN_TONES.length];
        const toneStyle = '--column-tint:' + tone.tint + ';--column-head:' + tone.head + ';--column-edge:' + tone.edge + ';--column-empty:' + tone.empty + ';';
        return '<section class="column" style="' + toneStyle + '" data-column-id="' + escapeHtml(column.id) + '"><div class="column__head"><div class="column__title">' + escapeHtml(column.label) + '</div><div class="column__count">' + cards.length + '</div></div><div class="column__cards">' + (cards.length ? cards.map(cardHtml).join('') : '<div class="empty">ЗДЕСЬ ПОКА ПУСТО.</div>') + '</div><button class="btn" data-create-in="' + escapeHtml(column.id) + '">+ КАРТОЧКА</button></section>';
      }).join('') + '<div class="sticky-layer" id="stickyLayer"></div>';
      els.stickyLayer = document.getElementById('stickyLayer');
      renderStickies();
      renderArchive();
    }

    function setTab(name) {
      state.currentTab = name;
      document.querySelectorAll('[data-tab]').forEach((button) => button.classList.toggle('is-active', button.dataset.tab === name));
      document.querySelectorAll('[data-panel]').forEach((panel) => panel.classList.toggle('hidden', panel.dataset.panel !== name));
    }

    function openCardModal(card) {
      state.activeCard = card || null;
      state.editingId = card?.id || null;
      state.draftTags = (card?.tags || []).slice();
      els.cardModalTitle.textContent = card?.id ? 'КАРТОЧКА / ' + cardHeading(card) : 'НОВАЯ КАРТОЧКА';
      els.cardVehicle.value = card?.vehicle || '';
      els.cardTitle.value = card?.title || '';
      els.cardDescription.value = card?.description || '';
      populateColumns(card?.column || state.snapshot?.columns?.[0]?.id);
      const parts = secondsToParts(card?.remaining_seconds || 86400);
      els.signalDays.value = parts.days;
      els.signalHours.value = parts.hours;
      renderSignalPreview();
      els.cardMetaLine.textContent = card?.id ? ('СОЗДАНО: ' + formatDate(card.created_at) + ' · ИЗМЕНЕНО: ' + formatDate(card.updated_at)) : 'НОВАЯ ЗАПИСЬ';
      els.archiveAction.classList.toggle('hidden', !card?.id || card.archived);
      els.restoreAction.classList.toggle('hidden', !card?.id || !card.archived);
      renderTags();
      renderFiles(card);
      renderLogs([]);
      setTab('overview');
      els.cardModal.classList.add('is-open');
      if (card?.id) loadLogs(card.id);
    }

    function closeCardModal() {
      els.cardModal.classList.remove('is-open');
      state.activeCard = null;
      state.editingId = null;
      state.draftTags = [];
      els.fileInput.value = '';
    }

    async function refreshSnapshot(showSuccess = false) {
      try {
        state.snapshot = await api('/api/get_board_snapshot?archive_limit=10');
        applyBoardScale(state.snapshot?.settings?.board_scale ?? state.boardScale ?? 1, { syncInput: true });
        renderBoard();
        primeBoardViewport();
        if (els.gptWallModal.classList.contains('is-open')) await loadGptWall(false);
        const data = state.snapshot;
        setStatus(showSuccess ? ('ДОСКА ОБНОВЛЕНА · ' + new Date().toLocaleTimeString('ru-RU')) : ('СЕРВЕР АКТИВЕН · КАРТОЧЕК: ' + data.cards.length + ' · АРХИВ: ' + data.archive.length));
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function saveCard() {
      const payload = currentCardPayload();
      if (!payload.title) return setStatus('УКАЖИ ЗАГОЛОВОК КАРТОЧКИ.', true);
      try {
        if (state.editingId) {
          await api('/api/update_card', { method: 'POST', body: { card_id: state.editingId, ...payload } });
        } else {
          await api('/api/create_card', { method: 'POST', body: payload });
        }
        closeCardModal();
        await refreshSnapshot(true);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function loadLogs(cardId) {
      try {
        const data = await api('/api/get_card_log?card_id=' + encodeURIComponent(cardId));
        renderLogs(data.events || []);
      } catch (error) {
        renderLogs([{ message: error.message, timestamp: new Date().toISOString(), actor_name: 'СИСТЕМА', source: 'ui', details: {} }]);
      }
    }

    async function openCardById(cardId) {
      try {
        const data = await api('/api/get_card?card_id=' + encodeURIComponent(cardId));
        openCardModal(data.card);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function moveCard(cardId, columnId) {
      try {
        await api('/api/move_card', { method: 'POST', body: { card_id: cardId, column: columnId, actor_name: state.actor, source: 'ui' } });
        await refreshSnapshot(true);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function restoreCard(cardId) {
      try {
        await api('/api/restore_card', { method: 'POST', body: { card_id: cardId, actor_name: state.actor, source: 'ui' } });
        await refreshSnapshot(true);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    function openBoardSettings() {
      applyBoardScale(state.boardScale || 1, { syncInput: true });
      els.boardSettingsModal.classList.add('is-open');
    }

    function openStickyModal(sticky = null) {
      const existing = sticky || null;
      state.stickyDraft = existing;
      els.stickyModalTitle.textContent = existing ? ('СТИКЕР / ' + String(existing.id).slice(0, 8).toUpperCase()) : 'НОВЫЙ СТИКЕР';
      els.stickyText.value = existing?.text || '';
      const parts = secondsToParts(existing?.deadline_total_seconds || 4 * 3600);
      els.stickyDays.value = parts.days ?? 0;
      els.stickyHours.value = parts.hours ?? 4;
      els.stickyModal.classList.add('is-open');
      setTimeout(() => els.stickyText.focus(), 0);
    }

    function closeStickyModal() {
      els.stickyModal.classList.remove('is-open');
      state.stickyDraft = null;
    }

    function buildStickyPayload() {
      const draft = stickyPayload();
      const deadline = stickyDeadlineInput();
      const placement = state.stickyDraft?.id ? null : getStickyComposerPlacement();
      return {
        sticky_id: state.stickyDraft?.id || null,
        text: draft.text,
        deadline,
        x: state.stickyDraft?.x ?? placement?.x ?? 0,
        y: state.stickyDraft?.y ?? placement?.y ?? 0,
      };
    }

    async function saveSticky() {
      const payload = buildStickyPayload();
      if (!payload.text) return setStatus('УКАЖИ ТЕКСТ СТИКЕРА.', true);
      try {
        if (payload.sticky_id) {
          await api('/api/update_sticky', { method: 'POST', body: { sticky_id: payload.sticky_id, text: payload.text, deadline: payload.deadline, actor_name: state.actor, source: 'ui' } });
        } else {
          await api('/api/create_sticky', { method: 'POST', body: { text: payload.text, x: payload.x, y: payload.y, deadline: payload.deadline, actor_name: state.actor, source: 'ui' } });
        }
        closeStickyModal();
        await refreshSnapshot(true);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function removeSticky(stickyId) {
      try {
        await api('/api/delete_sticky', { method: 'POST', body: { sticky_id: stickyId, actor_name: state.actor, source: 'ui' } });
        await refreshSnapshot(true);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function saveBoardScale() {
      const scale = normalizeBoardScale(Number(els.boardScaleInput.value) / 100);
      applyBoardScale(scale, { syncInput: true });
      try {
        await api('/api/update_board_settings', {
          method: 'POST',
          body: { board_scale: scale, actor_name: state.actor, source: 'ui' },
        });
        await refreshSnapshot(true);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    function beginStickyDrag(event) {
      if (event.button !== 0) return;
      if (!(event.target instanceof HTMLElement)) return;
      const sticky = event.target.closest('.sticky');
      if (!sticky || event.target.closest('.sticky__close')) return;
      const stickyId = sticky.dataset.stickyId;
      const current = (state.snapshot?.stickies || []).find((item) => item.id === stickyId);
      if (!current) return;
      state.stickyDrag = {
        active: true,
        pointerId: event.pointerId,
        stickyId,
        startX: Number(current.x || 0),
        startY: Number(current.y || 0),
        startClientX: event.clientX,
        startClientY: event.clientY,
        moved: false,
      };
      sticky.classList.add('is-dragging');
      event.preventDefault();
    }

    function moveStickyDrag(event) {
      const drag = state.stickyDrag;
      if (!drag.active || drag.pointerId !== event.pointerId) return;
      const scale = state.boardScale || 1;
      const dx = event.clientX - drag.startClientX;
      const dy = event.clientY - drag.startClientY;
      if (!drag.moved && Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
      if (!drag.moved) return;
      const nextX = Math.max(0, Math.round(drag.startX + (dx / scale)));
      const nextY = Math.max(0, Math.round(drag.startY + (dy / scale)));
      const sticky = document.querySelector('.sticky[data-sticky-id="' + CSS.escape(drag.stickyId) + '"]');
      if (sticky) {
        sticky.style.left = stickyRenderPosition(nextX, scale) + 'px';
        sticky.style.top = stickyRenderPosition(nextY, scale) + 'px';
      }
      event.preventDefault();
    }

    async function endStickyDrag(event) {
      const drag = state.stickyDrag;
      if (!drag.active || drag.pointerId !== event.pointerId) return;
      const sticky = document.querySelector('.sticky[data-sticky-id="' + CSS.escape(drag.stickyId) + '"]');
      if (sticky) sticky.classList.remove('is-dragging');
      state.stickyDrag = {
        active: false,
        pointerId: null,
        stickyId: null,
        startX: 0,
        startY: 0,
        startClientX: 0,
        startClientY: 0,
        moved: false,
      };
      if (!drag.moved) return;
      const scale = state.boardScale || 1;
      const dx = event.clientX - drag.startClientX;
      const dy = event.clientY - drag.startClientY;
      const nextX = Math.max(0, Math.round(drag.startX + (dx / scale)));
      const nextY = Math.max(0, Math.round(drag.startY + (dy / scale)));
      try {
        await api('/api/move_sticky', { method: 'POST', body: { sticky_id: drag.stickyId, x: nextX, y: nextY, actor_name: state.actor, source: 'ui' } });
        await refreshSnapshot(true);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function uploadFiles() {
      if (!state.editingId || !els.fileInput.files.length) return;
      try {
        for (const file of els.fileInput.files) {
          const buffer = await file.arrayBuffer();
          const base64 = arrayBufferToBase64(buffer);
          await api('/api/add_card_attachment', { method: 'POST', body: { card_id: state.editingId, actor_name: state.actor, source: 'ui', file_name: file.name, mime_type: file.type || 'application/octet-stream', content_base64: base64 } });
        }
        const data = await api('/api/get_card?card_id=' + encodeURIComponent(state.editingId));
        state.activeCard = data.card;
        renderFiles(data.card);
        await refreshSnapshot(true);
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        els.fileInput.value = '';
      }
    }

    document.addEventListener('click', async (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      if (target.dataset.close === 'card') closeCardModal();
      if (target.dataset.close === 'archive') els.archiveModal.classList.remove('is-open');
      if (target.dataset.close === 'wall') els.gptWallModal.classList.remove('is-open');
      if (target.dataset.close === 'settings') els.boardSettingsModal.classList.remove('is-open');
      if (target.dataset.close === 'sticky') closeStickyModal();
      if (target.dataset.tab) setTab(target.dataset.tab);
      if (target.dataset.createIn) openCardModal({ column: target.dataset.createIn, tags: [], attachments: [], remaining_seconds: 86400 });
      if (target === els.stickyDockButton || target.closest('#stickyDockButton')) { openStickyModal(); return; }
      if (target.closest('.sticky__close')) {
        await removeSticky(target.closest('.sticky').dataset.stickyId);
        return;
      }
      if (target.closest('.sticky')) return;
      const attachmentLink = target.closest('a[href*="/api/attachment"]');
      if (attachmentLink) {
        event.preventDefault();
        try {
          await downloadAttachment(withAccessToken(attachmentLink.getAttribute('href')));
        } catch (error) {
          setStatus(error.message, true);
        }
        return;
      }
      if (target.closest('.card')) await openCardById(target.closest('.card').dataset.cardId);
      if (target.dataset.removeTag) { state.draftTags = state.draftTags.filter((tag) => tag !== target.dataset.removeTag); renderTags(); }
      if (target.dataset.suggestTag) addSuggestedTag(target.dataset.suggestTag);
      if (target.dataset.restoreCard) await restoreCard(target.dataset.restoreCard);
      if (target.dataset.removeFile && state.editingId) {
        await api('/api/remove_card_attachment', { method: 'POST', body: { card_id: state.editingId, attachment_id: target.dataset.removeFile, actor_name: state.actor, source: 'ui' } });
        const data = await api('/api/get_card?card_id=' + encodeURIComponent(state.editingId));
        state.activeCard = data.card;
        renderFiles(data.card);
        await refreshSnapshot(true);
      }
    });

    document.addEventListener('dragstart', (event) => {
      const card = event.target.closest('.card');
      if (card) event.dataTransfer?.setData('text/plain', card.dataset.cardId);
    });
    document.addEventListener('dragover', (event) => {
      const column = event.target.closest('.column');
      if (!column) return;
      event.preventDefault();
      column.classList.add('is-drop-target');
    });
    document.addEventListener('dragleave', (event) => {
      const column = event.target.closest('.column');
      if (column) column.classList.remove('is-drop-target');
    });
    document.addEventListener('drop', async (event) => {
      const column = event.target.closest('.column');
      if (!column) return;
      event.preventDefault();
      column.classList.remove('is-drop-target');
      const cardId = event.dataTransfer?.getData('text/plain');
      if (cardId) await moveCard(cardId, column.dataset.columnId);
    });
    els.boardScroll.addEventListener('pointerdown', beginBoardPan);
    els.boardScroll.addEventListener('pointermove', moveBoardPan);
    els.boardScroll.addEventListener('pointerup', endBoardPan);
    els.boardScroll.addEventListener('pointercancel', endBoardPan);
    els.boardScroll.addEventListener('lostpointercapture', endBoardPan);
    window.addEventListener('resize', () => { adjustBoardBounds(); });
    document.addEventListener('pointerdown', beginStickyDrag);
    document.addEventListener('pointermove', moveStickyDrag);
    document.addEventListener('pointerup', endStickyDrag);
    document.addEventListener('pointercancel', endStickyDrag);

    els.identitySave.addEventListener('click', () => {
      const actor = els.identityInput.value.trim().toUpperCase();
      if (!actor) return setStatus('НУЖНО УКАЗАТЬ ИМЯ ОПЕРАТОРА.', true);
      state.actor = actor;
      localStorage.setItem(ACTOR_STORAGE_KEY, actor);
      ensureActor();
    });
    els.operatorButton.addEventListener('click', () => { state.actor = ''; localStorage.removeItem(ACTOR_STORAGE_KEY); ensureActor(); });
    els.boardSettingsButton.addEventListener('click', () => { openBoardSettings(); });
    els.archiveButton.addEventListener('click', () => { els.archiveModal.classList.add('is-open'); });
    els.gptWallButton.addEventListener('click', () => { loadGptWall(true); });
    els.gptWallRefresh.addEventListener('click', () => { loadGptWall(false); });
    els.boardScaleInput.addEventListener('input', () => { zoomBoardTo(Number(els.boardScaleInput.value) / 100); });
    els.boardScaleInput.addEventListener('change', async () => { await saveBoardScale(); });
    els.boardScaleReset.addEventListener('click', async () => {
      els.boardScaleInput.value = '100';
      zoomBoardTo(1, { syncInput: true });
      await saveBoardScale();
    });
    els.columnButton.addEventListener('click', async () => {
      const label = window.prompt('Название столбца');
      if (!label) return;
      try {
        await api('/api/create_column', { method: 'POST', body: { label, actor_name: state.actor, source: 'ui' } });
        await refreshSnapshot(true);
      } catch (error) {
        setStatus(error.message, true);
      }
    });
    els.cardButton.addEventListener('click', () => openCardModal({ column: state.snapshot?.columns?.[0]?.id || 'inbox', tags: [], attachments: [], remaining_seconds: 86400 }));
    [els.signalDays, els.signalHours].forEach((input) => {
      input.addEventListener('input', renderSignalPreview);
      input.addEventListener('change', renderSignalPreview);
    });
    els.tagAddButton.addEventListener('click', addDraftTag);
    els.tagInput.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      addDraftTag();
    });
    els.saveCardButton.addEventListener('click', saveCard);
    els.saveStickyButton.addEventListener('click', saveSticky);
    els.archiveAction.addEventListener('click', async () => {
      if (!state.editingId) return;
      try {
        await api('/api/archive_card', { method: 'POST', body: { card_id: state.editingId, actor_name: state.actor, source: 'ui' } });
        closeCardModal();
        await refreshSnapshot(true);
      } catch (error) {
        setStatus(error.message, true);
      }
    });
    els.restoreAction.addEventListener('click', async () => {
      if (!state.editingId) return;
      await restoreCard(state.editingId);
      closeCardModal();
    });
    els.uploadButton.addEventListener('click', uploadFiles);
    els.stickyModal.addEventListener('click', (event) => {
      if (!(event.target instanceof HTMLElement)) return;
      if (event.target.classList.contains('modal')) closeStickyModal();
    });

    consumeUrlAccessToken();
    mountStatusLine();
    ensureActor();
    refreshSnapshot(true);
    state.pollHandle = setInterval(() => refreshSnapshot(false), 2500);
  </script>
</body>
</html>
""",
    ]
)
